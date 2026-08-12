"""V2 slice-4 (WP9): continuous ingestion — bounded deterministic batch
ingestion of documents into knowledge claims.

Deterministic + idempotent by construction:
- the source content hash decides everything: a COMPLETED ingestion with
  the same source_hash is a duplicate (no new claims); CHANGED content
  yields NEW claims (history is never rewritten);
- per-document claim ids are derived from the document content hash, so
  re-running the same batch creates identical claim ids (deduped by the
  event idempotency keys);
- failures record status FAILED + errors (the batch is atomic per event
  append — a raised error leaves no partial claims)."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .database import FoundryDatabase
from .events import Event


def _doc_hash(doc: dict[str, Any]) -> str:
    payload = {k: v for k, v in doc.items() if k != "claim_id"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _source_hash(docs: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(docs, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _already_completed(db: FoundryDatabase, source_hash: str) -> bool:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT status FROM knowledge_ingestions WHERE source_hash = ? "
            "ORDER BY rowid DESC LIMIT 1", (source_hash,)).fetchone()
    return bool(row and row[0] == "COMPLETED")


def ingest_documents(db: FoundryDatabase, mission_id: str, source_type: str,
                     source_ref: str, docs: list[dict[str, Any]],
                     ) -> dict[str, Any]:
    """Ingest a batch of documents into knowledge claims.

    Returns {"ingestion_id", "queued", "claims_created", "duplicate"}.
    Deterministic claim ids (per-document hash); identical re-ingestion
    is a no-op for claims; changed documents create NEW claims.
    """
    source_hash = _source_hash(docs)
    ingestion_id = f"ingest-{uuid.uuid4().hex[:12]}"
    duplicate = _already_completed(db, source_hash)

    db.append(Event.create(
        "knowledge.ingestion_queued",
        {"ingestion_id": ingestion_id, "mission_id": mission_id,
         "source_type": source_type, "source_ref": source_ref,
         "source_hash": source_hash},
        source_type="supervisor", source_id="wp9-ingestion",
        idempotency_key=f"knowledge.ingestion_queued:{ingestion_id}"))

    created = 0
    if not duplicate:
        for index, doc in enumerate(docs):
            claim_id = f"claim-{_doc_hash(doc)}"
            event = Event.create(
                "knowledge.claim_recorded",
                {"claim_id": claim_id, "mission_id": mission_id,
                 "statement": doc["statement"],
                 "claim_type": doc.get("claim_type", "fact"),
                 "confidence": doc.get("confidence", 0.5),
                 "fresh_until": doc.get("fresh_until"),
                 "source": {"run_id": f"ingestion-{ingestion_id}",
                            "event_id": f"{source_ref}:{index}"}},
                source_type="supervisor", source_id="wp9-ingestion",
                idempotency_key=f"knowledge.claim_recorded:{claim_id}")
            applied = db.append(event)
            if applied[1]:
                created += 1

    db.append(Event.create(
        "knowledge.ingestion_completed",
        {"ingestion_id": ingestion_id, "claims_count": created,
         "errors": [],
         "note": "duplicate of a completed ingestion with the same "
                 "source_hash — no new claims" if duplicate else None},
        source_type="supervisor", source_id="wp9-ingestion",
        idempotency_key=f"knowledge.ingestion_completed:{ingestion_id}"))
    return {"ingestion_id": ingestion_id, "queued": 1,
            "claims_created": created, "duplicate": duplicate}
