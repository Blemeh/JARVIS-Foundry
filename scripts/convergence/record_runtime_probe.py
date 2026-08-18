"""Capture a read-only canonical runtime/ledger probe for convergence evidence."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1
    )
    connection.row_factory = sqlite3.Row
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    latest = connection.execute(
        "SELECT event_type, occurred_at, source_id FROM events "
        "ORDER BY sequence DESC LIMIT 1").fetchone()
    heartbeat = connection.execute(
        "SELECT occurred_at, source_id, payload_json FROM events "
        "WHERE event_type='supervisor.heartbeat' ORDER BY sequence DESC "
        "LIMIT 1").fetchone()
    supervisor = connection.execute(
        "SELECT * FROM supervisor_state WHERE singleton=1").fetchone()
    lease = connection.execute(
        "SELECT * FROM leases WHERE lease_key='supervisor:singleton'"
    ).fetchone()
    schema = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    stop_rows = connection.execute(
        "SELECT directive_id, command_type, target, active, updated_at "
        "FROM directives WHERE command_type IN ('STOP_EVERYTHING','PAUSE') "
        "ORDER BY updated_at DESC LIMIT 5").fetchall()
    payload = {
        "schema": "jarvis-canonical-runtime-probe-v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "database_exists": database.is_file(),
        "sqlite_quick_check": connection.execute("PRAGMA quick_check").fetchone()[0],
        "schema_version": schema[0] if schema else None,
        "event_head": connection.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0],
        "latest_event": dict(latest) if latest else None,
        "latest_heartbeat": dict(heartbeat) if heartbeat else None,
        "supervisor_state": dict(supervisor) if supervisor else None,
        "supervisor_lease": dict(lease) if lease else None,
        "stop_directives": [dict(row) for row in stop_rows],
        "required_tables_present": all(
            table in tables for table in (
                "events", "supervisor_state", "leases", "schema_migrations")),
    }
    connection.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
