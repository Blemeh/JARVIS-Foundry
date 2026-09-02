"""V11 A4 durability foundation: hard-kill/restart/STOP/replay proof."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import rebuild_executive_state
from jarvis_foundry.executive.continuous import rebuild_wake_state, run_continuous_cycle, tick_wakes
from jarvis_foundry.events import Event
from jarvis_foundry.mission.contracts import latest_contracts_by_step
from jarvis_foundry.mission.verification import accepted_verifications, latest_verdict_for_step
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import LOOP_LEASE_KEY, run_cycle
from jarvis_foundry.supervisor.leases import LeaseManager
from jarvis_foundry.supervisor.service import Supervisor
from jarvis_foundry.supervisor.worker import process_creation_identity


ROOT = Path(__file__).resolve().parents[2]
FAULT_WORKER = Path(__file__).with_name("v11_a4_fault_worker.py")
SECRET = b"v11-a4-owner-secret-32-bytes!!"
HARD_EXIT = 86


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _setup(root: Path) -> tuple[FoundryDatabase, Path]:
    database = FoundryDatabase(root / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-09-02T00:00:00Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    repository = root / "resource"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    _git(repository, "config", "user.email", "a4@example.invalid")
    _git(repository, "config", "user.name", "A4 Durability")
    (repository / "README.md").write_text("# A4\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "baseline")
    database.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-a4")
    ))
    set_capability_state(
        database, "resource-a4", "write_registered_worktree_files", "READY",
        evidence={"source": "v11-a4-fault-harness"},
    )
    set_capability_state(
        database, "resource-a4", "git_local_commit", "READY",
        evidence={"source": "v11-a4-fault-harness"},
    )
    text = "Create the durable A4 receipt. success: outcome receipt exists"
    nonce = "a4-goal"
    OwnerControlService(database, secret=SECRET, owner_id="owner").apply(
        "GOAL", target="GLOBAL", exact_text=text, nonce=nonce,
        signature=sign_command(SECRET, "GOAL", "GLOBAL", text, nonce),
    )
    return database, repository


def _config(root: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        resource_id="resource-a4",
        lab_scripted_hermes=True,
        executive_autonomy_enabled=True,
        runtime_state_root=str(root / "runtime"),
    )


def _hard_exit(root: Path, mode: str, *, fault: str = "") -> None:
    command = [sys.executable, str(FAULT_WORKER), mode, str(root)]
    if fault:
        command.extend(["--fault", fault])
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == HARD_EXIT, (result.stdout, result.stderr)


def _restart(database: FoundryDatabase, root: Path, generation: int) -> None:
    supervisor = Supervisor(
        database, root / "EMERGENCY_STOP",
        instance_id=f"a4-supervisor-{generation}",
    )
    assert supervisor.start() == "RUNNING"
    supervisor.stop()


def _cycle(database: FoundryDatabase, root: Path, number: int) -> dict[str, object]:
    return run_cycle(
        database, FoundryPaths(root), _config(root),
        loop_id=f"a4-parent-{number}", instance_id=f"a4-parent-{number}",
    )


def _settle(database: FoundryDatabase, root: Path, start: int = 1) -> None:
    for number in range(start, start + 6):
        _cycle(database, root, number)
        with database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT state FROM missions ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        if row is not None and row["state"] == "COMPLETE":
            return
    raise AssertionError("mission did not settle within the bounded harness")


def _duplicate_builder_steps(database: FoundryDatabase) -> int:
    with database.connect(readonly=True) as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM (SELECT mission_id, step_id FROM agent_runs "
            "WHERE role = 'builder' GROUP BY mission_id, step_id HAVING COUNT(*) > 1)"
        ).fetchone()[0])


def _duplicate_effects(database: FoundryDatabase) -> int:
    with database.connect(readonly=True) as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM (SELECT json_extract(payload_json, '$.action_key') "
            "FROM events WHERE event_type = 'system.action_started' "
            "GROUP BY json_extract(payload_json, '$.action_key') HAVING COUNT(*) > 1)"
        ).fetchone()[0])


def _count(database: FoundryDatabase, event_type: str) -> int:
    return database.event_count(event_type=event_type)


def _role_count(database: FoundryDatabase, role: str) -> int:
    with database.connect(readonly=True) as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE role = ?", (role,),
        ).fetchone()[0])


def _one_mission(database: FoundryDatabase) -> str:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT mission_id FROM missions ORDER BY mission_id"
        ).fetchall()
    assert len(rows) == 1
    return str(rows[0]["mission_id"])


def _mission_count(database: FoundryDatabase) -> int:
    with database.connect(readonly=True) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0])


def _unverified_completions(database: FoundryDatabase) -> int:
    """Count terminal-success missions lacking an accepted PASS contract."""
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT mission_id FROM missions WHERE state = 'COMPLETE'"
        ).fetchall()
    failures = 0
    for row in rows:
        mission_id = str(row["mission_id"])
        accepted = accepted_verifications(database, mission_id=mission_id)
        for step_id in latest_contracts_by_step(database, mission_id):
            verdict = latest_verdict_for_step(database, mission_id, step_id)
            if (
                verdict is None
                or str(verdict.get("status") or "") != "PASS"
                or str(verdict.get("verdict_id") or "") not in accepted
            ):
                failures += 1
                break
    return failures


def test_a_kill_after_mission_creation_resumes_exact_mission(tmp_path: Path) -> None:
    database, _ = _setup(tmp_path)
    _hard_exit(tmp_path, "materialize")
    mission_id = _one_mission(database)
    _restart(database, tmp_path, 1)
    _settle(database, tmp_path)
    assert _one_mission(database) == mission_id
    assert _count(database, "mission.completed") == 1
    assert _unverified_completions(database) == 0


@pytest.mark.parametrize(
    ("fault", "expect_reconciled"),
    (("pre effect dispatch", False), ("post effect dispatch", True)),
)
def test_b_c_effect_boundary_kills_dispatch_once(
    tmp_path: Path, fault: str, expect_reconciled: bool,
) -> None:
    database, repository = _setup(tmp_path)
    _hard_exit(tmp_path, "cycle", fault=fault)
    _restart(database, tmp_path, 1)
    _settle(database, tmp_path)
    assert _one_mission(database)
    assert _role_count(database, "builder") == 2
    assert _duplicate_builder_steps(database) == 0
    assert _count(database, "system.action_started") == 2
    assert _duplicate_effects(database) == 0
    assert _git(repository, "rev-list", "--count", "HEAD") == "3"
    assert (_count(database, "effect.reconciled") == 1) is expect_reconciled
    assert _count(database, "mission.completed") == 1
    assert _unverified_completions(database) == 0


def test_d_kill_after_acceptance_never_redispatches(tmp_path: Path) -> None:
    database, repository = _setup(tmp_path)
    _hard_exit(
        tmp_path, "cycle", fault="post verification.accepted:02-execute"
    )
    assert _git(repository, "rev-list", "--count", "HEAD") == "3"
    assert _count(database, "verification.accepted") == 2
    _restart(database, tmp_path, 1)
    _settle(database, tmp_path)
    assert _role_count(database, "builder") == 2
    assert _duplicate_builder_steps(database) == 0
    assert _count(database, "system.action_started") == 2
    assert _duplicate_effects(database) == 0
    assert _count(database, "verification.accepted") == 2
    assert _count(database, "evidence.created") == 2
    assert _count(database, "mission.completed") == 1
    assert _unverified_completions(database) == 0


def test_e_wait_wake_survives_restart_and_fires_once(tmp_path: Path) -> None:
    database, _ = _setup(tmp_path)
    _hard_exit(tmp_path, "wake")
    reopened = FoundryDatabase(database.path)
    wakes = rebuild_wake_state(reopened)
    assert len([row for row in wakes.values() if row["objective_id"] == "wait-a4"]) == 1
    assert len(tick_wakes(reopened)) == 1
    assert tick_wakes(reopened) == []
    assert _count(reopened, "executive.wake_triggered") == 1


def test_f_g_stop_restart_and_resume_are_durable(tmp_path: Path) -> None:
    database, _ = _setup(tmp_path)
    _hard_exit(tmp_path, "stop")
    (tmp_path / "EMERGENCY_STOP").unlink()
    blocked = run_continuous_cycle(database, cycle_id="a4-stopped-restart")
    assert blocked["status"] == "OWNER_BLOCKED"
    assert _role_count(database, "builder") == 0
    assert _mission_count(database) == 0

    _hard_exit(tmp_path, "resume")
    _restart(database, tmp_path, 1)
    _settle(database, tmp_path)
    assert _role_count(database, "builder") == 2
    assert _duplicate_builder_steps(database) == 0
    assert _mission_count(database) == 1
    assert _count(database, "mission.completed") == 1
    assert _unverified_completions(database) == 0


def test_h_post_completion_kill_and_repeated_replay_converge(tmp_path: Path) -> None:
    database, _ = _setup(tmp_path)
    _hard_exit(tmp_path, "cycle", fault="post mission.completed")
    mission_id = _one_mission(database)
    assert database.row("missions", "mission_id", mission_id)["state"] == "COMPLETE"
    assert next(iter(rebuild_executive_state(database).objectives.values())).status != "COMPLETED"

    for generation in range(1, 4):
        _restart(database, tmp_path, generation)
        _cycle(database, tmp_path, generation)

    objective = next(iter(rebuild_executive_state(database).objectives.values()))
    assert objective.status == "COMPLETED"
    assert _mission_count(database) == 1
    assert _role_count(database, "builder") == 2
    assert _duplicate_builder_steps(database) == 0
    assert _count(database, "system.action_started") == 2
    assert _duplicate_effects(database) == 0
    assert _count(database, "verification.accepted") == 2
    assert _count(database, "evidence.created") == 2
    assert _count(database, "mission.completed") == 1
    terminal_progress_events = [
        event for event in database.events(
            event_types=("executive.objective_progress_recorded",)
        )
        if event.payload.get("status") == "COMPLETED"
    ]
    assert len(terminal_progress_events) == 1
    assert _unverified_completions(database) == 0
    assert database.quick_check() == "ok"


def test_supervisor_never_adopts_a_live_external_loop(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    owner_id = "live-external-loop"
    LeaseManager(database).acquire(LOOP_LEASE_KEY, owner_id, 120)
    database.append(Event.create(
        "worker_loop.cycle_started",
        {
            "instance_id": owner_id,
            "loop_id": "live-cycle",
            "resource_id": "resource-a4",
            "capability": "write_registered_worktree_files",
            "pid": os.getpid(),
            "creation_identity": process_creation_identity(os.getpid()),
        },
        source_type="supervisor",
        source_id=owner_id,
        idempotency_key="worker_loop.cycle_started:live-cycle",
    ))
    supervisor = Supervisor(
        database, tmp_path / "EMERGENCY_STOP", instance_id="a4-live-guard",
    )
    assert supervisor.start() == "RUNNING"
    assert database.row("leases", "lease_key", LOOP_LEASE_KEY)["owner_id"] == owner_id
    assert _count(database, "worker_loop.lease_reclaimed") == 0
    supervisor.stop()
