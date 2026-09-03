"""V12 campaign runtime lifecycle and fault-boundary proofs.

These tests use only the existing objective, mission, verifier, effect, wake,
and owner-control authorities.  The campaign aggregate is exercised as a
durable event stream and rebuilt from a fresh database handle where relevant.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    create_objective,
    rebuild_executive_state,
    run_autonomy_cycle,
    set_objective_status,
)
from jarvis_foundry.executive.campaigns import (
    CampaignError,
    apply_replan,
    bind_child_effect,
    bind_child_mission,
    campaign_dispatchable_mission_ids,
    cancel_campaign,
    campaign_snapshot,
    checkpoint_campaign,
    complete_campaign,
    create_campaign,
    declare_child_mission,
    evaluate_joins,
    handle_deadlines,
    propagate_stop,
    record_child_state,
    rebuild_campaign_state,
    reconcile_child_effects,
    reserve_child_retry,
    resume_campaign,
    startup_resume_pass,
)
from jarvis_foundry.executive.continuous import rebuild_wake_state, trigger_wake
from jarvis_foundry.effects import effect_state, finalize_verified_effect
from jarvis_foundry.mission.verification import record_final_outcome


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    create_objective(
        database,
        "V12 campaign objective",
        objective_id="objective-v12",
        success_criteria=("all required child outcomes accepted",),
    )
    return database


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: dict,
    key: str,
    *,
    source_id: str = "v12-proof",
) -> None:
    database.append(
        Event.create(
            event_type,
            payload,
            source_type="supervisor" if event_type.startswith("verification") or event_type == "mission.completed" else "test",
            source_id=source_id,
            idempotency_key=key,
        )
    )


def _accepted_mission(database: FoundryDatabase, mission_id: str) -> str:
    """Materialize the existing mission/verifier acceptance contract."""

    _append(
        database,
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": f"accepted {mission_id}",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["accepted final outcome"],
            "next_action": "done",
        },
        f"mission.created:{mission_id}",
    )
    transitions = (
        "ORIENTING",
        "ACTION_SELECTION",
        "READY",
        "EXECUTING",
        "VERIFYING",
        "ACCEPTING",
        "COMPLETE",
    )
    for index, state in enumerate(transitions):
        _append(
            database,
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": "done"},
            f"mission.state_changed:{mission_id}:{index}",
        )
    _append(
        database,
        "verification.started",
        {
            "verdict_id": f"verdict:{mission_id}",
            "mission_id": mission_id,
            "subject_type": "mission_step",
            "subject_id": "step",
            "verifier_run_id": f"verifier-run:{mission_id}",
            "builder_run_id": f"builder-run:{mission_id}",
            "independence": {"proof": "separate"},
            "criteria": ["accepted final outcome"],
        },
        f"verification.started:{mission_id}",
    )
    _append(
        database,
        "verification.passed",
        {"verdict_id": f"verdict:{mission_id}", "findings": [], "evidence": {}},
        f"verification.passed:{mission_id}",
    )
    _append(
        database,
        "verification.accepted",
        {
            "decision": "ACCEPT",
            "verdict_id": f"verdict:{mission_id}",
            "mission_id": mission_id,
        },
        f"verification.accepted:{mission_id}",
    )
    _append(
        database,
        "mission.completed",
        {"mission_id": mission_id, "objective": f"accepted {mission_id}", "outcome": "PASSED"},
        f"mission.completed:{mission_id}",
    )
    finalized = record_final_outcome(database, mission_id=mission_id)
    assert finalized["finalized"] is True
    return str(finalized["outcome_id"])


def test_checkpoint_rebuild_and_dependency_wait(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-checkpoint")
    declare_child_mission(database, "campaign-checkpoint", child_id="first")
    declare_child_mission(
        database,
        "campaign-checkpoint",
        child_id="second",
        dependencies=("first",),
    )
    record_child_state(
        database,
        "second",
        "WAITING",
        wait_kind="DEPENDENCY",
        wait_reason="waiting for first child acceptance",
        reason="mission materialized before sibling acceptance",
    )
    state = rebuild_campaign_state(database)
    assert state.children["second"].status == "WAITING"
    assert state.children["second"].wait_kind == "DEPENDENCY"
    checkpoint = state.checkpoints["campaign-checkpoint"]
    assert checkpoint.pending_work[1]["unmet_dependencies"] == ["first"]

    # A second startup pass is a no-op checkpoint-wise, proving durable
    # reconstruction instead of a process-local context cache.
    before = database.event_count(event_type="campaign.checkpoint_recorded")
    startup_resume_pass(database, resume_id="checkpoint-replay-1")
    after_first = database.event_count(event_type="campaign.checkpoint_recorded")
    startup_resume_pass(database, resume_id="checkpoint-replay-2")
    after = database.event_count(event_type="campaign.checkpoint_recorded")
    assert after_first >= before
    assert after == after_first
    assert campaign_snapshot(database)["campaigns"]["campaign-checkpoint"]["checkpoint"]


def test_campaign_contract_rejects_invalid_deadline_and_retry_budget(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with pytest.raises(CampaignError, match="deadline"):
        create_campaign(
            database,
            "objective-v12",
            campaign_id="campaign-invalid-deadline",
            deadline="not-a-timestamp",
        )
    with pytest.raises(CampaignError, match="finite"):
        create_campaign(
            database,
            "objective-v12",
            campaign_id="campaign-invalid-budget",
            retry_budget={"total": float("nan")},
        )


def test_per_child_retry_budget_is_narrower_than_campaign_total(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(
        database,
        "objective-v12",
        campaign_id="campaign-child-budget",
        retry_budget={"budgeted-child": 0, "total": 3},
    )
    declare_child_mission(database, "campaign-child-budget", child_id="budgeted-child")
    record_child_state(database, "budgeted-child", "FAILED", reason="bounded child fault")
    assert reserve_child_retry(database, "budgeted-child") is None
    assert rebuild_campaign_state(database).campaigns["campaign-child-budget"].status == "ESCALATED"


def test_child_binding_and_retry_require_canonical_authority(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(
        database,
        "objective-v12",
        campaign_id="campaign-child-authority",
        join_conditions={"join": {"children": ["one"], "mode": "ALL"}},
    )
    declare_child_mission(database, "campaign-child-authority", child_id="one")
    declare_child_mission(database, "campaign-child-authority", child_id="two")
    with pytest.raises(CampaignError, match="not listed in join"):
        # A child cannot claim a join id that does not include it.
        declare_child_mission(
            database,
            "campaign-child-authority",
            child_id="three",
            join_ids=("join",),
        )
    with pytest.raises(CampaignError, match="requires a bound canonical mission"):
        record_child_state(database, "one", "READY")
    _append(
        database,
        "mission.created",
        {
            "mission_id": "mission-child-authority",
            "objective": "canonical child authority",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["retry acceptance"],
            "next_action": "retry",
        },
        "mission.created:child-authority",
    )
    bind_child_mission(database, "one", "mission-child-authority")
    record_child_state(database, "one", "FAILED", reason="canonical failure")
    with pytest.raises(CampaignError, match="retry reservation"):
        record_child_state(database, "one", "READY")


def test_time_wait_provider_outage_and_recovery_wake(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-waits")
    declare_child_mission(database, "campaign-waits", child_id="time")
    declare_child_mission(
        database,
        "campaign-waits",
        child_id="provider",
        provider_id="provider-v12",
    )
    record_child_state(
        database,
        "time",
        "WAITING",
        wait_kind="TIME",
        wait_until="2020-01-01T00:00:00Z",
        reason="not before deadline",
    )
    _append(
        database,
        "provider.outage",
        {"provider_id": "provider-v12", "status": "UNAVAILABLE"},
        "provider.outage:v12",
    )
    outage = startup_resume_pass(
        database,
        now="2021-01-01T00:00:00Z",
        resume_id="wait-outage",
    )
    state = rebuild_campaign_state(database)
    # A released wait without a canonical MissionLoop binding is pending
    # materialization, never synthetic READY dispatch authority.
    assert state.children["time"].status == "PENDING"
    assert state.children["provider"].status == "WAITING"
    assert state.children["provider"].wait_kind == "PROVIDER"
    assert rebuild_executive_state(database).objectives["objective-v12"].status == "WAITING"
    assert "campaign-waits" in outage.waiting_campaigns
    assert any(
        wake["kind"] == "WAIT_FOR_PROVIDER"
        for wake in campaign_snapshot(database, "campaign-waits")["campaigns"]["campaign-waits"]["wakes"]
    )

    _append(
        database,
        "provider.health_sample",
        {
            "provider_id": "provider-v12",
            "health": "HEALTHY",
            "auth_state": "READY",
            "availability": "AVAILABLE",
            "status": "READY",
        },
        "provider.health_sample:v12-recovery",
    )
    recovered = startup_resume_pass(database, resume_id="wait-recovery")
    state = rebuild_campaign_state(database)
    assert state.children["provider"].status == "PENDING"
    assert state.campaigns["campaign-waits"].status == "EXECUTING"
    assert rebuild_executive_state(database).objectives["objective-v12"].status == "ACTIVE"
    assert database.event_count(event_type="executive.wake_triggered") >= 1
    assert not any(
        wake.get("objective_id") == "objective-v12"
        and wake.get("kind") == "WAIT_FOR_PROVIDER"
        and wake.get("status") == "ACTIVE"
        for wake in rebuild_wake_state(database).values()
    )
    assert not recovered.errors


def test_rich_provider_degradation_overrides_status_ready(tmp_path: Path) -> None:
    """A misleading status field cannot bypass health/availability gates."""

    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-provider-rich")
    declare_child_mission(
        database,
        "campaign-provider-rich",
        child_id="provider-child",
        provider_id="provider-rich",
    )
    _append(
        database,
        "provider.health_sample",
        {
            "provider_id": "provider-rich",
            "status": "READY",
            "health": "DEGRADED",
            "auth_state": "READY",
            "availability": "UNAVAILABLE",
        },
        "provider.health_sample:provider-rich-degraded",
    )
    report = startup_resume_pass(database, resume_id="provider-rich-degraded")
    state = rebuild_campaign_state(database)
    assert not report.errors
    assert state.children["provider-child"].status == "WAITING"
    assert state.children["provider-child"].wait_kind == "PROVIDER"


def test_time_wake_releases_objective_wait_without_unbound_dispatch(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-time-only")
    declare_child_mission(database, "campaign-time-only", child_id="time-child")
    record_child_state(
        database,
        "time-child",
        "WAITING",
        wait_kind="TIME",
        wait_until="2020-01-01T00:00:00Z",
        reason="hold until the due time",
    )
    assert rebuild_executive_state(database).objectives["objective-v12"].status == "WAITING"

    report = startup_resume_pass(
        database,
        now="2021-01-01T00:00:00Z",
        resume_id="time-only-recovery",
    )
    state = rebuild_campaign_state(database)
    assert not report.errors
    assert state.children["time-child"].status == "PENDING"
    assert state.campaigns["campaign-time-only"].status == "EXECUTING"
    assert rebuild_executive_state(database).objectives["objective-v12"].status == "ACTIVE"


def test_parallel_children_join_and_acceptance_truth(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first_outcome = _accepted_mission(database, "mission-parallel-a")
    second_outcome = _accepted_mission(database, "mission-parallel-b")
    create_campaign(
        database,
        "objective-v12",
        campaign_id="campaign-join",
        join_conditions={
            "parallel-all": {
                "children": ["child-a", "child-b"],
                "mode": "ALL",
            }
        },
    )
    declare_child_mission(database, "campaign-join", child_id="child-a", mission_id="mission-parallel-a", join_ids=("parallel-all",))
    declare_child_mission(database, "campaign-join", child_id="child-b", mission_id="mission-parallel-b", join_ids=("parallel-all",), sibling_group="parallel")

    result = evaluate_joins(database, "campaign-join")
    state = rebuild_campaign_state(database)
    assert result["completed"] is True
    assert state.campaigns["campaign-join"].status == "COMPLETED"
    assert state.children["child-a"].acceptance_outcome_id == first_outcome
    assert state.children["child-b"].acceptance_outcome_id == second_outcome
    assert rebuild_executive_state(database).objectives["objective-v12"].status == "COMPLETED"
    assert database.event_count(event_type="campaign.completed") == 1
    assert complete_campaign(database, "campaign-join") is True
    assert database.event_count(event_type="campaign.completed") == 1


def test_campaign_completion_respects_blocked_objective_authority(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outcome_id = _accepted_mission(database, "mission-objective-blocked")
    create_campaign(database, "objective-v12", campaign_id="campaign-objective-blocked")
    declare_child_mission(
        database,
        "campaign-objective-blocked",
        child_id="accepted",
        mission_id="mission-objective-blocked",
    )
    set_objective_status(
        database,
        "objective-v12",
        "BLOCKED",
        reason="owner blocked objective",
    )
    assert evaluate_joins(database, "campaign-objective-blocked")["completed"] is False
    state = rebuild_campaign_state(database)
    assert state.children["accepted"].acceptance_outcome_id == outcome_id
    assert state.campaigns["campaign-objective-blocked"].status != "COMPLETED"


def test_sibling_campaign_completion_does_not_prematurely_complete_objective(tmp_path: Path) -> None:
    """One decomposition may finish without hiding another's pending work."""

    database = _database(tmp_path)
    first_outcome = _accepted_mission(database, "mission-sibling-campaign-a")
    second_outcome = _accepted_mission(database, "mission-sibling-campaign-b")
    create_campaign(database, "objective-v12", campaign_id="campaign-sibling-a")
    create_campaign(database, "objective-v12", campaign_id="campaign-sibling-b")
    declare_child_mission(
        database,
        "campaign-sibling-a",
        child_id="child-a",
        mission_id="mission-sibling-campaign-a",
    )
    declare_child_mission(
        database,
        "campaign-sibling-b",
        child_id="child-b",
        mission_id="mission-sibling-campaign-b",
    )

    first = evaluate_joins(database, "campaign-sibling-a")
    state = rebuild_campaign_state(database)
    assert first["completed"] is True
    assert state.campaigns["campaign-sibling-a"].status == "COMPLETED"
    assert state.campaigns["campaign-sibling-b"].status == "EXECUTING"
    assert state.children["child-a"].acceptance_outcome_id == first_outcome
    assert rebuild_executive_state(database).objectives["objective-v12"].status == "ACTIVE"

    second = evaluate_joins(database, "campaign-sibling-b")
    state = rebuild_campaign_state(database)
    assert second["completed"] is True
    assert state.children["child-b"].acceptance_outcome_id == second_outcome
    assert rebuild_executive_state(database).objectives["objective-v12"].status == "COMPLETED"


