"""World entity management - typed, temporal, versioned entities."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json

from jarvis_foundry.world_model.constants import (
    ENTITY_TYPES, ENTITY_STATES, AUTHORITY_LEVELS, RECORD_KINDS,
    AUTHORITY_PRECEDENCE, validate_entity_type,
)


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


class WorldEntityStore:
    """Event-sourced world entity management."""

    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def record(
        self,
        entity_type: str,
        display_name: str,
        *,
        entity_id: str | None = None,
        state: str = "ACTIVE",
        attributes: dict[str, Any] | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
        observed_at: str | None = None,
        stale_after: str | None = None,
        due_at: str | None = None,
        provenance: dict[str, Any] | None = None,
        confidence: float = 0.5,
        salience: float = 0.5,
        authority: str = "DERIVED",
        record_kind: str = "FACT",
        supersedes: str | None = None,
        derived_from: list[str] | None = None,
        source_id: str = "world-model",
        idempotency_key: str | None = None,
    ) -> str:
        validate_entity_type(entity_type)
        if not display_name or not display_name.strip():
            raise ValueError("display_name is required")
        if state not in ENTITY_STATES:
            raise ValueError(f"invalid state {state!r}")
        if authority not in AUTHORITY_LEVELS:
            raise ValueError(f"invalid authority {authority!r}")
        if record_kind not in RECORD_KINDS:
            raise ValueError(f"invalid record_kind {record_kind!r}")
        eid = entity_id or f"we-{uuid.uuid4().hex[:12]}"
        now = _now()
        payload: dict[str, Any] = {
            "entity_id": eid,
            "entity_type": entity_type,
            "display_name": display_name.strip(),
            "state": state,
            "attributes": attributes or {},
            "valid_from": valid_from or now,
            "valid_until": valid_until,
            "observed_at": observed_at or now,
            "stale_after": stale_after,
            "due_at": due_at,
            "resolved_at": None,
            "provenance": provenance or {},
            "confidence": _clamp(confidence),
            "salience": _clamp(salience),
            "authority": authority,
            "record_kind": record_kind,
            "supersedes": supersedes,
            "derived_from": derived_from or [],
            "version": 1,
        }
        event = Event.create(
            "world.entity_recorded", payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=idempotency_key or f"world.entity_recorded:{eid}",
        )
        self.db.append(event)
        return eid

    def update_state(
        self,
        entity_id: str,
        *,
        state: str | None = None,
        attributes: dict[str, Any] | None = None,
        confidence: float | None = None,
        salience: float | None = None,
        supersedes: str | None = None,
        stale_after: str | None = None,
        due_at: str | None = None,
        resolved_at: str | None = None,
        provenance: dict[str, Any] | None = None,
        source_id: str = "world-model",
        idempotency_key: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {"entity_id": entity_id}
        if state is not None:
            if state not in ENTITY_STATES:
                raise ValueError(f"invalid state {state!r}")
            payload["state"] = state
        if attributes is not None:
            payload["attributes"] = attributes
        if confidence is not None:
            payload["confidence"] = _clamp(confidence)
        if salience is not None:
            payload["salience"] = _clamp(salience)
        if supersedes is not None:
            payload["supersedes"] = supersedes
        if stale_after is not None:
            payload["stale_after"] = stale_after
        if due_at is not None:
            payload["due_at"] = due_at
        if resolved_at is not None:
            payload["resolved_at"] = resolved_at
        if provenance is not None:
            payload["provenance"] = provenance
        event = Event.create(
            "world.entity_state_changed", payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=idempotency_key or f"world.entity_state_changed:{entity_id}:{_now()}",
        )
        self.db.append(event)
        return entity_id

    def supersede(
        self,
        old_entity_id: str,
        new_entity_type: str,
        new_display_name: str,
        *,
        attributes: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        authority: str = "DERIVED",
        source_id: str = "world-model",
    ) -> str:
        """Create a new entity that supersedes an old one."""
        new_id = self.record(
            new_entity_type, new_display_name,
            attributes=attributes, provenance=provenance,
            authority=authority, supersedes=old_entity_id,
            derived_from=[old_entity_id], source_id=source_id,
        )
        self.update_state(old_entity_id, state="SUPERSEDED", supersedes=new_id, source_id=source_id)
        return new_id

    def get(self, entity_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM world_entities WHERE entity_id = ?", (entity_id,)).fetchone()
            if row is None:
                return None
            return _row_to_entity(dict(row))

    def list(
        self,
        entity_type: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if entity_type:
            clauses.append("entity_type = ?")
            args.append(entity_type)
        if state:
            clauses.append("state = ?")
            args.append(state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 500))
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM world_entities{where} ORDER BY updated_at DESC LIMIT {limit}",
                tuple(args),
            ).fetchall()
        return [_row_to_entity(dict(r)) for r in rows]

    def current_view(self, limit: int = 200) -> list[dict[str, Any]]:
        """Current authoritative view: ACTIVE entities only."""
        return self.list(state="ACTIVE", limit=limit)

    def history(self, entity_id: str) -> list[dict[str, Any]]:
        """Historical trace for an entity from the ledger."""
        events = self.db.events()
        trace: list[dict[str, Any]] = []
        for ev in events:
            if ev.event_type in ("world.entity_recorded", "world.entity_state_changed"):
                if ev.payload.get("entity_id") == entity_id:
                    trace.append({"event_type": ev.event_type, "payload": ev.payload, "recorded_at": ev.recorded_at})
        return trace

    def stale_entities(self, now: str | None = None) -> list[dict[str, Any]]:
        """Entities whose stale_after has passed but are still ACTIVE."""
        now = now or _now()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_entities WHERE state = 'ACTIVE' AND stale_after IS NOT NULL AND stale_after <= ? ORDER BY stale_after",
                (now,),
            ).fetchall()
        return [_row_to_entity(dict(r)) for r in rows]

    def resolve_authority_conflict(
        self,
        entity_id_a: str,
        entity_id_b: str,
        *,
        source_id: str = "world-model",
    ) -> str:
        """Resolve conflict between two entities claiming same identity.

        Higher authority wins; if equal, newer observed_at wins.
        The loser is marked SUPERSEDED.
        """
        a = self.get(entity_id_a)
        b = self.get(entity_id_b)
        if a is None or b is None:
            raise ValueError("both entities must exist")
        rank_a = AUTHORITY_PRECEDENCE.get(a["authority"], -1)
        rank_b = AUTHORITY_PRECEDENCE.get(b["authority"], -1)
        if rank_a > rank_b:
            winner, loser = entity_id_a, entity_id_b
        elif rank_b > rank_a:
            winner, loser = entity_id_b, entity_id_a
        else:
            # Tie: newer observed_at wins
            if (a.get("observed_at") or "") >= (b.get("observed_at") or ""):
                winner, loser = entity_id_a, entity_id_b
            else:
                winner, loser = entity_id_b, entity_id_a
        self.update_state(loser, state="SUPERSEDED", supersedes=winner, source_id=source_id)
        return winner


def _row_to_entity(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("attributes_json", "provenance_json", "derived_from_json"):
        raw = row.pop(key, None)
        target = key.replace("_json", "")
        if key == "derived_from_json":
            target = "derived_from"
        try:
            row[target] = json.loads(raw) if raw else ({} if key != "derived_from_json" else [])
        except (TypeError, ValueError):
            row[target] = {} if key != "derived_from_json" else []
    return row