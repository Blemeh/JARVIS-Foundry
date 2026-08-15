"""Typed, replayable R7 portfolio records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _as_tuple(value: Any) -> tuple[Any, ...]:
    return tuple(value) if value is not None else ()


def _serialize(value: Any) -> Any:
    if isinstance(value, Record):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    return value


class Record:
    """Base record with explicit deterministic dict serialization."""

    def to_dict(self) -> dict[str, Any]:
        return {
            field_name: _serialize(getattr(self, field_name)) for field_name in self.dict_fields()
        }

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        raise NotImplementedError


@dataclass(frozen=True)
class SourceRef(Record):
    source_type: str
    source_id: str
    captured_at: str
    content_hash: str
    locator: str = ""
    capture_method: str = ""
    trust_class: str = "owner-controlled"
    freshness_seconds: int = 3600

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "source_type",
            "source_id",
            "captured_at",
            "content_hash",
            "locator",
            "capture_method",
            "trust_class",
            "freshness_seconds",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SourceRef:
        return SourceRef(
            source_type=str(payload["source_type"]),
            source_id=str(payload["source_id"]),
            captured_at=str(payload["captured_at"]),
            content_hash=str(payload["content_hash"]),
            locator=str(payload.get("locator", "")),
            capture_method=str(payload.get("capture_method", "")),
            trust_class=str(payload.get("trust_class", "owner-controlled")),
            freshness_seconds=int(payload.get("freshness_seconds", 3600)),
        )


@dataclass(frozen=True)
class ProjectRisk(Record):
    risk_id: str
    title: str
    severity: str
    likelihood: str
    source_ref: SourceRef

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return ("risk_id", "title", "severity", "likelihood", "source_ref")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProjectRisk:
        return ProjectRisk(
            risk_id=str(payload["risk_id"]),
            title=str(payload["title"]),
            severity=str(payload["severity"]),
            likelihood=str(payload["likelihood"]),
            source_ref=SourceRef.from_dict(payload["source_ref"]),
        )


@dataclass(frozen=True)
class PortfolioProject(Record):
    project_id: str
    title: str
    canonical_target: str
    goal_id: str
    goal_title: str
    status: str
    deadline: str | None
    resources: tuple[str, ...]
    cost_usd: float
    expected_value: float
    urgency: float
    information_gain: float
    reversibility: float
    opportunity_cost: float
    risk: float
    dependencies: tuple[str, ...]
    risks: tuple[ProjectRisk, ...]
    source_ref: SourceRef
    observed_at: str
    priority: int = 0
    notes: str = ""

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "project_id",
            "title",
            "canonical_target",
            "goal_id",
            "goal_title",
            "status",
            "deadline",
            "resources",
            "cost_usd",
            "expected_value",
            "urgency",
            "information_gain",
            "reversibility",
            "opportunity_cost",
            "risk",
            "dependencies",
            "risks",
            "source_ref",
            "observed_at",
            "priority",
            "notes",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PortfolioProject:
        return PortfolioProject(
            project_id=str(payload["project_id"]),
            title=str(payload["title"]),
            canonical_target=str(payload["canonical_target"]),
            goal_id=str(payload["goal_id"]),
            goal_title=str(payload["goal_title"]),
            status=str(payload["status"]),
            deadline=payload.get("deadline"),
            resources=tuple(str(item) for item in _as_tuple(payload.get("resources"))),
            cost_usd=float(payload.get("cost_usd", 0.0)),
            expected_value=float(payload.get("expected_value", 0.0)),
            urgency=float(payload.get("urgency", 0.0)),
            information_gain=float(payload.get("information_gain", 0.0)),
            reversibility=float(payload.get("reversibility", 0.0)),
            opportunity_cost=float(payload.get("opportunity_cost", 0.0)),
            risk=float(payload.get("risk", 0.0)),
            dependencies=tuple(str(item) for item in _as_tuple(payload.get("dependencies"))),
            risks=tuple(ProjectRisk.from_dict(item) for item in _as_tuple(payload.get("risks"))),
            source_ref=SourceRef.from_dict(payload["source_ref"]),
            observed_at=str(payload["observed_at"]),
            priority=int(payload.get("priority", 0)),
            notes=str(payload.get("notes", "")),
        )


@dataclass(frozen=True)
class CandidateAction(Record):
    candidate_id: str
    project_id: str
    objective: str
    action_class: str
    canonical_target: str
    evidence_refs: tuple[SourceRef, ...]
    expected_value: float
    uncertainty: float
    urgency: float
    information_gain: float
    reversibility: float
    opportunity_cost: float
    risk: float
    cost_usd: float
    budget_effects: int
    status: str = "READY"
    priority_boost: int = 0
    created_at: str = ""

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "candidate_id",
            "project_id",
            "objective",
            "action_class",
            "canonical_target",
            "evidence_refs",
            "expected_value",
            "uncertainty",
            "urgency",
            "information_gain",
            "reversibility",
            "opportunity_cost",
            "risk",
            "cost_usd",
            "budget_effects",
            "status",
            "priority_boost",
            "created_at",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CandidateAction:
        return CandidateAction(
            candidate_id=str(payload["candidate_id"]),
            project_id=str(payload["project_id"]),
            objective=str(payload["objective"]),
            action_class=str(payload["action_class"]),
            canonical_target=str(payload["canonical_target"]),
            evidence_refs=tuple(
                SourceRef.from_dict(item) for item in _as_tuple(payload.get("evidence_refs"))
            ),
            expected_value=float(payload.get("expected_value", 0.0)),
            uncertainty=float(payload.get("uncertainty", 0.0)),
            urgency=float(payload.get("urgency", 0.0)),
            information_gain=float(payload.get("information_gain", 0.0)),
            reversibility=float(payload.get("reversibility", 0.0)),
            opportunity_cost=float(payload.get("opportunity_cost", 0.0)),
            risk=float(payload.get("risk", 0.0)),
            cost_usd=float(payload.get("cost_usd", 0.0)),
            budget_effects=int(payload.get("budget_effects", 0)),
            status=str(payload.get("status", "READY")),
            priority_boost=int(payload.get("priority_boost", 0)),
            created_at=str(payload.get("created_at", "")),
        )


@dataclass(frozen=True)
class AlternativeScore(Record):
    candidate_id: str
    total_score: float
    dimensions: Mapping[str, float]
    uncertainty: float
    opportunity_cost: float
    tradeoffs: tuple[str, ...]
    reason: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "candidate_id",
            "total_score",
            "dimensions",
            "uncertainty",
            "opportunity_cost",
            "tradeoffs",
            "reason",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AlternativeScore:
        return AlternativeScore(
            candidate_id=str(payload["candidate_id"]),
            total_score=float(payload["total_score"]),
            dimensions={str(k): float(v) for k, v in dict(payload.get("dimensions", {})).items()},
            uncertainty=float(payload.get("uncertainty", 0.0)),
            opportunity_cost=float(payload.get("opportunity_cost", 0.0)),
            tradeoffs=tuple(str(item) for item in _as_tuple(payload.get("tradeoffs"))),
            reason=str(payload.get("reason", "")),
        )


@dataclass(frozen=True)
class SelectionDecision(Record):
    decision_id: str
    portfolio_hash: str
    control_hash: str
    budget_key: str
    selected_candidate_id: str | None
    project_id: str | None
    canonical_target: str | None
    status: str
    reason: str
    alternatives: tuple[AlternativeScore, ...]
    uncertainty: Mapping[str, float]
    supersedes: str | None
    created_at: str
    idempotency_key: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "decision_id",
            "portfolio_hash",
            "control_hash",
            "budget_key",
            "selected_candidate_id",
            "project_id",
            "canonical_target",
            "status",
            "reason",
            "alternatives",
            "uncertainty",
            "supersedes",
            "created_at",
            "idempotency_key",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SelectionDecision:
        return SelectionDecision(
            decision_id=str(payload["decision_id"]),
            portfolio_hash=str(payload["portfolio_hash"]),
            control_hash=str(payload["control_hash"]),
            budget_key=str(payload["budget_key"]),
            selected_candidate_id=payload.get("selected_candidate_id"),
            project_id=payload.get("project_id"),
            canonical_target=payload.get("canonical_target"),
            status=str(payload["status"]),
            reason=str(payload.get("reason", "")),
            alternatives=tuple(
                AlternativeScore.from_dict(item) for item in _as_tuple(payload.get("alternatives"))
            ),
            uncertainty={str(k): float(v) for k, v in dict(payload.get("uncertainty", {})).items()},
            supersedes=payload.get("supersedes"),
            created_at=str(payload["created_at"]),
            idempotency_key=str(payload["idempotency_key"]),
        )


@dataclass(frozen=True)
class Observation(Record):
    observation_id: str
    source_type: str
    source_id: str
    captured_at: str
    content_hash: str
    evidence_refs: tuple[SourceRef, ...]
    observed_value: str
    meaningful_change: bool = False
    change_hash: str | None = None
    project_id: str = ""

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "observation_id",
            "source_type",
            "source_id",
            "captured_at",
            "content_hash",
            "evidence_refs",
            "observed_value",
            "meaningful_change",
            "change_hash",
            "project_id",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Observation:
        return Observation(
            observation_id=str(payload["observation_id"]),
            source_type=str(payload["source_type"]),
            source_id=str(payload["source_id"]),
            captured_at=str(payload["captured_at"]),
            content_hash=str(payload["content_hash"]),
            evidence_refs=tuple(
                SourceRef.from_dict(item) for item in _as_tuple(payload.get("evidence_refs"))
            ),
            observed_value=str(payload.get("observed_value", "")),
            meaningful_change=bool(payload.get("meaningful_change", False)),
            change_hash=payload.get("change_hash"),
            project_id=str(payload.get("project_id", "")),
        )


@dataclass(frozen=True)
class DiscoveredCandidate(Record):
    candidate_id: str
    observation_id: str
    project_id: str
    objective: str
    evidence_refs: tuple[SourceRef, ...]
    status: str
    duplicate_of: str | None
    created_at: str
    authority: str = "NONE"
    rejection_reason: str = ""

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "candidate_id",
            "observation_id",
            "project_id",
            "objective",
            "evidence_refs",
            "status",
            "duplicate_of",
            "created_at",
            "authority",
            "rejection_reason",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DiscoveredCandidate:
        return DiscoveredCandidate(
            candidate_id=str(payload["candidate_id"]),
            observation_id=str(payload["observation_id"]),
            project_id=str(payload.get("project_id", "")),
            objective=str(payload.get("objective", "")),
            evidence_refs=tuple(
                SourceRef.from_dict(item) for item in _as_tuple(payload.get("evidence_refs"))
            ),
            status=str(payload.get("status", "CANDIDATE")),
            duplicate_of=payload.get("duplicate_of"),
            created_at=str(payload.get("created_at", "")),
            authority=str(payload.get("authority", "NONE")),
            rejection_reason=str(payload.get("rejection_reason", "")),
        )


@dataclass(frozen=True)
class ResearchSource(Record):
    source_id: str
    locator: str
    capture_method: str
    captured_at: str
    content_hash: str
    trust_class: str
    freshness_seconds: int
    status: str = "FRESH"
    content_excerpt: str = ""

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "source_id",
            "locator",
            "capture_method",
            "captured_at",
            "content_hash",
            "trust_class",
            "freshness_seconds",
            "status",
            "content_excerpt",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ResearchSource:
        return ResearchSource(
            source_id=str(payload["source_id"]),
            locator=str(payload["locator"]),
            capture_method=str(payload.get("capture_method", "")),
            captured_at=str(payload["captured_at"]),
            content_hash=str(payload.get("content_hash", "")),
            trust_class=str(payload.get("trust_class", "owner-controlled")),
            freshness_seconds=int(payload.get("freshness_seconds", 3600)),
            status=str(payload.get("status", "FRESH")),
            content_excerpt=str(payload.get("content_excerpt", "")),
        )


@dataclass(frozen=True)
class ResearchClaim(Record):
    claim_id: str
    source_id: str
    statement: str
    polarity: str
    freshness_ok: bool
    citation: str
    uncertainty: float

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "claim_id",
            "source_id",
            "statement",
            "polarity",
            "freshness_ok",
            "citation",
            "uncertainty",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ResearchClaim:
        return ResearchClaim(
            claim_id=str(payload["claim_id"]),
            source_id=str(payload["source_id"]),
            statement=str(payload["statement"]),
            polarity=str(payload.get("polarity", "NEUTRAL")),
            freshness_ok=bool(payload.get("freshness_ok", False)),
            citation=str(payload.get("citation", "")),
            uncertainty=float(payload.get("uncertainty", 0.0)),
        )


@dataclass(frozen=True)
class ResearchSynthesis(Record):
    research_id: str
    question: str
    project_id: str
    value_statement: str
    sources: tuple[ResearchSource, ...]
    claims: tuple[ResearchClaim, ...]
    contradictions: tuple[ResearchClaim, ...]
    unknowns: tuple[str, ...]
    decision_input: str
    status: str
    authority: str
    injection_attempts: int
    snapshot_dir: str
    created_at: str
    idempotency_key: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "research_id",
            "question",
            "project_id",
            "value_statement",
            "sources",
            "claims",
            "contradictions",
            "unknowns",
            "decision_input",
            "status",
            "authority",
            "injection_attempts",
            "snapshot_dir",
            "created_at",
            "idempotency_key",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ResearchSynthesis:
        return ResearchSynthesis(
            research_id=str(payload["research_id"]),
            question=str(payload["question"]),
            project_id=str(payload["project_id"]),
            value_statement=str(payload.get("value_statement", "")),
            sources=tuple(
                ResearchSource.from_dict(item) for item in _as_tuple(payload.get("sources"))
            ),
            claims=tuple(
                ResearchClaim.from_dict(item) for item in _as_tuple(payload.get("claims"))
            ),
            contradictions=tuple(
                ResearchClaim.from_dict(item) for item in _as_tuple(payload.get("contradictions"))
            ),
            unknowns=tuple(str(item) for item in _as_tuple(payload.get("unknowns"))),
            decision_input=str(payload.get("decision_input", "")),
            status=str(payload.get("status", "READY")),
            authority=str(payload.get("authority", "UNTRUSTED_DATA")),
            injection_attempts=int(payload.get("injection_attempts", 0)),
            snapshot_dir=str(payload.get("snapshot_dir", "")),
            created_at=str(payload.get("created_at", "")),
            idempotency_key=str(payload.get("idempotency_key", "")),
        )


@dataclass(frozen=True)
class MonitorCursor(Record):
    source_id: str
    locator: str
    last_captured_at: str
    last_content_hash: str
    last_change_hash: str | None
    candidate_id: str | None
    freshness_seconds: int
    status: str = "ACTIVE"

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "source_id",
            "locator",
            "last_captured_at",
            "last_content_hash",
            "last_change_hash",
            "candidate_id",
            "freshness_seconds",
            "status",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MonitorCursor:
        return MonitorCursor(
            source_id=str(payload["source_id"]),
            locator=str(payload.get("locator", "")),
            last_captured_at=str(payload.get("last_captured_at", "")),
            last_content_hash=str(payload.get("last_content_hash", "")),
            last_change_hash=payload.get("last_change_hash"),
            candidate_id=payload.get("candidate_id"),
            freshness_seconds=int(payload.get("freshness_seconds", 900)),
            status=str(payload.get("status", "ACTIVE")),
        )


@dataclass(frozen=True)
class MonitorChange(Record):
    change_id: str
    source_id: str
    before_hash: str
    after_hash: str
    captured_at: str
    meaningful: bool
    candidate_id: str | None
    duplicate_of: str | None
    idempotency_key: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "change_id",
            "source_id",
            "before_hash",
            "after_hash",
            "captured_at",
            "meaningful",
            "candidate_id",
            "duplicate_of",
            "idempotency_key",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MonitorChange:
        return MonitorChange(
            change_id=str(payload["change_id"]),
            source_id=str(payload["source_id"]),
            before_hash=str(payload["before_hash"]),
            after_hash=str(payload["after_hash"]),
            captured_at=str(payload["captured_at"]),
            meaningful=bool(payload.get("meaningful", False)),
            candidate_id=payload.get("candidate_id"),
            duplicate_of=payload.get("duplicate_of"),
            idempotency_key=str(payload["idempotency_key"]),
        )


@dataclass(frozen=True)
class MissionLink(Record):
    mission_id: str
    goal_id: str
    candidate_id: str
    decision_id: str
    ledger_event_id: str
    ledger_header_hash: str | None
    created_at: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "mission_id",
            "goal_id",
            "candidate_id",
            "decision_id",
            "ledger_event_id",
            "ledger_header_hash",
            "created_at",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MissionLink:
        return MissionLink(
            mission_id=str(payload["mission_id"]),
            goal_id=str(payload["goal_id"]),
            candidate_id=str(payload.get("candidate_id", "")),
            decision_id=str(payload.get("decision_id", "")),
            ledger_event_id=str(payload.get("ledger_event_id", "")),
            ledger_header_hash=payload.get("ledger_header_hash"),
            created_at=str(payload.get("created_at", "")),
        )


@dataclass(frozen=True)
class BudgetState(Record):
    budget_key: str
    max_calls: int
    max_tokens: int
    max_cost_usd: float
    max_effects: int
    used_calls: int
    used_tokens: int
    used_cost_usd: float

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "budget_key",
            "max_calls",
            "max_tokens",
            "max_cost_usd",
            "max_effects",
            "used_calls",
            "used_tokens",
            "used_cost_usd",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BudgetState:
        return BudgetState(
            budget_key=str(payload["budget_key"]),
            max_calls=int(payload.get("max_calls", 0)),
            max_tokens=int(payload.get("max_tokens", 0)),
            max_cost_usd=float(payload.get("max_cost_usd", 0.0)),
            max_effects=int(payload.get("max_effects", 0)),
            used_calls=int(payload.get("used_calls", 0)),
            used_tokens=int(payload.get("used_tokens", 0)),
            used_cost_usd=float(payload.get("used_cost_usd", 0.0)),
        )


@dataclass(frozen=True)
class ControlDirective(Record):
    directive_id: str
    command_type: str
    target: str | None
    nonce: str
    reason: str
    recorded_at: str
    status: str
    value: str = ""

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "directive_id",
            "command_type",
            "target",
            "nonce",
            "reason",
            "recorded_at",
            "status",
            "value",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ControlDirective:
        return ControlDirective(
            directive_id=str(payload["directive_id"]),
            command_type=str(payload["command_type"]),
            target=payload.get("target"),
            nonce=str(payload["nonce"]),
            reason=str(payload.get("reason", "")),
            recorded_at=str(payload.get("recorded_at", "")),
            status=str(payload.get("status", "ACTIVE")),
            value=str(payload.get("value", "")),
        )
