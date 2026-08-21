"""Tests for Windows headless subprocess helpers and production launchers."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis_foundry.deployment.layout import headless_launcher_cmd, headless_task_command, launcher_cmd
from jarvis_foundry.deployment.release import _install_stable_launcher
from jarvis_foundry.platform.windows_subprocess import (
    CREATE_NO_WINDOW,
    background_python_executable,
    creationflags_no_window,
    headless_popen,
    headless_popen_kwargs,
    headless_run,
    headless_run_kwargs,
    pid_alive,
)


def test_headless_run_kwargs_adds_create_no_window_on_windows(monkeypatch):
    monkeypatch.setattr("jarvis_foundry.platform.windows_subprocess.is_windows", lambda: True)
    kwargs = headless_run_kwargs(capture_output=True)
    assert kwargs["creationflags"] & CREATE_NO_WINDOW


def test_headless_popen_kwargs_adds_create_no_window_on_windows(monkeypatch):
    monkeypatch.setattr("jarvis_foundry.platform.windows_subprocess.is_windows", lambda: True)
    kwargs = headless_popen_kwargs()
    assert kwargs["creationflags"] & CREATE_NO_WINDOW


def test_headless_kwargs_noop_off_windows(monkeypatch):
    monkeypatch.setattr("jarvis_foundry.platform.windows_subprocess.is_windows", lambda: False)
    assert headless_run_kwargs() == {}
    assert headless_popen_kwargs() == {}


def test_background_python_executable_prefers_pythonw(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis_foundry.platform.windows_subprocess.is_windows", lambda: True)
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "python.exe").write_text("", encoding="utf-8")
    (scripts / "pythonw.exe").write_text("", encoding="utf-8")
    assert background_python_executable(scripts / "python.exe") == str((scripts / "pythonw.exe").resolve())


def test_stable_launchers_install_cmd_and_vbs(tmp_path):
    root = tmp_path / "JARVIS"
    path = _install_stable_launcher(root)
    assert path == launcher_cmd(root)
    headless = headless_launcher_cmd(root)
    assert headless.is_file()
    text = headless.read_text(encoding="utf-8")
    assert "pythonw.exe" in text
    assert "jarvis_foundry.deployment.headless_launcher" in text
    assert "CURRENT" in text


def test_headless_task_command_points_at_wscript_and_vbs(tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    _install_stable_launcher(root)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    wscript_dir = tmp_path / "Windows" / "System32"
    wscript_dir.mkdir(parents=True)
    (wscript_dir / "wscript.exe").write_text("", encoding="utf-8")
    command, prefix = headless_task_command(root)
    assert command.endswith("wscript.exe")
    assert "jarvis-supervisor-headless.vbs" in prefix
    assert prefix.startswith("//B //Nologo")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only subprocess flag probe")
def test_create_no_window_suppresses_console_for_cmd():
    result = subprocess.run(
        ["cmd", "/c", "echo jarvis-headless-probe"],
        capture_output=True,
        text=True,
        timeout=10,
        **headless_run_kwargs(),
    )
    assert result.returncode == 0
    assert "jarvis-headless-probe" in result.stdout


def test_headless_kwargs_preserve_existing_creationflags(monkeypatch):
    monkeypatch.setattr("jarvis_foundry.platform.windows_subprocess.is_windows", lambda: True)
    extra = 0x00000008  # DETACHED_PROCESS
    kwargs = headless_run_kwargs(creationflags=extra)
    assert kwargs["creationflags"] & CREATE_NO_WINDOW
    assert kwargs["creationflags"] & extra


def test_headless_run_wrapper_uses_no_window(monkeypatch):
    monkeypatch.setattr("jarvis_foundry.platform.windows_subprocess.is_windows", lambda: True)
    calls: list[dict] = []

    def fake_run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("jarvis_foundry.platform.windows_subprocess.subprocess.run", fake_run)
    result = headless_run(["cmd", "/c", "echo test"], capture_output=True, text=True)
    assert result.stdout == "ok"
    assert calls[0]["creationflags"] & CREATE_NO_WINDOW


def test_headless_popen_wrapper_uses_no_window(monkeypatch):
    monkeypatch.setattr("jarvis_foundry.platform.windows_subprocess.is_windows", lambda: True)
    calls: list[dict] = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append(kwargs)
            self.args = args
            self.pid = 4242

        def poll(self):
            return 0

    monkeypatch.setattr("jarvis_foundry.platform.windows_subprocess.subprocess.Popen", FakePopen)
    process = headless_popen(["cmd", "/c", "echo test"])
    assert process.pid == 4242
    assert calls[0]["creationflags"] & CREATE_NO_WINDOW


def test_pid_alive_current_process():
    assert pid_alive(os.getpid()) is True


def test_pid_alive_invalid_pid():
    assert pid_alive(-1) is False
    assert pid_alive(0) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only pid probe")
def test_pid_alive_does_not_spawn_tasklist():
    assert pid_alive(os.getpid()) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only subprocess flag probe")
def test_headless_run_capture_and_returncode():
    result = headless_run(
        ["cmd", "/c", "exit", "7"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 7


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only subprocess flag probe")
def test_headless_popen_capture_and_terminate():
    process = headless_popen(
        ["cmd", "/c", "echo jarvis-popen-probe"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, _stderr = process.communicate(timeout=10)
    assert process.returncode == 0
    assert "jarvis-popen-probe" in stdout


def test_release_python_executables_accepts_pythonw_pair(tmp_path):
    from jarvis_foundry.deployment.launcher import _release_python_executables

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    py = scripts / "python.exe"
    pyw = scripts / "pythonw.exe"
    py.write_text("", encoding="utf-8")
    pyw.write_text("", encoding="utf-8")
    allowed = _release_python_executables(py)
    assert py.resolve() in allowed
    assert pyw.resolve() in allowed
