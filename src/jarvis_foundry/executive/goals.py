"""Structured goals projected from authenticated owner GOAL directives."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..database import FoundryDatabase
from ..knowledge import canonicalize_statement

_PRIORITY_RE = re.compile(
    r"(?:priority|priority[_ -]?level)\s*(?:=|:)?\s*([-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_HORIZON_RE = re.compile(
    r"(?:horizon|deadline|by)\s*(?:=|:)?\s*(\S+)",
    re.IGNORECASE,
)
_SUCCESS_RE = re.compile(
    r"(?:success|criteria|done when)\s*(?:=|:)?\s*(.+)$",
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


def _owner_goal_directives(database: FoundryDatabase) -> list[dict[str, Any]]:
    """Rebuild authenticated GOAL directives from ledger events."""
    records: dict[str, dict[str, Any]] = {}
    nonce_uses: dict[str, Any] = {}
    seq = 0
    for event in database.events():
        if event.event_type == "owner_control.nonce_used":
            payload = event.payload
            nonce = payload.get("nonce")
            if (event.source_type == "owner" and isinstance(nonce, str) and nonce):
                nonce_uses[nonce] = event
        elif event.event_type == "owner_directive.recorded":
            payload = event.payload
            if (event.source_type != "owner"
                    or payload.get("authenticated") is not True
                    or payload.get("command_type") != "GOAL"):
                continue
            directive_id = str(payload.get("directive_id") or "")
            exact_text = payload.get("exact_text")
            if not directive_id or not isinstance(exact_text, str) or not exact_text.strip():
                continue
            records[directive_id] = {
                "directive_id": directive_id,
                "target": payload.get("target"),
                "exact_text": exact_text.strip(),
                "event_id": event.event_id,
                "sequence": seq,
                "active": True,
            }
            seq += 1
        elif event.event_type == "owner_directive.revoked":
            directive_id = str(event.payload.get("directive_id") or "")
            if directive_id in records:
                records[directive_id]["active"] = False
    return [r for r in records.values() if r["active"]]


def _active_constraints(database: FoundryDatabase) -> tuple[str, ...]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT exact_text FROM directives WHERE active = 1 "
            "AND command_type IN ('CONSTRAINT', 'ADD_CONSTRAINT')"
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def active_goals(database: FoundryDatabase) -> list[StructuredGoal]:
    """Project structured goals from authenticated GOAL directives."""
    constraints = _active_constraints(database)
    goals: list[StructuredGoal] = []
    for record in _owner_goal_directives(database):
        text = record["exact_text"]
        target = record.get("target")
        related = (str(target),) if target and target != "GLOBAL" else ()
        goals.append(StructuredGoal(
            goal_id=record["directive_id"],
            objective=text,
            priority=_parse_priority(text),
            success_criteria=_parse_success_criteria(text),
            status="ACTIVE",
            time_horizon=_parse_horizon(text),
            constraints=constraints,
            related_resources=related,
            provenance={
                "directive_id": record["directive_id"],
                "event_id": record["event_id"],
                "sequence": record["sequence"],
            },
        ))
    goals.sort(key=lambda g: (-g.priority, g.goal_id))
    return goals


def goal_alignment_score(goal: StructuredGoal, objective: str) -> float:
    """Deterministic lexical alignment between a goal and candidate objective."""
    goal_tokens = set(goal.canonical_objective.split())
    obj_tokens = set(canonicalize_statement(objective).split())
    if not goal_tokens or not obj_tokens:
        return 0.0
    overlap = len(goal_tokens & obj_tokens)
    return min(1.0, overlap / max(3.0, len(goal_tokens) * 0.5))
