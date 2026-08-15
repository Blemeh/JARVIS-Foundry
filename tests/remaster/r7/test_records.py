from __future__ import annotations

from r7_helpers import FIXED_UTC, make_candidate, make_claim, make_observation, make_project

from jarvis_foundry.kernel.portfolio.records import (
    AlternativeScore,
    CandidateAction,
    MonitorChange,
    Observation,
    PortfolioProject,
    ResearchClaim,
    ResearchSource,
    ResearchSynthesis,
    SelectionDecision,
    SourceRef,
)


def test_source_ref_round_trip(fixed_time: str = FIXED_UTC) -> None:
    ref = SourceRef(
        source_type="git",
        source_id="leadgen",
        captured_at=fixed_time,
        content_hash="a" * 64,
        locator="proving/status.txt",
        capture_method="read-only",
        trust_class="owner-controlled",
        freshness_seconds=900,
    )
    assert SourceRef.from_dict(ref.to_dict()) == ref


def test_project_round_trip_has_all_required_fields(fixed_time: str = FIXED_UTC) -> None:
    project = make_project(fixed_time=fixed_time)
    restored = PortfolioProject.from_dict(project.to_dict())
    assert restored == project
    required = {
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
    }
    assert required <= set(project.to_dict())


def test_candidate_round_trip(fixed_time: str = FIXED_UTC) -> None:
    candidate = make_candidate(fixed_time=fixed_time)
    assert CandidateAction.from_dict(candidate.to_dict()) == candidate


def test_observation_and_claim_round_trip(fixed_time: str = FIXED_UTC) -> None:
    observation = make_observation(fixed_time=fixed_time)
    assert Observation.from_dict(observation.to_dict()) == observation
    claim = make_claim()
    assert ResearchClaim.from_dict(claim.to_dict()) == claim


def test_decision_records_alternatives_and_binding(fixed_time: str = FIXED_UTC) -> None:
    alt = AlternativeScore(
        candidate_id="alpha",
        total_score=0.6,
        dimensions={"value": 0.5, "urgency": 0.2},
        uncertainty=0.1,
        opportunity_cost=0.2,
        tradeoffs=("higher value, higher risk",),
        reason="evidence-backed repair",
    )
    decision = SelectionDecision(
        decision_id="decision-1",
        portfolio_hash="p" * 64,
        control_hash="c" * 64,
        budget_key="b",
        selected_candidate_id="alpha",
        project_id="leadgen",
        canonical_target="proving/leadgen",
        status="SELECTED",
        reason="highest deterministic score",
        alternatives=(alt,),
        uncertainty={"alpha": 0.1},
        supersedes=None,
        created_at="2026-08-15T16:30:00Z",
        idempotency_key="decision:1",
    )
    restored = SelectionDecision.from_dict(decision.to_dict())
    assert restored == decision
    assert restored.alternatives[0].candidate_id == "alpha"


def test_research_synthesis_authority_is_never_owner(fixed_time: str = FIXED_UTC) -> None:
    source = ResearchSource(
        source_id="s",
        locator="proving/s.md",
        capture_method="read-only",
        captured_at="2026-08-15T16:30:00Z",
        content_hash="f" * 64,
        trust_class="official",
        freshness_seconds=3600,
    )
    synthesis = ResearchSynthesis(
        research_id="r",
        question="q",
        project_id="p",
        value_statement="v",
        sources=(source,),
        claims=(),
        contradictions=(),
        unknowns=(),
        decision_input="hold",
        status="READY",
        authority="UNTRUSTED_DATA",
        injection_attempts=0,
        snapshot_dir="proving/snapshots",
        created_at="2026-08-15T16:30:00Z",
        idempotency_key="research:1",
    )
    assert ResearchSynthesis.from_dict(synthesis.to_dict()).authority == "UNTRUSTED_DATA"


def test_monitor_change_round_trip(fixed_time: str = FIXED_UTC) -> None:
    change = MonitorChange(
        change_id="change-1",
        source_id="health-feed",
        before_hash="b" * 64,
        after_hash="a" * 64,
        captured_at="2026-08-15T16:30:00Z",
        meaningful=True,
        candidate_id="cand-1",
        duplicate_of=None,
        idempotency_key="change:1",
    )
    assert MonitorChange.from_dict(change.to_dict()) == change
