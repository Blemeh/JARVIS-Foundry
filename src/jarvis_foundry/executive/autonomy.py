"""Event-derived executive autonomy over the canonical mission spine.

This module owns executive intent, planning, prioritisation and continuation
decisions.  It deliberately does not execute work: portfolio selection,
mission materialisation, the Supervisor and MissionLoop remain the existing
authorities for those boundaries.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..clock import isoformat
from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event, EventConflict, canonical_json
from ..owner_directives import active_directives_for

SUPERVISOR_CONTROLLER = "supervisor"
_TERMINAL_OBJECTIVE = frozenset({"COMPLETED", "FAILED", "CANCELLED", "SUSPENDED"})
_ACTIVE_OBJECTIVE = frozenset({"ACTIVE", "BLOCKED", "WAITING", "PLANNING", "REGRESSED"})
_MISSION_GENERATION_OBJECTIVE = frozenset({"ACTIVE", "PLANNING", "REGRESSED"})
_TERMINAL_STEP = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def _number(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, _number(value)))


def _tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,) if values else ()
    try:
        return tuple(str(value) for value in values if str(value))
    except TypeError:
        return (str(values),)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:24]


def _timestamp(value: Any = None) -> str:
    if value:
        return str(value)
    return isoformat()


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _json(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    source_id: str = "v8-executive-autonomy",
    correlation_id: str | None = None,
) -> str:
    event = Event.create(
        event_type,
        dict(payload),
        source_type="supervisor",
        source_id=source_id,
        correlation_id=correlation_id,
        idempotency_key=key,
    )
    return str(database.append(event)[0])


@dataclass(frozen=True, slots=True)
class ObjectiveRecord:
    objective_id: str
    objective: str
    parent_id: str | None = None
    priority: float = 1.0
    urgency: float = 0.0
    deadline: str | None = None
    constraints: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    failure_criteria: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    owner_authority: str = "owner"
    autonomy_boundaries: tuple[str, ...] = ()
    budget: dict[str, float] = field(default_factory=dict)
    expected_impact: float = 0.5
    effort: float = 0.5
    risk: float = 0.5
    opportunity_cost: float = 0.0
    blocking_value: float = 0.0
    reversibility: float = 0.5
    confidence: float = 0.5
    resource_id: str | None = None
    required_capabilities: tuple[str, ...] = ()
    status: str = "ACTIVE"
    progress: float = 0.0
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    mission_ids: tuple[str, ...] = ()
    plan_ids: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    version: int = 1
    created_at: str = field(default_factory=isoformat)
    updated_at: str = field(default_factory=isoformat)

    def __post_init__(self) -> None:
        if not self.objective_id or not self.objective.strip():
            raise ValueError("objective_id and objective are required")
        if self.status not in _ACTIVE_OBJECTIVE | _TERMINAL_OBJECTIVE:
            raise ValueError(f"unsupported objective status: {self.status}")
        if self.progress < 0 or self.progress > 1:
            raise ValueError("progress must be between 0 and 1")

    def to_payload(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "objective": self.objective,
            "parent_id": self.parent_id,
            "priority": self.priority,
            "urgency": self.urgency,
            "deadline": self.deadline,
            "constraints": list(self.constraints),
            "success_criteria": list(self.success_criteria),
            "failure_criteria": list(self.failure_criteria),
            "dependencies": list(self.dependencies),
            "owner_authority": self.owner_authority,
            "autonomy_boundaries": list(self.autonomy_boundaries),
            "budget": dict(self.budget),
            "expected_impact": self.expected_impact,
            "effort": self.effort,
            "risk": self.risk,
            "opportunity_cost": self.opportunity_cost,
            "blocking_value": self.blocking_value,
            "reversibility": self.reversibility,
            "confidence": self.confidence,
            "resource_id": self.resource_id,
            "required_capabilities": list(self.required_capabilities),
            "status": self.status,
            "progress": self.progress,
            "provenance": dict(self.provenance),
            "metadata": dict(self.metadata),
            "mission_ids": list(self.mission_ids),
            "plan_ids": list(self.plan_ids),
            "conflicts_with": list(self.conflicts_with),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ObjectiveRecord":
        p = dict(payload)
        return cls(
            objective_id=str(p.get("objective_id") or ""),
            objective=str(p.get("objective") or ""),
            parent_id=str(p["parent_id"]) if p.get("parent_id") else None,
            priority=_number(p.get("priority"), 1.0),
            urgency=_clamp(p.get("urgency"), -1.0, 1.0),
            deadline=str(p["deadline"]) if p.get("deadline") else None,
            constraints=_tuple(p.get("constraints")),
            success_criteria=_tuple(p.get("success_criteria")),
            failure_criteria=_tuple(p.get("failure_criteria")),
            dependencies=_tuple(p.get("dependencies")),
            owner_authority=str(p.get("owner_authority") or "owner"),
            autonomy_boundaries=_tuple(p.get("autonomy_boundaries")),
            budget={str(k): _number(v) for k, v in _json(p.get("budget")).items()},
            expected_impact=_clamp(p.get("expected_impact")),
            effort=_clamp(p.get("effort")),
            risk=_clamp(p.get("risk")),
            opportunity_cost=_clamp(p.get("opportunity_cost")),
            blocking_value=_clamp(p.get("blocking_value")),
            reversibility=_clamp(p.get("reversibility")),
            confidence=_clamp(p.get("confidence")),
            resource_id=str(p["resource_id"]) if p.get("resource_id") else None,
            required_capabilities=_tuple(p.get("required_capabilities")),
            status=str(p.get("status") or "ACTIVE"),
            progress=_clamp(p.get("progress")),
            provenance=_json(p.get("provenance")),
            metadata=_json(p.get("metadata")),
            mission_ids=_tuple(p.get("mission_ids")),
            plan_ids=_tuple(p.get("plan_ids")),
            conflicts_with=_tuple(p.get("conflicts_with")),
            version=max(1, int(_number(p.get("version"), 1))),
            created_at=_timestamp(p.get("created_at")),
            updated_at=_timestamp(p.get("updated_at")),
        )


@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    objective_id: str
    description: str
    dependencies: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    probation_capabilities: tuple[str, ...] = ()
    resource_id: str | None = None
    output_contract: tuple[str, ...] = ()
    verification_contract: tuple[str, ...] = ()
    fallback: str | None = None
    status: str = "READY"
    mission_id: str | None = None
    retries: int = 0
    max_retries: int = 1
    evidence_refs: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id, "objective_id": self.objective_id,
            "description": self.description, "dependencies": list(self.dependencies),
            "required_capabilities": list(self.required_capabilities),
            "probation_capabilities": list(self.probation_capabilities),
            "resource_id": self.resource_id, "output_contract": list(self.output_contract),
            "verification_contract": list(self.verification_contract), "fallback": self.fallback,
            "status": self.status, "mission_id": self.mission_id, "retries": self.retries,
            "max_retries": self.max_retries, "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_payload(cls, p: Mapping[str, Any]) -> "PlanStep":
        return cls(
            step_id=str(p.get("step_id") or ""), objective_id=str(p.get("objective_id") or ""),
            description=str(p.get("description") or ""), dependencies=_tuple(p.get("dependencies")),
            required_capabilities=_tuple(p.get("required_capabilities")),
            probation_capabilities=_tuple(p.get("probation_capabilities")),
            resource_id=str(p["resource_id"]) if p.get("resource_id") else None,
            output_contract=_tuple(p.get("output_contract")),
            verification_contract=_tuple(p.get("verification_contract")),
            fallback=str(p["fallback"]) if p.get("fallback") else None,
            status=str(p.get("status") or "READY"),
            mission_id=str(p["mission_id"]) if p.get("mission_id") else None,
            retries=max(0, int(_number(p.get("retries")))),
            max_retries=max(0, int(_number(p.get("max_retries"), 1))),
            evidence_refs=_tuple(p.get("evidence_refs")),
        )


@dataclass(frozen=True, slots=True)
class PlanRecord:
    plan_id: str
    objective_id: str
    steps: tuple[PlanStep, ...]
    critical_path: tuple[str, ...] = ()
    status: str = "ACTIVE"
    version: int = 1
    created_at: str = field(default_factory=isoformat)
    updated_at: str = field(default_factory=isoformat)

    def to_payload(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id, "objective_id": self.objective_id,
            "steps": [step.to_payload() for step in self.steps],
            "critical_path": list(self.critical_path), "status": self.status,
            "version": self.version, "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, p: Mapping[str, Any]) -> "PlanRecord":
        return cls(
            plan_id=str(p.get("plan_id") or ""), objective_id=str(p.get("objective_id") or ""),
            steps=tuple(PlanStep.from_payload(row) for row in (p.get("steps") or ()) if isinstance(row, Mapping)),
            critical_path=_tuple(p.get("critical_path")), status=str(p.get("status") or "ACTIVE"),
            version=max(1, int(_number(p.get("version"), 1))), created_at=_timestamp(p.get("created_at")),
            updated_at=_timestamp(p.get("updated_at")),
        )


@dataclass(frozen=True, slots=True)
class PriorityScore:
    objective_id: str
    score: float
    factors: dict[str, float]
    explanation: str
    eligible: bool = True
    blockers: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id, "score": self.score,
            "factors": dict(self.factors), "explanation": self.explanation,
            "eligible": self.eligible, "blockers": list(self.blockers),
        }


@dataclass(frozen=True, slots=True)
class ProgressEvaluation:
    objective_id: str
    status: str
    progress: float
    confidence: float
    evidence_refs: tuple[str, ...]
    completed_steps: int
    failed_steps: int
    stale: bool
    explanation: str
    regressed: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id, "status": self.status, "progress": self.progress,
            "confidence": self.confidence, "evidence_refs": list(self.evidence_refs),
            "completed_steps": self.completed_steps, "failed_steps": self.failed_steps,
            "stale": self.stale, "explanation": self.explanation,
            "regressed": self.regressed,
        }


@dataclass(frozen=True, slots=True)
class ContinuationDecision:
    objective_id: str
    action: str
    reason: str
    evidence_refs: tuple[str, ...] = ()
    next_step_id: str | None = None
    retry_number: int = 0
    escalation_id: str | None = None
    wake_on: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id, "action": self.action, "reason": self.reason,
            "evidence_refs": list(self.evidence_refs), "next_step_id": self.next_step_id,
            "retry_number": self.retry_number, "escalation_id": self.escalation_id,
            "wake_on": list(self.wake_on),
        }


@dataclass(frozen=True, slots=True)
class EscalationRecord:
    escalation_id: str
    objective_id: str
    decision: str
    why: str
    evidence_refs: tuple[str, ...]
    options: tuple[str, ...]
    recommendation: str
    delay_consequence: str
    status: str = "OPEN"
    created_at: str = field(default_factory=isoformat)

    def to_payload(self) -> dict[str, Any]:
        return {
            "escalation_id": self.escalation_id, "objective_id": self.objective_id,
            "decision": self.decision, "why": self.why, "evidence_refs": list(self.evidence_refs),
            "options": list(self.options), "recommendation": self.recommendation,
            "delay_consequence": self.delay_consequence, "status": self.status,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class CoordinationDecision:
    decision_id: str
    objective_ids: tuple[str, ...]
    selected_objective_id: str | None
    reason: str
    conflicts: tuple[dict[str, Any], ...] = ()
    concurrency: dict[str, Any] = field(default_factory=dict)
    allocations: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id, "objective_ids": list(self.objective_ids),
            "selected_objective_id": self.selected_objective_id, "reason": self.reason,
            "conflicts": [dict(row) for row in self.conflicts],
            "concurrency": dict(self.concurrency), "allocations": dict(self.allocations),
        }


@dataclass(frozen=True, slots=True)
class ExecutiveBriefing:
    briefing_id: str
    generated_at: str
    advanced: tuple[dict[str, Any], ...]
    failed: tuple[dict[str, Any], ...]
    changed_priorities: tuple[dict[str, Any], ...]
    decisions: tuple[dict[str, Any], ...]
    owner_actions: tuple[dict[str, Any], ...]
    next_actions: tuple[dict[str, Any], ...]
    blockers: tuple[dict[str, Any], ...]
    outcomes: tuple[dict[str, Any], ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "briefing_id": self.briefing_id, "generated_at": self.generated_at,
            "advanced": [dict(row) for row in self.advanced], "failed": [dict(row) for row in self.failed],
            "changed_priorities": [dict(row) for row in self.changed_priorities],
            "decisions": [dict(row) for row in self.decisions],
            "owner_actions": [dict(row) for row in self.owner_actions],
            "next_actions": [dict(row) for row in self.next_actions],
            "blockers": [dict(row) for row in self.blockers],
            "outcomes": [dict(row) for row in self.outcomes],
        }


@dataclass(frozen=True, slots=True)
class ExecutiveAutonomyState:
    objectives: dict[str, ObjectiveRecord] = field(default_factory=dict)
    plans: dict[str, PlanRecord] = field(default_factory=dict)
    continuations: dict[str, ContinuationDecision] = field(default_factory=dict)
    escalations: dict[str, EscalationRecord] = field(default_factory=dict)
    coordination: dict[str, CoordinationDecision] = field(default_factory=dict)
    briefings: dict[str, ExecutiveBriefing] = field(default_factory=dict)
    mission_requests: dict[str, dict[str, Any]] = field(default_factory=dict)
    progress: dict[str, ProgressEvaluation] = field(default_factory=dict)
    # Operational intelligence is advisory input to Executive Autonomy.  The
    # proposal records below are durable receipts of that hand-off; they are
    # deliberately not objectives or missions and therefore cannot bypass the
    # owner/trust/mission authorities.
    operational_followup_proposals: dict[str, dict[str, Any]] = field(default_factory=dict)


class ExecutiveAutonomyService:
    """Small Supervisor-owned facade over the event-derived autonomy API."""

    def __init__(self, database: FoundryDatabase, *, controller: str = SUPERVISOR_CONTROLLER) -> None:
        self.database = database
        self.controller = controller

    def state(self) -> ExecutiveAutonomyState:
        return rebuild_executive_state(self.database)

    def sync_owner_goals(self) -> list[ObjectiveRecord]:
        return sync_owner_goals(self.database, controller=self.controller)

    def create_objective(self, objective: str, **kwargs: Any) -> ObjectiveRecord:
        return create_objective(self.database, objective, controller=self.controller, **kwargs)

    def update_objective(self, objective_id: str, changes: Mapping[str, Any]) -> ObjectiveRecord:
        return update_objective(self.database, objective_id, changes, controller=self.controller)

    def build_plan(self, objective_id: str, steps: Sequence[PlanStep | Mapping[str, Any]], **kwargs: Any) -> PlanRecord:
        return build_plan(self.database, objective_id, steps, controller=self.controller, **kwargs)

    def rank(self, **kwargs: Any) -> list[PriorityScore]:
        return rank_objectives(self.database, controller=self.controller, **kwargs)

    def coordinate(self, **kwargs: Any) -> CoordinationDecision:
        return coordinate_objectives(self.database, controller=self.controller, **kwargs)

    def decide_continuation(self, objective_id: str, **kwargs: Any) -> ContinuationDecision:
        return decide_continuation(self.database, objective_id, controller=self.controller, **kwargs)

    def cycle(self, cycle_id: str, **kwargs: Any) -> AutonomyCycle:
        return run_autonomy_cycle(self.database, cycle_id=cycle_id, controller=self.controller, **kwargs)

    def briefing(self, **kwargs: Any) -> ExecutiveBriefing:
        return build_executive_briefing(self.database, controller=self.controller, **kwargs)


def _apply_objective_update(
    objectives: dict[str, ObjectiveRecord], payload: Mapping[str, Any], *, replace: bool = False,
) -> None:
    changes = _json(payload.get("changes"))
    if changes:
        oid = str(payload.get("objective_id") or changes.get("objective_id") or "")
        prior = objectives.get(oid)
        if prior is None:
            try:
                objectives[oid] = ObjectiveRecord.from_payload(changes)
            except (TypeError, ValueError):
                return
            return
        merged = prior.to_payload()
        merged.update(changes)
        merged["objective_id"] = prior.objective_id
        try:
            objectives[prior.objective_id] = ObjectiveRecord.from_payload(merged)
        except (TypeError, ValueError):
            return
        return
    try:
        incoming = ObjectiveRecord.from_payload(payload)
    except (TypeError, ValueError):
        return
    prior = objectives.get(incoming.objective_id)
    if replace or prior is None:
        objectives[incoming.objective_id] = incoming
    else:
        objectives[incoming.objective_id] = incoming


def rebuild_executive_state(
    database: FoundryDatabase,
    *,
    _events: Sequence[Event] | None = None,
) -> ExecutiveAutonomyState:
    """Rebuild executive state solely from the append-only event ledger."""
    objectives: dict[str, ObjectiveRecord] = {}
    plans: dict[str, PlanRecord] = {}
    continuations: dict[str, ContinuationDecision] = {}
    escalations: dict[str, EscalationRecord] = {}
    coordination: dict[str, CoordinationDecision] = {}
    briefings: dict[str, ExecutiveBriefing] = {}
    mission_requests: dict[str, dict[str, Any]] = {}
    progress: dict[str, ProgressEvaluation] = {}
    operational_followup_proposals: dict[str, dict[str, Any]] = {}
    executive_events = (
        _events
        if _events is not None
        else database.events(event_type_prefixes=("executive.",))
    )
    for event in executive_events:
        payload = event.payload
        typ = event.event_type
        if typ == "executive.objective_created":
            _apply_objective_update(objectives, payload, replace=True)
        elif typ == "executive.objective_updated":
            _apply_objective_update(objectives, payload)
        elif typ == "executive.objective_status_changed":
            oid = str(payload.get("objective_id") or "")
            if oid in objectives:
                merged = objectives[oid].to_payload()
                merged.update({"status": payload.get("status"), "updated_at": payload.get("updated_at"), "version": payload.get("version", objectives[oid].version + 1)})
                try:
                    objectives[oid] = ObjectiveRecord.from_payload(merged)
                except (TypeError, ValueError):
                    pass
        elif typ == "executive.objective_progress_recorded":
            oid = str(payload.get("objective_id") or "")
            if oid in objectives:
                merged = objectives[oid].to_payload()
                merged.update({"progress": payload.get("progress"), "status": payload.get("status", objectives[oid].status),
                               "confidence": payload.get("confidence", objectives[oid].confidence)})
                if payload.get("updated_at"):
                    merged["updated_at"] = payload.get("updated_at")
                try:
                    objectives[oid] = ObjectiveRecord.from_payload(merged)
                except (TypeError, ValueError):
                    pass
            try:
                progress[oid] = ProgressEvaluation(
                    objective_id=oid, status=str(payload.get("status") or "UNKNOWN"),
                    progress=_clamp(payload.get("progress")), confidence=_clamp(payload.get("confidence")),
                    evidence_refs=_tuple(payload.get("evidence_refs")),
                    completed_steps=max(0, int(_number(payload.get("completed_steps")))),
                    failed_steps=max(0, int(_number(payload.get("failed_steps")))),
                    stale=bool(payload.get("stale")), explanation=str(payload.get("explanation") or ""),
                    regressed=bool(payload.get("regressed")),
                )
            except (TypeError, ValueError):
                pass
        elif typ == "executive.plan_created" or typ == "executive.plan_updated":
            try:
                plan = PlanRecord.from_payload(payload)
                if plan.plan_id:
                    plans[plan.plan_id] = plan
            except (TypeError, ValueError):
                pass
        elif typ == "executive.continuation_decided":
            try:
                row = ContinuationDecision(
                    objective_id=str(payload.get("objective_id") or ""), action=str(payload.get("action") or "WAIT"),
                    reason=str(payload.get("reason") or ""), evidence_refs=_tuple(payload.get("evidence_refs")),
                    next_step_id=str(payload["next_step_id"]) if payload.get("next_step_id") else None,
                    retry_number=max(0, int(_number(payload.get("retry_number")))),
                    escalation_id=str(payload["escalation_id"]) if payload.get("escalation_id") else None,
                    wake_on=_tuple(payload.get("wake_on")),
                )
                if row.objective_id:
                    continuations[row.objective_id] = row
            except (TypeError, ValueError):
                pass
        elif typ == "executive.escalation_opened" or typ == "executive.escalation_resolved":
            try:
                row = EscalationRecord(
                    escalation_id=str(payload.get("escalation_id") or ""),
                    objective_id=str(payload.get("objective_id") or ""),
                    decision=str(payload.get("decision") or ""), why=str(payload.get("why") or ""),
                    evidence_refs=_tuple(payload.get("evidence_refs")), options=_tuple(payload.get("options")),
                    recommendation=str(payload.get("recommendation") or ""),
                    delay_consequence=str(payload.get("delay_consequence") or ""),
                    status=str(payload.get("status") or ("RESOLVED" if "resolved" in typ else "OPEN")),
                    created_at=_timestamp(payload.get("created_at")),
                )
                if row.escalation_id:
                    escalations[row.escalation_id] = row
            except (TypeError, ValueError):
                pass
        elif typ == "executive.coordination_decided":
            try:
                row = CoordinationDecision(
                    decision_id=str(payload.get("decision_id") or ""), objective_ids=_tuple(payload.get("objective_ids")),
                    selected_objective_id=str(payload["selected_objective_id"]) if payload.get("selected_objective_id") else None,
                    reason=str(payload.get("reason") or ""),
                    conflicts=tuple(dict(v) for v in (payload.get("conflicts") or ()) if isinstance(v, Mapping)),
                    concurrency=_json(payload.get("concurrency")), allocations=_json(payload.get("allocations")),
                )
                if row.decision_id:
                    coordination[row.decision_id] = row
            except (TypeError, ValueError):
                pass
        elif typ == "executive.mission_requested":
            key = str(payload.get("request_id") or payload.get("objective_id") or event.idempotency_key)
            mission_requests[key] = dict(payload)
        elif typ == "executive.operational_followup_proposed":
            key = str(payload.get("proposal_id") or event.idempotency_key)
            if key:
                operational_followup_proposals[key] = dict(payload)
        elif typ == "executive.briefing_recorded":
            try:
                briefing = ExecutiveBriefing(
                    briefing_id=str(payload.get("briefing_id") or ""), generated_at=_timestamp(payload.get("generated_at")),
                    advanced=tuple(dict(v) for v in (payload.get("advanced") or ()) if isinstance(v, Mapping)),
                    failed=tuple(dict(v) for v in (payload.get("failed") or ()) if isinstance(v, Mapping)),
                    changed_priorities=tuple(dict(v) for v in (payload.get("changed_priorities") or ()) if isinstance(v, Mapping)),
                    decisions=tuple(dict(v) for v in (payload.get("decisions") or ()) if isinstance(v, Mapping)),
                    owner_actions=tuple(dict(v) for v in (payload.get("owner_actions") or ()) if isinstance(v, Mapping)),
                    next_actions=tuple(dict(v) for v in (payload.get("next_actions") or ()) if isinstance(v, Mapping)),
                    blockers=tuple(dict(v) for v in (payload.get("blockers") or ()) if isinstance(v, Mapping)),
                    outcomes=tuple(dict(v) for v in (payload.get("outcomes") or ()) if isinstance(v, Mapping)),
                )
                if briefing.briefing_id:
                    briefings[briefing.briefing_id] = briefing
            except (TypeError, ValueError):
                pass
    return ExecutiveAutonomyState(
        objectives, plans, continuations, escalations, coordination, briefings,
        mission_requests, progress, operational_followup_proposals,
    )


def _gate(
    database: FoundryDatabase,
    controller: str,
    *,
    stop_path: str | Path | None = None,
) -> None:
    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("executive autonomy is Supervisor-owned")
    stop = emergency_stop_reason(
        database,
        Path(stop_path) if stop_path is not None else database.path.parent / "EMERGENCY_STOP",
    )
    if stop:
        raise RuntimeError(f"STOP active: {stop}")
    veto = matching_veto_reason(database, {"GLOBAL", "executive", "objectives"})
    if veto:
        raise RuntimeError(f"owner veto active: {veto}")


def _assert_acyclic(graph: Mapping[str, Sequence[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"dependency cycle detected at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, ()):
            if dependency in graph:
                visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def _parse_goal_metadata(record: Any) -> dict[str, Any]:
    text = str(getattr(record, "exact_text", "") or "")
    lowered = text.lower()
    priority = None
    urgency = None
    deadline = None
    for token in ("priority", "urgency", "deadline"):
        import re
        match = re.search(rf"{token}\s*(?:=|:)\s*([-+]?\d+(?:\.\d+)?|\S+)", text, re.I)
        if match:
            value = match.group(1)
            if token == "deadline":
                deadline = value
            elif token == "priority":
                priority = _number(value, 1.0)
            else:
                urgency = _number(value, 0.0)
    def section(name: str) -> tuple[str, ...]:
        import re
        match = re.search(rf"{name}\s*(?:=|:)\s*(.+?)(?=\s+(?:priority|urgency|deadline|constraints?|depends?[_ -]?on|success|failure)\s*(?:=|:)|$)", text, re.I)
        if not match:
            return ()
        return tuple(part.strip() for part in re.split(r"[;,]", match.group(1)) if part.strip())
    return {
        "priority": priority if priority is not None else (3.0 if any(w in lowered for w in ("critical", "urgent", "p0")) else 1.0),
        "urgency": urgency if urgency is not None else (1.0 if any(w in lowered for w in ("urgent", "today", "now")) else 0.0),
        "deadline": deadline,
        "constraints": section("constraints?"),
        "dependencies": section(r"depends?[_ -]?on"),
        "success_criteria": section("success(?: criteria)?"),
        "failure_criteria": section("failure(?: criteria)?"),
    }


def create_objective(
    database: FoundryDatabase,
    objective: str,
    *,
    objective_id: str | None = None,
    parent_id: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    priority: float = 1.0,
    urgency: float = 0.0,
    deadline: str | None = None,
    constraints: Sequence[str] = (),
    success_criteria: Sequence[str] = (),
    failure_criteria: Sequence[str] = (),
    dependencies: Sequence[str] = (),
    owner_authority: str = "owner",
    autonomy_boundaries: Sequence[str] = (),
    budget: Mapping[str, float] | None = None,
    expected_impact: float = 0.5,
    effort: float = 0.5,
    risk: float = 0.5,
    opportunity_cost: float = 0.0,
    blocking_value: float = 0.0,
    reversibility: float = 0.5,
    confidence: float = 0.5,
    resource_id: str | None = None,
    required_capabilities: Sequence[str] = (),
    status: str = "ACTIVE",
    progress: float = 0.0,
    provenance: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    source_id: str = "v8-executive-autonomy",
) -> ObjectiveRecord:
    _gate(database, controller)
    oid = objective_id or f"objective-{_hash({'objective': objective, 'parent_id': parent_id})}"
    state = rebuild_executive_state(database)
    prior = state.objectives.get(oid)
    if prior is not None:
        if prior.objective != objective:
            raise ValueError(f"objective id already names a different objective: {oid}")
        return prior
    record = ObjectiveRecord(
        objective_id=oid, objective=objective, parent_id=parent_id, priority=_number(priority, 1.0),
        urgency=_clamp(urgency, -1.0, 1.0), deadline=deadline, constraints=_tuple(constraints),
        success_criteria=_tuple(success_criteria), failure_criteria=_tuple(failure_criteria), dependencies=_tuple(dependencies),
        owner_authority=owner_authority, autonomy_boundaries=_tuple(autonomy_boundaries),
        budget={str(k): _number(v) for k, v in dict(budget or {}).items()}, expected_impact=_clamp(expected_impact),
        effort=_clamp(effort), risk=_clamp(risk), opportunity_cost=_clamp(opportunity_cost), blocking_value=_clamp(blocking_value),
        reversibility=_clamp(reversibility), confidence=_clamp(confidence), resource_id=resource_id,
        required_capabilities=_tuple(required_capabilities), status=status, progress=_clamp(progress),
        provenance=_json(provenance), metadata=_json(metadata),
    )
    if parent_id and parent_id not in state.objectives:
        raise ValueError(f"unknown parent objective: {parent_id}")
    if any(dep == oid for dep in record.dependencies):
        raise ValueError("objective cannot depend on itself")
    graph = {key: value.dependencies for key, value in state.objectives.items()}
    graph[oid] = record.dependencies
    _assert_acyclic(graph)
    _append(database, "executive.objective_created", record.to_payload(), key=f"executive.objective_created:{oid}", source_id=source_id)
    return record


def withdraw_objective_authority(
    database: FoundryDatabase,
    objective_id: str,
    *,
    directive_id: str,
    reason: str,
    controller: str = SUPERVISOR_CONTROLLER,
    cancel_descendants: bool = True,
    transition_id: str | None = None,
) -> ObjectiveRecord | None:
    """Suspend one owner-derived objective and optionally cancel descendants."""

    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("objective authority withdrawal is Supervisor-owned")
    transition = transition_id or directive_id
    state = rebuild_executive_state(database)
    objective = state.objectives.get(objective_id)
    if objective is None:
        return None
    immutable_terminal = objective.status in {"COMPLETED", "FAILED", "CANCELLED"}
    if not immutable_terminal:
        if objective.status != "SUSPENDED":
            now = isoformat()
            try:
                _append(
                    database,
                    "executive.objective_status_changed",
                    {
                        "objective_id": objective.objective_id,
                        "status": "SUSPENDED",
                        "reason": reason,
                        "evidence_refs": list(filter(None, (
                            str(objective.provenance.get("event_id") or ""),
                            f"owner-directive:{directive_id}",
                        ))),
                        "updated_at": now,
                        "version": objective.version + 1,
                    },
                    key=(
                        "executive.objective_status_changed:"
                        f"{objective.objective_id}:"
                        f"{objective.version + 1}:SUSPENDED"
                    ),
                    source_id="v13-owner-authority",
                    correlation_id=objective.objective_id,
                )
            except EventConflict:
                concurrent = rebuild_executive_state(database).objectives.get(
                    objective.objective_id
                )
                if concurrent is None or concurrent.status != "SUSPENDED":
                    raise
            objective = rebuild_executive_state(database).objectives[
                objective.objective_id
            ]
        _append(
            database,
            "executive.objective_authority_revoked",
            {
                "objective_id": objective.objective_id,
                "directive_id": directive_id,
                "reason": reason,
                "transition_id": transition,
            },
            key=(
                "executive.objective_authority_revoked:"
                f"{objective.objective_id}:{_hash(transition)}"
            ),
            correlation_id=objective.objective_id,
        )
    if cancel_descendants:
        for mission_id in objective.mission_ids:
            mission = database.row("missions", "mission_id", mission_id)
            if (
                mission is None
                or str(mission.get("state") or "") in {
                    "COMPLETE", "FAILED", "BLOCKED", "CANCELLED",
                }
            ):
                continue
            try:
                _append(database, "mission.state_changed", {
                    "mission_id": mission_id,
                    "state": "CANCELLED",
                    "next_action": "owner authority revoked",
                    "reason": reason,
                }, key=(
                    f"mission.state_changed:{mission_id}:CANCELLED:"
                    f"executive-authority-revoked:{_hash(transition)}"
                ))
            except ValueError:
                pass
        try:
            from ..evolution.executive_portfolio import (
                cancel_work_for_authority_withdrawal,
                rebuild_portfolio_state,
            )

            portfolio = rebuild_portfolio_state(database)
            for item in portfolio.work_items.values():
                if (
                    item.metadata.get("executive_objective_id")
                    == objective.objective_id
                    and item.status not in {
                        "COMPLETED", "FAILED", "BLOCKED", "CANCELLED",
                    }
                ):
                    cancel_work_for_authority_withdrawal(
                        database,
                        item.item_id,
                        reason=reason,
                        controller=controller,
                        source_id="v13-owner-authority",
                    )
        except (ValueError, KeyError):
            pass
    return rebuild_executive_state(database).objectives.get(objective_id)


def sync_owner_goals(database: FoundryDatabase, *, controller: str = SUPERVISOR_CONTROLLER) -> list[ObjectiveRecord]:
    """Admit each authenticated active GOAL exactly once with full provenance."""
    _gate(database, controller)
    state = rebuild_executive_state(database)
    active_records = list(active_directives_for(database, command_types=frozenset({"GOAL"})))
    active_directive_ids = {str(record.directive_id) for record in active_records}
    created: list[ObjectiveRecord] = []
    for record in active_records:
        oid = f"owner-goal:{record.directive_id}"
        if oid in state.objectives:
            continue
        from .standing_intent import is_discovery_mandate, standing_intent_payload
        goal_text = str(record.exact_text or "owner goal")
        if is_discovery_mandate(goal_text):
            _append(
                database,
                "executive.standing_intent_recorded",
                standing_intent_payload(
                    goal_text, directive_id=str(record.directive_id),
                ),
                key=f"executive.standing_intent_recorded:{record.directive_id}",
                source_id="v8-owner-goal-admission",
            )
            continue
        metadata = _parse_goal_metadata(record)
        created.append(create_objective(
            database,
            goal_text,
            objective_id=oid,
            controller=controller,
            priority=metadata["priority"], urgency=metadata["urgency"], deadline=metadata["deadline"],
            constraints=metadata["constraints"], success_criteria=metadata["success_criteria"],
            failure_criteria=metadata["failure_criteria"], dependencies=metadata["dependencies"],
            owner_authority=str(getattr(record, "source_id", "owner") or "owner"),
            provenance={"directive_id": record.directive_id, "event_id": record.event_id,
                        "correlation_id": record.correlation_id, "nonce": record.nonce,
                        "authenticated": bool(record.authenticated)},
            metadata={"target": record.target, "command_type": record.command_type},
            source_id="v8-owner-goal-admission",
        ))
    revoked = [objective for objective in state.objectives.values()
               if objective.objective_id.startswith("owner-goal:")
               and str(objective.provenance.get("directive_id") or "") not in active_directive_ids
               and objective.status not in _TERMINAL_OBJECTIVE]
    for objective in revoked:
        withdraw_objective_authority(
            database,
            objective.objective_id,
            directive_id=str(
                objective.provenance.get("directive_id") or "unknown",
            ),
            reason=(
                "owner GOAL directive was revoked; autonomous authority "
                "is withdrawn"
            ),
            controller=controller,
        )
    from ..delegation.service import reconcile_owner_delegations

    reconcile_owner_delegations(database, controller=controller)
    return created


def update_objective(
    database: FoundryDatabase, objective_id: str, changes: Mapping[str, Any], *,
    controller: str = SUPERVISOR_CONTROLLER, source_id: str = "v8-executive-autonomy",
) -> ObjectiveRecord:
    _gate(database, controller)
    prior = rebuild_executive_state(database).objectives.get(objective_id)
    if prior is None:
        raise ValueError(f"unknown objective: {objective_id}")
    allowed = set(prior.to_payload()) - {"objective_id", "created_at", "version"}
    clean = {key: value for key, value in dict(changes).items() if key in allowed}
    if not clean:
        return prior
    merged = prior.to_payload()
    merged.update(clean)
    merged["updated_at"] = isoformat()
    merged["version"] = prior.version + 1
    candidate = ObjectiveRecord.from_payload(merged)
    graph = {key: value.dependencies for key, value in rebuild_executive_state(database).objectives.items()}
    graph[objective_id] = candidate.dependencies
    _assert_acyclic(graph)
    _append(database, "executive.objective_updated", {"objective_id": objective_id, "changes": candidate.to_payload(), "updated_at": candidate.updated_at}, key=f"executive.objective_updated:{objective_id}:{candidate.version}", source_id=source_id)
    return candidate


def set_objective_status(database: FoundryDatabase, objective_id: str, status: str, *, controller: str = SUPERVISOR_CONTROLLER, reason: str = "", evidence_refs: Sequence[str] = ()) -> ObjectiveRecord:
    if status not in _ACTIVE_OBJECTIVE | _TERMINAL_OBJECTIVE:
        raise ValueError(f"unsupported objective status: {status}")
    prior = rebuild_executive_state(database).objectives.get(objective_id)
    if prior is None:
        raise ValueError(f"unknown objective: {objective_id}")
    if prior.status == status:
        return prior
    if status == "COMPLETED" and not tuple(evidence_refs) and not prior.success_criteria:
        raise ValueError("completion requires evidence or success criteria")
    _gate(database, controller)
    now = isoformat()
    _append(database, "executive.objective_status_changed", {"objective_id": objective_id, "status": status, "reason": reason, "evidence_refs": list(evidence_refs), "updated_at": now, "version": prior.version + 1}, key=f"executive.objective_status_changed:{objective_id}:{prior.version + 1}:{status}")
    return ObjectiveRecord.from_payload({**prior.to_payload(), "status": status, "updated_at": now, "version": prior.version + 1})


def record_objective_progress(database: FoundryDatabase, evaluation: ProgressEvaluation, *, controller: str = SUPERVISOR_CONTROLLER) -> None:
    _gate(database, controller)
    _append(database, "executive.objective_progress_recorded", evaluation.to_payload(), key=f"executive.objective_progress_recorded:{evaluation.objective_id}:{_hash(evaluation.to_payload())}")


def _topological_steps(steps: Sequence[PlanStep]) -> tuple[PlanStep, ...]:
    by_id = {step.step_id: step for step in steps}
    graph = {step.step_id: tuple(dep for dep in step.dependencies if dep in by_id) for step in steps}
    _assert_acyclic(graph)
    ordered: list[PlanStep] = []
    remaining = set(by_id)
    while remaining:
        ready = sorted(step_id for step_id in remaining if all(dep not in remaining for dep in graph[step_id]))
        if not ready:
            raise ValueError("plan dependency graph is cyclic")
        ordered.extend(by_id[step_id] for step_id in ready)
        remaining.difference_update(ready)
    return tuple(ordered)


def _critical_path(steps: Sequence[PlanStep]) -> tuple[str, ...]:
    by_id = {step.step_id: step for step in steps}
    ordered = _topological_steps(steps)
    distance: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    for step in ordered:
        dep = max((distance.get(item, 0) for item in step.dependencies), default=0)
        distance[step.step_id] = dep + 1
        parent[step.step_id] = max(
            (item for item in step.dependencies),
            key=lambda item: distance.get(item, 0), default=None,
        )
    if not distance:
        return ()
    current = max(distance, key=lambda item: (distance[item], item))
    path: list[str] = []
    while current:
        path.append(current)
        current = parent.get(current)
    return tuple(reversed(path))


def build_plan(
    database: FoundryDatabase,
    objective_id: str,
    steps: Sequence[PlanStep | Mapping[str, Any]],
    *,
    plan_id: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    source_id: str = "v8-executive-autonomy",
) -> PlanRecord:
    _gate(database, controller)
    objective = rebuild_executive_state(database).objectives.get(objective_id)
    if objective is None:
        raise ValueError(f"unknown objective: {objective_id}")
    normalized: list[PlanStep] = []
    for raw in steps:
        if isinstance(raw, PlanStep):
            step = raw
        else:
            data = dict(raw)
            step = PlanStep(
                step_id=str(data.get("step_id") or ""), objective_id=objective_id,
                description=str(data.get("description") or data.get("objective") or ""),
                dependencies=_tuple(data.get("dependencies")),
                required_capabilities=_tuple(data.get("required_capabilities") or objective.required_capabilities),
                probation_capabilities=_tuple(data.get("probation_capabilities")),
                resource_id=str(data["resource_id"]) if data.get("resource_id") else objective.resource_id,
                output_contract=_tuple(data.get("output_contract")), verification_contract=_tuple(data.get("verification_contract")),
                fallback=str(data["fallback"]) if data.get("fallback") else None,
                status=str(data.get("status") or "READY"), mission_id=str(data["mission_id"]) if data.get("mission_id") else None,
                retries=max(0, int(_number(data.get("retries")))), max_retries=max(0, int(_number(data.get("max_retries"), 1))),
                evidence_refs=_tuple(data.get("evidence_refs")),
            )
        if not step.step_id or not step.description:
            raise ValueError("plan steps require step_id and description")
        if step.objective_id != objective_id:
            step = replace(step, objective_id=objective_id)
        normalized.append(step)
    if not normalized:
        raise ValueError("plan requires at least one executable step")
    if len({step.step_id for step in normalized}) != len(normalized):
        raise ValueError("plan step ids must be unique")
    by_id = {step.step_id: step for step in normalized}
    unknown = {dep for step in normalized for dep in step.dependencies if dep not in by_id}
    if unknown:
        raise ValueError(f"plan depends on unknown steps: {sorted(unknown)}")
    ordered = _topological_steps(normalized)
    pid = plan_id or f"plan:{objective_id}:{_hash([step.to_payload() for step in ordered])}"
    state = rebuild_executive_state(database)
    prior = state.plans.get(pid)
    if prior is not None:
        return prior
    plan = PlanRecord(plan_id=pid, objective_id=objective_id, steps=ordered, critical_path=_critical_path(ordered))
    _append(database, "executive.plan_created", plan.to_payload(), key=f"executive.plan_created:{pid}", source_id=source_id, correlation_id=objective_id)
    current = rebuild_executive_state(database).objectives[objective_id]
    if pid not in current.plan_ids:
        update_objective(database, objective_id, {"plan_ids": [*current.plan_ids, pid]}, controller=controller, source_id=source_id)
    return plan


def active_plan(database: FoundryDatabase, objective_id: str) -> PlanRecord | None:
    state = rebuild_executive_state(database)
    return _active_plan_from_state(state, objective_id)


def _active_plan_from_state(
    state: ExecutiveAutonomyState, objective_id: str,
) -> PlanRecord | None:
    """Return an active plan without replaying the ledger again."""
    plans = [
        plan for plan in state.plans.values()
        if plan.objective_id == objective_id and plan.status == "ACTIVE"
    ]
    return max(plans, key=lambda plan: (plan.version, plan.updated_at), default=None)


def _provider_observation_blockers(
    database: FoundryDatabase,
    provider_id: str,
    model_id: str | None = None,
) -> tuple[str, ...]:
    """Return fail-closed blockers for a capability's provider binding.

    The operational projection is intentionally read-only, but Executive
    Autonomy must still consult the same provider evidence before creating
    work.  A registered provider with no complete health/auth/availability
    sample is UNKNOWN, never silently healthy.  A model-specific sample wins;
    a provider-level sample is the only permitted fallback.
    """
    provider_id = str(provider_id or "").strip()
    if not provider_id:
        return ()
    selected: dict[str, Any] | None = None
    provider_level: dict[str, Any] | None = None
    try:
        events = database.events(
            readonly=True, newest_first=True, limit=500,
            event_type_prefixes=("provider.",),
        )
    except Exception:
        return (f"provider:{provider_id}:UNKNOWN",)
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        observed_provider = str(payload.get("provider_id") or payload.get("provider") or "").strip()
        if observed_provider != provider_id:
            continue
        observed_model = str(payload.get("model_id") or payload.get("model") or "").strip()
        row = dict(payload)
        row["event_id"] = event.event_id
        if model_id and observed_model == str(model_id):
            selected = row
            break
        if not observed_model and provider_level is None:
            provider_level = row
    sample = selected or provider_level
    if sample is None:
        return (f"provider:{provider_id}:UNKNOWN",)
    blockers: list[str] = []
    health = str(sample.get("health") or sample.get("status") or "UNKNOWN").upper()
    auth = str(sample.get("auth_state") or "UNKNOWN").upper()
    availability = str(sample.get("availability") or "UNKNOWN").upper()
    if health not in {"HEALTHY", "READY"}:
        blockers.append(f"provider:{provider_id}:{health}")
    if auth not in {"READY", "CONFIGURED"}:
        blockers.append(f"provider:{provider_id}:AUTH_{auth}")
    if availability not in {"AVAILABLE", "READY"}:
        blockers.append(f"provider:{provider_id}:AVAILABILITY_{availability}")
    return tuple(dict.fromkeys(blockers))


def _fabric_capability_blockers(
    database: FoundryDatabase,
    capability: str,
    *,
    allow_probation: bool = False,
) -> tuple[str, ...]:
    """Read Fabric capability readiness from the single catalog authority."""

    from ..fabric.catalog import get_spec, observed_readiness

    spec = get_spec(database, capability)
    if spec is None:
        return ()
    readiness = observed_readiness(database, capability)
    blockers: list[str] = []
    if readiness.state.value != "READY":
        blockers.append(f"capability:{capability}:{readiness.state.value}")
    trust_state = readiness.trust_state.value
    if trust_state == "DISABLED":
        blockers.append(f"capability:{capability}:DISABLED")
    elif trust_state == "PROBATION" and not allow_probation:
        blockers.append(f"capability:{capability}:PROBATION")
    for provider_id in spec.provider_deps:
        blockers.extend(_provider_observation_blockers(database, provider_id))
    return tuple(dict.fromkeys(blockers))


def _legacy_operational_capability_blockers(
    database: FoundryDatabase,
    capability: str,
) -> tuple[str, ...]:
    """Read the gated pre-Fabric registry only for legacy compatibility ids."""
    try:
        from ..capabilities.registry import discover_capabilities
        rows = discover_capabilities(database)
    except Exception:
        # A missing/failed compatibility projection is an unknown execution
        # precondition for the legacy id; Fabric ids never use this path.
        return (f"capability:{capability}:UNKNOWN",)
    matches = [
        row for row in rows
        if str(row.get("capability_id") or "") == capability
        or str(row.get("name") or "") == capability
        or capability in tuple(str(value) for value in (row.get("surfaces") or ()))
    ]
    if not matches:
        return ()
    # A capability id is the strongest identity.  Otherwise deterministic
    # lexical ordering keeps a surface alias stable when several rows expose
    # the same name.
    row = sorted(matches, key=lambda item: str(item.get("capability_id") or ""))[0]
    blockers: list[str] = []
    health = str(row.get("health") or "UNKNOWN").upper()
    auth = str(row.get("auth_state") or "UNKNOWN").upper()
    availability = str(row.get("availability") or "UNKNOWN").upper()
    if health not in {"HEALTHY", "READY"}:
        blockers.append(f"capability:{capability}:{health}")
    if auth not in {"READY", "CONFIGURED"}:
        blockers.append(f"capability:{capability}:AUTH_{auth}")
    if availability not in {"AVAILABLE", "READY"}:
        blockers.append(f"capability:{capability}:AVAILABILITY_{availability}")
    provider_id = row.get("provider_id")
    if provider_id:
        blockers.extend(_provider_observation_blockers(
            database, str(provider_id),
            str(row.get("model_id")) if row.get("model_id") else None,
        ))
    return tuple(dict.fromkeys(blockers))


def _operational_capability_blockers(
    database: FoundryDatabase,
    capability: str,
) -> tuple[str, ...]:
    """Use Fabric readiness first; retain the old registry as a fallback only."""

    from ..fabric.catalog import get_spec

    if get_spec(database, capability) is not None:
        return _fabric_capability_blockers(database, capability)
    return _legacy_operational_capability_blockers(database, capability)


def _capability_blockers(database: FoundryDatabase, step: PlanStep, *, resource_id: str | None = None) -> tuple[str, ...]:
    resource = resource_id or step.resource_id
    if not resource and step.required_capabilities:
        return ("resource_unconfigured",)
    if not step.required_capabilities:
        return ()
    from .portfolio_bridge import capability_readiness
    from ..fabric.catalog import get_spec, observed_readiness
    blockers: list[str] = []
    for capability in step.required_capabilities:
        # Fabric catalog ids are the canonical selection/readiness authority.
        # Do not let the legacy portfolio or capability registry override it.
        if get_spec(database, capability) is not None:
            readiness = observed_readiness(database, capability)
            allow_probation = capability in step.probation_capabilities
            if readiness.state.value != "READY":
                blockers.append(f"{capability}:{readiness.state.value}")
            trust_state = readiness.trust_state.value
            if trust_state == "DISABLED":
                blockers.append(f"{capability}:DISABLED")
            elif trust_state == "PROBATION" and not allow_probation:
                blockers.append(f"{capability}:PROBATION")
            blockers.extend(_fabric_capability_blockers(
                database, capability, allow_probation=allow_probation,
            ))
            continue
        readiness = capability_readiness(database, resource_id=str(resource), capability=capability)
        if not readiness.get("ready", False) or str(readiness.get("state") or "UNKNOWN") in {"UNREGISTERED", "UNKNOWN"}:
            blockers.append(f"{capability}:{readiness.get('state') or 'UNKNOWN'}")
        blockers.extend(_legacy_operational_capability_blockers(database, capability))
    return tuple(dict.fromkeys(blockers))


def plan_ready_steps(database: FoundryDatabase, objective_id: str, *, resource_id: str | None = None) -> list[PlanStep]:
    plan = active_plan(database, objective_id)
    if plan is None:
        return []
    completed = {step.step_id for step in plan.steps if step.status == "COMPLETED"}
    ready: list[PlanStep] = []
    for step in plan.steps:
        if step.status in _TERMINAL_STEP or step.status in {"RUNNING", "IN_PROGRESS"}:
            continue
        if any(dep not in completed for dep in step.dependencies):
            continue
        if _capability_blockers(database, step, resource_id=resource_id):
            continue
        ready.append(step)
    return ready


def generate_missions_for_objective(
    database: FoundryDatabase,
    objective_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    max_new: int = 1,
    source_id: str = "v8-executive-autonomy",
) -> list[dict[str, Any]]:
    """Generate bounded portfolio work, never a parallel mission engine."""
    _gate(database, controller)
    state = rebuild_executive_state(database)
    objective = state.objectives.get(objective_id)
    if objective is None:
        raise ValueError(f"unknown objective: {objective_id}")
    if objective.status not in _MISSION_GENERATION_OBJECTIVE:
        return []
    plan = active_plan(database, objective_id)
    if plan is None:
        return []
    from ..evolution.executive_portfolio import register_work_item, rebuild_portfolio_state
    portfolio = rebuild_portfolio_state(database)
    existing = set(portfolio.work_items)
    requests: list[dict[str, Any]] = []
    max_missions = int(_number(objective.budget.get("max_missions"), 0))
    if max_missions > 0 and len(objective.mission_ids) >= max_missions:
        _append(database, "executive.mission_generation_blocked", {"objective_id": objective_id, "reason": "objective mission budget exhausted", "max_missions": max_missions}, key=f"executive.mission_generation_blocked:budget:{objective_id}", source_id=source_id, correlation_id=objective_id)
        return requests
    for step in plan_ready_steps(database, objective_id):
        if len(requests) >= max(0, int(max_new)):
            break
        directive_id = str(objective.provenance.get("directive_id") or "")
        base_item_id = f"owner-goal:{directive_id}" if directive_id else f"executive-step:{objective_id}:{step.step_id}"
        suffix = f":retry:{step.retries}" if step.retries else ""
        item_id = f"{base_item_id}{suffix}"
        if item_id in existing:
            continue
        blockers = _capability_blockers(database, step)
        if blockers:
            _append(database, "executive.mission_generation_blocked", {"objective_id": objective_id, "step_id": step.step_id, "blockers": list(blockers)}, key=f"executive.mission_generation_blocked:{item_id}", source_id=source_id, correlation_id=objective_id)
            continue
        item_metadata: dict[str, Any] = {
            "executive_objective_id": objective_id, "executive_plan_id": plan.plan_id,
            "executive_step_id": step.step_id,
            "required_capabilities": list(dict.fromkeys((
                *step.required_capabilities,
                *(
                    str(value)
                    for value in (objective.metadata.get("required_capabilities") or ())
                    if str(value).strip()
                ),
                *((("cap.workflow.run",) if objective.metadata.get("recipe_id")
                   else ())),
            ))),
            "probation_capabilities": list(step.probation_capabilities),
            "goal_id": objective.provenance.get("directive_id"),
            "success_criteria": list(objective.success_criteria),
            "constraints": list(objective.constraints),
            "time_horizon": objective.deadline,
            "recipe_id": objective.metadata.get("recipe_id"),
            "recipe_params": dict(objective.metadata.get("recipe_params") or {}),
        }
        intent_block = objective.metadata.get("intent")
        if isinstance(intent_block, Mapping):
            # A normalized mission intent travels with the work item so the
            # materialization boundary can resolve the workflow/action plan
            # itself instead of requiring a declared recipe id.
            item_metadata["intent"] = dict(intent_block)
        register_work_item(
            database, item_id=item_id, kind="OWNER_GOAL", objective=step.description,
            controller=controller, goal_ids=(directive_id or objective_id,), project_id=objective.metadata.get("project_id"),
            resource_id=step.resource_id or objective.resource_id, expected_value=max(0.9, objective.expected_impact),
            urgency=max(0.2, objective.urgency), confidence=max(0.8, objective.confidence), effort=objective.effort,
            risk=objective.risk, reversibility=objective.reversibility, opportunity_cost=objective.opportunity_cost,
            dependencies=tuple(f"executive-step:{objective_id}:{dep}" for dep in step.dependencies),
            due_at=objective.deadline, owner_only=False, requires_verifier=True,
            evidence_refs=step.evidence_refs, provenance={**objective.provenance, "objective_id": objective_id, "plan_id": plan.plan_id, "step_id": step.step_id},
            metadata=item_metadata,
            source_type="supervisor", source_id=source_id,
        )
        request = {"request_id": item_id, "objective_id": objective_id, "plan_id": plan.plan_id, "step_id": step.step_id, "item_id": item_id, "status": "REQUESTED"}
        _append(database, "executive.mission_requested", request, key=f"executive.mission_requested:{item_id}", source_id=source_id, correlation_id=objective_id)
        requests.append(request)
    return requests


def bind_mission_to_step(database: FoundryDatabase, objective_id: str, step_id: str, mission_id: str, *, controller: str = SUPERVISOR_CONTROLLER) -> PlanStep:
    _gate(database, controller)
    plan = active_plan(database, objective_id)
    if plan is None:
        raise ValueError(f"no active plan for {objective_id}")
    target = next((step for step in plan.steps if step.step_id == step_id), None)
    if target is None:
        raise ValueError(f"unknown plan step: {step_id}")
    if target.mission_id == mission_id:
        return target
    changed = PlanStep(**{field: getattr(target, field) for field in PlanStep.__dataclass_fields__} | {"mission_id": mission_id, "status": "RUNNING"})
    steps = tuple(changed if step.step_id == step_id else step for step in plan.steps)
    updated = PlanRecord(plan_id=plan.plan_id, objective_id=plan.objective_id, steps=steps, critical_path=plan.critical_path, status=plan.status, version=plan.version + 1)
    _append(database, "executive.plan_updated", updated.to_payload(), key=f"executive.plan_updated:{plan.plan_id}:{updated.version}", correlation_id=objective_id)
    objective = rebuild_executive_state(database).objectives.get(objective_id)
    if objective is not None and mission_id not in objective.mission_ids:
        update_objective(database, objective_id, {"mission_ids": [*objective.mission_ids, mission_id]}, controller=controller)
    return changed


def _mission_truth(database: FoundryDatabase, mission_id: str) -> dict[str, Any]:
    mission = database.row("missions", "mission_id", mission_id)
    result: dict[str, Any] = {"mission_id": mission_id, "state": "UNKNOWN", "status": "UNKNOWN", "evidence_refs": []}
    if mission is None:
        result["status"] = "BLOCKED"
        result["reason"] = "mission is not present in the authoritative projection"
        return result
    state = str(mission.get("state") or "UNKNOWN")
    result.update({"state": state, "objective": mission.get("objective"), "updated_at": mission.get("updated_at")})
    if state == "COMPLETE":
        result["status"] = "COMPLETED"
    elif state in {"FAILED", "CANCELLED"}:
        result["status"] = "FAILED"
    elif state == "BLOCKED":
        result["status"] = "BLOCKED"
    else:
        result["status"] = "RUNNING"
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT verdict_id, subject_id, status, reason, blocking_dependency, issued_at "
            "FROM verification_verdicts WHERE mission_id = ? ORDER BY rowid", (mission_id,)
        ).fetchall()
    if rows:
        latest = dict(rows[-1])
        result["verdict"] = latest
        result["evidence_refs"] = [str(latest.get("verdict_id") or "")]
        if latest.get("status") == "FAIL":
            result["status"] = "FAILED"
        if latest.get("status") in {"BLOCKED", "NOT_TESTED"} and result["status"] == "RUNNING":
            result["status"] = "BLOCKED"
    return result


def observe_mission_outcome(
    database: FoundryDatabase,
    mission_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Reconcile authoritative mission/verifier truth into the executive DAG."""
    _gate(database, controller)
    truth = _mission_truth(database, mission_id)
    mission_row = database.row("missions", "mission_id", mission_id) or {}
    created_from = str(mission_row.get("created_from") or "")
    if created_from.startswith("portfolio:") and truth.get("status") in {"COMPLETED", "FAILED", "BLOCKED"}:
        from ..evolution.executive_portfolio import rebuild_portfolio_state, set_work_status
        item_id = created_from.split("portfolio:", 1)[1]
        item = rebuild_portfolio_state(database).work_items.get(item_id)
        if item is not None and item.status != str(truth["status"]):
            set_work_status(database, item_id, str(truth["status"]), reason=f"mission {mission_id} observed as {truth['status']}", controller=controller, source_id="v8-executive-autonomy")
    state = rebuild_executive_state(database)
    changed_steps: list[tuple[str, str]] = []
    affected_objectives: set[str] = set()
    for plan in state.plans.values():
        for step in plan.steps:
            if step.mission_id != mission_id:
                continue
            affected_objectives.add(plan.objective_id)
            new_status = str(truth["status"])
            if new_status == "RUNNING":
                new_status = "RUNNING"
            if step.status != new_status or tuple(evidence_refs) and not set(evidence_refs).issubset(step.evidence_refs):
                refs = tuple(dict.fromkeys((*step.evidence_refs, *tuple(evidence_refs), *tuple(truth.get("evidence_refs") or ()))))
                changed_steps.append((plan.plan_id, step.step_id))
                replacement = replace(step, status=new_status, evidence_refs=refs)
                updated_steps = tuple(replacement if row.step_id == step.step_id else row for row in plan.steps)
                updated = PlanRecord(plan_id=plan.plan_id, objective_id=plan.objective_id, steps=updated_steps, critical_path=plan.critical_path, status=plan.status, version=plan.version + 1)
                _append(database, "executive.plan_updated", updated.to_payload(), key=f"executive.plan_updated:{plan.plan_id}:{updated.version}", correlation_id=plan.objective_id)
                break
    # A terminal mission result is not merely an observation for the next
    # polling tick: reconcile objective progress immediately so the executive
    # state, operational projection, portfolio status, and memory writeback
    # share one durable outcome boundary.
    if truth.get("status") in {"COMPLETED", "FAILED", "BLOCKED"}:
        for objective_id in sorted(affected_objectives):
            try:
                evaluate_objective_progress(database, objective_id, controller=controller)
            except (ValueError, RuntimeError):
                # The mission/verifier truth remains authoritative even if a
                # malformed historical plan cannot be evaluated.
                continue
    return {**truth, "changed_steps": [{"plan_id": pid, "step_id": sid} for pid, sid in changed_steps]}


