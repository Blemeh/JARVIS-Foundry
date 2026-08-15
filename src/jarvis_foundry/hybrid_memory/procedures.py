"""V8 hybrid memory — procedural memory registry (Letta M9 adapted).

Procedures are experiential guidance with provenance, versioning, lifecycle
state and expiry. A procedure may say "this sequence worked previously"; it
can never claim current truth on its own. Executors are referenced (e.g.
Hermes skills), never duplicated here.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event

PROCEDURE_STATES = ("ACTIVE", "STALE", "SUPERSEDED", "ARCHIVED")


class ProcedureRegistry:
    """Governed procedure registry."""

    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def add(self, name: str, description: str, body: str, trigger_terms: list[str],
            *, scope: str = "role", confidence: float = 0.5,
            mission_id: str = "", created_by: str = "worker",
            source_ids: list[str] | None = None,
            evidence_ids: list[str] | None = None,
            expires_at: str | None = None) -> str:
        procedure_id = uuid.uuid4().hex[:16]
        event = Event.create(
            "procedure.created",
            {"procedure_id": procedure_id, "name": name, "description": description,
             "body": body, "trigger_terms": trigger_terms, "scope": scope,
             "state": "ACTIVE", "confidence": max(0.0, min(1.0, confidence)),
             "source_ids": source_ids or [], "evidence_ids": evidence_ids or [],
             "mission_id": mission_id, "created_by": created_by, "expires_at": expires_at},
            source_type="memory", source_id=created_by,
            idempotency_key=f"procedure.created:{procedure_id}")
        self.db.append(event)
        return procedure_id

    def get(self, procedure_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tier_c_procedures WHERE procedure_id=?",
                (procedure_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["trigger_terms"] = json.loads(result.pop("trigger_terms_json") or "[]")
        result["source_ids"] = json.loads(result.pop("source_ids_json") or "[]")
        result["evidence_ids"] = json.loads(result.pop("evidence_ids_json") or "[]")
        return result

    def search(self, text: str, *, scope: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        """Match procedures by trigger terms or body tokens (lexical)."""
        needle = (text or "").lower()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tier_c_procedures WHERE state='ACTIVE' ORDER BY rowid").fetchall()
        hits: list[dict[str, Any]] = []
        for row in rows:
            triggers = json.loads(row["trigger_terms_json"] or "[]")
            trigger_hit = any(t.lower() in needle for t in triggers)
            body_hit = needle and any(len(t) > 3 and t in row["body"].lower()
                                      for t in needle.split())
            if not (trigger_hit or body_hit):
                continue
            if scope and row["scope"] != scope and row["scope"] != "global":
                continue
            hits.append(dict(row))
        return hits[:limit]

    def set_state(self, procedure_id: str, state: str, *, actor: str,
                  reason: str, supersedes: str | None = None) -> dict[str, Any]:
        if state not in PROCEDURE_STATES:
            raise ValueError(f"unknown procedure state {state!r}")
        current = self.get(procedure_id)
        if current is None:
            raise KeyError(procedure_id)
        event = Event.create(
            "procedure.updated",
            {"procedure_id": procedure_id, "state": state, "actor": actor,
             "reason": reason, "supersedes": supersedes},
            source_type="memory", source_id=actor,
            idempotency_key=f"procedure.updated:{procedure_id}:{state}:{reason}")
        self.db.append(event)
        return self.get(procedure_id)
