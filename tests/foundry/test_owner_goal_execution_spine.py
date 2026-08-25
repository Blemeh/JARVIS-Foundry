"""Owner-facing GOAL admission through the one canonical execution spine."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

from jarvis_foundry.command_center import CommandCenterServer
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.supervisor.service import Supervisor

def _register(database: FoundryDatabase, repository: Path) -> None:
    database.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-owner-proof")))
    set_capability_state(
        database,
        "resource-owner-proof",
        "write_registered_worktree_files",
        "READY",
        evidence={"source": "owner-goal-spine-test"},
    )


def test_command_center_goal_plans_executes_rejects_false_success_and_repairs(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    repository = tmp_path / "owner-proof-repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email",
                    "owner-proof@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name",
                    "Owner Proof"], check=True)
    (repository / "README.md").write_text("# Owner proof\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "baseline"],
                   check=True, capture_output=True, text=True)
    _register(database, repository)
    server = CommandCenterServer(database, stop_path=tmp_path / "EMERGENCY_STOP")
    server.configure_owner(b"owner-goal-spine-secret-32-bytes!!")
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        body = json.dumps({
            "command_type": "GOAL",
            "target": "GLOBAL",
            "exact_text": (
                "Create a durable hello artifact. "
                "success: outcome receipt exists; direct inspection agrees"
            ),
        }).encode()
        with urlopen(Request(
            f"{base}/api/owner-command",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        ), timeout=3) as response:
            assert json.loads(response.read())["status"] == "APPLIED"
    finally:
        server.close()

    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        resource_id="resource-owner-proof",
        lab_scripted_hermes=True,
        lab_false_success_step_id="02-execute",
        portfolio_selection_enabled=True,
        max_repairs=2,
    )
    paths = FoundryPaths(tmp_path)
    first = run_cycle(
        database, paths, config,
        loop_id="owner-proof-plan", instance_id="owner-proof-supervisor-1",
    )
    assert first["outcome"] == "PASSED", first
    mission_id = first["mission_id"]
    mission = database.row("missions", "mission_id", mission_id)
    assert mission["state"] == "ACTION_SELECTION"
    assert mission["next_action"]

    second = run_cycle(
        database, paths, config,
        loop_id="owner-proof-execute", instance_id="owner-proof-supervisor-2",
    )
    assert second["outcome"] == "PASSED", second
    mission = database.row("missions", "mission_id", mission_id)
    assert mission["state"] == "COMPLETE"

    with database.connect(readonly=True) as connection:
        verdicts = [dict(row) for row in connection.execute(
            "SELECT * FROM verification_verdicts WHERE mission_id = ? "
            "ORDER BY rowid", (mission_id,)).fetchall()]
        run_rows = [dict(row) for row in connection.execute(
            "SELECT role FROM agent_runs WHERE mission_id = ? ORDER BY rowid",
            (mission_id,)).fetchall()]
    execute_verdicts = [
        row for row in verdicts if row["subject_id"] == "02-execute"]
    assert [row["status"] for row in execute_verdicts] == ["FAIL", "PASS"]
    roles = [row["role"] for row in run_rows]
    assert "repair" in roles
    events = [event.event_type for event in database.events()]
    assert "mission.completed" in events
    assert "portfolio.outcome_recorded" in events

    third = run_cycle(
        database, paths, config,
        loop_id="owner-proof-after-complete",
        instance_id="owner-proof-supervisor-3",
    )
    assert third["outcome"] == "WAITING", third
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 1


def test_machine_local_owner_secret_is_stable(tmp_path: Path) -> None:
    from jarvis_foundry.owner_controls import load_or_create_owner_secret

    path = tmp_path / "owner-control.key"
    first = load_or_create_owner_secret(path)
    second = load_or_create_owner_secret(path)
    assert first == second
    assert len(first) == 32
    assert first.hex() == path.read_text(encoding="ascii").strip()


def test_supervisor_owns_command_center_and_continues_goal_without_prompts(
    tmp_path: Path,
) -> None:
    """Production process shape: HTTP goal -> Supervisor-owned child cycles."""
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    repository = tmp_path / "supervisor-owned-repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email",
                    "supervisor-proof@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name",
                    "Supervisor Proof"], check=True)
    (repository / "README.md").write_text("# Supervisor proof\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "baseline"],
                   check=True, capture_output=True, text=True)
    _register(database, repository)

    config = tmp_path / "worker-routing.yaml"
    config.write_text(
        "\n".join([
            "enabled: true",
            "status: ACTIVE",
            "resource_id: resource-owner-proof",
            "provider: opencode_go",
            "model: deepseek-v4-flash",
            "lab_scripted_hermes: true",
            "lab_false_success_step_id: 02-execute",
            f"runtime_state_root: {json.dumps(str(tmp_path / 'state'))}",
            "executive:",
            "  portfolio_selection_enabled: true",
            "verification:",
            "  max_repairs: 2",
            "",
        ]),
        encoding="utf-8",
    )
    supervisor = Supervisor(
        database,
        tmp_path / "EMERGENCY_STOP",
        instance_id="supervisor-owner-spine",
        spawn_worker_loop=True,
        worker_loop_config=config,
        command_center_port=0,
    )
    assert supervisor.start() == "RUNNING"
    try:
        assert supervisor._command_center is not None
        host, port = supervisor._command_center.address
        body = json.dumps({
            "command_type": "GOAL",
            "target": "GLOBAL",
            "exact_text": (
                "Create the autonomous supervisor proof artifact. "
                "success: the durable outcome receipt exists"
            ),
        }).encode()
        with urlopen(Request(
            f"http://{host}:{port}/api/owner-command",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        ), timeout=3) as response:
            assert json.loads(response.read())["status"] == "APPLIED"

        deadline = time.monotonic() + 30
        mission = None
        while time.monotonic() < deadline:
            supervisor.heartbeat()
            with database.connect(readonly=True) as connection:
                row = connection.execute(
                    "SELECT * FROM missions ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                mission = dict(row) if row is not None else None
            if mission and mission["state"] == "COMPLETE":
                break
            time.sleep(0.2)
        assert mission is not None
        assert mission["state"] == "COMPLETE"
        with database.connect(readonly=True) as connection:
            verdicts = [row[0] for row in connection.execute(
                "SELECT status FROM verification_verdicts "
                "WHERE mission_id = ? AND subject_id = '02-execute' "
                "ORDER BY rowid", (mission["mission_id"],)).fetchall()]
        assert verdicts == ["FAIL", "PASS"]
        assert "mission.completed" in {
            event.event_type for event in database.events()}
    finally:
        supervisor.stop()
