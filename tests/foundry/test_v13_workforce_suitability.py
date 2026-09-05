from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, EventConflict
from jarvis_foundry.workforce.contracts import SuitabilitySample
from jarvis_foundry.workforce.suitability import (
    METRIC_NAMES,
    SuitabilityCandidate,
    SuitabilityError,
    SuitabilityMetricPolicy,
    SuitabilityRecommendationPolicy,
    aggregate_suitability,
    load_suitability_samples,
    recommend_suitable_candidates,
    record_suitability_sample,
)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _sample(sample_id: str, **overrides) -> SuitabilitySample:
    fields = {
        "sample_id": sample_id,
        "package_id": f"package-{sample_id}",
        "session_id": f"session-{sample_id}",
        "role": "builder",
        "framework": "codex",
        "provider": "provider-a",
        "model": "model-a",
        "domain": "python",
        "success": True,
        "verifier_rejected": False,
        "repaired": False,
        "latency_seconds": 20.0,
        "cost_usd": 1.25,
        "provider_failed": False,
        "drifted": False,
        "stalled": False,
        "scope_violated": False,
        "artifact_quality": 0.9,
        "cancellation_recovered": True,
        "recorded_at": "2026-09-04T10:00:00Z",
    }
    fields.update(overrides)
    return SuitabilitySample(**fields)


def _all_evidence(prefix: str) -> dict[str, tuple[str, ...]]:
    return {
        metric: (f"evidence:{prefix}:{metric}",)
        for metric in METRIC_NAMES
    }


def _candidate(candidate_id: str, **overrides) -> SuitabilityCandidate:
    fields = {
        "candidate_id": candidate_id,
        "role": "builder",
        "domain": "python",
        "framework": "codex",
        "provider": "provider-a",
        "model": "model-a",
        "provider_ready": True,
        "allowed": True,
    }
    fields.update(overrides)
    return SuitabilityCandidate(**fields)


def test_record_is_immutable_idempotent_and_restart_safe(tmp_path: Path) -> None:
    database = _database(tmp_path)
    sample = _sample("sample-1")
    evidence = _all_evidence("sample-1")
    evidence["success"] = ("verdict:1", "outcome:1")

    first = record_suitability_sample(
        database,
        sample,
        evidence_by_metric=evidence,
    )
    replay = record_suitability_sample(
        database,
        sample,
        evidence_by_metric={
            **evidence,
            "success": ("outcome:1", "verdict:1"),
        },
    )

    assert first.inserted is True
    assert replay.inserted is False
    assert first.sequence == replay.sequence
    assert database.event_count(event_type="workforce.suitability_sample_recorded") == 1

    reopened = FoundryDatabase(database.path)
    records = load_suitability_samples(reopened)
    assert len(records) == 1
    assert records[0].sample == sample
    assert records[0].evidence_for("success") == ("outcome:1", "verdict:1")

    with pytest.raises(EventConflict):
        record_suitability_sample(
            database,
            replace(sample, success=False),
            evidence_by_metric=evidence,
        )


def test_rollup_treats_missing_boolean_evidence_as_unknown(tmp_path: Path) -> None:
    database = _database(tmp_path)
    observed_failure = _sample(
        "observed-failure",
        success=False,
        latency_seconds=12.0,
    )
    default_looking_values = _sample(
        "missing-evidence",
        success=False,
        provider_failed=False,
        latency_seconds=None,
    )
    record_suitability_sample(
        database,
        observed_failure,
        evidence_by_metric={
            "success": ("verdict:failure",),
            "latency_seconds": ("run:duration",),
        },
    )
    record_suitability_sample(database, default_looking_values)

    rollup = aggregate_suitability(database)[0]
    assert rollup.sample_count == 2
    assert {metric.metric for metric in rollup.metrics} == set(METRIC_NAMES)

    success = rollup.metric("success")
    assert success.observed_count == 1
    assert success.unknown_count == 1
    assert success.true_count == 0
    assert success.false_count == 1
    assert success.rate == 0.0

    provider_failure = rollup.metric("provider_failed")
    assert provider_failure.observed_count == 0
    assert provider_failure.unknown_count == 2
    assert provider_failure.rate is None

    latency = rollup.metric("latency_seconds")
    assert latency.observed_count == 1
    assert latency.unknown_count == 1
    assert latency.average == 12.0


