from __future__ import annotations

import hashlib
from pathlib import Path

from conftest import make_legacy_db, sample_rows

from jarvis_foundry.kernel.compat.corrections import (
    Correction,
    CorrectionLedger,
)
from jarvis_foundry.kernel.compat.readers import LegacyEventsReader
from jarvis_foundry.kernel.compat.registry import compat_kind_registry
from jarvis_foundry.kernel.ledger import Ledger


def _corrections() -> list[Correction]:
    return [
        Correction(
            correction_id="R5C-001",
            target_event_id="evt-1",
            reason="rehearsal correction",
            evidence_ref="tests/remaster/r5/test_corrections.py",
            evidence_sha256="e" * 64,
            corrected_payload={"mission_id": "mission-1-corrected"},
        )
    ]


def test_correction_appends_new_event_only(tmp_path: Path) -> None:
    legacy = make_legacy_db(tmp_path / "legacy.db", sample_rows())
    before = hashlib.sha256(legacy.read_bytes()).hexdigest()
    ledger_path = tmp_path / "r4.db"
    registry = compat_kind_registry(
        {row["event_type"] for row in sample_rows()},
        extra_types=["compat.correction.recorded"],
    )
    ledger = Ledger(ledger_path, registry=registry)
    events = LegacyEventsReader(legacy).read_events()
    with ledger.transaction() as connection:
        for event in events:
            ledger.append_with_connection(connection, event)
    before_count = ledger.next_sequence() - 1

    ledger_file = CorrectionLedger(tmp_path / "corrections.json")
    ledger_file.append(_corrections()[0])
    results = ledger_file.apply_to_ledger(ledger, ledger_file.load())
    assert len(results) == 1
    assert ledger.next_sequence() - 1 == before_count + 1
    correction_event = results[0].event
    assert correction_event.event_type == "compat.correction.recorded"
    assert correction_event.payload["reason"] == "rehearsal correction"
    assert correction_event.payload["evidence_ref"] == "tests/remaster/r5/test_corrections.py"

    after = hashlib.sha256(legacy.read_bytes()).hexdigest()
    assert before == after
    stored = ledger_file.load()
    assert stored[0].correction_id == "R5C-001"


def test_correction_apply_is_idempotent(tmp_path: Path) -> None:
    ledger_path = tmp_path / "r4.db"
    registry = compat_kind_registry(
        set(),
        extra_types=["compat.correction.recorded"],
    )
    ledger = Ledger(ledger_path, registry=registry)
    ledger_file = CorrectionLedger(tmp_path / "corrections.json")
    ledger_file.save(_corrections())
    first = ledger_file.apply_to_ledger(ledger, ledger_file.load())
    second = ledger_file.apply_to_ledger(ledger, ledger_file.load())
    assert [result.sequence for result in first] == [result.sequence for result in second]
    assert [result.created for result in first] == [True]
    assert [result.created for result in second] == [False]


def test_correction_ledger_rejects_duplicate_id(tmp_path: Path) -> None:
    from jarvis_foundry.kernel.compat.errors import CompatReadError

    ledger_file = CorrectionLedger(tmp_path / "corrections.json")
    ledger_file.append(_corrections()[0])
    try:
        ledger_file.append(_corrections()[0])
    except CompatReadError as error:
        assert "already recorded" in str(error)
    else:
        raise AssertionError("duplicate correction id must be refused")
