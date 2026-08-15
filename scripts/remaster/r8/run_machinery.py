"""Run real R8 machinery missions and bind machine-readable evidence."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, PYTHON, TEMP_BASE, run, sha256_file, write_json

EVIDENCE_UTC = "2026-08-15T20:30:00Z"
ALPHA_PROJECT = TEMP_BASE / "projects" / "alpha_ledger"
CANDIDATE_PROJECT = TEMP_BASE / "projects" / "r8_orchestration_candidate"


def _config(
    *,
    mission_id: str,
    kind: str,
    source_project: Path,
    worker_module: str,
    worker_spec: dict[str, Any] | None = None,
    required_tests: list[str] | tuple[str, ...] = ("pytest tests -q",),
    repair_finding_id: str = "",
    reproduced_test_command: str = "",
    next_action: str = "",
) -> dict[str, Any]:
    return {
        "mission_id": mission_id,
        "kind": kind,
        "source_project": str(source_project),
        "branch": f"r8/{mission_id}",
        "worker_module": worker_module,
        "worker_id": f"worker-{mission_id}",
        "role": "builder",
        "objective": f"complete R8 mission {mission_id}",
        "known_verified_state": {
            "program": "POST_V7_JARVIS_REMASTER",
            "stage": "R8",
            "source": str(source_project),
        },
        "relevant_previous_failures": ["prior R8 builder returned empty final and no files"],
        "allowed_mutation": ["tests/", "docs/", "src/"],
        "forbidden_mutation": [".git/", "protected/**", "live/**", "vault/**"],
        "acceptance_criteria": [
            "machine-readable worker result",
            "real diff inspection",
            "fresh verifier PASS",
            "ledger lifecycle complete",
            "source repo unchanged",
        ],
        "required_tests": list(required_tests),
        "required_evidence": ["worker_result.json", "mission_report.json", "diff.json"],
        "rollback_expectation": "changes confined to isolated clone; restore demonstrated",
        "worker_spec": worker_spec or {},
        "snapshot_untracked": False,
        "candidate_id": f"candidate-{mission_id}",
        "decision_id": f"decision-{mission_id}",
        "goal_id": f"goal-{mission_id}",
        "repair_finding_id": repair_finding_id,
        "reproduced_test_command": reproduced_test_command,
        "python": PYTHON,
        "evidence_dir": str(EVIDENCE_DIR / "machinery"),
        "proving_root": str(TEMP_BASE),
        "timeout_seconds": 2400,
        "next_action": next_action,
    }


def _run_mission(config: dict[str, Any]) -> dict[str, Any]:
    config_path = EVIDENCE_DIR / "machinery" / f"{config['mission_id']}_config.json"
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
        timeout_seconds=3000,
        env={"JARVIS_R8_TEMP": str(TEMP_BASE)},
    )
    return {
        "mission_id": config["mission_id"],
        "kind": config["kind"],
        "config_path": str(config_path),
        "config_path_sha256": sha256_file(config_path),
        "orchestrator_exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "output_tail": result["output"][-6000:],
    }


def main() -> int:
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    from proving_project import create_alpha_ledger, create_orchestration_candidate

    create_alpha_ledger()
    create_orchestration_candidate()

    asset_config = _config(
        mission_id="mission-r8a-asset",
        kind="asset",
        source_project=ALPHA_PROJECT,
        worker_module="jarvis_foundry.orchestration.workers.asset_builder",
        worker_spec={"asset_type": "regression_suite"},
        next_action="dispatch real repair B on the recorded verifier defect",
    )
    repair_config = _config(
        mission_id="mission-r8b-repair",
        kind="repair",
        source_project=CANDIDATE_PROJECT,
        worker_module="jarvis_foundry.orchestration.workers.repair_worker",
        worker_spec={
            "target_file": "src/jarvis_foundry/orchestration/verify.py",
            "transform_id": "verifier_recompute",
            "reproduce_command": ["pytest", "tests/test_verify_repro.py", "-q"],
            "regression_command": ["pytest", "tests", "-q"],
        },
        required_tests=(
            "pytest tests/test_verify_repro.py -q",
            "pytest tests -q",
        ),
        repair_finding_id="R8-F-001",
        reproduced_test_command="pytest tests/test_verify_repro.py -q",
        next_action="emit honest WAITING",
    )
    waiting_config = _config(
        mission_id="mission-r8c-waiting",
        kind="waiting",
        source_project=ALPHA_PROJECT,
        worker_module="jarvis_foundry.orchestration.workers.asset_builder",
        next_action="await a new useful mission",
    )

    asset_run = _run_mission(asset_config)
    repair_run = _run_mission(repair_config)
    waiting_run = _run_mission(waiting_config)

    asset_report = json.loads(
        (EVIDENCE_DIR / "machinery" / "mission-r8a-asset" / "mission_report.json").read_text(
            encoding="utf-8"
        )
    )
    repair_report = json.loads(
        (EVIDENCE_DIR / "machinery" / "mission-r8b-repair" / "mission_report.json").read_text(
            encoding="utf-8"
        )
    )
    waiting_report = json.loads(
        (EVIDENCE_DIR / "machinery" / "mission-r8c-waiting" / "mission_report.json").read_text(
            encoding="utf-8"
        )
    )
    write_json(EVIDENCE_DIR / "R8_MISSION_ASSET_A.json", asset_report)
    write_json(EVIDENCE_DIR / "R8_MISSION_REPAIR_B.json", repair_report)
    write_json(EVIDENCE_DIR / "R8_MISSION_WAITING.json", waiting_report)
    summary = {
        "schema": "jarvis-r8-machinery-v1",
        "generated_utc": EVIDENCE_UTC,
        "missions": {
            "asset_a": {
                "status": asset_report["status"],
                "run": asset_run,
                "report_path": "R8_MISSION_ASSET_A.json",
            },
            "repair_b": {
                "status": repair_report["status"],
                "run": repair_run,
                "report_path": "R8_MISSION_REPAIR_B.json",
            },
            "waiting": {
                "status": waiting_report["status"],
                "run": waiting_run,
                "report_path": "R8_MISSION_WAITING.json",
            },
        },
        "exit_code": 0
        if (
            asset_report["status"] == "ACCEPTED"
            and repair_report["status"] == "ACCEPTED"
            and waiting_report["status"] == "WAITING"
        )
        else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    write_json(EVIDENCE_DIR / "R8_MACHINERY_REPORT.json", summary)
    print(f"machinery: {json.dumps(summary['missions'], sort_keys=True)}")
    print(f"wall_seconds={summary['wall_seconds']}")
    return summary["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
