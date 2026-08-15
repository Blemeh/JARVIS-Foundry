"""Non-emitting secret scan over R4-owned files."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, r4_owned_files, record_enabled, sha256_bytes, write_json

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "aws_access_key_id",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "aws_secret_access_key",
        re.compile(r"\baws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{40}\b"),
    ),
    (
        "github_token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
    ),
    (
        "generic_secret_assignment",
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|github[_-]?token|secret[_-]?key|"
            r"password|passwd|authorization|bearer)\s*[:=]\s*"
            r"['\"]?[A-Za-z0-9_\-./+=]{24,}"
        ),
    ),
]


def scan_file(path: Path, rel: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    matches: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern_name, pattern in PATTERNS:
            for match in pattern.finditer(line):
                matches.append(
                    {
                        "path": rel,
                        "line": line_number,
                        "pattern": pattern_name,
                        "value_sha256": sha256_bytes(match.group(0).encode("utf-8")),
                    }
                )
    return matches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R4_SECRET_SCAN.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R4_SECRET_SCAN_RAW.json")
    args = parser.parse_args(argv)
    started = time.monotonic()
    files = [args.root / relative for relative in r4_owned_files(args.root)]
    matches: list[dict[str, Any]] = []
    for path in files:
        if not path.is_file():
            continue
        matches.extend(scan_file(path, path.relative_to(args.root).as_posix()))
    real_matches = len(matches)
    result = {
        "schema": "jarvis-r4-secret-scan-v1",
        "files_scanned": len(files),
        "total_matches": real_matches,
        "real_matches": real_matches,
        "wall_seconds": round(time.monotonic() - started, 3),
        "exit_code": 0 if real_matches == 0 else 1,
        "evidence_note": "matched values are never emitted; only sha256 digests",
        "matches": matches,
    }
    if record_enabled():
        write_json(args.raw, result)
        write_json(args.output, {k: v for k, v in result.items() if k != "matches"})
    print(
        json.dumps(
            {
                "files_scanned": result["files_scanned"],
                "real_matches": result["real_matches"],
            }
        )
    )
    return 1 if real_matches else 0


if __name__ == "__main__":
    sys.exit(main())
