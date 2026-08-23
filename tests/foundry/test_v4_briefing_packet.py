from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jarvis_foundry.briefing_packet import (
    PACKET_SCHEMA,
    build_briefing_packet,
    export_briefing_packet,
    serialize_briefing_packet,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def test_structured_packet_is_stable_local_export_and_does_not_write_outbox(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "packet.db")
    database.initialize()
    database.append(Event.create(
        "executive.decision_recorded",
        {
            "decision_id": "decision-packet", "decision": "SELECT",
            "candidate_id": "candidate-packet", "options": ["inspect", "wait"],
            "reason": "owner review needed",
        },
        source_type="test", source_id="packet", idempotency_key="decision-packet",
    ))
    before = len(database.events())
    clock = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)

    packet = build_briefing_packet(database, now=clock, period_key="2026-08-23:AM")
    same_packet = build_briefing_packet(database, now=clock, period_key="2026-08-23:AM")

    assert packet["schema"] == PACKET_SCHEMA
    assert packet["window"] == same_packet["window"]
    assert packet["content_digest"] == same_packet["content_digest"]
    assert packet["delivery_eligibility"]["local_export"]["status"] == "ELIGIBLE"
    assert packet["delivery_eligibility"]["external_sinks"]["status"] == "NOT_IMPLEMENTED"
    assert serialize_briefing_packet(packet) == serialize_briefing_packet(same_packet)
    assert len(database.events()) == before

    destination = export_briefing_packet(packet, tmp_path / "exports" / "briefing.json")
    assert destination.read_text(encoding="utf-8") == serialize_briefing_packet(packet)


def test_packet_rejects_unknown_schema_and_kind(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "packet-errors.db")
    database.initialize()
    try:
        build_briefing_packet(database, kind="VOICE")
    except ValueError as error:
        assert "unsupported briefing packet kind" in str(error)
    else:
        raise AssertionError("unknown packet kind was accepted")
    try:
        serialize_briefing_packet({"schema": "v0"})
    except ValueError as error:
        assert "unsupported briefing packet schema" in str(error)
    else:
        raise AssertionError("unknown packet schema was serialized")

