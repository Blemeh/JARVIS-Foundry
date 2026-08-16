"""R12-G04: full integrated fault campaign gate."""

from __future__ import annotations

import sys
import time

from common import EVIDENCE_DIR, read_json, write_json


def main() -> int:
    started = time.monotonic()
    matrix = read_json(EVIDENCE_DIR / "R12_FAULT_MATRIX.json")
    c06 = read_json(EVIDENCE_DIR / "R12_C06_CONTROL.json")
    outcomes = matrix.get("outcomes", [])
    required = set(matrix.get("required_outcome_fields", []))
    all_complete = all(required.issubset(outcome.keys()) for outcome in outcomes)
    campaign_ok = bool(
        matrix.get("campaign_passed")
        and matrix.get("fault_count") >= 44
        and matrix.get("incomplete_records") == 0
        and all_complete
    )
    c06_ok = all(
        bool(c06.get(key))
        for key in (
            "stop_never_bypassed",
            "veto_never_bypassed",
            "forged_directive_isolated",
            "prompt_injection_isolated",
            "malicious_content_never_authority",
        )
    )
    passed = campaign_ok and c06_ok
    payload = {
        "schema": "jarvis-r12-g04-faults-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "fault_count": matrix.get("fault_count"),
        "passed_faults": matrix.get("passed"),
        "failed_faults": matrix.get("failed"),
        "all_records_complete": all_complete,
        "control_guards_ok": c06_ok,
        "c06": c06,
    }
    write_json(EVIDENCE_DIR / "R12_G04_FAULTS.json", payload)
    print(f"g04 faults: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
