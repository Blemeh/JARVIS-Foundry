"""V3 WP6 tests: outcome recording — PASSED-only, distinct fields,
honest UNKNOWN, calibration both directions."""
from __future__ import annotations

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import approve_candidate, generate_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.mission.verification import (
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)
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


def _linked(db, label: str):
    """Create the durable candidate -> COMPLETE mission proof required by V3."""
    generated = generate_candidate(
        db, "mission-seed", "test", label,
        [{"kind": "test", "ref": label}],
        typed={"expected_outcome": "verified"})
    candidate_id = generated["candidate_id"]
    mission_id = approve_candidate(db, candidate_id)
    for state in ("ORIENTING", "ACTION_SELECTION", "COMPLETE"):
        db.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": None},
            source_type="supervisor", source_id="test",
            idempotency_key=f"test:state:{mission_id}:{state}"))
    db.append(Event.create(
        "mission.completed",
        {"mission_id": mission_id, "outcome": "PASSED", "objective": label},
        source_type="supervisor", source_id="test",
        idempotency_key=f"test:completed:{mission_id}"))
    verdict_id = f"verdict-{mission_id}"
    start_verification(
        db,
        verdict_id=verdict_id,
        mission_id=mission_id,
        step_id="step-1",
        builder_run_id=f"builder-{mission_id}",
        verifier_run_id=f"verifier-{mission_id}",
        independence={"session_independent": True},
        criteria=["durable pass"],
    )
    record_verification_outcome(
        db,
        verdict_id=verdict_id,
        outcome="PASS",
        findings=[],
        evidence={"durable": True},
    )
    record_verification_acceptance(
        db,
        verdict_id=verdict_id,
        mission_id=mission_id,
        step_id="step-1",
        builder_run_id=f"builder-{mission_id}",
        verifier_run_id=f"verifier-{mission_id}",
        checks={"durable": "accepted"},
    )
    return candidate_id, mission_id


def test_passed_mission_records_immediate_outcome(db):
    candidate_id, mission_id = _linked(db, "outcome one")
    payload = record_outcome(
        db, candidate_id=candidate_id, mission_id=mission_id,
        mission_outcome="PASSED",
        predicted_value={"score": 0.55},
        actual_value={"score": 0.61, "technical": "PASSED"},
        confidence=0.9)
    assert payload["status"] == "RECORDED"
    row = db.row("candidate_outcomes", "outcome_id", f"{candidate_id}:immediate")
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
    candidate_id, mission_id = _linked(db, "unknown outcome")
    payload = record_outcome(
        db, candidate_id=candidate_id, mission_id=mission_id,
        mission_outcome="PASSED")
    assert payload["status"] == "UNKNOWN"
    row = db.row("candidate_outcomes", "outcome_id", f"{candidate_id}:immediate")
    assert row["status"] == "UNKNOWN"