def test_rollup_covers_every_requested_metric(tmp_path: Path) -> None:
    database = _database(tmp_path)
    record_suitability_sample(
        database,
        _sample(
            "all-metrics",
            success=False,
            verifier_rejected=True,
            repaired=True,
            latency_seconds=18.0,
            cost_usd=0.75,
            provider_failed=True,
            drifted=True,
            stalled=True,
            scope_violated=True,
            artifact_quality=0.4,
            cancellation_recovered=False,
        ),
        evidence_by_metric=_all_evidence("all-metrics"),
    )

    rollup = aggregate_suitability(database)[0]
    assert {
        metric: rollup.metric(metric).value
        for metric in METRIC_NAMES
    } == {
        "success": 0.0,
        "verifier_rejected": 1.0,
        "repaired": 1.0,
        "latency_seconds": 18.0,
        "cost_usd": 0.75,
        "provider_failed": 1.0,
        "drifted": 1.0,
        "stalled": 1.0,
        "scope_violated": 1.0,
        "artifact_quality": 0.4,
        "cancellation_recovered": 0.0,
    }


def test_legacy_sample_without_evidence_mask_is_entirely_unknown(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    sample = _sample("legacy")
    database.append(Event.create(
        "workforce.suitability_sample_recorded",
        sample.to_payload(),
        source_type="supervisor",
        source_id="legacy-workforce",
        correlation_id=sample.package_id,
        idempotency_key="workforce.suitability_sample_recorded:legacy",
    ))

    rollup = aggregate_suitability(database)[0]
    assert rollup.sample_count == 1
    assert all(metric.observed_count == 0 for metric in rollup.metrics)
    assert all(metric.unknown_count == 1 for metric in rollup.metrics)


def test_rollups_support_each_canonical_dimension(tmp_path: Path) -> None:
    database = _database(tmp_path)
    record_suitability_sample(
        database,
        _sample("a"),
        evidence_by_metric={"success": ("verdict:a",)},
    )
    record_suitability_sample(
        database,
        _sample(
            "b",
            role="verifier",
            domain="typescript",
            framework="claude-code",
            provider="provider-b",
            model="model-b",
        ),
        evidence_by_metric={"success": ("verdict:b",)},
    )

    for dimension in ("role", "domain", "framework", "provider", "model"):
        rollups = aggregate_suitability(database, group_by=(dimension,))
        assert len(rollups) == 2
        assert all(rollup.group_by == (dimension,) for rollup in rollups)

    global_rollup = aggregate_suitability(database, group_by=())
    assert len(global_rollup) == 1
    assert global_rollup[0].sample_count == 2


def test_recommendation_uses_only_ready_allowed_caller_candidates(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    record_suitability_sample(
        database,
        _sample("quality", artifact_quality=0.95, latency_seconds=25.0),
        evidence_by_metric=_all_evidence("quality"),
    )
    record_suitability_sample(
        database,
        _sample(
            "speed",
            framework="claude-code",
            provider="provider-b",
            model="model-b",
            artifact_quality=0.6,
            latency_seconds=5.0,
        ),
        evidence_by_metric=_all_evidence("speed"),
    )
    record_suitability_sample(
        database,
        _sample(
            "unready",
            framework="unready-framework",
            provider="provider-c",
            model="model-c",
            artifact_quality=1.0,
            latency_seconds=1.0,
        ),
        evidence_by_metric=_all_evidence("unready"),
    )

    candidates = (
        _candidate(
            "speed",
            framework="claude-code",
            provider="provider-b",
            model="model-b",
        ),
        _candidate("quality"),
        _candidate(
            "unready",
            framework="unready-framework",
            provider="provider-c",
            model="model-c",
            provider_ready=False,
        ),
        _candidate("blocked", provider="provider-d", model="model-d", allowed=False),
    )
    quality_policy = SuitabilityRecommendationPolicy(metrics=(
        SuitabilityMetricPolicy(
            "artifact_quality",
            weight=1.0,
            preference="HIGHER",
            best=1.0,
            worst=0.0,
        ),
    ))

    quality_result = recommend_suitable_candidates(
        database,
        candidates,
        policy=quality_policy,
    )
    assert quality_result.recommended is not None
    assert quality_result.recommended.candidate.candidate_id == "quality"
    assert [item.candidate.candidate_id for item in quality_result.ranked] == [
        "quality",
        "speed",
    ]
    assert {
        item.candidate.candidate_id: item.status
        for item in quality_result.excluded
    } == {
        "unready": "PROVIDER_NOT_READY",
        "blocked": "NOT_ALLOWED",
    }

    latency_policy = SuitabilityRecommendationPolicy(metrics=(
        SuitabilityMetricPolicy(
            "latency_seconds",
            weight=1.0,
            preference="LOWER",
            best=0.0,
            worst=30.0,
        ),
    ))
    latency_result = recommend_suitable_candidates(
        database,
        candidates,
        policy=latency_policy,
    )
    assert latency_result.recommended is not None
    assert latency_result.recommended.candidate.candidate_id == "speed"


def test_missing_evidence_never_becomes_a_favorable_zero(tmp_path: Path) -> None:
    database = _database(tmp_path)
    record_suitability_sample(database, _sample("unknown"))
    policy = SuitabilityRecommendationPolicy(metrics=(
        SuitabilityMetricPolicy(
            "provider_failed",
            weight=1.0,
            preference="LOWER",
            best=0.0,
            worst=1.0,
        ),
    ))

    result = recommend_suitable_candidates(
        database,
        (_candidate("unknown"),),
        policy=policy,
    )
    assert result.recommended is None
    assert result.ranked[0].status == "INSUFFICIENT_EVIDENCE"
    assert result.ranked[0].score is None
    assert result.ranked[0].unknown_metrics == ("provider_failed",)


def test_recommendation_requires_full_default_coverage_and_refuses_ties(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    record_suitability_sample(
        database,
        _sample("partial"),
        evidence_by_metric={"success": ("verdict:partial",)},
    )
    record_suitability_sample(
        database,
        _sample(
            "complete",
            framework="claude-code",
            provider="provider-b",
            model="model-b",
        ),
        evidence_by_metric={
            "success": ("verdict:complete",),
            "provider_failed": ("provider:complete",),
        },
    )
    policy = SuitabilityRecommendationPolicy(metrics=(
        SuitabilityMetricPolicy(
            "success",
            weight=1.0,
            preference="HIGHER",
            best=1.0,
            worst=0.0,
        ),
        SuitabilityMetricPolicy(
            "provider_failed",
            weight=1.0,
            preference="LOWER",
            best=0.0,
            worst=1.0,
        ),
    ))

    result = recommend_suitable_candidates(
        database,
        (
            _candidate("partial"),
            _candidate(
                "complete",
                framework="claude-code",
                provider="provider-b",
                model="model-b",
            ),
        ),
        policy=policy,
    )
    assert result.status == "RECOMMENDED"
    assert result.recommended is not None
    assert result.recommended.candidate.candidate_id == "complete"
    assert {
        item.candidate.candidate_id: item.status
        for item in result.ranked
    } == {
        "complete": "RANKED",
        "partial": "INSUFFICIENT_EVIDENCE",
    }

    record_suitability_sample(
        database,
        _sample(
            "equal",
            framework="openhands",
            provider="provider-c",
            model="model-c",
        ),
        evidence_by_metric={
            "success": ("verdict:equal",),
            "provider_failed": ("provider:equal",),
        },
    )
    tied = recommend_suitable_candidates(
        database,
        (
            _candidate(
                "complete",
                framework="claude-code",
                provider="provider-b",
                model="model-b",
            ),
            _candidate(
                "equal",
                framework="openhands",
                provider="provider-c",
                model="model-c",
            ),
        ),
        policy=policy,
    )
    assert tied.status == "TIED"
    assert tied.recommended is None


def test_observed_metrics_require_supported_values_and_evidence(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    with pytest.raises(SuitabilityError, match="without a sample value"):
        record_suitability_sample(
            database,
            _sample("missing-latency", latency_seconds=None),
            evidence_by_metric={"latency_seconds": ("run:duration",)},
        )
    with pytest.raises(SuitabilityError, match="unsupported suitability metric"):
        record_suitability_sample(
            database,
            _sample("unknown-metric"),
            evidence_by_metric={"made_up_metric": ("evidence:1",)},
        )
    with pytest.raises(SuitabilityError, match="requires evidence"):
        record_suitability_sample(
            database,
            _sample("empty-evidence"),
            evidence_by_metric={"success": ()},
        )
    with pytest.raises(SuitabilityError, match="must be iterable"):
        record_suitability_sample(
            database,
            _sample("invalid-evidence"),
            evidence_by_metric={"success": None},
        )
    with pytest.raises(SuitabilityError, match="must be boolean"):
        record_suitability_sample(
            database,
            _sample("invalid-boolean", success=1),
            evidence_by_metric={"success": ("verdict:invalid",)},
        )
