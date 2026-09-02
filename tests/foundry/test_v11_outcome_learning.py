"""V11 Lane 3: final outcome -> learning -> WorldModel -> briefing truth."""
from __future__ import annotations

from collections.abc import Callable

import pytest

from jarvis_foundry.briefings import BriefingPlanner
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.calibration import rebuild_calibration
from jarvis_foundry.mission.verification import (
    final_outcome_history,
    final_outcome_projection,
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.world_model import WorldModel, reconcile_outcome_learning
from jarvis_foundry.world_model.connector import writeback_mission_outcome


@pytest.fixture()
def database(tmp_path) -> FoundryDatabase:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    return db


def _append(db: FoundryDatabase, event_type: str, payload: dict, key: str) -> None:
    db.append(Event.create(
        event_type,
        payload,
        source_type="supervisor",
        source_id="v11-outcome-fixture",
        idempotency_key=key,
    ))


def _seed_mission(
    db: FoundryDatabase,
    mission_id: str,
    *,
    objective_id: str = "objective-v11",
    objective: str = "Improve the owner project safely",
) -> str:
    candidate_id = f"candidate-{mission_id}"
    _append(db, "discovery.candidate_generated", {
        "candidate_id": candidate_id,
        "mission_id": f"observation-{mission_id}",
        "source": "project_drift",
        "objective": objective,
        "objective_canonical": objective.lower(),
        "evidence": [{"evidence_ref": f"observed-{mission_id}"}],
        "expected_outcome": "project health improves",
    }, f"candidate:{candidate_id}")
    _append(db, "executive.decision_recorded", {
        "decision_id": f"decision-{mission_id}",
        "candidate_id": candidate_id,
        "mission_id": mission_id,
        "decision": "SELECT",
        "reason": "high-value reversible local improvement",
    }, f"decision:{mission_id}")
    _append(db, "executive.prediction_recorded", {
        "decision_id": f"decision-{mission_id}",
        "candidate_id": candidate_id,
        "prediction": {"outcome": "SUCCEEDED", "confidence": 0.8},
    }, f"prediction:{mission_id}")
    _append(db, "executive.objective_created", {
        "objective_id": objective_id,
        "objective": objective,
        "status": "ACTIVE",
        "progress": 0.0,
        "metadata": {"project_id": "owner-project"},
        "mission_ids": [mission_id],
        "plan_ids": [],
        "provenance": {"source": "fixture"},
        "version": 1,
    }, f"objective:{objective_id}:{mission_id}")
    _append(db, "mission.created", {
        "mission_id": mission_id,
        "objective": objective,
        "resource_id": "owner-project",
        "state": "PLANNED",
        "acceptance_criteria": ["independent verifier and AcceptanceGate accept"],
        "next_action": "build and verify",
        "created_from": f"candidate:{candidate_id}",
    }, f"mission.created:{mission_id}")
    for state in ("ORIENTING", "ACTION_SELECTION", "READY", "EXECUTING", "VERIFYING"):
        _append(db, "mission.state_changed", {
            "mission_id": mission_id,
            "state": state,
            "next_action": "build and verify",
        }, f"mission.state:{mission_id}:{state}")
    return candidate_id


def _verdict(
    db: FoundryDatabase,
    mission_id: str,
    *,
    verdict_id: str,
    outcome: str,
    reason: str = "",
    accept: bool = False,
) -> None:
    start_verification(
        db,
        verdict_id=verdict_id,
        mission_id=mission_id,
        step_id="build",
        builder_run_id=f"builder-{verdict_id}",
        verifier_run_id=f"verifier-{verdict_id}",
        independence={"session_independent": True},
        criteria=["observed result matches acceptance criteria"],
    )
    record_verification_outcome(
        db,
        verdict_id=verdict_id,
        outcome=outcome,
        findings=[] if not reason else [reason],
        evidence={"evidence_ref": f"evidence-{verdict_id}"},
        reason=reason or None,
        repair_requirements=["repair and re-verify"] if outcome == "FAIL" else None,
    )
    if accept:
        record_verification_acceptance(
            db,
            verdict_id=verdict_id,
            mission_id=mission_id,
            step_id="build",
            builder_run_id=f"builder-{verdict_id}",
            verifier_run_id=f"verifier-{verdict_id}",
            checks={"verdict_status": "PASS", "acceptance_gate": "accepted"},
        )


def _complete_success(db: FoundryDatabase, mission_id: str, verdict_id: str) -> None:
    _verdict(db, mission_id, verdict_id=verdict_id, outcome="PASS", accept=True)
    _append(db, "mission.state_changed", {
        "mission_id": mission_id, "state": "ACCEPTING", "next_action": None,
    }, f"mission.state:{mission_id}:ACCEPTING:{verdict_id}")
    _append(db, "mission.state_changed", {
        "mission_id": mission_id, "state": "COMPLETE", "next_action": None,
    }, f"mission.state:{mission_id}:COMPLETE:{verdict_id}")
    _append(db, "mission.completed", {
        "mission_id": mission_id, "outcome": "PASSED", "run_id": f"builder-{verdict_id}",
    }, f"mission.completed:{mission_id}")


def _complete_failure(db: FoundryDatabase, mission_id: str, verdict_id: str) -> None:
    _verdict(
        db, mission_id, verdict_id=verdict_id, outcome="FAIL",
        reason="independent verification found the observed result incorrect",
    )
    _append(db, "mission.state_changed", {
        "mission_id": mission_id, "state": "REPAIRING", "next_action": "repair",
    }, f"mission.state:{mission_id}:REPAIRING")
    _append(db, "mission.state_changed", {
        "mission_id": mission_id, "state": "BLOCKED", "next_action": "WAIT",
        "reason": "repair budget exhausted",
    }, f"mission.state:{mission_id}:BLOCKED")


def _count(db: FoundryDatabase, event_type: str) -> int:
    return db.event_count(event_type=event_type)


def _world_pattern_count(db: FoundryDatabase, pattern_kind: str) -> int:
    with db.connect(readonly=True) as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM world_patterns WHERE pattern_kind = ?",
            (pattern_kind,),
        ).fetchone()[0])


