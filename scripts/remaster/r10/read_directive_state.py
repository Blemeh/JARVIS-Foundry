"""Read canonical directive control state from an isolated proving store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jarvis_foundry.command_center.directives import DirectiveService
from jarvis_foundry.command_center.ledger import DirectiveLedger
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proving-root", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.proving_root / "stores"
    service = DirectiveService(
        PortfolioStore(root / "portfolio"),
        DirectiveLedger(root / "directive-ledger.jsonl"),
    )
    state = service.state_dict()
    state["ledger_event_count"] = service.ledger.count()
    state["ledger_sha256"] = service.ledger.sha256()
    print(json.dumps(state, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
