"""Owner goals stating an explicit deliverable must reach execution.

A normal owner goal naming a concrete verified deliverable with enumerated
coverage states explicit success criteria in substance.  Admitting it with a
generic contract parks it permanently behind the executable-contract gate
with no path forward.  Vague aspirations without a deliverable stay parked.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import (
    CONTRACT_SCOPE_MARKER,
    PlanStep,
    _deliverable_criteria,
    _parse_goal_metadata,
    _replan_scope_stalled_steps,
    active_plan,
    build_plan,
    create_objective,
    explicit_success_criteria_of,
    rebuild_executive_state,
    step_has_executable_contract,
    sync_owner_goals,
)
from jarvis_foundry.owner_controls import OwnerControlService, sign_command

ACCEPTANCE_GOAL = (
    "Inspect the current JARVIS runtime and produce a verified status report "
    "covering Supervisor health, worker-loop health, active goals, blocked "
    "missions, and the next intended autonomous action."
)
BROAD_GOAL = (
    "Autonomously improve the quality, reliability, usefulness, and "
    "maintainability of the owner's active registered software projects."
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "deliverable.db")
    database.initialize()
    return database


def _record(text: str) -> SimpleNamespace:
    return SimpleNamespace(exact_text=text)


def test_deliverable_with_coverage_parses_to_explicit_criteria() -> None:
    criteria = _parse_goal_metadata(_record(ACCEPTANCE_GOAL))["success_criteria"]
    assert criteria == (
        "verified status report covering Supervisor health",
        "verified status report covering worker-loop health",
        "verified status report covering active goals",
        "verified status report covering blocked missions",
        "verified status report covering the next intended autonomous action",
    )


def test_broad_and_vague_goals_parse_to_no_criteria() -> None:
    assert _deliverable_criteria(BROAD_GOAL) == ()
    assert _parse_goal_metadata(_record(BROAD_GOAL))["success_criteria"] == ()
    assert _parse_goal_metadata(_record("Inspect the runtime."))["success_criteria"] == ()
    assert _parse_goal_metadata(_record("Produce a report."))["success_criteria"] == ()


def test_success_marker_path_is_unchanged() -> None:
    criteria = _parse_goal_metadata(_record(
        "Report the supervisor heartbeat age in seconds. "
        "success: one verified line stating the heartbeat age."
    ))["success_criteria"]
    assert criteria == ("one verified line stating the heartbeat age.",)


def test_verified_single_deliverable_is_explicit() -> None:
    assert _parse_goal_metadata(
        _record("Write a verified migration plan.")
    )["success_criteria"] == ("verified migration plan",)


def test_parsed_criteria_satisfy_the_executable_contract_gate() -> None:
    from jarvis_foundry.executive.autonomy import ObjectiveRecord

    objective = ObjectiveRecord(
        objective_id="obj-deliverable", objective=ACCEPTANCE_GOAL,
        success_criteria=_parse_goal_metadata(_record(ACCEPTANCE_GOAL))["success_criteria"],
        provenance={"directive_id": "owner-test"},
    )
    step = PlanStep(
        step_id="execute", objective_id="obj-deliverable",
        description=ACCEPTANCE_GOAL,
        output_contract=objective.success_criteria,
    )
    assert explicit_success_criteria_of(objective)
    ok, _ = step_has_executable_contract(objective, step)
    assert ok is True


def test_broad_goal_stays_parked_behind_the_gate() -> None:
    from jarvis_foundry.executive.autonomy import ObjectiveRecord

    objective = ObjectiveRecord(
        objective_id="obj-broad", objective=BROAD_GOAL,
        provenance={"directive_id": "owner-test"},
    )
    step = PlanStep(
        step_id="execute", objective_id="obj-broad",
        description=BROAD_GOAL,
        output_contract=("durable outcome evidence",),
    )
    ok, missing = step_has_executable_contract(objective, step)
    assert ok is False and missing


def _admit_goal(db: FoundryDatabase, text: str) -> str:
    secret = b"deliverable-test-secret-0123456789abcdef"
    service = OwnerControlService(
        db, secret=secret, stop_path=db.path.parent / "STOP", owner_id="test-owner",
    )
    applied = service.apply(
        "GOAL", target="GLOBAL", exact_text=text, nonce=f"nonce-{hash(text) % 10**8}",
        signature=sign_command(secret, "GOAL", "GLOBAL", text, f"nonce-{hash(text) % 10**8}"),
    )
    return applied.directive_id


def test_sync_heals_criteria_missed_by_an_older_admission(db: FoundryDatabase) -> None:
    directive_id = _admit_goal(db, ACCEPTANCE_GOAL)
    oid = f"owner-goal:{directive_id}"
    create_objective(
        db, ACCEPTANCE_GOAL, objective_id=oid,
        provenance={"directive_id": directive_id, "authenticated": True},
    )
    before = rebuild_executive_state(db).objectives[oid]
    assert explicit_success_criteria_of(before) == ()
    sync_owner_goals(db)
    healed = rebuild_executive_state(db).objectives[oid]
    assert len(explicit_success_criteria_of(healed)) == 5
    version_after_heal = healed.version
    sync_owner_goals(db)
    again = rebuild_executive_state(db).objectives[oid]
    assert again.version == version_after_heal


def test_scope_stalled_plan_replans_once_it_is_contract_bearing(db: FoundryDatabase) -> None:
    from jarvis_foundry.executive.continuous import rebuild_wake_state, schedule_wait_for_signal

    directive_id = _admit_goal(db, ACCEPTANCE_GOAL)
    oid = f"owner-goal:{directive_id}"
    create_objective(
        db, ACCEPTANCE_GOAL, objective_id=oid,
        success_criteria=_parse_goal_metadata(_record(ACCEPTANCE_GOAL))["success_criteria"],
        provenance={"directive_id": directive_id, "authenticated": True},
    )
    stalled = build_plan(db, oid, [PlanStep(
        step_id="execute", objective_id=oid, description=ACCEPTANCE_GOAL,
        output_contract=("durable outcome evidence",),
        status="BLOCKED", evidence_refs=(CONTRACT_SCOPE_MARKER,),
    )])
    assert stalled.steps[0].status == "BLOCKED"
    wake = schedule_wait_for_signal(
        db, oid, signal=f"executive-scope:{oid}:execute",
        reason="plan steps blocked; await executable scope evidence",
    )
    wake_id = wake["wake_id"] if isinstance(wake, dict) else wake.wake_id
    objective = rebuild_executive_state(db).objectives[oid]
    replanned = _replan_scope_stalled_steps(
        db, objective, stalled, capability="write_registered_worktree_files",
    )
    assert replanned is not None
    assert replanned.plan_id != stalled.plan_id
    assert replanned.steps[0].status == "READY"
    assert CONTRACT_SCOPE_MARKER not in tuple(replanned.steps[0].evidence_refs)
    assert rebuild_wake_state(db)[wake_id]["status"] == "TRIGGERED"
    current = active_plan(db, oid)
    assert current is not None and current.plan_id == replanned.plan_id
    assert _replan_scope_stalled_steps(db, objective, current, capability="x") is None


def test_replan_refuses_plans_without_the_scope_marker(db: FoundryDatabase) -> None:
    directive_id = _admit_goal(db, ACCEPTANCE_GOAL)
    oid = f"owner-goal:{directive_id}"
    create_objective(
        db, ACCEPTANCE_GOAL, objective_id=oid,
        success_criteria=_parse_goal_metadata(_record(ACCEPTANCE_GOAL))["success_criteria"],
        provenance={"directive_id": directive_id, "authenticated": True},
    )
    plan = build_plan(db, oid, [PlanStep(
        step_id="execute", objective_id=oid, description=ACCEPTANCE_GOAL,
        output_contract=("durable outcome evidence",),
    )])
    objective = rebuild_executive_state(db).objectives[oid]
    assert _replan_scope_stalled_steps(db, objective, plan, capability="x") is None
