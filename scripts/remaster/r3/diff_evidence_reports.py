"""Print the differing fields between two R3 builder report JSON files."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def diff(a: dict[str, Any], b: dict[str, Any], prefix: str = "") -> list[str]:
    lines: list[str] = []
    keys = sorted(set(a) | set(b))
    for key in keys:
        path = f"{prefix}.{key}" if prefix else key
        if key not in a:
            lines.append(f"{path}: ONLY_B {json.dumps(b[key])[:80]}")
        elif key not in b:
            lines.append(f"{path}: ONLY_A {json.dumps(a[key])[:80]}")
        elif isinstance(a[key], dict) and isinstance(b[key], dict):
            lines.extend(diff(a[key], b[key], path))
        elif a[key] != b[key]:
            lines.append(f"{path}: A={json.dumps(a[key])[:80]} B={json.dumps(b[key])[:80]}")
    return lines


def main() -> int:
    a_path = Path(sys.argv[1])
    b_path = Path(sys.argv[2])
    a = json.loads(a_path.read_text(encoding="utf-8"))
    b = json.loads(b_path.read_text(encoding="utf-8"))
    lines = diff(a, b)
    print("\n".join(lines) if lines else "IDENTICAL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
