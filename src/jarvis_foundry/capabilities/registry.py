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
from typing import Any

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER, EvolutionControlError,
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
    for event in database.events():
        if event.event_type == "capability.registered":
            payload = event.payload
            caps[payload["capability_id"]] = dict(payload)
    return caps


def register_capability(database: FoundryDatabase, *, capability_id: str,
                        name: str, family: str, surfaces: list[str],
                        effect_class: str, risk: str,
                        verification_hint: str, controller: str,
                        source_type: str, source_id: str) -> str:
    _gate(database, controller)
    _require(effect_class in _EFFECT_MATRIX,
             f"unknown effect class: {effect_class}")
    payload = {
        "capability_id": capability_id, "name": name, "family": family,
        "surfaces": list(surfaces), "effect_class": effect_class,
        "risk": risk, "verification_hint": verification_hint,
        "controller": controller,
    }
    content = json.dumps(payload, sort_keys=True)
    event = Event.create(
        "capability.registered", payload,
        idempotency_key=f"capability.registered:{capability_id}:"
                        f"{hashlib.sha256(content.encode('utf-8')).hexdigest()[:8]}",
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


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
