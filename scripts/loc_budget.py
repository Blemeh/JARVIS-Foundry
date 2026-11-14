"""Fail when src/jarvis exceeds its line budget (non-blank, non-comment lines)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "jarvis"


def count(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=5000)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    files = sorted(ROOT.rglob("*.py"))
    total = 0
    for file in files:
        n = count(file)
        total += n
        if args.verbose:
            print(f"{n:6}  {file.relative_to(ROOT.parent)}")
    status = "OK" if total <= args.budget else "OVER BUDGET"
    print(f"src/jarvis: {total} non-blank, non-comment lines in {len(files)} files "
          f"(budget {args.budget}): {status}")
    return 0 if total <= args.budget else 1


if __name__ == "__main__":
    sys.exit(main())
