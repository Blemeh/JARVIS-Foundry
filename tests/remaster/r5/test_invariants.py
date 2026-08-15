from __future__ import annotations

import sqlite3
from pathlib import Path

from conftest import legacy_payload_hash, make_legacy_db, sample_rows

from jarvis_foundry.kernel.compat.invariants import (
    check_projection_snapshot,
    validate_rows,
)
from jarvis_foundry.kernel.compat.projections import (
    HistoricalLedgerProjectionHandler,
)
from jarvis_foundry.kernel.compat.readers import LegacyEventsReader
from jarvis_foundry.kernel.projections import ProjectionSnapshot


def _report(path: Path):
    reader = LegacyEventsReader(path)
    return validate_rows(reader.read_rows())


def test_clean_rows_pass_invariants(legacy_db: Path) -> None:
    report = _report(legacy_db)
    assert report.ok
    assert report.event_count == 4
    assert report.min_sequence == 1
    assert report.max_sequence == 4
    assert report.sequence_gaps == ()
    assert report.event_type_counts["supervisor.heartbeat"] == 1


def test_sequence_gap_detected(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows())
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM events WHERE event_id = 'evt-2'")
    report = _report(db)
    assert not report.ok
    assert 2 in report.sequence_gaps


def test_duplicate_event_id_detected(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows(), strict=False)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE events SET event_id = 'evt-1', idempotency_key = 'dup:1' "
            "WHERE event_id = 'evt-2'"
        )
    report = _report(db)
    assert not report.ok
    assert "evt-1" in report.duplicate_event_ids


def test_duplicate_idempotency_key_detected(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows(), strict=False)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE events SET idempotency_key = 'mission:1', event_id = 'evt-9' "
            "WHERE event_id = 'evt-2'"
        )
    report = _report(db)
    assert not report.ok
    assert "mission:1" in report.duplicate_idempotency_keys


def test_payload_hash_mismatch_detected(tmp_path: Path) -> None:
    db = make_legacy_db(tmp_path / "legacy.db", sample_rows(), strict=False)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE events SET payload_hash = ? WHERE event_id = 'evt-3'",
            ("0" * 64,),
        )
    report = _report(db)
    assert not report.ok
    assert "evt-3" in report.payload_hash_mismatches


def test_projection_state_invariants_match_replay_state(tmp_path: Path) -> None:
    from jarvis_foundry.kernel.compat.readers import LegacyRow
    from jarvis_foundry.kernel.events import LedgerEvent

    rows = sample_rows()
    legacy_rows = [
        LegacyRow(
            sequence=index + 1,
            event_id=row["event_id"],
            event_type=row["event_type"],
            occurred_at="2026-08-15T00:00:00Z",
            recorded_at="2026-08-15T00:00:00Z",
            source_type="test",
            source_id="test",
            correlation_id=row["event_id"],
            causation_id=None,
            idempotency_key=row["idempotency_key"],
            schema_version=1,
            payload=row["payload"],
            payload_json="",
            payload_hash=legacy_payload_hash(row["payload"]),
        )
        for index, row in enumerate(rows)
    ]
    row_report = validate_rows(legacy_rows)
    assert row_report.ok

    events = [
        LedgerEvent.create(
            event_type=row["event_type"],
            payload=row["payload"],
            source_type="test",
            source_id="test",
            idempotency_key=row["idempotency_key"],
            event_id=row["event_id"],
            occurred_at="2026-08-15T00:00:00Z",
            recorded_at="2026-08-15T00:00:00Z",
        ).with_chain(sequence=index + 1, previous_hash=None, header_hash=f"h{index + 1}")
        for index, row in enumerate(rows)
    ]
    handler = HistoricalLedgerProjectionHandler(event_types={row["event_type"] for row in rows})
    state = handler.initial_state()
    for event in events:
        state = handler.apply(state, event)
    snapshot = ProjectionSnapshot(states={"legacy_history": state}, versions={"legacy_history": 1})
    check = check_projection_snapshot(snapshot, row_report)
    assert check.ok
    assert check.mismatches == ()
