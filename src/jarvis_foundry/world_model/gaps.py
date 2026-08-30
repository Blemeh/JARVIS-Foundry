"""Knowledge gaps - explicit representation of what JARVIS does NOT know."""
from __future__ import annotations

import json
import uuid
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json
from jarvis_foundry.world_model.constants import GAP_KINDS


def _now() -> str:
    return isoformat()


class WorldKnowledgeGapStore:
    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def record(
        self,
        gap_kind: str,
        description: str,
        *,
        gap_id: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        blocking: bool = False,
        source_id: str = "world-model",
        idempotency_key: str | None = None,
    ) -> str:
        if gap_kind not in GAP_KINDS:
            raise ValueError(f"invalid gap_kind {gap_kind!r}")
        if not description or not description.strip():
            raise ValueError("description is required")
        gid = gap_id or f"wg-{uuid.uuid4().hex[:12]}"
        payload: dict[str, Any] = {
            "gap_id": gid,
            "gap_kind": gap_kind,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "description": description.strip(),
            "blocking": blocking,
            "resolved": False,
        }
        event = Event.create(
            "world.knowledge_gap_recorded", payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=idempotency_key or f"world.knowledge_gap_recorded:{gid}",
        )
        self.db.append(event)
        return gid

    def resolve(self, gap_id: str, resolved_by: str, *, source_id: str = "world-model") -> str:
        current = self.get(gap_id)
        if current is None:
            raise ValueError(f"unknown gap {gap_id}")
        if current["resolved"]:
            return gap_id
        payload: dict[str, Any] = {
            "gap_id": gap_id,
            "gap_kind": current["gap_kind"],
            "subject_type": current.get("subject_type"),
            "subject_id": current.get("subject_id"),
            "description": current["description"],
            "blocking": bool(current["blocking"]),
            "resolved": True,
            "resolved_by": resolved_by,
        }
        event = Event.create(
            "world.knowledge_gap_recorded", payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=f"world.knowledge_gap_resolved:{gap_id}",
        )
        self.db.append(event)
        return gap_id

    def get(self, gap_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM world_knowledge_gaps WHERE gap_id = ?", (gap_id,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def list(self, gap_kind: str | None = None, resolved: bool | None = None, blocking: bool | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if gap_kind:
            clauses.append("gap_kind = ?")
            args.append(gap_kind)
        if resolved is not None:
            clauses.append("resolved = ?")
            args.append(1 if resolved else 0)
        if blocking is not None:
            clauses.append("blocking = ?")
            args.append(1 if blocking else 0)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM world_knowledge_gaps{where} ORDER BY updated_at DESC LIMIT ?", tuple(args) + (max(1, min(limit, 500)),)).fetchall()
        return [dict(r) for r in rows]

    def unresolved(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.list(resolved=False, limit=limit)

    def blocking_gaps(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.list(blocking=True, resolved=False, limit=limit)