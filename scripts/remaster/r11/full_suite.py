"""Run the full repository suite split (foundry+r6..r10, then r11) and combined."""

from __future__ import annotations

import sys

from common import EVIDENCE_DIR, PYTHON, REPO_ROOT, run, sha256_file, write_json


def _summary(output: str) -> str:
    for line in reversed(output.splitlines()):
        if "%" in line or "passed" in line or "failed" in line:
            return line.strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    del argv
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    part1 = EVIDENCE_DIR / "R11_FULL_SUITE_PART1_JUNIT.xml"
    part2 = EVIDENCE_DIR / "R11_FULL_SUITE_PART2_JUNIT.xml"
    combined = EVIDENCE_DIR / "R11_FULL_SUITE_COMBINED_JUNIT.xml"
    first = run(
        [
            PYTHON,
            "-m",
            "pytest",
            "tests/foundry",
            "tests/remaster/r6",
            "tests/remaster/r7",
            "tests/remaster/r8",
            "tests/remaster/r9",
            "tests/remaster/r10",
            "-q",
            "--junitxml=" + str(part1),
        ],
        cwd=REPO_ROOT,
        timeout_seconds=10800,
    )
    second = run(
        [
            PYTHON,
            "-m",
            "pytest",
            "tests/remaster/r11",
            "-q",
            "--junitxml=" + str(part2),
        ],
        cwd=REPO_ROOT,
        timeout_seconds=3600,
    )
    third = run(
        [
            PYTHON,
            "-m",
            "pytest",
            "tests/foundry",
            "tests/remaster/r6",
            "tests/remaster/r7",
            "tests/remaster/r8",
            "tests/remaster/r9",
            "tests/remaster/r10",
            "tests/remaster/r11",
            "-q",
            "--junitxml=" + str(combined),
        ],
        cwd=REPO_ROOT,
        timeout_seconds=14400,
    )
    output = (
        "PART1 (foundry + r6 + r7 + r8 + r9 + r10):\n"
        + first["output"]
        + "\nPART2 (r11):\n"
        + second["output"]
        + "\nCOMBINED (foundry + r6 + r7 + r8 + r9 + r10 + r11):\n"
        + third["output"]
    )
    raw = EVIDENCE_DIR / "R11_FULL_SUITE_RAW.txt"
    raw.write_text(output, encoding="utf-8")
    payload = {
        "schema": "jarvis-r11-full-suite-v1",
        "exit_code": 0
        if (first["exit_code"] == 0 and second["exit_code"] == 0 and third["exit_code"] == 0)
        else 1,
        "wall_seconds": round(
            first["wall_seconds"] + second["wall_seconds"] + third["wall_seconds"], 3
        ),
        "summary_line": _summary(output),
        "raw_output": raw.name,
        "raw_output_sha256": sha256_file(raw),
        "parts": {
            "foundry_r6_r7_r8_r9_r10": {
                "exit_code": first["exit_code"],
                "wall_seconds": first["wall_seconds"],
                "junit_output": part1.name,
            },
            "r11": {
                "exit_code": second["exit_code"],
                "wall_seconds": second["wall_seconds"],
                "junit_output": part2.name,
            },
            "combined_all": {
                "exit_code": third["exit_code"],
                "wall_seconds": third["wall_seconds"],
                "junit_output": combined.name,
            },
        },
        "junit_sha256": {
            "part1": sha256_file(part1) if part1.is_file() else None,
            "part2": sha256_file(part2) if part2.is_file() else None,
            "combined": sha256_file(combined) if combined.is_file() else None,
        },
    }
    write_json(EVIDENCE_DIR / "R11_FULL_SUITE.json", payload)
    print(f"full suite: exit_code={payload['exit_code']} summary={payload['summary_line']!r}")
    return payload["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
