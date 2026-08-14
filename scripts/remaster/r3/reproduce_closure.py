"""Reproduce the R3 closure twice and report byte-identical hashes.

Runs close_r3_evidence.py twice with the isolated venv and compares the report
and manifest hashes. This is the deterministic idempotency check the R3 round-2
verifier required.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
VENV_PY = Path(r"C:\Users\b\AppData\Local\Temp\jarvis-r3-venv\Scripts\python.exe")
CLOSE = ROOT / "scripts" / "remaster" / "r3" / "close_r3_evidence.py"
REPORT = ROOT / "docs" / "remaster" / "evidence" / "r3" / "R3_BUILDER_REPORT.json"
MANIFEST = ROOT / "docs" / "remaster" / "evidence" / "r3" / "R3_ARTIFACT_MANIFEST.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    first: dict[str, str] = {}
    reports: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    for run_no in (1, 2):
        completed = subprocess.run(
            [str(VENV_PY), "-B", str(CLOSE)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=1200,
            check=False,
        )
        print(f"run {run_no} exit={completed.returncode}")
        report_sha = sha(REPORT)
        manifest_sha = sha(MANIFEST)
        reports.append(json.loads(REPORT.read_text(encoding="utf-8")))
        manifests.append(json.loads(MANIFEST.read_text(encoding="utf-8")))
        print(f"  report={report_sha}")
        print(f"  manifest={manifest_sha}")
        if run_no == 1:
            first = {"report": report_sha, "manifest": manifest_sha}
        else:
            identical = first["report"] == report_sha and first["manifest"] == manifest_sha
            print(f"IDEMPOTENT={identical}")
            a = reports[0]
            b = reports[1]
            for key in sorted(set(a) | set(b)):
                if isinstance(a.get(key), dict) and isinstance(b.get(key), dict):
                    for k2 in sorted(set(a[key]) | set(b[key])):
                        va = a[key].get(k2)
                        vb = b[key].get(k2)
                        if va != vb:
                            print(
                                f"DIFF {key}.{k2}: {json.dumps(va)[:120]} | {json.dumps(vb)[:120]}"
                            )
                elif a.get(key) != b.get(key):
                    print(
                        f"DIFF {key}: {json.dumps(a.get(key))[:120]} | {json.dumps(b.get(key))[:120]}"
                    )
            return 0 if (identical and completed.returncode == 0) else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
