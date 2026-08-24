"""Phase H — new-project lifecycle memory (Lane 3 shape).

A newly created project becomes a first-class institutional entity:
- selected and rejected ideas stay distinguishable;
- rejected idea rationale is durably retrievable (so JARVIS does not rebuild
  the same bad project next week);
- the project entity links repo/resource identity;
- architecture decisions are versioned/supersedable claims;
- an accepted MVP outcome becomes durable project state.
"""
from __future__ import annotations

import uuid

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.hybrid_memory import RecallEngine
from jarvis_foundry.software_intelligence.writeback import (
    record_engineering_outcome,
    record_research,
    render_project_note,
)
from obsidian_fixture import make_service
from jarvis_foundry.software_intelligence.writeback import write_project_note


@pytest.fixture()
def db(tmp_path):
    database = FoundryDatabase(tmp_path / "lifecycle.db")
    database.initialize()
    return database


class TestNewProjectLifecycle:
    def test_selected_vs_rejected_ideas_distinguishable(self, db, tmp_path):
        # candidate machinery: two ideas generated, one rejected with reason,
        # one approved into a mission (canonical discovery events)
        from jarvis_foundry.discovery_candidates import (
            generate_candidate, reject_candidate)

        gen = generate_candidate(db, "mission-lane3", source="opportunity",
                                 objective="build realtime queue simulator",
                                 evidence=[{"idea": "simulator"}])
        alt = generate_candidate(db, "mission-lane3", source="opportunity",
                                 objective="build blockchain todo list",
                                 evidence=[{"idea": "blockchain-todo"}])
        assert gen["created"] and alt["created"]
        reject_candidate(db, alt["candidate_id"],
                         reason="rejected: no measurable user value; "
                                "maintenance cost exceeds benefit")
        row = db.row("candidates", "candidate_id", alt["candidate_id"])
        assert row["status"] == "REJECTED"
        kept = db.row("candidates", "candidate_id", gen["candidate_id"])
        assert kept["status"] != "REJECTED"
        # rationale retrievable from durable state alone (evidence_json)
        import json as _json
        evidence = _json.loads(row["evidence_json"] or "[]")
        joined = " ".join(str(e) for e in evidence)
        assert "no measurable user value" in joined

    def test_rejected_rationale_survives_into_memory(self, db):
        from jarvis_foundry.software_intelligence.writeback import (
            record_software_observation)
        mid = record_software_observation(
            db, mission_id="m-lane3",
            content="REJECTED IDEA blockchain todo list: no measurable user "
                    "value; maintenance cost exceeds benefit. Do not "
                    "regenerate this project shape.",
            source_ids=["candidate:alt-1"])
        from jarvis_foundry.hybrid_memory import CurationPipeline

        CurationPipeline(db).validate_and_accept(
            mid, placement="CORE", actor="software-intelligence",
            require_independent=False)
        hits = RecallEngine(db).recall("blockchain todo list project idea")
        assert any("REJECTED IDEA" in h["content"] for h in hits)

    def test_project_entity_links_resource(self, db, tmp_path):
        from jarvis_foundry.control.registration import registration_event
        from jarvis_foundry.connectors.resources import discover_resource
        proj_root = tmp_path / "newproj"
        proj_root.mkdir(parents=True)
        rec = discover_resource(
            "local_repository",
            str(proj_root),
            provenance={"hosted_slugs": [], "registered_via": "lane3"})
        db.append(registration_event(rec, source_id="lane3-test"))
        entity_id = f"entity-{uuid.uuid4().hex[:8]}"
        db.append(Event.create(
            "knowledge.entity_registered",
            {"entity_id": entity_id, "mission_id": "m-lane3",
             "canonical_name": "QueueSim", "aliases": ["queue-simulator"],
             "entity_type": "project"},
            source_type="supervisor", source_id="test",
            idempotency_key=f"entity:{entity_id}"))
        db.append(Event.create(
            "knowledge.entity_alias_added",
            {"entity_id": entity_id, "alias": rec["resource_id"]},
            source_type="supervisor", source_id="test",
            idempotency_key=f"entity-alias:{entity_id}:{rec['resource_id']}"))
        row = db.row("knowledge_entities", "entity_id", entity_id)
        assert rec["resource_id"] in str(row["aliases_json"])

    def test_architecture_decision_supersedable(self, db):
        first = record_research(
            db, mission_id="m-lane3", question="storage engine choice",
            findings=[{"statement": "decision: use SQLite WAL for v0",
                       "claim_type": "decision"}],
            source_type="design", source_ref="arch-rfc-1")
        second = record_research(
            db, mission_id="m-lane3", question="storage engine choice",
            findings=[{"statement": "decision: move to Postgres for v1 "
                                    "(concurrent writers required)",
                       "claim_type": "decision"}],
            source_type="design", source_ref="arch-rfc-2",
            supersedes_claim_id=first.claim_ids[0])
        old_row = db.row("knowledge_claims", "claim_id", first.claim_ids[0])
        new_row = db.row("knowledge_claims", "claim_id", second.claim_ids[0])
        assert old_row["status"] == "SUPERSEDED"
        assert old_row["superseded_by"] == new_row["claim_id"]
        assert new_row["status"] == "ACCEPTED"

    def test_accepted_mvp_outcome_becomes_durable_state(self, db):
        db.append(Event.create(
            "mission.created",
            {"mission_id": "m-mvp", "objective": "ship MVP slice",
             "acceptance_criteria": ["verifier pass"]},
            source_type="supervisor", source_id="test",
            idempotency_key="mission.created:m-mvp"))
        for state in ("ORIENTING", "ACTION_SELECTION", "COMPLETE"):
            db.append(Event.create(
                "mission.state_changed",
                {"mission_id": "m-mvp", "state": state},
                source_type="supervisor", source_id="test",
                idempotency_key=f"m-mvp:{state}"))
        result = record_engineering_outcome(
            db, mission_id="m-mvp", step_id="s1", objective="ship MVP slice",
            builder_run_id="b9", verifier_run_id="v9",
            criteria=["verifier pass"], verdict="PASS", findings=[],
            evidence_bundle_id="bundle-mvp", commit_sha="deadbee",
            lessons=["wake condition: revisit after first real user cohort"])
        outcome_claim = None
        for cid in result.claim_ids:
            row = db.row("knowledge_claims", "claim_id", cid)
            if row and row["claim_type"] == "outcome":
                outcome_claim = row
        assert outcome_claim is not None
        assert "verifier PASS" in outcome_claim["statement"]
