"""V8 hybrid memory — bounded core memory blocks (Letta M1 adapted).

Small, high-value context real estate with char budgets, occupancy
rendering, read-only protection and full revision history. Event-derived
via ``block.updated`` events.
"""
from __future__ import annotations

import uuid
from typing import Any

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event

DEFAULT_BLOCKS: dict[str, dict[str, Any]] = {
    "owner": {"description": "stable owner preferences and identity facts",
              "char_limit": 500, "read_only": True},
    "identity": {"description": "who JARVIS is and how it operates",
                 "char_limit": 500, "read_only": True},
    "strategic_goals": {"description": "current strategic priorities",
                        "char_limit": 500, "read_only": False},
    "operating_principles": {"description": "critical operating rules",
                             "char_limit": 800, "read_only": True},
    "active_constraints": {"description": "currently active constraints",
                           "char_limit": 500, "read_only": False},
    "current_projects": {"description": "current major project identities",
                         "char_limit": 800, "read_only": False},
    "critical_lessons": {"description": "repeated lessons with strong evidence",
                         "char_limit": 1000, "read_only": False},
    "memory_index": {"description": "compact index pointing into deeper memory",
                     "char_limit": 800, "read_only": False},
}


class BlockBudgetExceeded(ValueError):
    """Block value exceeds its configured char limit."""


class ReadOnlyBlock(ValueError):
    """Block is read-only (owner-defined identity/governance surface)."""


class CoreMemoryRegistry:
    """Bounded core memory block registry."""

    def __init__(self, db: FoundryDatabase, *, default_limit: int = 2000) -> None:
        self.db = db
        self.default_limit = default_limit

    def ensure_defaults(self, *, actor: str = "owner") -> None:
        """Create the default block set if missing (idempotent)."""
        with self.db.connect() as conn:
            existing = {r["label"] for r in conn.execute("SELECT label FROM tier_c_blocks")}
        for label, spec in DEFAULT_BLOCKS.items():
            if label in existing:
                continue
            self._emit(label, value="", char_limit=spec.get("char_limit", self.default_limit),
                       read_only=spec.get("read_only", False),
                       description=spec.get("description", ""), owner="owner",
                       actor=actor, reason="ensure default block", revision=0)

    def create_block(self, label: str, value: str = "", *, char_limit: int | None = None,
                     read_only: bool = False, description: str = "",
                     actor: str = "memory-service", reason: str = "create block") -> dict[str, Any]:
        if label in self.labels():
            raise KeyError(f"block {label!r} already exists")
        limit = char_limit or self.default_limit
        if len(value) > limit:
            raise BlockBudgetExceeded(
                f"block {label!r} value {len(value)} chars exceeds limit {limit}")
        self._emit(label, value=value, char_limit=limit, read_only=read_only,
                   description=description, owner=actor, actor=actor,
                   reason=reason, revision=0)
        return self.get_block(label)

    def get_block(self, label: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tier_c_blocks WHERE label=?", (label,)).fetchone()
        return dict(row) if row else None

    def labels(self) -> set[str]:
        with self.db.connect() as conn:
            return {r["label"] for r in conn.execute("SELECT label FROM tier_c_blocks")}

    def occupancy(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        with self.db.connect() as conn:
            rows = conn.execute("SELECT label, value, char_limit FROM tier_c_blocks").fetchall()
        for r in rows:
            out[r["label"]] = {"chars": len(r["value"]), "limit": r["char_limit"]}
        return out

    def append(self, label: str, content: str, *, actor: str, reason: str) -> dict[str, Any]:
        block = self.get_block(label)
        if block is None:
            raise KeyError(label)
        if block["read_only"]:
            raise ReadOnlyBlock(label)
        new_value = block["value"] + content
        if len(new_value) > block["char_limit"]:
            raise BlockBudgetExceeded(
                f"block {label!r} would exceed {block['char_limit']} chars")
        self._emit(label, value=new_value, char_limit=block["char_limit"],
                   read_only=block["read_only"], description=block["description"],
                   owner=block["owner"], actor=actor, reason=reason,
                   revision=block["revision"] + 1)
        return self.get_block(label)

    def replace(self, label: str, new_value: str, *, actor: str, reason: str) -> dict[str, Any]:
        block = self.get_block(label)
        if block is None:
            raise KeyError(label)
        if block["read_only"]:
            raise ReadOnlyBlock(label)
        if len(new_value) > block["char_limit"]:
            raise BlockBudgetExceeded(
                f"block {label!r} value {len(new_value)} chars exceeds {block['char_limit']}")
        self._emit(label, value=new_value, char_limit=block["char_limit"],
                   read_only=block["read_only"], description=block["description"],
                   owner=block["owner"], actor=actor, reason=reason,
                   revision=block["revision"] + 1)
        return self.get_block(label)

    def revisions(self, label: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tier_c_block_revisions WHERE label=? ORDER BY revision_id",
                (label,)).fetchall()
        return [dict(r) for r in rows]

    def _emit(self, label: str, *, value: str, char_limit: int, read_only: bool,
              description: str, owner: str, actor: str, reason: str, revision: int) -> None:
        event = Event.create(
            "block.updated",
            {"label": label, "value": value, "char_limit": char_limit,
             "read_only": read_only, "description": description, "owner": owner,
             "revision": revision, "actor": actor, "reason": reason},
            source_type="memory", source_id=actor,
            idempotency_key=f"block.updated:{label}:{revision}:{uuid.uuid4().hex[:8]}")
        self.db.append(event)
