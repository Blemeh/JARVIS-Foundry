"""Seed the R10 isolated proving stores from real gate evidence."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from common import EVIDENCE_DIR, EVIDENCE_UTC, REPO_ROOT, safe_clear, write_json

from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.command_center.seeding import seed_proving_stores


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    root = Path("C:/Users/b/AppData/Local/Temp/jarvis-r10-builder")
    if root.is_dir():
        safe_clear(root)
    layout = ProvingLayout(root)
    summary = seed_proving_stores(layout, REPO_ROOT / "docs" / "remaster" / "evidence")
    summary.update(
        {
            "schema": "jarvis-r10-seed-gate-v1",
            "evidence_utc": EVIDENCE_UTC,
            "exit_code": 0,
            "wall_seconds": round(time.monotonic() - started, 3),
            "passed": True,
            "proving_root": str(layout.root),
        }
    )
    write_json(EVIDENCE_DIR / "R10_SEED.json", summary)
    print(
        f"seed: portfolio_events={summary['portfolio_events']} "
        f"missions={summary['missions']} capabilities={summary['capabilities']} "
        f"wall_seconds={summary['wall_seconds']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
