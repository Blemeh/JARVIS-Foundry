"""Durable capability-gap pause/expansion/resume coordination.

This module is deliberately a bridge, not another planner, scheduler, mission
loop, or capability authority.  Structured Executive plan requirements are
rechecked against :mod:`jarvis_foundry.fabric.catalog`; missing Fabric
capabilities become stable, deduplicated gap candidates and Supervisor-owned
wakes.  Expansion search/trial work is handed to a bounded port.  Only a
verified Catalog state can release the original objective.

All bridge state is rebuilt from append-only events, so a process restart may
repeat any public operation without duplicating a gap, attempt, wake, replan,
or resume.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..capability_requirements import (
    CapabilityRequirementSet,
    resolve_capability_requirements,
)
from ..clock import isoformat
from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..discovery_candidates import ingest_observed_candidate
from ..events import Event, canonical_json
from .catalog import capability_metadata, get_spec, observed_readiness, record_gap
from .model import CapabilityTrust, GapKind, GapRecord, GapStatus, Readiness


CONTRACT_VERSION = "jarvis.v12.capability-gap-resume.v1"
SOURCE_ID = "v12-capability-gap-resume"
EVENT_PREFIX = "fabric.capability_gap."

GAP_OPENED = EVENT_PREFIX + "opened"
GAP_CANDIDATE_LINKED = EVENT_PREFIX + "candidate_linked"
WAIT_ENTERED = EVENT_PREFIX + "wait_entered"
EXPANSION_REQUESTED = EVENT_PREFIX + "expansion_requested"
ATTEMPT_STARTED = EVENT_PREFIX + "attempt_started"
ATTEMPT_DISPATCHED = EVENT_PREFIX + "attempt_dispatched"
ATTEMPT_FAILED = EVENT_PREFIX + "attempt_failed"
BUDGET_EXHAUSTED = EVENT_PREFIX + "budget_exhausted"
GAP_DEFERRED = EVENT_PREFIX + "deferred_by_open_gap_budget"
RECURSION_BLOCKED = EVENT_PREFIX + "recursion_blocked"
ALTERNATE_REPLAN_REQUESTED = EVENT_PREFIX + "alternate_replan_requested"
ALTERNATE_SELECTED = EVENT_PREFIX + "alternate_selected"
WAKE_FIRED = EVENT_PREFIX + "wake_fired"
RESUMED = EVENT_PREFIX + "resumed"


class GapBridgeError(ValueError):
    """A requested gap transition is invalid or unsafe."""


class GapBridgeStopped(RuntimeError):
    """Owner STOP/veto prevented the bridge from mutating or dispatching."""


class CapabilityGapStatus(StrEnum):
    WAITING = "WAITING"
    EXPANSION_PENDING = "EXPANSION_PENDING"
    EXPANDING = "EXPANDING"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    READY_TO_RESUME = "READY_TO_RESUME"
    RESUMED = "RESUMED"
    ALTERNATE_RESUMED = "ALTERNATE_RESUMED"
    RECURSION_BLOCKED = "RECURSION_BLOCKED"


class ExpansionAttemptStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    SATISFIED = "SATISFIED"


@dataclass(frozen=True, slots=True)
class GapPolicy:
    """Durable anti-runaway limits for one root objective."""

    max_attempts_per_gap: int = 3
    max_total_attempts_per_objective: int = 6
    max_open_gaps_per_objective: int = 4
    max_expansion_depth: int = 1
    allow_probation: bool = True

    def __post_init__(self) -> None:
        for name in (
            "max_attempts_per_gap",
            "max_total_attempts_per_objective",
            "max_open_gaps_per_objective",
            "max_expansion_depth",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise GapBridgeError(f"{name} must be a positive integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_attempts_per_gap": self.max_attempts_per_gap,
            "max_total_attempts_per_objective": self.max_total_attempts_per_objective,
            "max_open_gaps_per_objective": self.max_open_gaps_per_objective,
            "max_expansion_depth": self.max_expansion_depth,
            "allow_probation": self.allow_probation,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "GapPolicy":
        row = dict(value or {})
        return cls(
            max_attempts_per_gap=int(row.get("max_attempts_per_gap") or 3),
            max_total_attempts_per_objective=int(
                row.get("max_total_attempts_per_objective") or 6
            ),
            max_open_gaps_per_objective=int(
                row.get("max_open_gaps_per_objective") or 4
            ),
            max_expansion_depth=int(row.get("max_expansion_depth") or 1),
            allow_probation=bool(row.get("allow_probation", True)),
        )


@dataclass(frozen=True, slots=True)
class ExpansionWorkRequest:
    work_id: str
    gap_id: str
    origin_objective_id: str
    root_objective_id: str
    candidate_id: str
    required_capability_id: str
    requirement_set_id: str
    budget: Mapping[str, Any]
    expansion_depth: int
    attempt_id: str | None = None
    attempt_ordinal: int | None = None
    authority: str = "CAPABILITY_FABRIC_CATALOG"

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "work_id": self.work_id,
            "gap_id": self.gap_id,
            "origin_objective_id": self.origin_objective_id,
            "root_objective_id": self.root_objective_id,
            "candidate_id": self.candidate_id,
            "required_capability_id": self.required_capability_id,
            "requirement_set_id": self.requirement_set_id,
            "budget": dict(self.budget),
            "expansion_depth": self.expansion_depth,
            "attempt_id": self.attempt_id,
            "attempt_ordinal": self.attempt_ordinal,
            "authority": self.authority,
            "solution_search": "PORT_OWNED",
            "isolated_trial": "PORT_OWNED",
        }


@runtime_checkable
class ExpansionWorkPort(Protocol):
    """Session 6/7 seam; claims never override Fabric Catalog truth."""

    def dispatch(self, request: ExpansionWorkRequest) -> Mapping[str, Any]:
        """Accept bounded search/trial work and return a JSON-safe receipt."""


@dataclass(frozen=True, slots=True)
class ExpansionAttempt:
    attempt_id: str
    ordinal: int
    strategy_id: str
    status: ExpansionAttemptStatus
    started_at: str
    dispatched_at: str | None = None
    failed_at: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "ordinal": self.ordinal,
            "strategy_id": self.strategy_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "dispatched_at": self.dispatched_at,
            "failed_at": self.failed_at,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class CapabilityGapState:
    gap_id: str
    origin_objective_id: str
    root_objective_id: str
    plan_id: str
    step_id: str
    required_capability_id: str
    requirement_set_id: str
    requirement_snapshot_id: str
    candidate_id: str | None
    work_id: str | None
    wake_id: str | None
    status: CapabilityGapStatus
    policy: GapPolicy
    expansion_depth: int
    attempts: tuple[ExpansionAttempt, ...] = ()
    effective_capability_state: str | None = None
    resumed_plan_id: str | None = None
    failure_reason: str | None = None
    created_at: str = field(default_factory=isoformat)
    updated_at: str = field(default_factory=isoformat)

    @property
    def active_attempt(self) -> ExpansionAttempt | None:
        return next(
            (item for item in reversed(self.attempts)
             if item.status is ExpansionAttemptStatus.ACTIVE),
            None,
        )

    @property
    def terminal(self) -> bool:
        return self.status in {
            CapabilityGapStatus.RESUMED,
            CapabilityGapStatus.ALTERNATE_RESUMED,
            CapabilityGapStatus.RECURSION_BLOCKED,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "gap_id": self.gap_id,
            "origin_objective_id": self.origin_objective_id,
            "root_objective_id": self.root_objective_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "required_capability_id": self.required_capability_id,
            "requirement_set_id": self.requirement_set_id,
            "requirement_snapshot_id": self.requirement_snapshot_id,
            "candidate_id": self.candidate_id,
            "work_id": self.work_id,
            "wake_id": self.wake_id,
            "status": self.status.value,
            "policy": self.policy.as_dict(),
            "expansion_depth": self.expansion_depth,
            "attempts": [item.as_dict() for item in self.attempts],
            "effective_capability_state": self.effective_capability_state,
            "resumed_plan_id": self.resumed_plan_id,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _digest(value: Any, length: int = 24) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def stable_gap_id(root_objective_id: str, required_capability_id: str) -> str:
    """Identity survives plan versions/restarts and dedupes repeated misses."""

    return "cap-gap-" + _digest({
        "contract_version": CONTRACT_VERSION,
        "root_objective_id": str(root_objective_id),
        "required_capability_id": str(required_capability_id),
    })


def stable_attempt_id(gap_id: str, ordinal: int) -> str:
    return "expansion-attempt-" + _digest({"gap_id": gap_id, "ordinal": ordinal})


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    correlation_id: str | None = None,
) -> str:
    event = Event.create(
        event_type,
        dict(payload),
        source_type="supervisor",
        source_id=SOURCE_ID,
        correlation_id=correlation_id,
        idempotency_key=key,
    )
    return str(database.append(event)[0])


def _gate(database: FoundryDatabase, *targets: str) -> None:
    stop = emergency_stop_reason(
        database, Path(database.path).resolve().parent / "EMERGENCY_STOP"
    )
    if stop:
        raise GapBridgeStopped(f"STOP active: {stop}")
    veto = matching_veto_reason(
        database, {"GLOBAL", "executive", "capability-expansion", *targets}
    )
    if veto:
        raise GapBridgeStopped(f"owner veto active: {veto}")


def rebuild_gap_state(database: FoundryDatabase) -> dict[str, CapabilityGapState]:
    """Rebuild all bridge state solely from durable events."""

    states: dict[str, CapabilityGapState] = {}
    for event in database.events(event_type_prefixes=(EVENT_PREFIX,)):
        payload = event.payload
        gap_id = str(payload.get("gap_id") or "")
        if not gap_id:
            continue
        if event.event_type in {GAP_OPENED, RECURSION_BLOCKED}:
            try:
                status = (
                    CapabilityGapStatus.RECURSION_BLOCKED
                    if event.event_type == RECURSION_BLOCKED
                    else CapabilityGapStatus.EXPANSION_PENDING
                )
                states[gap_id] = CapabilityGapState(
                    gap_id=gap_id,
                    origin_objective_id=str(payload["origin_objective_id"]),
                    root_objective_id=str(payload["root_objective_id"]),
                    plan_id=str(payload.get("plan_id") or ""),
                    step_id=str(payload.get("step_id") or ""),
                    required_capability_id=str(payload["required_capability_id"]),
                    requirement_set_id=str(payload.get("requirement_set_id") or ""),
                    requirement_snapshot_id=str(
                        payload.get("requirement_snapshot_id") or ""
                    ),
                    candidate_id=(
                        str(payload["candidate_id"])
                        if payload.get("candidate_id") else None
                    ),
                    work_id=(str(payload["work_id"]) if payload.get("work_id") else None),
                    wake_id=(str(payload["wake_id"]) if payload.get("wake_id") else None),
                    status=status,
                    policy=GapPolicy.from_mapping(payload.get("policy")),
                    expansion_depth=int(payload.get("expansion_depth") or 0),
                    failure_reason=str(payload.get("reason") or "") or None,
                    created_at=str(payload.get("created_at") or event.recorded_at),
                    updated_at=str(payload.get("updated_at") or event.recorded_at),
                )
            except (KeyError, TypeError, ValueError):
                continue
            continue
        prior = states.get(gap_id)
        if prior is None:
            continue
        now = str(payload.get("at") or payload.get("updated_at") or event.recorded_at)
        if event.event_type == GAP_CANDIDATE_LINKED:
            states[gap_id] = replace(
                prior, candidate_id=str(payload.get("candidate_id") or "") or None,
                updated_at=now,
            )
        elif event.event_type == WAIT_ENTERED:
            states[gap_id] = replace(
                prior,
                wake_id=str(payload.get("wake_id") or "") or None,
                status=CapabilityGapStatus.WAITING,
                updated_at=now,
            )
        elif event.event_type == EXPANSION_REQUESTED:
            states[gap_id] = replace(
                prior,
                work_id=str(payload.get("work_id") or "") or None,
                status=CapabilityGapStatus.EXPANSION_PENDING,
                updated_at=now,
            )
        elif event.event_type == ATTEMPT_STARTED:
            attempt = ExpansionAttempt(
                attempt_id=str(payload["attempt_id"]),
                ordinal=int(payload["ordinal"]),
                strategy_id=str(payload["strategy_id"]),
                status=ExpansionAttemptStatus.ACTIVE,
                started_at=str(payload.get("started_at") or now),
            )
            attempts = tuple(
                item for item in prior.attempts if item.attempt_id != attempt.attempt_id
            ) + (attempt,)
            states[gap_id] = replace(
                prior, attempts=attempts,
                status=CapabilityGapStatus.EXPANDING, updated_at=now,
            )
        elif event.event_type == ATTEMPT_FAILED:
            attempts = tuple(
                replace(
                    item,
                    status=ExpansionAttemptStatus.FAILED,
                    failed_at=str(payload.get("failed_at") or now),
                    reason_code=str(payload.get("reason_code") or "EXPANSION_FAILED"),
                    reason=str(payload.get("reason") or "expansion attempt failed"),
                    retryable=bool(payload.get("retryable")),
                ) if item.attempt_id == str(payload.get("attempt_id") or "") else item
                for item in prior.attempts
            )
            states[gap_id] = replace(
                prior,
                attempts=attempts,
                status=(
                    CapabilityGapStatus.RETRYABLE_FAILURE
                    if payload.get("retryable")
                    else CapabilityGapStatus.REPLAN_REQUIRED
                ),
                failure_reason=str(payload.get("reason") or "expansion attempt failed"),
                updated_at=now,
            )
        elif event.event_type == ATTEMPT_DISPATCHED:
            attempts = tuple(
                replace(item, dispatched_at=now)
                if item.attempt_id == str(payload.get("attempt_id") or "") else item
                for item in prior.attempts
            )
            states[gap_id] = replace(prior, attempts=attempts, updated_at=now)
        elif event.event_type == BUDGET_EXHAUSTED:
            states[gap_id] = replace(
                prior,
                status=CapabilityGapStatus.BUDGET_EXHAUSTED,
                failure_reason=str(payload.get("reason") or "expansion budget exhausted"),
                updated_at=now,
            )
        elif event.event_type == ALTERNATE_REPLAN_REQUESTED:
            states[gap_id] = replace(
                prior, status=CapabilityGapStatus.REPLAN_REQUIRED,
                failure_reason=str(payload.get("reason") or "alternate replan required"),
                updated_at=now,
            )
        elif event.event_type == WAKE_FIRED:
            states[gap_id] = replace(
                prior, status=CapabilityGapStatus.READY_TO_RESUME,
                effective_capability_state=str(payload.get("effective_state") or ""),
                updated_at=now,
            )
        elif event.event_type == RESUMED:
            attempts = tuple(
                replace(item, status=ExpansionAttemptStatus.SATISFIED)
                if item.status is ExpansionAttemptStatus.ACTIVE else item
                for item in prior.attempts
            )
            states[gap_id] = replace(
                prior,
                attempts=attempts,
                status=CapabilityGapStatus.RESUMED,
                effective_capability_state=str(payload.get("effective_state") or ""),
                resumed_plan_id=str(payload.get("plan_id") or "") or None,
                updated_at=now,
            )
        elif event.event_type == ALTERNATE_SELECTED:
            states[gap_id] = replace(
                prior,
                status=CapabilityGapStatus.ALTERNATE_RESUMED,
                effective_capability_state=str(
                    payload.get("alternate_capability_id") or "ALTERNATE"
                ),
                resumed_plan_id=str(payload.get("plan_id") or "") or None,
                updated_at=now,
            )
    return states


def _objective_lineage(database: FoundryDatabase, objective_id: str) -> tuple[str, int, bool]:
    from ..executive.autonomy import rebuild_executive_state

    state = rebuild_executive_state(database)
    objective = state.objectives.get(objective_id)
    if objective is None:
        raise GapBridgeError(f"unknown objective: {objective_id}")
    root = objective
    depth = int(objective.metadata.get("expansion_depth") or 0)
    expansion_owned = objective.metadata.get("objective_class") == "CAPABILITY_EXPANSION"
    seen = {objective.objective_id}
    while root.parent_id:
        if root.parent_id in seen:
            raise GapBridgeError("objective ancestry is cyclic")
        seen.add(root.parent_id)
        parent = state.objectives.get(root.parent_id)
        if parent is None:
            break
        root = parent
        depth = max(depth, int(parent.metadata.get("expansion_depth") or 0))
        expansion_owned = expansion_owned or (
            parent.metadata.get("objective_class") == "CAPABILITY_EXPANSION"
        )
    return root.objective_id, depth, expansion_owned


def _requirement_set(
    database: FoundryDatabase,
    *,
    objective_id: str,
    plan_id: str,
    step_id: str,
    capability_id: str,
    resource_id: str | None,
) -> CapabilityRequirementSet:
    """Use only structured planner metadata; no objective/source prose."""

    return resolve_capability_requirements(
        database,
        {
            "candidate_id": f"planner:{objective_id}:{plan_id}:{step_id}",
            "resource_id": resource_id,
        },
        action_metadata=({"capability_id": capability_id, "optional": False},),
        unattended=True,
    )


def _candidate_for_gap(
    database: FoundryDatabase,
    *,
    gap_id: str,
    objective_id: str,
    plan_id: str,
    step_id: str,
    capability_id: str,
    requirement_set: CapabilityRequirementSet,
) -> str:
    material = {
        "gap_id": gap_id,
        "origin_objective_id": objective_id,
        "plan_id": plan_id,
        "step_id": step_id,
        "required_capability_id": capability_id,
        "requirement_set_id": requirement_set.requirement_set_id,
    }
    result = ingest_observed_candidate(
        database,
        objective_id,
        observation_id=f"planner-gap:{gap_id}",
        source_kind="jarvis_planner",
        source_identity=f"{plan_id}:{step_id}",
        content_hash="sha256:" + hashlib.sha256(
            canonical_json(material).encode("utf-8")
        ).hexdigest(),
        objective=f"Acquire capability {capability_id} for objective {objective_id}",
        authority="JARVIS_SYSTEM_METADATA",
        target_project=objective_id,
        alias_key=gap_id,
        evidence=[{
            **material,
            "authority": "JARVIS_SYSTEM_METADATA",
            "external_source_text_used": False,
        }],
        candidate_type="capability_gap",
        expected_outcome=f"Fabric Catalog verifies {capability_id}",
        confidence=1.0,
        trusted_requirements={
            "required_capability_ids": [capability_id],
            "source": SOURCE_ID,
        },
    )
    candidate_id = str(result.get("candidate_id") or result.get("duplicate_of") or "")
    if not candidate_id:
        raise GapBridgeError("capability-gap candidate could not be linked")
    return candidate_id


def _work_request(
    state: CapabilityGapState,
    attempt: ExpansionAttempt | None = None,
) -> ExpansionWorkRequest:
    if not state.candidate_id:
        raise GapBridgeError(f"gap {state.gap_id} has no candidate linkage")
    work_id = state.work_id or f"expansion-work:{state.gap_id}"
    return ExpansionWorkRequest(
        work_id=work_id,
        gap_id=state.gap_id,
        origin_objective_id=state.origin_objective_id,
        root_objective_id=state.root_objective_id,
        candidate_id=state.candidate_id,
        required_capability_id=state.required_capability_id,
        requirement_set_id=state.requirement_set_id,
        budget={
            "max_attempts": state.policy.max_attempts_per_gap,
            "max_total_attempts_for_root": state.policy.max_total_attempts_per_objective,
        },
        expansion_depth=state.expansion_depth + 1,
        attempt_id=attempt.attempt_id if attempt is not None else None,
        attempt_ordinal=attempt.ordinal if attempt is not None else None,
    )


def _ensure_wait_and_work(
    database: FoundryDatabase,
    state: CapabilityGapState,
    *,
    resource_id: str | None,
    missing_kind: GapKind,
) -> CapabilityGapState:
    """Finish a partially persisted open transition after a hard restart."""

    if state.terminal:
        return state
    if state.wake_id is None:
        record_gap(database, GapRecord(
            gap_id=state.gap_id,
            needed_action=state.required_capability_id,
            missing=missing_kind,
            status=GapStatus.SPECIFIED,
            detail=(
                f"Fabric Catalog has no registered "
                f"{state.required_capability_id}"
            ),
        ))
        from ..executive.autonomy import (
            rebuild_executive_state,
            set_objective_status,
            update_objective,
        )
        from ..executive.continuous import schedule_wait_for_capability

        objective = rebuild_executive_state(database).objectives[
            state.origin_objective_id
        ]
        linked = list(objective.metadata.get("capability_gap_ids") or ())
        if state.gap_id not in linked:
            update_objective(
                database,
                state.origin_objective_id,
                {"metadata": {
                    **objective.metadata,
                    "capability_gap_ids": [*linked, state.gap_id],
                }},
                source_id=SOURCE_ID,
            )
        set_objective_status(
            database,
            state.origin_objective_id,
            "WAITING",
            reason=(
                f"waiting for Fabric capability "
                f"{state.required_capability_id}"
            ),
            evidence_refs=(state.gap_id, state.requirement_snapshot_id),
        )
        wake = schedule_wait_for_capability(
            database,
            state.origin_objective_id,
            capability=state.required_capability_id,
            resource_id=resource_id,
            reason=(
                f"capability gap {state.gap_id} waits for "
                f"{state.required_capability_id}"
            ),
        )
        wake_id = str(wake.get("wake_id") or "")
        _append(
            database, WAIT_ENTERED,
            {
                "gap_id": state.gap_id,
                "objective_id": state.origin_objective_id,
                "wake_id": wake_id,
                "required_capability_id": state.required_capability_id,
                "at": isoformat(),
            },
            key=f"{WAIT_ENTERED}:{state.gap_id}",
            correlation_id=state.root_objective_id,
        )
        state = rebuild_gap_state(database)[state.gap_id]
    # The work request has no timestamp and is safe to redeliver after a crash.
    request = _work_request(state)
    _append(
        database, EXPANSION_REQUESTED, request.as_dict(),
        key=f"{EXPANSION_REQUESTED}:{state.gap_id}",
        correlation_id=state.root_objective_id,
    )
    return rebuild_gap_state(database)[state.gap_id]


def open_capability_gap(
    database: FoundryDatabase,
    *,
    objective_id: str,
    plan_id: str,
    step_id: str,
    required_capability_id: str,
    resource_id: str | None = None,
    policy: GapPolicy | None = None,
    missing_kind: GapKind = GapKind.ADAPTER,
) -> CapabilityGapState | None:
    """Pause an objective for one Catalog-confirmed missing capability.

    A registered-but-unready capability is a readiness/recovery problem, not
    a self-expansion gap, and therefore returns ``None``.
    """

    chosen_policy = policy or GapPolicy()
    _gate(database, objective_id, required_capability_id)
    root_id, expansion_depth, expansion_owned = _objective_lineage(
        database, objective_id
    )
    gap_id = stable_gap_id(root_id, required_capability_id)
    existing = rebuild_gap_state(database).get(gap_id)
    if existing is not None:
        return _ensure_wait_and_work(
            database,
            existing,
            resource_id=resource_id,
            missing_kind=missing_kind,
        )
    requirement_set = _requirement_set(
        database,
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        capability_id=required_capability_id,
        resource_id=resource_id,
    )
    if required_capability_id not in requirement_set.missing_capabilities:
        return None

    if expansion_owned or expansion_depth >= chosen_policy.max_expansion_depth:
        payload = {
            "contract_version": CONTRACT_VERSION,
            "gap_id": gap_id,
            "origin_objective_id": objective_id,
            "root_objective_id": root_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "required_capability_id": required_capability_id,
            "requirement_set_id": requirement_set.requirement_set_id,
            "requirement_snapshot_id": requirement_set.snapshot_id,
            "policy": chosen_policy.as_dict(),
            "expansion_depth": expansion_depth,
            "reason": "capability expansion cannot recursively spawn capability expansion",
            "created_at": isoformat(),
        }
        _append(
            database, RECURSION_BLOCKED, payload,
            key=f"{RECURSION_BLOCKED}:{gap_id}", correlation_id=root_id,
        )
        return rebuild_gap_state(database)[gap_id]

    states = rebuild_gap_state(database)
    open_for_root = [
        item for item in states.values()
        if item.root_objective_id == root_id and not item.terminal
    ]
    if len(open_for_root) >= chosen_policy.max_open_gaps_per_objective:
        # Bound concurrent expansion without aborting the Executive cycle.
        # The requirement remains in the durable plan and is reconsidered
        # after an existing gap becomes terminal.
        _append(
            database,
            GAP_DEFERRED,
            {
                "gap_id": gap_id,
                "origin_objective_id": objective_id,
                "root_objective_id": root_id,
                "plan_id": plan_id,
                "step_id": step_id,
                "required_capability_id": required_capability_id,
                "max_open_gaps_per_objective": (
                    chosen_policy.max_open_gaps_per_objective
                ),
                "open_gap_ids": sorted(item.gap_id for item in open_for_root),
                "at": isoformat(),
            },
            key=f"{GAP_DEFERRED}:{gap_id}",
            correlation_id=root_id,
        )
        return None

    candidate_id = _candidate_for_gap(
        database,
        gap_id=gap_id,
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        capability_id=required_capability_id,
        requirement_set=requirement_set,
    )
    created_at = isoformat()
    work_id = f"expansion-work:{gap_id}"
    opened = {
        "contract_version": CONTRACT_VERSION,
        "gap_id": gap_id,
        "origin_objective_id": objective_id,
        "root_objective_id": root_id,
        "plan_id": plan_id,
        "step_id": step_id,
        "required_capability_id": required_capability_id,
        "requirement_set_id": requirement_set.requirement_set_id,
        "requirement_snapshot_id": requirement_set.snapshot_id,
        "catalog_digest": requirement_set.catalog_digest,
        "candidate_id": candidate_id,
        "work_id": work_id,
        "policy": chosen_policy.as_dict(),
        "expansion_depth": expansion_depth,
        "fabric_authority": "CAPABILITY_FABRIC_CATALOG",
        "external_source_text_used": False,
        "created_at": created_at,
    }
    _append(
        database, GAP_OPENED, opened,
        key=f"{GAP_OPENED}:{gap_id}", correlation_id=root_id,
    )
    _append(
        database, GAP_CANDIDATE_LINKED,
        {
            "gap_id": gap_id,
            "candidate_id": candidate_id,
            "origin_objective_id": objective_id,
            "at": isoformat(),
        },
        key=f"{GAP_CANDIDATE_LINKED}:{gap_id}", correlation_id=root_id,
    )
    state = rebuild_gap_state(database)[gap_id]
    return _ensure_wait_and_work(
        database,
        state,
        resource_id=resource_id,
        missing_kind=missing_kind,
    )


def bridge_missing_plan_capabilities(
    database: FoundryDatabase,
    objective_id: str,
    *,
    policy: GapPolicy | None = None,
) -> tuple[CapabilityGapState, ...]:
    """Bridge missing ``cap.*`` requirements from the active Executive plan."""

    from ..executive.autonomy import active_plan, rebuild_executive_state

    _gate(database, objective_id)
    plan = active_plan(database, objective_id)
    objective = rebuild_executive_state(database).objectives.get(objective_id)
    if plan is None or objective is None:
        return ()
    opened: list[CapabilityGapState] = []
    for step in plan.steps:
        if step.status in {"COMPLETED", "CANCELLED"}:
            continue
        for capability_id in step.required_capabilities:
            # Pre-Fabric compatibility ids remain governed by their existing
            # resource registry.  A new Fabric gap must carry the canonical
            # capability namespace and be confirmed absent by the Catalog.
            if not capability_id.startswith("cap.") or get_spec(database, capability_id):
                continue
            state = open_capability_gap(
                database,
                objective_id=objective_id,
                plan_id=plan.plan_id,
                step_id=step.step_id,
                required_capability_id=capability_id,
                resource_id=step.resource_id or objective.resource_id,
                policy=policy,
            )
            if state is not None:
                opened.append(state)
    unique = {item.gap_id: item for item in opened}
    return tuple(unique[key] for key in sorted(unique))


def start_expansion_attempt(
    database: FoundryDatabase,
    gap_id: str,
    *,
    strategy_id: str,
    port: ExpansionWorkPort | None = None,
) -> CapabilityGapState:
    """Start one bounded, restart-idempotent expansion attempt."""

    state = rebuild_gap_state(database).get(gap_id)
    if state is None:
        raise GapBridgeError(f"unknown capability gap: {gap_id}")
    _gate(database, state.origin_objective_id, gap_id)
    if state.terminal:
        return state
    if state.status is CapabilityGapStatus.BUDGET_EXHAUSTED:
        return state
    attempt = state.active_attempt
    if attempt is None:
        root_attempts = sum(
            len(item.attempts) for item in rebuild_gap_state(database).values()
            if item.root_objective_id == state.root_objective_id
        )
        ordinal = len(state.attempts) + 1
        if (
            ordinal > state.policy.max_attempts_per_gap
            or root_attempts >= state.policy.max_total_attempts_per_objective
        ):
            _append(
                database, BUDGET_EXHAUSTED,
                {
                    "gap_id": gap_id,
                    "reason": "bounded expansion attempt budget exhausted",
                    "attempts_for_gap": len(state.attempts),
                    "attempts_for_root": root_attempts,
                    "at": isoformat(),
                },
                key=f"{BUDGET_EXHAUSTED}:{gap_id}",
                correlation_id=state.root_objective_id,
            )
            return rebuild_gap_state(database)[gap_id]
        strategy = str(strategy_id or "").strip()
        if not strategy or len(strategy) > 256 or any(char.isspace() for char in strategy):
            raise GapBridgeError("strategy_id must be a bounded identity")
        attempt_id = stable_attempt_id(gap_id, ordinal)
        _append(
            database, ATTEMPT_STARTED,
            {
                "gap_id": gap_id,
                "attempt_id": attempt_id,
                "ordinal": ordinal,
                "strategy_id": strategy,
                "work_id": state.work_id,
                "started_at": isoformat(),
            },
            key=f"{ATTEMPT_STARTED}:{attempt_id}",
            correlation_id=state.root_objective_id,
        )
        state = rebuild_gap_state(database)[gap_id]
        attempt = state.active_attempt
    if port is None or attempt is None or attempt.dispatched_at is not None:
        return state
    attempt_id = attempt.attempt_id

    # STOP is rechecked immediately before the external expansion seam.
    _gate(database, state.origin_objective_id, gap_id, attempt_id)
    request = _work_request(state, attempt)
    try:
        raw = dict(port.dispatch(request))
    except Exception as error:
        return fail_expansion_attempt(
            database,
            gap_id,
            attempt_id=attempt_id,
            reason=f"expansion port failed: {type(error).__name__}: {error}",
            reason_code="EXPANSION_PORT_FAILED",
            retryable=True,
        )
    # STOP may arrive while external search/trial work is in flight.  Do not
    # append its receipt or advance bridge state after owner control changed.
    # On RESUME the same stable attempt id is redelivered for port-side dedupe.
    _gate(database, state.origin_objective_id, gap_id, attempt_id)
    # Port output is an opaque receipt.  A claim such as REGISTERED can never
    # release the mission; reconciliation below re-reads the real Catalog.
    safe_receipt = {
        str(key): value for key, value in raw.items()
        if str(key) not in {"source", "code", "patch", "content", "secret", "token"}
    }
    _append(
        database, ATTEMPT_DISPATCHED,
        {
            "gap_id": gap_id,
            "attempt_id": attempt_id,
            "receipt": safe_receipt,
            "receipt_authority": "DATA_ONLY",
            "fabric_authority_rechecked": True,
            "at": isoformat(),
        },
        key=f"{ATTEMPT_DISPATCHED}:{attempt_id}",
        correlation_id=state.root_objective_id,
    )
    if str(raw.get("status") or "").upper() == "FAILED":
        return fail_expansion_attempt(
            database,
            gap_id,
            attempt_id=attempt_id,
            reason=str(raw.get("reason") or "expansion port reported failure"),
            reason_code=str(raw.get("reason_code") or "EXPANSION_FAILED"),
            retryable=bool(raw.get("retryable", True)),
        )
    reconcile_ready_gaps(database, capability_id=state.required_capability_id)
    return rebuild_gap_state(database)[gap_id]


def fail_expansion_attempt(
    database: FoundryDatabase,
    gap_id: str,
    *,
    attempt_id: str | None = None,
    reason: str,
    reason_code: str = "EXPANSION_FAILED",
    retryable: bool = True,
    alternate_capability_id: str | None = None,
) -> CapabilityGapState:
    """Close one attempt honestly and optionally enter alternate replan."""

    state = rebuild_gap_state(database).get(gap_id)
    if state is None:
        raise GapBridgeError(f"unknown capability gap: {gap_id}")
    _gate(database, state.origin_objective_id, gap_id)
    if state.terminal:
        return state
    attempt = (
        next((item for item in state.attempts if item.attempt_id == attempt_id), None)
        if attempt_id else state.active_attempt
    )
    if attempt is None:
        raise GapBridgeError(f"gap {gap_id} has no matching active attempt")
    if attempt.status is ExpansionAttemptStatus.FAILED:
        return state
    bounded_reason = str(reason or "expansion attempt failed")[:1000]
    remaining = attempt.ordinal < state.policy.max_attempts_per_gap
    actual_retryable = bool(retryable and remaining)
    _append(
        database, ATTEMPT_FAILED,
        {
            "gap_id": gap_id,
            "attempt_id": attempt.attempt_id,
            "reason": bounded_reason,
            "reason_code": str(reason_code or "EXPANSION_FAILED")[:120],
            "retryable": actual_retryable,
            "failed_at": isoformat(),
        },
        key=f"{ATTEMPT_FAILED}:{attempt.attempt_id}",
        correlation_id=state.root_objective_id,
    )
    if alternate_capability_id:
        return request_alternate_replan(
            database,
            gap_id,
            alternate_capability_id=alternate_capability_id,
            reason=bounded_reason,
        )
    if not actual_retryable:
        _append(
            database, BUDGET_EXHAUSTED,
            {
                "gap_id": gap_id,
                "reason": bounded_reason,
                "attempts_for_gap": attempt.ordinal,
                "at": isoformat(),
            },
            key=f"{BUDGET_EXHAUSTED}:{gap_id}",
            correlation_id=state.root_objective_id,
        )
        record_gap(database, GapRecord(
            gap_id=gap_id,
            needed_action=state.required_capability_id,
            missing=GapKind.ADAPTER,
            status=GapStatus.REJECTED,
            detail=bounded_reason,
        ))
    return rebuild_gap_state(database)[gap_id]


def _effective_state(database: FoundryDatabase, state: CapabilityGapState) -> str | None:
    if get_spec(database, state.required_capability_id) is None:
        return None
    readiness = observed_readiness(database, state.required_capability_id)
    if readiness.state is not Readiness.READY:
        return None
    if readiness.trust_state is CapabilityTrust.DISABLED:
        return None
    if readiness.trust_state is CapabilityTrust.PROBATION:
        return "PROBATION" if state.policy.allow_probation else None
    return "READY"


def _replan_after_ready(
    database: FoundryDatabase,
    state: CapabilityGapState,
    *,
    effective_state: str,
) -> str | None:
    """Run a bounded Executive replan pass over the existing active plan."""

    from ..executive.autonomy import (
        PlanRecord,
        active_plan,
        rebuild_executive_state,
    )

    executive = rebuild_executive_state(database)
    objective = executive.objectives.get(state.origin_objective_id)
    if objective is None:
        raise GapBridgeError(f"origin objective disappeared: {state.origin_objective_id}")
    plan = active_plan(database, state.origin_objective_id)
    remaining_gap_ids = tuple(sorted(
        item.gap_id
        for item in rebuild_gap_state(database).values()
        if item.gap_id != state.gap_id
        and item.origin_objective_id == state.origin_objective_id
        and not item.terminal
    ))
    now = isoformat()
    events: list[Event] = []
    plan_id: str | None = None
    if plan is not None:
        changed_steps = []
        for step in plan.steps:
            if state.required_capability_id not in step.required_capabilities:
                changed_steps.append(step)
                continue
            probation = set(step.probation_capabilities)
            if effective_state == "PROBATION":
                probation.add(state.required_capability_id)
            else:
                probation.discard(state.required_capability_id)
            changed_steps.append(replace(
                step,
                probation_capabilities=tuple(sorted(probation)),
                status="READY" if step.status == "BLOCKED" else step.status,
                evidence_refs=tuple(dict.fromkeys((
                    *step.evidence_refs,
                    state.gap_id,
                    state.requirement_snapshot_id,
                ))),
            ))
        updated = PlanRecord(
            plan_id=plan.plan_id,
            objective_id=plan.objective_id,
            steps=tuple(changed_steps),
            critical_path=plan.critical_path,
            status="ACTIVE",
            version=plan.version + 1,
            created_at=plan.created_at,
            updated_at=now,
        )
        plan_id = updated.plan_id
        events.append(Event.create(
            "executive.plan_updated",
            updated.to_payload(),
            source_type="supervisor",
            source_id=SOURCE_ID,
            correlation_id=state.origin_objective_id,
            idempotency_key=(
                f"executive.plan_updated:{plan.plan_id}:gap-resume:{state.gap_id}"
            ),
        ))
    # A capability wake is per gap.  With multiple required capabilities, the
    # satisfied gap and its wake become terminal but the objective remains in
    # WAIT until every sibling gap has cleared.  This prevents a partly-ready
    # plan from briefly re-entering admission between sequential registrations.
    if not remaining_gap_ids and objective.status != "ACTIVE":
        events.append(Event.create(
            "executive.objective_status_changed",
            {
                "objective_id": objective.objective_id,
                "status": "ACTIVE",
                "reason": f"capability gap {state.gap_id} satisfied",
                "evidence_refs": [state.gap_id, state.requirement_snapshot_id],
                "updated_at": now,
                "version": objective.version + 1,
            },
            source_type="supervisor",
            source_id=SOURCE_ID,
            correlation_id=objective.objective_id,
            idempotency_key=(
                f"executive.objective_status_changed:{objective.objective_id}:"
                f"gap-resume:{state.gap_id}"
            ),
        ))
    events.extend((
        Event.create(
            "executive.capability_gap_replanned",
            {
                "gap_id": state.gap_id,
                "objective_id": state.origin_objective_id,
                "plan_id": plan_id,
                "required_capability_id": state.required_capability_id,
                "effective_state": effective_state,
                "remaining_gap_ids": list(remaining_gap_ids),
                "at": now,
            },
            source_type="supervisor", source_id=SOURCE_ID,
            correlation_id=state.origin_objective_id,
            idempotency_key=f"executive.capability_gap_replanned:{state.gap_id}",
        ),
        Event.create(
            WAKE_FIRED,
            {
                "gap_id": state.gap_id,
                "wake_id": state.wake_id,
                "required_capability_id": state.required_capability_id,
                "effective_state": effective_state,
                "remaining_gap_ids": list(remaining_gap_ids),
                "at": now,
            },
            source_type="supervisor", source_id=SOURCE_ID,
            correlation_id=state.root_objective_id,
            idempotency_key=f"{WAKE_FIRED}:{state.gap_id}",
        ),
        Event.create(
            RESUMED,
            {
                "gap_id": state.gap_id,
                "objective_id": state.origin_objective_id,
                "plan_id": plan_id,
                "required_capability_id": state.required_capability_id,
                "effective_state": effective_state,
                "remaining_gap_ids": list(remaining_gap_ids),
                "at": now,
            },
            source_type="supervisor", source_id=SOURCE_ID,
            correlation_id=state.root_objective_id,
            idempotency_key=f"{RESUMED}:{state.gap_id}",
        ),
    ))
    if state.candidate_id:
        attempt_id = (
            state.active_attempt.attempt_id
            if state.active_attempt is not None else state.work_id or state.gap_id
        )
        events.append(Event.create(
            "discovery.candidate_outcome_recorded",
            {
                "candidate_id": state.candidate_id,
                "mission_id": attempt_id,
                "outcome": {
                    "status": "CAPABILITY_REGISTERED",
                    "gap_id": state.gap_id,
                    "capability_id": state.required_capability_id,
                    "effective_state": effective_state,
                },
            },
            source_type="supervisor", source_id=SOURCE_ID,
            correlation_id=state.root_objective_id,
            idempotency_key=(
                f"discovery.candidate_outcome_recorded:{state.candidate_id}"
            ),
        ))
    database.append_events(events)
    return plan_id


def reconcile_ready_gaps(
    database: FoundryDatabase,
    *,
    capability_id: str | None = None,
) -> tuple[CapabilityGapState, ...]:
    """Fire eligible capability wakes exactly once and re-run planning."""

    states = rebuild_gap_state(database)
    resumed: list[CapabilityGapState] = []
    for gap_id in sorted(states):
        state = states[gap_id]
        if state.terminal or state.status is CapabilityGapStatus.RECURSION_BLOCKED:
            continue
        if capability_id and state.required_capability_id != capability_id:
            continue
        effective = _effective_state(database, state)
        if effective is None:
            continue
        _gate(database, state.origin_objective_id, gap_id)
        from ..executive.continuous import rebuild_wake_state, trigger_wake

        wake_state = rebuild_wake_state(database)
        wake = wake_state.get(str(state.wake_id or ""))
        if wake is not None and wake.get("status") == "ACTIVE":
            trigger_wake(
                database,
                str(state.wake_id),
                reason=(
                    f"capability {state.required_capability_id} became "
                    f"{effective} for {state.gap_id}"
                ),
            )
        record_gap(database, GapRecord(
            gap_id=state.gap_id,
            needed_action=state.required_capability_id,
            missing=GapKind.ADAPTER,
            status=GapStatus.REGISTERED,
            detail=(
                f"Fabric Catalog verified {state.required_capability_id} "
                f"as {effective}"
            ),
        ))
        _replan_after_ready(database, state, effective_state=effective)
        resumed.append(rebuild_gap_state(database)[gap_id])
    return tuple(resumed)


def request_alternate_replan(
    database: FoundryDatabase,
    gap_id: str,
    *,
    reason: str,
    alternate_capability_id: str | None = None,
) -> CapabilityGapState:
    """Leave expansion and request/perform a bounded alternate plan.

    If a ready alternate is supplied, the existing plan is rewritten through
    an Executive ``plan_updated`` event and resumes.  Otherwise the old plan
    is superseded and the objective remains ``PLANNING`` for the real planner.
    No second missing-capability expansion is spawned here.
    """

    state = rebuild_gap_state(database).get(gap_id)
    if state is None:
        raise GapBridgeError(f"unknown capability gap: {gap_id}")
    _gate(database, state.origin_objective_id, gap_id)
    if state.terminal:
        return state
    alternate = str(alternate_capability_id or "").strip() or None
    alternate_effective: str | None = None
    if alternate:
        spec = get_spec(database, alternate)
        if spec is not None:
            ready = observed_readiness(database, alternate)
            if ready.state is Readiness.READY:
                if ready.trust_state is CapabilityTrust.ESTABLISHED:
                    alternate_effective = "READY"
                elif (
                    ready.trust_state is CapabilityTrust.PROBATION
                    and state.policy.allow_probation
                ):
                    alternate_effective = "PROBATION"

    from ..executive.autonomy import PlanRecord, active_plan, rebuild_executive_state
    from ..executive.continuous import clear_wake, rebuild_wake_state

    plan = active_plan(database, state.origin_objective_id)
    objective = rebuild_executive_state(database).objectives[state.origin_objective_id]
    now = isoformat()
    events: list[Event] = []
    selected_plan_id: str | None = None
    if plan is not None:
        if alternate and alternate_effective:
            rewritten = []
            for step in plan.steps:
                required = tuple(
                    alternate if item == state.required_capability_id else item
                    for item in step.required_capabilities
                )
                probation = set(step.probation_capabilities)
                probation.discard(state.required_capability_id)
                if alternate_effective == "PROBATION":
                    probation.add(alternate)
                rewritten.append(replace(
                    step,
                    required_capabilities=required,
                    probation_capabilities=tuple(sorted(probation)),
                    status="READY" if step.status == "BLOCKED" else step.status,
                    evidence_refs=tuple(dict.fromkeys((*step.evidence_refs, state.gap_id))),
                ))
            updated = PlanRecord(
                plan_id=plan.plan_id, objective_id=plan.objective_id,
                steps=tuple(rewritten), critical_path=plan.critical_path,
                status="ACTIVE", version=plan.version + 1,
                created_at=plan.created_at, updated_at=now,
            )
            selected_plan_id = updated.plan_id
            events.append(Event.create(
                "executive.plan_updated", updated.to_payload(),
                source_type="supervisor", source_id=SOURCE_ID,
                correlation_id=state.origin_objective_id,
                idempotency_key=(
                    f"executive.plan_updated:{plan.plan_id}:alternate:{state.gap_id}"
                ),
            ))
        else:
            superseded = PlanRecord(
                plan_id=plan.plan_id, objective_id=plan.objective_id,
                steps=plan.steps, critical_path=plan.critical_path,
                status="SUPERSEDED", version=plan.version + 1,
                created_at=plan.created_at, updated_at=now,
            )
            events.append(Event.create(
                "executive.plan_updated", superseded.to_payload(),
                source_type="supervisor", source_id=SOURCE_ID,
                correlation_id=state.origin_objective_id,
                idempotency_key=(
                    f"executive.plan_updated:{plan.plan_id}:replan:{state.gap_id}"
                ),
            ))
    target_status = "ACTIVE" if alternate_effective else "PLANNING"
    if objective.status != target_status:
        events.append(Event.create(
            "executive.objective_status_changed",
            {
                "objective_id": objective.objective_id,
                "status": target_status,
                "reason": str(reason or "alternate solution required")[:1000],
                "evidence_refs": [state.gap_id],
                "updated_at": now,
                "version": objective.version + 1,
            },
            source_type="supervisor", source_id=SOURCE_ID,
            correlation_id=objective.objective_id,
            idempotency_key=(
                f"executive.objective_status_changed:{objective.objective_id}:"
                f"alternate:{state.gap_id}:{target_status}"
            ),
        ))
    events.append(Event.create(
        ALTERNATE_REPLAN_REQUESTED,
        {
            "gap_id": state.gap_id,
            "objective_id": state.origin_objective_id,
            "alternate_capability_id": alternate,
            "alternate_effective_state": alternate_effective,
            "reason": str(reason or "alternate solution required")[:1000],
            "at": now,
        },
        source_type="supervisor", source_id=SOURCE_ID,
        correlation_id=state.root_objective_id,
        idempotency_key=f"{ALTERNATE_REPLAN_REQUESTED}:{state.gap_id}",
    ))
    if alternate and alternate_effective:
        events.append(Event.create(
            ALTERNATE_SELECTED,
            {
                "gap_id": state.gap_id,
                "objective_id": state.origin_objective_id,
                "alternate_capability_id": alternate,
                "effective_state": alternate_effective,
                "plan_id": selected_plan_id,
                "at": now,
            },
            source_type="supervisor", source_id=SOURCE_ID,
            correlation_id=state.root_objective_id,
            idempotency_key=f"{ALTERNATE_SELECTED}:{state.gap_id}",
        ))
    database.append_events(events)
    wake = rebuild_wake_state(database).get(str(state.wake_id or ""))
    if wake is not None and wake.get("status") == "ACTIVE":
        clear_wake(
            database, str(state.wake_id),
            reason=f"gap {state.gap_id} left capability-wait for alternate replan",
        )
    if not alternate_effective:
        record_gap(database, GapRecord(
            gap_id=state.gap_id,
            needed_action=state.required_capability_id,
            missing=GapKind.ADAPTER,
            status=GapStatus.REJECTED,
            detail=str(reason or "alternate replan required")[:1000],
        ))
    return rebuild_gap_state(database)[gap_id]


def gap_bridge_snapshot(database: FoundryDatabase) -> dict[str, Any]:
    states = rebuild_gap_state(database)
    ordered = [states[key].as_dict() for key in sorted(states)]
    return {
        "contract_version": CONTRACT_VERSION,
        "count": len(ordered),
        "open": sum(not item.terminal for item in states.values()),
        "resumed": sum(item.status in {
            CapabilityGapStatus.RESUMED,
            CapabilityGapStatus.ALTERNATE_RESUMED,
        } for item in states.values()),
        "gaps": ordered,
    }


__all__ = [
    "CapabilityGapState",
    "CapabilityGapStatus",
    "CONTRACT_VERSION",
    "ExpansionAttempt",
    "ExpansionAttemptStatus",
    "ExpansionWorkPort",
    "ExpansionWorkRequest",
    "GapBridgeError",
    "GapBridgeStopped",
    "GapPolicy",
    "bridge_missing_plan_capabilities",
    "fail_expansion_attempt",
    "gap_bridge_snapshot",
    "open_capability_gap",
    "rebuild_gap_state",
    "reconcile_ready_gaps",
    "request_alternate_replan",
    "stable_attempt_id",
    "stable_gap_id",
    "start_expansion_attempt",
]
