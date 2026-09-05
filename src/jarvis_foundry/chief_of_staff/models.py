"""Immutable contracts for the event-derived Chief of Staff layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Mapping, Sequence

from ..clock import isoformat, parse


CHIEF_OF_STAFF_CONTRACT = "jarvis.chief-of-staff.v1"
CHIEF_OF_STAFF_SCHEMA_VERSION = 40
UNKNOWN = "UNKNOWN"

FrozenItems = tuple[tuple[str, Any], ...]


def _text(value: Any, *, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        source: Sequence[Any] = (value,)
    elif isinstance(value, Sequence):
        source = value
    else:
        raise ValueError("reference collections must contain strings")
    result: list[str] = []
    seen: set[str] = set()
    for item in source:
        normalized = str(item).strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _freeze(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple):
        if all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            for item in value
        ):
            return {key: _thaw(item) for key, item in value}
        return [_thaw(item) for item in value]
    return value


def freeze_mapping(value: Mapping[str, Any] | None) -> FrozenItems:
    frozen = _freeze(dict(value or {}))
    return frozen if isinstance(frozen, tuple) else ()


def thaw_mapping(value: FrozenItems) -> dict[str, Any]:
    thawed = _thaw(value)
    return thawed if isinstance(thawed, dict) else {}


def _timestamp(value: Any, *, name: str, default_now: bool = False) -> str | None:
    normalized = _optional_text(value)
    if normalized is None and default_now:
        normalized = isoformat()
    if normalized is None:
        return None
    try:
        parse(normalized)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    return normalized


def _optional_float(value: Any, *, name: str) -> float | None:
    if value is None or value == "":
        return None
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class ProjectChange:
    """One externally observed project change parsed at the service boundary."""

    change_id: str
    project_id: str
    change_type: str
    summary: str
    occurred_at: str
    goal_id: str | None = None
    program_id: str | None = None
    objective_id: str | None = None
    situation_state: str | None = None
    severity: str | None = None
    source_event_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    attributes: FrozenItems = ()

    @classmethod
    def from_mapping(
        cls,
        value: "ProjectChange | Mapping[str, Any]",
    ) -> "ProjectChange":
        if isinstance(value, cls):
            return value
        raw = dict(value)
        supplied_attributes = raw.pop("attributes", {})
        if supplied_attributes is None:
            supplied_attributes = {}
        if not isinstance(supplied_attributes, Mapping):
            raise ValueError("ProjectChange attributes must be a mapping")
        known = {
            "change_id",
            "idempotency_key",
            "project_id",
            "project",
            "change_type",
            "kind",
            "type",
            "summary",
            "description",
            "current_situation",
            "occurred_at",
            "observed_at",
            "changed_at",
            "timestamp",
            "goal_id",
            "program_id",
            "objective_id",
            "situation_state",
            "state",
            "severity",
            "source_event_refs",
            "source_refs",
            "event_refs",
            "evidence_refs",
        }
        attributes = dict(supplied_attributes)
        attributes.update({
            key: item
            for key, item in raw.items()
            if key not in known
        })
        return cls(
            change_id=_text(
                raw.get("change_id") or raw.get("idempotency_key"),
                name="change_id",
            ),
            project_id=_text(
                raw.get("project_id") or raw.get("project"),
                name="project_id",
            ),
            change_type=_text(
                raw.get("change_type") or raw.get("kind") or raw.get("type"),
                name="change_type",
            ).upper(),
            summary=_text(
                raw.get("summary")
                or raw.get("description")
                or raw.get("current_situation"),
                name="summary",
            ),
            occurred_at=_timestamp(
                raw.get("occurred_at")
                or raw.get("observed_at")
                or raw.get("changed_at")
                or raw.get("timestamp"),
                name="occurred_at",
                default_now=True,
            ) or isoformat(),
            goal_id=_optional_text(raw.get("goal_id")),
            program_id=_optional_text(raw.get("program_id")),
            objective_id=_optional_text(raw.get("objective_id")),
            situation_state=_optional_text(
                raw.get("situation_state") or raw.get("state"),
            ),
            severity=_optional_text(raw.get("severity")),
            source_event_refs=_strings(
                raw.get("source_event_refs")
                or raw.get("source_refs")
                or raw.get("event_refs"),
            ),
            evidence_refs=_strings(raw.get("evidence_refs")),
            attributes=freeze_mapping(attributes),
        )

    def attribute(self, name: str, default: Any = None) -> Any:
        return thaw_mapping(self.attributes).get(name, default)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": CHIEF_OF_STAFF_CONTRACT,
            "schema": CHIEF_OF_STAFF_SCHEMA_VERSION,
            "change_id": self.change_id,
            "project_id": self.project_id,
            "change_type": self.change_type,
            "summary": self.summary,
            "occurred_at": self.occurred_at,
            "goal_id": self.goal_id,
            "program_id": self.program_id,
            "objective_id": self.objective_id,
            "situation_state": self.situation_state,
            "severity": self.severity,
            "source_event_refs": list(self.source_event_refs),
            "evidence_refs": list(self.evidence_refs),
            "attributes": thaw_mapping(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class HierarchyLink:
    parent_kind: str
    parent_id: str
    child_kind: str
    child_id: str
    source_event_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "parent_kind": self.parent_kind,
            "parent_id": self.parent_id,
            "child_kind": self.child_kind,
            "child_id": self.child_id,
            "source_event_refs": list(self.source_event_refs),
        }


@dataclass(frozen=True, slots=True)
class ProjectState:
    """Evidence-backed project truth without owning downstream lifecycles."""

    project_id: str
    title: str
    goal_id: str | None = None
    program_id: str | None = None
    objective_ids: tuple[str, ...] = ()
    campaign_ids: tuple[str, ...] = ()
    mission_ids: tuple[str, ...] = ()
    action_ids: tuple[str, ...] = ()
    hierarchy_links: tuple[HierarchyLink, ...] = ()
    status: str = UNKNOWN
    health: str = UNKNOWN
    progress_status: str = UNKNOWN
    progress_completed: int | None = None
    progress_total: int | None = None
    blockers: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    recent_changes: tuple[str, ...] = ()
    last_meaningful_activity: str | None = None
    next_likely_action: str | None = None
    waiting_condition: str | None = None
    owner_decision_needed: bool = False
    owner_attention_ids: tuple[str, ...] = ()
    jarvis_handled_work: tuple[str, ...] = ()
    staleness: str = UNKNOWN
    risk_attention: str = UNKNOWN
    intentional_idle: bool = False
    source_event_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        progress_percent: float | str = UNKNOWN
        if (
            self.progress_completed is not None
            and self.progress_total is not None
            and self.progress_total > 0
        ):
            progress_percent = round(
                100.0 * self.progress_completed / self.progress_total,
                2,
            )
        return {
            "project_id": self.project_id,
            "title": self.title,
            "goal_id": self.goal_id,
            "program_id": self.program_id,
            "objective_ids": list(self.objective_ids),
            "campaign_ids": list(self.campaign_ids),
            "mission_ids": list(self.mission_ids),
            "action_ids": list(self.action_ids),
            "hierarchy_links": [
                link.as_dict() for link in self.hierarchy_links
            ],
            "status": self.status,
            "health": self.health,
            "progress": {
                "status": self.progress_status,
                "completed": self.progress_completed,
                "total": self.progress_total,
                "percent": progress_percent,
            },
            "blockers": list(self.blockers),
            "dependencies": list(self.dependencies),
            "recent_changes": list(self.recent_changes),
            "last_meaningful_activity": self.last_meaningful_activity,
            "next_likely_action": self.next_likely_action,
            "waiting_condition": self.waiting_condition,
            "owner_decision_needed": self.owner_decision_needed,
            "owner_attention_ids": list(self.owner_attention_ids),
            "jarvis_handled_work": list(self.jarvis_handled_work),
            "staleness": self.staleness,
            "risk_attention": self.risk_attention,
            "intentional_idle": self.intentional_idle,
            "source_event_refs": list(self.source_event_refs),
            "evidence_refs": list(self.evidence_refs),
            "unknown_fields": list(self.unknown_fields),
        }


@dataclass(frozen=True, slots=True)
class PriorityFactor:
    name: str
    raw_value: float | None
    normalized_value: float | None
    weight: float
    contribution: float
    evidence_refs: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.raw_value is not None and self.normalized_value is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "known": self.known,
            "raw_value": self.raw_value if self.known else UNKNOWN,
            "normalized_value": (
                self.normalized_value if self.known else UNKNOWN
            ),
            "weight": self.weight,
            "contribution": self.contribution,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ProjectPriority:
    project_id: str
    rank: int | None
    score: float
    factors: tuple[PriorityFactor, ...]
    unknown_factors: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    deliberate_idle: bool = False
    source_event_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "rank": self.rank if self.rank is not None else UNKNOWN,
            "score": self.score,
            "factors": {
                factor.name: factor.as_dict()
                for factor in self.factors
            },
            "unknown_factors": list(self.unknown_factors),
            "rationale": list(self.rationale),
            "deliberate_idle": self.deliberate_idle,
            "source_event_refs": list(self.source_event_refs),
        }


@dataclass(frozen=True, slots=True)
class OwnerAttentionItem:
    attention_id: str
    project_id: str
    category: str
    summary: str
    reason: str
    created_at: str
    objective_id: str | None = None
    status: str = "OPEN"
    standing_order_ids: tuple[str, ...] = ()
    source_event_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "attention_id": self.attention_id,
            "project_id": self.project_id,
            "objective_id": self.objective_id,
            "category": self.category,
            "summary": self.summary,
            "reason": self.reason,
            "created_at": self.created_at,
            "status": self.status,
            "standing_order_ids": list(self.standing_order_ids),
            "source_event_refs": list(self.source_event_refs),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class CandidateProposal:
    objective: str
    candidate_type: str
    expected_outcome: str
    value: float | None
    urgency: float | None
    confidence: float | None
    reversible: bool | None
    evidence_refs: tuple[str, ...] = ()
    standing_order_ids: tuple[str, ...] = ()
    attributes: FrozenItems = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "candidate_type": self.candidate_type,
            "expected_outcome": self.expected_outcome,
            "value": self.value if self.value is not None else UNKNOWN,
            "urgency": self.urgency if self.urgency is not None else UNKNOWN,
            "confidence": (
                self.confidence if self.confidence is not None else UNKNOWN
            ),
            "reversible": (
                self.reversible if self.reversible is not None else UNKNOWN
            ),
            "evidence_refs": list(self.evidence_refs),
            "standing_order_ids": list(self.standing_order_ids),
            "attributes": thaw_mapping(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class ConsiderResult:
    change_id: str
    project_id: str
    status: str
    change_event_ref: str | None
    recorded: bool
    proposal: CandidateProposal | None = None
    candidate_id: str | None = None
    candidate_status: str | None = None
    duplicate: bool = False
    rejection_reason: str | None = None
    owner_attention_id: str | None = None
    wait_id: str | None = None
    canonical_wake_id: str | None = None
    standing_order_ids: tuple[str, ...] = ()
    source_event_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "project_id": self.project_id,
            "status": self.status,
            "change_event_ref": self.change_event_ref,
            "recorded": self.recorded,
            "proposal": self.proposal.as_dict() if self.proposal else None,
            "candidate_id": self.candidate_id,
            "candidate_status": self.candidate_status,
            "duplicate": self.duplicate,
            "rejection_reason": self.rejection_reason,
            "owner_attention_id": self.owner_attention_id,
            "wait_id": self.wait_id,
            "canonical_wake_id": self.canonical_wake_id,
            "standing_order_ids": list(self.standing_order_ids),
            "source_event_refs": list(self.source_event_refs),
        }


@dataclass(frozen=True, slots=True)
class BriefingEntry:
    entry_id: str
    project_id: str
    category: str
    summary: str
    occurred_at: str
    source_event_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "project_id": self.project_id,
            "category": self.category,
            "summary": self.summary,
            "occurred_at": self.occurred_at,
            "source_event_refs": list(self.source_event_refs),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ChiefOfStaffProjection:
    generated_at: str
    projects: tuple[ProjectState, ...]
    priorities: tuple[ProjectPriority, ...]
    owner_attention: tuple[OwnerAttentionItem, ...]
    briefing: tuple[BriefingEntry, ...]
    source_event_refs: tuple[str, ...] = field(default_factory=tuple)

    def project(self, project_id: str) -> ProjectState | None:
        return next(
            (item for item in self.projects if item.project_id == project_id),
            None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": CHIEF_OF_STAFF_CONTRACT,
            "schema": CHIEF_OF_STAFF_SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "projects": [item.as_dict() for item in self.projects],
            "priorities": [item.as_dict() for item in self.priorities],
            "owner_attention": [
                item.as_dict() for item in self.owner_attention
            ],
            "briefing": [item.as_dict() for item in self.briefing],
            "source_event_refs": list(self.source_event_refs),
        }


__all__ = [
    "BriefingEntry",
    "CHIEF_OF_STAFF_CONTRACT",
    "CHIEF_OF_STAFF_SCHEMA_VERSION",
    "CandidateProposal",
    "ChiefOfStaffProjection",
    "ConsiderResult",
    "FrozenItems",
    "HierarchyLink",
    "OwnerAttentionItem",
    "PriorityFactor",
    "ProjectChange",
    "ProjectPriority",
    "ProjectState",
    "UNKNOWN",
    "freeze_mapping",
    "thaw_mapping",
]
