"""V7-S7 — portfolio executive: real projects/goals with provenance.

Projects, goals, dependencies, risks, costs, capacity, deadlines, EV,
urgency, information gain, reversibility and opportunity cost are recorded
in the ledger; scoring/selection/pause/resume/reprioritize are
Supervisor-only decisions recorded with reasons (no routine owner prompts);
unknowns and conflicts are recorded, never silently resolved.
"""
from __future__ import annotations

import hashlib

import json
from dataclasses import dataclass, field
from typing import Any

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER, EvolutionControlError,
)
from jarvis_foundry.supervisor.estop import EmergencyStop

PortfolioError = EvolutionControlError

SCORE_WEIGHTS = {
    "ev": 1.0, "urgency": 1.0, "information_gain": 1.0,
    "reversibility": 1.0, "opportunity_cost": -1.0,
}


@dataclass
class Project:
    project_id: str
    name: str = ""
    goals: list[dict[str, object]] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risks: list[dict[str, object]] = field(default_factory=list)
    costs_usd: float = 0.0
    capacity_slots: int = 1
    deadline: str | None = None
    ev: float = 0.0
    urgency: float = 0.0
    information_gain: float = 0.0
    reversibility: float = 0.0
    opportunity_cost: float = 0.0
    status: str = "PLANNED"
    priority_bump: float = 0.0
    provenance: dict[str, object] = field(default_factory=dict)
    sequence: int = 0


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PortfolioError(message)


def _gate(database: FoundryDatabase, controller: str) -> None:
    _require(controller == SUPERVISOR_CONTROLLER,
             "only the Supervisor can manage the portfolio")
    _require(not EmergencyStop(database.path.parent / "EMERGENCY_STOP").active(),
             "STOP sentinel active")


def _rebuild(database: FoundryDatabase) -> dict[str, Project]:
    projects: dict[str, Project] = {}
    for _stream_index, event in enumerate(database.events()):
        payload = event.payload
        if event.event_type == "portfolio.project_registered":
            project = Project(
                project_id=payload["project_id"],
                name=payload.get("name", ""),
                goals=list(payload.get("goals", [])),
                dependencies=list(payload.get("dependencies", [])),
                risks=list(payload.get("risks", [])),
                costs_usd=float(payload.get("costs_usd", 0.0)),
                capacity_slots=int(payload.get("capacity_slots", 1)),
                deadline=payload.get("deadline"),
                ev=float(payload.get("ev", 0.0)),
                urgency=float(payload.get("urgency", 0.0)),
                information_gain=float(payload.get("information_gain", 0.0)),
                reversibility=float(payload.get("reversibility", 0.0)),
                opportunity_cost=float(payload.get("opportunity_cost", 0.0)),
                provenance=dict(payload.get("provenance", {})),
                sequence=_stream_index)
            projects[project.project_id] = project
        elif event.event_type == "portfolio.status_changed":
            project = projects.get(payload["project_id"])
            if project is not None:
                project.status = payload["status"]
        elif event.event_type == "portfolio.reprioritized":
            project = projects.get(payload["project_id"])
            if project is not None:
                project.priority_bump += float(payload.get("bump", 0.0))
    return projects


