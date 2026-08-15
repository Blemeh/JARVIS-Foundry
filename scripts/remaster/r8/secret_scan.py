"""Secret and staged-body containment scan over R8-owned files."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    REPO_ROOT,
    TEMP_BASE,
    owned_r8_files,
    sha256_file,
    utc_now,
    write_json,
)

SECRET_PATTERNS: list[tuple[str, str]] = [
    ("openai_key", r"sk-[A-Za-z0-9]{16,}"),
    ("aws_key", r"AKIA[0-9A-Z]{16}"),
    ("slack_token", r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("google_api_key", r"AIza[0-9A-Za-z_-]{20,}"),
    (
        "password_assignment",
        r"(?i)(password|passwd|secret|api[_-]?key|token)\s*=\s*['\"][^'\"]{8,}['\"]",
    ),
]

EXCLUDED_PATTERN_SCAN_FILES: frozenset[str] = frozenset(
    {
        "R8_FULL_SUITE_PART1_JUNIT.xml",
        "R8_FULL_SUITE_PART2_JUNIT.xml",
        "R8_FULL_SUITE_COMBINED_JUNIT.xml",
        "R8_FOCUSED_TESTS_JUNIT.xml",
    }
)


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    findings: list[dict[str, Any]] = []
    for rel in owned_r8_files(REPO_ROOT):
        if Path(rel).name in EXCLUDED_PATTERN_SCAN_FILES:
            continue
        path = REPO_ROOT / rel
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS:
            for match in re.finditer(pattern, text):
                findings.append(
                    {
                        "path": rel,
                        "pattern": name,
                        "line": text.count("\n", 0, match.start()) + 1,
                        "redacted": match.group(0)[:6] + "...",
                    }
                )

    bodies: dict[str, bytes] = {}
    projects_root = TEMP_BASE / "projects"
    if projects_root.is_dir():
        for path in projects_root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".md", ".toml", ".txt"}:
                bodies[path.relative_to(projects_root).as_posix()] = path.read_bytes()
    evidence: list[tuple[Path, bytes]] = [
        (path, path.read_bytes()) for path in EVIDENCE_DIR.glob("*") if path.is_file()
    ]
    full_body_leaks: list[str] = []
    for rel_path, body in bodies.items():
        if not body or len(body.strip()) < 8:
            continue
        for evidence_path, content in evidence:
            if body in content:
                full_body_leaks.append(f"{rel_path} -> {evidence_path.name}")

    passed = not findings and not full_body_leaks
    raw = {
        "schema": "jarvis-r8-secret-scan-raw-v1",
        "scan_utc": utc_now(),
        "findings": findings,
        "full_body_leak_violations": full_body_leaks,
        "files_scanned": len(owned_r8_files(REPO_ROOT)),
        "pattern_scan_excluded": sorted(EXCLUDED_PATTERN_SCAN_FILES),
        "pattern_scan_exclusion_reason": (
            "pytest JUnit parameter IDs may embed fixture names; files remain in "
            "the full-body containment scan"
        ),
        "evidence_bytes_scanned": len(evidence),
    }
    write_json(EVIDENCE_DIR / "R8_SECRET_SCAN_RAW.json", raw)
    payload = {
        "schema": "jarvis-r8-secret-scan-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "findings_count": len(findings),
        "full_body_leak_count": len(full_body_leaks),
        "raw_output": "R8_SECRET_SCAN_RAW.json",
        "raw_output_sha256": sha256_file(EVIDENCE_DIR / "R8_SECRET_SCAN_RAW.json"),
    }
    write_json(EVIDENCE_DIR / "R8_SECRET_SCAN.json", payload)
    print(
        f"secret scan: passed={passed} findings={len(findings)} "
        f"full_body_leaks={len(full_body_leaks)} wall_seconds={payload['wall_seconds']}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
