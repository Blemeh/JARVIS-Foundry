"""V8 hybrid memory — deterministic lexical recall (Letta M4 adapted).

Precision-first lexical retrieval over ``tier_c_memories`` with the
production metadata contract: relevance, confidence, freshness, scope,
evidence refs, contradictions. Malformed expiry metadata degrades instead
of crashing (audit repair L2). Recall returns CONTEXT, never authority.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from ..database import FoundryDatabase
from .lifecycle import INCLUDE_STATES

STOPWORDS = frozenset(
    "a an and or of to in on for is was are it this that with from by at as the be been being "
    "have has had do does did will would can could should may might must not no nor but yet so "
    "about into over after before between out up down off under again further then once here "
    "when where why how all any both each few more most other some such only own same than too "
    "very s t just don now".split())

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


def _is_stale(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)
        return now > expiry
    except (ValueError, TypeError):
        return False  # malformed metadata degrades, never crashes (L2)


class RecallEngine:
    """Lexical recall over accepted Tier C experience."""

    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def recall(self, query: str, *, scope: str | None = None,
               mission_id: str | None = None, limit: int = 5,
               include_states: tuple[str, ...] = INCLUDE_STATES) -> list[dict[str, Any]]:
        """Return ranked recall hits with metadata. Highest first."""
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        placeholders = ",".join("?" for _ in include_states)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tier_c_memories WHERE state IN ({placeholders})",
                include_states).fetchall()
            if mission_id:
                rows = [r for r in rows if not r["mission_id"] or r["mission_id"] == mission_id]
        hits: list[dict[str, Any]] = []
        for row in rows:
            content = row["content"] or ""
            content_tokens = _tokens(content)
            overlap = len(set(query_tokens) & set(content_tokens))
            if overlap == 0:
                continue
            lexical = overlap / max(1, len(set(query_tokens)))
            stale = _is_stale(row["expires_at"])
            recency = 0.5 if stale else 1.0
            score = lexical * 0.7 + recency * 0.15 + row["confidence"] * 0.1
            if scope and row["scope"] != scope and row["scope"] != "global":
                score *= 0.5
            hits.append({
                "memory_id": row["memory_id"],
                "content": content,
                "summary": content[:220],
                "relevance": round(score, 3),
                "memory_type": row["mem_type"],
                "scope": row["scope"],
                "state": row["state"],
                "source_ids": json.loads(row["source_ids_json"] or "[]"),
                "evidence_refs": json.loads(row["evidence_ids_json"] or "[]"),
                "confidence": row["confidence"],
                "freshness": "STALE" if stale else "FRESH",
                "last_verified": row["last_verified"],
                "expires_at": row["expires_at"],
                "contradicts": row["contradicts"],
                "supersedes": row["supersedes"],
                "mission_id": row["mission_id"],
                "created_at": row["created_at"],
            })
        hits.sort(key=lambda h: h["relevance"], reverse=True)
        return hits[:limit]


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat()
    except TypeError:  # pragma: no cover
        return datetime.utcnow().isoformat()
