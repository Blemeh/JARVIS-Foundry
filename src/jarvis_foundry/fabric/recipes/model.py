"""Canonical recipe data model for Fabric workflow composition.

Recipes are data, not a planner. The Executive decides what should be done;
composition executes a declared recipe through existing Fabric actions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..model import FabricError, WorkflowStep

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_IDENTITY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]*$")
_RECIPE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
# Normalized need vocabulary: lowercase namespaced intent requirements
# ("web.authenticated_interaction") that a recipe declares it can satisfy.
_NEED = re.compile(r"^[a-z][a-z0-9_.-]*$")


def _recipe_identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FabricError(f"{field_name} must be a non-empty identity")
    result = value.strip()
    if not _IDENTITY.fullmatch(result):
        raise FabricError(f"{field_name} must be an exact identity")
    return result


@dataclass(frozen=True, slots=True)
class RecipeParameter:
    name: str
    required: bool = False
    default: Any = None
    has_default: bool = False
    description: str = ""
    bind: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "required": self.required,
            "description": self.description,
        }
        if self.has_default:
            payload["default"] = self.default
        if self.bind:
            payload["bind"] = self.bind
        return payload


@dataclass(frozen=True, slots=True)
class Recipe:
    recipe_id: str
    version: str
    description: str
    required_capabilities: tuple[str, ...] = ()
    parameters: tuple[RecipeParameter, ...] = ()
    steps: tuple[WorkflowStep, ...] = ()
    outputs: Mapping[str, str] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        cleaned_id = str(self.recipe_id or "").strip()
        if not _RECIPE_ID.fullmatch(cleaned_id):
            raise FabricError(f"recipe_id must be a stable dotted identity, got {self.recipe_id!r}")
        object.__setattr__(self, "recipe_id", cleaned_id)
        if not isinstance(self.version, str) or not _SEMVER.match(self.version.strip()):
            raise FabricError(f"recipe version must be N.N.N, got {self.version!r}")
        object.__setattr__(self, "version", self.version.strip())
        if not isinstance(self.description, str) or not self.description.strip():
            raise FabricError("recipe description must be a non-empty string")
        object.__setattr__(self, "description", self.description.strip())
        if not self.steps:
            raise FabricError("recipe requires at least one step")
        seen: set[str] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise FabricError("recipe step_id values must be unique")
            seen.add(step.step_id)
        caps = tuple(_recipe_identity(item, "required_capability") for item in self.required_capabilities)
        object.__setattr__(self, "required_capabilities", caps)
        names = [param.name for param in self.parameters]
        if len(names) != len(set(names)):
            raise FabricError("recipe parameter names must be unique")
        object.__setattr__(self, "outputs", dict(self.outputs))
        for key, ref in self.outputs.items():
            if not isinstance(ref, str) or not ref.startswith("$"):
                raise FabricError(f"recipe output {key!r} must be a $step.field reference")
        object.__setattr__(self, "tags", tuple(str(tag) for tag in self.tags))
        cleaned_needs: list[str] = []
        for item in self.needs:
            need = str(item or "").strip()
            if not _NEED.fullmatch(need):
                raise FabricError(f"need must be a normalized lowercase identity, got {item!r}")
            cleaned_needs.append(need)
        object.__setattr__(self, "needs", tuple(cleaned_needs))

    def as_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "version": self.version,
            "description": self.description,
            "required_capabilities": list(self.required_capabilities),
            "parameters": [param.as_dict() for param in self.parameters],
            "steps": [
                {
                    "step_id": step.step_id,
                    "action_kind": step.action_kind,
                    "payload": dict(step.payload),
                    "on_failure": step.on_failure,
                    "step_version": step.step_version,
                    "postconditions": list(step.postconditions),
                }
                for step in self.steps
            ],
            "outputs": dict(self.outputs),
            "tags": list(self.tags),
            "needs": list(self.needs),
        }


def parameter_from_mapping(name: str, value: Mapping[str, Any] | Any) -> RecipeParameter:
    if not isinstance(value, Mapping):
        return RecipeParameter(name=str(name), required=False, default=value, has_default=True)
    has_default = "default" in value
    return RecipeParameter(
        name=str(name),
        required=bool(value.get("required", not has_default)),
        default=value.get("default"),
        has_default=has_default,
        description=str(value.get("description") or ""),
        bind=str(value.get("bind") or ""),
    )


def recipe_from_mapping(payload: Mapping[str, Any]) -> Recipe:
    raw_params = payload.get("parameters") or {}
    if not isinstance(raw_params, Mapping):
        raise FabricError("recipe parameters must be a mapping")
    parameters = tuple(parameter_from_mapping(str(name), spec) for name, spec in raw_params.items())
    raw_steps = payload.get("steps") or []
    steps: list[WorkflowStep] = []
    for item in raw_steps:
        if not isinstance(item, Mapping):
            raise FabricError("recipe steps must be mappings")
        steps.append(WorkflowStep(
            step_id=str(item["step_id"]),
            action_kind=str(item["action_kind"]),
            payload=dict(item.get("payload") or {}),
            on_failure=str(item.get("on_failure") or "stop"),
            step_version=int(item.get("step_version") or 1),
            postconditions=tuple(item.get("postconditions") or ()),
        ))
    return Recipe(
        recipe_id=str(payload["recipe_id"]),
        version=str(payload.get("version") or "1.0.0"),
        description=str(payload.get("description") or ""),
        required_capabilities=tuple(payload.get("required_capabilities") or ()),
        parameters=parameters,
        steps=tuple(steps),
        outputs=dict(payload.get("outputs") or {}),
        tags=tuple(payload.get("tags") or ()),
        needs=tuple(payload.get("needs") or ()),
    )
