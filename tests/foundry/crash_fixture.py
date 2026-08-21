"""Subprocess crash fixture for WP1 interrupted-write recovery tests.

This script is executed as a separate OS process by
``tests/foundry/test_crash_recovery.py``. It deliberately opens a Foundry
database, begins a transaction, executes statements, and then waits to be
hard-killed (TerminateProcess on Windows) while the transaction is still open
— simulating a process crash between transaction statements.

Modes (argv[1] = database path, argv[2] = mode, argv[3] = marker path):

- ``event-between-statements``: mirrors ``FoundryDatabase.append`` exactly —
  BEGIN IMMEDIATE, INSERT the events row, apply the mission projection, then
  hold. The parent kills the process before COMMIT.
- ``migration-mid-script``: opens a schema-2 database and executes the first
  DDL statement of migration version 3 inside an open transaction (the real
  statement from ``migrations.MIGRATIONS[3]`` via ``split_sql_statements``),
  then holds. The parent kills the process before the migration finishes.

In both modes the marker file is written only after the statements executed,
so the parent knows the child is exactly mid-transaction when it kills it.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_foundry.clock import isoformat  # noqa: E402
from jarvis_foundry.events import Event, canonical_json  # noqa: E402
from jarvis_foundry.migrations import MIGRATIONS, split_sql_statements  # noqa: E402
from jarvis_foundry.projections import apply_event  # noqa: E402


def _connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _hold(marker: str) -> None:
    """Signal readiness and hold until the parent kills this process."""
    Path(marker).write_text("mid-transaction", encoding="utf-8")
    time.sleep(120)


def _crash_event_between_statements(database_path: str, marker: str) -> None:
    connection = _connect(database_path)
    event = Event.create(
        "mission.created",
        {
            "mission_id": "crash-mission-1",
            "objective": "must never appear",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["never committed"],
        },
        source_type="crash-fixture",
        source_id="fixture",
        idempotency_key="crash-fixture:mission-1",
    )
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        """INSERT INTO events(
            event_id, event_type, occurred_at, recorded_at, source_type, source_id,
            correlation_id, causation_id, idempotency_key, schema_version,
            payload_json, payload_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.event_id, event.event_type, event.occurred_at, event.recorded_at,
            event.source_type, event.source_id, event.correlation_id, event.causation_id,
            event.idempotency_key, event.schema_version, canonical_json(event.payload), event.payload_hash,
        ),
    )
    apply_event(connection, event)  # INSERT INTO missions — same transaction
    _hold(marker)
    connection.commit()  # unreachable: process is killed first


def _crash_migration_mid_script(database_path: str, marker: str) -> None:
    connection = _connect(database_path)
    first_statement = split_sql_statements(MIGRATIONS[3])[0]
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(first_statement)  # CREATE TABLE agent_runs — partial migration
    _hold(marker)
    connection.commit()  # unreachable: process is killed first


def _crash_migration_v35_first_statement(database_path: str,
                                         marker: str) -> None:
    """WP-24H-V2KB: crash between the FIRST DDL statement of the newest
    knowledge migration and the rest of it (mirrors migrate()'s
    per-statement execution inside BEGIN IMMEDIATE)."""
    latest = max(MIGRATIONS)
    first_statement = split_sql_statements(MIGRATIONS[latest])[0]
    connection = _connect(database_path)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(first_statement)  # partial v35 — never committed
    _hold(marker)
    connection.commit()  # unreachable: process is killed first


def main() -> None:
    database_path, mode, marker = sys.argv[1], sys.argv[2], sys.argv[3]
    if mode == "event-between-statements":
        _crash_event_between_statements(database_path, marker)
    elif mode == "migration-mid-script":
        _crash_migration_mid_script(database_path, marker)
    elif mode == "migration-v35-first-statement":
        _crash_migration_v35_first_statement(database_path, marker)
    else:
        raise SystemExit(f"unknown crash mode: {mode}")


if __name__ == "__main__":
    main()
