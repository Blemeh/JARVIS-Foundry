"""World observations - normalized input that drives the world model."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json


def _now() -> str:
    return isoformat()


def _text(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    t = str(value).replace("\x00", " ").strip()
    return t if len(t) <= limit else t[: limit - 1] + "..."


class WorldObservationStore:
    """Normalized observation ingestion."""

    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def record(
        self,
        source_kind: str,
        source_ref: str,
        payload: dict[str, Any],
        *,
        observation_id: str | None = None,
        observed_at: str | None = None,
        provenance: dict[str, Any] | None = None,
        source_id: str = "world-model",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not source_kind or not source_ref:
            raise ValueError("source_kind and source_ref are required")
        oid = observation_id or f"wo-{uuid.uuid4().hex[:12]}"
        now = _now()
        normalized = self._normalize(source_kind, payload)
        entities = self._extract_entities(normalized)
        relationships = self._extract_relationships(normalized)
        event_payload: dict[str, Any] = {
            "observation_id": oid,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "observed_at": observed_at or now,
            "payload": payload,
            "normalized": normalized,
            "entities": entities,
            "relationships": relationships,
            "provenance": provenance or {"source_kind": source_kind, "source_ref": source_ref},
            "processed": False,
        }
        event = Event.create(
            "world.observation_recorded", event_payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=idempotency_key or f"world.observation_recorded:{oid}",
        )
        self.db.append(event)
        return {"observation_id": oid, "normalized": normalized, "entities": entities, "relationships": relationships}

    def _normalize(self, source_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize raw payload into canonical observation fields."""
        normalized: dict[str, Any] = {
            "source_kind": source_kind,
            "summary": _text(payload.get("summary") or payload.get("reason") or payload.get("objective") or json.dumps(payload, sort_keys=True)[:300]),
            "entities_mentioned": [],
            "signals": [],
        }
        # Extract entity hints from payload keys
        for key in ("mission_id", "project_id", "objective_id", "provider_id", "capability_id", "resource_id"):
            if payload.get(key):
                normalized["entities_mentioned"].append({"key": key, "value": str(payload[key])})
        # Extract status signals
        for key in ("status", "state", "outcome", "health", "availability"):
            if payload.get(key):
                normalized["signals"].append({"signal": key, "value": str(payload[key])})
        return normalized

    def _extract_entities(self, normalized: dict[str, Any]) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        for item in normalized.get("entities_mentioned", []):
            key = item.get("key", "")
            entity_type = "UNKNOWN"
            if "mission" in key:
                entity_type = "MISSION"
            elif "project" in key:
                entity_type = "PROJECT"
            elif "objective" in key:
                entity_type = "OWNER_OBJECTIVE"
            elif "provider" in key:
                entity_type = "PROVIDER"
            elif "capability" in key:
                entity_type = "CAPABILITY"
            elif "resource" in key:
                entity_type = "SYSTEM"
            entities.append({"entity_type": entity_type, "display_name": str(item.get("value", "")), "source_key": key})
        return entities

    def _extract_relationships(self, normalized: dict[str, Any]) -> list[dict[str, Any]]:
        # Relationships are inferred later by the intelligence engine, not here
        return []

    def get(self, observation_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM world_observations WHERE observation_id = ?", (observation_id,)).fetchone()
            if row is None:
                return None
            d = dict(row)
            for key in ("payload_json", "normalized_json", "entities_json", "relationships_json", "provenance_json"):
                raw = d.pop(key, None)
                target = key.replace("_json", "")
                if key == "payload_json":
                    target = "payload"
                elif key == "normalized_json":
                    target = "normalized"
                elif key == "entities_json":
                    target = "entities"
                elif key == "relationships_json":
                    target = "relationships"
                elif key == "provenance_json":
                    target = "provenance"
                try:
                    d[target] = json.loads(raw) if raw else ({} if target in ("payload", "normalized", "provenance") else [])
                except (TypeError, ValueError):
                    d[target] = {} if target in ("payload", "normalized", "provenance") else []
            return d

    def list(self, source_kind: str | None = None, processed: bool | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if source_kind:
            clauses.append("source_kind = ?")
            args.append(source_kind)
        if processed is not None:
            clauses.append("processed = ?")
            args.append(1 if processed else 0)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM world_observations{where} ORDER BY observed_at DESC LIMIT ?", tuple(args) + (max(1, min(limit, 500)),)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            for key in ("payload_json", "normalized_json", "entities_json", "relationships_json", "provenance_json"):
                raw = d.pop(key, None)
                target = key.replace("_json", "")
                if key == "payload_json":
                    target = "payload"
                elif key == "normalized_json":
                    target = "normalized"
                elif key == "entities_json":
                    target = "entities"
                elif key == "relationships_json":
                    target = "relationships"
                elif key == "provenance_json":
                    target = "provenance"
                try:
                    d[target] = json.loads(raw) if raw else ({} if target in ("payload", "normalized", "provenance") else [])
                except (TypeError, ValueError):
                    d[target] = {} if target in ("payload", "normalized", "provenance") else []
            result.append(d)
        return result

    def mark_processed(self, observation_id: str, source_id: str = "world-model") -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT observation_id FROM world_observations WHERE observation_id = ?", (observation_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown observation {observation_id}")
        event = Event.create(
            "world.observation_recorded",
            {"observation_id": observation_id, "source_kind": "_processed", "source_ref": observation_id, "processed": True},
            source_type="world_model", source_id=source_id,
            idempotency_key=f"world.observation_processed:{observation_id}",
        )
        # Use a direct update instead of re-recording
        with self.db.connect() as conn:
            conn.execute("UPDATE world_observations SET processed = 1 WHERE observation_id = ?", (observation_id,))
            conn.commit()