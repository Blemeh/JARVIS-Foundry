"""V4-4/V4-5 deterministic briefing item and window proof."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.briefing_items import project_briefing_items
from jarvis_foundry.briefings import BriefingPlanner
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _event(
    database: FoundryDatabase,
    event_type: str,
    payload: dict,
    key: str,
    *,
    occurred_at: str = "2026-08-23T08:00:00+00:00",
) -> None:
    database.append(Event.create(
        event_type,
        payload,
        source_type="test",
        source_id="briefing-test",
        idempotency_key=key,
        occurred_at=occurred_at,
    ))


def test_event_projection_has_traceable_item_contract(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _event(
        database,
        "executive.materialization_blocked",
        {
            "decision_id": "decision-1",
            "candidate_id": "candidate-1",
            "reason": "provider dependency unavailable",
            "evidence_refs": ["evidence-1"],
        },
        "materialization-blocked-1",
    )
    _event(
        database,
        "knowledge.note_recorded",
        {"mission_id": "mission-1", "content": "x" * 500},
        "knowledge-1",
        occurred_at="2026-08-10T08:00:00+00:00",
    )

    items = project_briefing_items(
        database,
        now="2026-08-23T09:00:00+00:00",
        limit=20,
    )

    blocked = next(item for item in items if item.source_event_type == "executive.materialization_blocked")
    late = next(item for item in items if item.source_event_type == "knowledge.note_recorded")
    assert blocked.category == "BLOCKER"
    assert blocked.owner_action_required == "BLOCKED_EXTERNAL_DEPENDENCY"
    assert blocked.entity_kind == "decision"
    assert blocked.entity_id == "decision-1"
    assert blocked.mission_id is None
    assert blocked.source_event_id in blocked.evidence_refs
    assert "evidence-1" in blocked.evidence_refs
    assert late.freshness == "LATE"
    assert len(late.summary) <= 320


def test_twice_daily_selection_is_restart_safe_and_cross_window_deduped(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _event(
        database,
        "supervisor.degraded",
        {"reason": "gateway unavailable"},
        "degraded-1",
    )
    planner = BriefingPlanner(database)
    first = planner.plan(
        "TWICE_DAILY",
        period_key="2026-08-23:AM",
        now=__import__("datetime").datetime.fromisoformat("2026-08-23T09:00:00+00:00"),
    )
    assert first["created"] is True
    assert first["payload"]["selection"]["selected_count"] == 1
    assert first["payload"]["items"][0]["source_event_type"] == "supervisor.degraded"

    database.rebuild_projections()
    retry = planner.plan(
        "TWICE_DAILY",
        period_key="2026-08-23:AM",
        now=__import__("datetime").datetime.fromisoformat("2026-08-23T09:01:00+00:00"),
    )
    assert retry["created"] is False
    assert retry["status"] == "PLANNED"

    second_window = planner.plan(
        "TWICE_DAILY",
        period_key="2026-08-23:PM",
        now=__import__("datetime").datetime.fromisoformat("2026-08-23T18:00:00+00:00"),
    )
    assert second_window["created"] is False
    assert second_window["suppressed"] is True
    assert second_window["reason"] == "ZERO_CHANGE"

    _event(
        database,
        "executive.decision_recorded",
        {"decision_id": "decision-2", "decision": "WAIT", "reason": "no eligible candidate"},
        "decision-2",
        occurred_at="2026-08-23T18:01:00+00:00",
    )
    changed = planner.plan(
        "TWICE_DAILY",
        period_key="2026-08-24:AM",
        now=__import__("datetime").datetime.fromisoformat("2026-08-24T09:00:00+00:00"),
    )
    assert changed["created"] is True
    assert changed["payload"]["selection"]["selected_count"] == 1
    assert changed["payload"]["items"][0]["source_event_id"]


def test_exception_window_suppresses_informational_noise(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _event(
        database,
        "knowledge.note_recorded",
        {"mission_id": "mission-1", "content": "routine note"},
        "knowledge-routine",
    )
    planner = BriefingPlanner(database)
    result = planner.plan(
        "EXCEPTION",
        period_key="provider:healthy",
        now=__import__("datetime").datetime.fromisoformat("2026-08-23T09:00:00+00:00"),
    )
    assert result["created"] is False
    assert result["reason"] == "NO_NEW_EXCEPTION"
