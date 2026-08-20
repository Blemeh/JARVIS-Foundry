"""Supervisor-owned admission for authenticated owner GOAL directives.

The Command Center records owner intent on the authenticated directive ledger.
This module is the small missing handoff between that ledger and the existing
mission/contract runtime.  It deliberately does not execute work: it only
materializes a bounded, Supervisor-owned step on the already configured
worker resource.  The normal worker loop remains the sole executor.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from ..mission.contracts import MissionStepContract, MissionStepContractError, record_step_contract
from ..owner_controls import canonical_command
from ..runtime.config import WorkerRuntimeConfig


OWNER_GOAL_ADMITTED = "supervisor.owner_goal_admitted"
OWNER_GOAL_BLOCKED = "supervisor.owner_goal_admission_blocked"
OWNER_GOAL_STEP = "step-a"
OWNER_GOAL_ARTIFACT = "step_a.md"
OWNER_GOAL_EXPECTED = "STEP-A\n"


@dataclass(frozen=True, slots=True)
class OwnerGoalAdmission:
    directive_id: str
    status: str
    mission_id: str | None = None
    contract_id: str | None = None
    reason: str | None = None


def _post_start_goals(
    database: FoundryDatabase, *, after_sequence: int,
) -> list[dict[str, Any]]:
    """Read authenticated GOAL directives recorded after this Supervisor start.

    A start watermark prevents old owner goals from being replayed as new
    missions when a release is activated.  The owner service writes the nonce
    and directive atomically, so both records are in the bounded query window.
    """
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT sequence, event_id, event_type, source_type,
                   correlation_id, causation_id, payload_json
            FROM events
            WHERE sequence > ?
              AND event_type IN ('owner_control.nonce_used',
                                 'owner_directive.recorded',
                                 'owner_directive.revoked')
            ORDER BY sequence
            """,
            (int(after_sequence),),
        ).fetchall()

    nonce_events: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        event_type = str(row["event_type"])
        if event_type == "owner_control.nonce_used":
            nonce = payload.get("nonce")
            command_hash = payload.get("command_hash")
            if (row["source_type"] == "owner"
                    and isinstance(nonce, str) and nonce
                    and isinstance(command_hash, str) and command_hash):
                nonce_events[nonce] = {
                    "event_id": row["event_id"],
                    "correlation_id": row["correlation_id"],
                    "payload": payload,
                }
            continue
        if event_type == "owner_directive.revoked":
            directive_id = str(payload.get("directive_id") or "")
            if directive_id in records:
                records[directive_id]["active"] = False
            continue
        if (row["source_type"] != "owner"
                or payload.get("authenticated") is not True
                or payload.get("command_type") != "GOAL"):
            continue
        directive_id = str(payload.get("directive_id") or "")
        exact_text = payload.get("exact_text")
        nonce = payload.get("nonce")
        command_hash = payload.get("command_hash")
        target = payload.get("target")
        nonce_event = nonce_events.get(nonce) if isinstance(nonce, str) else None
        if (not directive_id or not isinstance(exact_text, str)
                or not exact_text.strip() or not isinstance(nonce, str)
                or nonce_event is None or not isinstance(command_hash, str)
                or not command_hash):
            continue
        try:
            expected_hash = hashlib.sha256(canonical_command(
                "GOAL", str(target) if target is not None else None,
                exact_text, nonce,
            )).hexdigest()
        except (TypeError, ValueError):
            continue
        if (command_hash != expected_hash
                or nonce_event["payload"].get("command_hash") != expected_hash
                or row["causation_id"] != nonce_event["event_id"]
                or row["correlation_id"] != nonce_event["correlation_id"]):
            continue
        records[directive_id] = {
            "directive_id": directive_id,
            "exact_text": exact_text.strip(),
            "target": str(target) if target is not None else None,
            "command_hash": command_hash,
            "event_id": row["event_id"],
            "active": True,
        }
    return [record for record in records.values() if record["active"]]


def _mission_id(directive_id: str) -> str:
    # Keep the durable identity bounded and independent of arbitrary owner text.
    digest = hashlib.sha256(directive_id.encode("utf-8")).hexdigest()[:20]
    return f"mission-owner-goal-{digest}"


def _existing_mission(database: FoundryDatabase, directive_id: str) -> str | None:
    marker = f"owner-directive:{directive_id}"
    for event in database.events():
        if event.event_type != "mission.created":
            continue
        if str(event.payload.get("created_from") or "") == marker:
            return str(event.payload.get("mission_id") or "") or None
    return None


def _record_blocked(
    database: FoundryDatabase,
    *,
    directive_id: str,
    reason: str,
    source_id: str,
) -> OwnerGoalAdmission:
    reason_hash = hashlib.sha256(reason.encode("utf-8")).hexdigest()[:16]
    database.append(Event.create(
        OWNER_GOAL_BLOCKED,
        {"directive_id": directive_id, "reason": reason},
        source_type="supervisor", source_id=source_id,
        idempotency_key=f"{OWNER_GOAL_BLOCKED}:{directive_id}:{reason_hash}",
    ))
    return OwnerGoalAdmission(directive_id, "BLOCKED", reason=reason)


