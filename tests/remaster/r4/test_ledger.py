from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jarvis_foundry.kernel.errors import (
    EventConflictError,
    EventIntegrityError,
    FinancialActionDeniedError,
    UnknownEventError,
)
from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent, payload_hash
from jarvis_foundry.kernel.ledger import Ledger, Subscription

KINDS = ("goal.created", "mission.created", "supervisor.started", "owner.directive.recorded")


def registry() -> EventKindRegistry:
    return (
        EventKindRegistry()
        .register("goal.created", required_keys=("goal_id",))
        .register("mission.created", required_keys=("mission_id",))
        .register("supervisor.started", required_keys=("instance_id",))
        .register("owner.directive.recorded", required_keys=("directive_id", "command_type"))
        .register("payment.requested", required_keys=("amount",), financial=True)
    )


def valid_event(index: int, kind: str, text: str, registry_used: EventKindRegistry) -> LedgerEvent:
    payload: dict[str, Any]
    if kind == "goal.created":
        payload = {"goal_id": f"goal-{index}", "title": text, "status": "ACTIVE"}
    elif kind == "mission.created":
        payload = {"mission_id": f"mission-{index}", "objective": text, "status": "PLANNED"}
    elif kind == "supervisor.started":
        payload = {"instance_id": f"instance-{index}", "trust_state": "LAB"}
    else:
        payload = {
            "directive_id": f"directive-{index}",
            "command_type": "PAUSE",
            "exact_text": text,
        }
    return LedgerEvent.create(
        event_type=kind,
        payload=payload,
        source_type="r4-test",
        source_id="hypothesis",
        idempotency_key=f"{kind}:{index}",
        correlation_id=f"corr-{index}",
        event_id=f"evt-{index}",
        occurred_at="2026-08-15T00:00:00.000Z",
    )


def fresh_ledger(path: Path) -> Ledger:
    return Ledger(path / "kernel.db", registry=registry())


def _write_connection(ledger: Ledger) -> sqlite3.Connection:
    """Private writable handle for trigger-enforcement probes only."""
    connection = sqlite3.connect(ledger.path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def test_payload_hash_is_content_derived() -> None:
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})
    assert len(payload_hash({"a": 1})) == 64


def test_unknown_event_kind_is_refused(tmp_path: Path) -> None:
    ledger = fresh_ledger(tmp_path)
    event = LedgerEvent.create(
        event_type="mystery.kind",
        payload={},
        source_type="r4-test",
        source_id="x",
        idempotency_key="mystery:1",
        event_id="evt-unknown",
    )
    with pytest.raises(UnknownEventError):
        ledger.append(event)


def test_financial_event_kind_is_deterministically_denied(tmp_path: Path) -> None:
    ledger = fresh_ledger(tmp_path)
    event = LedgerEvent.create(
        event_type="payment.requested",
        payload={"amount": "1", "currency": "USD"},
        source_type="r4-test",
        source_id="x",
        idempotency_key="payment:1",
        event_id="evt-pay",
    )
    with pytest.raises(FinancialActionDeniedError):
        ledger.append(event)


def test_append_read_replay_and_subscribe(tmp_path: Path) -> None:
    ledger = fresh_ledger(tmp_path)
    seen: list[LedgerEvent] = []

    def subscriber(event: LedgerEvent) -> None:
        seen.append(event)

    subscription = ledger.subscribe(subscriber)
    assert isinstance(subscription, Subscription)
    first = ledger.append(valid_event(1, "goal.created", "objective", registry()))
    second = ledger.append(valid_event(2, "mission.created", "objective", registry()))
    assert first.created is True
    assert first.sequence == 1
    assert second.sequence == 2
    assert first.event.header_hash is not None
    assert second.event.previous_hash == first.event.header_hash
    assert len(seen) == 2
    replayed = ledger.replay()
    assert [event.event_id for event in replayed] == ["evt-1", "evt-2"]
    read = ledger.read(after=1)
    assert [event.event_id for event in read] == ["evt-2"]
    subscription.close()
    ledger.append(valid_event(3, "supervisor.started", "x", registry()))
    assert len(seen) == 2


def test_duplicate_event_is_idempotent_and_conflict_is_rejected(tmp_path: Path) -> None:
    ledger = fresh_ledger(tmp_path)
    first = ledger.append(valid_event(1, "goal.created", "objective", registry()))
    second = ledger.append(valid_event(1, "goal.created", "objective", registry()))
    assert second.created is False
    assert second.sequence == first.sequence
    assert len(ledger.replay()) == 1
    conflicting = valid_event(2, "goal.created", "objective", registry())
    conflicting = conflicting.with_idempotency_key("goal.created:1")
    with pytest.raises(EventConflictError):
        ledger.append(conflicting)


def test_invalid_event_is_rejected_by_registry_and_payload_check(tmp_path: Path) -> None:
    ledger = fresh_ledger(tmp_path)
    missing_required = LedgerEvent.create(
        event_type="goal.created",
        payload={},
        source_type="r4-test",
        source_id="x",
        idempotency_key="goal:bad",
        event_id="evt-bad",
    )
    with pytest.raises(EventIntegrityError):
        ledger.append(missing_required)
    tampered = valid_event(1, "goal.created", "objective", registry())
    tampered = tampered.with_payload_hash("0" * 64)
    with pytest.raises(EventIntegrityError):
        ledger.append(tampered)


