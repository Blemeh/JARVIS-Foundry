"""R9 privacy boundary: no private vault/identity content in evidence."""

from __future__ import annotations

import re
import sys
import time
from typing import Any

from common import (
    EVIDENCE_DIR,
    EXCLUDED_VAULT_CATEGORIES,
    PROTECTED_MAIN,
    VAULT_ROOT,
    write_json,
)


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    evidence_text = ""
    for path in sorted(EVIDENCE_DIR.glob("*.json")):
        if path.name in {
            "R9_PRIVACY.json",
            "R9_LIVE_GUARD_BEFORE.json",
            "R9_LIVE_GUARD_AFTER.json",
        }:
            continue
        evidence_text += path.read_text(encoding="utf-8", errors="replace")
    for path in sorted(EVIDENCE_DIR.glob("*.txt")):
        evidence_text += path.read_text(encoding="utf-8", errors="replace")

    forbidden_patterns = [
        re.escape(str(VAULT_ROOT)),
        re.escape("C:/Users/b/Blendi"),
        re.escape(str(PROTECTED_MAIN)) + r"(?![A-Za-z0-9_-])",
    ]
    path_leaks = [
        pattern
        for pattern in forbidden_patterns
        if re.search(pattern, evidence_text, re.IGNORECASE)
    ]
    private_category_mentions = [
        part for part in EXCLUDED_VAULT_CATEGORIES if part.lower() in evidence_text.lower()
    ]
    passed = not path_leaks and not private_category_mentions
    payload: dict[str, Any] = {
        "schema": "jarvis-r9-privacy-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": 0.0,
        "passed": passed,
        "excluded_categories": list(EXCLUDED_VAULT_CATEGORIES),
        "path_leaks": path_leaks,
        "private_category_mentions": private_category_mentions,
        "evidence_file_count": len(list(EVIDENCE_DIR.glob("*"))),
        "evidence_bytes": sum(
            path.stat().st_size for path in EVIDENCE_DIR.glob("*") if path.is_file()
        ),
    }
    write_json(EVIDENCE_DIR / "R9_PRIVACY.json", payload)
    print(
        f"privacy: passed={passed} path_leaks={len(path_leaks)} "
        f"category_mentions={len(private_category_mentions)} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
