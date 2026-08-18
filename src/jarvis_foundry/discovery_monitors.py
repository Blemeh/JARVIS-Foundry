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
from typing import Any

from .database import FoundryDatabase
from .events import Event, EventConflict
from .discovery_candidates import generate_candidate


def register_monitor(db: FoundryDatabase, mission_id: str, source_kind: str,
                     source_ref: str, poll_interval_s: int = 300) -> str:
    monitor_id = f"mon-{uuid.uuid4().hex[:12]}"
    db.append(Event.create(
        "discovery.monitor_registered",
        {"monitor_id": monitor_id, "mission_id": mission_id,
         "source_kind": source_kind, "source_ref": source_ref,
         "poll_interval_s": poll_interval_s},
        source_type="supervisor", source_id="wp10-discovery",
        idempotency_key=f"discovery.monitor_registered:{monitor_id}"))
    return monitor_id


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
