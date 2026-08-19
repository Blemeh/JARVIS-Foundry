"""Goal 1: canonical STOP identity, owned-tree kill, STOP latency."""
from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.mission.loop import BLOCKED_OUTCOME, MissionLoop
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import _default_stop_path
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.supervisor.worker import WorkerController, pid_alive
from jarvis_foundry.workers.adapters.base import STOP_POLL_SECONDS, run_subprocess_with_heartbeat
from jarvis_foundry.workers.adapters.fakes import ScriptedWorkerAdapter
from jarvis_foundry.roles.router import RoleRouter


def _sleep(seconds: int = 60) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_cli_supervisor_worker_loop_share_one_sentinel(tmp_path):
    home = tmp_path / "foundry"
    home.mkdir()
    paths = FoundryPaths(home)
    cfg = WorkerRuntimeConfig(stop_sentinel_path="foundry/EMERGENCY_STOP")
    cli = paths.emergency_stop.resolve()
    supervisor = Path(FoundryDatabase(paths.database).path).parent / "EMERGENCY_STOP"
    loop = _default_stop_path(paths, cfg).resolve()
    mission = MissionLoop(
        FoundryDatabase(home / "foundry.db"), RoleRouter(),
        ScriptedWorkerAdapter("openhands"))
    assert cli == supervisor.resolve() == loop == mission.stop_path.resolve()
    assert cli == home / "EMERGENCY_STOP"
    assert (home / "foundry" / "EMERGENCY_STOP") != cli


def test_stop_poll_is_independent_of_heartbeat_interval():
    assert STOP_POLL_SECONDS <= 1.0


def test_stop_latency_samples_max_under_10s(tmp_path):
    import threading

    samples: list[float] = []
    for index in range(5):
        stop = tmp_path / f"EMERGENCY_STOP-{index}"
        started = time.monotonic()

        def arm(path=stop) -> None:
            time.sleep(0.2)
            path.write_text('{"reason":"latency"}', encoding="utf-8")

        threading.Thread(target=arm, daemon=True).start()
        result = run_subprocess_with_heartbeat(
            _sleep(60),
            cwd=str(tmp_path),
            env=os.environ.copy(),
            timeout_seconds=30,
            heartbeat=lambda: None,
            interval_seconds=30,
            run_id=f"lat-{index}",
            stop_path=stop,
            pid_table=tmp_path / "adapter_worker_pids.json",
            stop_poll_seconds=1.0,
        )
        elapsed = time.monotonic() - started
        samples.append(elapsed)
        assert result.returncode != 0
    samples.sort()
    median = statistics.median(samples)
    assert max(samples) <= 10.0, samples
    assert median <= 10.0
    (tmp_path / "latency-samples.txt").write_text(
        f"min={min(samples):.3f} median={median:.3f} max={max(samples):.3f} "
        f"n={len(samples)} values={samples}\n",
        encoding="utf-8")


def test_mission_loop_blocks_on_canonical_sentinel(tmp_path):
    from worker_test_helpers import mission_database

    database, repository = mission_database(tmp_path)
    stop = Path(database.path).parent / "EMERGENCY_STOP"
    EmergencyStop(stop).activate("goal1")
    loop = MissionLoop(database, RoleRouter(), ScriptedWorkerAdapter("openhands"))
    outcome = loop.run_until_settled(
        mission_id="mission-test",
        step_id="s1",
        capability="write_registered_worktree_files",
        target=repository,
        instructions="no-op",
        expected_effects=("none",),
        rollback={"revert": "none"},
    )
    assert outcome.outcome == BLOCKED_OUTCOME
    assert "emergency stop" in (outcome.reason or "").lower()