def reconcile_terminal_mission_outcomes(
    database: FoundryDatabase,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
) -> list[str]:
    """Replay terminal child-mission truth into objective state exactly once.

    A worker can be hard-killed after MissionLoop atomically completes a
    mission but before the worker records the parent plan/objective outcome.
    Rebuild the bindings from ledger truth and reuse ``observe_mission_outcome``
    instead of creating a second continuation or recovery authority.
    """
    _gate(database, controller)
    state = rebuild_executive_state(database)
    candidates = sorted({
        str(step.mission_id)
        for plan in state.plans.values()
        for step in plan.steps
        if step.mission_id
        and step.status not in {"COMPLETED", "FAILED", "BLOCKED"}
    })
    reconciled: list[str] = []
    for mission_id in candidates:
        if _mission_truth(database, mission_id).get("status") not in {
            "COMPLETED", "FAILED", "BLOCKED",
        }:
            continue
        # MissionLoop can be interrupted after its atomic terminal append but
        # before WorkerLoop projects the accepted outcome into calibration and
        # World Intelligence.  Recover that boundary before advancing the
        # parent plan so a later cycle cannot make objective completion outrun
        # final-outcome truth or lose the learning writeback permanently.
        from ..world_model.connector import reconcile_outcome_learning

        learning = reconcile_outcome_learning(
            database,
            mission_id=mission_id,
            source_id="executive-terminal-reconciler",
        )
        if not learning.get("finalized"):
            continue
        result = observe_mission_outcome(
            database, mission_id, controller=controller,
        )
        if result.get("changed_steps"):
            reconciled.append(mission_id)
    return reconciled


