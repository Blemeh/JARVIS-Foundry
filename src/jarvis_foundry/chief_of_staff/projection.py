"""Read-only Chief of Staff projection over existing durable authorities."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..clock import isoformat, parse
from ..database import FoundryDatabase
from ..executive.autonomy import ObjectiveRecord, rebuild_executive_state
from ..executive.campaigns import rebuild_campaign_state
from ..executive.continuous import rebuild_wake_state
from ..owner_context import OwnerContextRecord, project_owner_context
from .models import (
    BriefingEntry,
    ChiefOfStaffProjection,
    HierarchyLink,
    OwnerAttentionItem,
    PriorityFactor,
    ProjectPriority,
    ProjectState,
    UNKNOWN,
)


CHIEF_OF_STAFF_EVENT_PREFIX = "chief_of_staff."
_TERMINAL_ITEM_STATES = frozenset({"RESOLVED", "SUPERSEDED", "REVOKED"})
_TERMINAL_OBJECTIVE_STATES = frozenset({
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "SUSPENDED",
})
_PRIORITY_WEIGHTS = {
    "owner_priority": 0.19,
    "strategic_value": 0.12,
    "urgency": 0.10,
    "deadline": 0.12,
    "blockers": 0.04,
    "dependency_leverage": 0.09,
    "information_gain": 0.10,
    "cost": 0.08,
    "risk": 0.05,
    "reversibility": 0.02,
    "confidence": 0.02,
    "opportunity_cost": 0.04,
    "staleness": 0.03,
}
_FACTOR_AUTHORITY_OWNER_AUTHENTICATED = "OWNER_CONTEXT_AUTHENTICATED"
_FACTOR_AUTHORITY_OWNER_CURRENT = "OWNER_CONTEXT_CURRENT"
_FACTOR_AUTHORITY_CHIEF = "CHIEF_PROJECT"
_FACTOR_AUTHORITY_OBJECTIVE = "CURRENT_OBJECTIVE"
_OWNER_PRIORITY_AUTHORITY_PRECEDENCE = {
    _FACTOR_AUTHORITY_OWNER_AUTHENTICATED: 40,
    _FACTOR_AUTHORITY_OWNER_CURRENT: 30,
    _FACTOR_AUTHORITY_CHIEF: 20,
    _FACTOR_AUTHORITY_OBJECTIVE: 10,
}
_PROJECT_FACTOR_AUTHORITY_PRECEDENCE = {
    _FACTOR_AUTHORITY_CHIEF: 40,
    _FACTOR_AUTHORITY_OWNER_AUTHENTICATED: 30,
    _FACTOR_AUTHORITY_OWNER_CURRENT: 20,
    _FACTOR_AUTHORITY_OBJECTIVE: 10,
}


def _strings(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


def _bounded_text(value: Any, *, limit: int = 500) -> str:
    result = str(value or "").replace("\x00", " ").strip()
    return result if len(result) <= limit else result[: limit - 3] + "..."


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return max(0.0, min(1.0, result))


def _priority_value(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    if result > 1.0:
        result /= 100.0
    return max(0.0, min(1.0, result))


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = parse(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _semantic_order_key(
    value: Any,
    *tie_breakers: Any,
) -> tuple[Any, ...]:
    observed = _timestamp(value)
    timestamp = (
        observed.astimezone(timezone.utc).timestamp()
        if observed is not None
        else float("-inf")
    )
    return (
        timestamp,
        *(str(part or "") for part in tie_breakers),
    )


def _deadline_pressure(value: Any, now: str) -> float | None:
    numeric = _priority_value(value)
    if numeric is not None:
        return numeric
    deadline = _timestamp(value)
    current = _timestamp(now)
    if deadline is None or current is None:
        return None
    seconds = (deadline - current).total_seconds()
    if seconds <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - seconds / (14 * 86400.0)))


def _staleness_pressure(value: Any, now: str) -> float | None:
    observed = _timestamp(value)
    current = _timestamp(now)
    if observed is None or current is None:
        return None
    age_seconds = max(0.0, (current - observed).total_seconds())
    return max(0.0, min(1.0, age_seconds / (7 * 86400.0)))


def _hash(value: str, *, length: int = 20) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _record_project_id(record: OwnerContextRecord) -> str | None:
    attributes = record.attributes
    for key in ("project_id", "project", "target_project"):
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if record.scope.project_ids:
        return record.scope.project_ids[0]
    if record.kind == "PROJECT":
        return record.context_id
    return None


def _record_objective_id(record: OwnerContextRecord) -> str:
    value = record.attributes.get("objective_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if record.scope.objective_ids:
        return record.scope.objective_ids[0]
    return record.context_id


def _related(record: OwnerContextRecord, prefix: str) -> str | None:
    direct = record.attributes.get(f"{prefix}_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for context_id in record.related_context_ids:
        if context_id.lower().startswith(f"{prefix}:"):
            return context_id
    return None


def _new_project(project_id: str, title: str | None = None) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "title": title or project_id,
        "goal_id": None,
        "program_id": None,
        "objective_ids": set(),
        "campaign_ids": set(),
        "mission_ids": set(),
        "action_ids": set(),
        "status": UNKNOWN,
        "health": UNKNOWN,
        "progress_status": UNKNOWN,
        "progress_values": [],
        "progress_completed": None,
        "progress_total": None,
        "blockers": [],
        "dependencies": [],
        "recent_changes": [],
        "last_meaningful_activity": None,
        "next_likely_action": None,
        "waiting_condition": None,
        "owner_decision_needed": False,
        "owner_attention_ids": set(),
        "jarvis_handled_work": [],
        "intentional_idle": False,
        "semantic_waits": {},
        "item_state_changes": {
            "WAIT": {},
            "OWNER_ATTENTION": {},
        },
        "source_event_refs": [],
        "evidence_refs": [],
        "unknown_fields": set(),
        "factor_sources": defaultdict(dict),
        "priority_disposition": None,
        "priority_disposition_order": (),
    }


def _ensure(
    projects: dict[str, dict[str, Any]],
    project_id: str,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    value = projects.setdefault(project_id, _new_project(project_id, title))
    if title and value["title"] == project_id:
        value["title"] = title
    return value


def _append_unique(target: list[str], *values: Any) -> None:
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in target:
            target.append(normalized)


def _set_priority_disposition(
    project: dict[str, Any],
    value: Any,
    *,
    order_key: Iterable[Any],
) -> None:
    order = tuple(order_key)
    current = tuple(project["priority_disposition_order"])
    if current and order < current:
        return
    normalized = str(value or "").strip().upper()
    project["priority_disposition"] = normalized or None
    project["priority_disposition_order"] = order


def _set_factor(
    project: dict[str, Any],
    name: str,
    value: Any,
    evidence_refs: Iterable[Any] = (),
    *,
    source_key: str,
    authority: str,
    order_key: Iterable[Any] = (),
    invert: bool = False,
) -> None:
    if name not in _PRIORITY_WEIGHTS:
        return
    order = tuple(order_key)
    sources = project["factor_sources"][name]
    current = sources.get(source_key)
    if current is not None and order < current["order_key"]:
        return
    raw = _priority_value(value)
    if raw is None:
        sources.pop(source_key, None)
        return
    sources[source_key] = {
        "source_key": source_key,
        "authority": authority,
        "raw_value": raw,
        "normalized_value": round(1.0 - raw, 6) if invert else raw,
        "evidence_refs": _strings(evidence_refs),
        "order_key": order,
    }


def _first_present(
    values: Mapping[str, Any],
    names: Iterable[str],
) -> tuple[bool, Any]:
    for name in names:
        if name in values:
            return True, values[name]
    return False, None


def _apply_explicit_factors(
    project: dict[str, Any],
    values: Mapping[str, Any],
    evidence_refs: Iterable[Any],
    *,
    source_key: str,
    authority: str,
    order_key: Iterable[Any],
    now: str,
    allow_value_alias: bool = False,
) -> None:
    aliases = {
        "owner_priority": ("owner_priority",),
        "strategic_value": (
            ("strategic_value", "value")
            if allow_value_alias
            else ("strategic_value",)
        ),
        "urgency": ("urgency",),
        "deadline": ("deadline",),
        "blockers": ("blocking_value", "blocker_value", "blockers"),
        "dependency_leverage": ("dependency_leverage",),
        "information_gain": ("information_gain",),
        "cost": ("cost",),
        "risk": ("risk",),
        "reversibility": ("reversibility", "reversible"),
        "confidence": ("confidence",),
        "opportunity_cost": ("opportunity_cost",),
        "staleness": ("staleness",),
    }
    for name, keys in aliases.items():
        present, value = _first_present(values, keys)
        if not present:
            continue
        if name == "deadline":
            value = _deadline_pressure(value, now)
        _set_factor(
            project,
            name,
            value,
            evidence_refs,
            source_key=source_key,
            authority=authority,
            order_key=order_key,
            invert=name in {"cost", "opportunity_cost"},
        )


def _selected_factor(
    project: Mapping[str, Any],
    name: str,
) -> Mapping[str, Any] | None:
    sources = project["factor_sources"].get(name, {})
    if not sources:
        return None
    precedence = (
        _OWNER_PRIORITY_AUTHORITY_PRECEDENCE
        if name == "owner_priority"
        else _PROJECT_FACTOR_AUTHORITY_PRECEDENCE
    )
    return max(
        sources.values(),
        key=lambda observation: (
            precedence.get(observation["authority"], 0),
            observation["order_key"],
            observation["source_key"],
        ),
    )


def _project_from_objective(
    objective: ObjectiveRecord,
    projects: Mapping[str, dict[str, Any]],
    context_project_ids: Mapping[str, str],
) -> str | None:
    for project_id, project in projects.items():
        if objective.objective_id in project["objective_ids"]:
            return project_id
    values = (
        objective.metadata.get("project_id"),
        objective.metadata.get("target_project"),
        objective.metadata.get("project"),
        objective.provenance.get("project_id"),
        objective.provenance.get("target_project"),
        objective.provenance.get("project"),
        objective.parent_id,
    )
    title_map = {
        str(project["title"]).strip().lower(): project_id
        for project_id, project in projects.items()
    }
    for value in values:
        candidate = str(value or "").strip()
        if not candidate:
            continue
        if candidate in projects:
            return candidate
        if candidate in context_project_ids:
            return context_project_ids[candidate]
        if candidate.lower() in title_map:
            return title_map[candidate.lower()]
    return None


def _event_reference_maps(
    database: FoundryDatabase,
) -> tuple[
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, str],
]:
    objective_refs: dict[str, list[str]] = defaultdict(list)
    campaign_refs: dict[str, list[str]] = defaultdict(list)
    mission_refs: dict[str, list[str]] = defaultdict(list)
    action_refs: dict[str, list[str]] = defaultdict(list)
    action_missions: dict[str, str] = {}
    events = database.events(
        readonly=True,
        event_type_prefixes=(
            "executive.",
            "campaign.",
            "mission.",
            "action.",
            "system.action_",
            "effect.",
        ),
    )
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key, target in (
            ("objective_id", objective_refs),
            ("campaign_id", campaign_refs),
            ("mission_id", mission_refs),
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                _append_unique(target[value.strip()], event.event_id)
        for key in ("action_id", "action_key", "effect_ticket_id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                action_id = value.strip()
                _append_unique(action_refs[action_id], event.event_id)
                mission_id = payload.get("mission_id")
                if isinstance(mission_id, str) and mission_id.strip():
                    action_missions[action_id] = mission_id.strip()
    return (
        objective_refs,
        campaign_refs,
        mission_refs,
        action_refs,
        action_missions,
    )


def _attention_from_context(
    record: OwnerContextRecord,
    project_id: str,
) -> OwnerAttentionItem:
    attention_id = f"cos-attention-{_hash(record.context_id)}"
    return OwnerAttentionItem(
        attention_id=attention_id,
        project_id=project_id,
        category=record.kind,
        summary=record.text,
        reason="authenticated owner context requires an owner decision",
        created_at=record.updated_at,
        objective_id=(
            record.scope.objective_ids[0]
            if record.scope.objective_ids else None
        ),
        source_event_refs=_strings((record.source_event_id,)),
        evidence_refs=record.provenance.evidence_refs,
    )


def _freshness(last_activity: str | None, now: str) -> str:
    observed = _timestamp(last_activity)
    clock = _timestamp(now)
    if observed is None or clock is None:
        return UNKNOWN
    age_days = (clock - observed).total_seconds() / 86400.0
    if age_days < 0:
        return "FUTURE"
    if age_days <= 2:
        return "FRESH"
    if age_days <= 7:
        return "AGING"
    return "STALE"


def _priority_projection(
    projects: list[ProjectState],
    mutable: Mapping[str, dict[str, Any]],
) -> tuple[ProjectPriority, ...]:
    unranked: list[
        tuple[
            str,
            float,
            tuple[PriorityFactor, ...],
            tuple[str, ...],
            tuple[str, ...],
            bool,
            bool,
            tuple[str, ...],
        ]
    ] = []
    for state in projects:
        source = mutable[state.project_id]
        factors: list[PriorityFactor] = []
        unknown: list[str] = []
        rationale: list[str] = []
        score = 0.0
        for name, weight in _PRIORITY_WEIGHTS.items():
            observation = _selected_factor(source, name)
            if observation is None:
                unknown.append(name)
                raw_value = None
                normalized_value = None
                evidence_refs = ()
                contribution = 0.0
            else:
                raw_value = observation["raw_value"]
                normalized_value = observation["normalized_value"]
                evidence_refs = observation["evidence_refs"]
                scored_value = normalized_value
                if (
                    name == "urgency"
                    and observation["source_key"] == "chief"
                    and source["priority_disposition"]
                    == "LOW_STRATEGIC_VALUE"
                ):
                    scored_value = 0.0
                    rationale.append(
                        "chief urgency excluded after LOW_STRATEGIC_VALUE",
                    )
                contribution = round(scored_value * weight, 6)
                score += contribution
                rationale.append(f"{name}={normalized_value:.2f}")
            factors.append(PriorityFactor(
                name=name,
                raw_value=raw_value,
                normalized_value=normalized_value,
                weight=weight,
                contribution=contribution,
                evidence_refs=evidence_refs,
            ))
        if state.intentional_idle or state.status == "COMPLETED":
            score = 0.0
            rationale.append(
                "deliberate idle" if state.intentional_idle else "project complete",
            )
        rankable = (
            any(factor.known for factor in factors)
            or state.intentional_idle
        )
        if not rankable:
            rationale.append("priority rank unknown: no evidence-backed factors")
        unranked.append((
            state.project_id,
            round(score, 6),
            tuple(factors),
            tuple(unknown),
            tuple(rationale),
            state.intentional_idle,
            rankable,
            state.source_event_refs,
        ))
    unranked.sort(key=lambda value: (
        not value[6],
        -value[1],
        value[0],
    ))
    priorities: list[ProjectPriority] = []
    next_rank = 0
    for (
        project_id,
        score,
        factors,
        unknown,
        rationale,
        deliberate_idle,
        rankable,
        refs,
    ) in unranked:
        rank = None
        if rankable:
            next_rank += 1
            rank = next_rank
        priorities.append(ProjectPriority(
            project_id=project_id,
            rank=rank,
            score=score,
            factors=factors,
            unknown_factors=unknown,
            rationale=rationale,
            deliberate_idle=deliberate_idle,
            source_event_refs=refs,
        ))
    return tuple(priorities)


def project_chief_of_staff(
    database: FoundryDatabase,
    *,
    now: str | None = None,
) -> ChiefOfStaffProjection:
    """Rebuild source-backed project truth without performing any writes."""

    generated_at = now or isoformat()
    owner = project_owner_context(database, now=generated_at)
    projects: dict[str, dict[str, Any]] = {}
    context_project_ids: dict[str, str] = {}
    attention: dict[str, OwnerAttentionItem] = {}
    briefing: dict[str, BriefingEntry] = {}
    all_source_refs: list[str] = []

    current_records = [
        record for record in owner.records if record.current(generated_at)
    ]
    for record in current_records:
        if record.kind != "PROJECT":
            continue
        project_id = _record_project_id(record)
        if project_id is None:
            continue
        context_project_ids[record.context_id] = project_id
        title = (
            record.attributes.get("title")
            or record.attributes.get("name")
            or record.text
        )
        project = _ensure(projects, project_id, title=_bounded_text(title))
        project["goal_id"] = _related(record, "goal") or project["goal_id"]
        project["program_id"] = (
            _related(record, "program")
            or (record.scope.program_ids[0] if record.scope.program_ids else None)
            or project["program_id"]
        )
        _append_unique(project["source_event_refs"], record.source_event_id)
        _append_unique(project["evidence_refs"], *record.provenance.evidence_refs)

    for record in current_records:
        project_ids = list(record.scope.project_ids)
        attribute_project = _record_project_id(record)
        if attribute_project and attribute_project not in project_ids:
            project_ids.append(attribute_project)
        if record.kind == "PROGRAM":
            for project_id in project_ids:
                project = _ensure(projects, project_id)
                project["program_id"] = record.context_id
                _append_unique(project["source_event_refs"], record.source_event_id)
        if record.kind == "GOAL":
            for project_id in project_ids:
                project = _ensure(projects, project_id)
                project["goal_id"] = record.context_id
                _append_unique(project["source_event_refs"], record.source_event_id)
        for project_id in project_ids:
            project = _ensure(projects, project_id)
            _append_unique(project["source_event_refs"], record.source_event_id)
            _append_unique(project["evidence_refs"], *record.provenance.evidence_refs)
            record_refs = (
                record.source_event_id,
                *record.provenance.evidence_refs,
            )
            record_authority = (
                _FACTOR_AUTHORITY_OWNER_AUTHENTICATED
                if record.provenance.authority == "AUTHENTICATED_OWNER"
                else _FACTOR_AUTHORITY_OWNER_CURRENT
            )
            record_order = _semantic_order_key(
                record.effective_at,
                record.updated_at,
                record.recorded_at,
                record.context_id,
            )
            _apply_explicit_factors(
                project,
                record.attributes,
                record_refs,
                source_key=record_authority,
                authority=record_authority,
                order_key=record_order,
                now=generated_at,
            )
            if record.kind == "ACTIVE_OBJECTIVE":
                project["objective_ids"].add(_record_objective_id(record))
                _set_factor(
                    project,
                    "owner_priority",
                    record.priority,
                    record_refs,
                    source_key=record_authority,
                    authority=record_authority,
                    order_key=record_order,
                )
            elif record.kind == "PRIORITY":
                _set_factor(
                    project,
                    "owner_priority",
                    record.attributes.get("value", record.priority),
                    record_refs,
                    source_key=record_authority,
                    authority=record_authority,
                    order_key=record_order,
                )
            elif record.kind == "UNRESOLVED_ITEM":
                _append_unique(project["blockers"], record.text)
                if record.attributes.get("owner_decision_needed") is True:
                    item = _attention_from_context(record, project_id)
                    attention[item.attention_id] = item
                    project["owner_attention_ids"].add(item.attention_id)
                    project["owner_decision_needed"] = True
            elif record.kind == "WAITING_ON_OWNER":
                project["waiting_condition"] = record.text
                project["owner_decision_needed"] = True
                item = _attention_from_context(record, project_id)
                attention[item.attention_id] = item
                project["owner_attention_ids"].add(item.attention_id)
            elif record.kind == "COMMITMENT":
                next_action = record.attributes.get("next_action")
                project["next_likely_action"] = _bounded_text(
                    next_action or record.text,
                )

    record_by_id = {record.context_id: record for record in owner.records}
    for conflict in owner.contradictions:
        context_ids = [
            str(value)
            for value in conflict.get("context_ids", ())
            if str(value)
        ]
        conflict_projects: set[str] = set()
        conflict_refs: list[str] = []
        for context_id in context_ids:
            record = record_by_id.get(context_id)
            if record is None:
                continue
            conflict_projects.update(record.scope.project_ids)
            _append_unique(conflict_refs, record.source_event_id)
        for project_id in sorted(conflict_projects):
            project = _ensure(projects, project_id)
            attention_id = f"cos-attention-{_hash(str(conflict.get('contradiction_id')) + project_id)}"
            item = OwnerAttentionItem(
                attention_id=attention_id,
                project_id=project_id,
                category="CONTRADICTORY_OWNER_CONTEXT",
                summary="Owner context contains unresolved contradictory project truth",
                reason=str(conflict.get("contradiction_id") or "contradiction"),
                created_at=generated_at,
                source_event_refs=_strings(conflict_refs),
            )
            attention[item.attention_id] = item
            project["owner_attention_ids"].add(item.attention_id)
            project["owner_decision_needed"] = True

    chief_events = database.events(
        readonly=True,
        event_type_prefixes=(CHIEF_OF_STAFF_EVENT_PREFIX,),
    )
    for chief_sequence, event in enumerate(chief_events, start=1):
        payload = event.payload if isinstance(event.payload, dict) else {}
        change = payload.get("change")
        change_payload = change if isinstance(change, dict) else payload
        project_id = str(
            change_payload.get("project_id")
            or payload.get("project_id")
            or ""
        ).strip()
        if not project_id:
            continue
        project = _ensure(projects, project_id)
        _append_unique(project["source_event_refs"], event.event_id)
        _append_unique(all_source_refs, event.event_id)
        for ref in (
            *(change_payload.get("source_event_refs") or ()),
            *(change_payload.get("evidence_refs") or ()),
            *(payload.get("source_event_refs") or ()),
            *(payload.get("evidence_refs") or ()),
        ):
            _append_unique(project["evidence_refs"], ref)
        event_order = (f"{chief_sequence:020d}",)
        event_refs = _strings((
            event.event_id,
            *(change_payload.get("source_event_refs") or ()),
            *(change_payload.get("evidence_refs") or ()),
            *(payload.get("source_event_refs") or ()),
            *(payload.get("evidence_refs") or ()),
        ))
        if event.event_type == "chief_of_staff.item_state_changed":
            item_kind = str(payload.get("item_kind") or "").upper()
            item_id = str(payload.get("item_id") or "").strip()
            from_state = str(payload.get("from_state") or "").upper()
            to_state = str(payload.get("to_state") or "").upper()
            if (
                item_kind in {"WAIT", "OWNER_ATTENTION"}
                and item_id
                and to_state in _TERMINAL_ITEM_STATES
            ):
                project["item_state_changes"][item_kind][item_id] = {
                    "status": to_state,
                    "reason": _bounded_text(payload.get("reason")),
                    "change_id": str(payload.get("change_id") or ""),
                    "source_event_refs": event_refs,
                    "evidence_refs": _strings(
                        payload.get("evidence_refs") or (),
                    ),
                    "order_key": event_order,
                }
            continue
        if event.event_type == "chief_of_staff.change_observed":
            summary = _bounded_text(change_payload.get("summary"))
            _append_unique(project["recent_changes"], summary)
            activity = str(
                change_payload.get("occurred_at") or event.occurred_at
            )
            factor_order = _semantic_order_key(
                activity,
                event.recorded_at,
                event_order[0],
                event.event_id,
            )
            _set_priority_disposition(
                project,
                None,
                order_key=factor_order,
            )
            if (
                project["last_meaningful_activity"] is None
                or activity > project["last_meaningful_activity"]
            ):
                project["last_meaningful_activity"] = activity
            attributes = change_payload.get("attributes")
            values = attributes if isinstance(attributes, dict) else {}
            state = str(
                change_payload.get("situation_state")
                or values.get("status")
                or ""
            ).upper()
            if state:
                project["status"] = state
            if values.get("health"):
                project["health"] = str(values["health"]).upper()
            if values.get("next_action"):
                project["next_likely_action"] = _bounded_text(
                    values["next_action"],
                )
            _apply_explicit_factors(
                project,
                values,
                event_refs,
                source_key="chief",
                authority=_FACTOR_AUTHORITY_CHIEF,
                order_key=factor_order,
                now=generated_at,
                allow_value_alias=True,
            )
            if "staleness" not in values:
                _set_factor(
                    project,
                    "staleness",
                    _staleness_pressure(activity, generated_at),
                    event_refs,
                    source_key="chief",
                    authority=_FACTOR_AUTHORITY_CHIEF,
                    order_key=factor_order,
                )
            blockers = values.get("blockers")
            if isinstance(blockers, (list, tuple)):
                _append_unique(project["blockers"], *blockers)
            dependencies = values.get("dependencies")
            if isinstance(dependencies, (list, tuple)):
                _append_unique(project["dependencies"], *dependencies)
            entry = BriefingEntry(
                entry_id=f"cos-brief-{_hash(event.event_id)}",
                project_id=project_id,
                category="PROJECT_CHANGE",
                summary=summary or "project change observed",
                occurred_at=activity,
                source_event_refs=(event.event_id,),
                evidence_refs=_strings(project["evidence_refs"]),
            )
            briefing[entry.entry_id] = entry
        elif event.event_type == "chief_of_staff.candidate_routed":
            objective = _bounded_text(
                (payload.get("proposal") or {}).get("objective")
                if isinstance(payload.get("proposal"), dict)
                else payload.get("objective"),
            )
            if objective:
                _append_unique(project["jarvis_handled_work"], objective)
                project["next_likely_action"] = objective
            entry = BriefingEntry(
                entry_id=f"cos-brief-{_hash(event.event_id)}",
                project_id=project_id,
                category="PROACTIVE_CANDIDATE",
                summary=objective or "proactive candidate routed",
                occurred_at=event.occurred_at,
                source_event_refs=(event.event_id,),
                evidence_refs=_strings(payload.get("evidence_refs") or ()),
            )
            briefing[entry.entry_id] = entry
        elif event.event_type == "chief_of_staff.recommendation_rejected":
            _set_priority_disposition(
                project,
                payload.get("reason"),
                order_key=_semantic_order_key(
                    event.occurred_at,
                    event.recorded_at,
                    event_order[0],
                    event.event_id,
                ),
            )
            entry = BriefingEntry(
                entry_id=f"cos-brief-{_hash(event.event_id)}",
                project_id=project_id,
                category="RECOMMENDATION_REJECTED",
                summary=_bounded_text(
                    payload.get("reason") or "recommendation rejected",
                ),
                occurred_at=event.occurred_at,
                source_event_refs=(event.event_id,),
                evidence_refs=_strings(payload.get("evidence_refs") or ()),
            )
            briefing[entry.entry_id] = entry
        elif event.event_type == "chief_of_staff.wait_recorded":
            wait_id = str(payload.get("wait_id") or "").strip()
            if not wait_id:
                continue
            wait_reason = _bounded_text(
                payload.get("reason") or payload.get("condition"),
            )
            project["semantic_waits"][wait_id] = {
                "wait_id": wait_id,
                "occurred_at": event.occurred_at,
                "reason": wait_reason,
                "intentional_idle": payload.get("intentional_idle") is True,
                "canonical_wake_id": (
                    str(payload["canonical_wake_id"])
                    if payload.get("canonical_wake_id") else None
                ),
                "source_event_refs": event_refs,
                "evidence_refs": _strings(
                    payload.get("evidence_refs") or (),
                ),
            }
            entry = BriefingEntry(
                entry_id=f"cos-brief-{_hash(event.event_id)}",
                project_id=project_id,
                category="WAIT",
                summary=wait_reason or "project is waiting",
                occurred_at=event.occurred_at,
                source_event_refs=(event.event_id,),
                evidence_refs=_strings(payload.get("evidence_refs") or ()),
            )
            briefing[entry.entry_id] = entry
        elif event.event_type == "chief_of_staff.owner_attention_required":
            attention_id = str(payload.get("attention_id") or "").strip()
            if not attention_id:
                continue
            item = OwnerAttentionItem(
                attention_id=attention_id,
                project_id=project_id,
                objective_id=(
                    str(payload["objective_id"])
                    if payload.get("objective_id") else None
                ),
                category=str(payload.get("category") or "OWNER_DECISION"),
                summary=_bounded_text(payload.get("summary")),
                reason=_bounded_text(payload.get("reason")),
                created_at=str(payload.get("created_at") or event.occurred_at),
                standing_order_ids=_strings(
                    payload.get("standing_order_ids") or (),
                ),
                source_event_refs=_strings((
                    event.event_id,
                    *(payload.get("source_event_refs") or ()),
                )),
                evidence_refs=_strings(payload.get("evidence_refs") or ()),
            )
            attention[attention_id] = item
            project["owner_attention_ids"].add(attention_id)
            project["owner_decision_needed"] = True

    (
        objective_refs,
        campaign_refs,
        mission_refs,
        action_refs,
        action_missions,
    ) = (
        _event_reference_maps(database)
    )
    executive = rebuild_executive_state(database)
    current_objectives = {
        objective_id: objective
        for objective_id, objective in executive.objectives.items()
        if objective.status not in _TERMINAL_OBJECTIVE_STATES
    }
    objective_descendants = {
        objective_id: 0
        for objective_id in current_objectives
    }
    for objective in current_objectives.values():
        for dependency_id in objective.dependencies:
            if dependency_id in objective_descendants:
                objective_descendants[dependency_id] += 1
    objective_projects: dict[str, str] = {}
    for objective in executive.objectives.values():
        project_id = _project_from_objective(
            objective,
            projects,
            context_project_ids,
        )
        if project_id is None:
            continue
        objective_projects[objective.objective_id] = project_id
        project = _ensure(projects, project_id)
        project["objective_ids"].add(objective.objective_id)
        project["mission_ids"].update(objective.mission_ids)
        _append_unique(project["dependencies"], *objective.dependencies)
        project["progress_values"].append(objective.progress)
        project["progress_status"] = objective.status
        if objective.objective_id in current_objectives:
            factor_refs = objective_refs.get(objective.objective_id, ())
            factor_source = f"objective:{objective.objective_id}"
            factor_order = _semantic_order_key(
                objective.updated_at,
                f"{objective.version:020d}",
                objective.objective_id,
            )
            objective_factors = {
                "owner_priority": _number(objective.priority / 3.0),
                "strategic_value": objective.expected_impact,
                "urgency": _number((objective.urgency + 1.0) / 2.0),
                "deadline": _deadline_pressure(
                    objective.deadline,
                    generated_at,
                ),
                "blockers": objective.blocking_value,
                "dependency_leverage": _number(
                    objective_descendants.get(objective.objective_id, 0)
                    / max(1, len(current_objectives)),
                ),
                "risk": objective.risk,
                "reversibility": objective.reversibility,
                "confidence": objective.confidence,
                "opportunity_cost": objective.opportunity_cost,
                "staleness": _staleness_pressure(
                    objective.updated_at,
                    generated_at,
                ),
            }
            for factor_name, factor_value in objective_factors.items():
                _set_factor(
                    project,
                    factor_name,
                    factor_value,
                    factor_refs,
                    source_key=factor_source,
                    authority=_FACTOR_AUTHORITY_OBJECTIVE,
                    order_key=factor_order,
                    invert=factor_name == "opportunity_cost",
                )
        _append_unique(
            project["source_event_refs"],
            *objective_refs.get(objective.objective_id, ()),
        )
        if objective.status in {"BLOCKED", "FAILED"}:
            _append_unique(
                project["blockers"],
                f"objective {objective.objective_id} is {objective.status}",
            )
        if objective.status in {"ACTIVE", "PLANNED", "READY", "EXECUTING"}:
            project["status"] = "ACTIVE"

    campaigns = rebuild_campaign_state(database)
    for campaign in campaigns.campaigns.values():
        project_id = objective_projects.get(campaign.objective_id)
        if project_id is None:
            continue
        project = _ensure(projects, project_id)
        project["campaign_ids"].add(campaign.campaign_id)
        _append_unique(
            project["source_event_refs"],
            *campaign_refs.get(campaign.campaign_id, ()),
        )
        children = [
            campaigns.children[child_id]
            for child_id in campaign.child_ids
            if child_id in campaigns.children
        ]
        completed_children = 0
        for child in children:
            if child.mission_id:
                project["mission_ids"].add(child.mission_id)
                for action_id in child.effect_keys:
                    action_missions.setdefault(action_id, child.mission_id)
            project["action_ids"].update(child.effect_keys)
            if child.status == "SUCCEEDED":
                completed_children += 1
            if child.status in {"BLOCKED", "FAILED", "UNKNOWN"}:
                _append_unique(
                    project["blockers"],
                    f"campaign child {child.child_id} is {child.status}",
                )
            if child.status == "WAITING":
                project["waiting_condition"] = (
                    child.wait_reason
                    or f"campaign child {child.child_id} is waiting"
                )
        if children:
            project["progress_completed"] = completed_children
            project["progress_total"] = len(children)
        if campaign.status == "WAITING":
            project["waiting_condition"] = (
                campaign.last_reason or f"campaign {campaign.campaign_id} waits"
            )
        elif campaign.status in {"FAILED", "ESCALATED"}:
            _append_unique(
                project["blockers"],
                campaign.last_reason
                or f"campaign {campaign.campaign_id} is {campaign.status}",
            )
        elif campaign.status in {"EXECUTING", "REPLANNING", "PLANNED"}:
            project["status"] = "ACTIVE"

    try:
        wakes = rebuild_wake_state(database)
    except Exception:
        wakes = {}
    for wake in wakes.values():
        if str(wake.get("status") or "") != "ACTIVE":
            continue
        objective_id = str(wake.get("objective_id") or "")
        project_id = objective_projects.get(objective_id)
        if project_id is None:
            continue
        project = _ensure(projects, project_id)
        project["waiting_condition"] = _bounded_text(
            wake.get("reason") or wake.get("kind") or "objective wait",
        )
        _append_unique(
            project["source_event_refs"],
            wake.get("last_event_id"),
        )
    for project in projects.values():
        unresolved_waits = []
        for semantic_wait in project["semantic_waits"].values():
            wait_id = str(semantic_wait.get("wait_id") or "")
            wake_id = semantic_wait.get("canonical_wake_id")
            wake_status = str(
                wakes.get(str(wake_id), {}).get("status") or "",
            ).upper()
            if wake_id and wake_status in {"TRIGGERED", "CLEARED"}:
                continue
            transition = project["item_state_changes"]["WAIT"].get(wait_id)
            if (
                not wake_id
                and transition is not None
                and transition["status"] in _TERMINAL_ITEM_STATES
            ):
                continue
            unresolved_waits.append(semantic_wait)
        project["intentional_idle"] = any(
            wait.get("intentional_idle") is True
            for wait in unresolved_waits
        )
        if unresolved_waits:
            latest_wait = max(
                unresolved_waits,
                key=lambda item: (
                    str(item.get("occurred_at") or ""),
                    str(item.get("wait_id") or ""),
                ),
            )
            project["waiting_condition"] = _bounded_text(
                latest_wait.get("reason") or "project is waiting",
            )
        elif (
            project["waiting_condition"] is None
            and project["status"] in {"WAITING", "IDLE"}
        ):
            project["status"] = UNKNOWN

        for attention_id in tuple(project["owner_attention_ids"]):
            transition = project["item_state_changes"]["OWNER_ATTENTION"].get(
                attention_id,
            )
            item = attention.get(attention_id)
            if transition is None or item is None:
                continue
            attention[attention_id] = replace(
                item,
                status=transition["status"],
                source_event_refs=_strings((
                    *item.source_event_refs,
                    *transition["source_event_refs"],
                )),
                evidence_refs=_strings((
                    *item.evidence_refs,
                    *transition["evidence_refs"],
                )),
            )
        project["owner_attention_ids"] = {
            attention_id
            for attention_id in project["owner_attention_ids"]
            if (
                attention_id in attention
                and attention[attention_id].status == "OPEN"
            )
        }
        project["owner_decision_needed"] = bool(
            project["owner_attention_ids"],
        )

    mission_projects: dict[str, str] = {}
    for project_id, project in projects.items():
        for mission_id in project["mission_ids"]:
            mission_projects[str(mission_id)] = project_id
    for mission_id, project_id in sorted(mission_projects.items()):
        row = database.row("missions", "mission_id", mission_id)
        if row is None:
            continue
        project = projects[project_id]
        _append_unique(
            project["source_event_refs"],
            row.get("last_event_id"),
            *mission_refs.get(mission_id, ()),
        )
        activity = str(row.get("updated_at") or "")
        if (
            activity
            and (
                project["last_meaningful_activity"] is None
                or activity > project["last_meaningful_activity"]
            )
        ):
            project["last_meaningful_activity"] = activity
        state = str(row.get("state") or "").upper()
        if state in {"FAILED", "BLOCKED", "UNKNOWN"}:
            _append_unique(project["blockers"], f"mission {mission_id} is {state}")
    for action_id, mission_id in sorted(action_missions.items()):
        project_id = mission_projects.get(mission_id)
        if project_id is None:
            continue
        project = projects[project_id]
        project["action_ids"].add(action_id)
        _append_unique(
            project["source_event_refs"],
            *action_refs.get(action_id, ()),
        )

    states: list[ProjectState] = []
    for project_id in sorted(projects):
        project = projects[project_id]
        if project["progress_values"] and project["progress_total"] is None:
            average = sum(project["progress_values"]) / len(
                project["progress_values"],
            )
            project["progress_completed"] = int(round(average * 100))
            project["progress_total"] = 100
        if project["intentional_idle"]:
            project["status"] = "IDLE"
            project["health"] = "GREEN"
        elif project["owner_decision_needed"]:
            project["status"] = "WAITING_ON_OWNER"
            project["health"] = "AMBER"
        elif project["blockers"]:
            project["status"] = "BLOCKED"
            project["health"] = "RED"
        elif project["waiting_condition"]:
            project["status"] = "WAITING"
            project["health"] = "AMBER"
        elif project["status"] in {"ACTIVE", "COMPLETED"}:
            project["health"] = (
                project["health"]
                if project["health"] != UNKNOWN
                else "GREEN"
            )
        if project["status"] == UNKNOWN:
            project["unknown_fields"].add("status")
        if project["health"] == UNKNOWN:
            project["unknown_fields"].add("health")
        if project["goal_id"] is None:
            project["unknown_fields"].add("goal_id")
        if project["program_id"] is None:
            project["unknown_fields"].add("program_id")
        staleness = _freshness(
            project["last_meaningful_activity"],
            generated_at,
        )
        risk_attention = (
            "OWNER_DECISION"
            if project["owner_decision_needed"]
            else "BLOCKED"
            if project["blockers"]
            else "WATCH"
            if project["health"] == "AMBER" or staleness in {"AGING", "STALE"}
            else "CLEAR"
            if project["health"] == "GREEN"
            else UNKNOWN
        )
        links: list[HierarchyLink] = []
        link_refs = _strings(project["source_event_refs"])
        if project["goal_id"] and project["program_id"]:
            links.append(HierarchyLink(
                "OWNER_GOAL",
                project["goal_id"],
                "PROGRAM",
                project["program_id"],
                link_refs,
            ))
        if project["program_id"]:
            links.append(HierarchyLink(
                "PROGRAM",
                project["program_id"],
                "PROJECT",
                project_id,
                link_refs,
            ))
        for objective_id in sorted(project["objective_ids"]):
            links.append(HierarchyLink(
                "PROJECT",
                project_id,
                "OBJECTIVE",
                objective_id,
                _strings(objective_refs.get(objective_id, ()) or link_refs),
            ))
        for campaign_id in sorted(project["campaign_ids"]):
            campaign = campaigns.campaigns.get(campaign_id)
            if campaign is not None:
                links.append(HierarchyLink(
                    "OBJECTIVE",
                    campaign.objective_id,
                    "CAMPAIGN",
                    campaign_id,
                    _strings(campaign_refs.get(campaign_id, ()) or link_refs),
                ))
        campaign_by_mission: dict[str, str] = {}
        for child in campaigns.children.values():
            if child.mission_id:
                campaign_by_mission[child.mission_id] = child.campaign_id
        for mission_id in sorted(project["mission_ids"]):
            campaign_id = campaign_by_mission.get(mission_id)
            if campaign_id:
                links.append(HierarchyLink(
                    "CAMPAIGN",
                    campaign_id,
                    "MISSION",
                    mission_id,
                    _strings(mission_refs.get(mission_id, ()) or link_refs),
                ))
        for action_id in sorted(project["action_ids"]):
            action_mission = action_missions.get(action_id)
            if action_mission:
                links.append(HierarchyLink(
                    "MISSION",
                    action_mission,
                    "ACTION",
                    action_id,
                    _strings(action_refs.get(action_id, ()) or link_refs),
                ))
        source_refs = _strings(project["source_event_refs"])
        _append_unique(all_source_refs, *source_refs)
        states.append(ProjectState(
            project_id=project_id,
            title=_bounded_text(project["title"]),
            goal_id=project["goal_id"],
            program_id=project["program_id"],
            objective_ids=tuple(sorted(project["objective_ids"])),
            campaign_ids=tuple(sorted(project["campaign_ids"])),
            mission_ids=tuple(sorted(project["mission_ids"])),
            action_ids=tuple(sorted(project["action_ids"])),
            hierarchy_links=tuple(links),
            status=project["status"],
            health=project["health"],
            progress_status=project["progress_status"],
            progress_completed=project["progress_completed"],
            progress_total=project["progress_total"],
            blockers=_strings(project["blockers"]),
            dependencies=_strings(project["dependencies"]),
            recent_changes=tuple(project["recent_changes"][-8:]),
            last_meaningful_activity=project["last_meaningful_activity"],
            next_likely_action=project["next_likely_action"],
            waiting_condition=project["waiting_condition"],
            owner_decision_needed=project["owner_decision_needed"],
            owner_attention_ids=tuple(sorted(project["owner_attention_ids"])),
            jarvis_handled_work=_strings(project["jarvis_handled_work"]),
            staleness=staleness,
            risk_attention=risk_attention,
            intentional_idle=project["intentional_idle"],
            source_event_refs=source_refs,
            evidence_refs=_strings(project["evidence_refs"]),
            unknown_fields=tuple(sorted(project["unknown_fields"])),
        ))

    states.sort(key=lambda value: value.project_id)
    priorities = _priority_projection(states, projects)
    attention_values = tuple(sorted(
        attention.values(),
        key=lambda value: (
            value.status != "OPEN",
            value.project_id,
            value.created_at,
            value.attention_id,
        ),
    ))
    briefing_values = tuple(sorted(
        briefing.values(),
        key=lambda value: (
            value.occurred_at,
            value.entry_id,
        ),
        reverse=True,
    ))
    return ChiefOfStaffProjection(
        generated_at=generated_at,
        projects=tuple(states),
        priorities=priorities,
        owner_attention=attention_values,
        briefing=briefing_values,
        source_event_refs=_strings(all_source_refs),
    )


def chief_of_staff_read_projection(
    database: FoundryDatabase,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Return the JSON-safe, read-only Chief of Staff snapshot."""

    return project_chief_of_staff(database, now=now).as_dict()


__all__ = [
    "CHIEF_OF_STAFF_EVENT_PREFIX",
    "chief_of_staff_read_projection",
    "project_chief_of_staff",
]
