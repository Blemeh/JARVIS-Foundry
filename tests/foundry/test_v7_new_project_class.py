"""Regression tests for the NEW_PROJECT portfolio work class.

Lane 3 (autonomous new projects) needs evidence-selected greenfield project
work to flow through the existing V7A executive portfolio engine instead of a
second selection system. These tests pin that NEW_PROJECT items register,
score/select, materialize into missions via the bridge, and record outcomes
exactly like every other work class.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.portfolio_bridge import (
    materialize_portfolio_selection,
)
from jarvis_foundry.evolution.executive_portfolio import (
    PortfolioPolicy,
    configure_portfolio_policy,
    record_portfolio_outcome,
    register_work_item,
    select_next_work,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "portfolio-new-project.db")
    database.initialize()
    return database


def _register_resource(database: FoundryDatabase, resource_id: str) -> None:
    database.append(Event.create(
        "project.registered",
        {
            "resource_id": resource_id,
            "kind": "new_project_repo",
            "root_path": "C:/temp/JARVIS-Autonomous-Project-Lab/taskward",
            "resolved_root": "C:/temp/JARVIS-Autonomous-Project-Lab/taskward",
            "baseline": {"head": "init"},
            "capabilities": {"write_registered_worktree_files": "ACTIVE"},
            "readiness_state": "READY",
        },
        source_type="test", source_id="test-new-project",
        idempotency_key=f"project.registered:{resource_id}",
    ))


def test_new_project_registers_selects_and_records_outcome(db: FoundryDatabase):
    configure_portfolio_policy(db, PortfolioPolicy())
    seq = register_work_item(
        db,
        item_id="new-project:taskward-20260824",
        kind="NEW_PROJECT",
        objective=(
            "Create taskward: agent-safe Windows Task-Scheduler-as-code "
            "toolkit selected from researched opportunities"
        ),
        expected_value=0.85,
        urgency=0.6,
        information_gain=0.7,
        confidence=0.75,
        effort=0.35,
        risk=0.25,
        reversibility=0.95,
        evidence_refs=[
            "docs/evidence/integration/autonomous-new-projects-20260824/"
            "03_CANDIDATE_CONTRACTS.json"
        ],
        metadata={"project_slug": "taskward"},
    )
    assert int(seq) > 0

    decision = select_next_work(db, loop_id="loop-np-1")
    assert decision.kind == "SELECT"
    assert decision.selected_class == "NEW_PROJECT"
    assert decision.entity_id == "new-project:taskward-20260824"

    outcome_seq = record_portfolio_outcome(
        db,
        outcome_id="outcome-np-1",
        selection_id=decision.decision_id,
        entity_id=decision.entity_id,
        kind="NEW_PROJECT",
        predicted={"expected_value": 0.85},
        actual={"mvp_accepted": True},
        status="PASSED",
    )
    assert int(outcome_seq) > 0


def test_new_project_duplicate_registration_is_idempotent(db: FoundryDatabase):
    kwargs = {
        "item_id": "new-project:dedupe-probe",
        "kind": "NEW_PROJECT",
        "objective": "idempotency probe",
    }
    first = register_work_item(db, **kwargs)
    second = register_work_item(db, **kwargs)
    assert int(first) == int(second)
    from jarvis_foundry.evolution.executive_portfolio import (
        rebuild_portfolio_state,
    )
    state = rebuild_portfolio_state(db)
    assert sum(1 for item_id in state.work_items
               if item_id == "new-project:dedupe-probe") == 1


def test_bridge_materializes_mission_for_new_project_selection(
        db: FoundryDatabase):
    configure_portfolio_policy(db, PortfolioPolicy())
    register_work_item(
        db,
        item_id="new-project:bridge-probe",
        kind="NEW_PROJECT",
        objective="materialize a NEW_PROJECT mission through the bridge",
        confidence=0.8,
        expected_value=0.8,
    )
    decision = select_next_work(db, loop_id="loop-np-bridge")
    _register_resource(db, "res-taskward")
    result = materialize_portfolio_selection(
        db,
        decision=decision.to_payload(),
        resource_id="res-taskward",
        source_id="test-new-project",
    )
    assert result["materialized"] is True
    assert result["blocked_reason"] is None
    assert str(result["mission_id"]).startswith("mission-")


def test_unsupported_class_still_rejected(db: FoundryDatabase):
    with pytest.raises(Exception):
        register_work_item(
            db,
            item_id="new-project:bad-class",
            kind="GARBAGE_CLASS",
            objective="must fail",
        )
