"""WP2 worker control tests: spawn, lease, heartbeat, termination, and reap."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.supervisor.worker import WorkerController
from jarvis_foundry.workers.adapters.base import run_subprocess_with_heartbeat


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


def test_adapter_heartbeat_child_is_killed_by_terminate_all(tmp_path):
    """P0-A: adapter children started via run_subprocess_with_heartbeat
    must be registered with WorkerController so STOP/terminate_all kills
    them. In-flight Hermes must not outlive jarvis stop."""
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=900)
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            run_subprocess_with_heartbeat(
                sleep_command(60),
                cwd=str(tmp_path),
                env=os.environ.copy(),
                timeout_seconds=120,
                heartbeat=lambda: None,
                interval_seconds=0.1,
                run_id="adapter-run",
                workers=controller,
                stop_path=tmp_path / "NO_STOP",
            )
        except BaseException as error:  # noqa: BLE001 - test harness
            errors.append(error)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not controller.status("adapter-run").alive:
        time.sleep(0.05)
    status = controller.status("adapter-run")
    assert status.alive is True, "adapter child was never registered with WorkerController"
    process = status.process
    assert process is not None and process.poll() is None

    killed = controller.terminate_all()
    assert "adapter-run" in killed
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.05)
    assert process.poll() is not None, "STOP/terminate_all left the adapter child alive"
    thread.join(timeout=5)


def test_terminate_all_kills_pid_persisted_by_another_controller(tmp_path):
    """P0-A: Supervisor terminate_all must kill adapter PIDs recorded next
    to the ledger even when this process did not spawn them."""
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    registrar = WorkerController(database, lease_seconds=900)
    child = __import__("subprocess").Popen(sleep_command(60), cwd=str(tmp_path))
    try:
        registrar.attach("cross-proc", child, acquire_lease=False)
        assert child.poll() is None
        killer = WorkerController(database, lease_seconds=900)
        killer.terminate_all()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.05)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_heartbeat_runner_kills_child_when_stop_file_appears(tmp_path):
    """P0-A: run_subprocess_with_heartbeat must poll the STOP sentinel and
    kill the in-flight child instead of waiting out the adapter timeout."""
    stop = tmp_path / "EMERGENCY_STOP"
    started = time.monotonic()

    def heartbeat() -> None:
        if not stop.exists():
            stop.write_text('{"reason": "test stop"}', encoding="utf-8")

    result = run_subprocess_with_heartbeat(
        sleep_command(60),
        cwd=str(tmp_path),
        env=os.environ.copy(),
        timeout_seconds=30,
        heartbeat=heartbeat,
        interval_seconds=0.1,
        run_id="stop-run",
        stop_path=stop,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 8, f"STOP did not interrupt adapter child ({elapsed:.1f}s)"
    assert result.returncode != 0
