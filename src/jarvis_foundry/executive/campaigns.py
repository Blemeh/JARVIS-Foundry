"""V12 objective-scale campaign runtime.

Campaigns are a durable aggregate *over* the existing Executive objective,
plan, MissionLoop, wake, effect, verifier, and owner-control authorities.  A
campaign never executes a mission itself.  It records child bindings,
checkpointed pending work, joins, retry/deadline decisions, and recovery
receipts so the existing Supervisor/worker path can resume the exact next
unit of work after a process dies or a provider disappears.

The aggregate is intentionally event-sourced.  Schema v40 already gives the
ledger atomic append/idempotency and the objective/mission projections needed
by the runtime; a new table would create a second source of truth without
buying recovery semantics.  ``rebuild_campaign_state`` is therefore the only
state reconstruction authority in this module.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..clock import isoformat
from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..effects import EffectStateError, effect_state, freeze_effect
from ..events import Event, EventConflict, canonical_json
from ..roles.router import RoutingRequest
from .continuous import (
    WakeCondition,
    clear_wake,
    create_wake,
    rearm_wake,
    rebuild_wake_state,
    schedule_wait_for_provider,
    tick_wakes,
    trigger_wake,
)

SUPERVISOR_CONTROLLER = "supervisor"
CAMPAIGN_SCHEMA = "v12.campaign-runtime.v1"

CAMPAIGN_STATUSES = frozenset({
    "PLANNED", "EXECUTING", "WAITING", "REPLANNING", "ESCALATED",
    "STOPPED", "COMPLETED", "FAILED", "CANCELLED",
})
CHILD_STATUSES = frozenset({
    "PENDING", "READY", "RUNNING", "WAITING", "RETRYING", "UNKNOWN",
    "BLOCKED", "SUCCEEDED", "FAILED", "CANCELLED", "STOPPED",
})
TERMINAL_CHILD_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
ACCEPTED_CHILD_STATUS = "SUCCEEDED"
CHILD_WAIT_KINDS = frozenset({
    "TIME",
    "PROVIDER",
    "DEPENDENCY",
    "EFFECT_RECONCILIATION",
    "OWNER",
    "SIGNAL",
    "RESOURCE",
})

_CHILD_TRANSITIONS: dict[str, frozenset[str]] = {
    # Startup recovery can discover a mission that completed before its
    # child binding was projected.  Accepting directly from PENDING/READY is
    # therefore intentional: the mission verifier remains the sole success
    # authority and the child aggregate merely catches up to that truth.
    "PENDING": frozenset({"READY", "RUNNING", "WAITING", "UNKNOWN", "BLOCKED", "FAILED", "SUCCEEDED", "STOPPED", "CANCELLED"}),
    "READY": frozenset({"RUNNING", "WAITING", "UNKNOWN", "BLOCKED", "FAILED", "SUCCEEDED", "STOPPED", "CANCELLED"}),
    "RUNNING": frozenset({"READY", "WAITING", "UNKNOWN", "BLOCKED", "FAILED", "SUCCEEDED", "STOPPED", "CANCELLED"}),
    # A wait can release before the canonical MissionLoop materializes the
    # child mission.  WAITING -> PENDING preserves that exact boundary rather
    # than claiming READY dispatch authority for an unbound child.
    "WAITING": frozenset({"PENDING", "READY", "RUNNING", "UNKNOWN", "BLOCKED", "FAILED", "SUCCEEDED", "STOPPED", "CANCELLED"}),
    "RETRYING": frozenset({"READY", "WAITING", "BLOCKED", "STOPPED", "CANCELLED"}),
    "UNKNOWN": frozenset({"READY", "WAITING", "BLOCKED", "FAILED", "SUCCEEDED", "STOPPED", "CANCELLED"}),
    "BLOCKED": frozenset({"READY", "WAITING", "RETRYING", "FAILED", "SUCCEEDED", "STOPPED", "CANCELLED"}),
    # Canonical mission truth may arrive after a transient child failure
    # receipt (for example a verifier acceptance committed just before the
    # failure projection).  Reconciliation is allowed to promote that child
    # to SUCCEEDED, but only after _validate_child_acceptance has re-read the
    # accepted final outcome and bound-effect truth.  This is not a retry
    # shortcut; it is acceptance authority winning over stale child state.
    "FAILED": frozenset({"RETRYING", "READY", "BLOCKED", "STOPPED", "CANCELLED", "SUCCEEDED"}),
    # Resume may discover that the canonical mission completed while STOP
    # was active.  That is an authoritative reconciliation, not a synthetic
    # execution transition, so STOPPED -> SUCCEEDED is explicitly legal.
    "STOPPED": frozenset({"PENDING", "READY", "RUNNING", "WAITING", "RETRYING", "UNKNOWN", "BLOCKED", "FAILED", "SUCCEEDED", "CANCELLED"}),
    "SUCCEEDED": frozenset(),
    "CANCELLED": frozenset(),
}
_CAMPAIGN_TRANSITIONS: dict[str, frozenset[str]] = {
    "PLANNED": frozenset({"EXECUTING", "WAITING", "STOPPED", "CANCELLED"}),
    "EXECUTING": frozenset({"WAITING", "REPLANNING", "ESCALATED", "STOPPED", "COMPLETED", "FAILED", "CANCELLED"}),
    "WAITING": frozenset({"EXECUTING", "REPLANNING", "ESCALATED", "STOPPED", "COMPLETED", "FAILED", "CANCELLED"}),
    # Late authoritative acceptance can arrive while a deadline/retry
    # decision is awaiting owner direction.  Closing from these states is
    # safe because complete_campaign revalidates every winner against mission
    # and verifier truth immediately before the transition.
    "REPLANNING": frozenset({"EXECUTING", "WAITING", "ESCALATED", "STOPPED", "COMPLETED", "CANCELLED"}),
    "ESCALATED": frozenset({"REPLANNING", "EXECUTING", "STOPPED", "COMPLETED", "CANCELLED"}),
    "STOPPED": frozenset({"EXECUTING", "WAITING", "REPLANNING", "ESCALATED", "FAILED", "COMPLETED", "CANCELLED"}),
    "FAILED": frozenset({"REPLANNING", "EXECUTING", "STOPPED", "COMPLETED", "CANCELLED"}),
    "COMPLETED": frozenset(),
    "CANCELLED": frozenset(),
}


class CampaignError(ValueError):
    """A deterministic campaign contract or lifecycle refusal."""


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    try:
        return tuple(str(item) for item in value if str(item))
    except TypeError:
        return (str(value),)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _timestamp(value: Any = None) -> str:
    return str(value) if value else isoformat()


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _hash(value: Any, *, length: int = 24) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    campaign_id: str | None = None,
    source_id: str = "campaign-runtime",
) -> tuple[int, bool]:
    event = Event.create(
        event_type,
        dict(payload),
        source_type="supervisor",
        source_id=source_id,
        correlation_id=campaign_id,
        idempotency_key=key,
    )
    return database.append(event)


def _stop_reason(
    database: FoundryDatabase,
    stop_path: str | Path | None,
    *,
    campaign_id: str | None = None,
) -> str | None:
    identities = {"GLOBAL", "executive", "campaigns"}
    if campaign_id:
        identities.add(campaign_id)
        # Owner controls may target the existing objective rather than the
        # campaign id.  Include that canonical identity when it can be
        # rebuilt, while keeping STOP checks inert for a brand-new campaign.
        try:
            state = rebuild_campaign_state(database)
            campaign = state.campaigns.get(str(campaign_id))
            if campaign is not None:
                identities.add(campaign.objective_id)
                identities.update(
                    child.mission_id
                    for child in state.children.values()
                    if child.campaign_id == str(campaign_id) and child.mission_id
                )
        except Exception:
            pass
    effective_path = (
        Path(stop_path)
        if stop_path is not None
        else Path(database.path).resolve().parent / "EMERGENCY_STOP"
    )
    emergency_reason = emergency_stop_reason(
        database,
        effective_path,
        identities=identities,
    )
    if emergency_reason:
        return emergency_reason
    # STOP_MISSION/VETO_ACTION are the existing scoped owner-control
    # directives.  A campaign must not route around one merely because its
    # parent/child work is represented by a subordinate aggregate.  The
    # identity set deliberately includes GLOBAL, the campaign, its objective,
    # and bound mission ids so the canonical broker's matching semantics are
    # reused without creating a second control plane.
    try:
        return matching_veto_reason(database, identities)
    except Exception as error:  # noqa: BLE001 - control read failure is fail-closed
        return f"campaign owner-control read failed: {type(error).__name__}"


def _gate(
    database: FoundryDatabase,
    controller: str,
    *,
    stop_path: str | Path | None = None,
    campaign_id: str | None = None,
    allow_stop: bool = False,
) -> None:
    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("campaign runtime is Supervisor-owned")
    reason = _stop_reason(database, stop_path, campaign_id=campaign_id)
    if reason and not allow_stop:
        raise PermissionError(f"campaign runtime stopped: {reason}")


@dataclass(frozen=True, slots=True)
class ChildMissionRecord:
    child_id: str
    campaign_id: str
    mission_id: str | None = None
    step_id: str | None = None
    dependencies: tuple[str, ...] = ()
    sibling_group: str | None = None
    join_ids: tuple[str, ...] = ()
    provider_id: str | None = None
    routing_requirements: dict[str, Any] = field(default_factory=dict)
    routing_identity: str | None = None
    isolation_key: str | None = None
    effect_action_key: str | None = None
    effect_keys: tuple[str, ...] = ()
    status: str = "PENDING"
    wait_kind: str | None = None
    wait_reason: str | None = None
    wait_until: str | None = None
    attempt: int = 0
    max_retries: int = 1
    retries_used: int = 0
    acceptance_outcome_id: str | None = None
    acceptance_identity: str | None = None
    # STOP preserves the exact pre-stop lifecycle so RESUME does not turn a
    # waiting/replanning/escalated campaign into synthetic EXECUTING work.
    resume_status: str | None = None
    resume_wait_kind: str | None = None
    resume_wait_reason: str | None = None
    resume_wait_until: str | None = None
    last_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    created_at: str = field(default_factory=isoformat)
    updated_at: str = field(default_factory=isoformat)

    def __post_init__(self) -> None:
        if not self.child_id or not self.campaign_id:
            raise CampaignError("child_id and campaign_id are required")
        if self.status not in CHILD_STATUSES:
            raise CampaignError(f"unsupported child status: {self.status}")
        if self.wait_kind is not None and str(self.wait_kind).upper() not in CHILD_WAIT_KINDS:
            raise CampaignError(f"unsupported child wait kind: {self.wait_kind}")
        if self.resume_status is not None and self.resume_status not in CHILD_STATUSES:
            raise CampaignError(f"unsupported child resume status: {self.resume_status}")
        if self.attempt < 0 or self.max_retries < 0 or self.retries_used < 0:
            raise CampaignError("child retry counters cannot be negative")
        if bool(self.routing_requirements) != bool(self.routing_identity):
            raise CampaignError(
                "child routing requirements and identity must be supplied together"
            )
        if self.routing_requirements:
            try:
                request = RoutingRequest.from_mapping(self.routing_requirements)
            except (TypeError, ValueError) as error:
                raise CampaignError(f"invalid child routing requirements: {error}") from error
            if request.identity_hash() != self.routing_identity:
                raise CampaignError("child routing identity does not match requirements")
        if self.effect_action_key and self.effect_action_key not in self.effect_keys:
            raise CampaignError("child effect identity must be present in effect_keys")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": CAMPAIGN_SCHEMA,
            "child_id": self.child_id,
            "campaign_id": self.campaign_id,
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "dependencies": list(self.dependencies),
            "sibling_group": self.sibling_group,
            "join_ids": list(self.join_ids),
            "provider_id": self.provider_id,
            "routing_requirements": dict(self.routing_requirements),
            "routing_identity": self.routing_identity,
            "isolation_key": self.isolation_key,
            "effect_action_key": self.effect_action_key,
            "effect_keys": list(self.effect_keys),
            "status": self.status,
            "wait_kind": self.wait_kind,
            "wait_reason": self.wait_reason,
            "wait_until": self.wait_until,
            "attempt": self.attempt,
            "max_retries": self.max_retries,
            "retries_used": self.retries_used,
            "acceptance_outcome_id": self.acceptance_outcome_id,
            "acceptance_identity": self.acceptance_identity,
            "resume_status": self.resume_status,
            "resume_wait_kind": self.resume_wait_kind,
            "resume_wait_reason": self.resume_wait_reason,
            "resume_wait_until": self.resume_wait_until,
            "last_reason": self.last_reason,
            "metadata": dict(self.metadata),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ChildMissionRecord":
        p = dict(payload)
        return cls(
            child_id=str(p.get("child_id") or ""),
            campaign_id=str(p.get("campaign_id") or ""),
            mission_id=str(p["mission_id"]) if p.get("mission_id") else None,
            step_id=str(p["step_id"]) if p.get("step_id") else None,
            dependencies=_tuple(p.get("dependencies")),
            sibling_group=str(p["sibling_group"]) if p.get("sibling_group") else None,
            join_ids=_tuple(p.get("join_ids")),
            provider_id=str(p["provider_id"]) if p.get("provider_id") else None,
            routing_requirements=_mapping(p.get("routing_requirements")),
            routing_identity=(
                str(p["routing_identity"]) if p.get("routing_identity") else None
            ),
            isolation_key=str(p["isolation_key"]) if p.get("isolation_key") else None,
            effect_action_key=(
                str(p["effect_action_key"]) if p.get("effect_action_key") else None
            ),
            effect_keys=_tuple(p.get("effect_keys")),
            status=str(p.get("status") or "PENDING"),
            wait_kind=str(p["wait_kind"]) if p.get("wait_kind") else None,
            wait_reason=str(p["wait_reason"]) if p.get("wait_reason") else None,
            wait_until=str(p["wait_until"]) if p.get("wait_until") else None,
            attempt=max(0, int(float(p.get("attempt") or 0))),
            max_retries=max(0, int(float(p.get("max_retries") if p.get("max_retries") is not None else 1))),
            retries_used=max(0, int(float(p.get("retries_used") or 0))),
            acceptance_outcome_id=str(p["acceptance_outcome_id"]) if p.get("acceptance_outcome_id") else None,
            acceptance_identity=str(p["acceptance_identity"]) if p.get("acceptance_identity") else None,
            resume_status=str(p["resume_status"]) if p.get("resume_status") else None,
            resume_wait_kind=str(p["resume_wait_kind"]) if p.get("resume_wait_kind") else None,
            resume_wait_reason=str(p["resume_wait_reason"]) if p.get("resume_wait_reason") else None,
            resume_wait_until=str(p["resume_wait_until"]) if p.get("resume_wait_until") else None,
            last_reason=str(p.get("last_reason") or ""),
            metadata=_mapping(p.get("metadata")),
            version=max(1, int(float(p.get("version") or 1))),
            created_at=_timestamp(p.get("created_at")),
            updated_at=_timestamp(p.get("updated_at")),
        )


@dataclass(frozen=True, slots=True)
class CampaignRecord:
    campaign_id: str
    objective_id: str
    status: str = "EXECUTING"
    deadline: str | None = None
    max_replans: int = 1
    max_parallel_children: int = 2
    replans_used: int = 0
    retry_budget: dict[str, float] = field(default_factory=dict)
    retries_used: int = 0
    child_ids: tuple[str, ...] = ()
    join_conditions: dict[str, dict[str, Any]] = field(default_factory=dict)
    join_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkpoint_id: str | None = None
    checkpoint_version: int = 0
    wake_ids: tuple[str, ...] = ()
    stop_reason: str | None = None
    resume_status: str | None = None
    last_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    created_at: str = field(default_factory=isoformat)
    updated_at: str = field(default_factory=isoformat)

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.objective_id:
            raise CampaignError("campaign_id and objective_id are required")
        if self.status not in CAMPAIGN_STATUSES:
            raise CampaignError(f"unsupported campaign status: {self.status}")
        if self.deadline is not None and _dt(self.deadline) is None:
            raise CampaignError("campaign deadline must be an ISO-8601 timestamp")
        if self.resume_status is not None and self.resume_status not in CAMPAIGN_STATUSES:
            raise CampaignError(f"unsupported campaign resume status: {self.resume_status}")
        if (
            self.max_replans < 0
            or self.replans_used < 0
            or self.retries_used < 0
            or any(not math.isfinite(float(value)) or float(value) < 0 for value in self.retry_budget.values())
        ):
            raise CampaignError("campaign retry/replan counters and budgets must be finite and non-negative")
        if not 1 <= int(self.max_parallel_children) <= 64:
            raise CampaignError("campaign max_parallel_children is outside [1, 64]")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": CAMPAIGN_SCHEMA,
            "campaign_id": self.campaign_id,
            "objective_id": self.objective_id,
            "status": self.status,
            "deadline": self.deadline,
            "max_replans": self.max_replans,
            "max_parallel_children": self.max_parallel_children,
            "replans_used": self.replans_used,
            "retry_budget": dict(self.retry_budget),
            "retries_used": self.retries_used,
            "child_ids": list(self.child_ids),
            "join_conditions": {str(k): dict(v) for k, v in self.join_conditions.items()},
            "join_results": {str(k): dict(v) for k, v in self.join_results.items()},
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_version": self.checkpoint_version,
            "wake_ids": list(self.wake_ids),
            "stop_reason": self.stop_reason,
            "resume_status": self.resume_status,
            "last_reason": self.last_reason,
            "metadata": dict(self.metadata),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CampaignRecord":
        p = dict(payload)
        return cls(
            campaign_id=str(p.get("campaign_id") or ""),
            objective_id=str(p.get("objective_id") or ""),
            status=str(p.get("status") or "EXECUTING"),
            deadline=str(p["deadline"]) if p.get("deadline") else None,
            max_replans=max(0, int(float(p.get("max_replans") if p.get("max_replans") is not None else 1))),
            max_parallel_children=int(float(
                p.get("max_parallel_children")
                if p.get("max_parallel_children") is not None else 2
            )),
            replans_used=max(0, int(float(p.get("replans_used") or 0))),
            retry_budget={str(k): float(v) for k, v in _mapping(p.get("retry_budget")).items()},
            retries_used=max(0, int(float(p.get("retries_used") or 0))),
            child_ids=_tuple(p.get("child_ids")),
            join_conditions={str(k): dict(v) for k, v in _mapping(p.get("join_conditions")).items() if isinstance(v, Mapping)},
            join_results={str(k): dict(v) for k, v in _mapping(p.get("join_results")).items() if isinstance(v, Mapping)},
            checkpoint_id=str(p["checkpoint_id"]) if p.get("checkpoint_id") else None,
            checkpoint_version=max(0, int(float(p.get("checkpoint_version") or 0))),
            wake_ids=_tuple(p.get("wake_ids")),
            stop_reason=str(p["stop_reason"]) if p.get("stop_reason") else None,
            resume_status=str(p["resume_status"]) if p.get("resume_status") else None,
            last_reason=str(p.get("last_reason") or ""),
            metadata=_mapping(p.get("metadata")),
            version=max(1, int(float(p.get("version") or 1))),
            created_at=_timestamp(p.get("created_at")),
            updated_at=_timestamp(p.get("updated_at")),
        )


@dataclass(frozen=True, slots=True)
class CampaignCheckpoint:
    checkpoint_id: str
    campaign_id: str
    objective_id: str
    version: int
    status: str
    completed_children: tuple[str, ...]
    pending_children: tuple[str, ...]
    pending_work: tuple[dict[str, Any], ...]
    required_wakes: tuple[dict[str, Any], ...]
    joins: dict[str, dict[str, Any]]
    deadline: str | None
    retry_budget: dict[str, float]
    retries_used: int
    created_at: str
    projection_hash: str

    def _hash_material(self) -> dict[str, Any]:
        return {
            "schema": CAMPAIGN_SCHEMA,
            "checkpoint_id": self.checkpoint_id,
            "campaign_id": self.campaign_id,
            "objective_id": self.objective_id,
            "version": self.version,
            "status": self.status,
            "completed_children": list(self.completed_children),
            "pending_children": list(self.pending_children),
            "pending_work": [dict(v) for v in self.pending_work],
            "required_wakes": [dict(v) for v in self.required_wakes],
            "joins": {str(k): dict(v) for k, v in self.joins.items()},
            "deadline": self.deadline,
            "retry_budget": dict(self.retry_budget),
            "retries_used": self.retries_used,
            "created_at": self.created_at,
        }

    def to_payload(self) -> dict[str, Any]:
        payload = self._hash_material()
        payload["projection_hash"] = self.projection_hash
        return payload

    def validate(self) -> None:
        expected = hashlib.sha256(canonical_json(self._hash_material()).encode("utf-8")).hexdigest()
        if self.projection_hash != expected:
            raise CampaignError("campaign checkpoint projection hash mismatch")
        if self.status not in CAMPAIGN_STATUSES:
            raise CampaignError(f"invalid checkpoint status: {self.status}")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CampaignCheckpoint":
        p = dict(payload)
        checkpoint = cls(
            checkpoint_id=str(p.get("checkpoint_id") or ""),
            campaign_id=str(p.get("campaign_id") or ""),
            objective_id=str(p.get("objective_id") or ""),
            version=max(1, int(float(p.get("version") or 1))),
            status=str(p.get("status") or "UNKNOWN"),
            completed_children=_tuple(p.get("completed_children")),
            pending_children=_tuple(p.get("pending_children")),
            pending_work=tuple(dict(v) for v in (p.get("pending_work") or ()) if isinstance(v, Mapping)),
            required_wakes=tuple(dict(v) for v in (p.get("required_wakes") or ()) if isinstance(v, Mapping)),
            joins={str(k): dict(v) for k, v in _mapping(p.get("joins")).items() if isinstance(v, Mapping)},
            deadline=str(p["deadline"]) if p.get("deadline") else None,
            retry_budget={str(k): float(v) for k, v in _mapping(p.get("retry_budget")).items()},
            retries_used=max(0, int(float(p.get("retries_used") or 0))),
            created_at=_timestamp(p.get("created_at")),
            projection_hash=str(p.get("projection_hash") or ""),
        )
        checkpoint.validate()
        return checkpoint


@dataclass(frozen=True, slots=True)
class CampaignState:
    campaigns: dict[str, CampaignRecord] = field(default_factory=dict)
    children: dict[str, ChildMissionRecord] = field(default_factory=dict)
    checkpoints: dict[str, CampaignCheckpoint] = field(default_factory=dict)
    latest_resume_id: str | None = None

    def campaign(self, campaign_id: str) -> CampaignRecord:
        try:
            return self.campaigns[str(campaign_id)]
        except KeyError as error:
            raise CampaignError(f"unknown campaign: {campaign_id}") from error

    def child(self, child_id: str) -> ChildMissionRecord:
        try:
            return self.children[str(child_id)]
        except KeyError as error:
            raise CampaignError(f"unknown child mission: {child_id}") from error


@dataclass(frozen=True, slots=True)
class CampaignResumeReport:
    resume_id: str
    status: str
    campaigns_scanned: int = 0
    resumed_campaigns: tuple[str, ...] = ()
    waiting_campaigns: tuple[str, ...] = ()
    replan_required: tuple[str, ...] = ()
    escalated_campaigns: tuple[str, ...] = ()
    completed_campaigns: tuple[str, ...] = ()
    stopped_campaigns: tuple[str, ...] = ()
    reconciled_children: tuple[str, ...] = ()
    reconciled_effects: tuple[str, ...] = ()
    rebuilt_wakes: tuple[str, ...] = ()
    dispatchable_mission_ids: tuple[str, ...] = ()
    blocked_mission_ids: tuple[str, ...] = ()
    errors: tuple[dict[str, Any], ...] = ()
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": CAMPAIGN_SCHEMA,
            "resume_id": self.resume_id,
            "status": self.status,
            "campaigns_scanned": self.campaigns_scanned,
            "resumed_campaigns": list(self.resumed_campaigns),
            "waiting_campaigns": list(self.waiting_campaigns),
            "replan_required": list(self.replan_required),
            "escalated_campaigns": list(self.escalated_campaigns),
            "completed_campaigns": list(self.completed_campaigns),
            "stopped_campaigns": list(self.stopped_campaigns),
            "reconciled_children": list(self.reconciled_children),
            "reconciled_effects": list(self.reconciled_effects),
            "rebuilt_wakes": list(self.rebuilt_wakes),
            "dispatchable_mission_ids": list(self.dispatchable_mission_ids),
            "blocked_mission_ids": list(self.blocked_mission_ids),
            "errors": [dict(v) for v in self.errors],
            "reason": self.reason,
        }


def _replace_campaign(
    campaigns: dict[str, CampaignRecord],
    campaign_id: str,
    **changes: Any,
) -> None:
    prior = campaigns.get(campaign_id)
    if prior is None:
        return
    try:
        campaigns[campaign_id] = replace(prior, **changes)
    except (TypeError, ValueError, CampaignError):
        return


def _replace_child(
    children: dict[str, ChildMissionRecord],
    child_id: str,
    **changes: Any,
) -> None:
    prior = children.get(child_id)
    if prior is None:
        return
    try:
        children[child_id] = replace(prior, **changes)
    except (TypeError, ValueError, CampaignError):
        return


def rebuild_campaign_state(database: FoundryDatabase) -> CampaignState:
    """Reconstruct campaigns, children, and checkpoints from ledger truth."""

    campaigns: dict[str, CampaignRecord] = {}
    children: dict[str, ChildMissionRecord] = {}
    checkpoints: dict[str, CampaignCheckpoint] = {}
    latest_resume_id: str | None = None
    for event in database.events(event_type_prefixes=("campaign.",)):
        payload = event.payload
        typ = event.event_type
        if typ == "campaign.created":
            try:
                record = CampaignRecord.from_payload(payload)
            except (TypeError, ValueError, CampaignError):
                continue
            campaigns[record.campaign_id] = record
        elif typ == "campaign.updated":
            try:
                record = CampaignRecord.from_payload(payload)
            except (TypeError, ValueError, CampaignError):
                continue
            campaigns[record.campaign_id] = record
        elif typ == "campaign.child_declared":
            try:
                child = ChildMissionRecord.from_payload(payload)
            except (TypeError, ValueError, CampaignError):
                continue
            children[child.child_id] = child
            campaign = campaigns.get(child.campaign_id)
            if campaign is not None and child.child_id not in campaign.child_ids:
                _replace_campaign(campaigns, child.campaign_id, child_ids=(*campaign.child_ids, child.child_id))
        elif typ == "campaign.child_bound":
            child_id = str(payload.get("child_id") or "")
            if child_id:
                _replace_child(
                    children,
                    child_id,
                    mission_id=str(payload["mission_id"]) if payload.get("mission_id") else None,
                    step_id=str(payload["step_id"]) if payload.get("step_id") else None,
                    version=max(1, int(float(payload.get("version") or (children.get(child_id).version + 1 if child_id in children else 1)))),
                    updated_at=_timestamp(payload.get("updated_at") or event.recorded_at),
                )
        elif typ == "campaign.child_effect_bound":
            child_id = str(payload.get("child_id") or "")
            child = children.get(child_id)
            effect_key = str(payload.get("effect_key") or "")
            if child is not None and effect_key and effect_key not in child.effect_keys:
                _replace_child(children, child_id, effect_keys=(*child.effect_keys, effect_key), updated_at=event.recorded_at, version=child.version + 1)
        elif typ == "campaign.child_state_changed":
            child_id = str(payload.get("child_id") or "")
            child = children.get(child_id)
            if child is None:
                continue
            changes: dict[str, Any] = {
                "status": str(payload.get("status") or child.status),
                "wait_kind": str(payload["wait_kind"]) if payload.get("wait_kind") else None,
                "wait_reason": str(payload["wait_reason"]) if payload.get("wait_reason") else None,
                "wait_until": str(payload["wait_until"]) if payload.get("wait_until") else None,
                "resume_status": str(payload["resume_status"]) if payload.get("resume_status") else None,
                "resume_wait_kind": str(payload["resume_wait_kind"]) if payload.get("resume_wait_kind") else None,
                "resume_wait_reason": str(payload["resume_wait_reason"]) if payload.get("resume_wait_reason") else None,
                "resume_wait_until": str(payload["resume_wait_until"]) if payload.get("resume_wait_until") else None,
                "last_reason": str(payload.get("reason") or child.last_reason),
                "updated_at": _timestamp(payload.get("updated_at") or event.recorded_at),
                "version": max(child.version + 1, int(float(payload.get("version") or (child.version + 1)))),
            }
            if payload.get("attempt") is not None:
                changes["attempt"] = max(0, int(float(payload.get("attempt") or 0)))
            if payload.get("acceptance_outcome_id"):
                changes["acceptance_outcome_id"] = str(payload["acceptance_outcome_id"])
            if payload.get("acceptance_identity"):
                changes["acceptance_identity"] = str(payload["acceptance_identity"])
            _replace_child(children, child_id, **changes)
        elif typ == "campaign.retry_reserved":
            child_id = str(payload.get("child_id") or "")
            child = children.get(child_id)
            if child is not None:
                _replace_child(
                    children,
                    child_id,
                    retries_used=max(child.retries_used, int(float(payload.get("retries_used") or child.retries_used + 1))),
                    attempt=max(child.attempt, int(float(payload.get("attempt") or child.attempt + 1))),
                    updated_at=event.recorded_at,
                    version=child.version + 1,
                )
            campaign_id = str(payload.get("campaign_id") or "")
            campaign = campaigns.get(campaign_id)
            if campaign is not None:
                _replace_campaign(campaigns, campaign_id, retries_used=max(campaign.retries_used, int(float(payload.get("campaign_retries_used") or campaign.retries_used + 1))), updated_at=event.recorded_at, version=campaign.version + 1)
        elif typ == "campaign.checkpoint_recorded":
            try:
                checkpoint = CampaignCheckpoint.from_payload(payload)
            except (TypeError, ValueError, CampaignError):
                continue
            prior = checkpoints.get(checkpoint.campaign_id)
            if prior is None or checkpoint.version >= prior.version:
                checkpoints[checkpoint.campaign_id] = checkpoint
                _replace_campaign(
                    campaigns,
                    checkpoint.campaign_id,
                    checkpoint_id=checkpoint.checkpoint_id,
                    checkpoint_version=checkpoint.version,
                    updated_at=event.recorded_at,
                    version=max(campaigns[checkpoint.campaign_id].version, checkpoint.version)
                    if checkpoint.campaign_id in campaigns else checkpoint.version,
                )
        elif typ in {"campaign.status_changed", "campaign.stop_propagated", "campaign.resumed", "campaign.cancelled"}:
            campaign_id = str(payload.get("campaign_id") or "")
            campaign = campaigns.get(campaign_id)
            if campaign is None:
                continue
            status = str(payload.get("status") or campaign.status)
            _replace_campaign(
                campaigns,
                campaign_id,
                status=status,
                last_reason=str(payload.get("reason") or campaign.last_reason),
                stop_reason=(
                    None
                    if typ == "campaign.resumed"
                    else (str(payload["stop_reason"]) if payload.get("stop_reason") else campaign.stop_reason)
                ),
                resume_status=(
                    None
                    if typ == "campaign.resumed"
                    else (
                        str(payload["resume_status"])
                        if payload.get("resume_status")
                        else campaign.resume_status
                    )
                ),
                updated_at=_timestamp(payload.get("updated_at") or event.recorded_at),
                version=max(campaign.version + 1, int(float(payload.get("version") or campaign.version + 1))),
            )
        elif typ == "campaign.deadline_replan_requested":
            campaign_id = str(payload.get("campaign_id") or "")
            campaign = campaigns.get(campaign_id)
            if campaign is not None:
                _replace_campaign(
                    campaigns,
                    campaign_id,
                    status="REPLANNING",
                    replans_used=max(campaign.replans_used, int(float(payload.get("replan_number") or campaign.replans_used + 1))),
                    last_reason=str(payload.get("reason") or "deadline reached; replan required"),
                    updated_at=event.recorded_at,
                    version=campaign.version + 1,
                )
        elif typ == "campaign.replan_applied":
            campaign_id = str(payload.get("campaign_id") or "")
            campaign = campaigns.get(campaign_id)
            if campaign is not None:
                _replace_campaign(
                    campaigns,
                    campaign_id,
                    status=str(payload.get("status") or "EXECUTING"),
                    resume_status=None,
                    deadline=str(payload["deadline"]) if payload.get("deadline") else None,
                    metadata={**campaign.metadata, **_mapping(payload.get("metadata"))},
                    last_reason=str(payload.get("reason") or "deadline replan applied"),
                    updated_at=event.recorded_at,
                    version=campaign.version + 1,
                )
        elif typ == "campaign.join_satisfied" or typ == "campaign.join_blocked":
            campaign_id = str(payload.get("campaign_id") or "")
            join_id = str(payload.get("join_id") or "")
            campaign = campaigns.get(campaign_id)
            if campaign is not None and join_id:
                result = {
                    "status": "SATISFIED" if typ.endswith("satisfied") else str(payload.get("status") or "BLOCKED"),
                    "child_ids": list(payload.get("child_ids") or ()),
                    "accepted_outcome_ids": list(payload.get("accepted_outcome_ids") or ()),
                    "reason": str(payload.get("reason") or ""),
                    "updated_at": event.recorded_at,
                }
                _replace_campaign(campaigns, campaign_id, join_results={**campaign.join_results, join_id: result}, updated_at=event.recorded_at, version=campaign.version + 1)
        elif typ == "campaign.wakes_rebuilt":
            campaign_id = str(payload.get("campaign_id") or "")
            if campaign_id in campaigns:
                _replace_campaign(campaigns, campaign_id, wake_ids=_tuple(payload.get("wake_ids")), updated_at=event.recorded_at, version=campaigns[campaign_id].version + 1)
        elif typ == "campaign.completed":
            campaign_id = str(payload.get("campaign_id") or "")
            if campaign_id in campaigns:
                _replace_campaign(campaigns, campaign_id, status="COMPLETED", last_reason=str(payload.get("reason") or "all child missions accepted"), updated_at=event.recorded_at, version=campaigns[campaign_id].version + 1)
        elif typ == "campaign.deadline_escalated":
            campaign_id = str(payload.get("campaign_id") or "")
            if campaign_id in campaigns:
                _replace_campaign(campaigns, campaign_id, status="ESCALATED", last_reason=str(payload.get("reason") or "deadline exceeded"), updated_at=event.recorded_at, version=campaigns[campaign_id].version + 1)
        elif typ == "campaign.resume_passed":
            latest_resume_id = str(payload.get("resume_id") or latest_resume_id or "") or latest_resume_id
    return CampaignState(campaigns, children, checkpoints, latest_resume_id)


def _normalize_join_conditions(value: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw_id, raw in _mapping(value).items():
        if not isinstance(raw, Mapping):
            raise CampaignError(f"join {raw_id!r} must be a mapping")
        join_id = str(raw_id).strip()
        child_ids = _tuple(raw.get("children") or raw.get("child_ids"))
        if not join_id or not child_ids:
            raise CampaignError("join conditions require an id and children")
        mode = str(raw.get("mode") or "ALL").upper()
        if mode not in {"ALL", "ANY"}:
            raise CampaignError(f"join {join_id} mode must be ALL or ANY")
        result[join_id] = {
            "children": list(dict.fromkeys(child_ids)),
            "mode": mode,
            "required": bool(raw.get("required", True)),
        }
    return result


def _assert_child_dag(children: Mapping[str, ChildMissionRecord]) -> None:
    graph = {key: tuple(dep for dep in value.dependencies if dep in children) for key, value in children.items()}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise CampaignError("campaign child dependency graph is cyclic")
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, ()):
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def create_campaign(
    database: FoundryDatabase,
    objective_id: str,
    *,
    campaign_id: str | None = None,
    deadline: str | None = None,
    max_replans: int = 1,
    max_parallel_children: int = 2,
    retry_budget: Mapping[str, float] | None = None,
    join_conditions: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> CampaignRecord:
    _gate(database, controller, stop_path=stop_path)
    from .autonomy import rebuild_executive_state

    objective = rebuild_executive_state(database).objectives.get(str(objective_id))
    if objective is None:
        raise CampaignError(f"campaign objective does not exist: {objective_id}")
    if objective.status in {"COMPLETED", "CANCELLED", "SUSPENDED"}:
        raise CampaignError(f"cannot start campaign for terminal objective {objective_id}")
    joins = _normalize_join_conditions(join_conditions)
    clean_budget = {str(k): float(v) for k, v in _mapping(retry_budget).items()}
    if any(not math.isfinite(value) or value < 0 for value in clean_budget.values()):
        raise CampaignError("retry budget values must be finite and non-negative")
    effective_deadline = deadline or objective.deadline
    if effective_deadline is not None and _dt(effective_deadline) is None:
        raise CampaignError("campaign deadline must be an ISO-8601 timestamp")
    parallel_limit = int(max_parallel_children)
    if not 1 <= parallel_limit <= 64:
        raise CampaignError("campaign max_parallel_children is outside [1, 64]")
    cid = campaign_id or f"campaign:{objective_id}:{_hash({'deadline': effective_deadline, 'max_replans': max(0, int(max_replans)), 'max_parallel_children': parallel_limit, 'retry_budget': clean_budget, 'joins': joins, 'metadata': dict(metadata or {})})}"
    state = rebuild_campaign_state(database)
    prior = state.campaigns.get(cid)
    if prior is not None:
        if prior.objective_id != str(objective_id):
            raise CampaignError(f"campaign id already names another objective: {cid}")
        # An explicit campaign id is an immutable identity boundary.  A
        # replay with changed decomposition/deadline policy must not silently
        # reuse the old aggregate and pretend the new contract was accepted.
        requested_metadata = _mapping(metadata)
        if (
            prior.deadline != effective_deadline
            or prior.max_replans != max(0, int(max_replans))
            or prior.max_parallel_children != parallel_limit
            or prior.retry_budget != clean_budget
            or prior.join_conditions != joins
            or prior.metadata != requested_metadata
        ):
            raise CampaignError(
                f"campaign id already names a different immutable contract: {cid}"
            )
        return prior
    record = CampaignRecord(
        campaign_id=cid,
        objective_id=str(objective_id),
        status="EXECUTING",
        deadline=effective_deadline,
        max_replans=max(0, int(max_replans)),
        max_parallel_children=parallel_limit,
        retry_budget=clean_budget,
        join_conditions=joins,
        metadata=_mapping(metadata),
    )
    try:
        _append(database, "campaign.created", record.to_payload(), key=f"campaign.created:{cid}", campaign_id=cid)
    except EventConflict:
        # A concurrent Supervisor recovery may have created the same
        # deterministic campaign between the read and append.  Rebuild and
        # return that durable identity only when its immutable contract is
        # identical.  Explicit ids are still an identity boundary under a
        # race; accepting a different deadline/decomposition would make
        # restart reconstruction ambiguous.
        concurrent = rebuild_campaign_state(database).campaigns.get(cid)
        if concurrent is None:
            raise
        if (
            concurrent.objective_id != str(objective_id)
            or concurrent.deadline != effective_deadline
            or concurrent.max_replans != max(0, int(max_replans))
            or concurrent.max_parallel_children != parallel_limit
            or concurrent.retry_budget != clean_budget
            or concurrent.join_conditions != joins
            or concurrent.metadata != _mapping(metadata)
        ):
            raise CampaignError(
                f"campaign id already names a different immutable contract: {cid}"
            )
    checkpoint_campaign(database, cid, controller=controller, stop_path=stop_path)
    return rebuild_campaign_state(database).campaigns[cid]


def _campaign_status_event(
    database: FoundryDatabase,
    campaign: CampaignRecord,
    status: str,
    *,
    reason: str,
    controller: str,
    stop_path: str | Path | None = None,
    allow_stop: bool = False,
    event_type: str = "campaign.status_changed",
    extra: Mapping[str, Any] | None = None,
) -> CampaignRecord:
    _gate(database, controller, stop_path=stop_path, campaign_id=campaign.campaign_id, allow_stop=allow_stop)
    target = str(status).upper()
    if target not in CAMPAIGN_STATUSES:
        raise CampaignError(f"unsupported campaign status: {status}")
    if target == campaign.status:
        return campaign
    if target not in _CAMPAIGN_TRANSITIONS.get(campaign.status, frozenset()):
        raise CampaignError(f"invalid campaign transition: {campaign.status} -> {target}")
    now = isoformat()
    payload = {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": campaign.campaign_id,
        "objective_id": campaign.objective_id,
        "status": target,
        "reason": reason,
        "updated_at": now,
        "version": campaign.version + 1,
        **dict(extra or {}),
    }
    try:
        _append(
            database,
            event_type,
            payload,
            key=f"{event_type}:{campaign.campaign_id}:v{campaign.version + 1}:{target}",
            campaign_id=campaign.campaign_id,
        )
    except EventConflict:
        concurrent = rebuild_campaign_state(database).campaigns.get(campaign.campaign_id)
        if concurrent is None or concurrent.version < campaign.version + 1:
            raise
        return concurrent
    state = rebuild_campaign_state(database)
    return state.campaigns.get(campaign.campaign_id, replace(campaign, status=target, updated_at=now, version=campaign.version + 1))


def _sync_campaign_readiness(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str,
    stop_path: str | Path | None,
) -> CampaignRecord:
    """Project aggregate WAITING/EXECUTING from exact child readiness.

    The campaign status is advisory orchestration state; child mission and
    effect projections remain authoritative.  We only park the aggregate when
    no child is dispatchable and at least one child carries an explicit wait,
    dependency, provider, or reconciliation blocker.
    """

    # Normalize dependency ordering before projecting aggregate readiness.
    # This also protects direct lifecycle calls that attempt READY while a
    # sibling is still unresolved.
    dependency_changed = _reconcile_dependency_waits(
        database,
        campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    if campaign.status not in {"EXECUTING", "WAITING"}:
        return campaign
    children = [child for child in state.children.values() if child.campaign_id == campaign_id]

    def sync_objective_wait(target_campaign_status: str) -> None:
        """Mirror campaign wait/release through the canonical objective seam.

        Campaign state remains the orchestration aggregate, but an owner
        briefing must not report an ACTIVE objective while every campaign
        child is durably waiting.  Only ACTIVE <-> WAITING is mirrored here;
        terminal/owner-controlled objective states remain authoritative.
        """

        try:
            from .autonomy import rebuild_executive_state, set_objective_status

            objective = rebuild_executive_state(database).objectives.get(campaign.objective_id)
            if objective is None:
                return
            # Several campaigns may decompose the same objective.  A wait in
            # one sibling campaign must not park the objective while another
            # campaign still has dispatchable canonical mission work.
            aggregate_state = rebuild_campaign_state(database)
            objective_has_dispatchable = False
            for sibling_campaign in aggregate_state.campaigns.values():
                if (
                    sibling_campaign.objective_id != campaign.objective_id
                    or sibling_campaign.status != "EXECUTING"
                ):
                    continue
                for sibling_child in aggregate_state.children.values():
                    if (
                        sibling_child.campaign_id != sibling_campaign.campaign_id
                        or sibling_child.status not in {"READY", "RUNNING"}
                        or _unmet_dependencies(aggregate_state, sibling_child)
                        or not sibling_child.mission_id
                    ):
                        continue
                    mission = database.row("missions", "mission_id", sibling_child.mission_id)
                    if str((mission or {}).get("state") or "") in {
                        "ACTION_SELECTION", "REPAIRING", "READY", "VERIFYING",
                    }:
                        objective_has_dispatchable = True
                        break
                if objective_has_dispatchable:
                    break
            if target_campaign_status == "WAITING" and objective.status == "ACTIVE":
                if objective_has_dispatchable:
                    return
                set_objective_status(
                    database,
                    campaign.objective_id,
                    "WAITING",
                    controller=controller,
                    reason="campaign children are durably waiting for a wake or owner decision",
                    evidence_refs=(f"campaign:{campaign_id}:waiting",),
                )
            elif target_campaign_status == "EXECUTING" and objective.status == "WAITING":
                set_objective_status(
                    database,
                    campaign.objective_id,
                    "ACTIVE",
                    controller=controller,
                    reason="campaign wake released dispatchable child work",
                    evidence_refs=(f"campaign:{campaign_id}:resumed",),
                )
        except (KeyError, TypeError, ValueError):
            # Campaign recovery must retain its own exact checkpoint even when
            # an older objective projection cannot accept the advisory mirror.
            pass

    def mission_dispatchable(child: ChildMissionRecord) -> bool:
        if not child.mission_id:
            return False
        row = database.row("missions", "mission_id", child.mission_id)
        return str((row or {}).get("state") or "") in {
            "ACTION_SELECTION", "REPAIRING", "READY", "VERIFYING",
        }

    dispatchable = any(
        mission_dispatchable(child)
        and child.status in {"READY", "RUNNING"}
        and not _unmet_dependencies(state, child)
        for child in children
    )
    explicit_wait = any(
        child.status in {"WAITING", "UNKNOWN", "BLOCKED"}
        or (
            child.status == "SUCCEEDED"
            and not _accepted_child_truth_is_durable(database, child)
        )
        or bool(child.wait_kind or child.wait_reason)
        for child in children
    )
    if dispatchable and campaign.status == "WAITING":
        updated = _campaign_status_event(
            database,
            campaign,
            "EXECUTING",
            reason="a child is dispatchable after wait recovery",
            controller=controller,
            stop_path=stop_path,
        )
        sync_objective_wait("EXECUTING")
        return updated
    if (
        campaign.status == "WAITING"
        and dependency_changed
        and not dispatchable
        and not explicit_wait
    ):
        # A dependency wake may release an unbound child before the normal
        # Executive materialization path creates its canonical mission.  The
        # child is exact PENDING work, not dispatchable READY work, but the
        # campaign/objective must still leave WAITING so a later materializer
        # can observe and bind that pending step.
        updated = _campaign_status_event(
            database,
            campaign,
            "EXECUTING",
            reason="a dependency wake released pending child materialization",
            controller=controller,
            stop_path=stop_path,
        )
        sync_objective_wait("EXECUTING")
        return updated
    if not dispatchable and explicit_wait and campaign.status == "EXECUTING":
        updated = _campaign_status_event(
            database,
            campaign,
            "WAITING",
            reason="all currently required child work is waiting or blocked",
            controller=controller,
            stop_path=stop_path,
        )
        sync_objective_wait("WAITING")
        return updated
    return campaign


def _mirror_objective_active_after_campaign_resume(
    database: FoundryDatabase,
    campaign: CampaignRecord,
    *,
    controller: str,
) -> None:
    """Release an objective WAITING mirror after a durable campaign wake.

    Provider recovery can release an unbound child before a canonical mission
    is materialized.  That campaign is still EXECUTING (pending exact child
    work), even though the generic dispatchability predicate is temporarily
    false; keep the Executive objective mirror consistent with that parent
    state without touching terminal/owner-controlled objectives.
    """

    try:
        from .autonomy import rebuild_executive_state, set_objective_status

        objective = rebuild_executive_state(database).objectives.get(campaign.objective_id)
        if objective is None or objective.status != "WAITING":
            return
        if not any(
            sibling.objective_id == campaign.objective_id
            and sibling.status == "EXECUTING"
            for sibling in rebuild_campaign_state(database).campaigns.values()
        ):
            return
        set_objective_status(
            database,
            campaign.objective_id,
            "ACTIVE",
            controller=controller,
            reason="campaign provider wake released pending child work",
            evidence_refs=(f"campaign:{campaign.campaign_id}:resumed",),
        )
    except (KeyError, TypeError, ValueError):
        pass


def _child_routing_request(
    *,
    provider_id: str | None,
    task_type: str,
    required_capabilities: Sequence[str],
    required_tools: Sequence[str],
    routing_requirements: Mapping[str, Any] | RoutingRequest | None,
) -> RoutingRequest:
    """Normalize one immutable child requirement onto the canonical router type."""

    if isinstance(routing_requirements, RoutingRequest):
        request = routing_requirements
    elif routing_requirements is not None:
        request = RoutingRequest.from_mapping(routing_requirements)
    else:
        request = RoutingRequest(
            task_type=str(task_type or "builder"),
            required_capabilities=tuple(dict.fromkeys(
                str(item) for item in required_capabilities if str(item)
            )),
            required_tools=tuple(dict.fromkeys(
                str(item) for item in required_tools if str(item)
            )),
            preferred_provider=str(provider_id) if provider_id else None,
            allowed_providers=(str(provider_id),) if provider_id else (),
        )
    capabilities = tuple(dict.fromkeys((
        *request.required_capabilities,
        *(str(item) for item in required_capabilities if str(item)),
    )))
    tools = tuple(dict.fromkeys((
        *request.required_tools,
        *(str(item) for item in required_tools if str(item)),
    )))
    preferred = request.preferred_provider
    if provider_id:
        if preferred and preferred != str(provider_id):
            raise CampaignError(
                "child provider_id conflicts with routing preferred_provider"
            )
        preferred = str(provider_id)
    if (
        request.allowed_providers
        and preferred
        and preferred not in request.allowed_providers
    ):
        raise CampaignError(
            "child preferred provider must be included in allowed_providers"
        )
    return replace(
        request,
        required_capabilities=capabilities,
        required_tools=tools,
        preferred_provider=preferred,
    )


def declare_child_mission(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    child_id: str | None = None,
    mission_id: str | None = None,
    step_id: str | None = None,
    dependencies: Sequence[str] = (),
    sibling_group: str | None = None,
    join_ids: Sequence[str] = (),
    provider_id: str | None = None,
    task_type: str = "builder",
    required_capabilities: Sequence[str] = (),
    required_tools: Sequence[str] = (),
    routing_requirements: Mapping[str, Any] | RoutingRequest | None = None,
    isolation_key: str | None = None,
    effect_action_key: str | None = None,
    effect_keys: Sequence[str] = (),
    max_retries: int = 1,
    metadata: Mapping[str, Any] | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> ChildMissionRecord:
    _gate(database, controller, stop_path=stop_path, campaign_id=campaign_id)
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    if campaign.status in {"COMPLETED", "CANCELLED"}:
        raise CampaignError(f"cannot add child to terminal campaign {campaign_id}")
    sid = str(step_id or "")
    normalized_dependencies = tuple(dict.fromkeys(str(v) for v in dependencies if str(v)))
    normalized_join_ids = tuple(dict.fromkeys(str(v) for v in join_ids if str(v)))
    request = _child_routing_request(
        provider_id=provider_id,
        task_type=task_type,
        required_capabilities=required_capabilities,
        required_tools=required_tools,
        routing_requirements=routing_requirements,
    )
    routing_payload = request.to_dict()
    routing_identity = request.identity_hash()
    normalized_effects = tuple(dict.fromkeys(
        str(v) for v in effect_keys if str(v)
    ))
    primary_effect = str(effect_action_key) if effect_action_key else None
    if primary_effect and primary_effect not in normalized_effects:
        normalized_effects = (*normalized_effects, primary_effect)
    cid = child_id or f"child:{campaign_id}:{sid or _hash({'mission_id': mission_id, 'step_id': step_id, 'deps': list(normalized_dependencies), 'joins': list(normalized_join_ids), 'routing_identity': routing_identity, 'effects': list(normalized_effects), 'effect_action_key': primary_effect, 'sibling_group': sibling_group, 'isolation_key': isolation_key})}"
    prior = state.children.get(cid)
    if prior is not None:
        identity = (
            prior.campaign_id,
            prior.mission_id,
            prior.step_id,
            prior.dependencies,
            prior.sibling_group,
            prior.join_ids,
            prior.provider_id,
            prior.routing_requirements,
            prior.routing_identity,
            prior.isolation_key,
            prior.effect_action_key,
            prior.effect_keys,
            prior.max_retries,
            prior.metadata,
        )
        requested_identity = (
            str(campaign_id),
            str(mission_id) if mission_id else None,
            str(step_id) if step_id else None,
            normalized_dependencies,
            str(sibling_group) if sibling_group else None,
            normalized_join_ids,
            str(provider_id) if provider_id else None,
            routing_payload,
            routing_identity,
            str(isolation_key) if isolation_key else None,
            primary_effect,
            normalized_effects,
            max(0, int(max_retries)),
            _mapping(metadata),
        )
        if identity != requested_identity:
            raise CampaignError(f"child id already names different work: {cid}")
        return prior
    requested_mission_id = str(mission_id) if mission_id else None
    if requested_mission_id:
        if database.row("missions", "mission_id", requested_mission_id) is None:
            raise CampaignError(
                f"bound mission {requested_mission_id} is absent from the authoritative projection"
            )
        duplicate = next(
            (
                other for other in state.children.values()
                if other.mission_id == requested_mission_id
            ),
            None,
        )
        if duplicate is not None:
            raise CampaignError(
                f"mission {requested_mission_id} is already bound to child {duplicate.child_id}"
            )
        from .autonomy import rebuild_executive_state, update_objective

        objective = rebuild_executive_state(database).objectives.get(
            campaign.objective_id
        )
        if objective is None:
            raise CampaignError(
                f"campaign objective disappeared before child declaration: {campaign.objective_id}"
            )
        objective_needs_link = requested_mission_id not in objective.mission_ids
    else:
        objective = None
        objective_needs_link = False
    deps = normalized_dependencies
    unknown = {
        dependency for dependency in deps
        if dependency not in state.children
        or state.children[dependency].campaign_id != campaign_id
    }
    if unknown:
        raise CampaignError(f"child dependencies are not declared: {sorted(unknown)}")
    record = ChildMissionRecord(
        child_id=cid,
        campaign_id=str(campaign_id),
        mission_id=requested_mission_id,
        step_id=str(step_id) if step_id else None,
        dependencies=deps,
        sibling_group=str(sibling_group) if sibling_group else None,
        join_ids=tuple(dict.fromkeys(normalized_join_ids)),
        provider_id=str(provider_id) if provider_id else None,
        routing_requirements=routing_payload,
        routing_identity=routing_identity,
        isolation_key=str(isolation_key) if isolation_key else None,
        effect_action_key=primary_effect,
        effect_keys=normalized_effects,
        max_retries=max(0, int(max_retries)),
        metadata=_mapping(metadata),
    )
    for join_id in record.join_ids:
        if join_id not in campaign.join_conditions:
            raise CampaignError(f"child references unknown join: {join_id}")
        if record.child_id not in _tuple(
            campaign.join_conditions[join_id].get("children")
        ):
            raise CampaignError(
                f"child {record.child_id} is not listed in join {join_id}"
            )
    # Validate the complete graph before appending.  A rejected declaration
    # must not leave an irreversible event that later replay cannot remove.
    _assert_child_dag({**state.children, cid: record})
    if objective_needs_link and objective is not None and requested_mission_id:
        update_objective(
            database,
            campaign.objective_id,
            {"mission_ids": [*objective.mission_ids, requested_mission_id]},
            controller=controller,
            source_id="campaign-runtime",
        )
    try:
        _append(
            database,
            "campaign.child_declared",
            record.to_payload(),
            key=f"campaign.child_declared:{cid}",
            campaign_id=campaign_id,
        )
    except EventConflict:
        concurrent = rebuild_campaign_state(database).children.get(cid)
        if concurrent is None:
            raise
        if (
            concurrent.campaign_id != record.campaign_id
            or concurrent.mission_id != record.mission_id
            or concurrent.step_id != record.step_id
            or concurrent.dependencies != record.dependencies
            or concurrent.sibling_group != record.sibling_group
            or concurrent.join_ids != record.join_ids
            or concurrent.provider_id != record.provider_id
            or concurrent.routing_requirements != record.routing_requirements
            or concurrent.routing_identity != record.routing_identity
            or concurrent.isolation_key != record.isolation_key
            or concurrent.effect_action_key != record.effect_action_key
            or concurrent.effect_keys != record.effect_keys
            or concurrent.max_retries != record.max_retries
            or concurrent.metadata != record.metadata
        ):
            raise CampaignError(f"child id already names different work: {cid}")
    checkpoint_campaign(database, campaign_id, controller=controller, stop_path=stop_path)
    return rebuild_campaign_state(database).children[cid]


def bind_child_mission(
    database: FoundryDatabase,
    child_id: str,
    mission_id: str,
    *,
    step_id: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> ChildMissionRecord:
    _gate(database, controller, stop_path=stop_path)
    requested_mission_id = str(mission_id).strip()
    if not requested_mission_id:
        raise CampaignError("mission_id is required")
    # Binding is a projection edge, not a placeholder for future work.  The
    # canonical MissionLoop mission must already exist so a crash/restart can
    # never leave a child apparently bound to work that the selector cannot
    # reconstruct.
    if database.row("missions", "mission_id", requested_mission_id) is None:
        raise CampaignError(
            f"bound mission {requested_mission_id} is absent from the authoritative projection"
        )
    state = rebuild_campaign_state(database)
    child = state.child(child_id)
    if child.mission_id == requested_mission_id and (step_id is None or child.step_id == str(step_id)):
        return child
    if child.mission_id is not None and child.mission_id != requested_mission_id:
        raise CampaignError(f"child {child_id} is already bound to {child.mission_id}")
    if (
        child.mission_id == requested_mission_id
        and step_id is not None
        and child.step_id != str(step_id)
    ):
        raise CampaignError(f"child {child_id} is already bound to step {child.step_id}")
    duplicate = next(
        (
            other
            for other in state.children.values()
            if other.child_id != child.child_id
            and other.mission_id == requested_mission_id
        ),
        None,
    )
    if duplicate is not None:
        raise CampaignError(
            f"mission {requested_mission_id} is already bound to child {duplicate.child_id}"
        )
    # The campaign edge and the Executive objective edge must agree.  Link
    # the already-existing mission into the existing objective before
    # recording the child binding; a crash in between leaves only a harmless
    # canonical objective relation, never a child that routes around its
    # owning objective.
    campaign = state.campaign(child.campaign_id)
    from .autonomy import rebuild_executive_state, update_objective

    objective = rebuild_executive_state(database).objectives.get(
        campaign.objective_id
    )
    if objective is None:
        raise CampaignError(
            f"campaign objective disappeared before child binding: {campaign.objective_id}"
        )
    if requested_mission_id not in objective.mission_ids:
        update_objective(
            database,
            campaign.objective_id,
            {"mission_ids": [*objective.mission_ids, requested_mission_id]},
            controller=controller,
            source_id="campaign-runtime",
        )
    version = child.version + 1
    payload = {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": child.campaign_id,
        "child_id": child.child_id,
        "mission_id": requested_mission_id,
        "step_id": step_id or child.step_id,
        "version": version,
        "updated_at": isoformat(),
    }
    try:
        _append(
            database,
            "campaign.child_bound",
            payload,
            key=f"campaign.child_bound:{child_id}:{requested_mission_id}",
            campaign_id=child.campaign_id,
        )
    except EventConflict:
        concurrent = rebuild_campaign_state(database).children.get(child_id)
        if (
            concurrent is None
            or concurrent.mission_id != requested_mission_id
            or (step_id is not None and concurrent.step_id != str(step_id))
        ):
            raise
    result = rebuild_campaign_state(database).children[child_id]
    checkpoint_campaign(
        database,
        child.campaign_id,
        controller=controller,
        stop_path=stop_path,
        reason="child mission binding checkpoint",
    )
    return result


def bind_child_effect(
    database: FoundryDatabase,
    child_id: str,
    effect_key: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> ChildMissionRecord:
    _gate(database, controller, stop_path=stop_path)
    state = rebuild_campaign_state(database)
    child = state.child(child_id)
    key = str(effect_key).strip()
    if not key:
        raise CampaignError("effect_key is required")
    if key in child.effect_keys:
        return child
    payload = {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": child.campaign_id,
        "child_id": child_id,
        "effect_key": key,
    }
    try:
        _append(
            database,
            "campaign.child_effect_bound",
            payload,
            key=f"campaign.child_effect_bound:{child_id}:{key}",
            campaign_id=child.campaign_id,
        )
    except EventConflict:
        concurrent = rebuild_campaign_state(database).children.get(child_id)
        if concurrent is None or key not in concurrent.effect_keys:
            raise
    result = rebuild_campaign_state(database).children[child_id]
    checkpoint_campaign(
        database,
        child.campaign_id,
        controller=controller,
        stop_path=stop_path,
        reason="child effect binding checkpoint",
    )
    return result


def _accepted_outcome(
    database: FoundryDatabase,
    mission_id: str | None,
) -> dict[str, Any] | None:
    if not mission_id:
        return None
    from ..mission.verification import final_outcome_history, validate_final_outcome_contract

    for final in final_outcome_history(database, mission_id=mission_id, limit=50):
        if validate_final_outcome_contract(database, final):
            return final
    return None


def _effect_attempted(database: FoundryDatabase, effect_key: str) -> bool:
    """Return whether one declared effect identity ever entered the journal."""

    for event in database.events(
        readonly=True,
        event_type_prefixes=("system.action_", "effect."),
    ):
        payload_key = str(
            event.payload.get("action_key")
            or event.payload.get("effect_id")
            or ""
        )
        if payload_key == effect_key:
            return True
    return False


def _effect_blockers(
    database: FoundryDatabase,
    child: ChildMissionRecord,
) -> dict[str, str]:
    """Return bound effects that are not safe for an accepted join."""

    blockers: dict[str, str] = {}
    for key in child.effect_keys:
        status = _safe_effect_status(database, key)
        if status == "UNKNOWN" and not _effect_attempted(database, key):
            # A campaign child's declared effect identity is isolation truth,
            # not a started claim. Never-started keys must not strand a child
            # after MissionLoop PASS and verifier acceptance already exist.
            continue
        if status in {"STARTED", "FROZEN", "UNKNOWN", "FAILED"}:
            blockers[key] = status
    return blockers


def _safe_effect_status(database: FoundryDatabase, effect_key: str) -> str:
    """Read an effect projection without converting missing truth to a crash."""

    try:
        projected = effect_state(database, effect_key)
        status = str(projected.get("status") or "UNKNOWN").upper()
        if (
            projected.get("accepted") is True
            and projected.get("reconciled") is True
            and str(projected.get("terminal_outcome") or "").upper()
            == "COMPLETED"
        ):
            status = "COMPLETED"
    except Exception:  # noqa: BLE001 - an absent effect is durable UNKNOWN truth
        status = "UNKNOWN"
    # The legacy system_actions projection retains FROZEN after an additive
    # provider reconciliation.  Campaign joins must consume the complete
    # event truth. A reconciliation observation alone is not acceptance:
    # campaign joins wait for the canonical terminal-truth receipt.
    for event in database.events(
        readonly=True,
        event_type_prefixes=("system.action_", "effect."),
    ):
        if str(event.payload.get("action_key") or "") != effect_key:
            continue
        if event.event_type == "system.action_started":
            status = "STARTED"
        elif event.event_type == "system.action_completed":
            status = "COMPLETED"
        elif event.event_type == "system.action_failed":
            status = "FAILED"
        elif event.event_type in {
            "effect.frozen", "effect.reconcile_mismatch", "effect.compensated",
        }:
            status = "FROZEN"
        elif event.event_type == "effect.terminal_truth":
            if (
                event.payload.get("accepted") is True
                and event.payload.get("reconciled") is True
                and str(event.payload.get("outcome") or "").upper()
                == "COMPLETED"
            ):
                status = "COMPLETED"
            else:
                status = "FROZEN"
    return status


def _child_mission_truth(database: FoundryDatabase, child: ChildMissionRecord) -> dict[str, Any]:
    if not child.mission_id:
        return {"status": child.status, "accepted": child.status == ACCEPTED_CHILD_STATUS, "reason": "child has no bound mission"}
    mission = database.row("missions", "mission_id", child.mission_id)
    if mission is None:
        return {"status": "UNKNOWN", "accepted": False, "reason": "bound mission is absent from authoritative projection"}
    state = str(mission.get("state") or "UNKNOWN").upper()
    final = _accepted_outcome(database, child.mission_id)
    if state == "COMPLETE":
        if final is not None and str(final.get("status") or "") == "SUCCEEDED":
            effect_blockers = _effect_blockers(database, child)
            if effect_blockers:
                return {
                    "status": "UNKNOWN",
                    "accepted": False,
                    "outcome": final,
                    "reason": f"bound effects require reconciliation: {effect_blockers}",
                }
            return {"status": "SUCCEEDED", "accepted": True, "outcome": final, "reason": "accepted final outcome is durable"}
        if final is not None and str(final.get("status") or "") == "FAILED":
            return {
                "status": "FAILED",
                "accepted": False,
                "outcome": final,
                "reason": "accepted failure outcome is durable",
            }
        return {"status": "UNKNOWN", "accepted": False, "reason": "mission is COMPLETE without a valid accepted final outcome"}
    if state == "CANCELLED":
        return {"status": "CANCELLED", "accepted": False, "outcome": final, "reason": "mission was cancelled"}
    if state == "BLOCKED":
        if final is not None and str(final.get("status") or "") == "FAILED":
            return {"status": "FAILED", "accepted": False, "outcome": final, "reason": "accepted failure outcome is durable"}
        return {"status": "BLOCKED", "accepted": False, "outcome": final, "reason": "mission is durably blocked"}
    if state in {"ACTION_SELECTION", "READY", "VERIFYING", "REPAIRING"}:
        # These are the existing MissionLoop dispatchable states.  A child
        # is READY for campaign purposes even when the mission projection
        # itself uses a more specific continuation state.
        return {"status": "READY", "accepted": False, "reason": f"mission remains {state}"}
    if state in {"PLANNED", "ORIENTING"}:
        return {"status": "PENDING", "accepted": False, "reason": f"mission remains {state}"}
    if state in {"EXECUTING", "ACCEPTING", "REJECTING"}:
        return {"status": "RUNNING", "accepted": False, "reason": f"mission remains {state}"}
    return {"status": "UNKNOWN", "accepted": False, "reason": f"unknown mission state {state}"}


def _retry_receipt_authorizes_reopen(
    database: FoundryDatabase,
    child: ChildMissionRecord,
) -> bool:
    """Return whether a retry receipt follows the child's latest failure.

    The canonical mission projection can be reopened by other recovery code.
    A campaign must not interpret that state alone as permission to consume a
    retry budget.  Only the campaign's own durable reservation, committed
    after the latest child terminal receipt and for the current attempt, can
    release FAILED/BLOCKED child state back to MissionLoop dispatch.
    """

    if not child.mission_id:
        return False
    with database.connect(readonly=True) as connection:
        child_row = connection.execute(
            "SELECT sequence FROM events "
            "WHERE event_type = 'campaign.child_state_changed' "
            "AND json_extract(payload_json, '$.child_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (child.child_id,),
        ).fetchone()
        retry_row = connection.execute(
            "SELECT sequence, payload_json FROM events "
            "WHERE event_type = 'campaign.retry_reserved' "
            "AND json_extract(payload_json, '$.child_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (child.child_id,),
        ).fetchone()
    if child_row is None or retry_row is None:
        return False
    try:
        retry_payload = json.loads(str(retry_row[1]))
        attempt = int(float(retry_payload.get("attempt") or -1))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        int(retry_row[0]) > int(child_row[0])
        and attempt == child.attempt
    )


def _unmet_dependencies(
    state: CampaignState,
    child: ChildMissionRecord,
) -> tuple[str, ...]:
    """Return dependencies without an accepted child outcome.

    Missing dependencies are deliberately treated as unmet rather than
    inferred complete.  This is the exact pending-work boundary used by both
    wake reconstruction and selector exclusion.
    """

    return tuple(
        dependency
        for dependency in child.dependencies
        if state.children.get(dependency) is None
        or state.children[dependency].status != "SUCCEEDED"
        or not state.children[dependency].acceptance_outcome_id
    )


def _reconcile_dependency_waits(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str,
    stop_path: str | Path | None,
) -> list[str]:
    """Park children behind incomplete siblings and release exact waits."""

    state = rebuild_campaign_state(database)
    changed: list[str] = []
    for child in tuple(state.children.values()):
        if child.campaign_id != campaign_id or child.status in TERMINAL_CHILD_STATUSES:
            continue
        unmet = _unmet_dependencies(state, child)
        if unmet and child.status in {"PENDING", "READY", "RUNNING", "UNKNOWN", "RETRYING"}:
            _append_child_state_event(
                database,
                child,
                "WAITING",
                reason="child waits for accepted sibling outcomes",
                wait_kind="DEPENDENCY",
                wait_reason=f"waiting for dependencies: {', '.join(unmet)}",
                controller=controller,
                stop_path=stop_path,
            )
            changed.append(child.child_id)
            continue
        if not unmet and child.status == "WAITING" and child.wait_kind == "DEPENDENCY":
            truth = _child_mission_truth(database, child)
            target = (
                "RUNNING"
                if truth.get("status") == "RUNNING" and child.mission_id
                else "READY"
                if child.mission_id
                else "PENDING"
            )
            try:
                _append_child_state_event(
                    database,
                    child,
                    target,
                    reason="all sibling dependencies accepted; exact child released",
                    controller=controller,
                    stop_path=stop_path,
                )
                changed.append(child.child_id)
            except CampaignError:
                # A malformed/absent mission remains a durable pending child;
                # do not synthesize dispatch authority.
                pass
    return changed


def _validate_child_acceptance(
    database: FoundryDatabase,
    child: ChildMissionRecord,
    *,
    acceptance_outcome_id: str | None,
    acceptance_identity: str | None,
) -> dict[str, Any]:
    if not child.mission_id:
        raise CampaignError("a child cannot be marked SUCCEEDED before it is bound to a mission")
    effect_blockers = _effect_blockers(database, child)
    if effect_blockers:
        raise CampaignError(
            f"child success requires effect reconciliation: {effect_blockers}"
        )
    final = _accepted_outcome(database, child.mission_id)
    if final is None or str(final.get("status") or "") != "SUCCEEDED":
        raise CampaignError("child success requires a valid accepted final mission outcome")
    if acceptance_outcome_id and str(final.get("outcome_id") or "") != str(acceptance_outcome_id):
        raise CampaignError("child acceptance outcome identity does not match final truth")
    if acceptance_identity and str(final.get("acceptance_identity") or "") != str(acceptance_identity):
        raise CampaignError("child acceptance identity does not match final truth")
    return final


def _append_child_state_event(
    database: FoundryDatabase,
    child: ChildMissionRecord,
    status: str,
    *,
    reason: str,
    wait_kind: str | None = None,
    wait_reason: str | None = None,
    wait_until: str | None = None,
    acceptance_outcome_id: str | None = None,
    acceptance_identity: str | None = None,
    resume_status: str | None = None,
    resume_wait_kind: str | None = None,
    resume_wait_reason: str | None = None,
    resume_wait_until: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
    allow_stop: bool = False,
) -> ChildMissionRecord:
    _gate(database, controller, stop_path=stop_path, campaign_id=child.campaign_id, allow_stop=allow_stop)
    target = str(status).upper()
    if target not in CHILD_STATUSES:
        raise CampaignError(f"unsupported child status: {status}")
    if target == child.status:
        same_projection = (
            (not reason or reason == child.last_reason)
            and (wait_kind is None or wait_kind == child.wait_kind)
            and (wait_reason is None or wait_reason == child.wait_reason)
            and (wait_until is None or wait_until == child.wait_until)
            and (
                acceptance_outcome_id is None
                or str(acceptance_outcome_id) == str(child.acceptance_outcome_id)
            )
            and (
                acceptance_identity is None
                or str(acceptance_identity) == str(child.acceptance_identity)
            )
            and (resume_status is None or resume_status == child.resume_status)
            and (resume_wait_kind is None or resume_wait_kind == child.resume_wait_kind)
            and (resume_wait_reason is None or resume_wait_reason == child.resume_wait_reason)
            and (resume_wait_until is None or resume_wait_until == child.resume_wait_until)
        )
        if same_projection:
            return child
    elif target not in _CHILD_TRANSITIONS.get(child.status, frozenset()):
        raise CampaignError(f"invalid child transition: {child.status} -> {target}")
    now = isoformat()
    payload: dict[str, Any] = {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": child.campaign_id,
        "child_id": child.child_id,
        "mission_id": child.mission_id,
        "status": target,
        "reason": reason,
        "wait_kind": wait_kind,
        "wait_reason": wait_reason,
        "wait_until": wait_until,
        "resume_status": resume_status,
        "resume_wait_kind": resume_wait_kind,
        "resume_wait_reason": resume_wait_reason,
        "resume_wait_until": resume_wait_until,
        "attempt": child.attempt,
        "version": child.version + 1,
        "updated_at": now,
    }
    if acceptance_outcome_id:
        payload["acceptance_outcome_id"] = str(acceptance_outcome_id)
    if acceptance_identity:
        payload["acceptance_identity"] = str(acceptance_identity)
    key = f"campaign.child_state_changed:{child.child_id}:v{child.version + 1}:{target}"
    try:
        _append(database, "campaign.child_state_changed", payload, key=key, campaign_id=child.campaign_id)
    except EventConflict:
        concurrent = rebuild_campaign_state(database).children.get(child.child_id)
        if concurrent is None or concurrent.version < child.version + 1:
            raise
        # A concurrent recovery already committed this exact lifecycle
        # generation.  Rebuild from the ledger rather than emitting a second
        # transition or treating the race as an execution failure.
        return concurrent
    return rebuild_campaign_state(database).children.get(child.child_id, replace(child, status=target, last_reason=reason, version=child.version + 1, updated_at=now))


def record_child_state(
    database: FoundryDatabase,
    child_id: str,
    status: str,
    *,
    reason: str = "",
    wait_kind: str | None = None,
    wait_reason: str | None = None,
    wait_until: str | None = None,
    acceptance_outcome_id: str | None = None,
    acceptance_identity: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> ChildMissionRecord:
    state = rebuild_campaign_state(database)
    child = state.child(child_id)
    target = str(status).upper()
    accepted = None
    if target == "COMPLETED":
        target = "SUCCEEDED"
    if target == "RETRYING":
        raise CampaignError(
            "RETRYING is reserved for reserve_child_retry so budget accounting cannot be bypassed"
        )
    if target in {"READY", "RUNNING"} and not child.mission_id:
        raise CampaignError(
            f"child {child_id} requires a bound canonical mission before {target}"
        )
    if (
        child.status in {"FAILED", "BLOCKED", "UNKNOWN"}
        and target in {"READY", "RUNNING"}
        and not _retry_receipt_authorizes_reopen(database, child)
    ):
        raise CampaignError(
            f"child {child_id} requires an explicit budgeted retry reservation before {target}"
        )
    if target == "SUCCEEDED":
        accepted = _validate_child_acceptance(database, child, acceptance_outcome_id=acceptance_outcome_id, acceptance_identity=acceptance_identity)
        acceptance_outcome_id = str(accepted.get("outcome_id") or "")
        acceptance_identity = str(accepted.get("acceptance_identity") or "")
    if wait_kind:
        wait_kind = str(wait_kind).upper()
        if wait_kind not in CHILD_WAIT_KINDS:
            raise CampaignError(f"unsupported child wait kind: {wait_kind}")
    if target == "WAITING" and not (wait_kind or wait_reason or reason):
        raise CampaignError("WAITING child state requires a durable wait condition")
    if target == "WAITING" and wait_kind == "TIME" and not wait_until:
        raise CampaignError("TIME waits require wait_until")
    if target == "WAITING" and wait_until and _dt(wait_until) is None:
        raise CampaignError("wait_until must be an ISO-8601 timestamp")
    if (
        target == "WAITING"
        and wait_kind == "PROVIDER"
        and not _child_provider_ids(database, child)
    ):
        raise CampaignError("PROVIDER waits require a compatible provider set")
    result = _append_child_state_event(
        database,
        child,
        target,
        reason=reason or ("accepted final outcome" if target == "SUCCEEDED" else target.lower()),
        wait_kind=wait_kind,
        wait_reason=wait_reason,
        wait_until=wait_until,
        acceptance_outcome_id=acceptance_outcome_id,
        acceptance_identity=acceptance_identity,
        controller=controller,
        stop_path=stop_path,
    )
    _sync_campaign_readiness(
        database,
        child.campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    checkpoint_campaign(database, child.campaign_id, controller=controller, stop_path=stop_path)
    return result


def reconcile_child_effects(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Reconcile every bound effect before a child can satisfy a join.

    A STARTED effect with no terminal receipt is frozen append-only.  It is
    never performed a second time.  Provider-side reconciliation remains an
    explicit effect-layer operation; this function records the honest
    ``FROZEN/UNKNOWN`` boundary and parks the child.
    """

    _gate(database, controller, stop_path=stop_path, campaign_id=campaign_id)
    state = rebuild_campaign_state(database)
    results: list[dict[str, Any]] = []
    for child in tuple(state.children.values()):
        if child.campaign_id != campaign_id or not child.effect_keys:
            continue
        for key in child.effect_keys:
            try:
                before = effect_state(database, key)
            except Exception as error:  # noqa: BLE001 - unknown effect is a blocker
                planned_not_started = bool(
                    child.effect_action_key == key
                    and child.wait_kind != "EFFECT_RECONCILIATION"
                    and not any(
                        event.event_type == "system.action_started"
                        and str(event.payload.get("action_key") or "") == key
                        for event in database.events(
                            readonly=True,
                            event_type_prefixes=("system.action_",),
                        )
                    )
                )
                before = {
                    "action_key": key,
                    "status": "NOT_STARTED" if planned_not_started else "UNKNOWN",
                    "reason": (
                        "declared campaign effect has not been attempted"
                        if planned_not_started else type(error).__name__
                    ),
                }
            status = _safe_effect_status(database, key)
            if (
                status == "UNKNOWN"
                and child.effect_action_key == key
                and child.wait_kind != "EFFECT_RECONCILIATION"
                and not any(
                    event.event_type == "system.action_started"
                    and str(event.payload.get("action_key") or "") == key
                    for event in database.events(
                        readonly=True,
                        event_type_prefixes=("system.action_",),
                    )
                )
            ):
                status = "NOT_STARTED"
            if status == "STARTED":
                try:
                    freeze_effect(database, key, reason="campaign startup found an in-flight effect; provider reconciliation is required")
                    status = "FROZEN"
                except (EffectStateError, ValueError):
                    status = _safe_effect_status(database, key)
            payload = {
                "schema": CAMPAIGN_SCHEMA,
                "campaign_id": campaign_id,
                "child_id": child.child_id,
                "action_key": key,
                "status": status,
                "reason": (
                    "declared effect has not been attempted"
                    if status == "NOT_STARTED"
                    else "effect is terminal"
                    if status in {"COMPLETED", "FAILED"}
                    else "effect requires explicit reconciliation"
                ),
            }
            try:
                _append(
                    database,
                    "campaign.effect_reconciled",
                    payload,
                    key=f"campaign.effect_reconciled:{campaign_id}:{child.child_id}:{key}:{status}",
                    campaign_id=campaign_id,
                )
            except EventConflict:
                # Effect reconciliation is a durable observation.  Replaying
                # startup after the same terminal/frozen status must be a
                # read/no-op, not a partial-resume fault.
                pass
            results.append(payload)
            if status in {"FROZEN", "UNKNOWN", "FAILED"}:
                current = rebuild_campaign_state(database).children.get(child.child_id)
                if current is not None and current.status not in TERMINAL_CHILD_STATUSES and current.status != "WAITING":
                    _append_child_state_event(
                        database,
                        current,
                        "WAITING",
                        reason="partial effect requires reconciliation before join",
                        wait_kind="EFFECT_RECONCILIATION",
                        wait_reason=f"effect {key} is {status}",
                        controller=controller,
                        stop_path=stop_path,
                    )
    _sync_campaign_readiness(
        database,
        campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    return results


def reconcile_campaign_children(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
    reconcile_missions: bool = True,
) -> list[str]:
    """Reconcile mission/verifier truth into child lifecycle state.

    ``startup_resume_pass`` performs the global terminal-mission repair once
    before iterating campaigns and passes ``reconcile_missions=False`` to
    avoid rescanning every Executive plan for every campaign.  Direct callers
    retain the safe default and repair canonical mission outcomes first.
    """

    _gate(database, controller, stop_path=stop_path, campaign_id=campaign_id)
    if reconcile_missions:
        from .autonomy import reconcile_terminal_mission_outcomes

        reconcile_terminal_mission_outcomes(database, controller=controller)
    _reconcile_dependency_waits(
        database,
        campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    state = rebuild_campaign_state(database)
    changed: list[str] = []
    for child in tuple(state.children.values()):
        if child.campaign_id != campaign_id or child.status == "STOPPED":
            continue
        mission_row = (
            database.row("missions", "mission_id", child.mission_id)
            if child.mission_id else None
        )
        if (
            mission_row is not None
            and str(mission_row.get("state") or "").upper() == "COMPLETE"
            and _accepted_outcome(database, child.mission_id) is None
        ):
            # Campaign missions are objective-owned even when they are not a
            # step in the legacy Executive plan projection.  Close the same
            # canonical V11 final-outcome/learning boundary used by global
            # terminal reconciliation before evaluating child acceptance.
            from ..world_model.connector import reconcile_outcome_learning

            reconcile_outcome_learning(
                database,
                mission_id=child.mission_id,
                source_id="campaign-terminal-reconciler",
            )
        truth = _child_mission_truth(database, child)
        target = str(truth.get("status") or "UNKNOWN")
        if (
            child.status == "WAITING"
            and child.wait_kind == "PROVIDER"
            and (
                not _child_provider_ids(database, child)
                or not any(
                    _provider_available(database, provider_id)
                    for provider_id in _child_provider_ids(database, child)
                )
            )
        ):
            # Mission state may still be EXECUTING while its provider is
            # unavailable.  Keep the durable provider wait until a positive
            # recovery observation is seen.
            continue
        if child.status == "WAITING" and child.wait_kind == "EFFECT_RECONCILIATION":
            effect_statuses = {
                _safe_effect_status(database, key)
                for key in child.effect_keys
            }
            if effect_statuses & {"STARTED", "FROZEN", "UNKNOWN"}:
                continue
        if (
            child.status == "WAITING"
            and child.wait_kind not in {None, "DEPENDENCY"}
            and target in {"READY", "RUNNING", "UNKNOWN", "WAITING"}
        ):
            # Specialized waits are released only by their durable wake.
            continue
        # A mission can report READY/COMPLETE while its sibling dependency is
        # still unresolved.  Preserve the dependency wait until accepted
        # sibling truth is present; selector exclusion is defense in depth.
        unmet = _unmet_dependencies(state, child)
        if (
            child.status in {"FAILED", "BLOCKED"}
            and target in {"READY", "RUNNING"}
            and not _retry_receipt_authorizes_reopen(database, child)
        ):
            # A reopened canonical mission is not, by itself, a campaign
            # retry.  Keep the terminal child parked until an explicit,
            # budget-accounted campaign.retry_reserved receipt is present.
            continue
        if unmet and target in {"RUNNING", "UNKNOWN"}:
            target = "WAITING"
        if target == "WAITING":
            if child.status == "WAITING" and (
                child.wait_kind or child.wait_reason
            ):
                # A specialized wait owner (time, provider, dependency,
                # effect, owner, signal, ...) controls release; mission
                # projection alone must not rewrite it as an empty
                # dependency wait.
                continue
            if child.status != "WAITING" or child.wait_kind != "DEPENDENCY":
                try:
                    _append_child_state_event(
                        database,
                        child,
                        "WAITING",
                        reason="child waits for accepted sibling outcomes",
                        wait_kind="DEPENDENCY",
                        wait_reason=f"waiting for dependencies: {', '.join(unmet)}",
                        controller=controller,
                        stop_path=stop_path,
                    )
                    changed.append(child.child_id)
                except CampaignError:
                    pass
        elif target == "RUNNING":
            target = "RUNNING" if child.status in {"PENDING", "READY", "RUNNING", "UNKNOWN", "WAITING"} else child.status
        elif target == "READY":
            # Existing MissionLoop dispatchable states (ACTION_SELECTION,
            # READY, VERIFYING, REPAIRING) are represented as a READY child
            # regardless of whether the child was still PENDING or had been
            # parked in a generic wait.  Specialized waits were preserved by
            # the guards above and are not rewritten here.
            target = "READY" if child.status in {"PENDING", "READY", "RUNNING", "UNKNOWN", "WAITING"} else child.status
        if target in {"READY", "RUNNING"} and target != child.status:
            try:
                _append_child_state_event(
                    database,
                    child,
                    target,
                    reason=str(truth.get("reason") or f"mission is {target.lower()}"),
                    controller=controller,
                    stop_path=stop_path,
                )
                changed.append(child.child_id)
            except CampaignError:
                # The mission projection may have advanced in a way the
                # child lifecycle cannot safely mirror.  Leave the durable
                # child state untouched for the next recovery pass.
                pass
        elif target == "SUCCEEDED":
            outcome = truth.get("outcome") or {}
            outcome_id = str(outcome.get("outcome_id") or "")
            acceptance_identity = str(outcome.get("acceptance_identity") or "")
            # Accepted child state is a durable projection of canonical
            # verifier truth.  Once both identities match, replaying startup
            # must be a read/no-op; emitting another same-status event on
            # every bounded poll would grow the ledger forever.
            if (
                child.status == "SUCCEEDED"
                and child.acceptance_outcome_id == outcome_id
                and (
                    not acceptance_identity
                    or child.acceptance_identity == acceptance_identity
                )
            ):
                continue
            result = _append_child_state_event(
                database,
                child,
                "SUCCEEDED",
                reason=str(truth.get("reason") or "accepted final outcome"),
                acceptance_outcome_id=outcome_id,
                acceptance_identity=acceptance_identity,
                controller=controller,
                stop_path=stop_path,
            )
            if result.child_id == child.child_id and result.status == "SUCCEEDED":
                changed.append(child.child_id)
        elif target in {"FAILED", "BLOCKED", "CANCELLED", "UNKNOWN"} and target != child.status:
            try:
                _append_child_state_event(
                    database,
                    child,
                    target,
                    reason=str(truth.get("reason") or "mission truth reconciled"),
                    controller=controller,
                    stop_path=stop_path,
                )
                changed.append(child.child_id)
            except CampaignError:
                # A non-monotonic mission observation is preserved as UNKNOWN
                # rather than fabricating a lifecycle transition.
                if child.status not in TERMINAL_CHILD_STATUSES:
                    try:
                        _append_child_state_event(
                            database,
                            child,
                            "UNKNOWN",
                            reason=f"mission truth {target} could not transition from {child.status}",
                            controller=controller,
                            stop_path=stop_path,
                        )
                        changed.append(child.child_id)
                    except CampaignError:
                        pass
    if changed:
        checkpoint_campaign(database, campaign_id, controller=controller, stop_path=stop_path)
    return changed


def checkpoint_campaign(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
    reason: str = "durable campaign checkpoint",
    allow_stop: bool = False,
) -> CampaignCheckpoint:
    """Persist an exact pending-work reconstruction for one campaign."""

    _gate(
        database,
        controller,
        stop_path=stop_path,
        campaign_id=campaign_id,
        allow_stop=allow_stop,
    )
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    children = [child for child in state.children.values() if child.campaign_id == campaign_id]
    children.sort(key=lambda row: row.child_id)
    completed = tuple(
        child.child_id
        for child in children
        if (
            child.status == "SUCCEEDED"
            and child.acceptance_outcome_id
            and _accepted_child_truth_is_durable(database, child)
        )
    )
    join_excluded: set[str] = set()
    if campaign.status in {"COMPLETED", "CANCELLED"}:
        # Terminal campaigns have no remaining dispatchable work, even if a
        # non-winning ANY sibling retains a durable FAILED/CANCELLED receipt.
        join_excluded.update(child.child_id for child in children)
    else:
        for join_id, spec in campaign.join_conditions.items():
            if (
                spec.get("required", True)
                and campaign.join_results.get(join_id, {}).get("status") == "SATISFIED"
            ):
                join_excluded.update(_tuple(spec.get("children")))
    pending = tuple(
        child.child_id
        for child in children
        if child.child_id not in completed
        and child.child_id not in join_excluded
        and child.status not in {"CANCELLED"}
    )
    wakes = rebuild_wake_state(database)
    required_wakes = tuple(
        dict(wake) for wake in sorted(wakes.values(), key=lambda value: str(value.get("wake_id")))
        if str(wake.get("wake_id") or "").find(f":campaign:{campaign_id}:") >= 0 and wake.get("status") == "ACTIVE"
    )
    pending_work: list[dict[str, Any]] = []
    for child in children:
        if child.child_id in pending:
            effect_states = {
                key: _safe_effect_status(database, key)
                for key in child.effect_keys
            }
            pending_work.append({
                "child_id": child.child_id,
                "mission_id": child.mission_id,
                "step_id": child.step_id,
                "status": child.status,
                "dependencies": list(child.dependencies),
                "unmet_dependencies": [dep for dep in child.dependencies if dep not in completed],
                "attempt": child.attempt,
                "retries_used": child.retries_used,
                "max_retries": child.max_retries,
                "provider_id": child.provider_id,
                "wait_kind": child.wait_kind,
                "wait_until": child.wait_until,
                "effect_states": effect_states,
                "next_action": child.wait_reason or child.last_reason or ("dispatch" if child.status == "READY" else "wait"),
            })
    # Compare semantic pending work before allocating a new revision.  A
    # worker may run this pass every bounded cycle; an unchanged projection
    # must not create an unbounded stream of equivalent checkpoints.
    semantic_material = {
        "status": campaign.status,
        "completed_children": list(completed),
        "pending_children": list(pending),
        "pending_work": pending_work,
        "required_wakes": list(required_wakes),
        "joins": {str(k): dict(v) for k, v in campaign.join_results.items()},
        "deadline": campaign.deadline,
        "retry_budget": dict(campaign.retry_budget),
        "retries_used": campaign.retries_used,
    }
    previous = state.checkpoints.get(campaign_id)
    if previous is not None:
        previous_semantic = {
            "status": previous.status,
            "completed_children": list(previous.completed_children),
            "pending_children": list(previous.pending_children),
            "pending_work": [dict(v) for v in previous.pending_work],
            "required_wakes": [dict(v) for v in previous.required_wakes],
            "joins": {str(k): dict(v) for k, v in previous.joins.items()},
            "deadline": previous.deadline,
            "retry_budget": dict(previous.retry_budget),
            "retries_used": previous.retries_used,
        }
        if previous_semantic == semantic_material:
            return previous
    version = max(campaign.checkpoint_version, previous.version if previous else 0) + 1
    created_at = isoformat()
    checkpoint_id = f"checkpoint:{campaign_id}:v{version}"
    material = {
        "schema": CAMPAIGN_SCHEMA,
        "checkpoint_id": checkpoint_id,
        "campaign_id": campaign_id,
        "objective_id": campaign.objective_id,
        "version": version,
        **semantic_material,
        "created_at": created_at,
    }
    projection_hash = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    checkpoint = CampaignCheckpoint(
        checkpoint_id=checkpoint_id,
        campaign_id=campaign_id,
        objective_id=campaign.objective_id,
        version=version,
        status=campaign.status,
        completed_children=completed,
        pending_children=pending,
        pending_work=tuple(pending_work),
        required_wakes=required_wakes,
        joins={str(k): dict(v) for k, v in campaign.join_results.items()},
        deadline=campaign.deadline,
        retry_budget=dict(campaign.retry_budget),
        retries_used=campaign.retries_used,
        created_at=created_at,
        projection_hash=projection_hash,
    )
    try:
        _append(
            database,
            "campaign.checkpoint_recorded",
            {**checkpoint.to_payload(), "reason": reason},
            # The semantic projection, not the wall-clock receipt, is the
            # idempotency identity.  Two Supervisor recovery passes racing on
            # the same checkpoint therefore converge on one event; the loser
            # reloads the winner below instead of creating duplicate vN rows.
            key=f"campaign.checkpoint_recorded:{campaign_id}:v{version}:{_hash(semantic_material)}",
            campaign_id=campaign_id,
        )
    except EventConflict:
        concurrent = rebuild_campaign_state(database).checkpoints.get(campaign_id)
        if concurrent is None or concurrent.version < version:
            raise
        return concurrent
    return checkpoint


def _latest_provider_observation(database: FoundryDatabase, provider_id: str) -> dict[str, Any] | None:
    wanted = str(provider_id)
    for event in database.events(readonly=True, newest_first=True, limit=5000):
        if not event.event_type.startswith(("provider.", "capability.", "connector.")):
            # Campaign/mission payloads also carry provider_id as binding
            # metadata.  They are not health observations and must never
            # shadow the last real outage/recovery sample.
            continue
        payload = event.payload
        identity = str(payload.get("provider_id") or payload.get("connector_id") or payload.get("provider") or "")
        if identity != wanted and wanted not in identity:
            continue
        status = str(payload.get("status") or payload.get("health") or payload.get("readiness") or payload.get("availability") or "UNKNOWN").upper()
        if event.event_type in {"provider.outage", "provider.unavailable", "provider.failed", "connector.outage"}:
            status = "UNAVAILABLE"
        return {"status": status, "event_type": event.event_type, "event_id": event.event_id, "recorded_at": event.recorded_at, "payload": dict(payload)}
    return None


def _provider_available(database: FoundryDatabase, provider_id: str) -> bool:
    sample = _latest_provider_observation(database, provider_id)
    if sample is None:
        return False
    payload = sample.get("payload") if isinstance(sample.get("payload"), Mapping) else {}
    # When a provider event carries the richer V8 health contract, require all
    # three positive dimensions.  Legacy status-only observations remain
    # accepted for compatibility with pre-V8 ledgers.
    rich_contract = any(
        key in payload
        for key in ("health", "auth_state", "auth", "readiness", "availability")
    )
    dimensions = (
        payload.get("health") or payload.get("status"),
        payload.get("auth_state") or payload.get("auth") or payload.get("readiness"),
        payload.get("availability"),
    )
    if rich_contract:
        health, auth, availability = (str(value or "UNKNOWN").upper() for value in dimensions)
        return (
            health in {"HEALTHY", "READY"}
            and auth in {"READY", "CONFIGURED"}
            and availability in {"AVAILABLE", "READY"}
        )
    status = str(sample.get("status") or "UNKNOWN").upper()
    if status not in {"READY", "HEALTHY", "AVAILABLE", "ACTIVE", "CONFIGURED"}:
        return False
    return True


def _open_mission_provider_wait(
    database: FoundryDatabase,
    child: ChildMissionRecord,
) -> tuple[str, ...] | None:
    if not child.mission_id:
        return None
    from .continuous import (
        canonical_provider_resume_events,
        canonical_provider_wait_start_events,
    )

    resumed = {
        str(event.payload.get("wait_id") or "")
        for event in canonical_provider_resume_events(
            database,
            mission_id=child.mission_id,
        )
    }
    starts = canonical_provider_wait_start_events(
        database,
        mission_id=child.mission_id,
    )
    for event in reversed(starts):
        payload = event.payload
        if str(payload.get("wait_id") or "") not in resumed:
            return tuple(dict.fromkeys(
                str(item) for item in payload.get("provider_ids") or ()
                if str(item)
            ))
    return None


def _child_provider_ids(
    database: FoundryDatabase,
    child: ChildMissionRecord,
) -> tuple[str, ...]:
    open_wait = _open_mission_provider_wait(database, child)
    if open_wait is not None:
        return open_wait
    if child.routing_requirements:
        request = RoutingRequest.from_mapping(child.routing_requirements)
        if request.strict_identity is not None:
            return (request.strict_identity[0],)
        # An explicit allow-set is the exact compatible provider universe.
        # Without one, provider discovery/routing remains the registry's job;
        # the campaign must not park on only its preference and hide a valid
        # alternate from the canonical router.
        if request.allowed_providers:
            return tuple(request.allowed_providers)
        return ()
    return (child.provider_id,) if child.provider_id else ()


def _handle_provider_states(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str,
    stop_path: str | Path | None,
) -> tuple[list[str], list[str]]:
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    waiting: list[str] = []
    recovered: list[str] = []
    for child in tuple(state.children.values()):
        provider_ids = _child_provider_ids(database, child)
        if child.campaign_id != campaign_id or not provider_ids:
            continue
        if _unmet_dependencies(state, child):
            # Dependency ordering is evaluated before provider readiness.  A
            # provider outage is re-probed once the sibling join releases the
            # child, avoiding a lossy overwrite of the dependency wait.
            continue
        samples = {
            provider: _latest_provider_observation(database, provider)
            for provider in provider_ids
        }
        if (
            _open_mission_provider_wait(database, child) is None
            and any(sample is None for sample in samples.values())
        ):
            # Pre-dispatch campaign reconstruction does not invent provider
            # unavailability from a missing sample.  The canonical router
            # has the registered profiles; once it classifies an all-down
            # episode, mission.provider_wait_started becomes the exact wait
            # universe consumed here on every restart.
            continue
        statuses = {
            provider: str((sample or {}).get("status") or "UNKNOWN").upper()
            for provider, sample in samples.items()
        }
        if child.status in TERMINAL_CHILD_STATUSES:
            continue
        available = tuple(
            provider for provider in provider_ids
            if _provider_available(database, provider)
        )
        provider_available = bool(available)
        open_mission_wait = _open_mission_provider_wait(database, child)
        if not provider_available:
            if child.status not in {"WAITING", "STOPPED"} or child.wait_kind != "PROVIDER":
                _append_child_state_event(
                    database,
                    child,
                    "WAITING",
                    reason=(
                        "all compatible providers unavailable: "
                        + ", ".join(
                            f"{provider}={statuses[provider]}"
                            for provider in provider_ids
                        )
                    ),
                    wait_kind="PROVIDER",
                    wait_reason=(
                        "WAIT_FOR_PROVIDER: " + ", ".join(provider_ids)
                    ),
                    controller=controller,
                    stop_path=stop_path,
                )
            waiting.append(child.child_id)
        elif child.status == "WAITING" and child.wait_kind == "PROVIDER":
            if open_mission_wait is not None:
                # The mission wait carries the exact run and compatible
                # provider set.  Only resume_mission_provider_waits may
                # release it; campaign startup must not race that authority.
                continue
            recovered_provider = available[0]
            recovered.append(child.child_id)
            # Make the recovery observable through the existing wake
            # authority before releasing the child.  A positive provider
            # sample can predate wake creation (for example after a kill
            # between classification and projection), so trigger the
            # campaign-specific wake explicitly when it is still ACTIVE.
            for wake in rebuild_wake_state(database).values():
                wake_id = str(wake.get("wake_id") or "")
                if (
                    wake.get("status") == "ACTIVE"
                    and wake.get("kind") == "WAIT_FOR_PROVIDER"
                    and str(wake.get("objective_id") or "") == campaign.objective_id
                    and (
                        str(wake.get("provider_id") or "") in available
                        or f":campaign:{campaign_id}:child:{child.child_id}:" in wake_id
                    )
                ):
                    try:
                        trigger_wake(
                            database,
                            wake_id,
                            controller=controller,
                            reason=f"provider {recovered_provider} recovery observed",
                        )
                    except (EventConflict, ValueError):
                        pass
            # Preserve the canonical MissionLoop state when the provider
            # outage interrupted an in-flight mission.  Only a dispatchable
            # mission (or an as-yet-unbound child) may become READY; an
            # executing mission remains RUNNING until MissionLoop recovery
            # advances it through its own state machine.
            truth = _child_mission_truth(database, child)
            target = (
                "RUNNING"
                if child.mission_id and truth.get("status") == "RUNNING"
                else "READY"
                if child.mission_id
                else "PENDING"
            )
            try:
                _append_child_state_event(
                    database,
                    child,
                    target,
                    reason=f"provider {recovered_provider} recovered; wake released",
                    controller=controller,
                    stop_path=stop_path,
                )
            except CampaignError:
                pass
    if recovered and campaign.status == "WAITING":
        campaign = _campaign_status_event(
            database,
            campaign,
            "EXECUTING",
            reason="provider recovery wake released a child",
            controller=controller,
            stop_path=stop_path,
        )
        _mirror_objective_active_after_campaign_resume(
            database,
            campaign,
            controller=controller,
        )
    _sync_campaign_readiness(
        database,
        campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    return waiting, recovered


def _wake_id_for(
    campaign: CampaignRecord,
    child_id: str,
    kind: str,
    condition: Mapping[str, Any],
    *,
    revision: int,
    wakes: Mapping[str, Mapping[str, Any]],
) -> str:
    base = f"wake:{campaign.objective_id}:campaign:{campaign.campaign_id}:child:{child_id}:{kind}:{_hash(condition, length=12)}"
    prior = wakes.get(base)
    # TRIGGERED is a durable notification, not proof that the child was
    # released.  Reusing the same generation while its release predicate is
    # still false avoids creating a fresh wake on every bounded startup pass.
    # Callers include a child/campaign generation in the condition whenever a
    # wait is intentionally re-entered, so a later wait still receives a new
    # identity after this one is consumed.
    if prior is None or prior.get("status") in {"ACTIVE", "TRIGGERED"}:
        return base
    return f"{base}:r{max(1, revision)}"


def rebuild_required_wakes(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> list[str]:
    """Re-derive missing waits from current durable child/checkpoint state."""

    _gate(database, controller, stop_path=stop_path, campaign_id=campaign_id)
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    wakes = rebuild_wake_state(database)
    created: list[str] = []
    cleared: list[str] = []
    specs: list[tuple[str, str, dict[str, Any], str | None]] = []
    unresolved = [
        child
        for child in state.children.values()
        if (
            child.campaign_id == campaign_id
            and campaign.status not in {"COMPLETED", "CANCELLED"}
            # FAILED is terminal mission truth but still requires an owner
            # retry/replan wake.  Only accepted/cancelled children disappear
            # from the pending-work boundary.
            and (
                child.status not in {"SUCCEEDED", "CANCELLED"}
                or not _accepted_child_truth_is_durable(database, child)
            )
        )
    ]
    for child in sorted(unresolved, key=lambda row: row.child_id):
        if child.status == "WAITING":
            kind = str(child.wait_kind or "SIGNAL").upper()
            provider_ids = (
                _child_provider_ids(database, child)
                if kind == "PROVIDER" else ()
            )
            conditions = provider_ids or (None,)
            for provider_id in conditions:
                condition: dict[str, Any] = {
                    "wait_kind": kind,
                    "wait_until": child.wait_until,
                    "provider_id": provider_id,
                    "reason": child.wait_reason,
                    "generation": child.version,
                }
                specs.append((child.child_id, kind, condition, child.wait_until))
        unmet = [dep for dep in child.dependencies if state.children.get(dep) is None or state.children[dep].status != "SUCCEEDED"]
        if (
            unmet
            and child.status not in {"RUNNING", "STOPPED"}
            and not (child.status == "WAITING" and child.wait_kind == "DEPENDENCY")
        ):
            specs.append((child.child_id, "DEPENDENCY", {"dependencies": unmet, "generation": child.version}, None))
        if child.status in {"FAILED", "BLOCKED", "UNKNOWN"} or (
            child.status == "SUCCEEDED"
            and not _accepted_child_truth_is_durable(database, child)
        ):
            # Failed/unknown child truth cannot be retried or guessed around
            # by startup.  Preserve an explicit owner wake for review/replan
            # (and for a bounded retry reservation) instead of leaving a
            # WAITING campaign with no future event that can wake it.
            specs.append((
                child.child_id,
                "OWNER",
                {
                    "status": child.status,
                    "reason": child.last_reason,
                    "generation": child.version,
                },
                None,
            ))
    if campaign.deadline and campaign.status not in {"COMPLETED", "CANCELLED"}:
        specs.append(("deadline", "DEADLINE", {"deadline": campaign.deadline, "replans_used": campaign.replans_used}, campaign.deadline))
    if campaign.status in {"ESCALATED", "REPLANNING"}:
        specs.append(("owner", "OWNER", {"status": campaign.status, "replans_used": campaign.replans_used}, None))
    objective = None
    try:
        from .autonomy import rebuild_executive_state

        objective = rebuild_executive_state(database).objectives.get(campaign.objective_id)
        objective_blocked = objective is not None and objective.status in {
            "FAILED", "CANCELLED", "SUSPENDED",
        }
    except (KeyError, TypeError, ValueError):
        objective_blocked = False
    blocked_join = any(
        spec.get("required", True)
        and campaign.join_results.get(join_id, {}).get("status") == "BLOCKED"
        for join_id, spec in campaign.join_conditions.items()
    )
    if campaign.status not in {"COMPLETED", "CANCELLED"} and (
        objective_blocked or blocked_join
    ):
        specs.append((
            "owner-completion",
            "OWNER",
            {
                "status": campaign.status,
                "objective_status": (
                    objective.status if objective is not None else "UNKNOWN"
                ),
                "reason": (
                    "objective authority is terminal/suspended"
                    if objective_blocked
                    else "required campaign join is blocked"
                ),
                "generation": campaign.version,
            },
            None,
        ))
    desired_wake_ids: set[str] = set()
    for child_id, kind, condition, not_before in specs:
        wake_kind = {
            "TIME": "WAIT_UNTIL_TIME",
            "DEADLINE": "WAIT_UNTIL_TIME",
            "PROVIDER": "WAIT_FOR_PROVIDER",
            "DEPENDENCY": "WAIT_FOR_SIGNAL",
            "SIGNAL": "WAIT_FOR_SIGNAL",
            "OWNER": "WAIT_FOR_OWNER",
            "RESOURCE": "WAIT_FOR_RESOURCE",
            "EFFECT_RECONCILIATION": "WAIT_FOR_SIGNAL",
        }.get(kind, "WAIT_FOR_SIGNAL")
        wake_id = _wake_id_for(campaign, child_id, wake_kind, condition, revision=campaign.checkpoint_version + 1, wakes=wakes)
        desired_wake_ids.add(wake_id)
        if wake_id in wakes and wakes[wake_id].get("status") == "ACTIVE":
            continue
        child = state.children.get(child_id)
        reason = f"[campaign:{campaign_id}:child:{child_id}] {condition.get('reason') or kind.lower()}"
        wake = WakeCondition(
            wake_id=wake_id,
            objective_id=campaign.objective_id,
            kind=wake_kind,
            reason=reason,
            created_at=isoformat(),
            not_before=not_before if wake_kind == "WAIT_UNTIL_TIME" else None,
            provider_id=(
                str(condition.get("provider_id"))
                if wake_kind == "WAIT_FOR_PROVIDER" and condition.get("provider_id")
                else None
            ),
            resource_id=(
                str(child.isolation_key)
                if wake_kind == "WAIT_FOR_RESOURCE"
                and child is not None
                and child.isolation_key
                else None
            ),
            signal=(
                str(condition.get("signal") or "campaign.child_state_changed")
                if wake_kind == "WAIT_FOR_SIGNAL" else None
            ),
        )
        try:
            create_wake(database, wake, controller=controller)
            wakes[wake_id] = wake.to_payload()
            created.append(wake_id)
        except (ValueError, EventConflict):
            continue
    # Clear only campaign-scoped ACTIVE wakes whose release predicate is no
    # longer present (for example an owner wake after apply_replan, or a
    # child wait released by a provider/dependency).  Triggered wakes remain
    # as durable notifications/history and are harmless to future passes.
    campaign_marker = f":campaign:{campaign_id}:"
    for wake_id, wake in tuple(wakes.items()):
        if (
            campaign_marker in str(wake_id)
            and wake.get("status") == "ACTIVE"
            and wake_id not in desired_wake_ids
        ):
            try:
                clear_wake(
                    database,
                    wake_id,
                    controller=controller,
                    reason="campaign wait predicate is no longer required",
                )
                wakes[wake_id]["status"] = "CLEARED"
                cleared.append(wake_id)
            except (ValueError, EventConflict):
                pass
    wake_change_identity = {
        "created": sorted(created),
        "cleared": sorted(cleared),
        "prior": sorted(campaign.wake_ids),
    }
    if created or cleared:
        try:
            _append(
                database,
                "campaign.wakes_rebuilt",
                {"schema": CAMPAIGN_SCHEMA, "campaign_id": campaign_id, "objective_id": campaign.objective_id, "wake_ids": sorted(set((*campaign.wake_ids, *created))), "cleared_wake_ids": sorted(cleared), "reason": "startup re-derived campaign wakes and cleared stale predicates"},
                key=f"campaign.wakes_rebuilt:{campaign_id}:{_hash(wake_change_identity)}",
                campaign_id=campaign_id,
            )
        except EventConflict:
            # A concurrent startup pass may have recorded the same wake
            # projection.  Its wake state is already authoritative.
            pass
    return created


def reconcile_campaign_resource_capacity(
    database: FoundryDatabase,
    resource_capacity: Mapping[str, int],
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist and release cross-campaign resource waits deterministically.

    A child's existing ``isolation_key`` is the resource identity. Running
    children consume capacity first; READY and prior RESOURCE-waiting children
    then compete in stable campaign/child order. No scheduler is introduced:
    blocked children use the existing ``WAIT_FOR_RESOURCE`` wake and recovery
    path.
    """

    _gate(database, controller, stop_path=stop_path)
    capacity: dict[str, int] = {}
    for raw_key, raw_value in dict(resource_capacity or {}).items():
        key = str(raw_key or "").strip()
        if not key:
            raise CampaignError("resource capacity keys must be non-empty")
        value = int(raw_value)
        if value < 0:
            raise CampaignError("resource capacity must be non-negative")
        capacity[key] = value
    if not capacity:
        return {"capacity": {}, "waiting": [], "released": [], "unchanged": True}

    state = rebuild_campaign_state(database)
    used = {key: 0 for key in capacity}
    for child in state.children.values():
        key = str(child.isolation_key or "")
        if child.status == "RUNNING" and key in used:
            used[key] += 1

    candidates = sorted(
        (
            child
            for child in state.children.values()
            if child.isolation_key in capacity
            and (
                child.status == "READY"
                or (child.status == "WAITING" and child.wait_kind == "RESOURCE")
            )
        ),
        key=lambda child: (child.campaign_id, child.child_id),
    )
    admitted: set[str] = set()
    for child in candidates:
        key = str(child.isolation_key)
        if used[key] >= capacity[key]:
            continue
        used[key] += 1
        admitted.add(child.child_id)

    waiting: list[str] = []
    affected_campaigns: set[str] = set()
    for child in candidates:
        if child.child_id in admitted or child.status != "READY":
            continue
        record_child_state(
            database,
            child.child_id,
            "WAITING",
            reason=f"resource capacity exhausted for {child.isolation_key}",
            wait_kind="RESOURCE",
            wait_reason=f"wait for resource {child.isolation_key}",
            controller=controller,
            stop_path=stop_path,
        )
        waiting.append(child.child_id)
        affected_campaigns.add(child.campaign_id)

    for campaign_id in sorted(affected_campaigns):
        rebuild_required_wakes(
            database,
            campaign_id,
            controller=controller,
            stop_path=stop_path,
        )

    released: list[str] = []
    wakes = rebuild_wake_state(database)
    refreshed = rebuild_campaign_state(database)
    for child_id in sorted(admitted):
        child = refreshed.children.get(child_id)
        if child is None or child.status != "WAITING" or child.wait_kind != "RESOURCE":
            continue
        marker = f":campaign:{child.campaign_id}:child:{child.child_id}:WAIT_FOR_RESOURCE:"
        active = next(
            (
                wake
                for wake in wakes.values()
                if marker in str(wake.get("wake_id") or "")
                and wake.get("status") == "ACTIVE"
            ),
            None,
        )
        if active is None:
            rebuild_required_wakes(
                database,
                child.campaign_id,
                controller=controller,
                stop_path=stop_path,
            )
            wakes = rebuild_wake_state(database)
            active = next(
                (
                    wake
                    for wake in wakes.values()
                    if marker in str(wake.get("wake_id") or "")
                    and wake.get("status") == "ACTIVE"
                ),
                None,
            )
        if active is None:
            continue
        trigger_wake(
            database,
            str(active["wake_id"]),
            controller=controller,
            reason=f"resource {child.isolation_key} capacity recovered",
        )
        _recover_triggered_waits(
            database,
            child.campaign_id,
            controller=controller,
            stop_path=stop_path,
        )
        released.append(child.child_id)

    return {
        "capacity": capacity,
        "used": used,
        "waiting": waiting,
        "released": released,
        "admitted": sorted(admitted),
        "unchanged": not waiting and not released,
    }


def _recover_triggered_waits(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str,
    stop_path: str | Path | None,
) -> list[str]:
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    wakes = rebuild_wake_state(database)
    recovered: list[str] = []
    for child in tuple(state.children.values()):
        if child.campaign_id != campaign_id or child.status != "WAITING":
            continue
        matching = [wake for wake in wakes.values() if wake.get("objective_id") == campaign.objective_id and f":campaign:{campaign_id}:child:{child.child_id}:" in str(wake.get("wake_id") or "") and wake.get("status") == "TRIGGERED"]
        if not matching:
            continue

        def rearm_matching(reason: str) -> None:
            for wake in matching:
                try:
                    rearm_wake(
                        database,
                        str(wake["wake_id"]),
                        controller=controller,
                        reason=reason,
                        generation=str(wake.get("triggered_at") or ""),
                    )
                except (EventConflict, ValueError):
                    # A concurrent recovery pass may already have re-armed it.
                    pass

        if child.wait_kind == "PROVIDER":
            provider_ids = _child_provider_ids(database, child)
            if _open_mission_provider_wait(database, child) is not None:
                rearm_matching(
                    "exact MissionLoop provider wait has not resumed yet"
                )
                continue
            if not provider_ids or not any(
                _provider_available(database, provider_id)
                for provider_id in provider_ids
            ):
                rearm_matching("campaign child provider predicate still false")
                continue
        if child.wait_kind == "DEPENDENCY" and _unmet_dependencies(state, child):
            # The wake is only a notification; sibling acceptance truth is
            # still re-read before releasing this child.
            rearm_matching("campaign child dependency predicate still false")
            continue
        if child.wait_kind == "EFFECT_RECONCILIATION":
            effect_statuses = {
                _safe_effect_status(database, key)
                for key in child.effect_keys
            }
            if effect_statuses & {"STARTED", "FROZEN", "UNKNOWN"}:
                rearm_matching("campaign child effect predicate still false")
                continue
        truth = _child_mission_truth(database, child)
        target = (
            "RUNNING"
            if truth.get("status") == "RUNNING" and child.mission_id
            else "READY"
            if child.mission_id
            else "PENDING"
        )
        try:
            _append_child_state_event(
                database,
                child,
                target,
                reason=(
                    "durable wake triggered; exact child work is ready"
                    if target != "PENDING"
                    else "durable wake released; canonical mission still needs materialization"
                ),
                controller=controller,
                stop_path=stop_path,
            )
            recovered.append(child.child_id)
        except CampaignError:
            pass
    if recovered and campaign.status == "WAITING":
        try:
            campaign = _campaign_status_event(
                database,
                campaign,
                "EXECUTING",
                reason="durable child wake released pending work",
                controller=controller,
                stop_path=stop_path,
            )
            _mirror_objective_active_after_campaign_resume(
                database,
                campaign,
                controller=controller,
            )
        except CampaignError:
            pass
    _sync_campaign_readiness(
        database,
        campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    return recovered


def _join_acceptance_ready(children: Sequence[ChildMissionRecord]) -> tuple[bool, list[str], list[str]]:
    accepted = [child.child_id for child in children if child.status == "SUCCEEDED" and child.acceptance_outcome_id]
    unresolved = [child.child_id for child in children if child.child_id not in accepted]
    return not unresolved, accepted, unresolved


def _accepted_child_truth_is_durable(
    database: FoundryDatabase,
    child: ChildMissionRecord,
) -> bool:
    """Re-read canonical mission acceptance before terminal campaign work.

    The child projection is a durable receipt, not an independent success
    authority.  This guard keeps a malformed/replayed ``child_state_changed``
    event from completing a campaign without a verifier-accepted final
    outcome and an effect-safe binding.
    """

    if not child.mission_id:
        return False
    truth = _child_mission_truth(database, child)
    outcome = truth.get("outcome")
    return bool(
        truth.get("status") == "SUCCEEDED"
        and truth.get("accepted") is True
        and isinstance(outcome, Mapping)
        and outcome.get("outcome_id")
        and (
            not child.acceptance_outcome_id
            or str(child.acceptance_outcome_id) == str(outcome.get("outcome_id"))
        )
    )


def _completion_eligible(campaign: CampaignRecord, state: CampaignState) -> bool:
    children = [child for child in state.children.values() if child.campaign_id == campaign.campaign_id]
    if not children:
        return False
    joined: set[str] = set()
    for join_id, spec in campaign.join_conditions.items():
        if not spec.get("required", True):
            continue
        joined.update(_tuple(spec.get("children")))
        if campaign.join_results.get(join_id, {}).get("status") != "SATISFIED":
            return False
    for child in children:
        if child.child_id in joined:
            continue
        if child.status != "SUCCEEDED" or not child.acceptance_outcome_id:
            return False
    return True


def _campaign_acceptance_truth_eligible(
    database: FoundryDatabase,
    campaign: CampaignRecord,
    state: CampaignState,
) -> bool:
    """Validate one campaign's terminal claim against canonical truth.

    ``CampaignRecord.status`` and child receipts are projections.  Before an
    objective is allowed to complete, the winning side of every required
    join (and every child outside those joins) must still have a verifier-
    accepted mission outcome and effect-safe bindings.  Keeping this helper
    separate lets objective-level coordination apply the same predicate to
    sibling campaigns without making a second completion authority.
    """

    if not _completion_eligible(campaign, state):
        return False
    children = [
        child
        for child in state.children.values()
        if child.campaign_id == campaign.campaign_id
    ]
    joined_child_ids: set[str] = set()
    for join_id, spec in campaign.join_conditions.items():
        if not spec.get("required", True):
            continue
        result = campaign.join_results.get(join_id, {})
        if result.get("status") != "SATISFIED":
            return False
        join_ids = set(_tuple(spec.get("children")))
        joined_child_ids.update(join_ids)
        accepted_ids = {
            str(value)
            for value in (result.get("accepted_outcome_ids") or ())
        }
        winners = [
            child
            for child in children
            if child.child_id in join_ids
            and child.acceptance_outcome_id
            and (
                not accepted_ids
                or str(child.acceptance_outcome_id) in accepted_ids
            )
        ]
        if not winners or not all(
            _accepted_child_truth_is_durable(database, child)
            for child in winners
        ):
            return False
    return all(
        _accepted_child_truth_is_durable(database, child)
        for child in children
        if child.child_id not in joined_child_ids
    )


def _objective_campaigns_acceptance_eligible(
    database: FoundryDatabase,
    campaign: CampaignRecord,
    state: CampaignState,
) -> bool:
    """Require every non-cancelled campaign for an objective to be settled.

    Campaigns are subordinate decompositions of one Executive objective.  A
    completed child set in one decomposition must not prematurely mark that
    objective complete while another non-cancelled campaign still has pending,
    waiting, failed, or owner-stopped work.  CANCELLED siblings are explicit
    owner decisions and therefore do not hold the objective open.
    """

    for sibling in state.campaigns.values():
        if sibling.objective_id != campaign.objective_id:
            continue
        if sibling.status == "CANCELLED":
            continue
        if sibling.status != "COMPLETED":
            # The candidate campaign is normally still EXECUTING here; its
            # own acceptance predicate is checked below.  Every other
            # non-terminal sibling keeps the objective incomplete.
            if sibling.campaign_id != campaign.campaign_id:
                return False
            continue
        if not _campaign_acceptance_truth_eligible(database, sibling, state):
            return False
    return _campaign_acceptance_truth_eligible(database, campaign, state)


def evaluate_joins(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    join_id: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> dict[str, Any]:
    """Reconcile children, then satisfy joins only from accepted truth."""

    _gate(database, controller, stop_path=stop_path, campaign_id=campaign_id)
    reconcile_campaign_children(database, campaign_id, controller=controller, stop_path=stop_path)
    reconcile_child_effects(database, campaign_id, controller=controller, stop_path=stop_path)
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    ids = (str(join_id),) if join_id else tuple(sorted(campaign.join_conditions))
    outcomes: dict[str, Any] = {}
    for current_join_id in ids:
        spec = campaign.join_conditions.get(current_join_id)
        if spec is None:
            raise CampaignError(f"unknown join: {current_join_id}")
        child_ids = _tuple(spec.get("children"))
        rows = [state.children.get(child_id) for child_id in child_ids]
        missing = [child_id for child_id, row in zip(child_ids, rows) if row is None]
        if missing:
            status, reason, accepted, unresolved = "BLOCKED", f"join references undeclared children: {missing}", [], missing
        else:
            children = [row for row in rows if row is not None]
            accepted_ready, accepted, unresolved = _join_acceptance_ready(children)
            mode = str(spec.get("mode") or "ALL").upper()
            if mode == "ANY":
                # ANY remains a wait while at least one candidate can still
                # produce accepted truth.  It is blocked only when every
                # candidate has terminal non-success truth; one failed
                # sibling must not strand a still-running sibling.
                accepted_ready = bool(accepted)
                if not accepted_ready:
                    all_terminal_non_success = all(
                        child.status in {"FAILED", "BLOCKED", "CANCELLED"}
                        for child in children
                    )
                else:
                    all_terminal_non_success = False
            else:
                all_terminal_non_success = False
            if accepted_ready:
                status, reason = "SATISFIED", f"join {current_join_id} has accepted child truth"
            elif (
                mode == "ANY"
                and all_terminal_non_success
            ) or (
                mode != "ANY"
                and any(child.status in {"FAILED", "BLOCKED", "UNKNOWN", "CANCELLED"} for child in children)
            ):
                status, reason = "BLOCKED", "join cannot proceed until failed/unknown child is replanned or reconciled"
            else:
                status, reason = "WAITING", "join waits for every required child acceptance"
        accepted_outcomes = [str(state.children[item].acceptance_outcome_id) for item in accepted if item in state.children and state.children[item].acceptance_outcome_id]
        prior_result = campaign.join_results.get(current_join_id, {})
        if prior_result.get("status") != status or prior_result.get("accepted_outcome_ids") != accepted_outcomes or prior_result.get("reason") != reason:
            payload = {"schema": CAMPAIGN_SCHEMA, "campaign_id": campaign_id, "objective_id": campaign.objective_id, "join_id": current_join_id, "status": status, "child_ids": list(child_ids), "accepted_outcome_ids": accepted_outcomes, "reason": reason}
            event_type = "campaign.join_satisfied" if status == "SATISFIED" else "campaign.join_blocked"
            try:
                _append(
                    database,
                    event_type,
                    payload,
                    key=f"{event_type}:{campaign_id}:{current_join_id}:{_hash(payload)}",
                    campaign_id=campaign_id,
                )
            except EventConflict:
                # Concurrent startup passes may have recorded the same join
                # truth.  Rebuild below and let the durable winner drive the
                # completion check.
                pass
        outcomes[current_join_id] = {"status": status, "child_ids": list(child_ids), "accepted_outcome_ids": accepted_outcomes, "unresolved": unresolved, "reason": reason}
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    completed = False
    if _completion_eligible(campaign, state):
        completed = complete_campaign(database, campaign_id, controller=controller, stop_path=stop_path)
    return {"campaign_id": campaign_id, "joins": outcomes, "completed": completed, "eligible": completed or _completion_eligible(campaign, state)}


def complete_campaign(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> bool:
    _gate(database, controller, stop_path=stop_path, campaign_id=campaign_id)
    from .autonomy import set_objective_status, rebuild_executive_state

    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    if campaign.status == "COMPLETED":
        # Idempotent callers may arrive after a process died between the
        # campaign.status_changed and campaign.completed receipts.  Repair
        # that terminal summary/checkpoint instead of returning a seemingly
        # complete but unreconciled aggregate.
        _repair_terminal_campaign(
            database,
            campaign,
            controller=controller,
            stop_path=stop_path,
        )
        return True
    # Revalidate this campaign and every sibling decomposition against the
    # canonical mission/verifier projection immediately before changing the
    # Executive objective.  This is deliberately repeated even though
    # evaluate_joins reconciles first: callers may invoke complete_campaign
    # directly after a process restart.
    if not _campaign_acceptance_truth_eligible(database, campaign, state):
        return False
    outcome_ids = [child.acceptance_outcome_id for child in state.children.values() if child.campaign_id == campaign_id and child.acceptance_outcome_id]
    objective = rebuild_executive_state(database).objectives.get(campaign.objective_id)
    if objective is None or objective.status in {
        "CANCELLED", "SUSPENDED", "FAILED", "BLOCKED",
    }:
        return False
    # The campaign parent may complete independently of a sibling
    # decomposition for the same objective.  Only the canonical Executive
    # objective mirror waits for every non-cancelled sibling campaign to
    # settle; otherwise one finished campaign would strand its own terminal
    # receipt behind unrelated pending work.
    objective_eligible = _objective_campaigns_acceptance_eligible(
        database,
        campaign,
        state,
    )
    if objective_eligible and objective.status != "COMPLETED":
        set_objective_status(database, campaign.objective_id, "COMPLETED", controller=controller, reason="campaign completed only after accepted child mission truth", evidence_refs=tuple(str(v) for v in outcome_ids))
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    if campaign.status != "COMPLETED":
        _campaign_status_event(database, campaign, "COMPLETED", reason="all required child missions have accepted final outcomes", controller=controller, stop_path=stop_path)
    payload = {"schema": CAMPAIGN_SCHEMA, "campaign_id": campaign_id, "objective_id": campaign.objective_id, "accepted_outcome_ids": sorted(str(v) for v in outcome_ids), "reason": "all required child missions have accepted final outcomes"}
    _append(database, "campaign.completed", payload, key=f"campaign.completed:{campaign_id}:{_hash(payload)}", campaign_id=campaign_id)
    # Terminal campaigns must not leave deadline/provider/time wakes active;
    # clear only the campaign-scoped identities and preserve unrelated
    # Executive wake history.
    rebuild_required_wakes(
        database,
        campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    checkpoint_campaign(database, campaign_id, controller=controller, stop_path=stop_path, reason="terminal campaign checkpoint")
    return True


def reserve_child_retry(
    database: FoundryDatabase,
    child_id: str,
    *,
    reason: str = "bounded child retry",
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> ChildMissionRecord | None:
    _gate(database, controller, stop_path=stop_path)
    state = rebuild_campaign_state(database)
    child = state.child(child_id)
    campaign = state.campaign(child.campaign_id)
    if child.status not in {"FAILED", "BLOCKED", "UNKNOWN"}:
        raise CampaignError(f"child {child_id} is not retryable from {child.status}")
    total_budget = campaign.retry_budget.get("total")
    # ``total`` bounds the whole campaign; an optional child/step key gives a
    # narrower budget without inventing another retry authority.  A step key
    # wins when the decomposition supplied one, otherwise the stable child
    # identity is used.
    specific_budget = campaign.retry_budget.get(child.child_id)
    if specific_budget is None and child.step_id:
        specific_budget = campaign.retry_budget.get(child.step_id)
    budget_exhausted = (
        child.retries_used >= child.max_retries
        or (
            total_budget is not None
            and campaign.retries_used >= int(total_budget)
        )
        or (
            specific_budget is not None
            and child.retries_used >= int(specific_budget)
        )
    )
    if budget_exhausted:
        from .autonomy import record_escalation

        escalation = record_escalation(database, objective_id=campaign.objective_id, decision="campaign retry budget exhausted", why=f"child {child_id} cannot retry after bounded attempts", evidence_refs=tuple(v for v in (child.acceptance_outcome_id,) if v), options=("owner changes strategy", "owner grants a new retry budget", "cancel campaign"), recommendation="replan or escalate; do not retry the same child", delay_consequence="campaign remains incomplete", controller=controller)
        payload = {"schema": CAMPAIGN_SCHEMA, "campaign_id": campaign.campaign_id, "child_id": child_id, "status": "ESCALATED", "escalation_id": escalation.escalation_id, "reason": "bounded retry budget exhausted"}
        try:
            _append(
                database,
                "campaign.retry_exhausted",
                payload,
                key=f"campaign.retry_exhausted:{campaign.campaign_id}:{child_id}:{child.retries_used}",
                campaign_id=campaign.campaign_id,
            )
        except EventConflict:
            # A repeated owner retry request observes the same bounded
            # exhaustion receipt; it must remain an idempotent no-op.
            pass
        if campaign.status not in {"ESCALATED", "COMPLETED", "CANCELLED"}:
            _campaign_status_event(database, campaign, "ESCALATED", reason="child retry budget exhausted", controller=controller, stop_path=stop_path)
        checkpoint_campaign(
            database,
            campaign.campaign_id,
            controller=controller,
            stop_path=stop_path,
            reason="retry budget exhaustion checkpoint",
        )
        return None
    if not child.mission_id:
        # A retry without a canonical mission would only make the campaign
        # projection claim READY while the MissionLoop has nothing to run.
        # Replan/bind a new mission first; never synthesize dispatch authority.
        raise CampaignError(f"child {child_id} retry requires a bound mission")
    next_attempt = child.attempt + 1
    payload = {"schema": CAMPAIGN_SCHEMA, "campaign_id": campaign.campaign_id, "child_id": child_id, "attempt": next_attempt, "retries_used": child.retries_used + 1, "campaign_retries_used": campaign.retries_used + 1, "reason": reason}
    # Re-opening the canonical mission and reserving the campaign retry are
    # one durable transaction.  A kill between those receipts would
    # otherwise let startup infer READY from a FAILED child without recording
    # the retry budget consumption, enabling an unbounded retry leak.
    reopen_event = _reopen_bound_mission_for_retry(
        database,
        child,
        attempt=next_attempt,
        controller=controller,
    )
    retry_event = Event.create(
        "campaign.retry_reserved",
        payload,
        source_type="supervisor",
        source_id="campaign-runtime",
        correlation_id=campaign.campaign_id,
        idempotency_key=(
            f"campaign.retry_reserved:{campaign.campaign_id}:{child_id}:"
            f"attempt:{next_attempt}"
        ),
    )
    try:
        database.append_events(
            [event for event in (reopen_event, retry_event) if event is not None]
        )
    except EventConflict:
        concurrent_state = rebuild_campaign_state(database)
        concurrent_child = concurrent_state.children.get(child_id)
        concurrent_mission = database.row("missions", "mission_id", child.mission_id)
        if (
            concurrent_child is None
            or concurrent_child.retries_used < child.retries_used + 1
            or str((concurrent_mission or {}).get("state") or "").upper()
            not in {"ACTION_SELECTION", "READY", "REPAIRING", "VERIFYING"}
        ):
            raise
    current = rebuild_campaign_state(database).children[child_id]
    _append_child_state_event(database, current, "RETRYING", reason=reason, controller=controller, stop_path=stop_path)
    current = rebuild_campaign_state(database).children[child_id]
    result = _append_child_state_event(database, current, "READY", reason="retry budget reserved; exact child is ready", controller=controller, stop_path=stop_path)
    _sync_campaign_readiness(
        database,
        campaign.campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    checkpoint_campaign(database, campaign.campaign_id, controller=controller, stop_path=stop_path)
    return result


def _reopen_bound_mission_for_retry(
    database: FoundryDatabase,
    child: ChildMissionRecord,
    *,
    attempt: int,
    controller: str,
) -> Event | None:
    """Re-enter the existing MissionLoop dispatch boundary for a retry.

    Campaign retry accounting is subordinate to the mission state machine.  A
    failed MissionLoop pass normally leaves its mission BLOCKED; merely
    changing the child status would make the campaign appear READY while the
    canonical selector still rejects that mission.  The only safe reopening
    is the already-supported ``BLOCKED -> ACTION_SELECTION`` transition,
    recorded as a normal mission receipt.  Other terminal/unknown states are
    surfaced for an explicit replan instead of guessed around.
    """

    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("mission retry reopening is Supervisor-owned")
    if not child.mission_id:
        raise CampaignError(f"child {child.child_id} has no bound mission")
    mission = database.row("missions", "mission_id", child.mission_id)
    if mission is None:
        raise CampaignError(f"bound mission {child.mission_id} is absent")
    current = str(mission.get("state") or "UNKNOWN").upper()
    if current in {"ACTION_SELECTION", "READY", "REPAIRING", "VERIFYING"}:
        # It is already at the canonical selection boundary; the campaign
        # retry receipt below is sufficient and remains idempotent.
        return None
    if current != "BLOCKED":
        raise CampaignError(
            f"bound mission {child.mission_id} is {current}; explicit replan is required before retry"
        )
    payload = {
        "mission_id": child.mission_id,
        "state": "ACTION_SELECTION",
        "next_action": mission.get("next_action"),
        "run_id": f"campaign-retry-{child.child_id}-{attempt}",
        "reason": f"campaign retry {attempt} reopened the canonical MissionLoop boundary",
    }
    return Event.create(
        "mission.state_changed",
        payload,
        source_type="supervisor",
        source_id="campaign-runtime",
        correlation_id=child.campaign_id,
        idempotency_key=(
            f"mission.state_changed:{child.mission_id}:ACTION_SELECTION:"
            f"campaign-retry:{attempt}"
        ),
    )


def handle_deadlines(
    database: FoundryDatabase,
    *,
    now: datetime | str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> dict[str, list[str]]:
    """Turn expired deadlines into durable replan/escalate decisions."""

    _gate(database, controller, stop_path=stop_path)
    current = _dt(now) or datetime.now(timezone.utc)
    result = {"replan_required": [], "escalated": []}
    state = rebuild_campaign_state(database)
    for campaign in tuple(state.campaigns.values()):
        if campaign.status in {"COMPLETED", "CANCELLED", "ESCALATED", "REPLANNING", "STOPPED"}:
            continue
        deadline = _dt(campaign.deadline)
        if deadline is None or deadline > current:
            continue
        # A process may die after mission/verifier acceptance but before the
        # child projection and join receipt are appended.  Deadline policy is
        # for pending work only; re-read mission truth so accepted work cannot
        # be incorrectly replanned or escalated during startup recovery.
        children = [
            child for child in state.children.values()
            if child.campaign_id == campaign.campaign_id
        ]
        if children and all(
            _accepted_child_truth_is_durable(database, child)
            for child in children
        ):
            continue
        if campaign.replans_used < campaign.max_replans:
            payload = {"schema": CAMPAIGN_SCHEMA, "campaign_id": campaign.campaign_id, "objective_id": campaign.objective_id, "deadline": campaign.deadline, "replan_number": campaign.replans_used + 1, "reason": "campaign deadline reached; replan is required before more dispatch"}
            try:
                _append(database, "campaign.deadline_replan_requested", payload, key=f"campaign.deadline_replan_requested:{campaign.campaign_id}:{campaign.deadline}:{campaign.replans_used + 1}", campaign_id=campaign.campaign_id)
            except EventConflict:
                concurrent = rebuild_campaign_state(database).campaigns.get(campaign.campaign_id)
                if concurrent is None or concurrent.status != "REPLANNING":
                    raise
            result["replan_required"].append(campaign.campaign_id)
        else:
            from .autonomy import record_escalation

            escalation = record_escalation(database, objective_id=campaign.objective_id, decision="campaign deadline exhausted", why=f"deadline {campaign.deadline} passed with pending child work", evidence_refs=tuple(campaign.checkpoint_id for _ in (0,) if campaign.checkpoint_id), options=("owner extends deadline", "owner changes decomposition", "cancel campaign"), recommendation="owner review required; deadline never expires silently", delay_consequence="pending child work remains parked", controller=controller)
            payload = {"schema": CAMPAIGN_SCHEMA, "campaign_id": campaign.campaign_id, "objective_id": campaign.objective_id, "deadline": campaign.deadline, "escalation_id": escalation.escalation_id, "reason": "deadline passed after bounded replan budget"}
            try:
                _append(database, "campaign.deadline_escalated", payload, key=f"campaign.deadline_escalated:{campaign.campaign_id}:{campaign.deadline}:{escalation.escalation_id}", campaign_id=campaign.campaign_id)
            except EventConflict:
                concurrent = rebuild_campaign_state(database).campaigns.get(campaign.campaign_id)
                if concurrent is None or concurrent.status != "ESCALATED":
                    raise
            result["escalated"].append(campaign.campaign_id)
    return result


def apply_replan(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    deadline: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    reason: str = "owner/executive replan applied",
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> CampaignRecord:
    _gate(database, controller, stop_path=stop_path, campaign_id=campaign_id)
    if deadline is not None and _dt(deadline) is None:
        raise CampaignError("campaign deadline must be an ISO-8601 timestamp")
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    if campaign.status == "EXECUTING":
        # Replays after a concurrent owner/recovery apply are idempotent; the
        # existing EXECUTING contract is already the durable result.
        return campaign
    if campaign.status not in {"REPLANNING", "ESCALATED", "FAILED"}:
        raise CampaignError(f"campaign {campaign_id} is not awaiting a replan")
    payload = {"schema": CAMPAIGN_SCHEMA, "campaign_id": campaign_id, "objective_id": campaign.objective_id, "deadline": deadline, "metadata": _mapping(metadata), "status": "EXECUTING", "reason": reason}
    try:
        _append(database, "campaign.replan_applied", payload, key=f"campaign.replan_applied:{campaign_id}:{_hash(payload)}", campaign_id=campaign_id)
    except EventConflict:
        concurrent = rebuild_campaign_state(database).campaigns.get(campaign_id)
        if concurrent is None or concurrent.status != "EXECUTING":
            raise
    result = rebuild_campaign_state(database).campaigns[campaign_id]
    checkpoint_campaign(database, campaign_id, controller=controller, stop_path=stop_path, reason="post-replan checkpoint")
    return result


def propagate_stop(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    reason: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
) -> list[str]:
    """Propagate owner STOP to campaign and child records without cancelling."""

    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("campaign STOP propagation is Supervisor-owned")
    state = rebuild_campaign_state(database)
    global_stop = _stop_reason(database, stop_path)
    scoped_campaigns: list[tuple[CampaignRecord, str]] = []
    for campaign in state.campaigns.values():
        if campaign.status in {"COMPLETED", "CANCELLED"}:
            continue
        scoped_reason = global_stop or _stop_reason(
            database,
            stop_path,
            campaign_id=campaign.campaign_id,
        )
        if scoped_reason:
            scoped_campaigns.append((campaign, scoped_reason))
    if not scoped_campaigns:
        return []
    stopped: list[str] = []
    for campaign, scoped_reason in tuple(scoped_campaigns):
        # Re-read the parent after a concurrent sibling propagation; terminal
        # campaigns are never reopened by a stale STOP loop.
        campaign = rebuild_campaign_state(database).campaigns.get(campaign.campaign_id, campaign)
        if campaign.status in {"COMPLETED", "CANCELLED"}:
            continue
        why = reason or scoped_reason
        now = isoformat()
        changed = False
        if campaign.status != "STOPPED":
            payload = {"schema": CAMPAIGN_SCHEMA, "campaign_id": campaign.campaign_id, "objective_id": campaign.objective_id, "status": "STOPPED", "resume_status": campaign.status, "stop_reason": why, "reason": "owner STOP propagated; exact work remains checkpointed", "updated_at": now, "version": campaign.version + 1}
            # Include the pre-stop campaign version in the idempotency
            # identity.  A STOP is intentionally repeatable over the lifetime
            # of a campaign: after an owner clears STOP and the campaign
            # resumes, a later STOP must produce a new propagation event.
            try:
                _append(
                    database,
                    "campaign.stop_propagated",
                    payload,
                    key=f"campaign.stop_propagated:{campaign.campaign_id}:{campaign.version}:{_hash(why)}",
                    campaign_id=campaign.campaign_id,
                )
            except EventConflict:
                concurrent = rebuild_campaign_state(database).campaigns.get(campaign.campaign_id)
                if concurrent is None or concurrent.status != "STOPPED":
                    raise
            changed = True
        for child in tuple(rebuild_campaign_state(database).children.values()):
            if child.campaign_id != campaign.campaign_id or child.status in TERMINAL_CHILD_STATUSES or child.status == "STOPPED":
                continue
            _append_child_state_event(database, child, "STOPPED", reason="owner STOP propagated; child will resume from this checkpoint", controller=controller, stop_path=stop_path, allow_stop=True, resume_status=child.status, resume_wait_kind=child.wait_kind, resume_wait_reason=child.wait_reason, resume_wait_until=child.wait_until)
            changed = True
        if changed:
            checkpoint_campaign(
                database,
                campaign.campaign_id,
                controller=controller,
                stop_path=stop_path,
                reason="STOP propagation checkpoint",
                allow_stop=True,
            )
            stopped.append(campaign.campaign_id)
    return stopped


def cancel_campaign(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    reason: str = "campaign cancelled by owner/supervisor",
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> CampaignRecord:
    """Cancel a campaign and propagate cancellation to unfinished children.

    Cancellation is distinct from STOP: it is terminal and intentionally
    discards no evidence or effect history.  A later resume is refused; the
    owner must create a new campaign/replan with a new identity.
    """

    _gate(database, controller, stop_path=stop_path, campaign_id=campaign_id)
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    if campaign.status == "CANCELLED":
        return campaign
    if campaign.status == "COMPLETED":
        raise CampaignError("a completed campaign cannot be cancelled")
    _campaign_status_event(
        database,
        campaign,
        "CANCELLED",
        reason=reason,
        controller=controller,
        stop_path=stop_path,
        event_type="campaign.cancelled",
    )
    for child in tuple(rebuild_campaign_state(database).children.values()):
        if child.campaign_id != campaign_id or child.status in TERMINAL_CHILD_STATUSES:
            continue
        _append_child_state_event(
            database,
            child,
            "CANCELLED",
            reason=reason,
            controller=controller,
            stop_path=stop_path,
        )
    # The Executive remains the objective authority; cancellation here only
    # reaches the objective when it is still active and owner evidence allows
    # the canonical status transition.
    try:
        from .autonomy import rebuild_executive_state, set_objective_status
        objective = rebuild_executive_state(database).objectives.get(campaign.objective_id)
        if objective is not None and objective.status not in {"COMPLETED", "CANCELLED", "SUSPENDED"}:
            set_objective_status(
                database,
                campaign.objective_id,
                "CANCELLED",
                controller=controller,
                reason=reason,
                evidence_refs=(f"campaign:{campaign_id}:cancelled",),
            )
    except (KeyError, TypeError, ValueError):
        # Campaign cancellation remains durable even if a historical objective
        # projection is malformed; do not fabricate an objective transition.
        pass
    rebuild_required_wakes(
        database,
        campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    checkpoint_campaign(
        database,
        campaign_id,
        controller=controller,
        stop_path=stop_path,
        reason="terminal cancellation checkpoint",
    )
    return rebuild_campaign_state(database).campaigns[campaign_id]


def cancel_child_mission(
    database: FoundryDatabase,
    child_id: str,
    *,
    reason: str = "child mission cancelled by owner/supervisor",
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> ChildMissionRecord:
    """Cancel one child without rewriting its bound mission/effect history."""

    _gate(database, controller, stop_path=stop_path)
    child = rebuild_campaign_state(database).child(child_id)
    if child.status == "CANCELLED":
        return child
    if child.status == "SUCCEEDED":
        raise CampaignError("an accepted child mission cannot be cancelled")
    result = _append_child_state_event(
        database,
        child,
        "CANCELLED",
        reason=reason,
        controller=controller,
        stop_path=stop_path,
    )
    _sync_campaign_readiness(
        database,
        child.campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    checkpoint_campaign(
        database,
        child.campaign_id,
        controller=controller,
        stop_path=stop_path,
        reason="child cancellation checkpoint",
    )
    return result


def resume_campaign(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> CampaignRecord:
    _gate(database, controller, stop_path=stop_path, campaign_id=campaign_id)
    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    if campaign.status != "STOPPED":
        return campaign
    target_campaign_status = campaign.resume_status or "EXECUTING"
    if target_campaign_status not in _CAMPAIGN_TRANSITIONS["STOPPED"]:
        target_campaign_status = "EXECUTING"
    payload = {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": campaign_id,
        "objective_id": campaign.objective_id,
        "status": target_campaign_status,
        "resume_status": None,
        "reason": "owner STOP cleared; resume from exact checkpoint",
        "updated_at": isoformat(),
        "version": campaign.version + 1,
    }
    try:
        _append(
            database,
            "campaign.resumed",
            payload,
            key=f"campaign.resumed:{campaign_id}:{campaign.version + 1}",
            campaign_id=campaign_id,
        )
    except EventConflict:
        concurrent = rebuild_campaign_state(database).campaigns.get(campaign_id)
        if concurrent is None or concurrent.status == "STOPPED":
            raise
    _resume_stopped_children(
        database,
        campaign_id,
        controller=controller,
        stop_path=stop_path,
    )
    rebuild_required_wakes(database, campaign_id, controller=controller, stop_path=stop_path)
    checkpoint_campaign(database, campaign_id, controller=controller, stop_path=stop_path, reason="post-resume checkpoint")
    return rebuild_campaign_state(database).campaigns[campaign_id]


def _resume_stopped_children(
    database: FoundryDatabase,
    campaign_id: str,
    *,
    controller: str,
    stop_path: str | Path | None,
) -> list[str]:
    """Finish a partially persisted RESUME without inventing child work.

    ``campaign.resumed`` is written before child receipts so a process kill
    cannot leave the parent STOPPED forever.  A later startup pass therefore
    recognizes a resumed parent with remaining STOPPED children and replays
    each exact pre-stop projection.  The helper is idempotent because children
    whose receipt was already applied are no longer STOPPED.
    """

    state = rebuild_campaign_state(database)
    campaign = state.campaign(campaign_id)
    if campaign.status == "STOPPED":
        return []
    changed: list[str] = []
    for child in tuple(state.children.values()):
        if child.campaign_id != campaign_id or child.status != "STOPPED":
            continue
        current_state = rebuild_campaign_state(database)
        current_child = current_state.children.get(child.child_id)
        if current_child is None or current_child.status != "STOPPED":
            continue
        # A child may have completed while STOP was active.  Reconcile that
        # authoritative mission outcome before reopening dispatch; never
        # overwrite accepted truth with a synthetic READY transition.
        truth = _child_mission_truth(database, current_child)
        if truth.get("status") == "SUCCEEDED":
            outcome = truth.get("outcome") or {}
            _append_child_state_event(
                database,
                current_child,
                "SUCCEEDED",
                reason="accepted mission outcome reconciled during resume",
                acceptance_outcome_id=str(outcome.get("outcome_id") or ""),
                acceptance_identity=str(outcome.get("acceptance_identity") or ""),
                controller=controller,
                stop_path=stop_path,
            )
            changed.append(current_child.child_id)
            continue
        deps_ready = all(
            current_state.children.get(dep) is not None
            and current_state.children[dep].status == "SUCCEEDED"
            for dep in current_child.dependencies
        )
        preserved_status = current_child.resume_status
        if preserved_status == "WAITING":
            target_child_status = "WAITING"
            preserved_wait_kind = current_child.resume_wait_kind
            preserved_wait_reason = current_child.resume_wait_reason
            preserved_wait_until = current_child.resume_wait_until
        elif preserved_status in {
            "PENDING", "READY", "RUNNING", "FAILED", "BLOCKED",
            "RETRYING", "UNKNOWN",
        } and deps_ready:
            # Preserve PENDING/FAILED too: STOP/RESUME is not permission to
            # synthesize a retry or a dispatch.
            target_child_status = preserved_status
            preserved_wait_kind = None
            preserved_wait_reason = None
            preserved_wait_until = None
        else:
            target_child_status = "PENDING" if not current_child.mission_id else ("READY" if deps_ready else "WAITING")
            preserved_wait_kind = None if deps_ready else "DEPENDENCY"
            preserved_wait_reason = None if deps_ready else "dependency child is incomplete"
            preserved_wait_until = None
        _append_child_state_event(
            database,
            current_child,
            target_child_status,
            reason="resume released exact child checkpoint" if target_child_status != "WAITING" else "resume preserved exact wait checkpoint",
            wait_kind=preserved_wait_kind,
            wait_reason=preserved_wait_reason,
            wait_until=preserved_wait_until,
            resume_status=None,
            resume_wait_kind=None,
            resume_wait_reason=None,
            resume_wait_until=None,
            controller=controller,
            stop_path=stop_path,
        )
        changed.append(current_child.child_id)
    return changed


def _repair_terminal_campaign(
    database: FoundryDatabase,
    campaign: CampaignRecord,
    *,
    controller: str,
    stop_path: str | Path | None,
) -> list[str]:
    """Finish terminal-parent receipts interrupted by process death.

    Campaign parent and child receipts are intentionally separate events so
    each lifecycle transition remains replayable.  If cancellation commits
    the parent first and the process dies, startup must still propagate
    CANCELLED to every unfinished child; otherwise stale children would look
    like resumable work under a terminal campaign.  A terminal checkpoint is
    also repaired when the process died after the status/completion receipt.
    """

    if campaign.status not in {"COMPLETED", "CANCELLED"}:
        return []
    changed: list[str] = []
    if campaign.status == "CANCELLED":
        # Cancellation is a campaign-level owner decision.  If the process
        # died before cancel_campaign reached the Executive objective seam,
        # replay the same terminal intent on startup (never overwrite an
        # already-completed/suspended objective).
        try:
            from .autonomy import rebuild_executive_state, set_objective_status

            objective = rebuild_executive_state(database).objectives.get(campaign.objective_id)
            if objective is not None and objective.status not in {"COMPLETED", "CANCELLED", "SUSPENDED"}:
                set_objective_status(
                    database,
                    campaign.objective_id,
                    "CANCELLED",
                    controller=controller,
                    reason="terminal campaign cancellation recovered during startup",
                    evidence_refs=(f"campaign:{campaign.campaign_id}:cancelled",),
                )
        except (KeyError, TypeError, ValueError):
            # Preserve the campaign/child cancellation evidence even if an
            # older malformed objective projection needs separate repair.
            pass
        for child in tuple(rebuild_campaign_state(database).children.values()):
            if child.campaign_id != campaign.campaign_id:
                continue
            if child.status in TERMINAL_CHILD_STATUSES:
                continue
            _append_child_state_event(
                database,
                child,
                "CANCELLED",
                reason="terminal campaign cancellation propagated during startup recovery",
                controller=controller,
                stop_path=stop_path,
            )
            changed.append(child.child_id)
    current = rebuild_campaign_state(database).campaigns.get(campaign.campaign_id)
    if current is not None:
        if current.status == "COMPLETED":
            # Reconcile any child receipts that were still in flight when the
            # parent status was committed before validating the terminal
            # completion summary.
            try:
                changed.extend(reconcile_campaign_children(
                    database,
                    current.campaign_id,
                    controller=controller,
                    stop_path=stop_path,
                ))
            except CampaignError:
                # The terminal checkpoint below still records the durable
                # parent state; a malformed child remains visible for owner
                # review rather than being guessed into success.
                pass
            current = rebuild_campaign_state(database).campaigns.get(current.campaign_id)
        if current is not None and current.status == "COMPLETED":
            # ``complete_campaign`` changes the campaign status before its
            # final summary receipt.  Repair that narrow crash window only
            # when the canonical acceptance predicate still holds; a
            # malformed hand-written status event must not gain a synthetic
            # completion claim.
            has_completion_receipt = any(
                event.event_type == "campaign.completed"
                and str(event.payload.get("campaign_id") or "") == current.campaign_id
                for event in database.events(readonly=True)
            )
            state = rebuild_campaign_state(database)
            # Repair this campaign's own terminal receipts independently of
            # sibling objective decompositions.  Only the Executive objective
            # mirror waits for every non-cancelled sibling to settle.
            eligible = _campaign_acceptance_truth_eligible(database, current, state)
            objective_eligible = _objective_campaigns_acceptance_eligible(
                database,
                current,
                state,
            )
            if objective_eligible:
                # The normal completion path marks the Executive objective
                # before the campaign terminal receipt.  If a process died
                # after a hand-written/older campaign status receipt (or in a
                # narrow projection-repair window), restore that canonical
                # objective boundary only when every child still has accepted
                # mission/verifier truth.  Never override a terminal or
                # owner-suspended objective during repair.
                try:
                    from .autonomy import rebuild_executive_state, set_objective_status

                    objective = rebuild_executive_state(database).objectives.get(current.objective_id)
                    if objective is not None and objective.status in {
                        "ACTIVE", "WAITING", "PLANNING", "REGRESSED",
                    }:
                        outcome_ids = [
                            child.acceptance_outcome_id
                            for child in state.children.values()
                            if child.campaign_id == current.campaign_id
                            and child.acceptance_outcome_id
                        ]
                        set_objective_status(
                            database,
                            current.objective_id,
                            "COMPLETED",
                            controller=controller,
                            reason="terminal campaign completion recovered after accepted child truth",
                            evidence_refs=tuple(str(value) for value in outcome_ids),
                        )
                except (KeyError, TypeError, ValueError):
                    # Preserve the terminal campaign receipt/checkpoint for
                    # owner review if a historical objective projection is
                    # malformed or has an incompatible status transition.
                    pass
            if not has_completion_receipt and eligible:
                outcome_ids = [
                    child.acceptance_outcome_id
                    for child in state.children.values()
                    if child.campaign_id == current.campaign_id
                    and child.acceptance_outcome_id
                ]
                payload = {
                    "schema": CAMPAIGN_SCHEMA,
                    "campaign_id": current.campaign_id,
                    "objective_id": current.objective_id,
                    "accepted_outcome_ids": sorted(str(value) for value in outcome_ids),
                    "reason": "completion receipt repaired during startup recovery",
                }
                try:
                    _append(
                        database,
                        "campaign.completed",
                        payload,
                        key=f"campaign.completed:{current.campaign_id}:{_hash(payload)}",
                        campaign_id=current.campaign_id,
                    )
                except EventConflict:
                    pass
                current = rebuild_campaign_state(database).campaigns.get(current.campaign_id)
        if current is not None:
            rebuild_required_wakes(
                database,
                campaign.campaign_id,
                controller=controller,
                stop_path=stop_path,
            )
            checkpoint = rebuild_campaign_state(database).checkpoints.get(campaign.campaign_id)
            if changed or checkpoint is None or checkpoint.status != current.status:
                checkpoint_campaign(
                    database,
                    campaign.campaign_id,
                    controller=controller,
                    stop_path=stop_path,
                    reason="terminal campaign checkpoint repaired during startup",
                )
    return changed


def startup_resume_pass(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    now: datetime | str | None = None,
    resume_id: str | None = None,
    controller: str = SUPERVISOR_CONTROLLER,
    fault_hook: Callable[[str], None] | None = None,
    resource_capacity: Mapping[str, int] | None = None,
) -> CampaignResumeReport:
    """Recover EXECUTING/WAITING campaigns before any new dispatch.

    Ordering is deliberate: STOP -> existing terminal/effect reconciliation
    -> child truth/provider waits -> deadline decisions -> wake re-derivation
    -> joins.
    No action is dispatched here; the normal worker loop remains the sole
    MissionLoop entry point.
    """

    rid = str(resume_id or f"campaign-startup:{database.event_count()}")
    sequence_before = database.event_count()
    stop = _stop_reason(database, stop_path)
    if stop:
        stopped = propagate_stop(database, stop_path=stop_path, reason=stop, controller=controller)
        report = CampaignResumeReport(rid, "STOPPED", campaigns_scanned=len(rebuild_campaign_state(database).campaigns), stopped_campaigns=tuple(stopped), reason=stop)
        if database.event_count() != sequence_before:
            try:
                _append(database, "campaign.resume_passed", report.to_payload(), key=f"campaign.resume_passed:{rid}")
            except (EventConflict, PermissionError):
                pass
        return report
    _gate(database, controller, stop_path=stop_path)
    initial_state = rebuild_campaign_state(database)
    scoped_stops: list[tuple[str, str]] = []
    for campaign in initial_state.campaigns.values():
        if campaign.status in {"COMPLETED", "CANCELLED"}:
            continue
        scoped_reason = _stop_reason(
            database,
            stop_path,
            campaign_id=campaign.campaign_id,
        )
        if scoped_reason:
            scoped_stops.append((campaign.campaign_id, scoped_reason))
    if scoped_stops:
        reason = str(scoped_stops[0][1] or "campaign-scoped owner STOP")
        stopped = propagate_stop(
            database,
            stop_path=stop_path,
            reason=reason,
            controller=controller,
        )
        report = CampaignResumeReport(
            rid,
            "STOPPED",
            campaigns_scanned=len(initial_state.campaigns),
            stopped_campaigns=tuple(stopped),
            reason=reason,
        )
        if database.event_count() != sequence_before:
            try:
                _append(
                    database,
                    "campaign.resume_passed",
                    report.to_payload(),
                    key=f"campaign.resume_passed:{rid}",
                )
            except (EventConflict, PermissionError):
                pass
        return report
    if not initial_state.campaigns:
        # Campaign runtime is opt-in and must be inert on an untouched V11
        # ledger.  In particular, do not emit a new resume receipt on every
        # worker poll when there is no campaign aggregate to recover.
        return CampaignResumeReport(
            rid,
            "READY",
            campaigns_scanned=0,
            reason="no durable campaigns require startup recovery",
        )
    from .autonomy import reconcile_terminal_mission_outcomes

    reconciled_missions = tuple(reconcile_terminal_mission_outcomes(database, controller=controller))
    if fault_hook:
        fault_hook("campaign after terminal reconciliation")
    state = rebuild_campaign_state(database)
    resumed: list[str] = []
    waiting: list[str] = []
    replans: list[str] = []
    escalated: list[str] = []
    completed: list[str] = []
    reconciled_children: list[str] = []
    reconciled_effects: list[str] = []
    rebuilt_wakes: list[str] = []
    errors: list[dict[str, Any]] = []
    # Deadline decisions are intentionally deferred until the first phase has
    # reconciled effects and mission/verifier truth.  A child accepted just
    # before process death must not be replanned merely because its effect
    # receipt has not yet been projected into the campaign aggregate.
    deadline_result: dict[str, list[str]] = {
        "replan_required": [],
        "escalated": [],
    }
    campaign_ids = tuple(state.campaigns)
    # Phase one reconciles every campaign and derives all missing waits.  It
    # deliberately does not tick the global wake set yet; doing that once
    # after this phase avoids O(campaigns * wakes) scans on large objectives.
    for campaign_id in campaign_ids:
        campaign = rebuild_campaign_state(database).campaigns.get(campaign_id)
        if campaign is None:
            continue
        try:
            if campaign.status in {"COMPLETED", "CANCELLED"}:
                repaired = _repair_terminal_campaign(
                    database,
                    campaign,
                    controller=controller,
                    stop_path=stop_path,
                )
                reconciled_children.extend(repaired)
                if campaign.status == "COMPLETED":
                    completed.append(campaign.campaign_id)
                continue
            if campaign.status == "STOPPED":
                campaign = resume_campaign(database, campaign.campaign_id, controller=controller, stop_path=stop_path)
                resumed.append(campaign.campaign_id)
            # A process can die after ``campaign.resumed`` but before all
            # child receipts are appended.  Complete that partial resume even
            # though the parent is already EXECUTING/WAITING in the replayed
            # aggregate; otherwise the remaining STOPPED children would be
            # skipped forever by normal reconciliation.
            partial_children = _resume_stopped_children(
                database,
                campaign.campaign_id,
                controller=controller,
                stop_path=stop_path,
            )
            if partial_children and campaign.campaign_id not in resumed:
                resumed.append(campaign.campaign_id)
            reconciled_effects.extend(item.get("action_key") for item in reconcile_child_effects(database, campaign.campaign_id, controller=controller, stop_path=stop_path) if item.get("action_key"))
            reconciled_children.extend(
                reconcile_campaign_children(
                    database,
                    campaign.campaign_id,
                    controller=controller,
                    stop_path=stop_path,
                    reconcile_missions=False,
                )
            )
            # Re-derive any child-specific wake that existed before this
            # process was interrupted.  This happens before provider recovery
            # release so a positive sample is recorded as a wake trigger.
            rebuilt_wakes.extend(
                rebuild_required_wakes(
                    database,
                    campaign.campaign_id,
                    controller=controller,
                    stop_path=stop_path,
                )
            )
            provider_waiting, _provider_recovered = _handle_provider_states(database, campaign.campaign_id, controller=controller, stop_path=stop_path)
            if provider_waiting:
                waiting.append(campaign.campaign_id)
            # Create at most one shared provider wake per campaign/provider.
            # The existing Continuous Executive wake object remains the
            # scheduler-facing authority; this module only binds its identity
            # to the exact waiting children.  Older histories may already
            # contain the generic wake with a different reason, so a benign
            # idempotency conflict must not strand the campaign.
            provider_children: dict[str, list[str]] = {}
            for child in rebuild_campaign_state(database).children.values():
                if (
                    child.campaign_id == campaign.campaign_id
                    and child.status == "WAITING"
                    and child.wait_kind == "PROVIDER"
                    and child.provider_id
                ):
                    provider_children.setdefault(child.provider_id, []).append(child.child_id)
            for provider_id, child_ids in sorted(provider_children.items()):
                try:
                    schedule_wait_for_provider(
                        database,
                        campaign.objective_id,
                        provider_id=provider_id,
                        reason=(
                            f"campaign {campaign.campaign_id} children "
                            f"{','.join(sorted(child_ids))} wait for provider recovery"
                        ),
                        controller=controller,
                    )
                except EventConflict:
                    # The generic wake is already durable.  Campaign-specific
                    # wakes below still carry exact child generation state.
                    pass
            rebuilt_wakes.extend(
                rebuild_required_wakes(
                    database,
                    campaign.campaign_id,
                    controller=controller,
                    stop_path=stop_path,
                )
            )
        except Exception as error:  # noqa: BLE001 - one campaign cannot strand siblings
            errors.append({"campaign_id": campaign.campaign_id, "error": f"{type(error).__name__}: {error}"[:500]})

    # Now that in-flight effects, mission outcomes, provider waits, and child
    # projections are durable, turn an expired deadline into an explicit
    # replan or escalation.  There is no silent expiry and no dispatch between
    # this decision and the phase-two join/checkpoint reconciliation.
    try:
        deadline_result = handle_deadlines(
            database,
            now=now,
            controller=controller,
            stop_path=stop_path,
        )
    except Exception as error:  # noqa: BLE001 - surface a bounded recovery fault
        errors.append({
            "campaign_id": "*",
            "error": f"deadline recovery failed: {type(error).__name__}: {error}"[:500],
        })

    if resource_capacity is not None:
        try:
            reconcile_campaign_resource_capacity(
                database,
                resource_capacity,
                controller=controller,
                stop_path=stop_path,
            )
        except Exception as error:  # noqa: BLE001 - bounded recovery report
            errors.append({
                "campaign_id": "*",
                "error": (
                    "resource capacity recovery failed: "
                    f"{type(error).__name__}: {error}"
                )[:500],
            })

    # One bounded tick drives the existing Continuous Executive wake
    # authority.  Recovery re-checks exact child predicates in phase two.
    tick_wakes(database, controller=controller, now=_dt(now) or datetime.now(timezone.utc))

    # Phase two consumes triggered wakes, evaluates joins, and records the
    # post-recovery checkpoint for each campaign.  A process kill at any one
    # checkpoint leaves all prior campaigns durable and the next startup pass
    # resumes from this exact ledger state.
    for campaign_id in campaign_ids:
        campaign = rebuild_campaign_state(database).campaigns.get(campaign_id)
        if campaign is None or campaign.status in {"COMPLETED", "CANCELLED"}:
            continue
        try:
            _recover_triggered_waits(database, campaign.campaign_id, controller=controller, stop_path=stop_path)
            join_result = evaluate_joins(database, campaign.campaign_id, controller=controller, stop_path=stop_path)
            if join_result.get("completed"):
                completed.append(campaign.campaign_id)
            current = rebuild_campaign_state(database).campaigns[campaign.campaign_id]
            if current.status == "WAITING":
                waiting.append(campaign.campaign_id)
            if current.status == "REPLANNING":
                replans.append(campaign.campaign_id)
            if current.status == "ESCALATED":
                escalated.append(campaign.campaign_id)
            rebuilt_wakes.extend(rebuild_required_wakes(database, campaign.campaign_id, controller=controller, stop_path=stop_path))
            checkpoint_campaign(database, campaign.campaign_id, controller=controller, stop_path=stop_path, reason="startup/resume checkpoint")
            if fault_hook:
                # A proof harness may kill the process at this boundary.  All
                # campaign/effect/wake decisions before it are durable and the
                # next process must rebuild them without redispatch.
                fault_hook(f"campaign {campaign.campaign_id} after checkpoint")
        except Exception as error:  # noqa: BLE001 - one campaign cannot strand siblings
            errors.append({"campaign_id": campaign.campaign_id, "error": f"{type(error).__name__}: {error}"[:500]})
    if fault_hook:
        fault_hook("campaign after wake reconstruction")
    final_state = rebuild_campaign_state(database)
    final_waiting = [
        campaign_id
        for campaign_id, campaign in final_state.campaigns.items()
        if campaign.status == "WAITING"
    ]
    final_replans = [
        campaign_id
        for campaign_id, campaign in final_state.campaigns.items()
        if campaign.status == "REPLANNING"
    ]
    final_escalated = [
        campaign_id
        for campaign_id, campaign in final_state.campaigns.items()
        if campaign.status == "ESCALATED"
    ]
    dispatchable = list(campaign_dispatchable_mission_ids(database))
    blocked = list(campaign_blocked_mission_ids(database))
    waiting = final_waiting
    replans = final_replans
    escalated = final_escalated
    status = (
        "PARTIAL" if errors
        # Surface the highest-severity durable decision when several
        # campaigns recover in one pass; a waiting sibling must not hide an
        # escalation or an owner-required replan in the aggregate report.
        else "ESCALATED" if escalated
        else "REPLAN_REQUIRED" if replans
        else "WAITING" if waiting and not dispatchable
        else "READY"
    )
    report = CampaignResumeReport(
        rid,
        status,
        campaigns_scanned=len(state.campaigns),
        resumed_campaigns=tuple(dict.fromkeys(resumed)),
        waiting_campaigns=tuple(dict.fromkeys(waiting)),
        replan_required=tuple(dict.fromkeys(replans)),
        escalated_campaigns=tuple(dict.fromkeys(escalated)),
        completed_campaigns=tuple(dict.fromkeys(completed)),
        reconciled_children=tuple(dict.fromkeys(reconciled_children)),
        reconciled_effects=tuple(dict.fromkeys(reconciled_effects)),
        rebuilt_wakes=tuple(dict.fromkeys(rebuilt_wakes)),
        dispatchable_mission_ids=tuple(dict.fromkeys(dispatchable)),
        blocked_mission_ids=tuple(dict.fromkeys(blocked)),
        errors=tuple(errors),
        reason="startup objective resume pass completed" if not errors else "one or more campaigns require review",
    )
    # A bounded worker poll that reconstructs an unchanged aggregate is not a
    # new fact.  Avoid an unbounded resume-receipt stream while retaining a
    # receipt whenever recovery actually changed the durable ledger.
    if database.event_count() != sequence_before:
        try:
            _append(database, "campaign.resume_passed", report.to_payload(), key=f"campaign.resume_passed:{rid}")
        except EventConflict:
            pass
    return report


def campaign_snapshot(database: FoundryDatabase, campaign_id: str | None = None) -> dict[str, Any]:
    """Read-only owner projection including exact pending work/checkpoints."""

    state = rebuild_campaign_state(database)
    selected = [state.campaign(campaign_id)] if campaign_id else sorted(state.campaigns.values(), key=lambda row: row.campaign_id)
    wakes = rebuild_wake_state(database)
    payload: dict[str, Any] = {}
    for campaign in selected:
        children = [child for child in state.children.values() if child.campaign_id == campaign.campaign_id]
        checkpoint = state.checkpoints.get(campaign.campaign_id)
        payload[campaign.campaign_id] = {
            **campaign.to_payload(),
            "children": [child.to_payload() for child in sorted(children, key=lambda row: row.child_id)],
            "checkpoint": checkpoint.to_payload() if checkpoint else None,
            "wakes": [dict(wake) for wake in wakes.values() if f":campaign:{campaign.campaign_id}:" in str(wake.get("wake_id") or "")],
            "pending_work": list(checkpoint.pending_work) if checkpoint else [],
            "accepted_child_count": sum(child.status == "SUCCEEDED" and bool(child.acceptance_outcome_id) for child in children),
        }
    return {"schema": CAMPAIGN_SCHEMA, "campaigns": payload}


def campaign_dispatch_context(
    database: FoundryDatabase,
    *,
    mission_id: str,
    step_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the one immutable campaign/routing identity for a mission."""

    state = rebuild_campaign_state(database)
    matches = [
        child for child in state.children.values()
        if child.mission_id == str(mission_id)
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise CampaignError(
            f"mission {mission_id} has multiple campaign child bindings"
        )
    child = matches[0]
    campaign = state.campaigns.get(child.campaign_id)
    if campaign is None:
        raise CampaignError(f"campaign is absent for child {child.child_id}")
    if campaign.status != "EXECUTING":
        raise CampaignError(
            f"campaign {campaign.campaign_id} is not dispatchable: {campaign.status}"
        )
    if child.status not in {"READY", "RUNNING"}:
        raise CampaignError(
            f"campaign child {child.child_id} is not dispatchable: {child.status}"
        )
    if (
        child.status == "READY"
        and str(mission_id) not in campaign_dispatchable_mission_ids(database)
    ):
        raise CampaignError(
            f"campaign child {child.child_id} is outside its bounded admission slots"
        )
    if child.step_id and step_id and child.step_id != str(step_id):
        raise CampaignError(
            f"campaign child {child.child_id} is bound to step {child.step_id}, not {step_id}"
        )
    if not child.routing_requirements or not child.routing_identity:
        raise CampaignError(
            f"campaign child {child.child_id} has no exact routing contract"
        )
    request = RoutingRequest.from_mapping(child.routing_requirements)
    if request.identity_hash() != child.routing_identity:
        raise CampaignError(
            f"campaign child {child.child_id} routing contract hash drifted"
        )
    from .autonomy import rebuild_executive_state

    objective = rebuild_executive_state(database).objectives.get(
        campaign.objective_id
    )
    if objective is None or str(mission_id) not in objective.mission_ids:
        raise CampaignError(
            f"campaign child {child.child_id} is not bound to objective {campaign.objective_id}"
        )
    effect_action_key = child.effect_action_key
    if effect_action_key is None and len(child.effect_keys) == 1:
        effect_action_key = child.effect_keys[0]
    return {
        "objective_id": campaign.objective_id,
        "campaign_id": campaign.campaign_id,
        "child_id": child.child_id,
        "mission_id": str(mission_id),
        "step_id": child.step_id,
        "routing_request": request,
        "routing_identity": child.routing_identity,
        "isolation_key": child.isolation_key,
        "effect_action_key": effect_action_key,
        "child_status": child.status,
    }


def campaign_dispatchable_mission_ids(database: FoundryDatabase) -> tuple[str, ...]:
    """Return campaign child missions allowed through the normal selector."""

    state = rebuild_campaign_state(database)
    # The campaign aggregate never outranks the existing Executive objective
    # authority.  A terminal/suspended objective must not regain dispatch
    # merely because a stale child still says READY.
    from .autonomy import rebuild_executive_state

    objective_state = rebuild_executive_state(database)
    objective_blocked = {
        objective_id
        for objective_id, objective in objective_state.objectives.items()
        if objective.status in {"COMPLETED", "FAILED", "CANCELLED", "SUSPENDED", "BLOCKED"}
    }
    result: list[str] = []
    dispatchable_states = {"ACTION_SELECTION", "REPAIRING", "READY", "VERIFYING"}
    for campaign in sorted(state.campaigns.values(), key=lambda row: row.campaign_id):
        if (
            campaign.status != "EXECUTING"
            or campaign.objective_id in objective_blocked
        ):
            continue
        children = sorted(
            (
                child for child in state.children.values()
                if child.campaign_id == campaign.campaign_id
            ),
            key=lambda row: row.child_id,
        )
        running = [
            child for child in children
            if child.status == "RUNNING"
            and child.mission_id
        ]
        if any(_unmet_dependencies(state, child) for child in running):
            raise CampaignError(
                f"campaign {campaign.campaign_id} has a running child with unmet dependencies"
            )
        if len(running) > campaign.max_parallel_children:
            raise CampaignError(
                f"campaign {campaign.campaign_id} exceeds its parallel child limit"
            )
        running_isolation = [
            child.isolation_key for child in running if child.isolation_key
        ]
        if len(running_isolation) != len(set(running_isolation)):
            raise CampaignError(
                f"campaign {campaign.campaign_id} has overlapping sibling isolation"
            )
        active_isolation = {
            *running_isolation
        }
        # Existing in-flight work keeps its continuation slot.  Only new
        # READY siblings consume remaining capacity.
        for child in running:
            mission = database.row("missions", "mission_id", child.mission_id)
            if (mission or {}).get("state") in dispatchable_states:
                result.append(str(child.mission_id))
        remaining = max(0, campaign.max_parallel_children - len(running))
        for child in children:
            if remaining <= 0:
                break
            if (
                child.status != "READY"
                or not child.mission_id
                or _unmet_dependencies(state, child)
                or (
                    child.isolation_key
                    and child.isolation_key in active_isolation
                )
            ):
                continue
            mission = database.row("missions", "mission_id", child.mission_id)
            if (mission or {}).get("state") not in dispatchable_states:
                continue
            result.append(str(child.mission_id))
            remaining -= 1
            if child.isolation_key:
                active_isolation.add(child.isolation_key)
    return tuple(dict.fromkeys(result))


def campaign_blocked_mission_ids(database: FoundryDatabase) -> tuple[str, ...]:
    state = rebuild_campaign_state(database)
    from .autonomy import rebuild_executive_state

    objective_state = rebuild_executive_state(database)
    objective_blocked = {
        objective_id
        for objective_id, objective in objective_state.objectives.items()
        if objective.status in {"COMPLETED", "FAILED", "CANCELLED", "SUSPENDED", "BLOCKED"}
    }
    result = [
        child.mission_id
        for child in state.children.values()
        if child.mission_id
        and (
            child.status in {"WAITING", "STOPPED", "UNKNOWN", "BLOCKED", "FAILED", "CANCELLED"}
            or (
                child.status == "SUCCEEDED"
                and not _accepted_child_truth_is_durable(database, child)
            )
            or state.campaigns.get(child.campaign_id) is None
            or (
                child.status != "SUCCEEDED"
                and state.campaigns.get(child.campaign_id) is not None
                and state.campaigns[child.campaign_id].objective_id in objective_blocked
            )
            or (
                child.status != "SUCCEEDED"
                and state.campaigns[child.campaign_id].status != "EXECUTING"
            )
            or (
                child.status != "SUCCEEDED"
                and bool(_unmet_dependencies(state, child))
            )
        )
    ]
    return tuple(sorted(dict.fromkeys(str(value) for value in result)))


# Human-friendly aliases used by startup integrations and external proof code.
resume_campaigns = startup_resume_pass
record_campaign_checkpoint = checkpoint_campaign
rebuild_campaign_wakes = rebuild_required_wakes
Campaign = CampaignRecord
ChildMission = ChildMissionRecord
CampaignProgress = CampaignCheckpoint


__all__ = [
    "CAMPAIGN_SCHEMA", "CAMPAIGN_STATUSES", "CHILD_STATUSES", "CHILD_WAIT_KINDS",
    "CampaignError", "CampaignRecord", "ChildMissionRecord", "Campaign", "ChildMission", "CampaignProgress",
    "CampaignCheckpoint", "CampaignState", "CampaignResumeReport",
    "rebuild_campaign_state", "create_campaign", "declare_child_mission",
    "bind_child_mission", "bind_child_effect", "record_child_state",
    "reconcile_child_effects", "reconcile_campaign_children",
    "checkpoint_campaign", "record_campaign_checkpoint",
    "rebuild_required_wakes", "rebuild_campaign_wakes", "evaluate_joins",
    "complete_campaign", "reserve_child_retry", "handle_deadlines",
    "apply_replan", "propagate_stop", "cancel_campaign", "cancel_child_mission", "resume_campaign",
    "startup_resume_pass", "resume_campaigns", "campaign_snapshot",
    "campaign_dispatch_context", "campaign_dispatchable_mission_ids",
    "campaign_blocked_mission_ids",
    "reconcile_campaign_resource_capacity",
]
