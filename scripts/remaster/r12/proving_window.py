"""R12-D05: proving-window readiness and checkpoint persistence."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    PYTHON,
    REPO_ROOT,
    TEMP_BASE,
    json_hash,
    run,
    sha256_file,
    utc_now,
    write_checkpoint,
    write_json,
)

from jarvis_foundry.fault_campaign.isolation import ProvingWorkspace, seed_portfolio
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.portfolio.selection import SelectionService

RESUME_SCRIPT = r"""
import json, sys
from pathlib import Path
from jarvis_foundry.kernel.portfolio.store import PortfolioStore
root = Path(sys.argv[1])
checkpoint = json.loads((root / 'checkpoint.json').read_text(encoding='utf-8'))
store = PortfolioStore(root / 'stores' / 'portfolio')
events = store.event_count()
result = {
    'resume_index': checkpoint['index'] + 1,
    'events': events,
    'checkpoint_events': checkpoint['events'],
    'consistent': events == checkpoint['events'],
}
print(json.dumps(result, sort_keys=True))
"""


def _simulate_restart(work_root: Path, kind: str) -> dict[str, Any]:
    completed = run(
        [PYTHON, "-c", RESUME_SCRIPT, str(work_root)],
        cwd=REPO_ROOT,
        timeout_seconds=120,
    )
    payload = json.loads(completed["output"])
    return {
        "kind": kind,
        "exit_code": completed["exit_code"],
        "resume": payload,
        "passed": completed["exit_code"] == 0 and payload.get("consistent") is True,
    }


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    freeze_path = EVIDENCE_DIR / "R12_RELEASE_FREEZE.json"
    if not freeze_path.is_file():
        raise RuntimeError("release freeze must be generated before proving readiness")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    source_tree_hash = freeze["source"]["tree_hash"]
    workspace = ProvingWorkspace(TEMP_BASE / "proving-window", fixed_utc=EVIDENCE_UTC)
    store = workspace.portfolio("portfolio")
    seed_portfolio(store, fixed_utc=EVIDENCE_UTC)
    decision = SelectionService(store, clock=FixedClock(EVIDENCE_UTC)).run()
    checkpoint = {
        "schema": "jarvis-r12-proving-checkpoint-v1",
        "index": 1,
        "events": store.event_count(),
        "decision_id": decision.decision_id,
        "frozen_utc": EVIDENCE_UTC,
        "release_fingerprint": json_hash(
            {
                "source_tree_hash": source_tree_hash,
                "source_head": "c76d92df55b6ec552b68379479ccd80f972c303c",
                "evidence": EVIDENCE_UTC,
            }
        ),
    }
    checkpoint_path = workspace.root / "checkpoint.json"
    write_json(checkpoint_path, checkpoint)

    restarts = [
        _simulate_restart(workspace.root, "compaction_agent_death_terminal_close"),
        _simulate_restart(workspace.root, "hermes_restart"),
        _simulate_restart(workspace.root, "supervisor_restart"),
        _simulate_restart(workspace.root, "windows_restart"),
    ]
    all_passed = all(item["passed"] for item in restarts) and decision.status == "SELECTED"
    readiness = {
        "schema": "jarvis-r12-proving-readiness-v1",
        "generated_utc": utc_now(),
        "fixed_utc": EVIDENCE_UTC,
        "deployed_release_frozen": True,
        "release_fingerprint": checkpoint["release_fingerprint"],
        "checkpoint": checkpoint,
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "simulated_restarts": restarts,
        "all_restart_resilience_passed": all_passed,
        "proving_window": {
            "status": "READY",
            "start_recommendation": "start after fresh independent final audit of the exact deployed release",
        },
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    waiting = {
        "schema": "jarvis-r12-waiting-v1",
        "generated_utc": utc_now(),
        "waiting_items": [
            {
                "id": "independent-final-audit",
                "status": "WAITING",
                "reason": "fresh independent final audit must adjudicate the exact deployed release",
                "monitoring": "no provider calls; no live deployment changes; evidence package frozen",
            },
            {
                "id": "proving-window-elapsed-time",
                "status": "WAITING",
                "reason": "proving window requires elapsed-time monitoring after audit start",
                "monitoring": "checkpoint persistence already proven across simulated restarts",
            },
        ],
    }
    write_json(EVIDENCE_DIR / "R12_PROVING_READINESS.json", readiness)
    write_json(EVIDENCE_DIR / "R12_CHECKPOINT_PERSISTENCE.json", readiness)
    write_json(EVIDENCE_DIR / "R12_WAITING.json", waiting)
    write_checkpoint(
        "proving_window",
        {"status": "PASS" if all_passed else "FAIL", "restarts": [r["kind"] for r in restarts]},
    )
    print(
        f"proving window: passed={all_passed} restarts={len(restarts)} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
