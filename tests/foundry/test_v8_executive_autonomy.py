"""V8 executive autonomy: durable planning, continuation and truth surfaces."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.evolution.executive_portfolio import register_work_item, set_work_status
from jarvis_foundry.executive.autonomy import (
    build_plan,
    create_objective,
    executive_autonomy_snapshot,
    generate_missions_for_objective,
    rank_objectives,
    rebuild_executive_state,
    run_autonomy_cycle,
    sync_owner_goals,
    update_objective,
)
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle


def _db(tmp_path: Path) -> tuple[FoundryDatabase, Path]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    repository = tmp_path / "resource"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "v8@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "V8 Test"], check=True)
    (repository / "README.md").write_text("# V8\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "baseline"], check=True, capture_output=True, text=True)
    database.append(registration_event(inspect_git_resource(repository, resource_id="resource-v8")))
    set_capability_state(database, "resource-v8", "write_registered_worktree_files", "READY", evidence={"source": "v8-test"})
    return database, repository


def _goal(database: FoundryDatabase, text: str) -> None:
    secret = b"v8-owner-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    nonce = "v8-goal-" + str(abs(hash(text)))
    service.apply(
        "GOAL", target="GLOBAL", exact_text=text, nonce=nonce,
        signature=sign_command(secret, "GOAL", "GLOBAL", text, nonce),
    )


def test_objective_plan_is_durable_and_cycles_are_rejected(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    parent = create_objective(database, "Parent objective", objective_id="parent")
    child = create_objective(database, "Child objective", objective_id="child", dependencies=(parent.objective_id,))
    plan = build_plan(database, child.objective_id, [
        {"step_id": "prepare", "description": "Prepare evidence"},
        {"step_id": "finish", "description": "Finish objective", "dependencies": ("prepare",)},
    ])
    restored = rebuild_executive_state(FoundryDatabase(database.path))
    assert restored.objectives["child"].dependencies == ("parent",)
    assert restored.plans[plan.plan_id].critical_path == ("prepare", "finish")
    with pytest.raises(ValueError, match="cycle"):
        build_plan(database, parent.objective_id, [
            {"step_id": "a", "description": "A", "dependencies": ("b",)},
            {"step_id": "b", "description": "B", "dependencies": ("a",)},
        ])


def test_priority_vector_exposes_capability_blocker(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    set_capability_state(database, "resource-v8", "write_registered_worktree_files", "DEGRADED", evidence={"source": "fault"})
    objective = create_objective(
        database, "Ship the guarded objective", objective_id="guarded",
        resource_id="resource-v8", required_capabilities=("write_registered_worktree_files",),
        deadline="2099-01-01T00:00:00Z", blocking_value=1.0,
    )
    build_plan(database, objective.objective_id, [{"step_id": "execute", "description": "Execute guarded objective"}])
    row = next(item for item in rank_objectives(database) if item.objective_id == "guarded")
    assert row.eligible is False
    assert any("DEGRADED" in blocker for blocker in row.blockers)
    assert set(row.factors) >= {
        "owner_priority", "deadline", "expected_impact", "dependency_value", "urgency",
        "risk", "effort", "resource_availability", "opportunity_cost", "failure_history",
        "stale_age", "blocking_value", "confidence", "reversibility",
    }


def test_autonomous_wip_excludes_owner_goals_with_owner_id_authorities(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    create_objective(
        database, "Local owner goal", objective_id="owner-goal:local-owner",
        owner_authority="local-owner", status="BLOCKED",
    )
    create_objective(
        database, "Staged owner goal", objective_id="owner-goal:staged-owner",
        owner_authority="owner-v7-stage", status="ACTIVE",
    )
    create_objective(
        database, "Explicit owner objective", objective_id="explicit-owner",
        owner_authority="owner", status="ACTIVE",
    )
    create_objective(
        database, "Autonomous objective", objective_id="autonomous-work",
        owner_authority="initiative-a2", status="ACTIVE",
    )

    from jarvis_foundry.executive.scoring import _autonomous_wip

    assert _autonomous_wip(database) == 1


def test_owner_goal_autonomously_materializes_through_existing_portfolio(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Create the durable receipt. success: outcome receipt exists")
    cycle = run_autonomy_cycle(database, cycle_id="v8-cycle-1", resource_id="resource-v8")
    assert cycle.status == "MISSION_MATERIALIZED"
    assert cycle.materialization["mission_id"]
    state = rebuild_executive_state(database)
    objective = state.objectives[cycle.objective_id]
    assert objective.mission_ids == (cycle.materialization["mission_id"],)
    assert any(step.status == "RUNNING" for plan in state.plans.values() for step in plan.steps)
    restarted = rebuild_executive_state(FoundryDatabase(database.path))
    assert restarted.objectives[cycle.objective_id].mission_ids == objective.mission_ids
    assert executive_autonomy_snapshot(database, include_briefing=False)["latest_cycle"]["cycle_id"] == "v8-cycle-1"


def test_terminal_legacy_owner_goal_item_does_not_shadow_executive_step(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    goal_text = "Create the durable receipt. success: outcome receipt exists"
    _goal(database, goal_text)
    directive = next(
        event.payload
        for event in database.events(event_types=("owner_directive.recorded",))
        if event.payload.get("command_type") == "GOAL"
    )
    directive_id = str(directive["directive_id"])
    legacy_mission_id = "mission-legacy-owner-goal"
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": legacy_mission_id,
            "objective": goal_text,
            "resource_id": "resource-v8",
            "state": "PLANNED",
            "acceptance_criteria": ["legacy work completed"],
            "next_action": "legacy owner-goal item",
        },
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.created:{legacy_mission_id}",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": legacy_mission_id,
            "state": "CANCELLED",
            "next_action": "no eligible worker",
        },
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.state_changed:{legacy_mission_id}:CANCELLED",
    ))
    legacy_item_id = f"owner-goal:{directive_id}"
    register_work_item(
        database,
        item_id=legacy_item_id,
        kind="OWNER_GOAL",
        objective=goal_text,
        goal_ids=(directive_id,),
        resource_id="resource-v8",
        mission_id=legacy_mission_id,
    )
    set_work_status(
        database, legacy_item_id, "BLOCKED",
        reason="legacy mission had no eligible worker",
    )

    cycle = run_autonomy_cycle(
        database, cycle_id="legacy-terminal-owner-goal-cycle",
        resource_id="resource-v8",
    )

    assert database.row("missions", "mission_id", legacy_mission_id)["state"] == "CANCELLED"
    assert cycle.status == "MISSION_MATERIALIZED"
    assert cycle.materialization["mission_id"]
    assert len(cycle.mission_requests) == 1
    assert cycle.mission_requests[0]["item_id"] == (
        f"executive-step:{cycle.objective_id}:execute"
    )
    assert len(database.events(event_types=("provider.dispatch_attempted",))) == 0


def test_owner_goal_plan_steps_get_distinct_items_without_reusing_legacy_identity(
    tmp_path: Path,
) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Complete two checks. success: first receipt; second receipt")
    objective = sync_owner_goals(database)[0]
    plan = build_plan(database, objective.objective_id, [
        {
            "step_id": "first",
            "description": "Complete the first check",
            "output_contract": ["first receipt exists"],
        },
        {
            "step_id": "second",
            "description": "Complete the second check",
            "output_contract": ["second receipt exists"],
        },
    ])

    requests = generate_missions_for_objective(
        database, objective.objective_id, max_new=2,
    )

    assert len(requests) == 2
    assert requests[0]["item_id"] == f"owner-goal:{objective.provenance['directive_id']}"
    assert requests[1]["item_id"] == (
        f"executive-step:{objective.objective_id}:second"
    )
    assert plan.plan_id == requests[0]["plan_id"] == requests[1]["plan_id"]


def test_owner_goal_without_executable_contract_waits_without_creating_mission(
    tmp_path: Path,
) -> None:
    database, _ = _db(tmp_path)
    _goal(
        database,
        "V7 full autonomy: complete bounded local work end to end, keep "
        "independent verification honest, prefer useful maintenance when no "
        "mission work exists, and WAIT when no action has positive expected value.",
    )

    cycle = run_autonomy_cycle(
        database, cycle_id="uncontracted-owner-goal-cycle",
        resource_id="resource-v8",
    )

    assert cycle.status == "WAITING"
    assert cycle.portfolio_decision["decision"] == "WAIT"
    assert not cycle.mission_requests
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0
    blocked = database.events(event_types=("executive.mission_generation_blocked",))
    assert any("no explicit executable" in event.payload.get("reason", "") for event in blocked)


def test_invalid_intent_and_blank_recipe_do_not_make_a_contractless_goal_executable(
    tmp_path: Path,
) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Continue bounded maintenance. WAIT when no useful action exists.")
    objective = sync_owner_goals(database)[0]
    update_objective(
        database, objective.objective_id,
        {"metadata": {
            "intent": {"unrecognized": "not a normalized intent"},
            "recipe_id": "  ",
        }},
    )

    cycle = run_autonomy_cycle(
        database, cycle_id="invalid-owner-intent-cycle",
        resource_id="resource-v8",
    )

    assert cycle.status == "WAITING"
    assert cycle.portfolio_decision["decision"] == "WAIT"
    assert not cycle.mission_requests
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0


def test_valid_owner_goal_intent_uses_cycle_resource_for_contract_validation(
    tmp_path: Path,
) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Resolve the registered resource check.")
    objective = sync_owner_goals(database)[0]
    update_objective(
        database, objective.objective_id,
        {"metadata": {"intent": {"needs": ["registered resource check"]}}},
    )
    build_plan(database, objective.objective_id, [
        {"step_id": "execute", "description": objective.objective},
    ])

    requests = generate_missions_for_objective(
        database, objective.objective_id, resource_id="resource-v8",
    )

    assert len(requests) == 1
    assert requests[0]["step_id"] == "execute"


def test_owner_goal_binds_only_registered_resource_targets(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    goal_text = "Update the registered worktree. success: verified change"
    _goal(database, goal_text)
    secret = b"v8-owner-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    nonce = "resource-target-v8"
    service.apply(
        "GOAL", target="resource-v8", exact_text=goal_text, nonce=nonce,
        signature=sign_command(secret, "GOAL", "resource-v8", goal_text, nonce),
    )
    missing_nonce = "unregistered-resource-target-v8"
    service.apply(
        "GOAL", target="resource-not-registered", exact_text=goal_text,
        nonce=missing_nonce,
        signature=sign_command(
            secret, "GOAL", "resource-not-registered", goal_text,
            missing_nonce,
        ),
    )

    from jarvis_foundry.executive.autonomy import sync_owner_goals

    objectives = sync_owner_goals(database)
    by_target = {objective.metadata["target"]: objective for objective in objectives}
    assert set(by_target) == {"GLOBAL", "resource-v8"}
    assert by_target["resource-v8"].resource_id == "resource-v8"
    assert by_target["GLOBAL"].resource_id is None
    plan = build_plan(database, by_target["resource-v8"].objective_id, [
        {"step_id": "execute", "description": "Update the registered worktree"},
    ])
    assert plan.steps[0].resource_id == "resource-v8"


def test_runtime_continues_owner_goal_without_prompt_and_closes_it(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Create the durable receipt. success: outcome receipt exists")
    config = WorkerRuntimeConfig(
        enabled=True, status="ACTIVE", resource_id="resource-v8",
        lab_scripted_hermes=True, executive_autonomy_enabled=True,
        runtime_state_root=str(tmp_path / "runtime"),
    )
    paths = FoundryPaths(tmp_path)
    reports = [run_cycle(database, paths, config, loop_id=f"v8-{i}", instance_id=f"v8-worker-{i}") for i in range(1, 4)]
    assert reports[0]["outcome"] == "PASSED"
    assert reports[1]["outcome"] == "PASSED"
    mission = database.row("missions", "mission_id", reports[0]["mission_id"])
    assert mission["state"] == "COMPLETE"
    state = rebuild_executive_state(database)
    assert next(iter(state.objectives.values())).status == "COMPLETED"
    assert reports[2]["outcome"] == "WAITING"


def test_failed_strategy_retries_once_then_escalates(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Create the durable receipt. success: outcome receipt exists")
    config = WorkerRuntimeConfig(
        enabled=True, status="ACTIVE", resource_id="resource-v8",
        lab_scripted_hermes=True, lab_false_success_step_id="02-execute",
        max_repairs=0, executive_autonomy_enabled=True,
        runtime_state_root=str(tmp_path / "runtime"),
    )
    paths = FoundryPaths(tmp_path)
    reports = [run_cycle(database, paths, config, loop_id=f"retry-{i}", instance_id=f"retry-worker-{i}") for i in range(1, 7)]
    missions = [row["mission_id"] for row in database.all("mission_step_contracts")]
    assert len(set(missions)) == 2
    continuation_actions = [event.payload.get("action") for event in database.events(event_types=("executive.continuation_decided",))]
    assert "RETRY" in continuation_actions
    assert "ESCALATE" in continuation_actions
    assert any(item.status == "OPEN" for item in rebuild_executive_state(database).escalations.values())
    assert "BLOCKED" in [report["outcome"] for report in reports]


def test_stop_sentinel_blocks_executive_mutation(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    objective = create_objective(database, "Stop-protected objective", objective_id="stop-objective")
    (tmp_path / "EMERGENCY_STOP").write_text("owner stop\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="STOP"):
        generate_missions_for_objective(database, objective.objective_id)


def test_revoked_owner_goal_suspends_objective_and_cancels_unfinished_mission(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    text = "Create the durable receipt. success: outcome receipt exists"
    _goal(database, text)
    cycle = run_autonomy_cycle(database, cycle_id="revoke-cycle", resource_id="resource-v8")
    objective_id = cycle.objective_id
    directive_id = rebuild_executive_state(database).objectives[objective_id].provenance["directive_id"]
    secret = b"v8-owner-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    revoke_text = "revoke the owner goal"
    nonce = "revoke-v8"
    service.apply(
        "REVOKE", target=directive_id, exact_text=revoke_text, nonce=nonce,
        signature=sign_command(secret, "REVOKE", directive_id, revoke_text, nonce),
    )
    from jarvis_foundry.executive.autonomy import sync_owner_goals
    sync_owner_goals(database)
    state = rebuild_executive_state(database)
    assert state.objectives[objective_id].status == "SUSPENDED"
    mission = database.row("missions", "mission_id", cycle.materialization["mission_id"])
    assert mission["state"] == "CANCELLED"


def test_owner_goal_revocation_cleanup_retries_after_receipt(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    text = "Create the durable receipt. success: outcome receipt exists"
    _goal(database, text)
    cycle = run_autonomy_cycle(database, cycle_id="revoke-retry-cycle", resource_id="resource-v8")
    objective = rebuild_executive_state(database).objectives[cycle.objective_id]
    directive_id = objective.provenance["directive_id"]
    mission_id = cycle.materialization["mission_id"]
    secret = b"v8-owner-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    revoke_text = "revoke the owner goal before descendant cleanup"
    nonce = "revoke-before-cleanup"
    service.apply(
        "REVOKE", target=directive_id, exact_text=revoke_text, nonce=nonce,
        signature=sign_command(secret, "REVOKE", directive_id, revoke_text, nonce),
    )

    from jarvis_foundry.executive.autonomy import (
        sync_owner_goals,
        withdraw_objective_authority,
    )

    withdraw_objective_authority(
        database,
        objective.objective_id,
        directive_id=directive_id,
        reason=(
            "owner GOAL directive was revoked; autonomous authority "
            "is withdrawn"
        ),
        cancel_descendants=False,
    )
    assert database.row("missions", "mission_id", mission_id)["state"] != "CANCELLED"
    assert len(database.events(event_types=("executive.objective_authority_revoked",))) == 1

    sync_owner_goals(database)

    assert database.row("missions", "mission_id", mission_id)["state"] == "CANCELLED"
    assert len(database.events(event_types=("executive.objective_authority_revoked",))) == 1


def test_revoked_owner_goal_cannot_generate_work_before_sync(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Create the durable receipt. success: outcome receipt exists")
    from jarvis_foundry.executive.autonomy import (
        generate_missions_for_objective,
        sync_owner_goals,
    )

    objective = sync_owner_goals(database)[0]
    build_plan(database, objective.objective_id, [
        {"step_id": "execute", "description": "Create the durable receipt"},
    ])
    before_revoke = generate_missions_for_objective(database, objective.objective_id)
    assert len(before_revoke) == 1
    request_count_before_revoke = len(
        database.events(event_types=("executive.mission_requested",))
    )
    assert request_count_before_revoke == 1
    directive_id = objective.provenance["directive_id"]
    secret = b"v8-owner-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    revoke_text = "revoke before mission generation"
    nonce = "revoke-before-generation"
    service.apply(
        "REVOKE", target=directive_id, exact_text=revoke_text, nonce=nonce,
        signature=sign_command(secret, "REVOKE", directive_id, revoke_text, nonce),
    )

    assert generate_missions_for_objective(database, objective.objective_id) == []
    assert len(database.events(event_types=("executive.mission_requested",))) == request_count_before_revoke


def test_failed_revoked_owner_goal_is_not_ranked_or_retried(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Create the durable receipt. success: outcome receipt exists")
    from jarvis_foundry.executive.autonomy import (
        decide_continuation,
        set_objective_status,
        sync_owner_goals,
    )

    objective = sync_owner_goals(database)[0]
    plan = build_plan(database, objective.objective_id, [
        {
            "step_id": "execute",
            "description": "Create the durable receipt",
            "status": "FAILED",
            "max_retries": 2,
        },
    ])
    set_objective_status(
        database, objective.objective_id, "FAILED", reason="simulated failed attempt",
    )
    directive_id = objective.provenance["directive_id"]
    secret = b"v8-owner-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    revoke_text = "revoke the failed owner goal"
    nonce = "revoke-failed-goal"
    service.apply(
        "REVOKE", target=directive_id, exact_text=revoke_text, nonce=nonce,
        signature=sign_command(secret, "REVOKE", directive_id, revoke_text, nonce),
    )

    sync_owner_goals(database)
    sync_owner_goals(database)
    state = rebuild_executive_state(database)
    assert state.objectives[objective.objective_id].status == "FAILED"
    assert state.plans[plan.plan_id].steps[0].status == "FAILED"
    revocations = database.events(
        event_types=("executive.objective_authority_revoked",),
    )
    assert [event.payload["objective_id"] for event in revocations] == [
        objective.objective_id,
    ]

    ranked = next(
        row for row in rank_objectives(database)
        if row.objective_id == objective.objective_id
    )
    assert ranked.eligible is False
    assert "owner_authority_revoked" in ranked.blockers
    briefing = executive_autonomy_snapshot(database)["briefing"]
    assert not any(
        row["objective_id"] == objective.objective_id
        for row in briefing["next_actions"]
    )

    decision = decide_continuation(database, objective.objective_id)
    assert decision.action == "WAIT"
    assert "no continuation is authorized" in decision.reason
    state = rebuild_executive_state(database)
    assert state.objectives[objective.objective_id].status == "FAILED"
    assert state.plans[plan.plan_id].steps[0].status == "FAILED"
    assert not database.events(event_types=("executive.mission_requested",))


def test_revoked_suspended_goal_waits_without_progress_or_status_writes(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Create the durable receipt. success: outcome receipt exists")
    from jarvis_foundry.executive.autonomy import (
        decide_continuation,
        sync_owner_goals,
    )

    objective = sync_owner_goals(database)[0]
    build_plan(database, objective.objective_id, [
        {"step_id": "execute", "description": "Create the durable receipt"},
    ])
    directive_id = objective.provenance["directive_id"]
    secret = b"v8-owner-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    revoke_text = "revoke the suspended owner goal"
    nonce = "revoke-suspended-goal"
    service.apply(
        "REVOKE", target=directive_id, exact_text=revoke_text, nonce=nonce,
        signature=sign_command(secret, "REVOKE", directive_id, revoke_text, nonce),
    )
    sync_owner_goals(database)
    assert rebuild_executive_state(database).objectives[objective.objective_id].status == "SUSPENDED"
    progress_count = len(database.events(event_types=("executive.objective_progress_recorded",)))
    status_count = len(database.events(event_types=("executive.objective_status_changed",)))

    decision = decide_continuation(database, objective.objective_id)

    assert decision.action == "WAIT"
    assert "no continuation is authorized" in decision.reason
    assert len(database.events(event_types=("executive.objective_progress_recorded",))) == progress_count
    assert len(database.events(event_types=("executive.objective_status_changed",))) == status_count
    assert rebuild_executive_state(database).objectives[objective.objective_id].status == "SUSPENDED"


def test_revoked_goal_progress_reconciliation_cannot_reactivate_it(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Create the durable receipt. success: outcome receipt exists")
    from jarvis_foundry.executive.autonomy import (
        evaluate_objective_progress,
        set_objective_status,
        sync_owner_goals,
        update_objective,
    )

    objective = sync_owner_goals(database)[0]
    build_plan(database, objective.objective_id, [
        {"step_id": "execute", "description": "Create the durable receipt"},
    ])
    directive_id = objective.provenance["directive_id"]
    secret = b"v8-owner-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    revoke_text = "revoke the progress-reconciled owner goal"
    nonce = "revoke-progress-reconciled-goal"
    service.apply(
        "REVOKE", target=directive_id, exact_text=revoke_text, nonce=nonce,
        signature=sign_command(secret, "REVOKE", directive_id, revoke_text, nonce),
    )
    sync_owner_goals(database)
    status_count = len(database.events(event_types=("executive.objective_status_changed",)))

    evaluation = evaluate_objective_progress(database, objective.objective_id)

    assert evaluation.status == "SUSPENDED"
    assert rebuild_executive_state(database).objectives[objective.objective_id].status == "SUSPENDED"
    assert len(database.events(event_types=("executive.objective_status_changed",))) == status_count
    with pytest.raises(PermissionError, match="cannot reactivate"):
        set_objective_status(database, objective.objective_id, "ACTIVE")
    with pytest.raises(PermissionError, match="cannot reactivate"):
        update_objective(database, objective.objective_id, {"status": "ACTIVE"})


def test_revoked_goal_still_reconciles_terminal_mission_evidence(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, "Create the durable receipt. success: outcome receipt exists")
    from jarvis_foundry.executive.autonomy import (
        evaluate_objective_progress,
        sync_owner_goals,
    )

    objective = sync_owner_goals(database)[0]
    build_plan(database, objective.objective_id, [
        {
            "step_id": "execute",
            "description": "Create the durable receipt",
            "status": "COMPLETED",
            "evidence_refs": ["verified-mission-outcome:revoke-race"],
        },
    ])
    directive_id = objective.provenance["directive_id"]
    secret = b"v8-owner-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    revoke_text = "revoke after the mission outcome is durable"
    nonce = "revoke-terminal-outcome"
    service.apply(
        "REVOKE", target=directive_id, exact_text=revoke_text, nonce=nonce,
        signature=sign_command(secret, "REVOKE", directive_id, revoke_text, nonce),
    )
    sync_owner_goals(database)
    assert rebuild_executive_state(database).objectives[objective.objective_id].status == "SUSPENDED"

    evaluation = evaluate_objective_progress(database, objective.objective_id)

    assert evaluation.status == "COMPLETED"
    assert "verified-mission-outcome:revoke-race" in evaluation.evidence_refs
    assert rebuild_executive_state(database).objectives[objective.objective_id].status == "COMPLETED"


def test_command_center_exposes_additive_executive_projection(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    create_objective(database, "API objective", objective_id="api-objective")
    snapshot = build_snapshot(database)
    assert executive_autonomy_snapshot(database, include_briefing=False)["objectives"]["api-objective"]["objective"] == "API objective"
    assert snapshot["executive"]["activation_epoch"] is None


def test_yaml_runtime_flag_is_opt_in(tmp_path: Path) -> None:
    config_file = tmp_path / "worker.yaml"
    config_file.write_text(
        "enabled: true\n"
        "executive:\n  autonomy_enabled: true\n"
        "hermes:\n  allow_data_training_tiers_noninteractive: true\n",
        encoding="utf-8",
    )
    config = WorkerRuntimeConfig.from_yaml(config_file)
    assert config.executive_autonomy_enabled is True
    assert WorkerRuntimeConfig(enabled=True).executive_autonomy_enabled is False
