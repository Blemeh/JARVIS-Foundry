"""R12-G05: proving-window readiness gate."""

from __future__ import annotations

import sys
import time

from common import EVIDENCE_DIR, read_json, write_json


def main() -> int:
    started = time.monotonic()
    readiness = read_json(EVIDENCE_DIR / "R12_PROVING_READINESS.json")
    waiting = read_json(EVIDENCE_DIR / "R12_WAITING.json")
    restarts_ok = bool(readiness.get("all_restart_resilience_passed"))
    frozen_ok = bool(readiness.get("deployed_release_frozen"))
    waiting_ok = bool(
        waiting.get("waiting_items")
        and all(item.get("status") == "WAITING" for item in waiting["waiting_items"])
    )
    passed = restarts_ok and frozen_ok and waiting_ok
    payload = {
        "schema": "jarvis-r12-g05-proving-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "release_frozen": frozen_ok,
        "restart_resilience_passed": restarts_ok,
        "waiting_records_honest": waiting_ok,
        "simulated_restart_kinds": [
            item["kind"] for item in readiness.get("simulated_restarts", [])
        ],
    }
    write_json(EVIDENCE_DIR / "R12_G05_PROVING.json", payload)
    print(f"g05 proving: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
