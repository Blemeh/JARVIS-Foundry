"""V4-S3 (R2a): heartbeat lease renewal — a live worker must never be reaped.

DIR-2026-08-12 item 2: long-lived real worker/verifier processes must
heartbeat AND renew their leases while running; a live process must not
be reaped; terminal states must be monotonic and race-safe.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.clock import parse, utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.dispatcher import RoleDispatcher
from jarvis_foundry.roles.reaper import reap_stale_role_runs
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.supervisor.leases import LeaseManager, LeaseUnavailable

DURATION = 300


def make_db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def started_run(database: FoundryDatabase, run_id: str,
                step_id: str = "step-1") -> None:
    database.append(Event.create(
        "agent_run.started",
        {"run_id": run_id, "mission_id": "mission-1", "step_id": step_id,
         "role": "builder", "provider": "opencode_go",
         "model": "deepseek-v4-flash", "context_manifest_hash": "h",
         "tool_policy": ["code"]},
        source_type="test", source_id="test-suite",
        idempotency_key=f"agent_run.started:{run_id}:opencode_go"))


def test_renew_extends_expiry_for_owner(tmp_path):
    database = make_db(tmp_path)
    leases = LeaseManager(database)
    leases.acquire("role:builder:m1:start", "run-1", DURATION)
    with database.connect() as connection:
        original = connection.execute(
            "SELECT expires_at FROM leases WHERE lease_key = "
            "'role:builder:m1:start'").fetchone()[0]
    leases.renew("role:builder:m1:start", "run-1", DURATION)
    with database.connect() as connection:
        renewed = connection.execute(
            "SELECT expires_at FROM leases WHERE lease_key = "
            "'role:builder:m1:start'").fetchone()[0]
    assert parse(renewed) > parse(original)
    assert parse(renewed) > utc_now()


def test_renew_by_non_owner_raises(tmp_path):
    database = make_db(tmp_path)
    leases = LeaseManager(database)
    leases.acquire("role:builder:m1:start", "run-1", DURATION)
    with pytest.raises(LeaseUnavailable):
        leases.renew("role:builder:m1:start", "other-owner", DURATION)


def test_renew_missing_lease_raises(tmp_path):
    database = make_db(tmp_path)
    with pytest.raises(LeaseUnavailable):
        LeaseManager(database).renew("role:builder:m1:start", "run-1", DURATION)


def test_dispatcher_heartbeat_renews_the_session_lease(tmp_path):
    """While a session runs, each heartbeat extends the lease — the lease
    cannot expire under a live, heartbeating process."""
    database = make_db(tmp_path)

    class Probe(FakeHermesAdapter):
        def __init__(self, database):
            super().__init__()
            self.database = database
            self.lease_expiry_seen = None

        def dispatch(self, manifest, *, heartbeat):
            heartbeat()  # the dispatcher's callback: event + renewal
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT expires_at FROM leases "
                    "WHERE lease_key LIKE 'role:%'").fetchone()
            self.lease_expiry_seen = row[0] if row else None
            return super().dispatch(manifest, heartbeat=heartbeat)

    probe = Probe(database)
    dispatcher = RoleDispatcher(database, RoleRouter(), probe)
    dispatcher.dispatch(role="builder", mission_id="mission-1",
                        resource_id="resource-1", capability="write",
                        step_id="step-1", run_id="run-hb1",
                        duration_seconds=DURATION)
    assert probe.lease_expiry_seen is not None
    # the mid-session expiry must be beyond acquire-time + DURATION
    assert parse(probe.lease_expiry_seen) > utc_now()


def test_reaper_never_reaps_a_heartbeating_session(tmp_path):
    """A RUNNING run whose lease is renewed (live heartbeats) must NOT be
    reaped; the same run with an expired lease IS reaped."""
    database = make_db(tmp_path)
    started_run(database, "run-live")
    started_run(database, "run-dead", step_id="step-2")
    leases = LeaseManager(database)
    leases.acquire("role:builder:mission-1:step-1", "run-live", DURATION)
    leases.renew("role:builder:mission-1:step-1", "run-live", DURATION)
    # run-dead: lease acquired and then aged past expiry
    leases.acquire("role:builder:mission-1:step-2", "run-dead", DURATION)
    with database.connect() as connection:
        connection.execute(
            "UPDATE leases SET expires_at = '2000-01-01T00:00:00Z' "
            "WHERE owner_id = 'run-dead'")
    reaped = reap_stale_role_runs(database)
    assert "run-live" not in reaped   # live session untouched
    assert "run-dead" in reaped       # expired session reaped
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT run_id, status FROM agent_runs ORDER BY run_id").fetchall()
    assert dict(rows)["run-live"] == "RUNNING"
    assert dict(rows)["run-dead"] == "FAILED"
