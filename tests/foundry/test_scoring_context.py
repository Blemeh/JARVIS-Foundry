"""V4-S6 (R7): bounded and fair candidate scoring context.

DIR-2026-08-12 item 7: consider freshness, source trust, evidence,
expected value, urgency, cost, risk, duplication, mission similarity,
previous attempts and cooldowns — WITHOUT lowering thresholds to
manufacture work. Scoring stays deterministic; SCORED candidates are
FROZEN (their historical score is returned; the context factors never
re-score a recorded candidate, so the live loop cannot EventConflict
when age/attempts drift).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import (
    generate_candidate,
    score_candidate,
    score_objective,
)
from jarvis_foundry.events import Event
from jarvis_foundry.v3_loop import DEFAULT_MIN_SCORE


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def make_candidate(db: FoundryDatabase, objective: str, *,
                   source: str = "vault", evidence: list | None = None,
                   mission_id: str = "mission-1") -> str:
    result = generate_candidate(db, mission_id, source, objective,
                                evidence=evidence or [{"n": 1}])
    assert result["created"] is True
    return result["candidate_id"]


def recorded_weights(db: FoundryDatabase, candidate_id: str) -> dict:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'discovery.candidate_scored' AND payload_json LIKE ? "
            "ORDER BY sequence LIMIT 1",
            (f'%"candidate_id":"{candidate_id}"%',)).fetchone()
    return json.loads(row[0])["weights"]


def test_freshness_decays_score(db):
    objective = "Fix the reported gateway crash risk today."
    score_old, _ = score_objective(objective, age_hours=7 * 24)
    score_new, _ = score_objective(objective, age_hours=0.0)
    assert score_new > score_old
    # the decay is exactly the documented freshness factor
    assert round(score_new - score_old, 4) == round(
        0.05 * (1 - math.exp(-7 * 24 / 168.0)), 4)


def test_cooldown_penalizes_previous_attempts(db):
    objective = "Implement the steering dashboard panel."
    base, _ = score_objective(objective, previous_attempts=0)
    cooled, weights = score_objective(objective, previous_attempts=4)
    assert cooled < base
    assert weights["cooldown_penalty"] == pytest.approx(0.12)
    # bounded: absurd attempt counts clamp at the cap (5)
    clamped, weights_capped = score_objective(objective, previous_attempts=99)
    at_cap, weights_at_cap = score_objective(objective, previous_attempts=5)
    assert clamped == at_cap
    assert weights_capped["cooldown_penalty"] == \
        weights_at_cap["cooldown_penalty"] == pytest.approx(0.15)


def test_source_trust_orders_sources(db):
    objective = "Verify the scheduler behavior under load."
    vault, w_vault = score_objective(objective, source_trust=1.0)
    queue, w_queue = score_objective(objective, source_trust=0.85)
    assert vault > queue
    assert w_vault["source_trust"] == 1.0 and w_queue["source_trust"] == 0.85


def test_evidence_richness_bonus(db):
    objective = "Fix the reaper edge case."
    thin, _ = score_objective(objective, evidence_items=0)
    rich, weights = score_objective(objective, evidence_items=4)
    assert rich > thin
    assert weights["evidence_bonus"] == pytest.approx(0.04)
    clamped, _ = score_objective(objective, evidence_items=99)
    at_cap, _ = score_objective(objective, evidence_items=5)
    assert clamped == at_cap


def test_similarity_penalty_bounded(db):
    objective = "Add the retry budget to the gateway."
    alone, _ = score_objective(objective, similar_missions=0)
    crowded, weights = score_objective(objective, similar_missions=2)
    assert crowded < alone
    assert weights["similarity_penalty"] == pytest.approx(0.04)


def test_score_stays_bounded_extremes(db):
    score, weights = score_objective(
        "Fix the urgent gateway crash risk today.",
        age_hours=100_000, previous_attempts=99, source_trust=0.0,
        evidence_items=99, similar_missions=99)
    assert 0.0 <= score <= 1.0
    assert weights["cooldown_penalty"] <= 0.15
    assert weights["evidence_bonus"] <= 0.05
    assert weights["similarity_penalty"] <= 0.06


def test_threshold_never_lowered(db):
    assert DEFAULT_MIN_SCORE == 0.35


def test_score_candidate_uses_db_context(db):
    objective = "Repair the unsupported claim detection."
    # research_queue source + thin evidence + a completed mission for the
    # same objective (previous attempt)
    candidate = make_candidate(db, objective, source="research_queue",
                               evidence=[{"reason": "STALE"}])
    db.append(Event.create(
        "mission.created",
        {"mission_id": "mission-prev", "step_id": "x",
         "objective": objective, "role": "builder",
         "acceptance_criteria": ["c"], "created_from": "candidate:other"},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-prev"))
    db.append(Event.create(
        "mission.completed",
        {"mission_id": "mission-prev", "outcome": "PASSED",
         "notes": "done"},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.completed:mission-prev"))
    score = score_candidate(db, candidate)
    weights = recorded_weights(db, candidate)
    assert weights["cooldown_penalty"] >= 0.03   # the previous attempt
    assert weights["source_trust"] == 0.85       # research_queue
    assert weights["evidence_bonus"] == pytest.approx(0.01)
    # the contextual score differs from the context-free formula
    plain, _ = score_objective(objective)
    assert score != plain


def test_scored_candidate_frozen_no_rescore(db):
    """Re-scoring a SCORED candidate returns the stored score and never
    appends a second scored event — age/attempt drift cannot change the
    recorded score (no EventConflict on the live loop's re-scans)."""
    objective = "Fix the freeze window in the gateway."
    candidate = make_candidate(db, objective, source="vault")
    first = score_candidate(db, candidate)
    with db.connect() as connection:
        events_after_first = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'discovery.candidate_scored'").fetchone()[0]
    # drift the context: a previous attempt appears + 2 days pass
    db.append(Event.create(
        "mission.created",
        {"mission_id": "mission-later", "step_id": "x",
         "objective": objective, "role": "builder",
         "acceptance_criteria": ["c"], "created_from": "candidate:other"},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-later"))
    with db.connect() as connection:
        connection.execute(
            "UPDATE events SET recorded_at = ? WHERE idempotency_key = ?",
            ("2026-08-10T00:00:00Z",
             "discovery.candidate_generated:" + candidate))
        connection.commit()
    second = score_candidate(db, candidate)
    with db.connect() as connection:
        events_after_second = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'discovery.candidate_scored'").fetchone()[0]
    assert second == first
    assert events_after_second == events_after_first == 1


def test_scoring_deterministic(db):
    """Same candidate scored twice (fresh + frozen paths) yields the
    same score and exactly one scored event."""
    objective = "Ship the bounded retry policy."
    candidate = make_candidate(db, objective, source="system")
    first = score_candidate(db, candidate)
    second = score_candidate(db, candidate)
    assert first == second
    with db.connect() as connection:
        events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'discovery.candidate_scored'").fetchone()[0]
    assert events == 1
