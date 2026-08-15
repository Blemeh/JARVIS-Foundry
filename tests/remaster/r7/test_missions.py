from __future__ import annotations

from pathlib import Path

import pytest
from r7_helpers import kernel_ledger

from jarvis_foundry.kernel.portfolio.missions import (
    MissionIntegrator,
    TargetMismatchError,
)
from jarvis_foundry.kernel.projections import (
    GoalProjectionHandler,
    MissionProjectionHandler,
    ProjectionDispatcher,
    ProjectionRegistry,
)


def _integrator(tmp_path: Path) -> MissionIntegrator:
    return MissionIntegrator(kernel_ledger(tmp_path), clock=None)


def test_goal_and_mission_are_durable_kernel_records(tmp_path: Path) -> None:
    ledger = kernel_ledger(tmp_path)
    integrator = MissionIntegrator(ledger)
    goal = integrator.sync_goal(
        goal_id="goal-leadgen",
        title="Advance LeadGen",
        objective="restore export lineage",
        status="ACTIVE",
    )
    assert goal.created is True
    mission = integrator.create_mission(
        goal_id="goal-leadgen",
        objective="Repair the sales worklist export lineage",
        candidate_id="leadgen-repair",
        decision_id="decision-1",
        project_target="proving/leadgen",
        expected_target="proving/leadgen",
    )
    assert mission.mission_id
    assert ledger.verify_chain().valid is True
    dispatcher = ProjectionDispatcher(
        ProjectionRegistry(
            {
                "goals": GoalProjectionHandler(),
                "missions": MissionProjectionHandler(),
            }
        )
    )
    snapshot = dispatcher.replay(ledger.replay())
    assert mission.mission_id in snapshot.states["missions"]


def test_mission_creation_is_idempotent_across_restart(tmp_path: Path) -> None:
    ledger = kernel_ledger(tmp_path)
    integrator = MissionIntegrator(ledger)
    integrator.sync_goal("goal-1", "Title", "Objective", "ACTIVE")
    first = integrator.create_mission(
        goal_id="goal-1",
        objective="Repair export lineage",
        candidate_id="cand-1",
        decision_id="decision-1",
        project_target="proving/leadgen",
        expected_target="proving/leadgen",
    )
    reopened = MissionIntegrator(kernel_ledger(tmp_path))
    second = reopened.create_mission(
        goal_id="goal-1",
        objective="Repair export lineage",
        candidate_id="cand-1",
        decision_id="decision-1",
        project_target="proving/leadgen",
        expected_target="proving/leadgen",
    )
    assert second.mission_id == first.mission_id
    assert len(ledger.replay()) == len(reopened.ledger.replay())
    assert ledger.verify_chain().valid is True


def test_reconcile_target_reports_git_truth(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    from jarvis_foundry.kernel.portfolio.provenance import run_git

    run_git(repo, ["init", "-q"])
    run_git(repo, ["config", "user.email", "r7@test.local"])
    run_git(repo, ["config", "user.name", "R7 Test"])
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    run_git(repo, ["add", "README.md"])
    run_git(repo, ["commit", "-q", "-m", "init"])
    report = MissionIntegrator(kernel_ledger(tmp_path / "ledger")).reconcile_target(str(repo))
    assert report["exists"] is True
    assert report["is_git_repo"] is True
    assert report["head"]
    assert report["porcelain_count"] == 0


def test_target_mismatch_is_refused(tmp_path: Path) -> None:
    integrator = _integrator(tmp_path)
    with pytest.raises(TargetMismatchError):
        integrator.validate_target("proving/leadgen", "proving/operators")


def test_create_mission_refuses_off_decision_target(tmp_path: Path) -> None:
    """The kernel dispatch path must enforce canonical-target binding."""

    integrator = _integrator(tmp_path)
    integrator.sync_goal(
        goal_id="goal-a",
        title="Advance LeadGen",
        objective="restore export lineage",
    )
    with pytest.raises(TargetMismatchError):
        integrator.create_mission(
            goal_id="goal-a",
            objective="repair leadgen",
            candidate_id="leadgen-repair",
            decision_id="decision-1",
            project_target="proving/OTHER",
            expected_target="proving/p1",
        )


def test_create_mission_requires_expected_target(tmp_path: Path) -> None:
    """The binding must be mandatory: omitting expected_target is a contract error."""

    integrator = _integrator(tmp_path)
    integrator.sync_goal(
        goal_id="goal-b",
        title="Advance LeadGen",
        objective="restore export lineage",
    )
    with pytest.raises(TypeError):
        integrator.create_mission(
            goal_id="goal-b",
            objective="repair leadgen",
            candidate_id="leadgen-repair",
            decision_id="decision-1",
            project_target="proving/OTHER",
        )
