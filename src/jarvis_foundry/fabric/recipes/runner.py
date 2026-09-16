"""Recipe runner: preflight + typed bindings + checkpointed workflow execution."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..compose import bind_payload, lookup, run_workflow
from ..model import FabricError, TaskIntent, WorkflowStep
from .checkpoint import build_checkpoint
from .model import Recipe
from .preflight import preflight_recipe, resolve_params
from .registry import load_recipe

import re as _re

_PARAMS_ROOTS = frozenset({"params", "input"})
_EMBEDDED_REF = _re.compile(r"\$\{([A-Za-z0-9_.:/-]+)\}")


def _bind_params_only(value: object, params: dict) -> object:
    """Render ``$params.*`` / ``$input.*`` / ``${params.*}`` now; keep step refs for run time."""
    scope = {"params": params, "input": params}
    if isinstance(value, str):
        if value.startswith("$$") and "${" not in value:
            return value
        if value.startswith("$") and not value.startswith("$$") and "${" not in value:
            head = value[1:].split(".", 1)[0]
            if head in _PARAMS_ROOTS:
                return lookup(value[1:], scope)
            return value
        if "${" in value:
            if value.startswith("${") and value.endswith("}") and value.count("${") == 1:
                inner = value[2:-1]
                if inner.split(".", 1)[0] in _PARAMS_ROOTS:
                    return lookup(inner, scope)
                return value
            def _replace(match: object) -> str:
                path = match.group(1)  # type: ignore[union-attr]
                if path.split(".", 1)[0] not in _PARAMS_ROOTS:
                    return match.group(0)  # type: ignore[union-attr]
                bound = lookup(path, scope)
                return bound if isinstance(bound, str) else __import__("json").dumps(
                    bound, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
            return _EMBEDDED_REF.sub(_replace, value.replace("$${", "\x00ESCAPED_BINDING\x00")).replace(
                "\x00ESCAPED_BINDING\x00", "${"
            )
        return value
    if isinstance(value, dict):
        return {str(key): _bind_params_only(item, params) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bind_params_only(item, params) for item in value]
    return value


def _bind_outputs(outputs: Mapping[str, str], verified_context: Mapping[str, Any]) -> dict[str, Any]:
    bound: dict[str, Any] = {}
    missing: list[str] = []
    for key, ref in outputs.items():
        try:
            if isinstance(ref, str) and ref.startswith("$"):
                bound[str(key)] = lookup(ref[1:], verified_context)
            else:
                bound[str(key)] = bind_payload({"value": ref}, verified_context)["value"]
        except FabricError:
            missing.append(str(key))
    if missing:
        raise FabricError(f"recipe outputs are missing verified bindings: {', '.join(sorted(missing))}")
    return bound


def run_recipe(
    database: Any,
    ctx: Any,
    recipe: Recipe | str,
    params: Mapping[str, Any] | None = None,
    *,
    task_id: str | None = None,
    workflow_id: str | None = None,
    checkpoint_path: str | Path | None = None,
    write_world_model: bool = True,
) -> dict[str, Any]:
    from ..loop import seed_builtins as _seed_builtins

    _seed_builtins(database)
    current = load_recipe(str(recipe)) if isinstance(recipe, str) else recipe
    resolved = resolve_params(current, params)
    report = preflight_recipe(database, current, resolved, owned_root=getattr(ctx, "owned_root", None))
    if not report["ready"]:
        raise FabricError(
            f"recipe {current.recipe_id} preflight is not ready; blocked_step={report['blocked_step']}"
        )
    steps = tuple(
        WorkflowStep(
            step_id=step.step_id,
            action_kind=step.action_kind,
            payload=dict(step.payload),
            on_failure=step.on_failure,
            step_version=step.step_version,
            postconditions=tuple(step.postconditions),
        )
        for step in current.steps
    )
    # Params/defaults render deterministically before dispatch; verified
    # step outputs ($step.field) still bind at step time inside run_workflow.
    rendered: list[WorkflowStep] = []
    for step in steps:
        payload = _bind_params_only(dict(step.payload), dict(resolved))
        assert isinstance(payload, dict)
        rendered.append(WorkflowStep(
            step_id=step.step_id,
            action_kind=step.action_kind,
            payload=dict(payload),
            on_failure=step.on_failure,
            step_version=step.step_version,
            postconditions=tuple(step.postconditions),
        ))
    intent = TaskIntent(
        task_id=str(task_id or f"recipe-{current.recipe_id}".replace(".", "-").replace("_", "-")[:60] or "recipe"),
        required_actions=tuple(step.action_kind for step in rendered),
    )
    result = run_workflow(
        database,
        ctx,
        intent,
        tuple(rendered),
        workflow_id=workflow_id or f"workflow:{intent.task_id}",
        write_world_model=write_world_model,
        checkpoint_path=checkpoint_path,
    )
    try:
        outputs = _bind_outputs(current.outputs, result.get("verified_context") or {})
    except FabricError:
        outputs = {}
    summary = {
        "recipe_id": current.recipe_id,
        "version": current.version,
        "success": bool(result.get("success")),
        "outcome": str(result.get("outcome") or ("PASS" if result.get("success") else "FAILED")),
        "stopped_at": result.get("stopped_at"),
        "completed": [row["step_id"] for row in result.get("results", []) if row.get("claimed") == "COMPLETED" and row.get("verified") == "MATCH"],
        "failed": [row["step_id"] for row in result.get("results", []) if not (row.get("claimed") == "COMPLETED" and row.get("verified") == "MATCH") and not row.get("resumed")],
        "soft_failed_steps": list(result.get("soft_failed_steps") or ()),
        "resumed_steps": list(result.get("resumed_steps") or ()),
        "outputs": outputs,
        "receipts": list(result.get("receipts") or ()),
    }
    return {**result, "recipe": {"recipe_id": current.recipe_id, "version": current.version}, "summary": summary}


def resume_recipe(
    database: Any,
    ctx: Any,
    recipe: Recipe | str,
    params: Mapping[str, Any] | None = None,
    *,
    checkpoint: Mapping[str, Any],
    task_id: str | None = None,
    workflow_id: str | None = None,
    checkpoint_path: str | Path | None = None,
    write_world_model: bool = True,
) -> dict[str, Any]:
    from ..loop import seed_builtins as _seed_builtins

    _seed_builtins(database)
    current = load_recipe(str(recipe)) if isinstance(recipe, str) else recipe
    resolved = resolve_params(current, params)
    rendered: list[WorkflowStep] = []
    for step in current.steps:
        payload = _bind_params_only(dict(step.payload), dict(resolved))
        assert isinstance(payload, dict)
        rendered.append(WorkflowStep(
            step_id=step.step_id,
            action_kind=step.action_kind,
            payload=dict(payload),
            on_failure=step.on_failure,
            step_version=step.step_version,
            postconditions=tuple(step.postconditions),
        ))
    intent = TaskIntent(
        task_id=str(task_id or str(checkpoint.get("task_id") or f"recipe-{current.recipe_id}".replace(".", "-")[:60])),
        required_actions=tuple(step.action_kind for step in rendered),
    )
    result = run_workflow(
        database,
        ctx,
        intent,
        tuple(rendered),
        workflow_id=str(workflow_id or checkpoint.get("workflow_id") or f"workflow:{intent.task_id}"),
        write_world_model=write_world_model,
        resume_checkpoint=checkpoint,
        skip_completed=True,
        checkpoint_path=checkpoint_path,
    )
    try:
        outputs = _bind_outputs(current.outputs, result.get("verified_context") or {})
    except FabricError:
        outputs = {}
    summary = {
        "recipe_id": current.recipe_id,
        "version": current.version,
        "success": bool(result.get("success")),
        "outcome": str(result.get("outcome") or ("PASS" if result.get("success") else "FAILED")),
        "stopped_at": result.get("stopped_at"),
        "completed": [row["step_id"] for row in result.get("results", []) if row.get("claimed") == "COMPLETED" and row.get("verified") == "MATCH"],
        "failed": [row["step_id"] for row in result.get("results", []) if not (row.get("claimed") == "COMPLETED" and row.get("verified") == "MATCH")],
        "soft_failed_steps": list(result.get("soft_failed_steps") or ()),
        "resumed_steps": list(result.get("resumed_steps") or ()),
        "outputs": outputs,
        "receipts": list(result.get("receipts") or ()),
    }
    return {**result, "recipe": {"recipe_id": current.recipe_id, "version": current.version}, "summary": summary}


def workflow_result_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    rows = result.get("results") or ()
    receipts = [row.get("receipt") for row in rows if isinstance(row, Mapping) and row.get("receipt")]
    return {
        "workflow_id": result.get("workflow_id"),
        "task_id": result.get("task_id"),
        "success": bool(result.get("success")),
        "outcome": str(result.get("outcome") or ""),
        "stopped_at": result.get("stopped_at"),
        "completed": [str(row.get("step_id")) for row in rows if isinstance(row, Mapping) and row.get("claimed") == "COMPLETED" and row.get("verified") == "MATCH"],
        "effect_ids": [str(receipt.get("effect_id")) for receipt in receipts if isinstance(receipt, Mapping) and receipt.get("effect_id")],
        "receipt_ids": [str(receipt.get("receipt_id")) for receipt in receipts if isinstance(receipt, Mapping) and receipt.get("receipt_id")],
        "resumed_steps": list(result.get("resumed_steps") or ()),
        "soft_failed_steps": list(result.get("soft_failed_steps") or ()),
        "checkpoint": build_checkpoint(result),
    }
