"""V3 slice-2 (WP10): monitored sources + mission-execution wiring.

Bounded, deterministic:
- register_monitor / disable_monitor: source registry with an on/off
  switch (polls on disabled monitors raise).
- poll_monitor: feeds items into the candidate pipeline
  (generate_candidate per item — repeat polls dedup via the canonical
  objective detection) and records the poll (last_poll_at + found
  count).
- mission completion wiring: a mission whose created_from is
  candidate:<id> auto-records the candidate outcome (prediction =
  objective; reality = the mission's outcome) and marks it EXECUTED —
  implemented in the mission.completed projection handler."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from .database import FoundryDatabase
from .events import Event, EventConflict
from .discovery_candidates import generate_candidate


def register_monitor(db: FoundryDatabase, mission_id: str, source_kind: str,
                     source_ref: str, poll_interval_s: int = 300) -> str:
    if not str(source_kind or "").strip() or not str(source_ref or "").strip():
        raise ValueError("source_kind and source_ref are required")
    if int(poll_interval_s) < 1:
        raise ValueError("poll_interval_s must be positive")
    monitor_id = f"mon-{uuid.uuid4().hex[:12]}"
    db.append(Event.create(
        "discovery.monitor_registered",
        {"monitor_id": monitor_id, "mission_id": mission_id,
         "source_kind": source_kind, "source_ref": source_ref,
         "poll_interval_s": poll_interval_s},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.monitor_registered:{monitor_id}"))
    return monitor_id


def ensure_monitor(
    db: FoundryDatabase,
    mission_id: str,
    source_kind: str,
    source_ref: str,
    poll_interval_s: int = 300,
) -> str:
    """Return the existing source monitor or register it once.

    The schema's unique ``(source_kind, source_ref)`` index remains the race
    authority.  A2 uses this helper so restart never creates a second monitor
    for one configured source.
    """
    source_kind = str(source_kind or "").strip()
    source_ref = str(source_ref or "").strip()
    with db.connect() as connection:
        row = connection.execute(
            "SELECT monitor_id FROM monitors "
            "WHERE source_kind = ? AND source_ref = ? LIMIT 1",
            (source_kind, source_ref),
        ).fetchone()
    if row is not None:
        return str(row[0])
    return register_monitor(
        db, mission_id, source_kind, source_ref,
        poll_interval_s=max(1, int(poll_interval_s)),
    )


def _as_utc(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def monitor_due(
    db: FoundryDatabase,
    monitor_id: str,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Read the durable monitor schedule and decide whether it is due."""
    current = _as_utc(now)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT enabled, poll_interval_s, last_poll_at FROM monitors "
            "WHERE monitor_id = ?",
            (monitor_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown monitor {monitor_id}")
    interval = max(1, int(row[1]))
    if int(row[0]) != 1:
        return {"due": False, "enabled": False, "next_due_at": None}
    last_poll = None
    if row[2]:
        try:
            last_poll = _as_utc(str(row[2]))
        except ValueError:
            # Malformed legacy time cannot suppress observation forever.
            last_poll = None
    next_due = current if last_poll is None else last_poll + timedelta(seconds=interval)
    return {
        "due": last_poll is None or current >= next_due,
        "enabled": True,
        "last_poll_at": row[2],
        "poll_interval_s": interval,
        "next_due_at": next_due.isoformat().replace("+00:00", "Z"),
    }


def record_monitor_attempt(
    db: FoundryDatabase,
    monitor_id: str,
    *,
    poll_key: str,
    items_seen: int,
    candidates_found: int,
    additional_events: Sequence[Event] = (),
) -> dict[str, Any]:
    """Record one completed/failed due attempt without feeding legacy work.

    Source adapters perform A2 fingerprint-first ingest themselves.  This
    helper advances only the existing monitor clock and preserves the familiar
    ``discovery.monitor_polled`` projection without invoking candidate approval
    or any execution path.
    """
    poll_key = str(poll_key or "").strip()
    if not poll_key:
        raise ValueError("poll_key is required")
    event_key = f"discovery.monitor_polled:{monitor_id}:{poll_key}"
    payload = {
        "monitor_id": monitor_id,
        "items_seen": max(0, int(items_seen)),
        "candidates_found": max(0, int(candidates_found)),
    }
    with db.transaction() as connection:
        row = connection.execute(
            "SELECT enabled FROM monitors WHERE monitor_id = ?",
            (monitor_id,),
        ).fetchone()
        if row is None or int(row[0]) != 1:
            raise ValueError(
                f"record_monitor_attempt requires an ENABLED monitor ({monitor_id})"
            )
        results = db.append_events_on_connection(connection, [Event.create(
            "discovery.monitor_polled",
            payload,
            source_type="supervisor",
            source_id="v11-a2-observation",
            idempotency_key=event_key,
        ), *additional_events])
        result = results[0]
    return {**payload, "recorded": bool(result[1])}


def poll_monitor(db: FoundryDatabase, monitor_id: str,
                 items: list[dict[str, Any]], *,
                 poll_key: str | None = None) -> dict[str, Any]:
    """Feed items into the candidate pipeline.

    Repeat polls dedup via the pipeline's canonicalized objective
    detection (identical re-delivery finds 0 new candidates; the
    pipeline never records duplicate rows as selectable work — V4-S5
    round 2, deleg_71efdc9e FAIL findings). Items whose objective
    canonicalizes empty are skipped (never fabricated); ``poll_key``
    makes a crash-retried poll of the same cycle idempotent (the loop
    passes its loop_id — distinct cycles poll distinctly).
    """
    poll_event_key = (f"discovery.monitor_polled:{monitor_id}:"
                      f"{poll_key or uuid.uuid4().hex[:8]}")
    with db.transaction() as connection:
        row = connection.execute(
            "SELECT enabled FROM monitors WHERE monitor_id = ?",
            (monitor_id,)).fetchone()
        if row is None or row[0] != 1:
            raise ValueError(
                f"poll_monitor requires an ENABLED monitor ({monitor_id}) — "
                f"rolled back")
        if poll_key:
            existing = connection.execute(
                "SELECT event_type, payload_json FROM events "
                "WHERE idempotency_key = ?",
                (poll_event_key,),
            ).fetchone()
            if existing:
                if existing[0] != "discovery.monitor_polled":
                    raise EventConflict(
                        f"idempotency conflict for {poll_event_key}")
                payload = json.loads(existing[1])
                if payload.get("monitor_id") != monitor_id:
                    raise EventConflict(
                        f"idempotency conflict for {poll_event_key}")
                return {
                    "monitor_id": monitor_id,
                    "items_seen": payload["items_seen"],
                    "candidates_found": payload["candidates_found"],
                }
        found = 0
        for item in items:
            objective = item.get("objective")
            if not objective or not str(objective).strip():
                continue  # empty objectives never fabricate candidates
            from .discovery_candidates import covered_objective
            if covered_objective(db, str(objective), connection=connection) is not None:
                continue  # already covered — polls never duplicate work
            result = generate_candidate(
                db, item.get("mission_id", "mission-1"),
                item.get("source", "monitor-feed"),
                objective, item.get("evidence"), connection=connection)
            if result["created"]:
                found += 1
        db.append_events_on_connection(connection, [Event.create(
            "discovery.monitor_polled",
            {"monitor_id": monitor_id, "items_seen": len(items),
             "candidates_found": found},
            source_type="supervisor", source_id="wp10-discovery",
            idempotency_key=poll_event_key)])
        return {"monitor_id": monitor_id, "items_seen": len(items),
                "candidates_found": found}


def disable_monitor(db: FoundryDatabase, monitor_id: str, reason: str) -> None:
    db.append(Event.create(
        "discovery.monitor_disabled",
        {"monitor_id": monitor_id, "reason": reason},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.monitor_disabled:{monitor_id}"))
