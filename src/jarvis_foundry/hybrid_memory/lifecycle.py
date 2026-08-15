"""V8 hybrid memory — Tier C lifecycle state machine (event-derived).

The Foundry event ledger is the source of truth: every mutation appends a
``memory.*`` event (idempotency-keyed) and ``projections.apply_event``
projects the Tier C tables. Acceptance transitions require a validation
stamp written by the governed curation pipeline (audit repair H1): raw
callers cannot skip validation.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event

STATES = ("OBSERVED", "CANDIDATE_MEMORY", "VALIDATING", "ACCEPTED_EXPERIENCE",
          "CORE", "EXTERNAL", "PROCEDURAL", "STALE", "SUPERSEDED", "ARCHIVED")

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "OBSERVED": {"CANDIDATE_MEMORY", "ARCHIVED"},
    "CANDIDATE_MEMORY": {"VALIDATING", "ARCHIVED"},
    "VALIDATING": {"ACCEPTED_EXPERIENCE", "ARCHIVED"},
    "ACCEPTED_EXPERIENCE": {"CORE", "EXTERNAL", "PROCEDURAL", "STALE", "SUPERSEDED", "ARCHIVED"},
    "CORE": {"EXTERNAL", "STALE", "SUPERSEDED", "ARCHIVED"},
    "EXTERNAL": {"CORE", "STALE", "SUPERSEDED", "ARCHIVED"},
    "PROCEDURAL": {"EXTERNAL", "STALE", "SUPERSEDED", "ARCHIVED"},
    "STALE": {"VALIDATING", "SUPERSEDED", "ARCHIVED"},
    "SUPERSEDED": {"ARCHIVED"},
    "ARCHIVED": set(),
}

STAMP_REQUIRED = {"ACCEPTED_EXPERIENCE", "CORE", "EXTERNAL", "PROCEDURAL"}

INCLUDE_STATES = ("CORE", "EXTERNAL", "PROCEDURAL", "ACCEPTED_EXPERIENCE", "STALE")


class InvalidTransition(ValueError):
    """Lifecycle transition refused (illegal or missing validation stamp)."""


class TierCMemoryStore:
    """Event-driven promotion state machine for Tier C experiential memory."""

    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    # ---- observation -----------------------------------------------------
    def record_observation(self, content: str, *, mission_id: str = "",
                           agent_run_id: str | None = None, created_by: str = "worker",
                           mem_type: str = "episodic", scope: str = "role",
                           source_ids: list[str] | None = None,
                           evidence_ids: list[str] | None = None,
                           confidence: float = 0.5,
                           expires_at: str | None = None,
                           idempotency_key: str | None = None) -> str:
        """Ingest an observation (idempotent). Returns the memory_id."""
        memory_id = uuid.uuid4().hex[:16]
        event = Event.create(
            "memory.observed",
            {"memory_id": memory_id, "mem_type": mem_type, "scope": scope,
             "content": content, "mission_id": mission_id,
             "agent_run_id": agent_run_id, "created_by": created_by,
             "source_ids": source_ids or [], "evidence_ids": evidence_ids or [],
             "confidence": max(0.0, min(1.0, confidence)), "expires_at": expires_at},
            source_type="memory", source_id=created_by,
            idempotency_key=idempotency_key or f"memory.observed:{memory_id}")
        self.db.append(event)
        return memory_id

    # ---- state access ----------------------------------------------------
    def get(self, memory_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tier_c_memories WHERE memory_id=?", (memory_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("source_ids_json", "evidence_ids_json"):
            result[key.replace("_json", "")] = json.loads(result.pop(key) or "[]")
        return result

    def list(self, state: str | None = None) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            if state:
                rows = conn.execute(
                    "SELECT * FROM tier_c_memories WHERE state=? ORDER BY rowid", (state,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tier_c_memories ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]

    def revisions(self, memory_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tier_c_memory_revisions WHERE memory_id=? ORDER BY revision_id",
                (memory_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---- transitions -----------------------------------------------------
    def propose(self, memory_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        return self.transition(memory_id, "CANDIDATE_MEMORY", actor=actor, reason=reason)

    def start_validation(self, memory_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        return self.transition(memory_id, "VALIDATING", actor=actor, reason=reason)

    def accept(self, memory_id: str, *, placement: str, actor: str, reason: str) -> dict[str, Any]:
        if placement not in ("CORE", "EXTERNAL", "PROCEDURAL"):
            raise InvalidTransition(f"unknown placement {placement!r}")
        current = self.get(memory_id)
        if current is None:
            raise KeyError(memory_id)
        if current["state"] != "ACCEPTED_EXPERIENCE":
            self.transition(memory_id, "ACCEPTED_EXPERIENCE", actor=actor, reason="accept")
        return self.transition(memory_id, placement, actor=actor, reason=reason)

    def mark_stale(self, memory_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        return self.transition(memory_id, "STALE", actor=actor, reason=reason)

    def supersede(self, memory_id: str, *, by_memory_id: str, actor: str,
                  reason: str) -> dict[str, Any]:
        return self.transition(memory_id, "SUPERSEDED", actor=actor, reason=reason,
                               supersedes=by_memory_id)

    def archive(self, memory_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        return self.transition(memory_id, "ARCHIVED", actor=actor, reason=reason)

    def transition(self, memory_id: str, target: str, *, actor: str, reason: str,
                   supersedes: str | None = None,
                   contradicts: str | None = None) -> dict[str, Any]:
        target = target.upper()
        if target not in STATES:
            raise InvalidTransition(f"unknown state {target!r}")
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tier_c_memories WHERE memory_id=?", (memory_id,)).fetchone()
            if row is None:
                raise KeyError(memory_id)
            current = row["state"]
            if target not in ALLOWED_TRANSITIONS.get(current, set()):
                raise InvalidTransition(f"{current} -> {target}")
            if target in STAMP_REQUIRED:
                stamp = conn.execute(
                    "SELECT validated_by FROM tier_c_validations WHERE memory_id=?",
                    (memory_id,)).fetchone()
                if stamp is None:
                    raise InvalidTransition(
                        f"{current} -> {target} requires a validation stamp "
                        "(use the governed curation pipeline)")
        event = Event.create(
            "memory.transition",
            {"memory_id": memory_id, "from": current, "to": target,
             "actor": actor, "reason": reason, "content_before": row["content"],
             "supersedes": supersedes, "contradicts": contradicts},
            source_type="memory", source_id=actor,
            idempotency_key=f"memory.transition:{memory_id}:{target}:{actor}:{reason}:{supersedes or ''}")
        self.db.append(event)
        return self.get(memory_id)
