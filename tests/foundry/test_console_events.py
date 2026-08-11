"""Regression tests: console control events must produce a fast durable stop.

Live incident 2026-08-12: the V1 scheduled-task daemon ran as an interactive
console process; a console close (CTRL_CLOSE_EVENT) killed it with
0xC000013A (STATUS_CONTROL_C_EXIT) without releasing the singleton lease or
recording ``supervisor.stopped`` — the DB was left with a stale RUNNING
projection and an expired lease until startup recovery.

The daemon now installs a Windows console control handler; EVERY control
event (Ctrl+C / Ctrl+Break / console close / logoff / shutdown) performs a
fast durable stop. These tests pin that behavior:

- in-process: simulate CTRL_CLOSE_EVENT directly on the handler;
- end-to-end: spawn the real daemon in its own console/process group and
  deliver CTRL_BREAK_EVENT through the real Windows mechanism.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.supervisor.console import (
    CTRL_BREAK_EVENT,
    CTRL_CLOSE_EVENT,
    ConsoleControlHandler,
)
from jarvis_foundry.supervisor.run import make_console_handler
from jarvis_foundry.supervisor.service import Supervisor

LEASED = "supervisor:singleton"


def _wait_for(predicate, timeout: float = 30.0, interval: float = 0.1) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not reached within timeout")


def test_ctrl_close_event_produces_durable_stop(tmp_path, monkeypatch):
    """CTRL_CLOSE (console closed) must release the lease, record
    supervisor.stopped, and exit 0 — the exact failure mode of the live
    0xC000013A incident, driven through the handler directly."""
    database = FoundryDatabase(tmp_path / "foundry.db")
    stop_path = tmp_path / "EMERGENCY_STOP"
    supervisor = Supervisor(database, stop_path, instance_id="console-close-test")
    assert supervisor.start() == "RUNNING"

    exits: list[int] = []
    monkeypatch.setattr(
        "jarvis_foundry.supervisor.run.os._exit",
        lambda code: exits.append(code),
    )
    handler = make_console_handler(supervisor)
    assert handler._handle(CTRL_CLOSE_EVENT) == 1  # handled: grace period granted

    assert exits == [0]
    assert supervisor.started is False
    event_types = [event.event_type for event in database.events()]
    assert event_types.count("supervisor.stopped") == 1
    assert database.row("leases", "lease_key", LEASED) is None  # lease released


def test_ctrl_c_and_break_events_also_stop_durably(tmp_path, monkeypatch):
    database = FoundryDatabase(tmp_path / "foundry.db")
    stop_path = tmp_path / "EMERGENCY_STOP"
    supervisor = Supervisor(database, stop_path, instance_id="console-ctrl-c-test")
    assert supervisor.start() == "RUNNING"

    exits: list[int] = []
    monkeypatch.setattr(
        "jarvis_foundry.supervisor.run.os._exit",
        lambda code: exits.append(code),
    )
    handler = make_console_handler(supervisor)
    assert handler._handle(CTRL_BREAK_EVENT) == 1
    assert exits == [0]
    assert supervisor.started is False
    assert database.row("leases", "lease_key", LEASED) is None


def test_console_handler_swallows_callback_failures(tmp_path):
    """A failing stop callback must not crash the handler thread; the
    system's termination (or lease expiry) remains the fallback."""
    calls: list[int] = []

    def boom(event_code: int) -> None:
        calls.append(event_code)
        raise RuntimeError("stop failed")

    handler = ConsoleControlHandler(boom)
    assert handler._handle(CTRL_CLOSE_EVENT) == 1
    assert calls == [CTRL_CLOSE_EVENT]


