"""V3 Discovery and Building: deterministic scoring service (WP2).

The ScoringService reads the versioned scoring policy from the
``scoring_policy_registry`` projection and produces a fully auditable,
byte-stable score trace per candidate. Hard filters REJECT; they never
become negative weights. Determinism contract: same ledger state +
same policy version -> identical trace bytes.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from .database import FoundryDatabase
from .events import Event
from .knowledge import canonicalize_statement

POLICY_VERSION = "v3-policy-2026-08-21"

# Dimension order is part of the determinism contract.
DIMENSIONS = (
    "strategic_fit", "expected_value", "information_gain", "urgency",
    "confidence", "effort", "cost", "risk", "reversibility",
    "opportunity_cost",
)

VALUE_TERMS = ("fix", "fail", "incident", "outage", "risk", "security",
               "revenue", "customer", "latency", "regression", "crash",
               "data loss", "compliance")
INFORMATION_TERMS = ("unknown", "unclear", "investigate", "why",
                     "root cause", "reproduce")
URGENCY_TERMS = ("now", "urgent", "tonight", "today", "blocked", "deadline")
EFFORT_TERMS = ("rename", "typo", "cosmetic", "document", "label")
COST_TERMS = ("migrate", "refactor", "rewrite", "replatform")

CIRCULAR_PATTERNS = ("improve the improvement process",
                     "optimize this scoring policy",
                     "select this candidate repeatedly")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def fingerprint_for(objective: str, candidate_type: str,
                    primary_entity: str = "") -> str:
    """Stable content fingerprint: canonical objective + type + entity."""
    material = "\x1f".join((
        canonicalize_statement(objective), candidate_type, primary_entity))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def derive_dimensions(objective: str, *, evidence_count: int = 0,
                      strategic_fit: float | None = None,
                      source_trust: float = 1.0) -> dict[str, float]:
    """Deterministic dimension derivation from the objective text.

    ``strategic_fit`` accepts an externally computed goal-alignment
    score (executive/goals.py); when absent, the MVP fallback derives
    it from the objective's own value/urgency/evidence signals. Every
    value clamped to [0, 1]. Evidence caps confidence at
    min(1, len(evidence)/3) scaled by source trust (policy §3).
    """
    lower = objective.lower()
    value_count = sum(t in lower for t in VALUE_TERMS)
    info_count = sum(t in lower for t in INFORMATION_TERMS)
    urgency_count = sum(t in lower for t in URGENCY_TERMS)
    fit = strategic_fit if strategic_fit is not None else _clamp01(
        0.30 * value_count + 0.20 * urgency_count
        + (0.10 if evidence_count > 0 else 0.0))
    dims = {
        "strategic_fit": fit,
        "expected_value": _clamp01(0.25 + 0.20 * value_count),
        "information_gain": _clamp01(0.15 + 0.25 * info_count),
        "urgency": _clamp01(0.10 + 0.25 * urgency_count),
        "confidence": _clamp01(min(1.0, evidence_count / 3.0))
        * _clamp01(source_trust),
        "effort": _clamp01(0.1 * sum(t in lower for t in EFFORT_TERMS)),
        "cost": _clamp01(0.1 * sum(t in lower for t in COST_TERMS)),
        "risk": 0.15,
        "reversibility": 0.85,
        "opportunity_cost": 0.10,
    }
    return {k: round(v, 4) for k, v in dims.items()}


class HardFilterError(ValueError):
    """A hard eligibility filter rejected the candidate."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
        self.reason_code = reason_code


def check_hard_filters(candidate: dict[str, Any], policy: dict[str, Any],
                       *, stop_reason: str | None = None,
                       veto_reason: str | None = None) -> list[str]:
    """Evaluate hard filters in fixed order; return reason codes.

    Raises nothing: callers decide rejection vs exception. An empty
    result means the candidate may proceed to scoring.
    """
    reasons: list[str] = []
    if stop_reason:
        reasons.append("HARD_STOP")
    if veto_reason:
        reasons.append("HARD_VETO")
    if not str(candidate.get("objective") or "").strip():
        reasons.append("HARD_UNMEASURABLE")
    if not candidate.get("expected_outcome"):
        reasons.append("HARD_UNMEASURABLE")
    if len(candidate.get("evidence_refs") or []) < int(
            policy.get("hard_filters", {}).get("min_evidence_refs", 1)):
        reasons.append("HARD_NO_EVIDENCE")
    lowered = str(candidate.get("objective") or "").lower()
    if any(pattern in lowered for pattern in CIRCULAR_PATTERNS):
        reasons.append("HARD_CIRCULAR")
    blast_radius = float(policy.get("hard_filters", {}).get(
        "max_blast_radius", 0.85))
    if _clamp01(float(candidate.get("blast_radius") or 0.0)) > blast_radius:
        reasons.append("HARD_BLAST")
    return reasons


