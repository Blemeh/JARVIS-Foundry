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
from datetime import datetime, timezone
from typing import Any

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
