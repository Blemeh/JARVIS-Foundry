"""Unified portfolio comparison for executive candidate ranking."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..database import FoundryDatabase
from ..discovery_candidates import score_candidate, score_objective
from .calibration import CalibrationState, rebuild_calibration
from .goals import StructuredGoal, active_goals, goal_alignment_score

DECISION_VERSION = "1.0.0"
UNKNOWN_PENALTY = 0.03


@dataclass(frozen=True, slots=True)
class FactorValue:
    name: str
    value: float | None
    kind: str  # KNOWN | ESTIMATED | UNKNOWN
    weight: float
    contribution: float


@dataclass(frozen=True, slots=True)
class CandidateScore:
    candidate_id: str
    objective: str
    source: str
    composite: float
    base_score: float
    goal_fit: float
    calibration_adjustment: float
    owner_priority: float
    factors: tuple[FactorValue, ...]
    compared_goals: tuple[str, ...]


def _factor(name: str, value: float | None, weight: float, *, kind: str | None = None) -> FactorValue:
    if value is None:
        return FactorValue(name, None, kind or "UNKNOWN", weight, -UNKNOWN_PENALTY * weight)
    return FactorValue(name, value, kind or "KNOWN", weight, round(value * weight, 4))


def score_candidate_for_executive(
    database: FoundryDatabase,
    row: dict[str, Any],
    *,
    goals: list[StructuredGoal] | None = None,
    calibration: CalibrationState | None = None,
    resource_contention: float = 0.0,
) -> CandidateScore:
    """Score one eligible candidate with goal alignment and calibration."""
    goals = goals if goals is not None else active_goals(database)
    calibration = calibration if calibration is not None else rebuild_calibration(database)
    candidate_id = str(row["candidate_id"])
    objective = str(row["objective"])
    source = str(row["source"])

    base = score_candidate(database, candidate_id)
    with database.connect() as connection:
        score_row = connection.execute(
            "SELECT weights_json FROM candidate_scores WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    weights: dict[str, Any] = {}
    if score_row:
        import json
        try:
            weights = json.loads(score_row[0] or "{}")
        except (TypeError, ValueError):
            weights = {}

    goal_fit = 0.0
    compared: list[str] = []
    owner_priority = 0.0
    if goals:
        fits = [goal_alignment_score(g, objective) for g in goals]
        goal_fit = max(fits) if fits else 0.0
        best_idx = fits.index(goal_fit) if fits else 0
        compared = [goals[best_idx].goal_id] if goals else []
        owner_priority = goals[best_idx].priority / 3.0 if goals else 0.0
    else:
        compared = []

    cal_adj = calibration.adjustment_for(source=source, objective=objective)

    # Keyword-bait penalty: high urgency/value terms but zero goal fit.
    value_term = float(weights.get("value", weights.get("context", {}).get("value", 0.0)))
    urgency_term = float(weights.get("urgency", 0.0))
    bait_penalty = 0.0
    if goal_fit < 0.1 and (value_term > 0.5 or urgency_term > 0.5):
        bait_penalty = 0.15

    # Reversibility / risk from evidence and objective terms.
    reversibility = 0.7
    if "irreversible" in objective.lower() or "migrate" in objective.lower():
        reversibility = 0.2
    risk_penalty = 0.1 if reversibility < 0.5 else 0.0

    factors = (
        _factor("goal_fit", goal_fit, 0.30),
        _factor("expected_value", value_term, 0.20, kind="ESTIMATED"),
        _factor("information_gain", float(weights.get("information_gain", 0.0)), 0.10),
        _factor("urgency", urgency_term, 0.10),
        _factor("confidence", float(weights.get("source_trust", 0.85)), 0.05),
        _factor("effort", 1.0 - float(weights.get("effort_penalty", 0.0)), 0.05, kind="ESTIMATED"),
        _factor("cost", 1.0 - float(weights.get("cost_penalty", 0.0)), 0.05, kind="ESTIMATED"),
        _factor("reversibility", reversibility, 0.05),
        _factor("resource_contention", 1.0 - resource_contention, 0.05),
        _factor("owner_priority", owner_priority, 0.10),
        _factor("freshness", float(weights.get("freshness", 0.0)) + base * 0.1, 0.05),
    )

    composite = sum(f.contribution for f in factors)
    composite += cal_adj
    composite -= bait_penalty
    composite -= risk_penalty
    composite = round(max(0.0, min(1.0, composite)), 4)

    return CandidateScore(
        candidate_id=candidate_id,
        objective=objective,
        source=source,
        composite=composite,
        base_score=base,
        goal_fit=goal_fit,
        calibration_adjustment=cal_adj,
        owner_priority=owner_priority,
        factors=factors,
        compared_goals=tuple(compared),
    )


def rank_candidates(
    database: FoundryDatabase,
    rows: list[dict[str, Any]],
    *,
    goals: list[StructuredGoal] | None = None,
    calibration: CalibrationState | None = None,
    resource_contention: float = 0.0,
) -> list[CandidateScore]:
    """Rank eligible candidates by composite executive score."""
    calibration = calibration if calibration is not None else rebuild_calibration(database)
    scored = [
        score_candidate_for_executive(
            database, row, goals=goals, calibration=calibration,
            resource_contention=resource_contention,
        )
        for row in rows
    ]
    scored.sort(key=lambda s: (-s.composite, -s.goal_fit, s.candidate_id))
    return scored
