"""Worker used to prove malformed results fail closed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", required=True, type=Path)
    parser.add_argument("--clone", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    # Deliberately omits required contract keys such as files_changed, tests, etc.
    args.result.write_text(
        json.dumps(
            {
                "status": "PASS",
                "claims": {"prose": "this result is malformed"},
                "uncertainty": "low",
                "recommended_next_action": "none",
            }
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    main()
