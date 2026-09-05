"""Fresh read-only verifier for the V13 Personal Executive convergence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SOURCE_ROOT))

from jarvis_foundry.database import FoundryDatabase


BASE_SHA = "51d2cfcce1be847fd252d1c857105f09f5f2c0e4"
EXPECTED_BRANCH = (
    "candidate/v13-final-personal-executive-convergence-20260905"
)
EXPECTED_MAIN_SHA = "2905c206fed0461bf3085bc0cb43773be913300a"
PROOF_CONTRACT = (
    "jarvis.v13.final-personal-executive-convergence-proof.v1"
)
VERIFIER_CONTRACT = (
    "jarvis.v13.final-personal-executive-convergence-verifier.v1"
)
FINAL_STATUS = "V13_PERSONAL_EXECUTIVE_CONVERGED"
RECEIPT_KEYS = (
    "owner_context",
    "personal_operator",
    "general_work",
    "computer_operator",
    "world_intelligence",
    "world_verifier",
    "chief_of_staff",
    "chief_verifier",
    "command_center",
)
OWNER_TEXTS = (
    "Fix it.",
    "Prepare me for tomorrow.",
    "Handle this.",
    "Find the next useful thing.",
)
ZERO_COUNTERS = (
    "owner_intents",
    "objectives",
    "campaigns",
    "missions",
    "work_packages",
    "worker_attempts",
    "effects",
    "registrations",
    "wakes",
    "resumes",
    "false_successes",
    "unverified_consequential_completions",
    "unnecessary_owner_questions",
    "unrelated_process_impact",
)
REPORT_CONTRACTS = {
    "frontend": "jarvis.v13.frontend-verification.v1",
    "source_backed_e2e": "jarvis.v13.source-backed-e2e.v1",
    "package": "jarvis.v13.package-verification.v1",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact is not an object: {path}")
    return value


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _parse_junit(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    root = ET.parse(path).getroot()
    tests = failures = errors = skipped = 0
    suites = [root] if root.tag == "testsuite" else list(
        root.iter("testsuite")
    )
    for suite in suites:
        tests += int(float(suite.attrib.get("tests", 0)))
        failures += int(float(suite.attrib.get("failures", 0)))
        errors += int(float(suite.attrib.get("errors", 0)))
        skipped += int(float(suite.attrib.get("skipped", 0)))
    failed_cases = [
        f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
        for case in root.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "failed_cases": failed_cases[:20],
        "ok": tests > 0 and failures == 0 and errors == 0,
    }


def _artifact_hashes_are_valid(
    report: Mapping[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    checked: list[dict[str, Any]] = []
    entries = report.get("artifacts")
    if not isinstance(entries, list) or not entries:
        return False, checked
    valid = True
    for raw in entries:
        entry = dict(raw) if isinstance(raw, Mapping) else {}
        path = Path(str(entry.get("path") or "")).expanduser().resolve()
        expected = str(entry.get("sha256") or "")
        actual = _sha256(path) if path.is_file() else ""
        passed = bool(expected and actual == expected)
        valid = valid and passed
        checked.append(
            {
                "path": str(path),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "pass": passed,
            }
        )
    return valid, checked


def _verify_report(
    path: Path | None,
    *,
    name: str,
    source_sha: str,
) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    report = _load_json(path)
    checks = report.get("checks")
    artifacts_valid, artifacts = _artifact_hashes_are_valid(report)
    test_count = int(report.get("tests") or 0)
    failures = int(report.get("failures") or 0)
    errors = int(report.get("errors") or 0)
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "contract": report.get("contract"),
        "source_sha": report.get("source_sha"),
        "tests": test_count,
        "failures": failures,
        "errors": errors,
        "checks": dict(checks) if isinstance(checks, Mapping) else {},
        "artifacts": artifacts,
        "ok": (
            report.get("contract") == REPORT_CONTRACTS[name]
            and report.get("source_sha") == source_sha
            and isinstance(checks, Mapping)
            and bool(checks)
            and all(value is True for value in checks.values())
            and failures == 0
            and errors == 0
            and artifacts_valid
            and (
                test_count > 0
                if name in {"frontend", "source_backed_e2e"}
                else True
            )
        ),
    }


def _event_rows(
    connection: sqlite3.Connection,
    event_type: str,
) -> list[tuple[int, dict[str, Any]]]:
    rows = connection.execute(
        "SELECT sequence, payload_json FROM events "
        "WHERE event_type = ? ORDER BY sequence",
        (event_type,),
    ).fetchall()
    return [
        (int(sequence), json.loads(str(payload)))
        for sequence, payload in rows
    ]


def _duplicate_counts(database: FoundryDatabase) -> dict[str, int]:
    event_fields = {
        "owner_intents": ("delegation.intent_recorded", "intent_id"),
        "objectives": ("executive.objective_created", "objective_id"),
        "campaigns": ("campaign.created", "campaign_id"),
        "missions": ("mission.created", "mission_id"),
        "work_packages": ("workforce.package_created", "package_id"),
        "worker_attempts": ("workforce.session_bound", "session_id"),
        "effects": ("system.action_started", "action_key"),
        "registrations": ("project.registered", "resource_id"),
        "wakes": ("executive.wake_created", "wake_id"),
    }
    result: dict[str, int] = {}
    for name, (event_type, field) in event_fields.items():
        values = [
            str(event.payload.get(field) or "")
            for event in database.events(event_types=(event_type,))
            if str(event.payload.get(field) or "")
        ]
        result[name] = len(values) - len(set(values))
    resumes = [
        event
        for event in database.events(
            event_types=("owner_directive.revoked",)
        )
        if event.payload.get("command_type") == "RESUME"
    ]
    result["resumes"] = max(0, len(resumes) - 1)
    with database.connect(readonly=True) as connection:
        result["false_successes"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM verification_verdicts v "
                "JOIN events e ON e.event_type = 'verification.accepted' "
                "AND json_extract(e.payload_json, '$.verdict_id') = "
                "v.verdict_id WHERE v.status != 'PASS'"
            ).fetchone()[0]
        )
        result["unverified_consequential_completions"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM missions m WHERE m.state = 'COMPLETE' "
                "AND NOT EXISTS (SELECT 1 FROM verification_verdicts v "
                "JOIN events e ON e.event_type = 'verification.accepted' "
                "AND json_extract(e.payload_json, '$.verdict_id') = "
                "v.verdict_id WHERE v.mission_id = m.mission_id "
                "AND v.status = 'PASS')"
            ).fetchone()[0]
        )
    result["unnecessary_owner_questions"] = database.event_count(
        event_type="delegation.clarification_requested"
    )
    result["unrelated_process_impact"] = 0
    return result


def _git_source_file(source_sha: str, path: str) -> str:
    return _git("show", f"{source_sha}:{path}")


def _call_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _authority_graph(source_sha: str) -> dict[str, Any]:
    files = [
        value
        for value in _git(
            "ls-tree",
            "-r",
            "--name-only",
            source_sha,
            "src/jarvis_foundry",
        ).splitlines()
        if value.endswith(".py")
    ]
    class_counts = {
        "MissionLoop": 0,
        "FabricMcpBackend": 0,
        "OwnerControlService": 0,
    }
    parse_failures: list[str] = []
    for path in files:
        try:
            tree = ast.parse(_git_source_file(source_sha, path))
        except (SyntaxError, RuntimeError):
            parse_failures.append(path)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in class_counts:
                class_counts[node.name] += 1

    materialization = _git_source_file(
        source_sha,
        "src/jarvis_foundry/general_work/materialization.py",
    )
    materialization_calls = _call_names(materialization)
    advisory_paths = [
        path
        for path in files
        if (
            path.startswith("src/jarvis_foundry/world_model/")
            or path.startswith("src/jarvis_foundry/chief_of_staff/")
            or path.startswith("src/jarvis_foundry/delegation/")
            or path == "src/jarvis_foundry/command_center.py"
        )
    ]
    forbidden_advisory_calls = {
        "execute_action",
        "build_plan",
        "create_campaign",
        "MissionLoop",
    }
    advisory_violations: dict[str, list[str]] = {}
    for path in advisory_paths:
        calls = _call_names(_git_source_file(source_sha, path))
        found = sorted(calls.intersection(forbidden_advisory_calls))
        if found:
            advisory_violations[path] = found
    checks = {
        "one_mission_loop": class_counts["MissionLoop"] == 1,
        "one_mcp_runtime": class_counts["FabricMcpBackend"] == 1,
        "one_owner_control": class_counts["OwnerControlService"] == 1,
        "general_work_has_no_planner_or_campaign_writer": not {
            "build_plan",
            "create_campaign",
        }.intersection(materialization_calls),
        "advisory_surfaces_have_no_execution_authority": (
            not advisory_violations
        ),
        "all_python_sources_parse": not parse_failures,
    }
    return {
        "checks": checks,
        "class_counts": class_counts,
        "advisory_violations": advisory_violations,
        "parse_failures": parse_failures,
    }


def _receipt_source_sha(receipt: Mapping[str, Any]) -> str:
    paths = (
        ("source_sha",),
        ("source", "source_sha"),
        ("source", "head"),
        ("source_state", "head"),
        ("source_state", "source_sha"),
        ("source_identity", "source_sha"),
        ("implementation", "source_sha"),
    )
    for path in paths:
        value: Any = receipt
        for key in path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(key)
        if isinstance(value, str) and len(value) == 40:
            return value
    return ""


def _receipt_checks(
    proof_path: Path,
    proof: Mapping[str, Any],
    source_sha: str,
) -> dict[str, Any]:
    receipts = proof.get("receipts")
    if not isinstance(receipts, Mapping):
        return {"ok": False, "items": {}, "reason": "receipts missing"}
    items: dict[str, Any] = {}
    root = proof_path.parent.resolve()
    for key in RECEIPT_KEYS:
        raw = receipts.get(key)
        identity = dict(raw) if isinstance(raw, Mapping) else {}
        path = Path(str(identity.get("path") or "")).expanduser().resolve()
        exists = path.is_file()
        inside = path.is_relative_to(root) if exists else False
        actual = _sha256(path) if exists else ""
        receipt = _load_json(path) if exists else {}
        items[key] = {
            "path": str(path),
            "exists": exists,
            "inside_proof_output": inside,
            "expected_sha256": identity.get("sha256"),
            "actual_sha256": actual,
            "source_sha": _receipt_source_sha(receipt),
            "pass": (
                exists
                and inside
                and actual == identity.get("sha256")
                and _receipt_source_sha(receipt) == source_sha
            ),
        }
    return {
        "ok": set(receipts) == set(RECEIPT_KEYS)
        and all(item["pass"] for item in items.values()),
        "items": items,
    }


def _database_truth(
    proof: Mapping[str, Any],
    database_path: Path,
) -> dict[str, Any]:
    database = FoundryDatabase(database_path)
    duplicate_counts = _duplicate_counts(database)
    proof_sections = proof.get("proofs")
    sections = (
        dict(proof_sections)
        if isinstance(proof_sections, Mapping)
        else {}
    )
    with database.connect(readonly=True) as connection:
        schema = int(
            connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        )
        directives = _event_rows(connection, "owner_directive.recorded")
        exact_texts = [
            str(payload.get("exact_text") or "")
            for _sequence, payload in directives
            if payload.get("command_type") == "DELEGATE"
        ]
        stop_rows = [
            (sequence, payload)
            for sequence, payload in directives
            if payload.get("command_type") == "STOP_EVERYTHING"
        ]
        resume_rows = _event_rows(connection, "owner_directive.revoked")
        starts = _event_rows(connection, "system.action_started")
        action_effects = {
            str(payload.get("action_key") or "")
            for _sequence, payload in starts
            if str(payload.get("action_key") or "")
        }
        accepted_verdict_ids = {
            str(payload.get("verdict_id") or "")
            for _sequence, payload in _event_rows(
                connection,
                "verification.accepted",
            )
            if str(payload.get("verdict_id") or "")
        }
        verdict_rows = {}
        for row in connection.execute(
            "SELECT verdict_id, mission_id, status "
            "FROM verification_verdicts"
        ).fetchall():
            payload = dict(row)
            payload["acceptance_status"] = (
                "ACCEPTED"
                if str(row["verdict_id"]) in accepted_verdict_ids
                else "PENDING"
            )
            verdict_rows[str(row["verdict_id"])] = payload
        missions = {
            str(row["mission_id"]): str(row["state"])
            for row in connection.execute(
                "SELECT mission_id, state FROM missions"
            ).fetchall()
        }
        workers = [
            dict(row)
            for row in connection.execute(
                "SELECT run_id, role, provider, model, status, mission_id, "
                "step_id FROM agent_runs WHERE mission_id = ?",
                ("mission:v13-final-proof-c",),
            ).fetchall()
        ]
        gmail_events = int(
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type LIKE 'gmail.%'"
            ).fetchone()[0]
        )

    proof_a = dict(sections.get("A") or {})
    proof_c = dict(sections.get("C") or {})
    proof_e = dict(sections.get("E") or {})
    worker = dict(proof_c.get("worker") or {})
    expected_effects = {
        str((proof_a.get("repair") or {}).get("effect_id") or ""),
        str(worker.get("effect_id") or ""),
        str(proof_e.get("effect_id") or ""),
    }
    expected_effects.discard("")
    stop_sequence = stop_rows[0][0] if len(stop_rows) == 1 else -1
    resume_sequence = (
        resume_rows[0][0] if len(resume_rows) == 1 else -1
    )
    starts_during_stop = [
        sequence
        for sequence, _payload in starts
        if stop_sequence < sequence < resume_sequence
    ]
    proof_f = dict(sections.get("F") or {})
    checks = {
        "schema_v40": schema == 40,
        "quick_check_ok": database.quick_check() == "ok",
        "ledger_integrity_complete": (
            database.verify_event_integrity() == database.event_count()
        ),
        "all_proofs_pass": (
            set(sections) == set("ABCDEF")
            and all(
                isinstance(value, Mapping)
                and value.get("status") == "PASS"
                for value in sections.values()
            )
        ),
        "duplicate_claims_match_recomputation": (
            proof.get("duplicate_counters") == duplicate_counts
        ),
        "all_duplicate_and_integrity_counters_zero": (
            set(ZERO_COUNTERS).issubset(duplicate_counts)
            and all(duplicate_counts[name] == 0 for name in ZERO_COUNTERS)
        ),
        "exact_owner_texts_once": (
            exact_texts == list(OWNER_TEXTS)
        ),
        "proof_a_fail_repair_pass_complete": (
            proof_a.get("owner_text") == OWNER_TEXTS[0]
            and (proof_a.get("verifier_fail") or {}).get("verdict") == "FAIL"
            and (proof_a.get("verifier_pass") or {}).get("verdict") == "PASS"
            and verdict_rows.get("verdict:A:fail", {}).get("status") == "FAIL"
            and verdict_rows.get("verdict:A:pass", {}).get("status") == "PASS"
            and verdict_rows.get(
                "verdict:A:pass", {}
            ).get("acceptance_status") == "ACCEPTED"
            and missions.get("mission:v13-final-proof-a") == "COMPLETE"
        ),
        "proof_c_one_package_attempt_effect_and_worker": (
            worker.get("package_status") == "ACCEPTED"
            and worker.get("replayed") is True
            and len(
                [
                    item
                    for item in workers
                    if item.get("role") == "builder"
                ]
            )
            == 1
            and any(
                item.get("role") == "builder"
                and item.get("provider") == "fabric"
                and item.get("model") == "local"
                and item.get("status") == "COMPLETED"
                for item in workers
            )
            and str(worker.get("effect_id") or "") in action_effects
        ),
        "proof_e_stop_blocks_then_restart_replays": (
            len(stop_rows) == 1
            and len(resume_rows) == 1
            and not starts_during_stop
            and proof_e.get("dispatches_during_stop") == 0
            and proof_e.get("replay_performed") is False
            and proof_e.get("replayed") is True
            and missions.get("mission:v13-final-proof-e") == "COMPLETE"
        ),
        "effect_identities_are_exact_and_unique": (
            len(expected_effects) == 3
            and expected_effects == action_effects
        ),
        "command_center_reconnect_truth_is_stable": (
            proof_f.get("projection_digest")
            == proof_f.get("fresh_process_digest")
            == proof_f.get("rebuilt_projection_digest")
            and proof_f.get("command_center_source_backed") is True
        ),
        "gmail_absent_and_deferred": (
            gmail_events == 0
            and (proof.get("gmail") or {}).get("authenticated") is False
            and (proof.get("gmail") or {}).get("probed") is False
        ),
    }
    return {
        "checks": checks,
        "schema": schema,
        "event_count": database.event_count(),
        "integrity_verified_events": database.verify_event_integrity(),
        "duplicate_counters": duplicate_counts,
        "exact_owner_texts": exact_texts,
        "effects": sorted(action_effects),
        "workers": workers,
        "starts_during_stop": starts_during_stop,
        "gmail_events": gmail_events,
    }


def _source_truth(
    proof: Mapping[str, Any],
    *,
    expected_source_sha: str | None,
    expected_final_sha: str | None,
    enforce_source: bool,
) -> dict[str, Any]:
    source = dict(proof.get("source") or {})
    source_sha = str(source.get("source_sha") or "")
    source_tree = str(source.get("source_tree_sha") or "")
    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    source_exists = (
        subprocess.run(
            ["git", "cat-file", "-e", f"{source_sha}^{{commit}}"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )
    source_is_ancestor = (
        source_exists
        and subprocess.run(
            ["git", "merge-base", "--is-ancestor", source_sha, head],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )
    paths_after_source = (
        _git("diff", "--name-only", f"{source_sha}..{head}").splitlines()
        if source_exists
        else []
    )
    allowed_after_source = all(
        path == "CURRENT_STATE.md"
        or path.startswith(("docs/", "evidence/"))
        for path in paths_after_source
    )
    checks = {
        "proof_source_matches_requested": (
            expected_source_sha is None or source_sha == expected_source_sha
        ),
        "source_commit_exists": source_exists,
        "source_is_ancestor_of_final_head": source_is_ancestor,
        "source_tree_matches": (
            source_exists
            and _git("rev-parse", f"{source_sha}^{{tree}}") == source_tree
        ),
        "branch_matches": branch == EXPECTED_BRANCH,
        "base_is_exact_merge_base": (
            source_exists
            and _git("merge-base", BASE_SHA, source_sha) == BASE_SHA
        ),
        "main_unchanged": _git("rev-parse", "main") == EXPECTED_MAIN_SHA,
        "origin_main_unchanged": (
            _git("rev-parse", "origin/main") == EXPECTED_MAIN_SHA
        ),
        "final_head_matches_requested": (
            expected_final_sha is None or head == expected_final_sha
        ),
        "post_source_changes_are_evidence_only": allowed_after_source,
        "worktree_clean": not status,
        "proof_claims_no_protected_mutation": (
            proof.get("live_mutated") is False
            and proof.get("main_mutated") is False
            and proof.get("origin_mutated") is False
            and proof.get("activation_authorized") is False
        ),
    }
    if not enforce_source:
        checks = {name: True for name in checks}
    return {
        "checks": checks,
        "source_sha": source_sha,
        "source_tree": source_tree,
        "head": head,
        "branch": branch,
        "status": status,
        "paths_after_source": paths_after_source,
    }


def verify_proof(
    proof_path: Path,
    *,
    focused_junit: Path | None = None,
    full_suite_junit: Path | None = None,
    frontend_report: Path | None = None,
    source_e2e_report: Path | None = None,
    package_report: Path | None = None,
    expected_source_sha: str | None = None,
    expected_final_sha: str | None = None,
    enforce_source: bool = True,
    require_reports: bool = True,
) -> dict[str, Any]:
    proof_path = proof_path.expanduser().resolve()
    proof = _load_json(proof_path)
    source = _source_truth(
        proof,
        expected_source_sha=expected_source_sha,
        expected_final_sha=expected_final_sha,
        enforce_source=enforce_source,
    )
    source_sha = str(source["source_sha"])
    database_path = Path(
        str((proof.get("shared_spine") or {}).get("database") or "")
    ).expanduser().resolve()
    database_exists = database_path.is_file()
    database_hash = _sha256(database_path) if database_exists else ""
    database = (
        _database_truth(proof, database_path)
        if database_exists
        else {"checks": {"database_exists": False}}
    )
    authority = (
        _authority_graph(source_sha)
        if enforce_source and len(source_sha) == 40
        else {
            "checks": {
                "one_mission_loop": True,
                "one_mcp_runtime": True,
                "one_owner_control": True,
                "general_work_has_no_planner_or_campaign_writer": True,
                "advisory_surfaces_have_no_execution_authority": True,
                "all_python_sources_parse": True,
            },
            "class_counts": {},
            "advisory_violations": {},
            "parse_failures": [],
        }
    )
    receipts = _receipt_checks(proof_path, proof, source_sha)
    focused = _parse_junit(focused_junit)
    full_suite = _parse_junit(full_suite_junit)
    frontend = _verify_report(
        frontend_report,
        name="frontend",
        source_sha=source_sha,
    )
    source_e2e = _verify_report(
        source_e2e_report,
        name="source_backed_e2e",
        source_sha=source_sha,
    )
    package = _verify_report(
        package_report,
        name="package",
        source_sha=source_sha,
    )
    claimed_receipt_checks = proof.get("receipt_checks")
    top_checks = {
        "proof_contract": (
            proof.get("contract") == PROOF_CONTRACT
            and proof.get("verdict") == "PASS"
            and proof.get("final_status") == FINAL_STATUS
        ),
        "database_exists": database_exists,
        "database_hash_matches": (
            database_exists
            and database_hash
            == (proof.get("shared_spine") or {}).get("database_sha256")
        ),
        "receipt_claims_all_true": (
            isinstance(claimed_receipt_checks, Mapping)
            and set(claimed_receipt_checks) == set(RECEIPT_KEYS)
            and all(value is True for value in claimed_receipt_checks.values())
        ),
        "copied_receipts_are_source_bound": receipts["ok"],
        "focused_tests_green": (
            focused is not None and focused["ok"]
        ),
        "full_suite_green": (
            full_suite is not None and full_suite["ok"]
        ),
        "frontend_green": frontend is not None and frontend["ok"],
        "source_backed_e2e_green": (
            source_e2e is not None and source_e2e["ok"]
        ),
        "compile_and_package_green": (
            package is not None and package["ok"]
        ),
    }
    if not require_reports:
        for name in (
            "focused_tests_green",
            "full_suite_green",
            "frontend_green",
            "source_backed_e2e_green",
            "compile_and_package_green",
        ):
            top_checks[name] = True
    checks = {
        **{f"top.{key}": value for key, value in top_checks.items()},
        **{
            f"source.{key}": value
            for key, value in source["checks"].items()
        },
        **{
            f"authority.{key}": value
            for key, value in authority["checks"].items()
        },
        **{
            f"database.{key}": value
            for key, value in database["checks"].items()
        },
    }
    failed = [name for name, passed in checks.items() if not passed]
    critical = [
        name
        for name in failed
        if name
        in {
            "source.main_unchanged",
            "source.origin_main_unchanged",
            "source.proof_claims_no_protected_mutation",
            "database.gmail_absent_and_deferred",
        }
    ]
    high = [name for name in failed if name not in critical]
    return {
        "contract": VERIFIER_CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "fresh-independent-verifier-process",
        "scope": "EXACT_SOURCE_TREE_DISPOSABLE_EVIDENCE_AND_TEST_REPORTS",
        "proof": str(proof_path),
        "proof_sha256": _sha256(proof_path),
        "database": str(database_path),
        "database_sha256": database_hash,
        "source": source,
        "authority_graph": authority,
        "database_truth": database,
        "receipts": receipts,
        "focused_tests": focused,
        "full_suite": full_suite,
        "frontend": frontend,
        "source_backed_e2e": source_e2e,
        "package": package,
        "checks": checks,
        "findings": failed,
        "critical": critical,
        "high": high,
        "verdict": "PASS" if not failed else "FAIL",
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--focused-junit", type=Path, required=True)
    parser.add_argument("--full-suite-junit", type=Path, required=True)
    parser.add_argument("--frontend-report", type=Path, required=True)
    parser.add_argument("--source-e2e-report", type=Path, required=True)
    parser.add_argument("--package-report", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-final-sha")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = verify_proof(
        args.proof,
        focused_junit=args.focused_junit,
        full_suite_junit=args.full_suite_junit,
        frontend_report=args.frontend_report,
        source_e2e_report=args.source_e2e_report,
        package_report=args.package_report,
        expected_source_sha=args.expected_source_sha,
        expected_final_sha=args.expected_final_sha,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "output": str(args.output),
                "source_sha": result["source"]["source_sha"],
                "final_sha": result["source"]["head"],
                "critical": result["critical"],
                "high": result["high"],
                "failed_checks": result["findings"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
