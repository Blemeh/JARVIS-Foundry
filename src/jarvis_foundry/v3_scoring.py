"""V3 Discovery and Building: deterministic scoring service (WP2).

The ScoringService reads the versioned scoring policy from the
``scoring_policy_registry`` projection and produces a fully auditable,
byte-stable score trace per candidate. Hard filters REJECT; they never
become negative weights. Determinism contract: same ledger state +
same policy version -> identical trace bytes.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from datetime import datetime, timezone
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


def _validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Validate a registered policy before it can influence selection."""
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    version = policy.get("policy_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("policy_version is required")
    weights = policy.get("weights")
    if not isinstance(weights, dict):
        raise ValueError("policy weights must be an object")
    missing = sorted(set(DIMENSIONS) - set(weights))
    unknown = sorted(set(weights) - set(DIMENSIONS))
    if missing or unknown:
        raise ValueError(
            f"policy weights must contain exactly the scoring dimensions; "
            f"missing={missing}, unknown={unknown}")
    numeric_weights: dict[str, float] = {}
    for name in DIMENSIONS:
        try:
            value = float(weights[name])
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"policy weight {name!r} is not numeric") from error
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"policy weight {name!r} is invalid")
        numeric_weights[name] = value
    if abs(sum(numeric_weights.values()) - 1.0) > 1e-6:
        raise ValueError("policy weights must sum to 1.0")

    thresholds = policy.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("policy thresholds must be an object")
    required_thresholds = (
        "min_total_score", "min_strategic_fit", "min_confidence",
        "min_information_gain_for_research_override",
        "comparative_wait_margin",
    )
    normalized_thresholds: dict[str, float] = {}
    for name in required_thresholds:
        try:
            value = float(thresholds[name])
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"policy threshold {name!r} is invalid") from error
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"policy threshold {name!r} is outside [0, 1]")
        normalized_thresholds[name] = value

    hard_filters = policy.get("hard_filters")
    if not isinstance(hard_filters, dict):
        raise ValueError("policy hard_filters must be an object")
    try:
        max_blast = float(hard_filters["max_blast_radius"])
        min_evidence = int(hard_filters["min_evidence_refs"])
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("policy hard_filters are invalid") from error
    if not math.isfinite(max_blast) or not 0.0 <= max_blast <= 1.0:
        raise ValueError("policy max_blast_radius is outside [0, 1]")
    if min_evidence < 0:
        raise ValueError("policy min_evidence_refs must be non-negative")
    return {
        "policy_version": version,
        "weights": numeric_weights,
        "thresholds": normalized_thresholds,
        "hard_filters": {
            "max_blast_radius": max_blast,
            "min_evidence_refs": min_evidence,
        },
    }


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
                       veto_reason: str | None = None,
                       now: str | None = None) -> list[str]:
    """Evaluate hard filters in fixed order; return reason codes.

    Raises nothing: callers decide rejection vs exception. An empty
    result means the candidate may proceed to scoring.
    """
    reasons: list[str] = []
    if stop_reason:
        reasons.append("HARD_STOP")
    if veto_reason:
        reasons.append("HARD_VETO")
    if candidate.get("action_allowed") is False or candidate.get("illegal_action"):
        reasons.append("HARD_ILLEGAL")
    required = candidate.get("required_capabilities") or []
    readiness = candidate.get("capability_readiness") or {}
    if any(readiness.get(capability) not in ("READY", "ACTIVE")
           for capability in required):
        reasons.append("HARD_CAPABILITY")
    if candidate.get("severity_one_open"):
        reasons.append("HARD_DEFECT")
    try:
        blast_radius = float(candidate.get("blast_radius") or 0.0)
    except (TypeError, ValueError, OverflowError):
        blast_radius = 1.0
    if not math.isfinite(blast_radius):
        blast_radius = 1.0
    hard_filters = policy.get("hard_filters", {})
    try:
        max_blast = float(hard_filters.get("max_blast_radius", 0.85))
        min_evidence = int(hard_filters.get("min_evidence_refs", 1))
        if not math.isfinite(max_blast) or not 0.0 <= max_blast <= 1.0 or min_evidence < 0:
            raise ValueError
    except (AttributeError, TypeError, ValueError, OverflowError):
        reasons.append("HARD_POLICY")
        max_blast = 0.0
        min_evidence = 1
    if _clamp01(blast_radius) > max_blast:
        reasons.append("HARD_BLAST")
    expires_at = candidate.get("expires_at")
    if expires_at:
        try:
            expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            current = datetime.fromisoformat(
                str(now or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
            if expires <= current:
                reasons.append("HARD_EXPIRED")
        except (TypeError, ValueError, OverflowError):
            reasons.append("HARD_EXPIRED")
    if candidate.get("duplicate_unresolved") or candidate.get("unresolved_duplicate"):
        reasons.append("HARD_DUPLICATE")
    if not str(candidate.get("objective") or "").strip():
        reasons.append("HARD_UNMEASURABLE")
    if not str(candidate.get("expected_outcome") or "").strip():
        reasons.append("HARD_UNMEASURABLE")
    if len(candidate.get("evidence_refs") or []) < min_evidence:
        reasons.append("HARD_NO_EVIDENCE")
    lowered = str(candidate.get("objective") or "").lower()
    if any(pattern in lowered for pattern in CIRCULAR_PATTERNS):
        reasons.append("HARD_CIRCULAR")
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
    policy = {
        "policy_version": row[0],
        "weights": _load_json(row[1]),
        "thresholds": _load_json(row[2]),
        "hard_filters": _load_json(row[3]),
    }
    return _validate_policy(policy)


def _canonical_fabric_snapshot(db: FoundryDatabase) -> dict[str, dict[str, str]]:
    """Return one bounded Catalog snapshot for scoring readiness checks."""
    from .fabric.catalog import catalog_snapshot

    result: dict[str, dict[str, str]] = {}
    for row in catalog_snapshot(db).get("capabilities", ()):
        if not isinstance(row, dict):
            continue
        spec = row.get("spec") or {}
        readiness = row.get("readiness") or {}
        capability_id = str(spec.get("capability_id") or "")
        if capability_id:
            result[capability_id] = {
                "state": str(readiness.get("state") or "UNKNOWN"),
                "unattended": str(spec.get("unattended") or "UNKNOWN"),
            }
    return result


def _apply_fabric_truth(
    candidate: dict[str, Any],
    snapshot: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Replace any candidate-supplied readiness claims with Catalog truth."""
    required = tuple(str(value) for value in (candidate.get("required_capabilities") or ()))
    readiness: dict[str, str] = {}
    for capability_id in required:
        row = snapshot.get(capability_id)
        if row is None:
            readiness[capability_id] = "MISSING"
            continue
        state = row["state"]
        if row["unattended"] != "ALLOWED":
            state = "NOT_READY"
        readiness[capability_id] = state
    return {**candidate, "capability_readiness": readiness}


def _load_json(raw: str | None) -> dict[str, Any]:
    import json
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError) as error:
        raise ValueError("policy JSON is malformed") from error
    if not isinstance(value, dict):
        raise ValueError("policy JSON must contain an object")
    return value


def register_policy(db: FoundryDatabase, policy: dict[str, Any]) -> None:
    """Append ``scoring.policy_registered`` for the given policy payload."""
    normalized = _validate_policy({
        **policy,
        "policy_version": policy.get("policy_version") or POLICY_VERSION,
    })
    version = normalized["policy_version"]
    db.append(Event.create(
        "scoring.policy_registered",
        {"policy_version": version,
         "weights": normalized["weights"],
         "thresholds": normalized["thresholds"],
         "hard_filters": normalized["hard_filters"]},
        source_type="supervisor", source_id="v3-scoring",
        idempotency_key=f"scoring.policy:{version}"))


class ScoringService:
    """Registry-driven deterministic scoring over candidates."""

    def __init__(self, db: FoundryDatabase) -> None:
        self._db = db

    def score_candidate(self, candidate_id: str, *,
                        dimensions: dict[str, float] | None = None,
                        stop_reason: str | None = None,
                        veto_reason: str | None = None,
                        _fabric_snapshot: dict[str, dict[str, str]] | None = None,
                        ) -> dict[str, Any]:
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
        candidate = _apply_fabric_truth(
            candidate,
            _fabric_snapshot if _fabric_snapshot is not None
            else _canonical_fabric_snapshot(self._db),
        )
        dims = dimensions or derive_dimensions(
            candidate["objective"],
            evidence_count=len(candidate["evidence_refs"]),
            source_trust=1.0)

        reject_reasons = check_hard_filters(
            candidate, policy, stop_reason=stop_reason, veto_reason=veto_reason,
            now=_current_utc())
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
        threshold_failures: list[str] = []
        if total < threshold:
            threshold_failures.append("BELOW_THRESHOLD")
        if dims["strategic_fit"] < float(policy["thresholds"].get(
                "min_strategic_fit", 0.0)):
            threshold_failures.append("BELOW_STRATEGIC_FIT")
        if dims["confidence"] < float(policy["thresholds"].get(
                "min_confidence", 0.0)):
            threshold_failures.append("BELOW_CONFIDENCE")
        below = bool(threshold_failures)
        trace = {
            "policy_version": policy["policy_version"],
            "dimensions": dims,
            "weights": policy["weights"],
            "calibration_adjust": 0.0,
            "hard_filters": [],
            "thresholds": policy.get("thresholds", {}),
            "below_threshold": below,
            "threshold_failures": threshold_failures,
        }
        scored_event = Event.create(
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
            idempotency_key=f"discovery.candidate_scored:{candidate_id}:{policy['policy_version']}")
        final_status = "ELIGIBLE"
        events = [scored_event]
        if below:
            # Score recorded, portfolio bar not met: stays visible but
            # BELOW_THRESHOLD-rejected for selection purposes.
            events.append(Event.create(
                "discovery.candidate_rejected",
                {"candidate_id": candidate_id,
                 "reason": "; ".join(threshold_failures),
                 "filter_or_score": "score",
                 "reason_code": threshold_failures[0]},
                source_type="supervisor", source_id="v3-scoring",
                idempotency_key=f"discovery.candidate_rejected:{candidate_id}:BELOW_THRESHOLD"))
            final_status = "REJECTED"
        self._db.append_events(events)
        if final_status == "ELIGIBLE":
            # Admission is a post-filter, post-threshold operation. A stop
            # or veto that arrives while scoring is in flight must not grant
            # executive authority to a candidate that was not safe to admit.
            from .control.broker import emergency_stop_reason, matching_veto_reason
            from .executive.eligibility import (
                current_activation_epoch, is_candidate_admitted,
                admit_candidate)
            live_stop = emergency_stop_reason(self._db, None)
            live_veto = matching_veto_reason(self._db, {"GLOBAL", candidate_id})
            epoch = current_activation_epoch(self._db)
            if not live_stop and not live_veto and epoch and not is_candidate_admitted(
                    self._db, candidate_id, epoch):
                admit_candidate(
                    self._db, candidate_id, epoch=epoch,
                    source="v3-scoring")
        return {"candidate_id": candidate_id, "status": final_status,
                "reason_codes": threshold_failures,
                "total": total, "trace": trace, "appended": True}

    def score_all_eligible(self, *, limit: int = 50,
                           stop_reason: str | None = None,
                           veto_reason: str | None = None) -> list[dict[str, Any]]:
        """Score NEW candidates oldest-first (bounded)."""
        with self._db.connect() as connection:
            rows = connection.execute(
                "SELECT candidate_id FROM candidates WHERE status = 'NEW' "
                "ORDER BY rowid LIMIT ?", (int(limit),)).fetchall()
        fabric_snapshot = _canonical_fabric_snapshot(self._db)
        return [self.score_candidate(
            r[0], stop_reason=stop_reason, veto_reason=veto_reason,
            _fabric_snapshot=fabric_snapshot,
        ) for r in rows]

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
        extras: dict[str, Any] = {}
        event_row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'discovery.candidate_generated' AND json_extract(payload_json, "
            "'$.candidate_id') = ? ORDER BY sequence LIMIT 1",
            (candidate_id,),
        ).fetchone()
        if event_row:
            try:
                payload = json.loads(event_row[0])
                extras = {
                    key: payload.get(key)
                    for key in (
                        "required_capabilities", "capability_readiness",
                        "action_allowed", "illegal_action",
                        "severity_one_open", "duplicate_unresolved",
                    )
                    if key in payload
                }
                if "required_capabilities" not in extras:
                    for item in refs:
                        if item.get("required_capabilities") is not None:
                            extras["required_capabilities"] = item.get("required_capabilities")
                            break
            except (TypeError, ValueError):
                extras = {"malformed_payload": True}
        return {
            "objective": row[0],
            "evidence_refs": refs,
            "fingerprint": row[2],
            "candidate_type": row[3],
            "expected_outcome": row[4],
            "provenance_hash": row[5],
            "expires_at": row[6],
            **extras,
        }


def _current_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


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
