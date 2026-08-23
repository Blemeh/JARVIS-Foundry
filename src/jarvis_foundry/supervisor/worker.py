"""Bounded worker process control for the independent Supervisor.

The Supervisor spawns workers as ordinary subprocesses, tracks each one with
a durable lease, and can terminate them deterministically on STOP or lease
expiry. Workers never outlive their lease: ``reap`` terminates any still-alive
worker whose lease expired and releases the lease of any worker whose process
already exited.

Adapter children (Hermes / OpenHands) that are not spawned by this
controller still register their PIDs here (in-memory and in a JSON table
next to the ledger) so STOP / ``terminate_all`` can kill the owned tree.

Termination is always by exact owned PID (and that PID's descendant tree).
Never by executable or process name.

The controller is Hermes-independent by construction (no Hermes imports).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase
from ..platform.windows_subprocess import (
    headless_popen_kwargs,
    headless_run_kwargs,
    pid_alive as _pid_alive,
)
from .leases import LeaseManager, LeaseUnavailable

ADAPTER_PID_TABLE_NAME = "adapter_worker_pids.json"
GRACEFUL_WAIT_SECONDS = 2.0
FORCE_WAIT_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class WorkerStatus:
    run_id: str
    lease_key: str
    owner_id: str | None
    expires_at: str | None
    alive: bool
    lease_expired: bool
    process: subprocess.Popen | None = None


def worker_lease_key(run_id: str) -> str:
    return f"worker:{run_id}"


def adapter_pid_table_path(database: FoundryDatabase | Path) -> Path:
    root = Path(database.path if isinstance(database, FoundryDatabase) else database)
    return root.resolve().parent / ADAPTER_PID_TABLE_NAME


def default_adapter_pid_table() -> Path | None:
    env = os.environ.get("JARVIS_ADAPTER_PID_TABLE")
    if env:
        return Path(env)
    home = os.environ.get("JARVIS_HOME")
    if home:
        return Path(home) / ADAPTER_PID_TABLE_NAME
    return None


def load_adapter_pids(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for run_id, record in raw.items():
        if isinstance(record, dict) and "pid" in record:
            out[str(run_id)] = record
    return out


def _retry_io(op, retries: int = 16) -> None:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            op()
            return
        except OSError as error:
            last = error
            time.sleep(0.05 * (attempt + 1))
    if last is not None:
        raise last


def _write_adapter_pids(path: Path, records: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")

    def _replace() -> None:
        temporary.write_text(
            json.dumps(records, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    try:
        _retry_io(_replace)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def process_creation_identity(pid: int) -> dict[str, Any]:
    """Best-effort Windows process identity (never used as a kill key)."""
    identity: dict[str, Any] = {"pid": int(pid)}
    if pid <= 0:
        return identity
    if os.name != "nt":
        identity["exe"] = None
        identity["create_time"] = None
        return identity
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return identity
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if kernel32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel), ctypes.byref(user)):
                identity["create_time"] = (
                    creation.dwHighDateTime << 32) | creation.dwLowDateTime
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                identity["exe"] = buf.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — identity is advisory
        return identity
    return identity


def list_process_tree(root_pid: int) -> list[int]:
    """Owned descendant PIDs including ``root_pid``, never by name."""
    if root_pid <= 0:
        return []
    children = {root_pid: []}
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            TH32CS_SNAPPROCESS = 0x00000002
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

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

            snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snap == -1:
                return [root_pid]
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                Process32FirstW = kernel32.Process32FirstW
                Process32NextW = kernel32.Process32NextW
                Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
                Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
                ok = Process32FirstW(snap, ctypes.byref(entry))
                parent_map: dict[int, int] = {}
                while ok:
                    parent_map[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                    ok = Process32NextW(snap, ctypes.byref(entry))
            finally:
                kernel32.CloseHandle(snap)
            children = {root_pid: []}
            for pid, parent in parent_map.items():
                children.setdefault(parent, []).append(pid)
                children.setdefault(pid, [])
        except Exception:  # noqa: BLE001
            return [root_pid]
    owned = []
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        owned.append(pid)
        stack.extend(children.get(pid, []))
    return owned


def pid_alive(pid: int) -> bool:
    return _pid_alive(pid)


def _record_is_live(record: Mapping[str, Any]) -> bool:
    """Confirm liveness using a complete creation identity.

    A PID without a creation timestamp is ambiguous after a restart. It can
    be reported for diagnostics, but it is never sufficient evidence for a
    kill or an adoption decision.
    """
    pid = int(record["pid"])
    if not pid_alive(pid):
        return False
    expected = record.get("creation_identity")
    if not isinstance(expected, Mapping) or expected.get("create_time") is None:
        return False
    observed = process_creation_identity(pid)
    if observed.get("create_time") != expected.get("create_time"):
        return False
    expected_exe = expected.get("exe")
    observed_exe = observed.get("exe")
    if expected_exe and observed_exe:
        return str(expected_exe).casefold() == str(observed_exe).casefold()
    return True


def _taskkill(pid: int, *, force: bool) -> None:
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    subprocess.run(
        command, capture_output=True, text=True, timeout=15, check=False,
        **headless_run_kwargs(),
    )


def terminate_owned_tree(
    pid: int,
    *,
    process: subprocess.Popen | None = None,
    grace_seconds: float = GRACEFUL_WAIT_SECONDS,
) -> list[int]:
    """Graceful terminate → bounded wait → forced owned-tree kill.

    Returns the owned PID set that was targeted. Unrelated processes with
    the same executable name are never selected.
    """
    if pid <= 0:
        return []
    owned = list_process_tree(pid)
    if os.name == "nt":
        _taskkill(pid, force=False)
    elif process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + max(0.1, grace_seconds)
    while time.monotonic() < deadline:
        if not any(pid_alive(item) for item in owned):
            return owned
        time.sleep(0.05)
    if os.name == "nt":
        _taskkill(pid, force=True)
        for item in owned:
            if pid_alive(item):
                _taskkill(item, force=True)
    elif process is not None and process.poll() is None:
        process.kill()
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    force_deadline = time.monotonic() + FORCE_WAIT_SECONDS
    while time.monotonic() < force_deadline:
        if not any(pid_alive(item) for item in owned):
            return owned
        time.sleep(0.05)
        owned = list(dict.fromkeys([*owned, *list_process_tree(pid)]))
    return owned


def kill_pid(pid: int) -> None:
    """Terminate a process tree. Best-effort; missing PIDs are ignored."""
    terminate_owned_tree(pid)


def terminate_popen(process: subprocess.Popen, *, timeout: float = 10) -> None:
    if process.poll() is not None:
        return
    terminate_owned_tree(process.pid, process=process, grace_seconds=min(GRACEFUL_WAIT_SECONDS, timeout))
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def register_adapter_pid(
    path: Path,
    run_id: str,
    pid: int,
    *,
    mission_id: str | None = None,
    step_id: str | None = None,
    worker_type: str | None = None,
    working_directory: str | Path | None = None,
    stop_path: str | Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = load_adapter_pids(path)
    identity = process_creation_identity(pid)
    record: dict[str, Any] = {
        "pid": int(pid),
        "root_pid": int(pid),
        "run_id": run_id,
        "mission_id": mission_id,
        "step_id": step_id,
        "worker_type": worker_type,
        "working_directory": str(working_directory) if working_directory else None,
        "stop_path": str(Path(stop_path).resolve()) if stop_path else None,
        "recorded_at": isoformat(),
        "creation_identity": identity,
        "owned_descendants": list_process_tree(pid),
        "termination_state": "running",
    }
    if extra:
        record.update(extra)
    records[run_id] = record
    _write_adapter_pids(path, records)
    return record


def update_adapter_pid(path: Path, run_id: str, **fields: Any) -> None:
    records = load_adapter_pids(path)
    if run_id not in records:
        return
    records[run_id].update(fields)
    _write_adapter_pids(path, records)


def unregister_adapter_pid(path: Path, run_id: str) -> None:
    records = load_adapter_pids(path)
    if run_id not in records:
        return
    records.pop(run_id, None)
    if records:
        _write_adapter_pids(path, records)
    elif path.is_file():
        try:
            _retry_io(lambda: path.unlink(missing_ok=True))
        except OSError:
            _write_adapter_pids(path, {})


class WorkerController:
    """Spawn, track, heartbeat, and terminate bounded worker subprocesses."""

    def __init__(self, database: FoundryDatabase, *, lease_seconds: int = 900) -> None:
        self.database = database
        self.leases = LeaseManager(database)
        self.lease_seconds = lease_seconds
        self._processes: dict[str, subprocess.Popen] = {}
        self._leased: set[str] = set()

    def pid_table_path(self) -> Path:
        return adapter_pid_table_path(self.database)

    def spawn(
        self,
        run_id: str,
        command: list[str],
        *,
        cwd: Path | None = None,
        worker_type: str | None = None,
        mission_id: str | None = None,
        step_id: str | None = None,
        stop_path: str | Path | None = None,
    ) -> WorkerStatus:
        """Start one worker process and lease it for ``lease_seconds``.

        Refuses to spawn while a live lease exists for the same run (any
        owner), so a second controller — or the same controller after a
        restart — can never silently double-spawn a worker. If the process
        cannot be started, the lease is released again and the error
        propagates. The PID is registered in the durable table BEFORE wait.
        """
        key = worker_lease_key(run_id)
        existing = self.database.row("leases", "lease_key", key)
        if existing is not None and parse(existing["expires_at"]) > utc_now():
            raise LeaseUnavailable(f"worker {run_id} already has a live lease")
        self.leases.acquire(key, key, self.lease_seconds)
        try:
            popen_kwargs = headless_popen_kwargs(
                cwd=str(cwd) if cwd is not None else None,
            )
            process = subprocess.Popen(command, **popen_kwargs)  # type: ignore[arg-type]
        except BaseException:
            self.leases.release(key, key)
            raise
        self._processes[run_id] = process
        self._leased.add(run_id)
        try:
            register_adapter_pid(
                self.pid_table_path(), run_id, process.pid,
                mission_id=mission_id, step_id=step_id, worker_type=worker_type,
                working_directory=cwd, stop_path=stop_path,
            )
        except BaseException:
            # The process has started, but durable PID registration failed.
            # Do not leak a worker that a restarted controller cannot safely
            # identify. The exact process object is still available here.
            self._processes.pop(run_id, None)
            self._leased.discard(run_id)
            try:
                terminate_owned_tree(process.pid, process=process)
            finally:
                self.leases.release(key, key)
            raise
        return self.status(run_id)

    def attach(
        self,
        run_id: str,
        process: subprocess.Popen,
        *,
        acquire_lease: bool = False,
        mission_id: str | None = None,
        step_id: str | None = None,
        worker_type: str | None = None,
        working_directory: str | Path | None = None,
        stop_path: str | Path | None = None,
    ) -> WorkerStatus:
        """Register an already-started child so STOP can kill its tree.

        Adapter heartbeat runners use ``acquire_lease=False`` so they do not
        collide with the role-run lease. Supervisor-spawned workers keep
        using ``spawn``.
        """
        if acquire_lease:
            key = worker_lease_key(run_id)
            existing = self.database.row("leases", "lease_key", key)
            if existing is not None and parse(existing["expires_at"]) > utc_now():
                raise LeaseUnavailable(f"worker {run_id} already has a live lease")
            self.leases.acquire(key, key, self.lease_seconds)
            self._leased.add(run_id)
        self._processes[run_id] = process
        register_adapter_pid(
            self.pid_table_path(), run_id, process.pid,
            mission_id=mission_id, step_id=step_id, worker_type=worker_type,
            working_directory=working_directory, stop_path=stop_path,
        )
        return self.status(run_id)

    def status(self, run_id: str) -> WorkerStatus:
        row = self.database.row("leases", "lease_key", worker_lease_key(run_id))
        process = self._processes.get(run_id)
        alive = process is not None and process.poll() is None
        record = load_adapter_pids(self.pid_table_path()).get(run_id)
        if not alive:
            if record is not None and not _record_is_live(record):
                record = None
            if record is not None:
                alive = pid_alive(int(record["pid"]))
        expired = row is not None and parse(row["expires_at"]) <= utc_now()
        return WorkerStatus(
            run_id=run_id,
            lease_key=worker_lease_key(run_id),
            owner_id=row["owner_id"] if row else None,
            expires_at=row["expires_at"] if row else None,
            alive=alive,
            lease_expired=expired,
            process=process,
        )

    def heartbeat(self, run_id: str) -> WorkerStatus:
        """Renew the lease of a live worker (no-op safe for unknown runs)."""
        key = worker_lease_key(run_id)
        if self.database.row("leases", "lease_key", key) is not None:
            self.leases.acquire(key, key, self.lease_seconds)
        return self.status(run_id)

    def terminate(self, run_id: str) -> bool:
        """Terminate the owned process tree and release its lease."""
        process = self._processes.pop(run_id, None)
        record = load_adapter_pids(self.pid_table_path()).get(run_id)
        pid = None
        if process is not None:
            pid = process.pid
        elif record is not None:
            pid = int(record["pid"])
        if process is None and record is not None and not _record_is_live(record):
            unregister_adapter_pid(self.pid_table_path(), run_id)
            self.leases.release(worker_lease_key(run_id), worker_lease_key(run_id))
            self._leased.discard(run_id)
            return True
        if pid is not None:
            update_adapter_pid(self.pid_table_path(), run_id, termination_state="graceful")
            owned = terminate_owned_tree(pid, process=process)
            leftover = [item for item in owned if pid_alive(item)]
            update_adapter_pid(
                self.pid_table_path(), run_id,
                termination_state="forced" if leftover else "exited",
                owned_descendants=owned,
            )
        unregister_adapter_pid(self.pid_table_path(), run_id)
        self.leases.release(worker_lease_key(run_id), worker_lease_key(run_id))
        self._leased.discard(run_id)
        return True

    def detach(self, run_id: str) -> None:
        """Drop tracking for a child that already exited (do not kill)."""
        self._processes.pop(run_id, None)
        update_adapter_pid(self.pid_table_path(), run_id, termination_state="reconciled")
        unregister_adapter_pid(self.pid_table_path(), run_id)

    def reap(self) -> list[str]:
        """Enforce the lease contract: terminate expired-but-alive workers and
        release leases of workers whose processes already exited."""
        reaped: list[str] = []
        known = list(dict.fromkeys([*self._processes, *load_adapter_pids(self.pid_table_path())]))
        for run_id in known:
            status = self.status(run_id)
            if status.alive and status.lease_expired:
                self.terminate(run_id)
                reaped.append(run_id)
            elif not status.alive:
                self._processes.pop(run_id, None)
                unregister_adapter_pid(self.pid_table_path(), run_id)
                self.leases.release(worker_lease_key(run_id), worker_lease_key(run_id))
                self._leased.discard(run_id)
        return reaped

    def terminate_all(self) -> list[str]:
        """Terminate every tracked worker and every PID in the durable table."""
        known = list(dict.fromkeys([*self._processes, *load_adapter_pids(self.pid_table_path())]))
        terminated = [run_id for run_id in known if self.terminate(run_id)]
        leftover = load_adapter_pids(self.pid_table_path())
        for run_id, record in leftover.items():
            if _record_is_live(record):
                terminate_owned_tree(int(record["pid"]))
            else:
                # A stale or incomplete identity is not a kill authority.
                # Remove only the ledger row and release its matching lease.
                unregister_adapter_pid(self.pid_table_path(), run_id)
                owner = worker_lease_key(run_id)
                connection = self.database.connect(readonly=True)
                try:
                    row = connection.execute(
                        "SELECT owner_id FROM leases WHERE lease_key = ?",
                        (owner,),
                    ).fetchone()
                finally:
                    connection.close()
                if row is not None:
                    self.leases.release(owner, str(row["owner_id"]))
            terminated.append(run_id)
        table = self.pid_table_path()
        if table.is_file():
            try:
                _retry_io(lambda: table.unlink(missing_ok=True))
            except OSError:
                try:
                    _write_adapter_pids(table, {})
                except OSError:
                    pass
        return terminated

    def active_runs(self, *, readonly: bool = False) -> list[str]:
        with self.database.connect(readonly=readonly) as connection:
            rows = connection.execute(
                "SELECT lease_key FROM leases WHERE lease_key LIKE 'worker:%'"
            ).fetchall()
        leased = [row["lease_key"].removeprefix("worker:") for row in rows]
        persisted = list(load_adapter_pids(self.pid_table_path()))
        return list(dict.fromkeys([*leased, *persisted, *self._processes]))
