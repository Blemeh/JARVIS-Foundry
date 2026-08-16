"""R11-G01: complete durable lifecycle cycles plus honest WAITING."""

from __future__ import annotations

import sys
import time
from typing import Any

from common import EVIDENCE_DIR, EVIDENCE_UTC, read_json, write_json

REQUIRED_STAGES = (
    "evolution_live.observed",
    "evolution_live.hypothesized",
    "evolution_live.candidate_isolated",
    "evolution_live.evaluated",
    "evolution_live.independently_verified",
)
PROMOTED_EXTRA = (
    "evolution_live.shadowed",
    "evolution_live.canary_ready",
    "evolution_live.promoted",
)
REJECTED_EXTRA = ("evolution_live.rejected",)
TERMINAL = ("evolution_live.outcome_measured", "evolution_live.learned")


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    payload = read_json(EVIDENCE_DIR / "R11_CYCLES.json")
    events = payload["events"]
    by_cycle: dict[str, list[str]] = {}
    for event in events:
        cycle_id = event["payload"].get("cycle_id")
        if isinstance(cycle_id, str):
            by_cycle.setdefault(cycle_id, []).append(event["event_type"])
    cycle_checks: dict[str, bool] = {}
    for record in payload["cycles"]:
        cycle_id = str(record["cycle_id"])
        stage_types = by_cycle.get(cycle_id, [])
        required = list(REQUIRED_STAGES)
        required += PROMOTED_EXTRA if record["outcome"] == "promoted" else REJECTED_EXTRA
        required += list(TERMINAL)
        cycle_checks[cycle_id] = all(stage in stage_types for stage in required)
    event_ids = [str(event["event_id"]) for event in events]
    checks: dict[str, Any] = {
        "cycle_count_at_least_3": int(payload["counts"]["cycles"]) >= 3,
        "every_cycle_has_full_lifecycle": all(cycle_checks.values()),
        "lifecycle_events_durable": int(payload["ledger"]["event_count"]) > 0,
        "ledger_chain_valid": bool(payload["ledger"]["chain_valid"]),
        "event_ids_unique": len(event_ids) == len(set(event_ids)),
        "waiting_created": bool(payload["waiting"]["created_first_call"]),
        "waiting_replay_not_duplicated": not bool(
            payload["waiting"]["replay_after_restart_created"]
        ),
        "waiting_honest": bool(payload["waiting"]["honest_waiting"]),
    }
    passed = all(checks.values())
    output = {
        "schema": "jarvis-r11-g01-cycles-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "checks": checks,
        "cycle_checks": cycle_checks,
        "counts": payload["counts"],
        "ledger": payload["ledger"],
        "waiting": payload["waiting"],
        "evidence": ["R11_CYCLES.json", "R11_LIFECYCLE_EVENTS.json", "R11_WAITING.json"],
    }
    write_json(EVIDENCE_DIR / "R11_G01_CYCLES.json", output)
    print(f"g01 cycles: passed={passed} cycles={payload['counts']['cycles']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
