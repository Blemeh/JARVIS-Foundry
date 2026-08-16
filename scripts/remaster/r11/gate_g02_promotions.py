"""R11-G02: measured successful promotions including R1 and bounded R2."""

from __future__ import annotations

import sys
import time
from typing import Any

from common import EVIDENCE_DIR, EVIDENCE_UTC, read_json, write_json


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    payload = read_json(EVIDENCE_DIR / "R11_CYCLES.json")
    promoted = [record for record in payload["cycles"] if record["outcome"] == "promoted"]
    promotion_checks: dict[str, bool] = {}
    for record in promoted:
        promotion = record["promotion"]
        promotion_checks[str(record["cycle_id"])] = bool(
            record["verification"]["verdict"] == "PASS"
            and record["baseline_metrics"]["pytest_passed"]
            < record["candidate_metrics"]["pytest_passed"]
            and promotion["live_before_hash"] != promotion["live_after_hash"]
            and promotion["live_outcome"]["pytest_passed"]
            > record["baseline_metrics"]["pytest_passed"]
        )
    checks: dict[str, Any] = {
        "at_least_2_measured_promotions": len(promoted) >= 2,
        "at_least_1_r1_promotion": int(payload["counts"]["r1_promotions"]) >= 1,
        "at_least_1_bounded_r2_promotion": int(payload["counts"]["r2_promotions"]) >= 1,
        "every_promotion_measured_before_after": all(promotion_checks.values()),
        "promotions_independently_verified": all(
            record["verification"]["verdict"] == "PASS" for record in promoted
        ),
        "rollback_restore_evidence_present": bool(
            read_json(EVIDENCE_DIR / "R11_ROLLBACK.json").get("independent_probe_ok")
        ),
    }
    passed = all(checks.values())
    output = {
        "schema": "jarvis-r11-g02-promotions-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "checks": checks,
        "promotion_checks": promotion_checks,
        "promoted_cycles": [
            {
                "cycle_id": record["cycle_id"],
                "risk_class": record["risk_class"],
                "baseline_pytest_passed": record["baseline_metrics"]["pytest_passed"],
                "candidate_pytest_passed": record["candidate_metrics"]["pytest_passed"],
                "verdict": record["verification"]["verdict"],
                "live_outcome_pytest_passed": record["promotion"]["live_outcome"]["pytest_passed"],
            }
            for record in promoted
        ],
        "evidence": ["R11_CYCLES.json", "R11_ROLLBACK.json"],
    }
    write_json(EVIDENCE_DIR / "R11_G02_PROMOTIONS.json", output)
    print(
        f"g02 promotions: passed={passed} promoted={len(promoted)} "
        f"r1={payload['counts']['r1_promotions']} r2={payload['counts']['r2_promotions']}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
