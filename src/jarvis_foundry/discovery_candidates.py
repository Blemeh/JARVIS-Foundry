"""V3 slice-1 (WP10): discovery candidate pipeline.

Bounded, deterministic, test-driven:
- generate_candidate: records the candidate WITH evidence; duplicate
  detection via the canonicalized objective against existing candidates
  AND missions (a duplicate records duplicate_of and creates nothing).
- score_candidate: calibrated deterministic scoring from the registry
  weights (value/information-gain/urgency/confidence weighted; effort/
  cost/risk subtracted; all normalized) — same inputs, same score.
- approve_candidate: validates the candidate exists and is scored,
  appends discovery.candidate_approved + mission.created (the mission
  carries the candidate_id).
- reject_candidate / record_outcome: status transitions with reasons
  (outcome tracking: prediction vs reality recorded in evidence)."""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from typing import Any

from .database import FoundryDatabase
from .events import Event
from .knowledge import canonicalize_statement

# Registry-driven calibrated weights (deterministic; values 0..1)
VALUE_TERMS = ("fix", "fail", "incident", "outage", "risk", "security",
               "revenue", "customer", "latency", "regression", "crash",
               "data loss", "compliance")
INFORMATION_TERMS = ("unknown", "unclear", "investigate", "why", "root cause",
                     "reproduce")
URGENCY_TERMS = ("now", "urgent", "tonight", "today", "blocked", "deadline")
EFFORT_TERMS = ("rename", "typo", "cosmetic", "document", "label")
_COSTLY_TERMS = ("migrate", "refactor", "rewrite", "replatform")


def _count_terms(objective: str, terms: tuple[str, ...]) -> int:
    lower = objective.lower()
    return sum(1 for term in terms if term in lower)


def score_objective(objective: str, *, age_hours: float = 0.0,
                    previous_attempts: int = 0, source_trust: float = 1.0,
                    evidence_items: int = 0,
                    similar_missions: int = 0) -> tuple[float, dict[str, Any]]:
    """Calibrated deterministic score in [0, 1] + the weights used.

    V4-S6 (DIR-2026-08-12 item 7): bounded context factors —
    freshness (exponential decay, 1-week horizon), cooldown for
    previous attempts (cap 5), source trust (multiplier around 1.0),
    evidence richness (cap 5 items) and mission-similarity penalty
    (cap 3). Every factor is clamped; the base [0,1] calibration is
    unchanged; thresholds are never lowered (the loop's min_score
    stays 0.35 — the selection bar is the same, the ORDER is fairer).
    """
    value = min(1.0, 0.15 + 0.12 * _count_terms(objective, VALUE_TERMS))
    information = min(1.0, 0.1 + 0.2 * _count_terms(objective, INFORMATION_TERMS))
    urgency = min(1.0, 0.05 + 0.2 * _count_terms(objective, URGENCY_TERMS))
    effort_penalty = min(0.3, 0.1 * _count_terms(objective, EFFORT_TERMS))
    cost_penalty = min(0.3, 0.1 * _count_terms(objective, _COSTLY_TERMS))
    freshness = math.exp(-max(0.0, float(age_hours)) / 168.0)  # 1-week decay
    freshness_bonus = round(0.05 * freshness, 4)
    cooldown = round(0.03 * min(max(0, int(previous_attempts)), 5), 4)
    trust = max(0.5, min(1.0, float(source_trust)))
    trust_adjust = round((trust - 1.0) * 0.04, 4)
    evidence_bonus = round(0.01 * min(max(0, int(evidence_items)), 5), 4)
    similarity = round(0.02 * min(max(0, int(similar_missions)), 3), 4)
    score = round(max(0.0, min(1.0, 0.55 * value + 0.2 * information +
                               0.25 * urgency - effort_penalty -
                               cost_penalty + freshness_bonus +
                               evidence_bonus - cooldown - similarity +
                               trust_adjust)), 4)
    weights = {"value": value, "information_gain": information,
               "urgency": urgency, "effort_penalty": effort_penalty,
               "cost_penalty": cost_penalty,
               "freshness": freshness_bonus, "cooldown_penalty": cooldown,
               "source_trust": trust, "evidence_bonus": evidence_bonus,
               "similarity_penalty": similarity}
    return score, weights


