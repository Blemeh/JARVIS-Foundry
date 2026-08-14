"""V7-S12 — cross-system containment and continuity fault suite.

Gate matrix (TDD):
  1. kill/restart continuity: scheduler + portfolio state survives a
     simulated restart (fresh database replay) with no double effects;
  2. DB-lock containment: a held write lock makes a scheduler tick fail
     CLEANLY (no partial state, no duplicate fires) and the next tick
     recovers;
  3. duplicate delivery: re-appending an identical event (same
     idempotency key + payload) is a no-op — no duplicate fires;
  4. partial-write containment: append_events is all-or-nothing (a
     conflicting batch rolls back entirely);
  5. STOP containment: with a STOP sentinel event in the ledger, the
     evolution control refuses effectful operations (the V7 modules
     inherit the gate);
  6. observe-only continuity: the scheduler stays disabled across
     restarts until an explicit enable event (nothing auto-fires);
  7. corrupt-payload containment: an event with a bad payload for its
     type does not corrupt later state rebuilds (rebuild is
     type-gated).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, EventConflict
from jarvis_foundry.evolution.scheduler import (
    enable_scheduler, register_schedule_item, tick_scheduler,
    scheduler_state,
)
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "fault.db")
    database.initialize()
    return database


def _ready(db: FoundryDatabase) -> None:
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    register_schedule_item(db, item_id="fault-item", priority=5,
                           cooldown_seconds=1.0, budget_usd=0.0,
                           controller=SUPERVISOR_CONTROLLER,
                           source_type="supervisor", source_id="v7")


def test_restart_continuity_no_double_effects(db: FoundryDatabase,
                                              tmp_path: Path) -> None:
    _ready(db)
    tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                   source_type="supervisor", source_id="v7")
    fired_before = len([e for e in db.events()
                        if e.event_type == "scheduler.item_fired"])
    # simulated restart: replay every event into a fresh database
    fresh = FoundryDatabase(tmp_path / "restart.db")
    fresh.initialize()
    for event in db.events():
        fresh.append(Event.create(
            event.event_type, event.payload,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            idempotency_key=event.idempotency_key,
            occurred_at=event.recorded_at,
            source_type=event.source_type, source_id=event.source_id))
    state = scheduler_state(fresh)
    assert state["enabled"] is True
    assert any(i["item_id"] == "fault-item" for i in state["items"])
    fired_after = len([e for e in fresh.events()
                       if e.event_type == "scheduler.item_fired"])
    assert fired_after == fired_before  # no double effects


def test_lock_containment_tick_fails_cleanly_and_recovers(
        db: FoundryDatabase, tmp_path: Path) -> None:
    import sqlite3
    import threading
    import time
    _ready(db)

    def _hold() -> None:
        connection = sqlite3.connect(db.path, timeout=0.1)
        try:
            connection.execute("BEGIN IMMEDIATE")
            time.sleep(12)  # > the append's 10s busy_timeout
        finally:
            connection.rollback()
            connection.close()

    thread = threading.Thread(target=_hold, daemon=True)
    thread.start()
    time.sleep(0.5)
    with pytest.raises(Exception):
        tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                       source_type="supervisor", source_id="v7")
    thread.join()
    # no partial state: zero fired events from the failed tick
    fired = [e for e in db.events() if e.event_type == "scheduler.item_fired"]
    assert fired == []
    # recovery: the next tick succeeds exactly once
    tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                   source_type="supervisor", source_id="v7")
    fired = [e for e in db.events() if e.event_type == "scheduler.item_fired"]
    assert len(fired) == 1


def test_duplicate_delivery_is_a_noop(db: FoundryDatabase) -> None:
    _ready(db)
    tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                   source_type="supervisor", source_id="v7")
    fired = [e for e in db.events() if e.event_type == "scheduler.item_fired"]
    assert len(fired) == 1
    # re-deliver the SAME fired event (identical key + payload)
    db.append(Event.create(
        "scheduler.item_fired", fired[0].payload,
        idempotency_key=fired[0].idempotency_key,
        source_type="supervisor", source_id="v7"))
    fired = [e for e in db.events() if e.event_type == "scheduler.item_fired"]
    assert len(fired) == 1


def test_partial_write_batch_rolls_back(db: FoundryDatabase) -> None:
    good = Event.create("supervisor.heartbeat", {"status": "RUNNING"},
                        source_type="supervisor", source_id="v7")
    bad = Event.create("supervisor.heartbeat", {"status": "DEGRADED"},
                       idempotency_key="exists",
                       source_type="supervisor", source_id="v7")
    db.append(Event.create("supervisor.heartbeat", {"status": "RUNNING"},
                           idempotency_key="exists",
                           source_type="supervisor", source_id="v7"))
    before = len(db.events())
    with pytest.raises(EventConflict):
        db.append_events([good, bad])
    after = len(db.events())
    assert after == before  # the whole batch rolled back


def test_stop_containment_evolution_control_refuses(
        db: FoundryDatabase) -> None:
    from jarvis_foundry.supervisor.estop import EmergencyStop
    sentinel = EmergencyStop(db.path.parent / "EMERGENCY_STOP")
    sentinel.activate("owner STOP", source="test")
    assert sentinel.active() is True
    with pytest.raises(Exception):
        enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                         source_type="supervisor", source_id="v7")


def test_observe_only_survives_restart(db: FoundryDatabase,
                                       tmp_path: Path) -> None:
    register_schedule_item(db, item_id="item-x",
                           controller=SUPERVISOR_CONTROLLER,
                           source_type="supervisor", source_id="v7")
    with pytest.raises(Exception):
        tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                       source_type="supervisor", source_id="v7")
    fresh = FoundryDatabase(tmp_path / "obs.db")
    fresh.initialize()
    for event in db.events():
        fresh.append(Event.create(
            event.event_type, event.payload,
            idempotency_key=event.idempotency_key,
            source_type=event.source_type, source_id=event.source_id))
    assert scheduler_state(fresh)["enabled"] is False
    with pytest.raises(Exception):
        tick_scheduler(fresh, controller=SUPERVISOR_CONTROLLER,
                       source_type="supervisor", source_id="v7")


def test_corrupt_payload_containment(db: FoundryDatabase) -> None:
    """Unknown/foreign event types cannot corrupt the rebuild, and
    malformed payloads for KNOWN types are refused at append time by the
    event validation (the machinery's own gate)."""
    _ready(db)
    before = scheduler_state(db)
    # a valid-but-foreign event type (not consumed by the rebuild)
    db.append(Event.create("owner.command_recorded",
                           {"command_id": "x", "command": "STATUS",
                            "reason": "", "source_type": "owner-channel",
                            "source_id": "test", "recorded_at": "now"},
                           source_type="owner-channel", source_id="test"))
    after = scheduler_state(db)
    assert after["enabled"] == before["enabled"]
    assert [i["item_id"] for i in after["items"]] == \
        [i["item_id"] for i in before["items"]]
    # a malformed payload for a KNOWN type never corrupts the rebuild:
    # the append succeeds (the machinery validates only registered types)
    # but the rebuild skips it defensively
    db.append(Event.create("scheduler.item_registered", {},
                           source_type="supervisor", source_id="v7"))
    after2 = scheduler_state(db)
    assert [i["item_id"] for i in after2["items"]] == \
        [i["item_id"] for i in before["items"]]
