from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import ingest_observed_candidate
from jarvis_foundry.executive.decisions import ExecutiveDecisionService
from jarvis_foundry.executive.owner_context import load_owner_context, seed_owner_context
from jarvis_foundry.executive.scoring import A2GovernorBounds
from jarvis_foundry.fabric.catalog import record_outcome, register_capability
from jarvis_foundry.fabric.model import (
    CapabilitySpec,
    Kind,
    Semantic,
    UnattendedMode,
    VerifyVerdict,
)
from jarvis_foundry.research_queue import (
    candidate_research_state,
    complete_candidate_research,
    enqueue_candidate_research,
    start_candidate_research,
)


CAPABILITY = "write_registered_worktree_files"


def _database(tmp_path: Path) -> tuple[FoundryDatabase, ExecutiveDecisionService]:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    register_capability(db, CapabilitySpec(
        capability_id=CAPABILITY,
        version="1.0.0",
        kind=Kind.TOOL,
        action_kind="filesystem.write",
        semantic=Semantic.WRITE,
        executor="test",
        unattended=UnattendedMode.ALLOWED,
    ))
    record_outcome(
        db, CAPABILITY, success=True, verified=VerifyVerdict.MATCH,
        note="focused A2 test", at="2026-09-02T00:00:00Z",
    )
    service = ExecutiveDecisionService(
        db, min_action_score=0.10, wait_margin=0.0, source_id="a2-test",
    )
    return db, service


def _candidate(db: FoundryDatabase, objective: str, *, token: str) -> str:
    result = ingest_observed_candidate(
        db,
        "a2-observation-cycle",
        observation_id=f"observation-{token}",
        source_kind="PROJECT",
        source_identity="repo://owner/project",
        content_hash=f"sha256:{token * 64}",
        objective=objective,
        target_project="JARVIS Foundry",
        evidence=[{
            "source_ref": f"repo://owner/project/{token}",
            "dependencies": [],
        }],
        candidate_type="test_opportunity",
        trusted_requirements={
            "required_capability_ids": [CAPABILITY],
            "source": "a2-test-fixture",
        },
        expected_outcome="bounded repository improvement",
        confidence=0.8,
        expires_at="2099-01-01T00:00:00Z",
    )
    assert result["created"]
    return str(result["candidate_id"])


def test_a2_judgment_ranks_rejects_and_never_materializes(tmp_path: Path) -> None:
    db, service = _database(tmp_path)
    seed_owner_context(
        db,
        goals=[{
            "text": "Improve JARVIS reliability with bounded verified changes",
            "source_ref": "IDEA.md#north-star",
            "source_hash": "a" * 64,
        }],
        projects=[{
            "text": "JARVIS Foundry active project",
            "source_ref": "docs/CURRENT_STATE.md#program-state",
            "source_hash": "b" * 64,
        }],
        standing_rejections=[{
            "text": "Do not build voice localization",
            "source_ref": "docs/handoffs/frozen-roadmap.md#deferred",
            "source_hash": "c" * 64,
        }],
    )
    good = _candidate(
        db,
        "Improve JARVIS Foundry reliability and fix verified regression risk",
        token="1",
    )
    _candidate(db, "Build voice localization for JARVIS", token="2")

    decision = service.evaluate_a2(
        resource_id="resource-1",
        loop_id="a2-judgment-1",
        governor=A2GovernorBounds(do_nothing_score=0.10),
    )
    assert decision.kind == "SELECT"
    assert decision.candidate_id == good
    events = list(db.events())
    judgments = [event.payload for event in events if event.event_type == "discovery.candidate_judged"]
    assert {row["outcome"] for row in judgments} == {"ELIGIBLE", "REJECTED"}
    assert any(row["reason_code"] == "REVERSIBILITY_POLICY_BOUNDARY" for row in judgments)
    assert not any(event.event_type == "executive.candidate_admitted" for event in events)
    assert not any(event.event_type == "executive.objective_created" for event in events)
    assert not any(event.event_type == "mission.created" for event in events)
    assert not any(event.event_type == "executive.decision_materialized" for event in events)

    replay = service.evaluate_a2(
        resource_id="resource-1",
        loop_id="a2-judgment-1",
        governor=A2GovernorBounds(do_nothing_score=0.10),
    )
    assert replay.decision_id == decision.decision_id
    assert len([event for event in db.events() if event.event_type == "discovery.candidate_judged"]) == 2


