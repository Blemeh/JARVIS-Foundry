"""WP2 STOP control tests: emergency hotkey and tray control (Windows).

The controls are exercised through the real hidden window and message loop:
the hotkey handler is triggered by posting WM_HOTKEY, and the tray command
path by posting WM_COMMAND. Real right-click interaction with the tray icon
cannot be automated headlessly; the menu wiring itself is exercised through
the same command handler the menu returns into.
"""

from __future__ import annotations

import sys
import time

import pytest

from jarvis_foundry.supervisor.controls import (
    ID_HOTKEY_STOP,
    ID_TRAY_EXIT,
    ID_TRAY_RESUME,
    ID_TRAY_STOP,
    WM_APP_CLOSE,
    WM_COMMAND,
    WM_HOTKEY,
    StopControls,
    StopControlError,
)
from jarvis_foundry.supervisor.estop import EmergencyStop

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="STOP controls are Windows-only"
)


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_default_interval_meets_stop_latency_cap():
    """Gate 04 Sec 6 caps STOP detection latency at 10s; the supervisor's
    sentinel poll cadence is the interval (review deleg_acbc8ce5 measured
    24s at the old 30s default — FIND-2026-08-12-06). The default must
    keep detection inside the cap."""
    from jarvis_foundry.supervisor.run import build_parser
    args = build_parser().parse_args([])
    assert args.interval <= 5.0
    # explicit override remains possible
    assert build_parser().parse_args(["--interval", "30"]).interval == 30.0


def test_stop_detection_latency_within_cap_staged(tmp_path):
    """Real supervisor subprocess at the compliant cadence: sentinel ->
    emergency_stop.frozen within 10s; resume (sentinel clear) continues
    the same loop. Staging home only — the live daemon is untouched."""
    import json
    import sqlite3
    import subprocess

    from jarvis_foundry.database import FoundryDatabase

    home = tmp_path / "home"
    home.mkdir()
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    estop = EmergencyStop(home / "EMERGENCY_STOP")

    proc = subprocess.Popen(
        [sys.executable, "-m", "jarvis_foundry.supervisor.run",
         "--home", str(home), "--interval", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        started = json.loads(proc.stdout.readline().strip())
        assert started["supervisor"] == "RUNNING"

        def frozen_latency():
            activate = time.monotonic()
            estop.activate("staged latency test", source="cli")
            deadline = activate + 15.0
            while time.monotonic() < deadline:
                conn = sqlite3.connect(f"file:{home / 'foundry.db'}?mode=ro",
                                       uri=True)
                row = conn.execute(
                    "SELECT MAX(occurred_at) FROM events "
                    "WHERE event_type = 'emergency_stop.frozen'").fetchone()
                conn.close()
                if row and row[0]:
                    return time.monotonic() - activate
                time.sleep(0.1)
            return None

        latency = None
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and latency is None:
            latency = frozen_latency()
        assert latency is not None, "emergency_stop.frozen never recorded"
        assert latency <= 10.0, f"STOP detection {latency:.1f}s > 10s cap"

        # resume: clearing the sentinel lets the same loop continue
        estop.clear()
        assert _wait_until(lambda: not estop.active()) is True
        def heartbeat_after_freeze():
            conn = sqlite3.connect(f"file:{home / 'foundry.db'}?mode=ro",
                                   uri=True)
            row = conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = "
                "'supervisor.heartbeat' AND occurred_at > ?",
                (estop.read().get("at", "") if estop.read() else "",)).fetchone()
            conn.close()
            return row[0]
        # a heartbeat after the frozen event proves the loop resumed
        conn = sqlite3.connect(f"file:{home / 'foundry.db'}?mode=ro", uri=True)
        frozen_at = conn.execute(
            "SELECT MAX(occurred_at) FROM events WHERE event_type = "
            "'emergency_stop.frozen'").fetchone()[0]
        conn.close()
        assert _wait_until(lambda: _heartbeat_count_after(home, frozen_at) >= 1,
                           timeout=15.0) is True
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _heartbeat_count_after(home, occurred_at) -> int:
    import sqlite3
    conn = sqlite3.connect(f"file:{home / 'foundry.db'}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = 'supervisor.heartbeat' "
        "AND occurred_at > ?", (occurred_at,)).fetchone()
    conn.close()
    return row[0]


def test_hotkey_writes_sentinel_with_hotkey_source(tmp_path):
    stop_path = tmp_path / "EMERGENCY_STOP"
    with StopControls(stop_path) as controls:
        assert controls._hwnd is not None
        assert controls.estop.active() is False

        # Simulate the registered hotkey firing.
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW(controls._hwnd, WM_HOTKEY, ID_HOTKEY_STOP, 0)

        assert _wait_until(controls.estop.active) is True
        record = controls.estop.read()
        assert record["source"] == "hotkey"
        assert "hotkey" in record["reason"]


def test_tray_stop_resume_exit_commands(tmp_path):
    stop_path = tmp_path / "EMERGENCY_STOP"
    controls = StopControls(stop_path)
    controls.start()
    try:
        assert controls._hwnd is not None
        assert controls.estop.active() is False

        # Tray STOP command path (the same handler the tray menu returns into).
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW(controls._hwnd, WM_COMMAND, ID_TRAY_STOP, 0)
        assert _wait_until(controls.estop.active) is True
        assert controls.estop.read()["source"] == "tray"

        # Tray RESUME clears the sentinel.
        user32.PostMessageW(controls._hwnd, WM_COMMAND, ID_TRAY_RESUME, 0)
        assert _wait_until(lambda: not controls.estop.active()) is True

        # Tray EXIT tears the window down; the message loop ends.
        thread = controls._thread
        user32.PostMessageW(controls._hwnd, WM_COMMAND, ID_TRAY_EXIT, 0)
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        controls.close()


def test_close_is_idempotent(tmp_path):
    stop_path = tmp_path / "EMERGENCY_STOP"
    controls = StopControls(stop_path)
    controls.start()
    controls.close()
    controls.close()


def test_hotkey_collision_fails_closed_with_real_error(tmp_path):
    """If another control already registered the hotkey, start() must raise
    the real registration error instead of hanging or silently running."""
    first = StopControls(tmp_path / "EMERGENCY_STOP", hotkey=0x77)  # VK_F8
    first.start()
    try:
        second = StopControls(tmp_path / "EMERGENCY_STOP_2", hotkey=0x77)
        with pytest.raises(StopControlError, match="RegisterHotKey failed"):
            second.start()
    finally:
        first.close()


def test_emergency_stop_used_by_controls_is_deterministic(tmp_path):
    stop_path = tmp_path / "EMERGENCY_STOP"
    estop = EmergencyStop(stop_path)
    record = estop.activate("tray STOP selected", source="tray")
    assert estop.read()["stop_id"] == record["stop_id"]
    assert estop.clear() is True
    assert estop.active() is False
