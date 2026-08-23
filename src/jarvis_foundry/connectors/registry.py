"""V5-S4: connector registry — health, quota, disable/enable, routing.

Append-only events (no projection, no migration):
- connector.registered     (versioned spec; full-payload key — a new
                            version records new history, identical
                            re-delivery dedupes)
- connector.health_sample  (HEALTHY/DEGRADED/DISABLED/UNKNOWN; the
                            latest sample is the derived health)
- connector.quota_recorded (used/limit accounting per connector)
- connector.disabled / connector.enabled (explicit control)

Routing is deterministic and isolated: ONE connector's outage never
blocks unrelated connectors; selection only ever happens among
verified (registered), policy-allowed (no STOP, no veto), healthy,
enabled, capability-satisfying, within-quota connectors.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..database import FoundryDatabase
from ..events import Event, payload_hash
from .contract import ConnectorSpec, latest_spec, validate_connector_spec

_HEALTH_STATES = frozenset({"HEALTHY", "DEGRADED", "DISABLED", "UNKNOWN"})
_READY_STATES = frozenset({"READY", "ACTIVE"})


class ConnectorUnavailable(Exception):
    """Deterministic routing refusal — the reason is always attached."""


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    connector: ConnectorSpec | None
    reason: str


def _append_full_key(database: FoundryDatabase, event_type: str,
                     payload: dict[str, Any]) -> None:
    database.append(Event.create(
        event_type, payload,
        source_type="supervisor", source_id="v5-connector-registry",
        idempotency_key=(f"{event_type}:{payload.get('connector_id')}:"
                         f"{payload_hash(payload)}")))


def register_connector(database: FoundryDatabase, spec: ConnectorSpec) -> None:
    """Register (or re-register at a new version) a connector.

    The spec must satisfy the versioned contract. Identical re-delivery
    dedupes; a version change records NEW registration history and the
    derived state follows the latest record.
    """
    error = validate_connector_spec(spec)
    if error:
        raise ValueError(f"invalid connector spec: {error}")
    _append_full_key(database, "connector.registered", {
        "connector_id": spec.connector_id, "kind": spec.kind,
        "version": spec.version, "actions": dict(spec.actions),
        "capabilities": dict(spec.capabilities),
        "retry_policy": dict(spec.retry_policy),
        "target_kinds": list(spec.target_kinds),
        "idempotency_strategy": spec.idempotency_strategy,
        "reconciliation_strategy": spec.reconciliation_strategy,
        "supports_cancellation": spec.supports_cancellation,
        "cost_metadata": dict(spec.cost_metadata),
        "evidence_fields": list(spec.evidence_fields),
        "stop_policy": spec.stop_policy})


def record_health_sample(database: FoundryDatabase, connector_id: str,
                         status: str, *, detail: str = "") -> None:
    if status not in _HEALTH_STATES:
        raise ValueError(
            f"health status must be one of {sorted(_HEALTH_STATES)}, "
            f"got {status!r}")
    _append_full_key(database, "connector.health_sample", {
        "connector_id": connector_id, "status": status, "detail": detail})


def record_quota_use(database: FoundryDatabase, connector_id: str, *,
                     used: int | float, limit: int | float) -> None:
    if used < 0 or limit <= 0:
        raise ValueError("quota used must be >= 0 and limit must be > 0")
    _append_full_key(database, "connector.quota_recorded", {
        "connector_id": connector_id, "used": used, "limit": limit})


def disable_connector(database: FoundryDatabase, connector_id: str, *,
                      reason: str) -> None:
    _append_full_key(database, "connector.disabled", {
        "connector_id": connector_id, "reason": reason})


def enable_connector(database: FoundryDatabase, connector_id: str) -> None:
    _append_full_key(database, "connector.enabled", {
        "connector_id": connector_id})


def _latest_bool(database: FoundryDatabase, connector_id: str,
                 yes_type: str, no_type: str) -> bool | None:
    """Derived yes/no from the latest of two event types (None = never
    recorded)."""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT event_type FROM events WHERE event_type IN (?, ?) "
            "AND json_extract(payload_json, '$.connector_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (yes_type, no_type, connector_id)).fetchone()
    if row is None:
        return None
    return row[0] == yes_type


def connector_status(database: FoundryDatabase,
                     connector_id: str) -> dict[str, Any]:
    """Derived connector state: version, health, quota, disabled —
    rebuilt deterministically from the append-only event stream."""
    spec = latest_spec(database, connector_id)
    if spec is None:
        raise ConnectorUnavailable(f"connector {connector_id} is not registered")
    with database.connect() as connection:
        health_row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'connector.health_sample' AND json_extract(payload_json, "
            "'$.connector_id') = ? ORDER BY sequence DESC LIMIT 1",
            (connector_id,)).fetchone()
        quota_row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'connector.quota_recorded' AND json_extract(payload_json, "
            "'$.connector_id') = ? ORDER BY sequence DESC LIMIT 1",
            (connector_id,)).fetchone()
    health = "UNKNOWN"
    if health_row is not None:
        health = json.loads(health_row["payload_json"])["status"]
    quota: dict[str, int | float] = {"used": 0, "limit": None}
    if quota_row is not None:
        payload = json.loads(quota_row["payload_json"])
        quota = {"used": payload["used"], "limit": payload["limit"]}
    disabled = _latest_bool(database, connector_id,
                            "connector.disabled", "connector.enabled") or False
    return {"connector_id": connector_id, "version": spec.version,
            "health": health, "quota": quota, "disabled": disabled,
            "capabilities": dict(spec.capabilities),
            "actions": dict(spec.actions)}


def _registered_ids(database: FoundryDatabase) -> list[str]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT json_extract(payload_json, '$.connector_id') "
            "AS id FROM events WHERE event_type = 'connector.registered'"
        ).fetchall()
    return sorted(str(row["id"]) for row in rows)


def select_connector(
    database: FoundryDatabase,
    action_kind: str,
    *,
    capabilities_required: tuple[str, ...] = (),
    preferred: str | None = None,
    stop_path: str | None = None,
) -> RoutingOutcome:
    """Deterministic routing among verified, policy-allowed connectors.

    Order: emergency STOP -> veto -> health -> disabled -> capability
    -> quota -> deterministic pick (preferred first when it satisfies
    every gate, else the first qualifying connector by id — reroute).
    One connector's outage NEVER blocks unrelated connectors: every
    gate is evaluated per connector.
    """
    from ..control.broker import (emergency_stop_reason,
                                  matching_veto_reason,
                                  normalize_action_kind)
    canonical = normalize_action_kind(action_kind)
    stop = emergency_stop_reason(database, stop_path)
    if stop:
        return RoutingOutcome(None, f"EMERGENCY STOP: {stop}")
    veto = matching_veto_reason(database, {canonical, *capabilities_required})
    if veto:
        return RoutingOutcome(None, f"vetoed: {veto}")

    candidates: list[tuple[str, ConnectorSpec]] = []
    for connector_id in _registered_ids(database):
        spec = latest_spec(database, connector_id)
        if spec is None or canonical not in spec.actions:
            continue
        candidates.append((connector_id, spec))

    def _qualifies(connector_id: str, spec: ConnectorSpec) -> str | None:
        veto = matching_veto_reason(database, {
            connector_id, spec.kind, canonical,
            *capabilities_required})
        if veto:
            return f"vetoed: {veto}"
        with database.connect() as connection:
            health_row = connection.execute(
                "SELECT payload_json FROM events WHERE event_type = "
                "'connector.health_sample' AND json_extract(payload_json, "
                "'$.connector_id') = ? ORDER BY sequence DESC LIMIT 1",
                (connector_id,)).fetchone()
            quota_row = connection.execute(
                "SELECT payload_json FROM events WHERE event_type = "
                "'connector.quota_recorded' AND json_extract(payload_json, "
                "'$.connector_id') = ? ORDER BY sequence DESC LIMIT 1",
                (connector_id,)).fetchone()
            disabled = connection.execute(
                "SELECT event_type FROM events WHERE event_type IN "
                "('connector.disabled', 'connector.enabled') AND "
                "json_extract(payload_json, '$.connector_id') = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (connector_id,)).fetchone()
        if health_row is None:
            health = "UNKNOWN"
        else:
            health = json.loads(health_row["payload_json"])["status"]
        if health != "HEALTHY":
            return (f"connector {connector_id} health is {health} — "
                    "not selectable (outage or never sampled)")
        if disabled is not None and disabled[0] == "connector.disabled":
            return f"connector {connector_id} is disabled"
        for capability in capabilities_required:
            state = spec.capabilities.get(capability)
            if state not in _READY_STATES:
                return (f"connector {connector_id} capability "
                        f"{capability} is {state or 'UNREGISTERED'}")
        if quota_row is not None:
            quota = json.loads(quota_row["payload_json"])
            if quota["used"] >= quota["limit"]:
                return (f"connector {connector_id} quota exhausted "
                        f"({quota['used']}/{quota['limit']})")
        return None

    # preferred connector first when it qualifies (reroute otherwise)
    if preferred is not None:
        preferred_entry = next(
            ((cid, spec) for cid, spec in candidates if cid == preferred),
            None)
        if preferred_entry is None:
            preferred_gap = (f"preferred connector {preferred} is not "
                             "registered or does not declare the action")
        else:
            # S4 verifier deleg_761e4d85 LOW: the old for/else ALWAYS
            # executed its else (no break) and misattributed the reroute
            # cause when the preferred connector IS registered but fails
            # a gate — the gap text now names the real reason.
            preferred_gap = _qualifies(preferred_entry[0],
                                       preferred_entry[1])
            if preferred_gap is None:
                return RoutingOutcome(preferred_entry[1], "selected")
        for connector_id, spec in candidates:
            if connector_id == preferred:
                continue
            gap = _qualifies(connector_id, spec)
            if gap is None:
                return RoutingOutcome(
                    spec, f"rerouted from {preferred}: {preferred_gap}")
        return RoutingOutcome(None, preferred_gap)

    gaps: list[str] = []
    for connector_id, spec in candidates:  # deterministic id order
        gap = _qualifies(connector_id, spec)
        if gap is None:
            return RoutingOutcome(spec, "selected")
        gaps.append(gap)
    if not candidates:
        return RoutingOutcome(
            None, f"no registered connector declares action {canonical!r}")
    return RoutingOutcome(None, "; ".join(gaps))
