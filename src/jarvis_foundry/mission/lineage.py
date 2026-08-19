"""Durable mission identity. One lineage; no third ID scheme.

Production uses the ledger ``mission_id``. Candidate-created missions record
``created_from = candidate:<id>``. Parent missions use ``created_from`` as
another mission id. Restart reconstructs the same ``mission_id``; it does
not mint a duplicate mission row.
"""
from __future__ import annotations

from ..database import FoundryDatabase


def origin_mission_id(database: FoundryDatabase, mission_id: str) -> str:
    """Walk ``created_from`` until the durable origin mission."""
    seen: set[str] = set()
    current = mission_id
    while current and current not in seen:
        seen.add(current)
        row = database.row("missions", "mission_id", current)
        if row is None:
            return current
        created = row.get("created_from")
        if not created:
            return current
        parent = str(created)
        if parent.startswith("candidate:"):
            return current
        if database.row("missions", "mission_id", parent) is None:
            return current
        current = parent
    return mission_id
