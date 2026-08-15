"""R8-G04: independent fresh verifiers and malformed-result fail-closed proof."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from common import EVIDENCE_DIR, PYTHON, TEMP_BASE, run, sha256_file, write_json

ALPHA_PROJECT = TEMP_BASE / "projects" / "alpha_ledger"


def _malformed_mission() -> dict[str, object]:
    mission_id = "mission-r8d-malformed"
    config = {
        "mission_id": mission_id,
        "kind": "asset",
        "source_project": str(ALPHA_PROJECT),
        "branch": f"r8/{mission_id}",
        "worker_module": "jarvis_foundry.orchestration.workers.malformed_worker",
        "worker_id": f"worker-{mission_id}",
        "role": "builder",
        "objective": "prove malformed result fails closed",
        "known_verified_state": {},
        "relevant_previous_failures": [],
        "allowed_mutation": ["tests/"],
        "forbidden_mutation": ["protected/"],
        "acceptance_criteria": ["result must be rejected"],
        "required_tests": ["pytest tests -q"],
        "required_evidence": ["worker_result.json"],
        "rollback_expectation": "none",
        "worker_spec": {"asset_type": "regression_suite"},
        "snapshot_untracked": False,
        "candidate_id": f"candidate-{mission_id}",
        "decision_id": f"decision-{mission_id}",
        "goal_id": f"goal-{mission_id}",
        "repair_finding_id": "",
        "reproduced_test_command": "",
        "python": PYTHON,
        "evidence_dir": str(EVIDENCE_DIR / "machinery"),
        "proving_root": str(TEMP_BASE),
        "timeout_seconds": 1200,
        "next_action": "",
    }
    config_path = EVIDENCE_DIR / "machinery" / f"{mission_id}_config.json"
    write_json(config_path, config)
    result = run(
        [
            PYTHON,
            "-m",
            "jarvis_foundry.orchestration.orchestrator",
            "--config",
            str(config_path),
        ],
        cwd=Path(__file__).resolve().parents[3],
        timeout_seconds=1800,
        env={"JARVIS_R8_TEMP": str(TEMP_BASE)},
    )
    report_path = EVIDENCE_DIR / "machinery" / mission_id / "mission_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    return {
        "orchestrator_exit_code": result["exit_code"],
        "status": report.get("status"),
        "rejection_reasons": report.get("rejection_reasons"),
        "events": report.get("ledger", {}).get("events", []),
        "report_path": str(report_path),
        "report_sha256": sha256_file(report_path) if report_path.is_file() else None,
    }


def main() -> int:
    started = time.monotonic()
    asset = json.loads((EVIDENCE_DIR / "R8_MISSION_ASSET_A.json").read_text(encoding="utf-8"))
    repair = json.loads((EVIDENCE_DIR / "R8_MISSION_REPAIR_B.json").read_text(encoding="utf-8"))
    asset_reverified = [
        event for event in asset["ledger"]["events"] if event["event_type"] == "mission.reverified"
    ]
    repair_reverified = [
        event for event in repair["ledger"]["events"] if event["event_type"] == "mission.reverified"
    ]
    no_self_certification = (
        bool(asset_reverified)
        and bool(repair_reverified)
        and asset["worker_id"] != asset["verdict"]["verifier_id"]
        and repair["worker_id"] != repair["verdict"]["verifier_id"]
    )
    malformed = _malformed_mission()
    malformed_events = [
        event
        for event in malformed["events"]
        if event["event_id"].startswith("evt-mission-r8d-malformed-")
    ]
    fail_closed = (
        malformed["status"] == "REJECTED"
        and bool(malformed["rejection_reasons"])
        and any(event["event_type"] == "mission.malformed_rejected" for event in malformed_events)
        and not any(event["event_type"] == "mission.accepted" for event in malformed_events)
    )
    passed = bool(no_self_certification and fail_closed)
    payload = {
        "schema": "jarvis-r8-g04-verification-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "fresh_verifier_events": {
            "asset_mission": asset_reverified,
            "repair_mission": repair_reverified,
        },
        "no_builder_self_certification": no_self_certification,
        "malformed_result_proof": malformed,
        "fail_closed": fail_closed,
    }
    write_json(EVIDENCE_DIR / "R8_G04_VERIFICATION.json", payload)
    print(f"g04 verification: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
