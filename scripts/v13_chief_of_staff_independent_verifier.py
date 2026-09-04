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
from jarvis_foundry.owner_context import (
    ContextScope,
    compile_standing_order,
    project_standing_orders,
)
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.reconcile import reconcile


BASE_SHA = "7f153851517b5529dd1e582725a0c2a6d96ddc9b"
V12_FINAL_CANDIDATE = "51d2cfcce1be847fd252d1c857105f09f5f2c0e4"
OWNER_CONTEXT_SOURCE = "41dfcd285e3fb407237a43b9c2caba47d9d6ab1b"
OWNER_CONTEXT_FINAL = "ca3e9adb6a559f507253bdf7b70854424bc8b35d"
EXPECTED_BRANCH = "candidate/v13-chief-of-staff-20260903"
INITIAL_MAIN_SHA = "2905c206fed0461bf3085bc0cb43773be913300a"
SECRET = b"v13-chief-of-staff-proof-secret"
REPO_ROOT = Path(__file__).resolve().parents[1]
PROOF_SCRIPT = REPO_ROOT / "scripts" / "v13_chief_of_staff_proof.py"

JARVIS_ORDER = "directive:standing-jarvis-blocked"
LEADGEN_ORDER = "directive:standing-leadgen-recovery"
OPERATOR_ORDER = "directive:standing-operator-clean"
RUNTIME_OBSERVATION_IDENTITY = "project:jarvis:reliability-regression"
PRIORITY_FACTOR_NAMES = {
    "owner_priority",
    "strategic_value",
    "urgency",
    "deadline",
    "blockers",
    "dependency_leverage",
    "information_gain",
    "cost",
    "risk",
    "reversibility",
    "confidence",
    "opportunity_cost",
    "staleness",
}

