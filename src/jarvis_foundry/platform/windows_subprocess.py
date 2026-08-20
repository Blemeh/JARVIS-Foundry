"""Headless subprocess defaults for JARVIS-owned Windows background work."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


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
    return kwargs


def headless_run_kwargs(**extra: object) -> dict[str, object]:
    """Keyword arguments for ``subprocess.run`` that suppress console windows."""
    kwargs = dict(extra)
    if is_windows():
        flags = int(kwargs.pop("creationflags", 0))
        kwargs["creationflags"] = creationflags_no_window(extra=flags)
    return kwargs


def background_python_executable(preferred: str | Path | None = None) -> str:
    """Return ``pythonw.exe`` for GUI-subsystem background workers on Windows."""
    exe = Path(preferred or sys.executable)
    if is_windows():
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw.resolve())
    return str(exe.resolve())
