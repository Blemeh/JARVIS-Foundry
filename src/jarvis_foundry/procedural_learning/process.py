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
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: list[tuple[int, int]] = []
    for line in result.stdout.splitlines():
        try:
            child, parent = line.strip().split("|", 1)
            rows.append((int(child), int(parent)))
        except (ValueError, AttributeError):
            continue
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
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
                 "if($p){'1'}else{'0'}"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return result.stdout.strip() == "1"
        except (OSError, subprocess.TimeoutExpired):
            return False
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
                )
            for pid in sorted(owned - {self.pid}, reverse=True):
                if pid_alive(pid):
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True, text=True, timeout=10, check=False,
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
        self.refresh()
        alive = tuple(pid for pid in sorted(self.descendants_seen) if pid_alive(pid))
        if alive:
            raise RuntimeError(
                f"owned OpenAdapt descendants remain after {self.run_id}: {alive}")


def launch_owned(
    command: list[str],
    *,
    run_id: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> OwnedProcessHandle:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    return OwnedProcessHandle(process, run_id, tuple(command))