def load_active_policy(db: FoundryDatabase) -> dict[str, Any]:
    """Read the active policy row from the projection (fail-closed)."""
    with db.connect() as connection:
        row = connection.execute(
            "SELECT policy_version, weights_json, thresholds_json, "
            "hard_filters_json FROM scoring_policy_registry "
            "WHERE active = 1 ORDER BY updated_at DESC LIMIT 1").fetchone()
    if row is None:
        raise ValueError(
            "no active scoring policy registered — register a policy via "
            "register_policy() before scoring (fail-closed)")
    return {
        "policy_version": row[0],
        "weights": _load_json(row[1]),
        "thresholds": _load_json(row[2]),
        "hard_filters": _load_json(row[3]),
    }


def _load_json(raw: str | None) -> dict[str, Any]:
    import json
    try:
        return json.loads(raw or "{}")
    except ValueError:
        return {}


def register_policy(db: FoundryDatabase, policy: dict[str, Any]) -> None:
    """Append ``scoring.policy_registered`` for the given policy payload."""
    version = policy.get("policy_version") or POLICY_VERSION
    db.append(Event.create(
        "scoring.policy_registered",
        {"policy_version": version,
         "weights": policy.get("weights", {}),
         "thresholds": policy.get("thresholds", {}),
         "hard_filters": policy.get("hard_filters", {})},
        source_type="supervisor", source_id="v3-scoring",
        idempotency_key=f"scoring.policy:{version}"))


