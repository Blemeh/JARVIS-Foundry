"""Durable capability lifecycle registry with proof-gated activation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jarvis_foundry.capabilities_live.ledger_events import record_event
from jarvis_foundry.capabilities_live.projections import lifecycle_snapshot
from jarvis_foundry.capabilities_live.states import CapabilityMachine, CapabilityStatus
from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import CapabilityError
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ledger import Ledger


@dataclass(frozen=True)
class ActivationProof:
    """Before-state, real effect, system receipt, and reconciliation for ACTIVE."""

    before_state_hash: str
    after_state_hash: str
    receipt_hash: str
    reconciliation: Mapping[str, Any]

    def validate(self) -> None:
        if not (self.before_state_hash and self.after_state_hash and self.receipt_hash):
            raise CapabilityError("activation proof is incomplete: hashes required")
        if self.before_state_hash == self.after_state_hash:
            raise CapabilityError("activation proof has no real before/after effect")
        if not self.reconciliation.get("reconciled"):
            raise CapabilityError("activation proof is not reconciled")


class CapabilityRegistry:
    """Lifecycle operations whose transitions are durable kernel ledger events."""

    def __init__(self, ledger: Ledger, *, clock: Clock | None = None) -> None:
        self._ledger = ledger
        self._clock = clock if clock is not None else SystemClock()

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    def events(self) -> list[LedgerEvent]:
        return self._ledger.read()

    def snapshot(self) -> dict[str, Any]:
        return lifecycle_snapshot(self.events())

    def status(self, capability_id: str) -> CapabilityStatus | None:
        state = self.snapshot()["states"].get("capabilities", {})
        entry = state.get(capability_id)
        if entry is None:
            return None
        return CapabilityStatus(str(entry["status"]))

    def _transition(self, capability_id: str, action: str) -> CapabilityStatus:
        current = self.status(capability_id) or CapabilityStatus.DISCOVERED
        return CapabilityMachine(current).transition(action).status

    def _record(
        self,
        capability_id: str,
        event_type: str,
        action: str,
        payload: Mapping[str, Any],
    ) -> LedgerEvent:
        occurred_at = self._clock.now_utc()
        return record_event(
            self._ledger,
            event_type=event_type,
            payload=payload,
            source_type="capability_live.registry",
            source_id=capability_id,
            idempotency_key=f"{event_type}:{capability_id}",
            event_id=f"r9:{event_type}:{capability_id}:{action}",
            occurred_at=occurred_at,
        )

    def discover(
        self,
        *,
        capability_id: str,
        domain: str,
        name: str,
        system_id: str,
    ) -> CapabilityStatus:
        if self.status(capability_id) is not None:
            raise CapabilityError(f"capability already discovered: {capability_id}")
        self._record(
            capability_id,
            "capability.discovered",
            "discover",
            {
                "capability_id": capability_id,
                "domain": domain,
                "name": name,
                "system_id": system_id,
            },
        )
        return CapabilityStatus.DISCOVERED

    def probe(self, capability_id: str, probe_note: str = "probe requested") -> CapabilityStatus:
        next_status = self._transition(capability_id, "probe")
        self._record(
            capability_id,
            "capability.probing",
            "probe",
            {"capability_id": capability_id, "probe_note": probe_note},
        )
        return next_status

    def mark_ready(self, capability_id: str, reason: str = "probe succeeded") -> CapabilityStatus:
        next_status = self._transition(capability_id, "ready")
        self._record(
            capability_id,
            "capability.ready",
            "ready",
            {"capability_id": capability_id, "reason": reason},
        )
        return next_status

    def activate(self, capability_id: str, proof: ActivationProof) -> CapabilityStatus:
        next_status = self._transition(capability_id, "activate")
        proof.validate()
        proof_hash = canonical_hash(
            {
                "before_state_hash": proof.before_state_hash,
                "after_state_hash": proof.after_state_hash,
                "receipt_hash": proof.receipt_hash,
                "reconciliation": proof.reconciliation,
            }
        )
        self._record(
            capability_id,
            "capability.active",
            "activate",
            {
                "capability_id": capability_id,
                "proof_hash": proof_hash,
                "before_state_hash": proof.before_state_hash,
                "after_state_hash": proof.after_state_hash,
                "receipt_hash": proof.receipt_hash,
                "reconciliation_hash": canonical_hash(proof.reconciliation),
            },
        )
        return next_status

    def block(self, capability_id: str, reason: str) -> CapabilityStatus:
        next_status = self._transition(capability_id, "block")
        self._record(
            capability_id,
            "capability.blocked",
            "block",
            {"capability_id": capability_id, "reason": reason},
        )
        return next_status

    def not_ready(self, capability_id: str, reason: str) -> CapabilityStatus:
        next_status = self._transition(capability_id, "not_ready")
        self._record(
            capability_id,
            "capability.not_ready",
            "not_ready",
            {"capability_id": capability_id, "reason": reason},
        )
        return next_status

    def not_tested(self, capability_id: str, reason: str) -> CapabilityStatus:
        next_status = self._transition(capability_id, "not_tested")
        self._record(
            capability_id,
            "capability.not_tested",
            "not_tested",
            {"capability_id": capability_id, "reason": reason},
        )
        return next_status

    def degrade(self, capability_id: str, reason: str) -> CapabilityStatus:
        next_status = self._transition(capability_id, "degrade")
        self._record(
            capability_id,
            "capability.degraded",
            "degrade",
            {"capability_id": capability_id, "reason": reason},
        )
        return next_status

    def suspend(self, capability_id: str, reason: str) -> CapabilityStatus:
        next_status = self._transition(capability_id, "suspend")
        self._record(
            capability_id,
            "capability.suspended",
            "suspend",
            {"capability_id": capability_id, "reason": reason},
        )
        return next_status

    def revoke(self, capability_id: str, reason: str) -> CapabilityStatus:
        next_status = self._transition(capability_id, "revoke")
        self._record(
            capability_id,
            "capability.revoked",
            "revoke",
            {"capability_id": capability_id, "reason": reason},
        )
        return next_status