def covered_objective(
    db: FoundryDatabase, objective: str,
    *, connection: sqlite3.Connection | None = None,
) -> str | None:
    """Whether the canonicalized objective is already covered by an
    existing candidate or mission (V4-S5 round 2: the poll feed uses
    this so repeated polls can never increase candidates). Returns the
    covering candidate/mission id, or None."""
    return _find_duplicate(db, canonicalize_statement(objective), connection=connection)


def _find_duplicate(
    db: FoundryDatabase, canonical: str,
    *, connection: sqlite3.Connection | None = None,
) -> str | None:
    def find(current: sqlite3.Connection) -> str | None:
        row = current.execute(
            "SELECT candidate_id FROM candidates "
            "WHERE objective_canonical = ? ORDER BY rowid LIMIT 1",
            (canonical,)).fetchone()
        if row:
            return row[0]
        # missions store RAW objectives — compare canonicalized in Python
        for mission in current.execute(
                "SELECT mission_id, objective FROM missions "
                "ORDER BY rowid").fetchall():
            if canonicalize_statement(mission[1]) == canonical:
                return mission[0]
        return None

    if connection is not None:
        return find(connection)
    with db.connect() as current:
        return find(current)


def generate_candidate(db: FoundryDatabase, mission_id: str, source: str,
                       objective: str, evidence: list[dict[str, Any]] | None = None,
                       *, connection: sqlite3.Connection | None = None,
                       typed: dict[str, Any] | None = None,
                       ) -> dict[str, Any]:
    """Record a candidate; duplicate detection via the canonicalized
    objective (vs candidates AND missions). Returns
    {candidate_id, created, duplicate_of}.

    V3 WP1: ``typed`` optionally carries fingerprint / candidate_type /
    expected_outcome / provenance_hash / expires_at so the projection
    stores the full typed record. Absent fields stay NULL (v34
    behavior unchanged).
    """
    from .executive.eligibility import stamp_executive_eligibility

    canonical = canonicalize_statement(objective)
    duplicate = _find_duplicate(db, canonical, connection=connection)
    candidate_id = f"cand-{uuid.uuid4().hex[:12]}"
    payload: dict[str, Any] = {
        "candidate_id": candidate_id, "mission_id": mission_id,
        "source": source, "objective": objective,
        "objective_canonical": canonical,
        "evidence": list(evidence or []),
        "duplicate_of": duplicate}
    for field in ("fingerprint", "candidate_type", "expected_outcome",
                  "provenance_hash", "expires_at"):
        value = (typed or {}).get(field)
        if value is not None:
            payload[field] = value
    event = Event.create(
        "discovery.candidate_generated",
        payload,
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.candidate_generated:{candidate_id}")
    if connection is None:
        db.append(event)
        stamp_executive_eligibility(
            db, evidence, source=source, candidate_id=candidate_id,
        )
    else:
        db.append_events_on_connection(connection, [event])
        stamp_executive_eligibility(
            db, evidence, source=source, candidate_id=candidate_id,
        )
    return {"candidate_id": candidate_id, "created": duplicate is None,
            "duplicate_of": duplicate}


def supersede_candidate(db: FoundryDatabase, candidate_id: str, *,
                        superseded_by: str | None = None,
                        reason: str = "") -> None:
    """Additively supersede a candidate whose premise was re-measured
    false (V4-S1 / DIR-2026-08-12 item 5: preserve history, supersede
    polluted projections — never delete, never rewrite). The projection
    flips the status to SUPERSEDED; selection scans NEW/SCORED only, so
    a superseded candidate is never executed. Idempotent: identical
    re-delivery dedupes on the event key."""
    db.append(Event.create(
        "discovery.candidate_superseded",
        {"candidate_id": candidate_id, "superseded_by": superseded_by,
         "reason": reason},
        source_type="supervisor", source_id="knowledge-hygiene",
        idempotency_key=f"discovery.candidate_superseded:{candidate_id}"))


def score_candidate(db: FoundryDatabase, candidate_id: str) -> float:
    """Deterministic calibrated scoring; stores the weights.

    V4-S6 context (bounded and fair, DIR-2026-08-12 item 7): the score
    is computed from the candidate's OWN state — age (freshness decay),
    source (trust), evidence richness, previous attempts (cooldown:
    same-objective missions/candidates) and similar missions. SCORED
    candidates are FROZEN: the recorded score is returned and nothing
    is re-appended — context drift (age, new attempts) can never
    re-score a recorded candidate, so the live loop's per-cycle
    re-scan cannot EventConflict (the scored event is append-once).
    """
    with db.connect() as connection:
        row = connection.execute(
            "SELECT score FROM candidate_scores WHERE candidate_id = ?",
            (candidate_id,)).fetchone()
        if row is not None:
            return float(row[0])  # frozen: historical score, additive
        candidate = connection.execute(
            "SELECT objective, source, evidence_json FROM candidates "
            "WHERE candidate_id = ?", (candidate_id,)).fetchone()
    if candidate is None:
        raise ValueError(f"score_candidate requires an existing candidate "
                         f"({candidate_id}) — rolled back")
    objective, source, evidence_json = (
        candidate[0], candidate[1], candidate[2] or "[]")
    context = _score_context(db, candidate_id, objective, source,
                             evidence_json)
    score, weights = score_objective(objective, **context)
    weights["context"] = {k: v for k, v in context.items()
                          if k != "source_trust"}
    db.append(Event.create(
        "discovery.candidate_scored",
        {"candidate_id": candidate_id, "score": score, "weights": weights},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.candidate_scored:{candidate_id}"))
    return score


_SOURCE_TRUST = {
    "vault": 1.0, "system": 1.0, "repo": 0.95, "handoffs": 0.9,
    "maintenance": 0.9, "research_queue": 0.85,
}


def _score_context(db: FoundryDatabase, candidate_id: str, objective: str,
                   source: str, evidence_json: str) -> dict[str, Any]:
    """The bounded, deterministic context factors for one candidate."""
    canonical = canonicalize_statement(objective)
    now_iso = _now_iso(db)
    created_at = None
    previous_attempts = 0
    similar = 0
    with db.connect() as connection:
        row = connection.execute(
            "SELECT recorded_at FROM events WHERE event_type = "
            "'discovery.candidate_generated' AND payload_json LIKE ? "
            "ORDER BY sequence LIMIT 1",
            (f'%"candidate_id":"{candidate_id}"%',)).fetchone()
        if row:
            created_at = row[0]
        for mission in connection.execute(
                "SELECT mission_id, objective FROM missions ORDER BY rowid"
        ).fetchall():
            mission_canonical = canonicalize_statement(mission[1])
            if mission_canonical == canonical:
                previous_attempts += 1
            elif _token_overlap(canonical, mission_canonical) >= 0.6:
                similar += 1
        executed = connection.execute(
            "SELECT objective_canonical FROM candidates WHERE status = "
            "'EXECUTED'").fetchall()
        for row in executed:
            if row[0] == canonical:
                previous_attempts += 1
    try:
        evidence_items = len(json.loads(evidence_json or "[]"))
    except (TypeError, ValueError):
        evidence_items = 0
    age_hours = 0.0
    if created_at:
        try:
            from datetime import datetime
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
            age_hours = max(0.0, (now - created).total_seconds() / 3600.0)
        except ValueError:
            age_hours = 0.0
    return {
        "age_hours": age_hours,
        "previous_attempts": min(previous_attempts, 5),
        "source_trust": _SOURCE_TRUST.get(source, 0.85),
        "evidence_items": min(evidence_items, 5),
        "similar_missions": min(similar, 3),
    }


def _now_iso(db: FoundryDatabase) -> str:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT recorded_at FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    return row[0] if row else "1970-01-01T00:00:00Z"


def _token_overlap(first: str, second: str) -> float:
    if not first or not second:
        return 0.0
    a = set(first.split())
    b = set(second.split())
    return len(a & b) / max(1.0, len(a))


def approve_candidate(db: FoundryDatabase, candidate_id: str,
                      role: str = "builder",
                      *,
                      resource_id: str | None = None,
                      next_action: str | None = None) -> str:
    """Validate (exists + scored) then approve AND create the mission.

    Optional ``resource_id`` / ``next_action`` bind the existing Operating
    Kernel resource without a second mission creator.
    """
    with db.connect() as connection:
        row = connection.execute(
            "SELECT objective, status FROM candidates WHERE candidate_id = ?",
            (candidate_id,)).fetchone()
        approval = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'discovery.candidate_approved' AND json_extract(payload_json, "
            "'$.candidate_id') = ? ORDER BY sequence LIMIT 1",
            (candidate_id,)).fetchone()
        mission_event = None
        if approval:
            import json
            approved_payload = json.loads(approval[0])
            approved_mission_id = str(approved_payload.get("mission_id") or "")
            if approved_mission_id:
                mission_event = connection.execute(
                    "SELECT 1 FROM events WHERE event_type = 'mission.created' "
                    "AND json_extract(payload_json, '$.mission_id') = ? LIMIT 1",
                    (approved_mission_id,),
                ).fetchone()
    from .executive.eligibility import ELIGIBLE_STATUSES
    approved_mission_id = ""
    if approval:
        import json
        approved_mission_id = str(json.loads(approval[0]).get("mission_id") or "")
    if row is None or (row[1] not in ELIGIBLE_STATUSES
                       and not (row[1] == "APPROVED" and approved_mission_id)):
        raise ValueError(
            f"approve_candidate requires an existing, scorable candidate "
            f"({candidate_id}, status={row[1] if row else 'MISSING'})")
    # Bind the mission identity to the candidate. This makes a retry after a
    # crash between approval and mission creation converge on one mission,
    # including concurrent materializers racing on the same decision.
    mission_id = approved_mission_id or f"mission-{candidate_id}"
    created: dict[str, Any] = {
        "mission_id": mission_id, "step_id": "v3-candidate",
        "objective": row[0], "role": role,
        "acceptance_criteria": [f"Complete: {row[0]}"],
        "created_from": f"candidate:{candidate_id}",
    }
    if resource_id:
        created["resource_id"] = resource_id
    if next_action:
        created["next_action"] = next_action
    events: list[Event] = []
    if not approval:
        events.append(Event.create(
            "discovery.candidate_approved",
            {"candidate_id": candidate_id, "mission_id": mission_id},
            source_type="supervisor", source_id="wp10-discovery",
            idempotency_key=f"discovery.candidate_approved:{candidate_id}"))
    if not mission_event:
        events.append(Event.create(
            "mission.created", created,
            source_type="supervisor", source_id="wp10-discovery",
            idempotency_key=f"mission.created:{mission_id}"))
    if events:
        db.append_events(events)
    return mission_id


def reject_candidate(db: FoundryDatabase, candidate_id: str,
                     reason: str) -> None:
    db.append(Event.create(
        "discovery.candidate_rejected",
        {"candidate_id": candidate_id, "reason": reason},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.candidate_rejected:{candidate_id}"))


def record_outcome(db: FoundryDatabase, candidate_id: str, mission_id: str,
                   outcome: dict[str, Any]) -> None:
    db.append(Event.create(
        "discovery.candidate_outcome_recorded",
        {"candidate_id": candidate_id, "mission_id": mission_id,
         "outcome": outcome},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.candidate_outcome_recorded:{candidate_id}"))
