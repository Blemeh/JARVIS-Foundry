from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_legacy_db, sample_rows

from jarvis_foundry.kernel.compat.replay import replay_legacy, replay_signature
from jarvis_foundry.kernel.errors import UnknownEventError


def test_replay_is_deterministic_and_chain_valid(legacy_db: Path) -> None:
    first = replay_legacy(legacy_db)
    second = replay_legacy(legacy_db)
    assert first.passed
    assert replay_signature(first) == replay_signature(second)
    assert first.row_count == 4
    assert first.converted_count == 4
    assert first.chain_sequence_count == 4
    assert first.chain_valid
    assert first.projection_invariant_ok
    assert first.refused_count == 0


def test_replay_projection_uses_r4_handlers(legacy_db: Path) -> None:
    result = replay_legacy(legacy_db)
    # The R4 dispatcher raises if a handler set cannot be produced; the
    # snapshot hash is the deterministic proof of state.
    assert result.projection_snapshot_sha256
    assert "legacy_history" in result.projection_versions
    assert result.event_type_counts["mission.created"] == 1


def test_replay_refuses_unknown_catalog_type(tmp_path: Path) -> None:
    rows = sample_rows()
    rows.append(
        {
            "event_id": "mystery-1",
            "event_type": "mystery.kind",
            "idempotency_key": "mystery:1",
            "payload": {},
        }
    )
    db = make_legacy_db(tmp_path / "legacy.db", rows)
    result = replay_legacy(db)
    assert not result.passed
    assert result.refused_count == 1
    assert "not in the historical compatibility catalog" in result.refusals[0].reason


def test_replay_fails_closed_on_integrity_defect(tmp_path: Path) -> None:
    rows = sample_rows()
    rows[1]["payload_hash"] = "1" * 64
    db = make_legacy_db(tmp_path / "legacy.db", rows)
    result = replay_legacy(db)
    assert not result.passed
    assert result.refused_count == 1
    assert "payload hash mismatch" in result.refusals[0].reason


def test_replay_refuses_unregistered_ledger_kind_on_manual_append(
    legacy_db: Path,
) -> None:
    from jarvis_foundry.kernel.compat.registry import compat_kind_registry
    from jarvis_foundry.kernel.events import LedgerEvent
    from jarvis_foundry.kernel.ledger import Ledger

    registry = compat_kind_registry({"mission.created"})
    ledger = Ledger(legacy_db.parent / "r4.db", registry=registry)
    event = LedgerEvent.create(
        event_type="unregistered.kind",
        payload={},
        source_type="test",
        source_id="test",
        idempotency_key="unregistered:1",
    )
    with pytest.raises(UnknownEventError):
        ledger.append(event)
