from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from conftest import make_legacy_db, sample_rows

from jarvis_foundry.kernel.compat.errors import CompatReadError, CompatSchemaError
from jarvis_foundry.kernel.compat.readers import LegacyEventsReader


def test_reader_maps_legacy_rows_to_ledger_events_without_mutation(
    legacy_db: Path,
) -> None:
    before = hashlib.sha256(legacy_db.read_bytes()).hexdigest()
    reader = LegacyEventsReader(legacy_db, migration_version=32)
    events = reader.read_events()
    assert [event.event_id for event in events] == ["evt-1", "evt-2", "evt-3", "evt-4"]
    assert events[0].event_type == "mission.created"
    assert events[0].schema_version == 1
    assert events[0].kind_version == 1
    assert events[0].payload["mission_id"] == "mission-1"
    assert events[0].previous_hash is None
    assert events[0].header_hash is None
    after = hashlib.sha256(legacy_db.read_bytes()).hexdigest()
    assert before == after
    assert not list(legacy_db.parent.glob("legacy.db-wal"))
    assert not list(legacy_db.parent.glob("legacy.db-shm"))


def test_reader_preserves_null_causation_id(tmp_path: Path) -> None:
    rows = sample_rows()
    for row in rows:
        row["causation_id"] = None
    db = make_legacy_db(tmp_path / "legacy.db", rows)
    reader = LegacyEventsReader(db)
    events = reader.read_events()
    assert len(events) == len(rows)
    assert all(event.causation_id is None for event in events)
    assert not any(event.causation_id == "None" for event in events)


def test_reader_orders_rows_by_sequence_not_insertion_order(tmp_path: Path) -> None:
    rows = sample_rows()
    db = make_legacy_db(tmp_path / "legacy.db", rows)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE events SET sequence = 400 WHERE event_id = 'evt-1'")
    reader = LegacyEventsReader(db)
    assert [row.sequence for row in reader.read_rows()] == [2, 3, 4, 400]


def test_reader_fails_closed_on_unsupported_table(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows())
    with pytest.raises(CompatSchemaError, match="unsupported legacy event table"):
        LegacyEventsReader(db, table="other")


def test_reader_fails_closed_on_missing_column(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows())
    with sqlite3.connect(db) as connection:
        connection.execute("ALTER TABLE events DROP COLUMN correlation_id")
    with pytest.raises(CompatSchemaError, match="unsupported legacy events columns"):
        LegacyEventsReader(db)


def test_reader_fails_closed_on_bad_payload_hash(tmp_path: Path) -> None:
    rows = sample_rows()
    rows[0]["payload_hash"] = "0" * 64
    db = make_legacy_db(tmp_path / "legacy.db", rows)
    reader = LegacyEventsReader(db)
    with pytest.raises(CompatReadError, match="payload hash mismatch"):
        reader.read_events()


def test_reader_fails_closed_on_invalid_json(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows())
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE events SET payload_json = '{bad' WHERE event_id = 'evt-1'")
    reader = LegacyEventsReader(db)
    with pytest.raises(CompatReadError, match="not valid JSON"):
        reader.read_events()


def test_reader_fails_closed_on_unsupported_schema_version(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows())
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE events SET schema_version = 9 WHERE event_id = 'evt-1'")
    reader = LegacyEventsReader(db)
    with pytest.raises(CompatReadError, match="unsupported row schema version"):
        reader.read_events()


def test_reader_fails_closed_on_malformed_event_type(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows())
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE events SET event_type = 'bad' WHERE event_id = 'evt-1'")
    reader = LegacyEventsReader(db)
    with pytest.raises(CompatReadError, match="malformed"):
        reader.read_events()


def test_reader_collects_multiple_refusals(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows(), strict=False)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE events SET payload_hash = ? WHERE event_id = 'evt-1'",
            ("0" * 64,),
        )
        connection.execute("UPDATE events SET schema_version = 2 WHERE event_id = 'evt-2'")
    reader = LegacyEventsReader(db)
    refusals = reader.collect_refusals(reader.read_rows())
    assert len(refusals) == 2
    assert {refusal.sequence for refusal in refusals} == {1, 2}


def test_reader_accepts_unknown_but_wellformed_event_type(tmp_path: Path) -> None:
    rows = sample_rows()
    rows.append(
        {
            "event_id": "evt-5",
            "event_type": "compat.new.historical.kind",
            "idempotency_key": "new:1",
            "payload": {"x": 1},
        }
    )
    db = make_legacy_db(tmp_path / "legacy.db", rows)
    reader = LegacyEventsReader(db)
    events = reader.read_events()
    assert events[-1].event_type == "compat.new.historical.kind"
