"""WP11 slice D (V2-OPERATIONAL): research queues.

Deterministic + append-only:
- detect_research_items scans knowledge claims and creates one queue
  item per (claim, reason) for STALE (fresh_until passed), CONTRADICTED
  (member of a contradiction pair), UNSUPPORTED (no source or source
  file missing) and WEAK (confidence below threshold) claims;
- low-value items (WEAK below the low-value floor) are REJECTED at
  creation with a recorded reason — no research activity is invented
  for trivia;
- generate_research_candidates turns OPEN items into discovery
  candidates (evidence = reason + claim provenance); the candidate
  pipeline dedups against existing candidates AND missions, so an item
  already covered resolves REJECTED with a reference;
- every mutation is an event (rebuild + reconcile deterministic).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Iterable, Mapping

from .database import FoundryDatabase
from .discovery_candidates import generate_candidate
from .events import Event
from .vault_retrieval import batch_support

WEAK_THRESHOLD = 0.2
LOW_VALUE_FLOOR = 0.1

_REASONS = ("STALE", "CONTRADICTED", "UNSUPPORTED", "WEAK")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _item_id(claim_id: str, reason: str) -> str:
    digest = hashlib.sha256(f"{claim_id}|{reason}".encode("utf-8")).hexdigest()
    return f"rq-{digest[:12]}"


def _existing_items(db: FoundryDatabase) -> set[tuple[str, str]]:
    rows = db.all("research_queue")
    return {(row["claim_id"], row["reason"]) for row in rows}


def detect_research_items(db: FoundryDatabase, *,
                          weak_threshold: float = WEAK_THRESHOLD,
                          low_value_floor: float = LOW_VALUE_FLOOR,
                          now: str | None = None,
                          vault_root: str | Path | None = None,
                          handoff_dir: str | Path | None = None,
                          research_dirs: Iterable[str | Path] = (),
                          repo_paths: Iterable[str | Path] = (),
                          ) -> list[dict[str, Any]]:
    """Scan claims and create queue items (one per claim per reason).

    Returns the newly created items (projected rows). Re-scans are
    no-ops for existing (claim, reason) pairs. UNSUPPORTED is
    source-kind-aware (V4-S1): the provided roots let handoff/repo
    claims resolve their sources; without them those kinds report
    UNSUPPORTED (no source = no support).
    """
    now = now or _now()
    existing = _existing_items(db)
    created_items: list[dict[str, Any]] = []
    claims = db.all("knowledge_claims")
    with db.connect() as connection:
        support = batch_support(connection, claims, vault_root=vault_root,
                                handoff_dir=handoff_dir,
                                research_dirs=research_dirs,
                                repo_paths=repo_paths)
        contradicted: set[str] = set()
        for row in connection.execute(
                "SELECT claim_a_id FROM knowledge_contradictions "
                "UNION SELECT claim_b_id FROM knowledge_contradictions"):
            contradicted.add(row[0])

        for claim in claims:
            claim_id = claim["claim_id"]
            reasons: list[tuple[str, dict[str, Any]]] = []
            fresh_until = claim.get("fresh_until")
            if fresh_until and fresh_until <= now:
                reasons.append(("STALE", {"fresh_until": fresh_until}))
            if claim_id in contradicted:
                reasons.append(("CONTRADICTED", {"contradiction": True}))
            if support.get(claim_id) == "UNSUPPORTED":
                reasons.append(("UNSUPPORTED", {
                    "source_event_id": claim.get("source_event_id")}))
            confidence = claim.get("confidence") or 0.0
            if confidence < weak_threshold:
                reasons.append(("WEAK", {"confidence": confidence}))

            for reason, evidence in reasons:
                item_id = _item_id(claim_id, reason)
                if (claim_id, reason) in existing:
                    continue
                status = "OPEN"
                if reason == "WEAK" and confidence < low_value_floor:
                    status = "REJECTED"
                    evidence["rejection_reason"] = (
                        "low-value research activity rejected: confidence "
                        f"{confidence} below floor {low_value_floor}")
                evidence["claim_statement"] = claim["statement"]
                db.append(Event.create(
                    "research.queue_item_created",
                    {"item_id": item_id, "claim_id": claim_id,
                     "reason": reason, "evidence": evidence,
                     "status": status},
                    source_type="supervisor", source_id="research-queue",
                    idempotency_key=f"research.queue_item_created:{item_id}"))
                created_items.append({
                    "item_id": item_id, "claim_id": claim_id,
                    "reason": reason, "evidence": evidence,
                    "status": status})
    return created_items


def generate_research_candidates(db: FoundryDatabase, *,
                                 mission_id: str = "mission-wfm-optimizer",
                                 ) -> dict[str, Any]:
    """Turn OPEN items into evidence-backed discovery candidates.

    Items whose objective already exists (candidate or mission) resolve
    REJECTED with a reference — the research is already covered.
    """
    rows = db.all("research_queue")
    open_items = [r for r in rows if r["status"] == "OPEN"]
    open_items.sort(key=lambda r: r["item_id"])
    created = rejected = 0
    for item in open_items:
        claim = db.row("knowledge_claims", "claim_id", item["claim_id"])
        statement = claim["statement"] if claim else item["claim_id"]
        result = generate_candidate(
            db, mission_id, source="research_queue",
            objective=statement,
            evidence=[{"reason": item["reason"],
                       "claim_id": item["claim_id"],
                       "item_id": item["item_id"]}])
        if result["created"]:
            status = "CANDIDATE_CREATED"
            evidence = {"candidate_id": result["candidate_id"]}
            created += 1
        else:
            status = "REJECTED"
            evidence = {"reference": result["duplicate_of"],
                        "reason": "already covered by an existing "
                                  "candidate or mission"}
            rejected += 1
        db.append(Event.create(
            "research.queue_item_resolved",
            {"item_id": item["item_id"], "status": status,
             "evidence": evidence},
            source_type="supervisor", source_id="research-queue",
            idempotency_key=f"research.queue_item_resolved:{item['item_id']}"))
    return {"candidates_created": created, "items_rejected": rejected}


# V11 A2 candidate research -------------------------------------------------

_CANDIDATE_RESEARCH_EVENTS = (
    "research.candidate_enqueued",
    "research.candidate_started",
    "research.candidate_completed",
)


def _candidate_research_id(candidate_id: str, input_hash: str, pass_number: int) -> str:
    material = f"{candidate_id}\x1f{input_hash}\x1f{pass_number}"
    return f"research-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


def _candidate_research_events(db: FoundryDatabase) -> list[Event]:
    return list(db.events(event_types=_CANDIDATE_RESEARCH_EVENTS, readonly=True))


def candidate_research_state(
    db: FoundryDatabase,
    *,
    candidate_id: str | None = None,
    cycle_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return one latest record per deterministic candidate research id."""

    clauses = ["candidate_id IS NOT NULL"]
    args: list[Any] = []
    if candidate_id is not None:
        clauses.append("candidate_id = ?")
        args.append(candidate_id)
    if cycle_id is not None:
        clauses.append("cycle_id = ?")
        args.append(cycle_id)
    try:
        with db.connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT * FROM research_queue WHERE " + " AND ".join(clauses)
                + " ORDER BY candidate_id, pass_number",
                tuple(args),
            ).fetchall()
        return [_research_queue_row(row) for row in rows]
    except sqlite3.OperationalError:
        # Compatibility for callers pinned to a pre-A2 schema.
        state: dict[str, dict[str, Any]] = {}
        for event in _candidate_research_events(db):
            payload = dict(event.payload)
            if candidate_id is not None and payload.get("candidate_id") != candidate_id:
                continue
            if cycle_id is not None and payload.get("cycle_id") != cycle_id:
                continue
            research_id = str(payload.get("research_id") or "")
            if research_id:
                state[research_id] = payload
        return [state[key] for key in sorted(state)]