def _evaluate_objective_progress_pure(
    database: FoundryDatabase,
    objective_id: str,
    *,
    state: ExecutiveAutonomyState | None = None,
    stale_after_seconds: float = 3600.0,
) -> ProgressEvaluation:
    """Calculate objective progress without gates, appends, or status writes."""
    current_state = state or rebuild_executive_state(database)
    objective = current_state.objectives.get(objective_id)
    if objective is None:
        raise ValueError(f"unknown objective: {objective_id}")
    plan = _active_plan_from_state(current_state, objective_id)
    if plan is None or not plan.steps:
        return ProgressEvaluation(
            objective_id, objective.status, objective.progress,
            objective.confidence, (), 0, 0, False,
            "no executable plan has been recorded", False,
        )
    completed = sum(step.status == "COMPLETED" for step in plan.steps)
    failed = sum(step.status == "FAILED" for step in plan.steps)
    blocked = sum(step.status == "BLOCKED" for step in plan.steps)
    progress = completed / len(plan.steps)
    refs = tuple(dict.fromkeys(
        ref for step in plan.steps for ref in step.evidence_refs
    ))
    updated_at = _dt(objective.updated_at)
    stale = bool(
        updated_at
        and (datetime.now(timezone.utc) - updated_at).total_seconds()
        > stale_after_seconds
        and progress < 1.0
    )
    prior_evaluation = current_state.progress.get(objective_id)
    regressed = prior_evaluation is not None and progress < prior_evaluation.progress
    if completed == len(plan.steps):
        status = "COMPLETED"
        explanation = "every planned step has independent completion evidence"
    elif failed:
        status = "FAILED"
        explanation = (
            f"{failed} planned step(s) failed; continuation policy must "
            "choose repair or escalation"
        )
    elif blocked:
        status = "BLOCKED"
        explanation = (
            f"{blocked} planned step(s) are blocked by an authoritative "
            "dependency"
        )
    elif stale:
        status = "WAITING"
        explanation = (
            "no fresh evidence has advanced the objective within the stale "
            "threshold"
        )
    elif regressed:
        status = "REGRESSED"
        explanation = (
            f"evidence-backed progress regressed from "
            f"{prior_evaluation.progress:.3f} to {progress:.3f}; "
            "reassessment is required"
        )
    else:
        status = "ACTIVE"
        explanation = f"{completed}/{len(plan.steps)} planned step(s) completed"
    return ProgressEvaluation(
        objective_id, status, progress, _clamp(objective.confidence), refs,
        completed, failed + blocked, stale, explanation, regressed,
    )


