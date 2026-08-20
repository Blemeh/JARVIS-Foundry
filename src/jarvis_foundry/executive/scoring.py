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

    def _weight(name: str, default: float | None = None) -> tuple[float | None, str]:
        raw = weights.get(name)
        if raw is None:
            ctx = weights.get("context") or {}
            if isinstance(ctx, dict):
                raw = ctx.get(name)
        if raw is None:
            return default, "UNKNOWN" if default is None else "KNOWN"
        try:
            return float(raw), "KNOWN"
        except (TypeError, ValueError):
            return None, "UNKNOWN"

    value_term, value_kind = _weight("value", None)
    urgency_term, urgency_kind = _weight("urgency", None)
    info_gain, info_kind = _weight("information_gain", None)
    source_trust, trust_kind = _weight("source_trust", None)
    effort_raw, effort_kind = _weight("effort_penalty", None)
    cost_raw, cost_kind = _weight("cost_penalty", None)
    freshness_raw, freshness_kind = _weight("freshness", None)
    goal_fit = 0.0
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

    bait_penalty = 0.0
    if goal_fit < 0.1 and (
        (value_term is not None and value_term > 0.5)
        or (urgency_term is not None and urgency_term > 0.5)
    ):
        bait_penalty = 0.15

    # Reversibility / risk from evidence and objective terms.
    reversibility = 0.7
    if "irreversible" in objective.lower() or "migrate" in objective.lower():
        reversibility = 0.2
    risk_penalty = 0.1 if reversibility < 0.5 else 0.0

    factors = (
        _factor("goal_fit", goal_fit, 0.30),
        _factor("expected_value", value_term, 0.20, kind=value_kind),
        _factor("information_gain", info_gain, 0.10, kind=info_kind),
        _factor("urgency", urgency_term, 0.10, kind=urgency_kind),
        _factor("confidence", source_trust, 0.05, kind=trust_kind),
        _factor(
            "effort",
            None if effort_raw is None else 1.0 - effort_raw,
            0.05,
            kind=effort_kind,
        ),
        _factor(
            "cost",
            None if cost_raw is None else 1.0 - cost_raw,
            0.05,
            kind=cost_kind,
        ),
        _factor("reversibility", reversibility, 0.05),
        _factor("resource_contention", 1.0 - resource_contention, 0.05),
        _factor("owner_priority", owner_priority, 0.10),
        _factor(
            "freshness",
            None if freshness_raw is None else freshness_raw + base * 0.1,
            0.05,
            kind=freshness_kind,
        ),
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
