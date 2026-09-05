"""SQLite connection lifecycle contracts for disposable proof runners."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase


def test_database_context_closes_connection_after_transaction(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()

    with database.connect() as connection:
        connection.execute("SELECT 1")

    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("database context left a live SQLite connection")


def test_readonly_database_context_closes_connection(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()

    with database.connect(readonly=True) as connection:
        connection.execute("SELECT 1")

    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("read-only context left a live SQLite connection")


def test_precommit_guard_runs_at_commit_boundary_and_can_rollback(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "CREATE TABLE precommit_probe (value TEXT NOT NULL)",
        )

    observed: list[str] = []
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO precommit_probe(value) VALUES (?)",
            ("commit",),
        )
        database.register_precommit_guard(
            connection,
            lambda active: observed.append(str(
                active.execute(
                    "SELECT value FROM precommit_probe",
                ).fetchone()[0],
            )),
        )

    assert observed == ["commit"]

    class RefuseCommit(RuntimeError):
        pass

    try:
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO precommit_probe(value) VALUES (?)",
                ("rollback",),
            )

            def refuse_commit(_: sqlite3.Connection) -> None:
                raise RefuseCommit("guard refused commit")

            database.register_precommit_guard(connection, refuse_commit)
    except RefuseCommit:
        pass
    else:
        raise AssertionError("precommit guard did not abort the transaction")

    with database.connect(readonly=True) as connection:
        values = [
            str(row[0])
            for row in connection.execute(
                "SELECT value FROM precommit_probe ORDER BY rowid",
            ).fetchall()
        ]
    assert values == ["commit"]
