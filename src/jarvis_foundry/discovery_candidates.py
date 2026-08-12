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


def score_objective(objective: str) -> tuple[float, dict[str, Any]]:
    """Calibrated deterministic score in [0, 1] + the weights used."""
    value = min(1.0, 0.15 + 0.12 * _count_terms(objective, VALUE_TERMS))
    information = min(1.0, 0.1 + 0.2 * _count_terms(objective, INFORMATION_TERMS))
    urgency = min(1.0, 0.05 + 0.2 * _count_terms(objective, URGENCY_TERMS))
    effort_penalty = min(0.3, 0.1 * _count_terms(objective, EFFORT_TERMS))
    cost_penalty = min(0.3, 0.1 * _count_terms(objective, _COSTLY_TERMS))
    score = round(max(0.0, min(1.0, 0.55 * value + 0.2 * information +
                               0.25 * urgency - effort_penalty -
                               cost_penalty)), 4)
    weights = {"value": value, "information_gain": information,
               "urgency": urgency, "effort_penalty": effort_penalty,
               "cost_penalty": cost_penalty}
    return score, weights


def _find_duplicate(db: FoundryDatabase, canonical: str) -> str | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT candidate_id FROM candidates "
            "WHERE objective_canonical = ? ORDER BY rowid LIMIT 1",
            (canonical,)).fetchone()
        if row:
            return row[0]
        # missions store RAW objectives — compare canonicalized in Python
        for mission in connection.execute(
                "SELECT mission_id, objective FROM missions "
                "ORDER BY rowid").fetchall():
            if canonicalize_statement(mission[1]) == canonical:
                return mission[0]
        return None


def generate_candidate(db: FoundryDatabase, mission_id: str, source: str,
                       objective: str, evidence: list[dict[str, Any]] | None = None,
                       ) -> dict[str, Any]:
    """Record a candidate; duplicate detection via the canonicalized
    objective (vs candidates AND missions). Returns
    {candidate_id, created, duplicate_of}."""
    canonical = canonicalize_statement(objective)
    duplicate = _find_duplicate(db, canonical)
    candidate_id = f"cand-{uuid.uuid4().hex[:12]}"
    db.append(Event.create(
        "discovery.candidate_generated",
        {"candidate_id": candidate_id, "mission_id": mission_id,
         "source": source, "objective": objective,
         "objective_canonical": canonical,
         "evidence": evidence or [],
         "duplicate_of": duplicate},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.candidate_generated:{candidate_id}"))
    return {"candidate_id": candidate_id, "created": duplicate is None,
            "duplicate_of": duplicate}


def score_candidate(db: FoundryDatabase, candidate_id: str) -> float:
    """Deterministic calibrated scoring; stores the weights."""
    with db.connect() as connection:
        row = connection.execute(
            "SELECT objective FROM candidates WHERE candidate_id = ?",
            (candidate_id,)).fetchone()
    if row is None:
        raise ValueError(f"score_candidate requires an existing candidate "
                         f"({candidate_id}) — rolled back")
    score, weights = score_objective(row[0])
    db.append(Event.create(
        "discovery.candidate_scored",
        {"candidate_id": candidate_id, "score": score, "weights": weights},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.candidate_scored:{candidate_id}"))
    return score


def approve_candidate(db: FoundryDatabase, candidate_id: str,
                      role: str = "builder") -> str:
    """Validate (exists + scored) then approve AND create the mission."""
    with db.connect() as connection:
        row = connection.execute(
            "SELECT objective, status FROM candidates WHERE candidate_id = ?",
            (candidate_id,)).fetchone()
    if row is None or row[1] not in ("NEW", "SCORED"):
        raise ValueError(
            f"approve_candidate requires an existing, scorable candidate "
            f"({candidate_id}, status={row[1] if row else 'MISSING'})")
    mission_id = f"mission-{uuid.uuid4().hex[:12]}"
    db.append(Event.create(
        "discovery.candidate_approved",
        {"candidate_id": candidate_id, "mission_id": mission_id},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.candidate_approved:{candidate_id}"))
    db.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "step_id": "v3-candidate",
         "objective": row[0], "role": role,
         "acceptance_criteria": [f"Complete: {row[0]}"],
         "created_from": f"candidate:{candidate_id}"},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"mission.created:{mission_id}"))
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
