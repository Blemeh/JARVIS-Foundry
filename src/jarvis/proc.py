"""Run a subprocess with a timeout and STOP polling; kill the whole process tree."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

WINDOWS = sys.platform == "win32"
POLL_S = 0.2

_SECRET = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_\-]{20,}"
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}"
    r"|(?i:(?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s'\"]{8,})")


def redact(text: str) -> str:
    """Mask things that look like credentials before text leaves the process."""
    return _SECRET.sub("[REDACTED]", text)


def tail(text: str, limit: int = 3000) -> str:
    return text if len(text) <= limit else "...\n" + text[-limit:]


@dataclass
class Result:
    code: int | None
    out: str
    err: str
    duration: float
    timed_out: bool = False
    stopped: bool = False


def which(command: str) -> str:
    """Resolve an executable (handles .cmd shims on Windows)."""
    return shutil.which(command) or command


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run(cmd: list[str] | str, cwd: Path, *, timeout: float, should_stop: Callable[[], bool]
        = lambda: False, stdin_text: str | None = None, env: dict | None = None) -> Result:
    """A str command runs through the shell (verification commands from config)."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="jarvis-proc-") as tmp:
        out_path, err_path, in_path = (Path(tmp) / n for n in ("out", "err", "in"))
        in_path.write_text(stdin_text or "", encoding="utf-8")
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS \
            else {"start_new_session": True}
        with in_path.open("rb") as fin, out_path.open("wb") as fout, err_path.open("wb") as ferr:
            try:
                proc = subprocess.Popen(cmd, cwd=str(cwd), shell=isinstance(cmd, str),
                                        stdin=fin, stdout=fout, stderr=ferr, env=env, **kwargs)
            except OSError as exc:
                return Result(None, "", f"could not start {cmd!r}: {exc}", 0.0)
            timed_out = stopped = False
            while proc.poll() is None:
                if should_stop():
                    stopped = True
                elif time.monotonic() - started > timeout:
                    timed_out = True
                if stopped or timed_out:
                    kill_tree(proc)
                    break
                time.sleep(POLL_S)
        out = out_path.read_bytes().decode("utf-8", errors="replace")
        err = err_path.read_bytes().decode("utf-8", errors="replace")
    return Result(proc.returncode, out, err, time.monotonic() - started, timed_out, stopped)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if WINDOWS:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFO
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
