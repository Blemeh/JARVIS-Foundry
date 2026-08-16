"""R11-G04: fresh independent verifier reproduces cycles and rollback/restore."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    PYTHON,
    REPO_ROOT,
    TEMP_BASE,
    read_json,
    sha256_file,
    write_json,
)

VERIFIER_ID = "r11-fresh-verifier-reproduction"
WORK_PACKAGE_SHA256 = "9512BAAC8CF0DD902E82E46A9E7B8ED9D837A6BA5C62A2E7607427B865EC6ABE"


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    payload = read_json(EVIDENCE_DIR / "R11_CYCLES.json")
    per_candidate_checks: dict[str, bool] = {}
    for record in payload["cycles"]:
        verification = record["verification"]
        per_candidate_checks[str(record["cycle_id"])] = bool(
            str(verification["verifier_id"]).startswith("r11-fresh-verifier-")
            and str(verification["verifier_id"]) != "r11_builder_fresh"
            and verification["verdict"] in {"PASS", "FAIL"}
        )
    event_source_ok = all(
        event["source_type"] == "independent_verifier"
        for event in payload["events"]
        if event["event_type"] == "evolution_live.independently_verified"
    )

    mirror_root = TEMP_BASE / "gates" / "g04" / "mirror"
    completed = subprocess.run(
        [
            PYTHON,
            str(REPO_ROOT / "scripts" / "remaster" / "r11" / "verifier.py"),
            "--evidence-dir",
            str(EVIDENCE_DIR),
            "--mirror-root",
            str(mirror_root),
            "--worktree",
            str(REPO_ROOT),
            "--python",
            PYTHON,
            "--expected-work-package-sha",
            WORK_PACKAGE_SHA256,
            "--verifier-id",
            VERIFIER_ID,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=5400,
        check=False,
    )
    verdict_path = EVIDENCE_DIR / "R11_VERIFIER_REPRODUCTION.json"
    verdict = read_json(verdict_path) if verdict_path.is_file() else {}
    no_self_certification = bool(
        verdict.get("verifier_id") == VERIFIER_ID
        and verdict.get("verifier_id") != "r11_builder_fresh"
    )
    checks: dict[str, Any] = {
        "every_candidate_independently_verified": all(per_candidate_checks.values()),
        "verdicts_recorded_by_verifier_source": event_source_ok,
        "fresh_reproduction_subprocess": completed.returncode == 0,
        "reproduction_passed": verdict.get("passed") is True,
        "no_builder_self_certification": no_self_certification,
    }
    passed = all(checks.values())
    output = {
        "schema": "jarvis-r11-g04-independent-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "checks": checks,
        "per_candidate_checks": per_candidate_checks,
        "verifier_subprocess": {
            "exit_code": completed.returncode,
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        },
        "verdict_path": str(verdict_path),
        "verdict_sha256": sha256_file(verdict_path) if verdict_path.is_file() else None,
        "evidence": ["R11_VERIFIER_REPRODUCTION.json"],
    }
    write_json(EVIDENCE_DIR / "R11_G04_INDEPENDENT.json", output)
    print(f"g04 independent: passed={passed} wall_seconds={output['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
