"""Run the full repository suite (foundry + R6 + R7) in the isolated venv."""

from __future__ import annotations

import sys

from common import EVIDENCE_DIR, REPO_ROOT, run, sha256_file, write_json

PYTHON = "C:/Users/b/AppData/Local/Temp/jarvis-r3-venv/Scripts/python.exe"


def main(argv: list[str] | None = None) -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    part1 = EVIDENCE_DIR / "R7_FULL_SUITE_PART1_JUNIT.xml"
    part2 = EVIDENCE_DIR / "R7_FULL_SUITE_PART2_JUNIT.xml"
    first = run(
        [
            PYTHON,
            "-m",
            "pytest",
            "tests/foundry",
            "tests/remaster/r6",
            "-q",
            "--junitxml=" + str(part1),
        ],
        cwd=REPO_ROOT,
        timeout_seconds=3600,
    )
    second = run(
        [
            PYTHON,
            "-m",
            "pytest",
            "tests/remaster/r7",
            "-q",
            "--junitxml=" + str(part2),
        ],
        cwd=REPO_ROOT,
        timeout_seconds=1800,
    )
    result = {
        "command": [
            f"{PYTHON} -m pytest tests/foundry tests/remaster/r6 -q",
            f"{PYTHON} -m pytest tests/remaster/r7 -q",
        ],
        "exit_code": 0 if (first["exit_code"] == 0 and second["exit_code"] == 0) else 1,
        "wall_seconds": round(first["wall_seconds"] + second["wall_seconds"], 3),
        "output": (
            "PART1 (foundry + r6):\n" + first["output"] + "\nPART2 (r7):\n" + second["output"]
        ),
        "parts": {
            "foundry_r6": {
                "exit_code": first["exit_code"],
                "wall_seconds": first["wall_seconds"],
                "junit_output": part1.name,
            },
            "r7": {
                "exit_code": second["exit_code"],
                "wall_seconds": second["wall_seconds"],
                "junit_output": part2.name,
            },
        },
    }
    raw = EVIDENCE_DIR / "R7_FULL_SUITE_RAW.txt"
    raw.write_text(result["output"], encoding="utf-8")
    summary_line = ""
    for line in reversed(result["output"].splitlines()):
        if "%" in line or "passed" in line or "failed" in line:
            summary_line = line.strip()
            break
    payload = {
        "schema": "jarvis-r7-full-suite-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "summary_line": summary_line,
        "raw_output": raw.name,
        "raw_output_sha256": sha256_file(raw),
        "junit_output": "R7_FULL_SUITE_PART1_JUNIT.xml + R7_FULL_SUITE_PART2_JUNIT.xml",
        "junit_sha256": (
            sha256_file(part1) if part1.is_file() else None,
            sha256_file(part2) if part2.is_file() else None,
        ),
    }
    write_json(EVIDENCE_DIR / "R7_FULL_SUITE.json", payload)
    print(f"full suite: exit_code={payload['exit_code']} summary={summary_line!r}")
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
