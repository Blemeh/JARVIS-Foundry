"""R7 forward-port gate: portfolio/goals/selection/WAITING via canonical main.

Adapted from the remaster R7 portfolio contract onto canonical main's
`evolution/portfolio.py` (V7-S7 portfolio executive) and directive model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution import portfolio
from jarvis_foundry.supervisor.evolution_control import EvolutionControlError

CTRL = "supervisor"


def _db(tmp_path: Path) -> FoundryDatabase:
    db = FoundryDatabase(tmp_path / "portfolio.db")
    db.initialize()
    return db


def _register(
    db: FoundryDatabase, project_id: str, *, ev: float, urgency: float
) -> None:
    portfolio.register_project(
        db,
        controller=CTRL,
        project_id=project_id,
        name=project_id,
        goals=[{"goal_id": f"goal-{project_id}", "title": f"Advance {project_id}"}],
        dependencies=[],
        risks=[],
        ev=ev,
        urgency=urgency,
        information_gain=0.5,
        reversibility=0.8,
        opportunity_cost=0.3,
        source_type="convergence-gate",
        source_id="r7",
    )


def test_portfolio_models_goals_and_selects_top_project(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _register(db, "p1", ev=0.9, urgency=0.9)
    _register(db, "p2", ev=0.3, urgency=0.2)
    scored = portfolio.score_projects(db)
    assert len(scored) == 2
    assert scored[0]["project_id"] == "p1"
    view = portfolio.project_view(db, "p1")
    assert any(goal["goal_id"] == "goal-p1" for goal in view["goals"])
    selected = portfolio.select_projects(
        db,
        controller=CTRL,
        available_slots=1,
        source_type="convergence-gate",
        source_id="r7",
    )
    assert [row["project_id"] for row in selected] == ["p1"]


def test_pause_excludes_and_reprioritize_changes_selection(tmp_path: Path) -> None:
    db = _tmp_db(tmp_path)
    _register(db, "p1", ev=0.9, urgency=0.9)
    _register(db, "p2", ev=0.3, urgency=0.2)
    portfolio.pause_project(
        db,
        "p1",
        controller=CTRL,
        reason="owner pause",
        source_type="convergence-gate",
        source_id="r7",
    )
    scored = portfolio.score_projects(db)
    p1_row = next(row for row in scored if row["project_id"] == "p1")
    # Paused projects score -inf and are excluded from selection (honest).
    assert p1_row["score"] == float("-inf")
    assert p1_row["status"] == "PAUSED"
    paused_selection = portfolio.select_projects(
        db,
        controller=CTRL,
        available_slots=1,
        source_type="convergence-gate",
        source_id="r7",
    )
    assert [row["project_id"] for row in paused_selection] == ["p2"]
    portfolio.resume_project(
        db,
        "p1",
        controller=CTRL,
        source_type="convergence-gate",
        source_id="r7",
    )
    portfolio.reprioritize_project(
        db,
        "p2",
        priority_bump=2.0,
        controller=CTRL,
        source_type="convergence-gate",
        source_id="r7",
    )
    selected = portfolio.select_projects(
        db,
        controller=CTRL,
        available_slots=1,
        source_type="convergence-gate",
        source_id="r7",
    )
    assert selected[0]["project_id"] == "p2"


def test_unknown_and_conflict_are_recorded(tmp_path: Path) -> None:
    db = _tmp_db(tmp_path)
    portfolio.record_unknown(
        db,
        "q1",
        "is the live release frozen?",
        controller=CTRL,
        source_type="convergence-gate",
        source_id="r7",
    )
    portfolio.record_conflict(
        db,
        "c1",
        "claim A contradicts claim B",
        controller=CTRL,
        source_type="convergence-gate",
        source_id="r7",
    )
    events = db.events()
    kinds = {e.event_type for e in events}
    assert "portfolio.unknown" in kinds
    assert "portfolio.conflict" in kinds


def _tmp_db(tmp_path: Path) -> FoundryDatabase:
    db = FoundryDatabase(tmp_path / "portfolio.db")
    db.initialize()
    return db


def test_non_supervisor_rejected(tmp_path: Path) -> None:
    db = _tmp_db(tmp_path)
    with pytest.raises(EvolutionControlError):
        portfolio.register_project(
            db,
            controller="candidate-self",
            project_id="x",
            name="x",
            source_type="convergence-gate",
            source_id="r7",
        )
