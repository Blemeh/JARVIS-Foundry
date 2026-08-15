"""R5 secret scan: no plaintext secret values in owned artifacts."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, owned_r5_files, sha256_file, write_json

PATTERNS: dict[str, str] = {
    "openai_sk": r"sk-[A-Za-z0-9]{20,}",
    "api_key_assignment": r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"][^'\"]{16,}",
    "opencode_env_value": r"OPENCODE_GO_API_KEY\s*=\s*\S{16,}",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R5_SECRET_SCAN.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R5_SECRET_SCAN_RAW.txt")
    args = parser.parse_args(argv)

    matches: list[dict[str, str]] = []
    scanned: list[dict[str, Any]] = []
    for relative in owned_r5_files(REPO_ROOT):
        path = REPO_ROOT / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = ""
        for pattern_name, pattern in PATTERNS.items():
            for match in re.findall(pattern, text):
                matches.append(
                    {
                        "file": relative,
                        "pattern": pattern_name,
                        "match_snippet": match[:40],
                    }
                )
        scanned.append({"file": relative, "sha256": sha256_file(path)})
    lines = [f"scanned={len(scanned)} matches={len(matches)}"]
    raw_text = "\n".join(lines) + "\n"
    payload = {
        "schema": "jarvis-r5-secret-scan-v1",
        "files_scanned": len(scanned),
        "matches": matches,
        "passed": not matches,
    }
    write_json(args.output, payload)
    args.raw.write_text(raw_text, encoding="utf-8")
    print(raw_text)
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
