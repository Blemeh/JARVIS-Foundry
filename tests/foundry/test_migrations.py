"""WP1 migration atomicity and statement-splitting adversarial tests."""

from __future__ import annotations

import sqlite3

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.migrations import MIGRATIONS, SCHEMA_VERSION, migrate, split_sql_statements


def test_split_sql_statements_keeps_semicolons_inside_string_literals():
    script = """
    CREATE TABLE t (a TEXT DEFAULT 'x;y');
    INSERT INTO t VALUES ('a;b;c');
    SELECT ';' ;
    """
    statements = split_sql_statements(script)
    assert len(statements) == 3
    assert statements[0].startswith("CREATE TABLE t")
    assert statements[1].endswith("VALUES ('a;b;c')")
    assert statements[2] == "SELECT ';'"


def test_split_sql_statements_handles_empty_and_trailing_semicolons():
    assert split_sql_statements("") == []
    assert split_sql_statements("   ;  ;  ") == []
    assert split_sql_statements("CREATE TABLE a (x INTEGER);") == ["CREATE TABLE a (x INTEGER)"]
    assert split_sql_statements("CREATE TABLE a (x INTEGER);;") == ["CREATE TABLE a (x INTEGER)"]


def test_split_sql_statements_rejects_unterminated_string():
    with pytest.raises(ValueError, match="unterminated string literal"):
        split_sql_statements("CREATE TABLE a (x TEXT DEFAULT 'oops);")


def test_migration_failure_mid_version_rolls_back_everything(tmp_path, monkeypatch):
    """A failing statement inside a migration must roll back the whole version:
    no partial DDL and no schema_migrations marker for the failed version."""
    path = tmp_path / "foundry.db"
    connection = sqlite3.connect(path)
    connection.executescript(MIGRATIONS[1])
    connection.executescript(MIGRATIONS[2])
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?), (2, ?)",
        ("2026-08-11T00:00:00Z", "2026-08-11T00:00:01Z"),
    )
    connection.commit()
    connection.close()

    broken = MIGRATIONS[3] + "\nCREATE TABLE broken_should_never_exist (x INTEGER);\nTHIS IS NOT SQL;\n"
    original = MIGRATIONS[3]
    monkeypatch.setitem(MIGRATIONS, 3, broken)

    database = FoundryDatabase(path)
    with pytest.raises(sqlite3.OperationalError):
        database.initialize()

    with sqlite3.connect(path) as check:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "agent_runs" not in tables, "partial DDL from the failed version must roll back"
        assert "broken_should_never_exist" not in tables
        assert check.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 2
        assert check.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    monkeypatch.setitem(MIGRATIONS, 3, original)

    healed = FoundryDatabase(path)
    assert healed.initialize() == SCHEMA_VERSION
    with healed.connect() as check:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"agent_runs", "evidence_bundles", "verification_verdicts", "budgets"} <= tables
    assert healed.quick_check() == "ok"


def test_migrate_is_idempotent_on_repeated_initialize(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    assert database.initialize() == SCHEMA_VERSION
    assert database.initialize() == SCHEMA_VERSION
    with database.connect() as connection:
        rows = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert [row[0] for row in rows] == list(range(1, SCHEMA_VERSION + 1))
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert database.quick_check() == "ok"


def test_migrate_rejects_unknown_schema_version():
    with pytest.raises(KeyError):
        migrate(sqlite3.connect(":memory:"), "2026-08-11T00:00:00Z", target=99)


def test_migrate_returns_target_version():
    connection = sqlite3.connect(":memory:")
    assert migrate(connection, "2026-08-11T00:00:00Z") == SCHEMA_VERSION
    connection.close()
