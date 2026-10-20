"""Authenticated objective outcomes enter the existing campaign/mission spine."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.executive.autonomy import (
    active_plan, generate_missions_for_objective, rebuild_executive_state,
    run_autonomy_cycle,
)
from jarvis_foundry.executive.campaigns import (
    campaign_dispatchable_mission_ids, ensure_warranted_campaign,
    materialize_campaign_children, rebuild_campaign_state,
    reconcile_campaign_step_bindings,
    startup_resume_pass,
)
from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection
from jarvis_foundry.evolution.executive_portfolio import select_next_work
from jarvis_foundry.mission.contracts import next_executable_contract
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.provider_config import default_provider_settings
from jarvis_foundry.runtime.worker_loop import run_cycle

from test_v8_executive_autonomy import _db


def _scoped_goal(database, text: str) -> str:
    secret = b"campaign-planning-owner-secret-32-bytes"
    service = OwnerControlService(database, secret=secret, owner_id="owner")
    nonce = "campaign-plan-" + str(abs(hash(text)))
    service.apply(
        "GOAL", target="resource-v8", exact_text=text, nonce=nonce,
        signature=sign_command(secret, "GOAL", "resource-v8", text, nonce),
    )
    return str(next(event.payload["directive_id"] for event in database.events(event_types=("owner_directive.recorded",))))


def test_actionable_owner_outcomes_cross_into_campaign_and_canonical_mission(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    directive = _scoped_goal(
        database,
        "Improve the registered project. success: 1. Build a durable project artifact; "
        "2. Verify the project artifact by direct inspection",
    )
    objective_id = f"owner-goal:{directive}"

    first = run_autonomy_cycle(database, cycle_id="plan-first", resource_id="resource-v8")
    plan = active_plan(database, objective_id)
    assert plan is not None
    assert [step.step_id for step in plan.steps] == ["outcome-1", "outcome-2"]
    assert plan.steps[1].dependencies == ("outcome-1",)
    assert first.materialization["materialized"] is False
    assert database.event_count(event_type="executive.decomposition_warranted") == 1

    created = ensure_warranted_campaign(database)
    assert len(created) == 1 and created[0]["created"]
    materialize_campaign_children(database)
    startup_resume_pass(database)

    second = run_autonomy_cycle(database, cycle_id="plan-second", resource_id="resource-v8")
    mission_id = second.materialization["mission_id"]
    assert mission_id and database.row("missions", "mission_id", mission_id)
    campaign = next(iter(rebuild_campaign_state(database).campaigns.values()))
    children = rebuild_campaign_state(database).children
    assert children[f"child:{campaign.campaign_id}:outcome-1"].mission_id == mission_id
    assert children[f"child:{campaign.campaign_id}:outcome-1"].status == "READY"
    assert children[f"child:{campaign.campaign_id}:outcome-2"].mission_id is None
    assert rebuild_executive_state(database).objectives[objective_id].mission_ids == (mission_id,)
    assert next_executable_contract(database, mission_id) is not None
    assert mission_id in campaign_dispatchable_mission_ids(database)


def test_simple_or_ambiguous_goal_stays_one_step(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    directive = _scoped_goal(
        database,
        "Improve the registered project. success: quality is higher; tests pass",
    )
    run_autonomy_cycle(database, cycle_id="simple-goal", resource_id="resource-v8")
    plan = active_plan(database, f"owner-goal:{directive}")
    assert plan is not None and [step.step_id for step in plan.steps] == ["execute"]
    assert database.event_count(event_type="executive.decomposition_warranted") == 0


def test_interrupted_portfolio_to_campaign_binding_recovers_from_durable_receipt(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    directive = _scoped_goal(
        database,
        "Improve the project. success: 1. Build a durable project artifact; "
        "2. Verify the project artifact by direct inspection",
    )
    objective_id = f"owner-goal:{directive}"
    run_autonomy_cycle(database, cycle_id="interrupted-plan", resource_id="resource-v8")
    ensure_warranted_campaign(database)
    materialize_campaign_children(database)
    startup_resume_pass(database)
    requests = generate_missions_for_objective(database, objective_id)
    assert requests, rebuild_executive_state(database).objectives[objective_id].status
    decision = select_next_work(
        database, loop_id="interrupted-selection", objective_id=objective_id,
        resource_id="resource-v8",
    )
    materialized = materialize_portfolio_selection(
        database, decision=decision.to_payload(), resource_id="resource-v8",
    )
    assert materialized["materialized"], (decision.reason, materialized)
    child = next(row for row in rebuild_campaign_state(database).children.values()
                 if row.step_id == "outcome-1")
    assert child.mission_id is None

    recovered = reconcile_campaign_step_bindings(database)

    assert recovered[0]["mission_id"] == materialized["mission_id"]
    assert materialized["mission_id"] in campaign_dispatchable_mission_ids(database)
    assert reconcile_campaign_step_bindings(database) == []


def test_normal_worker_reaches_campaign_child_execution_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, _ = _db(tmp_path)
    directive = _scoped_goal(
        database,
        "Improve the registered project. success: 1. Build a durable project artifact; "
        "2. Verify the project artifact by direct inspection",
    )
    objective_id = f"owner-goal:{directive}"
    settings = default_provider_settings()
    config = WorkerRuntimeConfig(
        enabled=True, continuous_enabled=True, continuous_discovery_enabled=False,
        campaign_runtime_enabled=True, provider_settings=settings,
        provider_settings_pool=(settings,),
    )

    class ExecutionBoundary(Exception):
        pass

    from jarvis_foundry.runtime import worker_loop

    def stop_before_provider(*args, **kwargs):
        raise ExecutionBoundary

    monkeypatch.setattr(worker_loop, "build_worker_stack", stop_before_provider)
    first = run_cycle(database, FoundryPaths(tmp_path), config,
                      loop_id="worker-plan-1", instance_id="worker-plan")
    assert first["outcome"] == "WAITING"
    assert len(active_plan(database, objective_id).steps) == 2

    with pytest.raises(ExecutionBoundary):
        run_cycle(database, FoundryPaths(tmp_path), config,
                  loop_id="worker-plan-2", instance_id="worker-plan")
    campaign = next(iter(rebuild_campaign_state(database).campaigns.values()))
    first_child = rebuild_campaign_state(database).children[f"child:{campaign.campaign_id}:outcome-1"]
    assert first_child.mission_id
    assert first_child.mission_id in campaign_dispatchable_mission_ids(database)
    assert next_executable_contract(database, first_child.mission_id) is not None
