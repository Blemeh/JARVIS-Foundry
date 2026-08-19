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


def tree_command(marker: str) -> list[str]:
    return [sys.executable, "-c", (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{marker}', 'w', encoding='utf-8').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )]


def test_spawn_registers_pid_table_before_wait(tmp_path):
    from jarvis_foundry.supervisor.worker import load_adapter_pids

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=900)
    status = controller.spawn(
        "run-owned", sleep_command(), cwd=tmp_path,
        worker_type="probe", mission_id="m1", step_id="s1",
        stop_path=tmp_path / "EMERGENCY_STOP",
    )
    records = load_adapter_pids(controller.pid_table_path())
    assert "run-owned" in records
    record = records["run-owned"]
    assert record["pid"] == status.process.pid
    assert record["run_id"] == "run-owned"
    assert record["mission_id"] == "m1"
    assert record["step_id"] == "s1"
    assert record["worker_type"] == "probe"
    assert record["termination_state"] == "running"
    assert record["creation_identity"]["pid"] == status.process.pid
    controller.terminate("run-owned")
    assert load_adapter_pids(controller.pid_table_path()) == {}


def test_terminate_all_kills_owned_tree_and_spares_unrelated_python(tmp_path):
    import subprocess
    from jarvis_foundry.supervisor.worker import pid_alive

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    controller = WorkerController(database, lease_seconds=900)
    marker = tmp_path / "child.pid"
    decoy = subprocess.Popen(sleep_command(120), cwd=str(tmp_path))
    try:
        status = controller.spawn("tree-run", tree_command(str(marker)), cwd=tmp_path)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.05)
        child_pid = int(marker.read_text(encoding="utf-8"))
        assert pid_alive(child_pid)
        assert decoy.poll() is None
        controller.terminate_all()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (
                status.process.poll() is None or pid_alive(child_pid)):
            time.sleep(0.05)
        assert status.process.poll() is not None
        assert not pid_alive(child_pid)
        assert decoy.poll() is None, "unrelated python process was killed by name"
    finally:
        if decoy.poll() is None:
            decoy.kill()
            decoy.wait(timeout=5)


def test_adapter_heartbeat_child_is_killed_by_terminate_all(tmp_path):
    import os
    import threading
    from jarvis_foundry.workers.adapters.base import run_subprocess_with_heartbeat

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
                worker_type="probe",
            )
        except BaseException as error:  # noqa: BLE001 - test harness
            errors.append(error)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not controller.status("adapter-run").alive:
        time.sleep(0.05)
    status = controller.status("adapter-run")
    assert status.alive is True
    process = status.process
    assert process is not None and process.poll() is None
    killed = controller.terminate_all()
    assert "adapter-run" in killed
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.05)
    assert process.poll() is not None
    thread.join(timeout=5)


def test_heartbeat_runner_kills_child_when_stop_file_appears(tmp_path):
    import os
    from jarvis_foundry.workers.adapters.base import run_subprocess_with_heartbeat

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
        pid_table=tmp_path / "adapter_worker_pids.json",
    )
    elapsed = time.monotonic() - started
    assert elapsed < 8, f"STOP did not interrupt adapter child ({elapsed:.1f}s)"
    assert result.returncode != 0


def test_terminate_all_kills_pid_persisted_by_another_controller(tmp_path):
    import subprocess

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    registrar = WorkerController(database, lease_seconds=900)
    child = subprocess.Popen(sleep_command(60), cwd=str(tmp_path))
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

