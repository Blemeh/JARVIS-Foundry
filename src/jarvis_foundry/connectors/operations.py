"""V5-11 truthful operational status and deterministic connector choice."""

from __future__ import annotations

import json
import math
from typing import Any

from ..control.broker import emergency_stop_reason, matching_veto_reason, normalize_action_kind
from ..database import FoundryDatabase
from ..events import Event, payload_hash
from .contract import ConnectorSpec, latest_spec
from .registry import RoutingOutcome


_HEALTH_STATES = frozenset({"HEALTHY", "READY", "DEGRADED", "BLOCKED", "UNKNOWN", "DISABLED", "VETOED"})
_READY_HEALTH = frozenset({"HEALTHY", "READY"})
_READY_CAPABILITIES = frozenset({"READY", "ACTIVE"})


def _append(database: FoundryDatabase, event_type: str, payload: dict[str, Any]) -> None:
    database.append(Event.create(
        event_type, payload, source_type="supervisor", source_id="v5-connector-operations",
        idempotency_key=f"{event_type}:{payload.get('connector_id')}:{payload_hash(payload)}",
    ))


def record_operational_health(database: FoundryDatabase, connector_id: str, status: str, *, detail: str = "") -> None:
    if status not in _HEALTH_STATES:
        raise ValueError(f"health status must be one of {sorted(_HEALTH_STATES)}")
    _append(database, "connector.health_sample", {
        "connector_id": connector_id, "status": status, "detail": detail,
    })


def record_cost_sample(database: FoundryDatabase, connector_id: str, *, cost_usd: float | None, known: bool) -> None:
    if not isinstance(known, bool):
        raise ValueError("cost known flag must be boolean")
    if cost_usd is not None and (
        not isinstance(cost_usd, (int, float)) or isinstance(cost_usd, bool)
        or not math.isfinite(float(cost_usd)) or cost_usd < 0
    ):
        raise ValueError("cost_usd must be a finite non-negative number or None")
    _append(database, "connector.cost_sample", {
        "connector_id": connector_id, "cost_usd": cost_usd, "known": known,
    })


def _latest_payload(database: FoundryDatabase, event_type: str, connector_id: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json, sequence FROM events WHERE event_type = ? AND "
            "json_extract(payload_json, '$.connector_id') = ? ORDER BY sequence DESC LIMIT 1",
            (event_type, connector_id),
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    payload["_sequence"] = int(row[1])
    return payload


def operational_status(database: FoundryDatabase, connector_id: str) -> dict[str, Any]:
    spec = latest_spec(database, connector_id)
    if spec is None:
        raise ValueError(f"connector {connector_id} is not registered")
    health_payload = _latest_payload(database, "connector.health_sample", connector_id)
    health = str(health_payload.get("status")) if health_payload else "UNKNOWN"
    quota_payload = _latest_payload(database, "connector.quota_recorded", connector_id)
    if quota_payload is None:
        quota_state = "UNSAMPLED"
        quota = {"used": 0, "limit": None}
    else:
        quota = {"used": quota_payload["used"], "limit": quota_payload["limit"]}
        quota_state = "EXHAUSTED" if quota["used"] >= quota["limit"] else "AVAILABLE"
    cost_payload = _latest_payload(database, "connector.cost_sample", connector_id)
    if cost_payload is None:
        cost_state = "KNOWN" if spec.cost_metadata.get("cost_known") is True else "UNKNOWN"
        cost = None
    else:
        cost_state = "KNOWN" if cost_payload.get("known") else "UNKNOWN"
        cost = cost_payload.get("cost_usd")
    disabled_payload = _latest_payload(database, "connector.disabled", connector_id)
    enabled_payload = _latest_payload(database, "connector.enabled", connector_id)
    disabled = disabled_payload is not None and (
        enabled_payload is None or disabled_payload.get("_sequence", 0) >= enabled_payload.get("_sequence", -1)
    )
    readiness = "READY" if health in _READY_HEALTH else health
    return {
        "connector_id": connector_id, "version": spec.version,
        "health": health, "readiness": readiness, "quota": quota,
        "quota_state": quota_state, "cost": cost, "cost_state": cost_state,
        "disabled": disabled, "capabilities": dict(spec.capabilities),
        "actions": dict(spec.actions),
    }


def _registered_specs(database: FoundryDatabase) -> list[ConnectorSpec]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT json_extract(payload_json, '$.connector_id') "
            "AS connector_id FROM events WHERE event_type = 'connector.registered'"
        ).fetchall()
    specs = [latest_spec(database, str(row[0])) for row in rows]
    return sorted((spec for spec in specs if spec is not None), key=lambda item: item.connector_id)


def select_operational_connector(
    database: FoundryDatabase,
    action_kind: str,
    *,
    capabilities_required: tuple[str, ...] = (),
    preferred: str | None = None,
    stop_path: str | None = None,
) -> RoutingOutcome:
    canonical = normalize_action_kind(action_kind)
    stop = emergency_stop_reason(database, stop_path)
    if stop:
        return RoutingOutcome(None, f"EMERGENCY STOP: {stop}")
    global_veto = matching_veto_reason(database, {canonical, *capabilities_required})
    if global_veto:
        return RoutingOutcome(None, f"vetoed: {global_veto}")
    specs = [spec for spec in _registered_specs(database) if canonical in spec.actions]
    if preferred:
        specs.sort(key=lambda spec: (0 if spec.connector_id == preferred else 1, spec.connector_id))
    gaps: list[str] = []
    for spec in specs:
        status = operational_status(database, spec.connector_id)
        reason: str | None = None
        local_veto = matching_veto_reason(database, {
            spec.connector_id, spec.kind, canonical, *capabilities_required,
        })
        if local_veto:
            reason = f"connector {spec.connector_id} VETOED: {local_veto}"
        elif status["health"] not in _READY_HEALTH:
            reason = f"connector {spec.connector_id} health is {status['health']}"
        elif status["disabled"]:
            reason = f"connector {spec.connector_id} is DISABLED"
        elif status["quota_state"] == "EXHAUSTED":
            reason = f"connector {spec.connector_id} quota exhausted ({status['quota']['used']}/{status['quota']['limit']})"
        else:
            for capability in capabilities_required:
                if spec.capabilities.get(capability) not in _READY_CAPABILITIES:
                    reason = f"connector {spec.connector_id} capability {capability} is {spec.capabilities.get(capability, 'UNREGISTERED')}"
                    break
        if reason is None:
            if preferred and spec.connector_id != preferred:
                preferred_gap = gaps[0] if gaps else f"preferred connector {preferred} is unavailable"
                return RoutingOutcome(spec, f"rerouted from {preferred}: {preferred_gap}")
            return RoutingOutcome(spec, "selected")
        gaps.append(reason)
    if not specs:
        return RoutingOutcome(None, f"no registered connector declares action {canonical!r}")
    return RoutingOutcome(None, "; ".join(gaps))


__all__ = ["operational_status", "record_cost_sample", "record_operational_health", "select_operational_connector"]