def test_accepted_success_updates_each_existing_learning_path_once(database) -> None:
    mission_id = "mission-success"
    _seed_mission(database, mission_id)
    _complete_success(database, mission_id, "verdict-success")

    result = reconcile_outcome_learning(database, mission_id=mission_id)

    assert result["finalized"] is True
    assert result["status"] == "SUCCEEDED"
    assert _count(database, "mission.outcome_finalized") == 1
    assert _count(database, "executive.calibration_recorded") == 1
    assert _count(database, "world.observation_recorded") == 1
    assert _count(database, "memory.observed") == 1
    assert result["world"]["accepted"] is True


def test_accepted_failure_is_one_negative_update_and_not_blocker_truth(database) -> None:
    mission_id = "mission-failure"
    _seed_mission(database, mission_id)
    _complete_failure(database, mission_id, "verdict-failure")

    result = reconcile_outcome_learning(database, mission_id=mission_id)
    calibration = rebuild_calibration(database)

    assert result["status"] == "FAILED"
    assert result["final"]["actual_observed"]["mission_state"] == "BLOCKED"
    assert result["final"]["failure_reason"]
    assert len(result["final"]["repair_attempts"]) == 1
    assert calibration.adjustment_for(
        source="project_drift", objective="Improve the owner project safely",
    ) < 0
    assert _world_pattern_count(database, "MISSION_FAILURE") == 1


def test_blocked_without_failed_verifier_stays_neutral_and_distinct(database) -> None:
    mission_id = "mission-blocked"
    _seed_mission(database, mission_id)
    _verdict(
        database, mission_id, verdict_id="verdict-blocked",
        outcome="BLOCKED", reason="provider unavailable",
    )
    _append(database, "mission.state_changed", {
        "mission_id": mission_id, "state": "BLOCKED", "next_action": "WAIT",
        "reason": "provider unavailable",
    }, f"mission.state:{mission_id}:BLOCKED")

    result = reconcile_outcome_learning(database, mission_id=mission_id)
    calibration = rebuild_calibration(database)

    assert result["status"] == "BLOCKED"
    assert calibration.failure_counts == {}
    assert _world_pattern_count(database, "MISSION_FAILURE") == 0


