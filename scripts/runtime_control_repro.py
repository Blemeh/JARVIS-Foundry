"""Run the owner-control state machine in a disposable Foundry home."""

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
from jarvis_foundry.supervisor.control import inspect_control, submit_request
from jarvis_foundry.supervisor.leases import LeaseUnavailable
from jarvis_foundry.supervisor.service import Supervisor


def _run(root: Path) -> dict[str, object]:
    root = root.resolve()
    if root == default_live_foundry_home().resolve():
        raise RuntimeError("refusing to run the disposable harness on the live Foundry home")
    root.mkdir(parents=True, exist_ok=True)
    database = FoundryDatabase(root / "foundry.db")
    database.initialize()
    stop_path = root / "EMERGENCY_STOP"

    submit_request(database, "start", request_id="harness-start")
    first = Supervisor(database, stop_path, instance_id="harness-first")
    if first.start() != "RUNNING":
        raise AssertionError("start request did not reach RUNNING")
    if inspect_control(database, stop_path=str(stop_path)).status != "RUNNING":
        raise AssertionError("running state was not independently observed")

    submit_request(database, "stop", request_id="harness-stop")
    if first.heartbeat() != "STOPPED":
        raise AssertionError("stop request did not reach STOPPED")

    submit_request(database, "restart", request_id="harness-restart")
    second = Supervisor(database, stop_path, instance_id="harness-second")
    if second.start() != "RUNNING":
        raise AssertionError("restart request did not reach RUNNING")
    state = inspect_control(database, stop_path=str(stop_path))
    if state.status != "RUNNING" or state.request_status != "APPLIED":
        raise AssertionError(f"unexpected final runtime state: {state.to_json()}")

    # A duplicate scheduler trigger must not resolve a live owner's STOP.
    submit_request(database, "stop", request_id="harness-live-stop")
    duplicate = Supervisor(database, stop_path, instance_id="harness-duplicate")
    try:
        duplicate.start()
    except LeaseUnavailable:
        pass
    else:
        raise AssertionError("duplicate trigger acquired a live supervisor lease")
    pending = inspect_control(database, stop_path=str(stop_path))
    if pending.status != "RUNNING" or pending.request_status != "PENDING":
        raise AssertionError(f"live STOP was resolved too early: {pending.to_json()}")
    if second.heartbeat() != "STOPPED":
        raise AssertionError("live owner did not apply the STOP request")

    # Reusing an instance ID must advance the durable generation and avoid an
    # old start/stop idempotency key collision.
    submit_request(database, "restart", request_id="harness-restart-again")
    reused = Supervisor(database, stop_path, instance_id="harness-second")
    if reused.start() != "RUNNING":
        raise AssertionError("reused instance ID did not restart")
    if reused._start_generation < 2:
        raise AssertionError("reused instance ID did not advance generation")
    submit_request(database, "stop", request_id="harness-final-stop")
    if reused.heartbeat() != "STOPPED":
        raise AssertionError("reused instance did not apply the final STOP")
    final_state = inspect_control(database, stop_path=str(stop_path))
    if final_state.status != "STOPPED":
        raise AssertionError(f"runtime did not settle at STOPPED: {final_state.to_json()}")
    second.stop()
    result = {
        "harness": "runtime-control",
        "verdict": "PASS",
        "root": str(root),
        "final_state": final_state.to_json(),
        "events": database.event_count(),
    }
    del reused, duplicate, second, first, database
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
    with tempfile.TemporaryDirectory(prefix="jarvis-runtime-control-") as path:
        try:
            result = _run(Path(path))
        finally:
            # sqlite context managers commit/rollback but do not guarantee
            # close on every supported Python build; collect before Windows
            # removes the disposable directory.
            gc.collect()
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
