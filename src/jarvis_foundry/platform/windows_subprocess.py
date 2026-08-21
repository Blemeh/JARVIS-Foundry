"""Headless subprocess defaults for JARVIS-owned Windows background work."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

CREATE_NO_WINDOW = 0x08000000
_STILL_ACTIVE = 259


def is_windows() -> bool:
    return os.name == "nt"


def creationflags_no_window(*, extra: int = 0) -> int:
    if not is_windows():
        return extra
    return extra | getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)


def headless_popen_kwargs(**extra: object) -> dict[str, object]:
    """Keyword arguments for ``subprocess.Popen`` that suppress console windows."""
    kwargs = dict(extra)
    if is_windows():
        flags = int(kwargs.pop("creationflags", 0))
        kwargs["creationflags"] = creationflags_no_window(extra=flags)
        startupinfo = kwargs.get("startupinfo")
        if startupinfo is None:
            startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
    return kwargs


def headless_run_kwargs(**extra: object) -> dict[str, object]:
    """Keyword arguments for ``subprocess.run`` that suppress console windows."""
    kwargs = dict(extra)
    if is_windows():
        flags = int(kwargs.pop("creationflags", 0))
        kwargs["creationflags"] = creationflags_no_window(extra=flags)
        startupinfo = kwargs.get("startupinfo")
        if startupinfo is None:
            startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
    return kwargs


def headless_run(
    args: Any,
    *,
    background: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run a subprocess; suppress console allocation on Windows when ``background``."""
    if background:
        kwargs = {**headless_run_kwargs(), **kwargs}
    return subprocess.run(args, **kwargs)


def headless_popen(
    args: Any,
    *,
    background: bool = True,
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    """Start a subprocess; suppress console allocation on Windows when ``background``."""
    if background:
        kwargs = {**headless_popen_kwargs(), **kwargs}
    return subprocess.Popen(args, **kwargs)


def pid_alive(pid: int) -> bool:
    """Return whether ``pid`` refers to a live process without spawning a console."""
    if pid <= 0:
        return False
    if is_windows():
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — advisory liveness probe
        return False


def background_python_executable(preferred: str | Path | None = None) -> str:
    """Return ``pythonw.exe`` for GUI-subsystem background workers on Windows."""
    exe = Path(preferred or sys.executable)
    if is_windows():
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw.resolve())
    return str(exe.resolve())
