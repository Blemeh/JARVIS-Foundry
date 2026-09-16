"""Capability preflight for recipes: structured, never silent-downgrade."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..catalog import specs_for_action
from ..model import FabricError
from .model import Recipe


def resolve_params(recipe: Recipe, params: Mapping[str, Any] | None) -> dict[str, Any]:
    supplied = dict(params or {})
    resolved: dict[str, Any] = {}
    missing: list[str] = []
    for param in recipe.parameters:
        if param.name in supplied:
            resolved[param.name] = supplied[param.name]
        elif param.has_default:
            resolved[param.name] = param.default
        elif param.required:
            missing.append(param.name)
    if missing:
        raise FabricError(f"recipe {recipe.recipe_id} is missing required params: {', '.join(sorted(missing))}")
    for key in supplied:
        if key not in resolved:
            resolved[key] = supplied[key]
    return resolved


def preflight_recipe(
    database: Any,
    recipe: Recipe,
    params: Mapping[str, Any] | None,
    *,
    owned_root: str | Path | None = None,
) -> dict[str, Any]:
    """Answer: registered? params present? scope usable? which step blocked?"""
    try:
        resolved = resolve_params(recipe, params)
        params_ok = True
        missing_params: list[str] = []
    except FabricError as error:
        resolved = dict(params or {})
        params_ok = False
        missing_params = [str(error)]
    step_reports: list[dict[str, Any]] = []
    blocked_step: str | None = None
    for step in recipe.steps:
        specs = specs_for_action(database, step.action_kind)
        registered = bool(specs)
        entry: dict[str, Any] = {
            "step_id": step.step_id,
            "action_kind": step.action_kind,
            "registered": registered,
            "capability_ids": [spec.capability_id for spec in specs],
            "blocked": not registered,
        }
        if not registered:
            entry["reason"] = f"action {step.action_kind} is not registered"
            if blocked_step is None:
                blocked_step = step.step_id
        step_reports.append(entry)
    scope_ok = True
    scope_note = "owned-root scope is enforced at dispatch; no static refusal"
    if owned_root is not None:
        try:
            root = Path(str(owned_root)).expanduser().resolve()
            scope_ok = root.is_absolute()
            scope_note = f"owned_root={root}" if scope_ok else f"owned_root is not absolute: {owned_root}"
        except (OSError, ValueError) as error:
            scope_ok = False
            scope_note = f"owned_root is unusable: {error}"
    ready = params_ok and all(item["registered"] for item in step_reports) and scope_ok
    if not params_ok and blocked_step is None:
        blocked_step = "params"
    return {
        "recipe_id": recipe.recipe_id,
        "version": recipe.version,
        "ready": ready,
        "params_ok": params_ok,
        "missing_params": missing_params,
        "resolved_params": resolved,
        "scope_ok": scope_ok,
        "scope_note": scope_note,
        "steps": step_reports,
        "blocked_step": blocked_step,
    }
