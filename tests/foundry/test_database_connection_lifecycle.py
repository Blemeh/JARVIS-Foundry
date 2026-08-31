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
