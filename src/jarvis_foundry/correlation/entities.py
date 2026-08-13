"""V5-S5: cross-system entity resolution (roadmap §8).

Stable entity identities with source-specific references across
systems. Deterministic matching rules with confidence and ambiguity
preservation: name duplicates are BOTH returned (never a destructive
merge), and a source ref has exactly ONE holder (the second claim is
refused and names the holder — ambiguity preserved deterministically).
All state derives from append-only ``correlation.*`` events.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..database import FoundryDatabase
from ..events import Event, payload_hash
from ..knowledge import canonicalize_statement

_MATCH_RULES = ("exact", "canonicalized")


@dataclass(frozen=True, slots=True)
class EntityRef:
    system_id: str
    source_ref: str


def register_entity(database: FoundryDatabase, *, entity_id: str,
                    kind: str, canonical_name: str,
                    source_refs: list[EntityRef] | tuple[EntityRef, ...] = (),
                    mission_id: str | None = None) -> None:
    """Register (or re-register at a new canonical name) an entity.

    Append-only: identical re-delivery dedupes; a changed canonical name
    records NEW registration history and the derived state follows the
    latest record. A source ref already bound to ANOTHER entity is
    refused (deterministic holder — ambiguity preserved).
    """
    if not entity_id or not kind or not canonical_name:
        raise ValueError("entity id, kind, and canonical name are required")
    refs = [{"system_id": ref.system_id, "source_ref": ref.source_ref}
            for ref in source_refs]
    for ref in refs:
        holder = resolve_entity_ref(database, ref["system_id"],
                                    ref["source_ref"])
        if holder is not None and holder != entity_id:
            raise ValueError(
                f"source ref ({ref['system_id']}, {ref['source_ref']}) is "
                f"already bound to entity {holder} — the first holder "
                "wins (ambiguity preserved, no destructive merge)")
    payload = {
        "entity_id": entity_id, "kind": kind,
        "canonical_name": canonical_name, "source_refs": refs,
        "mission_id": mission_id,
    }
    database.append(Event.create(
        "correlation.entity_registered", payload,
        source_type="supervisor", source_id="v5-correlation",
        idempotency_key=(f"correlation.entity_registered:{entity_id}:"
                         f"{payload_hash(payload)}")))


def add_entity_ref(database: FoundryDatabase, entity_id: str,
                   system_id: str, source_ref: str) -> None:
    """Bind one more (system, ref) to an EXISTING entity — atomic: an
    unknown entity rolls the whole event back (mirrors the knowledge
    entity_alias_added handler's reference validation)."""
    if _latest_entity(database, entity_id) is None:
        raise ValueError(
            f"correlation.entity_ref_added requires an existing entity "
            f"({entity_id}) — rolled back")
    holder = resolve_entity_ref(database, system_id, source_ref)
    if holder is not None and holder != entity_id:
        raise ValueError(
            f"source ref ({system_id}, {source_ref}) is already bound to "
            f"entity {holder} — the first holder wins")
    database.append(Event.create(
        "correlation.entity_ref_added",
        {"entity_id": entity_id, "system_id": system_id,
         "source_ref": source_ref},
        source_type="supervisor", source_id="v5-correlation",
        idempotency_key=(f"correlation.entity_ref_added:{entity_id}:"
                         f"{system_id}:{payload_hash({'source_ref': source_ref})}")))


# ---------------------------------------------------------------------------
# derived state (pure; rebuild-safe)
# ---------------------------------------------------------------------------

def _entity_rows(database: FoundryDatabase) -> list[dict[str, object]]:
    """All entities with their LATEST canonical name + full ref history."""
    with database.connect() as connection:
        registrations = connection.execute(
            "SELECT payload_json, sequence FROM events WHERE event_type = "
            "'correlation.entity_registered' ORDER BY sequence").fetchall()
        ref_additions = connection.execute(
            "SELECT payload_json, sequence FROM events WHERE event_type = "
            "'correlation.entity_ref_added' ORDER BY sequence").fetchall()
    latest: dict[str, dict[str, object]] = {}
    for row in registrations:
        payload = json.loads(row["payload_json"])
        latest[payload["entity_id"]] = {
            "entity_id": payload["entity_id"], "kind": payload["kind"],
            "canonical_name": payload["canonical_name"],
            "refs": {(r["system_id"], r["source_ref"])
                     for r in payload.get("source_refs", [])},
        }
    for row in ref_additions:
        payload = json.loads(row["payload_json"])
        entry = latest.get(payload["entity_id"])
        if entry is not None:
            entry["refs"].add((payload["system_id"], payload["source_ref"]))
    return [dict(entry, refs=sorted(entry["refs"]))
            for entry in latest.values()]


def _latest_entity(database: FoundryDatabase,
                   entity_id: str) -> dict[str, object] | None:
    for entry in _entity_rows(database):
        if entry["entity_id"] == entity_id:
            return entry
    return None


def resolve_entity_ref(database: FoundryDatabase, system_id: str,
                       source_ref: str) -> str | None:
    """The single entity bound to a (system, ref) — deterministic by
    construction (one holder per ref)."""
    for entry in _entity_rows(database):
        if (system_id, source_ref) in entry["refs"]:
            return str(entry["entity_id"])
    return None


def match_entities(database: FoundryDatabase,
                   query: str) -> list[tuple[str, float, str]]:
    """Deterministic candidates for a query: (entity_id, confidence,
    rule). Exact canonical-name match -> 1.0; canonicalized-equality ->
    0.9. Duplicates are ALL returned (ambiguity preserved — the caller
    decides; nothing is ever merged destructively)."""
    canonical_query = canonicalize_statement(query)
    if not canonical_query:
        return []
    candidates: list[tuple[str, float, str]] = []
    for entry in _entity_rows(database):
        name = str(entry["canonical_name"])
        if name == query:
            candidates.append((str(entry["entity_id"]), 1.0, "exact"))
        elif canonicalize_statement(name) == canonical_query:
            candidates.append((str(entry["entity_id"]), 0.9, "canonicalized"))
    candidates.sort(key=lambda item: (-item[1], item[0]))
    return candidates
