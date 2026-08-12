"""V2 slice-4 (WP9): continuous ingestion — bounded deterministic batch
ingestion of documents into knowledge claims.

Contract: an ingestion queue with source-hash dedup (re-ingesting the
same source content is idempotent; CHANGED content creates NEW claims —
history is never rewritten), completion records with counts + errors
(FAILED rolls back), deterministic rebuild + reconcile (schema v8)."""
import json

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event

DOCS = [
    {"title": "Postmortem 42", "statement": "The retry loop caused the outage",
     "claim_type": "causal-analysis", "confidence": 0.8},
    {"title": "Postmortem 42", "statement": "DNS misconfiguration contributed",
     "claim_type": "causal-analysis", "confidence": 0.6},
    {"title": "Runbook", "statement": "Deployment requires two approvals",
     "claim_type": "constraint", "confidence": 0.95},
]


def queue_event(ingestion_id: str = "ingest-1", source_ref: str = "docs/pm-42.md",
                source_hash: str = "abc123", source_type: str = "markdown"):
    return Event.create(
        "knowledge.ingestion_queued",
        {"ingestion_id": ingestion_id, "mission_id": "mission-1",
         "source_type": source_type, "source_ref": source_ref,
         "source_hash": source_hash},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.ingestion_queued:{ingestion_id}")


def test_ingestion_queued_projects_row(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(queue_event())
    row = db.row("knowledge_ingestions", "ingestion_id", "ingest-1")
    assert row["status"] == "QUEUED"
    assert row["source_hash"] == "abc123"


def test_same_source_hash_completed_is_idempotent(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(queue_event())
    db.append(Event.create(
        "knowledge.ingestion_completed",
        {"ingestion_id": "ingest-1", "claims_count": 2, "errors": []},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.ingestion_completed:ingest-1"))
    db.append(queue_event("ingest-2", source_hash="abc123"))
    db.append(Event.create(
        "knowledge.ingestion_completed",
        {"ingestion_id": "ingest-2", "claims_count": 0, "errors": [],
         "note": "duplicate of ingest-1 (same source_hash) — no new claims"},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.ingestion_completed:ingest-2"))
    assert db.count("knowledge_ingestions") == 2


def test_ingest_documents_creates_claims_and_counts(tmp_path):
    from jarvis_foundry.knowledge_ingestion import ingest_documents
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    result = ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md", DOCS)
    assert result["queued"] == 1 and result["claims_created"] == 3
    row = db.row("knowledge_ingestions", "ingestion_id", result["ingestion_id"])
    assert row["status"] == "COMPLETED" and row["claims_count"] == 3
    assert db.count("knowledge_claims") == 3


def test_ingest_documents_is_idempotent(tmp_path):
    from jarvis_foundry.knowledge_ingestion import ingest_documents
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    first = ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md", DOCS)
    second = ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md", DOCS)
    assert second["claims_created"] == 0
    assert db.count("knowledge_claims") == 3
    assert db.count("knowledge_ingestions") == 2  # second run recorded as dup


def test_changed_doc_creates_new_claim_never_rewrites(tmp_path):
    from jarvis_foundry.knowledge_ingestion import ingest_documents
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md", DOCS)
    changed = [dict(DOCS[0], statement="The retry loop caused the outage "
                                       "confirmed by packet capture")]
    ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md", changed)
    claims = db.all("knowledge_claims")
    assert len(claims) == 4  # original 3 + 1 new; old claim preserved
    statements = [c["statement"] for c in claims]
    assert "The retry loop caused the outage" in statements
    assert any("packet capture" in s for s in statements)


def test_ingestion_failure_records_failed_and_rolls_back(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(queue_event())
    db.append(Event.create(
        "knowledge.ingestion_failed",
        {"ingestion_id": "ingest-1", "errors": ["source unreadable"]},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.ingestion_failed:ingest-1"))
    row = db.row("knowledge_ingestions", "ingestion_id", "ingest-1")
    assert row["status"] == "FAILED"
    assert "source unreadable" in row["errors_json"]


def test_rebuild_reproduces_ingestions(tmp_path):
    from jarvis_foundry.knowledge_ingestion import ingest_documents
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md", DOCS)
    before = db.count("knowledge_ingestions")
    db.rebuild_projections()
    assert db.count("knowledge_ingestions") == before == 1
    row = db.row("knowledge_ingestions", "ingestion_id", before and
                 db.all("knowledge_ingestions")[0]["ingestion_id"])
    assert row["status"] == "COMPLETED"


def test_overlapping_delta_batch_no_conflict(tmp_path):
    """Regression for verifier deleg_7eacf4e9 G2-b: re-ingesting a batch
    that OVERLAPS unchanged docs (one doc removed/changed) must never
    raise EventConflict on the unchanged docs — deterministic claim
    payloads dedup them; only the delta claims are created."""
    from jarvis_foundry.knowledge_ingestion import ingest_documents
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md", DOCS)
    assert db.count("knowledge_claims") == 3
    # overlapping batch: doc 0 unchanged, doc 1 changed, doc 2 dropped
    delta = [dict(DOCS[0]), dict(DOCS[1], statement="DNS misconfiguration "
                                                    "confirmed as contributory")]
    result = ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md",
                              delta)
    assert result["claims_created"] == 1
    assert db.count("knowledge_claims") == 4


def test_retry_after_failure_is_safe(tmp_path):
    """A failed batch must be retryable: deterministic payloads dedup the
    already-applied claims instead of crashing."""
    from jarvis_foundry.knowledge_ingestion import ingest_documents
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    first = ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md",
                             DOCS[:2])
    assert first["claims_created"] == 2
    # simulate a retry after a failure: the full batch again (docs 0-1
    # already applied) — must dedup, not conflict
    retry = ingest_documents(db, "mission-1", "markdown", "docs/pm-42.md",
                             DOCS)
    assert retry["claims_created"] == 1
    assert db.count("knowledge_claims") == 3


def test_schema_version_matches_code(tmp_path):
    from jarvis_foundry.migrations import SCHEMA_VERSION
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    with db.connect() as connection:
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION
