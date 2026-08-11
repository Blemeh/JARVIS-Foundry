"""WP1 interrupted-write recovery tests.

These tests spawn a real subprocess (``crash_fixture.py``) that opens the
Foundry database, executes transaction statements, and is hard-killed
(TerminateProcess) while the transaction is still open. They prove the
all-or-nothing contract:

- an event and its projection either commit together or not at all;
- a crash mid-migration leaves the previous schema intact and the next
  initialization migrates cleanly;
- committed state survives a later crash of an unrelated transaction.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.migrations import MIGRATIONS

FIXTURE = Path(__file__).resolve().parent / "crash_fixture.py"

MARKER_TIMEOUT_SECONDS = 30


def _wait_for_marker(marker: Path, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + MARKER_TIMEOUT_SECONDS
    while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if not marker.exists():
        process.kill()
        process.wait(timeout=10)
        raise AssertionError("crash fixture never reached the mid-transaction state")


def _run_and_kill(database_path: Path, mode: str, marker: Path) -> int:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE), str(database_path), mode, str(marker)],
        cwd=str(FIXTURE.parent),
    )
    _wait_for_marker(marker, process)
    process.kill()  # TerminateProcess — no clean shutdown, no transaction commit
    process.wait(timeout=15)
    return process.returncode


def _committed_mission(database: FoundryDatabase) -> None:
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": "committed-1",
            "objective": "already durable",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["survives later crash"],
        },
        source_type="test",
        source_id="test-suite",
        idempotency_key="committed:mission-1",
    ))


def test_crash_between_event_and_projection_statements_leaves_no_partial_state(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    marker = tmp_path / "mid-transaction.marker"

    returncode = _run_and_kill(database.path, "event-between-statements", marker)
    assert returncode != 0

    reopened = FoundryDatabase(database.path)
    assert reopened.quick_check() == "ok"
    assert reopened.events() == []
    assert reopened.row("missions", "mission_id", "crash-mission-1") is None
    assert reopened.verify_event_integrity() == 0


def test_crash_after_committed_state_preserves_committed_event_and_projection(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    _committed_mission(database)
    marker = tmp_path / "mid-transaction-2.marker"

    returncode = _run_and_kill(database.path, "event-between-statements", marker)
    assert returncode != 0

    reopened = FoundryDatabase(database.path)
    assert reopened.quick_check() == "ok"
    events = reopened.events()
    assert len(events) == 1
    assert events[0].payload["mission_id"] == "committed-1"
    mission = reopened.row("missions", "mission_id", "committed-1")
    assert mission is not None
    assert mission["state"] == "PLANNED"
    assert reopened.row("missions", "mission_id", "crash-mission-1") is None


def test_crash_mid_migration_rolls_back_partial_schema_and_heals(tmp_path):
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
    marker = tmp_path / "mid-migration.marker"

    returncode = _run_and_kill(path, "migration-mid-script", marker)
    assert returncode != 0

    with sqlite3.connect(path) as check:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "agent_runs" not in tables, "partial migration DDL must roll back"
        assert check.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 2
        assert check.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    database = FoundryDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    with database.connect() as healed:
        tables = {row[0] for row in healed.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"agent_runs", "evidence_bundles", "verification_verdicts", "budgets"} <= tables
    assert database.quick_check() == "ok"
