"""Incremental checkpoint helper for the R12 builder."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from common import write_checkpoint


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print("usage: checkpoint.py <name> <json-payload>")
        return 2
    name = args[0]
    payload = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    path = write_checkpoint(name, payload)
    print(f"checkpoint written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
