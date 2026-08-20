"""Disposable end-to-end proof: API bridge -> ledger -> UI adapter contract."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.command_center import CommandCenterServer
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def _seed_worker_cycle(db: FoundryDatabase) -> str:
    mission_id = "cc-live-e2e-1"
    run_id = "cc-live-run-1"
    bundle_id = "cc-live-evidence-1"
    events = [
        Event.create(
            "mission.created",
            {
                "mission_id": mission_id,
                "objective": "command center live e2e probe",
                "state": "PLANNED",
                "acceptance_criteria": ["ui reads lifecycle"],
                "next_action": "dispatch worker",
            },
            source_type="supervisor",
            source_id="cc-e2e",
            idempotency_key=f"mission.created:{mission_id}",
        ),
        Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": "ORIENTING", "next_action": "orient"},
            source_type="supervisor",
            source_id="cc-e2e",
            idempotency_key=f"mission.state_changed:{mission_id}:ORIENTING",
        ),
        Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": "ACTION_SELECTION", "next_action": "dispatch worker"},
            source_type="supervisor",
            source_id="cc-e2e",
            idempotency_key=f"mission.state_changed:{mission_id}:ACTION_SELECTION",
        ),
        Event.create(
            "agent_run.started",
            {"run_id": run_id, "mission_id": mission_id, "role": "builder", "provider": "opencode_go", "model": "probe"},
            source_type="supervisor",
            source_id="cc-e2e",
            idempotency_key=f"agent_run.started:{run_id}",
        ),
        Event.create(
            "evidence.created",
            {
                "bundle_id": bundle_id,
                "mission_id": mission_id,
                "step_id": "step-1",
                "objective": "e2e evidence",
                "status": "RECORDED",
                "artifact_hashes": {"report": "abc123"},
            },
            source_type="supervisor",
            source_id="cc-e2e",
            idempotency_key=f"evidence.created:{bundle_id}",
        ),
        Event.create(
            "agent_run.completed",
            {"run_id": run_id, "result": {"ok": True}},
            source_type="supervisor",
            source_id="cc-e2e",
            idempotency_key=f"agent_run.completed:{run_id}",
        ),
    ]
    for event in events:
        db.append(event)
    return mission_id


def main() -> int:
    tmp = Path(__file__).resolve().parent / ".cc_live_e2e"
    tmp.mkdir(exist_ok=True)
    db_path = tmp / "foundry.db"
    stop_path = tmp / "STOP"
    if db_path.exists():
        db_path.unlink()
    db = FoundryDatabase(db_path)
    db.initialize()
    mission_id = _seed_worker_cycle(db)

    secret = b"cc-live-e2e-secret"
    server = CommandCenterServer(db, stop_path=stop_path, port=0)
    server.configure_owner(secret)
    server.start()
    base = f"http://{server.address[0]}:{server.address[1]}"

    try:
        with urlopen(f"{base}/api/command-center", timeout=5) as response:
            snapshot = json.loads(response.read())
        assert any(m["mission_id"] == mission_id for m in snapshot["missions"])
        assert any(w.get("run_id") == "cc-live-run-1" for w in snapshot["workers"])
        assert any(e.get("bundle_id") == "cc-live-evidence-1" for e in snapshot["evidence"])

        goal_body = json.dumps({
            "command_type": "GOAL",
            "target": "GLOBAL",
            "exact_text": "owner goal from live e2e probe",
        }).encode()
        with urlopen(Request(f"{base}/api/owner-command", data=goal_body, method="POST"), timeout=5) as response:
            goal = json.loads(response.read())
        assert goal["status"] == "APPLIED"

        stop_body = json.dumps({
            "command_type": "STOP_EVERYTHING",
            "target": "GLOBAL",
            "exact_text": "e2e stop probe",
        }).encode()
        with urlopen(Request(f"{base}/api/owner-command", data=stop_body, method="POST"), timeout=5) as response:
            stop = json.loads(response.read())
        assert stop["status"] == "APPLIED"

        with urlopen(f"{base}/api/command-center", timeout=5) as response:
            after = json.loads(response.read())
        assert after["stop"]["active"] is True
        assert after["controls"]["goals"]

        print(json.dumps({
            "status": "PASS",
            "mission_id": mission_id,
            "ledger_head": after["source_of_truth"]["ledger_head_sequence"],
            "stop_active": after["stop"]["active"],
            "goals": len(after["controls"]["goals"]),
        }, indent=2))
        return 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
