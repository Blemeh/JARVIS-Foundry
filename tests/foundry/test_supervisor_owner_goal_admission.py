from __future__ import annotations

import json
import subprocess
from pathlib import Path

from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.owner_controls import OwnerControlService
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.supervisor.service import Supervisor


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "resource"
    root.mkdir()
    for args in (("init",), ("config", "user.email", "jarvis-test@example.invalid"),
                 ("config", "user.name", "JARVIS Test")):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True, text=True)
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=root, check=True,
                   capture_output=True, text=True)
    return root


def _runtime_config(tmp_path: Path, resource_id: str) -> Path:
    path = tmp_path / "worker.yaml"
    path.write_text(
        "\n".join((
            "enabled: true",
            "status: ACTIVE",
            f"resource_id: {resource_id}",
            "capability: write_registered_worktree_files",
            "lab_scripted_hermes: true",
        )) + "\n",
        encoding="utf-8",
    )
    return path


def _owner_goal(database: FoundryDatabase, secret: bytes, text: str) -> None:
    service = OwnerControlService(database, secret=secret)
    from jarvis_foundry.owner_controls import sign_command
    nonce = f"nonce-{text[:12]}"
    service.apply(
        "GOAL", target="GLOBAL", exact_text=text, nonce=nonce,
        signature=sign_command(secret, "GOAL", "GLOBAL", text, nonce),
    )


def _fixture(tmp_path: Path) -> tuple[FoundryDatabase, Path, Path, bytes]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    resource = _repo(tmp_path)
    resource_id = "resource-owner-goal"
    database.append(registration_event(
        inspect_git_resource(resource, resource_id=resource_id)))
    stop = tmp_path / "EMERGENCY_STOP"
    config = _runtime_config(tmp_path, resource_id)
    return database, stop, config, b"owner-secret"


def test_supervisor_materializes_new_authenticated_goal_once(tmp_path: Path) -> None:
    database, stop, config, secret = _fixture(tmp_path)
    supervisor = Supervisor(
        database, stop, instance_id="supervisor-test",
        spawn_worker_loop=False, worker_loop_config=config,
    )
    assert supervisor.start() == "RUNNING"
    _owner_goal(database, secret, "Create one bounded local evidence artifact")

    assert supervisor.heartbeat() == "RUNNING"
    mission_events = [
        event for event in database.events()
        if event.event_type == "mission.created"
        and str(event.payload.get("created_from", "")).startswith("owner-directive:")
    ]
    assert len(mission_events) == 1
    mission_id = mission_events[0].payload["mission_id"]
    assert database.row("missions", "mission_id", mission_id)["state"] == "ACTION_SELECTION"
    contracts = database.all("mission_step_contracts", mission_id=mission_id)
    assert len(contracts) == 1
    assert contracts[0]["step_id"] == "step-a"
    assert contracts[0]["resource_id"] == "resource-owner-goal"

    # Replaying the Supervisor heartbeat cannot create a second mission or
    # contract for the same authenticated directive.
    assert supervisor.heartbeat() == "RUNNING"
    assert len([
        event for event in database.events()
        if event.event_type == "mission.created"
        and str(event.payload.get("created_from", "")).startswith("owner-directive:")
    ]) == 1
    assert len(database.all("mission_step_contracts", mission_id=mission_id)) == 1


def test_supervisor_does_not_replay_pre_start_goal(tmp_path: Path) -> None:
    database, stop, config, secret = _fixture(tmp_path)
    _owner_goal(database, secret, "Historical owner goal must not replay")
    supervisor = Supervisor(
        database, stop, instance_id="supervisor-test",
        spawn_worker_loop=False, worker_loop_config=config,
    )
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"
    assert not any(
        event.event_type == "mission.created"
        and str(event.payload.get("created_from", "")).startswith("owner-directive:")
        for event in database.events()
    )


