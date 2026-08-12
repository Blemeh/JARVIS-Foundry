"""WP11 slice D (V2-OPERATIONAL): research queues.

Contract: detect_research_items scans knowledge claims for STALE,
CONTRADICTED, UNSUPPORTED and WEAK claims (one item per claim per
reason; re-scans dedup); low-value items (WEAK below 0.1) are REJECTED
immediately with a recorded reason; generate_research_candidates turns
OPEN items into evidence-backed discovery candidates (deduped against
candidates AND missions; items covered by an existing candidate or
mission resolve REJECTED with a reference); rebuild + reconcile
deterministic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def _claim(db: FoundryDatabase, claim_id: str, statement: str, *,
           mission_id: str = "mission-1", claim_type: str = "fact",
           confidence: float = 0.5, fresh_until: str | None = None,
           source_event: str | None = None) -> None:
    db.append(Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": mission_id,
         "statement": statement, "claim_type": claim_type,
         "confidence": confidence, "fresh_until": fresh_until,
         "source": {"run_id": f"doc-{claim_id}", "event_id": source_event}},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}"))


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_stale_claim_creates_item(db: FoundryDatabase):
    _claim(db, "c1", "The old scheduler runs at midnight.",
           fresh_until="2000-01-01T00:00:00Z")
    from jarvis_foundry.research_queue import detect_research_items
    items = detect_research_items(db)
    stale = [i for i in items if i["reason"] == "STALE"]
    assert len(stale) == 1
    assert stale[0]["claim_id"] == "c1"


def test_contradicted_claim_creates_item(db: FoundryDatabase):
    _claim(db, "c1", "The gateway retries.")
    _claim(db, "c2", "The gateway never retries.")
    from jarvis_foundry.research_queue import detect_research_items
    items = detect_research_items(db)
    reasons = {i["reason"] for i in items}
    assert "CONTRADICTED" in reasons


def test_unsupported_claim_creates_item(db: FoundryDatabase):
    _claim(db, "c1", "The gateway retries.",
           source_event="JARVIS/V1/ghost.md")
    from jarvis_foundry.research_queue import detect_research_items
    items = detect_research_items(db)
    assert any(i["reason"] == "UNSUPPORTED" for i in items)


def test_supported_claim_no_item(db: FoundryDatabase, tmp_path: Path):
    from jarvis_foundry.vault_indexer import sync_vault
    from jarvis_foundry.knowledge_extract import extract_statements
    from jarvis_foundry.live_ingestion import run_ingestion_cycle
    vault = tmp_path / "vault"
    (vault / "JARVIS" / "V1").mkdir(parents=True)
    (vault / "JARVIS" / "V1" / "ok.md").write_text(
        "The gateway retries.\n", encoding="utf-8", newline="")
    sync_vault(db, vault)
    run_ingestion_cycle(db, sources=["vault"], vault_root=vault)
    from jarvis_foundry.research_queue import detect_research_items
    items = detect_research_items(db)
    assert not any(i["reason"] == "UNSUPPORTED" for i in items)


def test_weak_low_value_rejected_immediately(db: FoundryDatabase):
    _claim(db, "c1", "A tiny cosmetic label thought.", confidence=0.05)
    from jarvis_foundry.research_queue import detect_research_items
    items = detect_research_items(db)
    weak = [i for i in items if i["reason"] == "WEAK"]
    assert len(weak) == 1
    assert weak[0]["status"] == "REJECTED"
    assert "low-value" in weak[0]["evidence"]["rejection_reason"]


def test_weak_but_valuable_stays_open(db: FoundryDatabase):
    _claim(db, "c1", "A moderately confident guess.", confidence=0.15)
    from jarvis_foundry.research_queue import detect_research_items
    items = detect_research_items(db)
    weak = [i for i in items if i["reason"] == "WEAK"]
    assert weak[0]["status"] == "OPEN"


def test_rescan_dedupes(db: FoundryDatabase):
    _claim(db, "c1", "The old scheduler runs at midnight.",
           fresh_until="2000-01-01T00:00:00Z")
    from jarvis_foundry.research_queue import detect_research_items
    first = detect_research_items(db)
    assert len([i for i in first if i["reason"] == "STALE"]) == 1
    second = detect_research_items(db)
    assert second == []  # nothing new: (claim, reason) pairs already exist
    rows = db.all("research_queue")
    assert len([r for r in rows if r["reason"] == "STALE"]) == 1


def test_generate_candidates_creates_candidate(db: FoundryDatabase):
    _claim(db, "c1", "Investigate the nightly integration failure.",
           fresh_until="2000-01-01T00:00:00Z")
    from jarvis_foundry.research_queue import (detect_research_items,
                                               generate_research_candidates)
    detect_research_items(db)
    result = generate_research_candidates(db)
    assert result["candidates_created"] == 1
    candidates = db.all("candidates")
    assert candidates[0]["source"] == "research_queue"
    rows = db.all("research_queue")
    assert rows[0]["status"] == "CANDIDATE_CREATED"


def test_duplicate_objective_resolves_rejected(db: FoundryDatabase):
    db.append(Event.create(
        "mission.created",
        {"mission_id": "mission-x", "step_id": "step-0",
         "objective": "Investigate the nightly integration failure",
         "role": "builder", "acceptance_criteria": ["done"]},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-x"))
    _claim(db, "c1", "Investigate the nightly integration failure.",
           fresh_until="2000-01-01T00:00:00Z")
    from jarvis_foundry.research_queue import (detect_research_items,
                                               generate_research_candidates)
    detect_research_items(db)
    result = generate_research_candidates(db)
    assert result["candidates_created"] == 0
    rows = db.all("research_queue")
    resolved = [r for r in rows if r["status"] == "REJECTED"]
    assert resolved  # the covered item(s) resolved REJECTED
    assert any("mission-x" in r["evidence_json"] for r in resolved)


def test_rebuild_and_reconcile_deterministic(db: FoundryDatabase):
    _claim(db, "c1", "The old scheduler runs at midnight.",
           fresh_until="2000-01-01T00:00:00Z")
    from jarvis_foundry.research_queue import detect_research_items
    detect_research_items(db)
    from jarvis_foundry.reconcile import reconcile
    before = db.all("research_queue")
    db.rebuild_projections()
    assert db.all("research_queue") == before
    assert reconcile(db).consistent
