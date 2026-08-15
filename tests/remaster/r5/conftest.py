from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  causation_id TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  schema_version INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64)
);
"""

EVENTS_DDL_LAX = """
CREATE TABLE IF NOT EXISTS events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  causation_id TEXT,
  idempotency_key TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL
);
"""


def legacy_payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def make_legacy_db(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    migrations: int = 32,
    strict: bool = True,
) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);"
    )
    connection.executescript(EVENTS_DDL if strict else EVENTS_DDL_LAX)
    connection.executemany(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, '2026-08-15T00:00:00Z')",
        [(version,) for version in range(1, migrations + 1)],
    )
    for row in rows:
        connection.execute(
            "INSERT INTO events ("
            "sequence, event_id, event_type, occurred_at, recorded_at, source_type, "
            "source_id, correlation_id, causation_id, idempotency_key, schema_version, "
            "payload_json, payload_hash"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.get("sequence"),
                row["event_id"],
                row["event_type"],
                row.get("occurred_at", "2026-08-15T00:00:00Z"),
                row.get("recorded_at", "2026-08-15T00:00:00Z"),
                row.get("source_type", "test"),
                row.get("source_id", "test"),
                row.get("correlation_id", row["event_id"]),
                row.get("causation_id"),
                row["idempotency_key"],
                row.get("schema_version", 1),
                json.dumps(row.get("payload", {}), sort_keys=True, separators=(",", ":")),
                row.get(
                    "payload_hash",
                    legacy_payload_hash(row.get("payload", {})),
                ),
            ),
        )
    connection.commit()
    connection.close()
    return path


def sample_rows() -> list[dict[str, Any]]:
    return [
        {
            "event_id": "evt-1",
            "event_type": "mission.created",
            "idempotency_key": "mission:1",
            "payload": {"mission_id": "mission-1", "objective": "alpha"},
        },
        {
            "event_id": "evt-2",
            "event_type": "owner_directive.recorded",
            "idempotency_key": "owner:1",
            "payload": {"directive_id": "directive-1", "command_type": "PAUSE"},
        },
        {
            "event_id": "evt-3",
            "event_type": "supervisor.heartbeat",
            "idempotency_key": "supervisor:1",
            "payload": {"instance_id": "instance-1", "trust_state": "LAB"},
        },
        {
            "event_id": "evt-4",
            "event_type": "knowledge.claim_recorded",
            "idempotency_key": "knowledge:1",
            "payload": {"claim_id": "claim-1", "statement": "x"},
        },
    ]


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    return make_legacy_db(tmp_path / "legacy.db", sample_rows())


@pytest.fixture
def rows_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
