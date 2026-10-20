"""A live worker-loop generation must not read as stale health.

A loop cycle legitimately outlasts its 120s singleton TTL while a
multi-minute worker run proceeds.  The snapshot must degrade on a dead
generation's lease but not on a live loop's expired TTL, or every long
mission degrades the product while succeeding.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.worker import process_creation_identity


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "lease-health.db")
    database.initialize()
    return database


def _lease_row(db: FoundryDatabase, owner: str, heartbeat: str, expires: str) -> None:
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO leases (lease_key, owner_id, expires_at, heartbeat_at)"
            " VALUES (?, ?, ?, ?)",
            ("worker-loop:singleton", owner, expires, heartbeat),
        )


def _cycle_event(db: FoundryDatabase, instance: str, pid: int, identity: dict) -> None:
    db.append(Event.create(
        "worker_loop.cycle_started",
        {"instance_id": instance, "pid": pid, "creation_identity": dict(identity)},
        source_type="supervisor", source_id=instance,
        idempotency_key=f"worker_loop.cycle_started:test-{instance}",
    ))


def _degraded(db: FoundryDatabase, tmp_path: Path) -> bool:
    snapshot = build_snapshot(db, stop_path=tmp_path / "STOP", timeline_limit=5)
    assert snapshot["health"]["supervisor_status"] != "DEGRADED"
    return bool(snapshot["health"]["degraded"])


def test_live_loop_with_expired_ttl_is_not_stale(db: FoundryDatabase, tmp_path: Path) -> None:
    instance = "worker-loop-test-live"
    _lease_row(db, instance, "2026-10-06T10:00:00Z", "2026-10-06T10:02:00Z")
    _cycle_event(db, instance, os.getpid(), process_creation_identity(os.getpid()))
    assert _degraded(db, tmp_path) is False


def test_dead_generation_lease_stays_stale(db: FoundryDatabase, tmp_path: Path) -> None:
    instance = "worker-loop-test-dead"
    _lease_row(db, instance, "2026-10-06T10:00:00Z", "2026-10-06T10:02:00Z")
    _cycle_event(db, instance, -1, {})
    assert _degraded(db, tmp_path) is True
