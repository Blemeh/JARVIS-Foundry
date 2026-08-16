"""Run the local R10 Command Center: python -m jarvis_foundry.command_center."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from .proving import ProvingLayout
from .seeding import seed_proving_stores
from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JARVIS R10 Command Center")
    parser.add_argument("--proving-root", type=Path, default=None)
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path(__file__).resolve().parents[4] / "docs" / "remaster" / "evidence",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    layout = ProvingLayout(args.proving_root) if args.proving_root else None
    if layout is None:
        from .proving import default_proving_layout

        layout = default_proving_layout()
    seed_proving_stores(layout, args.evidence_root)
    running = serve(
        layout,
        evidence_root=args.evidence_root,
        host=args.host,
        port=args.port,
    )
    print(f"R10 Command Center serving at {running.base_url}/ (Ctrl+C to stop)")

    def _stop(_signum: int, _frame: object) -> None:
        running.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        while True:
            import time

            time.sleep(3600)
    except KeyboardInterrupt:
        running.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
