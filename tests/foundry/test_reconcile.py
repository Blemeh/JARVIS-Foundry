"""WP1 event/projection reconciliation diagnostics tests.

Regression (V2-operational gate 2026-08-12): reconcile must read the
events AND the compared rows from ONE WAL snapshot — a concurrent
writer appending supervisor heartbeats mid-reconcile must not fabricate
supervisor_state mismatches (the old multi-connection reconcile raced
5/5 runs against the live daemon).
"""
from __future__ import annotations

import threading
import time

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.reconcile import reconcile


def mission_created(mission_id: str = "mission-1") -> Event:
    return Event.create(
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": "Prove durable autonomy",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["state survives restart"],
        },
        source_type="test",
        source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    )


def seeded_database(tmp_path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    created = mission_created()
    database.append(created)
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-1", "state": "ORIENTING", "next_action": "inspect"},
        source_type="test", source_id="test-suite", causation_id=created.event_id,
        idempotency_key="mission.state_changed:mission-1",
    ))
    database.append(Event.create(
        "agent_run.started",
        {
            "run_id": "run-1", "mission_id": "mission-1", "step_id": "step-1",
            "role": "builder", "provider": "opencode_go", "model": "kimi-k2.7-code",
            "context_manifest_hash": "c" * 64,
            "tool_policy": {"mutate": ["registered_worktree"]},
        },
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.started:run-1",
    ))
    database.append(Event.create(
        "verification.started",
        {
            "verdict_id": "verdict-1", "mission_id": "mission-1",
            "subject_type": "STEP", "subject_id": "step-1",
            "verifier_run_id": "run-2", "builder_run_id": "run-1",
            "independence": {"same_agent": False, "same_session": False, "same_provider": False, "limitations": []},
            "criteria": ["criterion A"],
        },
        source_type="test", source_id="test-suite",
        idempotency_key="verification.started:verdict-1",
    ))
    database.append(Event.create(
        "budget.allocation_recorded",
        {"provider": "vertex_ai", "lifetime_budget_usd": 50.0, "daily_soft_limit_usd": 5.0, "billing_verified": False},
        source_type="test", source_id="test-suite",
        idempotency_key="budget.allocation_recorded:vertex_ai",
    ))
    return database


def test_consistent_database_reconciles_clean(tmp_path):
    database = seeded_database(tmp_path)
    report = reconcile(database)

    assert report.consistent is True
    assert report.event_count == 5
    assert report.integrity_failures == []
    assert report.mismatches == []
    assert report.unexpected_rows == []
    assert report.missing_rows == []


def test_projection_drift_reports_exact_column_mismatch(tmp_path):
    database = seeded_database(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE missions SET state = 'PLANNED' WHERE mission_id = 'mission-1'"
        )

    report = reconcile(database)

    assert report.consistent is False
    assert report.event_count == 5
    assert report.integrity_failures == []
    assert report.unexpected_rows == []
    assert report.missing_rows == []
    assert len(report.mismatches) == 1
    mismatch = report.mismatches[0]
    assert mismatch["table"] == "missions"
    assert mismatch["key_column"] == "mission_id"
    assert mismatch["key"] == "mission-1"
    assert mismatch["column"] == "state"
    assert mismatch["expected"] == "ORIENTING"
    assert mismatch["actual"] == "PLANNED"


def test_deleted_projection_row_is_reported_missing(tmp_path):
    database = seeded_database(tmp_path)
    with database.transaction() as connection:
        connection.execute("DELETE FROM agent_runs WHERE run_id = 'run-1'")

    report = reconcile(database)

    assert report.consistent is False
    assert {"table": "agent_runs", "key_column": "run_id", "key": "run-1"} in report.missing_rows


def test_orphaned_projection_row_is_reported_unexpected(tmp_path):
    database = seeded_database(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO resources(
                resource_id, kind, root_path, resolved_root, baseline_json,
                capabilities_json, readiness_state, last_event_id, updated_at
            ) VALUES ('orphan-1', 'git_worktree', '/fake', '/fake', '{}', '[]', 'READY', 'none', '2026-08-11T00:00:00Z')"""
        )

    report = reconcile(database)

    assert report.consistent is False
    assert {"table": "resources", "key_column": "resource_id", "key": "orphan-1"} in report.unexpected_rows


def test_tampered_event_is_reported_as_integrity_failure(tmp_path):
    database = seeded_database(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_type = 'mission.created'",
            ('{"mission_id":"forged"}',),
        )

    report = reconcile(database)

    assert report.consistent is False
    assert len(report.integrity_failures) == 1
    failure = report.integrity_failures[0]
    assert failure["event_type"] == "mission.created"
    assert "payload hash mismatch" in failure["error"]


def test_reconcile_does_not_mutate_live_database(tmp_path):
    database = seeded_database(tmp_path)
    before_events = [event.event_id for event in database.events()]
    before_mission = database.row("missions", "mission_id", "mission-1")

    report = reconcile(database)

    assert report.consistent is True
    assert [event.event_id for event in database.events()] == before_events
    assert database.row("missions", "mission_id", "mission-1") == before_mission
    assert database.quick_check() == "ok"


def test_reconcile_on_old_schema_reports_version_mismatch_without_crashing(tmp_path):
    import sqlite3

    from jarvis_foundry.migrations import MIGRATIONS, SCHEMA_VERSION

    path = tmp_path / "foundry-v2.db"
    connection = sqlite3.connect(path)
    connection.executescript(MIGRATIONS[1])
    connection.executescript(MIGRATIONS[2])
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?), (2, ?)",
        ("2026-08-11T00:00:00Z", "2026-08-11T00:00:01Z"),
    )
    connection.commit()
    connection.close()

    database = FoundryDatabase(path)
    report = reconcile(database)

    assert report.consistent is False
    assert report.version_mismatch is not None
    assert report.version_mismatch["live_schema_version"] == 2
    assert report.version_mismatch["expected_schema_version"] == SCHEMA_VERSION
    assert report.integrity_failures == []


def test_reconcile_reports_null_float_mismatch_without_crashing(tmp_path):
    database = seeded_database(tmp_path)
    with database.transaction() as connection:
        connection.execute("UPDATE budgets SET lifetime_budget_usd = NULL WHERE provider = 'vertex_ai'")

    report = reconcile(database)

    assert report.consistent is False
    assert any(
        mismatch["table"] == "budgets"
        and mismatch["column"] == "lifetime_budget_usd"
        and mismatch["expected"] == 50.0
        and mismatch["actual"] is None
        for mismatch in report.mismatches
    )


def test_reconcile_consistent_under_concurrent_writer(tmp_path):
    """Regression (V2-operational gate): a concurrent writer appending
    supervisor heartbeats mid-reconcile must NOT fabricate
    supervisor_state mismatches — reconcile reads one WAL snapshot."""
    database = seeded_database(tmp_path)

    def heartbeat_writer() -> None:
        for i in range(20):
            database.append(Event.create(
                "supervisor.heartbeat",
                {"trust_state": "LAB"},
                source_type="supervisor", source_id="writer",
                idempotency_key=f"supervisor.heartbeat:conc-{i}"))
            time.sleep(0.01)

    thread = threading.Thread(target=heartbeat_writer, daemon=True)
    thread.start()
    for _ in range(8):
        report = reconcile(database)
        assert report.consistent, report.mismatches
    thread.join()
    assert reconcile(database).consistent
