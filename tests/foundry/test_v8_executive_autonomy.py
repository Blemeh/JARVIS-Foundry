"""V8 executive autonomy: durable planning, continuation and truth surfaces."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import (
    build_plan,
    create_objective,
    executive_autonomy_snapshot,
    generate_missions_for_objective,
    rank_objectives,
    rebuild_executive_state,
    run_autonomy_cycle,
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


def test_command_center_exposes_additive_executive_projection(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    create_objective(database, "API objective", objective_id="api-objective")
    snapshot = build_snapshot(database)
    assert executive_autonomy_snapshot(database, include_briefing=False)["objectives"]["api-objective"]["objective"] == "API objective"
    assert snapshot["executive"]["activation_epoch"] is None


def test_yaml_runtime_flag_is_opt_in(tmp_path: Path) -> None:
    config_file = tmp_path / "worker.yaml"
    config_file.write_text("enabled: true\nexecutive:\n  autonomy_enabled: true\n", encoding="utf-8")
    config = WorkerRuntimeConfig.from_yaml(config_file)
    assert config.executive_autonomy_enabled is True
    assert WorkerRuntimeConfig(enabled=True).executive_autonomy_enabled is False
