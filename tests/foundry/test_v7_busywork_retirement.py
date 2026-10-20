"""Regression: unscoped immutable V6 records are quarantined from selection.

Live busywork incident 2026-08-24: an IMPROVEMENT item derived from a V6
improvement record kept being re-selected after PASSED outcomes because
derived items rebuild from the source table and never consume. Unscoped
improvements now stop at the V6 implementation gate. A durable block record
quarantines that immutable finding; future execution requires a newly
scoped record identity.
"""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.portfolio_bridge import (
    materialize_portfolio_selection,
)
from jarvis_foundry.evolution.executive_portfolio import (
    _improvement_items,
    select_next_work,
)


def _database(tmp_path: Path) -> FoundryDatabase:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(Event.create(
        "project.registered",
        {
            "resource_id": "resource-x",
            "kind": "git_worktree",
            "root_path": "/tmp/worktree",
            "resolved_root": "/tmp/worktree",
            "baseline": {"head": "base"},
            "capabilities": {"write_registered_worktree_files": "ACTIVE"},
            "readiness_state": "READY",
        },
        source_type="test", source_id="v7-busywork-tests",
        idempotency_key="project.registered:resource-x",
    ))
    return db


def test_unscoped_blocked_improvement_is_not_readmitted(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.append(Event.create(
        "improvement.recorded",
        {
            "improvement_id": "imp-busywork", "state": "FROZEN",
            "problem": "bounded local improvement",
            "evidence_refs": ["evidence:test:imp-busywork"],
            "provenance": {"source": "test"},
            "hypothesis": "retiring derived items prevents busywork",
            "expected_mechanism": "durable mirror consumed on terminal outcome",
            "baseline_metric": 1.0, "mmi": 0.5,
            "guardrails": ["local only"], "workload": "unit",
            "observation": {"window": "test"},
            "risk_class": "R1", "risk_class_declared": True,
            "surfaces": ["portfolio"], "budget": {"usd": 0.0},
            "reversibility": "reversible",
        },
        source_type="test", source_id="v7-busywork-tests",
        idempotency_key="improvement.recorded:imp-busywork",
    ))
    first = select_next_work(db, loop_id="busy-1")
    assert first.kind == "SELECT"
    result = materialize_portfolio_selection(
        db, decision=first.to_payload(), resource_id="resource-x")
    assert result["materialized"] is False
    assert result["mission_id"] is None
    assert db.event_count(event_type="mission.created") == 0
    assert not any(
        item.item_id == "improvement:imp-busywork"
        for item in _improvement_items(db)
    )

    second = select_next_work(db, loop_id="busy-2")
    ranked = second.entity_id if second.kind == "SELECT" else None
    assert ranked != "improvement:imp-busywork", (
        "completed derived item was re-admitted (busywork)")
