from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    record_step_contract,
)
from jarvis_foundry.supervisor.worker import WorkerController
from jarvis_foundry.workforce.commander import WorkforceCommander
from jarvis_foundry.workforce.contracts import WorkPackage


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "commander@example.invalid")
    _git(root, "config", "user.name", "Workforce Commander")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text(
        "BASE = True\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()
    return database


def _contract(package_id: str) -> MissionStepContract:
    return MissionStepContract(
        mission_id=f"mission-{package_id}",
        step_id=f"step-{package_id}",
        version=1,
        resource_id=f"workforce-resource:{package_id}",
        capability="git_local_commit",
        target_relative_path=f"src/{package_id}.py",
        instructions=f"Complete the bounded {package_id} task.",
        expected_effects=(f"{package_id} result exists",),
        rollback={"method": "preserve_worktree", "reversible": True},
        risk_class="C1",
        verification_criteria=(f"{package_id} focused test passes",),
        expected_artifacts=(f"src/{package_id}.py",),
        forbidden_paths=("live",),
    )


def _package(
    tmp_path: Path,
    source_sha: str,
    package_id: str,
    *,
    dependencies: tuple[str, ...] = (),
) -> tuple[WorkPackage, MissionStepContract]:
    contract = _contract(package_id)
    package = WorkPackage(
        package_id=package_id,
        parent_objective_id="objective-commander",
        mission_id=contract.mission_id,
        step_id=contract.step_id,
        role="builder",
        capability=contract.capability,
        bounded_goal=f"Exercise {package_id}.",
        acceptance_criteria=contract.verification_criteria,
        source_sha=source_sha,
        repository_id="commander-fixture",
        branch_name=f"candidate/{package_id}",
        worktree_path=str(tmp_path / "worktrees" / package_id),
        allowed_paths=(f"src/{package_id}.py",),
        forbidden_paths=contract.forbidden_paths,
        dependencies=dependencies,
        mission_step_contract_id=contract.contract_id,
        mission_step_contract_hash=contract.content_hash(),
        expected_artifacts=contract.expected_artifacts,
        required_tests=(
            "pytest -q tests/foundry/test_v13_workforce_commander.py",
        ),
        lease_seconds=300,
    )
    return package, contract


def _record_mission(
    database: FoundryDatabase,
    package: WorkPackage,
    contract: MissionStepContract,
) -> None:
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": package.mission_id,
            "objective": package.bounded_goal,
            "resource_id": contract.resource_id,
            "state": "PLANNED",
            "acceptance_criteria": list(package.acceptance_criteria),
            "next_action": contract.instructions,
        },
        source_type="test",
        source_id="v13-workforce-commander",
        idempotency_key=f"mission.created:{package.mission_id}",
    ))
    for status in ("ORIENTING", "ACTION_SELECTION"):
        database.append(Event.create(
            "mission.state_changed",
            {
                "mission_id": package.mission_id,
                "state": status,
                "next_action": contract.instructions,
            },
            source_type="test",
            source_id="v13-workforce-commander",
            idempotency_key=(
                f"mission.state_changed:{package.mission_id}:{status}"
            ),
        ))
    record_step_contract(database, contract)


def _sleep_command(seconds: int = 60) -> tuple[str, ...]:
    return (
        sys.executable,
        "-c",
        f"import time; time.sleep({seconds})",
    )


