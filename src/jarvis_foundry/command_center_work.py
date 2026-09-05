"""Bounded, read-only Work hierarchy for the Command Center.

The projection joins existing owner directives, Executive objectives/plans,
V12 campaigns, MissionLoop rows, workers, evidence, and verifier receipts. It
does not create hierarchy identities or grant execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


WORK_HIERARCHY_SCHEMA = "v13.command-center-work.v1"


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: object) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [dict(row) for row in value.values() if isinstance(row, Mapping)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


def _text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [text for item in value if (text := _text(item)) is not None]


def _unique(values: Sequence[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _explicit_context(
    objective: Mapping[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    metadata = _mapping(objective.get("metadata"))
    provenance = _mapping(objective.get("provenance"))
    project_id = _text(metadata.get("project_id") or provenance.get("project_id"))
    project_label = _text(
        metadata.get("project")
        or metadata.get("project_name")
        or provenance.get("project")
        or project_id
    )
    program_id = _text(metadata.get("program_id") or provenance.get("program_id"))
    program_label = _text(
        metadata.get("program")
        or metadata.get("program_name")
        or provenance.get("program")
        or program_id
    )
    return project_id, project_label, program_id, program_label


def project_work_hierarchy(
    *,
    autonomy: Mapping[str, Any] | None,
    goals: Sequence[Mapping[str, Any]],
    missions: Sequence[Mapping[str, Any]],
    workers: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    verdicts: Sequence[Mapping[str, Any]],
    mission_steps: Sequence[Mapping[str, Any]],
    limit: int = 100,
) -> dict[str, Any]:
    """Join canonical work records without inferring missing hierarchy."""

    bounded = max(1, min(int(limit), 500))
    executive = _mapping(autonomy)
    objective_rows = _rows(executive.get("objectives"))
    plan_rows = _rows(executive.get("plans"))
    progress_by_objective = {
        str(key): _mapping(value)
        for key, value in _mapping(executive.get("progress")).items()
    }
    campaign_envelope = _mapping(executive.get("campaigns"))
    campaign_rows = _rows(campaign_envelope.get("campaigns"))

    objective_by_id = {
        objective_id: row
        for row in objective_rows
        if (objective_id := _text(row.get("objective_id"))) is not None
    }
    campaign_by_id = {
        campaign_id: row
        for row in campaign_rows
        if (campaign_id := _text(row.get("campaign_id"))) is not None
    }

    mission_to_objective: dict[str, str] = {}
    mission_to_campaign: dict[str, str] = {}
    step_to_campaign: dict[str, str] = {}
    for objective_id, row in objective_by_id.items():
        for mission_id in _strings(row.get("mission_ids")):
            mission_to_objective.setdefault(mission_id, objective_id)
    for campaign_id, campaign in campaign_by_id.items():
        objective_id = _text(campaign.get("objective_id"))
        for child in _rows(campaign.get("children")):
            mission_id = _text(child.get("mission_id"))
            step_id = _text(child.get("step_id"))
            if mission_id:
                mission_to_campaign[mission_id] = campaign_id
                if objective_id:
                    mission_to_objective.setdefault(mission_id, objective_id)
            if step_id:
                step_to_campaign[step_id] = campaign_id

    goal_rows: list[dict[str, Any]] = []
    goal_by_directive: dict[str, str] = {}
    for goal in goals[:bounded]:
        directive_id = _text(goal.get("directive_id"))
        if not directive_id:
            continue
        goal_id = f"goal:{directive_id}"
        goal_by_directive[directive_id] = goal_id
        goal_rows.append({
            "goal_id": goal_id,
            "directive_id": directive_id,
            "label": _text(goal.get("exact_text")) or directive_id,
            "status": "ACTIVE" if bool(goal.get("active", 1)) else "INACTIVE",
            "target": _text(goal.get("target")) or "GLOBAL",
            "source_event_id": _text(goal.get("last_event_id") or goal.get("event_id")),
            "objective_ids": [],
        })

    projects: dict[str, dict[str, Any]] = {}
    programs: dict[str, dict[str, Any]] = {}
    objective_projection: list[dict[str, Any]] = []
    for objective_id, objective in sorted(objective_by_id.items()):
        provenance = _mapping(objective.get("provenance"))
        directive_id = _text(provenance.get("directive_id"))
        goal_id = goal_by_directive.get(directive_id or "")
        project_id, project_label, program_id, program_label = _explicit_context(objective)
        if project_id:
            project = projects.setdefault(project_id, {
                "project_id": project_id,
                "label": project_label or project_id,
                "program_id": program_id,
                "status": "SOURCE_BACKED",
                "source": "executive.objective.metadata",
                "objective_ids": [],
            })
            project["objective_ids"].append(objective_id)
        if program_id:
            program = programs.setdefault(program_id, {
                "program_id": program_id,
                "label": program_label or program_id,
                "status": "SOURCE_BACKED",
                "source": "executive.objective.metadata",
                "project_ids": [],
                "objective_ids": [],
            })
            if project_id:
                program["project_ids"].append(project_id)
            program["objective_ids"].append(objective_id)
        campaign_ids = [
            campaign_id
            for campaign_id, campaign in campaign_by_id.items()
            if _text(campaign.get("objective_id")) == objective_id
        ]
        mission_ids = _unique([
            *_strings(objective.get("mission_ids")),
            *[
                mission_id
                for mission_id, mapped_objective in mission_to_objective.items()
                if mapped_objective == objective_id
            ],
        ])
        progress = progress_by_objective.get(objective_id, {})
        objective_projection.append({
            "objective_id": objective_id,
            "label": _text(objective.get("objective")) or objective_id,
            "parent_id": _text(objective.get("parent_id")),
            "goal_id": goal_id,
            "project_id": project_id,
            "program_id": program_id,
            "status": _text(objective.get("status")) or "UNKNOWN",
            "priority": objective.get("priority"),
            "deadline": _text(objective.get("deadline")),
            "campaign_ids": sorted(campaign_ids),
            "mission_ids": mission_ids,
            "plan_ids": _strings(objective.get("plan_ids")),
            "progress_evidence": {
                "status": _text(progress.get("status")) or "UNAVAILABLE",
                "confidence": progress.get("confidence"),
                "evidence_refs": _strings(progress.get("evidence_refs")),
            },
            "source_event_id": _text(provenance.get("event_id")),
            "updated_at": _text(objective.get("updated_at")),
        })
        if goal_id:
            next(
                goal for goal in goal_rows if goal["goal_id"] == goal_id
            )["objective_ids"].append(objective_id)

    workers_by_mission: dict[str, list[str]] = {}
    workers_by_step: dict[str, list[str]] = {}
    for worker in workers:
        run_id = _text(worker.get("run_id"))
        if not run_id:
            continue
        mission_id = _text(worker.get("mission_id"))
        step_id = _text(worker.get("step_id"))
        if mission_id:
            workers_by_mission.setdefault(mission_id, []).append(run_id)
        if step_id:
            workers_by_step.setdefault(step_id, []).append(run_id)

    evidence_by_mission: dict[str, list[str]] = {}
    evidence_by_step: dict[str, list[str]] = {}
    for item in evidence:
        evidence_id = _text(item.get("bundle_id"))
        if not evidence_id:
            continue
        mission_id = _text(item.get("mission_id"))
        step_id = _text(item.get("step_id"))
        if mission_id:
            evidence_by_mission.setdefault(mission_id, []).append(evidence_id)
        if step_id:
            evidence_by_step.setdefault(step_id, []).append(evidence_id)

    verdicts_by_mission: dict[str, list[str]] = {}
    for verdict in verdicts:
        verdict_id = _text(verdict.get("verdict_id"))
        mission_id = _text(verdict.get("mission_id"))
        if verdict_id and mission_id:
            verdicts_by_mission.setdefault(mission_id, []).append(verdict_id)

    action_projection: list[dict[str, Any]] = []
    action_ids: set[str] = set()
    for plan in sorted(plan_rows, key=lambda row: str(row.get("plan_id") or "")):
        plan_id = _text(plan.get("plan_id"))
        objective_id = _text(plan.get("objective_id"))
        for step in _rows(plan.get("steps")):
            step_id = _text(step.get("step_id"))
            if not step_id or not objective_id:
                continue
            action_id = f"{plan_id or objective_id}:{step_id}"
            action_ids.add(action_id)
            mission_id = _text(step.get("mission_id"))
            campaign_id = (
                mission_to_campaign.get(mission_id or "")
                or step_to_campaign.get(step_id)
            )
            action_projection.append({
                "action_id": action_id,
                "step_id": step_id,
                "label": _text(step.get("description")) or step_id,
                "status": _text(step.get("status")) or "UNKNOWN",
                "objective_id": objective_id,
                "campaign_id": campaign_id,
                "mission_id": mission_id,
                "worker_ids": _unique([
                    *workers_by_step.get(step_id, []),
                    *workers_by_mission.get(mission_id or "", []),
                ]),
                "evidence_refs": _unique([
                    *_strings(step.get("evidence_refs")),
                    *evidence_by_step.get(step_id, []),
                    *evidence_by_mission.get(mission_id or "", []),
                ]),
                "verdict_ids": verdicts_by_mission.get(mission_id or "", []),
                "source": "executive.plan.step",
            })
    for step in mission_steps:
        contract_id = _text(step.get("contract_id"))
        mission_id = _text(step.get("mission_id"))
        step_id = _text(step.get("step_id"))
        if not contract_id or contract_id in action_ids:
            continue
        objective_id = mission_to_objective.get(mission_id or "")
        action_projection.append({
            "action_id": contract_id,
            "step_id": step_id,
            "label": _text(step.get("capability")) or step_id or contract_id,
            "status": "RECORDED",
            "objective_id": objective_id,
            "campaign_id": mission_to_campaign.get(mission_id or ""),
            "mission_id": mission_id,
            "worker_ids": _unique([
                *workers_by_step.get(step_id or "", []),
                *workers_by_mission.get(mission_id or "", []),
            ]),
            "evidence_refs": evidence_by_mission.get(mission_id or "", []),
            "verdict_ids": verdicts_by_mission.get(mission_id or "", []),
            "source": "mission_step_contracts",
        })

    actions_by_objective: dict[str, list[str]] = {}
    actions_by_campaign: dict[str, list[str]] = {}
    actions_by_mission: dict[str, list[str]] = {}
    for action in action_projection:
        action_id = str(action["action_id"])
        for mapping, key in (
            (actions_by_objective, _text(action.get("objective_id"))),
            (actions_by_campaign, _text(action.get("campaign_id"))),
            (actions_by_mission, _text(action.get("mission_id"))),
        ):
            if key:
                mapping.setdefault(key, []).append(action_id)
    for objective in objective_projection:
        objective["action_ids"] = actions_by_objective.get(
            str(objective["objective_id"]), []
        )

    campaign_projection: list[dict[str, Any]] = []
    for campaign_id, campaign in sorted(campaign_by_id.items()):
        children = _rows(campaign.get("children"))
        mission_ids = _unique([
            _text(child.get("mission_id")) for child in children
        ])
        wait_kinds = _unique([
            _text(child.get("wait_kind"))
            for child in children
            if _text(child.get("status")) == "WAITING"
        ])
        campaign_projection.append({
            "campaign_id": campaign_id,
            "objective_id": _text(campaign.get("objective_id")),
            "status": _text(campaign.get("status")) or "UNKNOWN",
            "mission_ids": mission_ids,
            "child_count": len(children),
            "accepted_child_count": campaign.get("accepted_child_count"),
            "pending_count": len(_rows(campaign.get("pending_work"))),
            "wait_kinds": wait_kinds,
            "action_ids": actions_by_campaign.get(campaign_id, []),
            "checkpoint_id": _text(campaign.get("checkpoint_id")),
            "updated_at": _text(campaign.get("updated_at")),
            "source": "executive.campaigns",
        })

    mission_projection: list[dict[str, Any]] = []
    for mission in missions[:bounded]:
        mission_id = _text(mission.get("mission_id"))
        if not mission_id:
            continue
        mission_projection.append({
            "mission_id": mission_id,
            "objective_id": mission_to_objective.get(mission_id),
            "campaign_id": mission_to_campaign.get(mission_id),
            "status": _text(mission.get("state")) or "UNKNOWN",
            "action_ids": actions_by_mission.get(mission_id, []),
            "worker_ids": _unique(workers_by_mission.get(mission_id, [])),
            "evidence_refs": _unique(evidence_by_mission.get(mission_id, [])),
            "verdict_ids": _unique(verdicts_by_mission.get(mission_id, [])),
            "source_event_id": _text(mission.get("last_event_id")),
        })

    for project in projects.values():
        project["objective_ids"] = _unique(project["objective_ids"])
    for program in programs.values():
        program["project_ids"] = _unique(program["project_ids"])
        program["objective_ids"] = _unique(program["objective_ids"])

    levels = {
        "goals": "AVAILABLE" if goal_rows else "EMPTY",
        "programs": "AVAILABLE" if programs else "NOT_YET_INTEGRATED",
        "projects": "AVAILABLE" if projects else "NOT_YET_INTEGRATED",
        "objectives": "AVAILABLE" if objective_by_id else "EMPTY",
        "campaigns": (
            "AVAILABLE"
            if campaign_by_id
            else "EMPTY"
            if campaign_envelope
            else "NOT_YET_INTEGRATED"
        ),
        "missions": "AVAILABLE" if mission_projection else "EMPTY",
        "actions": "AVAILABLE" if action_projection else "EMPTY",
        "workers": "AVAILABLE" if workers else "EMPTY",
        "evidence": "AVAILABLE" if evidence or verdicts else "EMPTY",
    }
    available_records = sum((
        len(goal_rows), len(programs), len(projects), len(objective_projection),
        len(campaign_projection), len(mission_projection),
        len(action_projection),
    ))
    return {
        "schema": WORK_HIERARCHY_SCHEMA,
        "status": (
            "PARTIAL"
            if any(value == "NOT_YET_INTEGRATED" for value in levels.values())
            and available_records
            else "AVAILABLE"
            if available_records
            else "EMPTY"
        ),
        "authority": "READ_ONLY_CANONICAL_PROJECTIONS",
        "ui_authority": "NONE",
        "levels": levels,
        "goals": goal_rows,
        "programs": list(programs.values())[:bounded],
        "projects": list(projects.values())[:bounded],
        "objectives": objective_projection[:bounded],
        "campaigns": campaign_projection[:bounded],
        "missions": mission_projection[:bounded],
        "actions": action_projection[:bounded],
        "bounds": {
            "records_per_kind": bounded,
            "history": "OMITTED",
        },
    }


__all__ = ["WORK_HIERARCHY_SCHEMA", "project_work_hierarchy"]
