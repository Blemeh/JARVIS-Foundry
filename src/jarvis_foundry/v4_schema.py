"""Additive durable tables used by the roadmap-V4 surface.

The tables are projections of the existing append-only ledger.  They are
created idempotently without advancing the historical schema version so an
isolated V4 candidate can be exercised against a schema-v20 database while
preserving the V5 branch's migration numbering and existing tests.
"""
from __future__ import annotations

import sqlite3


V4_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS cost_measurements (
    measurement_id TEXT PRIMARY KEY,
    mission_id TEXT,
    run_id TEXT,
    provider TEXT,
    model TEXT,
    status TEXT NOT NULL CHECK(status IN ('MEASURED', 'UNKNOWN', 'NOT_AVAILABLE', 'NOT_TESTED')),
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    duration_ms REAL,
    resource_json TEXT NOT NULL DEFAULT '{}',
    failure_class TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cost_measurements_mission ON cost_measurements(mission_id, recorded_at);
CREATE INDEX IF NOT EXISTS cost_measurements_run ON cost_measurements(run_id);

CREATE TABLE IF NOT EXISTS briefing_outbox (
    briefing_id TEXT PRIMARY KEY,
    briefing_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('TWICE_DAILY', 'EXCEPTION', 'MISSION_SUMMARY')),
    period_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PLANNED', 'CLAIMED', 'DELIVERED', 'FAILED', 'BLOCKED')),
    payload_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    idempotency_key TEXT NOT NULL UNIQUE,
    failure_reason TEXT,
    delivered_at TEXT,
    last_event_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS briefing_outbox_status ON briefing_outbox(status, period_key);

CREATE TABLE IF NOT EXISTS voice_interactions (
    interaction_id TEXT PRIMARY KEY,
    auth_status TEXT NOT NULL CHECK(auth_status IN ('AUTHENTICATED', 'UNAUTHENTICATED', 'REPLAYED', 'UNAUTHORIZED')),
    language TEXT,
    transcript TEXT,
    response_text TEXT,
    stt_provider TEXT,
    tts_provider TEXT,
    command_type TEXT,
    directive_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('COMPLETED', 'REJECTED', 'AMBIGUOUS', 'MALFORMED', 'BLOCKED', 'NOT_TESTED')),
    audio_metadata_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    last_event_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS voice_interactions_status ON voice_interactions(status, updated_at);

CREATE TABLE IF NOT EXISTS control_nonces (
    nonce TEXT PRIMARY KEY,
    command_hash TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    used_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def ensure_v4_tables(connection: sqlite3.Connection) -> None:
    """Create the additive V4 tables in the caller's transaction."""
    for statement in V4_TABLES_SQL.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(statement)

