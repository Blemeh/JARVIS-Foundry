"""V7-S7 — portfolio executive: real projects/goals with provenance.

Gate matrix (TDD):
  1. projects/goals are recorded in the ledger with provenance and a
     governed whole-project view (goals, dependencies, risks, costs,
     capacity, deadlines, EV, urgency, information gain, reversibility,
     opportunity cost);
  2. scoring ranks projects deterministically (EV, urgency, information
     gain, reversibility, opportunity cost are explicit inputs);
  3. select/pause/resume/reprioritize are Supervisor-only and recorded;
  4. no routine owner prompts: decisions are recorded as supervisor
     decisions with reasons (the owner is only surfaced via the briefing
     stream, not asked per decision);
  5. selection respects capacity (a project cannot be selected when at
     capacity without pause/resume of another);
  6. unknown/conflicting facts are recorded as unknowns/conflicts, never
     silently resolved.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.evolution.portfolio import (
    PortfolioError, register_project, score_projects,
    select_projects, pause_project, resume_project, reprioritize_project,
    project_view, record_unknown, record_conflict,
)
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "portfolio.db")
    database.initialize()
    return database


def _project(project_id: str, *, ev: float = 10.0, urgency: float = 5.0,
             info_gain: float = 5.0, reversibility: float = 5.0,
             opp_cost: float = 5.0, deadline: str | None = None,
             status: str = "PLANNED", capacity_slots: int = 1) -> dict[str, object]:
    return {
        "project_id": project_id,
        "name": f"Project {project_id}",
        "goals": [{"id": f"{project_id}-g1", "text": "goal 1"}],
        "dependencies": [],
        "risks": [{"id": f"{project_id}-r1", "text": "risk 1",
                   "severity": "medium"}],
        "costs_usd": 0.0,
        "capacity_slots": 1,
        "deadline": deadline,
        "ev": ev,
        "urgency": urgency,
        "information_gain": info_gain,
        "reversibility": reversibility,
        "opportunity_cost": opp_cost,
        "status": status,
        "provenance": {"source": "V7-S7 test", "recorded_at": "2026-08-14"},
    }


def test_projects_are_recorded_with_provenance(db: FoundryDatabase) -> None:
    register_project(db, controller=SUPERVISOR_CONTROLLER,
                     **_project("p1"),
                     source_type="supervisor", source_id="v7")
    view = project_view(db, "p1")
    assert view["project_id"] == "p1"
    assert view["goals"][0]["text"] == "goal 1"
    assert view["risks"][0]["severity"] == "medium"
    assert view["ev"] == 10.0
    assert view["provenance"]["source"] == "V7-S7 test"


def test_register_requires_supervisor(db: FoundryDatabase) -> None:
    with pytest.raises(PortfolioError):
        register_project(db, controller="candidate-1", **_project("p1"),
                         source_type="supervisor", source_id="v7")


def test_scoring_is_deterministic_and_ranked(db: FoundryDatabase) -> None:
    register_project(db, controller=SUPERVISOR_CONTROLLER,
                     **_project("low", ev=1.0, urgency=1.0),
                     source_type="supervisor", source_id="v7")
    register_project(db, controller=SUPERVISOR_CONTROLLER,
                     **_project("high", ev=9.0, urgency=9.0),
                     source_type="supervisor", source_id="v7")
    register_project(db, controller=SUPERVISOR_CONTROLLER,
                     **_project("mid", ev=5.0, urgency=5.0),
                     source_type="supervisor", source_id="v7")
    ranked = score_projects(db)
    ids = [r["project_id"] for r in ranked]
    assert ids == ["high", "mid", "low"]
    # the same inputs yield the same scores (determinism)
    again = score_projects(db)
    assert [r["score"] for r in again] == [r["score"] for r in ranked]


def test_selection_respects_capacity_and_records_decisions(
        db: FoundryDatabase) -> None:
    register_project(db, controller=SUPERVISOR_CONTROLLER,
                     **_project("a", ev=8.0, capacity_slots=1),
                     source_type="supervisor", source_id="v7")
    register_project(db, controller=SUPERVISOR_CONTROLLER,
                     **_project("b", ev=7.0, capacity_slots=1),
                     source_type="supervisor", source_id="v7")
    selected = select_projects(db, controller=SUPERVISOR_CONTROLLER,
                               available_slots=1,
                               source_type="supervisor", source_id="v7")
    assert [s["project_id"] for s in selected] == ["a"]
    # b is deferred (honest WAITING: capacity)
    deferred = [e for e in db.events() if e.event_type == "portfolio.deferred"]
    assert deferred and deferred[-1].payload["project_id"] == "b"
    assert deferred[-1].payload["reason"] == "capacity"
    # a second selection with no free slots selects nothing new
    selected2 = select_projects(db, controller=SUPERVISOR_CONTROLLER,
                                available_slots=0,
                                source_type="supervisor", source_id="v7")
    assert selected2 == []


def test_pause_resume_and_reprioritize_are_recorded(
        db: FoundryDatabase) -> None:
    register_project(db, controller=SUPERVISOR_CONTROLLER,
                     **_project("a", ev=8.0),
                     source_type="supervisor", source_id="v7")
    register_project(db, controller=SUPERVISOR_CONTROLLER,
                     **_project("b", ev=7.0),
                     source_type="supervisor", source_id="v7")
    pause_project(db, "a", controller=SUPERVISOR_CONTROLLER,
                  reason="blocked on external dependency",
                  source_type="supervisor", source_id="v7")
    assert project_view(db, "a")["status"] == "PAUSED"
    paused = [e for e in db.events()
              if e.event_type == "portfolio.status_changed"]
    assert paused and paused[-1].payload["status"] == "PAUSED"
    ranked = score_projects(db)
    assert ranked[0]["project_id"] == "b"  # paused projects rank last
    resume_project(db, "a", controller=SUPERVISOR_CONTROLLER,
                   source_type="supervisor", source_id="v7")
    assert project_view(db, "a")["status"] == "PLANNED"
    reprioritize_project(db, "b", priority_bump=2,
                         controller=SUPERVISOR_CONTROLLER,
                         source_type="supervisor", source_id="v7")
    ranked2 = score_projects(db)
    assert ranked2[0]["project_id"] == "b"
    with pytest.raises(PortfolioError):
        pause_project(db, "a", controller="candidate-1",
                      reason="x", source_type="supervisor", source_id="v7")


def test_unknowns_and_conflicts_are_recorded_not_resolved(
        db: FoundryDatabase) -> None:
    record_unknown(db, "q1", "is the external API key rotated?",
                   controller=SUPERVISOR_CONTROLLER,
                   source_type="supervisor", source_id="v7")
    record_conflict(db, "c1", "two monitors disagree on SLA breach",
                    controller=SUPERVISOR_CONTROLLER,
                    source_type="supervisor", source_id="v7")
    unknowns = [e for e in db.events() if e.event_type == "portfolio.unknown"]
    conflicts = [e for e in db.events()
                 if e.event_type == "portfolio.conflict"]
    assert unknowns and unknowns[-1].payload["question"] == \
        "is the external API key rotated?"
    assert conflicts and conflicts[-1].payload["description"] == \
        "two monitors disagree on SLA breach"
    with pytest.raises(PortfolioError):
        record_unknown(db, "q2", "x", controller="candidate-1",
                       source_type="supervisor", source_id="v7")


def test_no_routine_owner_prompts_decisions_are_recorded(
        db: FoundryDatabase) -> None:
    """Selection decisions carry reasons and provenance; they never block
    on an owner prompt (the briefing stream is the owner surface)."""
    register_project(db, controller=SUPERVISOR_CONTROLLER,
                     **_project("a", ev=6.0),
                     source_type="supervisor", source_id="v7")
    selected = select_projects(db, controller=SUPERVISOR_CONTROLLER,
                               available_slots=1,
                               source_type="supervisor", source_id="v7")
    assert selected[0]["project_id"] == "a"
    decision = [e for e in db.events()
                if e.event_type == "portfolio.decision"]
    assert decision
    assert decision[-1].payload["controller"] == "supervisor"
    assert decision[-1].payload["reason"]
