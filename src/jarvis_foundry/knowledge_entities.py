"""V2 slice-3 (WP9): deterministic entity resolution.

Bounded by design: exact + canonicalized alias matching over the entity
registry (canonicalization reuses knowledge.canonicalize_statement); no
NLP extraction — claims link to entities via explicit
knowledge.claim_entity_linked events. Queries are read-only helpers over
the projections."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .knowledge import canonicalize_statement


def _aliases(connection: sqlite3.Connection, entity_id: str) -> list[str]:
    row = connection.execute(
        "SELECT aliases_json FROM knowledge_entities WHERE entity_id = ?",
        (entity_id,)).fetchone()
    if row is None:
        return []
    return list(json.loads(row[0]))


def resolve_entity(connection: sqlite3.Connection, alias: str) -> str | None:
    """Resolve an alias (exact or canonicalized) to an entity_id.

    Deterministic: canonicalize the alias and compare against the
    canonical_name and every registered alias (canonicalized). Returns
    None when no entity matches.
    """
    canonical = canonicalize_statement(alias)
    if not canonical:
        return None
    for row in connection.execute(
            "SELECT entity_id, canonical_name FROM knowledge_entities").fetchall():
        entity_id, canonical_name = row
        if canonicalize_statement(canonical_name) == canonical:
            return entity_id
        for registered in _aliases(connection, entity_id):
            if canonicalize_statement(registered) == canonical:
                return entity_id
    return None


def claims_for_entity(connection: sqlite3.Connection,
                      entity_id: str) -> list[str]:
    rows = connection.execute(
        "SELECT claim_id FROM knowledge_claim_entities "
        "WHERE entity_id = ? ORDER BY claim_id", (entity_id,)).fetchall()
    return [r[0] for r in rows]


def entities_for_claim(connection: sqlite3.Connection,
                       claim_id: str) -> list[str]:
    rows = connection.execute(
        "SELECT entity_id FROM knowledge_claim_entities "
        "WHERE claim_id = ? ORDER BY entity_id",
        (claim_id,)).fetchall()
    return [r[0] for r in rows]


def entities_of_type(connection: sqlite3.Connection,
                     entity_type: str) -> list[str]:
    """Deterministic typed lookup: entity ids of one entity_type
    (goal/project/person/organization/system), id-ordered."""
    from .knowledge_records import validate_entity_type

    validate_entity_type(entity_type)
    rows = connection.execute(
        "SELECT entity_id FROM knowledge_entities WHERE entity_type = ? "
        "ORDER BY entity_id", (entity_type,)).fetchall()
    return [r[0] for r in rows]
