"""R12-G02: adoption ladder gate."""

from __future__ import annotations

import sys
import time

from common import EVIDENCE_DIR, read_json, write_json


def main() -> int:
    started = time.monotonic()
    ladder = read_json(EVIDENCE_DIR / "R12_ADOPTION_LADDER.json")
    rungs = ladder.get("rungs", [])
    reached_full = bool(
        rungs
        and rungs[-1]["rung"] == "FULL_DIGITAL_WORK_OPERATION"
        and rungs[-1]["status"] == "PASS"
    )
    transitions_ok = all(rung["transition_ok"] for rung in rungs)
    passed = bool(ladder.get("passed") and reached_full and transitions_ok and len(rungs) == 6)
    payload = {
        "schema": "jarvis-r12-g02-adoption-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "rung_count": len(rungs),
        "reached_full_operation": reached_full,
        "transitions_ok": transitions_ok,
        "rung_statuses": [rung["status"] for rung in rungs],
    }
    write_json(EVIDENCE_DIR / "R12_G02_ADOPTION.json", payload)
    print(f"g02 adoption: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
