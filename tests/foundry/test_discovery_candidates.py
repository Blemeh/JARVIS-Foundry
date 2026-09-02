"""V3 slice-1 (WP10): discovery candidate pipeline.

Contract: candidates with evidence; duplicate detection via canonicalized
objective (vs existing candidates AND missions); calibrated deterministic
scoring (registry-driven weights); approval creates a mission
(mission.created carries the candidate_id); rejection + outcome tracking
(prediction vs reality); deterministic rebuild + reconcile (schema v10)."""
import json

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def candidate_event(candidate_id="cand-1", objective="Add a retry budget to the gateway",
                    source="review-of-F8", evidence=None):
    return Event.create(
        "discovery.candidate_generated",
        {"candidate_id": candidate_id, "mission_id": "mission-1",
         "source": source, "objective": objective,
         "evidence": evidence or [{"kind": "test-result", "ref": "F8"}]},
        source_type="test", source_id="test-suite",
        idempotency_key=f"discovery.candidate_generated:{candidate_id}")


def test_candidate_generated_projects_row(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(candidate_event())
    row = db.row("candidates", "candidate_id", "cand-1")
    assert row["status"] == "NEW"
    assert "F8" in row["evidence_json"]


def test_duplicate_detection_canonicalized_objective(tmp_path):
    from jarvis_foundry.discovery_candidates import generate_candidate
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    first = generate_candidate(db, "mission-1", "review-of-F8",
                               "Add a retry budget to the gateway")
    dup = generate_candidate(db, "mission-1", "other-source",
                             "  ADD a RETRY budget to the GATEWAY.  ")
    assert dup["duplicate_of"] == first["candidate_id"]
    assert dup["created"] is False
    row = db.row("candidates", "candidate_id", dup["candidate_id"])
    assert row["duplicate_of"] == first["candidate_id"]
    # a genuinely different objective is NOT a duplicate
    fresh = generate_candidate(db, "mission-1", "review-of-F8",
                               "Add a DNS preflight check")
    assert fresh["duplicate_of"] is None and fresh["created"] is True


def test_duplicate_detection_against_missions(tmp_path):
    from jarvis_foundry.discovery_candidates import generate_candidate
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(Event.create(
        "mission.created",
        {"mission_id": "mission-existing", "step_id": "step-0",
         "objective": "Add a retry budget to the gateway",
         "role": "builder", "created_from": "seed",
         "acceptance_criteria": ["retries bounded"]},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-existing"))
    result = generate_candidate(db, "mission-1", "review-of-F8",
                                "Add a retry budget to the gateway")
    assert result["duplicate_of"] == "mission-existing"


def test_scoring_deterministic_registry_weights(tmp_path):
    from jarvis_foundry.discovery_candidates import generate_candidate, score_candidate
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    result = generate_candidate(db, "mission-1", "review-of-F8",
                                "Add a retry budget to the gateway")
    s1 = score_candidate(db, result["candidate_id"])
    s2 = score_candidate(db, result["candidate_id"])
    assert s1 == s2
    assert 0.0 <= s1 <= 1.0
    row = db.row("candidate_scores", "candidate_id", result["candidate_id"])
    assert row["score"] == s1
    # higher-value objective scores higher (deterministic ordering)
    low = generate_candidate(db, "mission-1", "review-of-F8",
                             "Rename an internal variable")
    high = generate_candidate(db, "mission-1", "review-of-F8",
                              "Fix the failing nightly integration")
    s_low = score_candidate(db, low["candidate_id"])
    s_high = score_candidate(db, high["candidate_id"])
    assert s_high > s_low


def test_approval_creates_mission_with_candidate_id(tmp_path):
    from jarvis_foundry.discovery_candidates import (generate_candidate,
                                                     score_candidate,
                                                     approve_candidate)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    result = generate_candidate(db, "mission-1", "review-of-F8",
                                "Add a retry budget to the gateway")
    score_candidate(db, result["candidate_id"])
    mission_id = approve_candidate(db, result["candidate_id"], "builder")
    assert mission_id.startswith("mission-")
    row = db.row("candidates", "candidate_id", result["candidate_id"])
    assert row["status"] == "APPROVED"
    with db.connect() as connection:
        created = connection.execute(
            "SELECT mission_id, objective FROM missions WHERE mission_id = ?",
            (mission_id,)).fetchone()
    assert created[1] == "Add a retry budget to the gateway"


def test_rejection_sets_status(tmp_path):
    from jarvis_foundry.discovery_candidates import (generate_candidate,
                                                     reject_candidate)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    result = generate_candidate(db, "mission-1", "review-of-F8",
                                "Rename an internal variable")
    reject_candidate(db, result["candidate_id"], "low value")
    row = db.row("candidates", "candidate_id", result["candidate_id"])
    assert row["status"] == "REJECTED"
    assert json.loads(row["evidence_json"]) == [{
        "rejection_reason": "low value",
        "reason_code": "LEGACY_REJECTION",
    }]
    judgments = db.all("candidate_judgments", candidate_id=result["candidate_id"])
    assert len(judgments) == 1
    assert judgments[0]["outcome"] == "REJECTED"
    assert judgments[0]["reason_detail"] == "low value"


def test_outcome_tracking(tmp_path):
    from jarvis_foundry.discovery_candidates import (generate_candidate,
                                                     score_candidate,
                                                     approve_candidate,
                                                     record_outcome)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    result = generate_candidate(db, "mission-1", "review-of-F8",
                                "Add a retry budget to the gateway")
    score_candidate(db, result["candidate_id"])
    mission_id = approve_candidate(db, result["candidate_id"], "builder")
    record_outcome(db, result["candidate_id"], mission_id,
                   {"prediction": "retries bounded at 3", "reality": "bounded"})
    row = db.row("candidates", "candidate_id", result["candidate_id"])
    assert row["status"] == "EXECUTED"
    assert "reality" in row["evidence_json"]


def test_rebuild_reproduces_candidates_and_scores(tmp_path):
    from jarvis_foundry.discovery_candidates import (generate_candidate,
                                                     score_candidate)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    result = generate_candidate(db, "mission-1", "review-of-F8",
                                "Add a retry budget to the gateway")
    score_candidate(db, result["candidate_id"])
    before = db.all("candidate_scores")
    db.rebuild_projections()
    after = db.all("candidate_scores")
    assert len(after) == len(before) == 1
    assert after[0]["score"] == before[0]["score"]


def test_schema_version_matches_code(tmp_path):
    from jarvis_foundry.migrations import SCHEMA_VERSION
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    with db.connect() as connection:
        version = connection.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION
