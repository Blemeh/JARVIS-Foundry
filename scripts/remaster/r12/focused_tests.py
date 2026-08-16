"""Run the R12 focused test suite and bind raw/junit evidence."""

from __future__ import annotations

import sys

from common import EVIDENCE_DIR, PYTHON, REPO_ROOT, TESTS_ROOT, run, sha256_file, write_json


def main(argv: list[str] | None = None) -> int:
    del argv
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    junit = EVIDENCE_DIR / "R12_FOCUSED_TESTS_JUNIT.xml"
    result = run(
        [
            PYTHON,
            "-m",
            "pytest",
            str(TESTS_ROOT),
            "-q",
            "--junitxml=" + str(junit),
        ],
        cwd=REPO_ROOT,
        timeout_seconds=3600,
    )
    raw = EVIDENCE_DIR / "R12_FOCUSED_TESTS_RAW.txt"
    raw.write_text(result["output"], encoding="utf-8")
    summary_line = ""
    for line in reversed(result["output"].splitlines()):
        if "%" in line or "passed" in line or "failed" in line:
            summary_line = line.strip()
            break
    payload = {
        "schema": "jarvis-r12-focused-tests-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "summary_line": summary_line,
        "raw_output": raw.name,
        "raw_output_sha256": sha256_file(raw),
        "junit_output": junit.name,
        "junit_sha256": sha256_file(junit) if junit.is_file() else None,
    }
    write_json(EVIDENCE_DIR / "R12_FOCUSED_TESTS.json", payload)
    print(f"focused tests: exit_code={payload['exit_code']} summary={summary_line!r}")
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