def evaluate_objective_progress(
    database: FoundryDatabase,
    objective_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stale_after_seconds: float = 3600.0,
) -> ProgressEvaluation:
    _gate(database, controller)
    state = rebuild_executive_state(database)
    objective = state.objectives.get(objective_id)
    evaluation = _evaluate_objective_progress_pure(
        database, objective_id, state=state,
        stale_after_seconds=stale_after_seconds,
    )
    record_objective_progress(database, evaluation, controller=controller)
    if (
        objective is not None
        and evaluation.status != objective.status
        and evaluation.status in {"COMPLETED", "FAILED", "BLOCKED", "WAITING", "ACTIVE"}
    ):
        try:
            set_objective_status(
                database, objective_id, evaluation.status, controller=controller,
                reason=evaluation.explanation,
                evidence_refs=evaluation.evidence_refs,
            )
        except ValueError:
            pass
    return evaluation


def _failure_count(database: FoundryDatabase, objective_id: str, step_id: str) -> int:
    count = 0
    for event in database.events(event_types=("executive.continuation_decided",)):
        if event.payload.get("objective_id") == objective_id and event.payload.get("next_step_id") == step_id and event.payload.get("action") == "RETRY":
            count += 1
    return count


def record_escalation(
    database: FoundryDatabase,
    *,
    objective_id: str,
    decision: str,
    why: str,
    evidence_refs: Sequence[str] = (),
    options: Sequence[str] = (),
    recommendation: str = "",
    delay_consequence: str = "",
    controller: str = SUPERVISOR_CONTROLLER,
    escalation_id: str | None = None,
) -> EscalationRecord:
    _gate(database, controller)
    eid = escalation_id or f"escalation:{objective_id}:{_hash({'decision': decision, 'why': why, 'evidence': list(evidence_refs)})}"
    prior = rebuild_executive_state(database).escalations.get(eid)
    if prior is not None:
        return prior
    record = EscalationRecord(eid, objective_id, decision, why, _tuple(evidence_refs), _tuple(options), recommendation, delay_consequence)
    _append(database, "executive.escalation_opened", record.to_payload(), key=f"executive.escalation_opened:{eid}", correlation_id=objective_id)
    return record


