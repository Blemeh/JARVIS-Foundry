"""WP4 reaper tests: heartbeat-timeout enforcement for role sessions.

Gate criterion: leases bound every session; heartbeat timeout terminates /
releases stale sessions deterministically. A crashed or lost dispatcher
leaves the run row RUNNING and the ``role:*`` lease expired-or-missing; the
reaper marks the run FAILED (heartbeat_timeout) and releases the lease.
"""
from __future__ import annotations

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.reaper import reap_stale_role_runs, role_lease_key


def mission_database(tmp_path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-11T15:33:05Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-1", "objective": "reaper fixture mission",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["stale sessions are terminated"]},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-1",
    ))
    return database


def start_run(database: FoundryDatabase, run_id: str, *, role: str = "builder",
              mission_id: str = "mission-1", step_id: str | None = None) -> None:
    """Durable RUNNING run row via the real event projection."""
    database.append(Event.create(
        "agent_run.started",
        {
            "run_id": run_id, "mission_id": mission_id, "step_id": step_id,
            "role": role, "provider": "opencode_go", "model": "test-model",
            "context_manifest_hash": "0" * 64, "tool_policy": [],
        },
        source_type="test", source_id="reaper-test",
        idempotency_key=f"agent_run.started:{run_id}:opencode_go",
    ))


def forge_lease(database: FoundryDatabase, key: str, expires_at: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at) VALUES (?, ?, ?, ?)",
            (key, "ghost-owner", expires_at, expires_at),
        )


def failed_event_count(database: FoundryDatabase) -> int:
    with database.connect() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_run.failed' "
            "AND payload_json LIKE '%heartbeat_timeout%'"
        ).fetchone()[0]


def test_reaper_marks_run_with_expired_lease_failed(tmp_path):
    database = mission_database(tmp_path)
    start_run(database, "run-stale", step_id="step-1")
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")

    reaped = reap_stale_role_runs(database)

    assert reaped == ["run-stale"]
    row = database.row("agent_runs", "run_id", "run-stale")
    assert row["status"] == "FAILED"
    assert row["failure_class"] == "heartbeat_timeout"
    assert failed_event_count(database) == 1
    # the expired lease was released
    assert database.row("leases", "lease_key",
                        role_lease_key("builder", "mission-1", "step-1")) is None


def test_reaper_marks_run_without_any_lease_failed(tmp_path):
    # crashed dispatcher: run row exists, lease never recorded / already gone
    database = mission_database(tmp_path)
    start_run(database, "run-orphan")

    reaped = reap_stale_role_runs(database)

    assert reaped == ["run-orphan"]
    assert database.row("agent_runs", "run_id", "run-orphan")["status"] == "FAILED"
    assert database.row("agent_runs", "run_id", "run-orphan")["failure_class"] == "heartbeat_timeout"


def test_reaper_is_idempotent(tmp_path):
    database = mission_database(tmp_path)
    start_run(database, "run-stale")
    forge_lease(database, role_lease_key("builder", "mission-1", None),
                "2000-01-01T00:00:00Z")

    first = reap_stale_role_runs(database)
    second = reap_stale_role_runs(database)

    assert first == ["run-stale"]
    assert second == []
    assert failed_event_count(database) == 1  # deduped on idempotency key


def test_reaper_skips_run_with_live_lease(tmp_path):
    database = mission_database(tmp_path)
    start_run(database, "run-live")
    forge_lease(database, role_lease_key("builder", "mission-1", None),
                "2999-01-01T00:00:00Z")

    reaped = reap_stale_role_runs(database)

    assert reaped == []
    assert database.row("agent_runs", "run_id", "run-live")["status"] == "RUNNING"
    assert database.row("leases", "lease_key",
                        role_lease_key("builder", "mission-1", None)) is not None


def test_reaper_releases_only_expired_role_leases(tmp_path):
    database = mission_database(tmp_path)
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")
    forge_lease(database, role_lease_key("verifier", "mission-1", "step-1"),
                "2999-01-01T00:00:00Z")

    reap_stale_role_runs(database)

    assert database.row("leases", "lease_key",
                        role_lease_key("builder", "mission-1", "step-1")) is None
    assert database.row("leases", "lease_key",
                        role_lease_key("verifier", "mission-1", "step-1")) is not None


def test_reaper_ignores_completed_and_non_role_leases(tmp_path):
    database = mission_database(tmp_path)
    # completed run: untouched even with an expired-looking absent lease
    start_run(database, "run-done")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE agent_runs SET status = 'COMPLETED' WHERE run_id = 'run-done'"
        )
    # non-role lease (worker lease): never deleted by the role reaper
    forge_lease(database, "worker:run-x", "2000-01-01T00:00:00Z")

    reaped = reap_stale_role_runs(database)

    assert reaped == []
    assert database.row("agent_runs", "run_id", "run-done")["status"] == "COMPLETED"
    assert database.row("leases", "lease_key", "worker:run-x") is not None
