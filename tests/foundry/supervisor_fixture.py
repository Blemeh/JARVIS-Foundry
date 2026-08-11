"""Subprocess fixture: a live Supervisor that is hard-killed mid-coordination.

The parent test spawns this script, waits for the marker file (written after
the Supervisor started and heartbeated), then kills the process with
TerminateProcess — simulating forced supervisor death with no clean shutdown.
The parent then constructs a fresh Supervisor on the same database to prove
reconstruction from durable state.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.supervisor.service import Supervisor  # noqa: E402


def main() -> None:
    database_path, stop_path, marker_path, lease_seconds = (
        sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    )
    database = FoundryDatabase(database_path)
    supervisor = Supervisor(
        database, stop_path,
        instance_id="fixture-supervisor",
        lease_seconds=lease_seconds,
    )
    result = supervisor.start()
    if result != "RUNNING":
        raise SystemExit(f"fixture supervisor did not start: {result}")
    supervisor.heartbeat()
    supervisor.heartbeat()
    Path(marker_path).write_text("supervisor-live", encoding="utf-8")
    time.sleep(120)  # parent kills this process before this returns


if __name__ == "__main__":
    main()