def resolve_escalation(database: FoundryDatabase, escalation_id: str, *, resolution: str, controller: str = SUPERVISOR_CONTROLLER) -> EscalationRecord:
    _gate(database, controller)
    prior = rebuild_executive_state(database).escalations.get(escalation_id)
    if prior is None:
        raise ValueError(f"unknown escalation: {escalation_id}")
    if prior.status == "RESOLVED":
        return prior
    payload = {**prior.to_payload(), "status": "RESOLVED", "resolution": resolution}
    _append(database, "executive.escalation_resolved", payload, key=f"executive.escalation_resolved:{escalation_id}", correlation_id=prior.objective_id)
    return EscalationRecord(**{field: getattr(prior, field) for field in EscalationRecord.__dataclass_fields__} | {"status": "RESOLVED"})


_PRIORITY_WEIGHTS = {
    "owner_priority": 0.19,
    "deadline": 0.12,
    "expected_impact": 0.12,
    "dependency_value": 0.09,
    "urgency": 0.10,
    "risk": 0.05,
    "effort": 0.07,
    "resource_availability": 0.07,
    "opportunity_cost": 0.04,
    "failure_history": 0.04,
    "stale_age": 0.03,
    "blocking_value": 0.04,
    "confidence": 0.02,
    "reversibility": 0.02,
}


