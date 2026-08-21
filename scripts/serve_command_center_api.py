#!/usr/bin/env python3
"""Start the canonical Command Center API for the React live adapter."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.command_center import CommandCenterServer
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.paths import FoundryPaths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve canonical Command Center API for React live mode")
    parser.add_argument("--home", type=str, default=None, help="Foundry home (defaults to discovered home)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--owner-secret-env", default="JARVIS_OWNER_SECRET", help="Env var holding owner secret bytes")
    args = parser.parse_args(argv)

    paths = FoundryPaths.discover(args.home)
    database = FoundryDatabase(paths.database)
    if not paths.database.is_file():
        print(f"database not initialized at {paths.database}", file=sys.stderr)
        return 1

    server = CommandCenterServer(database, stop_path=paths.emergency_stop, host=args.host, port=args.port)
    secret = os.environ.get(args.owner_secret_env, "").encode("utf-8")
    if secret:
        server.configure_owner(secret)
    server.start()
    host, port = server.address
    print(f"command center API listening on http://{host}:{port}")
    print("React live mode: VITE_JARVIS_MODE=live npm run dev (proxies /api)")

    def _shutdown(_signum: int, _frame: object) -> None:
        server.close()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