def test_calibration_recorded_over_estimate_negative_delta(db):
    candidate_id, mission_id = _linked(db, "calibration over")
    record_outcome(
        db, candidate_id=candidate_id, mission_id=mission_id,
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
    candidate_id, mission_id = _linked(db, "calibration under")
    record_outcome(
        db, candidate_id=candidate_id, mission_id=mission_id,
        mission_outcome="PASSED",
        predicted_value={"score": 0.4},
        actual_value={"score": 0.8})
    body = [e for e in db.events()
            if e.event_type == "executive.calibration_recorded"][0].payload
    assert body["delta"] == 0.4 and body["direction"] == "under"


def test_idempotent_replay_same_window_single_row(db):
    candidate_id, mission_id = _linked(db, "idempotent outcome")
    kwargs = dict(
        candidate_id=candidate_id, mission_id=mission_id,
        mission_outcome="PASSED",
        predicted_value={"score": 0.5}, actual_value={"score": 0.55})
    first = record_outcome(db, **kwargs)
    again = record_outcome(db, **kwargs)
    assert again["outcome_id"] == first["outcome_id"]
    rows = db.all("candidate_outcomes", **{"candidate_id": candidate_id})
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
    for state in ("ORIENTING", "ACTION_SELECTION", "COMPLETE"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": None},
            source_type="supervisor", source_id="test",
            idempotency_key=f"test:state:{mission_id}:{state}"))
    database.append(Event.create(
        "mission.completed",
        {"mission_id": mission_id, "outcome": "PASSED"},
        source_type="supervisor", source_id="test",
        idempotency_key=f"test:completed:{mission_id}"))
    verdict_id = f"verdict-{mission_id}"
    start_verification(
        database,
        verdict_id=verdict_id,
        mission_id=mission_id,
        step_id="step-1",
        builder_run_id=f"builder-{mission_id}",
        verifier_run_id=f"verifier-{mission_id}",
        independence={"session_independent": True},
        criteria=["durable pass"],
    )
    record_verification_outcome(
        database,
        verdict_id=verdict_id,
        outcome="PASS",
        findings=[],
        evidence={"durable": True},
    )
    record_verification_acceptance(
        database,
        verdict_id=verdict_id,
        mission_id=mission_id,
        step_id="step-1",
        builder_run_id=f"builder-{mission_id}",
        verifier_run_id=f"verifier-{mission_id}",
        checks={"durable": "accepted"},
    )
    record_outcome(
        database, candidate_id=generated["candidate_id"],
        mission_id=mission_id, mission_outcome="PASSED",
        predicted_value={"score": 0.42},
        actual_value={"score": 0.58})
    linked = outcome_for_mission(database, mission_id)
    assert linked is not None and linked["status"] == "RECORDED"
    assert linked["candidate_id"] == generated["candidate_id"]


def test_maintenance_hook_schedules_idempotent_wake(db):
    candidate_id, mission_id = _linked(db, "hook recorded")
    record_outcome(
        db, candidate_id=candidate_id, mission_id=mission_id,
        mission_outcome="PASSED",
        predicted_value={"score": 0.5}, actual_value={"score": 0.5})
    candidate_id2, mission_id2 = _linked(db, "hook unknown")
    record_outcome(
        db, candidate_id=candidate_id2, mission_id=mission_id2,
        mission_outcome="PASSED")
    report = maintenance_scan_hook(db, loop_id="loop-h")
    assert report["loop_id"] == "loop-h"
    assert report["outcomes_recorded"] == 1
    assert report["outcomes_unknown"] == 1
    assert report["wake_scheduled"] is True
    assert report["next_selection_id"] == "exec-loop-h:next"
    assert len([e for e in db.events()
                if e.event_type == "executive.next_selection_requested"
                and e.payload["loop_id"] == "loop-h"]) == 1


def test_outcome_requires_durable_completed_link_and_matching_candidate(db):
    candidate_id, mission_id = _linked(db, "strict linkage")
    with pytest.raises(ValueError, match="durable candidate"):
        record_outcome(
            db, candidate_id="cand-not-linked", mission_id=mission_id,
            mission_outcome="PASSED", predicted_value={"score": 0.5},
            actual_value={"score": 0.5})

    generated = generate_candidate(
        db, "mission-seed", "test", "not completed",
        [{"kind": "test", "ref": "not-completed"}])
    pending_mission = approve_candidate(db, generated["candidate_id"])
    with pytest.raises(ValueError, match="COMPLETE"):
        record_outcome(
            db, candidate_id=generated["candidate_id"],
            mission_id=pending_mission, mission_outcome="PASSED",
            predicted_value={"score": 0.5}, actual_value={"score": 0.5})


def test_outcome_carries_lineage_and_follow_up_wake(db):
    candidate_id, mission_id = _linked(db, "lineage outcome")
    payload = record_outcome(
        db, candidate_id=candidate_id, mission_id=mission_id,
        mission_outcome="PASSED", predicted_value={"score": 0.5},
        actual_value={"score": 0.6})
    lineage = payload["lineage"]
    assert lineage["candidate_id"] == candidate_id
    assert lineage["mission_id"] == mission_id
    assert lineage["mission_completed_event_id"]
    assert payload["continuation"]["wake_scheduled"] is True
    assert any(e.event_type == "discovery.maintenance_scan_scheduled"
               for e in db.events())
