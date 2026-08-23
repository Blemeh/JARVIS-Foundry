"""Generic reversible transaction planning above individual effects."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..events import canonical_json
from .procedure import OperationError


class PlanningError(OperationError):
    pass


def _exact(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningError(f"{field_name} must be non-empty")
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise PlanningError(f"{field_name} contains a control character")
    if any(token in result for token in ("*", "${", "%", "..")):
        raise PlanningError(f"{field_name} must be exact")
    return result


@dataclass(frozen=True, slots=True)
class EffectIntent:
    effect_ticket: str
    action: str
    target: str
    reversible: bool
    compensation: str | None = None

    def __post_init__(self) -> None:
        for key in ("effect_ticket", "action", "target"):
            object.__setattr__(self, key, _exact(getattr(self, key), key))
        if not isinstance(self.reversible, bool):
            raise PlanningError("effect reversibility must be boolean")
        if self.compensation is not None:
            object.__setattr__(self, "compensation", _exact(self.compensation, "compensation"))
        if not self.reversible and self.compensation is not None:
            raise PlanningError("an irreversible effect cannot claim a rollback compensation")


@dataclass(frozen=True, slots=True)
class RollbackOption:
    effect_ticket: str
    method: str
    available: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "effect_ticket", _exact(self.effect_ticket, "effect_ticket"))
        object.__setattr__(self, "method", _exact(self.method, "rollback method"))
        if not isinstance(self.available, bool):
            raise PlanningError("rollback availability must be boolean")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    name: str
    expected: dict[str, Any]
    evidence_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _exact(self.name, "checkpoint name"))
        if not isinstance(self.expected, Mapping) or not self.expected:
            raise PlanningError("checkpoint expected state must be a non-empty object")
        object.__setattr__(self, "expected", dict(self.expected))
        if not isinstance(self.evidence_required, bool):
            raise PlanningError("checkpoint evidence_required must be boolean")


@dataclass(frozen=True, slots=True)
class TransactionPlan:
    operation_id: str
    mission_id: str
    target: str
    before_state: dict[str, Any]
    intended_effects: tuple[EffectIntent, ...]
    irreversible_boundary: int | None
    rollback_options: tuple[RollbackOption, ...]
    checkpoints: tuple[Checkpoint, ...]
    evidence_requirements: tuple[str, ...]
    abort_conditions: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in ("operation_id", "mission_id", "target"):
            object.__setattr__(self, key, _exact(getattr(self, key), key))
        if not isinstance(self.before_state, Mapping) or not any(
            self.before_state.get(key) for key in ("ref", "hash", "identity")
        ):
            raise PlanningError("before_state must include ref, hash, or identity")
        object.__setattr__(self, "before_state", dict(self.before_state))
        effects = tuple(self.intended_effects)
        if not effects:
            raise PlanningError("transaction requires at least one intended effect")
        if len({effect.effect_ticket for effect in effects}) != len(effects):
            raise PlanningError("effect tickets must be unique")
        object.__setattr__(self, "intended_effects", effects)
        if self.irreversible_boundary is not None and (
            not isinstance(self.irreversible_boundary, int)
            or not 0 <= self.irreversible_boundary < len(effects)
        ):
            raise PlanningError("irreversible boundary must point at an intended effect")
        if self.irreversible_boundary is not None and effects[self.irreversible_boundary].reversible:
            raise PlanningError("irreversible boundary must point at an irreversible effect")
        rollbacks = tuple(self.rollback_options)
        if len({item.effect_ticket for item in rollbacks}) != len(rollbacks):
            raise PlanningError("rollback effect tickets must be unique")
        object.__setattr__(self, "rollback_options", rollbacks)
        checkpoints = tuple(self.checkpoints)
        if len({item.name for item in checkpoints}) != len(checkpoints):
            raise PlanningError("checkpoint names must be unique")
        object.__setattr__(self, "checkpoints", checkpoints)
        for key in ("evidence_requirements", "abort_conditions"):
            values = tuple(_exact(value, key[:-1] if key.endswith("s") else key)
                           for value in getattr(self, key))
            if len(set(values)) != len(values):
                raise PlanningError(f"{key} must not contain duplicates")
            object.__setattr__(self, key, values)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def plan_hash(self) -> str:
        payload = {
            "operation_id": self.operation_id,
            "mission_id": self.mission_id,
            "target": self.target,
            "before_state": self.before_state,
            "intended_effects": [effect.__dict__ if hasattr(effect, "__dict__") else {
                "effect_ticket": effect.effect_ticket, "action": effect.action,
                "target": effect.target, "reversible": effect.reversible,
                "compensation": effect.compensation,
            } for effect in self.intended_effects],
            "irreversible_boundary": self.irreversible_boundary,
            "rollback_options": [
                {"effect_ticket": item.effect_ticket, "method": item.method,
                 "available": item.available} for item in self.rollback_options
            ],
            "checkpoints": [
                {"name": item.name, "expected": item.expected,
                 "evidence_required": item.evidence_required}
                for item in self.checkpoints
            ],
            "evidence_requirements": self.evidence_requirements,
            "abort_conditions": self.abort_conditions,
            "metadata": self.metadata,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    @property
    def irreversible(self) -> bool:
        return self.irreversible_boundary is not None


@dataclass(frozen=True, slots=True)
class CheckpointResult:
    name: str
    passed: bool
    evidence_refs: tuple[str, ...]


class ReversibleTransactionPlanner:
    @staticmethod
    def checkpoint(
        plan: TransactionPlan,
        name: str,
        observed: Mapping[str, Any],
        *,
        evidence_refs: Iterable[str],
    ) -> CheckpointResult:
        name = _exact(name, "checkpoint name")
        checkpoint = next((item for item in plan.checkpoints if item.name == name), None)
        if checkpoint is None:
            raise PlanningError(f"unknown checkpoint {name}")
        refs = tuple(_exact(item, "evidence reference") for item in evidence_refs)
        if checkpoint.evidence_required and not refs:
            raise PlanningError(f"checkpoint {name} requires evidence")
        if any(observed.get(key) != value for key, value in checkpoint.expected.items()):
            raise PlanningError(f"checkpoint {name} oracle failed")
        return CheckpointResult(name=name, passed=True, evidence_refs=refs)

    @staticmethod
    def abort_reasons(
        plan: TransactionPlan,
        signals: Mapping[str, bool],
    ) -> tuple[str, ...]:
        return tuple(condition for condition in plan.abort_conditions if signals.get(condition, False))

    @staticmethod
    def can_cross_irreversible_boundary(
        plan: TransactionPlan,
        *,
        checkpoint_results: Iterable[CheckpointResult],
        stop_requested: bool = False,
    ) -> bool:
        if not plan.irreversible:
            return True
        if stop_requested:
            return False
        passed = {item.name for item in checkpoint_results if item.passed}
        required = {item.name for item in plan.checkpoints}
        return required.issubset(passed) and all(
            option.available for option in plan.rollback_options
            if option.effect_ticket in {
                effect.effect_ticket for effect in plan.intended_effects[:plan.irreversible_boundary]
            }
        )


__all__ = [
    "Checkpoint", "CheckpointResult", "EffectIntent", "PlanningError",
    "ReversibleTransactionPlanner", "RollbackOption", "TransactionPlan",
]
