"""Executable V3 crash-window matrix.

Each fault is deliberately one-shot.  The second invocation reuses the same
loop identity and must reconcile the durable prefix without making a second
mission or terminal cycle record.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.mission.loop import MissionLoop, PASSED
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.v3_cycle import run_discovery_cycle
from jarvis_foundry.v3_fault import FaultInjector, V3InjectedFault
from jarvis_foundry.v3_git import GitEffectAdapter, GitReconcilePending

from test_mission_loop import mission_fixture, step_kwargs
from test_v3_discovery_cycle import EPOCH, _cycle_env, _lab_candidate, _observe_stub
from test_v3_git_reconcile import _clone_with_commit
from v3_fixtures import make_bare_remote


CYCLE_POINTS = (
    "post cycle_started",
    "post candidate_generated",
    "post candidate_scored",
    "post decision_recorded SELECT",
    "post mission.created",
    "post cycle_completed",
)


@pytest.mark.parametrize("point", CYCLE_POINTS)
def test_discovery_fault_replay_is_idempotent(tmp_path: Path, point: str) -> None:
    database, _ = _cycle_env(tmp_path)
    _lab_candidate(database)
    injector = FaultInjector([point])

    with pytest.raises(V3InjectedFault, match=point):
        run_discovery_cycle(
            database,
            loop_id=f"fault-{point.replace(' ', '-')}",
            resource_id="resource-1",
            observe=_observe_stub(),
            fault_hook=injector,
        )

    report = run_discovery_cycle(
        database,
        loop_id=f"fault-{point.replace(' ', '-')}",
        resource_id="resource-1",
        observe=_observe_stub(),
        fault_hook=injector,
    )
    assert report.outcome == "COMPILED", report.to_payload()
    assert injector.triggered == [point]
    completed = [e for e in database.events()
                 if e.event_type == "discovery.cycle_completed"]
    assert len(completed) == 1
    missions = [e for e in database.events()
                if e.event_type == "mission.created"
                and str(e.payload.get("created_from") or "").startswith("candidate:")]
    assert len(missions) == 1


@pytest.mark.parametrize("point", ("post agent_run.started", "post verification.started"))
def test_mission_fault_replay_does_not_rerun_builder(tmp_path: Path, point: str) -> None:
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter(working_root=repository)
    injector = FaultInjector([point])
    loop = MissionLoop(database, router, adapter, policy=policy,
                       fault_hook=injector)
    kwargs = step_kwargs(
        target=Path("result.txt"),
        expected_artifacts=("result.txt",),
        run_id="fault-run-1",
    )

    with pytest.raises(V3InjectedFault, match=point):
        loop.run_step(**kwargs)

    resumed = loop.run_step(**kwargs)
    assert resumed.outcome == PASSED, resumed
    assert injector.triggered == [point]
    builder_starts = [e for e in database.events()
                      if e.event_type == "agent_run.started"
                      and e.payload.get("run_id") == "fault-run-1"]
    assert len(builder_starts) == 1
    assert len([e for e in database.events()
                if e.event_type == "mission.completed"]) == 0


@pytest.mark.parametrize("point", ("mid git push", "post effect.reconcile_pending"))
def test_git_fault_freezes_before_any_retry(tmp_path: Path, point: str) -> None:
    bare = make_bare_remote(tmp_path / "remote")
    database = FoundryDatabase(tmp_path / "git.db")
    database.initialize()
    work = _clone_with_commit(bare, tmp_path, "fault-git")
    injector = FaultInjector([point])
    adapter = GitEffectAdapter(database, fault_hook=injector)

    execute = (lambda *_: (_ for _ in ()).throw(TimeoutError("timeout")))
    with pytest.raises(V3InjectedFault, match=point):
        adapter.push(action_id="fault-git", worktree=work, remote=bare,
                     branch="main", execute=execute)

    with pytest.raises(GitReconcilePending):
        adapter.push(action_id="fault-git", worktree=work, remote=bare,
                     branch="main", execute=execute)
    assert injector.triggered == [point]
    assert database.event_count(event_type="effect.reconcile_pending") == 1
    assert database.event_count(event_type="effect.frozen") == 1
    assert database.event_count(event_type="system.action_started") == 1


@pytest.mark.parametrize("point", ("post mission.completed", "post outcome_recorded"))
def test_runtime_terminal_fault_reconciles_lineage(tmp_path: Path, point: str) -> None:
    database, _ = _cycle_env(tmp_path)
    _lab_candidate(database)
    config = WorkerRuntimeConfig(
        enabled=True,
        resource_id="resource-1",
        discovery_cycle_enabled=True,
        executive_activation_epoch=EPOCH,
        runtime_state_root=str(tmp_path / "state"),
        stop_sentinel_path=str(tmp_path / "STOP"),
        lab_scripted_hermes=True,
    )
    injector = FaultInjector([point])
    kwargs = dict(
        database=database,
        paths=FoundryPaths(tmp_path),
        config=config,
        loop_id=f"terminal-{point.replace(' ', '-')}",
        instance_id="terminal-instance",
        vault_root=tmp_path / "vault",
        handoff_dir=tmp_path / "handoffs",
        fault_hook=injector,
    )
    with pytest.raises(V3InjectedFault, match=point):
        run_cycle(**kwargs)

    assert database.event_count(event_type="discovery.cycle_lineage_completed") == 0
    resumed = run_cycle(**kwargs)
    assert resumed["outcome"] in {"WAITING", "PASSED"}
    assert database.count("candidate_outcomes") == 1
    assert database.event_count(event_type="discovery.cycle_lineage_completed") == 1
    assert injector.triggered == [point]
