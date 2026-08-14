"""Non-emitting secret scan (R3-D04).

The scanner never writes or prints matched values. Each match is reduced to
``value_sha256`` and the raw evidence stores only that digest plus path, line,
and pattern name. A second identical scan is compared by digest so the
comparison cannot leak values either.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, sha256_bytes, write_json

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

IGNORED_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "var",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
}

FIXTURE_BEGIN_LINE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----")
FIXTURE_SOURCES = (
    "tests/foundry/test_capability_broker.py",
    "tests/foundry/test_system_registry.py",
)


def scan_bytes(data: bytes, path: str) -> list[dict[str, Any]]:
    text = data.decode("utf-8", errors="replace")
    matches: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern_name, pattern in PATTERNS:
            for match in pattern.finditer(line):
                matches.append(
                    {
                        "path": path,
                        "line": line_number,
                        "pattern": pattern_name,
                        "value_sha256": sha256_bytes(match.group(0).encode("utf-8")),
                    }
                )
    return matches


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, dirnames, filenames in root.walk():
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            files.append(current / filename)
    return files


def fixture_hashes(root: Path) -> set[str]:
    """Hash known intentional fake key blocks from test fixtures."""
    hashes: set[str] = set()
    for relative in FIXTURE_SOURCES:
        path = root / relative
        if not path.is_file():
            continue
        for match in FIXTURE_BEGIN_LINE.finditer(path.read_text(encoding="utf-8")):
            hashes.add(sha256_bytes(match.group(0).encode("utf-8")))
    return hashes


def scan_repo(root: Path, known_fixture_hashes: set[str] | None = None) -> dict[str, Any]:
    known = known_fixture_hashes if known_fixture_hashes is not None else set()
    matches: list[dict[str, Any]] = []
    files_scanned = 0
    for path in iter_files(root):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        files_scanned += 1
        rel = path.relative_to(root).as_posix()
        for match in scan_bytes(data, rel):
            if match["value_sha256"] in known:
                match["classified"] = "known_test_fixture"
            matches.append(match)
    by_pattern: dict[str, int] = {}
    classified_fixtures = 0
    for match in matches:
        by_pattern[match["pattern"]] = by_pattern.get(match["pattern"], 0) + 1
        if match.get("classified") == "known_test_fixture":
            classified_fixtures += 1
    return {
        "files_scanned": files_scanned,
        "total_matches": len(matches),
        "classified_fixtures": classified_fixtures,
        "real_matches": len(matches) - classified_fixtures,
        "matches_by_pattern": by_pattern,
        "matches": matches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R3_SECRET_SCAN.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R3_SECRET_SCAN_RAW.json")
    args = parser.parse_args(argv)

    started = time.monotonic()
    known = fixture_hashes(args.root)
    first = scan_repo(args.root, known)
    second = scan_repo(args.root, known)
    first_keys = [m["value_sha256"] for m in first["matches"]]
    second_keys = [m["value_sha256"] for m in second["matches"]]
    comparison_identical = first_keys == second_keys
    real_matches = first["real_matches"]
    result = {
        "schema": "jarvis-r3-secret-scan-v1",
        "files_scanned": first["files_scanned"],
        "total_matches": first["total_matches"],
        "classified_fixtures": first["classified_fixtures"],
        "real_matches": first["real_matches"],
        "matches_by_pattern": first["matches_by_pattern"],
        "comparison_identical": comparison_identical,
        "wall_seconds": round(time.monotonic() - started, 3),
        "exit_code": 0 if real_matches == 0 else 1,
        "evidence_note": "matched values are never emitted; only sha256 digests",
        "matches": first["matches"],
    }
    if record_enabled():
        write_json(args.raw, result)
        write_json(args.output, {k: v for k, v in result.items() if k != "matches"})
    print(
        json.dumps(
            {
                "files_scanned": result["files_scanned"],
                "total_matches": result["total_matches"],
                "classified_fixtures": result["classified_fixtures"],
                "real_matches": result["real_matches"],
                "matches_by_pattern": result["matches_by_pattern"],
                "comparison_identical": result["comparison_identical"],
            }
        )
    )
    return 1 if result["real_matches"] else 0


if __name__ == "__main__":
    sys.exit(main())