def _deadline_factor(deadline: str | None, *, now: datetime) -> float:
    when = _dt(deadline)
    if when is None:
        return 0.0
    seconds = (when - now).total_seconds()
    if seconds <= 0:
        return 1.0
    return _clamp(1.0 - seconds / (14 * 86400.0))


def _objective_blockers(
    database: FoundryDatabase,
    objective: ObjectiveRecord,
    *,
    state: ExecutiveAutonomyState | None = None,
    plan: PlanRecord | None = None,
) -> tuple[str, ...]:
    current_state = state or rebuild_executive_state(database)
    blockers: list[str] = []
    veto = matching_veto_reason(database, {"GLOBAL", "executive", "objectives", objective.objective_id})
    if veto:
        blockers.append(f"veto:{veto}")
    for dependency in objective.dependencies:
        row = current_state.objectives.get(dependency)
        if row is None:
            blockers.append(f"missing_dependency:{dependency}")
        elif row.status != "COMPLETED":
            blockers.append(f"dependency:{dependency}:{row.status}")
    plan = plan if plan is not None else _active_plan_from_state(
        current_state, objective.objective_id
    )
    if plan is not None:
        for step in plan.steps:
            blockers.extend(_capability_blockers(database, step))
    if objective.resource_id is None and objective.required_capabilities:
        blockers.append("resource_unconfigured")
    return tuple(dict.fromkeys(blockers))


def _objective_has_retryable_failure(
    database: FoundryDatabase,
    objective: ObjectiveRecord,
) -> bool:
    """A FAILED objective may be reopened only for a bounded retry."""
    plan = active_plan(database, objective.objective_id)
    if plan is None:
        return False
    return any(
        step.status == "FAILED"
        and _failure_count(database, objective.objective_id, step.step_id) < step.max_retries
        for step in plan.steps
    )
def _objective_has_failed_step(
    database: FoundryDatabase,
    objective: ObjectiveRecord,
    *,
    plan: PlanRecord | None = None,
) -> bool:
    plan = plan if plan is not None else active_plan(database, objective.objective_id)
    return bool(plan and any(step.status == "FAILED" for step in plan.steps))


def _objective_failure_history(
    database: FoundryDatabase,
    objective_id: str,
    *,
    events: Sequence[Event] | None = None,
) -> float:
    failures = 0
    attempts = 0
    source_events = (
        events
        if events is not None
        else database.events(event_type_prefixes=("executive.",))
    )
    for event in source_events:
        if event.event_type in {"executive.continuation_decided", "executive.objective_progress_recorded"} and event.payload.get("objective_id") == objective_id:
            attempts += 1
            if event.payload.get("action") in {"RETRY", "ESCALATE"} or event.payload.get("status") == "FAILED":
                failures += 1
    return _clamp(failures / max(1, attempts))


def rank_objectives(
    database: FoundryDatabase,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    now: datetime | str | None = None,
    include_terminal: bool = False,
    _state: ExecutiveAutonomyState | None = None,
    _events: Sequence[Event] | None = None,
) -> list[PriorityScore]:
    """Rank active objectives with a deterministic, explainable factor vector."""
    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("only the Supervisor can rank executive objectives")
    current = _dt(now) or datetime.now(timezone.utc)
    executive_events = (
        list(_events)
        if _events is not None
        else database.events(event_type_prefixes=("executive.",))
    )
    state = _state or rebuild_executive_state(
        database, _events=executive_events
    )
    plans_by_objective: dict[str, PlanRecord] = {}
    for plan in state.plans.values():
        if plan.status != "ACTIVE":
            continue
        prior = plans_by_objective.get(plan.objective_id)
        if prior is None or (plan.version, plan.updated_at) > (prior.version, prior.updated_at):
            plans_by_objective[plan.objective_id] = plan
    failure_history: dict[str, float] = {}
    attempts_by_objective: dict[str, int] = {}
    failures_by_objective: dict[str, int] = {}
    for event in executive_events:
        if event.event_type not in {
            "executive.continuation_decided",
            "executive.objective_progress_recorded",
        }:
            continue
        objective_id = str(event.payload.get("objective_id") or "")
        if not objective_id:
            continue
        attempts_by_objective[objective_id] = attempts_by_objective.get(objective_id, 0) + 1
        if (
            event.payload.get("action") in {"RETRY", "ESCALATE"}
            or event.payload.get("status") == "FAILED"
        ):
            failures_by_objective[objective_id] = failures_by_objective.get(objective_id, 0) + 1
    for objective_id, attempts in attempts_by_objective.items():
        failure_history[objective_id] = _clamp(
            failures_by_objective.get(objective_id, 0) / max(1, attempts)
        )
    try:
        from .goals import active_priorities
        repriorities = active_priorities(database, targets=set(state.objectives))
    except Exception:
        repriorities = []
    explicit = {str(row.get("target")): _number(row.get("value")) for row in repriorities}
    descendants: dict[str, int] = {oid: 0 for oid in state.objectives}
    for row in state.objectives.values():
        for dependency in row.dependencies:
            if dependency in descendants:
                descendants[dependency] += 1
    result: list[PriorityScore] = []
    for objective in state.objectives.values():
        if (
            not include_terminal
            and objective.status in _TERMINAL_OBJECTIVE
            and not (
                objective.status == "FAILED"
                and _objective_has_failed_step(
                    database, objective,
                    plan=plans_by_objective.get(objective.objective_id),
                )
            )
        ):
            continue
        blockers = _objective_blockers(
            database, objective, state=state,
            plan=plans_by_objective.get(objective.objective_id),
        )
        age = 0.0
        updated = _dt(objective.updated_at)
        if updated is not None:
            age = _clamp((current - updated).total_seconds() / (7 * 86400.0))
        owner_priority = _clamp(objective.priority / 3.0 + explicit.get(objective.objective_id, 0.0), -1.0, 1.0)
        factors = {
            "owner_priority": owner_priority,
            "deadline": _deadline_factor(objective.deadline, now=current),
            "expected_impact": _clamp(objective.expected_impact),
            "dependency_value": _clamp(descendants.get(objective.objective_id, 0) / max(1.0, len(state.objectives))),
            "urgency": _clamp((objective.urgency + 1.0) / 2.0),
            "risk": _clamp(objective.risk),
            "effort": 1.0 - _clamp(objective.effort),
            "resource_availability": 0.0 if "resource_unconfigured" in blockers else 1.0,
            "opportunity_cost": 1.0 - _clamp(objective.opportunity_cost),
            "failure_history": 1.0 - failure_history.get(objective.objective_id, 0.0),
            "stale_age": age,
            "blocking_value": _clamp(objective.blocking_value),
            "confidence": _clamp(objective.confidence),
            "reversibility": _clamp(objective.reversibility),
        }
        score = sum(_PRIORITY_WEIGHTS[name] * factors[name] for name in _PRIORITY_WEIGHTS)
        eligible = not blockers and (
            objective.status in _ACTIVE_OBJECTIVE
            or (
                objective.status == "FAILED"
                and _objective_has_failed_step(
                    database, objective,
                    plan=plans_by_objective.get(objective.objective_id),
                )
            )
        )
        if not eligible:
            score = 0.0
        parts = ", ".join(f"{name}={value:.2f}" for name, value in factors.items() if value)
        explanation = f"score {score:.3f}; {parts or 'no positive factors'}"
        if blockers:
            explanation += f"; blocked by {', '.join(blockers)}"
        result.append(PriorityScore(objective.objective_id, score, factors, explanation, eligible, blockers))
    result.sort(key=lambda row: (-row.score, not row.eligible, row.objective_id))
    return result


def coordinate_objectives(
    database: FoundryDatabase,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    available_workers: int = 1,
    resource_capacity: Mapping[str, int] | None = None,
    excluded_objective_ids: Sequence[str] = (),
) -> CoordinationDecision:
    """Resolve cross-objective dependencies, contention and priority inversion."""
    _gate(database, controller)
    state = rebuild_executive_state(database)
    excluded = {str(value) for value in excluded_objective_ids}
    ranked = [
        row for row in rank_objectives(database, controller=controller)
        if row.objective_id not in excluded
    ]
    objective_ids = tuple(row.objective_id for row in ranked)
    conflicts: list[dict[str, Any]] = []
    for index, left in enumerate(ranked):
        a = state.objectives[left.objective_id]
        for right in ranked[index + 1:]:
            b = state.objectives[right.objective_id]
            same_resource = a.resource_id and a.resource_id == b.resource_id
            explicit_conflict = b.objective_id in a.conflicts_with or a.objective_id in b.conflicts_with
            if same_resource or explicit_conflict:
                conflicts.append({"left": a.objective_id, "right": b.objective_id, "reason": "shared_resource" if same_resource else "owner_declared_conflict", "winner": left.objective_id if left.score >= right.score else right.objective_id})
    selected = next((row.objective_id for row in ranked if row.eligible), None)
    capacity = {str(k): max(0, int(v)) for k, v in dict(resource_capacity or {}).items()}
    if selected is not None:
        chosen = state.objectives[selected]
        if available_workers < 1:
            selected = None
        elif chosen.resource_id and chosen.resource_id in capacity and capacity[chosen.resource_id] <= 0:
            selected = None
    reason = "highest explainable eligible priority"
    if selected is None:
        reason = "no objective is executable with current dependencies, capability or capacity"
    decision_id = f"coordination:{_hash({'objectives': objective_ids, 'selected': selected, 'capacity': capacity, 'workers': available_workers, 'conflicts': conflicts})}"
    prior = rebuild_executive_state(database).coordination.get(decision_id)
    if prior is not None:
        return prior
    decision = CoordinationDecision(decision_id, objective_ids, selected, reason, tuple(conflicts), {"available_workers": max(0, int(available_workers))}, {"resource_capacity": capacity})
    _append(database, "executive.coordination_decided", decision.to_payload(), key=f"executive.coordination_decided:{decision_id}")
    return decision


def decide_continuation(
    database: FoundryDatabase,
    objective_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    wake_on: Sequence[str] = ("mission.completed", "mission.failed", "capability.state_changed", "owner_directive.recorded"),
) -> ContinuationDecision:
    """Choose the next autonomous action after current evidence, without prompting."""
    _gate(database, controller)
    state = rebuild_executive_state(database)
    objective = state.objectives.get(objective_id)
    if objective is None:
        raise ValueError(f"unknown objective: {objective_id}")
    progress = evaluate_objective_progress(database, objective_id, controller=controller)
    plan = active_plan(database, objective_id)
    action = "WAIT"
    reason = progress.explanation
    next_step_id: str | None = None
    retry_number = 0
    escalation_id: str | None = None
    if progress.status == "COMPLETED":
        action = "CLOSE"
        reason = "objective has complete evidence and must not generate more work"
    elif progress.status == "REGRESSED":
        action = "REASSESS"
        reason = "new evidence reduced measured progress; reassess the plan before more execution"
    elif plan is None:
        action = "PLAN"
        reason = "objective has no active dependency-aware plan"
    else:
        failed_step = next((step for step in plan.steps if step.status == "FAILED"), None)
        blocked_step = next((step for step in plan.steps if step.status == "BLOCKED"), None)
        if failed_step is not None:
            next_step_id = failed_step.step_id
            retry_number = _failure_count(database, objective_id, failed_step.step_id)
            if retry_number < failed_step.max_retries:
                action = "RETRY"
                reason = f"step {failed_step.step_id} failed; retry {retry_number + 1}/{failed_step.max_retries} remains within budget"
                replacement = replace(failed_step, status="READY", mission_id=None, retries=failed_step.retries + 1)
                updated = PlanRecord(plan_id=plan.plan_id, objective_id=plan.objective_id, steps=tuple(replacement if row.step_id == failed_step.step_id else row for row in plan.steps), critical_path=plan.critical_path, status=plan.status, version=plan.version + 1)
                _append(database, "executive.plan_updated", updated.to_payload(), key=f"executive.plan_updated:{plan.plan_id}:{updated.version}", correlation_id=objective_id)
                if objective.status == "FAILED":
                    set_objective_status(database, objective_id, "ACTIVE", controller=controller, reason="bounded retry reopened the objective", evidence_refs=failed_step.evidence_refs)
            else:
                action = "ESCALATE"
                reason = f"step {failed_step.step_id} exhausted bounded retry budget"
                escalation = record_escalation(database, objective_id=objective_id, decision="retry budget exhausted", why=reason, evidence_refs=failed_step.evidence_refs, options=("owner changes strategy", "owner grants a new capability or budget", "suspend objective"), recommendation="suspend until owner direction or a materially different capability is available", delay_consequence="objective remains incomplete and downstream work stays blocked", controller=controller)
                escalation_id = escalation.escalation_id
        elif blocked_step is not None:
            action = "WAIT"
            next_step_id = blocked_step.step_id
            reason = f"step {blocked_step.step_id} is blocked by an authoritative capability or dependency; wake on state change"
        else:
            ready = plan_ready_steps(database, objective_id)
            if ready:
                action = "CONTINUE"
                next_step_id = ready[0].step_id
                reason = f"continue with dependency-ready step {next_step_id}"
            elif progress.stale:
                action = "REPRIORITIZE"
                reason = "objective is stale without evidence; recompute priority before creating more work"
            else:
                action = "WAIT"
                reason = "no ready step is available; wait for mission evidence or capability recovery"
    decision = ContinuationDecision(objective_id, action, reason, progress.evidence_refs, next_step_id, retry_number, escalation_id, _tuple(wake_on))
    prior = rebuild_executive_state(database).continuations.get(objective_id)
    if prior is not None and prior.to_payload() == decision.to_payload():
        return prior
    _append(database, "executive.continuation_decided", decision.to_payload(), key=f"executive.continuation_decided:{objective_id}:{_hash(decision.to_payload())}", correlation_id=objective_id)
    return decision


