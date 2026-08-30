"""World relationships - typed directed edges between entities."""
from __future__ import annotations

import json
import uuid
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json
from jarvis_foundry.world_model.constants import validate_relationship_type


def _now() -> str:
    return isoformat()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    import math
    if not math.isfinite(v):
        return 0.5
    return max(lo, min(hi, v))


class WorldRelationshipStore:
    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def record(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str,
        *,
        relationship_id: str | None = None,
        strength: float = 1.0,
        valid_from: str | None = None,
        valid_until: str | None = None,
        observed_at: str | None = None,
        provenance: dict[str, Any] | None = None,
        confidence: float = 0.5,
        source_id_event: str = "world-model",
        idempotency_key: str | None = None,
    ) -> str:
        validate_relationship_type(relationship_type)
        if not source_id or not target_id:
            raise ValueError("source_id and target_id are required")
        rid = relationship_id or f"wr-{uuid.uuid4().hex[:12]}"
        now = _now()
        payload: dict[str, Any] = {
            "relationship_id": rid,
            "source_id": source_id,
            "target_id": target_id,
            "relationship_type": relationship_type,
            "strength": _clamp(strength),
            "valid_from": valid_from or now,
            "valid_until": valid_until,
            "observed_at": observed_at or now,
            "provenance": provenance or {},
            "confidence": _clamp(confidence),
        }
        event = Event.create(
            "world.relationship_recorded", payload,
            source_type="world_model", source_id=source_id_event,
            idempotency_key=idempotency_key or f"world.relationship_recorded:{rid}",
        )
        self.db.append(event)
        return rid

    def get(self, relationship_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM world_relationships WHERE relationship_id = ?", (relationship_id,)).fetchone()
            if row is None:
                return None
            d = dict(row)
            try:
                d["provenance"] = json.loads(d.pop("provenance_json") or "{}")
            except (TypeError, ValueError):
                d["provenance"] = {}
            return d

    def for_entity(self, entity_id: str, direction: str = "both") -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            if direction == "outgoing":
                rows = conn.execute("SELECT * FROM world_relationships WHERE source_id = ? ORDER BY updated_at DESC", (entity_id,)).fetchall()
            elif direction == "incoming":
                rows = conn.execute("SELECT * FROM world_relationships WHERE target_id = ? ORDER BY updated_at DESC", (entity_id,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM world_relationships WHERE source_id = ? OR target_id = ? ORDER BY updated_at DESC",
                    (entity_id, entity_id),
                ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            try:
                d["provenance"] = json.loads(d.pop("provenance_json") or "{}")
            except (TypeError, ValueError):
                d["provenance"] = {}
            result.append(d)
        return result

    def traverse(
        self,
        start_id: str,
        relationship_types: list[str] | None = None,
        max_depth: int = 3,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """BFS traversal from start_id following relationships."""
        visited: set[str] = {start_id}
        frontier: list[tuple[str, int]] = [(start_id, 0)]
        results: list[dict[str, Any]] = []
        while frontier and len(results) < max_results:
            current, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            rels = self.for_entity(current, direction="outgoing")
            for rel in rels:
                if relationship_types and rel["relationship_type"] not in relationship_types:
                    continue
                target = rel["target_id"]
                if target not in visited:
                    visited.add(target)
                    frontier.append((target, depth + 1))
                    results.append(rel)
                    if len(results) >= max_results:
                        break
        return results

    def impact_chain(self, source_id: str, max_depth: int = 5) -> list[dict[str, Any]]:
        """Follow DEPENDS_ON / AFFECTS / BLOCKED_BY chains to find impacted entities."""
        return self.traverse(source_id, relationship_types=["DEPENDS_ON", "AFFECTS", "BLOCKED_BY", "REQUIRES"], max_depth=max_depth)

    def list_all(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM world_relationships ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            try:
                d["provenance"] = json.loads(d.pop("provenance_json") or "{}")
            except (TypeError, ValueError):
                d["provenance"] = {}
            result.append(d)
        return result