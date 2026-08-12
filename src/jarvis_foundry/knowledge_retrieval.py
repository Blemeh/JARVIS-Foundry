"""V2 slice-2 (WP9): deterministic bounded retrieval and context
manifests over the epistemic core.

Bounded by design: exact-match filters, confidence-ordered retrieval
with a limit cap, and size-capped manifests with EXPLICIT truncation
(never silent). No embeddings, no vector store (recorded future work)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# claim_type registry used for objective hints (deterministic matching)
CLAIM_TYPE_HINTS = ("causal-analysis", "fact", "constraint", "preference",
                    "decision")

_CLAIM_COLUMNS = ("claim_id, mission_id, statement, claim_type, confidence, "
                  "fresh_until, source_run_id, status")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def retrieve(connection: sqlite3.Connection, mission_id: str,
             claim_type: str | None = None, status: str = "ACCEPTED",
             fresh_only: bool = True, limit: int = 50,
             now: str | None = None) -> list[dict[str, Any]]:
    """Bounded, deterministic retrieval over knowledge_claims.

    Ordering: confidence DESC, then recency (rowid DESC). Capped at
    ``limit``. Fresh-only excludes expired claims (fresh_until < now).
    """
    clauses, args = ["mission_id = ?", "status = ?"], [mission_id, status]
    if claim_type:
        clauses.append("claim_type = ?")
        args.append(claim_type)
    if fresh_only:
        clauses.append("(fresh_until IS NULL OR fresh_until > ?)")
        args.append(now or _now())
    limit = max(1, min(int(limit), 500))
    sql = (f"SELECT {_CLAIM_COLUMNS} FROM knowledge_claims "
           f"WHERE {' AND '.join(clauses)} "
           f"ORDER BY confidence DESC, rowid DESC LIMIT {limit}")
    rows = connection.execute(sql, args).fetchall()
    return [dict(row) for row in rows]


@dataclass
class Manifest:
    objective: str
    objective_hash: str
    generated_at: str
    active_claims: list[dict[str, Any]] = field(default_factory=list)
    stale_claims: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    manifest_hash: str = ""

    @property
    def active_count(self) -> int:
        return len(self.active_claims)

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "objective_hash": self.objective_hash,
            "generated_at": self.generated_at,
            "active_claims": self.active_claims,
            "stale_claims": self.stale_claims,
            "conflicts": self.conflicts,
            "truncated": self.truncated,
            "active_count": self.active_count,
            "manifest_hash": self.manifest_hash,
        }

    def _finalize(self) -> "Manifest":
        payload = {
            "objective": self.objective,
            "objective_hash": self.objective_hash,
            "active_claims": self.active_claims,
            "stale_claims": self.stale_claims,
            "conflicts": self.conflicts,
            "truncated": self.truncated,
        }
        self.manifest_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return self


def _hint_types(objective: str) -> list[str]:
    canonical = objective.lower()
    return [hint for hint in CLAIM_TYPE_HINTS if hint in canonical]


def build_context_manifest(connection: sqlite3.Connection, mission_id: str,
                           objective: str, max_chars: int = 8000,
                           now: str | None = None) -> Manifest:
    """Build a bounded context manifest for an objective.

    - claim_type hints are parsed deterministically from the objective;
    - ACTIVE = ACCEPTED + fresh claims (hint-filtered when hints exist);
    - STALE = ACCEPTED but expired (never silently dropped);
    - CONFLICTS = live contradiction rows involving the mission's claims;
    - the serialized manifest is capped at max_chars; truncation is
      explicit (truncated flag + counts), never silent.
    """
    now = now or _now()
    hints = _hint_types(objective)
    active_all: list[dict[str, Any]] = []
    if hints:
        for hint in hints:
            active_all.extend(
                retrieve(connection, mission_id, claim_type=hint, now=now))
    else:
        active_all = retrieve(connection, mission_id, now=now)
    # de-duplicate by claim_id (hint overlap), keep confidence order
    seen: set[str] = set()
    active: list[dict[str, Any]] = []
    for claim in active_all:
        if claim["claim_id"] not in seen:
            seen.add(claim["claim_id"])
            active.append(claim)

    stale = retrieve(connection, mission_id, status="ACCEPTED",
                     fresh_only=False, now=now)
    stale = [c for c in stale if c["fresh_until"] and c["fresh_until"] <= now]

    conflicts_raw = connection.execute(
        "SELECT claim_a_id, claim_b_id, claim_type, reason "
        "FROM knowledge_contradictions "
        "WHERE claim_a_id IN (SELECT claim_id FROM knowledge_claims "
        "WHERE mission_id = ?) "
        "AND claim_b_id IN (SELECT claim_id FROM knowledge_claims "
        "WHERE mission_id = ?) "
        "ORDER BY rowid", (mission_id, mission_id)).fetchall()
    conflicts = [dict(row) for row in conflicts_raw]

    manifest = Manifest(
        objective=objective,
        objective_hash=hashlib.sha256(objective.encode("utf-8")).hexdigest()[:16],
        generated_at=now,
    )
    budget = max_chars
    serialized: list[dict[str, Any]] = []
    for claim in active:
        item = {"claim_id": claim["claim_id"], "statement": claim["statement"],
                "claim_type": claim["claim_type"],
                "confidence": claim["confidence"],
                "fresh_until": claim["fresh_until"],
                "source_run_id": claim["source_run_id"],
                "status": claim["status"]}
        cost = len(json.dumps(item))
        if sum(len(json.dumps(i)) for i in serialized) + cost > budget:
            manifest.truncated = True
            break
        serialized.append(item)
    manifest.active_claims = serialized
    manifest.conflicts = conflicts
    manifest.stale_claims = [
        {"claim_id": c["claim_id"], "statement": c["statement"],
         "claim_type": c["claim_type"], "confidence": c["confidence"],
         "fresh_until": c["fresh_until"], "status": "STALE"}
        for c in stale]
    return manifest._finalize()
