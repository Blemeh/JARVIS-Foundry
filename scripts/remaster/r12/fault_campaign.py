"""R12-D04: run the full integrated fault campaign in isolated proving."""

from __future__ import annotations

import sys
import time

from common import EVIDENCE_DIR, EVIDENCE_UTC, TEMP_BASE, utc_now, write_checkpoint, write_json

from jarvis_foundry.fault_campaign import run_campaign

C06_FAULTS = (
    "f34",
    "f35",
    "f36",
    "f37",
    "f38",
    "f39",
    "f40",
    "f41",
)


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    matrix = run_campaign(TEMP_BASE / "fault-campaign", fixed_utc=EVIDENCE_UTC)
    incidents = {
        "schema": "jarvis-r12-fault-incidents-v1",
        "generated_utc": utc_now(),
        "incidents": [
            {
                "fault_id": outcome["fault_id"],
                "incident_record": outcome["incident_record"],
                "truthful_state": outcome["truthful_state"],
                "reconciliation": outcome["reconciliation"],
                "suspension": outcome["suspension"],
                "recovery": outcome["recovery"],
                "truthful_briefing": outcome["truthful_briefing"],
            }
            for outcome in matrix["outcomes"]
        ],
    }
    c06 = {
        "schema": "jarvis-r12-c06-control-v1",
        "stop_never_bypassed": all(
            outcome["passed"] and "STOP" in outcome["name"]
            for outcome in matrix["outcomes"]
            if outcome["fault_id"] == "f37"
        ),
        "veto_never_bypassed": all(
            outcome["passed"] and "Veto" in outcome["name"]
            for outcome in matrix["outcomes"]
            if outcome["fault_id"] == "f38"
        ),
        "forged_directive_isolated": next(
            (outcome["passed"] for outcome in matrix["outcomes"] if outcome["fault_id"] == "f36"),
            False,
        ),
        "prompt_injection_isolated": next(
            (outcome["passed"] for outcome in matrix["outcomes"] if outcome["fault_id"] == "f35"),
            False,
        ),
        "malicious_content_never_authority": next(
            (outcome["passed"] for outcome in matrix["outcomes"] if outcome["fault_id"] == "f34"),
            False,
        ),
    }
    write_json(EVIDENCE_DIR / "R12_FAULT_MATRIX.json", matrix)
    write_json(EVIDENCE_DIR / "R12_FAULT_INCIDENTS.json", incidents)
    write_json(EVIDENCE_DIR / "R12_C06_CONTROL.json", c06)
    write_checkpoint(
        "fault_campaign",
        {
            "status": "PASS" if matrix["campaign_passed"] else "FAIL",
            "fault_count": matrix["fault_count"],
            "passed": matrix["passed"],
            "failed": matrix["failed"],
        },
    )
    print(
        f"fault campaign: passed={matrix['campaign_passed']} "
        f"faults={matrix['fault_count']} failed={matrix['failed']} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if matrix["campaign_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
