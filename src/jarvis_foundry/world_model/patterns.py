"""Pattern detection - recurring observations."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json


def _now() -> str:
    return isoformat()


def _pattern_key(pattern_kind: str, description: str) -> str:
    raw = f"{pattern_kind}:{description.strip().lower()[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class WorldPatternStore:
    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def record(
        self,
        pattern_kind: str,
        description: str,
        *,
        evidence: list[str] | None = None,
        confidence: float = 0.5,
        source_id: str = "world-model",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not pattern_kind or not description or not description.strip():
            raise ValueError("pattern_kind and description are required")
        key = _pattern_key(pattern_kind, description)
        # Check if pattern already exists - increment occurrences
        existing = self.get_by_key(pattern_kind, description)
        if existing:
            payload: dict[str, Any] = {
                "pattern_id": existing["pattern_id"],
                "pattern_kind": pattern_kind,
                "description": description.strip(),
                "occurrences": existing["occurrences"] + 1,
                "first_seen": existing["first_seen"],
                "last_seen": _now(),
                "evidence": list(set((existing.get("evidence") or []) + (evidence or [])))[:20],
                "confidence": min(0.95, existing["confidence"] + 0.05),
            }
            # Avoid overfitting from one event - require at least 2 occurrences for confidence boost
            if existing["occurrences"] < 2:
                payload["confidence"] = max(existing["confidence"], confidence)
            event = Event.create(
                "world.pattern_recorded", payload,
                source_type="world_model", source_id=source_id,
                idempotency_key=idempotency_key or f"world.pattern_recorded:{existing['pattern_id']}:{payload['occurrences']}",
            )
            self.db.append(event)
            return {"pattern_id": existing["pattern_id"], "occurrences": payload["occurrences"], "is_new": False}
        pid = f"wpat-{uuid.uuid4().hex[:12]}"
        # Use deterministic ID from key for dedup
        pid = f"wpat-{key}"
        now = _now()
        payload2: dict[str, Any] = {
            "pattern_id": pid,
            "pattern_kind": pattern_kind,
            "description": description.strip(),
            "occurrences": 1,
            "first_seen": now,
            "last_seen": now,
            "evidence": evidence or [],
            "confidence": max(0.0, min(1.0, confidence)),
        }
        event = Event.create(
            "world.pattern_recorded", payload2,
            source_type="world_model", source_id=source_id,
            idempotency_key=idempotency_key or f"world.pattern_recorded:{pid}",
        )
        self.db.append(event)
        return {"pattern_id": pid, "occurrences": 1, "is_new": True}

    def get(self, pattern_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM world_patterns WHERE pattern_id = ?", (pattern_id,)).fetchone()
            if row is None:
                return None
            return _row_to_pattern(dict(row))

    def get_by_key(self, pattern_kind: str, description: str) -> dict[str, Any] | None:
        key = _pattern_key(pattern_kind, description)
        pid = f"wpat-{key}"
        return self.get(pid)

    def list(self, pattern_kind: str | None = None, min_occurrences: int = 1, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = ["occurrences >= ?"]
        args: list[Any] = [min_occurrences]
        if pattern_kind:
            clauses.append("pattern_kind = ?")
            args.append(pattern_kind)
        where = f" WHERE {' AND '.join(clauses)}"
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM world_patterns{where} ORDER BY occurrences DESC, last_seen DESC LIMIT ?", tuple(args) + (max(1, min(limit, 500)),)).fetchall()
        return [_row_to_pattern(dict(r)) for r in rows]

    def significant(self, min_occurrences: int = 3, limit: int = 50) -> list[dict[str, Any]]:
        """Patterns with enough occurrences to be meaningful - avoid overfitting."""
        return self.list(min_occurrences=min_occurrences, limit=limit)


def _row_to_pattern(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.pop("evidence_json", None)
    try:
        row["evidence"] = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        row["evidence"] = []
    return row