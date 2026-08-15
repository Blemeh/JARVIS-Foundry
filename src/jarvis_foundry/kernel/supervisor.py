"""Durable supervisor lifecycle, leases, recovery, and backpressure."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import BackpressureError, DuplicateLaunchError, SupervisorError
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ids import DeterministicIdGenerator, IdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.timeutil import add_seconds_to_iso, iso_compare

SUPERVISOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS supervisor_state (
  instance_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  last_heartbeat_at TEXT NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS supervisor_leases (
  worker_id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL,
  status TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_heartbeat_at TEXT NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS supervisor_waiting (
  worker_id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL,
  queued_at TEXT NOT NULL,
  reason TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class SupervisorState:
    instance_id: str
    status: str
    started_at: str
    last_heartbeat_at: str
    reason: str | None = None


@dataclass(frozen=True)
class SupervisorLease:
    worker_id: str
    instance_id: str
    status: str
    acquired_at: str
    expires_at: str
    last_heartbeat_at: str
    reason: str | None = None


@dataclass(frozen=True)
class RecoveryReport:
    reclaimed_leases: tuple[str, ...]
    recovered_workers: tuple[str, ...]
    at: str


@dataclass(frozen=True)
class AdvanceReport:
    promoted: tuple[str, ...]
    at: str


class SupervisorService:
    def __init__(
        self,
        *,
        store_path: str | Path,
        ledger: Ledger,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        max_running: int = 4,
        lease_ttl_seconds: int = 30,
    ) -> None:
        self._store_path = Path(store_path)
        self.ledger = ledger
        self._clock = clock if clock is not None else SystemClock()
        self._ids = ids if ids is not None else DeterministicIdGenerator(seed=1)
        self._max_running = max_running
        self._lease_ttl_seconds = lease_ttl_seconds
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._store_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SUPERVISOR_SCHEMA)

    def start(self, instance_id: str) -> SupervisorState:
        now = self._clock.now_utc()
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO supervisor_state ("
                "instance_id, status, started_at, last_heartbeat_at, reason"
                ") VALUES (?, 'RUNNING', ?, ?, NULL)",
                (instance_id, now, now),
            )
            connection.execute(
                "UPDATE supervisor_state SET last_heartbeat_at = ? WHERE instance_id = ?",
                (now, instance_id),
            )
        self.ledger.append(
            LedgerEvent.create(
                event_type="supervisor.started",
                payload={"instance_id": instance_id, "trust_state": "LAB"},
                source_type="supervisor",
                source_id=instance_id,
                idempotency_key=f"supervisor.started:{instance_id}",
                event_id=f"supervisor-started-{instance_id}",
            )
        )
        return self.status(instance_id)

    def heartbeat(self, instance_id: str) -> SupervisorState:
        now = self._clock.now_utc()
        with self.connect() as connection:
            updated = connection.execute(
                "UPDATE supervisor_state SET last_heartbeat_at = ? WHERE instance_id = ?",
                (now, instance_id),
            ).rowcount
        if updated == 0:
            raise SupervisorError(f"unknown supervisor instance: {instance_id}")
        self.ledger.append(
            LedgerEvent.create(
                event_type="supervisor.heartbeat",
                payload={"instance_id": instance_id, "heartbeat_at": now},
                source_type="supervisor",
                source_id=instance_id,
                idempotency_key=f"supervisor.heartbeat:{instance_id}:{now}",
                event_id=f"supervisor-heartbeat-{instance_id}-{now}",
            )
        )
        return self.status(instance_id)

    def status(self, instance_id: str) -> SupervisorState:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM supervisor_state WHERE instance_id = ?", (instance_id,)
            ).fetchone()
        if row is None:
            raise SupervisorError(f"unknown supervisor instance: {instance_id}")
        return SupervisorState(
            instance_id=str(row["instance_id"]),
            status=str(row["status"]),
            started_at=str(row["started_at"]),
            last_heartbeat_at=str(row["last_heartbeat_at"]),
            reason=row["reason"],
        )

    def acquire_lease(
        self, worker_id: str, instance_id: str, *, allow_wait: bool = False
    ) -> SupervisorLease:
        now = self._clock.now_utc()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM supervisor_leases WHERE worker_id = ?", (worker_id,)
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["status"]) == "ACTIVE"
                    and iso_compare(str(existing["expires_at"]), now) > 0
                ):
                    raise DuplicateLaunchError(f"worker {worker_id} already has an active lease")
                connection.execute(
                    "DELETE FROM supervisor_leases WHERE worker_id = ?", (worker_id,)
                )
            running = connection.execute(
                "SELECT COUNT(*) AS count FROM supervisor_leases WHERE status = 'ACTIVE'"
            ).fetchone()
            running_count = int(running["count"]) if running is not None else 0
            if running_count >= self._max_running:
                if allow_wait:
                    connection.execute(
                        "INSERT OR REPLACE INTO supervisor_waiting ("
                        "worker_id, instance_id, queued_at, reason"
                        ") VALUES (?, ?, ?, 'BACKPRESSURE')",
                        (worker_id, instance_id, now),
                    )
                    return SupervisorLease(
                        worker_id=worker_id,
                        instance_id=instance_id,
                        status="WAITING",
                        acquired_at=now,
                        expires_at=add_seconds_to_iso(now, self._lease_ttl_seconds),
                        last_heartbeat_at=now,
                        reason="BACKPRESSURE",
                    )
                raise BackpressureError(
                    f"supervisor at capacity ({running_count}/{self._max_running})"
                )
            expires_at = add_seconds_to_iso(now, self._lease_ttl_seconds)
            connection.execute(
                "INSERT OR REPLACE INTO supervisor_leases ("
                "worker_id, instance_id, status, acquired_at, expires_at, "
                "last_heartbeat_at, reason"
                ") VALUES (?, ?, 'ACTIVE', ?, ?, ?, NULL)",
                (worker_id, instance_id, now, expires_at, now),
            )
        self._append_lease_event(worker_id, instance_id, "ACQUIRED")
        return self.lease(worker_id)

    def release_lease(self, worker_id: str, instance_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM supervisor_leases WHERE worker_id = ? AND instance_id = ?",
                (worker_id, instance_id),
            )
            connection.execute("DELETE FROM supervisor_waiting WHERE worker_id = ?", (worker_id,))
        self._append_lease_event(worker_id, instance_id, "RELEASED")

    def lease(self, worker_id: str) -> SupervisorLease:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM supervisor_leases WHERE worker_id = ?", (worker_id,)
            ).fetchone()
        if row is None:
            raise SupervisorError(f"no lease for worker: {worker_id}")
        return SupervisorLease(
            worker_id=str(row["worker_id"]),
            instance_id=str(row["instance_id"]),
            status=str(row["status"]),
            acquired_at=str(row["acquired_at"]),
            expires_at=str(row["expires_at"]),
            last_heartbeat_at=str(row["last_heartbeat_at"]),
            reason=row["reason"],
        )

    def advance_waiting(self, *, at: str | None = None) -> AdvanceReport:
        timestamp = at if at is not None else self._clock.now_utc()
        promoted: list[str] = []
        with self.connect() as connection:
            waiting_rows = connection.execute(
                "SELECT * FROM supervisor_waiting ORDER BY queued_at"
            ).fetchall()
            for row in waiting_rows:
                running = connection.execute(
                    "SELECT COUNT(*) AS count FROM supervisor_leases WHERE status = 'ACTIVE'"
                ).fetchone()
                running_count = int(running["count"]) if running is not None else 0
                if running_count >= self._max_running:
                    break
                worker_id = str(row["worker_id"])
                expires_at = add_seconds_to_iso(timestamp, self._lease_ttl_seconds)
                connection.execute(
                    "INSERT OR REPLACE INTO supervisor_leases ("
                    "worker_id, instance_id, status, acquired_at, expires_at, "
                    "last_heartbeat_at, reason"
                    ") VALUES (?, ?, 'ACTIVE', ?, ?, ?, NULL)",
                    (worker_id, str(row["instance_id"]), timestamp, expires_at, timestamp),
                )
                connection.execute(
                    "DELETE FROM supervisor_waiting WHERE worker_id = ?", (worker_id,)
                )
                promoted.append(worker_id)
        for worker_id in promoted:
            self._append_lease_event(worker_id, "advance", "ACQUIRED")
        return AdvanceReport(promoted=tuple(promoted), at=timestamp)

    def recover(self, *, at: str) -> RecoveryReport:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM supervisor_leases WHERE status = 'ACTIVE'"
            ).fetchall()
            reclaimed: list[str] = []
            for row in rows:
                if iso_compare(str(row["expires_at"]), at) <= 0:
                    worker_id = str(row["worker_id"])
                    connection.execute(
                        "DELETE FROM supervisor_leases WHERE worker_id = ?", (worker_id,)
                    )
                    connection.execute(
                        "DELETE FROM supervisor_waiting WHERE worker_id = ?", (worker_id,)
                    )
                    reclaimed.append(worker_id)
        for worker_id in reclaimed:
            self._append_lease_event(worker_id, "recovery", "RECOVERED")
        return RecoveryReport(
            reclaimed_leases=tuple(reclaimed),
            recovered_workers=tuple(reclaimed),
            at=at,
        )

    def running_count(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM supervisor_leases WHERE status = 'ACTIVE'"
                ).fetchone()["count"]
            )

    def _append_lease_event(self, worker_id: str, instance_id: str, status: str) -> None:
        self.ledger.append(
            LedgerEvent.create(
                event_type="supervisor.lease",
                payload={
                    "worker_id": worker_id,
                    "instance_id": instance_id,
                    "status": status,
                },
                source_type="supervisor",
                source_id=instance_id,
                idempotency_key=f"supervisor.lease:{worker_id}:{status}:{self._clock.now_utc()}",
                event_id=f"supervisor-lease-{worker_id}-{status}-{self._clock.now_utc()}",
            )
        )
