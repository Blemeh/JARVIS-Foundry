"""Regenerate focused/full-suite evidence JSON after test-set changes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VENV_PY = Path(r"C:\Users\b\AppData\Local\Temp\jarvis-r3-venv\Scripts\python.exe")
EVIDENCE = ROOT / "docs" / "remaster" / "evidence" / "r5"
BASETEMP = Path(r"C:\Users\b\AppData\Local\Temp\jarvis-r5-evidence-basetemp")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_pytest(scope: str, names: list[str], junit_name: str, raw_name: str) -> dict:
    junit = EVIDENCE / junit_name
    raw = EVIDENCE / raw_name
    started = time.monotonic()
    completed = subprocess.run(
        [
            str(VENV_PY),
            "-B",
            "-m",
            "pytest",
            *names,
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(BASETEMP),
            "-q",
            "--junitxml",
            str(junit),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=1800,
        check=False,
    )
    raw.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    summary = (completed.stdout + completed.stderr).strip().splitlines()
    summary_line = summary[-1] if summary else ""
    return {
        "schema": f"jarvis-r5-{scope}-v1",
        "exit_code": completed.returncode,
        "wall_seconds": round(time.monotonic() - started, 3),
        "summary_line": summary_line,
        "raw_output": raw_name,
        "raw_output_sha256": sha256_file(raw),
        "junit_output": junit_name,
        "junit_sha256": sha256_file(junit),
    }


def main() -> int:
    focused = run_pytest(
        "focused-tests",
        ["tests/remaster/r5"],
        "R5_FOCUSED_TESTS_JUNIT.xml",
        "R5_FOCUSED_TESTS_RAW.txt",
    )
    (EVIDENCE / "R5_FOCUSED_TESTS.json").write_text(
        json.dumps(focused, indent=2) + "\n", encoding="utf-8"
    )
    print(f"focused exit={focused['exit_code']} summary={focused['summary_line']}")

    full = run_pytest(
        "full-suite",
        ["tests/foundry", "tests/remaster/r3", "tests/remaster/r4", "tests/remaster/r5"],
        "R5_FULL_SUITE_JUNIT.xml",
        "R5_FULL_SUITE_RAW.txt",
    )
    (EVIDENCE / "R5_FULL_SUITE.json").write_text(
        json.dumps(full, indent=2) + "\n", encoding="utf-8"
    )
    print(f"full exit={full['exit_code']} summary={full['summary_line']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
