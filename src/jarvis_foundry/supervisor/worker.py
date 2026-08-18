"""Bounded worker process control for the independent Supervisor.

The Supervisor spawns workers as ordinary subprocesses, tracks each one with
a durable lease, and can terminate them deterministically on STOP or lease
expiry. Workers never outlive their lease: ``reap`` terminates any still-alive
worker whose lease expired and releases the lease of any worker whose process
already exited.

Adapter children (Hermes / OpenHands) that are not spawned by this
controller still register their PIDs here (in-memory and in a JSON table
next to the ledger) so STOP / ``terminate_all`` can kill the tree.

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

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase
from .leases import LeaseManager, LeaseUnavailable

ADAPTER_PID_TABLE_NAME = "adapter_worker_pids.json"


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


def load_adapter_pids(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
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


def _write_adapter_pids(path: Path, records: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")

    def _replace() -> None:
        temporary.write_text(json.dumps(records, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    try:
        _retry_io(_replace)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def register_adapter_pid(path: Path, run_id: str, pid: int) -> None:
    records = load_adapter_pids(path)
    records[run_id] = {"pid": int(pid), "recorded_at": isoformat()}
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


def kill_pid(pid: int) -> None:
    """Terminate a process tree. Best-effort; missing PIDs are ignored."""
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def terminate_popen(process: subprocess.Popen, *, timeout: float = 10) -> None:
    if process.poll() is not None:
        return
    pid = process.pid
    process.terminate()
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    kill_pid(pid)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


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

    def spawn(self, run_id: str, command: list[str], *, cwd: Path | None = None) -> WorkerStatus:
        """Start one worker process and lease it for ``lease_seconds``.

        Refuses to spawn while a live lease exists for the same run (any
        owner), so a second controller — or the same controller after a
        restart — can never silently double-spawn a worker. If the process
        cannot be started, the lease is released again and the error
        propagates.
        """
        key = worker_lease_key(run_id)
        existing = self.database.row("leases", "lease_key", key)
        if existing is not None and parse(existing["expires_at"]) > utc_now():
            raise LeaseUnavailable(f"worker {run_id} already has a live lease")
        self.leases.acquire(key, key, self.lease_seconds)
        try:
            process = subprocess.Popen(command, cwd=str(cwd) if cwd is not None else None)
        except BaseException:
            self.leases.release(key, key)
            raise
        self._processes[run_id] = process
        self._leased.add(run_id)
        register_adapter_pid(self.pid_table_path(), run_id, process.pid)
        return self.status(run_id)

    def attach(
        self,
        run_id: str,
        process: subprocess.Popen,
        *,
        acquire_lease: bool = False,
    ) -> WorkerStatus:
        """Register an already-started child so STOP can kill it.

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
        register_adapter_pid(self.pid_table_path(), run_id, process.pid)
        return self.status(run_id)

    def status(self, run_id: str) -> WorkerStatus:
        row = self.database.row("leases", "lease_key", worker_lease_key(run_id))
        process = self._processes.get(run_id)
        alive = process is not None and process.poll() is None
        if not alive:
            record = load_adapter_pids(self.pid_table_path()).get(run_id)
            if record is not None:
                pid = int(record["pid"])
                alive = _pid_alive(pid)
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
        """Terminate the worker process (escalating to kill) and release its lease."""
        process = self._processes.pop(run_id, None)
        record = load_adapter_pids(self.pid_table_path()).get(run_id)
        if process is not None and process.poll() is None:
            terminate_popen(process)
        elif record is not None:
            kill_pid(int(record["pid"]))
        unregister_adapter_pid(self.pid_table_path(), run_id)
        if run_id in self._leased:
            self.leases.release(worker_lease_key(run_id), worker_lease_key(run_id))
            self._leased.discard(run_id)
        else:
            self.leases.release(worker_lease_key(run_id), worker_lease_key(run_id))
        return True

    def detach(self, run_id: str) -> None:
        """Drop tracking for a child that already exited (do not kill)."""
        self._processes.pop(run_id, None)
        unregister_adapter_pid(self.pid_table_path(), run_id)

    def reap(self) -> list[str]:
        """Enforce the lease contract: terminate expired-but-alive workers and
        release leases of workers whose processes already exited."""
        reaped: list[str] = []
        for run_id in list(self._processes):
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
            kill_pid(int(record["pid"]))
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


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
