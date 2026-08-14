"""Trusted-kernel branch coverage measurement (R3-D05/C05).

The integration threshold remains 95%. R3 records the honest current
percentage as a baseline finding; the ``--fail-below`` mode is what gates
the rebuilt R4 kernel.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, run, sha256_file, write_json

TRUSTED_KERNEL_COV = [
    "jarvis_foundry.events",
    "jarvis_foundry.projections",
    "jarvis_foundry.database",
    "jarvis_foundry.owner_controls",
    "jarvis_foundry.effects",
    "jarvis_foundry.errors",
    "jarvis_foundry.observability",
    "jarvis_foundry.control.action_envelope",
    "jarvis_foundry.control.broker",
    "jarvis_foundry.control.capability_broker",
    "jarvis_foundry.control.policy",
    "jarvis_foundry.control.registration",
    "jarvis_foundry.supervisor.estop",
    "jarvis_foundry.supervisor.leases",
    "jarvis_foundry.supervisor.controls",
]
THRESHOLD = 95.0


def branch_coverage(report_path: Path) -> tuple[float, int, int, float, int, int]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    covered = 0
    total = 0
    lines_covered = 0
    lines_total = 0
    for file_data in report["files"].values():
        covered += file_data["summary"]["covered_branches"]
        total += file_data["summary"]["num_branches"]
        lines_covered += file_data["summary"]["covered_lines"]
        lines_total += file_data["summary"]["num_statements"]
    branch_pct = 100.0 * covered / total if total else 0.0
    line_pct = 100.0 * lines_covered / lines_total if lines_total else 0.0
    return branch_pct, covered, total, line_pct, lines_covered, lines_total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R3_COVERAGE.json")
    parser.add_argument("--fail-below", type=float, default=None)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)
    root = args.root
    coverage_name = "r3_coverage_tmp.json"
    coverage_json = root / coverage_name
    command = [
        str(args.python),
        "-B",
        "-m",
        "pytest",
        "tests/remaster/r3",
        "-q",
        *[f"--cov={module}" for module in TRUSTED_KERNEL_COV],
        "--cov-branch",
        f"--cov-report=json:{coverage_name}",
        "-p",
        "no:cacheprovider",
    ]
    result = run(command, cwd=root, timeout_seconds=1800)
    branch_pct, covered, total, line_pct, lines_covered, lines_total = branch_coverage(
        coverage_json
    )
    payload: dict[str, Any] = {
        "schema": "jarvis-r3-coverage-v1",
        "branch_percent": round(branch_pct, 3),
        "branches_covered": covered,
        "branches_total": total,
        "line_percent": round(line_pct, 3),
        "lines_covered": lines_covered,
        "lines_total": lines_total,
        "threshold_percent": THRESHOLD,
        "below_threshold": branch_pct < THRESHOLD,
        "finding": None,
        "pytest_exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
    }
    if branch_pct < THRESHOLD:
        payload["finding"] = {
            "id": "R3-F-COVERAGE",
            "severity": "S2",
            "disposition": "REPAIR_IN_R4",
            "title": "Trusted-kernel branch coverage is below the 95% threshold",
            "evidence": (
                f"branch coverage {round(branch_pct, 3)}% "
                f"({covered}/{total} branches); threshold stays {THRESHOLD}%"
            ),
        }
    if record_enabled():
        shutil.copy2(coverage_json, EVIDENCE_DIR / "R3_COVERAGE_JSON.json")
        raw_name = "R3_COVERAGE_RAW.txt"
        (EVIDENCE_DIR / raw_name).write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw_name
        payload["raw_output_sha256"] = sha256_file(EVIDENCE_DIR / raw_name)
        write_json(args.output, payload)
    print(
        f"branch coverage: {round(branch_pct, 3)}% ({covered}/{total}) "
        f"line: {round(line_pct, 3)}% pytest_exit={result['exit_code']} "
        f"wall={result['wall_seconds']}s"
    )
    coverage_json.unlink(missing_ok=True)
    (root / ".coverage").unlink(missing_ok=True)
    if args.fail_below is not None and branch_pct < args.fail_below:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
