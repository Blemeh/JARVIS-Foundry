"""V3 WP6 tests: outcome recording — PASSED-only, distinct fields,
honest UNKNOWN, calibration both directions."""
from __future__ import annotations

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.v3_outcomes import (
    maintenance_scan_hook,
    outcome_for_mission,
    record_outcome,
)


@pytest.fixture()
def db(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    return database


def test_passed_mission_records_immediate_outcome(db):
    payload = record_outcome(
        db, candidate_id="cand-o1", mission_id="mission-o1",
        mission_outcome="PASSED",
        predicted_value={"score": 0.55},
        actual_value={"score": 0.61, "technical": "PASSED"},
        confidence=0.9)
    assert payload["status"] == "RECORDED"
    row = db.row("candidate_outcomes", "outcome_id", "cand-o1:immediate")
    import json
    assert json.loads(row["predicted_value_json"]) == {"score": 0.55}
    assert json.loads(row["actual_value_json"]) == {
        "score": 0.61, "technical": "PASSED"}
    # Predicted and actual stay DISTINCT fields.
    assert row["predicted_value_json"] != row["actual_value_json"]


def test_failed_or_blocked_mission_never_records(db):
    for bad in ("FAILED", "BLOCKED"):
        with pytest.raises(ValueError, match="PASSED"):
            record_outcome(
                db, candidate_id=f"cand-{bad}", mission_id="mission-x",
                mission_outcome=bad,
                predicted_value={"score": 0.5},
                actual_value={"score": 0.5})
    assert db.count("candidate_outcomes") == 0


def test_missing_measurement_is_honest_unknown_not_pass(db):
    payload = record_outcome(
        db, candidate_id="cand-u1", mission_id="mission-u1",
        mission_outcome="PASSED")
    assert payload["status"] == "UNKNOWN"
    row = db.row("candidate_outcomes", "outcome_id", "cand-u1:immediate")
    assert row["status"] == "UNKNOWN"


def test_calibration_recorded_over_estimate_negative_delta(db):
    record_outcome(
        db, candidate_id="cand-cal-1", mission_id="mission-c1",
        mission_outcome="PASSED",
        predicted_value={"score": 0.7},
        actual_value={"score": 0.5})
    calibrations = [e for e in db.events()
                    if e.event_type == "executive.calibration_recorded"]
    assert len(calibrations) == 1
    body = calibrations[0].payload
    assert body["delta"] == round(0.5 - 0.7, 4)
    assert body["direction"] == "over"


def test_calibration_recorded_under_estimate_positive_delta(db):
    record_outcome(
        db, candidate_id="cand-cal-2", mission_id="mission-c2",
        mission_outcome="PASSED",
        predicted_value={"score": 0.4},
        actual_value={"score": 0.8})
    body = [e for e in db.events()
            if e.event_type == "executive.calibration_recorded"][0].payload
    assert body["delta"] == 0.4 and body["direction"] == "under"


def test_idempotent_replay_same_window_single_row(db):
    kwargs = dict(
        candidate_id="cand-idem", mission_id="mission-i",
        mission_outcome="PASSED",
        predicted_value={"score": 0.5}, actual_value={"score": 0.55})
    first = record_outcome(db, **kwargs)
    again = record_outcome(db, **kwargs)
    assert again["outcome_id"] == first["outcome_id"]
    rows = db.all("candidate_outcomes",
                  **{"candidate_id": "cand-idem"})
    assert len(rows) == 1


def test_mission_to_candidate_outcome_linkage(tmp_path):
    from jarvis_foundry.discovery_candidates import (
        approve_candidate, generate_candidate)
    from jarvis_foundry.v3_scoring import register_policy
    from v3_fixtures import load_policy

    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    register_policy(database, load_policy())
    generated = generate_candidate(
        database, "mission-seed", "repo", "Fix the failing nightly integration",
        [{"kind": "ci", "ref": "nightly"}],
        typed={"expected_outcome": "nightly green"})
    mission_id = approve_candidate(database, generated["candidate_id"])
    record_outcome(
        database, candidate_id=generated["candidate_id"],
        mission_id=mission_id, mission_outcome="PASSED",
        predicted_value={"score": 0.42},
        actual_value={"score": 0.58})
    linked = outcome_for_mission(database, mission_id)
    assert linked is not None and linked["status"] == "RECORDED"
    assert linked["candidate_id"] == generated["candidate_id"]


def test_maintenance_hook_reports_counts_without_wake(db):
    record_outcome(
        db, candidate_id="cand-h1", mission_id="mission-h1",
        mission_outcome="PASSED",
        predicted_value={"score": 0.5}, actual_value={"score": 0.5})
    record_outcome(
        db, candidate_id="cand-h2", mission_id="mission-h2",
        mission_outcome="PASSED")
    report = maintenance_scan_hook(db, loop_id="loop-h")
    assert report == {"loop_id": "loop-h", "outcomes_recorded": 1,
                      "outcomes_unknown": 1, "wake_scheduled": False}
