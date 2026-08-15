"""Deterministic explainable scoring for portfolio candidate actions."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis_foundry.kernel.portfolio.records import (
    AlternativeScore,
    CandidateAction,
    PortfolioProject,
)


@dataclass(frozen=True)
class ScoreWeights:
    value: float = 0.30
    urgency: float = 0.20
    information_gain: float = 0.15
    reversibility: float = 0.10
    risk: float = 0.10
    cost: float = 0.15
    uncertainty_discount: float = 0.20
    opportunity_cost: float = 0.05


def _round(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def score_alternative(
    project: PortfolioProject,
    candidate: CandidateAction,
    weights: ScoreWeights | None = None,
) -> AlternativeScore:
    """Score one candidate against its canonical project with full tradeoffs."""
    w = weights or ScoreWeights()
    value = w.value * candidate.expected_value
    urgency = w.urgency * (0.5 * candidate.urgency + 0.5 * project.urgency)
    information_gain = w.information_gain * candidate.information_gain
    reversibility = w.reversibility * candidate.reversibility
    risk = w.risk * candidate.risk
    cost = w.cost * min(1.0, candidate.cost_usd / 100.0)
    uncertainty = w.uncertainty_discount * candidate.uncertainty
    opportunity_cost = w.opportunity_cost * project.opportunity_cost
    total = _round(
        value
        + urgency
        + information_gain
        + reversibility
        - risk
        - cost
        - uncertainty
        - opportunity_cost
    )
    dimensions: dict[str, float] = {
        "value": round(value, 4),
        "urgency": round(urgency, 4),
        "information_gain": round(information_gain, 4),
        "reversibility": round(reversibility, 4),
        "risk": round(risk, 4),
        "cost": round(cost, 4),
        "uncertainty": round(uncertainty, 4),
        "opportunity_cost": round(opportunity_cost, 4),
    }
    tradeoffs: list[str] = []
    if candidate.expected_value >= project.expected_value:
        tradeoffs.append(f"value {candidate.expected_value:.2f} meets or beats project baseline")
    else:
        tradeoffs.append(f"value {candidate.expected_value:.2f} below project baseline")
    if candidate.risk > 0.5:
        tradeoffs.append(f"elevated risk {candidate.risk:.2f}")
    if candidate.uncertainty > 0.5:
        tradeoffs.append(f"high uncertainty {candidate.uncertainty:.2f}")
    tradeoffs.append(f"opportunity cost {project.opportunity_cost:.2f}")
    reason = (
        f"deterministic weighted score {total:.4f} from {len(candidate.evidence_refs)} "
        f"evidence refs for {candidate.action_class}"
    )
    return AlternativeScore(
        candidate_id=candidate.candidate_id,
        total_score=total,
        dimensions=dimensions,
        uncertainty=candidate.uncertainty,
        opportunity_cost=project.opportunity_cost,
        tradeoffs=tuple(tradeoffs),
        reason=reason,
    )


__all__ = ["AlternativeScore", "ScoreWeights", "score_alternative"]
