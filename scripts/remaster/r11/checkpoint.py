"""Persist an incremental R11 progress checkpoint."""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, TEMP_BASE, sha256_file, utc_now, write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    evidence_entries = []
    for path in sorted(EVIDENCE_DIR.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            evidence_entries.append(
                {
                    "path": str(path.relative_to(EVIDENCE_DIR).as_posix()),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    payload: dict[str, Any] = {
        "schema": "jarvis-r11-progress-v1",
        "index": args.index,
        "note": args.note,
        "recorded_utc": utc_now(),
        "evidence_utc": "2026-08-16T18:00:00Z",
        "evidence_files": len(evidence_entries),
        "evidence_bytes": sum(entry["size"] for entry in evidence_entries),
        "evidence": evidence_entries,
        "temp_base": str(TEMP_BASE),
        "temp_exists": TEMP_BASE.exists(),
        "git_porcelain": status.stdout.splitlines(),
        "git_exit_code": status.returncode,
    }
    write_json(EVIDENCE_DIR / f"R11_PROGRESS_{args.index:02d}.json", payload)
    print(
        f"checkpoint {args.index:02d}: evidence_files={payload['evidence_files']} "
        f"bytes={payload['evidence_bytes']} wall_seconds=0"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
