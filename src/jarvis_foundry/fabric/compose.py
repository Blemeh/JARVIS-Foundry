"""Generic multi-capability composition.

Workflows are sequences of fabric actions. Later payloads bind to earlier
observations with ``$step.field`` references. Combinations stay data; this
module does not hardcode research-to-commit or diagnose-to-repair paths.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..database import FoundryDatabase
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
) -> dict[str, Any]:
    """Execute ordered steps, binding each payload against prior observations."""
    seed_builtins(database)
    ordered = tuple(steps)
    if not ordered:
        raise FabricError("workflow requires at least one step")
    ids = [step.step_id for step in ordered]
    if len(ids) != len(set(ids)):
        raise FabricError("workflow step_id values must be unique")
    context: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    stopped_at: str | None = None
    for step in ordered:
        payload = bind_payload(step.payload, context)
        result = execute_action(database, ctx, intent, step.action_kind, payload)
        if result.claimed is ClaimOutcome.BLOCKED and result.capability_id == "missing":
            acquired = try_acquire(database, ctx, step.action_kind)
            if acquired.get("status") == "REGISTERED":
                result = execute_action(database, ctx, intent, step.action_kind, payload)
        row = result.as_dict()
        row["step_id"] = step.step_id
        results.append(row)
        context[step.step_id] = dict(result.observed)
        if result.success:
            continue
        if step.on_failure == "continue":
            continue
        stopped_at = step.step_id
        break
    return {
        "task_id": intent.task_id,
        "success": stopped_at is None and all(item["claimed"] == "COMPLETED" and item["verified"] == "MATCH" for item in results) and len(results) == len(ordered),
        "results": results,
        "context": context,
        "stopped_at": stopped_at,
        "catalog": catalog_snapshot(database),
        "provider": route_provider(unattended=intent.unattended).as_dict(),
    }
