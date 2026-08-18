"""V7-S10 — capability-family mini-gates.

Every family (research, monitoring, local-computer, git, deployment,
cloud) has a declarative policy: read-only operation classes pass,
effectful classes deny by default. Every gate decision is a ledger
record. All gates are Supervisor-only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.authority import SUPERVISOR_CONTROLLER
from jarvis_foundry.supervisor.evolution_control import (
    EvolutionControlError,
)
from jarvis_foundry.supervisor.estop import EmergencyStop

GateError = EvolutionControlError

# family -> {read_only_classes, effectful_classes}
FAMILIES: dict[str, dict[str, list[str]]] = {
    "research": {
        "read_only_classes": ["web_search", "web_extract"],
        "effectful_classes": ["web_write", "subscribe", "unsubscribe"],
    },
    "monitoring": {
        "read_only_classes": ["list_monitors", "status"],
        "effectful_classes": ["send_alert", "create_monitor",
                              "delete_monitor"],
    },
    "local-computer": {
        "read_only_classes": ["read_file", "list_dir", "process_list"],
        "effectful_classes": ["write_file", "delete_file", "run_command"],
    },
    "git": {
        "read_only_classes": ["status", "log", "diff", "branch"],
        "effectful_classes": ["commit", "push", "merge", "checkout",
                              "revert", "tag"],
    },
    "deployment": {
        "read_only_classes": ["dry_run", "status"],
        "effectful_classes": ["deploy", "rollback", "scale"],
    },
    "cloud": {
        "read_only_classes": ["list_resources", "status"],
        "effectful_classes": ["create_resource", "delete_resource",
                              "modify_resource"],
    },
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def _gate(database: FoundryDatabase, controller: str) -> None:
    _require(controller == SUPERVISOR_CONTROLLER,
             "only the Supervisor can invoke gates")
    _require(not EmergencyStop(database.path.parent / "EMERGENCY_STOP").active(),
             "STOP sentinel active")


def family_policy(database: FoundryDatabase, family: str) -> dict[str, Any]:
    _require(family in FAMILIES, f"unknown family: {family}")
    policy = FAMILIES[family]
    return {"family": family,
            "read_only_classes": list(policy["read_only_classes"]),
            "effectful_classes": list(policy["effectful_classes"])}


def gate_operation(database: FoundryDatabase, *, family: str,
                   operation: str, controller: str, params: dict[str, Any],
                   source_type: str, source_id: str) -> dict[str, Any]:
    _gate(database, controller)
    _require(family in FAMILIES, f"unknown family: {family}")
    policy = FAMILIES[family]
    if operation in policy["read_only_classes"]:
        allowed, reason = True, "read-only class"
        denied = False
    elif operation in policy["effectful_classes"]:
        allowed, reason = False, (
            "deny by default: effectful class not yet authorized "
            f"({operation})")
        denied = True
    else:
        raise GateError(f"unknown operation {operation!r} for {family}")
    payload = {"family": family, "operation": operation,
               "allowed": allowed, "denied": denied, "reason": reason,
               "params": dict(params), "controller": controller}
    params_digest = hashlib.sha256(
        json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    event = Event.create(
        "gate.decision", payload,
        idempotency_key=f"gate.decision:{family}:{operation}:{params_digest}",
        source_type=source_type, source_id=source_id)
    sequence = database.append(event)[0]
    payload["recorded"] = True
    payload["sequence"] = sequence
    return payload


def decision_history(database: FoundryDatabase) -> list[dict[str, Any]]:
    return [dict(e.payload) for e in database.events()
            if e.event_type == "gate.decision"]
