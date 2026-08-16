"""Secret and staged-body containment scan over R9-owned files."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    REPO_ROOT,
    TEMP_BASE,
    owned_r9_files,
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
        "R9_FULL_SUITE_PART1_JUNIT.xml",
        "R9_FULL_SUITE_PART2_JUNIT.xml",
        "R9_FULL_SUITE_COMBINED_JUNIT.xml",
        "R9_FOCUSED_TESTS_JUNIT.xml",
    }
)


def _known_secret_values() -> list[str]:
    manifest = TEMP_BASE / "stores" / "secrets_manifest.json"
    if not manifest.is_file():
        return []
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    return [str(value) for value in payload.get("secrets", [])]


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    findings: list[dict[str, Any]] = []
    for rel in owned_r9_files(REPO_ROOT):
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

    secrets = _known_secret_values()
    evidence_files = sorted(EVIDENCE_DIR.glob("*"))
    full_body_leaks: list[str] = []
    for evidence_path in evidence_files:
        if not evidence_path.is_file():
            continue
        content = evidence_path.read_bytes()
        for secret in secrets:
            if secret and secret.encode("utf-8") in content:
                full_body_leaks.append(f"{secret[:6]}... -> {evidence_path.name}")

    passed = not findings and not full_body_leaks
    raw = {
        "schema": "jarvis-r9-secret-scan-raw-v1",
        "scan_utc": utc_now(),
        "findings": findings,
        "full_body_leak_violations": full_body_leaks,
        "known_secret_value_count": len(secrets),
        "files_scanned": len(owned_r9_files(REPO_ROOT)),
        "pattern_scan_excluded": sorted(EXCLUDED_PATTERN_SCAN_FILES),
        "pattern_scan_exclusion_reason": (
            "pytest JUnit parameter IDs may embed fixture names; files remain in "
            "the full-body containment scan"
        ),
        "evidence_bytes_scanned": len(evidence_files),
    }
    write_json(EVIDENCE_DIR / "R9_SECRET_SCAN_RAW.json", raw)
    payload = {
        "schema": "jarvis-r9-secret-scan-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "findings_count": len(findings),
        "full_body_leak_count": len(full_body_leaks),
        "raw_output": "R9_SECRET_SCAN_RAW.json",
        "raw_output_sha256": sha256_file(EVIDENCE_DIR / "R9_SECRET_SCAN_RAW.json"),
    }
    write_json(EVIDENCE_DIR / "R9_SECRET_SCAN.json", payload)
    print(
        f"secret scan: passed={passed} findings={len(findings)} "
        f"full_body_leaks={len(full_body_leaks)} wall_seconds={payload['wall_seconds']}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
