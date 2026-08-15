from __future__ import annotations

from r7_helpers import FIXED_UTC, make_candidate, make_project

from jarvis_foundry.kernel.portfolio.scoring import score_alternative


def test_scoring_is_deterministic(fixed_time: str = FIXED_UTC) -> None:
    project = make_project(fixed_time=fixed_time)
    candidate = make_candidate(fixed_time=fixed_time)
    first = score_alternative(project, candidate)
    second = score_alternative(project, candidate)
    assert first.total_score == second.total_score
    assert first.to_dict() == second.to_dict()


def test_scoring_records_all_dimensions_and_tradeoffs(fixed_time: str = FIXED_UTC) -> None:
    project = make_project(fixed_time=fixed_time)
    candidate = make_candidate(fixed_time=fixed_time)
    scored = score_alternative(project, candidate)
    required_dimensions = {
        "value",
        "urgency",
        "information_gain",
        "reversibility",
        "risk",
        "cost",
        "uncertainty",
        "opportunity_cost",
    }
    assert required_dimensions <= set(scored.dimensions)
    assert scored.tradeoffs
    assert scored.reason


def test_uncertainty_and_risk_reduce_score(fixed_time: str = FIXED_UTC) -> None:
    project = make_project(fixed_time=fixed_time)
    safe = make_candidate(
        "safe", uncertainty=0.0, risk=0.0, expected_value=0.8, fixed_time=fixed_time
    )
    risky = make_candidate(
        "risky", uncertainty=0.9, risk=0.9, expected_value=0.8, fixed_time=fixed_time
    )
    assert (
        score_alternative(project, safe).total_score > score_alternative(project, risky).total_score
    )


def test_tie_break_is_candidate_id(fixed_time: str = FIXED_UTC) -> None:
    project = make_project(fixed_time=fixed_time)
    alpha = make_candidate("alpha-1", fixed_time=fixed_time)
    beta = make_candidate("beta-1", fixed_time=fixed_time)
    score_a = score_alternative(project, alpha)
    score_b = score_alternative(project, beta)
    assert score_a.total_score == score_b.total_score
    ordered = sorted((alpha, beta), key=lambda c: score_alternative(project, c).total_score)
    assert ordered[0].candidate_id == "alpha-1"


def test_higher_value_candidate_wins(fixed_time: str = FIXED_UTC) -> None:
    project = make_project(fixed_time=fixed_time)
    low = make_candidate("low", expected_value=0.1, fixed_time=fixed_time)
    high = make_candidate("high", expected_value=0.95, fixed_time=fixed_time)
    assert (
        score_alternative(project, high).total_score > score_alternative(project, low).total_score
    )
