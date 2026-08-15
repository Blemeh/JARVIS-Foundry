"""Static analysis for R7-owned source, scripts, and tests."""

from __future__ import annotations

import sys
import time

from common import (
    EVIDENCE_DIR,
    REPO_ROOT,
    SCRIPTS_ROOT,
    SOURCE_ROOT,
    TESTS_ROOT,
    run,
    sha256_file,
    write_json,
)

PYTHON = "C:/Users/b/AppData/Local/Temp/jarvis-r3-venv/Scripts/python.exe"


def _gate(name: str, command: list[str]) -> dict[str, object]:
    result = run(command, cwd=REPO_ROOT, timeout_seconds=900)
    raw_path = EVIDENCE_DIR / f"R7_{name}_RAW.txt"
    raw_path.write_text(result["output"], encoding="utf-8")
    payload = {
        "schema": f"jarvis-r7-{name.lower()}-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "passed": result["exit_code"] == 0,
        "raw_output": raw_path.name,
        "raw_output_sha256": sha256_file(raw_path),
    }
    write_json(EVIDENCE_DIR / f"R7_{name}.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    ruff = _gate(
        "RUFF",
        [
            PYTHON,
            "-m",
            "ruff",
            "check",
            str(SOURCE_ROOT),
            str(SCRIPTS_ROOT),
            str(TESTS_ROOT),
        ],
    )
    mypy = _gate(
        "MYPY",
        [PYTHON, "-m", "mypy", str(SOURCE_ROOT), "--show-error-codes"],
    )
    bandit = _gate(
        "BANDIT",
        [PYTHON, "-m", "bandit", "-r", str(SOURCE_ROOT), "-q"],
    )
    format_check = _gate(
        "RUFF_FORMAT",
        [
            PYTHON,
            "-m",
            "ruff",
            "format",
            "--check",
            str(SOURCE_ROOT),
            str(SCRIPTS_ROOT),
            str(TESTS_ROOT),
        ],
    )
    passed = all(item["passed"] for item in (ruff, format_check, mypy, bandit))
    aggregate = {
        "schema": "jarvis-r7-static-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "subgates": {
            "ruff": ruff,
            "ruff_format": format_check,
            "mypy": mypy,
            "bandit": bandit,
        },
    }
    write_json(EVIDENCE_DIR / "R7_STATIC.json", aggregate)
    print(
        f"static: ruff={ruff['passed']} format={format_check['passed']} "
        f"mypy={mypy['passed']} bandit={bandit['passed']} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
