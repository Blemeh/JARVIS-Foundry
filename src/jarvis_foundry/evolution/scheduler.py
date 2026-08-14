"""V7-S7 — event-first scheduler: durable, restart-safe, budgeted.

All schedule state is rebuilt from the event stream (restart-safe); every
decision is ledger-recorded with the supervisor controller identity. The
scheduler is DISABLED by default (observe-only) until an explicit
enable event.
"""
from __future__ import annotations

import hashlib

import json
from dataclasses import dataclass, field
from typing import Any

from jarvis_foundry.clock import parse, utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER, EvolutionControlError,
)
from jarvis_foundry.supervisor.estop import EmergencyStop

SchedulerError = EvolutionControlError


@dataclass
class ScheduleItem:
    item_id: str
    priority: int = 5
    cooldown_seconds: float = 60.0
    budget_usd: float = 0.0
    deadline: str | None = None
    sequence: int = 0
    last_fired_at: str | None = None


@dataclass
class SchedulerState:
    enabled: bool = False
    items: list[ScheduleItem] = field(default_factory=list)
    suspended_reason: str | None = None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SchedulerError(message)


def _rebuild(database: FoundryDatabase) -> SchedulerState:
    state = SchedulerState()
    last_degraded = -1
    last_heartbeat = -1
    for _stream_index, event in enumerate(database.events()):
        if event.event_type == "scheduler.enabled":
            state.enabled = True
        elif event.event_type == "scheduler.disabled":
            state.enabled = False
        elif event.event_type == "scheduler.item_registered":
            try:
                payload = event.payload
                item = ScheduleItem(
                    item_id=payload["item_id"],
                    priority=int(payload.get("priority", 5)),
                    cooldown_seconds=float(payload.get("cooldown_seconds", 60.0)),
                    budget_usd=float(payload.get("budget_usd", 0.0)),
                    deadline=payload.get("deadline"),
                    sequence=_stream_index)
            except (KeyError, TypeError, ValueError):
                continue  # defensive: a malformed record never corrupts
            state.items = [i for i in state.items
                           if i.item_id != item.item_id]
            state.items.append(item)
        elif event.event_type == "scheduler.item_fired":
            try:
                item_id = event.payload["item_id"]
            except (KeyError, TypeError):
                continue
            for item in state.items:
                if item.item_id == item_id:
                    item.last_fired_at = event.payload.get("fired_at")
        elif event.event_type == "supervisor.degraded":
            last_degraded = _stream_index
        elif event.event_type == "supervisor.heartbeat":
            last_heartbeat = _stream_index
    # suspension: a degraded event newer than the last heartbeat suspends
    if last_degraded > last_heartbeat:
        state.suspended_reason = "degraded"
    return state


def is_scheduler_enabled(database: FoundryDatabase) -> bool:
    return _rebuild(database).enabled


def scheduler_state(database: FoundryDatabase) -> dict[str, Any]:
    state = _rebuild(database)
    return {
        "enabled": state.enabled,
        "suspended_reason": state.suspended_reason,
        "items": [{
            "item_id": i.item_id, "priority": i.priority,
            "cooldown_seconds": i.cooldown_seconds,
            "budget_usd": i.budget_usd, "deadline": i.deadline,
            "last_fired_at": i.last_fired_at,
        } for i in state.items],
    }


def _controller_gate(database: FoundryDatabase, controller: str) -> None:
    _require(controller == SUPERVISOR_CONTROLLER,
             "only the Supervisor can control the scheduler")
    _require(not EmergencyStop(database.path.parent / "EMERGENCY_STOP").active(),
             "STOP sentinel active")


