from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jarvis_foundry.briefing_items import project_briefing_items
from jarvis_foundry.briefing_packet import build_briefing_packet
from jarvis_foundry.briefings import BriefingPlanner
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.decision_queue import build_owner_attention_queue
from jarvis_foundry.events import Event


def test_malformed_legacy_event_and_invalid_clock_fail_honestly_without_replay_crash(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "malformed.db")
    database.initialize()
    malformed = Event.create(
        "legacy.observation",
        {"summary": "this payload is replaced by an old malformed fixture"},
        source_type="legacy", source_id="old-fixture", idempotency_key="legacy-observation",
    )
    database.append(malformed)
    with database.connect() as connection:
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            ("[]", malformed.event_id),
        )

    items = project_briefing_items(database, now="not-a-clock", limit=20)
    assert items
    legacy_item = next(item for item in items if item.source_event_id == malformed.event_id)
    assert legacy_item.owner_action_required == "UNKNOWN"
    assert legacy_item.freshness == "UNKNOWN"

    snapshot = build_snapshot(database, now="2026-08-23T12:00:00Z")
    assert snapshot["source_of_truth"]["event_count"] == 1
    assert snapshot["result_traceability"]["status"] == "UNKNOWN"
    assert build_owner_attention_queue(database)["history_count"] >= 1
    assert build_briefing_packet(database, now=datetime(2026, 8, 23, tzinfo=timezone.utc))["schema"] == "v4.briefing-packet.v1"


def test_future_and_hostile_text_are_bounded_and_explicit(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "hostile.db")
    database.initialize()
    event = Event.create(
        "portfolio.unknown",
        {"question": "\x00" + "x" * 5000},
        source_type="test", source_id="hostile", idempotency_key="hostile-question",
        occurred_at="2099-01-01T00:00:00+00:00",
    )
    database.append(event)

    item = project_briefing_items(database, now="2026-08-23T12:00:00Z", limit=20)[0]
    assert item.freshness == "FUTURE"
    assert len(item.summary) <= 320
    assert "\x00" not in item.summary


def test_malformed_outbox_payload_is_not_replayed_as_truth(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "outbox.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO briefing_outbox(briefing_id, briefing_key, kind, period_key, status, "
            "payload_json, evidence_json, attempt_count, idempotency_key, last_event_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("briefing-malformed", "TWICE_DAILY:2099-01-01:AM", "TWICE_DAILY",
             "2099-01-01:AM", "PLANNED", "not-json", "{}", 0,
             "briefing-malformed-key", "legacy-event", "2026-08-23T12:00:00Z"),
        )

    result = BriefingPlanner(database).plan(
        "TWICE_DAILY", period_key="2099-01-01:AM",
        now=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    assert result["created"] is False
    assert result["payload"]["status"] == "UNKNOWN"
    assert "malformed" in result["payload"]["reason"]

