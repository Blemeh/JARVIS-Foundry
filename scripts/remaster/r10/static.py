"""Static analysis for R10-owned source, scripts, tests, and web assets."""

from __future__ import annotations

import shutil
import sys
import time

from common import (
    EVIDENCE_DIR,
    PYTHON,
    REPO_ROOT,
    SCRIPTS_ROOT,
    SOURCE_ROOT,
    TESTS_ROOT,
    run,
    sha256_file,
    write_json,
)


def _gate(name: str, command: list[str]) -> dict[str, object]:
    result = run(command, cwd=REPO_ROOT, timeout_seconds=1200)
    raw_path = EVIDENCE_DIR / f"R10_{name}_RAW.txt"
    raw_path.write_text(result["output"], encoding="utf-8")
    payload = {
        "schema": f"jarvis-r10-{name.lower()}-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "passed": result["exit_code"] == 0,
        "raw_output": raw_path.name,
        "raw_output_sha256": sha256_file(raw_path),
    }
    write_json(EVIDENCE_DIR / f"R10_{name}.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    del argv
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
    mypy = _gate("MYPY", [PYTHON, "-m", "mypy", str(SOURCE_ROOT), "--show-error-codes"])
    bandit = _gate("BANDIT", [PYTHON, "-m", "bandit", "-r", str(SOURCE_ROOT), "-q"])
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
    advisory = run(
        [PYTHON, "-m", "bandit", "-r", str(SCRIPTS_ROOT), "-q"],
        cwd=REPO_ROOT,
        timeout_seconds=1200,
    )
    node = shutil.which("node")
    js_status = "NOT_TESTED"
    js_result: dict[str, object] = {
        "available": False,
        "note": "node toolchain not found",
    }
    if node:
        js_path = SOURCE_ROOT / "static" / "app.js"
        js_result = run([node, "--check", str(js_path)], cwd=REPO_ROOT, timeout_seconds=120)
        js_status = "PASS" if js_result["exit_code"] == 0 else "FAIL"
    css_toolchain = shutil.which("stylelint")
    css_status = "NOT_TESTED"
    css_result: dict[str, object] = {
        "available": False,
        "note": "no CSS linter installed in the R10 toolchain",
    }
    if css_toolchain:
        css_result = run(
            [css_toolchain, str(SOURCE_ROOT / "static" / "styles.css")],
            cwd=REPO_ROOT,
            timeout_seconds=120,
        )
        css_status = "PASS" if css_result["exit_code"] == 0 else "FAIL"
    passed = all(item["passed"] for item in (ruff, format_check, mypy, bandit))
    passed = passed and js_status == "PASS"
    aggregate = {
        "schema": "jarvis-r10-static-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "subgates": {
            "ruff": ruff,
            "ruff_format": format_check,
            "mypy": mypy,
            "bandit": bandit,
            "bandit_scripts_advisory": {
                "exit_code": advisory["exit_code"],
                "passed": advisory["exit_code"] == 0,
                "note": "advisory only; mandatory bandit gate covers command_center source",
            },
            "javascript_node_check": {
                "status": js_status,
                "exit_code": js_result.get("exit_code", -1),
                "passed": js_status == "PASS",
                "output": js_result.get("output", ""),
            },
            "css_linter": {
                "status": css_status,
                "exit_code": css_result.get("exit_code", -1),
                "passed": css_status == "PASS",
                "note": css_result.get("note", "honest NOT_TESTED when no linter is installed"),
            },
        },
    }
    write_json(EVIDENCE_DIR / "R10_STATIC.json", aggregate)
    print(
        f"static: ruff={ruff['passed']} format={format_check['passed']} "
        f"mypy={mypy['passed']} bandit={bandit['passed']} js={js_status} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
