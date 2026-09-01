"""Generic multi-capability composition.

Workflows are sequences of fabric actions. Later payloads bind to earlier
observations with ``$step.field`` references. Combinations stay data; this
module does not hardcode research-to-commit or diagnose-to-repair paths.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..database import FoundryDatabase
from ..world_model import writeback_mission_outcome
from .catalog import catalog_snapshot
from .executors import ExecutorContext
from .loop import execute_action, seed_builtins, try_acquire
from .model import (
    ClaimOutcome,
    FabricError,
    TaskIntent,
    WorkflowStep,
)
from .providers import route_provider


def lookup(path: str, context: Mapping[str, Any]) -> Any:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise FabricError(f"workflow binding ${path} is missing")
        current = current[part]
    return current


def bind_value(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$") and not value.startswith("$$"):
        return lookup(value[1:], context)
    if isinstance(value, Mapping):
        return {str(key): bind_value(item, context) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [bind_value(item, context) for item in value]
    return value


def bind_payload(payload: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    return bind_value(dict(payload), context)


def run_workflow(
    database: FoundryDatabase,
    ctx: ExecutorContext,
    intent: TaskIntent,
    steps: tuple[WorkflowStep, ...] | list[WorkflowStep],
    *,
    workflow_id: str | None = None,
    write_world_model: bool = True,
) -> dict[str, Any]:
    """Execute ordered steps, binding each payload against prior observations."""
    seed_builtins(database)
    ordered = tuple(steps)
    if not ordered:
        raise FabricError("workflow requires at least one step")
    ids = [step.step_id for step in ordered]
    if len(ids) != len(set(ids)):
        raise FabricError("workflow step_id values must be unique")
    resolved_workflow_id = str(workflow_id or f"workflow:{intent.task_id}")
    # ``context`` is the diagnostic record returned to the caller and keeps
    # truthful failed observations (for example a test return code).  Only
    # ``binding_context`` is executable composition input; it receives a
    # step after independent verification succeeds.
    context: dict[str, Any] = {}
    binding_context: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    stopped_at: str | None = None
    for step in ordered:
        payload = bind_payload(step.payload, binding_context)
        if step.postconditions:
            payload["postconditions"] = list(step.postconditions)
        requested_attempt = payload.get("attempt", 1)
        attempt = requested_attempt if isinstance(requested_attempt, int) and requested_attempt > 0 else 1
        result = execute_action(
            database,
            ctx,
            intent,
            step.action_kind,
            payload,
            workflow_id=resolved_workflow_id,
            step_id=step.step_id,
            step_version=step.step_version,
            attempt=attempt,
        )
        if result.claimed is ClaimOutcome.BLOCKED and result.capability_id == "missing":
            acquired = try_acquire(database, ctx, step.action_kind)
            if acquired.get("status") == "REGISTERED":
                result = execute_action(
                    database,
                    ctx,
                    intent,
                    step.action_kind,
                    payload,
                    workflow_id=resolved_workflow_id,
                    step_id=step.step_id,
                    step_version=step.step_version,
                    attempt=attempt,
                )
        row = result.as_dict()
        row["step_id"] = step.step_id
        results.append(row)
        context[step.step_id] = dict(result.observed)
        if result.success:
            # Only independently verified observations become composition
            # inputs. Claims, blocked results, and ambiguous outcomes cannot
            # silently drive a later effect.
            binding_context[step.step_id] = dict(result.observed)
            continue
        if step.on_failure == "continue":
            continue
        stopped_at = step.step_id
        break
    success = (
        stopped_at is None
        and all(
            item["claimed"] == "COMPLETED" and item["verified"] == "MATCH"
            for item in results
        )
        and len(results) == len(ordered)
    )
    if success:
        outcome = "PASS"
    elif any(item["claimed"] == "BLOCKED" for item in results):
        outcome = "BLOCKED"
    elif any(
        (item.get("receipt") or {}).get("state") in {"FROZEN", "UNKNOWN"}
        or item.get("verified") == "UNKNOWN"
        for item in results
    ):
        outcome = "UNKNOWN"
    else:
        outcome = "FAILED"

    world_report = None
    if write_world_model:
        safe_receipts = [
            dict(item["receipt"])
            for item in results
            if isinstance(item.get("receipt"), Mapping)
        ]
        evidence_refs = tuple(
            str(receipt["receipt_id"])
            for receipt in safe_receipts
            if receipt.get("receipt_id")
        )
        verified_terminal = bool(results) and all(
            item.get("verified") in {"MATCH", "MISMATCH"}
            and item.get("claimed") != "BLOCKED"
            and (item.get("receipt") or {}).get("state") not in {"FROZEN", "UNKNOWN"}
            for item in results
        )
        if verified_terminal:
            world_report = writeback_mission_outcome(
                database,
                intent.task_id,
                outcome,
                {
                    "workflow_id": resolved_workflow_id,
                    "terminal": len(results) == len(ordered) or stopped_at is not None,
                    "verified_success": success,
                    "receipts": safe_receipts,
                },
                evidence_refs=evidence_refs,
                source_id="v10-fabric-workflow",
            )
            observation_id = (world_report.get("observation") or {}).get("observation_id")
            if observation_id:
                for item in results:
                    receipt = item.get("receipt")
                    if isinstance(receipt, dict):
                        receipt["world_model_refs"] = [str(observation_id)]
        else:
            world_report = {
                "status": "NOT_EMITTED",
                "reason": "terminal outcome is not independently verified",
                "outcome": outcome,
                "verified_success": False,
            }

    return {
        "task_id": intent.task_id,
        "workflow_id": resolved_workflow_id,
        "success": success,
        "results": results,
        "context": context,
        "verified_context": binding_context,
        "stopped_at": stopped_at,
        "catalog": catalog_snapshot(database),
        "provider": route_provider(unattended=intent.unattended).as_dict(),
        "world_report": world_report,
        "receipts": [item["receipt"] for item in results if item.get("receipt")],
    }