def test_admission_is_parallel_replay_safe_and_dependency_order_independent(
    tmp_path: Path,
) -> None:
    source, source_sha = _repository(tmp_path)
    database = _database(tmp_path)
    commander = WorkforceCommander(database)
    first, _ = _package(tmp_path, source_sha, "package-a")
    second, _ = _package(tmp_path, source_sha, "package-b")

    admitted = commander.admit(
        (second, first),
        source_repositories=source,
        max_parallel=2,
    )

    assert [item.package_id for item in admitted] == [
        "package-b",
        "package-a",
    ]
    assert {item.package_state.status for item in admitted} == {"READY"}
    assert all(item.preparation is not None for item in admitted)
    assert database.event_count(
        event_type="workforce.package_created",
        readonly=True,
    ) == 2
    assert database.event_count(
        event_type="workforce.worktree_prepared",
        readonly=True,
    ) == 2

    replayed = commander.admit(
        (second, first),
        source_repositories=source,
        max_parallel=2,
    )

    assert all(item.package_state.status == "READY" for item in replayed)
    assert database.event_count(
        event_type="workforce.package_created",
        readonly=True,
    ) == 2
    assert database.event_count(
        event_type="workforce.worktree_prepared",
        readonly=True,
    ) == 2

    dependent, _ = _package(
        tmp_path,
        source_sha,
        "package-dependent",
        dependencies=("package-a",),
    )
    dependency_result = commander.admit(
        (dependent,),
        source_repositories=source,
    )
    assert dependency_result[0].package_state.status == "PLANNED"
    assert dependency_result[0].preparation is None
    assert dependency_result[0].blocked_reason == (
        "dependencies pending: package-a"
    )


def test_admission_rejects_unknown_or_cyclic_dependencies_before_mutation(
    tmp_path: Path,
) -> None:
    source, source_sha = _repository(tmp_path)
    database = _database(tmp_path)
    commander = WorkforceCommander(database)
    missing, _ = _package(
        tmp_path,
        source_sha,
        "package-missing",
        dependencies=("not-present",),
    )

    with pytest.raises(ValueError, match="unknown workforce dependencies"):
        commander.admit((missing,), source_repositories=source)
    assert database.event_count(
        event_type="workforce.package_created",
        readonly=True,
    ) == 0

    cycle_a, _ = _package(
        tmp_path,
        source_sha,
        "cycle-a",
        dependencies=("cycle-b",),
    )
    cycle_b, _ = _package(
        tmp_path,
        source_sha,
        "cycle-b",
        dependencies=("cycle-a",),
    )
    with pytest.raises(ValueError, match="cyclic workforce dependencies"):
        commander.admit(
            (cycle_b, cycle_a),
            source_repositories=source,
        )
    assert database.event_count(
        event_type="workforce.package_created",
        readonly=True,
    ) == 0


def test_launch_report_proves_two_owned_processes_overlap_and_recover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, source_sha = _repository(tmp_path)
    database = _database(tmp_path)
    commander = WorkforceCommander(database)
    first, first_contract = _package(
        tmp_path,
        source_sha,
        "parallel-a",
    )
    second, second_contract = _package(
        tmp_path,
        source_sha,
        "parallel-b",
    )
    commander.admit(
        (first, second),
        source_repositories=source,
        max_parallel=2,
    )
    _record_mission(database, first, first_contract)
    _record_mission(database, second, second_contract)
    monkeypatch.setattr(
        commander.gateway,
        "_runner_command",
        lambda _package_id, _run_id: list(_sleep_command()),
    )

    report = commander.launch_processes(
        (first.package_id, second.package_id)
    )
    try:
        assert len(report.launches) == 2
        assert all(item.spawned for item in report.launches)
        assert len(report.live_run_ids) == 2
        assert report.peak_concurrency == 2
        assert all(
            commander.workers.status(run_id).alive
            for run_id in report.live_run_ids
        )

        restart = commander.audit_restart()
        assert {
            item.sessions[0].category for item in restart.packages
        } == {"ACTIVE"}
        assert restart.duplicate_issues == ()

        replay = commander.launch_processes(
            (first.package_id, second.package_id)
        )
        assert all(not item.spawned for item in replay.launches)
        assert replay.peak_concurrency == 2
        assert database.event_count(
            event_type="workforce.session_bound",
            readonly=True,
        ) == 2
    finally:
        for launch in report.launches:
            commander.workers.terminate(launch.binding.session.run_id)


def test_commander_rejects_a_process_controller_for_another_ledger(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "primary")
    other = _database(tmp_path / "other")

    with pytest.raises(ValueError, match="commander database"):
        WorkforceCommander(
            database,
            workers=WorkerController(other),
        )
