"""Supervisor-owned lifecycle contract for controlled self-evolution.

The lifecycle is a frozen state machine.  Every transition is a durable kernel
ledger event.  Only the Supervisor identity (matching
``jarvis_foundry.supervisor.evolution_control.SUPERVISOR_CONTROLLER``) may
perform effectful control operations; a candidate identity is always refused.
"""

from __future__ import annotations

from typing import Any

SUPERVISOR_CONTROLLER = "supervisor"
CANDIDATE_ROLE = "candidate"

RISK_ORDER = ("R0", "R1", "R2", "R3")

LIFECYCLE_EVENTS: tuple[str, ...] = (
    "evolution_live.observed",
    "evolution_live.hypothesized",
    "evolution_live.candidate_isolated",
    "evolution_live.evaluated",
    "evolution_live.independently_verified",
    "evolution_live.shadowed",
    "evolution_live.canary_ready",
    "evolution_live.promoted",
    "evolution_live.rejected",
    "evolution_live.outcome_measured",
    "evolution_live.learned",
    "evolution_live.rolled_back",
    "evolution_live.restored",
)

SESSION_EVENTS: tuple[str, ...] = (
    "evolution_live.waiting",
    "evolution_live.refused",
)

#: event type -> event types that may follow it in one cycle.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "evolution_live.observed": frozenset({"evolution_live.hypothesized"}),
    "evolution_live.hypothesized": frozenset({"evolution_live.candidate_isolated"}),
    "evolution_live.candidate_isolated": frozenset({"evolution_live.evaluated"}),
    "evolution_live.evaluated": frozenset({"evolution_live.independently_verified"}),
    "evolution_live.independently_verified": frozenset(
        {"evolution_live.shadowed", "evolution_live.rejected"}
    ),
    "evolution_live.shadowed": frozenset(
        {"evolution_live.canary_ready", "evolution_live.rejected"}
    ),
    "evolution_live.canary_ready": frozenset(
        {"evolution_live.promoted", "evolution_live.rejected"}
    ),
    "evolution_live.promoted": frozenset(
        {"evolution_live.outcome_measured", "evolution_live.rolled_back"}
    ),
    "evolution_live.rejected": frozenset({"evolution_live.outcome_measured"}),
    "evolution_live.outcome_measured": frozenset({"evolution_live.learned"}),
    "evolution_live.learned": frozenset({"evolution_live.rolled_back"}),
    "evolution_live.rolled_back": frozenset({"evolution_live.restored"}),
    "evolution_live.restored": frozenset(),
}


class LifecycleError(RuntimeError):
    """The lifecycle refused a transition or identity."""


class EvolutionControlError(LifecycleError):
    """Only the Supervisor may drive the evolution control plane."""


def risk_index(risk: str) -> int:
    """Index of a risk class in R0..R3, refusing unknown classes."""
    if risk not in RISK_ORDER:
        raise LifecycleError(f"unknown risk class {risk!r}; expected one of {RISK_ORDER}")
    return RISK_ORDER.index(risk)


def require_supervisor(controller: str, operation: str) -> None:
    """Fail closed unless the controller is exactly the Supervisor identity."""
    if not isinstance(controller, str) or controller != SUPERVISOR_CONTROLLER:
        raise EvolutionControlError(
            f"only the Supervisor controller can {operation}; identity {controller!r} is refused"
        )


def is_candidate_identity(identity: str) -> bool:
    return CANDIDATE_ROLE in str(identity).lower()


class LifecycleMachine:
    """Frozen milestone transition graph for one evolution cycle."""

    def __init__(self, last_event: str | None = None) -> None:
        self._last_event = last_event

    @property
    def last_event(self) -> str | None:
        return self._last_event

    def assert_transition(self, event_type: str) -> None:
        if self._last_event is None:
            if event_type != "evolution_live.observed":
                raise LifecycleError(f"cycle must start with observed, not {event_type}")
            return
        allowed = _TRANSITIONS.get(self._last_event)
        if allowed is None or event_type not in allowed:
            raise LifecycleError(f"transition {self._last_event} -> {event_type} is not allowed")

    def next_milestone(self, event_type: str) -> LifecycleMachine:
        self.assert_transition(event_type)
        return LifecycleMachine(event_type)


def validate_criteria(criteria: Any) -> list[str]:
    if not isinstance(criteria, list) or not criteria:
        raise LifecycleError("acceptance criteria must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in criteria):
        raise LifecycleError("every acceptance criterion must be a non-empty string")
    return list(criteria)


def validate_surfaces(surfaces: Any) -> list[str]:
    if not isinstance(surfaces, list) or not surfaces:
        raise LifecycleError("surfaces must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in surfaces):
        raise LifecycleError("every surface must be a non-empty string")
    return list(surfaces)


def validate_metrics(metrics: Any, label: str) -> dict[str, Any]:
    if not isinstance(metrics, dict) or not metrics:
        raise LifecycleError(f"{label} must be a non-empty mapping")
    passed = metrics.get("pytest_passed")
    failed = metrics.get("pytest_failed")
    if not isinstance(passed, int) or passed < 0:
        raise LifecycleError(f"{label}.pytest_passed must be a non-negative integer")
    if not isinstance(failed, int) or failed < 0:
        raise LifecycleError(f"{label}.pytest_failed must be a non-negative integer")
    return dict(metrics)


def require_text(payload: dict[str, Any], key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LifecycleError(f"{key} must be a non-empty string")
