"""V5-10 cross-system connector/effect lineage proof."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.connectors.lineage import (
    LineageError,
    find_external_object,
    lineage_for_mission,
    record_lineage,
)
from jarvis_foundry.correlation.entities import (
    EntityRef,
    match_entities,
    register_entity,
)
from jarvis_foundry.database import FoundryDatabase


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_lineage_binds_mission_connector_effect_external_and_evidence(db: FoundryDatabase) -> None:
    first = record_lineage(
        db,
        resource_id="resource-http",
        mission_id="mission-1",
        connector_request_id="request-1",
        effect_ticket_id="effect-1",
        external_object_id="message:123",
        evidence_refs=("event:before", "event:after"),
        outcome="COMPLETED",
        source_refs=("mail:recipient@example.invalid",),
    )
    assert first.outcome == "COMPLETED"
    assert first.lineage_id
    duplicate = record_lineage(
        db,
        resource_id="resource-http",
        mission_id="mission-1",
        connector_request_id="request-1",
        effect_ticket_id="effect-1",
        external_object_id="message:123",
        evidence_refs=("event:before", "event:after"),
        outcome="COMPLETED",
        source_refs=("mail:recipient@example.invalid",),
    )
    assert duplicate.lineage_id == first.lineage_id
    rows = lineage_for_mission(db, "mission-1")
    assert len(rows) == 1
    assert rows[0]["external_object_id"] == "message:123"
    assert find_external_object(db, "message:123")["effect_ticket_id"] == "effect-1"


def test_missing_provider_id_is_unknown_and_exact_external_lookup_is_ambiguous(db: FoundryDatabase) -> None:
    missing = record_lineage(
        db,
        resource_id="resource-calendar",
        mission_id="mission-2",
        connector_request_id="request-2",
        effect_ticket_id="effect-2",
        external_object_id=None,
        evidence_refs=("event:timeout",),
        outcome="COMPLETED",
    )
    assert missing.outcome == "UNKNOWN"
    assert missing.ambiguity_reason == "missing_provider_id"
    record_lineage(
        db,
        resource_id="resource-a", mission_id="mission-3",
        connector_request_id="request-3", effect_ticket_id="effect-3",
        external_object_id="object:collision", evidence_refs=(), outcome="UNKNOWN",
    )
    record_lineage(
        db,
        resource_id="resource-b", mission_id="mission-4",
        connector_request_id="request-4", effect_ticket_id="effect-4",
        external_object_id="object:collision", evidence_refs=(), outcome="UNKNOWN",
    )
    with pytest.raises(LineageError, match="ambiguous"):
        find_external_object(db, "object:collision")
    assert find_external_object(db, "object:stale") is None


def test_entity_collisions_and_unicode_remain_explicit_ambiguity(db: FoundryDatabase) -> None:
    register_entity(db, entity_id="entity-a", kind="person", canonical_name="José", source_refs=(EntityRef("mail", "a"),))
    register_entity(db, entity_id="entity-b", kind="person", canonical_name="José", source_refs=(EntityRef("mail", "b"),))
    matches = match_entities(db, "José")
    assert {row[0] for row in matches} == {"entity-a", "entity-b"}
    with pytest.raises(ValueError, match="already bound"):
        register_entity(db, entity_id="entity-c", kind="person", canonical_name="Other", source_refs=(EntityRef("mail", "a"),))
