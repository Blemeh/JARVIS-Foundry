from __future__ import annotations

import pytest
from r9_helpers import FIXED_UTC, make_ledger, make_registry

from jarvis_foundry.capabilities_live.registry import ActivationProof, CapabilityRegistry
from jarvis_foundry.capabilities_live.states import CapabilityStatus
from jarvis_foundry.kernel.errors import CapabilityError, TransitionError


def _discover(registry: CapabilityRegistry, capability_id: str = "capability.files") -> None:
    registry.discover(
        capability_id=capability_id,
        domain="files",
        name="Files adapter",
        system_id="r9-canary",
    )


def _proof() -> ActivationProof:
    return ActivationProof(
        before_state_hash="before-123",
        after_state_hash="after-456",
        receipt_hash="receipt-789",
        reconciliation={"reconciled": True, "detail": "read-back matched"},
    )


def test_full_lifecycle_to_active_is_durable(tmp_path) -> None:
    ledger = make_ledger(tmp_path)
    registry = make_registry(ledger)
    _discover(registry)
    assert registry.status("capability.files") == CapabilityStatus.DISCOVERED
    assert registry.probe("capability.files") == CapabilityStatus.PROBING
    assert registry.mark_ready("capability.files") == CapabilityStatus.READY
    assert registry.activate("capability.files", _proof()) == CapabilityStatus.ACTIVE

    events = ledger.read()
    types = [event.event_type for event in events]
    assert types == [
        "capability.discovered",
        "capability.probing",
        "capability.ready",
        "capability.active",
    ]
    assert ledger.verify_chain().valid
    snapshot = registry.snapshot()
    entry = snapshot["states"]["capabilities"]["capability.files"]
    assert entry["status"] == "ACTIVE"
    assert entry["before_state_hash"] == "before-123"
    assert [item["event_type"] for item in entry["timeline"]] == types


def test_active_requires_real_proof(tmp_path) -> None:
    registry = make_registry(make_ledger(tmp_path))
    _discover(registry)
    registry.probe("capability.files")
    registry.mark_ready("capability.files")
    with pytest.raises(CapabilityError, match="no real before/after effect"):
        registry.activate(
            "capability.files",
            ActivationProof(
                before_state_hash="same",
                after_state_hash="same",
                receipt_hash="r",
                reconciliation={"reconciled": True},
            ),
        )
    with pytest.raises(CapabilityError, match="not reconciled"):
        registry.activate(
            "capability.files",
            ActivationProof(
                before_state_hash="a",
                after_state_hash="b",
                receipt_hash="r",
                reconciliation={"reconciled": False},
            ),
        )
    with pytest.raises(CapabilityError, match="incomplete"):
        registry.activate(
            "capability.files",
            ActivationProof(
                before_state_hash="",
                after_state_hash="b",
                receipt_hash="r",
                reconciliation={"reconciled": True},
            ),
        )
    assert registry.status("capability.files") == CapabilityStatus.READY
    assert all("capability.active" != e.event_type for e in registry.events())


def test_illegal_transition_never_records_event(tmp_path) -> None:
    registry = make_registry(make_ledger(tmp_path))
    _discover(registry)
    with pytest.raises(TransitionError):
        registry.activate("capability.files", _proof())
    assert registry.status("capability.files") == CapabilityStatus.DISCOVERED
    assert [e.event_type for e in registry.events()] == ["capability.discovered"]


def test_suspend_then_revoke_is_durable(tmp_path) -> None:
    registry = make_registry(make_ledger(tmp_path))
    _discover(registry)
    registry.probe("capability.files")
    registry.mark_ready("capability.files")
    registry.activate("capability.files", _proof())
    assert registry.suspend("capability.files", "owner suspension") == CapabilityStatus.SUSPENDED
    assert registry.revoke("capability.files", "owner revocation") == CapabilityStatus.REVOKED
    with pytest.raises(TransitionError):
        registry.revoke("capability.files", "again")
    entry = registry.snapshot()["states"]["capabilities"]["capability.files"]
    assert entry["status"] == "REVOKED"


def test_blocked_and_not_tested_are_honest(tmp_path) -> None:
    registry = make_registry(make_ledger(tmp_path))
    registry.discover(
        capability_id="capability.communication",
        domain="communication",
        name="Communication",
        system_id="none",
    )
    assert registry.block("capability.communication", "no provider") == CapabilityStatus.BLOCKED
    registry.discover(
        capability_id="capability.browser",
        domain="browser",
        name="Browser",
        system_id="none",
    )
    assert registry.not_tested("capability.browser", "no driver") == CapabilityStatus.NOT_TESTED
    states = registry.snapshot()["states"]["capabilities"]
    assert states["capability.communication"]["reason"] == "no provider"
    assert states["capability.browser"]["reason"] == "no driver"
    assert "capability.active" not in [e.event_type for e in registry.events()]


def test_discovery_is_single_shot(tmp_path) -> None:
    registry = make_registry(make_ledger(tmp_path))
    _discover(registry)
    with pytest.raises(CapabilityError, match="already discovered"):
        _discover(registry)


def test_ledger_rejects_unknown_event_kind(tmp_path) -> None:
    ledger = make_ledger(tmp_path)
    from jarvis_foundry.capabilities_live.ledger_events import record_event
    from jarvis_foundry.kernel.errors import UnknownEventError

    with pytest.raises(UnknownEventError):
        record_event(
            ledger,
            event_type="capability.bogus",
            payload={"capability_id": "x"},
            source_type="test",
            source_id="x",
            idempotency_key="x",
            event_id="x",
            occurred_at=FIXED_UTC,
        )
