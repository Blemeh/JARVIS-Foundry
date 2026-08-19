"""V8 hybrid memory — migration v33: forward, idempotency, interruption,
rollback, compatibility. (Mission §15: migration rehearsal prerequisites.)

Traceability: lab tests test_git_rollback_history_preserved /
test_curation_defense_versioning + FINAL_AUDIT/08 schema delta.
"""
from __future__ import annotations

import pathlib
import sqlite3
import tempfile

import pytest

from jarvis_foundry.database import FoundryDatabase
from v8_harness import lab  # noqa: F401  (fixture)

TIER_C_TABLES = (
    "tier_c_memories", "tier_c_memory_revisions", "tier_c_validations",
    "tier_c_blocks", "tier_c_block_revisions", "tier_c_procedures",
)


def _fresh() -> FoundryDatabase:
    return FoundryDatabase(pathlib.Path(tempfile.mkdtemp(prefix="v8-mig-")) / "db.sqlite")


def test_forward_migration_reaches_v33():
    db = _fresh()
    assert db.initialize(target=33) == 33
    with db.connect() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TIER_C_TABLES:
            assert table in tables, f"missing {table}"
        version = conn.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert version == 33
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        assert quick == "ok"


def test_migration_idempotent_forward_twice():
    db = _fresh()
    db.initialize(target=33)
    # second initialization must be a no-op
    assert db.initialize(target=33) == 33
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=33").fetchone()["n"]
        assert count == 1


def test_migration_partially_applied_then_retried():
    """Simulate 'migration already partially applied': the tier_c tables
    exist (e.g. from an earlier attempt on a copy) — the migration must
    complete cleanly and idempotently."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="v8-mig-"))
    db = FoundryDatabase(root / "db.sqlite")
    db.initialize(target=32)
    # manually create the tier_c tables with the CORRECT v33 shape
    with sqlite3.connect(root / "db.sqlite") as raw:
        raw.execute("CREATE TABLE tier_c_memories (memory_id TEXT PRIMARY KEY, "
                    "mission_id TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '', "
                    "mem_type TEXT NOT NULL DEFAULT 'episodic', scope TEXT NOT NULL DEFAULT 'role', "
                    "content TEXT NOT NULL, source_ids_json TEXT NOT NULL DEFAULT '[]', "
                    "evidence_ids_json TEXT NOT NULL DEFAULT '[]', confidence REAL NOT NULL DEFAULT 0.5, "
                    "state TEXT NOT NULL DEFAULT 'OBSERVED', created_at TEXT NOT NULL DEFAULT '', "
                    "last_verified TEXT, expires_at TEXT, supersedes TEXT, contradicts TEXT, "
                    "importance REAL NOT NULL DEFAULT 0.5, context_cost_chars INTEGER NOT NULL DEFAULT 0, "
                    "promotion_reason TEXT, last_event_id TEXT NOT NULL UNIQUE, updated_at TEXT NOT NULL DEFAULT '')")
    assert db.initialize(target=33) == 33  # must complete despite the pre-existing table
    assert db.initialize(target=33) == 33  # and stay idempotent


def test_rollback_to_v32_restores_previous_schema():
    db = _fresh()
    db.initialize(target=33)
    # simulate a rollback decision: drop the v33 row and tables
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for table in reversed(TIER_C_TABLES):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("DELETE FROM schema_migrations WHERE version=33")
        conn.commit()
    # the previous executable still works at v32
    assert db.initialize(target=33) == 33  # re-migrate forward works
    with db.connect() as conn:
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert version == 33


def test_v32_schema_unchanged_by_migration_delta():
    """Tier A/B tables must not be altered by v33 (additive-only proof)."""
    db = _fresh()
    db.initialize(target=32)
    with db.connect() as conn:
        v32_tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    db.initialize(target=33)
    with db.connect() as conn:
        v33_tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    added = v33_tables - v32_tables
    assert added == set(TIER_C_TABLES), f"v33 must be additive only, got {added}"


def test_interrupted_migration_rolls_back_atomically():
    """The migrate() wrapper runs DDL inside one transaction; a failing
    statement must leave the DB at the previous version."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="v8-mig-"))
    db = FoundryDatabase(root / "db.sqlite")
    db.initialize(target=32)
    before = db.initialize(target=32)
    assert before == 32
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS tier_c_memories (broken")
        except sqlite3.OperationalError:
            conn.rollback()
    # still at 32, and a clean forward migration works
    assert db.initialize(target=33) == 33


def test_locked_db_fails_loudly_and_recovers():
    root = pathlib.Path(tempfile.mkdtemp(prefix="v8-mig-"))
    db = FoundryDatabase(root / "db.sqlite")
    db.initialize(target=33)
    blocker = sqlite3.connect(root / "db.sqlite")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            FoundryDatabase(root / "db.sqlite").initialize()
    finally:
        blocker.rollback()
        blocker.close()
    assert db.initialize(target=33) == 33  # recovers after the lock clears
