"""R12 fault campaign harness: run every required fault in isolation."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from jarvis_foundry.fault_campaign.isolation import ProvingWorkspace, json_hash, utc_now
from jarvis_foundry.fault_campaign.records import REQUIRED_OUTCOME_FIELDS, FaultOutcome
from jarvis_foundry.fault_campaign.scenarios import FAULT_SPECS


def _outcome_complete(outcome: FaultOutcome) -> bool:
    payload = outcome.to_dict()
    return all(
        field_name in payload and payload[field_name] is not None
        for field_name in REQUIRED_OUTCOME_FIELDS
    )


def run_campaign(root: Path, *, fixed_utc: str) -> dict[str, Any]:
    """Run the full fault list against isolated remastered-stack scenarios."""
    started = time.monotonic()
    workspace = ProvingWorkspace(root, fixed_utc=fixed_utc)
    outcomes: list[FaultOutcome] = []
    for spec, scenario in FAULT_SPECS:
        work = workspace.fresh(f"scenarios/{spec.fault_id}")
        try:
            outcome = scenario(work)
        except Exception as exc:  # noqa: BLE001 - pragma: no cover - defensive truthful failure
            outcome = FaultOutcome(
                fault_id=spec.fault_id,
                name=spec.name,
                passed=False,
                truthful_state="scenario raised while executing",
                blast_radius={"scope": "isolated proving workspace", "protected_touched": False},
                incident_record={"incident_id": f"inc-{spec.fault_id}", "detected": True},
                reconciliation={"reconciled": False, "detail": str(exc)[:300]},
                suspension={"required": False, "applied": False, "detail": "not evaluated"},
                unrelated_work_continuation={
                    "continued": False,
                    "detail": "not evaluated after scenario exception",
                },
                compensation={"required": False, "detail": "not evaluated"},
                recovery={"restored": False, "detail": str(exc)[:300]},
                truthful_briefing=f"{spec.name} failed truthfully; error recorded.",
                error=f"{type(exc).__name__}: {exc}",
            )
        outcomes.append(outcome)

    payloads = [outcome.to_dict() for outcome in outcomes]
    passed = sum(1 for outcome in outcomes if outcome.passed)
    failed = sum(1 for outcome in outcomes if not outcome.passed)
    incomplete = sum(1 for outcome in outcomes if not _outcome_complete(outcome))
    spec_list = [spec.to_dict() for spec, _ in FAULT_SPECS]
    matrix = {
        "schema": "jarvis-r12-fault-matrix-v1",
        "generated_utc": utc_now(),
        "fixed_utc": fixed_utc,
        "fault_count": len(outcomes),
        "passed": passed,
        "failed": failed,
        "incomplete_records": incomplete,
        "required_outcome_fields": list(REQUIRED_OUTCOME_FIELDS),
        "all_records_complete": incomplete == 0,
        "campaign_passed": passed == len(outcomes) and failed == 0 and incomplete == 0,
        "matrix_hash": json_hash({"faults": spec_list, "outcomes": payloads}),
        "faults": spec_list,
        "outcomes": payloads,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    return matrix
