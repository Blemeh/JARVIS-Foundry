"""V13 advisory research planning and decision contracts.

This module is deliberately subordinate to the existing Executive. It plans
bounded research, compares serious options, records advisory artifacts in the
existing event ledger, and can submit one DATA_ONLY observation candidate to
the canonical A2 decision service. It cannot create an objective or mission.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar
from urllib.parse import urlsplit

from .control.redaction import redact_text
from .database import FoundryDatabase
from .events import Event, canonical_json
from .research_dossier import DOSSIER_CONTRACT, DOSSIER_SCORING_VERSION


RESEARCH_PLAN_CONTRACT = "jarvis.v13.research-plan.v1"
DECISION_OPTION_CONTRACT = "jarvis.v13.decision-option.v1"
DECISION_RECOMMENDATION_CONTRACT = "jarvis.v13.decision-recommendation.v1"
DECISION_RESULT_CONTRACT = "jarvis.v13.decision-result.v1"
DECISION_OUTCOME_CONTRACT = "jarvis.v13.decision-outcome.v1"
CALIBRATION_CONTRACT = "jarvis.v13.research-decision-calibration.v1"

DATA_ONLY = "DATA_ONLY"
ADVISORY_ONLY = "ADVISORY_ONLY"

SOURCE_CLASSES = frozenset({
    "PRIMARY_OFFICIAL",
    "UPSTREAM_TECHNICAL",
    "HIGH_QUALITY_SECONDARY",
    "COMMUNITY_EXPERIENCE",
    "WEAK_UNKNOWN",
})
STRONG_SOURCE_CLASSES = frozenset({
    "PRIMARY_OFFICIAL",
    "UPSTREAM_TECHNICAL",
    "HIGH_QUALITY_SECONDARY",
})

DECISION_DIMENSIONS = (
    "owner_goals",
    "project_context",
    "expected_value",
    "evidence_strength",
    "confidence",
    "quality",
    "cost",
    "time",
    "risk",
    "reversibility",
    "dependencies",
    "maintainability",
    "compatibility",
    "opportunity_cost",
)
DEFAULT_CRITICAL_DIMENSIONS = (
    "owner_goals",
    "evidence_strength",
    "confidence",
    "risk",
    "dependencies",
    "compatibility",
)


class ResearchDisposition(StrEnum):
    CONTINUE = "CONTINUE"
    STOP_ENOUGH_EVIDENCE = "STOP_ENOUGH_EVIDENCE"
    STOP_INSUFFICIENT_EVIDENCE = "STOP_INSUFFICIENT_EVIDENCE"


class QualitativeAssessment(StrEnum):
    STRONG_SUPPORT = "STRONG_SUPPORT"
    SUPPORT = "SUPPORT"
    NEUTRAL = "NEUTRAL"
    CONCERN = "CONCERN"
    BLOCKER = "BLOCKER"
    UNKNOWN = "UNKNOWN"


class RecommendationStatus(StrEnum):
    RECOMMEND = "RECOMMEND"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ConfidenceBand(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ActualOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ResearchDecisionError(ValueError):
    """A V13 research-decision contract is invalid."""


class ExecutiveBridgeError(ResearchDecisionError):
    """An advisory recommendation cannot cross the A2 boundary."""


_ASSESSMENT_VALUE = {
    QualitativeAssessment.STRONG_SUPPORT: 2,
    QualitativeAssessment.SUPPORT: 1,
    QualitativeAssessment.NEUTRAL: 0,
    QualitativeAssessment.CONCERN: -1,
    QualitativeAssessment.BLOCKER: -3,
    QualitativeAssessment.UNKNOWN: -1,
}
_DIMENSION_WEIGHT = {
    "owner_goals": 2,
    "project_context": 2,
    "expected_value": 2,
    "evidence_strength": 2,
    "confidence": 2,
    "quality": 2,
    "risk": 2,
    "dependencies": 2,
    "compatibility": 2,
}
_CONFIDENCE_ORDER = {
    ConfidenceBand.LOW: 0,
    ConfidenceBand.MEDIUM: 1,
    ConfidenceBand.HIGH: 2,
}


def _text(value: Any, field_name: str, *, limit: int = 1200) -> str:
    result = redact_text(str(value or "").replace("\x00", " ").strip())[:limit]
    if not result:
        raise ResearchDecisionError(f"{field_name} is required")
    return result


def _optional_text(value: Any, *, limit: int = 1200) -> str:
    return redact_text(str(value or "").replace("\x00", " ").strip())[:limit]


def _identity(value: Any, field_name: str) -> str:
    result = str(value or "").strip()
    if (
        not result
        or len(result) > 256
        or any(char.isspace() or ord(char) < 32 for char in result)
    ):
        raise ResearchDecisionError(
            f"{field_name} must be a non-empty bounded identity"
        )
    return result


def _strings(
    values: Sequence[Any] | None,
    field_name: str,
    *,
    limit: int = 32,
    item_limit: int = 600,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ResearchDecisionError(f"{field_name} must be a sequence")
    rows = tuple(
        _text(value, field_name, limit=item_limit)
        for value in (values or ())
    )
    if len(rows) > limit:
        raise ResearchDecisionError(f"{field_name} exceeds {limit} items")
    return tuple(dict.fromkeys(rows))


def _utc(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as error:
            raise ResearchDecisionError(f"invalid ISO timestamp {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | str | None = None) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: Any, *, length: int = 24) -> str:
    return f"{prefix}-{_sha(value)[:length]}"


def _plain(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def _confidence_band(value: Any) -> ConfidenceBand:
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return ConfidenceBand.LOW
    if not math.isfinite(score):
        return ConfidenceBand.LOW
    if score >= 0.75:
        return ConfidenceBand.HIGH
    if score >= 0.50:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def _normalize_source_class(value: Any) -> str:
    result = str(value or "WEAK_UNKNOWN").strip().upper()
    if result not in SOURCE_CLASSES:
        return "WEAK_UNKNOWN"
    return result


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    """One bounded, provider-free research plan."""

    CONTRACT: ClassVar[str] = RESEARCH_PLAN_CONTRACT

    plan_id: str
    objective: str
    knowns: tuple[str, ...] = ()
    decision_critical_unknowns: tuple[str, ...] = ()
    research_questions: tuple[str, ...] = ()
    search_targets: tuple[str, ...] = ()
    preferred_source_classes: tuple[str, ...] = (
        "PRIMARY_OFFICIAL",
        "UPSTREAM_TECHNICAL",
        "HIGH_QUALITY_SECONDARY",
    )
    max_initial_reads: int = 4
    max_follow_up_reads: int = 2
    min_independent_sources: int = 2
    min_strong_sources: int = 1
    max_unresolved_critical_unknowns: int = 0
    minimum_confidence_band: ConfidenceBand = ConfidenceBand.MEDIUM
    minimum_marginal_information_value: float = 0.20
    action_urgency: str = "NORMAL"
    revisit_trigger: str = "material evidence revision or dossier expiry"
    require_fresh_dossier: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _identity(self.plan_id, "plan_id"))
        object.__setattr__(
            self, "objective", _text(self.objective, "objective", limit=1000)
        )
        object.__setattr__(
            self, "knowns", _strings(self.knowns, "knowns", limit=24)
        )
        object.__setattr__(
            self,
            "decision_critical_unknowns",
            _strings(
                self.decision_critical_unknowns,
                "decision_critical_unknowns",
                limit=16,
            ),
        )
        object.__setattr__(
            self,
            "research_questions",
            _strings(self.research_questions, "research_questions", limit=24),
        )
        object.__setattr__(
            self,
            "search_targets",
            _strings(self.search_targets, "search_targets", limit=24),
        )
        classes = tuple(dict.fromkeys(
            _normalize_source_class(item)
            for item in self.preferred_source_classes
        ))
        if not classes or any(item == "WEAK_UNKNOWN" for item in classes):
            raise ResearchDecisionError(
                "preferred_source_classes must name known non-weak classes"
            )
        object.__setattr__(self, "preferred_source_classes", classes)
        if not 1 <= int(self.max_initial_reads) <= 12:
            raise ResearchDecisionError("max_initial_reads must be within [1, 12]")
        if not 0 <= int(self.max_follow_up_reads) <= 8:
            raise ResearchDecisionError(
                "max_follow_up_reads must be within [0, 8]"
            )
        total = self.max_read_budget
        if not 1 <= int(self.min_independent_sources) <= total:
            raise ResearchDecisionError(
                "min_independent_sources must fit within the read budget"
            )
        if not 1 <= int(self.min_strong_sources) <= total:
            raise ResearchDecisionError(
                "min_strong_sources must fit within the read budget"
            )
        if not 0 <= int(self.max_unresolved_critical_unknowns) <= 8:
            raise ResearchDecisionError(
                "max_unresolved_critical_unknowns must be within [0, 8]"
            )
        if not isinstance(self.minimum_confidence_band, ConfidenceBand):
            object.__setattr__(
                self,
                "minimum_confidence_band",
                ConfidenceBand(self.minimum_confidence_band),
            )
        marginal = float(self.minimum_marginal_information_value)
        if not math.isfinite(marginal) or not 0.0 <= marginal <= 1.0:
            raise ResearchDecisionError(
                "minimum_marginal_information_value must be within [0, 1]"
            )
        object.__setattr__(
            self, "minimum_marginal_information_value", marginal,
        )
        urgency = str(self.action_urgency or "NORMAL").strip().upper()
        if urgency not in {"LOW", "NORMAL", "HIGH", "CRITICAL"}:
            raise ResearchDecisionError(
                "action_urgency must be LOW, NORMAL, HIGH, or CRITICAL"
            )
        object.__setattr__(self, "action_urgency", urgency)
        object.__setattr__(
            self,
            "revisit_trigger",
            _text(self.revisit_trigger, "revisit_trigger", limit=600),
        )

    @property
    def max_read_budget(self) -> int:
        return int(self.max_initial_reads + self.max_follow_up_reads)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.CONTRACT,
            "plan_id": self.plan_id,
            "objective": self.objective,
            "knowns": list(self.knowns),
            "decision_critical_unknowns": list(
                self.decision_critical_unknowns
            ),
            "research_questions": list(self.research_questions),
            "search_targets": list(self.search_targets),
            "preferred_source_classes": list(self.preferred_source_classes),
            "read_budget": {
                "max_initial_reads": self.max_initial_reads,
                "max_follow_up_reads": self.max_follow_up_reads,
                "max_total_reads": self.max_read_budget,
            },
            "stopping_criteria": {
                "min_independent_sources": self.min_independent_sources,
                "min_strong_sources": self.min_strong_sources,
                "max_unresolved_critical_unknowns": (
                    self.max_unresolved_critical_unknowns
                ),
                "minimum_confidence_band": self.minimum_confidence_band.value,
                "minimum_marginal_information_value": (
                    self.minimum_marginal_information_value
                ),
                "action_urgency": self.action_urgency,
                "revisit_trigger": self.revisit_trigger,
                "require_fresh_dossier": self.require_fresh_dossier,
            },
            "authority": ADVISORY_ONLY,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ResearchPlan":
        if value.get("contract") not in {None, cls.CONTRACT}:
            raise ResearchDecisionError("unsupported research-plan contract")
        budget = value.get("read_budget")
        budget = budget if isinstance(budget, Mapping) else {}
        stopping = value.get("stopping_criteria")
        stopping = stopping if isinstance(stopping, Mapping) else {}
        return cls(
            plan_id=str(value.get("plan_id") or ""),
            objective=str(value.get("objective") or ""),
            knowns=tuple(value.get("knowns") or ()),
            decision_critical_unknowns=tuple(
                value.get("decision_critical_unknowns") or ()
            ),
            research_questions=tuple(value.get("research_questions") or ()),
            search_targets=tuple(value.get("search_targets") or ()),
            preferred_source_classes=tuple(
                value.get("preferred_source_classes") or ()
            ),
            max_initial_reads=int(budget.get("max_initial_reads", 4)),
            max_follow_up_reads=int(budget.get("max_follow_up_reads", 2)),
            min_independent_sources=int(
                stopping.get("min_independent_sources", 2)
            ),
            min_strong_sources=int(stopping.get("min_strong_sources", 1)),
            max_unresolved_critical_unknowns=int(
                stopping.get("max_unresolved_critical_unknowns", 0)
            ),
            minimum_confidence_band=ConfidenceBand(
                stopping.get("minimum_confidence_band", "MEDIUM")
            ),
            minimum_marginal_information_value=float(
                stopping.get("minimum_marginal_information_value", 0.20)
            ),
            action_urgency=str(stopping.get("action_urgency") or "NORMAL"),
            revisit_trigger=str(
                stopping.get("revisit_trigger")
                or "material evidence revision or dossier expiry"
            ),
            require_fresh_dossier=bool(
                stopping.get("require_fresh_dossier", True)
            ),
        )


def plan_research(
    *,
    objective: str,
    knowns: Sequence[str] = (),
    decision_critical_unknowns: Sequence[str] = (),
    research_questions: Sequence[str] = (),
    search_targets: Sequence[str] = (),
    preferred_source_classes: Sequence[str] = (
        "PRIMARY_OFFICIAL",
        "UPSTREAM_TECHNICAL",
        "HIGH_QUALITY_SECONDARY",
    ),
    max_initial_reads: int = 4,
    max_follow_up_reads: int = 2,
    min_independent_sources: int = 2,
    min_strong_sources: int = 1,
    max_unresolved_critical_unknowns: int = 0,
    minimum_confidence_band: ConfidenceBand | str = ConfidenceBand.MEDIUM,
    minimum_marginal_information_value: float = 0.20,
    action_urgency: str = "NORMAL",
    revisit_trigger: str = "material evidence revision or dossier expiry",
    require_fresh_dossier: bool = True,
    plan_id: str | None = None,
) -> ResearchPlan:
    """Build a stable research plan without performing any retrieval."""

    material = {
        "objective": _text(objective, "objective", limit=1000),
        "knowns": list(knowns),
        "decision_critical_unknowns": list(decision_critical_unknowns),
        "research_questions": list(research_questions),
        "search_targets": list(search_targets),
        "preferred_source_classes": list(preferred_source_classes),
        "max_initial_reads": max_initial_reads,
        "max_follow_up_reads": max_follow_up_reads,
        "min_independent_sources": min_independent_sources,
        "min_strong_sources": min_strong_sources,
        "max_unresolved_critical_unknowns": max_unresolved_critical_unknowns,
        "minimum_confidence_band": str(minimum_confidence_band),
        "minimum_marginal_information_value": (
            minimum_marginal_information_value
        ),
        "action_urgency": action_urgency,
        "revisit_trigger": revisit_trigger,
        "require_fresh_dossier": require_fresh_dossier,
    }
    return ResearchPlan(
        plan_id=plan_id or _stable_id("research-plan", material),
        objective=material["objective"],
        knowns=tuple(knowns),
        decision_critical_unknowns=tuple(decision_critical_unknowns),
        research_questions=tuple(research_questions),
        search_targets=tuple(search_targets),
        preferred_source_classes=tuple(preferred_source_classes),
        max_initial_reads=max_initial_reads,
        max_follow_up_reads=max_follow_up_reads,
        min_independent_sources=min_independent_sources,
        min_strong_sources=min_strong_sources,
        max_unresolved_critical_unknowns=max_unresolved_critical_unknowns,
        minimum_confidence_band=ConfidenceBand(minimum_confidence_band),
        minimum_marginal_information_value=minimum_marginal_information_value,
        action_urgency=action_urgency,
        revisit_trigger=revisit_trigger,
        require_fresh_dossier=require_fresh_dossier,
    )


@dataclass(frozen=True, slots=True)
class ResearchStopAssessment:
    disposition: ResearchDisposition
    reasons: tuple[str, ...]
    reads_used: int
    read_budget: int
    unresolved_critical_unknowns: tuple[str, ...] = ()
    marginal_information_value: float = 0.0
    remaining_read_budget: int = 0
    action_urgency: str = "NORMAL"
    revisit_trigger: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "reasons": list(self.reasons),
            "reads_used": self.reads_used,
            "read_budget": self.read_budget,
            "unresolved_critical_unknowns": list(
                self.unresolved_critical_unknowns
            ),
            "marginal_information_value": self.marginal_information_value,
            "remaining_read_budget": self.remaining_read_budget,
            "action_urgency": self.action_urgency,
            "revisit_trigger": self.revisit_trigger,
            "authority": ADVISORY_ONLY,
        }


def _provenance(dossier: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = dossier.get("source_provenance")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    rows = [dict(item) for item in raw if isinstance(item, Mapping)]
    for row in rows:
        if str(row.get("authority") or "").upper() != DATA_ONLY:
            raise ResearchDecisionError(
                "retrieved source provenance must remain DATA_ONLY"
            )
    return rows


def _validate_dossier(dossier: Mapping[str, Any]) -> list[dict[str, Any]]:
    if dossier.get("contract") != DOSSIER_CONTRACT:
        raise ResearchDecisionError(
            f"decision engine requires {DOSSIER_CONTRACT}"
        )
    if dossier.get("scoring_version") != DOSSIER_SCORING_VERSION:
        raise ResearchDecisionError(
            f"decision engine requires dossier scoring {DOSSIER_SCORING_VERSION}"
        )
    if str(dossier.get("authority") or "").upper() != DATA_ONLY:
        raise ResearchDecisionError("research dossier authority must be DATA_ONLY")
    return _provenance(dossier)


def _freshness(
    dossier: Mapping[str, Any],
    *,
    now: datetime | str | None,
) -> tuple[bool, dict[str, Any]]:
    raw = dossier.get("freshness")
    value = dict(raw) if isinstance(raw, Mapping) else {}
    status = str(value.get("status") or "UNKNOWN").upper()
    expiry = str(value.get("fresh_until") or "").strip()
    stale_ids = tuple(str(item) for item in value.get("stale_evidence_ids") or ())
    unknown_ids = tuple(
        str(item)
        for item in value.get("unknown_freshness_evidence_ids") or ()
    )
    fresh = (
        status == "FRESH"
        and not stale_ids
        and not unknown_ids
        and bool(expiry)
    )
    if fresh:
        try:
            fresh = _utc(expiry) > _utc(now)
        except ResearchDecisionError:
            fresh = False
    return fresh, {
        "status": status,
        "evaluated_at": value.get("evaluated_at"),
        "fresh_until": expiry or None,
        "stale_evidence_ids": list(stale_ids),
        "unknown_freshness_evidence_ids": list(unknown_ids),
    }


def _unknown_identity(value: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(value.get("unknown_id") or "").strip().lower(),
        " ".join(str(value.get("question") or "").lower().split()),
    )


def _critical_unknowns(
    plan: ResearchPlan,
    dossier: Mapping[str, Any],
) -> tuple[str, ...]:
    requested = tuple(
        " ".join(item.lower().split())
        for item in plan.decision_critical_unknowns
    )
    unresolved: list[str] = []
    raw = dossier.get("unresolved_unknowns")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        unknown_id, question = _unknown_identity(item)
        if str(item.get("status") or "UNRESOLVED").upper() == "RESOLVED":
            continue
        if any(
            target in {unknown_id, question}
            or (target and question and (target in question or question in target))
            for target in requested
        ):
            unresolved.append(unknown_id or question)
    return tuple(sorted(set(unresolved)))


def _dossier_confidence(dossier: Mapping[str, Any]) -> ConfidenceBand:
    raw = dossier.get("confidence")
    confidence = raw.get("score") if isinstance(raw, Mapping) else None
    return _confidence_band(confidence)


def _source_groups(
    sources: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    rank = {
        "PRIMARY_OFFICIAL": 5,
        "UPSTREAM_TECHNICAL": 4,
        "HIGH_QUALITY_SECONDARY": 3,
        "COMMUNITY_EXPERIENCE": 2,
        "WEAK_UNKNOWN": 1,
    }
    groups: dict[str, str] = {}
    for row in sources:
        group = str(
            row.get("source_group")
            or row.get("independent_source")
            or row.get("source_ref")
            or row.get("evidence_id")
        )
        source_class = _normalize_source_class(row.get("source_class"))
        current = groups.get(group)
        if current is None or rank[source_class] > rank[current]:
            groups[group] = source_class
    return groups


def _marginal_information_value(dossier: Mapping[str, Any]) -> float:
    raw = dossier.get("unresolved_unknowns")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return 0.0
    values: list[float] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "UNRESOLVED").upper() == "RESOLVED":
            continue
        try:
            value = float(item.get("expected_information_value", 0.0))
        except (TypeError, ValueError, OverflowError):
            value = 0.0
        if math.isfinite(value):
            values.append(max(0.0, min(1.0, value)))
    return round(max(values, default=0.0), 4)


def assess_research_plan(
    plan: ResearchPlan | Mapping[str, Any],
    dossier: Mapping[str, Any],
    *,
    now: datetime | str | None = None,
) -> ResearchStopAssessment:
    """Decide whether a bounded plan should continue or stop."""

    current = plan if isinstance(plan, ResearchPlan) else ResearchPlan.from_mapping(plan)
    sources = _validate_dossier(dossier)
    groups = _source_groups(sources)
    strong = sum(
        source_class in STRONG_SOURCE_CLASSES
        for source_class in groups.values()
    )
    budget = dossier.get("budget")
    budget = budget if isinstance(budget, Mapping) else {}
    try:
        reads_used = max(0, int(budget.get("source_reads_used", len(sources))))
    except (TypeError, ValueError):
        reads_used = len(sources)
    unresolved = _critical_unknowns(current, dossier)
    marginal_information_value = _marginal_information_value(dossier)
    fresh, _ = _freshness(dossier, now=now)
    confidence = _dossier_confidence(dossier)
    conclusion = dossier.get("final_research_conclusion")
    conclusion = conclusion if isinstance(conclusion, Mapping) else {}
    verdict = str(conclusion.get("verdict") or "INSUFFICIENT_EVIDENCE").upper()

    gaps: list[str] = []
    if len(groups) < current.min_independent_sources:
        gaps.append("independent-source floor not met")
    if strong < current.min_strong_sources:
        gaps.append("strong-source floor not met")
    if len(unresolved) > current.max_unresolved_critical_unknowns:
        gaps.append("decision-critical unknowns remain")
    if current.require_fresh_dossier and not fresh:
        gaps.append("dossier is not fresh")
    if (
        _CONFIDENCE_ORDER[confidence]
        < _CONFIDENCE_ORDER[current.minimum_confidence_band]
    ):
        gaps.append("confidence band is below the stopping floor")
    if verdict == "INSUFFICIENT_EVIDENCE":
        gaps.append("dossier conclusion is insufficient evidence")

    remaining_reads = max(0, current.max_read_budget - reads_used)
    further_research_worthwhile = (
        remaining_reads > 0
        and marginal_information_value
        >= current.minimum_marginal_information_value
        and current.action_urgency != "CRITICAL"
    )
    if not gaps:
        disposition = ResearchDisposition.STOP_ENOUGH_EVIDENCE
        reasons = ("all bounded stopping criteria are satisfied",)
    elif further_research_worthwhile:
        disposition = ResearchDisposition.CONTINUE
        reasons = tuple(gaps)
    else:
        disposition = ResearchDisposition.STOP_INSUFFICIENT_EVIDENCE
        stop_reasons = list(gaps)
        if remaining_reads == 0:
            stop_reasons.append("bounded read budget is exhausted")
        elif (
            marginal_information_value
            < current.minimum_marginal_information_value
        ):
            stop_reasons.append(
                "further research has lower expected information value than "
                "the configured stopping floor"
            )
        if current.action_urgency == "CRITICAL":
            stop_reasons.append(
                "critical action urgency does not permit more research"
            )
        reasons = tuple(stop_reasons)
    return ResearchStopAssessment(
        disposition=disposition,
        reasons=reasons,
        reads_used=reads_used,
        read_budget=current.max_read_budget,
        unresolved_critical_unknowns=unresolved,
        marginal_information_value=marginal_information_value,
        remaining_read_budget=remaining_reads,
        action_urgency=current.action_urgency,
        revisit_trigger=current.revisit_trigger,
    )


@dataclass(frozen=True, slots=True)
class DecisionOption:
    """One serious option evaluated on the complete V13 dimension set."""

    CONTRACT: ClassVar[str] = DECISION_OPTION_CONTRACT

    option_id: str
    title: str
    summary: str
    dimensions: Mapping[str, QualitativeAssessment | str]
    evidence_refs: tuple[str, ...] = ()
    dimension_rationales: Mapping[str, str] = field(default_factory=dict)
    critical_dimensions: tuple[str, ...] = DEFAULT_CRITICAL_DIMENSIONS
    implications: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    serious: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "option_id", _identity(self.option_id, "option_id"))
        object.__setattr__(self, "title", _text(self.title, "title", limit=300))
        object.__setattr__(
            self, "summary", _text(self.summary, "summary", limit=1000)
        )
        supplied = dict(self.dimensions)
        missing = [name for name in DECISION_DIMENSIONS if name not in supplied]
        extras = sorted(set(supplied) - set(DECISION_DIMENSIONS))
        if missing or extras:
            raise ResearchDecisionError(
                f"option dimensions mismatch; missing={missing}, extra={extras}"
            )
        normalized = {
            name: (
                value
                if isinstance((value := supplied[name]), QualitativeAssessment)
                else QualitativeAssessment(str(value))
            )
            for name in DECISION_DIMENSIONS
        }
        object.__setattr__(
            self, "dimensions", MappingProxyType(normalized)
        )
        rationales = {
            str(name): _optional_text(value, limit=600)
            for name, value in dict(self.dimension_rationales).items()
            if str(name) in DECISION_DIMENSIONS and str(value or "").strip()
        }
        object.__setattr__(
            self, "dimension_rationales", MappingProxyType(rationales)
        )
        critical = tuple(dict.fromkeys(str(item) for item in self.critical_dimensions))
        if any(item not in DECISION_DIMENSIONS for item in critical):
            raise ResearchDecisionError(
                "critical_dimensions must be known decision dimensions"
            )
        object.__setattr__(self, "critical_dimensions", critical)
        object.__setattr__(
            self,
            "evidence_refs",
            _strings(self.evidence_refs, "evidence_refs", limit=32, item_limit=1000),
        )
        object.__setattr__(
            self, "implications", _strings(self.implications, "implications")
        )
        object.__setattr__(
            self, "constraints", _strings(self.constraints, "constraints")
        )
        if not isinstance(self.serious, bool):
            raise ResearchDecisionError("serious must be boolean")

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.CONTRACT,
            "option_id": self.option_id,
            "title": self.title,
            "summary": self.summary,
            "dimensions": {
                name: self.dimensions[name].value
                for name in DECISION_DIMENSIONS
            },
            "dimension_rationales": dict(self.dimension_rationales),
            "critical_dimensions": list(self.critical_dimensions),
            "evidence_refs": list(self.evidence_refs),
            "implications": list(self.implications),
            "constraints": list(self.constraints),
            "serious": self.serious,
            "evidence_authority": DATA_ONLY,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DecisionOption":
        if value.get("contract") not in {None, cls.CONTRACT}:
            raise ResearchDecisionError("unsupported decision-option contract")
        return cls(
            option_id=str(value.get("option_id") or ""),
            title=str(value.get("title") or ""),
            summary=str(value.get("summary") or ""),
            dimensions=dict(value.get("dimensions") or {}),
            evidence_refs=tuple(value.get("evidence_refs") or ()),
            dimension_rationales=dict(
                value.get("dimension_rationales") or {}
            ),
            critical_dimensions=tuple(
                value.get("critical_dimensions") or DEFAULT_CRITICAL_DIMENSIONS
            ),
            implications=tuple(value.get("implications") or ()),
            constraints=tuple(value.get("constraints") or ()),
            serious=bool(value.get("serious", True)),
        )


def _owner_refs(
    values: Sequence[Mapping[str, Any]] | None,
) -> tuple[Mapping[str, Any], ...]:
    if len(values or ()) > 24:
        raise ResearchDecisionError("owner-context refs exceed 24 items")
    rows: list[Mapping[str, Any]] = []
    for value in values or ():
        if not isinstance(value, Mapping):
            raise ResearchDecisionError("owner-context refs must be mappings")
        provenance = value.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        source_ref = _text(
            value.get("source_ref") or provenance.get("source_ref"),
            "source_ref",
            limit=1000,
        )
        source_hash = str(
            value.get("source_hash")
            or value.get("content_hash")
            or provenance.get("content_hash")
            or ""
        ).strip().lower()
        if source_hash and (
            len(source_hash) != 64
            or any(char not in "0123456789abcdef" for char in source_hash)
        ):
            raise ResearchDecisionError(
                "owner-context source_hash must be sha256 hex"
            )
        row: dict[str, Any] = {"source_ref": source_ref}
        if source_hash:
            row["source_hash"] = source_hash
        for name in ("context_id", "kind", "state", "freshness"):
            if value.get(name) is not None:
                row[name] = _optional_text(value.get(name), limit=300)
        authority = value.get("context_authority") or value.get("authority")
        if isinstance(authority, str) and authority.strip():
            row["context_authority"] = _optional_text(authority, limit=160)
        provenance_authority = (
            value.get("provenance_authority")
            or provenance.get("authority")
        )
        if (
            isinstance(provenance_authority, str)
            and provenance_authority.strip()
        ):
            row["provenance_authority"] = _optional_text(
                provenance_authority, limit=160,
            )
        rows.append(MappingProxyType(row))
    return tuple(sorted(rows, key=lambda row: (
        row["source_ref"], row.get("source_hash", "")
    )))


@dataclass(frozen=True, slots=True)
class DecisionRecommendation:
    """A reviewable advisory conclusion that cannot authorize execution."""

    CONTRACT: ClassVar[str] = DECISION_RECOMMENDATION_CONTRACT

    recommendation_id: str
    objective: str
    status: RecommendationStatus
    selected_option_id: str | None
    options: tuple[DecisionOption, ...]
    ranked_option_ids: tuple[str, ...]
    serious_alternatives: tuple[Mapping[str, Any], ...]
    dossier_revision: str
    dossier_freshness: Mapping[str, Any]
    owner_context_advisory_refs: tuple[Mapping[str, Any], ...]
    implications: tuple[str, ...]
    constraints: tuple[str, ...]
    expires_at: str
    generated_at: str
    rationale: tuple[str, ...]
    confidence_band: ConfidenceBand
    source_quality_summary: Mapping[str, Any]
    initial_apparent_winner_id: str | None = None
    changed_mind: bool = False
    evidence_authority: str = DATA_ONLY
    authority: str = ADVISORY_ONLY

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "recommendation_id",
            _identity(self.recommendation_id, "recommendation_id"),
        )
        object.__setattr__(
            self, "objective", _text(self.objective, "objective", limit=1000)
        )
        if not isinstance(self.status, RecommendationStatus):
            object.__setattr__(
                self, "status", RecommendationStatus(self.status)
            )
        options = tuple(
            item if isinstance(item, DecisionOption)
            else DecisionOption.from_mapping(item)
            for item in self.options
        )
        option_ids = [item.option_id for item in options]
        if len(option_ids) != len(set(option_ids)):
            raise ResearchDecisionError("option ids must be unique")
        object.__setattr__(self, "options", options)
        ranked = tuple(str(item) for item in self.ranked_option_ids)
        if set(ranked) - set(option_ids):
            raise ResearchDecisionError("ranked options must exist")
        object.__setattr__(self, "ranked_option_ids", ranked)
        selected = (
            _identity(self.selected_option_id, "selected_option_id")
            if self.selected_option_id else None
        )
        if self.status is RecommendationStatus.RECOMMEND:
            if selected not in option_ids:
                raise ResearchDecisionError(
                    "RECOMMEND requires a selected existing option"
                )
        elif selected is not None:
            raise ResearchDecisionError(
                "INSUFFICIENT_EVIDENCE cannot select an option"
            )
        object.__setattr__(self, "selected_option_id", selected)
        initial = (
            _identity(
                self.initial_apparent_winner_id,
                "initial_apparent_winner_id",
            )
            if self.initial_apparent_winner_id else None
        )
        if initial is not None and initial not in option_ids:
            raise ResearchDecisionError(
                "initial apparent winner must exist in options"
            )
        object.__setattr__(self, "initial_apparent_winner_id", initial)
        object.__setattr__(
            self,
            "serious_alternatives",
            tuple(MappingProxyType(dict(item)) for item in self.serious_alternatives),
        )
        revision = str(self.dossier_revision or "").strip()
        if not revision:
            raise ResearchDecisionError("dossier_revision is required")
        object.__setattr__(self, "dossier_revision", revision)
        object.__setattr__(
            self, "dossier_freshness", _mapping(self.dossier_freshness)
        )
        object.__setattr__(
            self,
            "owner_context_advisory_refs",
            _owner_refs(self.owner_context_advisory_refs),
        )
        object.__setattr__(
            self, "implications", _strings(self.implications, "implications")
        )
        object.__setattr__(
            self, "constraints", _strings(self.constraints, "constraints")
        )
        object.__setattr__(self, "expires_at", _iso(self.expires_at))
        object.__setattr__(self, "generated_at", _iso(self.generated_at))
        object.__setattr__(
            self,
            "rationale",
            _strings(self.rationale, "rationale", limit=64, item_limit=1000),
        )
        if not self.rationale:
            raise ResearchDecisionError("recommendation rationale is required")
        if not isinstance(self.confidence_band, ConfidenceBand):
            object.__setattr__(
                self,
                "confidence_band",
                ConfidenceBand(self.confidence_band),
            )
        object.__setattr__(
            self,
            "source_quality_summary",
            _mapping(self.source_quality_summary),
        )
        if self.evidence_authority != DATA_ONLY:
            raise ResearchDecisionError(
                "recommendation evidence authority must be DATA_ONLY"
            )
        if self.authority != ADVISORY_ONLY:
            raise ResearchDecisionError(
                "recommendation authority must be ADVISORY_ONLY"
            )
        if not isinstance(self.changed_mind, bool):
            raise ResearchDecisionError("changed_mind must be boolean")

    def option(self, option_id: str) -> DecisionOption:
        match = next(
            (item for item in self.options if item.option_id == option_id),
            None,
        )
        if match is None:
            raise ResearchDecisionError(f"unknown option {option_id}")
        return match

    @property
    def recommendation_hash(self) -> str:
        return _sha(self.as_dict())

    def is_fresh(self, *, now: datetime | str | None = None) -> bool:
        if self.status is not RecommendationStatus.RECOMMEND:
            return False
        try:
            if _utc(self.expires_at) <= _utc(now):
                return False
        except ResearchDecisionError:
            return False
        status = str(self.dossier_freshness.get("status") or "").upper()
        stale = self.dossier_freshness.get("stale_evidence_ids") or ()
        fresh_until = self.dossier_freshness.get("fresh_until")
        if status != "FRESH" or stale or not fresh_until:
            return False
        try:
            return _utc(str(fresh_until)) > _utc(now)
        except ResearchDecisionError:
            return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.CONTRACT,
            "recommendation_id": self.recommendation_id,
            "objective": self.objective,
            "status": self.status.value,
            "selected_option_id": self.selected_option_id,
            "initial_apparent_winner_id": self.initial_apparent_winner_id,
            "changed_mind": self.changed_mind,
            "options": [item.as_dict() for item in self.options],
            "ranked_option_ids": list(self.ranked_option_ids),
            "serious_alternatives": _plain(self.serious_alternatives),
            "dossier_revision": self.dossier_revision,
            "dossier_freshness": _plain(self.dossier_freshness),
            "owner_context_advisory_refs": _plain(
                self.owner_context_advisory_refs
            ),
            "implications": list(self.implications),
            "constraints": list(self.constraints),
            "expires_at": self.expires_at,
            "generated_at": self.generated_at,
            "rationale": list(self.rationale),
            "confidence_band": self.confidence_band.value,
            "source_quality_summary": _plain(self.source_quality_summary),
            "evidence_authority": self.evidence_authority,
            "authority": self.authority,
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "DecisionRecommendation":
        if value.get("contract") != cls.CONTRACT:
            raise ResearchDecisionError(
                "unsupported decision-recommendation contract"
            )
        return cls(
            recommendation_id=str(value.get("recommendation_id") or ""),
            objective=str(value.get("objective") or ""),
            status=RecommendationStatus(
                value.get("status") or RecommendationStatus.INSUFFICIENT_EVIDENCE
            ),
            selected_option_id=value.get("selected_option_id"),
            initial_apparent_winner_id=value.get(
                "initial_apparent_winner_id"
            ),
            changed_mind=bool(value.get("changed_mind", False)),
            options=tuple(
                DecisionOption.from_mapping(item)
                for item in value.get("options") or ()
            ),
            ranked_option_ids=tuple(value.get("ranked_option_ids") or ()),
            serious_alternatives=tuple(
                dict(item)
                for item in value.get("serious_alternatives") or ()
            ),
            dossier_revision=str(value.get("dossier_revision") or ""),
            dossier_freshness=dict(value.get("dossier_freshness") or {}),
            owner_context_advisory_refs=tuple(
                dict(item)
                for item in value.get("owner_context_advisory_refs") or ()
            ),
            implications=tuple(value.get("implications") or ()),
            constraints=tuple(value.get("constraints") or ()),
            expires_at=str(value.get("expires_at") or ""),
            generated_at=str(value.get("generated_at") or ""),
            rationale=tuple(value.get("rationale") or ()),
            confidence_band=ConfidenceBand(
                value.get("confidence_band") or "LOW"
            ),
            source_quality_summary=dict(
                value.get("source_quality_summary") or {}
            ),
            evidence_authority=str(
                value.get("evidence_authority") or DATA_ONLY
            ),
            authority=str(value.get("authority") or ADVISORY_ONLY),
        )


def _option_score(option: DecisionOption) -> int:
    return sum(
        _ASSESSMENT_VALUE[option.dimensions[name]]
        * _DIMENSION_WEIGHT.get(name, 1)
        for name in DECISION_DIMENSIONS
    )


def _evidence_score(option: DecisionOption) -> int:
    return sum(
        _ASSESSMENT_VALUE[option.dimensions[name]]
        for name in ("evidence_strength", "confidence", "quality")
    )


def _critical_gaps(
    option: DecisionOption,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    blockers = tuple(
        name for name in option.critical_dimensions
        if option.dimensions[name] is QualitativeAssessment.BLOCKER
    )
    unknowns = tuple(
        name for name in option.critical_dimensions
        if option.dimensions[name] is QualitativeAssessment.UNKNOWN
    )
    return blockers, unknowns


def _source_quality_summary(
    dossier: Mapping[str, Any],
) -> dict[str, Any]:
    rows = _provenance(dossier)
    strongest_by_group: dict[str, str] = {}
    class_rank = {
        "PRIMARY_OFFICIAL": 5,
        "UPSTREAM_TECHNICAL": 4,
        "HIGH_QUALITY_SECONDARY": 3,
        "COMMUNITY_EXPERIENCE": 2,
        "WEAK_UNKNOWN": 1,
    }
    domains: set[str] = set()
    for row in rows:
        source_class = _normalize_source_class(row.get("source_class"))
        group = str(
            row.get("source_group")
            or row.get("independent_source")
            or row.get("source_ref")
            or row.get("evidence_id")
        )
        current = strongest_by_group.get(group)
        if current is None or class_rank[source_class] > class_rank[current]:
            strongest_by_group[group] = source_class
        parsed = urlsplit(str(row.get("source_ref") or ""))
        if parsed.hostname:
            domains.add(parsed.hostname.lower())
        elif row.get("independent_source"):
            domains.add(str(row["independent_source"]).lower())
    counts = {
        name: sum(value == name for value in strongest_by_group.values())
        for name in sorted(SOURCE_CLASSES)
    }
    strongest_class = max(
        strongest_by_group.values(),
        key=lambda item: class_rank[item],
        default="WEAK_UNKNOWN",
    )
    return {
        "independent_source_groups": len(strongest_by_group),
        "strong_source_groups": sum(
            value in STRONG_SOURCE_CLASSES
            for value in strongest_by_group.values()
        ),
        "by_class": counts,
        "strongest_source_class": strongest_class,
        "source_domains": sorted(domains),
        "method": "strongest-class-per-independent-source-group",
        "contradictions": len(dossier.get("contradictions") or ()),
        "evidence_authority": DATA_ONLY,
    }


def _option_evidence_summary(
    option: DecisionOption,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [evidence_by_id[evidence_id] for evidence_id in option.evidence_refs]
    classes = tuple(
        _normalize_source_class(row.get("source_class"))
        for row in rows
    )
    strong_ids = tuple(
        str(row.get("evidence_id") or "")
        for row, source_class in zip(rows, classes, strict=True)
        if source_class in STRONG_SOURCE_CLASSES
    )
    return {
        "evidence_refs": list(option.evidence_refs),
        "source_classes": list(classes),
        "strong_evidence_refs": list(strong_ids),
        "strong_source_supported": bool(strong_ids),
    }


def _high_value_unresolved(dossier: Mapping[str, Any]) -> tuple[str, ...]:
    raw = dossier.get("unresolved_unknowns")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    result: list[str] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "UNRESOLVED").upper() == "RESOLVED":
            continue
        try:
            value = float(item.get("expected_information_value", 0.0))
        except (TypeError, ValueError, OverflowError):
            value = 0.0
        if value >= 0.85 or bool(item.get("decision_critical")):
            result.append(
                str(item.get("unknown_id") or item.get("question") or "unknown")
            )
    return tuple(sorted(set(result)))


def _loss_reasons(
    option: DecisionOption,
    winner: DecisionOption | None,
) -> tuple[str, ...]:
    blockers, unknowns = _critical_gaps(option)
    reasons: list[str] = []
    if blockers:
        reasons.append("critical blocker in " + ", ".join(blockers))
    if unknowns:
        reasons.append("critical unknown in " + ", ".join(unknowns))
    if winner is not None and winner.option_id != option.option_id:
        weaker = [
            name for name in DECISION_DIMENSIONS
            if _ASSESSMENT_VALUE[option.dimensions[name]]
            < _ASSESSMENT_VALUE[winner.dimensions[name]]
        ]
        if weaker:
            reasons.append("weaker than selected option on " + ", ".join(weaker))
    if not reasons:
        reasons.append("did not establish a decisive advantage")
    return tuple(reasons)


def compare_decision_options(
    *,
    objective: str,
    options: Sequence[DecisionOption | Mapping[str, Any]],
    dossier: Mapping[str, Any],
    owner_context_advisory_refs: Sequence[Mapping[str, Any]] = (),
    initial_apparent_winner_id: str | None = None,
    implications: Sequence[str] = (),
    constraints: Sequence[str] = (),
    generated_at: datetime | str | None = None,
    expires_at: datetime | str | None = None,
    recommendation_id: str | None = None,
) -> DecisionRecommendation:
    """Compare serious options and return a non-authoritative recommendation."""

    objective_text = _text(objective, "objective", limit=1000)
    normalized = tuple(
        item if isinstance(item, DecisionOption)
        else DecisionOption.from_mapping(item)
        for item in options
    )
    option_ids = [item.option_id for item in normalized]
    if len(option_ids) != len(set(option_ids)):
        raise ResearchDecisionError("option ids must be unique")
    if initial_apparent_winner_id and initial_apparent_winner_id not in option_ids:
        raise ResearchDecisionError("initial apparent winner must exist")
    sources = _validate_dossier(dossier)
    evidence_ids = {
        str(row.get("evidence_id") or "")
        for row in sources
        if str(row.get("evidence_id") or "")
    }
    evidence_by_id = {
        str(row["evidence_id"]): row
        for row in sources
        if str(row.get("evidence_id") or "")
    }
    for option in normalized:
        if option.serious and not option.evidence_refs:
            raise ResearchDecisionError(
                f"serious option {option.option_id} requires evidence refs"
            )
        missing_refs = sorted(set(option.evidence_refs) - evidence_ids)
        if missing_refs:
            raise ResearchDecisionError(
                f"option {option.option_id} references missing dossier "
                f"evidence: {missing_refs}"
            )
    option_evidence = {
        option.option_id: _option_evidence_summary(option, evidence_by_id)
        for option in normalized
    }

    generated = _iso(
        generated_at
        or (
            (dossier.get("freshness") or {}).get("evaluated_at")
            if isinstance(dossier.get("freshness"), Mapping)
            else None
        )
    )
    fresh, freshness = _freshness(dossier, now=generated)
    dossier_expiry = freshness.get("fresh_until")
    expiry = _iso(expires_at or dossier_expiry or generated)
    if dossier_expiry and _utc(expiry) > _utc(str(dossier_expiry)):
        expiry = _iso(str(dossier_expiry))

    serious = tuple(item for item in normalized if item.serious)
    quality = _source_quality_summary(dossier)
    confidence = _dossier_confidence(dossier)
    critical_unknowns = _high_value_unresolved(dossier)
    conclusion = dossier.get("final_research_conclusion")
    conclusion = conclusion if isinstance(conclusion, Mapping) else {}
    verdict = str(conclusion.get("verdict") or "INSUFFICIENT_EVIDENCE").upper()
    dossier_revision = str(
        dossier.get("dossier_revision")
        or dossier.get("evidence_revision_identity")
        or ""
    ).strip()
    if not dossier_revision:
        raise ResearchDecisionError("dossier revision is required")

    ranked = sorted(
        serious,
        key=lambda item: (
            -_option_score(item),
            -_evidence_score(item),
            item.option_id,
        ),
    )
    eligible = [
        item for item in ranked
        if (
            not any(_critical_gaps(item))
            and option_evidence[item.option_id]["strong_source_supported"]
        )
    ]
    selected: DecisionOption | None = None
    insufficiency: list[str] = []
    if len(serious) < 2:
        insufficiency.append("at least two serious options are required")
    if not fresh:
        insufficiency.append("dossier is stale or freshness is unknown")
    if confidence is ConfidenceBand.LOW:
        insufficiency.append("dossier confidence is LOW")
    if int(quality["strong_source_groups"]) < 1:
        insufficiency.append("no strong independent source group supports comparison")
    if critical_unknowns:
        insufficiency.append(
            "decision-critical evidence remains unknown: "
            + ", ".join(critical_unknowns)
        )
    if verdict == "INSUFFICIENT_EVIDENCE":
        insufficiency.append("dossier conclusion is insufficient evidence")
    if not eligible:
        insufficiency.append(
            "every serious option has a critical blocker, critical unknown, "
            "or lacks a strong cited source"
        )
    if eligible:
        selected = eligible[0]
        if (
            len(eligible) > 1
            and _option_score(eligible[0]) == _option_score(eligible[1])
            and _evidence_score(eligible[0]) == _evidence_score(eligible[1])
        ):
            insufficiency.append("top options remain qualitatively tied")
            selected = None

    status = (
        RecommendationStatus.INSUFFICIENT_EVIDENCE
        if insufficiency else RecommendationStatus.RECOMMEND
    )
    if status is RecommendationStatus.INSUFFICIENT_EVIDENCE:
        selected = None

    changed_mind = False
    if selected is not None and initial_apparent_winner_id:
        initial = next(
            item for item in normalized
            if item.option_id == initial_apparent_winner_id
        )
        changed_mind = (
            initial.option_id != selected.option_id
            and _evidence_score(selected) > _evidence_score(initial)
        )

    alternatives: tuple[dict[str, Any], ...] = tuple()
    alternative_rows: list[dict[str, Any]] = []
    for item in normalized:
        if selected is not None and item.option_id == selected.option_id:
            continue
        loss_reasons = list(_loss_reasons(item, selected))
        if not option_evidence[item.option_id]["strong_source_supported"]:
            loss_reasons.append("no strong cited source supports this option")
        alternative_rows.append({
            "option_id": item.option_id,
            "title": item.title,
            "serious": item.serious,
            "critical_blockers": list(_critical_gaps(item)[0]),
            "critical_unknowns": list(_critical_gaps(item)[1]),
            "why_it_lost": loss_reasons,
            "evidence": option_evidence[item.option_id],
            "dimensions": {
                name: item.dimensions[name].value
                for name in DECISION_DIMENSIONS
            },
        })
    alternatives = tuple(alternative_rows)

    rationale: list[str] = []
    if insufficiency:
        rationale.extend(insufficiency)
    elif selected is not None:
        strengths = [
            name for name in DECISION_DIMENSIONS
            if selected.dimensions[name] in {
                QualitativeAssessment.STRONG_SUPPORT,
                QualitativeAssessment.SUPPORT,
            }
        ]
        rationale.append(
            f"{selected.title} is the strongest eligible option"
            + (f" on {', '.join(strengths)}" if strengths else "")
        )
        for item in alternatives:
            rationale.append(
                f"{item['title']} lost because "
                + "; ".join(item["why_it_lost"])
            )
        if changed_mind:
            rationale.append(
                "stronger evidence reversed the initial apparent winner "
                f"{initial_apparent_winner_id}"
            )

    material = {
        "objective": objective_text,
        "options": [item.as_dict() for item in normalized],
        "dossier_revision": dossier_revision,
        "generated_at": generated,
    }
    return DecisionRecommendation(
        recommendation_id=(
            recommendation_id
            or _stable_id("decision-recommendation", material)
        ),
        objective=objective_text,
        status=status,
        selected_option_id=selected.option_id if selected else None,
        initial_apparent_winner_id=initial_apparent_winner_id,
        changed_mind=changed_mind,
        options=normalized,
        ranked_option_ids=tuple(item.option_id for item in ranked),
        serious_alternatives=alternatives,
        dossier_revision=dossier_revision,
        dossier_freshness=freshness,
        owner_context_advisory_refs=_owner_refs(
            owner_context_advisory_refs
        ),
        implications=tuple(implications) + (
            selected.implications if selected is not None else ()
        ),
        constraints=tuple(constraints) + (
            selected.constraints if selected is not None else ()
        ),
        expires_at=expiry,
        generated_at=generated,
        rationale=tuple(rationale),
        confidence_band=confidence,
        source_quality_summary=quality,
    )


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """Reference-only receipt for candidate ingest and optional A2 judgment."""

    CONTRACT: ClassVar[str] = DECISION_RESULT_CONTRACT

    result_id: str
    recommendation_id: str
    recommendation_hash: str
    candidate_id: str
    candidate_created: bool
    candidate_fingerprint: str
    evaluated_by_executive: bool
    consumed_by_executive: bool
    executive_decision_id: str | None = None
    executive_kind: str | None = None
    executive_candidate_id: str | None = None
    objective_id: None = None
    mission_id: None = None
    authority: str = "EXECUTIVE_REFERENCE_ONLY"

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_id", _identity(self.result_id, "result_id"))
        object.__setattr__(
            self,
            "recommendation_id",
            _identity(self.recommendation_id, "recommendation_id"),
        )
        object.__setattr__(
            self, "candidate_id", _identity(self.candidate_id, "candidate_id")
        )
        digest = str(self.recommendation_hash or "").lower()
        if len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ResearchDecisionError(
                "recommendation_hash must be sha256 hex"
            )
        object.__setattr__(self, "recommendation_hash", digest)
        object.__setattr__(
            self,
            "candidate_fingerprint",
            _identity(self.candidate_fingerprint, "candidate_fingerprint"),
        )
        if self.objective_id is not None or self.mission_id is not None:
            raise ResearchDecisionError(
                "advisory bridge cannot create an objective or mission"
            )
        if self.authority != "EXECUTIVE_REFERENCE_ONLY":
            raise ResearchDecisionError(
                "decision result authority must be EXECUTIVE_REFERENCE_ONLY"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.CONTRACT,
            "result_id": self.result_id,
            "recommendation_id": self.recommendation_id,
            "recommendation_hash": self.recommendation_hash,
            "candidate_id": self.candidate_id,
            "candidate_created": self.candidate_created,
            "candidate_fingerprint": self.candidate_fingerprint,
            "evaluated_by_executive": self.evaluated_by_executive,
            "consumed_by_executive": self.consumed_by_executive,
            "executive_decision_id": self.executive_decision_id,
            "executive_kind": self.executive_kind,
            "executive_candidate_id": self.executive_candidate_id,
            "objective_id": None,
            "mission_id": None,
            "authority": self.authority,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DecisionResult":
        if value.get("contract") != cls.CONTRACT:
            raise ResearchDecisionError("unsupported decision-result contract")
        return cls(
            result_id=str(value.get("result_id") or ""),
            recommendation_id=str(value.get("recommendation_id") or ""),
            recommendation_hash=str(value.get("recommendation_hash") or ""),
            candidate_id=str(value.get("candidate_id") or ""),
            candidate_created=bool(value.get("candidate_created")),
            candidate_fingerprint=str(
                value.get("candidate_fingerprint") or ""
            ),
            evaluated_by_executive=bool(
                value.get("evaluated_by_executive")
            ),
            consumed_by_executive=bool(
                value.get("consumed_by_executive")
            ),
            executive_decision_id=value.get("executive_decision_id"),
            executive_kind=value.get("executive_kind"),
            executive_candidate_id=value.get("executive_candidate_id"),
            objective_id=None,
            mission_id=None,
            authority=str(
                value.get("authority") or "EXECUTIVE_REFERENCE_ONLY"
            ),
        )


def record_research_plan(
    database: FoundryDatabase,
    plan: ResearchPlan | Mapping[str, Any],
) -> bool:
    current = plan if isinstance(plan, ResearchPlan) else ResearchPlan.from_mapping(plan)
    return bool(database.append(Event.create(
        "research.plan_recorded",
        current.as_dict(),
        source_type="supervisor",
        source_id="v13-research-decision",
        idempotency_key=f"research.plan_recorded:{current.plan_id}",
    ))[1])


def record_decision_recommendation(
    database: FoundryDatabase,
    recommendation: DecisionRecommendation | Mapping[str, Any],
) -> bool:
    current = (
        recommendation
        if isinstance(recommendation, DecisionRecommendation)
        else DecisionRecommendation.from_mapping(recommendation)
    )
    return bool(database.append(Event.create(
        "research.decision_recommended",
        current.as_dict(),
        source_type="supervisor",
        source_id="v13-research-decision",
        idempotency_key=(
            f"research.decision_recommended:{current.recommendation_id}"
        ),
        occurred_at=current.generated_at,
    ))[1])


def record_decision_result(
    database: FoundryDatabase,
    result: DecisionResult | Mapping[str, Any],
) -> bool:
    current = (
        result if isinstance(result, DecisionResult)
        else DecisionResult.from_mapping(result)
    )
    return bool(database.append(Event.create(
        "research.decision_result_recorded",
        current.as_dict(),
        source_type="supervisor",
        source_id="v13-research-decision-bridge",
        correlation_id=current.recommendation_id,
        idempotency_key=(
            f"research.decision_result_recorded:{current.result_id}"
        ),
    ))[1])


def load_research_plan(
    database: FoundryDatabase,
    plan_id: str,
) -> ResearchPlan | None:
    target = _identity(plan_id, "plan_id")
    for event in reversed(database.events(
        event_types=("research.plan_recorded",)
    )):
        if event.payload.get("plan_id") == target:
            return ResearchPlan.from_mapping(event.payload)
    return None


def load_decision_recommendation(
    database: FoundryDatabase,
    recommendation_id: str,
) -> DecisionRecommendation | None:
    target = _identity(recommendation_id, "recommendation_id")
    for event in reversed(database.events(
        event_types=("research.decision_recommended",)
    )):
        if event.payload.get("recommendation_id") == target:
            return DecisionRecommendation.from_mapping(event.payload)
    return None


def load_decision_result(
    database: FoundryDatabase,
    result_id: str,
) -> DecisionResult | None:
    target = _identity(result_id, "result_id")
    for event in reversed(database.events(
        event_types=("research.decision_result_recorded",)
    )):
        if event.payload.get("result_id") == target:
            return DecisionResult.from_mapping(event.payload)
    return None


def rebuild_research_decision_state(
    database: FoundryDatabase,
) -> dict[str, Any]:
    """Rebuild V13 advisory state exclusively from ledger events."""

    plans: dict[str, dict[str, Any]] = {}
    recommendations: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    for event in database.events(event_types=(
        "research.plan_recorded",
        "research.decision_recommended",
        "research.decision_result_recorded",
        "research.decision_outcome_recorded",
    )):
        payload = dict(event.payload)
        if event.event_type == "research.plan_recorded":
            plans[str(payload["plan_id"])] = payload
        elif event.event_type == "research.decision_recommended":
            recommendations[str(payload["recommendation_id"])] = payload
        elif event.event_type == "research.decision_result_recorded":
            results[str(payload["result_id"])] = payload
        else:
            outcomes[str(payload["outcome_id"])] = payload
    return {
        "plans": {key: plans[key] for key in sorted(plans)},
        "recommendations": {
            key: recommendations[key] for key in sorted(recommendations)
        },
        "results": {key: results[key] for key in sorted(results)},
        "outcomes": {key: outcomes[key] for key in sorted(outcomes)},
        "authority": "EVENT_LEDGER_READ_MODEL",
    }


def record_decision_outcome(
    database: FoundryDatabase,
    *,
    recommendation_id: str,
    actual_outcome: ActualOutcome | str,
    outcome_ref: str,
    observed_at: datetime | str,
    research_cost_reads: int,
    source_domains: Sequence[str] = (),
    source_classes: Sequence[str] = (),
) -> dict[str, Any]:
    """Record one observed calibration outcome without changing authority."""

    recommendation = load_decision_recommendation(
        database, recommendation_id
    )
    if recommendation is None:
        raise ResearchDecisionError(
            f"unknown recommendation {recommendation_id}"
        )
    actual = (
        actual_outcome
        if isinstance(actual_outcome, ActualOutcome)
        else ActualOutcome(actual_outcome)
    )
    ref = _text(outcome_ref, "outcome_ref", limit=1000)
    reads = int(research_cost_reads)
    if not 0 <= reads <= 10_000:
        raise ResearchDecisionError(
            "research_cost_reads must be within [0, 10000]"
        )
    summary_domains = recommendation.source_quality_summary.get(
        "source_domains"
    ) or ()
    summary_classes = (
        recommendation.source_quality_summary.get("by_class") or {}
    )
    domains = _strings(
        source_domains or tuple(summary_domains),
        "source_domains",
        limit=64,
        item_limit=500,
    )
    classes = tuple(sorted({
        _normalize_source_class(item)
        for item in (
            source_classes
            or tuple(
                key for key, count in dict(summary_classes).items()
                if int(count or 0) > 0
            )
        )
    }))
    observed = _iso(observed_at)
    outcome_id = _stable_id("decision-outcome", {
        "recommendation_id": recommendation.recommendation_id,
        "outcome_ref": ref,
    })
    payload = {
        "contract": DECISION_OUTCOME_CONTRACT,
        "outcome_id": outcome_id,
        "recommendation_id": recommendation.recommendation_id,
        "predicted_recommendation": (
            recommendation.selected_option_id
            or RecommendationStatus.INSUFFICIENT_EVIDENCE.value
        ),
        "predicted_confidence_band": recommendation.confidence_band.value,
        "actual_outcome": actual.value,
        "outcome_ref": ref,
        "observed_at": observed,
        "source_domains": list(domains),
        "source_classes": list(classes),
        "research_cost_reads": reads,
        "recommendation_reversal": recommendation.changed_mind,
        "false_confidence_signal": (
            recommendation.status is RecommendationStatus.RECOMMEND
            and recommendation.confidence_band is ConfidenceBand.HIGH
            and actual is ActualOutcome.FAILED
        ),
        "authority": "CALIBRATION_OBSERVATION_ONLY",
        "self_training": False,
    }
    created = database.append(Event.create(
        "research.decision_outcome_recorded",
        payload,
        source_type="supervisor",
        source_id="v13-research-decision",
        idempotency_key=(
            f"research.decision_outcome_recorded:{outcome_id}"
        ),
        occurred_at=observed,
    ))[1]
    return {**payload, "created": bool(created)}


def rebuild_decision_calibration(
    database: FoundryDatabase,
) -> dict[str, Any]:
    """Derive transparent calibration counts from outcome events only."""

    rows = [
        dict(event.payload)
        for event in database.events(
            event_types=("research.decision_outcome_recorded",)
        )
    ]
    outcomes = {item.value: 0 for item in ActualOutcome}
    by_band = {
        item.value: {
            "total": 0,
            "succeeded": 0,
            "partial": 0,
            "failed": 0,
            "unknown": 0,
            "false_confidence_signals": 0,
        }
        for item in ConfidenceBand
    }
    by_source_class: dict[str, dict[str, int]] = {}
    by_source_domain: dict[str, dict[str, int]] = {}

    def observe(target: dict[str, dict[str, int]], key: str, actual: str) -> None:
        bucket = target.setdefault(
            key,
            {"total": 0, "succeeded": 0, "partial": 0, "failed": 0, "unknown": 0},
        )
        bucket["total"] += 1
        bucket[actual.lower()] += 1

    total_reads = 0
    for row in rows:
        actual = str(row.get("actual_outcome") or "UNKNOWN").upper()
        if actual not in outcomes:
            actual = ActualOutcome.UNKNOWN.value
        outcomes[actual] += 1
        band = str(row.get("predicted_confidence_band") or "LOW").upper()
        if band not in by_band:
            band = ConfidenceBand.LOW.value
        by_band[band]["total"] += 1
        by_band[band][actual.lower()] += 1
        by_band[band]["false_confidence_signals"] += int(
            bool(row.get("false_confidence_signal"))
        )
        for source_class in row.get("source_classes") or ():
            observe(by_source_class, str(source_class), actual)
        for domain in row.get("source_domains") or ():
            observe(by_source_domain, str(domain), actual)
        total_reads += max(0, int(row.get("research_cost_reads") or 0))

    decided = outcomes["SUCCEEDED"] + outcomes["PARTIAL"] + outcomes["FAILED"]
    return {
        "contract": CALIBRATION_CONTRACT,
        "observations": len(rows),
        "outcomes": outcomes,
        "by_confidence_band": by_band,
        "source_reliability_by_class": {
            key: by_source_class[key] for key in sorted(by_source_class)
        },
        "source_reliability_by_domain": {
            key: by_source_domain[key] for key in sorted(by_source_domain)
        },
        "research_cost": {
            "total_source_reads": total_reads,
            "average_source_reads": (
                round(total_reads / len(rows), 2) if rows else 0.0
            ),
        },
        "recommendation_reversals": sum(
            bool(row.get("recommendation_reversal")) for row in rows
        ),
        "false_confidence_signals": sum(
            bool(row.get("false_confidence_signal")) for row in rows
        ),
        "observed_success": {
            "numerator": outcomes["SUCCEEDED"],
            "denominator": decided,
            "rate": round(outcomes["SUCCEEDED"] / decided, 4) if decided else None,
        },
        "authority": "READ_ONLY_CALIBRATION",
        "self_training": False,
    }


def _executive_consumed_candidate(
    database: FoundryDatabase,
    *,
    decision_id: str,
    candidate_id: str,
    decision: Any,
) -> bool:
    if getattr(decision, "candidate_id", None) == candidate_id:
        return True
    for row in (
        *tuple(getattr(decision, "rejected", ()) or ()),
        *tuple(getattr(decision, "deferred", ()) or ()),
    ):
        if isinstance(row, Mapping) and row.get("candidate_id") == candidate_id:
            return True
    return any(
        event.payload.get("decision_id") == decision_id
        and event.payload.get("candidate_id") == candidate_id
        for event in database.events(
            event_types=("discovery.candidate_judged",)
        )
    )


def _recommendation_created_candidate(
    database: FoundryDatabase,
    *,
    recommendation_id: str,
    candidate_id: str,
) -> bool:
    created_from = f"observation:research-decision:{recommendation_id}"
    return any(
        event.payload.get("candidate_id") == candidate_id
        and event.payload.get("created_from") == created_from
        for event in database.events(
            event_types=("discovery.candidate_generated",)
        )
    )


def bridge_recommendation_to_executive(
    database: FoundryDatabase,
    recommendation: DecisionRecommendation | Mapping[str, Any],
    *,
    observation_cycle_id: str,
    target_project: str,
    trusted_requirements: Mapping[str, Any],
    now: datetime | str | None = None,
    evaluate_a2: bool = False,
    executive_service: Any | None = None,
    resource_id: str | None = None,
    loop_id: str | None = None,
    governor: Any | None = None,
) -> DecisionResult:
    """Submit one advisory recommendation to canonical A2 without admission."""

    current = (
        recommendation
        if isinstance(recommendation, DecisionRecommendation)
        else DecisionRecommendation.from_mapping(recommendation)
    )
    if current.status is not RecommendationStatus.RECOMMEND:
        raise ExecutiveBridgeError(
            "only a RECOMMEND recommendation may be submitted to A2"
        )
    if not current.is_fresh(now=now):
        raise ExecutiveBridgeError(
            "stale or expired recommendation cannot be submitted to A2"
        )
    if not isinstance(trusted_requirements, Mapping) or not trusted_requirements:
        raise ExecutiveBridgeError(
            "trusted_requirements must be supplied by the caller"
        )
    cycle_id = _identity(observation_cycle_id, "observation_cycle_id")
    project = _text(target_project, "target_project", limit=500)
    selected = current.option(str(current.selected_option_id))
    from .control.broker import emergency_stop_reason, matching_veto_reason

    identities = {
        "GLOBAL",
        current.recommendation_id,
        selected.option_id,
        project,
    }
    stop = emergency_stop_reason(database, None, identities)
    if stop:
        raise ExecutiveBridgeError(
            f"STOP or PAUSE blocks recommendation ingest: {stop}"
        )
    veto = matching_veto_reason(database, identities)
    if veto:
        raise ExecutiveBridgeError(
            f"owner veto blocks recommendation ingest: {veto}"
        )
    digest = current.recommendation_hash
    objective = _text(
        f"{selected.title}. {selected.summary}",
        "selected option objective",
        limit=1000,
    )
    evidence = [{
        "record_type": "jarvis.v13.research-decision-provenance.v1",
        "authority": DATA_ONLY,
        "recommendation_id": current.recommendation_id,
        "recommendation_hash": f"sha256:{digest}",
        "recommendation_contract": current.CONTRACT,
        "dossier_revision": current.dossier_revision,
        "selected_option_id": selected.option_id,
        "evidence_refs": list(selected.evidence_refs),
        "evidence_authority": DATA_ONLY,
        "owner_context_advisory_refs": _plain(
            current.owner_context_advisory_refs
        ),
        "confidence_band": current.confidence_band.value,
        "source_quality_summary": _plain(current.source_quality_summary),
        "dependencies": [],
    }]
    confidence = {
        ConfidenceBand.LOW: 0.35,
        ConfidenceBand.MEDIUM: 0.65,
        ConfidenceBand.HIGH: 0.85,
    }[current.confidence_band]

    from .discovery_candidates import ingest_observed_candidate

    ingested = ingest_observed_candidate(
        database,
        cycle_id,
        observation_id=f"research-decision:{current.recommendation_id}",
        source_kind="RESEARCH_DECISION",
        source_identity=f"jarvis:v13:{current.recommendation_id}",
        source_ref=(
            f"research-decision:{current.recommendation_id}"
            f"@{current.dossier_revision}"
        ),
        content_hash=f"sha256:{digest}",
        objective=objective,
        observed_at=current.generated_at,
        authority=DATA_ONLY,
        fresh_until=current.expires_at,
        target_project=project,
        alias_key=_stable_id(
            "research-objective", {"objective": current.objective}
        ),
        evidence=evidence,
        candidate_type="tracked_work",
        expected_outcome=selected.summary,
        confidence=confidence,
        expires_at=current.expires_at,
        trusted_requirements=trusted_requirements,
    )
    candidate_id = ingested.get("candidate_id")
    if not candidate_id:
        raise ExecutiveBridgeError(
            "recommendation is already covered by a known mission"
        )

    decision = None
    consumed = False
    if evaluate_a2:
        if not resource_id or not loop_id:
            raise ExecutiveBridgeError(
                "resource_id and loop_id are required for A2 evaluation"
            )
        if executive_service is None:
            from .executive.decisions import ExecutiveDecisionService
            executive_service = ExecutiveDecisionService(
                database,
                source_id="v13-research-decision-bridge",
            )
        service_database = getattr(executive_service, "database", database)
        if getattr(service_database, "path", None) != database.path:
            raise ExecutiveBridgeError(
                "Executive service must use the supplied database"
            )
        kwargs: dict[str, Any] = {
            "resource_id": str(resource_id),
            "loop_id": str(loop_id),
        }
        if governor is not None:
            kwargs["governor"] = governor
        decision = executive_service.evaluate_a2(**kwargs)
        consumed = _executive_consumed_candidate(
            database,
            decision_id=str(decision.decision_id),
            candidate_id=str(candidate_id),
            decision=decision,
        )

    result_material = {
        "recommendation_id": current.recommendation_id,
        "candidate_id": candidate_id,
        "executive_decision_id": (
            str(decision.decision_id) if decision is not None else None
        ),
    }
    result = DecisionResult(
        result_id=_stable_id("decision-result", result_material),
        recommendation_id=current.recommendation_id,
        recommendation_hash=digest,
        candidate_id=str(candidate_id),
        candidate_created=_recommendation_created_candidate(
            database,
            recommendation_id=current.recommendation_id,
            candidate_id=str(candidate_id),
        ),
        candidate_fingerprint=str(ingested.get("fingerprint") or ""),
        evaluated_by_executive=decision is not None,
        consumed_by_executive=consumed,
        executive_decision_id=(
            str(decision.decision_id) if decision is not None else None
        ),
        executive_kind=(
            str(decision.kind) if decision is not None else None
        ),
        executive_candidate_id=(
            str(decision.candidate_id)
            if decision is not None and decision.candidate_id else None
        ),
    )
    record_decision_result(database, result)
    return result


__all__ = [
    "ADVISORY_ONLY",
    "CALIBRATION_CONTRACT",
    "ConfidenceBand",
    "DATA_ONLY",
    "DECISION_DIMENSIONS",
    "DECISION_OPTION_CONTRACT",
    "DECISION_RECOMMENDATION_CONTRACT",
    "DECISION_RESULT_CONTRACT",
    "DecisionOption",
    "DecisionRecommendation",
    "DecisionResult",
    "ExecutiveBridgeError",
    "QualitativeAssessment",
    "RESEARCH_PLAN_CONTRACT",
    "RecommendationStatus",
    "ResearchDecisionError",
    "ResearchDisposition",
    "ResearchPlan",
    "ResearchStopAssessment",
    "ActualOutcome",
    "assess_research_plan",
    "bridge_recommendation_to_executive",
    "compare_decision_options",
    "load_decision_recommendation",
    "load_decision_result",
    "load_research_plan",
    "plan_research",
    "rebuild_decision_calibration",
    "rebuild_research_decision_state",
    "record_decision_outcome",
    "record_decision_recommendation",
    "record_decision_result",
    "record_research_plan",
]
