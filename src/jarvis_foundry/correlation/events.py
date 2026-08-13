"""V5-S5: event correlation across sources + bounded worker manifests.

The event envelope already carries correlation_id/causation_id/
idempotency_key/source (contract §10.3) — this module adds the
deterministic cross-source VIEW (ordering + causation chains) and the
bounded context manifest for workers using cross-system facts. All
reads are derived from the append-only event stream; nothing is ever
rewritten.
"""
from __future__ import annotations

import hashlib
import json

from ..database import FoundryDatabase
from ..events import Event, payload_hash


def correlation_chain(database: FoundryDatabase,
                      correlation_id: str) -> list[dict[str, object]]:
    """All events sharing a correlation id, ordered by ledger sequence
    (the durable cross-source order), each with its source and
    causation link."""
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT event_id, event_type, source_type, source_id, "
            "correlation_id, causation_id, occurred_at, recorded_at "
            "FROM events WHERE correlation_id = ? ORDER BY sequence",
            (correlation_id,)).fetchall()
    return [dict(row) for row in rows]


def build_cross_system_manifest(
    database: FoundryDatabase,
    mission_id: str,
    *,
    max_entities: int = 20,
    max_events: int = 100,
) -> dict[str, object]:
    """A bounded, deterministic worker context manifest of cross-system
    facts for a mission: entities (id + canonical name, id order) and
    correlated events (chronological, bounded), with explicit
    truncation flags. Same inputs -> same manifest (hash-stable)."""
    from .entities import _entity_rows

    entities = _entity_rows(database)
    entities_sorted = sorted(entities, key=lambda entry: entry["entity_id"])
    truncated_entities = len(entities_sorted) > max_entities
    entity_slice = entities_sorted[:max_entities]
    with database.connect() as connection:
        events = connection.execute(
            "SELECT event_id, event_type, source_id, correlation_id, "
            "causation_id, occurred_at FROM events "
            "WHERE correlation_id IS NOT NULL AND event_type != "
            "'correlation.manifest_generated' ORDER BY sequence LIMIT ?",
            (max_events,)).fetchall()
        total_correlated = connection.execute(
            "SELECT COUNT(*) FROM events WHERE correlation_id IS NOT NULL "
            "AND event_type != 'correlation.manifest_generated'"
        ).fetchone()[0]
    truncated_events = total_correlated > max_events
    manifest = {
        "manifest_id": f"xman-{mission_id}-"
                       f"{hashlib.sha256(mission_id.encode()).hexdigest()[:12]}",
        "mission_id": mission_id,
        "entities": [(entry["entity_id"], entry["canonical_name"])
                     for entry in entity_slice],
        "total_entities": len(entities_sorted),
        "events": [dict(row) for row in events],
        "total_events": len(events),
        "truncated": truncated_entities or truncated_events,
    }
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    database.append(Event.create(
        "correlation.manifest_generated",
        {"manifest_id": manifest["manifest_id"],
         "mission_id": mission_id,
         "entities_count": len(manifest["entities"]),
         "events_count": len(manifest["events"]),
         "truncated": int(manifest["truncated"]),
         "manifest_hash": manifest_hash},
        source_type="supervisor", source_id="v5-correlation",
        idempotency_key=(f"correlation.manifest_generated:{mission_id}:"
                         f"{payload_hash({'manifest_hash': manifest_hash})}")))
    return manifest
