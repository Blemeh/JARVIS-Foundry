from __future__ import annotations

import pytest

from jarvis_foundry.capabilities_live.states import CapabilityMachine, CapabilityStatus
from jarvis_foundry.kernel.errors import TransitionError


@pytest.mark.parametrize(
    ("current", "action", "expected"),
    [
        (CapabilityStatus.DISCOVERED, "probe", CapabilityStatus.PROBING),
        (CapabilityStatus.PROBING, "ready", CapabilityStatus.READY),
        (CapabilityStatus.READY, "activate", CapabilityStatus.ACTIVE),
        (CapabilityStatus.ACTIVE, "suspend", CapabilityStatus.SUSPENDED),
        (CapabilityStatus.SUSPENDED, "revoke", CapabilityStatus.REVOKED),
        (CapabilityStatus.ACTIVE, "degrade", CapabilityStatus.DEGRADED),
        (CapabilityStatus.DEGRADED, "ready", CapabilityStatus.READY),
        (CapabilityStatus.DISCOVERED, "block", CapabilityStatus.BLOCKED),
        (CapabilityStatus.DISCOVERED, "not_tested", CapabilityStatus.NOT_TESTED),
    ],
)
def test_legal_transitions(current, action, expected) -> None:
    machine = CapabilityMachine(current)
    assert machine.transition(action).status == expected


@pytest.mark.parametrize(
    ("current", "action"),
    [
        (CapabilityStatus.DISCOVERED, "activate"),
        (CapabilityStatus.PROBING, "activate"),
        (CapabilityStatus.NOT_TESTED, "activate"),
        (CapabilityStatus.BLOCKED, "activate"),
        (CapabilityStatus.REVOKED, "revoke"),
        (CapabilityStatus.REVOKED, "probe"),
        (CapabilityStatus.ACTIVE, "activate"),
        (CapabilityStatus.SUSPENDED, "activate"),
    ],
)
def test_illegal_transitions_refused(current, action) -> None:
    machine = CapabilityMachine(current)
    with pytest.raises(TransitionError):
        machine.transition(action)


def test_serialize_round_trip() -> None:
    machine = CapabilityMachine(CapabilityStatus.READY)
    assert CapabilityMachine.deserialize(machine.serialize()) == machine
