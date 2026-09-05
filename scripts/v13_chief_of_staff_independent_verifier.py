"""Fresh read-only verifier for the V13 Chief of Staff disposable proof.

The verifier does not trust the proof's verdict or counts. It reopens the
database, rebuilds the Chief of Staff and Command Center views, inspects event
causation, verifies Git identity, and optionally requires a real full-suite
JUnit artifact. Projection rebuilding and replay checks run only on backups.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from jarvis_foundry.chief_of_staff import (
    ChiefOfStaffService,
    project_chief_of_staff,
)
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import canonical_json
from jarvis_foundry.executive.continuous import rebuild_wake_state
from jarvis_foundry.owner_context import project_standing_orders
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.reconcile import reconcile


BASE_SHA = "7f153851517b5529dd1e582725a0c2a6d96ddc9b"
EXPECTED_BRANCH = "candidate/v13-chief-of-staff-20260903"
INITIAL_MAIN_SHA = "2905c206fed0461bf3085bc0cb43773be913300a"
SECRET = b"v13-chief-of-staff-proof-secret"
REPO_ROOT = Path(__file__).resolve().parents[1]
PROOF_SCRIPT = REPO_ROOT / "scripts" / "v13_chief_of_staff_proof.py"

JARVIS_ORDER = "directive:standing-jarvis-blocked"
LEADGEN_ORDER = "directive:standing-leadgen-recovery"
OPERATOR_ORDER = "directive:standing-operator-clean"

EXPECTED_CHANGE_IDS = {
    "change-jarvis-dependency-wait",
    "change-leadgen-recoverable",
    "change-operatoros-dirty",
    "change-operatoros-urgent-cosmetic",
    "change-operatoros-after-revoke",
    "change-leadgen-adjacent-opportunity",
    "change-leadgen-owner-decision",
    "change-operatoros-intentional-idle",
    "change-jarvis-after-dependency",
}


def _git(*args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ledger_digest(database: FoundryDatabase) -> str:
    rows = [
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "source_type": event.source_type,
            "source_id": event.source_id,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "idempotency_key": event.idempotency_key,
            "payload_hash": event.payload_hash,
        }
        for event in database.events()
    ]
    return hashlib.sha256(
        canonical_json(rows).encode("utf-8"),
    ).hexdigest()


def _event_count(database: FoundryDatabase, event_type: str) -> int:
    return database.event_count(event_type=event_type)


def _prefix_count(database: FoundryDatabase, prefix: str) -> int:
    return len(database.events(event_type_prefixes=(prefix,)))


def _table_count(database: FoundryDatabase, table: str) -> int:
    with database.connect(readonly=True) as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table}",
            ).fetchone()[0]
        )


def _schema(database: FoundryDatabase) -> int:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations",
        ).fetchone()
    return int(row[0] if row else 0)


def _fresh_process_snapshot(
    database_path: Path,
    *,
    now: str,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROOF_SCRIPT),
            "--snapshot-db",
            str(database_path),
            "--now",
            now,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _project(
    projection: Mapping[str, Any],
    project_id: str,
) -> dict[str, Any]:
    return next(
        dict(item)
        for item in projection.get("projects", ())
        if item.get("project_id") == project_id
    )


def _priority(
    projection: Mapping[str, Any],
    project_id: str,
) -> dict[str, Any]:
    return next(
        dict(item)
        for item in projection.get("priorities", ())
        if item.get("project_id") == project_id
    )


def _junit_integer(element: ET.Element, name: str) -> int | None:
    value = element.attrib.get(name)
    return int(float(value)) if value is not None else None


def parse_junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = (
        [root]
        if root.tag == "testsuite"
        else list(root.iter("testsuite"))
    )
    tests = _junit_integer(root, "tests")
    failures = _junit_integer(root, "failures")
    errors = _junit_integer(root, "errors")
    skipped = _junit_integer(root, "skipped")
    if tests is None or failures is None or errors is None:
        tests = sum(_junit_integer(item, "tests") or 0 for item in suites)
        failures = sum(
            _junit_integer(item, "failures") or 0 for item in suites
        )
        errors = sum(_junit_integer(item, "errors") or 0 for item in suites)
        skipped = sum(
            _junit_integer(item, "skipped") or 0 for item in suites
        )
    failed_cases = [
        f"{case.get('classname')}::{case.get('name')}"
        for case in root.iter("testcase")
        if (
            case.find("failure") is not None
            or case.find("error") is not None
        )
    ]
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped or 0,
        "failed_cases": failed_cases[:25],
        "ok": tests > 0 and failures == 0 and errors == 0,
    }


def _dirty_paths_allowed(status: str) -> bool:
    lines = [line for line in status.splitlines() if line.strip()]
    if not lines:
        return True
    return all(
        "evidence/v13-chief-of-staff/" in line.replace("\\", "/")
        for line in lines
    )


def _event_outcomes(
    database: FoundryDatabase,
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    chief_events = database.events(
        event_type_prefixes=("chief_of_staff.",),
    )
    observed: dict[str, Any] = {}
    change_by_event_id: dict[str, str] = {}
    for event in chief_events:
        if event.event_type != "chief_of_staff.change_observed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        change = payload.get("change")
        if not isinstance(change, dict):
            continue
        change_id = str(change.get("change_id") or "")
        if change_id:
            observed[change_id] = event
            change_by_event_id[event.event_id] = change_id
    outcomes: dict[str, list[Any]] = {
        change_id: [] for change_id in observed
    }
    for event in chief_events:
        if event.event_type == "chief_of_staff.change_observed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        change_id = str(payload.get("change_id") or "")
        if not change_id and event.causation_id:
            change_id = change_by_event_id.get(event.causation_id, "")
        if change_id:
            outcomes.setdefault(change_id, []).append(event)
    return observed, outcomes


def _payload_standing_orders(events: list[Any]) -> list[str]:
    values: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        values.update(
            str(item)
            for item in payload.get("standing_order_ids") or ()
            if str(item)
        )
    return sorted(values)


def _has_outcome(
    outcomes: Mapping[str, list[Any]],
    change_id: str,
    event_type: str,
) -> bool:
    return any(
        event.event_type == event_type
        for event in outcomes.get(change_id, ())
    )


def _replay_on_backup(
    database: FoundryDatabase,
    *,
    observed: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="jarvis-v13-chief-verifier-replay-",
    ) as raw:
        root = Path(raw)
        clone_path = database.backup_to(root / "foundry.db")
        clone = FoundryDatabase(clone_path)
        controls = OwnerControlService(clone, secret=SECRET)
        text = "Independent verifier revokes the disposable veto on its copy"
        controls.apply(
            "REVOKE",
            target="v13-proof-veto",
            exact_text=text,
            nonce="verifier-revoke-v13-proof-veto",
            signature=sign_command(
                SECRET,
                "REVOKE",
                "v13-proof-veto",
                text,
                "verifier-revoke-v13-proof-veto",
            ),
        )
        source_event = observed["change-jarvis-after-dependency"]
        change = dict(source_event.payload["change"])
        change.pop("contract", None)
        change.pop("schema", None)
        before = clone.event_count()
        result = ChiefOfStaffService(
            clone,
            stop_path=root / "EMERGENCY_STOP",
        ).consider(change, now=now)
        after = clone.event_count()
        return {
            "event_delta": after - before,
            "candidate_count": _table_count(clone, "candidates"),
            "status": result.status,
            "duplicate": result.duplicate,
            "candidate_id": result.candidate_id,
        }


def verify_proof(
    proof_path: Path,
    *,
    full_suite_junit: Path | None = None,
    require_full_suite: bool = True,
    enforce_source: bool = True,
) -> dict[str, Any]:
    proof_path = proof_path.resolve()
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    database_path = Path(str(proof.get("database") or "")).resolve()
    now = str(proof.get("generated_at") or "")
    findings: list[str] = []
    junit = (
        parse_junit(full_suite_junit.resolve())
        if full_suite_junit is not None and full_suite_junit.is_file()
        else None
    )
    if require_full_suite and junit is None:
        findings.append("full-suite JUnit artifact is required")
    elif junit is not None and not junit["ok"]:
        findings.append("full-suite JUnit is not green")
    if not database_path.is_file():
        return {
            "contract": "jarvis.v13.chief-of-staff-independent-verifier.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "proof": str(proof_path),
            "database": str(database_path),
            "checks": {"database_exists": False},
            "findings": [*findings, "proof database does not exist"],
            "verdict": "FAIL",
            "full_suite": junit,
        }

    database = FoundryDatabase(database_path)
    before_reads = database.event_count()
    direct_projection = project_chief_of_staff(database, now=now).as_dict()
    fresh_projection = _fresh_process_snapshot(database_path, now=now)
    snapshot = build_snapshot(database, now=now)
    command_center_projection = snapshot["executive"]["chief_of_staff"]
    packet_projection = snapshot["briefing_packet"]["chief_of_staff"]
    after_reads = database.event_count()
    observed, outcomes = _event_outcomes(database)
    replay = _replay_on_backup(database, observed=observed, now=now)

    with tempfile.TemporaryDirectory(
        prefix="jarvis-v13-chief-verifier-rebuild-",
    ) as raw:
        clone_path = database.backup_to(Path(raw) / "foundry.db")
        clone = FoundryDatabase(clone_path)
        clone.rebuild_projections()
        rebuilt_projection = project_chief_of_staff(
            clone,
            now=now,
        ).as_dict()
        rebuilt_reconciliation = reconcile(clone, readonly=True).consistent

    project_ids = {
        str(item["project_id"]) for item in direct_projection["projects"]
    }
    jarvis = _project(direct_projection, "jarvis")
    leadgen = _project(direct_projection, "leadgen")
    operatoros = _project(direct_projection, "operatoros")
    jarvis_edges = {
        (
            item["parent_kind"],
            item["parent_id"],
            item["child_kind"],
            item["child_id"],
        )
        for item in jarvis["hierarchy_links"]
    }
    required_edges = {
        (
            "OWNER_GOAL",
            "goal:owner-portfolio",
            "PROGRAM",
            "program:personal-operator",
        ),
        (
            "PROGRAM",
            "program:personal-operator",
            "PROJECT",
            "jarvis",
        ),
        (
            "PROJECT",
            "jarvis",
            "OBJECTIVE",
            "objective-jarvis-v13",
        ),
        (
            "OBJECTIVE",
            "objective-jarvis-v13",
            "CAMPAIGN",
            "campaign-jarvis-v13",
        ),
        (
            "CAMPAIGN",
            "campaign-jarvis-v13",
            "MISSION",
            "mission-jarvis-v13",
        ),
        (
            "MISSION",
            "mission-jarvis-v13",
            "ACTION",
            "action-jarvis-v13",
        ),
    }
    active_order_ids = {
        str(item["standing_order_id"])
        for item in project_standing_orders(
            database,
            now=now,
            include_history=False,
        )
    }
    wakes = rebuild_wake_state(database)
    canonical_wake_id = (
        "wake:objective-jarvis-v13:dep:objective-jarvis-dependency"
    )
    priority_jarvis = _priority(direct_projection, "jarvis")
    priority_leadgen = _priority(direct_projection, "leadgen")
    priority_operator = _priority(direct_projection, "operatoros")
    recomputed_counts = {
        "ledger_events": database.event_count(),
        "projects": len(direct_projection["projects"]),
        "chief_changes": _event_count(
            database, "chief_of_staff.change_observed",
        ),
        "candidate_routed": _event_count(
            database, "chief_of_staff.candidate_routed",
        ),
        "recommendations_rejected": _event_count(
            database, "chief_of_staff.recommendation_rejected",
        ),
        "waits_recorded": _event_count(
            database, "chief_of_staff.wait_recorded",
        ),
        "owner_attention": _event_count(
            database, "chief_of_staff.owner_attention_required",
        ),
        "candidates": _table_count(database, "candidates"),
        "objective_created": _event_count(
            database, "executive.objective_created",
        ),
        "mission_created": _event_count(database, "mission.created"),
        "wake_created": _event_count(
            database, "executive.wake_created",
        ),
        "wake_triggered": _event_count(
            database, "executive.wake_triggered",
        ),
        "effect_events": _prefix_count(database, "effect."),
        "decision_queue_matches": sum(
            1
            for item in snapshot["decision_queue"]["open"]
            if item.get("source_event_id")
            in {
                event.event_id
                for event in outcomes[
                    "change-leadgen-owner-decision"
                ]
                if event.event_type
                == "chief_of_staff.owner_attention_required"
            }
        ),
        "false_standing_order_triggers": 0,
    }
    source = proof.get("source") if isinstance(proof.get("source"), dict) else {}
    source_sha = str(source.get("source_sha") or "")
    source_commit_exists = subprocess.run(
        ["git", "cat-file", "-e", f"{source_sha}^{{commit}}"],
        cwd=REPO_ROOT,
        check=False,
    ).returncode == 0
    source_is_ancestor = (
        source_commit_exists
        and subprocess.run(
            ["git", "merge-base", "--is-ancestor", source_sha, "HEAD"],
            cwd=REPO_ROOT,
            check=False,
        ).returncode == 0
    )
    current_status = _git("status", "--porcelain")

    expected_standing_orders = {
        "change-jarvis-dependency-wait": [JARVIS_ORDER],
        "change-leadgen-recoverable": [LEADGEN_ORDER],
        "change-operatoros-dirty": [OPERATOR_ORDER],
        "change-operatoros-urgent-cosmetic": [],
        "change-operatoros-intentional-idle": [],
        "change-leadgen-owner-decision": [],
        "change-jarvis-after-dependency": [JARVIS_ORDER],
    }
    historical_order_matches = {
        change_id: _payload_standing_orders(outcomes[change_id])
        for change_id in expected_standing_orders
    }
    false_trigger_names = [
        change_id
        for change_id, expected in expected_standing_orders.items()
        if historical_order_matches[change_id] != expected
    ]
    recomputed_counts["false_standing_order_triggers"] = len(
        false_trigger_names,
    )
    service_authority_events = [
        event.event_type
        for event in database.events()
        if event.source_id == "v13-chief-of-staff"
        and (
            event.event_type.startswith("executive.objective_")
            or event.event_type.startswith("mission.")
            or event.event_type.startswith("effect.")
        )
    ]
    source_attestations = (
        proof.get("source_attestations")
        if isinstance(proof.get("source_attestations"), dict)
        else {}
    )
    proof_checks = (
        proof.get("checks") if isinstance(proof.get("checks"), dict) else {}
    )
    checks = {
        "database_exists": True,
        "schema_v40": _schema(database) == 40,
        "event_integrity": (
            database.verify_event_integrity() == database.event_count()
        ),
        "quick_check": database.quick_check() == "ok",
        "reconciliation": (
            reconcile(database, readonly=True).consistent
            and rebuilt_reconciliation
        ),
        "ledger_digest_matches": (
            _ledger_digest(database) == proof.get("ledger_digest")
        ),
        "three_projects": project_ids == {
            "jarvis",
            "leadgen",
            "operatoros",
        },
        "full_hierarchy": required_edges.issubset(jarvis_edges),
        "project_health_truth": (
            jarvis["status"] == "ACTIVE"
            and jarvis["health"] == "GREEN"
            and jarvis["waiting_condition"] is None
            and leadgen["status"] == "WAITING_ON_OWNER"
            and leadgen["owner_decision_needed"] is True
            and operatoros["status"] == "IDLE"
            and operatoros["health"] == "GREEN"
            and operatoros["intentional_idle"] is True
        ),
        "strategic_priority_not_raw_urgency": (
            priority_jarvis["rank"] < priority_operator["rank"]
            and priority_leadgen["rank"] < priority_operator["rank"]
            and priority_operator["factors"]["urgency"][
                "normalized_value"
            ] == 1.0
            and _has_outcome(
                outcomes,
                "change-operatoros-urgent-cosmetic",
                "chief_of_staff.recommendation_rejected",
            )
        ),
        "standing_order_history_is_exact": (
            not false_trigger_names
            and {JARVIS_ORDER, LEADGEN_ORDER}.issubset(active_order_ids)
            and OPERATOR_ORDER not in active_order_ids
        ),
        "observed_change_set_is_exact": (
            set(observed) == EXPECTED_CHANGE_IDS
            and "change-jarvis-stop" not in observed
            and "change-jarvis-veto" not in observed
        ),
        "quiet_recovery_has_no_attention": (
            _has_outcome(
                outcomes,
                "change-leadgen-recoverable",
                "chief_of_staff.wait_recorded",
            )
            and not _has_outcome(
                outcomes,
                "change-leadgen-recoverable",
                "chief_of_staff.owner_attention_required",
            )
        ),
        "revoked_order_and_adjacent_opportunity_wait": (
            _has_outcome(
                outcomes,
                "change-operatoros-after-revoke",
                "chief_of_staff.wait_recorded",
            )
            and not _has_outcome(
                outcomes,
                "change-operatoros-after-revoke",
                "chief_of_staff.candidate_routed",
            )
            and _has_outcome(
                outcomes,
                "change-leadgen-adjacent-opportunity",
                "chief_of_staff.wait_recorded",
            )
            and not _has_outcome(
                outcomes,
                "change-leadgen-adjacent-opportunity",
                "chief_of_staff.candidate_routed",
            )
        ),
        "one_real_owner_decision": (
            recomputed_counts["owner_attention"] == 1
            and recomputed_counts["decision_queue_matches"] == 1
        ),
        "one_candidate_no_duplicates": (
            recomputed_counts["candidates"] == 1
            and recomputed_counts["candidate_routed"] == 1
            and replay["event_delta"] == 0
            and replay["candidate_count"] == 1
            and replay["duplicate"] is True
        ),
        "wait_wake_and_continuation": (
            canonical_wake_id in wakes
            and wakes[canonical_wake_id]["status"] == "TRIGGERED"
            and recomputed_counts["wake_created"] == 1
            and recomputed_counts["wake_triggered"] == 1
            and _has_outcome(
                outcomes,
                "change-jarvis-after-dependency",
                "chief_of_staff.candidate_routed",
            )
        ),
        "no_second_operational_authority": (
            recomputed_counts["objective_created"] == 2
            and recomputed_counts["mission_created"] == 1
            and recomputed_counts["effect_events"] == 0
            and not service_authority_events
            and _prefix_count(database, "gmail.") == 0
            and _prefix_count(database, "voice.") == 0
        ),
        "read_models_are_identical_and_read_only": (
            direct_projection
            == fresh_projection
            == command_center_projection
            == packet_projection
            == rebuilt_projection
            and before_reads == after_reads
        ),
        "proof_claimed_counts_match": (
            proof.get("counts") == recomputed_counts
        ),
        "proof_checks_claim_only_true": (
            bool(proof_checks)
            and all(value is True for value in proof_checks.values())
            and proof.get("passed") is True
        ),
        "source_commit_exists": source_commit_exists,
        "source_commit_is_ancestor": source_is_ancestor,
        "source_tree_matches": (
            source_commit_exists
            and _git("rev-parse", f"{source_sha}^{{tree}}")
            == source.get("source_tree")
        ),
        "branch_and_base_match": (
            _git("branch", "--show-current") == EXPECTED_BRANCH
            and _git("merge-base", "HEAD", BASE_SHA) == BASE_SHA
        ),
        "main_and_origin_unchanged": (
            _git("rev-parse", "main") == INITIAL_MAIN_SHA
            and _git("rev-parse", "origin/main") == INITIAL_MAIN_SHA
        ),
        "proof_source_attestations_are_true": (
            bool(source_attestations)
            and all(
                value is True for value in source_attestations.values()
            )
            and proof.get("source_enforced") is True
        ),
        "current_dirty_paths_are_evidence_only": (
            _dirty_paths_allowed(current_status)
        ),
        "full_suite_is_green": (
            junit is not None and junit["ok"]
            if require_full_suite
            else junit is None or junit["ok"]
        ),
    }
    if not enforce_source:
        for name in (
            "source_commit_exists",
            "source_commit_is_ancestor",
            "source_tree_matches",
            "branch_and_base_match",
            "main_and_origin_unchanged",
            "proof_source_attestations_are_true",
            "current_dirty_paths_are_evidence_only",
        ):
            checks[name] = True
    for name, passed in checks.items():
        if not passed:
            findings.append(name)
    return {
        "contract": "jarvis.v13.chief-of-staff-independent-verifier.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "fresh-independent-verifier-process",
        "scope": "DISPOSABLE_DATABASE_AND_SOURCE_IDENTITY",
        "proof": str(proof_path),
        "proof_sha256": _sha256(proof_path),
        "database": str(database_path),
        "source_sha": source_sha,
        "checks": checks,
        "findings": findings,
        "recomputed_counts": recomputed_counts,
        "claimed_counts": proof.get("counts"),
        "historical_standing_order_matches": historical_order_matches,
        "false_standing_order_triggers": false_trigger_names,
        "replay_on_backup": replay,
        "full_suite": junit,
        "verdict": "PASS" if not findings else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--full-suite-junit", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_proof(
        args.proof,
        full_suite_junit=args.full_suite_junit,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps({
        "verdict": result["verdict"],
        "output": str(args.output) if args.output else None,
        "source_sha": result["source_sha"],
        "recomputed_counts": result["recomputed_counts"],
        "failed_checks": [
            name
            for name, passed in result["checks"].items()
            if not passed
        ],
        "full_suite": result["full_suite"],
    }, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
