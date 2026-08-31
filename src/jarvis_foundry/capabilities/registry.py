"""V7-S9 — capability discovery, brokerage, effect reconciliation matrix.

Capabilities are ledger records with family, surfaces, effect class,
risk and a verification hint; brokerage matches mission needs to
capabilities (deterministic, Supervisor-only) and records decisions;
the reconciliation matrix maps every capability to its effect class and
reconciliation method — complete and consistent by construction.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.authority import SUPERVISOR_CONTROLLER
from jarvis_foundry.supervisor.evolution_control import (
    EvolutionControlError,
)
from jarvis_foundry.supervisor.estop import EmergencyStop

CapabilityError = EvolutionControlError

# effect class -> reconciliation method (the matrix's canonical rows)
_EFFECT_MATRIX: dict[str, dict[str, str]] = {
    "local-command": {"reconciliation": "exit code + artifact hash"},
    "web-read": {"reconciliation": "fetch + content hash / URL reachable"},
    "web-write": {"reconciliation": "URL + fetch-back verification"},
    "file-write": {"reconciliation": "artifact hash + read-back"},
    "network-http": {"reconciliation": "response status + body probe"},
    "message-delivery": {"reconciliation": "delivery receipt / read-back"},
    "image-generation": {"reconciliation": "output artifact hash"},
    "audio-generation": {"reconciliation": "output artifact hash"},
    "browser-session": {"reconciliation": "session snapshot + state probe"},
    "desktop-input": {"reconciliation": "effect probe / re-read state"},
    "email-send": {"reconciliation": "sent-id receipt + folder check"},
    "calendar-write": {"reconciliation": "read-back of the event"},
    "finance-read": {"reconciliation": "statement hash + balance probe"},
    "database-write": {"reconciliation": "row read-back + event count"},
    "schedule-write": {"reconciliation": "task query read-back"},
}

# These values are deliberately small and explicit.  A missing sample is not
# promoted to a healthy/available state by the read model.
_HEALTH_STATES = frozenset({"HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"})
_AUTH_STATES = frozenset({"READY", "REQUIRED", "REVOKED", "UNKNOWN"})
_AVAILABILITY_STATES = frozenset({"AVAILABLE", "DEGRADED", "UNAVAILABLE", "UNKNOWN"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CapabilityError(message)


def _gate(database: FoundryDatabase, controller: str) -> None:
    _require(controller == SUPERVISOR_CONTROLLER,
             "only the Supervisor can manage the capability registry")
    _require(not EmergencyStop(database.path.parent / "EMERGENCY_STOP").active(),
             "STOP sentinel active")


def _rebuild(database: FoundryDatabase) -> dict[str, dict[str, Any]]:
    caps: dict[str, dict[str, Any]] = {}
    for event in database.events(
        event_types=("capability.registered", "capability.observed"),
    ):
        if event.event_type == "capability.registered":
            payload = event.payload
            capability_id = payload["capability_id"]
            caps[capability_id] = dict(payload)
            caps[capability_id].setdefault("health", "UNKNOWN")
            caps[capability_id].setdefault("auth_state", "UNKNOWN")
            caps[capability_id].setdefault("availability", "UNKNOWN")
            caps[capability_id].setdefault("latency_ms", None)
            caps[capability_id].setdefault("reliability", None)
            caps[capability_id].setdefault("limits", None)
            caps[capability_id].setdefault("permissions", None)
            caps[capability_id].setdefault("context", None)
            caps[capability_id].setdefault("tool_support", None)
            caps[capability_id].setdefault(
                "required_authority", payload.get("controller", "UNKNOWN"))
        elif event.event_type == "capability.observed":
            payload = event.payload
            capability_id = payload.get("capability_id")
            if not isinstance(capability_id, str) or capability_id not in caps:
                # An observation cannot create a capability.  It remains an
                # auditable ledger record, but discovery stays truthful.
                continue
            current = caps[capability_id]
            for key in (
                "health", "auth_state", "availability", "latency_ms",
                "reliability", "limits", "permissions", "context",
                "tool_support", "provider_id", "model_id", "failure_class",
                "observed_at", "observation_event_id", "evidence_refs",
            ):
                if key in payload:
                    current[key] = payload[key]
            current["observation_event_id"] = event.event_id
    return caps


def register_capability(database: FoundryDatabase, *, capability_id: str,
                        name: str, family: str, surfaces: list[str],
                        effect_class: str, risk: str,
                        verification_hint: str, controller: str,
                        source_type: str, source_id: str,
                        provider_id: str | None = None,
                        model_id: str | None = None,
                        permissions: list[str] | None = None,
                        context: dict[str, Any] | None = None,
                        tool_support: list[str] | None = None,
                        limits: dict[str, Any] | None = None,
                        required_authority: str | None = None) -> str:
    _gate(database, controller)
    _require(effect_class in _EFFECT_MATRIX,
             f"unknown effect class: {effect_class}")
    if permissions is not None:
        _require(isinstance(permissions, list), "permissions must be a list")
    if tool_support is not None:
        _require(isinstance(tool_support, list), "tool_support must be a list")
    for label, value in (("context", context), ("limits", limits)):
        if value is not None:
            _require(isinstance(value, dict), f"{label} must be an object")
    payload = {
        "capability_id": capability_id, "name": name, "family": family,
        "surfaces": list(surfaces), "effect_class": effect_class,
        "risk": risk, "verification_hint": verification_hint,
        "controller": controller,
        "provider_id": provider_id,
        "model_id": model_id,
        "permissions": list(permissions) if permissions is not None else None,
        "context": dict(context) if context is not None else None,
        "tool_support": list(tool_support) if tool_support is not None else None,
        "limits": dict(limits) if limits is not None else None,
        "required_authority": required_authority or controller,
        "health": "UNKNOWN", "auth_state": "UNKNOWN",
        "availability": "UNKNOWN", "latency_ms": None,
        "reliability": None,
    }
    content = json.dumps(payload, sort_keys=True)
    event = Event.create(
        "capability.registered", payload,
        idempotency_key=f"capability.registered:{capability_id}:"
                        f"{hashlib.sha256(content.encode('utf-8')).hexdigest()[:8]}",
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


def record_capability_observation(
    database: FoundryDatabase,
    capability_id: str,
    *,
    status: str = "UNKNOWN",
    auth_state: str = "UNKNOWN",
    availability: str = "UNKNOWN",
    latency_ms: float | None = None,
    reliability: float | None = None,
    limits: dict[str, Any] | None = None,
    permissions: list[str] | None = None,
    context: dict[str, Any] | None = None,
    tool_support: list[str] | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    failure_class: str | None = None,
    evidence_refs: list[str] | None = None,
    controller: str,
    source_type: str,
    source_id: str,
) -> str:
    """Record a bounded health/readiness sample for a registered capability.

    This is observation only.  It never registers an unknown id and it never
    stores credentials; callers must provide an existing Supervisor-owned
    capability identity.
    """
    _gate(database, controller)
    capability = _rebuild(database).get(capability_id)
    _require(capability is not None, f"unknown capability: {capability_id}")
    _require(status in _HEALTH_STATES,
             f"status must be one of {sorted(_HEALTH_STATES)}")
    _require(auth_state in _AUTH_STATES,
             f"auth_state must be one of {sorted(_AUTH_STATES)}")
    _require(availability in _AVAILABILITY_STATES,
             f"availability must be one of {sorted(_AVAILABILITY_STATES)}")
    if latency_ms is not None:
        _require(
            isinstance(latency_ms, (int, float))
            and not isinstance(latency_ms, bool)
            and math.isfinite(float(latency_ms))
            and float(latency_ms) >= 0,
            "latency_ms must be a finite non-negative number or None",
        )
    if reliability is not None:
        _require(
            isinstance(reliability, (int, float))
            and not isinstance(reliability, bool)
            and math.isfinite(float(reliability))
            and 0 <= float(reliability) <= 1,
            "reliability must be between 0 and 1 or None",
        )
    for label, value, kind in (
        ("limits", limits, dict), ("context", context, dict),
        ("permissions", permissions, list), ("tool_support", tool_support, list),
    ):
        if value is not None:
            _require(isinstance(value, kind), f"{label} must be a {kind.__name__}")
    payload: dict[str, Any] = {
        "capability_id": capability_id,
        "health": status,
        "auth_state": auth_state,
        "availability": availability,
        "latency_ms": float(latency_ms) if latency_ms is not None else None,
        "reliability": float(reliability) if reliability is not None else None,
    }
    optional = {
        "limits": dict(limits) if limits is not None else None,
        "permissions": list(permissions) if permissions is not None else None,
        "context": dict(context) if context is not None else None,
        "tool_support": list(tool_support) if tool_support is not None else None,
        "provider_id": provider_id,
        "model_id": model_id,
        "failure_class": failure_class,
        "evidence_refs": list(evidence_refs or []),
    }
    payload.update({key: value for key, value in optional.items()
                   if value is not None})
    content = json.dumps(payload, sort_keys=True)
    event = Event.create(
        "capability.observed", payload,
        idempotency_key=(f"capability.observed:{capability_id}:"
                         f"{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}"),
        source_type=source_type, source_id=source_id,
    )
    sequence, created = database.append(event)
    if created:
        return event.event_id
    # Idempotent re-delivery returns the original immutable event identity,
    # which makes callers restart-safe without adding a mutable side table.
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT event_id FROM events WHERE idempotency_key = ?",
            (event.idempotency_key,),
        ).fetchone()
    return str(row[0]) if row is not None else event.event_id


def capability_status(database: FoundryDatabase, capability_id: str) -> dict[str, Any]:
    """Return one bounded capability record, including truthful observation state."""
    capability = _rebuild(database).get(capability_id)
    _require(capability is not None, f"unknown capability: {capability_id}")
    return dict(capability)


def discover_capabilities(database: FoundryDatabase, *,
                          family: str | None = None,
                          surface: str | None = None) -> list[dict[str, Any]]:
    caps = _rebuild(database)
    result = []
    for capability_id in sorted(caps):
        cap = caps[capability_id]
        if family is not None and cap["family"] != family:
            continue
        if surface is not None and surface not in cap["surfaces"]:
            continue
        result.append(dict(cap))
    return result


def broker_mission_need(database: FoundryDatabase, *, need_id: str,
                        surfaces: list[str], controller: str,
                        source_type: str, source_id: str) -> list[str]:
    _gate(database, controller)
    matches = [cap for cap in discover_capabilities(database)
               if any(s in cap["surfaces"] for s in surfaces)]
    matches.sort(key=lambda c: (c["risk"], c["capability_id"]))
    if not matches:
        database.append(Event.create(
            "brokerage.waiting",
            {"need_id": need_id, "reason": "no-matching-capability",
             "surfaces": surfaces, "controller": controller},
            idempotency_key=f"brokerage.waiting:{need_id}",
            source_type=source_type, source_id=source_id))
        return []
    chosen = matches[0]
    database.append(Event.create(
        "brokerage.decision",
        {"need_id": need_id, "capability_id": chosen["capability_id"],
         "risk": chosen["risk"], "controller": controller,
         "reason": "lowest risk among surface matches"},
        idempotency_key=f"brokerage.decision:{need_id}:"
                        f"{chosen['capability_id']}",
        source_type=source_type, source_id=source_id))
    return [chosen["capability_id"]]


def matrix_for_capability(database: FoundryDatabase,
                          capability_id: str) -> dict[str, str]:
    caps = _rebuild(database)
    cap = caps.get(capability_id)
    _require(cap is not None, f"unknown capability: {capability_id}")
    row = dict(_EFFECT_MATRIX[cap["effect_class"]])
    row["capability_id"] = capability_id
    row["effect_class"] = cap["effect_class"]
    row["verification_hint"] = cap["verification_hint"]
    return row


def reconciliation_matrix(database: FoundryDatabase) -> list[dict[str, str]]:
    return [matrix_for_capability(database, cap["capability_id"])
            for cap in discover_capabilities(database)]


def matrix_complete(database: FoundryDatabase) -> bool:
    caps = discover_capabilities(database)
    if not caps:
        return False
    return all(cap["effect_class"] in _EFFECT_MATRIX for cap in caps)
