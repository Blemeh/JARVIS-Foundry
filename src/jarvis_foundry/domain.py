from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


ARTIFACT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


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


class BrokerDecision(StrEnum):
    """The policy broker's full contract (architecture §7): exactly these
    seven codes — an LLM may explain a decision, never override it."""
    ALLOW = "ALLOW"
    VETOED = "VETOED"
    NOT_READY = "NOT_READY"
    MISSING_RECOVERY_PATH = "MISSING_RECOVERY_PATH"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    INVALID_TARGET = "INVALID_TARGET"


@dataclass(frozen=True, slots=True)
class ActionEnvelope:
    action_id: str
    mission_id: str
    resource_id: str
    action_kind: str
    target: Path | None = None
    capability: str = ""
    expected_effects: tuple[str, ...] = ()
    risk_class: str = "C1"
    verification_required: bool = True
    rollback: dict[str, Any] = field(default_factory=dict)
    # V5-S2 architecture-contract §7 fields (defaults keep V4 callers
    # intact; the deterministic broker consumes these).
    step_id: str | None = None
    requested_by_agent_run_id: str | None = None
    target_resources: tuple[str, ...] = ()
    capabilities_required: tuple[str, ...] = ()
    capability_states_required: tuple[str, ...] = ()
    goal_alignment: str | None = None
    active_owner_vetoes_checked: tuple[str, ...] = ()
    expected_effect: str | None = None
    reversibility: str = "reversible"
    before_state_capture: dict[str, Any] = field(default_factory=dict)
    verification_plan: dict[str, Any] = field(default_factory=dict)
    rollback_plan: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    budget_estimate: dict[str, float] = field(default_factory=dict)
    credential_requirements: tuple[str, ...] = ()
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    decision: ActionDecision
    reason: str
    resolved_target: Path | None = None
    matched_veto_ids: tuple[str, ...] = ()