def propose_operational_followups(
    database: FoundryDatabase,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
    max_proposals: int = 5,
) -> list[dict[str, Any]]:
    """Admit salient operational discoveries as non-executable proposals.

    This is the only Executive/Operational hand-off.  It records a durable,
    provenance-backed proposal and deliberately does *not* create an
    objective, portfolio item, mission, schedule, or provider call.  An owner
    directive or an existing policy must subsequently authorize any work.
    """
    _gate(database, controller, stop_path=stop_path)
    from ..operational_intelligence import project_operational_intelligence

    projection = project_operational_intelligence(
        database,
        stop_path=stop_path,
    )
    followups = list(projection.get("followups") or ())
    recurring = projection.get("recurring_work") or {}
    patterns = {
        str(row.get("fingerprint") or "")
        for row in (recurring.get("patterns") or ())
        if isinstance(row, Mapping)
    }
    eligible: list[dict[str, Any]] = []
    for followup in followups:
        if not isinstance(followup, Mapping):
            continue
        if str(followup.get("state") or "").upper() != "OPEN":
            continue
        salience = str(followup.get("salience") or "UNKNOWN").upper()
        recurrence = max(0, int(_number(followup.get("recurrence_count"))))
        if salience not in {"CREATE_WORK", "OWNER_ATTENTION", "URGENT"} and (
            recurrence < 2 or str(followup.get("fingerprint") or "") not in patterns
        ):
            continue
        eligible.append(dict(followup))
    eligible.sort(key=lambda row: (
        {"URGENT": 0, "OWNER_ATTENTION": 1, "CREATE_WORK": 2}.get(
            str(row.get("salience") or "").upper(), 3
        ),
        -max(0, int(_number(row.get("recurrence_count")))),
        str(row.get("follow_up_id") or ""),
    ))
    prior_state = rebuild_executive_state(database)
    prior_by_followup = {
        str(row.get("follow_up_id") or ""): row
        for row in prior_state.operational_followup_proposals.values()
        if str(row.get("status") or "").upper() == "PROPOSED"
    }
    proposals: list[dict[str, Any]] = []
    for followup in eligible[: max(0, int(max_proposals))]:
        followup_id = str(followup.get("follow_up_id") or "")
        if not followup_id:
            continue
        # One open operational follow-up maps to one executive proposal.  A
        # fresh projection may contain more recurrence evidence, but that is a
        # refresh of the same concern—not permission to append another work
        # request on every polling cycle.
        prior = prior_by_followup.get(followup_id)
        if prior is not None:
            proposals.append(dict(prior))
            continue
        source_events = tuple(str(value) for value in (followup.get("source_event_ids") or ()) if value)
        proposal_id = f"operational-followup:{followup_id}"
        payload = {
            "proposal_id": proposal_id,
            "follow_up_id": followup_id,
            "signal_id": followup.get("provenance", {}).get("signal_id") if isinstance(followup.get("provenance"), Mapping) else None,
            "fingerprint": followup.get("fingerprint"),
            "kind": followup.get("kind"),
            "salience": str(followup.get("salience") or "UNKNOWN").upper(),
            "summary": followup.get("summary"),
            "next_action": followup.get("next_action"),
            "source_event_ids": list(source_events),
            "evidence_refs": list(followup.get("evidence_refs") or ()),
            "mission_id": followup.get("mission_id"),
            "recurrence_count": max(0, int(_number(followup.get("recurrence_count")))),
            "status": "PROPOSED",
            "requires_owner_or_policy": True,
            "auto_create_objective": False,
            "auto_materialize": False,
            "authority": "owner_control_and_existing_executive_policy",
        }
        _append(
            database,
            "executive.operational_followup_proposed",
            payload,
            key=f"executive.operational_followup_proposed:{proposal_id}",
            source_id="v8-operational-executive-bridge",
        )
        proposals.append(payload)
    return proposals


@dataclass(frozen=True, slots=True)
class AutonomyCycle:
    cycle_id: str
    objective_id: str | None
    coordination: CoordinationDecision
    ranking: tuple[PriorityScore, ...]
    plans: tuple[PlanRecord, ...]
    mission_requests: tuple[dict[str, Any], ...]
    portfolio_decision: dict[str, Any]
    materialization: dict[str, Any]
    continuation: ContinuationDecision | None
    status: str
    reason: str
    operational_proposals: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id, "objective_id": self.objective_id,
            "coordination": self.coordination.to_payload(),
            "ranking": [row.to_payload() for row in self.ranking],
            "plans": [row.to_payload() for row in self.plans],
            "mission_requests": [dict(row) for row in self.mission_requests],
            "portfolio_decision": dict(self.portfolio_decision), "materialization": dict(self.materialization),
            "continuation": self.continuation.to_payload() if self.continuation else None,
            "status": self.status, "reason": self.reason,
            "operational_proposals": [dict(row) for row in self.operational_proposals],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AutonomyCycle":
        """Restore a previously recorded cycle without re-running it."""
        coordination_payload = _json(payload.get("coordination"))
        coordination = CoordinationDecision(
            decision_id=str(coordination_payload.get("decision_id") or ""),
            objective_ids=_tuple(coordination_payload.get("objective_ids")),
            selected_objective_id=(
                str(coordination_payload["selected_objective_id"])
                if coordination_payload.get("selected_objective_id") else None
            ),
            reason=str(coordination_payload.get("reason") or ""),
            conflicts=tuple(
                dict(row) for row in (coordination_payload.get("conflicts") or ())
                if isinstance(row, Mapping)
            ),
            concurrency=_json(coordination_payload.get("concurrency")),
            allocations=_json(coordination_payload.get("allocations")),
        )
        ranking = tuple(
            PriorityScore(
                objective_id=str(row.get("objective_id") or ""),
                score=_number(row.get("score")),
                factors={str(key): _number(value) for key, value in _json(row.get("factors")).items()},
                explanation=str(row.get("explanation") or ""),
                eligible=bool(row.get("eligible", True)),
                blockers=_tuple(row.get("blockers")),
            )
            for row in (payload.get("ranking") or ())
            if isinstance(row, Mapping)
        )
        continuation_payload = payload.get("continuation")
        continuation = None
        if isinstance(continuation_payload, Mapping):
            continuation = ContinuationDecision(
                objective_id=str(continuation_payload.get("objective_id") or ""),
                action=str(continuation_payload.get("action") or "WAIT"),
                reason=str(continuation_payload.get("reason") or ""),
                evidence_refs=_tuple(continuation_payload.get("evidence_refs")),
                next_step_id=(
                    str(continuation_payload["next_step_id"])
                    if continuation_payload.get("next_step_id") else None
                ),
                retry_number=max(0, int(_number(continuation_payload.get("retry_number")))),
                escalation_id=(
                    str(continuation_payload["escalation_id"])
                    if continuation_payload.get("escalation_id") else None
                ),
                wake_on=_tuple(continuation_payload.get("wake_on")),
            )
        return cls(
            cycle_id=str(payload.get("cycle_id") or ""),
            objective_id=(str(payload["objective_id"]) if payload.get("objective_id") else None),
            coordination=coordination,
            ranking=ranking,
            plans=tuple(
                PlanRecord.from_payload(row)
                for row in (payload.get("plans") or ())
                if isinstance(row, Mapping)
            ),
            mission_requests=tuple(
                dict(row) for row in (payload.get("mission_requests") or ())
                if isinstance(row, Mapping)
            ),
            portfolio_decision=_json(payload.get("portfolio_decision")),
            materialization=_json(payload.get("materialization")),
            continuation=continuation,
            status=str(payload.get("status") or "UNKNOWN"),
            reason=str(payload.get("reason") or ""),
            operational_proposals=tuple(
                dict(row) for row in (payload.get("operational_proposals") or ())
                if isinstance(row, Mapping)
            ),
        )


def _dispatch_capability_expansion(
    database: FoundryDatabase,
    *,
    cycle_id: str,
    excluded: set[str],
) -> list[dict[str, Any]]:
    """Dispatch every non-terminal bridge gap through the canonical engine.

    One bounded pass per objective, using the existing ``ExpansionWorkPort``
    seam and ``run_isolated_expansion_then_resume`` product glue.  Nothing here
    decides readiness: the engine's own validation, the isolated trial, its
    independent verifier and the Fabric Catalog do, and the gap bridge keeps
    the WAIT/wake/replan ownership.  A failure is recorded and absorbed so it
    cannot abort the cycle or the other objectives.

    The STOP gate is the caller's (``_gate`` resolves the database-adjacent
    sentinel before this runs) plus the engine's own sentinel check, so this
    function deliberately takes no ``stop_path``: accepting one it never used
    would advertise a gate it does not apply.
    """

    from ..fabric.gap_bridge import rebuild_gap_state
    from ..fabric.expansion_resolver import production_port
    from ..self_expansion import run_isolated_expansion_then_resume

    try:
        gaps = rebuild_gap_state(database)
    except Exception as error:  # noqa: BLE001 - a corrupt gap ledger is not a cycle abort
        return [{
            "status": "UNDISPATCHED",
            "reason": f"{type(error).__name__}: {error}"[:300],
        }]
    by_objective: dict[str, list[str]] = {}
    for gap in gaps.values():
        if gap.terminal or gap.origin_objective_id in excluded:
            continue
        by_objective.setdefault(str(gap.origin_objective_id), []).append(gap.gap_id)
    if not by_objective:
        return []
    try:
        port = production_port(database)
    except Exception as error:  # noqa: BLE001 - report, never invent an expansion
        return [{
            "status": "PORT_UNAVAILABLE",
            "reason": f"{type(error).__name__}: {error}"[:300],
            "objectives": sorted(by_objective),
        }]
    receipts: list[dict[str, Any]] = []
    try:
        for objective_id in sorted(by_objective):
            entry: dict[str, Any] = {
                "objective_id": objective_id,
                "gap_ids": list(by_objective[objective_id]),
                "cycle_id": cycle_id,
            }
            try:
                result = run_isolated_expansion_then_resume(
                    database,
                    objective_id=objective_id,
                    port=port,
                    strategy_id="reuse-first-isolated-trial",
                )
                entry.update({
                    "status": "DISPATCHED",
                    "resumed_gap_ids": list(result.resumed_gap_ids),
                    "waiting_gap_ids": list(result.waiting_gap_ids),
                    "gap_status": {
                        gap.gap_id: gap.status.value for gap in result.gaps
                    },
                })
            except Exception as error:  # noqa: BLE001 - per-objective isolation
                entry.update({
                    "status": "FAILED",
                    "reason": f"{type(error).__name__}: {error}"[:300],
                })
            try:
                database.append(Event.create(
                    "executive.self_expansion_dispatch_recorded",
                    dict(entry),
                    source_type="supervisor",
                    source_id="v9-continuous",
                    idempotency_key=(
                        f"executive.self_expansion_dispatch:{cycle_id}:{objective_id}"
                    ),
                    correlation_id=objective_id,
                ))
            except Exception:  # noqa: BLE001 - receipt failure never rewrites the pass
                pass
            receipts.append(entry)
    finally:
        try:
            port.close()
        except Exception:  # noqa: BLE001 - best-effort backend cleanup
            pass
    return receipts


