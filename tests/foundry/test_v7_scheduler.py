"""V7-S7 — event-first scheduler: durable, restart-safe, budgeted.

Gate matrix (TDD):
  1. schedules are ledger-recorded and rebuild from the event stream
     (restart-safe: a fresh database replay yields the same schedule);
  2. default state is DISABLED (observe-only) until an enable event;
  3. budgets: per-item budget cap + total budget cap block due items;
  4. cooldowns: a recently fired item is deferred (honest WAITING);
  5. concurrency caps: at most N items due per tick;
  6. dedup: identical item keys fire once per window;
  7. backpressure: a degraded ledger (supervisor.degraded event) suspends
     firing (anomaly suspension) and records the suspension;
  8. priorities + deadlines: higher-priority items fire first; expired
     deadlines are recorded as missed (never fired late);
  9. tick is idempotent (repeat ticks produce no duplicate fire events);
 10. only the Supervisor controller can enable/disable/tick.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.evolution.scheduler import (
    SchedulerError, enable_scheduler, disable_scheduler,
    is_scheduler_enabled, register_schedule_item, tick_scheduler,
    scheduler_state,
)
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "sched.db")
    database.initialize()
    return database


def _events(db: FoundryDatabase, event_type: str) -> list[Event]:
    return [e for e in db.events() if e.event_type == event_type]


def _register(db: FoundryDatabase, item_id: str, *, priority: int = 5,
              cooldown_seconds: float = 60.0, budget_usd: float = 1.0,
              deadline: str | None = None, controller: str = "supervisor") -> str:
    return register_schedule_item(
        db, item_id=item_id, priority=priority,
        cooldown_seconds=cooldown_seconds, budget_usd=budget_usd,
        deadline=deadline, controller=controller,
        source_type="supervisor", source_id="v7")


def test_scheduler_defaults_to_disabled_observe_only(
        db: FoundryDatabase) -> None:
    assert is_scheduler_enabled(db) is False
    state = scheduler_state(db)
    assert state["enabled"] is False
    assert state["items"] == []


def test_enable_requires_supervisor_controller(db: FoundryDatabase) -> None:
    with pytest.raises(SchedulerError):
        enable_scheduler(db, controller="candidate-1",
                         source_type="supervisor", source_id="v7")
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    assert is_scheduler_enabled(db) is True
    # ledger-recorded
    assert len(_events(db, "scheduler.enabled")) == 1


def test_schedule_items_are_recorded_and_rebuilt_from_events(
        db: FoundryDatabase, tmp_path: Path) -> None:
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    _register(db, "item-a", priority=1)
    _register(db, "item-b", priority=9)
    state = scheduler_state(db)
    assert {i["item_id"] for i in state["items"]} == {"item-a", "item-b"}
    # restart-safety: replay the same events into a fresh database
    fresh = FoundryDatabase(tmp_path / "replay.db")
    fresh.initialize()
    for event in db.events():
        fresh.append(Event.create(
            event.event_type, event.payload,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            idempotency_key=event.idempotency_key,
            occurred_at=event.recorded_at,
            source_type=event.source_type, source_id=event.source_id))
    fresh_state = scheduler_state(fresh)
    assert fresh_state["enabled"] is True
    assert {i["item_id"] for i in fresh_state["items"]} == {
        "item-a", "item-b"}


def test_tick_requires_supervisor_controller_and_enabled(
        db: FoundryDatabase) -> None:
    with pytest.raises(SchedulerError):
        tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                       source_type="supervisor", source_id="v7")
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    with pytest.raises(SchedulerError):
        tick_scheduler(db, controller="candidate-1",
                       source_type="supervisor", source_id="v7")


def test_tick_fires_high_priority_first_and_respects_concurrency_cap(
        db: FoundryDatabase) -> None:
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    for i in range(5):
        _register(db, f"item-{i}", priority=i)
    fired = tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                           max_concurrent=2,
                           source_type="supervisor", source_id="v7")
    # items 4 (priority 9) and 3 (priority 8) fire first; cap = 2
    assert fired == ["item-4", "item-3"]
    fired_events = _events(db, "scheduler.item_fired")
    assert [e.payload["item_id"] for e in fired_events] == ["item-4", "item-3"]


def test_cooldown_defers_item_with_honest_waiting(
        db: FoundryDatabase) -> None:
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    _register(db, "item-a", cooldown_seconds=60.0)
    first = tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                           source_type="supervisor", source_id="v7")
    assert first == ["item-a"]
    second = tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                            now_override=10.0,
                            source_type="supervisor", source_id="v7")
    assert second == []  # within cooldown -> deferred (honest WAITING)
    deferred = _events(db, "scheduler.item_deferred")
    assert deferred and deferred[-1].payload["item_id"] == "item-a"
    third = tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                           now_override=61.0,
                           source_type="supervisor", source_id="v7")
    assert third == ["item-a"]


def test_budget_cap_blocks_due_item(db: FoundryDatabase) -> None:
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    _register(db, "item-a", budget_usd=5.0)
    _register(db, "item-b", budget_usd=3.0)
    fired = tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                           max_concurrent=2, total_budget_usd=6.0,
                           source_type="supervisor", source_id="v7")
    # item-a (5.0) fits, item-b (3.0) would exceed 6.0 -> deferred
    assert fired == ["item-a"]
    blocked = [e for e in _events(db, "scheduler.item_deferred")
               if e.payload.get("reason") == "budget"]
    assert blocked and blocked[-1].payload["item_id"] == "item-b"


def test_deadline_expiry_marks_missed_never_fires_late(
        db: FoundryDatabase) -> None:
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    _register(db, "late-item", deadline="2026-08-01T00:00:00Z")
    fired = tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                           now_override=1_800_000.0,
                           source_type="supervisor", source_id="v7")
    assert fired == []
    missed = _events(db, "scheduler.item_missed")
    assert missed and missed[-1].payload["item_id"] == "late-item"


def test_backpressure_anomaly_suspension(db: FoundryDatabase) -> None:
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    _register(db, "item-a")
    # a degraded event (the live-ledger incident class) suspends firing
    db.append(Event.create(
        "supervisor.degraded",
        {"reason": "heartbeat_failure",
         "error_class": "OperationalError", "error": "database is locked",
         "consecutive_failures": 1},
        source_type="supervisor", source_id="v7"))
    fired = tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                           source_type="supervisor", source_id="v7")
    assert fired == []
    suspended = _events(db, "scheduler.suspended")
    assert suspended and suspended[-1].payload["reason"] == "degraded"
    # recovery: a fresh RUNNING heartbeat clears the suspension
    db.append(Event.create("supervisor.heartbeat", {"status": "RUNNING"},
                           source_type="supervisor", source_id="v7"))
    fired = tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                           source_type="supervisor", source_id="v7")
    assert fired == ["item-a"]


def test_tick_is_idempotent_no_duplicate_fires(db: FoundryDatabase) -> None:
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    _register(db, "item-a", cooldown_seconds=1.0)
    tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                   source_type="supervisor", source_id="v7")
    tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                   source_type="supervisor", source_id="v7")
    tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                   source_type="supervisor", source_id="v7")
    assert len(_events(db, "scheduler.item_fired")) == 1


def test_disable_records_and_blocks_firing(db: FoundryDatabase) -> None:
    enable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                     source_type="supervisor", source_id="v7")
    _register(db, "item-a")
    disable_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                      source_type="supervisor", source_id="v7")
    assert is_scheduler_enabled(db) is False
    with pytest.raises(SchedulerError):
        tick_scheduler(db, controller=SUPERVISOR_CONTROLLER,
                       source_type="supervisor", source_id="v7")
    with pytest.raises(SchedulerError):
        disable_scheduler(db, controller="candidate-1",
                          source_type="supervisor", source_id="v7")
