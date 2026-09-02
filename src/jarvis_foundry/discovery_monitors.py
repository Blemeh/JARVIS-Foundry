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

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .database import FoundryDatabase
from .events import Event, EventConflict, canonical_json
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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def configure_monitor(
    db: FoundryDatabase,
    *,
    source_id: str,
    source_kind: str,
    source_identity: str,
    poll_interval_s: int,
    config: Mapping[str, Any] | None = None,
    enabled: bool = True,
    mission_id: str = "v11-continuous-observation",
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Persist one watch source on the existing monitor authority.

    Configuration events are content-addressed, so replaying unchanged YAML
    produces no durable work. A changed configuration is due immediately and
    keeps all prior attempts/history in the ledger.
    """
    source_id = str(source_id or "").strip()
    source_kind = str(source_kind or "").strip()
    source_identity = str(source_identity or "").strip()
    if not source_id or not source_kind or not source_identity:
        raise ValueError("source_id, source_kind, and source_identity are required")
    interval = int(poll_interval_s)
    if interval < 1 or interval > 86_400:
        raise ValueError("poll_interval_s must be between 1 and 86400")
    config_payload = dict(config or {})
    for key in config_payload:
        normalized = str(key).lower()
        if (any(token in normalized for token in (
                "password", "secret", "token", "authorization", "cookie"))
                and not normalized.endswith(("_ref", "_path"))):
            raise ValueError(
                f"watch source config must reference, not persist, secret field {key!r}")
    material = {
        "source_id": source_id,
        "source_kind": source_kind,
        "source_identity": source_identity,
        "poll_interval_s": interval,
        "config": config_payload,
        "enabled": bool(enabled),
    }
    config_hash = "sha256:" + hashlib.sha256(
        canonical_json(material).encode("utf-8")
    ).hexdigest()
    with db.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT monitor_id, source_kind, source_ref, config_hash, enabled "
            "FROM monitors "
            "WHERE source_id = ? OR (source_kind = ? AND source_ref = ?) "
            "ORDER BY CASE WHEN source_id = ? THEN 0 ELSE 1 END LIMIT 1",
            (source_id, source_kind, source_identity, source_id),
        ).fetchone()
    monitor_id = str(row[0]) if row is not None else ensure_monitor(
        db, mission_id, source_kind, source_identity, interval,
    )
    if (row is not None and str(row[3] or "") == config_hash
            and bool(row[4]) == bool(enabled)):
        return db.row("monitors", "monitor_id", monitor_id) or material
    current = _as_utc(now)
    payload = {
        **material,
        "monitor_id": monitor_id,
        "config_hash": config_hash,
        "next_due_at": _iso(current) if enabled else None,
    }
    db.append(Event.create(
        "discovery.monitor_configured",
        payload,
        source_type="supervisor",
        source_id="v11-continuous-observation",
        idempotency_key=f"discovery.monitor_configured:{monitor_id}:{config_hash}",
    ))
    return db.row("monitors", "monitor_id", monitor_id) or payload


def configured_monitors(
    db: FoundryDatabase,
    *,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    clauses = ["source_id IS NOT NULL"]
    if enabled_only:
        clauses.append("enabled = 1")
    with db.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT * FROM monitors WHERE " + " AND ".join(clauses)
            + " ORDER BY source_id"
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["config"] = json.loads(str(item.pop("config_json") or "{}"))
        except (TypeError, ValueError):
            item["config"] = {}
        result.append(item)
    return result


def set_monitor_enabled(
    db: FoundryDatabase,
    source_id: str,
    enabled: bool,
    *,
    reason: str = "",
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Enable/disable a configured source without losing its history."""
    row = next((item for item in configured_monitors(db)
                if item.get("source_id") == source_id), None)
    if row is None:
        raise ValueError(f"unknown configured source {source_id}")
    result = configure_monitor(
        db,
        source_id=source_id,
        source_kind=str(row["source_kind"]),
        source_identity=str(row["source_ref"]),
        poll_interval_s=int(row["poll_interval_s"]),
        config=dict(row.get("config") or {}),
        enabled=bool(enabled),
        mission_id=str(row.get("mission_id") or "v11-continuous-observation"),
        now=now,
    )
    if reason:
        # Reason is audit context only; the content-addressed configuration is
        # still the state authority.
        db.append(Event.create(
            "discovery.monitor_lifecycle_noted",
            {"monitor_id": row["monitor_id"], "source_id": source_id,
             "enabled": bool(enabled), "reason": str(reason)},
            source_type="supervisor",
            source_id="v11-continuous-observation",
            idempotency_key=(
                f"discovery.monitor_lifecycle_noted:{row['monitor_id']}:"
                f"{hashlib.sha256(str(reason).encode('utf-8')).hexdigest()[:16]}"
            ),
        ))
    return result


def due_monitors(
    db: FoundryDatabase,
    *,
    now: str | datetime | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return a fair bounded rotation of only enabled, due watch sources."""
    current = _iso(_as_utc(now))
    bounded = max(1, min(int(limit), 16))
    with db.connect(readonly=True) as connection:
        rows = connection.execute(
            """SELECT * FROM monitors
               WHERE source_id IS NOT NULL AND enabled = 1
                 AND COALESCE(backoff_until, next_due_at, '') <= ?
               ORDER BY COALESCE(last_attempt_at, '') ASC,
                        COALESCE(next_due_at, '') ASC, source_id ASC
               LIMIT ?""",
            (current, bounded),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["config"] = json.loads(str(item.pop("config_json") or "{}"))
        except (TypeError, ValueError):
            item["config"] = {}
        result.append(item)
    return result


def next_monitor_due_at(db: FoundryDatabase) -> str | None:
    """Return the durable earliest due/backoff time for enabled sources."""
    with db.connect(readonly=True) as connection:
        row = connection.execute(
            """SELECT MIN(CASE
                   WHEN backoff_until IS NOT NULL AND
                        (next_due_at IS NULL OR backoff_until > next_due_at)
                       THEN backoff_until
                   ELSE next_due_at END)
               FROM monitors WHERE source_id IS NOT NULL AND enabled = 1"""
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def monitor_due(
    db: FoundryDatabase,
    monitor_id: str,
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Read the durable monitor schedule and decide whether it is due."""
    current = _as_utc(now)
    with db.connect() as connection:
        columns = {str(value[1]) for value in connection.execute(
            "PRAGMA table_info(monitors)").fetchall()}
        if "next_due_at" in columns:
            row = connection.execute(
                "SELECT enabled, poll_interval_s, last_poll_at, next_due_at, "
                "backoff_until FROM monitors WHERE monitor_id = ?",
                (monitor_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT enabled, poll_interval_s, last_poll_at, NULL, NULL "
                "FROM monitors WHERE monitor_id = ?",
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
    explicit_due = None
    for value in (row[3], row[4]):
        if value:
            try:
                parsed = _as_utc(str(value))
                explicit_due = parsed if explicit_due is None else max(explicit_due, parsed)
            except ValueError:
                pass
    next_due = explicit_due or (
        current if last_poll is None else last_poll + timedelta(seconds=interval)
    )
    return {
        "due": last_poll is None or current >= next_due,
        "enabled": True,
        "last_poll_at": row[2],
        "poll_interval_s": interval,
        "next_due_at": _iso(next_due),
    }


def record_monitor_attempt(
    db: FoundryDatabase,
    monitor_id: str,
    *,
    poll_key: str,
    items_seen: int,
    candidates_found: int,
    additional_events: Sequence[Event] = (),
    status: str = "READY",
    attempted_at: str | None = None,
    observation_at: str | None = None,
    content_hash: str | None = None,
    meaningful_change: bool = False,
    next_due_at: str | None = None,
    consecutive_failures: int = 0,
    backoff_until: str | None = None,
    reason: str = "",
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
        "status": str(status).upper(),
        "attempted_at": attempted_at,
        "observation_at": observation_at,
        "content_hash": content_hash,
        "meaningful_change": bool(meaningful_change),
        "next_due_at": next_due_at,
        "consecutive_failures": max(0, int(consecutive_failures)),
        "backoff_until": backoff_until,
        "reason": str(reason or ""),
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
