"""Durable capability state transitions.

Capability states (DISCOVERED, PROBING, READY, ACTIVE, DEGRADED, VETOED,
STOPPED — architecture contract 02 §5.2) are projected from
``capability.state_changed`` events so readiness history is append-only and
rebuildable. ``set_capability_state`` is the single writer.
"""
from __future__ import annotations

from typing import Any

from ..database import FoundryDatabase
from ..domain import CapabilityState
from ..events import Event

_VALID_STATES = frozenset(state.value for state in CapabilityState)


class InvalidCapabilityState(ValueError):
    pass


def set_capability_state(
    database: FoundryDatabase,
    resource_id: str,
    capability: str,
    state: str,
    *,
    source_id: str = "capability-control",
    evidence: dict[str, Any] | None = None,
) -> Event:
    """Record a durable capability state transition for a registered resource."""
    if state not in _VALID_STATES:
        raise InvalidCapabilityState(
            f"state must be one of {sorted(_VALID_STATES)}, got {state!r}"
        )
    if not capability:
        raise InvalidCapabilityState("capability name is required")
    payload: dict[str, Any] = {
        "resource_id": resource_id,
        "capability": capability,
        "state": state,
    }
    if evidence:
        payload["evidence"] = evidence
    event = Event.create(
        "capability.state_changed",
        payload,
        source_type="supervisor",
        source_id=source_id,
        correlation_id=resource_id,
        # Bind the transition receipt to its owner so a later, independently
        # contained incident can repeat STOPPED/ACTIVE after the capability
        # has recovered, while retries from the same owner remain idempotent.
        idempotency_key=(
            f"capability.state_changed:{resource_id}:{capability}:{state}:{source_id}"
        ),
    )
    database.append(event)
    return event