EXPECTED_DIRECT_CHANGE_IDS = {
    "change-jarvis-dependency-wait",
    "change-leadgen-recoverable",
    "change-leadgen-recoverable-resolved",
    "change-operatoros-dirty",
    "change-operatoros-urgent-cosmetic",
    "change-operatoros-after-revoke",
    "change-leadgen-adjacent-opportunity",
    "change-operatoros-temporary-owner-decision",
    "change-operatoros-temporary-owner-decision-resolved",
    "change-leadgen-owner-decision",
    "change-operatoros-intentional-idle",
    "change-jarvis-late-transaction-stop",
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


def _revoke_proof_veto(
    database: FoundryDatabase,
    *,
    nonce: str,
) -> None:
    controls = OwnerControlService(database, secret=SECRET)
    text = "Independent verifier revokes the disposable veto on its copy"
    controls.apply(
        "REVOKE",
        target="v13-proof-veto",
        exact_text=text,
        nonce=nonce,
        signature=sign_command(
            SECRET,
            "REVOKE",
            "v13-proof-veto",
            text,
            nonce,
        ),
    )


def _replay_on_backup(
    database: FoundryDatabase,
    *,
    observed: Mapping[str, Any],
    runtime_change_id: str,
    now: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="jarvis-v13-chief-verifier-replay-",
    ) as raw:
        root = Path(raw)
        clone_path = database.backup_to(root / "foundry.db")
        clone = FoundryDatabase(clone_path)
        _revoke_proof_veto(
            clone,
            nonce="verifier-revoke-v13-proof-veto",
        )
        source_event = observed.get(runtime_change_id)
        if source_event is None:
            return {
                "event_delta": -1,
                "candidate_count": _table_count(clone, "candidates"),
                "status": "RUNTIME_CHANGE_MISSING",
                "duplicate": False,
                "candidate_id": None,
            }
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


def _projection_through_event(
    database: FoundryDatabase,
    *,
    event_id: str,
    now: str,
) -> dict[str, Any]:
    if not event_id:
        return {}
    with tempfile.TemporaryDirectory(
        prefix="jarvis-v13-chief-verifier-cutoff-",
    ) as raw:
        clone_path = database.backup_to(Path(raw) / "foundry.db")
        clone = FoundryDatabase(clone_path)
        with clone.transaction() as connection:
            row = connection.execute(
                "SELECT sequence FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                return {}
            connection.execute(
                "DELETE FROM events WHERE sequence > ?",
                (int(row["sequence"]),),
            )
        clone.rebuild_projections()
        return project_chief_of_staff(clone, now=now).as_dict()


def _late_stop_on_backup(
    database: FoundryDatabase,
    *,
    now: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="jarvis-v13-chief-verifier-late-stop-",
    ) as raw:
        root = Path(raw)
        clone_path = database.backup_to(root / "foundry.db")
        clone = FoundryDatabase(clone_path)
        _revoke_proof_veto(
            clone,
            nonce="verifier-late-stop-revoke-veto",
        )
        controls = OwnerControlService(clone, secret=SECRET)
        directive_id = "verifier-standing-blocked"
        text = "If a project becomes blocked, investigate it."
        draft = compile_standing_order(
            text,
            scope=ContextScope(project_ids=("verifier-project",)),
        )
        metadata = draft.as_metadata()
        controls.apply(
            "STANDING_ORDER",
            target="verifier-project",
            exact_text=text,
            nonce=directive_id,
            signature=sign_command(
                SECRET,
                "STANDING_ORDER",
                "verifier-project",
                text,
                directive_id,
                metadata=metadata,
            ),
            directive_id=directive_id,
            metadata=metadata,
        )
        stop_path = root / "EMERGENCY_STOP"
        guard_calls = {"count": 0}

        def inject(point: str) -> None:
            if point != "candidate_transaction_guard":
                return
            guard_calls["count"] += 1
            if guard_calls["count"] == 2:
                stop_path.write_text(
                    "independent verifier late stop\n",
                    encoding="utf-8",
                )

        candidates_before = _table_count(clone, "candidates")
        routes_before = _event_count(
            clone,
            "chief_of_staff.candidate_routed",
        )
        before = clone.event_count()
        result = ChiefOfStaffService(
            clone,
            stop_path=stop_path,
            fault_hook=inject,
        ).consider(
            {
                "change_id": "verifier-change-late-stop",
                "project_id": "verifier-project",
                "change_type": "BLOCKED",
                "summary": (
                    "The verifier project is blocked by a bounded failure"
                ),
                "occurred_at": "2026-09-04T11:00:00Z",
                "situation_state": "BLOCKED",
                "evidence_refs": ["verifier:evidence:late-stop"],
                "attributes": {
                    "proposed_objective": (
                        "Investigate the verifier transaction boundary"
                    ),
                    "expected_outcome": (
                        "No candidate crosses the late owner stop"
                    ),
                    "value": 0.95,
                    "strategic_value": 0.95,
                    "urgency": 0.30,
                    "confidence": 0.95,
                    "reversible": True,
                },
            },
            now=now,
        )
        return {
            "guard_calls": guard_calls["count"],
            "event_delta": clone.event_count() - before,
            "status": result.status,
            "result_standing_order_ids": list(result.standing_order_ids),
            "proposal_standing_order_ids": (
                list(result.proposal.standing_order_ids)
                if result.proposal is not None
                else []
            ),
            "candidates_before": candidates_before,
            "candidates_after": _table_count(clone, "candidates"),
            "routes_before": routes_before,
            "routes_after": _event_count(
                clone,
                "chief_of_staff.candidate_routed",
            ),
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
            "contract": "jarvis.v13.chief-of-staff-independent-verifier.v2",
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
    runtime_change_ids = sorted(
        change_id
        for change_id in observed
        if change_id.startswith("cos-source-")
    )
    runtime_change_id = (
        runtime_change_ids[0]
        if len(runtime_change_ids) == 1
        else ""
    )
    runtime_change_event = observed.get(runtime_change_id)
    runtime_change_source_refs = (
        set(
            runtime_change_event.payload["change"].get(
                "source_event_refs",
            ) or (),
        )
        if runtime_change_event is not None
        else set()
    )
    runtime_world_events = [
        event
        for event in database.events(
            event_types=("world.observation_recorded",),
        )
        if (
            isinstance(event.payload.get("provenance"), dict)
            and event.payload["provenance"].get("source_identity")
            == RUNTIME_OBSERVATION_IDENTITY
        )
    ]
    runtime_world_event_id = (
        runtime_world_events[0].event_id
        if len(runtime_world_events) == 1
        else ""
    )
    runtime_route_events = [
        event
        for event in outcomes.get(runtime_change_id, ())
        if event.event_type == "chief_of_staff.candidate_routed"
    ]
    runtime_candidate_id = (
        str(runtime_route_events[0].payload.get("candidate_id") or "")
        if len(runtime_route_events) == 1
        else ""
    )
    runtime_source_results: list[dict[str, Any]] = []
    runtime_cycle_ids: list[str] = []
    for event in database.events(
        event_types=("initiative.source_recorded",),
    ):
        payload = event.payload if isinstance(event.payload, dict) else {}
        for result in payload.get("chief_of_staff_results") or ():
            if (
                isinstance(result, dict)
                and result.get("change_id") == runtime_change_id
            ):
                runtime_source_results.append(dict(result))
                runtime_cycle_ids.append(str(payload.get("cycle_id") or ""))
    cycle_metrics_by_id = {
        str(event.payload.get("cycle_id") or ""): dict(
            event.payload.get("metrics") or {},
        )
        for event in database.events(
            event_types=("initiative.cycle_completed",),
        )
    }
    runtime_cycle_metrics = [
        cycle_metrics_by_id.get(cycle_id, {})
        for cycle_id in runtime_cycle_ids
    ]
    low_value_rejection_events = [
        event
        for event in outcomes.get(
            "change-operatoros-urgent-cosmetic",
            (),
        )
        if event.event_type
        == "chief_of_staff.recommendation_rejected"
    ]
    priority_cutoff_event_id = (
        low_value_rejection_events[0].event_id
        if len(low_value_rejection_events) == 1
        else ""
    )
    historical_projection = _projection_through_event(
        database,
        event_id=priority_cutoff_event_id,
        now=now,
    )
    historical_leadgen_priority = (
        _priority(historical_projection, "leadgen")
        if historical_projection
        else {}
    )
    historical_operator_priority = (
        _priority(historical_projection, "operatoros")
        if historical_projection
        else {}
    )
    replay = _replay_on_backup(
        database,
        observed=observed,
        runtime_change_id=runtime_change_id,
        now=now,
    )
    late_stop_probe = _late_stop_on_backup(database, now=now)

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
    dependency_wait_events = [
        event
        for event in outcomes.get("change-jarvis-dependency-wait", ())
        if event.event_type == "chief_of_staff.wait_recorded"
    ]
    semantic_dependency_wait_id = (
        str(dependency_wait_events[0].payload.get("wait_id") or "")
        if len(dependency_wait_events) == 1
        else ""
    )
    recorded_canonical_wake_id = (
        str(
            dependency_wait_events[0].payload.get(
                "canonical_wake_id",
            ) or "",
        )
        if len(dependency_wait_events) == 1
        else ""
    )
    quiet_wait_events = [
        event
        for event in outcomes.get("change-leadgen-recoverable", ())
        if event.event_type == "chief_of_staff.wait_recorded"
    ]
    quiet_wait_id = (
        str(quiet_wait_events[0].payload.get("wait_id") or "")
        if len(quiet_wait_events) == 1
        else ""
    )
    quiet_wait_resolutions = [
        event
        for event in outcomes.get(
            "change-leadgen-recoverable-resolved",
            (),
        )
        if (
            event.event_type == "chief_of_staff.item_state_changed"
            and event.payload.get("item_kind") == "WAIT"
            and event.payload.get("item_id") == quiet_wait_id
            and event.payload.get("to_state") == "RESOLVED"
        )
    ]
    temporary_attention_events = [
        event
        for event in outcomes.get(
            "change-operatoros-temporary-owner-decision",
            (),
        )
        if event.event_type
        == "chief_of_staff.owner_attention_required"
    ]
    temporary_attention_id = (
        str(
            temporary_attention_events[0].payload.get(
                "attention_id",
            ) or "",
        )
        if len(temporary_attention_events) == 1
        else ""
    )
    temporary_attention_resolutions = [
        event
        for event in outcomes.get(
            "change-operatoros-temporary-owner-decision-resolved",
            (),
        )
        if (
            event.event_type == "chief_of_staff.item_state_changed"
            and event.payload.get("item_kind") == "OWNER_ATTENTION"
            and event.payload.get("item_id") == temporary_attention_id
            and event.payload.get("to_state") == "RESOLVED"
        )
    ]
    final_attention_events = [
        event
        for event in outcomes.get("change-leadgen-owner-decision", ())
        if event.event_type
        == "chief_of_staff.owner_attention_required"
    ]
    final_attention_source_id = (
        final_attention_events[0].event_id
        if len(final_attention_events) == 1
        else ""
    )
    temporary_attention_source_id = (
        temporary_attention_events[0].event_id
        if len(temporary_attention_events) == 1
        else ""
    )
    open_queue_matches = [
        item
        for item in snapshot["decision_queue"]["open"]
        if item.get("source_event_id") == final_attention_source_id
    ]
    resolved_queue_matches = [
        item
        for item in snapshot["decision_queue"]["history"]
        if (
            item.get("source_event_id") == temporary_attention_source_id
            and item.get("status") == "RESOLVED"
        )
    ]
    observation_wakes = [
        wake
        for wake in wakes.values()
        if wake.get("objective_id") == "system:continuous-observation"
    ]
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
        "item_state_changes": _event_count(
            database, "chief_of_staff.item_state_changed",
        ),
        "world_observations": _table_count(
            database,
            "world_observations",
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
        "decision_queue_open_matches": len(open_queue_matches),
        "decision_queue_resolved_matches": len(
            resolved_queue_matches,
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
        "change-leadgen-recoverable-resolved": [],
        "change-operatoros-dirty": [OPERATOR_ORDER],
        "change-operatoros-urgent-cosmetic": [],
        "change-operatoros-after-revoke": [],
        "change-leadgen-adjacent-opportunity": [],
        "change-operatoros-temporary-owner-decision": [],
        "change-operatoros-temporary-owner-decision-resolved": [],
        "change-operatoros-intentional-idle": [],
        "change-leadgen-owner-decision": [],
        "change-jarvis-late-transaction-stop": [],
        runtime_change_id: [JARVIS_ORDER],
    }
    historical_order_matches = {
        change_id: _payload_standing_orders(outcomes.get(change_id, []))
        for change_id in expected_standing_orders
        if change_id
    }
    false_trigger_names = [
        change_id
        for change_id, expected in expected_standing_orders.items()
        if (
            not change_id
            or historical_order_matches.get(change_id) != expected
        )
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
    proof_inputs = (
        proof.get("inputs") if isinstance(proof.get("inputs"), dict) else {}
    )
    checks = {
        "database_exists": True,
        "proof_contract_and_scope": (
            proof.get("contract") == "jarvis.v13.chief-of-staff-proof.v2"
            and proof.get("scope")
            == "DISPOSABLE_SCHEMA_V40_DATABASE_ONLY"
            and proof.get("live_mutated") is False
            and proof.get("main_or_origin_mutated") is False
        ),
        "mission_inputs_are_exact": (
            proof_inputs.get("v12_final_candidate")
            == V12_FINAL_CANDIDATE
            and proof_inputs.get("v12_source") == BASE_SHA
            and proof_inputs.get("owner_context_final")
            == OWNER_CONTEXT_FINAL
            and proof_inputs.get("owner_context_source")
            == OWNER_CONTEXT_SOURCE
        ),
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
            bool(historical_leadgen_priority)
            and bool(historical_operator_priority)
            and historical_leadgen_priority["score"]
            > historical_operator_priority["score"]
            and historical_leadgen_priority["factors"][
                "strategic_value"
            ]["normalized_value"]
            > historical_operator_priority["factors"][
                "strategic_value"
            ]["normalized_value"]
            and historical_operator_priority["factors"]["urgency"][
                "normalized_value"
            ] == 1.0
            and len(low_value_rejection_events) == 1
        ),
        "standing_order_history_is_exact": (
            not false_trigger_names
            and {JARVIS_ORDER, LEADGEN_ORDER}.issubset(active_order_ids)
            and OPERATOR_ORDER not in active_order_ids
        ),
        "observed_change_set_is_exact": (
            len(runtime_change_ids) == 1
            and set(observed)
            == EXPECTED_DIRECT_CHANGE_IDS | {runtime_change_id}
            and "change-jarvis-stop" not in observed
            and "change-jarvis-veto" not in observed
        ),
        "quiet_recovery_resolves_without_attention": (
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
            and len(quiet_wait_events) == 1
            and not quiet_wait_events[0].payload.get("canonical_wake_id")
            and len(quiet_wait_resolutions) == 1
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
        "owner_attention_open_and_resolved_lifecycle": (
            recomputed_counts["owner_attention"] == 2
            and recomputed_counts["item_state_changes"] == 2
            and len(temporary_attention_events) == 1
            and len(temporary_attention_resolutions) == 1
            and len(final_attention_events) == 1
            and recomputed_counts["decision_queue_open_matches"] == 1
            and recomputed_counts["decision_queue_resolved_matches"] == 1
            and leadgen["owner_decision_needed"] is True
        ),
        "runtime_observation_routes_one_candidate_idempotently": (
            len(runtime_world_events) == 1
            and runtime_world_event_id in runtime_change_source_refs
            and len(runtime_route_events) == 1
            and len(runtime_source_results) == 2
            and len(runtime_cycle_metrics) == 2
            and runtime_source_results[0].get("status")
            == "ROUTED_TO_EXISTING_CANDIDATE_PATH"
            and runtime_source_results[0].get("duplicate") is False
            and runtime_source_results[0].get("candidate_id")
            == runtime_candidate_id
            and runtime_source_results[1].get("candidate_id")
            == runtime_candidate_id
            and runtime_source_results[1].get("duplicate") is True
            and runtime_cycle_metrics[0].get("raw_candidates_generated") == 1
            and runtime_cycle_metrics[0].get("unique_candidates") == 1
            and runtime_cycle_metrics[1].get("raw_candidates_generated") == 1
            and runtime_cycle_metrics[1].get("unique_candidates") == 0
            and recomputed_counts["world_observations"] == 1
            and recomputed_counts["candidates"] == 1
            and recomputed_counts["candidate_routed"] == 1
            and replay["event_delta"] == 0
            and replay["candidate_count"] == 1
            and replay["duplicate"] is True
        ),
        "wait_wake_and_runtime_continuation": (
            len(dependency_wait_events) == 1
            and semantic_dependency_wait_id
            and semantic_dependency_wait_id != canonical_wake_id
            and recorded_canonical_wake_id == canonical_wake_id
            and canonical_wake_id in wakes
            and wakes[canonical_wake_id]["status"] == "TRIGGERED"
            and len(observation_wakes) == 3
            and sum(
                wake.get("status") == "TRIGGERED"
                for wake in observation_wakes
            ) == 2
            and sum(
                wake.get("status") == "ACTIVE"
                for wake in observation_wakes
            ) == 1
            and recomputed_counts["wake_created"] == 4
            and recomputed_counts["wake_triggered"] == 3
            and len(runtime_route_events) == 1
        ),
        "all_priority_factors_and_unknowns_are_explicit": (
            set(priority_jarvis["factors"]) == PRIORITY_FACTOR_NAMES
            and priority_jarvis["unknown_factors"] == []
            and priority_jarvis["factors"]["cost"]["raw_value"] == 0.20
            and priority_jarvis["factors"]["cost"][
                "normalized_value"
            ] == 0.80
            and priority_jarvis["factors"]["opportunity_cost"][
                "raw_value"
            ] == 0.15
            and priority_jarvis["factors"]["opportunity_cost"][
                "normalized_value"
            ] == 0.85
            and set(priority_leadgen["factors"]) == PRIORITY_FACTOR_NAMES
            and bool(priority_leadgen["unknown_factors"])
            and all(
                priority_leadgen["factors"][name]["raw_value"]
                == "UNKNOWN"
                for name in priority_leadgen["unknown_factors"]
            )
            and set(priority_operator["factors"]) == PRIORITY_FACTOR_NAMES
        ),
        "late_owner_stop_blocks_final_candidate_insert": (
            late_stop_probe["guard_calls"] == 2
            and late_stop_probe["status"] == "BLOCKED_BY_OWNER_CONTROL"
            and late_stop_probe["result_standing_order_ids"] == []
            and late_stop_probe["proposal_standing_order_ids"]
            == ["directive:verifier-standing-blocked"]
            and late_stop_probe["candidates_before"]
            == late_stop_probe["candidates_after"]
            and late_stop_probe["routes_before"]
            == late_stop_probe["routes_after"]
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
        "contract": "jarvis.v13.chief-of-staff-independent-verifier.v2",
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
        "late_stop_on_backup": late_stop_probe,
        "runtime_change_id": runtime_change_id,
        "runtime_cycle_ids": runtime_cycle_ids,
        "priority_cutoff_event_id": priority_cutoff_event_id,
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