class ScoringService:
    """Registry-driven deterministic scoring over candidates."""

    def __init__(self, db: FoundryDatabase) -> None:
        self._db = db

    def score_candidate(self, candidate_id: str, *,
                        dimensions: dict[str, float] | None = None,
                        stop_reason: str | None = None,
                        veto_reason: str | None = None) -> dict[str, Any]:
        """Score one candidate under the active policy.

        Emits ``discovery.candidate_scored`` (with policy_version + full
        trace) on pass — flipping the projection to ELIGIBLE — or
        ``discovery.candidate_rejected`` with the filter reason codes.
        Returns the decision dict either way.

        SCORED/ELIGIBLE candidates are frozen: re-scoring returns the
        recorded decision without appending (idempotent replay).
        """
        policy = load_active_policy(self._db)
        with self._db.connect() as connection:
            row = connection.execute(
                "SELECT status, scoring_policy_version FROM candidates "
                "WHERE candidate_id = ?", (candidate_id,)).fetchone()
            prior_score = connection.execute(
                "SELECT score, weights_json FROM candidate_scores "
                "WHERE candidate_id = ?", (candidate_id,)).fetchone()
            if row is None:
                raise ValueError(
                    f"score_candidate requires an existing candidate "
                    f"({candidate_id}) — rolled back")
            status = row[0]
            if status in ("SCORED", "ELIGIBLE") and prior_score is not None:
                import json
                trace = _load_json(prior_score[1])
                trace.setdefault("policy_version", row[1])
                return {"candidate_id": candidate_id, "status": status,
                        "total": float(prior_score[0]), "trace": trace,
                        "appended": False}
            if status in ("REJECTED", "SUPERSEDED", "EXECUTED", "APPROVED",
                          "DEFERRED"):
                # Terminal/committed lifecycle states are frozen: no
                # re-scoring, no appended events, honest replay.
                return {"candidate_id": candidate_id, "status": status,
                        "total": None, "trace": {}, "appended": False}
            candidate = self._candidate_state(connection, candidate_id)
        dims = dimensions or derive_dimensions(
            candidate["objective"],
            evidence_count=len(candidate["evidence_refs"]),
            source_trust=1.0)

        reject_reasons = check_hard_filters(
            candidate, policy, stop_reason=stop_reason, veto_reason=veto_reason)
        if reject_reasons:
            reason_code = reject_reasons[0]
            self._db.append(Event.create(
                "discovery.candidate_rejected",
                {"candidate_id": candidate_id,
                 "reason": "; ".join(reject_reasons),
                 "filter_or_score": "filter",
                 "reason_code": reason_code},
                source_type="supervisor", source_id="v3-scoring",
                idempotency_key=f"discovery.candidate_rejected:{candidate_id}:{reason_code}"))
            return {"candidate_id": candidate_id, "status": "REJECTED",
                    "reason_codes": reject_reasons, "appended": True}

        total = compute_total(dims, policy["weights"])
        threshold = float(policy.get("thresholds", {}).get(
            "min_total_score", 0.40))
        below = total < threshold
        trace = {
            "policy_version": policy["policy_version"],
            "dimensions": dims,
            "weights": policy["weights"],
            "calibration_adjust": 0.0,
            "hard_filters": [],
            "thresholds": policy.get("thresholds", {}),
            "below_threshold": below,
        }
        self._db.append(Event.create(
            "discovery.candidate_scored",
            {"candidate_id": candidate_id,
             "score": total,
             "policy_version": policy["policy_version"],
             "weights": policy["weights"],
             "dimensions": dims,
             "trace": trace,
             "hard_filters_passed": True,
             "fingerprint": candidate["fingerprint"] or fingerprint_for(
                 candidate["objective"], candidate.get("candidate_type") or "repair"),
             "candidate_type": candidate.get("candidate_type"),
             "expected_outcome": candidate.get("expected_outcome"),
             "provenance_hash": candidate.get("provenance_hash"),
             "expires_at": candidate.get("expires_at")},
            source_type="supervisor", source_id="v3-scoring",
            idempotency_key=f"discovery.candidate_scored:{candidate_id}:{policy['policy_version']}"))
        final_status = "ELIGIBLE"
        if below:
            # Score recorded, portfolio bar not met: stays visible but
            # BELOW_THRESHOLD-rejected for selection purposes.
            self._db.append(Event.create(
                "discovery.candidate_rejected",
                {"candidate_id": candidate_id,
                 "reason": f"BELOW_THRESHOLD score={total}",
                 "filter_or_score": "score",
                 "reason_code": "BELOW_THRESHOLD"},
                source_type="supervisor", source_id="v3-scoring",
                idempotency_key=f"discovery.candidate_rejected:{candidate_id}:BELOW_THRESHOLD"))
            final_status = "REJECTED"
        return {"candidate_id": candidate_id, "status": final_status,
                "total": total, "trace": trace, "appended": True}

    def score_all_eligible(self, *, limit: int = 50,
                           stop_reason: str | None = None,
                           veto_reason: str | None = None) -> list[dict[str, Any]]:
        """Score NEW candidates oldest-first (bounded)."""
        with self._db.connect() as connection:
            rows = connection.execute(
                "SELECT candidate_id FROM candidates WHERE status = 'NEW' "
                "ORDER BY rowid LIMIT ?", (int(limit),)).fetchall()
        return [self.score_candidate(r[0], stop_reason=stop_reason,
                                     veto_reason=veto_reason) for r in rows]

    @staticmethod
    def _candidate_state(connection: sqlite3.Connection,
                         candidate_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT objective, evidence_json, fingerprint, candidate_type, "
            "expected_outcome, provenance_hash, expires_at FROM candidates "
            "WHERE candidate_id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise ValueError(f"missing candidate row ({candidate_id})")
        import json
        try:
            evidence = json.loads(row[1] or "[]")
        except ValueError:
            evidence = []
        refs = [item for item in evidence if isinstance(item, dict)]
        return {
            "objective": row[0],
            "evidence_refs": refs,
            "fingerprint": row[2],
            "candidate_type": row[3],
            "expected_outcome": row[4],
            "provenance_hash": row[5],
            "expires_at": row[6],
        }


def compute_total(dimensions: dict[str, float],
                  weights: dict[str, float]) -> float:
    """Policy formula: positives minus penalties, clamped to [0, 1]."""
    positive_weights = ("strategic_fit", "expected_value", "information_gain",
                        "urgency", "confidence", "reversibility")
    negative_weights = ("effort", "cost", "risk", "opportunity_cost")
    positive = sum(weights.get(w, 0.0) * dimensions.get(w, 0.0)
                   for w in positive_weights)
    negative = sum(weights.get(w, 0.0) * dimensions.get(w, 0.0)
                   for w in negative_weights)
    return round(_clamp01(positive - negative), 4)


def evaluate_portfolio(db: FoundryDatabase, loop_id: str,
                       decisions: list[dict[str, Any]], *,
                       policy_version: str | None = None) -> dict[str, Any]:
    """Emit ``portfolio.evaluated`` with honest counts from decisions."""
    eligible = sum(1 for d in decisions if d["status"] == "ELIGIBLE")
    rejected = sum(1 for d in decisions if d["status"] == "REJECTED")
    deferred = sum(1 for d in decisions if d["status"] == "DEFERRED")
    payload = {
        "loop_id": loop_id,
        "policy_version": policy_version or POLICY_VERSION,
        "eligible_count": eligible,
        "deferred_count": deferred,
        "rejected_count": rejected,
        "capacity_snapshot": {"decisions": len(decisions)},
    }
    db.append(Event.create(
        "portfolio.evaluated", payload,
        source_type="supervisor", source_id="v3-portfolio",
        idempotency_key=f"portfolio:{loop_id}:{payload['policy_version']}"))
    return payload
