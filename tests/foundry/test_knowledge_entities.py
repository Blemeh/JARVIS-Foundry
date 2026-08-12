"""V2 slice-3 (WP9): entity resolution — registry + claim linking.

Contract: deterministic entity registry (canonical_name + aliases),
claim→entity links validated against existing claims/entities (F9
rollback pattern), canonicalized alias resolution, dedup/conflict on
re-delivery, deterministic rebuild + reconcile (schema v7)."""
import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def entity_event(entity_id: str = "entity-retry-loop",
                 canonical_name: str = "retry loop",
                 aliases: list | None = None,
                 mission_id: str = "mission-1"):
    return Event.create(
        "knowledge.entity_registered",
        {"entity_id": entity_id, "mission_id": mission_id,
         "canonical_name": canonical_name,
         "aliases": aliases or ["the retry loop", "Retry Loop"]},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.entity_registered:{entity_id}")


def claim_event(claim_id: str = "claim-e1", statement: str = "The retry loop caused the latency",
                claim_type: str = "causal-analysis"):
    return Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": "mission-1",
         "statement": statement, "claim_type": claim_type,
         "confidence": 0.8, "fresh_until": "2026-12-31T00:00:00Z",
         "source": {"run_id": "run-1", "event_id": f"event-{claim_id}"}},
        source_type="test", source_id="test-suite",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}")


def test_entity_registered_projects_row(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(entity_event())
    row = db.row("knowledge_entities", "entity_id", "entity-retry-loop")
    assert row["canonical_name"] == "retry loop"
    assert "the retry loop" in row["aliases_json"]


def test_entity_redelivery_dedup_and_conflict(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(entity_event())
    db.append(entity_event())  # identical -> dedup
    assert db.count("knowledge_entities") == 1
    with pytest.raises(Exception) as exc:
        db.append(entity_event(aliases=["CHANGED ALIAS"]))
    assert "conflict" in str(exc.value).lower() or "EventConflict" in str(exc.value)
    assert db.count("knowledge_entities") == 1


def test_alias_add_dedup(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(entity_event())
    db.append(Event.create(
        "knowledge.entity_alias_added",
        {"entity_id": "entity-retry-loop", "alias": "the retry mechanism"},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.entity_alias_added:entity-retry-loop:the-retry-mechanism"))
    db.append(Event.create(
        "knowledge.entity_alias_added",
        {"entity_id": "entity-retry-loop", "alias": "the retry mechanism"},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.entity_alias_added:entity-retry-loop:the-retry-mechanism"))
    row = db.row("knowledge_entities", "entity_id", "entity-retry-loop")
    assert row["aliases_json"].count("the retry mechanism") == 1


def test_claim_entity_link_validates_both_exist(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    db.append(entity_event())
    db.append(Event.create(
        "knowledge.claim_entity_linked",
        {"claim_id": "claim-e1", "entity_id": "entity-retry-loop",
         "role": "subject"},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.claim_entity_linked:claim-e1:entity-retry-loop"))
    links = db.all("knowledge_claim_entities")
    assert len(links) == 1
    assert links[0]["entity_id"] == "entity-retry-loop"
    # missing entity -> rollback
    with pytest.raises(Exception):
        db.append(Event.create(
            "knowledge.claim_entity_linked",
            {"claim_id": "claim-e1", "entity_id": "does-not-exist",
             "role": "subject"},
            source_type="test", source_id="test-suite",
            idempotency_key="knowledge.claim_entity_linked:claim-e1:nope"))
    assert db.count("knowledge_claim_entities") == 1
    # missing claim -> rollback
    with pytest.raises(Exception):
        db.append(Event.create(
            "knowledge.claim_entity_linked",
            {"claim_id": "no-claim", "entity_id": "entity-retry-loop",
             "role": "subject"},
            source_type="test", source_id="test-suite",
            idempotency_key="knowledge.claim_entity_linked:nope:entity-retry-loop"))
    assert db.count("knowledge_claim_entities") == 1


def test_canonicalized_alias_resolution(tmp_path):
    from jarvis_foundry.knowledge_entities import resolve_entity
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(entity_event())
    db.append(Event.create(
        "knowledge.entity_alias_added",
        {"entity_id": "entity-retry-loop", "alias": "THE   Retry Loop"},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.entity_alias_added:entity-retry-loop:alias2"))
    with db.connect() as connection:
        hit = resolve_entity(connection, "the retry loop")
        hit2 = resolve_entity(connection, "Retry Loop")
        miss = resolve_entity(connection, "the retry mechanism")
    assert hit == "entity-retry-loop" and hit2 == "entity-retry-loop"
    assert miss is None


def test_claims_for_entity_and_entities_for_claim(tmp_path):
    from jarvis_foundry.knowledge_entities import (claims_for_entity,
                                                   entities_for_claim)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    db.append(claim_event("claim-e2", "The retry loop also slowed recovery"))
    db.append(entity_event())
    for claim_id in ("claim-e1", "claim-e2"):
        db.append(Event.create(
            "knowledge.claim_entity_linked",
            {"claim_id": claim_id, "entity_id": "entity-retry-loop",
             "role": "subject"},
            source_type="test", source_id="test-suite",
            idempotency_key=f"knowledge.claim_entity_linked:{claim_id}:entity-retry-loop"))
    with db.connect() as connection:
        claims = claims_for_entity(connection, "entity-retry-loop")
        entities = entities_for_claim(connection, "claim-e1")
    assert sorted(claims) == ["claim-e1", "claim-e2"]
    assert entities == ["entity-retry-loop"]


def test_rebuild_reproduces_entities_and_links(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(claim_event())
    db.append(entity_event())
    db.append(Event.create(
        "knowledge.claim_entity_linked",
        {"claim_id": "claim-e1", "entity_id": "entity-retry-loop",
         "role": "subject"},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.claim_entity_linked:claim-e1:entity-retry-loop"))
    before = db.all("knowledge_claim_entities")
    db.rebuild_projections()
    after = db.all("knowledge_claim_entities")
    assert len(after) == len(before) == 1
    assert db.row("knowledge_entities", "entity_id", "entity-retry-loop")[
        "canonical_name"] == "retry loop"


def test_schema_version_matches_code(tmp_path):
    """The live schema version must equal the code's SCHEMA_VERSION
    (version-agnostic — pinned literals broke at each migration)."""
    from jarvis_foundry.migrations import SCHEMA_VERSION
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    with db.connect() as connection:
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION
