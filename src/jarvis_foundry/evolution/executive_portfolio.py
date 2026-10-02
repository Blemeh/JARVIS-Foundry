"""V7 executive portfolio orchestration over the existing JARVIS authorities.

This module owns portfolio allocation only. Candidate scoring remains in
``executive.scoring``. Mission creation remains in ``executive.missions``.
Execution, verification, STOP, veto, and resource registration remain in
their existing modules. Portfolio state is reconstructed from additive ledger
events so a fresh process does not need session memory.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping, Sequence

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event, EventConflict, canonical_json
from ..executive.calibration import rebuild_calibration
from ..executive.eligibility import (
    ACTIVE_MISSION_STATES,
    current_activation_epoch,
    eligible_candidates,
)
from ..executive.goals import StructuredGoal, active_goals, goal_alignment_score
from ..executive.scoring import CandidateScore, rank_candidates
from ..supervisor.authority import SUPERVISOR_CONTROLLER
from ..supervisor.evolution_control import EvolutionControlError
from ..v3_teams import fresh_verifier_identity, validate_team

PortfolioError = EvolutionControlError

WorkClass = Literal[
    "ACTIVE_MISSION",
    "REPAIR",
    "CANDIDATE",
    "NEW_MISSION",
    "MAINTENANCE",
    "RESEARCH",
    "UNKNOWN",
    "INCIDENT",
    "IMPROVEMENT",
    "NEW_PROJECT",
    "OWNER_GOAL",
]

_WORK_CLASSES = frozenset({
    "ACTIVE_MISSION", "REPAIR", "CANDIDATE", "NEW_MISSION",
    "MAINTENANCE", "RESEARCH", "UNKNOWN", "INCIDENT", "IMPROVEMENT",
    "NEW_PROJECT", "OWNER_GOAL",
})
_TERMINAL_MISSION_STATES = frozenset({"COMPLETE", "FAILED", "BLOCKED", "CANCELLED"})
_WAKE_EVENT_TYPES = frozenset({
    "owner_directive.recorded",
    "portfolio.capacity_configured",
    "portfolio.policy_configured",
    "portfolio.work_registered",
    "portfolio.work_updated",
    "portfolio.work_status_changed",
    "portfolio.revisit_scheduled",
    "discovery.candidate_generated",
    "mission.completed",
    "mission.state_changed",
    "capability.registered",
    "capability.state_changed",
    "portfolio.lease_released",
    "portfolio.capacity_lease_released",
    "portfolio.selection_released",
    "portfolio.outcome_recorded",
})

_PORTFOLIO_LOCKS: dict[str, threading.Lock] = {}
_PORTFOLIO_LOCKS_GUARD = threading.Lock()


def _db_lock(database: FoundryDatabase) -> threading.Lock:
    key = str(database.path)
    with _PORTFOLIO_LOCKS_GUARD:
        return _PORTFOLIO_LOCKS.setdefault(key, threading.Lock())


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PortfolioError(message)


def _controller_gate(database: FoundryDatabase, controller: str) -> None:
    _require(controller == SUPERVISOR_CONTROLLER,
             "only the Supervisor can manage the executive portfolio")
    stop = emergency_stop_reason(database, database.path.parent / "EMERGENCY_STOP")
    _require(not stop, f"STOP active: {stop}")


def _controller_identity_gate(controller: str) -> None:
    _require(controller == SUPERVISOR_CONTROLLER,
             "only the Supervisor can select portfolio work")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return round(min(high, max(low, float(value))), 6)


def _finite(value: Any, *, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _number(value: Any, *, default: float = 0.0) -> float:
    parsed = _finite(value, default=default)
    return float(default if parsed is None else parsed)


def _as_iso(value: datetime | str | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _as_datetime(value: datetime | str | float | int | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _due(value: str | None, now: datetime) -> bool:
    parsed = _as_datetime(value)
    return parsed is not None and parsed <= now


def _hash_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(payload)).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class PortfolioPolicy:
    """Durable knobs for allocation and fairness.

    The policy is recorded as an event when changed. Defaults are conservative
    lab values and can be replaced by the Supervisor without code changes.
    """

    max_concurrent_missions: int = 1
    max_workers: int = 4
    max_verifiers: int = 1
    max_provider_calls: int | None = None
    budget_usd: float | None = None
    reserved_capacity: int = 0
    lease_ttl_seconds: float = 300.0
    revisit_seconds: float = 300.0
    max_repair_attempts: int = 3
    fairness_after_cycles: int = 2
    aging_step: float = 0.05
    max_aging_bonus: float = 0.20
    calibration_alpha: float = 0.20
    calibration_cap: float = 0.20
    unknown_penalty: float = 0.03
    candidate_goal_weight: float = 0.15
    candidate_capacity_weight: float = 0.05
    candidate_calibration_weight: float = 0.10
    work_ev_weight: float = 0.30
    work_urgency_weight: float = 0.20
    work_information_weight: float = 0.15
    work_confidence_weight: float = 0.10
    work_reversibility_weight: float = 0.10
    work_effort_weight: float = 0.05
    work_cost_weight: float = 0.05
    work_risk_weight: float = 0.05
    work_opportunity_cost_weight: float = 0.10
    project_share_caps: dict[str, int] = field(default_factory=dict)
    resource_capacity: dict[str, int] = field(default_factory=dict)
    provider_capacity: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "max_concurrent_missions", "max_workers", "max_verifiers",
            "reserved_capacity", "max_repair_attempts", "fairness_after_cycles",
        ):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in (
            "lease_ttl_seconds", "revisit_seconds", "aging_step",
            "max_aging_bonus", "calibration_alpha", "calibration_cap",
            "unknown_penalty", "candidate_goal_weight",
            "candidate_capacity_weight", "candidate_calibration_weight",
            "work_ev_weight", "work_urgency_weight", "work_information_weight",
            "work_confidence_weight", "work_reversibility_weight",
            "work_effort_weight", "work_cost_weight", "work_risk_weight",
            "work_opportunity_cost_weight",
        ):
            if _number(getattr(self, name), default=-1.0) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.budget_usd is not None and self.budget_usd < 0:
            raise ValueError("budget_usd must be non-negative")

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PortfolioPolicy":
        fields = {field_name for field_name in cls.__dataclass_fields__}
        values = {name: payload[name] for name in fields if name in payload}
        for name in ("project_share_caps", "resource_capacity", "provider_capacity"):
            values[name] = {
                str(key): int(value)
                for key, value in dict(values.get(name) or {}).items()
            }
        for name in (
            "max_concurrent_missions", "max_workers", "max_verifiers",
            "max_provider_calls", "reserved_capacity", "max_repair_attempts",
            "fairness_after_cycles",
        ):
            if name in values and values[name] is not None:
                values[name] = int(values[name])
        for name in (
            "budget_usd", "lease_ttl_seconds", "revisit_seconds", "aging_step",
            "max_aging_bonus", "calibration_alpha", "calibration_cap",
            "unknown_penalty", "candidate_goal_weight",
            "candidate_capacity_weight", "candidate_calibration_weight",
            "work_ev_weight", "work_urgency_weight", "work_information_weight",
            "work_confidence_weight", "work_reversibility_weight",
            "work_effort_weight", "work_cost_weight", "work_risk_weight",
            "work_opportunity_cost_weight",
        ):
            if name in values and values[name] is not None:
                values[name] = float(values[name])
        return cls(**values)


@dataclass(frozen=True, slots=True)
class WorkItem:
    item_id: str
    kind: str
    objective: str
    status: str = "OPEN"
    goal_ids: tuple[str, ...] = ()
    project_id: str | None = None
    resource_id: str | None = None
    mission_id: str | None = None
    candidate_id: str | None = None
    expected_value: float | None = None
    urgency: float | None = None
    information_gain: float | None = None
    confidence: float | None = None
    effort: float | None = None
    cost_usd: float = 0.0
    risk: float | None = None
    reversibility: float | None = None
    opportunity_cost: float | None = None
    dependencies: tuple[str, ...] = ()
    due_at: str | None = None
    expires_at: str | None = None
    blocked_reason: str | None = None
    owner_only: bool = False
    requires_workers: int = 1
    requires_verifier: bool = True
    evidence_refs: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["goal_ids"] = list(self.goal_ids)
        payload["dependencies"] = list(self.dependencies)
        payload["evidence_refs"] = list(self.evidence_refs)
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, sequence: int = 0) -> "WorkItem":
        values = dict(payload)
        for name in ("goal_ids", "dependencies", "evidence_refs"):
            values[name] = tuple(str(item) for item in values.get(name) or ())
        values["item_id"] = str(values.get("item_id") or "")
        values["kind"] = str(values.get("kind") or "")
        values["objective"] = str(values.get("objective") or "")
        values["status"] = str(values.get("status") or "OPEN")
        values["sequence"] = sequence
        for name in ("expected_value", "urgency", "information_gain", "confidence",
                     "effort", "risk", "reversibility", "opportunity_cost"):
            values[name] = _finite(values.get(name))
        values["cost_usd"] = _number(values.get("cost_usd"))
        values["requires_workers"] = max(0, int(values.get("requires_workers", 1)))
        values["requires_verifier"] = bool(values.get("requires_verifier", True))
        values["owner_only"] = bool(values.get("owner_only", False))
        values["provenance"] = dict(values.get("provenance") or {})
        values["metadata"] = dict(values.get("metadata") or {})
        allowed = {name for name in cls.__dataclass_fields__}
        return cls(**{name: values[name] for name in allowed if name in values})


@dataclass(frozen=True, slots=True)
class ScoredWork:
    item: WorkItem
    score: float
    base_score: float
    goal_alignment: float
    uncertainty: float
    unknown_fields: tuple[str, ...]
    fairness_bonus: float
    calibration_adjustment: float
    capacity: dict[str, Any]
    factors: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item.item_id,
            "kind": self.item.kind,
            "project_id": self.item.project_id,
            "resource_id": self.item.resource_id,
            "mission_id": self.item.mission_id,
            "candidate_id": self.item.candidate_id,
            "objective": self.item.objective,
            "cost_usd": self.item.cost_usd,
            "requires_workers": self.item.requires_workers,
            "requires_verifier": self.item.requires_verifier,
            "provider": self.item.metadata.get("provider"),
            "provider_call_count": max(
                0, int(_number(self.item.metadata.get("provider_call_count")))
            ) if self.item.metadata.get("provider_call_count") is not None
            else (1 if self.item.metadata.get("provider") else 0),
            "score": self.score,
            "base_score": self.base_score,
            "goal_alignment": self.goal_alignment,
            "uncertainty": self.uncertainty,
            "unknown_fields": list(self.unknown_fields),
            "fairness_bonus": self.fairness_bonus,
            "calibration_adjustment": self.calibration_adjustment,
            "capacity": dict(self.capacity),
            "factors": dict(self.factors),
            "metadata": dict(self.item.metadata),
            "evidence_refs": list(self.item.evidence_refs),
            "provenance": dict(self.item.provenance),
        }


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    decision_id: str
    loop_id: str
    kind: Literal["SELECT", "WAIT"]
    selected_class: str
    entity_id: str | None
    reason: str
    why_now: str
    alternatives: tuple[dict[str, Any], ...] = ()
    goal_alignment: float | None = None
    capacity_check: dict[str, Any] = field(default_factory=dict)
    veto_check: dict[str, Any] = field(default_factory=dict)
    expected_value: float | None = None
    uncertainty: float = 1.0
    selected_score: float | None = None
    wake_on: tuple[str, ...] = ()
    revisit_at: str | None = None
    selected_item: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "loop_id": self.loop_id,
            "decision": self.kind,
            "selected_class": self.selected_class,
            "entity_id": self.entity_id,
            "reason": self.reason,
            "why_now": self.why_now,
            "alternatives": list(self.alternatives),
            "goal_alignment": self.goal_alignment,
            "capacity_check": dict(self.capacity_check),
            "veto_check": dict(self.veto_check),
            "expected_value": self.expected_value,
            "uncertainty": self.uncertainty,
            "selected_score": self.selected_score,
            "wake_on": list(self.wake_on),
            "revisit_at": self.revisit_at,
            "selected_item": dict(self.selected_item),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PortfolioDecision":
        return cls(
            decision_id=str(payload.get("decision_id") or ""),
            loop_id=str(payload.get("loop_id") or ""),
            kind=str(payload.get("decision") or "WAIT"),  # type: ignore[arg-type]
            selected_class=str(payload.get("selected_class") or "WAIT"),
            entity_id=payload.get("entity_id"),
            reason=str(payload.get("reason") or ""),
            why_now=str(payload.get("why_now") or ""),
            alternatives=tuple(payload.get("alternatives") or ()),
            goal_alignment=_finite(payload.get("goal_alignment")),
            capacity_check=dict(payload.get("capacity_check") or {}),
            veto_check=dict(payload.get("veto_check") or {}),
            expected_value=_finite(payload.get("expected_value")),
            uncertainty=_clamp(_number(payload.get("uncertainty"), default=1.0)),
            selected_score=_finite(payload.get("selected_score")),
            wake_on=tuple(str(item) for item in payload.get("wake_on") or ()),
            revisit_at=payload.get("revisit_at"),
            selected_item=dict(payload.get("selected_item") or {}),
        )


@dataclass
class PortfolioState:
    policy: PortfolioPolicy = field(default_factory=PortfolioPolicy)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    work_items: dict[str, WorkItem] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    claims: dict[str, dict[str, Any]] = field(default_factory=dict)
    leases: dict[str, dict[str, Any]] = field(default_factory=dict)
    teams: dict[str, dict[str, Any]] = field(default_factory=dict)
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    fairness_cycles: dict[str, int] = field(default_factory=dict)
    wakes: dict[str, dict[str, Any]] = field(default_factory=dict)


def _project_from_event(state: PortfolioState, event: Event) -> None:
    payload = event.payload
    if event.event_type == "portfolio.project_registered":
        project_id = str(payload.get("project_id") or "")
        if project_id:
            state.projects[project_id] = dict(payload)
            state.projects[project_id]["status"] = str(payload.get("status") or "PLANNED")
    elif event.event_type == "portfolio.status_changed":
        project_id = str(payload.get("project_id") or "")
        if project_id in state.projects:
            state.projects[project_id]["status"] = str(payload.get("status") or "PLANNED")
            state.projects[project_id]["status_reason"] = payload.get("reason")
    elif event.event_type == "portfolio.reprioritized":
        project_id = str(payload.get("project_id") or "")
        if project_id in state.projects:
            current = _number(state.projects[project_id].get("priority_bump"))
            state.projects[project_id]["priority_bump"] = current + _number(payload.get("bump"))


def rebuild_portfolio_state(database: FoundryDatabase) -> PortfolioState:
    """Rebuild the V7 portfolio projection from the append-only ledger."""
    state = PortfolioState()
    # This projection is entirely derived from portfolio-domain events. Keep
    # the original ledger sequence while avoiding decoding unrelated worker,
    # provider, and perception history on every reconstruction.
    for sequence, event in database.events_with_sequences(
        event_type_prefixes=("portfolio.",),
    ):
        payload = event.payload
        _project_from_event(state, event)
        if event.event_type == "portfolio.policy_configured":
            try:
                state.policy = PortfolioPolicy.from_payload(payload.get("policy") or payload)
            except (TypeError, ValueError):
                continue
        elif event.event_type == "portfolio.work_registered":
            try:
                item = WorkItem.from_payload(payload, sequence=sequence)
            except (TypeError, ValueError):
                continue
            if item.item_id and item.kind in _WORK_CLASSES:
                state.work_items[item.item_id] = item
        elif event.event_type == "portfolio.work_updated":
            item_id = str(payload.get("item_id") or "")
            prior = state.work_items.get(item_id)
            if prior is not None:
                merged = prior.to_payload()
                merged.update(dict(payload.get("changes") or {}))
                state.work_items[item_id] = WorkItem.from_payload(merged, sequence=sequence)
        elif event.event_type == "portfolio.work_status_changed":
            item_id = str(payload.get("item_id") or "")
            prior = state.work_items.get(item_id)
            if prior is not None:
                merged = prior.to_payload()
                merged["status"] = payload.get("status") or prior.status
                merged["blocked_reason"] = payload.get("reason")
                state.work_items[item_id] = WorkItem.from_payload(merged, sequence=sequence)
        elif event.event_type == "portfolio.unknown":
            question_id = str(payload.get("question_id") or "")
            if question_id and question_id not in state.work_items:
                state.work_items[f"unknown:{question_id}"] = WorkItem(
                    item_id=f"unknown:{question_id}", kind="UNKNOWN",
                    objective=str(payload.get("question") or question_id),
                    confidence=None, requires_workers=1, requires_verifier=False,
                    provenance={"event_id": event.event_id, "source": "portfolio.unknown"},
                    sequence=sequence,
                )
        elif event.event_type == "portfolio.selection_recorded":
            decision = dict(payload)
            decision["sequence"] = sequence
            state.decisions[str(payload.get("decision_id") or "")] = decision
            if payload.get("decision") == "SELECT":
                selected = str(payload.get("entity_id") or "")
                if selected:
                    state.fairness_cycles[selected] = 0
            for alternative in payload.get("alternatives") or ():
                entity_id = str(alternative.get("entity_id") or "")
                if entity_id and entity_id != str(payload.get("entity_id") or ""):
                    state.fairness_cycles[entity_id] = state.fairness_cycles.get(entity_id, 0) + 1
        elif event.event_type == "portfolio.selection_claimed":
            entity_id = str(payload.get("entity_id") or "")
            if entity_id:
                state.claims[entity_id] = dict(payload)
        elif event.event_type in ("portfolio.selection_released", "portfolio.lease_released"):
            entity_id = str(payload.get("entity_id") or "")
            if entity_id:
                state.claims.pop(entity_id, None)
                state.leases.pop(entity_id, None)
        elif event.event_type in ("portfolio.capacity_lease_acquired", "portfolio.lease_acquired"):
            entity_id = str(payload.get("entity_id") or payload.get("lease_key") or "")
            if entity_id:
                state.leases[entity_id] = dict(payload)
        elif event.event_type in ("portfolio.capacity_lease_released",):
            entity_id = str(payload.get("entity_id") or payload.get("lease_key") or "")
            state.leases.pop(entity_id, None)
        elif event.event_type == "portfolio.team_created":
            team_id = str(payload.get("team_id") or "")
            if team_id:
                state.teams[team_id] = dict(payload)
        elif event.event_type == "portfolio.team_worker_replaced":
            team = state.teams.get(str(payload.get("team_id") or ""))
            if team is not None:
                workers = dict(team.get("workers") or {})
                role = str(payload.get("role") or "")
                if role:
                    workers[role] = str(payload.get("new_worker_id") or "")
                team["workers"] = workers
                retired = list(team.get("retired_workers") or [])
                old = str(payload.get("old_worker_id") or "")
                if old and old not in retired:
                    retired.append(old)
                team["retired_workers"] = retired
        elif event.event_type == "portfolio.team_worker_retired":
            team = state.teams.get(str(payload.get("team_id") or ""))
            if team is not None:
                retired = list(team.get("retired_workers") or [])
                worker_id = str(payload.get("worker_id") or "")
                if worker_id and worker_id not in retired:
                    retired.append(worker_id)
                team["retired_workers"] = retired
        elif event.event_type == "portfolio.outcome_recorded":
            outcome = dict(payload)
            outcome["sequence"] = sequence
            state.outcomes.append(outcome)
        elif event.event_type in ("portfolio.wake_registered", "portfolio.revisit_scheduled"):
            wake_id = str(payload.get("wake_id") or "")
            if wake_id:
                state.wakes[wake_id] = dict(payload)
    return state


def portfolio_snapshot(database: FoundryDatabase) -> dict[str, Any]:
    """Return a JSON-safe restart snapshot for evidence and consumers."""
    state = rebuild_portfolio_state(database)
    latest = max(
        state.decisions.values(),
        key=lambda row: int(row.get("sequence", 0)),
        default=None,
    )
    return {
        "policy": state.policy.to_payload(),
        "projects": {key: dict(value) for key, value in state.projects.items()},
        "work_items": {key: item.to_payload() for key, item in state.work_items.items()},
        "decisions": len(state.decisions),
        "latest_decision": dict(latest) if latest else None,
        "claims": {key: dict(value) for key, value in state.claims.items()},
        "leases": {key: dict(value) for key, value in state.leases.items()},
        "teams": {key: dict(value) for key, value in state.teams.items()},
        "outcomes": len(state.outcomes),
        "fairness_cycles": dict(state.fairness_cycles),
        "wakes": {key: dict(value) for key, value in state.wakes.items()},
    }


def configure_portfolio_policy(
    database: FoundryDatabase,
    policy: PortfolioPolicy,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    _controller_gate(database, controller)
    payload = {"policy": policy.to_payload(), "controller": controller}
    event = Event.create(
        "portfolio.policy_configured", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"portfolio.policy_configured:{_hash_payload(payload)}",
    )
    return str(database.append(event)[0])


def register_work_item(
    database: FoundryDatabase,
    *,
    item_id: str,
    kind: str,
    objective: str,
    controller: str = SUPERVISOR_CONTROLLER,
    goal_ids: Sequence[str] = (),
    project_id: str | None = None,
    resource_id: str | None = None,
    mission_id: str | None = None,
    candidate_id: str | None = None,
    expected_value: float | None = None,
    urgency: float | None = None,
    information_gain: float | None = None,
    confidence: float | None = None,
    effort: float | None = None,
    cost_usd: float = 0.0,
    risk: float | None = None,
    reversibility: float | None = None,
    opportunity_cost: float | None = None,
    dependencies: Sequence[str] = (),
    due_at: str | None = None,
    expires_at: str | None = None,
    owner_only: bool = False,
    requires_workers: int = 1,
    requires_verifier: bool = True,
    evidence_refs: Sequence[str] = (),
    provenance: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    _controller_gate(database, controller)
    _require(bool(item_id.strip()), "portfolio item_id is required")
    _require(kind in _WORK_CLASSES, f"unsupported portfolio class: {kind}")
    _require(bool(objective.strip()), "portfolio objective is required")
    _require(int(requires_workers) >= 0, "requires_workers must be non-negative")
    payload = WorkItem(
        item_id=item_id,
        kind=kind,
        objective=objective,
        goal_ids=tuple(str(value) for value in goal_ids),
        project_id=project_id,
        resource_id=resource_id,
        mission_id=mission_id,
        candidate_id=candidate_id,
        expected_value=_finite(expected_value),
        urgency=_finite(urgency),
        information_gain=_finite(information_gain),
        confidence=_finite(confidence),
        effort=_finite(effort),
        cost_usd=_number(cost_usd),
        risk=_finite(risk),
        reversibility=_finite(reversibility),
        opportunity_cost=_finite(opportunity_cost),
        dependencies=tuple(str(value) for value in dependencies),
        due_at=due_at,
        expires_at=expires_at,
        owner_only=owner_only,
        requires_workers=int(requires_workers),
        requires_verifier=requires_verifier,
        evidence_refs=tuple(str(value) for value in evidence_refs),
        provenance=dict(provenance or {}),
        metadata=dict(metadata or {}),
    )
    payload_dict = {**payload.to_payload(), "controller": controller}
    event = Event.create(
        "portfolio.work_registered", payload_dict,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"portfolio.work_registered:{item_id}",
    )
    return str(database.append(event)[0])


def update_work_item(
    database: FoundryDatabase,
    item_id: str,
    changes: Mapping[str, Any],
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    _controller_gate(database, controller)
    _require(item_id in rebuild_portfolio_state(database).work_items,
             f"unknown portfolio item: {item_id}")
    allowed = {
        "objective", "goal_ids", "project_id", "resource_id", "mission_id",
        "candidate_id",
        "expected_value",
        "urgency", "information_gain", "confidence", "effort", "cost_usd",
        "risk", "reversibility", "opportunity_cost", "dependencies", "due_at",
        "expires_at", "owner_only", "requires_workers", "requires_verifier",
        "evidence_refs", "provenance", "metadata",
    }
    clean = {key: value for key, value in dict(changes).items() if key in allowed}
    _require(bool(clean), "work update contains no supported fields")
    for name in ("goal_ids", "dependencies", "evidence_refs"):
        if name in clean:
            clean[name] = list(clean[name] or ())
    event = Event.create(
        "portfolio.work_updated",
        {"item_id": item_id, "changes": clean, "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"portfolio.work_updated:{item_id}:{_hash_payload(clean)}",
    )
    return str(database.append(event)[0])


def set_work_status(
    database: FoundryDatabase,
    item_id: str,
    status: str,
    *,
    reason: str = "",
    controller: str = SUPERVISOR_CONTROLLER,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    _controller_gate(database, controller)
    _require(item_id in rebuild_portfolio_state(database).work_items,
             f"unknown portfolio item: {item_id}")
    payload = {"item_id": item_id, "status": status, "reason": reason,
               "controller": controller}
    # Exactly-once belongs to ONE logical transition, not to a status value
    # for all time.  Keying by {item_id}:{status} burned the first CONSUMED /
    # BLOCKED forever, so a bounded recovery that legitimately re-enters the
    # same status collided with its own first cycle (live 2026-09-29 ->
    # 2026-10-02, pw-dupkey-...-f2757e1b6f92) and the guarded consumption
    # stamp could never run again — recovery was materialization-unreachable.
    # The reason names the transition (it carries the mission/candidate/cycle
    # that caused it), so a repeat of the SAME transition stays a no-op while
    # a genuinely new one records.
    event = Event.create(
        "portfolio.work_status_changed", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=(f"portfolio.work_status_changed:{item_id}:{status}:"
                         f"{hashlib.sha256(reason.encode('utf-8')).hexdigest()[:16]}"),
    )
    return str(database.append(event)[0])


def cancel_work_for_authority_withdrawal(
    database: FoundryDatabase,
    item_id: str,
    *,
    reason: str,
    controller: str = SUPERVISOR_CONTROLLER,
    source_id: str = "v13-owner-authority",
) -> str:
    """Cancel existing work during owner-control propagation.

    This is intentionally narrower than ``set_work_status``: it can only
    record the one-way safety transition to CANCELLED, so an active STOP or
    veto cannot prevent the Supervisor from making already-created work
    non-executable.
    """

    _controller_identity_gate(controller)
    _require(
        item_id in rebuild_portfolio_state(database).work_items,
        f"unknown portfolio item: {item_id}",
    )
    payload = {
        "item_id": item_id,
        "status": "CANCELLED",
        "reason": reason,
        "controller": controller,
    }
    event = Event.create(
        "portfolio.work_status_changed",
        payload,
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"portfolio.work_status_changed:{item_id}:CANCELLED",
    )
    return str(database.append(event)[0])


def register_maintenance(
    database: FoundryDatabase,
    *,
    maintenance_id: str,
    project_id: str,
    objective: str,
    due_at: str,
    verification_expiry: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
    **kwargs: Any,
) -> str:
    metadata = dict(kwargs.pop("metadata", {}) or {})
    metadata.update({"maintenance_id": maintenance_id,
                     "verification_expiry": verification_expiry})
    result = register_work_item(
        database, item_id=f"maintenance:{maintenance_id}", kind="MAINTENANCE",
        project_id=project_id, objective=objective, due_at=due_at,
        metadata=metadata, controller=controller, source_type=source_type,
        source_id=source_id, **kwargs,
    )
    database.append(Event.create(
        "portfolio.revisit_scheduled",
        {"wake_id": f"maintenance:{maintenance_id}:{due_at}",
         "entity_id": f"maintenance:{maintenance_id}", "due_at": due_at,
         "reason": "maintenance_due", "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"portfolio.revisit_scheduled:maintenance:{maintenance_id}:{due_at}",
    ))
    return result


def register_unknown(
    database: FoundryDatabase,
    *,
    unknown_id: str,
    question: str,
    why_matters: str,
    evidence_needed: Sequence[str] = (),
    confidence_threshold: float = 0.8,
    project_id: str | None = None,
    goal_ids: Sequence[str] = (),
    owner_only: bool = False,
    expires_at: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    return register_work_item(
        database, item_id=f"unknown:{unknown_id}", kind="UNKNOWN",
        objective=question, project_id=project_id, goal_ids=goal_ids,
        confidence=None, owner_only=owner_only, expires_at=expires_at,
        requires_verifier=False,
        evidence_refs=evidence_needed,
        metadata={
            "unknown_id": unknown_id,
            "why_matters": why_matters,
            "evidence_needed": list(evidence_needed),
            "confidence_threshold": float(confidence_threshold),
        },
        controller=controller, source_type=source_type, source_id=source_id,
    )


def register_research(
    database: FoundryDatabase,
    *,
    research_id: str,
    question: str,
    why_matters: str,
    evidence_needed: Sequence[str],
    confidence_threshold: float,
    project_id: str | None = None,
    goal_ids: Sequence[str] = (),
    owner_only: bool = False,
    expires_at: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    return register_work_item(
        database, item_id=f"research:{research_id}", kind="RESEARCH",
        objective=question, project_id=project_id, goal_ids=goal_ids,
        expected_value=0.6, information_gain=0.9, confidence=0.5,
        owner_only=owner_only, expires_at=expires_at, requires_verifier=False,
        evidence_refs=evidence_needed,
        metadata={
            "research_id": research_id,
            "why_matters": why_matters,
            "evidence_needed": list(evidence_needed),
            "confidence_threshold": float(confidence_threshold),
        },
        controller=controller, source_type=source_type, source_id=source_id,
    )


def _active_counts(
    database: FoundryDatabase, state: PortfolioState, now: datetime,
) -> dict[str, Any]:
    with database.connect(readonly=True) as connection:
        active_rows = connection.execute(
            "SELECT mission_id, resource_id, state FROM missions "
            "WHERE state IN (" + ",".join("?" for _ in ACTIVE_MISSION_STATES) + ")",
            tuple(ACTIVE_MISSION_STATES),
        ).fetchall()
        workers = connection.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE status = 'RUNNING'"
        ).fetchone()[0]
        verifiers = connection.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE status = 'RUNNING' "
            "AND lower(role) LIKE '%verif%'"
        ).fetchone()[0]
    active_leases = {
        key: value for key, value in state.leases.items()
        if (_as_datetime(value.get("expires_at")) is None
            or _as_datetime(value.get("expires_at")) > now)
    }
    active_claims = {
        key: value for key, value in state.claims.items()
        if (_as_datetime(value.get("expires_at")) is None
            or _as_datetime(value.get("expires_at")) > now)
    }
    claimed_cost = sum(_number(value.get("cost_usd")) for value in active_claims.values())
    claimed_workers = sum(max(0, int(_number(value.get("requires_workers"))))
                           for value in active_claims.values())
    claimed_verifiers = sum(
        1 for value in active_claims.values()
        if bool(value.get("requires_verifier"))
    )
    claimed_provider_calls = sum(
        max(0, int(_number(value.get("provider_call_count"))))
        for value in active_claims.values()
    )
    claimed_projects: dict[str, int] = {}
    claimed_resources: dict[str, int] = {}
    for value in active_claims.values():
        project_id = str(value.get("project_id") or "")
        resource_id = str(value.get("resource_id") or "")
        if project_id:
            claimed_projects[project_id] = claimed_projects.get(project_id, 0) + 1
        if resource_id:
            claimed_resources[resource_id] = claimed_resources.get(resource_id, 0) + 1
    spent = 0.0
    for outcome in state.outcomes:
        spent += _number(outcome.get("actual_cost_usd", outcome.get("cost_usd")))
    return {
        "active_missions": len(active_rows),
        "active_mission_rows": [dict(row) for row in active_rows],
        "active_claims": len(active_claims),
        "claimed_cost_usd": round(claimed_cost, 6),
        "claimed_workers": claimed_workers,
        "claimed_verifiers": claimed_verifiers,
        "claimed_provider_calls": claimed_provider_calls,
        "claimed_projects": claimed_projects,
        "claimed_resources": claimed_resources,
        "active_workers": int(workers or 0),
        "active_verifiers": int(verifiers or 0),
        "active_leases": len(active_leases),
        "budget_used_usd": round(spent, 6),
    }


def _project_active_counts(database: FoundryDatabase) -> dict[str, int]:
    active_states = tuple(sorted(ACTIVE_MISSION_STATES))
    placeholders = ", ".join("?" for _ in active_states)
    try:
        with database.connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT json_extract(e.payload_json, '$.project_id') AS project_id, "
                "COUNT(*) FROM events e "
                "JOIN missions m ON m.mission_id = "
                "json_extract(e.payload_json, '$.mission_id') "
                "WHERE e.event_type = 'mission.created' "
                f"AND m.state IN ({placeholders}) "
                "AND json_extract(e.payload_json, '$.project_id') IS NOT NULL "
                "GROUP BY project_id",
                active_states,
            ).fetchall()
    except Exception:  # optional historical payloads remain non-blocking
        return {}
    return {str(row[0]): int(row[1]) for row in rows if row[0]}


def _capacity_check(
    database: FoundryDatabase,
    state: PortfolioState,
    item: WorkItem,
    *,
    now: datetime,
    counts: dict[str, Any],
    project_active_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    policy = state.policy
    reasons: list[str] = []
    if item.status not in ("OPEN", "PLANNED", "ELIGIBLE"):
        reasons.append(f"status:{item.status}")
    # A work item's status is the authoritative blocked-ness.  The narrative
    # reason of the transition that set it is audit information, NOT a live
    # block: `set_work_status` stamps `blocked_reason = payload["reason"]` for
    # EVERY status change, including the ones that return an item to OPEN.
    # Gating on the narrative disqualified the item by the very reopen that
    # made it executable again (live 2026-10-02 21:00Z: the bounded recovery
    # reopened pw-dupkey-...-f2757e1b6f92 to OPEN and selection then WAITed
    # with "blocked:recovery reopen for cand-...: terminal lineage failed"
    # — the reopen made recovery unreachable while reading truthfully).
    #
    # Registered items reach this function only after `_all_work_items` has
    # filtered them to OPEN/PLANNED/ELIGIBLE, so the status clause below is
    # unreachable for them and the removed gate is NOT compensated here.  The
    # real enforcement of blocked-ness is that filter; the bounded retirement
    # of derived repair items lives in `_mission_items`.
    if item.blocked_reason and item.status in ("BLOCKED", "CANCELLED"):
        reasons.append(f"blocked:{item.blocked_reason}")
    if item.owner_only:
        reasons.append("owner_only_fact")
    if item.kind == "REPAIR":
        repair_attempts = max(0, int(_number(item.metadata.get("repair_attempts"))))
        if repair_attempts >= policy.max_repair_attempts:
            reasons.append("repair_budget_exhausted")
    if item.expires_at and _due(item.expires_at, now):
        reasons.append("expired")
    if item.kind == "MAINTENANCE" and item.due_at and not _due(item.due_at, now):
        reasons.append("not_due")
    for dependency in item.dependencies:
        project = state.projects.get(dependency)
        if project is not None and str(project.get("status")) != "COMPLETE":
            reasons.append(f"dependency:{dependency}")
    targets = {item.item_id}
    for value in (item.project_id, item.resource_id, item.mission_id, item.candidate_id):
        if value:
            targets.add(str(value))
    veto = matching_veto_reason(database, targets)
    if veto:
        reasons.append(f"veto:{veto}")

    regular_limit = max(0, policy.max_concurrent_missions - policy.reserved_capacity)
    mission_limit = (
        policy.max_concurrent_missions
        if item.kind in ("INCIDENT", "MAINTENANCE")
        else regular_limit
    )
    effective_active = max(counts["active_missions"], counts.get("active_claims", 0))
    if effective_active + 1 > mission_limit:
        reasons.append("capacity:concurrent_missions")
    effective_workers = counts["active_workers"] + counts.get("claimed_workers", 0)
    effective_verifiers = counts["active_verifiers"] + counts.get("claimed_verifiers", 0)
    if effective_workers + item.requires_workers > policy.max_workers:
        reasons.append("capacity:workers")
    if item.requires_verifier and effective_verifiers + 1 > policy.max_verifiers:
        reasons.append("capacity:verifiers")
    if (policy.budget_usd is not None
            and counts["budget_used_usd"] + counts.get("claimed_cost_usd", 0)
            + item.cost_usd > policy.budget_usd):
        reasons.append("budget:portfolio")
    provider = str(item.metadata.get("provider") or "")
    provider_calls = counts.get("claimed_provider_calls", 0)
    provider_call_count = max(
        0, int(_number(item.metadata.get("provider_call_count")))
    ) if item.metadata.get("provider_call_count") is not None else (1 if provider else 0)
    if policy.max_provider_calls is not None and provider_call_count:
        if provider_calls + provider_call_count > policy.max_provider_calls:
            reasons.append("capacity:provider_calls")
    if item.project_id:
        cap = policy.project_share_caps.get(item.project_id)
        active = (project_active_counts or {}).get(item.project_id, 0)
        claimed = counts.get("claimed_projects", {}).get(item.project_id, 0)
        if cap is not None and active + claimed + 1 > cap:
            reasons.append(f"capacity:project_share:{item.project_id}")
    if item.resource_id:
        cap = policy.resource_capacity.get(item.resource_id)
        if cap is not None:
            active_resource = sum(
                1 for row in counts["active_mission_rows"]
                if str(row["resource_id"] or "") == item.resource_id
            )
            claimed_resource = counts.get("claimed_resources", {}).get(item.resource_id, 0)
            if active_resource + claimed_resource + 1 > cap:
                reasons.append(f"capacity:resource:{item.resource_id}")
    if provider and provider in policy.provider_capacity:
        active_provider = sum(
            1 for claim in state.claims.values()
            if str(claim.get("provider") or "") == provider
            and (_as_datetime(claim.get("expires_at")) is None
                 or _as_datetime(claim.get("expires_at")) > now)
        )
        if active_provider + 1 > policy.provider_capacity[provider]:
            reasons.append(f"capacity:provider:{provider}")
    lease = state.claims.get(item.item_id)
    if lease and str(lease.get("decision_id") or ""):
        expires = _as_datetime(lease.get("expires_at"))
        if expires is None or expires > now:
            reasons.append("lease:held")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "active_missions": counts["active_missions"],
        "active_claims": counts.get("active_claims", 0),
        "active_workers": counts["active_workers"],
        "active_verifiers": counts["active_verifiers"],
        "claimed_workers": counts.get("claimed_workers", 0),
        "claimed_verifiers": counts.get("claimed_verifiers", 0),
        "claimed_provider_calls": counts.get("claimed_provider_calls", 0),
        "active_leases": counts["active_leases"],
        "budget_used_usd": counts["budget_used_usd"],
        "claimed_cost_usd": counts.get("claimed_cost_usd", 0.0),
        "budget_limit_usd": policy.budget_usd,
        "reserved_capacity": policy.reserved_capacity,
        "regular_mission_limit": regular_limit,
    }


def _goal_alignment(item: WorkItem, goals: Sequence[StructuredGoal]) -> float:
    direct = _finite(item.metadata.get("goal_alignment"))
    if direct is not None:
        return _clamp(direct)
    if item.goal_ids and goals:
        goal_ids = set(item.goal_ids)
        if any(goal.goal_id in goal_ids for goal in goals):
            return 1.0
    if not goals:
        return 0.0
    values = [goal_alignment_score(goal, item.objective) for goal in goals]
    return _clamp(max(values) if values else 0.0)


def _calibration_adjustment(state: PortfolioState, item: WorkItem) -> float:
    values: list[float] = []
    class_values: list[float] = []
    project_values: list[float] = []
    for outcome in state.outcomes:
        predicted = outcome.get("predicted") or {}
        actual = outcome.get("actual") or {}
        predicted_value = _finite(predicted.get("expected_value", predicted.get("value")))
        actual_value = _finite(actual.get("actual_value", actual.get("value")))
        if predicted_value is None or actual_value is None:
            continue
        if str(outcome.get("status") or "").upper() in {"BLOCKED", "NOT_TESTED"}:
            continue
        delta = _clamp(actual_value - predicted_value, -1.0, 1.0)
        values.append(delta)
        if str(outcome.get("kind") or "") == item.kind:
            class_values.append(delta)
        if item.project_id and str(outcome.get("project_id") or "") == item.project_id:
            project_values.append(delta)
    if not values:
        return 0.0
    def bounded(values_for_group: list[float]) -> float:
        if not values_for_group:
            return 0.0
        sample_weight = min(1.0, len(values_for_group) / 3.0)
        mean = sum(values_for_group) / len(values_for_group)
        return _clamp(mean * sample_weight * state.policy.calibration_alpha,
                      -state.policy.calibration_cap, state.policy.calibration_cap)
    return round(_clamp(bounded(values) + bounded(class_values) + bounded(project_values),
                        -state.policy.calibration_cap, state.policy.calibration_cap), 6)


def portfolio_calibration(database: FoundryDatabase) -> dict[str, Any]:
    """Return bounded calibration derived from immutable raw outcome events."""
    state = rebuild_portfolio_state(database)
    grouped: dict[str, list[float]] = {}
    for outcome in state.outcomes:
        predicted = outcome.get("predicted") or {}
        actual = outcome.get("actual") or {}
        before = _finite(predicted.get("expected_value", predicted.get("value")))
        after = _finite(actual.get("actual_value", actual.get("value")))
        if before is None or after is None:
            continue
        status = str(outcome.get("status") or "").upper()
        if status in {"BLOCKED", "NOT_TESTED"}:
            continue
        kind = str(outcome.get("kind") or "UNKNOWN")
        grouped.setdefault(kind, []).append(_clamp(after - before, -1.0, 1.0))
    adjustments: dict[str, float] = {}
    for kind, values in grouped.items():
        weight = min(1.0, len(values) / 3.0)
        adjustments[kind] = _clamp(
            (sum(values) / len(values)) * weight * state.policy.calibration_alpha,
            -state.policy.calibration_cap, state.policy.calibration_cap,
        )
    return {
        "policy": {"alpha": state.policy.calibration_alpha,
                   "cap": state.policy.calibration_cap},
        "sample_counts": {kind: len(values) for kind, values in grouped.items()},
        "adjustments": adjustments,
        "raw_outcomes": len(state.outcomes),
    }


def _work_score(
    database: FoundryDatabase,
    state: PortfolioState,
    item: WorkItem,
    *,
    goals: Sequence[StructuredGoal],
    capacity: dict[str, Any],
    now: datetime,
) -> ScoredWork:
    policy = state.policy
    goal_fit = _goal_alignment(item, goals)
    fairness_cycles = state.fairness_cycles.get(item.item_id, 0)
    fairness_bonus = min(policy.max_aging_bonus,
                         max(0, fairness_cycles - policy.fairness_after_cycles + 1)
                         * policy.aging_step)
    calibration = _calibration_adjustment(state, item)
    metadata_score = _finite(item.metadata.get("candidate_score"))
    unknown_fields: list[str] = []

    if metadata_score is not None:
        base = _clamp(metadata_score)
        headroom = 1.0 if capacity.get("eligible") else 0.0
        score = base + goal_fit * policy.candidate_goal_weight
        score += headroom * policy.candidate_capacity_weight
        score += calibration * policy.candidate_calibration_weight
        score += fairness_bonus
        factors = {
            "candidate_score": base,
            "goal_alignment": goal_fit,
            "capacity_headroom": headroom,
            "fairness_bonus": fairness_bonus,
            "calibration_adjustment": calibration,
        }
    else:
        values = {
            "expected_value": item.expected_value,
            "urgency": item.urgency,
            "information_gain": item.information_gain,
            "confidence": item.confidence,
            "reversibility": item.reversibility,
            "effort": item.effort,
            "cost": None if item.cost_usd is None else _clamp(1.0 - item.cost_usd),
            "risk": None if item.risk is None else _clamp(1.0 - item.risk),
            "opportunity_cost": None if item.opportunity_cost is None
            else _clamp(1.0 - item.opportunity_cost),
        }
        weights = {
            "expected_value": policy.work_ev_weight,
            "urgency": policy.work_urgency_weight,
            "information_gain": policy.work_information_weight,
            "confidence": policy.work_confidence_weight,
            "reversibility": policy.work_reversibility_weight,
            "effort": policy.work_effort_weight,
            "cost": policy.work_cost_weight,
            "risk": policy.work_risk_weight,
            "opportunity_cost": policy.work_opportunity_cost_weight,
        }
        contributions: dict[str, float] = {}
        total = 0.0
        for name, weight in weights.items():
            value = values[name]
            if value is None:
                unknown_fields.append(name)
                contribution = -policy.unknown_penalty * weight
            else:
                contribution = _clamp(value) * weight
            contributions[name] = round(contribution, 6)
            total += contribution
        score = total + goal_fit * policy.candidate_goal_weight
        score += calibration + fairness_bonus
        base = total
        factors = {
            "contributions": contributions,
            "goal_alignment": goal_fit,
            "fairness_bonus": fairness_bonus,
            "calibration_adjustment": calibration,
        }
    due_at = _as_datetime(item.due_at)
    if due_at is not None:
        seconds = (due_at - now).total_seconds()
        if seconds <= 0:
            factors["due"] = "now"
            score += policy.work_urgency_weight
        elif seconds <= policy.revisit_seconds:
            factors["due"] = "soon"
    score = round(max(-1.0, min(1.5, score)), 6)
    uncertainty = 1.0 if item.confidence is None else _clamp(1.0 - item.confidence)
    return ScoredWork(
        item=item,
        score=score,
        base_score=round(base, 6),
        goal_alignment=goal_fit,
        uncertainty=uncertainty,
        unknown_fields=tuple(unknown_fields),
        fairness_bonus=round(fairness_bonus, 6),
        calibration_adjustment=round(calibration, 6),
        capacity=capacity,
        factors=factors,
    )


def _project_items(state: PortfolioState) -> list[WorkItem]:
    items: list[WorkItem] = []
    for project_id, payload in state.projects.items():
        status = str(payload.get("status") or "PLANNED")
        if status in {"COMPLETE", "CANCELLED"}:
            continue
        goals = payload.get("goals") or ()
        goal_ids = tuple(str(goal.get("goal_id", goal.get("id")))
                        for goal in goals if isinstance(goal, Mapping)
                        and (goal.get("goal_id") or goal.get("id")))
        goal_texts = [str(goal.get("text", goal.get("title", "")))
                      for goal in goals if isinstance(goal, Mapping)]
        item = WorkItem(
            item_id=f"project:{project_id}", kind="NEW_MISSION",
            objective=str(payload.get("name") or project_id), status=status,
            project_id=project_id, goal_ids=goal_ids,
            expected_value=_finite(payload.get("ev")),
            urgency=_finite(payload.get("urgency")),
            information_gain=_finite(payload.get("information_gain")),
            reversibility=_finite(payload.get("reversibility")),
            opportunity_cost=_finite(payload.get("opportunity_cost")),
            cost_usd=_number(payload.get("costs_usd")),
            dependencies=tuple(str(value) for value in payload.get("dependencies") or ()),
            metadata={"goal_texts": goal_texts,
                      "priority_bump": _number(payload.get("priority_bump"))},
            provenance=dict(payload.get("provenance") or {}),
        )
        items.append(item)
    return items


def _owner_goal_items(
    database: FoundryDatabase, goals: Sequence[StructuredGoal],
) -> list[WorkItem]:
    """Expose each authenticated owner goal as first-class portfolio work.

    Goals used to influence scoring only.  With no independently discovered
    candidate in the ledger, that left the owner-facing path at a durable
    directive and made the executive WAIT forever.  The portfolio remains the
    sole selection authority: this adapter contributes comparable work items;
    it does not create missions or dispatch workers.
    """
    admitted: set[str] = set()
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT created_from, state FROM missions "
            "WHERE created_from LIKE 'portfolio:owner-goal:%'"
        ).fetchall()
    for row in rows:
        if str(row["state"] or "") != "CANCELLED":
            admitted.add(str(row["created_from"]).split("portfolio:", 1)[1])
    items: list[WorkItem] = []
    for goal in goals:
        if f"owner-goal:{goal.goal_id}" in admitted:
            continue
        priority = max(0.0, float(goal.priority))
        items.append(WorkItem(
            item_id=f"owner-goal:{goal.goal_id}",
            kind="OWNER_GOAL",
            objective=goal.objective,
            status="OPEN",
            goal_ids=(goal.goal_id,),
            expected_value=0.9,
            urgency=max(0.2, min(1.0, priority / 3.0)),
            information_gain=0.4,
            confidence=0.8,
            reversibility=0.8,
            requires_verifier=True,
            metadata={
                "goal_id": goal.goal_id,
                "success_criteria": list(goal.success_criteria),
                "constraints": list(goal.constraints),
                "time_horizon": goal.time_horizon,
                "goal_alignment": 1.0,
            },
            provenance=dict(goal.provenance),
        ))
    return items


def _retired_repair_missions(database: FoundryDatabase) -> set[str]:
    """Mission ids whose repair work has already been retired.

    ``portfolio.repair_retired`` is the durable signal that a REPAIR item for
    a terminal mission was attempted and could not be materialized (no
    remaining executable step contract).  Without honouring it here, the
    derived ``repair:{mission_id}`` item is re-admitted to the ranked pool on
    every cycle forever: it re-selects, materializes nothing, and appends a
    fresh ``portfolio.repair_retired`` each cycle because that event is keyed
    by ``{decision_id}:{mission_id}`` and decision_id changes per cycle.

    That unbounded loop was previously masked by the D5 narrative gate,
    which rejected the item for carrying a blocked_reason — a gate that also
    made the bounded recovery reopen unreachable.  The bounded truth lives
    here: a retired mission's repair is not re-offered.
    """
    retired: set[str] = set()
    with database.connect(readonly=True) as connection:
        for row in connection.execute(
                "SELECT json_extract(payload_json, '$.mission_id') AS m "
                "FROM events WHERE event_type = 'portfolio.repair_retired'"
        ).fetchall():
            if row["m"]:
                retired.add(str(row["m"]))
    return retired


def _mission_items(database: FoundryDatabase) -> list[WorkItem]:
    items: list[WorkItem] = []
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT mission_id, objective, resource_id, state, next_action "
            "FROM missions ORDER BY rowid"
        ).fetchall()
    retired = _retired_repair_missions(database)
    for row in rows:
        state = str(row["state"] or "")
        mission_id = str(row["mission_id"] or "")
        objective = str(row["objective"] or mission_id)
        if state in ACTIVE_MISSION_STATES:
            items.append(WorkItem(
                item_id=f"mission:{mission_id}", kind="ACTIVE_MISSION",
                objective=str(row["next_action"] or objective), status="OPEN",
                mission_id=mission_id, resource_id=row["resource_id"],
                expected_value=0.7, urgency=0.7, confidence=0.8,
                reversibility=0.7, requires_verifier=True,
                metadata={"mission_state": state},
            ))
        elif state in {"FAILED", "BLOCKED"}:
            if mission_id in retired:
                # Durable bounded retirement (see _retired_repair_missions):
                # this repair was already attempted and could not be
                # materialized, so it must not be re-offered every cycle.
                continue
            items.append(WorkItem(
                item_id=f"repair:{mission_id}", kind="REPAIR",
                objective=f"Repair mission {mission_id}: {objective}", status="OPEN",
                mission_id=mission_id, resource_id=row["resource_id"],
                expected_value=0.8, urgency=0.8, confidence=0.8,
                reversibility=0.6, metadata={"mission_state": state},
            ))
    return items


def _candidate_items(
    database: FoundryDatabase,
    *,
    resource_id: str | None,
    capability: str,
    candidate_limit: int,
    goals: Sequence[StructuredGoal],
) -> list[WorkItem]:
    epoch = current_activation_epoch(database)
    if not epoch:
        return []
    try:
        rows = eligible_candidates(
            database, activation_epoch=epoch, resource_id=resource_id,
            capability=capability, limit=candidate_limit,
        )
    except (KeyError, TypeError, ValueError):
        return []
    if not rows:
        return []
    try:
        ranked = rank_candidates(
            database, rows, goals=list(goals),
            calibration=rebuild_calibration(database), resource_contention=0.0,
        )
    except (KeyError, TypeError, ValueError):
        return []
    by_id = {str(row["candidate_id"]): row for row in rows}
    items: list[WorkItem] = []
    for score in ranked:
        row = by_id.get(score.candidate_id) or {}
        candidate_type = str(row.get("candidate_type") or "build")
        kind = "RESEARCH" if candidate_type == "research" else "CANDIDATE"
        items.append(WorkItem(
            item_id=f"candidate:{score.candidate_id}", kind=kind,
            objective=score.objective, status="ELIGIBLE",
            goal_ids=score.compared_goals, resource_id=resource_id,
            candidate_id=score.candidate_id,
            expected_value=_finite(score.factors[1].value if len(score.factors) > 1 else None),
            confidence=_finite(score.factors[4].value if len(score.factors) > 4 else None),
            reversibility=_finite(score.factors[7].value if len(score.factors) > 7 else None),
            requires_verifier=True,
            evidence_refs=(),
            metadata={
                "candidate_score": score.composite,
                "goal_alignment": score.goal_fit,
                "candidate_type": candidate_type,
                "source": row.get("source"),
                "candidate_row": {"candidate_id": score.candidate_id,
                                  "objective": score.objective},
            },
        ))
    return items


def _research_queue_items(database: FoundryDatabase) -> list[WorkItem]:
    try:
        rows = database.all("research_queue")
    except ValueError:
        return []
    items: list[WorkItem] = []
    for row in rows:
        if str(row.get("status") or "") != "OPEN":
            continue
        item_id = str(row.get("item_id") or "")
        claim = database.row("knowledge_claims", "claim_id", str(row.get("claim_id") or ""))
        objective = str((claim or {}).get("statement") or row.get("claim_id") or item_id)
        items.append(WorkItem(
            item_id=f"research-queue:{item_id}", kind="RESEARCH",
            objective=objective, expected_value=0.5, urgency=0.5,
            information_gain=0.8, confidence=0.4, reversibility=0.9,
            requires_verifier=False,
            metadata={"queue_item_id": item_id, "reason": row.get("reason")},
            evidence_refs=(str(row.get("evidence_json") or ""),),
        ))
    return items


def _improvement_items(database: FoundryDatabase) -> list[WorkItem]:
    try:
        rows = database.all("improvement_records")
    except ValueError:
        return []
    completed: set[str] = set()
    implementation_blocked: set[str] = set()
    extension_refs: dict[str, list[str]] = {}
    extension_snapshots: dict[str, str] = {}
    for event in database.events():
        if event.event_type == "cycle.completed":
            improvement_id = str(event.payload.get("improvement_id") or "")
            if improvement_id:
                completed.add(improvement_id)
        elif event.event_type == "improvement.implementation_blocked":
            improvement_id = str(event.payload.get("improvement_id") or "")
            if improvement_id:
                implementation_blocked.add(improvement_id)
        elif event.event_type == "improvement.evidence_extended":
            improvement_id = str(event.payload.get("improvement_id") or "")
            refs = event.payload.get("evidence_refs")
            if improvement_id and isinstance(refs, list):
                bucket = extension_refs.setdefault(improvement_id, [])
                bucket.extend(
                    str(ref) for ref in refs
                    if isinstance(ref, str) and ref.strip()
                    and ref not in bucket)
                snapshot_hash = event.payload.get("snapshot_hash")
                if isinstance(snapshot_hash, str) and snapshot_hash:
                    extension_snapshots[improvement_id] = snapshot_hash
    items: list[WorkItem] = []
    for row in rows:
        if str(row.get("state") or "") != "FROZEN" or \
                str(row.get("improvement_id")) in completed or \
                str(row.get("improvement_id")) in implementation_blocked:
            # Frozen records are immutable. If implementation authority was
            # missing, quarantine this record instead of reselecting it each
            # cycle; any later scoped finding must be a new record identity.
            continue
        try:
            evidence_refs = json.loads(
                str(row.get("evidence_refs_json") or "[]"))
        except (TypeError, ValueError):
            evidence_refs = []
        if not isinstance(evidence_refs, list):
            evidence_refs = []
        try:
            provenance = json.loads(
                str(row.get("provenance_json") or "{}"))
        except (TypeError, ValueError):
            provenance = {}
        if not isinstance(provenance, Mapping):
            provenance = {}
        improvement_id = str(row["improvement_id"])
        evidence_refs = list(dict.fromkeys(
            [str(ref) for ref in evidence_refs
             if isinstance(ref, str) and ref.strip()]
            + extension_refs.get(improvement_id, [])
        ))
        item_provenance = dict(provenance)
        latest_snapshot = extension_snapshots.get(improvement_id)
        if latest_snapshot:
            item_provenance["latest_detector_snapshot_hash"] = latest_snapshot
        items.append(WorkItem(
            item_id=f"improvement:{row['improvement_id']}", kind="IMPROVEMENT",
            objective=str(row.get("problem") or row["improvement_id"]),
            expected_value=0.7, urgency=0.4, information_gain=0.5,
            confidence=0.7, reversibility=0.8, requires_verifier=True,
            metadata={"improvement_id": row["improvement_id"],
                      "risk_class": row.get("risk_class")},
            evidence_refs=tuple(
                str(ref) for ref in evidence_refs
                if isinstance(ref, str) and ref.strip()),
            provenance=item_provenance,
        ))
    return items


_SEVERITY_URGENCY = {
    "S1": 0.95,
    "S2": 0.85,
    "S3": 0.6,
    "S4": 0.4,
}


def _incident_items(database: FoundryDatabase) -> list[WorkItem]:
    """Surface non-terminal resilience incidents as comparable portfolio work.

    Incidents outrank ordinary work through urgency, but STOP still outranks
    incident automation (checked independently by select_next_work and by the
    coordinator itself before any repair callback).
    """
    try:
        from ..resilience.core import rebuild_incidents
        incidents = rebuild_incidents(database)
    except (ImportError, ValueError, TypeError, KeyError):
        return []
    items: list[WorkItem] = []
    for incident in incidents:
        status = str(getattr(incident, "status", "") or "")
        if getattr(incident, "terminal", True):
            continue
        severity = str(getattr(incident, "severity", "") or "S3")
        urgency = _SEVERITY_URGENCY.get(severity, 0.5)
        incident_id = str(getattr(incident, "incident_id", "") or "")
        items.append(WorkItem(
            item_id=f"incident:{incident_id}", kind="INCIDENT",
            objective=(
                f"Contain/repair incident {incident_id} "
                f"[{severity}/{status}] "
                f"{getattr(incident, 'correlation_key', '')}"),
            status="OPEN",
            expected_value=0.9, urgency=urgency, information_gain=0.2,
            confidence=0.7, effort=0.3, risk=0.4, reversibility=0.5,
            requires_verifier=True,
            dependencies=tuple(str(m) for m in (
                getattr(incident, "affected_missions", ()) or ())),
            metadata={
                "incident_id": incident_id,
                "severity": severity,
                "incident_status": status,
                "correlation_key": getattr(
                    incident, "correlation_key", ""),
                "affected_capabilities": list(
                    getattr(incident, "affected_capabilities", ()) or ()),
                "effect_ambiguity": str(getattr(
                    incident, "effect_ambiguity", "")),
            },
            evidence_refs=(f"incident.detected:{incident_id}",),
        ))
    return items


def _all_work_items(
    database: FoundryDatabase,
    state: PortfolioState,
    *,
    resource_id: str | None,
    capability: str,
    candidate_limit: int,
    goals: Sequence[StructuredGoal],
    now: datetime,
    objective_id: str | None = None,
) -> list[WorkItem]:
    # Registered items occupy their identities even when closed: a terminal
    # mirror must shadow any derived duplicate (busywork guard).
    identities: dict[str, WorkItem] = {}
    for item in state.work_items.values():
        identities.setdefault(item.item_id, item)
    derived: list[WorkItem] = []
    for item in (_project_items(state) + _owner_goal_items(database, goals)
                 + _mission_items(database)):
        if item.item_id not in identities:
            derived.append(item)
    for item in _candidate_items(
            database, resource_id=resource_id, capability=capability,
            candidate_limit=candidate_limit, goals=goals):
        if item.item_id not in identities:
            derived.append(item)
    for item in (_research_queue_items(database)
                 + _improvement_items(database)
                 + _incident_items(database)):
        if item.item_id not in identities:
            derived.append(item)
    actionable = [
        item for item in identities.values()
        if item.status in ("OPEN", "PLANNED", "ELIGIBLE")
    ]
    result = actionable + [
        item for item in derived
        if all(item.item_id != kept.item_id for kept in actionable)
    ]
    if objective_id is not None:
        # Executive Autonomy may ask the canonical portfolio selector to
        # choose among work belonging to one objective.  Keep the selector,
        # scoring, leases, and materialization authority unchanged; this is a
        # deterministic scope filter that prevents an active objective from
        # accidentally materializing another objective's item.
        result = [
            item for item in result
            if str(item.metadata.get("executive_objective_id") or "") == str(objective_id)
        ]
    return result


def _latest_wait(state: PortfolioState) -> dict[str, Any] | None:
    waits = [value for value in state.decisions.values()
             if value.get("decision") == "WAIT"]
    return max(waits, key=lambda value: int(value.get("sequence", 0)), default=None)


def wake_is_due(database: FoundryDatabase, *, now: datetime | str | None = None) -> bool:
    """Return whether a WAIT has a deterministic reason to wake."""
    state = rebuild_portfolio_state(database)
    latest = _latest_wait(state)
    if latest is None:
        return True
    current = _as_datetime(now) or datetime.now(timezone.utc)
    latest_any = max(state.decisions.values(),
                     key=lambda value: int(value.get("sequence", 0)),
                     default=None)
    if (
        latest_any is not None
        and int(latest_any.get("sequence", 0)) > int(latest.get("sequence", 0))
    ):
        return True
    revisit = _as_datetime(latest.get("revisit_at"))
    if revisit is not None and revisit <= current:
        return True
    if any(
        (_as_datetime(value.get("due_at")) is not None
         and _as_datetime(value.get("due_at")) <= current)
        for value in state.wakes.values()
    ):
        return True
    sequence = int(latest.get("sequence", 0))
    return any(
        index > sequence and event.event_type in _WAKE_EVENT_TYPES
        for index, event in enumerate(database.events(), start=1)
    )


def _decision_for_loop(database: FoundryDatabase, loop_id: str) -> PortfolioDecision | None:
    for event in reversed(database.events()):
        if event.event_type == "portfolio.selection_recorded" \
                and str(event.payload.get("loop_id") or "") == loop_id:
            return PortfolioDecision.from_payload(event.payload)
    return None


def _append_selection(database: FoundryDatabase, decision: PortfolioDecision,
                      *, source_type: str, source_id: str) -> PortfolioDecision:
    try:
        database.append(Event.create(
            "portfolio.selection_recorded", decision.to_payload(),
            source_type=source_type, source_id=source_id,
            idempotency_key=f"portfolio.selection_recorded:{decision.loop_id}",
        ))
    except EventConflict:
        existing = _decision_for_loop(database, decision.loop_id)
        if existing is not None:
            return existing
        raise
    return decision


def select_next_work(
    database: FoundryDatabase,
    *,
    loop_id: str,
    controller: str = SUPERVISOR_CONTROLLER,
    resource_id: str | None = None,
    capability: str = "write_registered_worktree_files",
    candidate_limit: int = 20,
    now: datetime | str | None = None,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
    objective_id: str | None = None,
) -> PortfolioDecision:
    """Compare every available work class and record exactly one outcome."""
    _controller_identity_gate(controller)
    with _db_lock(database):
        prior = _decision_for_loop(database, loop_id)
        if prior is not None:
            return prior
        stop = emergency_stop_reason(database, database.path.parent / "EMERGENCY_STOP")
        veto = matching_veto_reason(database, {"GLOBAL"})
        current = _as_datetime(now) or datetime.now(timezone.utc)
        state = rebuild_portfolio_state(database)
        if stop or veto:
            reason = f"STOP active: {stop}" if stop else f"owner veto active: {veto}"
            decision = PortfolioDecision(
                decision_id=f"portfolio-{loop_id}", loop_id=loop_id,
                kind="WAIT", selected_class="WAIT", entity_id=None,
                reason=reason, why_now="owner control outranks portfolio value",
                capacity_check={}, veto_check={"stop": stop, "veto": veto},
                uncertainty=1.0, wake_on=("owner_directive", "stop_cleared"),
                revisit_at=_as_iso(current + timedelta(seconds=state.policy.revisit_seconds)),
            )
            return _append_selection(
                database, decision, source_type=source_type, source_id=source_id,
            )
        if not wake_is_due(database, now=current):
            latest = _latest_wait(state)
            if latest is not None:
                return PortfolioDecision.from_payload(latest)

        goals = active_goals(database)
        counts = _active_counts(database, state, current)
        project_active_counts = _project_active_counts(database)
        items = _all_work_items(
            database, state, resource_id=resource_id, capability=capability,
            candidate_limit=candidate_limit, goals=goals, now=current,
            objective_id=objective_id,
        )
        ranked: list[ScoredWork] = []
        rejected: list[dict[str, Any]] = []
        for item in items:
            capacity = _capacity_check(
                database, state, item, now=current, counts=counts,
                project_active_counts=project_active_counts,
            )
            score = _work_score(database, state, item, goals=goals,
                                capacity=capacity, now=current)
            if capacity["eligible"]:
                ranked.append(score)
            else:
                rejected.append({
                    "entity_id": item.item_id,
                    "selected_class": item.kind,
                    "score": score.score,
                    "reason": ";".join(capacity["reasons"]) or "not_eligible",
                    "capacity": capacity,
                })
        ranked.sort(key=lambda row: (-row.score, -row.goal_alignment, row.item.item_id))
        if not ranked:
            wake_on = ["new_evidence", "capacity_recovered", "owner_directive", "time"]
            reasons = sorted({
                reason
                for row in rejected
                for reason in row.get("capacity", {}).get("reasons", [])
            })
            reason = "no positive-value work is currently executable"
            if reasons:
                reason = "all available work is blocked or capacity constrained"
            revisit = current + timedelta(seconds=state.policy.revisit_seconds)
            alternatives = tuple(rejected[:candidate_limit])
            decision = PortfolioDecision(
                decision_id=f"portfolio-{loop_id}", loop_id=loop_id,
                kind="WAIT", selected_class="WAIT", entity_id=None,
                reason=reason,
                why_now=("waiting preserves capacity until a wake condition changes"
                         if reasons else "no candidate has positive executable value"),
                alternatives=alternatives,
                capacity_check={"snapshot": counts, "rejection_reasons": reasons},
                veto_check={"stop": None, "veto": None},
                uncertainty=1.0,
                wake_on=tuple(wake_on), revisit_at=_as_iso(revisit),
            )
            return _append_selection(
                database, decision, source_type=source_type, source_id=source_id,
            )

        selected = ranked[0]
        for row in ranked[1:]:
            rejected.append({
                "entity_id": row.item.item_id,
                "selected_class": row.item.kind,
                "score": row.score,
                "reason": f"ranked below {selected.item.item_id}",
                "goal_alignment": row.goal_alignment,
            })
        alternatives = tuple(sorted(
            rejected[:candidate_limit],
            key=lambda row: (str(row.get("reason")), str(row.get("entity_id"))),
        ))
        goal_text = f"goal alignment {selected.goal_alignment:.3f}"
        fairness_text = (
            f"; aging advanced after {state.fairness_cycles.get(selected.item.item_id, 0)} waits"
            if selected.fairness_bonus else ""
        )
        why_now = (
            f"{selected.item.kind} scored {selected.score:.3f} with "
            f"{goal_text}{fairness_text}"
        )
        decision = PortfolioDecision(
            decision_id=f"portfolio-{loop_id}", loop_id=loop_id,
            kind="SELECT", selected_class=selected.item.kind,
            entity_id=selected.item.item_id, reason="highest executable portfolio value",
            why_now=why_now, alternatives=alternatives,
            goal_alignment=selected.goal_alignment,
            capacity_check=selected.capacity,
            veto_check={"stop": None, "veto": None},
            expected_value=selected.item.expected_value,
            uncertainty=selected.uncertainty, selected_score=selected.score,
            wake_on=("work_completed", "mission.completed", "owner_directive"),
            selected_item=selected.to_payload(),
        )
        return _append_selection(database, decision, source_type=source_type, source_id=source_id)


def rank_portfolio_work(
    database: FoundryDatabase,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    resource_id: str | None = None,
    capability: str = "write_registered_worktree_files",
    candidate_limit: int = 20,
    now: datetime | str | None = None,
) -> list[dict[str, Any]]:
    """Return a read-oriented ranked audit without recording a selection."""
    _require(controller == SUPERVISOR_CONTROLLER,
             "only the Supervisor can read the portfolio ranking")
    current = _as_datetime(now) or datetime.now(timezone.utc)
    state = rebuild_portfolio_state(database)
    goals = active_goals(database)
    counts = _active_counts(database, state, current)
    project_active_counts = _project_active_counts(database)
    scored: list[ScoredWork] = []
    rejected: list[dict[str, Any]] = []
    for item in _all_work_items(
            database, state, resource_id=resource_id, capability=capability,
            candidate_limit=candidate_limit, goals=goals, now=current):
        capacity = _capacity_check(
            database, state, item, now=current, counts=counts,
            project_active_counts=project_active_counts,
        )
        row = _work_score(database, state, item, goals=goals,
                          capacity=capacity, now=current)
        if capacity["eligible"]:
            scored.append(row)
        else:
            rejected.append({**row.to_payload(), "eligible": False,
                             "reasons": list(capacity["reasons"])})
    scored.sort(key=lambda row: (-row.score, -row.goal_alignment, row.item.item_id))
    return [{**row.to_payload(), "eligible": True} for row in scored] + rejected


def claim_selection(
    database: FoundryDatabase,
    decision: PortfolioDecision,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    now: datetime | str | None = None,
    lease_ttl_seconds: float | None = None,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> dict[str, Any]:
    """Atomically claim one selected entity so concurrent loops converge."""
    _controller_gate(database, controller)
    _require(decision.kind == "SELECT" and decision.entity_id,
             "only SELECT decisions can be claimed")
    current = _as_datetime(now) or datetime.now(timezone.utc)
    state = rebuild_portfolio_state(database)
    ttl = float(lease_ttl_seconds if lease_ttl_seconds is not None
                else state.policy.lease_ttl_seconds)
    expires_at = _as_iso(current + timedelta(seconds=max(0.0, ttl)))
    entity_id = str(decision.entity_id)
    with _db_lock(database):
        fresh = rebuild_portfolio_state(database)
        existing = fresh.claims.get(entity_id)
        if existing:
            expiry = _as_datetime(existing.get("expires_at"))
            if expiry is None or expiry > current:
                if str(existing.get("decision_id") or "") == decision.decision_id:
                    return {"acquired": True, "entity_id": entity_id,
                            "reason": "idempotent_claim", "claim": dict(existing)}
                return {"acquired": False, "entity_id": entity_id,
                        "reason": "lease_held", "claim": dict(existing)}
        payload = {
            "decision_id": decision.decision_id,
            "loop_id": decision.loop_id,
            "entity_id": entity_id,
            "selected_class": decision.selected_class,
            "project_id": decision.selected_item.get("project_id"),
            "resource_id": decision.selected_item.get("resource_id"),
            "cost_usd": _number(decision.selected_item.get("cost_usd")),
            "requires_workers": max(
                0, int(_number(decision.selected_item.get("requires_workers")))
            ),
            "requires_verifier": bool(decision.selected_item.get("requires_verifier")),
            "provider": decision.selected_item.get("provider"),
            "provider_call_count": max(
                0, int(_number(decision.selected_item.get("provider_call_count")))
            ),
            "lease_id": f"lease:{decision.decision_id}",
            "expires_at": expires_at,
            "controller": controller,
        }
        event = Event.create(
            "portfolio.selection_claimed", payload,
            source_type=source_type, source_id=source_id,
            idempotency_key=f"portfolio.selection_claimed:{entity_id}:{decision.decision_id}",
        )
        lease_event = Event.create(
            "portfolio.capacity_lease_acquired", payload,
            source_type=source_type, source_id=source_id,
            idempotency_key=f"portfolio.capacity_lease_acquired:{entity_id}:{decision.decision_id}",
        )
        try:
            with database.transaction() as connection:
                database.append_events_on_connection(connection, [event, lease_event])
        except EventConflict:
            winner = rebuild_portfolio_state(database).claims.get(entity_id)
            return {"acquired": False, "entity_id": entity_id,
                    "reason": "concurrent_claim", "claim": dict(winner or {})}
    return {"acquired": True, "entity_id": entity_id, "claim": payload}


def release_selection_claim(
    database: FoundryDatabase,
    entity_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    reason: str = "completed",
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    _controller_gate(database, controller)
    state = rebuild_portfolio_state(database)
    existing = state.claims.get(entity_id) or {}
    decision_id = str(existing.get("decision_id") or "none")
    payload = {"entity_id": entity_id, "decision_id": decision_id,
               "reason": reason, "controller": controller}
    event = Event.create(
        "portfolio.selection_released", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"portfolio.selection_released:{entity_id}:{decision_id}",
    )
    return str(database.append(event)[0])


def record_portfolio_outcome(
    database: FoundryDatabase,
    *,
    outcome_id: str,
    selection_id: str,
    entity_id: str,
    kind: str,
    predicted: Mapping[str, Any],
    actual: Mapping[str, Any],
    status: str,
    project_id: str | None = None,
    actual_cost_usd: float = 0.0,
    actual_duration_seconds: float | None = None,
    repair_count: int = 0,
    controller: str = SUPERVISOR_CONTROLLER,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    _require(controller == SUPERVISOR_CONTROLLER,
             "only the Supervisor can record portfolio outcomes")
    _require(kind in _WORK_CLASSES, f"unsupported portfolio class: {kind}")
    _require(status in {"PASSED", "FAILED", "BLOCKED", "NOT_TESTED"},
             f"unsupported outcome status: {status}")
    state = rebuild_portfolio_state(database)
    claim = state.claims.get(entity_id)
    stale = bool(claim and str(claim.get("decision_id") or "") != selection_id)
    payload = {
        "outcome_id": outcome_id,
        "selection_id": selection_id,
        "entity_id": entity_id,
        "kind": kind,
        "project_id": project_id,
        "predicted": dict(predicted),
        "actual": dict(actual),
        "status": status,
        "actual_cost_usd": _number(actual_cost_usd),
        "actual_duration_seconds": _finite(actual_duration_seconds),
        "repair_count": int(repair_count),
        "stale_selection": stale,
        "controller": controller,
    }
    event = Event.create(
        "portfolio.outcome_recorded", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"portfolio.outcome_recorded:{outcome_id}",
    )
    return str(database.append(event)[0])


def plan_team(
    database: FoundryDatabase,
    *,
    mission_id: str,
    roles: Sequence[str],
    controller: str = SUPERVISOR_CONTROLLER,
    team_id: str | None = None,
    builder_provider: str | None = None,
    builder_run_id: str | None = None,
    verifier_identity: Mapping[str, Any] | None = None,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> dict[str, Any]:
    _controller_gate(database, controller)
    manifest = validate_team(list(roles))
    team_id = team_id or f"team:{mission_id}"
    verifier = None
    if "verifier" in manifest["roles"]:
        _require(verifier_identity is not None,
                 "a verifier identity is required for a verifier role")
        verifier = fresh_verifier_identity(
            verifier_provider=str(verifier_identity.get("provider") or ""),
            verifier_session_id=str(verifier_identity.get("session_id") or ""),
            builder_provider=builder_provider,
            builder_run_id=builder_run_id,
        )
    state = rebuild_portfolio_state(database)
    existing = state.teams.get(team_id)
    if existing:
        if tuple(existing.get("roles") or ()) != tuple(manifest["roles"]):
            raise PortfolioError(f"team {team_id} already exists with different roles")
        return dict(existing)
    workers = {role: f"{team_id}:{role}:1" for role in manifest["roles"]}
    payload = {
        "team_id": team_id, "mission_id": mission_id,
        "roles": list(manifest["roles"]), "workers": workers,
        "verifier": verifier, "retired_workers": [], "controller": controller,
    }
    database.append(Event.create(
        "portfolio.team_created", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"portfolio.team_created:{team_id}",
    ))
    return payload


def replace_team_worker(
    database: FoundryDatabase,
    *,
    team_id: str,
    role: str,
    old_worker_id: str,
    new_worker_id: str,
    reason: str,
    controller: str = SUPERVISOR_CONTROLLER,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    _controller_gate(database, controller)
    state = rebuild_portfolio_state(database)
    team = state.teams.get(team_id)
    _require(team is not None, f"unknown team: {team_id}")
    _require(role in set(team.get("roles") or ()), f"role {role} is not in team {team_id}")
    _require(str((team.get("workers") or {}).get(role) or "") == old_worker_id,
             "stale worker cannot retain team authority")
    payload = {"team_id": team_id, "role": role,
               "old_worker_id": old_worker_id, "new_worker_id": new_worker_id,
               "reason": reason, "controller": controller}
    return str(database.append(Event.create(
        "portfolio.team_worker_replaced", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"portfolio.team_worker_replaced:{team_id}:{old_worker_id}:{new_worker_id}",
    ))[0])


def retire_team_worker(
    database: FoundryDatabase,
    *,
    team_id: str,
    worker_id: str,
    reason: str,
    controller: str = SUPERVISOR_CONTROLLER,
    source_type: str = "supervisor",
    source_id: str = "v7-portfolio",
) -> str:
    _controller_gate(database, controller)
    state = rebuild_portfolio_state(database)
    _require(team_id in state.teams, f"unknown team: {team_id}")
    payload = {"team_id": team_id, "worker_id": worker_id,
               "reason": reason, "controller": controller}
    return str(database.append(Event.create(
        "portfolio.team_worker_retired", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"portfolio.team_worker_retired:{team_id}:{worker_id}",
    ))[0])


def management_packet(
    database: FoundryDatabase,
    *,
    now: datetime | str | None = None,
    resource_id: str | None = None,
    capability: str = "write_registered_worktree_files",
) -> dict[str, Any]:
    """Project the executive packet consumed by V4 or Command Center."""
    state = rebuild_portfolio_state(database)
    current = _as_datetime(now) or datetime.now(timezone.utc)
    latest = max(
        state.decisions.values(),
        key=lambda row: int(row.get("sequence", 0)),
        default=None,
    )
    ranking = rank_portfolio_work(
        database, resource_id=resource_id, capability=capability, now=current,
    )
    blocked = [row for row in ranking if not row.get("eligible")]
    owner_only = [row for row in blocked if "owner_only_fact" in row.get("reasons", ())]
    advanced = [
        {"event_type": event.event_type, "event_id": event.event_id,
         "payload": dict(event.payload)}
        for event in database.events()
        if event.event_type in {"portfolio.work_status_changed", "portfolio.outcome_recorded",
                                "mission.completed"}
    ][-10:]
    waiting = latest if latest and latest.get("decision") == "WAIT" else None
    return {
        "advanced": advanced,
        "rejected": list((latest or {}).get("alternatives") or ()),
        "blocked": blocked[:25],
        "waiting": waiting,
        "resource_cost_state": _active_counts(database, state, current),
        "next_selected": latest if latest and latest.get("decision") == "SELECT"
        else (ranking[0] if ranking else None),
        "owner_decisions_needed": owner_only[:25],
        "latest_selection": latest,
        "calibration": portfolio_calibration(database),
    }


__all__ = [
    "PortfolioError", "PortfolioPolicy", "PortfolioDecision", "PortfolioState",
    "WorkItem", "ScoredWork", "configure_portfolio_policy",
    "register_work_item", "update_work_item", "set_work_status",
    "cancel_work_for_authority_withdrawal",
    "register_maintenance", "register_unknown", "register_research",
    "rebuild_portfolio_state", "portfolio_snapshot", "rank_portfolio_work",
    "select_next_work", "wake_is_due", "claim_selection",
    "release_selection_claim", "record_portfolio_outcome", "portfolio_calibration",
    "plan_team", "replace_team_worker", "retire_team_worker", "management_packet",
]
