"""Materialization lookup: SQL-filtered newest-first match at scale.

Proves ``_materialization_for_decision`` returns results identical to the
legacy unbounded ``reversed(database.events())`` scan while only
materializing the ``portfolio.selection_materialized`` slice of the ledger.
"""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.portfolio_bridge import (
    _materialization_for_decision,
)

MATERIALIZED = "portfolio.selection_materialized"
NOISE_TYPES = (
    "worker_loop.cycle_started",
    "capability.observed",
    "portfolio.repair_retired",
)

SCALE_EVENTS = 2000


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "materialization.db")
    database.initialize()
    return database


def _materialized_event(
    decision_id: str, mission_id: str, key_suffix: str
) -> Event:
    return Event.create(
        MATERIALIZED,
        {
            "decision_id": decision_id,
            "item_id": f"item-{decision_id}",
            "kind": "MAINTENANCE",
            "mission_id": mission_id,
        },
        source_type="supervisor",
        source_id="materialization-test",
        idempotency_key=f"portfolio.selection_materialized:{decision_id}:{key_suffix}",
    )


def _noise_event(index: int) -> Event:
    return Event.create(
        NOISE_TYPES[index % len(NOISE_TYPES)],
        {"index": index},
        source_type="supervisor",
        source_id="materialization-test",
        idempotency_key=f"materialization-noise:{index}",
    )


def _legacy_lookup(database: FoundryDatabase, decision_id: str) -> str | None:
    for event in reversed(database.events()):
        if event.event_type == MATERIALIZED and str(
            event.payload.get("decision_id") or ""
        ) == decision_id:
            mission_id = str(event.payload.get("mission_id") or "")
            return mission_id or None
    return None


def _seed_scale_ledger(database: FoundryDatabase) -> list[str]:
    """Write SCALE_EVENTS mixed events; return probed decision ids."""
    decisions = [f"decision-{index:04d}" for index in range(10)]
    pending: list[Event] = []
    materialized_count = 0
    for index in range(SCALE_EVENTS):
        if index % 200 == 50:
            decision_id = decisions[(index // 200) % len(decisions)]
            pending.append(
                _materialized_event(
                    decision_id,
                    f"mission-{decision_id}-{index}",
                    key_suffix=str(index),
                )
            )
            materialized_count += 1
        else:
            pending.append(_noise_event(index))
    # Newest decision lands near the tail so a full scan must walk far.
    pending.append(
        _materialized_event(decisions[0], "mission-newest", key_suffix="tail")
    )
    database.append_events(pending)
    assert materialized_count > 0
    return decisions


def test_lookup_matches_legacy_scan_for_every_probed_decision(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    decisions = _seed_scale_ledger(database)

    for decision_id in [*decisions, "decision-unknown"]:
        assert _materialization_for_decision(
            database, decision_id
        ) == _legacy_lookup(database, decision_id)


def test_lookup_preserves_newest_first_match(tmp_path: Path) -> None:
    database = _db(tmp_path)
    database.append(_materialized_event("decision-x", "mission-old", key_suffix="old"))
    database.append(_noise_event(1))
    database.append(_materialized_event("decision-x", "mission-new", key_suffix="new"))

    assert _materialization_for_decision(database, "decision-x") == "mission-new"
    assert _legacy_lookup(database, "decision-x") == "mission-new"


def test_lookup_scans_bounded_slice(tmp_path: Path) -> None:
    database = _db(tmp_path)
    _seed_scale_ledger(database)

    calls: list[dict[str, object]] = []
    original_events = FoundryDatabase.events

    def recording_events(self, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(dict(kwargs))
        return original_events(self, **kwargs)

    FoundryDatabase.events = recording_events  # type: ignore[method-assign]
    try:
        assert (
            _materialization_for_decision(database, "decision-0000") is not None
        )
    finally:
        FoundryDatabase.events = original_events  # type: ignore[method-assign]

    assert calls, "lookup must read through FoundryDatabase.events"
    for call in calls:
        assert call.get("event_types") == (MATERIALIZED,)
        assert call.get("newest_first") is True
        assert call.get("event_type_prefixes") is None
        assert call.get("exclude_event_types") is None

    bounded = database.events(event_types=(MATERIALIZED,))
    assert 0 < len(bounded) < SCALE_EVENTS
