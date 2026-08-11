"""WP7 — headless Supervisor daemon (runs as the scheduled task).

Independent of Hermes Desktop: initializes, reconciles, honors an existing
STOP sentinel (FROZEN state), heartbeats on an interval, reaps stale
workers, and freezes deterministically when the sentinel appears. Exits
cleanly on SIGTERM / Ctrl-Break (``--once`` runs a single cycle for tests
and gate probes).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ..database import FoundryDatabase
from ..paths import FoundryPaths
from .service import Supervisor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-supervisor")
    parser.add_argument("--home", type=Path, default=argparse.SUPPRESS,
                        help="override the Foundry state directory")
    parser.add_argument("--interval", type=float, default=30.0,
                        help="heartbeat interval in seconds")
    parser.add_argument("--once", action="store_true",
                        help="run a single start+heartbeat cycle and exit")
    args = parser.parse_args(argv)

    paths = FoundryPaths.discover(getattr(args, "home", None))
    database = FoundryDatabase(paths.database)
    supervisor = Supervisor(database, paths.emergency_stop)

    state = supervisor.start()
    print(json.dumps({"supervisor": state, "home": str(paths.home)}), flush=True)
    if args.once:
        print(json.dumps({"heartbeat": supervisor.heartbeat()}), flush=True)
        supervisor.stop()
        return 0

    try:
        while True:
            time.sleep(args.interval)
            state = supervisor.heartbeat()
            if state == "FROZEN":
                # The STOP sentinel exists: stay alive as the watchdog, keep
                # the frozen record fresh, and wait for resume/owner.
                pass
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
