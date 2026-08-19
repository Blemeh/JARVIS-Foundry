"""Deterministic consumption of authenticated owner mission directives.

Owner controls are recorded on the canonical ledger and projected into the
``directives`` read model.  This module is the small downstream consumer that
turns the two R10 verbs into mission-loop input.  It deliberately does not
create missions or a second execution loop: redirects update the existing
mission ``next_action`` projection and reprioritizations only provide durable
dispatch-ranking metadata.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event

_DIRECTIVE_TYPES = frozenset({"REPRIORITIZE", "REDIRECT"})
_NUMBER = re.compile(
    r"(?:priority|priority[_ -]?bump|bump)\s*(?:=|:|by)?\s*([-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DirectiveEffect:
    directive_id: str
    command_type: str
    target: str | None
    exact_text: str
    mission_id: str
    dispatch_priority: float = 0.0
    blocked_reason: str | None = None


def _owner_directives(database: FoundryDatabase) -> list[dict[str, Any]]:
    """Rebuild the authenticated active directive stream from ledger events.

    Reading the event envelope (rather than trusting a manually inserted
    projection row) keeps unauthenticated or non-owner events out of the
    consumer.  Revocation is applied in ledger order, so replay is stable.
    """
    records: dict[str, dict[str, Any]] = {}
    for event in database.events():
        if event.event_type == "owner_directive.recorded":
            payload = event.payload
            if (event.source_type != "owner" or
                    payload.get("authenticated") is not True or
                    payload.get("command_type") not in _DIRECTIVE_TYPES):
                continue
            directive_id = str(payload.get("directive_id") or "")
            target = payload.get("target")
            exact_text = payload.get("exact_text")
            if not directive_id or not isinstance(exact_text, str) or not exact_text.strip():
                continue
            records[directive_id] = {
                "directive_id": directive_id,
                "command_type": str(payload["command_type"]),
                "target": str(target) if target is not None else None,
                "exact_text": exact_text,
                "event_id": event.event_id,
                "sequence": len(records),
                "active": True,
            }
        elif event.event_type == "owner_directive.revoked":
            directive_id = str(event.payload.get("directive_id") or "")
            if directive_id in records:
                records[directive_id]["active"] = False
    return [record for record in records.values() if record["active"]]


def _target_matches(target: str | None, mission: dict[str, Any]) -> bool:
    return target in {
        "GLOBAL",
        str(mission["mission_id"]),
        str(mission.get("resource_id") or ""),
    }


def _priority_value(text: str, sequence: int) -> float:
    match = _NUMBER.search(text)
    if match:
        return float(match.group(1))
    lowered = text.lower()
    if any(word in lowered for word in ("raise", "increase", "higher", "up")):
        return 1.0
    if any(word in lowered for word in ("lower", "decrease", "down")):
        return -1.0
    # A text-only reprioritization still has deterministic meaning: the
    # newest matching owner directive wins ties, without inventing a mission.
    return float(sequence + 1) / 1_000_000.0


def _blocked_reason(database: FoundryDatabase, mission: dict[str, Any]) -> str | None:
    stop = emergency_stop_reason(database, None)
    if stop:
        return stop
    identities = {"GLOBAL", str(mission["mission_id"]),
                  str(mission.get("resource_id") or "")}
    return matching_veto_reason(database, identities)


def matching_effects(database: FoundryDatabase, mission_id: str) -> list[DirectiveEffect]:
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        raise ValueError(f"unknown mission: {mission_id}")
    blocked = _blocked_reason(database, mission)
    effects: list[DirectiveEffect] = []
    for directive in _owner_directives(database):
        if not _target_matches(directive["target"], mission):
            continue
        effects.append(DirectiveEffect(
            directive_id=directive["directive_id"],
            command_type=directive["command_type"],
            target=directive["target"],
            exact_text=directive["exact_text"],
            mission_id=mission_id,
            dispatch_priority=(
                _priority_value(directive["exact_text"], directive["sequence"])
                if directive["command_type"] == "REPRIORITIZE" else 0.0),
            blocked_reason=blocked,
        ))
    return effects


def consume_directives(database: FoundryDatabase, mission_id: str) -> list[DirectiveEffect]:
    """Apply currently active directives to one existing mission.

    Each interpretation is an append-only, idempotent event.  The projection
    handler for ``mission.directive_interpreted`` updates ``next_action`` for
    redirects; reprioritization remains inspectable in the same event stream.
    STOP/veto precedence is checked immediately before interpretation.
    """
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        raise ValueError(f"unknown mission: {mission_id}")
    effects = matching_effects(database, mission_id)
    if not effects:
        return []
    for effect in effects:
        if effect.blocked_reason:
            continue
        if effect.command_type == "REDIRECT":
            next_action = f"redirect: {effect.exact_text.strip()}"
        else:
            next_action = mission.get("next_action")
        event = Event.create(
            "mission.directive_interpreted",
            {
                "mission_id": mission_id,
                "directive_id": effect.directive_id,
                "command_type": effect.command_type,
                "target": effect.target,
                "exact_text": effect.exact_text,
                "next_action": next_action,
                "dispatch_priority": effect.dispatch_priority,
                "interpretation": (
                    "replace_next_action" if effect.command_type == "REDIRECT"
                    else "dispatch_priority"),
            },
            source_type="supervisor",
            source_id="mission-directive-consumer",
            idempotency_key=(
                f"mission.directive_interpreted:{mission_id}:"
                f"{effect.directive_id}"),
        )
        database.append(event)
        mission = database.row("missions", "mission_id", mission_id) or mission
    return effects


def select_dispatchable_mission(database: FoundryDatabase,
                                mission_ids: Iterable[str]) -> str | None:
    """Select an existing dispatchable mission using active reprioritization.

    The input order is the canonical tie-breaker.  No mission is created and
    no action is invented when the caller supplies no dispatchable missions.
    """
    candidates: list[tuple[float, int, str]] = []
    for index, mission_id in enumerate(mission_ids):
        mission = database.row("missions", "mission_id", mission_id)
        if mission is None or mission["state"] not in {"ACTION_SELECTION", "REPAIRING", "READY"}:
            continue
        effects = consume_directives(database, mission_id)
        priorities = [e.dispatch_priority for e in effects
                      if e.command_type == "REPRIORITIZE" and not e.blocked_reason]
        candidates.append((max(priorities, default=0.0), index, mission_id))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (-item[0], item[1]))[0][2]