def register_project(database: FoundryDatabase, *, controller: str,
                     project_id: str, name: str = "",
                     goals: list[dict[str, object]] | None = None,
                     dependencies: list[str] | None = None,
                     risks: list[dict[str, object]] | None = None,
                     costs_usd: float = 0.0, capacity_slots: int = 1,
                     deadline: str | None = None, ev: float = 0.0,
                     urgency: float = 0.0, information_gain: float = 0.0,
                     reversibility: float = 0.0, opportunity_cost: float = 0.0,
                     status: str = "PLANNED",
                     provenance: dict[str, object] | None = None,
                     source_type: str, source_id: str) -> str:
    _gate(database, controller)
    payload = {
        "project_id": project_id, "name": name,
        "goals": list(goals or []), "dependencies": list(dependencies or []),
        "risks": list(risks or []), "costs_usd": float(costs_usd),
        "capacity_slots": int(capacity_slots), "deadline": deadline,
        "ev": float(ev), "urgency": float(urgency),
        "information_gain": float(information_gain),
        "reversibility": float(reversibility),
        "opportunity_cost": float(opportunity_cost),
        "status": status, "provenance": dict(provenance or {}),
        "controller": controller,
    }
    content = json.dumps(payload, sort_keys=True)
    event = Event.create(
        "portfolio.project_registered", payload,
        idempotency_key=f"portfolio.project_registered:{project_id}:"
                        f"{hashlib.sha256(content.encode('utf-8')).hexdigest()[:8]}",
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


def project_view(database: FoundryDatabase, project_id: str) -> dict[str, Any]:
    projects = _rebuild(database)
    project = projects.get(project_id)
    if project is None:
        raise PortfolioError(f"unknown project: {project_id}")
    return {
        "project_id": project.project_id, "name": project.name,
        "goals": project.goals, "dependencies": project.dependencies,
        "risks": project.risks, "costs_usd": project.costs_usd,
        "capacity_slots": project.capacity_slots, "deadline": project.deadline,
        "ev": project.ev, "urgency": project.urgency,
        "information_gain": project.information_gain,
        "reversibility": project.reversibility,
        "opportunity_cost": project.opportunity_cost,
        "status": project.status, "provenance": project.provenance,
    }


def _score(project: Project) -> float:
    if project.status == "PAUSED":
        return float("-inf")
    return (SCORE_WEIGHTS["ev"] * project.ev
            + SCORE_WEIGHTS["urgency"] * project.urgency
            + SCORE_WEIGHTS["information_gain"] * project.information_gain
            + SCORE_WEIGHTS["reversibility"] * project.reversibility
            + SCORE_WEIGHTS["opportunity_cost"] * project.opportunity_cost
            + project.priority_bump)


def score_projects(database: FoundryDatabase) -> list[dict[str, Any]]:
    projects = sorted(_rebuild(database).values(),
                      key=lambda p: (-_score(p), p.project_id))
    return [{
        "project_id": p.project_id, "score": round(_score(p), 4),
        "status": p.status, "ev": p.ev, "urgency": p.urgency,
    } for p in projects]


def select_projects(database: FoundryDatabase, *, controller: str,
                    available_slots: int, source_type: str,
                    source_id: str) -> list[dict[str, Any]]:
    _gate(database, controller)
    ranked = score_projects(database)
    selected: list[dict[str, Any]] = []
    remaining = available_slots
    for row in ranked:
        if row["status"] == "PAUSED":
            continue
        if remaining <= 0:
            database.append(Event.create(
                "portfolio.deferred",
                {"project_id": row["project_id"], "reason": "capacity",
                 "controller": controller},
                idempotency_key=f"portfolio.deferred:"
                                f"{row['project_id']}:capacity",
                source_type=source_type, source_id=source_id))
            continue
        database.append(Event.create(
            "portfolio.decision",
            {"project_id": row["project_id"], "action": "select",
             "score": row["score"], "controller": controller,
             "reason": f"ranked top {len(selected) + 1} by score"},
            idempotency_key=f"portfolio.decision:"
                            f"{row['project_id']}:select:{row['score']}",
            source_type=source_type, source_id=source_id))
        selected.append(project_view(database, row["project_id"]))
        remaining -= 1
    return selected


def _status_change(database: FoundryDatabase, project_id: str, status: str,
                   *, controller: str, reason: str,
                   source_type: str, source_id: str) -> str:
    _gate(database, controller)
    _ = _rebuild(database).get(project_id) or _require(
        False, f"unknown project: {project_id}")
    event = Event.create(
        "portfolio.status_changed",
        {"project_id": project_id, "status": status, "reason": reason,
         "controller": controller},
        idempotency_key=f"portfolio.status_changed:{project_id}:{status}",
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


def pause_project(database: FoundryDatabase, project_id: str, *,
                  controller: str, reason: str,
                  source_type: str, source_id: str) -> str:
    return _status_change(database, project_id, "PAUSED", controller=controller,
                          reason=reason, source_type=source_type,
                          source_id=source_id)


def resume_project(database: FoundryDatabase, project_id: str, *,
                   controller: str, source_type: str, source_id: str) -> str:
    return _status_change(database, project_id, "PLANNED",
                          controller=controller, reason="resumed",
                          source_type=source_type, source_id=source_id)


def reprioritize_project(database: FoundryDatabase, project_id: str, *,
                         priority_bump: float, controller: str,
                         source_type: str, source_id: str) -> str:
    _gate(database, controller)
    _ = _rebuild(database).get(project_id) or _require(
        False, f"unknown project: {project_id}")
    event = Event.create(
        "portfolio.reprioritized",
        {"project_id": project_id, "bump": float(priority_bump),
         "controller": controller},
        idempotency_key=f"portfolio.reprioritized:{project_id}:"
                        f"{float(priority_bump)}",
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


def record_unknown(database: FoundryDatabase, question_id: str, question: str,
                   *, controller: str, source_type: str,
                   source_id: str) -> str:
    _gate(database, controller)
    event = Event.create(
        "portfolio.unknown",
        {"question_id": question_id, "question": question,
         "controller": controller},
        idempotency_key=f"portfolio.unknown:{question_id}",
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


def record_conflict(database: FoundryDatabase, conflict_id: str,
                    description: str, *, controller: str,
                    source_type: str, source_id: str) -> str:
    _gate(database, controller)
    event = Event.create(
        "portfolio.conflict",
        {"conflict_id": conflict_id, "description": description,
         "controller": controller},
        idempotency_key=f"portfolio.conflict:{conflict_id}",
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])