def test_db_constraints_enforce_append_only(tmp_path: Path) -> None:
    ledger = fresh_ledger(tmp_path)
    result = ledger.append(valid_event(1, "goal.created", "objective", registry()))
    with _write_connection(ledger) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE ledger_events SET payload = '{}' WHERE sequence = 1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM ledger_events WHERE sequence = 1")
    with ledger.read_connection() as connection:
        row = connection.execute(
            "SELECT event_id, payload_hash, header_hash FROM ledger_events WHERE sequence = ?",
            (result.sequence,),
        ).fetchone()
    assert row is not None
    assert row["event_id"] == "evt-1"
    assert len(row["payload_hash"]) == 64
    assert len(row["header_hash"]) == 64


def test_fk_and_check_constraints_reject_bad_rows(tmp_path: Path) -> None:
    ledger = fresh_ledger(tmp_path)
    ledger.append(valid_event(1, "goal.created", "objective", registry()))
    with _write_connection(ledger) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO ledger_events "
            "(event_id, event_type, occurred_at, recorded_at, source_type, source_id, "
            "correlation_id, causation_id, idempotency_key, schema_version, payload, "
            "payload_hash, previous_hash, header_hash, kind_version) VALUES "
            "(?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, NULL, ?, ?)",
            (
                "evt-fk",
                "ghost.kind",
                "2026-08-15T00:00:00Z",
                "2026-08-15T00:00:00Z",
                "r4-test",
                "x",
                "ghost:1",
                1,
                "{}",
                "0" * 64,
                "1" * 64,
                1,
            ),
        )


def test_public_connect_handle_is_read_only(tmp_path: Path) -> None:
    ledger = fresh_ledger(tmp_path)
    ledger.append(valid_event(1, "goal.created", "objective", registry()))
    with ledger.read_connection() as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DROP TRIGGER IF EXISTS ledger_events_no_update")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE ledger_events SET payload = '{}' WHERE sequence = 1")


def test_no_arbitrary_mutation_api_exists() -> None:
    ledger = fresh_ledger(Path("."))
    public_names = {name for name in dir(ledger) if not name.startswith("_")}
    assert not {"update", "delete", "remove", "upsert"} & public_names
    assert "append" in public_names
    assert "read" in public_names
    assert "replay" in public_names
    assert "subscribe" in public_names


def test_chain_verification_detects_tampering(tmp_path: Path) -> None:
    ledger = fresh_ledger(tmp_path)
    ledger.append(valid_event(1, "goal.created", "objective", registry()))
    ledger.append(valid_event(2, "mission.created", "objective", registry()))
    report = ledger.verify_chain()
    assert report.valid is True
    assert report.sequence_count == 2
    with _write_connection(ledger) as connection:
        connection.execute(
            "INSERT INTO ledger_events ("
            "event_id, event_type, occurred_at, recorded_at, source_type, source_id, "
            "correlation_id, causation_id, idempotency_key, schema_version, kind_version, "
            "payload, payload_hash, previous_hash, header_hash"
            ") VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, 1, 1, ?, ?, NULL, ?)",
            (
                "evt-tampered",
                "goal.created",
                "2026-08-15T00:00:00Z",
                "2026-08-15T00:00:00Z",
                "r4-test",
                "x",
                "tampered:1",
                "{}",
                payload_hash({}),
                "0" * 64,
            ),
        )
    broken = ledger.verify_chain()
    assert broken.valid is False
    assert len(broken.problems) >= 1


def test_restart_safety_preserves_ledger(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    first = Ledger(path, registry=registry())
    first.append(valid_event(1, "goal.created", "objective", registry()))
    second = Ledger(path, registry=registry())
    assert len(second.replay()) == 1
    assert second.verify_chain().valid is True
    assert second.head_hash == first.head_hash


@settings(max_examples=25, deadline=None)
@given(
    indices=st.sets(st.integers(min_value=0, max_value=30), min_size=0, max_size=8).map(sorted),
    text=st.text(max_size=24),
)
def test_append_replay_determinism_property(
    tmp_path_factory: pytest.TempPathFactory, indices: list[int], text: str
) -> None:
    registry_used = registry()
    ledger = Ledger(tmp_path_factory.mktemp("ledger") / "kernel.db", registry=registry_used)
    events = [
        valid_event(index, KINDS[index % len(KINDS)], f"{text}-{index}", registry_used)
        for index in indices
    ]
    results = [ledger.append(event) for event in events]
    assert [result.sequence for result in results] == list(range(1, len(events) + 1))
    replayed = ledger.replay()
    assert [event.event_id for event in replayed] == [event.event_id for event in events]
    assert ledger.verify_chain().valid is True
    second = Ledger(tmp_path_factory.mktemp("ledger2") / "kernel.db", registry=registry_used)
    for event in events:
        second.append(event)
    assert [event.header_hash for event in second.replay()] == [
        event.header_hash for event in replayed
    ]
