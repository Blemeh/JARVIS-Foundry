"""R11-G03: a real rejected candidate with failed history preserved."""

from __future__ import annotations

import sys
import time
from typing import Any

from common import EVIDENCE_DIR, EVIDENCE_UTC, TEMP_BASE, read_json, write_json


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    payload = read_json(EVIDENCE_DIR / "R11_CYCLES.json")
    rejected = [record for record in payload["cycles"] if record["outcome"] == "rejected"]
    rejection_checks: dict[str, bool] = {}
    store_failed_history = TEMP_BASE / "proving" / "stores" / "failed_history"
    for record in rejected:
        cycle_id = str(record["cycle_id"])
        ref = str(record["rejection"]["failed_history_ref"])
        on_disk = (store_failed_history / cycle_id).is_dir()
        rejection_checks[cycle_id] = bool(
            record["rejection"]["failure_hash"]
            and ref.startswith("failed_history/")
            and record["verification"]["verdict"] == "FAIL"
            and on_disk
        )
    probes = read_json(EVIDENCE_DIR / "R11_PROBES.json")
    delete_probe = next(
        (item for item in probes["probes"] if item.get("operation") == "delete_failed_history"),
        {},
    )
    checks: dict[str, Any] = {
        "at_least_1_rejected": int(payload["counts"]["rejected"]) >= 1,
        "rejected_candidate_not_promoted": all(
            record.get("promotion") is None for record in rejected
        ),
        "failed_history_refs_and_hashes_present": all(rejection_checks.values()),
        "failed_history_preserved_on_disk": all(rejection_checks.values()),
        "candidate_delete_refused_and_preserved": bool(
            delete_probe.get("refused") is True
            and delete_probe.get("history_still_exists_after_refusal") is True
        ),
        "failed_history_never_deleted": not any(
            event["event_type"] == "evolution_live.failed_history_deleted"
            for event in payload["events"]
        ),
    }
    passed = all(checks.values())
    output = {
        "schema": "jarvis-r11-g03-rejection-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "checks": checks,
        "rejection_checks": rejection_checks,
        "rejected_cycles": [
            {
                "cycle_id": record["cycle_id"],
                "risk_class": record["risk_class"],
                "verdict": record["verification"]["verdict"],
                "reason": record["rejection"]["reason"],
                "failed_history_ref": record["rejection"]["failed_history_ref"],
                "failure_hash": record["rejection"]["failure_hash"],
            }
            for record in rejected
        ],
        "evidence": ["R11_CYCLES.json", "R11_PROBES.json"],
    }
    write_json(EVIDENCE_DIR / "R11_G03_REJECTION.json", output)
    print(f"g03 rejection: passed={passed} rejected={len(rejected)}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