def run_autonomy_cycle(
    database: FoundryDatabase,
    *,
    cycle_id: str,
    resource_id: str | None = None,
    capability: str = "write_registered_worktree_files",
    controller: str = SUPERVISOR_CONTROLLER,
    max_new_missions: int = 1,
    stop_path: str | Path | None = None,
    excluded_objective_ids: Sequence[str] = (),
    self_expansion: bool = False,
) -> AutonomyCycle:
    """Run one bounded executive pass through existing portfolio and mission authorities."""
    _gate(database, controller, stop_path=stop_path)
    # A cycle id is a durable idempotency boundary.  Replaying it after a
    # crash or duplicate scheduler tick returns the recorded decision rather
    # than creating another plan, portfolio item, or mission request.  Query
    # the immutable idempotency key directly instead of a bounded recent-event
    # window so an old cycle remains replay-safe after thousands of later
    # ledger events.
    cycle_key = f"executive.autonomy_cycle_recorded:{cycle_id}"
    with database.connect(readonly=True) as connection:
        recorded = connection.execute(
            "SELECT payload_json FROM events "
            "WHERE event_type = 'executive.autonomy_cycle_recorded' "
            "AND idempotency_key = ? ORDER BY sequence DESC LIMIT 1",
            (cycle_key,),
        ).fetchone()
    if recorded is not None:
        try:
            payload = json.loads(str(recorded[0]))
            return AutonomyCycle.from_payload(payload)
        except (TypeError, ValueError, KeyError) as error:
            # A corrupt receipt is an authority-integrity failure.  Do not
            # rerun and risk a conflicting append under the same idempotency
            # key; surface the failure for the Supervisor to reconcile.
            raise RuntimeError("recorded autonomy cycle payload is invalid") from error
    # V12 bridge reconciliation is a bounded Supervisor pass over durable
    # gaps.  It does not infer readiness: the Fabric Catalog is re-read and a
    # prior wake/replan receipt makes replay a no-op.
    from ..fabric.gap_bridge import (
        bridge_missing_plan_capabilities,
        reconcile_ready_gaps,
    )
    reconcile_ready_gaps(database)
    # Registration declares a resource's initial capability readiness; the
    # durable capability plane treats a missing record as UNKNOWN/not-ready,
    # so project the declared state once here.  Later control-plane
    # transitions own the state and are never overwritten.
    from ..control.registration import sync_registered_resource_capabilities

    sync_registered_resource_capabilities(database, source_id="v8-executive-autonomy")
    reconcile_terminal_mission_outcomes(database, controller=controller)
    operational_proposals = tuple(propose_operational_followups(
        database,
        controller=controller,
        stop_path=stop_path,
        max_proposals=5,
    ))
    sync_owner_goals(database, controller=controller)
    state = rebuild_executive_state(database)
    excluded = {str(value) for value in excluded_objective_ids}
    plans: list[PlanRecord] = []
    for objective in state.objectives.values():
        if (
            objective.objective_id in excluded
            or objective.status in _TERMINAL_OBJECTIVE
            or active_plan(database, objective.objective_id) is not None
        ):
            continue
        step = PlanStep(
            step_id="execute", objective_id=objective.objective_id, description=objective.objective,
            required_capabilities=objective.required_capabilities or (capability,), resource_id=objective.resource_id or resource_id,
            output_contract=objective.success_criteria or ("durable outcome evidence",),
            verification_contract=objective.success_criteria or ("independent verification agrees with direct inspection",),
            max_retries=int(_number(objective.budget.get("max_retries"), 1)),
        )
        plans.append(build_plan(database, objective.objective_id, (step,), controller=controller))
    # Planner-owned, structured ``cap.*`` requirements that the canonical
    # Catalog confirms missing enter the durable gap/WAIT/expansion bridge.
    # Legacy resource capability ids stay on their existing compatibility
    # path and cannot be misclassified as Fabric gaps.
    current = rebuild_executive_state(database)
    for objective in current.objectives.values():
        if (
            objective.objective_id in excluded
            or objective.status in _TERMINAL_OBJECTIVE
        ):
            continue
        bridge_missing_plan_capabilities(database, objective.objective_id)
    # The single production caller for the capability-expansion engine.  A
    # durable bridge gap whose capability the Catalog still cannot prove is
    # dispatched through the existing bounded port; the bridge keeps
    # WAIT/wake/replan ownership, so a failed or still-waiting attempt simply
    # leaves the objective parked for the next cycle.  One failing expansion
    # never aborts the cycle or the other objectives.
    expansion_dispatch: list[dict[str, Any]] = []
    if self_expansion:
        expansion_dispatch = _dispatch_capability_expansion(
            database,
            cycle_id=cycle_id,
            excluded=excluded,
        )
    ranking = tuple(
        row for row in rank_objectives(database, controller=controller)
        if row.objective_id not in excluded
    )
    coordination = coordinate_objectives(
        database,
        controller=controller,
        excluded_objective_ids=tuple(sorted(excluded)),
    )
    selected_oid = coordination.selected_objective_id or (ranking[0].objective_id if ranking and ranking[0].eligible else None)
    requests: list[dict[str, Any]] = []
    pre_continuation: ContinuationDecision | None = None
    if selected_oid is not None:
        pre_continuation = decide_continuation(database, selected_oid, controller=controller)
        if pre_continuation.action != "CLOSE":
            requests = generate_missions_for_objective(database, selected_oid, controller=controller, max_new=max_new_missions)
    portfolio_payload: dict[str, Any] = {"decision": "WAIT", "reason": "no objective selected"}
    materialization: dict[str, Any] = {"materialized": False, "mission_id": None, "blocked_reason": None}
    continuation: ContinuationDecision | None = None
    if selected_oid is not None and (pre_continuation is None or pre_continuation.action != "CLOSE"):
        from ..evolution.executive_portfolio import claim_selection, release_selection_claim, select_next_work
        from .portfolio_bridge import materialize_portfolio_selection
        decision = select_next_work(
            database,
            loop_id=f"executive:{cycle_id}",
            resource_id=resource_id or state.objectives[selected_oid].resource_id,
            capability=capability,
            source_id="v8-executive-autonomy",
            objective_id=selected_oid,
        )
        portfolio_payload = decision.to_payload()
        if decision.kind == "SELECT" and decision.entity_id:
            resolved_resource = str(decision.selected_item.get("resource_id") or resource_id or state.objectives[selected_oid].resource_id or "")
            materialization = materialize_portfolio_selection(database, decision=portfolio_payload, resource_id=resolved_resource, capability=capability, source_id="v8-executive-autonomy")
            if materialization.get("materialized"):
                try:
                    claim_selection(database, decision, controller=controller, source_id="v8-executive-autonomy")
                finally:
                    try:
                        release_selection_claim(database, decision.entity_id, controller=controller, source_id="v8-executive-autonomy")
                    except Exception:
                        pass
                item = decision.selected_item
                item_meta = _json(item.get("metadata"))
                if item_meta.get("executive_objective_id") == selected_oid and item_meta.get("executive_step_id"):
                    bind_mission_to_step(database, selected_oid, str(item_meta["executive_step_id"]), str(materialization.get("mission_id")), controller=controller)
        continuation = decide_continuation(database, selected_oid, controller=controller)
    elif pre_continuation is not None:
        continuation = pre_continuation
    status = "MISSION_MATERIALIZED" if materialization.get("materialized") else ("WAITING" if portfolio_payload.get("decision") != "SELECT" else "BLOCKED")
    reason = str(materialization.get("blocked_reason") or portfolio_payload.get("reason") or "cycle completed")
    if continuation is not None and continuation.action == "CLOSE":
        status, reason = "CLOSED", continuation.reason
    cycle = AutonomyCycle(
        cycle_id, selected_oid, coordination, ranking, tuple(plans),
        tuple(requests), portfolio_payload, materialization, continuation,
        status, reason, operational_proposals,
    )
    _append(database, "executive.autonomy_cycle_recorded", cycle.to_payload(), key=f"executive.autonomy_cycle_recorded:{cycle_id}", source_id="v8-executive-autonomy", correlation_id=selected_oid)
    return cycle


def build_executive_briefing(
    database: FoundryDatabase,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    limit: int = 20,
    _state: ExecutiveAutonomyState | None = None,
    _ranked: Sequence[PriorityScore] | None = None,
    _events: Sequence[Event] | None = None,
) -> ExecutiveBriefing:
    """Build an owner-readable briefing from durable decisions and evidence."""
    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("only the Supervisor can build executive briefings")
    state = _state or rebuild_executive_state(database, _events=_events)
    ranked = list(_ranked) if _ranked is not None else rank_objectives(
        database, controller=controller, _state=state, _events=_events
    )
    advanced: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for objective in state.objectives.values():
        evaluation = state.progress.get(objective.objective_id)
        if evaluation is None:
            continue
        row = {"objective_id": objective.objective_id, "objective": objective.objective, "progress": evaluation.progress, "status": evaluation.status, "explanation": evaluation.explanation, "evidence_refs": list(evaluation.evidence_refs)}
        if evaluation.status == "COMPLETED" or evaluation.progress > objective.progress:
            advanced.append(row)
        if evaluation.status == "FAILED":
            failed.append(row)
        if evaluation.status in {"BLOCKED", "WAITING"}:
            blockers.append(row)
    decisions = [row.to_payload() for row in state.continuations.values() if row.action not in {"WAIT"}]
    decisions.extend({"decision_id": row.decision_id, "selected_objective_id": row.selected_objective_id, "reason": row.reason} for row in state.coordination.values())
    owner_actions: list[dict[str, Any]] = []
    for escalation in state.escalations.values():
        if escalation.status == "OPEN":
            owner_actions.append({"escalation_id": escalation.escalation_id, "objective_id": escalation.objective_id, "decision": escalation.decision, "recommendation": escalation.recommendation, "options": list(escalation.options)})
    next_actions: list[dict[str, Any]] = []
    for score in ranked:
        if score.eligible and score.score > 0:
            next_actions.append({"objective_id": score.objective_id, "score": score.score, "action": "continue or generate next ready mission", "why": score.explanation})
    from ..mission.verification import final_outcome_projection

    outcome_projection = final_outcome_projection(database, limit=limit)
    outcome_rows = tuple(
        {
            **dict(item.get("briefing") or {}),
            "outcome_id": item.get("outcome_id"),
            "mission_id": item.get("mission_id"),
            "objective_id": item.get("objective_id"),
            "status": item.get("status"),
            "evidence_refs": list(item.get("evidence_refs") or []),
        }
        for item in outcome_projection.get("history") or []
    )
    briefing = ExecutiveBriefing(
        briefing_id=f"briefing:{_hash({'objectives': sorted(state.objectives), 'progress': {key: value.to_payload() for key, value in state.progress.items()}, 'continuations': {key: value.to_payload() for key, value in state.continuations.items()}, 'outcomes': [row.get('outcome_id') for row in outcome_rows]})}",
        generated_at=isoformat(), advanced=tuple(advanced[:limit]), failed=tuple(failed[:limit]),
        changed_priorities=tuple({"objective_id": row.objective_id, "score": row.score, "factors": row.factors} for row in ranked[:limit]),
        decisions=tuple(decisions[:limit]), owner_actions=tuple(owner_actions[:limit]), next_actions=tuple(next_actions[:limit]), blockers=tuple(blockers[:limit]),
        outcomes=outcome_rows[:limit],
    )
    return briefing


def record_executive_briefing(database: FoundryDatabase, briefing: ExecutiveBriefing, *, controller: str = SUPERVISOR_CONTROLLER) -> ExecutiveBriefing:
    _gate(database, controller)
    _append(database, "executive.briefing_recorded", briefing.to_payload(), key=f"executive.briefing_recorded:{briefing.briefing_id}:{_hash(briefing.to_payload())}")
    return briefing


def executive_autonomy_snapshot(
    database: FoundryDatabase,
    *,
    include_briefing: bool = True,
    _state: ExecutiveAutonomyState | None = None,
    _ranking: Sequence[PriorityScore] | None = None,
    _events: Sequence[Event] | None = None,
) -> dict[str, Any]:
    """Return a stable read projection for Command Center/API consumers."""
    executive_events = (
        list(_events)
        if _events is not None
        else database.events(event_type_prefixes=("executive.",))
    )
    state = _state or rebuild_executive_state(
        database, _events=executive_events
    )
    ranking = list(_ranking) if _ranking is not None else rank_objectives(
        database, _state=state, _events=executive_events
    )
    latest_cycle = next(
        (
            event.payload for event in reversed(executive_events)
            if event.event_type == "executive.autonomy_cycle_recorded"
        ),
        None,
    )
    briefing = (
        build_executive_briefing(
            database, _state=state, _ranked=ranking,
            _events=executive_events,
        )
        if include_briefing else None
    )
    from ..mission.verification import final_outcome_projection

    # V12 campaign state is a subordinate aggregate over this Executive
    # objective projection.  Keep the owner snapshot read-only and additive;
    # no campaign event is emitted while rendering it.
    try:
        from .campaigns import campaign_snapshot
        campaign_projection = campaign_snapshot(database)
    except Exception as error:  # noqa: BLE001 - malformed optional history
        campaign_projection = {
            "schema": "v12.campaign-runtime.v1",
            "campaigns": {},
            "error": f"campaign projection unavailable: {type(error).__name__}",
        }

    return {
        "objectives": {key: value.to_payload() for key, value in state.objectives.items()},
        "plans": {key: value.to_payload() for key, value in state.plans.items()},
        "priorities": [row.to_payload() for row in ranking],
        "continuations": {key: value.to_payload() for key, value in state.continuations.items()},
        "escalations": {key: value.to_payload() for key, value in state.escalations.items()},
        "coordination": {key: value.to_payload() for key, value in state.coordination.items()},
        "progress": {key: value.to_payload() for key, value in state.progress.items()},
        "mission_requests": {key: dict(value) for key, value in state.mission_requests.items()},
        "operational_followup_proposals": {
            key: dict(value) for key, value in state.operational_followup_proposals.items()
        },
        "latest_cycle": dict(latest_cycle or {}),
        "briefing": briefing.to_payload() if briefing else None,
        "outcome_learning": final_outcome_projection(database, limit=20),
        "campaigns": campaign_projection,
    }


__all__ = [
    "ObjectiveRecord", "PlanStep", "PlanRecord", "PriorityScore", "ProgressEvaluation",
    "ContinuationDecision", "EscalationRecord", "CoordinationDecision", "ExecutiveBriefing",
    "ExecutiveAutonomyState", "ExecutiveAutonomyService", "AutonomyCycle", "rebuild_executive_state", "create_objective",
    "sync_owner_goals", "withdraw_objective_authority", "update_objective",
    "set_objective_status", "record_objective_progress",
    "build_plan", "active_plan", "plan_ready_steps", "generate_missions_for_objective",
    "bind_mission_to_step", "observe_mission_outcome", "evaluate_objective_progress",
    "decide_continuation", "record_escalation", "resolve_escalation", "rank_objectives",
    "coordinate_objectives", "propose_operational_followups", "run_autonomy_cycle", "build_executive_briefing",
    "record_executive_briefing", "executive_autonomy_snapshot",
]