def test_scoped_stop_mission_directive_propagates_and_resume_rechecks_control(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-scoped-stop")
    declare_child_mission(database, "campaign-scoped-stop", child_id="scoped-child")
    _append(
        database,
        "owner_directive.recorded",
        {
            "directive_id": "stop-campaign-scoped",
            "command_type": "STOP_MISSION",
            "target": "campaign-scoped-stop",
            "exact_text": "stop campaign scoped",
            "reason": "owner paused campaign",
        },
        "owner_directive.recorded:stop-campaign-scoped",
    )
    report = startup_resume_pass(database, resume_id="scoped-stop-recovery")
    state = rebuild_campaign_state(database)
    assert report.status == "STOPPED"
    assert state.campaigns["campaign-scoped-stop"].status == "STOPPED"
    assert state.children["scoped-child"].status == "STOPPED"

    # The active directive still blocks an explicit resume; clearing/revoking
    # it is an owner action outside the campaign aggregate.
    with pytest.raises(PermissionError, match="stopped"):
        resume_campaign(database, "campaign-scoped-stop")


def test_partial_effect_is_frozen_before_join(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-effect")
    declare_child_mission(database, "campaign-effect", child_id="effect-child")
    bind_child_effect(database, "effect-child", "effect:v12:partial")
    _append(
        database,
        "system.action_started",
        {
            "action_key": "effect:v12:partial",
            "system_id": "system-v12",
            "target_ref": "file:/tmp/v12-proof",
            "payload_hash": "hash-v12",
            "kind": "file_write",
        },
        "system.action_started:effect:v12:partial",
    )
    reconciled = reconcile_child_effects(database, "campaign-effect")
    assert reconciled[0]["status"] == "FROZEN"
    assert effect_state(database, "effect:v12:partial")["status"] == "FROZEN"
    child = rebuild_campaign_state(database).children["effect-child"]
    assert child.status == "WAITING"
    assert child.wait_kind == "EFFECT_RECONCILIATION"
    assert database.event_count(event_type="system.action_started") == 1


def test_reconciliation_alone_cannot_release_join_without_terminal_truth(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    mission_id = "mission-effect-terminal-truth"
    outcome_id = _accepted_mission(database, mission_id)
    effect_key = "effect:v12:terminal-truth"
    create_campaign(
        database,
        "objective-v12",
        campaign_id="campaign-effect-terminal-truth",
    )
    declare_child_mission(
        database,
        "campaign-effect-terminal-truth",
        child_id="effect-terminal-child",
        mission_id=mission_id,
    )
    bind_child_effect(database, "effect-terminal-child", effect_key)
    _append(
        database,
        "system.action_started",
        {
            "action_key": effect_key,
            "system_id": "system-v12",
            "target_ref": "file:/tmp/v12-terminal-truth",
            "payload_hash": "hash-v12-terminal-truth",
            "kind": "file_write",
        },
        "system.action_started:effect:v12:terminal-truth",
    )
    _append(
        database,
        "effect.frozen",
        {
            "action_key": effect_key,
            "reason": "provider outcome was initially ambiguous",
        },
        "effect.frozen:effect:v12:terminal-truth",
    )
    _append(
        database,
        "effect.reconciled",
        {
            "action_key": effect_key,
            "provider_claim": {"status": "MATCH", "matches": True},
        },
        "effect.reconciled:effect:v12:terminal-truth",
    )

    before = evaluate_joins(database, "campaign-effect-terminal-truth")
    child = rebuild_campaign_state(database).children["effect-terminal-child"]
    assert before["completed"] is False
    assert child.status == "WAITING"
    assert child.acceptance_outcome_id is None
    assert database.event_count(event_type="effect.terminal_truth") == 0

    finalized = finalize_verified_effect(
        database,
        effect_key,
        provider_receipt={
            "provider_id": "disposable-v12-provider",
            "provider_ref": "disposable:v12:terminal-truth",
        },
        readback={
            "status": "MATCH",
            "matches": True,
            "sha256": "a" * 64,
        },
        result={
            "claimed": "COMPLETED",
            "path": "disposable:v12:terminal-truth",
        },
        acceptance_evidence={
            "authority": "independent-disposable-readback",
            "outcome_id": outcome_id,
        },
        reconciled=True,
    )
    assert finalized["terminal_truth"] is True
    after = evaluate_joins(database, "campaign-effect-terminal-truth")
    assert after["completed"] is True
    assert (
        rebuild_campaign_state(database)
        .children["effect-terminal-child"]
        .acceptance_outcome_id
        == outcome_id
    )
    assert database.event_count(event_type="effect.terminal_truth") == 1


def test_missing_effect_projection_is_durable_unknown_wait(tmp_path: Path) -> None:
    """A missing effect row parks the child without breaking checkpointing."""

    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-effect-missing")
    declare_child_mission(database, "campaign-effect-missing", child_id="missing-effect")
    bind_child_effect(database, "missing-effect", "effect:v12:missing")

    first = startup_resume_pass(database, resume_id="missing-effect-first")
    state = rebuild_campaign_state(database)
    child = state.children["missing-effect"]
    assert not first.errors
    assert child.status == "WAITING"
    assert child.wait_kind == "EFFECT_RECONCILIATION"
    pending = campaign_snapshot(database, "campaign-effect-missing")["campaigns"]["campaign-effect-missing"]["pending_work"]
    assert pending[0]["effect_states"] == {"effect:v12:missing": "UNKNOWN"}

    # Replaying the same missing projection remains an honest no-op rather
    # than producing a PARTIAL recovery report or duplicate effect receipt.
    second = startup_resume_pass(database, resume_id="missing-effect-second")
    assert not second.errors
    assert database.event_count(event_type="campaign.effect_reconciled") == 1


def test_stale_succeeded_child_projection_becomes_owner_visible_pending_work(tmp_path: Path) -> None:
    """A forged/stale SUCCEEDED receipt cannot disappear from checkpoints."""

    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-stale-success")
    declare_child_mission(database, "campaign-stale-success", child_id="stale")
    campaign = rebuild_campaign_state(database).campaigns["campaign-stale-success"]
    _append(
        database,
        "campaign.child_state_changed",
        {
            "schema": "v12.campaign-runtime.v1",
            "campaign_id": campaign.campaign_id,
            "child_id": "stale",
            "status": "SUCCEEDED",
            "reason": "synthetic stale acceptance",
            "version": 2,
        },
        "campaign.child_state_changed:stale:synthetic",
    )
    report = startup_resume_pass(database, resume_id="stale-success-recovery")
    state = rebuild_campaign_state(database)
    snapshot = campaign_snapshot(database, "campaign-stale-success")["campaigns"]["campaign-stale-success"]
    assert not report.errors
    assert state.campaigns["campaign-stale-success"].status == "WAITING"
    assert snapshot["pending_work"][0]["child_id"] == "stale"
    assert any(
        wake["kind"] == "WAIT_FOR_OWNER"
        for wake in snapshot["wakes"]
    )


def test_effect_reconciliation_is_idempotent_across_startup_replays(tmp_path: Path) -> None:
    """A frozen partial effect must not turn the next startup into PARTIAL."""

    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-effect-replay")
    declare_child_mission(database, "campaign-effect-replay", child_id="effect-child")
    bind_child_effect(database, "effect-child", "effect:v12:replay")
    _append(
        database,
        "system.action_started",
        {
            "action_key": "effect:v12:replay",
            "system_id": "system-v12",
            "target_ref": "file:/tmp/v12-replay",
            "payload_hash": "hash-v12-replay",
            "kind": "file_write",
        },
        "system.action_started:effect:v12:replay",
    )
    first = startup_resume_pass(database, resume_id="effect-replay-first")
    second = startup_resume_pass(database, resume_id="effect-replay-second")
    assert first.status in {"WAITING", "READY"}
    assert not second.errors
    assert second.status == "WAITING"
    assert database.event_count(event_type="campaign.effect_reconciled") == 1


def test_deadline_replan_then_escalation_never_silent(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(
        database,
        "objective-v12",
        campaign_id="campaign-deadline",
        deadline="2020-01-01T00:00:00Z",
        max_replans=1,
    )
    declare_child_mission(database, "campaign-deadline", child_id="deadline-child")
    first = handle_deadlines(database, now="2021-01-01T00:00:00Z")
    assert first["replan_required"] == ["campaign-deadline"]
    assert rebuild_campaign_state(database).campaigns["campaign-deadline"].status == "REPLANNING"
    apply_replan(database, "campaign-deadline", deadline="2020-01-01T00:00:00Z")
    second = handle_deadlines(database, now="2021-01-01T00:00:00Z")
    assert second["escalated"] == ["campaign-deadline"]
    assert rebuild_campaign_state(database).campaigns["campaign-deadline"].status == "ESCALATED"
    assert database.event_count(event_type="executive.escalation_opened") == 1


def test_stop_resume_cancel_and_bounded_retry(tmp_path: Path) -> None:
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    create_campaign(database, "objective-v12", campaign_id="campaign-stop")
    declare_child_mission(database, "campaign-stop", child_id="stop-child")
    stop_path.write_text("v12 stop", encoding="utf-8")
    assert propagate_stop(database, stop_path=stop_path) == ["campaign-stop"]
    assert rebuild_campaign_state(database).children["stop-child"].status == "STOPPED"
    stop_path.unlink()
    resumed = startup_resume_pass(database, stop_path=stop_path, resume_id="stop-resume")
    assert resumed.resumed_campaigns == ("campaign-stop",)
    assert rebuild_campaign_state(database).children["stop-child"].status == "PENDING"

    create_campaign(database, "objective-v12", campaign_id="campaign-stop-wait")
    declare_child_mission(database, "campaign-stop-wait", child_id="wait-child")
    record_child_state(
        database,
        "wait-child",
        "WAITING",
        wait_kind="TIME",
        wait_until="2099-01-01T00:00:00Z",
        reason="preserve this wait across STOP",
    )
    stop_path.write_text("v12 stop", encoding="utf-8")
    propagate_stop(database, stop_path=stop_path)
    stop_path.unlink()
    startup_resume_pass(database, stop_path=stop_path, resume_id="stop-wait-resume")
    wait_state = rebuild_campaign_state(database)
    assert wait_state.campaigns["campaign-stop-wait"].status == "WAITING"
    assert wait_state.children["wait-child"].status == "WAITING"
    assert wait_state.children["wait-child"].wait_kind == "TIME"

    create_campaign(database, "objective-v12", campaign_id="campaign-retry")
    declare_child_mission(database, "campaign-retry", child_id="retry-child", max_retries=0)
    record_child_state(database, "retry-child", "FAILED", reason="bounded fault")
    assert reserve_child_retry(database, "retry-child") is None
    assert reserve_child_retry(database, "retry-child") is None
    assert rebuild_campaign_state(database).campaigns["campaign-retry"].status == "ESCALATED"

    create_campaign(database, "objective-v12", campaign_id="campaign-cancel")
    declare_child_mission(database, "campaign-cancel", child_id="cancel-child")
    cancelled = cancel_campaign(database, "campaign-cancel", reason="owner withdrew campaign")
    assert cancelled.status == "CANCELLED"
    assert rebuild_campaign_state(database).children["cancel-child"].status == "CANCELLED"
    assert cancel_campaign(database, "campaign-cancel", reason="idempotent repeat").status == "CANCELLED"


def test_bounded_retry_reopens_canonical_blocked_mission(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-retry-reopen")
    _append(
        database,
        "mission.created",
        {
            "mission_id": "mission-retry-reopen",
            "objective": "retryable mission",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["retry acceptance"],
            "next_action": "retry",
        },
        "mission.created:retry-reopen",
    )
    for index, mission_state in enumerate(("ORIENTING", "ACTION_SELECTION", "READY", "BLOCKED")):
        _append(
            database,
            "mission.state_changed",
            {
                "mission_id": "mission-retry-reopen",
                "state": mission_state,
                "next_action": "retry",
            },
            f"mission.state_changed:retry-reopen:{index}",
        )
    declare_child_mission(
        database,
        "campaign-retry-reopen",
        child_id="retryable-child",
        mission_id="mission-retry-reopen",
        max_retries=1,
    )
    record_child_state(database, "retryable-child", "FAILED", reason="bounded retryable failure")
    retried = reserve_child_retry(database, "retryable-child")
    assert retried is not None
    assert retried.status == "READY"
    assert database.row("missions", "mission_id", "mission-retry-reopen")["state"] == "ACTION_SELECTION"
    assert "mission-retry-reopen" in campaign_dispatchable_mission_ids(database)


def test_retry_reservation_is_atomic_and_external_reopen_does_not_consume_budget(tmp_path: Path) -> None:
    """A mission reopen without a campaign receipt remains terminal.

    The retry path commits mission reopening and retry-budget consumption in
    one ledger transaction, so a process kill between those steps cannot
    create an unaccounted retry on the next startup pass.
    """

    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-retry-atomic")
    _append(
        database,
        "mission.created",
        {
            "mission_id": "mission-retry-atomic",
            "objective": "atomic retry mission",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["retry acceptance"],
            "next_action": "retry",
        },
        "mission.created:retry-atomic",
    )
    for index, mission_state in enumerate(("ORIENTING", "ACTION_SELECTION", "READY", "BLOCKED")):
        _append(
            database,
            "mission.state_changed",
            {
                "mission_id": "mission-retry-atomic",
                "state": mission_state,
                "next_action": "retry",
            },
            f"mission.state_changed:retry-atomic:{index}",
        )
    declare_child_mission(
        database,
        "campaign-retry-atomic",
        child_id="atomic-child",
        mission_id="mission-retry-atomic",
        max_retries=1,
    )
    record_child_state(database, "atomic-child", "FAILED", reason="first attempt failed")

    # Simulate an unrelated recovery reopening the canonical mission.  The
    # campaign must not infer a retry budget reservation from that state.
    _append(
        database,
        "mission.state_changed",
        {
            "mission_id": "mission-retry-atomic",
            "state": "ACTION_SELECTION",
            "next_action": "retry",
            "reason": "external recovery reopened selection",
        },
        "mission.state_changed:retry-atomic:external-reopen",
    )
    startup_resume_pass(database, resume_id="retry-atomic-external-reopen")
    parked = rebuild_campaign_state(database).children["atomic-child"]
    assert parked.status == "FAILED"
    assert parked.retries_used == 0
    assert database.event_count(event_type="campaign.retry_reserved") == 0

    retried = reserve_child_retry(database, "atomic-child")
    assert retried is not None
    state = rebuild_campaign_state(database)
    assert state.children["atomic-child"].status == "READY"
    assert state.children["atomic-child"].retries_used == 1
    retry_events = [
        event for event in database.events()
        if event.event_type == "campaign.retry_reserved"
        and event.payload.get("child_id") == "atomic-child"
    ]
    assert len(retry_events) == 1
    assert database.row("missions", "mission_id", "mission-retry-atomic")["state"] == "ACTION_SELECTION"


def test_campaign_objective_is_not_replaced_by_autonomy_admission(tmp_path: Path) -> None:
    database = _database(tmp_path)
    excluded_objective_id = "objective-v12-excluded-gap"
    create_objective(
        database,
        "Campaign-owned objective with a deliberately missing capability",
        objective_id=excluded_objective_id,
        required_capabilities=("cap.campaign.excluded.fixture",),
        success_criteria=("campaign owns admission",),
    )
    create_campaign(
        database,
        excluded_objective_id,
        campaign_id="campaign-admission-boundary",
    )
    cycle = run_autonomy_cycle(
        database,
        cycle_id="campaign-admission-boundary-cycle",
        excluded_objective_ids=("objective-v12", excluded_objective_id),
    )
    assert cycle.objective_id is None
    assert not any(
        event.event_type == "executive.mission_requested"
        and event.payload.get("objective_id") in {
            "objective-v12",
            excluded_objective_id,
        }
        for event in database.events()
    )
    assert not any(
        event.event_type == "fabric.capability_gap.opened"
        and event.payload.get("origin_objective_id") == excluded_objective_id
        for event in database.events()
    )


def test_stop_resume_reconciles_child_accepted_while_stopped(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outcome_id = _accepted_mission(database, "mission-stop-accepted")
    create_campaign(database, "objective-v12", campaign_id="campaign-stop-accepted")
    declare_child_mission(
        database,
        "campaign-stop-accepted",
        child_id="accepted-child",
        mission_id="mission-stop-accepted",
    )
    stop_path = tmp_path / "EMERGENCY_STOP"
    stop_path.write_text("v12 stop", encoding="utf-8")
    propagate_stop(database, stop_path=stop_path)
    stop_path.unlink()

    report = startup_resume_pass(
        database,
        stop_path=stop_path,
        resume_id="stop-accepted-resume",
    )
    state = rebuild_campaign_state(database)
    assert "campaign-stop-accepted" in report.completed_campaigns
    assert state.children["accepted-child"].status == "SUCCEEDED"
    assert state.children["accepted-child"].acceptance_outcome_id == outcome_id
    assert state.campaigns["campaign-stop-accepted"].status == "COMPLETED"
    assert rebuild_executive_state(database).objectives["objective-v12"].status == "COMPLETED"


def test_startup_finishes_partial_resume_without_synthetic_dispatch(tmp_path: Path) -> None:
    """A kill after the parent RESUME receipt must not strand STOPPED children."""

    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    create_campaign(database, "objective-v12", campaign_id="campaign-partial-resume")
    declare_child_mission(database, "campaign-partial-resume", child_id="pending-child")
    declare_child_mission(database, "campaign-partial-resume", child_id="failed-child")
    record_child_state(database, "failed-child", "FAILED", reason="explicit failure requires owner retry")
    stop_path.write_text("v12 partial resume", encoding="utf-8")
    propagate_stop(database, stop_path=stop_path)
    stop_path.unlink()

    # Simulate the durable parent receipt being committed immediately before
    # process death, with neither child RESUME receipt committed yet.
    stopped = rebuild_campaign_state(database).campaigns["campaign-partial-resume"]
    _append(
        database,
        "campaign.resumed",
        {
            "schema": "v12.campaign-runtime.v1",
            "campaign_id": stopped.campaign_id,
            "objective_id": stopped.objective_id,
            "status": "EXECUTING",
            "resume_status": None,
            "reason": "simulated crash after parent resume receipt",
            "version": stopped.version + 1,
        },
        "campaign.resumed:partial-crash",
    )

    report = startup_resume_pass(
        database,
        stop_path=stop_path,
        resume_id="partial-resume-recovery",
    )
    state = rebuild_campaign_state(database)
    assert "campaign-partial-resume" in report.resumed_campaigns
    assert state.campaigns["campaign-partial-resume"].status == "EXECUTING"
    assert state.children["pending-child"].status == "PENDING"
    assert state.children["failed-child"].status == "FAILED"
    assert not campaign_snapshot(database, "campaign-partial-resume")["campaigns"]["campaign-partial-resume"]["pending_work"] == []


def test_startup_repairs_partial_terminal_parent_receipts(tmp_path: Path) -> None:
    """Parent-terminal receipts cannot strand unfinished child projections."""

    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-terminal-repair")
    declare_child_mission(database, "campaign-terminal-repair", child_id="unfinished")
    campaign = rebuild_campaign_state(database).campaigns["campaign-terminal-repair"]
    _append(
        database,
        "campaign.cancelled",
        {
            "schema": "v12.campaign-runtime.v1",
            "campaign_id": campaign.campaign_id,
            "objective_id": campaign.objective_id,
            "status": "CANCELLED",
            "reason": "simulated kill after parent cancellation",
            "version": campaign.version + 1,
        },
        "campaign.cancelled:terminal-repair",
    )
    startup_resume_pass(database, resume_id="terminal-cancel-repair")
    state = rebuild_campaign_state(database)
    assert state.children["unfinished"].status == "CANCELLED"
    assert state.checkpoints["campaign-terminal-repair"].status == "CANCELLED"
    assert rebuild_executive_state(database).objectives["objective-v12"].status == "CANCELLED"

    # A second terminal crash window is completion status written before the
    # summary receipt.  Startup repairs the receipt without re-running work.
    accepted_outcome = _accepted_mission(database, "mission-terminal-complete")
    create_objective(
        database,
        "V12 completion terminal repair objective",
        objective_id="objective-v12-complete-repair",
        success_criteria=("accepted child",),
    )
    create_campaign(database, "objective-v12-complete-repair", campaign_id="campaign-complete-repair")
    declare_child_mission(
        database,
        "campaign-complete-repair",
        child_id="accepted",
        mission_id="mission-terminal-complete",
    )
    current = rebuild_campaign_state(database).campaigns["campaign-complete-repair"]
    _append(
        database,
        "campaign.status_changed",
        {
            "schema": "v12.campaign-runtime.v1",
            "campaign_id": current.campaign_id,
            "objective_id": current.objective_id,
            "status": "COMPLETED",
            "reason": "simulated kill after campaign status transition",
            "version": current.version + 1,
        },
        "campaign.status_changed:complete-repair",
    )
    startup_resume_pass(database, resume_id="terminal-complete-repair")
    state = rebuild_campaign_state(database)
    assert state.campaigns["campaign-complete-repair"].status == "COMPLETED"
    assert state.children["accepted"].acceptance_outcome_id == accepted_outcome
    assert any(
        event.event_type == "campaign.completed"
        and event.payload.get("campaign_id") == "campaign-complete-repair"
        for event in database.events()
    )
    assert state.checkpoints["campaign-complete-repair"].status == "COMPLETED"
    assert rebuild_executive_state(database).objectives["objective-v12-complete-repair"].status == "COMPLETED"


def test_any_join_waits_for_a_viable_sibling_after_one_failure(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(
        database,
        "objective-v12",
        campaign_id="campaign-any",
        join_conditions={
            "first-available": {
                "children": ["any-failed", "any-success"],
                "mode": "ANY",
            }
        },
    )
    declare_child_mission(
        database,
        "campaign-any",
        child_id="any-failed",
        join_ids=("first-available",),
    )
    declare_child_mission(
        database,
        "campaign-any",
        child_id="any-success",
        join_ids=("first-available",),
    )
    record_child_state(database, "any-failed", "FAILED", reason="one sibling failed")
    waiting = evaluate_joins(database, "campaign-any")
    assert waiting["joins"]["first-available"]["status"] == "WAITING"
    assert waiting["completed"] is False

    _accepted_mission(database, "mission-any-success")
    bind_child_mission(database, "any-success", "mission-any-success")
    done = evaluate_joins(database, "campaign-any")
    state = rebuild_campaign_state(database)
    assert done["joins"]["first-available"]["status"] == "SATISFIED"
    assert state.campaigns["campaign-any"].status == "COMPLETED"


def test_accepted_truth_wins_over_expired_deadline_before_child_projection(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outcome_id = _accepted_mission(database, "mission-deadline-already-accepted")
    create_campaign(
        database,
        "objective-v12",
        campaign_id="campaign-deadline-accepted",
        deadline="2020-01-01T00:00:00Z",
    )
    declare_child_mission(
        database,
        "campaign-deadline-accepted",
        child_id="accepted-before-deadline",
        mission_id="mission-deadline-already-accepted",
    )
    report = startup_resume_pass(
        database,
        now="2021-01-01T00:00:00Z",
        resume_id="deadline-accepted-recovery",
    )
    state = rebuild_campaign_state(database)
    assert report.escalated_campaigns == ()
    assert report.replan_required == ()
    assert state.children["accepted-before-deadline"].acceptance_outcome_id == outcome_id
    assert state.campaigns["campaign-deadline-accepted"].status == "COMPLETED"
    assert database.event_count(event_type="campaign.deadline_replan_requested") == 0


def test_repeated_startup_dedupes_accepted_child_projection(tmp_path: Path) -> None:
    database = _database(tmp_path)
    outcome_id = _accepted_mission(database, "mission-replay-accepted")
    create_campaign(database, "objective-v12", campaign_id="campaign-replay-accepted")
    declare_child_mission(
        database,
        "campaign-replay-accepted",
        child_id="accepted",
        mission_id="mission-replay-accepted",
    )
    # Keep a second sibling pending so the campaign remains non-terminal and
    # every subsequent startup pass must inspect the already accepted child.
    declare_child_mission(database, "campaign-replay-accepted", child_id="pending")
    startup_resume_pass(database, resume_id="accepted-replay-first")
    child_events_before = database.event_count(event_type="campaign.child_state_changed")
    startup_resume_pass(database, resume_id="accepted-replay-second")
    child_events_after = database.event_count(event_type="campaign.child_state_changed")
    state = rebuild_campaign_state(database)
    assert state.children["accepted"].status == "SUCCEEDED"
    assert state.children["accepted"].acceptance_outcome_id == outcome_id
    assert child_events_after == child_events_before


def test_triggered_unreleased_dependency_wake_is_reused(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_campaign(database, "objective-v12", campaign_id="campaign-wake-reuse")
    declare_child_mission(database, "campaign-wake-reuse", child_id="unaccepted-sibling")
    declare_child_mission(
        database,
        "campaign-wake-reuse",
        child_id="dependency-child",
        dependencies=("unaccepted-sibling",),
    )
    record_child_state(
        database,
        "dependency-child",
        "WAITING",
        wait_kind="DEPENDENCY",
        wait_reason="still waiting for a sibling",
    )
    first = startup_resume_pass(database, resume_id="wake-reuse-first")
    assert first.waiting_campaigns == ("campaign-wake-reuse",)
    first_wakes = campaign_snapshot(database, "campaign-wake-reuse")["campaigns"]["campaign-wake-reuse"]["wakes"]
    dependency_wakes = [wake for wake in first_wakes if wake["kind"] == "WAIT_FOR_SIGNAL"]
    assert dependency_wakes
    trigger_wake(database, dependency_wakes[0]["wake_id"], reason="unrelated signal arrived")
    before = database.event_count(event_type="executive.wake_created")
    second = startup_resume_pass(database, resume_id="wake-reuse-second")
    after = database.event_count(event_type="executive.wake_created")
    assert second.waiting_campaigns == ("campaign-wake-reuse",)
    assert after == before


def test_startup_projects_bound_dispatchable_mission_to_ready_child(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _append(
        database,
        "mission.created",
        {
            "mission_id": "mission-ready-child",
            "objective": "ready child",
            "resource_id": None,
            "state": "READY",
            "acceptance_criteria": ["accepted final outcome"],
            "next_action": "continue",
        },
        "mission.created:ready-child",
    )
    create_campaign(database, "objective-v12", campaign_id="campaign-ready-child")
    declare_child_mission(
        database,
        "campaign-ready-child",
        child_id="ready-child",
        mission_id="mission-ready-child",
    )
    startup_resume_pass(database, resume_id="ready-child-recovery")
    state = rebuild_campaign_state(database)
    assert state.children["ready-child"].status == "READY"
    assert campaign_snapshot(database, "campaign-ready-child")["campaigns"]["campaign-ready-child"]["status"] == "EXECUTING"


def test_disposable_multi_fault_proof_script(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "v12_campaign_fault_proof.py"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(script.parents[1] / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, str(script), "--mode", "full", "--root", str(tmp_path / "proof")],
        cwd=script.parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert proof["process_kill"]["returncode"] == 86
    assert proof["restart"]["time_child_status"] == "PENDING"
    assert proof["restart"]["provider_child_status"] == "WAITING"
    assert proof["provider_recovery"]["status"] in {"READY", "PARTIAL"}
    assert proof["parallel_join"]["campaign_status"] == "COMPLETED"
    assert proof["deadline"]["campaign_status"] == "ESCALATED"
    # The child had no bound mission before STOP; RESUME preserves that exact
    # PENDING checkpoint instead of inventing dispatch authority.
    assert proof["stop_resume"]["child_status"] == "PENDING"
