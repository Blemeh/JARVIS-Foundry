"""Worker-loop spawn ownership: one owner, idle backoff, no accumulation.

The Supervisor spawns a --once worker loop per heartbeat when none is
alive. Without idle awareness that churns ~5 processes/min with zero work
(live 2026-09-30). The spawn gate skips only when a previous spawn exists,
the cooldown has not elapsed, no actionable ledger change postdates it,
and no worker-blocked mission awaits recovery.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.events import Event  # noqa: E402
from jarvis_foundry.supervisor.service import (  # noqa: E402
    WORKER_LOOP_RUN_ID,
    Supervisor,
)


def _config(tmp_path: Path, *, enabled: bool = True,
            hermes_ready: bool = True) -> Path:
    lines = [
        "enabled: true" if enabled else "enabled: false",
        "status: ACTIVE",
        "provider: opencode_go",
        "model: muse-spark-1.3-contributor",
        "hermes:",
        "  enabled: true",
        "  root: C:/worker-root",
        "  python_exe: C:/worker-root/python.exe",
        "  allow_data_training_tiers_noninteractive: true",
        "readiness:",
    ]
    if hermes_ready:
        lines += [
            "  hermes: {state: READY, note: probe, "
            "evidence: [probe-1]}",
        ]
    else:
        lines += [
            "  hermes: {state: NOT_READY, note: none, evidence: []}",
        ]
    path = tmp_path / "workers.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _supervisor(tmp_path: Path, config: Path) -> tuple[Supervisor, list]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    supervisor = Supervisor(
        database, tmp_path / "STOP", instance_id="spawn-test",
        spawn_worker_loop=True, worker_loop_config=config,
    )
    calls: list = []

    def fake_spawn(run_id, command, **kwargs):
        calls.append((run_id, list(command)))

        class _Status:
            alive = True

        return _Status()

    supervisor.workers.spawn = fake_spawn  # type: ignore[method-assign]
    return supervisor, calls


# 1. First heartbeat spawns (no spawn memory).
def test_first_tick_spawns(tmp_path: Path) -> None:
    supervisor, calls = _supervisor(tmp_path, _config(tmp_path))
    supervisor._maybe_spawn_worker_loop()
    assert [call[0] for call in calls] == [WORKER_LOOP_RUN_ID]
    assert "--once" in calls[0][1]


# 2. Immediate second tick with no change is skipped.
def test_idle_second_tick_skipped(tmp_path: Path) -> None:
    supervisor, calls = _supervisor(tmp_path, _config(tmp_path))
    supervisor._maybe_spawn_worker_loop()
    supervisor._maybe_spawn_worker_loop()
    assert len(calls) == 1


# 3. An actionable ledger change re-arms the spawn.
def test_actionable_change_rearms_spawn(tmp_path: Path) -> None:
    supervisor, calls = _supervisor(tmp_path, _config(tmp_path))
    supervisor._maybe_spawn_worker_loop()
    supervisor.database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-new", "objective": "o",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["worker step verified and accepted"]},
        source_type="test", source_id="spawn-test",
        idempotency_key="spawn-test:mission-new",
    ))
    supervisor._maybe_spawn_worker_loop()
    assert len(calls) == 2


# 4. Cooldown expiry is the liveness backstop.
def test_cooldown_expiry_spawns(tmp_path: Path, monkeypatch) -> None:
    import jarvis_foundry.supervisor.service as service

    monkeypatch.setattr(
        service, "WORKER_SPAWN_IDLE_COOLDOWN_SECONDS", 0.0)
    supervisor, calls = _supervisor(tmp_path, _config(tmp_path))
    supervisor._maybe_spawn_worker_loop()
    supervisor._maybe_spawn_worker_loop()
    assert len(calls) == 2


# 5. A live worker still prevents duplicate spawn.
def test_live_worker_prevents_spawn(tmp_path: Path) -> None:
    supervisor, calls = _supervisor(tmp_path, _config(tmp_path))
    supervisor._maybe_spawn_worker_loop()
    assert len(calls) == 1
    # Simulate the child still alive: status() reports alive via the
    # in-memory process table.
    import subprocess as _subprocess

    supervisor.workers._processes[WORKER_LOOP_RUN_ID] = (
        _subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]))
    try:
        supervisor.database.append(Event.create(
            "mission.created",
            {"mission_id": "mission-busy", "objective": "o",
             "resource_id": "resource-1", "state": "PLANNED",
             "acceptance_criteria": ["worker step verified and accepted"]},
            source_type="test", source_id="spawn-test",
            idempotency_key="spawn-test:mission-busy",
        ))
        supervisor._maybe_spawn_worker_loop()
        assert len(calls) == 1
    finally:
        supervisor.workers.terminate_all()


# 6. Restart (new Supervisor object) spawns immediately.
def test_restart_spawns_immediately(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first, first_calls = _supervisor(tmp_path, config)
    first._maybe_spawn_worker_loop()
    assert len(first_calls) == 1
    second, second_calls = _supervisor(tmp_path, config)
    second._maybe_spawn_worker_loop()
    assert len(second_calls) == 1


# 7. Disabled engine never spawns, even with changes.
def test_disabled_engine_never_spawns(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=False)
    supervisor, calls = _supervisor(tmp_path, config)
    supervisor.database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-off", "objective": "o",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["worker step verified and accepted"]},
        source_type="test", source_id="spawn-test",
        idempotency_key="spawn-test:mission-off",
    ))
    supervisor._maybe_spawn_worker_loop()
    supervisor._maybe_spawn_worker_loop()
    assert calls == []


# 8. Only one worker-execution owner exists: the Worker-Loop task stays
#    refused while the Supervisor owns the lifecycle.
def test_standalone_task_refused_while_supervisor_owns(tmp_path: Path) -> None:
    from jarvis_foundry.installer import (
        InstallerError,
        WorkerLoopInstaller,
    )

    database = FoundryDatabase(tmp_path / "foundry2.db")
    database.initialize()
    installer = WorkerLoopInstaller(
        database, foundry_home=tmp_path, hermes_home=tmp_path)
    with pytest.raises(InstallerError):
        installer.set_enabled(True)


# 9. Trigger vocabulary: mission/portfolio/initiative/discovery re-arm,
#    churn types do not.
def test_trigger_vocabulary(tmp_path: Path) -> None:
    supervisor, calls = _supervisor(tmp_path, _config(tmp_path))
    supervisor._maybe_spawn_worker_loop()
    assert len(calls) == 1
    baseline = supervisor._last_worker_spawn_seq
    assert baseline is not None
    # Churn-only events must not re-arm.
    supervisor.database.append(Event.create(
        "worker_loop.cycle_started",
        {"loop_id": "x"},
        source_type="test", source_id="spawn-test",
        idempotency_key="spawn-test:churn-1",
    ))
    assert supervisor._worker_spawn_triggered_since(baseline) is False
    # Actionable events re-arm.
    triggers = [
        ("mission.created",
         {"mission_id": "mission-t", "objective": "o",
          "resource_id": "resource-1", "state": "PLANNED",
          "acceptance_criteria": ["x"]}),
        ("portfolio.discovery_completed",
         {"objective_id": "obj-t", "repository": "r", "opportunities": []}),
        ("owner_directive.recorded",
         {"directive_id": "dir-t", "command_type": "GOAL",
          "exact_text": "improve projects"}),
        ("discovery.candidate_approved", {"candidate_id": "k-t"}),
    ]
    for event_type, payload in triggers:
        supervisor.database.append(Event.create(
            event_type, payload,
            source_type="test", source_id="spawn-test",
            idempotency_key=f"spawn-test:{event_type}",
        ))
        assert supervisor._worker_spawn_triggered_since(baseline) is True, \
            event_type
        baseline = supervisor.database.connect().__enter__().execute(
            "SELECT MAX(sequence) FROM events").fetchone()[0]


# 10. Post-spawn events are not absorbed into the baseline.
def test_baseline_predates_spawn(tmp_path: Path) -> None:
    supervisor, calls = _supervisor(tmp_path, _config(tmp_path))
    supervisor._maybe_spawn_worker_loop()
    baseline = supervisor._last_worker_spawn_seq
    assert baseline is not None
    supervisor.database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-late", "objective": "o",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["x"]},
        source_type="test", source_id="spawn-test",
        idempotency_key="spawn-test:mission-late",
    ))
    # The late event postdates the pre-spawn baseline: next gate re-arms.
    assert supervisor._worker_spawn_triggered_since(baseline) is True
