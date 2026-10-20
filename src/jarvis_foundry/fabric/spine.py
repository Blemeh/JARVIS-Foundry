"""Mission/worker consumption of fabric results.

This is not a second mission loop. Callers on the canonical spine inject
``execute_for_spine`` as the effect executor and receive a journaled
evidence receipt they can attach to a step.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..database import FoundryDatabase
from ..events import Event, payload_hash
from .executors import ExecutorContext
from .loop import execute_action, seed_builtins
from .model import TaskIntent

SPINE_RESULT = "fabric.spine.result"


def execute_for_spine(
    database: FoundryDatabase,
    ctx: ExecutorContext,
    *,
    mission_id: str,
    step_id: str,
    action_kind: str,
    payload: Mapping[str, Any],
    source_id: str = "v9-fabric-spine",
    workflow_id: str | None = None,
    step_version: int = 1,
    attempt: int = 1,
    result_scope: str | None = None,
    route_owner: str | None = None,
    resource_id: str | None = None,
    resource_capability: str | None = None,
) -> dict[str, Any]:
    """Run one fabric action and journal an evidence receipt for the spine."""
    seed_builtins(database)
    intent = TaskIntent(
        task_id=mission_id,
        required_actions=(action_kind,),
        unattended=True,
        resource_id=resource_id,
        resource_capability=resource_capability,
    )
    result = execute_action(
        database,
        ctx,
        intent,
        action_kind,
        payload,
        workflow_id=workflow_id,
        step_id=step_id,
        step_version=step_version,
        attempt=attempt,
        route_owner=route_owner,
    )
    receipt = dict(result.receipt)
    if not receipt:
        # STOP and unavailable-capability results are deliberately not
        # effects, but the spine still receives a truthful bounded receipt.
        receipt = {
            "mission_id": mission_id,
            "workflow_id": workflow_id or f"workflow:{mission_id}",
            "step_id": step_id,
            "action_kind": action_kind,
            "capability_id": result.capability_id,
            "claimed": result.claimed.value,
            "verified": result.verified.value,
            "success": result.success,
            "note": result.note,
            "observed": {},
        }
    else:
        receipt.update({
            "mission_id": mission_id,
            "step_id": step_id,
            "claimed": result.claimed.value,
            "verified": result.verified.value,
            "success": result.success,
            "note": result.note,
        })
    stable_key = receipt.get("receipt_id") or payload_hash({
        "mission_id": mission_id,
        "step_id": step_id,
        "action_kind": action_kind,
        "payload": dict(payload),
    })
    scope_suffix = (
        f":scope:{payload_hash({'result_scope': result_scope})[:16]}"
        if result_scope
        else ""
    )
    database.append(Event.create(
        SPINE_RESULT,
        receipt,
        source_type="supervisor",
        source_id=source_id,
        correlation_id=mission_id,
        idempotency_key=(
            f"{SPINE_RESULT}:{mission_id}:{step_id}:{stable_key}{scope_suffix}"
        ),
    ))
    return receipt
