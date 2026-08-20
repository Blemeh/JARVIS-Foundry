"""Mission 1 — Executive Intelligence focused tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate, score_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.executive.calibration import rebuild_calibration, record_calibration
from jarvis_foundry.executive.decisions import ExecutiveDecisionService
from jarvis_foundry.executive.eligibility import (
    WAIT_COMPARATIVE,
    WAIT_NO_ELIGIBLE,
    assess_eligibility,
    current_activation_epoch,
    stamp_executive_eligibility,
)
from jarvis_foundry.executive.goals import active_goals, goal_alignment_score
from jarvis_foundry.executive.scoring import rank_candidates
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle

from worker_test_helpers import mission_database

EPOCH = "proof-epoch-m1"


def _register_resource(database: FoundryDatabase, repository: Path, resource_id: str = "resource-1") -> None:
    from jarvis_foundry.control.capabilities import set_capability_state
    from jarvis_foundry.control.registration import inspect_git_resource, registration_event
    database.append(registration_event(
        inspect_git_resource(repository, resource_id=resource_id)))
    set_capability_state(
        database, resource_id, "write_registered_worktree_files", "READY",
        evidence={"source": "executive-test"})


def _goal_directive(database: FoundryDatabase, secret: bytes, text: str) -> None:
    svc = OwnerControlService(database, secret=secret, owner_id="owner")
    nonce = f"goal-nonce-{hash(text) & 0xffff}"
    sig = sign_command(secret, "GOAL", "GLOBAL", text, nonce)
    svc.apply("GOAL", target="GLOBAL", exact_text=text, nonce=nonce, signature=sig)


def _activate_epoch(database: FoundryDatabase, epoch: str = EPOCH) -> None:
    database.append(Event.create(
        "executive.activation_recorded",
        {"epoch": epoch, "decision_version": "1.0.0"},
        source_type="supervisor", source_id="test",
        idempotency_key=f"executive.activation_recorded:{epoch}",
    ))


def _organic_candidate(
    database: FoundryDatabase, objective: str, *, source: str = "maintenance",
    evidence: list[dict] | None = None,
) -> str:
    """Generate candidate through normal production path — no manual eligibility."""
    result = generate_candidate(
        database, "mission-exec", source, objective, evidence=evidence or [])
    assert result["created"] is True
    cid = result["candidate_id"]
    score_candidate(database, cid)
    return cid


@pytest.fixture()
def exec_env(tmp_path: Path):
    database, repository = mission_database(tmp_path, resource_id="resource-1")
    secret = b"test-secret-32-bytes-minimum!!"
    _goal_directive(
        database, secret,
        "Improve gateway reliability and reduce customer outage risk today. "
        "priority: 3 success: latency under 200ms; zero customer incidents",
    )
    _activate_epoch(database)
    _register_resource(database, repository)
    return database, repository, secret


def test_organic_candidate_acquires_eligibility(exec_env):
    database, _, _ = exec_env
    cid = _organic_candidate(
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        source="maintenance",
    )
    row = database.row("candidates", "candidate_id", cid)
    eligibility = assess_eligibility(
        database, row, activation_epoch=EPOCH, resource_id="resource-1")
    assert eligibility.eligible is True
    evidence = json.loads(row["evidence_json"])
    assert any(
        item.get("eligibility_provenance", {}).get("stamped_by")
        for item in evidence if isinstance(item, dict)
    )


def test_historical_candidate_firewall_blocks_unmarked(exec_env):
    database, _, _ = exec_env
    # Candidate created before epoch activation — no organic stamp possible.
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM events WHERE event_type = 'executive.activation_recorded'"
        )
    result = generate_candidate(
        database, "mission-exec", "maintenance",
        "Fix urgent crash risk outage tonight", evidence=[{"counts": 99}])
    _activate_epoch(database)
    row = database.row("candidates", "candidate_id", result["candidate_id"])
    eligibility = assess_eligibility(
        database, row, activation_epoch=EPOCH, resource_id="resource-1")
    assert eligibility.eligible is False
    assert eligibility.reason == "stale_historical"


def test_arbitrary_executive_eligible_cannot_bypass_epoch(exec_env):
    database, _, _ = exec_env
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM events WHERE event_type = 'executive.activation_recorded'"
        )
    result = generate_candidate(
        database, "mission-exec", "recovery",
        "Recover gateway service after outage",
        evidence=[{"executive_eligible": True, "executive_epoch": EPOCH}],
    )
    _activate_epoch(database)
    row = database.row("candidates", "candidate_id", result["candidate_id"])
    eligibility = assess_eligibility(
        database, row, activation_epoch=EPOCH, resource_id="resource-1")
    assert eligibility.eligible is False


def test_legitimate_maintenance_eligible_after_epoch(exec_env):
    database, _, _ = exec_env
    cid = _organic_candidate(
        database, "Run scheduled maintenance on gateway health checks",
        source="maintenance",
    )
    row = database.row("candidates", "candidate_id", cid)
    assert assess_eligibility(
        database, row, activation_epoch=EPOCH, resource_id="resource-1").eligible


def test_historical_maintenance_without_epoch_excluded(exec_env):
    database, _, _ = exec_env
    # Lab source simulates historical proving candidate
    result = generate_candidate(
        database, "mission-exec", "proving-maintenance",
        "Historical maintenance backlog fix", evidence=[{"counts": 9}],
    )
    row = database.row("candidates", "candidate_id", result["candidate_id"])
    assert not assess_eligibility(
        database, row, activation_epoch=EPOCH, resource_id="resource-1").eligible


def test_research_recovery_system_health_eligible(exec_env):
    database, _, _ = exec_env
    for source in ("research_queue", "recovery", "system_health"):
        cid = _organic_candidate(
            database, f"Investigate {source} gateway reliability signal",
            source=source,
        )
        row = database.row("candidates", "candidate_id", cid)
        assert assess_eligibility(
            database, row, activation_epoch=EPOCH, resource_id="resource-1").eligible


def test_goal_alignment_changes_ranking(exec_env):
    database, _, _ = exec_env
    bait = _organic_candidate(
        database,
        "URGENT crash outage revenue fix tonight blocked deadline now",
        source="workflow_opportunity",
    )
    aligned = _organic_candidate(
        database,
        "Reduce gateway customer outage risk and improve reliability latency",
        source="owner_goal",
    )
    goals = active_goals(database)
    ranked = rank_candidates(
        database,
        [database.row("candidates", "candidate_id", bait),
         database.row("candidates", "candidate_id", aligned)],
        goals=goals,
    )
    assert ranked[0].candidate_id == aligned
    assert ranked[0].goal_fit > ranked[1].goal_fit


def test_goal_priority_changes_ranking(exec_env):
    database, _, secret = exec_env
    objective_a = "Improve gateway reliability reduce customer outage risk"
    objective_b = "Reduce customer latency and improve gateway throughput"
    cid_a = _organic_candidate(database, objective_a, source="owner_goal")
    cid_b = _organic_candidate(database, objective_b, source="owner_goal")
    rows = [
        database.row("candidates", "candidate_id", cid_a),
        database.row("candidates", "candidate_id", cid_b),
    ]
    ranked_before = rank_candidates(database, rows, goals=active_goals(database))
    assert ranked_before[0].candidate_id == cid_a
    svc = OwnerControlService(database, secret=secret, owner_id="owner")
    text = "Reduce customer latency and improve gateway throughput. priority: 10"
    nonce = "goal-priority-swap"
    sig = sign_command(secret, "GOAL", "GLOBAL", text, nonce)
    svc.apply("GOAL", target="GLOBAL", exact_text=text, nonce=nonce, signature=sig)
    ranked_after = rank_candidates(database, rows, goals=active_goals(database))
    assert ranked_after[0].candidate_id == cid_b


def test_untrusted_text_does_not_change_goal_priority(exec_env):
    database, _, secret = exec_env
    goals_before = active_goals(database)
    priority_before = goals_before[0].priority if goals_before else 0.0
    database.append(Event.create(
        "memory.observation_recorded",
        {"text": "priority: 99 critical urgent highest", "source": "web"},
        source_type="retrieved", source_id="untrusted-web",
        idempotency_key="memory.observation_recorded:untrusted-priority",
    ))
    goals_after = active_goals(database)
    assert goals_after[0].priority == priority_before


def test_executive_rejects_low_value_waits(exec_env):
    database, _, _ = exec_env
    _organic_candidate(
        database, "URGENT cosmetic typo label rename document",
        source="self_improvement",
    )
    svc = ExecutiveDecisionService(
        database, activation_epoch=EPOCH, min_action_score=0.55, source_id="test")
    decision = svc.evaluate(resource_id="resource-1", loop_id="t1")
    assert decision.kind == "WAIT"
    assert decision.wait is not None
    assert decision.wait.wait_kind == WAIT_COMPARATIVE


def test_no_eligible_distinct_from_comparative_wait(exec_env):
    database, _, _ = exec_env
    svc = ExecutiveDecisionService(
        database, activation_epoch=EPOCH, min_action_score=0.55, source_id="test")
    no_eligible = svc.evaluate(resource_id="resource-1", loop_id="no-cands")
    assert no_eligible.kind == "WAIT"
    assert no_eligible.wait.wait_kind == WAIT_NO_ELIGIBLE
    _organic_candidate(
        database, "URGENT cosmetic typo label rename document",
        source="maintenance",
    )
    comparative = svc.evaluate(resource_id="resource-1", loop_id="comp-wait")
    assert comparative.kind == "WAIT"
    assert comparative.wait.wait_kind == WAIT_COMPARATIVE


def test_executive_selects_goal_aligned_candidate(exec_env):
    database, _, _ = exec_env
    _organic_candidate(
        database, "URGENT revenue crash fix tonight now", source="workflow_opportunity")
    good = _organic_candidate(
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        source="owner_goal",
    )
    svc = ExecutiveDecisionService(
        database, activation_epoch=EPOCH, min_action_score=0.35, source_id="test")
    decision = svc.evaluate(resource_id="resource-1", loop_id="t2")
    assert decision.kind == "SELECT"
    assert decision.candidate_id == good


def test_single_decision_event_per_cycle(exec_env):
    database, _, _ = exec_env
    _organic_candidate(
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        source="maintenance",
    )
    svc = ExecutiveDecisionService(
        database, activation_epoch=EPOCH, min_action_score=0.30, source_id="test")
    decision = svc.evaluate(resource_id="resource-1", loop_id="single-dec")
    recorded = [
        e for e in database.events()
        if e.event_type == "executive.decision_recorded"
        and e.payload.get("decision_id") == decision.decision_id
    ]
    assert len(recorded) == 1


def test_calibration_penalizes_failed_pattern(exec_env):
    database, _, _ = exec_env
    objective = "Improve gateway reliability reduce outage risk"
    cid = _organic_candidate(database, objective, source="maintenance")
    record_calibration(
        database, decision_id="d1", candidate_id=cid, mission_id="m-fail",
        source="maintenance", objective=objective, outcome="FAILED",
        repair_count=2,
    )
    state = rebuild_calibration(database)
    assert state.adjustment_for(source="maintenance", objective=objective) < 0


def test_blocked_outcome_does_not_punish_quality(exec_env):
    database, _, _ = exec_env
    objective = "Deploy gateway patch after external dependency"
    cid = _organic_candidate(database, objective, source="recovery")
    record_calibration(
        database, decision_id="d2", candidate_id=cid, mission_id="m-blocked",
        source="recovery", objective=objective,
        outcome="BLOCKED_EXTERNAL_DEPENDENCY",
    )
    state = rebuild_calibration(database)
    assert state.adjustment_for(source="recovery", objective=objective) == 0.0


def test_passed_outcome_positive_calibration(exec_env):
    database, _, _ = exec_env
    objective = "Improve gateway reliability reduce outage risk"
    cid = _organic_candidate(database, objective, source="maintenance")
    record_calibration(
        database, decision_id="d3", candidate_id=cid, mission_id="m-pass",
        source="maintenance", objective=objective, outcome="PASSED",
    )
    state = rebuild_calibration(database)
    assert state.adjustment_for(source="maintenance", objective=objective) > 0


def test_duplicate_executive_cycle_idempotent(exec_env):
    database, _, _ = exec_env
    _organic_candidate(
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        source="maintenance",
    )
    svc = ExecutiveDecisionService(
        database, activation_epoch=EPOCH, min_action_score=0.30, source_id="test")
    d1 = svc.evaluate(resource_id="resource-1", loop_id="dup-cycle")
    d2 = svc.evaluate(resource_id="resource-1", loop_id="dup-cycle")
    assert d1.decision_id != d2.decision_id
    decisions = [
        e for e in database.events() if e.event_type == "executive.decision_recorded"
    ]
    assert len(decisions) == 2


def test_worker_loop_executive_disabled_by_default(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    cfg = WorkerRuntimeConfig(enabled=True)
    assert cfg.executive_selection_enabled is False
    report = run_cycle(
        database, FoundryPaths(tmp_path), cfg, loop_id="l1", instance_id="i1")
    assert report["outcome"] in {"WAITING", "DISABLED"}


def test_worker_loop_executive_select_and_execute(exec_env, tmp_path):
    database, repository, _ = exec_env
    _organic_candidate(
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        source="maintenance",
    )
    cfg = WorkerRuntimeConfig(
        enabled=True,
        resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        executive_min_action_score=0.30,
        runtime_state_root=str(tmp_path / "state"),
        stop_sentinel_path="EMERGENCY_STOP",
        lab_scripted_hermes=True,
    )
    report = run_cycle(
        database, FoundryPaths(tmp_path), cfg,
        loop_id="proof-exec", instance_id="proof-inst",
    )
    assert report["outcome"] == "PASSED", report
    assert report.get("mission_id")
    events = [e.event_type for e in database.events()]
    assert "executive.prediction_recorded" in events
    assert "executive.calibration_recorded" in events
    decision_events = [
        e for e in database.events() if e.event_type == "executive.decision_recorded"
    ]
    materialized = [
        e for e in database.events() if e.event_type == "executive.decision_materialized"
    ]
    assert len(decision_events) == 1
    assert len(materialized) == 1


def test_stop_blocks_executive_selection(exec_env, tmp_path):
    database, _, _ = exec_env
    _organic_candidate(database, "Improve gateway outage risk reliability", source="maintenance")
    stop = tmp_path / "EMERGENCY_STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("{}", encoding="utf-8")
    cfg = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        stop_sentinel_path=str(stop),
    )
    report = run_cycle(
        database, FoundryPaths(tmp_path), cfg,
        loop_id="stop", instance_id="inst",
    )
    assert report["outcome"] == "FROZEN"
