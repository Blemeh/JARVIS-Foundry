"""Empirical workforce suitability without a second provider authority.

Samples remain immutable event truth.  An explicit per-metric evidence mask
keeps a missing observation UNKNOWN even when the frozen contract carries a
false-looking default value.  Recommendations only compare candidates that
the caller has already marked provider-ready and allowed.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite

from ..database import FoundryDatabase
from ..events import Event
from .contracts import SuitabilitySample, WORKFORCE_SCHEMA


SUITABILITY_EVENT_TYPE = "workforce.suitability_sample_recorded"
SUITABILITY_EVIDENCE_POLICY = "explicit-per-metric-v1"

BOOLEAN_METRICS = frozenset({
    "success",
    "verifier_rejected",
    "repaired",
    "provider_failed",
    "drifted",
    "stalled",
    "scope_violated",
    "cancellation_recovered",
})
NUMERIC_METRICS = frozenset({
    "latency_seconds",
    "cost_usd",
    "artifact_quality",
})
METRIC_NAMES = (
    "success",
    "verifier_rejected",
    "repaired",
    "latency_seconds",
    "cost_usd",
    "provider_failed",
    "drifted",
    "stalled",
    "scope_violated",
    "artifact_quality",
    "cancellation_recovered",
)
ROLLUP_DIMENSIONS = ("role", "domain", "framework", "provider", "model")
_PREFERENCES = frozenset({"HIGHER", "LOWER"})


class SuitabilityError(ValueError):
    """Suitability evidence or ranking policy is incomplete or inconsistent."""


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SuitabilityError(f"{name} must be a non-empty string")
    return value.strip()


def _evidence_refs(value: Iterable[str] | str, metric: str) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = (value,)
    else:
        try:
            raw = tuple(value)
        except TypeError as error:
            raise SuitabilityError(
                f"evidence references for {metric} must be iterable"
            ) from error
    refs = tuple(sorted({
        _required_text(item, f"evidence reference for {metric}")
        for item in raw
    }))
    if not refs:
        raise SuitabilityError(f"observed metric {metric} requires evidence")
    return refs


@dataclass(frozen=True, slots=True)
class MetricEvidence:
    metric: str
    refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.metric not in METRIC_NAMES:
            raise SuitabilityError(f"unsupported suitability metric: {self.metric}")
        object.__setattr__(self, "refs", _evidence_refs(self.refs, self.metric))


@dataclass(frozen=True, slots=True)
class SuitabilityRecordReceipt:
    sample_id: str
    sequence: int
    inserted: bool
    observed_metrics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecordedSuitabilitySample:
    sample: SuitabilitySample
    metric_evidence: tuple[MetricEvidence, ...]
    event_id: str

    @property
    def observed_metrics(self) -> tuple[str, ...]:
        return tuple(item.metric for item in self.metric_evidence)

    def evidence_for(self, metric: str) -> tuple[str, ...]:
        for item in self.metric_evidence:
            if item.metric == metric:
                return item.refs
        return ()


@dataclass(frozen=True, slots=True)
class MetricRollup:
    metric: str
    observed_count: int
    unknown_count: int
    true_count: int | None = None
    false_count: int | None = None
    rate: float | None = None
    total: float | None = None
    average: float | None = None
    minimum: float | None = None
    maximum: float | None = None

    @property
    def value(self) -> float | None:
        return self.rate if self.metric in BOOLEAN_METRICS else self.average


@dataclass(frozen=True, slots=True)
class SuitabilityRollup:
    group_by: tuple[str, ...]
    role: str | None
    domain: str | None
    framework: str | None
    provider: str | None
    model: str | None
    sample_count: int
    metrics: tuple[MetricRollup, ...]

    @property
    def key(self) -> tuple[str, ...]:
        return tuple(str(getattr(self, name)) for name in self.group_by)

    def metric(self, name: str) -> MetricRollup:
        for item in self.metrics:
            if item.metric == name:
                return item
        raise SuitabilityError(f"rollup does not contain metric: {name}")


@dataclass(frozen=True, slots=True)
class SuitabilityCandidate:
    """A candidate already classified by canonical readiness authorities."""

    candidate_id: str
    role: str
    domain: str
    framework: str
    provider: str
    model: str
    provider_ready: bool
    allowed: bool

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "role",
            "domain",
            "framework",
            "provider",
            "model",
        ):
            object.__setattr__(
                self,
                name,
                _required_text(getattr(self, name), name),
            )
        if not isinstance(self.provider_ready, bool):
            raise SuitabilityError("provider_ready must be canonical boolean input")
        if not isinstance(self.allowed, bool):
            raise SuitabilityError("allowed must be canonical boolean input")

    @property
    def rollup_key(self) -> tuple[str, ...]:
        return (
            self.role,
            self.domain,
            self.framework,
            self.provider,
            self.model,
        )


@dataclass(frozen=True, slots=True)
class SuitabilityMetricPolicy:
    """Caller-owned normalization and weighting for one empirical metric."""

    metric: str
    weight: float
    preference: str
    best: float
    worst: float
    minimum_observations: int = 1

    def __post_init__(self) -> None:
        if self.metric not in METRIC_NAMES:
            raise SuitabilityError(f"unsupported suitability metric: {self.metric}")
        weight = float(self.weight)
        best = float(self.best)
        worst = float(self.worst)
        preference = str(self.preference).upper()
        if not isfinite(weight) or weight <= 0:
            raise SuitabilityError("metric policy weight must be finite and positive")
        if not isfinite(best) or not isfinite(worst) or best == worst:
            raise SuitabilityError("metric policy best and worst must be finite and distinct")
        if preference not in _PREFERENCES:
            raise SuitabilityError("metric preference must be HIGHER or LOWER")
        if preference == "HIGHER" and best <= worst:
            raise SuitabilityError("HIGHER preference requires best greater than worst")
        if preference == "LOWER" and best >= worst:
            raise SuitabilityError("LOWER preference requires best less than worst")
        if int(self.minimum_observations) < 1:
            raise SuitabilityError("minimum_observations must be positive")
        object.__setattr__(self, "weight", weight)
        object.__setattr__(self, "best", best)
        object.__setattr__(self, "worst", worst)
        object.__setattr__(self, "preference", preference)
        object.__setattr__(
            self,
            "minimum_observations",
            int(self.minimum_observations),
        )

    def normalize(self, value: float) -> float:
        if self.preference == "HIGHER":
            normalized = (value - self.worst) / (self.best - self.worst)
        else:
            normalized = (self.worst - value) / (self.worst - self.best)
        return max(0.0, min(1.0, normalized))


@dataclass(frozen=True, slots=True)
class SuitabilityRecommendationPolicy:
    metrics: tuple[SuitabilityMetricPolicy, ...]
    minimum_coverage: float = 1.0
    minimum_samples: int = 1

    def __post_init__(self) -> None:
        metrics = tuple(self.metrics)
        if not metrics:
            raise SuitabilityError("recommendation policy requires at least one metric")
        names = [item.metric for item in metrics]
        if len(names) != len(set(names)):
            raise SuitabilityError("recommendation policy metrics must be unique")
        coverage = float(self.minimum_coverage)
        if not isfinite(coverage) or not 0.0 <= coverage <= 1.0:
            raise SuitabilityError("minimum_coverage must be within [0, 1]")
        if int(self.minimum_samples) < 1:
            raise SuitabilityError("minimum_samples must be positive")
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "minimum_coverage", coverage)
        object.__setattr__(self, "minimum_samples", int(self.minimum_samples))


@dataclass(frozen=True, slots=True)
class CandidateSuitability:
    candidate: SuitabilityCandidate
    status: str
    score: float | None
    coverage: float
    sample_count: int
    metric_values: tuple[tuple[str, float | None], ...]
    unknown_metrics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SuitabilityRecommendation:
    ranked: tuple[CandidateSuitability, ...]
    excluded: tuple[CandidateSuitability, ...]

    @property
    def status(self) -> str:
        scored = tuple(item for item in self.ranked if item.score is not None)
        if not scored:
            return "UNKNOWN" if self.ranked else "NO_ELIGIBLE_CANDIDATES"
        if len(scored) > 1 and scored[0].score == scored[1].score:
            return "TIED"
        return "RECOMMENDED"

    @property
    def recommended(self) -> CandidateSuitability | None:
        if self.status != "RECOMMENDED":
            return None
        return self.ranked[0]


def _normalize_metric_evidence(
    sample: SuitabilitySample,
    evidence_by_metric: Mapping[str, Iterable[str] | str] | None,
) -> tuple[MetricEvidence, ...]:
    if evidence_by_metric is None:
        return ()
    if not isinstance(evidence_by_metric, Mapping):
        raise SuitabilityError("evidence_by_metric must be a mapping")
    evidence: list[MetricEvidence] = []
    for raw_metric, raw_refs in evidence_by_metric.items():
        metric = str(raw_metric)
        if metric not in METRIC_NAMES:
            raise SuitabilityError(f"unsupported suitability metric: {metric}")
        if getattr(sample, metric) is None:
            raise SuitabilityError(
                f"metric {metric} cannot be observed without a sample value"
            )
        if (
            metric in BOOLEAN_METRICS
            and not isinstance(getattr(sample, metric), bool)
        ):
            raise SuitabilityError(
                f"observed boolean metric {metric} must be boolean"
            )
        evidence.append(MetricEvidence(metric, _evidence_refs(raw_refs, metric)))
    return tuple(sorted(evidence, key=lambda item: item.metric))


def record_suitability_sample(
    database: FoundryDatabase,
    sample: SuitabilitySample,
    *,
    evidence_by_metric: Mapping[str, Iterable[str] | str] | None = None,
    source_id: str = "workforce-suitability",
) -> SuitabilityRecordReceipt:
    """Append one immutable empirical sample with explicit evidence coverage."""
    if not isinstance(sample, SuitabilitySample):
        raise SuitabilityError("sample must be a SuitabilitySample")
    evidence = _normalize_metric_evidence(sample, evidence_by_metric)
    payload = {
        **sample.to_payload(),
        "evidence_policy": SUITABILITY_EVIDENCE_POLICY,
        "metric_evidence": {
            item.metric: list(item.refs)
            for item in evidence
        },
    }
    event = Event.create(
        SUITABILITY_EVENT_TYPE,
        payload,
        source_type="supervisor",
        source_id=_required_text(source_id, "source_id"),
        correlation_id=sample.package_id,
        idempotency_key=f"{SUITABILITY_EVENT_TYPE}:{sample.sample_id}",
        occurred_at=sample.recorded_at,
    )
    sequence, inserted = database.append(event)
    return SuitabilityRecordReceipt(
        sample_id=sample.sample_id,
        sequence=sequence,
        inserted=inserted,
        observed_metrics=tuple(item.metric for item in evidence),
    )


def load_suitability_samples(
    database: FoundryDatabase,
) -> tuple[RecordedSuitabilitySample, ...]:
    """Rebuild immutable samples and their UNKNOWN-safe evidence masks."""
    records: dict[str, RecordedSuitabilitySample] = {}
    for event in database.events(event_types=(SUITABILITY_EVENT_TYPE,)):
        payload = event.payload
        if payload.get("schema") != WORKFORCE_SCHEMA:
            raise SuitabilityError(
                f"unsupported workforce suitability schema: {payload.get('schema')}"
            )
        evidence_policy = payload.get("evidence_policy")
        if evidence_policy not in {None, SUITABILITY_EVIDENCE_POLICY}:
            raise SuitabilityError(
                f"unsupported suitability evidence policy: {evidence_policy}"
            )
        sample = SuitabilitySample.from_payload(payload)
        raw_evidence = payload.get("metric_evidence", {})
        if raw_evidence is None:
            raw_evidence = {}
        if not isinstance(raw_evidence, Mapping):
            raise SuitabilityError("metric_evidence must be a mapping")
        evidence = _normalize_metric_evidence(sample, raw_evidence)
        record = RecordedSuitabilitySample(
            sample=sample,
            metric_evidence=evidence,
            event_id=event.event_id,
        )
        prior = records.get(sample.sample_id)
        if prior is not None:
            if (
                prior.sample != record.sample
                or prior.metric_evidence != record.metric_evidence
            ):
                raise SuitabilityError(
                    f"conflicting suitability sample identity: {sample.sample_id}"
                )
            continue
        records[sample.sample_id] = record
    return tuple(records.values())


def _validated_dimensions(group_by: Sequence[str]) -> tuple[str, ...]:
    dimensions = tuple(str(item) for item in group_by)
    if len(dimensions) != len(set(dimensions)):
        raise SuitabilityError("rollup dimensions must be unique")
    invalid = set(dimensions) - set(ROLLUP_DIMENSIONS)
    if invalid:
        raise SuitabilityError(f"unsupported rollup dimensions: {sorted(invalid)}")
    return dimensions


def _metric_rollup(
    metric: str,
    records: Sequence[RecordedSuitabilitySample],
) -> MetricRollup:
    values = [
        getattr(record.sample, metric)
        for record in records
        if record.evidence_for(metric)
    ]
    observed = len(values)
    unknown = len(records) - observed
    if metric in BOOLEAN_METRICS:
        true_count = sum(bool(value) for value in values)
        false_count = observed - true_count
        return MetricRollup(
            metric=metric,
            observed_count=observed,
            unknown_count=unknown,
            true_count=true_count,
            false_count=false_count,
            rate=(true_count / observed if observed else None),
        )
    numeric = tuple(float(value) for value in values)
    total = sum(numeric) if numeric else None
    return MetricRollup(
        metric=metric,
        observed_count=observed,
        unknown_count=unknown,
        total=total,
        average=(total / observed if total is not None else None),
        minimum=(min(numeric) if numeric else None),
        maximum=(max(numeric) if numeric else None),
    )


def aggregate_suitability(
    database: FoundryDatabase,
    *,
    group_by: Sequence[str] = ROLLUP_DIMENSIONS,
) -> tuple[SuitabilityRollup, ...]:
    """Aggregate observed metrics by any canonical workforce dimensions."""
    dimensions = _validated_dimensions(group_by)
    grouped: dict[
        tuple[str, ...],
        list[RecordedSuitabilitySample],
    ] = defaultdict(list)
    for record in load_suitability_samples(database):
        key = tuple(str(getattr(record.sample, name)) for name in dimensions)
        grouped[key].append(record)

    rollups: list[SuitabilityRollup] = []
    for key in sorted(grouped):
        records = grouped[key]
        dimension_values: dict[str, str | None] = {
            name: None for name in ROLLUP_DIMENSIONS
        }
        dimension_values.update(dict(zip(dimensions, key, strict=True)))
        rollups.append(SuitabilityRollup(
            group_by=dimensions,
            role=dimension_values["role"],
            domain=dimension_values["domain"],
            framework=dimension_values["framework"],
            provider=dimension_values["provider"],
            model=dimension_values["model"],
            sample_count=len(records),
            metrics=tuple(
                _metric_rollup(metric, records)
                for metric in METRIC_NAMES
            ),
        ))
    return tuple(rollups)


def recommend_suitable_candidates(
    database: FoundryDatabase,
    candidates: Sequence[SuitabilityCandidate],
    *,
    policy: SuitabilityRecommendationPolicy,
) -> SuitabilityRecommendation:
    """Rank only caller-authorized, provider-ready empirical candidates."""
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise SuitabilityError("candidate_id values must be unique")
    rollups = {
        rollup.key: rollup
        for rollup in aggregate_suitability(
            database,
            group_by=ROLLUP_DIMENSIONS,
        )
    }
    total_weight = sum(metric.weight for metric in policy.metrics)
    ranked: list[CandidateSuitability] = []
    excluded: list[CandidateSuitability] = []

    for candidate in candidates:
        if not candidate.allowed or not candidate.provider_ready:
            status = "NOT_ALLOWED" if not candidate.allowed else "PROVIDER_NOT_READY"
            excluded.append(CandidateSuitability(
                candidate=candidate,
                status=status,
                score=None,
                coverage=0.0,
                sample_count=0,
                metric_values=(),
                unknown_metrics=tuple(metric.metric for metric in policy.metrics),
            ))
            continue

        rollup = rollups.get(candidate.rollup_key)
        sample_count = rollup.sample_count if rollup is not None else 0
        known_weight = 0.0
        weighted_score = 0.0
        metric_values: list[tuple[str, float | None]] = []
        unknown_metrics: list[str] = []
        for metric_policy in policy.metrics:
            metric = (
                rollup.metric(metric_policy.metric)
                if rollup is not None
                else None
            )
            value = metric.value if metric is not None else None
            observed = metric.observed_count if metric is not None else 0
            metric_values.append((metric_policy.metric, value))
            if value is None or observed < metric_policy.minimum_observations:
                unknown_metrics.append(metric_policy.metric)
                continue
            known_weight += metric_policy.weight
            weighted_score += (
                metric_policy.normalize(value) * metric_policy.weight
            )

        coverage = known_weight / total_weight
        score = (
            weighted_score / known_weight
            if known_weight
            and sample_count >= policy.minimum_samples
            and coverage >= policy.minimum_coverage
            else None
        )
        ranked.append(CandidateSuitability(
            candidate=candidate,
            status="RANKED" if score is not None else "INSUFFICIENT_EVIDENCE",
            score=(round(score, 6) if score is not None else None),
            coverage=round(coverage, 6),
            sample_count=sample_count,
            metric_values=tuple(metric_values),
            unknown_metrics=tuple(unknown_metrics),
        ))

    ranked.sort(key=lambda item: (
        item.score is None,
        -(item.score if item.score is not None else 0.0),
        -item.coverage,
        item.candidate.candidate_id,
    ))
    return SuitabilityRecommendation(
        ranked=tuple(ranked),
        excluded=tuple(excluded),
    )


__all__ = [
    "BOOLEAN_METRICS",
    "METRIC_NAMES",
    "NUMERIC_METRICS",
    "ROLLUP_DIMENSIONS",
    "CandidateSuitability",
    "MetricEvidence",
    "MetricRollup",
    "RecordedSuitabilitySample",
    "SuitabilityCandidate",
    "SuitabilityError",
    "SuitabilityMetricPolicy",
    "SuitabilityRecommendation",
    "SuitabilityRecommendationPolicy",
    "SuitabilityRecordReceipt",
    "SuitabilityRollup",
    "aggregate_suitability",
    "load_suitability_samples",
    "recommend_suitable_candidates",
    "record_suitability_sample",
]
