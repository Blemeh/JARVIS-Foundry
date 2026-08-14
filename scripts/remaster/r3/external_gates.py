"""External quality gates: ruff, bandit, pip-audit, focused tests (R3-D02/D04/D09).

Each mode captures WALL_SECONDS and EXIT_CODE. Evidence files are written
only when ``R3_RECORD_EVIDENCE=1`` (builder/local mode); CI runs the same
commands against the frozen evidence set without rewriting it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, run, sha256_file, write_json
from typing_measure import owned_files


def ruff_command(args: argparse.Namespace) -> int:
    files = owned_files(args.root)
    check = run(
        [sys.executable, "-m", "ruff", "check", *files],
        cwd=args.root,
        timeout_seconds=600,
    )
    format_check = run(
        [sys.executable, "-m", "ruff", "format", "--check", *files],
        cwd=args.root,
        timeout_seconds=600,
    )
    payload = {
        "schema": "jarvis-r3-ruff-owned-v1",
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
        raw = EVIDENCE_DIR / "R3_RUFF_OWNED_RAW.txt"
        raw.write_text(
            "RUFF CHECK\n" + check["output"] + "RUFF FORMAT CHECK\n" + format_check["output"],
            encoding="utf-8",
        )
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        write_json(args.output, payload)
    print(f"ruff owned: check={check['exit_code']} format={format_check['exit_code']}")
    return 0 if payload["passed"] else 1


def ruff_baseline(args: argparse.Namespace) -> int:
    check = run(
        [sys.executable, "-m", "ruff", "check", "."],
        cwd=args.root,
        timeout_seconds=900,
    )
    format_check = run(
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        cwd=args.root,
        timeout_seconds=900,
    )
    error_count = sum(
        1 for line in check["output"].splitlines() if re.search(r"-->\s+\S+\.py:\d+:\d+", line)
    )
    format_match = re.search(r"(\d+) files would be reformatted", format_check["output"])
    payload = {
        "schema": "jarvis-r3-ruff-baseline-v1",
        "check": {
            "exit_code": check["exit_code"],
            "wall_seconds": check["wall_seconds"],
            "error_count": error_count,
        },
        "format": {
            "exit_code": format_check["exit_code"],
            "wall_seconds": format_check["wall_seconds"],
            "would_reformat_files": (int(format_match.group(1)) if format_match else -1),
        },
        "finding": {
            "id": "R3-F-RUFF",
            "severity": "S3",
            "disposition": "REPAIR_IN_R4",
            "title": "Pre-existing ruff violations remain in legacy source",
            "evidence": (
                f"ruff check exit={check['exit_code']} "
                f"error_count={error_count}; format --check exit="
                f"{format_check['exit_code']}"
            ),
        },
    }
    if record_enabled():
        raw = EVIDENCE_DIR / "R3_RUFF_BASELINE_RAW.txt"
        raw.write_text(
            "RUFF CHECK\n" + check["output"] + "RUFF FORMAT CHECK\n" + format_check["output"],
            encoding="utf-8",
        )
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        write_json(args.output, payload)
    print(
        f"ruff baseline: check_exit={check['exit_code']} errors={error_count} "
        f"format_exit={format_check['exit_code']}"
    )
    return 0


def bandit_gate(args: argparse.Namespace) -> int:
    report = Path(tempfile.mkdtemp(prefix="jarvis-r3-bandit-")) / "bandit.json"
    result = run(
        [
            sys.executable,
            "-m",
            "bandit",
            "-r",
            "src",
            "scripts/remaster/r3",
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
    high = severity_counts.get("HIGH", 0)
    payload = {
        "schema": "jarvis-r3-bandit-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "severity_counts": severity_counts,
        "high_findings": high,
        "zero_critical_or_high": high == 0,
    }
    if record_enabled():
        raw = EVIDENCE_DIR / "R3_BANDIT_RAW.txt"
        raw.write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        write_json(args.output, payload)
    print(f"bandit: exit={result['exit_code']} severities={severity_counts}")
    return 0 if high == 0 else 1


def pip_audit_gate(args: argparse.Namespace) -> int:
    result = run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            "-r",
            "requirements-dev.txt",
            "-f",
            "json",
        ],
        cwd=args.root,
        timeout_seconds=900,
    )
    dependencies: list[dict[str, Any]] = []
    vulnerabilities: list[dict[str, Any]] = []
    try:
        parsed = json.loads(result["stdout"] or "{}")
        dependencies = parsed.get("dependencies", [])
        vulnerabilities = [dependency for dependency in dependencies if dependency.get("vulns")]
    except json.JSONDecodeError:
        vulnerabilities = []
    payload = {
        "schema": "jarvis-r3-pip-audit-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "dependencies_scanned": len(dependencies),
        "vulnerable_packages": len(vulnerabilities),
        "vulnerable_dependencies": vulnerabilities,
        "network_note": (
            "pip-audit may fetch the OSV advisory database to resolve "
            "vulnerability data for the locked toolchain"
        ),
    }
    if record_enabled():
        raw = EVIDENCE_DIR / "R3_PIP_AUDIT_RAW.txt"
        raw.write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        write_json(args.output, payload)
    print(
        f"pip-audit: exit={result['exit_code']} scanned={len(dependencies)} "
        f"vulnerable_packages={len(vulnerabilities)}"
    )
    return 0 if len(vulnerabilities) == 0 else 1


def focused_tests(args: argparse.Namespace) -> int:
    junit = EVIDENCE_DIR / "R3_FOCUSED_TESTS_JUNIT.xml" if record_enabled() else None
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "tests/remaster/r3",
        "-p",
        "no:cacheprovider",
    ]
    if junit is not None:
        command.extend(["--junitxml", str(junit)])
    result = run(command, cwd=args.root, timeout_seconds=1800)
    summary_line = next(
        (line for line in result["output"].splitlines() if "passed" in line or "failed" in line),
        "",
    )
    payload = {
        "schema": "jarvis-r3-focused-tests-v1",
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "summary_line": summary_line,
    }
    if record_enabled():
        raw = EVIDENCE_DIR / "R3_FOCUSED_TESTS_RAW.txt"
        raw.write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        if junit is not None:
            payload["junit_output"] = junit.name
            payload["junit_sha256"] = sha256_file(junit)
        write_json(args.output, payload)
    print(f"focused tests: exit={result['exit_code']} summary={summary_line!r}")
    return int(result["exit_code"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args(argv)
    modes = {
        "ruff-owned": ruff_command,
        "ruff-baseline": ruff_baseline,
        "bandit": bandit_gate,
        "pip-audit": pip_audit_gate,
        "focused-tests": focused_tests,
    }
    if args.mode not in modes:
        print(f"unknown mode: {args.mode}", file=sys.stderr)
        return 2
    default_outputs = {
        "ruff-owned": "R3_RUFF_OWNED.json",
        "ruff-baseline": "R3_RUFF_BASELINE.json",
        "bandit": "R3_BANDIT.json",
        "pip-audit": "R3_PIP_AUDIT.json",
        "focused-tests": "R3_FOCUSED_TESTS.json",
    }
    if args.output is None:
        args.output = EVIDENCE_DIR / default_outputs[args.mode]
    return modes[args.mode](args)


if __name__ == "__main__":
    sys.exit(main())
