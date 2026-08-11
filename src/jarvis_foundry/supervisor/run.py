"""WP7 — headless Supervisor daemon (runs as the scheduled task).

Independent of Hermes Desktop: initializes, reconciles, honors an existing
STOP sentinel (FROZEN state), heartbeats on an interval, reaps stale
workers, and freezes deterministically when the sentinel appears. Exits
cleanly on SIGTERM / Ctrl-Break (``--once`` runs a single cycle for tests
and gate probes).

Console-event resilience: the daemon installs a Windows console control
handler so Ctrl+C / Ctrl+Break / console close / logoff / shutdown all
produce a fast durable stop (lease release + ``supervisor.stopped``) instead
of an abrupt 0xC000013A death with a stale lease (live incident 2026-08-12).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from ..database import FoundryDatabase
from ..paths import FoundryPaths
from .console import ConsoleControlHandler
from .service import Supervisor


def make_console_handler(supervisor: Supervisor) -> ConsoleControlHandler:
    """Console control handler: any control event performs a fast durable
    stop (lease release + ``supervisor.stopped``) and exits 0.

    Windows gives ~5 s after CTRL_CLOSE/LOGOFF/SHUTDOWN before forced
    termination, so the handler thread does the whole stop and then exits
    the process immediately (``os._exit`` skips remaining cleanup, which is
    already complete at that point).
    """

    def on_event(event_code: int) -> None:
        supervisor.stop()
        os._exit(0)

    return ConsoleControlHandler(on_event)


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
    console_handler = make_console_handler(supervisor)
    console_handler.install()

    state = supervisor.start()
    print(json.dumps({"supervisor": state, "home": str(paths.home)}), flush=True)
    if state == "DEGRADED":
        # Fail loud: a supervisor that cannot reconcile must not hold the
        # lease or heartbeat (finding: DEGRADED was silently ignored).
        supervisor.stop()
        return 1
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
        console_handler.uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
