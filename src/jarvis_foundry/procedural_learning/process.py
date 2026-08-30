"""Owned OpenAdapt process-tree control.

OpenAdapt-Flow can leave browser children behind when its parent is killed.
This controller scopes termination to a process it launched, records the
descendant set, and verifies cleanup.  It never issues a machine-wide browser
kill.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..platform.windows_subprocess import (
    headless_popen_kwargs,
    headless_run_kwargs,
    pid_alive as _platform_pid_alive,
    sanitized_env,
)


def _windows_process_rows() -> list[tuple[int, int]]:
    if os.name != "nt":
        return []
    script = (
        "Get-CimInstance Win32_Process | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.ParentProcessId)\" }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=5, check=False,
            env=sanitized_env(),
            **headless_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    rows: list[tuple[int, int]] = []
    if result is not None and result.returncode == 0:
        for line in result.stdout.splitlines():
            try:
                child, parent = line.strip().split("|", 1)
                rows.append((int(child), int(parent)))
            except (ValueError, AttributeError):
                continue
        if rows:
            return rows

    # CIM is optional and can be shadowed or absent on managed hosts. The
    # Toolhelp snapshot is a read-only kernel fallback with the same parent /
    # child data, so a provider failure does not silently lose owned children.
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        invalid_snapshot = ctypes.c_void_p(-1).value
        if snapshot in (-1, invalid_snapshot):
            return []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            first = kernel32.Process32FirstW
            next_process = kernel32.Process32NextW
            first.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            next_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            ok = first(snapshot, ctypes.byref(entry))
            while ok:
                rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
                ok = next_process(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:  # noqa: BLE001 - advisory fallback
        return []
    return rows


def descendant_pids(root_pid: int) -> tuple[int, ...]:
    """Return a deterministic snapshot of descendants, excluding root."""
    if os.name != "nt":
        # /proc is not needed for the supported Windows integration; callers
        # on POSIX get process-group cleanup instead.
        return ()
    children: dict[int, list[int]] = {}
    for child, parent in _windows_process_rows():
        children.setdefault(parent, []).append(child)
    found: set[int] = set()
    pending = list(children.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        if pid in found or pid == root_pid:
            continue
        found.add(pid)
        pending.extend(children.get(pid, ()))
    return tuple(sorted(found))


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _platform_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


@dataclass(slots=True)
class OwnedProcessHandle:
    process: subprocess.Popen[str]
    run_id: str
    command: tuple[str, ...]
    descendants_seen: set[int] = field(default_factory=set)
    terminated: bool = False

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    def refresh(self) -> tuple[int, ...]:
        descendants = descendant_pids(self.pid)
        self.descendants_seen.update(descendants)
        return descendants

    def wait(self, timeout: float | None = None) -> int:
        try:
            code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.terminate_tree(reason="timeout")
            code = self.process.wait(timeout=10)
        return int(code)

    def terminate_tree(self, *, reason: str = "STOP") -> tuple[int, ...]:
        """Terminate only this root and the descendants it owns."""
        self.refresh()
        owned = set(self.descendants_seen)
        owned.add(self.pid)
        if os.name == "nt":
            # /T follows the root's live tree atomically.  The recorded set is
            # the bounded fallback for children that outlive a fast parent.
            if self.process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                    capture_output=True, text=True, timeout=10, check=False,
                    **headless_run_kwargs(),
                )
            for pid in sorted(owned - {self.pid}, reverse=True):
                if pid_alive(pid):
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True, text=True, timeout=10, check=False,
                        **headless_run_kwargs(),
                    )
        else:
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        self.terminated = True
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                self.process.kill()
            except OSError:
                pass
            self.process.wait(timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            alive = tuple(pid for pid in sorted(owned) if pid_alive(pid))
            if not alive:
                return ()
            time.sleep(0.1)
        return tuple(pid for pid in sorted(owned) if pid_alive(pid))

    def assert_clean(self) -> None:
        # A browser child can be in the short Windows termination window
        # after taskkill returns. Poll the already-owned set before declaring
        # a cleanup failure; never broaden the set to unrelated browsers.
        deadline = time.monotonic() + 5
        while True:
            self.refresh()
            alive = tuple(pid for pid in sorted(self.descendants_seen)
                          if pid_alive(pid))
            if not alive:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"owned OpenAdapt descendants remain after {self.run_id}: {alive}")
            time.sleep(0.1)


def launch_owned(
    command: list[str],
    *,
    run_id: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> OwnedProcessHandle:
    kwargs: dict[str, Any] = headless_popen_kwargs(
        cwd=str(cwd) if cwd else None,
        env=sanitized_env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if os.name != "nt":
        kwargs["start_new_session"] = True
    elif "creationflags" in kwargs:
        kwargs["creationflags"] = int(kwargs["creationflags"]) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(command, **kwargs)
    return OwnedProcessHandle(process, run_id, tuple(command))
