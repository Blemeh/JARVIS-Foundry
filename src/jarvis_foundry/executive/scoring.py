"""Unified portfolio comparison for executive candidate ranking."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..database import FoundryDatabase
from ..discovery_candidates import score_candidate, score_objective
from .calibration import CalibrationState, rebuild_calibration
from .goals import (
    StructuredGoal,
    active_goals,
    goal_alignment_score,
    owner_priority_for,
)

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
    owner_directive_priority: float = 0.0
    owner_priority_directive_ids: tuple[str, ...] = ()


def _factor(name: str, value: float | None, weight: float, *, kind: str | None = None) -> FactorValue:
    if value is None:
        return FactorValue(name, None, kind or "UNKNOWN", weight, -UNKNOWN_PENALTY * weight)
    return FactorValue(name, value, kind or "KNOWN", weight, round(value * weight, 4))




def _policy_weights(database: FoundryDatabase, policy_version: str) -> dict[str, Any]:
    """Weights for a registered policy version; empty dict if unknown."""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT weights_json FROM scoring_policy_registry "
            "WHERE policy_version = ?", (policy_version,)).fetchone()
    if row is None:
        return {}
    try:
        return json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        return {}



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

    target_identities = {"GLOBAL", candidate_id}
    resource_target = str(row.get("resource_id") or "").strip()
    if resource_target:
        target_identities.add(resource_target)
    directive_priority, priority_directives = owner_priority_for(
        database, targets=target_identities)

    cal_adj = calibration.adjustment_for(source=source, objective=objective)

    # Keyword-bait damping: hype terms (URGENT/crash/now/...) inflate the
    # provider's value/urgency estimates independently of whether the work
    # advances an active owner goal. When goal fit is weak, scale those
    # terms by strategic fit so "interesting" cannot outrank "aligned".
    # The penalty is recorded as its own factor for explainability.
    hype_terms = ("urgent", "crash", "asap", "immediately", "deadline",
                  "tonight", "critical!!")
    has_hype = any(t in objective.lower() for t in hype_terms)
    bait_penalty = 0.0
    if goal_fit < 0.15 and (
        (value_term is not None and value_term > 0.5)
        or (urgency_term is not None and urgency_term > 0.5)
        or has_hype
    ):
        bait_penalty = 0.15
        value_term = None if value_term is None else round(value_term * 0.5, 4)
        urgency_term = None if urgency_term is None else round(urgency_term * 0.5, 4)

    # Reversibility / risk from evidence and objective terms.
    reversibility = 0.7
    if "irreversible" in objective.lower() or "migrate" in objective.lower():
        reversibility = 0.2
    risk_penalty = 0.1 if reversibility < 0.5 else 0.0

    factor_values = [
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
        _factor("keyword_bait", 1.0 if bait_penalty else 0.0, 0.15),
    ]
    # Reprioritization is a durable owner control, distinct from the lexical
    # goal-priority factor above.  Keep it out of legacy scores when absent;
    # when present its signed contribution can raise or lower a target.
    if priority_directives:
        factor_values.append(
            _factor("owner_directive_priority", directive_priority, 0.15))
    factors = tuple(factor_values)

    composite = round(sum(f.contribution for f in factors), 4)
    # V3 WP2 integration (MVP review repair): when the candidate was
    # scored under a registry policy, its PORTFOLIO TOTAL is the
    # composite base — the policy already weighs value/urgency/
    # confidence/effort/cost/risk deterministically and auditable. The
    # executive adds only what the policy does not model: active-goal
    # alignment, owner priority, resource contention headroom. Safety
    # adjustments below still apply. Candidates WITHOUT a policy trace
    # keep the legacy factor sum unchanged.
    with database.connect() as _connection:
        _policy_row = _connection.execute(
            "SELECT scoring_policy_version FROM candidates "
            "WHERE candidate_id = ?", (candidate_id,)).fetchone()
    if _policy_row is not None and _policy_row[0]:
        from ..v3_scoring import compute_total, derive_dimensions
        with database.connect() as _c2:
            evidence_row = _c2.execute(
                "SELECT evidence_json FROM candidates WHERE candidate_id = ?",
                (candidate_id,)).fetchone()
        try:
            evidence_count = len(json.loads((evidence_row[0] if evidence_row else None) or "[]"))
        except (TypeError, ValueError):
            evidence_count = 0
        dims = derive_dimensions(
            objective, evidence_count=evidence_count)
        policy_weights = _policy_weights(database, _policy_row[0])
        portfolio_total = compute_total(dims, policy_weights)
        goal_bonus = round(goal_fit * 0.15 + owner_priority * 0.05, 4)
        contention_headroom = round(0.05 * (1.0 - resource_contention), 4)
        composite = round(max(0.0, min(1.0,
            portfolio_total + goal_bonus + contention_headroom)), 4)
        composite = round(composite + directive_priority * 0.15, 4)
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
        owner_directive_priority=directive_priority,
        owner_priority_directive_ids=tuple(
            str(item["directive_id"])
            for item in priority_directives
            if item.get("directive_id")
        ),
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
