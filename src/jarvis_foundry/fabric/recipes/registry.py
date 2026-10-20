"""Named recipe registry: list, load, validate, inspect/dump."""
from __future__ import annotations

from typing import Any, Mapping

from ..model import FabricError, WorkflowStep
from .builtins import BUILTIN_RECIPE_MAPPINGS
from .model import Recipe, recipe_from_mapping


def _all_mappings() -> tuple[dict[str, Any], ...]:
    return tuple(dict(item) for item in BUILTIN_RECIPE_MAPPINGS)


def list_recipes() -> list[dict[str, Any]]:
    recipes = [recipe_from_mapping(item) for item in _all_mappings()]
    recipes.sort(key=lambda item: item.recipe_id)
    return [
        {
            "recipe_id": item.recipe_id,
            "version": item.version,
            "description": item.description,
            "required_capabilities": list(item.required_capabilities),
            "steps": len(item.steps),
            "tags": list(item.tags),
            "needs": list(item.needs),
        }
        for item in recipes
    ]


def load_recipe(recipe_id: str) -> Recipe:
    wanted = str(recipe_id or "").strip()
    for item in _all_mappings():
        if str(item.get("recipe_id")) == wanted:
            return recipe_from_mapping(item)
    raise FabricError(f"unknown recipe: {wanted}")


def validate_recipe(recipe: Recipe | Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        current = recipe if isinstance(recipe, Recipe) else recipe_from_mapping(recipe)
    except FabricError as error:
        return [str(error)]
    if not current.description.strip():
        errors.append("description is required")
    for param in current.parameters:
        if not param.name.strip():
            errors.append("parameter name is required")
    seen = {step.step_id for step in current.steps}
    for ref in current.outputs.values():
        head = str(ref)[1:].split(".", 1)[0] if str(ref).startswith("$") else ""
        if head and head not in seen and head != "params":
            errors.append(f"output {ref!r} references unknown step {head!r}")
    for step in current.steps:
        if not step.action_kind.strip():
            errors.append(f"step {step.step_id!r} is missing action_kind")
    return errors


def dump_recipe(recipe: Recipe | str) -> dict[str, Any]:
    current = load_recipe(str(recipe)) if isinstance(recipe, str) else recipe
    return current.as_dict()


def recipe_steps_with_params(recipe: Recipe, params: Mapping[str, Any]) -> tuple[WorkflowStep, ...]:
    """Merge defaults + supplied params; params are NOT yet bound into payloads.

    Payloads keep their ``$params.*`` / ``$step.*`` references; the runner
    seeds ``params`` into the binding context so ``compose.bind_payload``
    resolves them deterministically at step time.
    """
    from .preflight import resolve_params

    resolved = resolve_params(recipe, params)
    _ = resolved
    return tuple(
        WorkflowStep(
            step_id=step.step_id,
            action_kind=step.action_kind,
            payload=dict(step.payload),
            on_failure=step.on_failure,
            step_version=step.step_version,
            postconditions=tuple(step.postconditions),
        )
        for step in recipe.steps
    )
