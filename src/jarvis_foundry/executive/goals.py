"""Structured goals projected from authenticated owner GOAL directives."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..database import FoundryDatabase
from ..knowledge import canonicalize_statement
from ..owner_directives import OwnerDirectiveRecord, active_directives_for

_PRIORITY_RE = re.compile(
    r"(?:priority|priority[_ -]?level)\s*(?:=|:)?\s*([-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_HORIZON_RE = re.compile(
    r"(?:horizon|deadline|by)\s*(?:=|:)?\s*(\S+)",
    re.IGNORECASE,
)
_SUCCESS_RE = re.compile(
    r"(?<![\w-])(?:success|criteria|done when)\b\s*(?:=|:)?\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class StructuredGoal:
    goal_id: str
    objective: str
    priority: float
    success_criteria: tuple[str, ...] = ()
    status: str = "ACTIVE"
    time_horizon: str | None = None
    constraints: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    related_resources: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def canonical_objective(self) -> str:
        return canonicalize_statement(self.objective)


def _parse_priority(text: str) -> float:
    match = _PRIORITY_RE.search(text)
    if match:
        return float(match.group(1))
    lowered = text.lower()
    if any(word in lowered for word in ("critical", "urgent", "p0", "highest")):
        return 3.0
    if any(word in lowered for word in ("high", "p1", "important")):
        return 2.0
    if any(word in lowered for word in ("low", "p3", "minor")):
        return 0.5
    return 1.0


def _parse_success_criteria(text: str) -> tuple[str, ...]:
    match = _SUCCESS_RE.search(text)
    if match:
        raw = match.group(1).strip()
        parts = tuple(p.strip() for p in raw.split(";") if p.strip())
        if parts:
            return parts
    return ()


def _parse_horizon(text: str) -> str | None:
    match = _HORIZON_RE.search(text)
    return match.group(1).strip() if match else None


def _owner_goal_directives(database: FoundryDatabase) -> list[OwnerDirectiveRecord]:
    """Return authenticated active GOAL directives from the shared projection."""
    return active_directives_for(database, command_types=frozenset({"GOAL"}))


def _active_constraints(database: FoundryDatabase) -> tuple[str, ...]:
    records = active_directives_for(
        database,
        command_types=frozenset({"CONSTRAINT", "ADD_CONSTRAINT"}),
    )
    return tuple(str(record.exact_text or "") for record in records)


def active_goals(database: FoundryDatabase) -> list[StructuredGoal]:
    """Project structured goals from authenticated GOAL directives."""
    constraints = _active_constraints(database)
    goals: list[StructuredGoal] = []
    for record in _owner_goal_directives(database):
        text = str(record.exact_text or "")
        target = record.target
        related = (str(target),) if target and target != "GLOBAL" else ()
        goals.append(StructuredGoal(
            goal_id=record.directive_id,
            objective=text,
            priority=_parse_priority(text),
            success_criteria=_parse_success_criteria(text),
            status="ACTIVE",
            time_horizon=_parse_horizon(text),
            constraints=constraints,
            related_resources=related,
            provenance={
                "directive_id": record.directive_id,
                "event_id": record.event_id,
                "correlation_id": record.correlation_id,
                "causation_id": record.causation_id,
                "source": record.source_id,
                "nonce": record.nonce,
                "state": record.state,
            },
        ))
    goals.sort(key=lambda g: (-g.priority, g.goal_id))
    return goals


def goal_alignment_score(
    goal: StructuredGoal,
    objective: str,
    *,
    candidate_type: str = "",
    target_project: str = "",
) -> float:
    """Alignment between a standing goal and a candidate objective.

    Discovery mandates ("keep improving my projects") align with real
    project-improvement work even when the texts do not share tokens.
    Executable recipes keep lexical overlap.
    """
    from .standing_intent import standing_goal_alignment

    goal_tokens = set(goal.canonical_objective.split())
    obj_tokens = set(canonicalize_statement(objective).split())
    lexical = 0.0
    if goal_tokens and obj_tokens:
        overlap = len(goal_tokens & obj_tokens)
        lexical = min(1.0, overlap / max(3.0, len(goal_tokens) * 0.5))
    mandate = standing_goal_alignment(
        goal.objective,
        objective,
        candidate_type=candidate_type,
        target_project=target_project,
    )
    if mandate is None:
        return lexical
    return max(lexical, mandate)


def _priority_value(text: str) -> float:
    match = _PRIORITY_RE.search(text)
    if match:
        return float(match.group(1))
    lowered = text.lower()
    if any(word in lowered for word in ("raise", "increase", "higher", "up")):
        return 1.0
    if any(word in lowered for word in ("lower", "decrease", "down")):
        return -1.0
    return 0.0


def active_priorities(
    database: FoundryDatabase,
    *,
    targets: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Return durable owner reprioritizations applicable to target identities."""
    records = active_directives_for(
        database,
        command_types=frozenset({"REPRIORITIZE"}),
        targets=targets,
    )
    return [
        {
            "directive_id": record.directive_id,
            "target": record.target,
            "exact_text": record.exact_text,
            "value": _priority_value(str(record.exact_text or "")),
            "event_id": record.event_id,
            "nonce": record.nonce,
            "correlation_id": record.correlation_id,
        }
        for record in records
    ]


def owner_priority_for(
    database: FoundryDatabase,
    *,
    targets: set[str] | frozenset[str],
) -> tuple[float, list[dict[str, Any]]]:
    """Select the newest applicable owner priority without inventing one."""
    priorities = active_priorities(database, targets=targets)
    if not priorities:
        return 0.0, []
    specific = [p for p in priorities if p.get("target") in targets]
    selected = specific[0] if specific else priorities[0]
    # Normalize the free-form numeric convention to the scoring range while
    # retaining the raw value in the decision audit.
    raw = float(selected.get("value") or 0.0)
    return max(-1.0, min(1.0, raw / 10.0 if abs(raw) > 1.0 else raw)), priorities