def test_owner_rejected_work_stays_neutral_and_distinct(database) -> None:
    mission_id = "mission-rejected"
    _seed_mission(database, mission_id)
    _append(database, "mission.state_changed", {
        "mission_id": mission_id, "state": "REJECTING", "next_action": "reject",
    }, f"mission.state:{mission_id}:REJECTING")
    _append(database, "mission.state_changed", {
        "mission_id": mission_id, "state": "CANCELLED", "next_action": "WAIT",
        "reason": "owner rejected the proposed work",
    }, f"mission.state:{mission_id}:CANCELLED")

    result = reconcile_outcome_learning(database, mission_id=mission_id)
    calibration = rebuild_calibration(database)

    assert result["status"] == "REJECTED"
    assert calibration.failure_counts == {}
    assert calibration.success_counts == {}
    assert _world_pattern_count(database, "MISSION_FAILURE") == 0


def test_preliminary_pass_then_gate_failure_produces_zero_success_learning(database) -> None:
    mission_id = "mission-gate-refusal"
    _seed_mission(database, mission_id)
    _verdict(
        database, mission_id, verdict_id="verdict-refused",
        outcome="PASS", accept=False,
    )
    record_verification_outcome(
        database,
        verdict_id="verdict-refused",
        outcome="FAIL",
        findings=["AcceptanceGate refused changed artifacts"],
        evidence={"acceptance_gate": "REFUSE"},
        reason="acceptance gate refused",
        repair_requirements=["rebuild and re-verify"],
    )
    _append(database, "mission.state_changed", {
        "mission_id": mission_id, "state": "REPAIRING", "next_action": "repair",
    }, f"mission.state:{mission_id}:REPAIRING")
    _append(database, "mission.state_changed", {
        "mission_id": mission_id, "state": "BLOCKED", "next_action": "WAIT",
    }, f"mission.state:{mission_id}:BLOCKED")

    result = reconcile_outcome_learning(database, mission_id=mission_id)

    assert result["status"] == "FAILED"
    assert result["final"]["verification"]["acceptance_event_id"] is None
    assert rebuild_calibration(database).success_counts == {}
    assert not any(
        row["status"] == "SUCCEEDED" for row in final_outcome_history(database)
    )


def test_replay_same_accepted_outcome_adds_no_learning_or_briefing_growth(database) -> None:
    mission_id = "mission-replay"
    _seed_mission(database, mission_id)
    _complete_success(database, mission_id, "verdict-replay")
    first = reconcile_outcome_learning(database, mission_id=mission_id)
    counts = {
        kind: _count(database, kind)
        for kind in (
            "mission.outcome_finalized", "executive.calibration_recorded",
            "world.observation_recorded", "memory.observed",
        )
    }

    second = reconcile_outcome_learning(database, mission_id=mission_id)

    assert second["outcome_id"] == first["outcome_id"]
    assert second["created"] is False
    assert second["memory"]["memory_id"] == first["memory"]["memory_id"]
    assert second["memory"]["deduplicated"] is True
    assert counts == {kind: _count(database, kind) for kind in counts}
    planner = BriefingPlanner(database)
    one = planner.plan("MISSION_SUMMARY", period_key="outcome-replay")
    two = planner.plan("MISSION_SUMMARY", period_key="outcome-replay")
    assert one["created"] is True
    assert two["created"] is False
    assert _count(database, "briefing.planned") == 1


def test_changed_acceptance_identity_creates_new_truth_revision(database) -> None:
    mission_id = "mission-reverified"
    _seed_mission(database, mission_id)
    _complete_success(database, mission_id, "verdict-original")
    original = reconcile_outcome_learning(database, mission_id=mission_id)
    _verdict(
        database, mission_id, verdict_id="verdict-replacement",
        outcome="PASS", accept=True,
    )

    replacement = reconcile_outcome_learning(database, mission_id=mission_id)
    history = final_outcome_history(database, mission_id=mission_id)

    assert replacement["outcome_id"] != original["outcome_id"]
    assert replacement["final"]["verification"]["verdict_id"] == "verdict-replacement"
    assert len(history) == 2
    assert history[0]["revision"] == 2
    assert history[1]["revision"] == 1