def test_admitted_owner_goal_uses_existing_worker_loop(tmp_path: Path) -> None:
    database, stop, config_path, secret = _fixture(tmp_path)
    supervisor = Supervisor(
        database, stop, instance_id="supervisor-test",
        spawn_worker_loop=False, worker_loop_config=config_path,
    )
    assert supervisor.start() == "RUNNING"
    _owner_goal(database, secret, "Run one harmless bounded local evidence check")
    supervisor.heartbeat()
    config = WorkerRuntimeConfig.from_yaml(config_path)

    report = run_cycle(
        database, FoundryPaths(tmp_path), config,
        loop_id="owner-goal-cycle", instance_id="worker-loop-test",
    )
    assert report["outcome"] == "PASSED"
    mission_id = str(report["mission_id"])
    assert database.row("missions", "mission_id", mission_id)["state"] == "COMPLETE"
    assert any(
        event.event_type == "evidence.created"
        and event.payload.get("mission_id") == mission_id
        for event in database.events()
    )
    with database.connect() as connection:
        verdict = connection.execute(
            "SELECT status FROM verification_verdicts WHERE mission_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (mission_id,)
        ).fetchone()
    assert verdict is not None and verdict["status"] == "PASS"


def test_owner_goal_uses_isolated_artifact_when_default_already_exists(
    tmp_path: Path,
) -> None:
    database, stop, config_path, secret = _fixture(tmp_path)
    resource = Path(str(database.row("resources", "resource_id", "resource-owner-goal")["resolved_root"]))
    (resource / "step_a.md").write_text("STEP-A\n", encoding="utf-8")
    supervisor = Supervisor(
        database, stop, instance_id="supervisor-test",
        spawn_worker_loop=False, worker_loop_config=config_path,
    )
    assert supervisor.start() == "RUNNING"
    _owner_goal(database, secret, "Avoid a pre-existing artifact collision")
    supervisor.heartbeat()

    mission_event = next(
        event for event in database.events()
        if event.event_type == "mission.created"
        and str(event.payload.get("created_from", "")).startswith("owner-directive:")
    )
    mission_id = str(mission_event.payload["mission_id"])
    contract = database.all("mission_step_contracts", mission_id=mission_id)[0]
    artifact = str(contract["target_relative_path"])
    assert artifact.startswith("owner-goal-")
    assert artifact != "step_a.md"
    assert json.loads(str(contract["expected_artifacts_json"])) == [artifact]

    report = run_cycle(
        database, FoundryPaths(tmp_path), WorkerRuntimeConfig.from_yaml(config_path),
        loop_id="owner-goal-isolated-cycle", instance_id="worker-loop-test",
    )
    assert report["outcome"] == "PASSED"
    assert database.row("missions", "mission_id", mission_id)["state"] == "COMPLETE"
    with database.connect() as connection:
        verdict = connection.execute(
            "SELECT status FROM verification_verdicts WHERE mission_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (mission_id,)
        ).fetchone()
    assert verdict is not None and verdict["status"] == "PASS"


def test_stop_blocks_owner_goal_admission(tmp_path: Path) -> None:
    database, stop, config, secret = _fixture(tmp_path)
    supervisor = Supervisor(
        database, stop, instance_id="supervisor-test",
        spawn_worker_loop=False, worker_loop_config=config,
    )
    assert supervisor.start() == "RUNNING"
    service = OwnerControlService(database, secret=secret, stop_path=stop)
    from jarvis_foundry.owner_controls import sign_command
    stop_text = "freeze before owner goal admission"
    nonce = "nonce-stop"
    service.stop_now(
        reason=stop_text, nonce=nonce,
        signature=sign_command(secret, "STOP_EVERYTHING", "GLOBAL", stop_text, nonce),
    )
    _owner_goal(database, secret, "This goal must remain queued while STOP is active")
    assert supervisor.heartbeat() == "FROZEN"
    assert not any(
        event.event_type == "mission.created"
        and str(event.payload.get("created_from", "")).startswith("owner-directive:")
        for event in database.events()
    )
