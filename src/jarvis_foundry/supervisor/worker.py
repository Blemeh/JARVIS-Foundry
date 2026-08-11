"""Bounded worker process control for the independent Supervisor.

The Supervisor spawns workers as ordinary subprocesses, tracks each one with
a durable lease, and can terminate them deterministically on STOP or lease
expiry. Workers never outlive their lease: ``reap`` terminates any still-alive
worker whose lease expired and releases the lease of any worker whose process
already exited.

The controller is Hermes-independent by construction (no Hermes imports).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..clock import parse, utc_now
from ..database import FoundryDatabase
from .leases import LeaseManager, LeaseUnavailable


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


class WorkerController:
    """Spawn, track, heartbeat, and terminate bounded worker subprocesses."""

    def __init__(self, database: FoundryDatabase, *, lease_seconds: int = 900) -> None:
        self.database = database
        self.leases = LeaseManager(database)
        self.lease_seconds = lease_seconds
        self._processes: dict[str, subprocess.Popen] = {}

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
        return self.status(run_id)

    def status(self, run_id: str) -> WorkerStatus:
        row = self.database.row("leases", "lease_key", worker_lease_key(run_id))
        process = self._processes.get(run_id)
        alive = process is not None and process.poll() is None
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
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        self.leases.release(worker_lease_key(run_id), worker_lease_key(run_id))
        return True

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
                self.leases.release(worker_lease_key(run_id), worker_lease_key(run_id))
        return reaped

    def terminate_all(self) -> list[str]:
        """Terminate every tracked worker and release all worker leases."""
        terminated = [run_id for run_id in list(self._processes) if self.terminate(run_id)]
        return terminated

    def active_runs(self) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT lease_key FROM leases WHERE lease_key LIKE 'worker:%'"
            ).fetchall()
        return [row["lease_key"].removeprefix("worker:") for row in rows]