def test_failure_then_repaired_success_preserves_history_and_latest_truth(database) -> None:
    mission_id = "mission-repair"
    _seed_mission(database, mission_id, objective_id="objective-repair")
    _complete_failure(database, mission_id, "verdict-repair-failed")
    failure = reconcile_outcome_learning(database, mission_id=mission_id)
    for state in ("ACTION_SELECTION", "READY", "EXECUTING", "VERIFYING"):
        _append(database, "mission.state_changed", {
            "mission_id": mission_id,
            "state": state,
            "next_action": "repair and re-verify",
        }, f"mission.state:{mission_id}:{state}:repair")
    _complete_success(database, mission_id, "verdict-repair-success")
    success = reconcile_outcome_learning(database, mission_id=mission_id)

    projection = final_outcome_projection(database)
    statuses = [item["status"] for item in projection["history"]]

    assert failure["status"] == "FAILED"
    assert success["status"] == "SUCCEEDED"
    assert statuses[:2] == ["SUCCEEDED", "FAILED"]
    assert projection["latest"]["outcome_id"] == success["outcome_id"]
    assert success["final"]["repair_attempts"][0]["verdict_id"] == (
        "verdict-repair-failed"
    )


def test_command_center_and_briefing_match_durable_accepted_outcome(database) -> None:
    mission_id = "mission-briefing"
    _seed_mission(database, mission_id)
    _complete_failure(database, mission_id, "verdict-briefing")
    result = reconcile_outcome_learning(database, mission_id=mission_id)

    snapshot = build_snapshot(database)
    autonomy = snapshot["executive"]["autonomy"]
    projected = autonomy["outcome_learning"]["latest"]
    briefing = autonomy["briefing"]["outcomes"][0]
    packet = snapshot["briefing_packet"]

    assert projected["outcome_id"] == result["outcome_id"]
    assert briefing["outcome_id"] == result["outcome_id"]
    assert briefing["result"] == "FAILED"
    assert briefing["verification"]["verdict_id"] == "verdict-briefing"
    assert briefing["repair_or_failure"]["reason"]
    assert packet["autonomy_briefing"]["latest"]["outcome_id"] == result["outcome_id"]


def test_unaccepted_worker_or_provider_assertion_never_reaches_world_model(database) -> None:
    mission_id = "mission-unaccepted"
    _seed_mission(database, mission_id)
    _verdict(
        database, mission_id, verdict_id="verdict-preliminary",
        outcome="PASS", accept=False,
    )

    report = writeback_mission_outcome(
        database, mission_id, "COMPLETED",
        {"provider": "claimed success", "verified": True},
        evidence_refs=("provider-assertion",),
    )

    assert report["status"] == "NOT_EMITTED"
    assert report["accepted"] is False
    assert _count(database, "world.observation_recorded") == 0
    assert _count(database, "world.pattern_recorded") == 0
    with pytest.raises(ValueError, match="accepted final truth"):
        WorldModel(database).learn_from_mission_result(
            mission_id, "COMPLETED", final_outcome_id="fabricated-outcome")

    forged_id = "outcome-" + "0" * 24
    database.append(Event.create(
        "mission.outcome_finalized",
        {
            "schema": "jarvis.v11.outcome.v1",
            "outcome_id": forged_id,
            "mission_id": mission_id,
            "status": "SUCCEEDED",
            "truth_accepted": True,
            "acceptance_identity": "provider-assertion",
            "acceptance_kind": "verification.accepted",
        },
        source_type="provider",
        source_id="unaccepted-provider",
        idempotency_key=f"mission.outcome_finalized:{forged_id}",
    ))
    with pytest.raises(ValueError, match="accepted final truth"):
        WorldModel(database).learn_from_mission_result(
            mission_id, "SUCCEEDED", final_outcome_id=forged_id)
    assert final_outcome_projection(database)["latest"] is None
    assert writeback_mission_outcome(
        database, mission_id, "SUCCEEDED", final_outcome_id=forged_id,
    )["status"] == "NOT_EMITTED"


def test_outcome_reconciliation_uses_only_filtered_or_bounded_event_reads(
    database, monkeypatch,
) -> None:
    mission_id = "mission-bounded"
    _seed_mission(database, mission_id)
    _complete_success(database, mission_id, "verdict-bounded")
    original: Callable = database.events

    def guarded_events(*args, **kwargs):
        assert kwargs.get("event_types") or kwargs.get("event_type_prefixes"), (
            "outcome reconciliation attempted an unfiltered ledger scan")
        return original(*args, **kwargs)

    monkeypatch.setattr(database, "events", guarded_events)

    result = reconcile_outcome_learning(database, mission_id=mission_id)
    assert result["finalized"] is True
