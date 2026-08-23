"""Prove stale owner requests are bounded and never applied blindly."""

from __future__ import annotations

import argparse
import gc
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.layout import default_live_foundry_home
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.control import CONTROL_REQUESTED, inspect_control, submit_request
from jarvis_foundry.supervisor.service import Supervisor


def _append_stale_request(database: FoundryDatabase, *, action: str, request_id: str) -> None:
    database.append(Event.create(
        CONTROL_REQUESTED,
        {
            "request_id": request_id,
            "action": action,
            "requested_by": "stale-harness",
            "requested_at": "2020-01-01T00:00:00Z",
            "expected_release": None,
            "expires_at": "2020-01-01T01:00:00Z",
        },
        source_type="owner",
        source_id="stale-harness",
        idempotency_key=f"{CONTROL_REQUESTED}:{request_id}",
    ))


def _run(root: Path) -> dict[str, object]:
    root = root.resolve()
    if root == default_live_foundry_home().resolve():
        raise RuntimeError("refusing to run stale-request harness on the live Foundry home")
    root.mkdir(parents=True, exist_ok=True)
    database = FoundryDatabase(root / "foundry.db")
    database.initialize()
    stop_path = root / "EMERGENCY_STOP"

    _append_stale_request(database, action="START", request_id="stale-start")
    stale_start = Supervisor(database, stop_path, instance_id="stale-start-owner")
    if stale_start.start() != "STOPPED":
        raise AssertionError("stale START request launched a supervisor")
    start_state = inspect_control(database, stop_path=str(stop_path))
    if start_state.request_status != "STALE" or start_state.status != "STOPPED":
        raise AssertionError(f"stale START was not closed safely: {start_state.to_json()}")
    if database.event_count(event_type="supervisor.started") != 0:
        raise AssertionError("stale START emitted supervisor.started")

    submit_request(database, "START", request_id="fresh-start")
    live = Supervisor(database, stop_path, instance_id="stale-stop-owner")
    if live.start() != "RUNNING":
        raise AssertionError("live owner failed to start after stale START")
    _append_stale_request(database, action="STOP", request_id="stale-stop")
    if live.heartbeat() != "RUNNING":
        raise AssertionError("stale STOP interrupted the live owner")
    stop_state = inspect_control(database, stop_path=str(stop_path))
    if stop_state.request_status != "STALE" or stop_state.status != "RUNNING":
        raise AssertionError(f"stale STOP was applied or misreported: {stop_state.to_json()}")
    if database.event_count(event_type="supervisor.stopped") != 0:
        raise AssertionError("stale STOP emitted supervisor.stopped")
    live.stop()

    result = {
        "harness": "stale-request",
        "verdict": "PASS",
        "root": str(root),
        "stale_start": start_state.to_json(),
        "stale_stop": stop_state.to_json(),
        "events": database.event_count(),
    }
    del live, stale_start, database
    gc.collect()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()
    if args.root is not None:
        result = _run(args.root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    with tempfile.TemporaryDirectory(prefix="jarvis-stale-request-") as path:
        try:
            result = _run(Path(path))
        finally:
            gc.collect()
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
