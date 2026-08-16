"""R9-G01: durable honest capability lifecycle with proof-gated ACTIVE."""

from __future__ import annotations

import sys
import time

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    TEMP_BASE,
    read_json,
    safe_clear,
    write_json,
)

from jarvis_foundry.capabilities_live.ledger_events import r9_ledger
from jarvis_foundry.capabilities_live.registry import ActivationProof, CapabilityRegistry
from jarvis_foundry.capabilities_live.states import CapabilityStatus
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.errors import CapabilityError, TransitionError


def main() -> int:
    started = time.monotonic()
    lifecycle = read_json(EVIDENCE_DIR / "R9_LIFECYCLE.json")
    states = lifecycle["capability_states"]
    lifecycle_ledger = lifecycle["ledger"]
    active_checks: dict[str, bool] = {}
    for capability_id, proof_key in (
        ("capability.files", "files"),
        ("capability.git", "git"),
        ("capability.services", "services"),
    ):
        state = states[capability_id]
        proof = lifecycle[proof_key]["proof"]
        active_checks[capability_id] = bool(
            state["status"] == "ACTIVE"
            and state.get("proof_hash")
            and proof["before_state_hash"]
            and proof["after_state_hash"]
            and proof["receipt_hash"]
            and proof["before_state_hash"] != proof["after_state_hash"]
            and proof["reconciliation"].get("reconciled") is True
        )
    ledger_ok = bool(lifecycle_ledger["chain_valid"] and lifecycle_ledger["event_count"] >= 40)
    unavailable_ok = bool(
        states["capability.browser"]["status"] == "NOT_TESTED"
        and states["capability.communication"]["status"] == "BLOCKED"
        and states["capability.calendar"]["status"] == "BLOCKED"
    )

    probe_root = TEMP_BASE / "gates" / "g01"
    safe_clear(probe_root)
    probe_root.mkdir(parents=True, exist_ok=True)
    probe_ledger = r9_ledger(probe_root / "ledger.db", fixed_utc=EVIDENCE_UTC)
    registry = CapabilityRegistry(probe_ledger, clock=FixedClock(EVIDENCE_UTC))
    registry.discover(
        capability_id="capability.probe.one",
        domain="probe",
        name="Probe one",
        system_id="gate",
    )
    illegal_refused = False
    try:
        registry.activate(
            "capability.probe.one",
            ActivationProof(
                before_state_hash="a",
                after_state_hash="b",
                receipt_hash="r",
                reconciliation={"reconciled": True},
            ),
        )
    except TransitionError:
        illegal_refused = True
    illegal_left_no_event = len(probe_ledger.read()) == 1

    registry.discover(
        capability_id="capability.probe.two",
        domain="probe",
        name="Probe two",
        system_id="gate",
    )
    registry.probe("capability.probe.two")
    registry.mark_ready("capability.probe.two")
    no_proof_refused = False
    try:
        registry.activate(
            "capability.probe.two",
            ActivationProof(
                before_state_hash="same",
                after_state_hash="same",
                receipt_hash="r",
                reconciliation={"reconciled": True},
            ),
        )
    except CapabilityError:
        no_proof_refused = True
    no_proof_left_ready = registry.status("capability.probe.two") == CapabilityStatus.READY
    fresh_probe_ok = bool(
        illegal_refused and illegal_left_no_event and no_proof_refused and no_proof_left_ready
    )

    passed = all(active_checks.values()) and ledger_ok and unavailable_ok and fresh_probe_ok
    payload = {
        "schema": "jarvis-r9-g01-lifecycle-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "active_checks": active_checks,
        "ledger": {
            "chain_valid": lifecycle_ledger["chain_valid"],
            "event_count": lifecycle_ledger["event_count"],
            "head_hash": lifecycle_ledger["head_hash"],
        },
        "unavailable_connectors_recorded_honestly": unavailable_ok,
        "fresh_illegal_transition_probe": {
            "refused": illegal_refused,
            "no_event_recorded": illegal_left_no_event,
        },
        "fresh_activation_without_proof_probe": {
            "refused": no_proof_refused,
            "status_remained_ready": no_proof_left_ready,
        },
    }
    write_json(EVIDENCE_DIR / "R9_G01_LIFECYCLE.json", payload)
    print(f"g01 lifecycle: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
