"""WP2 worker control tests: spawn, lease, heartbeat, termination, and reap."""

from __future__ import annotations

import sys
import time

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.supervisor.worker import WorkerController


def sleep_command(seconds: int = 120) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_spawn_tracks_live_worker_with_lease(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=900)

    status = controller.spawn("run-1", sleep_command(), cwd=tmp_path)

    assert status.alive is True
    assert status.lease_expired is False
    assert controller.active_runs() == ["run-1"]
    lease = database.row("leases", "lease_key", "worker:run-1")
    assert lease is not None
    assert lease["owner_id"] == "worker:run-1"
    controller.terminate("run-1")


def test_heartbeat_renews_lease(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=60)

    controller.spawn("run-1", sleep_command(), cwd=tmp_path)
    before = database.row("leases", "lease_key", "worker:run-1")["expires_at"]
    time.sleep(0.2)
    controller.heartbeat("run-1")
    after = database.row("leases", "lease_key", "worker:run-1")["expires_at"]

    assert after > before
    controller.terminate("run-1")


def test_terminate_kills_process_and_releases_lease(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=900)

    status = controller.spawn("run-1", sleep_command(), cwd=tmp_path)
    process = status.process
    assert process is not None and process.poll() is None

    assert controller.terminate("run-1") is True
    assert process.poll() is not None
    assert database.row("leases", "lease_key", "worker:run-1") is None
    assert controller.active_runs() == []


def test_reap_terminates_expired_but_alive_worker(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=1)

    status = controller.spawn("run-1", sleep_command(), cwd=tmp_path)
    process = status.process
    assert process is not None and process.poll() is None
    time.sleep(1.5)

    status = controller.status("run-1")
    assert status.alive is True
    assert status.lease_expired is True

    reaped = controller.reap()
    assert reaped == ["run-1"]
    assert process.poll() is not None
    assert controller.active_runs() == []


def test_reap_releases_lease_of_naturally_exited_worker(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=900)

    controller.spawn("run-1", [sys.executable, "-c", "pass"], cwd=tmp_path)
    deadline = time.monotonic() + 10
    while controller.status("run-1").alive and time.monotonic() < deadline:
        time.sleep(0.05)
    assert controller.status("run-1").alive is False

    assert controller.reap() == []
    assert controller.active_runs() == []


def test_terminate_all_stops_every_worker(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=900)

    first = controller.spawn("run-1", sleep_command(), cwd=tmp_path)
    second = controller.spawn("run-2", sleep_command(), cwd=tmp_path)

    assert controller.terminate_all() == ["run-1", "run-2"]
    assert first.process.poll() is not None
    assert second.process.poll() is not None
    assert controller.active_runs() == []


def test_double_spawn_of_live_run_is_rejected(tmp_path):
    """A second spawn of an already-live run must fail, not silently
    double-spawn (adversarial finding: same-owner lease defeated the guard)."""
    from jarvis_foundry.supervisor.leases import LeaseUnavailable

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=900)
    other = WorkerController(database, lease_seconds=900)

    first = controller.spawn("run-1", sleep_command(), cwd=tmp_path)
    with pytest.raises(LeaseUnavailable, match="already has a live lease"):
        other.spawn("run-1", sleep_command(), cwd=tmp_path)
    with pytest.raises(LeaseUnavailable, match="already has a live lease"):
        controller.spawn("run-1", sleep_command(), cwd=tmp_path)

    assert first.process.poll() is None  # original worker untouched
    assert len(controller.active_runs()) == 1
    controller.terminate("run-1")


def test_failed_spawn_releases_lease(tmp_path):
    """If the process cannot be started, the lease must not leak."""
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=900)

    with pytest.raises(OSError):
        controller.spawn("run-bogus", [str(tmp_path / "no-such-executable.exe")], cwd=tmp_path)

    assert database.row("leases", "lease_key", "worker:run-bogus") is None
    assert controller.active_runs() == []
