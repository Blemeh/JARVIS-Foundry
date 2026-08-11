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
