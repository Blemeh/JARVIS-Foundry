"""Run the isolated JARVIS V3 hard gate and write auditable evidence.

This runner never opens a production database, pushes a branch, or contacts a
provider. Every test uses pytest fixtures and temporary lab state. A mandatory
criterion is only PASS when the evidence is both fresh and sufficient for the
criterion's documented rule; a green focused suite does not upgrade a missing
fault, soak, or end-to-end outcome proof.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BASE = "ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2"
FOCUSED_TESTS = [
    "tests/foundry/test_v3_fixtures.py",
    "tests/foundry/test_v3_projections.py",
    "tests/foundry/test_v3_scoring.py",
    "tests/foundry/test_v3_discovery_cycle.py",
    "tests/foundry/test_v3_loop.py",
    "tests/foundry/test_v3_teams.py",
    "tests/foundry/test_v3_git_reconcile.py",
    "tests/foundry/test_v3_outcomes.py",
    "tests/foundry/test_v3_smoke.py",
    "tests/foundry/test_v3_hard_core.py",
    "tests/foundry/test_knowledge_retrieval.py",
    "tests/foundry/test_role_manifest.py",
    "tests/foundry/test_executive_intelligence.py",
]
MIGRATION_TESTS = ["tests/foundry/test_knowledge_v35_durability.py"]
SUPPORTING_TESTS = [
    "tests/foundry/test_verifier_binding_hardening.py",
    "tests/foundry/test_role_gateway_kill.py",
    "tests/foundry/test_role_reaper.py",
    "tests/foundry/test_worker_gate.py",
]


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _junit_counts(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"collected": 0, "passed": 0, "failed": 0,
                "errors": 0, "skipped": 0, "failures": []}
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failures: list[dict[str, str]] = []
    passed = failed = errors = skipped = 0
    for case in cases:
        children = list(case)
        if any(child.tag == "error" for child in children):
            errors += 1
        elif any(child.tag == "failure" for child in children):
            failed += 1
            failure = next(child for child in children if child.tag == "failure")
            failures.append({
                "classname": case.attrib.get("classname", ""),
                "name": case.attrib.get("name", ""),
                "message": failure.attrib.get("message", ""),
            })
        elif any(child.tag == "skipped" for child in children):
            skipped += 1
        else:
            passed += 1
    return {"collected": len(cases), "passed": passed, "failed": failed,
            "errors": errors, "skipped": skipped, "failures": failures}


def _run_pytest(name: str, tests: list[str], evidence_dir: Path) -> dict[str, Any]:
    junit = evidence_dir / f"{name}.junit.xml"
    started = time.time()
    command = [sys.executable, "-m", "pytest", *tests, "-q",
               f"--junitxml={junit}"]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(command, cwd=ROOT, env=env,
                            capture_output=True, text=True)
    counts = _junit_counts(junit)
    payload = {
        "name": name,
        "command": command,
        "exit_code": result.returncode,
        "duration_seconds": round(time.time() - started, 3),
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
        "junit": str(junit),
        **counts,
    }
    _write(evidence_dir / f"{name}.run.json", payload)
    return payload


def _reuse_junit(name: str, junit: Path) -> dict[str, Any]:
    counts = _junit_counts(junit)
    payload = {
        "name": name,
        "command": "reused existing JUnit result",
        "exit_code": 0 if counts["failed"] == 0 and counts["errors"] == 0 else 1,
        "duration_seconds": None,
        "junit": str(junit),
        **counts,
    }
    return payload


def _default_off_probe() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tests" / "foundry"))
    from jarvis_foundry.runtime.config import WorkerRuntimeConfig
    config = WorkerRuntimeConfig()
    return {
        "status": "PASS" if config.discovery_cycle_enabled is False else "FAIL",
        "discovery_cycle_enabled": config.discovery_cycle_enabled,
        "reason": "runtime default remains disabled",
    }


def _schema_probe() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "src"))
    from jarvis_foundry.migrations import SCHEMA_VERSION
    return {
        "status": "PASS" if SCHEMA_VERSION == 36 else "FAIL",
        "schema_version": SCHEMA_VERSION,
        "expected": 36,
    }


def _bounded_soak(evidence_dir: Path, cycles: int = 8) -> dict[str, Any]:
    """Exercise the disposable idle cycle without model/provider calls."""
    sys.path.insert(0, str(ROOT / "src"))
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.v3_cycle import run_discovery_cycle
    from v3_fixtures import load_policy
    from jarvis_foundry.v3_scoring import register_policy

    root = evidence_dir / "soak-lab"
    root.mkdir(parents=True, exist_ok=True)
    database = FoundryDatabase(root / "soak.db")
    database.initialize()
    register_policy(database, load_policy())
    started = time.time()
    reports: list[dict[str, Any]] = []
    for index in range(cycles):
        report = run_discovery_cycle(
            database, loop_id=f"hard-soak-{index}",
            observe=lambda _db: {"observed": {}, "candidates_created": 0},
        )
        reports.append(report.to_payload())
        time.sleep(0.05)
    duration = round(time.time() - started, 3)
    payload = {
        "status": "PASS" if len(reports) == cycles and all(
            item["outcome"] == "WAITING" for item in reports) else "FAIL",
        "cycles": len(reports),
        "target_cycles": cycles,
        "duration_seconds": duration,
        "long_soak_target_seconds": 4 * 60 * 60,
        "long_soak_status": "NOT_TESTED",
        "provider_calls": 0,
        "reports": reports,
        "database": str(database.path),
    }
    _write(evidence_dir / "SOAK.json", payload)
    return payload


def _fault_matrix() -> list[dict[str, Any]]:
    # The matrix is explicit even where the current lane cannot safely inject
    # a process kill. Those points remain NOT_TESTED and therefore keep AC-08
    # from being misclassified as PASS.
    return [
        {"point": "post cycle_started", "status": "NOT_TESTED",
         "reason": "Hermes-owned worker loop has no lane-local fault hook"},
        {"point": "post candidate_generated", "status": "PASS",
         "evidence": "test_v3_hard_core.py::test_candidate_approval_crash_window_recovers_one_mission"},
        {"point": "post candidate_scored", "status": "PASS",
         "evidence": "test_v3_scoring.py::test_scoring_is_deterministic_byte_identical_traces"},
        {"point": "post decision_recorded SELECT", "status": "PASS",
         "evidence": "test_v3_smoke.py::test_restart_after_select_before_materialization_resumes_once"},
        {"point": "post mission.created", "status": "PASS",
         "evidence": "test_v3_hard_core.py::test_candidate_approval_crash_window_recovers_one_mission"},
        {"point": "post agent_run.started", "status": "NOT_TESTED",
         "reason": "no dedicated V3 process-kill hook in this lane"},
        {"point": "post verification.started", "status": "NOT_TESTED",
         "reason": "Hermes verifier crash transition is outside owned source"},
        {"point": "mid git push", "status": "PASS",
         "evidence": "test_v3_git_reconcile.py::test_subprocess_timeout_expired_is_ambiguous_not_failed"},
        {"point": "post effect.reconcile_pending", "status": "PASS",
         "evidence": "test_v3_git_reconcile.py::test_landed_reconciliation_is_terminal_and_does_not_repush"},
        {"point": "post mission.completed", "status": "NOT_TESTED",
         "reason": "outcome trigger belongs to Hermes-owned v3_outcomes path"},
        {"point": "post outcome_recorded", "status": "NOT_TESTED",
         "reason": "calibration/continuation recovery is Hermes-owned"},
        {"point": "post cycle_completed", "status": "PASS",
         "evidence": "test_v3_smoke.py::test_replay_same_loop_id_is_idempotent"},
    ]


def _defect_inventory() -> list[dict[str, Any]]:
    return [
        {
            "id": "V3-HARD-STOP-001",
            "status": "OPEN",
            "owner": "Hermes",
            "source": "src/jarvis_foundry/v3_loop.py:295-308",
            "summary": "legacy loop observes before a fresh STOP gate; race can write observation events after STOP",
        },
        {
            "id": "V3-HARD-FAULT-001",
            "status": "OPEN",
            "owner": "Hermes",
            "source": "src/jarvis_foundry/v3_cycle.py:56-192",
            "summary": "cycle_started and lane-local transition fault hooks are absent, so the full 12-point crash matrix cannot run",
        },
        {
            "id": "V3-HARD-OUTCOME-001",
            "status": "OPEN",
            "owner": "Hermes",
            "source": "src/jarvis_foundry/v3_outcomes.py:35-56",
            "summary": "outcome recording accepts caller-supplied mission/candidate identity without proving mission.completed linkage",
        },
        {
            "id": "V3-HARD-CONT-001",
            "status": "OPEN",
            "owner": "Hermes",
            "source": "src/jarvis_foundry/v3_outcomes.py:112-125",
            "summary": "maintenance continuation reports wake_scheduled=False and does not create a next selection",
        },
        {
            "id": "V3-HARD-RESOURCE-001",
            "status": "OPEN",
            "owner": "Hermes",
            "source": "docs/v3/05_V3_ACCEPTANCE_GATE.md:17-21",
            "summary": "no fresh V3 resource/counter envelope evidence exists beyond context bounds",
        },
        {
            "id": "V3-HARD-LINEAGE-001",
            "status": "OPEN",
            "owner": "Hermes",
            "source": "src/jarvis_foundry/v3_cycle.py:164-192",
            "summary": "cycle path ends at COMPILED and does not produce a fresh outcome-to-next-selection lineage",
        },
    ]


def _ac_matrix(focused: dict[str, Any], supporting: dict[str, Any],
               migration: dict[str, Any],
               default_off: dict[str, Any], schema: dict[str, Any],
               soak: dict[str, Any]) -> list[dict[str, Any]]:
    focused_ok = focused["exit_code"] == 0 and focused["failed"] == 0
    migration_ok = migration["exit_code"] == 0 and migration["failed"] == 0
    supporting_ok = supporting["exit_code"] == 0 and supporting["failed"] == 0
    return [
        {"id": "AC-01", "status": "FAIL", "evidence": [],
         "reason": "No fresh three-loop discovery-to-mission-to-outcome proof; outcome path is not wired in this lane."},
        {"id": "AC-02", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["focused-runs", "test_v3_scoring.py", "test_v3_smoke.py"]},
        {"id": "AC-03", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_scoring.py", "test_v3_git_reconcile.py", "test_v3_hard_core.py"]},
        {"id": "AC-04", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_hard_core.py", "test_v3_scoring.py"]},
        {"id": "AC-05", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_hard_core.py::test_research_override_selects_research_when_build_confidence_is_low"]},
        {"id": "AC-06", "status": "PASS" if supporting_ok else "FAIL",
         "evidence": ["supporting-runs", "test_v3_teams.py", "test_verifier_binding_hardening.py"]},
        {"id": "AC-07", "status": "FAIL",
         "evidence": [], "reason": "Fresh audit found the legacy v3_loop observes before STOP re-check and the unified path has a race window."},
        {"id": "AC-08", "status": "FAIL", "evidence": ["fault-matrix.json"],
         "reason": "Six named crash points remain NOT_TESTED without lane-local process fault hooks."},
        {"id": "AC-09", "status": "FAIL",
         "evidence": ["supporting-runs"],
         "reason": "Worker/verifier crash coverage is fresh, but no bounded repair-to-fresh-verification sequence closes the V3 criterion."},
        {"id": "AC-10", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_git_reconcile.py"]},
        {"id": "AC-11", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_git_reconcile.py::test_disposable_remote_push_completes_with_provider_proof"]},
        {"id": "AC-12", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_outcomes.py"]},
        {"id": "AC-13", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_outcomes.py", "test_executive_intelligence.py"]},
        {"id": "AC-14", "status": "FAIL", "evidence": [],
         "reason": "maintenance_scan_hook reports wake_scheduled=False and no fresh next-selection outcome chain exists."},
        {"id": "AC-15", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_loop.py", "test_v3_discovery_cycle.py"]},
        {"id": "AC-16", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_hard_core.py::test_context_manifest_bounds_hostile_objective_and_claims", "test_role_manifest.py"]},
        {"id": "AC-17", "status": "FAIL", "evidence": [],
         "reason": "Context bounds are proven, but a full V3 resource/counter envelope is not implemented or measured."},
        {"id": "AC-18", "status": "PASS" if focused_ok else "FAIL",
         "evidence": ["test_v3_hard_core.py::test_projection_tamper_is_detected_and_rebuild_restores_consistency"]},
        {"id": "AC-19", "status": "PASS" if migration_ok and schema["status"] == "PASS" else "FAIL",
         "evidence": ["test_knowledge_v35_durability.py", "migration-runs.junit.xml"]},
        {"id": "AC-20", "status": "FAIL", "evidence": [],
         "reason": "No single fresh lineage artifact spans goal, observation, mission verification, reconcile, outcome, and next selection."},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", default="evidence/v3/hard")
    parser.add_argument("--full-suite", action="store_true")
    parser.add_argument("--full-suite-junit", type=Path)
    args = parser.parse_args()
    evidence_dir = (ROOT / args.evidence_dir).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    base = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    status = _git("status", "--short")
    base_ancestry = _git("merge-base", "HEAD", EXPECTED_BASE)
    identity = {
        "expected_base": EXPECTED_BASE,
        "head_at_gate_start": base,
        "base_matches": base_ancestry == EXPECTED_BASE,
        "base_ancestry": base_ancestry,
        "branch": branch,
        "worktree": str(ROOT),
        "status_before_gate": status.splitlines(),
        "production_mutation": False,
        "live_database_mutation": False,
        "push_attempted": False,
    }
    _write(evidence_dir / "IDENTITY.json", identity)

    default_off = _default_off_probe()
    schema = _schema_probe()
    _write(evidence_dir / "DEFAULT_OFF.json", default_off)
    _write(evidence_dir / "SCHEMA.json", schema)

    focused = _run_pytest("focused-runs", FOCUSED_TESTS, evidence_dir)
    supporting = _run_pytest("supporting-runs", SUPPORTING_TESTS, evidence_dir)
    migration = _run_pytest("migration-runs", MIGRATION_TESTS, evidence_dir)
    soak = _bounded_soak(evidence_dir)
    fault_matrix = _fault_matrix()
    defects = _defect_inventory()
    _write(evidence_dir / "DEFECTS.json", defects)
    _write(evidence_dir / "fault-matrix.json", {
        "status": "FAIL" if any(item["status"] != "PASS" for item in fault_matrix) else "PASS",
        "points": fault_matrix,
    })

    full_suite: dict[str, Any] = {"status": "NOT_TESTED"}
    if args.full_suite_junit:
        full_suite = _reuse_junit("full-suite", args.full_suite_junit.resolve())
        full_suite["status"] = "PASS" if full_suite["exit_code"] == 0 else "FAIL"
    elif args.full_suite:
        full_suite = _run_pytest("full-suite", ["tests/foundry"], evidence_dir)
        full_suite["status"] = "PASS" if full_suite["exit_code"] == 0 else "FAIL"

    ac = _ac_matrix(focused, supporting, migration, default_off, schema, soak)
    mandatory_pass = all(item["status"] == "PASS" for item in ac)
    independent = {
        "status": "NOT_TESTED",
        "reason": "A separate verifier session was not available in this lane.",
    }
    verdict = "PASS" if mandatory_pass and independent["status"] == "PASS" \
        and full_suite.get("status") == "PASS" else "FAIL"
    report = {
        "verdict": verdict,
        "source_sha_verified": base,
        "branch": branch,
        "worktree": str(ROOT),
        "schema_version": schema,
        "feature_flag": default_off,
        "focused_suite": focused,
        "migration_suite": migration,
        "supporting_suite": supporting,
        "full_suite": full_suite,
        "soak": soak,
        "independent_verifier": independent,
        "ac": ac,
        "defects": defects + [item for item in ac if item["status"] != "PASS"],
        "production_mutation": False,
        "live_database_mutation": False,
        "push_attempted": False,
    }
    _write(evidence_dir / "GATE_REPORT.json", report)
    _write(evidence_dir / "HANDOFF.json", {
        "verdict": verdict,
        "verified_source_sha": base,
        "expected_base": EXPECTED_BASE,
        "branch": branch,
        "worktree": str(ROOT),
        "ac_summary": {item["id"]: item["status"] for item in ac},
        "defects": report["defects"],
        "fault_matrix": str(evidence_dir / "fault-matrix.json"),
        "soak": soak,
        "focused_suite": focused,
        "migration_suite": migration,
        "supporting_suite": supporting,
        "full_suite": full_suite,
        "independent_verifier": independent,
        "feature_flag_off": default_off["status"] == "PASS",
        "production_live_db_pushed": False,
        "next_integration_action": (
            "Do not promote. Hermes lane must close AC-01, AC-07, AC-08, "
            "AC-09, AC-14, AC-17, and AC-20, then rerun this gate with a "
            "separate verifier and full-suite exit 0."
        ),
    })
    print(json.dumps({
        "verdict": verdict,
        "source_sha_verified": base,
        "focused": focused,
        "migration": migration,
        "full_suite": full_suite,
        "evidence_dir": str(evidence_dir),
    }, indent=2))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