def test_daemon_stops_durably_on_real_console_break_event(tmp_path):
    """End-to-end: the REAL daemon process, given a real console control
    event (CTRL_BREAK via GenerateConsoleCtrlEvent), exits 0 and leaves a
    released lease + supervisor.stopped behind. This exercises the actual
    Windows handler wiring, not just the callback."""
    if sys.platform != "win32":
        pytest.skip("Windows console control events are not available here")

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()

    CREATE_NEW_PROCESS_GROUP = 0x00000200
    # NOTE: no CREATE_NEW_CONSOLE — GenerateConsoleCtrlEvent can only signal
    # a process group that shares the CALLER's console, so the daemon must
    # inherit this console. CREATE_NEW_PROCESS_GROUP gives it its own group,
    # so CTRL_BREAK reaches only the daemon, not the test runner.
    process = subprocess.Popen(
        [sys.executable, "-m", "jarvis_foundry.supervisor.run",
         "--home", str(tmp_path), "--interval", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )
    try:
        # Wait until the daemon holds the lease and has heartbeated once.
        def running() -> bool:
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT 1 FROM leases WHERE lease_key = ?", (LEASED,)
                ).fetchone()
                started = connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type = 'supervisor.started'"
                ).fetchone()[0]
            return row is not None and started == 1
        _wait_for(running)

        # Deliver a real console control event to the daemon's process group.
        assert ctypes.windll.kernel32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, process.pid)

        returncode = process.wait(timeout=30)
        assert returncode == 0, process.stderr.read()

        with database.connect() as connection:
            stopped = connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'supervisor.stopped'"
            ).fetchone()[0]
            lease = connection.execute(
                "SELECT owner_id FROM leases WHERE lease_key = ?", (LEASED,)
            ).fetchone()
        assert stopped == 1  # durable stop recorded
        assert lease is None  # lease released, not left to expire
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_stale_running_projection_recovers_on_restart(tmp_path):
    """Startup recovery after a hard death (the incident's aftermath): a
    stale RUNNING projection + expired lease must reconcile into a fresh
    RUNNING instance with one canonical state and no duplicate."""
    database = FoundryDatabase(tmp_path / "foundry.db")
    stop_path = tmp_path / "EMERGENCY_STOP"
    first = Supervisor(database, stop_path, instance_id="dead-instance")
    assert first.start() == "RUNNING"
    # Simulate the 0xC000013A aftermath: process gone without stop() — the
    # lease stays and the projection says RUNNING, then the lease expires.
    from jarvis_foundry.clock import utc_now
    from datetime import timedelta
    with database.transaction() as connection:
        connection.execute(
            "UPDATE leases SET expires_at = ? WHERE lease_key = ?",
            ((utc_now() - timedelta(seconds=1)).isoformat(), LEASED),
        )

    fresh = Supervisor(database, stop_path, instance_id="fresh-instance")
    assert fresh.start() == "RUNNING"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT owner_id FROM leases WHERE lease_key = ?", (LEASED,)
        ).fetchone()
        assert row["owner_id"] == "fresh-instance"
        started = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'supervisor.started'"
        ).fetchone()[0]
    assert started == 2  # dead + fresh, no duplicates
    from jarvis_foundry.reconcile import reconcile
    assert reconcile(database).consistent is True
    fresh.stop()


def test_non_windows_platform_module_imports_and_noops(monkeypatch):
    """Portability regression: on non-Windows hosts (e.g. CI) the console
    module must import cleanly (no ctypes.WINFUNCTYPE there) and the handler
    must be a safe no-op — install() False, no crash, handler still returns
    the handled code so callers behave deterministically."""
    import importlib
    import sys

    import jarvis_foundry.supervisor.console as console

    monkeypatch.setattr(sys, "platform", "linux")
    try:
        importlib.reload(console)
        handler = console.ConsoleControlHandler(lambda event: None)
        assert handler._callback is None
        assert handler.install() is False
        assert handler._handle(CTRL_CLOSE_EVENT) == 1
        handler.uninstall()  # must not raise
    finally:
        monkeypatch.undo()
        importlib.reload(console)
        assert console._HandlerType is not None  # win32 state restored
