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
MAX_MANIFEST_CHARS = 16_000
MAX_OBJECTIVE_CHARS = 2_000
MAX_CLAIM_TEXT_CHARS = 4_000

_CLAIM_COLUMNS = ("claim_id, mission_id, statement, claim_type, "
                  "record_kind, confidence, "
                  "fresh_until, source_run_id, status")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def retrieve(connection: sqlite3.Connection, mission_id: str,
             claim_type: str | None = None, status: str = "ACCEPTED",
             fresh_only: bool = True, limit: int = 50,
             now: str | None = None,
             record_kind: str | None = None,
             min_confidence: float | None = None,
             max_confidence: float | None = None,
             recorded_from: str | None = None,
             recorded_to: str | None = None,
             source_run: str | None = None,
             entity_id: str | None = None) -> list[dict[str, Any]]:
    """Bounded, deterministic retrieval over knowledge_claims.

    Ordering: confidence DESC, then recency (rowid DESC). Capped at
    ``limit``. Fresh-only excludes expired claims (fresh_until < now).
    Optional dimensions (WP-24H-V2KB): record_kind, confidence band
    (min/max), recorded-time window (from/to on updated_at), source_run
    and entity linkage. Every dimension is exact-match; the result is a
    deterministic, provenance-carrying subset — never the whole vault.
    """
    clauses, args = ["mission_id = ?", "status = ?"], [mission_id, status]
    if claim_type:
        clauses.append("claim_type = ?")
        args.append(claim_type)
    if record_kind:
        clauses.append("record_kind = ?")
        args.append(record_kind)
    if min_confidence is not None:
        clauses.append("confidence >= ?")
        args.append(float(min_confidence))
    if max_confidence is not None:
        clauses.append("confidence <= ?")
        args.append(float(max_confidence))
    if recorded_from:
        clauses.append("updated_at >= ?")
        args.append(recorded_from)
    if recorded_to:
        clauses.append("updated_at <= ?")
        args.append(recorded_to)
    if source_run:
        clauses.append("source_run_id = ?")
        args.append(source_run)
    if entity_id:
        clauses.append(
            "claim_id IN (SELECT claim_id FROM knowledge_claim_entities "
            "WHERE entity_id = ?)")
        args.append(entity_id)
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

    # ``max_chars`` is caller-controlled, so retain a hard upper bound. The
    # objective and every retrieved section are untrusted content and must
    # not be able to bypass the context envelope by requesting a huge budget.
    budget = max(256, min(int(max_chars), MAX_MANIFEST_CHARS))
    bounded_objective = str(objective)
    objective_limit = min(MAX_OBJECTIVE_CHARS, max(32, budget // 4))
    if len(bounded_objective) > objective_limit:
        bounded_objective = bounded_objective[:objective_limit]
    manifest = Manifest(
        objective=bounded_objective,
        objective_hash=hashlib.sha256(objective.encode("utf-8")).hexdigest()[:16],
        generated_at=now,
    )
    serialized: list[dict[str, Any]] = []
    stale_serialized: list[dict[str, Any]] = []
    conflict_serialized: list[dict[str, Any]] = []
    used = len(json.dumps({
        "objective": manifest.objective,
        "objective_hash": manifest.objective_hash,
        "generated_at": manifest.generated_at,
    }, ensure_ascii=False))

    def add_bounded(target: list[dict[str, Any]], item: dict[str, Any]) -> bool:
        nonlocal used
        encoded = json.dumps(item, ensure_ascii=False)
        if used + len(encoded) > budget:
            manifest.truncated = True
            return False
        target.append(item)
        used += len(encoded)
        return True

    for claim in active:
        item = {"claim_id": claim["claim_id"],
                "statement": str(claim["statement"] or "")[:MAX_CLAIM_TEXT_CHARS],
                "claim_type": claim["claim_type"],
                "confidence": claim["confidence"],
                "fresh_until": claim["fresh_until"],
                "source_run_id": claim["source_run_id"],
                "status": claim["status"]}
        if not add_bounded(serialized, item):
            break
    if not manifest.truncated:
        for claim in stale:
            item = {"claim_id": claim["claim_id"],
                    "statement": str(claim["statement"] or "")[:MAX_CLAIM_TEXT_CHARS],
                    "claim_type": claim["claim_type"],
                    "confidence": claim["confidence"],
                    "fresh_until": claim["fresh_until"], "status": "STALE"}
            if not add_bounded(stale_serialized, item):
                break
    if not manifest.truncated:
        for conflict in conflicts:
            item = {key: str(conflict.get(key) or "")[:MAX_CLAIM_TEXT_CHARS]
                    for key in ("claim_a_id", "claim_b_id", "claim_type", "reason")}
            if not add_bounded(conflict_serialized, item):
                break
    manifest.active_claims = serialized
    manifest.stale_claims = stale_serialized
    manifest.conflicts = conflict_serialized
    return manifest._finalize()