def enable_scheduler(database: FoundryDatabase, *, controller: str,
                     source_type: str, source_id: str) -> str:
    _controller_gate(database, controller)
    event = Event.create(
        "scheduler.enabled", {"controller": controller},
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


def disable_scheduler(database: FoundryDatabase, *, controller: str,
                      source_type: str, source_id: str) -> str:
    _controller_gate(database, controller)
    event = Event.create(
        "scheduler.disabled", {"controller": controller},
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


def register_schedule_item(
        database: FoundryDatabase, *, item_id: str, priority: int = 5,
        cooldown_seconds: float = 60.0, budget_usd: float = 0.0,
        deadline: str | None = None, controller: str,
        source_type: str, source_id: str) -> str:
    _controller_gate(database, controller)
    payload = {
        "item_id": item_id, "priority": int(priority),
        "cooldown_seconds": float(cooldown_seconds),
        "budget_usd": float(budget_usd), "deadline": deadline,
        "controller": controller,
    }
    content = json.dumps(payload, sort_keys=True)
    event = Event.create(
        "scheduler.item_registered", payload,
        idempotency_key=f"scheduler.item_registered:{item_id}:"
                        f"{hashlib.sha256(content.encode('utf-8')).hexdigest()[:8]}",
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


def tick_scheduler(
        database: FoundryDatabase, *, controller: str,
        max_concurrent: int = 1, total_budget_usd: float | None = None,
        now_override: float | None = None,
        source_type: str, source_id: str) -> list[str]:
    _controller_gate(database, controller)
    state = _rebuild(database)
    _require(state.enabled, "the scheduler is disabled (observe-only)")

    now = _now_seconds() + (now_override if now_override is not None else 0.0)

    if state.suspended_reason:
        suspended = [e for e in database.events()
                     if e.event_type == "scheduler.suspended"]
        if not suspended or \
                suspended[-1].payload.get("reason") != state.suspended_reason:
            database.append(Event.create(
                "scheduler.suspended",
                {"reason": state.suspended_reason, "controller": controller},
                idempotency_key=f"scheduler.suspended:"
                                f"{state.suspended_reason}",
                source_type=source_type, source_id=source_id))
        database.append(Event.create(
            "scheduler.tick", {"fired": [], "deferred": [], "missed": [],
                               "suspended": state.suspended_reason,
                               "controller": controller},
            source_type=source_type, source_id=source_id))
        return []

    # eligible items, priority-ordered (stable by registration sequence)
    eligible: list[ScheduleItem] = []
    deferred: list[str] = []
    missed: list[str] = []
    for item in state.items:
        if item.last_fired_at is not None:
            last = parse(item.last_fired_at)
            if (now - _iso_seconds(last)) < item.cooldown_seconds:
                database.append(Event.create(
                    "scheduler.item_deferred",
                    {"item_id": item.item_id, "reason": "cooldown",
                     "controller": controller},
                    idempotency_key=f"scheduler.item_deferred:{item.item_id}:"
                                    f"cooldown:{item.last_fired_at}",
                    source_type=source_type, source_id=source_id))
                deferred.append(item.item_id)
                continue
        if item.deadline is not None:
            if _iso_seconds(parse(item.deadline)) < now:
                database.append(Event.create(
                    "scheduler.item_missed",
                    {"item_id": item.item_id, "deadline": item.deadline,
                     "controller": controller},
                    idempotency_key=f"scheduler.item_missed:{item.item_id}",
                    source_type=source_type, source_id=source_id))
                missed.append(item.item_id)
                continue
        eligible.append(item)
    eligible.sort(key=lambda i: (-i.priority, i.sequence))

    fired: list[str] = []
    spent = 0.0
    for item in eligible:
        if len(fired) >= max_concurrent:
            break
        if total_budget_usd is not None and \
                spent + item.budget_usd > total_budget_usd:
            database.append(Event.create(
                "scheduler.item_deferred",
                {"item_id": item.item_id, "reason": "budget",
                 "controller": controller},
                idempotency_key=f"scheduler.item_deferred:{item.item_id}:"
                                f"budget:{total_budget_usd}",
                source_type=source_type, source_id=source_id))
            deferred.append(item.item_id)
            continue
        fired_at = _iso_now(now)
        database.append(Event.create(
            "scheduler.item_fired",
            {"item_id": item.item_id, "priority": item.priority,
             "fired_at": fired_at, "controller": controller},
            idempotency_key=f"scheduler.item_fired:{item.item_id}:{fired_at}",
            source_type=source_type, source_id=source_id))
        spent += item.budget_usd
        fired.append(item.item_id)

    database.append(Event.create(
        "scheduler.tick",
        {"fired": fired, "deferred": deferred, "missed": missed,
         "controller": controller},
        source_type=source_type, source_id=source_id))
    return fired


def _now_seconds() -> float:
    return _iso_seconds(utc_now())


def _iso_seconds(dt) -> float:
    import calendar
    return calendar.timegm(dt.utctimetuple())


def _iso_now(epoch_seconds: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        epoch_seconds, tz=datetime.timezone.utc).isoformat()
