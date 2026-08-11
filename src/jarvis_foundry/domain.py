from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class MissionState(StrEnum):
    PLANNED = "PLANNED"
    ORIENTING = "ORIENTING"
    ACTION_SELECTION = "ACTION_SELECTION"
    READY = "READY"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    ACCEPTING = "ACCEPTING"
    REPAIRING = "REPAIRING"
    REJECTING = "REJECTING"
    BLOCKED = "BLOCKED"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"


MISSION_TRANSITIONS: dict[MissionState, frozenset[MissionState]] = {
    MissionState.PLANNED: frozenset({MissionState.ORIENTING, MissionState.CANCELLED}),
    MissionState.ORIENTING: frozenset({MissionState.ACTION_SELECTION, MissionState.BLOCKED, MissionState.CANCELLED}),
    MissionState.ACTION_SELECTION: frozenset({MissionState.READY, MissionState.BLOCKED, MissionState.COMPLETE, MissionState.CANCELLED}),
    MissionState.READY: frozenset({MissionState.EXECUTING, MissionState.BLOCKED, MissionState.CANCELLED}),
    MissionState.EXECUTING: frozenset({MissionState.VERIFYING, MissionState.REPAIRING, MissionState.BLOCKED, MissionState.CANCELLED}),
    MissionState.VERIFYING: frozenset({MissionState.ACCEPTING, MissionState.REPAIRING, MissionState.REJECTING, MissionState.BLOCKED}),
    MissionState.ACCEPTING: frozenset({MissionState.ACTION_SELECTION, MissionState.COMPLETE}),
    MissionState.REPAIRING: frozenset({MissionState.READY, MissionState.BLOCKED, MissionState.REJECTING, MissionState.CANCELLED}),
    MissionState.REJECTING: frozenset({MissionState.ACTION_SELECTION, MissionState.CANCELLED}),
    MissionState.BLOCKED: frozenset({MissionState.ACTION_SELECTION, MissionState.CANCELLED}),
    MissionState.COMPLETE: frozenset(),
    MissionState.CANCELLED: frozenset(),
}


def require_transition(current: str, target: str) -> None:
    source_state = MissionState(current)
    target_state = MissionState(target)
    if target_state not in MISSION_TRANSITIONS[source_state]:
        raise ValueError(f"invalid mission transition: {source_state} -> {target_state}")


class CapabilityState(StrEnum):
    DISCOVERED = "DISCOVERED"
    PROBING = "PROBING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    VETOED = "VETOED"
    STOPPED = "STOPPED"


class ActionDecision(StrEnum):
    ALLOWED = "ALLOWED"
    VETOED = "VETOED"
    NOT_READY = "NOT_READY"
    STOPPED = "STOPPED"
    INVALID_TARGET = "INVALID_TARGET"


@dataclass(frozen=True, slots=True)
class ActionEnvelope:
    action_id: str
    mission_id: str
    resource_id: str
    action_kind: str
    target: Path
    capability: str
    expected_effects: tuple[str, ...]
    risk_class: str
    verification_required: bool = True
    rollback: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    decision: ActionDecision
    reason: str
    resolved_target: Path | None = None
    matched_veto_ids: tuple[str, ...] = ()

