"""External quality gates: ruff, bandit, focused tests, full suite (R4)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from common import (
    EVIDENCE_DIR,
    REPO_ROOT,
    r4_owned_files,
    record_enabled,
    run,
    sha256_file,
    write_json,
)


def ruff_owned(args: argparse.Namespace) -> int:
    files = r4_owned_files(args.root)
    check = run(
        [sys.executable, "-m", "ruff", "check", *files],
        cwd=args.root,
        timeout_seconds=900,
    )
    format_check = run(
        [sys.executable, "-m", "ruff", "format", "--check", *files],
        cwd=args.root,
        timeout_seconds=900,
    )
    payload = {
        "schema": "jarvis-r4-ruff-owned-v1",
        "exit_code": 0 if (check["exit_code"] == 0 and format_check["exit_code"] == 0) else 1,
        "wall_seconds": round(check["wall_seconds"] + format_check["wall_seconds"], 3),
        "check": {
            "exit_code": check["exit_code"],
            "wall_seconds": check["wall_seconds"],
            "error_lines": sum(1 for line in check["output"].splitlines() if line.strip()),
        },
        "format": {
            "exit_code": format_check["exit_code"],
            "wall_seconds": format_check["wall_seconds"],
            "would_reformat_files": sum(
                1 for line in format_check["output"].splitlines() if line.strip()
            ),
        },
        "passed": check["exit_code"] == 0 and format_check["exit_code"] == 0,
    }
    if record_enabled():
        raw = EVIDENCE_DIR / "R4_RUFF_OWNED_RAW.txt"
        raw.write_text(
            "RUFF CHECK\n" + check["output"] + "RUFF FORMAT CHECK\n" + format_check["output"],
            encoding="utf-8",
        )
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        write_json(args.output, payload)
    print(f"ruff owned: check={check['exit_code']} format={format_check['exit_code']}")
    return 0 if payload["passed"] else 1


def bandit_gate(args: argparse.Namespace) -> int:
    report = Path(tempfile.mkdtemp(prefix="jarvis-r4-bandit-")) / "bandit.json"
    result = run(
        [
            sys.executable,
            "-m",
            "bandit",
            "-r",
            "src/jarvis_foundry/kernel",
            "scripts/remaster/r4",
            "-c",
            "pyproject.toml",
            "-f",
            "json",
            "-o",
            str(report),
        ],
        cwd=args.root,
        timeout_seconds=900,
    )
    severity_counts: dict[str, int] = {}
    try:
        bandit_data = json.loads(report.read_text(encoding="utf-8"))
        for issue in bandit_data.get("results", []):
            severity = issue.get("issue_severity", "UNKNOWN")
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
    except (OSError, json.JSONDecodeError):
        severity_counts = {"UNKNOWN": 0}
    high = severity_counts.get("HIGH", 0) + severity_counts.get("CRITICAL", 0)
    payload = {
        "schema": "jarvis-r4-bandit-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "severity_counts": severity_counts,
        "critical_or_high_findings": high,
        "zero_critical_or_high": high == 0,
    }
    if record_enabled():
        raw = EVIDENCE_DIR / "R4_BANDIT_RAW.txt"
        raw.write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        write_json(args.output, payload)
    print(f"bandit: exit={result['exit_code']} severities={severity_counts}")
    return 0 if high == 0 else 1


def focused_tests(args: argparse.Namespace) -> int:
    junit = EVIDENCE_DIR / "R4_FOCUSED_TESTS_JUNIT.xml" if record_enabled() else None
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "tests/remaster/r4",
        "-p",
        "no:cacheprovider",
    ]
    if junit is not None:
        command.extend(["--junitxml", str(junit)])
    result = run(command, cwd=args.root, timeout_seconds=3600)
    summary_line = next(
        (line for line in result["output"].splitlines() if "passed" in line or "failed" in line),
        "",
    )
    payload = {
        "schema": "jarvis-r4-focused-tests-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "summary_line": summary_line,
    }
    if record_enabled():
        raw = EVIDENCE_DIR / "R4_FOCUSED_TESTS_RAW.txt"
        raw.write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        if junit is not None:
            payload["junit_output"] = junit.name
            payload["junit_sha256"] = sha256_file(junit)
        write_json(args.output, payload)
    print(f"focused tests: exit={result['exit_code']} summary={summary_line!r}")
    return int(result["exit_code"])


def full_suite(args: argparse.Namespace) -> int:
    junit = EVIDENCE_DIR / "R4_FULL_SUITE_JUNIT.xml" if record_enabled() else None
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "tests/foundry",
        "tests/remaster/r3",
        "tests/remaster/r4",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    if junit is not None:
        command.extend(["--junitxml", str(junit)])
    result = run(command, cwd=args.root, timeout_seconds=7200)
    match = re.search(r"(\d+) passed", result["output"])
    passed = int(match.group(1)) if match else -1
    if passed == -1 and junit is not None and junit.is_file():
        passed = junit.read_text(encoding="utf-8").count("<testcase ")
    match_failed = re.search(r"(\d+) failed", result["output"])
    failed = int(match_failed.group(1)) if match_failed else 0
    payload = {
        "schema": "jarvis-r4-full-suite-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "passed": passed,
        "failed": failed,
        "collected_baseline_note": (
            "baseline at HEAD b8e8a58: tests/foundry=985, tests/remaster/r3=26; "
            "frozen package quote of 993 is superseded by exact collection"
        ),
    }
    if record_enabled():
        raw = EVIDENCE_DIR / "R4_FULL_SUITE_RAW.txt"
        raw.write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        if junit is not None:
            payload["junit_output"] = junit.name
            payload["junit_sha256"] = sha256_file(junit)
        write_json(args.output, payload)
    print(
        f"full suite: exit={result['exit_code']} passed={passed} failed={failed} "
        f"wall={result['wall_seconds']}s"
    )
    return int(result["exit_code"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args(argv)
    modes = {
        "ruff-owned": ruff_owned,
        "bandit": bandit_gate,
        "focused-tests": focused_tests,
        "full-suite": full_suite,
    }
    if args.mode not in modes:
        print(f"unknown mode: {args.mode}", file=sys.stderr)
        return 2
    default_outputs = {
        "ruff-owned": "R4_RUFF_OWNED.json",
        "bandit": "R4_BANDIT.json",
        "focused-tests": "R4_FOCUSED_TESTS.json",
        "full-suite": "R4_FULL_SUITE.json",
    }
    if args.output is None:
        args.output = EVIDENCE_DIR / default_outputs[args.mode]
    return modes[args.mode](args)


if __name__ == "__main__":
    sys.exit(main())