def test_owner_context_requires_repository_provenance(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed_owner_context(
        db,
        goals=[{"text": "Ship JARVIS", "source_ref": "IDEA.md", "source_hash": "d" * 64}],
    )
    context = load_owner_context(db)
    assert context["goals"][0]["source_ref"] == "IDEA.md"
    assert context["authority"] == "ADVISORY_TIER_C_REPOSITORY_SEED"


def test_candidate_research_is_budgeted_one_pass_and_replay_safe(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    candidate_id = _candidate(
        db, "Research bounded JARVIS reliability evidence", token="4",
    )
    first = enqueue_candidate_research(
        db,
        candidate_id=candidate_id,
        cycle_id="cycle-a2",
        budget_used=0.5,
        source_ref="https://example.invalid/read-only",
        evidence={"authority": "DATA_ONLY"},
        attempted_at="2026-09-02T00:00:00Z",
    )
    replay = enqueue_candidate_research(
        db,
        candidate_id=candidate_id,
        cycle_id="cycle-a2",
        budget_used=0.5,
        attempted_at="2099-01-01T00:00:00Z",
    )
    assert replay["research_id"] == first["research_id"]
    assert replay["attempted_at"] == "2026-09-02T00:00:00Z"
    started = start_candidate_research(
        db, research_id=first["research_id"],
        started_at="2026-09-02T00:00:30Z",
    )
    assert started["status"] == "IN_PROGRESS"
    with pytest.raises(ValueError, match="exceeds reserved budget"):
        complete_candidate_research(
            db,
            research_id=first["research_id"],
            status="COMPLETED",
            evidence={"authority": "DATA_ONLY"},
            budget_used=0.75,
        )
    completed = complete_candidate_research(
        db,
        research_id=first["research_id"],
        status="COMPLETED",
        evidence={"finding": "bounded evidence", "authority": "DATA_ONLY"},
        completed_at="2026-09-02T00:01:00Z",
    )
    assert completed["content_hash"]
    assert candidate_research_state(db, candidate_id=candidate_id)[0]["status"] == "COMPLETED"
    blocked = enqueue_candidate_research(
        db,
        candidate_id=candidate_id,
        cycle_id="cycle-changed",
        pass_number=1,
    )
    assert blocked["status"] == "PASS_LIMIT"
    assert len([event for event in db.events() if event.event_type == "research.candidate_enqueued"]) == 1


def test_a2_preview_research_then_final_judgment_rescores_without_admission(
    tmp_path: Path,
) -> None:
    db, service = _database(tmp_path)
    candidate_id = _candidate(
        db, "Improve verified JARVIS reliability with bounded evidence", token="3",
    )
    before = service.preview_a2(
        resource_id="resource-1",
        governor=A2GovernorBounds(do_nothing_score=0.10),
    )
    assert before and before[0].candidate_id == candidate_id
    requirement_set = before[0].a2_metadata["capability_requirement_set"]
    assert requirement_set["contract_version"] == \
        "jarvis.v11.capability-requirements.v1"
    assert requirement_set["candidate_identity"]["candidate_id"] == candidate_id
    assert requirement_set["executable"] is True
    assert not any(
        event.event_type == "discovery.candidate_judged" for event in db.events()
    )
    queued = enqueue_candidate_research(
        db,
        candidate_id=candidate_id,
        cycle_id="cycle-research",
        budget_used=0.5,
        source_ref="repo://owner/project/research",
        evidence={"authority": "DATA_ONLY"},
        attempted_at="2026-09-02T00:00:00Z",
    )
    start_candidate_research(
        db, research_id=queued["research_id"],
        started_at="2026-09-02T00:00:30Z",
    )
    complete_candidate_research(
        db,
        research_id=queued["research_id"],
        status="COMPLETED",
        evidence={
            "authority": "DATA_ONLY",
            "confidence": 0.9,
            "finding": "bounded verified evidence",
        },
        completed_at="2026-09-02T00:01:00Z",
    )
    after = service.preview_a2(
        resource_id="resource-1",
        governor=A2GovernorBounds(do_nothing_score=0.10),
    )
    assert after[0].composite > before[0].composite
    assert after[0].a2_metadata["research"]["status"] == "COMPLETED"

    decision = service.evaluate_a2(
        resource_id="resource-1",
        loop_id="cycle-research-final",
        governor=A2GovernorBounds(do_nothing_score=0.10),
    )
    assert decision.candidate_id == candidate_id
    event_types = [event.event_type for event in db.events()]
    assert "discovery.candidate_judged" in event_types
    assert "executive.candidate_admitted" not in event_types
    assert "mission.created" not in event_types
