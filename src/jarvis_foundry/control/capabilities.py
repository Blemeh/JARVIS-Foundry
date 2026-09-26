"""Durable capability state transitions.

Capability states (DISCOVERED, PROBING, READY, ACTIVE, DEGRADED, VETOED,
STOPPED — architecture contract 02 §5.2) are projected from
``capability.state_changed`` events so readiness history is append-only and
rebuildable. ``set_capability_state`` is the single writer.
"""
from __future__ import annotations

import json
from typing import Any

from ..database import FoundryDatabase
from ..domain import CapabilityState
from ..events import Event

_VALID_STATES = frozenset(state.value for state in CapabilityState)
BLOCKING_STATES = frozenset({"STOPPED", "VETOED", "DEGRADED"})
# States a resource registration may declare as initial readiness.  A missing
# durable record is UNKNOWN/not-ready, so the runtime projects the declared
# state exactly once; later control-plane transitions own it from then on.
DECLARED_STATES = frozenset({
    CapabilityState.DISCOVERED.value,
    CapabilityState.PROBING.value,
    CapabilityState.READY.value,
})


class InvalidCapabilityState(ValueError):
    pass


def latest_capability_state(
    database: FoundryDatabase,
    resource_id: str,
    capability: str,
) -> dict[str, Any] | None:
    """Return the newest durable state for one exact resource/capability pair."""
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT payload_json, source_id, sequence FROM events "
            "WHERE event_type = 'capability.state_changed' "
            "AND correlation_id = ? "
            "AND json_extract(payload_json, '$.resource_id') = ? "
            "AND json_extract(payload_json, '$.capability') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (str(resource_id), str(resource_id), str(capability)),
        ).fetchone()
    if row is None:
        return None
    raw = row["payload_json"]
    payload = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    return {
        "state": str(payload.get("state") or ""),
        "source_id": str(row["source_id"] or ""),
        "sequence": int(row["sequence"]),
    }


def blocking_capability_state(
    database: FoundryDatabase,
    resource_id: str,
    capability: str,
) -> dict[str, Any] | None:
    """Return a scoped incident blocker, if the latest state suspends use."""
    latest = latest_capability_state(database, resource_id, capability) or {}
    state = str(latest.get("state") or "")
    if state not in BLOCKING_STATES:
        return None
    latest["reason"] = (
        f"resource capability {resource_id}/{capability} is "
        f"{state} (source {latest.get('source_id') or 'unknown'})"
    )
    return latest


def capability_state_recorded(
    database: FoundryDatabase,
    resource_id: str,
    capability: str,
) -> bool:
    """True when a durable capability state exists for the resource pair."""
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT 1 FROM events WHERE event_type = 'capability.state_changed' "
            "AND json_extract(payload_json, '$.resource_id') = ? "
            "AND json_extract(payload_json, '$.capability') = ? LIMIT 1",
            (str(resource_id), str(capability)),
        ).fetchone()
    return row is not None


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