def candidate_research_snapshot(
    db: FoundryDatabase,
    candidate_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Read the latest indexed research truth for a bounded candidate set."""

    ids = tuple(sorted({str(value) for value in candidate_ids if str(value)}))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with db.connect(readonly=True) as connection:
        rows = connection.execute(
            f"SELECT * FROM research_queue WHERE candidate_id IN ({placeholders}) "
            "ORDER BY candidate_id, pass_number DESC",
            ids,
        ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = _research_queue_row(row)
        result.setdefault(str(item["candidate_id"]), item)
    return result


def _research_queue_row(row: Any) -> dict[str, Any]:
    try:
        evidence = json.loads(str(row["evidence_json"] or "{}"))
    except (TypeError, ValueError):
        evidence = {}
    return {
        "research_id": str(row["item_id"]),
        "candidate_id": str(row["candidate_id"]),
        "cycle_id": str(row["cycle_id"] or ""),
        "pass_number": int(row["pass_number"] or 0),
        "status": str(row["status"]),
        "budget_used": float(row["budget_used"] or 0.0),
        "source_ref": row["source_ref"],
        "content_hash": row["content_hash"],
        "input_hash": row["input_hash"] if "input_hash" in row.keys() else None,
        "evidence": evidence,
        "attempted_at": row["started_at"],
        "completed_at": row["completed_at"],
        "recorded_at": row["updated_at"],
    }


def enqueue_candidate_research(
    db: FoundryDatabase,
    *,
    candidate_id: str,
    cycle_id: str,
    pass_number: int = 1,
    budget_used: float = 0.0,
    per_candidate_budget: float = 1.0,
    cycle_budget: float = 4.0,
    max_normal_passes: int = 1,
    source_ref: str | None = None,
    evidence: Mapping[str, Any] | None = None,
    attempted_at: str | None = None,
    input_hash: str | None = None,
) -> dict[str, Any]:
    """Enqueue one bounded research pass with deterministic replay identity.

    Budget units are caller-defined but must be finite non-negative numbers.
    Replaying the same candidate/cycle/pass returns the durable record without
    appending.  A2 permits at most one normal pass per candidate.
    """

    if not candidate_id or not cycle_id:
        raise ValueError("candidate_id and cycle_id are required")
    if pass_number < 1 or pass_number > max(0, int(max_normal_passes)):
        return {
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "pass_number": pass_number,
            "status": "PASS_LIMIT",
            "budget_used": 0.0,
        }
    try:
        requested = float(budget_used)
        candidate_cap = float(per_candidate_budget)
        cycle_cap = float(cycle_budget)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("research budgets must be numeric") from error
    if not all(value >= 0.0 and isfinite(value) for value in (
        requested, candidate_cap, cycle_cap
    )):
        raise ValueError("research budgets must be finite and non-negative")

    if input_hash is None:
        candidate = db.row("candidates", "candidate_id", candidate_id) or {}
        input_hash = str(candidate.get("provenance_hash") or candidate.get("fingerprint")
                         or f"legacy:{candidate_id}")
    input_hash = str(input_hash).strip()
    if not input_hash:
        raise ValueError("input_hash is required")
    research_id = _candidate_research_id(candidate_id, input_hash, pass_number)
    for row in candidate_research_state(db, candidate_id=candidate_id, cycle_id=cycle_id):
        if row.get("research_id") == research_id:
            return row
    prior_passes = candidate_research_state(db, candidate_id=candidate_id)
    if any(row.get("input_hash") == input_hash
           and int(row.get("pass_number") or 0) >= max_normal_passes
           for row in prior_passes):
        return {
            "research_id": research_id,
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "pass_number": pass_number,
            "status": "PASS_LIMIT",
            "budget_used": 0.0,
        }
    cycle_used = sum(float(row.get("budget_used") or 0.0) for row in
                     candidate_research_state(db, cycle_id=cycle_id))
    if requested > candidate_cap or cycle_used + requested > cycle_cap:
        return {
            "research_id": research_id,
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "pass_number": pass_number,
            "status": "BUDGET_BLOCKED",
            "budget_used": 0.0,
        }
    payload: dict[str, Any] = {
        "research_id": research_id,
        "candidate_id": candidate_id,
        "cycle_id": cycle_id,
        "pass_number": int(pass_number),
        "status": "ENQUEUED",
        "input_hash": input_hash,
        "budget_used": requested,
        "evidence": dict(evidence or {}),
        "attempted_at": attempted_at or _now(),
    }
    if source_ref:
        payload["source_ref"] = str(source_ref)
    db.append(Event.create(
        "research.candidate_enqueued",
        payload,
        source_type="supervisor",
        source_id="v11-a2-research",
        idempotency_key=f"research.candidate_enqueued:{research_id}",
    ))
    return payload


def complete_candidate_research(
    db: FoundryDatabase,
    *,
    research_id: str,
    status: str,
    evidence: Mapping[str, Any] | None = None,
    budget_used: float | None = None,
    source_ref: str | None = None,
    content_hash: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Complete a previously enqueued candidate research pass idempotently."""

    allowed = {"COMPLETED", "BLOCKED", "FAILED", "UNKNOWN"}
    normalized_status = str(status).upper()
    if normalized_status not in allowed:
        raise ValueError(f"unsupported candidate research status: {status}")
    try:
        with db.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT * FROM research_queue WHERE item_id = ? ",
                (research_id,),
            ).fetchone()
        current = _research_queue_row(row) if row is not None else None
    except sqlite3.OperationalError:
        rows = candidate_research_state(db)
        current = next((row for row in rows if row.get("research_id") == research_id), None)
    if current is None:
        raise ValueError(f"unknown candidate research id: {research_id}")
    if current.get("status") != "IN_PROGRESS":
        return current
    reserved = float(current.get("budget_used") or 0.0)
    used = reserved if budget_used is None else float(budget_used)
    if used < 0.0 or not isfinite(used):
        raise ValueError("research budget_used must be finite and non-negative")
    if used > reserved:
        raise ValueError(
            f"research budget_used {used} exceeds reserved budget {reserved}"
        )
    evidence_payload = dict(evidence or {})
    digest = content_hash or hashlib.sha256(
        json.dumps(evidence_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    payload: dict[str, Any] = {
        "research_id": research_id,
        "candidate_id": current["candidate_id"],
        "cycle_id": current["cycle_id"],
        "pass_number": int(current["pass_number"]),
        "status": normalized_status,
        "budget_used": used,
        "content_hash": digest,
        "evidence": evidence_payload,
        "attempted_at": current.get("attempted_at"),
        "completed_at": completed_at or _now(),
    }
    resolved_source = source_ref or current.get("source_ref")
    if resolved_source:
        payload["source_ref"] = str(resolved_source)
    db.append(Event.create(
        "research.candidate_completed",
        payload,
        source_type="supervisor",
        source_id="v11-a2-research",
        idempotency_key=f"research.candidate_completed:{research_id}",
    ))
    return payload


def start_candidate_research(
    db: FoundryDatabase,
    *,
    research_id: str,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Claim the single A2 research attempt before invoking a connector.

    ``IN_PROGRESS`` is durable. If the process dies after this event, replay
    does not invoke the reader again; the unfinished pass remains honest
    incomplete evidence rather than becoming an unbounded retry loop.
    """

    with db.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT * FROM research_queue WHERE item_id = ?",
            (research_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown candidate research id: {research_id}")
    current = _research_queue_row(row)
    if current.get("status") != "ENQUEUED":
        return current
    payload = {
        "research_id": research_id,
        "candidate_id": current["candidate_id"],
        "cycle_id": current["cycle_id"],
        "pass_number": int(current["pass_number"]),
        "status": "IN_PROGRESS",
        "budget_used": float(current.get("budget_used") or 0.0),
        "attempted_at": started_at or _now(),
    }
    db.append(Event.create(
        "research.candidate_started",
        payload,
        source_type="supervisor",
        source_id="v11-a2-research",
        idempotency_key=f"research.candidate_started:{research_id}",
    ))
    return payload


__all__ = [
    "candidate_research_snapshot",
    "candidate_research_state",
    "complete_candidate_research",
    "detect_research_items",
    "enqueue_candidate_research",
    "generate_research_candidates",
    "start_candidate_research",
]