def _materialize(
    database: FoundryDatabase,
    directive: dict[str, Any],
    config: WorkerRuntimeConfig,
    *,
    source_id: str,
) -> OwnerGoalAdmission:
    directive_id = str(directive["directive_id"])
    objective = str(directive["exact_text"])
    target = directive.get("target")
    if target not in (None, "GLOBAL"):
        return _record_blocked(
            database, directive_id=directive_id,
            reason="owner GOAL target must be GLOBAL for bounded admission",
            source_id=source_id,
        )
    if len(objective) > 4000:
        return _record_blocked(
            database, directive_id=directive_id,
            reason="owner GOAL text exceeds bounded admission size",
            source_id=source_id,
        )
    resource_id = str(config.resource_id or "")
    capability = str(config.capability or "")
    if not resource_id or not capability:
        return _record_blocked(
            database, directive_id=directive_id,
            reason="worker runtime config has no resource/capability binding",
            source_id=source_id,
        )
    resource = database.row("resources", "resource_id", resource_id)
    if resource is None:
        return _record_blocked(
            database, directive_id=directive_id,
            reason=f"configured resource is not registered: {resource_id}",
            source_id=source_id,
        )
    try:
        capabilities = json.loads(str(resource["capabilities_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        capabilities = {}
    if not isinstance(capabilities, dict) or capabilities.get(capability) not in {"READY", "ACTIVE"}:
        return _record_blocked(
            database, directive_id=directive_id,
            reason=f"configured capability is not READY: {resource_id}/{capability}",
            source_id=source_id,
        )

    mission_id = _existing_mission(database, directive_id) or _mission_id(directive_id)
    created_from = f"owner-directive:{directive_id}"
    if _existing_mission(database, directive_id) is None:
        next_action = f"create {OWNER_GOAL_ARTIFACT} containing STEP-A"
        database.append_events([
            Event.create(
                "mission.created",
                {
                    "mission_id": mission_id,
                    "objective": objective,
                    "resource_id": resource_id,
                    "state": "PLANNED",
                    "acceptance_criteria": [
                        f"{OWNER_GOAL_ARTIFACT} contains {OWNER_GOAL_EXPECTED.strip()}"
                    ],
                    "next_action": next_action,
                    "created_from": created_from,
                    "owner_directive_id": directive_id,
                },
                source_type="supervisor", source_id=source_id,
                idempotency_key=f"mission.created:{created_from}",
            ),
            Event.create(
                "mission.state_changed",
                {"mission_id": mission_id, "state": "ORIENTING", "next_action": next_action},
                source_type="supervisor", source_id=source_id,
                idempotency_key=f"mission.state_changed:{mission_id}:ORIENTING:owner-goal",
            ),
            Event.create(
                "mission.state_changed",
                {"mission_id": mission_id, "state": "ACTION_SELECTION", "next_action": next_action},
                source_type="supervisor", source_id=source_id,
                idempotency_key=f"mission.state_changed:{mission_id}:ACTION_SELECTION:owner-goal",
            ),
        ])

    contract = MissionStepContract(
        mission_id=mission_id,
        step_id=OWNER_GOAL_STEP,
        version=1,
        resource_id=resource_id,
        capability=capability,
        target_relative_path=OWNER_GOAL_ARTIFACT,
        instructions=(
            f"Bounded owner goal: {objective}\n"
            f"Create {OWNER_GOAL_ARTIFACT} containing {OWNER_GOAL_EXPECTED.strip()}."
        ),
        expected_effects=(f"{OWNER_GOAL_ARTIFACT} exists",),
        rollback={"method": "git_reset_soft", "reversible": True},
        risk_class="C1",
        verification_criteria=(f"{OWNER_GOAL_ARTIFACT} contains {OWNER_GOAL_EXPECTED.strip()}",),
        expected_artifacts=(OWNER_GOAL_ARTIFACT,),
        forbidden_paths=("owner-private",),
        recorded_by="supervisor",
        source="supervisor-owner-goal",
        provenance={
            "owner_directive_id": directive_id,
            "owner_event_id": directive["event_id"],
            "command_hash": directive["command_hash"],
            "admission": "bounded-existing-worker-config",
        },
    )
    try:
        record_step_contract(database, contract)
    except MissionStepContractError as error:
        return _record_blocked(
            database, directive_id=directive_id,
            reason=f"owner GOAL contract rejected: {error}",
            source_id=source_id,
        )
    database.append(Event.create(
        OWNER_GOAL_ADMITTED,
        {
            "directive_id": directive_id,
            "mission_id": mission_id,
            "contract_id": contract.contract_id,
            "resource_id": resource_id,
            "capability": capability,
        },
        source_type="supervisor", source_id=source_id,
        idempotency_key=f"{OWNER_GOAL_ADMITTED}:{directive_id}",
    ))
    return OwnerGoalAdmission(
        directive_id, "ADMITTED", mission_id=mission_id,
        contract_id=contract.contract_id,
    )


def admit_owner_goals(
    database: FoundryDatabase,
    config: WorkerRuntimeConfig,
    *,
    after_sequence: int,
    source_id: str,
) -> tuple[OwnerGoalAdmission, ...]:
    """Materialize new authenticated owner goals through the existing kernel."""
    if not config.enabled:
        return ()
    results: list[OwnerGoalAdmission] = []
    for directive in _post_start_goals(database, after_sequence=after_sequence):
        try:
            results.append(_materialize(database, directive, config, source_id=source_id))
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            results.append(_record_blocked(
                database, directive_id=str(directive["directive_id"]),
                reason=f"owner GOAL admission failed closed: {type(error).__name__}: {error}",
                source_id=source_id,
            ))
    return tuple(results)
