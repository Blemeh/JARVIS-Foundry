"""Live V7C activation: the supervisor builds the resilience coordinator
only when the worker runtime YAML explicitly enables it (fail-closed)."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.supervisor.run import _resilience_coordinator


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_coordinator_off_by_default(tmp_path: Path) -> None:
    database = _database(tmp_path)
    config = tmp_path / "worker.yaml"
    config.write_text("enabled: true\n", encoding="utf-8")
    assert _resilience_coordinator(database, config) is None


def test_coordinator_requires_explicit_flag(tmp_path: Path) -> None:
    database = _database(tmp_path)
    config = tmp_path / "worker.yaml"
    config.write_text(
        "enabled: true\nresilience:\n  coordinator_enabled: true\n",
        encoding="utf-8")
    coordinator = _resilience_coordinator(database, config)
    assert coordinator is not None


def test_coordinator_flag_false_stays_off_and_bad_yaml_fails_closed(
        tmp_path: Path) -> None:
    database = _database(tmp_path)
    off = tmp_path / "off.yaml"
    off.write_text("resilience:\n  coordinator_enabled: false\n",
                   encoding="utf-8")
    assert _resilience_coordinator(database, off) is None
    missing = tmp_path / "missing.yaml"
    assert _resilience_coordinator(database, missing) is None
