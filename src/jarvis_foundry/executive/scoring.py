"""Unified portfolio comparison for executive candidate ranking."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
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
    a2_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class A2GovernorBounds:
    """Bounded rejection-first policy for the inert A2 judgment gate."""

    autonomous_wip_cap: int = 1
    candidate_limit: int = 20
    research_passes_per_candidate: int = 1
    research_budget_per_candidate: float = 1.0
    research_budget_per_cycle: float = 4.0
    candidate_ttl_hours: float = 168.0
    do_nothing_score: float = 0.45

    def __post_init__(self) -> None:
        if self.autonomous_wip_cap != 1:
            raise ValueError("V11 A2 autonomous_wip_cap must remain 1")
        if self.candidate_limit < 1:
            raise ValueError("candidate_limit must be positive")
        if self.research_passes_per_candidate != 1:
            raise ValueError("V11 A2 permits one normal research pass per candidate")
        for name in (
            "research_budget_per_candidate",
            "research_budget_per_cycle",
            "candidate_ttl_hours",
            "do_nothing_score",
        ):
            value = float(getattr(self, name))
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.do_nothing_score > 1.0:
            raise ValueError("do_nothing_score must be within [0, 1]")


def _factor(name: str, value: float | None, weight: float, *, kind: str | None = None) -> FactorValue:
    if value is None:
        return FactorValue(name, None, kind or "UNKNOWN", weight, -UNKNOWN_PENALTY * weight)
    return FactorValue(name, value, kind or "KNOWN", weight, round(value * weight, 4))


def _centered_factor(name: str, value: float | None, weight: float) -> FactorValue:
    """A2 evidence factor whose neutral midpoint does not inflate legacy scores."""
    if value is None:
        return FactorValue(name, None, "UNKNOWN", weight, -UNKNOWN_PENALTY * weight)
    bounded = max(0.0, min(1.0, float(value)))
    return FactorValue(name, bounded, "KNOWN", weight, round((bounded - 0.5) * weight, 4))


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _evidence_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _json_list(row.get("evidence_json")) if isinstance(item, dict)]


def _context_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row.get(name) is not None:
            return row.get(name)
    for evidence in _evidence_rows(row):
        for name in names:
            if name in evidence and evidence.get(name) is not None:
                return evidence.get(name)
    return None


def _tokens(value: str) -> set[str]:
    import re
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _overlap(first: str, second: str) -> float:
    left, right = _tokens(first), _tokens(second)
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def _best_context_overlap(objective: str, items: list[dict[str, Any]]) -> float | None:
    if not items:
        return None
    return max((_overlap(objective, str(item.get("text") or "")) for item in items), default=0.0)


def _fabric_snapshot(database: FoundryDatabase) -> dict[str, dict[str, Any]]:
    """Read the canonical Catalog once for the whole bounded ranking pass."""
    from ..fabric.catalog import catalog_snapshot

    snapshot = catalog_snapshot(database)
    return {
        str(row.get("spec", {}).get("capability_id")): {
            "spec": dict(row.get("spec") or {}),
            "readiness": dict(row.get("readiness") or {}),
        }
        for row in snapshot.get("capabilities", ())
        if isinstance(row, dict) and row.get("spec", {}).get("capability_id")
    }


def _required_capabilities(row: dict[str, Any]) -> tuple[str, ...]:
    raw = _context_value(row, "required_capabilities", "capability_requirements")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            raw = parsed if isinstance(parsed, list) else [raw]
        except (TypeError, ValueError):
            raw = [raw]
    return tuple(sorted({str(value).strip() for value in (raw or ()) if str(value).strip()}))


def _dependency_state(database: FoundryDatabase, row: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
    marker = _context_value(row, "dependencies", "dependency_ids")
    if marker is None:
        return None, {"dependencies": [], "unknown": True}
    dependencies = _json_list(marker) if not isinstance(marker, list) else marker
    ids = tuple(sorted({str(value).strip() for value in dependencies if str(value).strip()}))
    if not ids:
        return 1.0, {"dependencies": [], "unknown": False}
    states: dict[str, str] = {}
    ready = 0
    for dependency_id in ids:
        candidate = database.row("candidates", "candidate_id", dependency_id)
        if candidate is not None:
            state = str(candidate.get("status") or "UNKNOWN").upper()
            states[dependency_id] = state
            if state == "EXECUTED":
                ready += 1
            continue
        mission = database.row("missions", "mission_id", dependency_id)
        state = str((mission or {}).get("state") or "MISSING").upper()
        states[dependency_id] = state
        if state in {"COMPLETED", "ACCEPTED", "PASSED"}:
            ready += 1
    return ready / len(ids), {"dependencies": list(ids), "states": states, "unknown": False}


def _history_state(database: FoundryDatabase, row: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(row.get("candidate_id") or "")
    fingerprint = str(row.get("fingerprint") or "")
    canonical = str(row.get("objective_canonical") or "")
    clauses: list[str] = []
    params: list[Any] = [candidate_id]
    if fingerprint:
        clauses.append("fingerprint = ?")
        params.append(fingerprint)
    if canonical:
        clauses.append("objective_canonical = ?")
        params.append(canonical)
    if not clauses:
        return {"rejected": [], "completed": [], "active": [], "superseded": []}
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT candidate_id, status FROM candidates WHERE candidate_id != ? "
            f"AND ({' OR '.join(clauses)}) ORDER BY rowid DESC LIMIT 50",
            tuple(params),
        ).fetchall()
    result = {"rejected": [], "completed": [], "active": [], "superseded": []}
    for prior in rows:
        status = str(prior["status"] or "").upper()
        if status == "REJECTED":
            result["rejected"].append(str(prior["candidate_id"]))
        elif status == "EXECUTED":
            result["completed"].append(str(prior["candidate_id"]))
        elif status == "APPROVED":
            result["active"].append(str(prior["candidate_id"]))
        elif status == "SUPERSEDED":
            result["superseded"].append(str(prior["candidate_id"]))
    return result


def _autonomous_wip(database: FoundryDatabase) -> int | None:
    try:
        from .autonomy import rebuild_executive_state
        state = rebuild_executive_state(database)
    except Exception:
        return None
    return sum(
        objective.status in {"ACTIVE", "RUNNING", "BLOCKED", "WAITING"}
        and str(objective.owner_authority or "owner").lower() != "owner"
        for objective in state.objectives.values()
    )


def _candidate_expiry(
    database: FoundryDatabase,
    row: dict[str, Any],
    ttl_hours: float,
) -> tuple[str | None, bool]:
    expires_at = str(row.get("expires_at") or "").strip()
    if not expires_at:
        try:
            with database.connect(readonly=True) as connection:
                source = connection.execute(
                    "SELECT MIN(observed_at) FROM candidate_sources "
                    "WHERE candidate_id = ?",
                    (str(row.get("candidate_id") or ""),),
                ).fetchone()
            if source is not None and source[0]:
                from datetime import timedelta
                observed = datetime.fromisoformat(str(source[0]).replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                expires_at = (observed + timedelta(hours=ttl_hours)).isoformat().replace(
                    "+00:00", "Z"
                )
        except (ValueError, TypeError):
            expires_at = ""
    if not expires_at:
        return None, False
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires_at, expires <= datetime.now(timezone.utc)
    except ValueError:
        return expires_at, True


def _research_factors(
    research: dict[str, Any] | None,
) -> tuple[float | None, float | None, dict[str, Any]]:
    if research is None:
        return None, None, {"status": "NOT_RESEARCHED"}
    status = str(research.get("status") or "UNKNOWN").upper()
    evidence = research.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    content_hash = str(research.get("content_hash") or "")
    if status != "COMPLETED":
        return 0.0 if status in {"FAILED", "BLOCKED"} else None, None, {
            "status": status,
            "research_id": research.get("research_id"),
            "budget_used": research.get("budget_used"),
        }
    authority_ok = str(evidence.get("authority") or "").upper() == "DATA_ONLY"
    hash_ok = len(content_hash) == 64 and all(ch in "0123456789abcdef" for ch in content_hash.lower())
    quality = sum((bool(evidence), authority_ok, hash_ok)) / 3.0
    confidence: float | None = None
    try:
        if evidence.get("confidence") is not None:
            value = float(evidence["confidence"])
            confidence = max(0.0, min(1.0, value)) if isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        confidence = None
    return quality, confidence, {
        "status": status,
        "research_id": research.get("research_id"),
        "budget_used": research.get("budget_used"),
        "source_ref": research.get("source_ref"),
        "content_hash": content_hash or None,
        "authority": evidence.get("authority"),
    }




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
    a2_mode: bool = False,
    governor: A2GovernorBounds | None = None,
    owner_context: dict[str, Any] | None = None,
    fabric_snapshot: dict[str, dict[str, Any]] | None = None,
    research_snapshot: dict[str, dict[str, Any]] | None = None,
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
    a2_metadata: dict[str, Any] = {}
    a2_adjustment = 0.0
    if a2_mode:
        governor = governor or A2GovernorBounds()
        if owner_context is None:
            from .owner_context import load_owner_context
            owner_context = load_owner_context(database)
        fabric_snapshot = fabric_snapshot if fabric_snapshot is not None else _fabric_snapshot(database)

        context_goal = _best_context_overlap(
            objective, list(owner_context.get("goals") or ()))
        project_text = " ".join(filter(None, (
            objective,
            resource_target,
            str(_context_value(row, "target_project", "project_id", "target_system") or ""),
        )))
        project_relevance = _best_context_overlap(
            project_text, list(owner_context.get("projects") or ()))
        standing_rejection = _best_context_overlap(
            objective, list(owner_context.get("standing_rejections") or ()))

        required = _required_capabilities(row)
        capability_states: dict[str, str] = {}
        unattended_states: dict[str, str] = {}
        capability_values: list[float] = []
        for capability_id in required:
            catalog_row = fabric_snapshot.get(capability_id)
            if catalog_row is None:
                capability_states[capability_id] = "MISSING"
                unattended_states[capability_id] = "UNKNOWN"
                capability_values.append(0.0)
                continue
            readiness = str(catalog_row["readiness"].get("state") or "UNKNOWN")
            unattended = str(catalog_row["spec"].get("unattended") or "UNKNOWN")
            capability_states[capability_id] = readiness
            unattended_states[capability_id] = unattended
            ready_value = 1.0 if readiness == "READY" else 0.25 if readiness == "DEGRADED" else 0.0
            if unattended == "INTERACTIVE_REQUIRED":
                ready_value = 0.0
            elif unattended == "UNKNOWN":
                ready_value = min(ready_value, 0.25)
            capability_values.append(ready_value)
        capability_readiness = (
            sum(capability_values) / len(capability_values) if capability_values else None
        )

        dependency_readiness, dependency_meta = _dependency_state(database, row)
        history = _history_state(database, row)
        novelty = 0.0 if history["completed"] or history["rejected"] else (
            0.25 if history["superseded"] else 1.0
        )
        wip = _autonomous_wip(database)
        wip_headroom = (
            None if wip is None else
            (1.0 if wip < governor.autonomous_wip_cap else 0.0)
        )

        opportunity_raw, _ = _weight("opportunity_cost", None)
        opportunity_headroom = (
            None if opportunity_raw is None else 1.0 - max(0.0, min(1.0, opportunity_raw))
        )
        requested_budget = _context_value(row, "research_budget", "estimated_research_cost")
        budget_headroom: float | None = None
        if requested_budget is not None:
            try:
                request_value = float(requested_budget)
                budget_headroom = max(
                    0.0,
                    min(1.0, 1.0 - request_value / max(governor.research_budget_per_candidate, 1e-9)),
                )
            except (TypeError, ValueError, OverflowError):
                budget_headroom = None

        research_quality, research_confidence, research_meta = _research_factors(
            (research_snapshot or {}).get(candidate_id)
        )

        a2_factors = [
            _centered_factor("owner_context_goal_relevance", context_goal, 0.08),
            _centered_factor("active_project_relevance", project_relevance, 0.07),
            _centered_factor("fabric_capability_readiness", capability_readiness, 0.08),
            _centered_factor("dependency_readiness", dependency_readiness, 0.05),
            _centered_factor("novelty_history", novelty, 0.05),
            _centered_factor("standing_rejection_clear", None if standing_rejection is None else 1.0 - standing_rejection, 0.08),
            _centered_factor("autonomous_wip_headroom", wip_headroom, 0.05),
            _centered_factor("research_budget_headroom", budget_headroom, 0.04),
            _centered_factor("opportunity_cost_headroom", opportunity_headroom, 0.06),
            _centered_factor("research_evidence_quality", research_quality, 0.06),
            _centered_factor("research_confidence", research_confidence, 0.05),
        ]
        factor_values.extend(a2_factors)
        a2_adjustment = round(sum(item.contribution for item in a2_factors), 4)
        expires_at, expired = _candidate_expiry(
            database, row, governor.candidate_ttl_hours,
        )
        a2_metadata = {
            "owner_context": {
                "goal_relevance": context_goal,
                "project_relevance": project_relevance,
                "standing_rejection_overlap": standing_rejection,
                "provenance": [
                    {"source_ref": item.get("source_ref"), "source_hash": item.get("source_hash")}
                    for kind in ("goals", "projects", "standing_rejections")
                    for item in (owner_context.get(kind) or ())
                    if isinstance(item, dict)
                ],
                "authority": owner_context.get("authority"),
            },
            "required_capabilities": list(required),
            "capability_states": capability_states,
            "unattended_states": unattended_states,
            "dependencies": dependency_meta,
            "history": history,
            "autonomous_wip": wip,
            "autonomous_wip_cap": governor.autonomous_wip_cap,
            "research_budget_requested": requested_budget,
            "research_budget_per_candidate": governor.research_budget_per_candidate,
            "expires_at": expires_at,
            "expired": expired,
            "research": research_meta,
            "duplicate_of": row.get("duplicate_of"),
            "do_nothing_score": governor.do_nothing_score,
        }
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
    composite += a2_adjustment
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
        a2_metadata=a2_metadata,
    )


def rank_candidates(
    database: FoundryDatabase,
    rows: list[dict[str, Any]],
    *,
    goals: list[StructuredGoal] | None = None,
    calibration: CalibrationState | None = None,
    resource_contention: float = 0.0,
    a2_mode: bool = False,
    governor: A2GovernorBounds | None = None,
    owner_context: dict[str, Any] | None = None,
) -> list[CandidateScore]:
    """Rank eligible candidates by composite executive score."""
    calibration = calibration if calibration is not None else rebuild_calibration(database)
    if a2_mode and owner_context is None:
        from .owner_context import load_owner_context
        owner_context = load_owner_context(database)
    fabric_snapshot = _fabric_snapshot(database) if a2_mode else None
    research_snapshot: dict[str, dict[str, Any]] | None = None
    if a2_mode:
        from ..research_queue import candidate_research_snapshot
        research_snapshot = candidate_research_snapshot(
            database, (str(row.get("candidate_id") or "") for row in rows)
        )
    scored = [
        score_candidate_for_executive(
            database, row, goals=goals, calibration=calibration,
            resource_contention=resource_contention,
            a2_mode=a2_mode,
            governor=governor,
            owner_context=owner_context,
            fabric_snapshot=fabric_snapshot,
            research_snapshot=research_snapshot,
        )
        for row in rows
    ]
    scored.sort(key=lambda s: (-s.composite, -s.goal_fit, s.candidate_id))
    return scored
