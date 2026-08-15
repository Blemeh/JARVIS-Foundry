"""R4 kernel branch coverage gate (>=95%, threshold never lowered)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, run, sha256_file, write_json

THRESHOLD = 95.0
EXCLUDED_INTERFACE_FILES = {"ports.py"}


def branch_coverage(report_path: Path) -> tuple[float, int, int, float, int, int]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    covered = 0
    total = 0
    lines_covered = 0
    lines_total = 0
    for file_path, file_data in report["files"].items():
        if Path(file_path).name in EXCLUDED_INTERFACE_FILES:
            continue
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
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R4_COVERAGE.json")
    parser.add_argument("--fail-below", type=float, default=THRESHOLD)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)
    root = args.root
    coverage_name = "r4_coverage_tmp.json"
    coverage_json = root / coverage_name
    result = run(
        [
            str(args.python),
            "-B",
            "-m",
            "pytest",
            "tests/remaster/r4",
            "-q",
            "--cov=jarvis_foundry.kernel",
            "--cov-branch",
            f"--cov-report=json:{coverage_name}",
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        timeout_seconds=3600,
    )
    branch_pct, covered, total, line_pct, lines_covered, lines_total = branch_coverage(
        coverage_json
    )
    payload: dict[str, Any] = {
        "schema": "jarvis-r4-coverage-v1",
        "exit_code": 0 if branch_pct >= THRESHOLD and result["exit_code"] == 0 else 1,
        "branch_percent": round(branch_pct, 3),
        "branches_covered": covered,
        "branches_total": total,
        "line_percent": round(line_pct, 3),
        "lines_covered": lines_covered,
        "lines_total": lines_total,
        "threshold_percent": THRESHOLD,
        "interface_exclusions": sorted(EXCLUDED_INTERFACE_FILES),
        "exclusion_reason": (
            "ports.py contains only Protocol interface declarations with no executable "
            "runtime branches; executable kernel modules are fully measured"
        ),
        "below_threshold": branch_pct < THRESHOLD,
        "pytest_exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
    }
    if record_enabled():
        shutil.copy2(coverage_json, EVIDENCE_DIR / "R4_COVERAGE_JSON.json")
        raw_name = "R4_COVERAGE_RAW.txt"
        (EVIDENCE_DIR / raw_name).write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw_name
        payload["raw_output_sha256"] = sha256_file(EVIDENCE_DIR / raw_name)
        write_json(args.output, payload)
    print(
        f"kernel branch coverage: {round(branch_pct, 3)}% ({covered}/{total}) "
        f"line: {round(line_pct, 3)}% pytest_exit={result['exit_code']} "
        f"wall={result['wall_seconds']}s"
    )
    coverage_json.unlink(missing_ok=True)
    (root / ".coverage").unlink(missing_ok=True)
    return 0 if branch_pct >= args.fail_below and result["exit_code"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
