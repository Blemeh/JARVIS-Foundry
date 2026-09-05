from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.chief_of_staff import (
    ChiefOfStaffService,
    project_chief_of_staff,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.decision_queue import build_owner_attention_queue
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    ObjectiveRecord,
    create_objective,
    set_objective_status,
)
from jarvis_foundry.owner_context import (
    ContextProvenance,
    ContextScope,
    OwnerContextStore,
)


NOW = "2026-09-04T12:00:00Z"
FACTOR_WEIGHTS = {
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
FACTOR_NAMES = set(FACTOR_WEIGHTS)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _append_event(
    database: FoundryDatabase,
    event_type: str,
    payload: dict,
    *,
    key: str,
    occurred_at: str = "2026-09-04T10:00:00Z",
) -> Event:
    event = Event.create(
        event_type,
        payload,
        source_type="supervisor",
        source_id="v13-chief-lifecycle-test",
        idempotency_key=key,
        occurred_at=occurred_at,
    )
    database.append(event)
    return event


def _resolve_item(
    database: FoundryDatabase,
    *,
    project_id: str,
    item_kind: str,
    item_id: str,
    from_state: str = "OPEN",
    to_state: str = "RESOLVED",
) -> Event:
    return _append_event(
        database,
        "chief_of_staff.item_state_changed",
        {
            "project_id": project_id,
            "item_kind": item_kind,
            "item_id": item_id,
            "from_state": from_state,
            "to_state": to_state,
            "reason": "focused lifecycle proof completed",
            "change_id": f"resolve:{item_id}",
            "source_event_refs": [f"source:{item_id}"],
            "evidence_refs": [f"evidence:{item_id}"],
        },
        key=f"chief_of_staff.item_state_changed:{item_id}:{to_state}",
        occurred_at="2026-09-04T11:00:00Z",
    )


def _append_change(
    database: FoundryDatabase,
    *,
    change_id: str,
    project_id: str,
    attributes: dict,
    occurred_at: str,
) -> Event:
    return _append_event(
        database,
        "chief_of_staff.change_observed",
        {
            "change": {
                "change_id": change_id,
                "project_id": project_id,
                "change_type": "PROJECT_UPDATE",
                "summary": f"Observed {change_id}",
                "occurred_at": occurred_at,
                "source_event_refs": [f"source:{change_id}"],
                "evidence_refs": [f"evidence:{change_id}"],
                "attributes": attributes,
            },
        },
        key=f"chief_of_staff.change_observed:{change_id}",
        occurred_at=occurred_at,
    )


def _record_priority(
    database: FoundryDatabase,
    *,
    context_id: str,
    value: float,
    effective_at: str,
    authority: str = "REPOSITORY_AUTHORITY",
) -> str:
    text = f"Owner priority {value}"
    record = OwnerContextStore(database).record(
        "PRIORITY",
        text,
        context_id=context_id,
        scope=ContextScope(project_ids=("jarvis",)),
        provenance=ContextProvenance.from_content(
            source_type="repository",
            source_id="v13-chief-lifecycle-test",
            source_ref=f"fixture/{context_id}",
            content=text,
            captured_at=effective_at,
            evidence_refs=(f"evidence:{context_id}",),
            authority=authority,
            owner_directive_id=(
                f"directive:{context_id}"
                if authority == "AUTHENTICATED_OWNER"
                else None
            ),
        ),
        observed_at=effective_at,
        effective_at=effective_at,
        priority=value,
        attributes={"value": value},
    )
    return record.source_event_id


def _record_project(
    database: FoundryDatabase,
    *,
    project_id: str,
    effective_at: str = "2026-09-04T09:00:00Z",
) -> None:
    text = f"Project {project_id}"
    OwnerContextStore(database).record(
        "PROJECT",
        text,
        context_id=f"project:{project_id}",
        scope=ContextScope(project_ids=(project_id,)),
        provenance=ContextProvenance.from_content(
            source_type="repository",
            source_id="v13-chief-lifecycle-test",
            source_ref=f"fixture/project/{project_id}",
            content=text,
            captured_at=effective_at,
            evidence_refs=(f"evidence:project:{project_id}",),
            authority="REPOSITORY_AUTHORITY",
        ),
        observed_at=effective_at,
        effective_at=effective_at,
        attributes={
            "project_id": project_id,
            "title": text,
        },
    )


def _priority_factors(database: FoundryDatabase, project_id: str) -> dict:
    projection = project_chief_of_staff(database, now=NOW)
    priority = next(
        item for item in projection.priorities
        if item.project_id == project_id
    )
    return {factor.name: factor for factor in priority.factors}


def test_noncanonical_idle_wait_resolves_and_stays_resolved_after_restart(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    result = ChiefOfStaffService(database).consider(
        {
            "change_id": "change-idle-until-review",
            "project_id": "jarvis",
            "change_type": "WAITING",
            "summary": "Remain idle until the review evidence arrives",
            "occurred_at": "2026-09-04T09:00:00Z",
            "situation_state": "WAITING",
            "evidence_refs": ["evidence:review-window"],
            "attributes": {
                "intentional_idle": True,
                "waiting_condition": "wait for the review evidence",
            },
        },
        now=NOW,
    )
    waiting = project_chief_of_staff(database, now=NOW).project("jarvis")

    assert result.wait_id
    assert waiting is not None
    assert waiting.intentional_idle is True
    assert waiting.status == "IDLE"

    create_objective(
        database,
        "Continue current JARVIS work",
        objective_id="objective-after-idle",
        provenance={"project_id": "jarvis"},
    )
    _resolve_item(
        database,
        project_id="jarvis",
        item_kind="WAIT",
        item_id=result.wait_id,
        to_state="ACTIVE",
    )
    still_waiting = project_chief_of_staff(database, now=NOW).project("jarvis")

    assert still_waiting is not None
    assert still_waiting.intentional_idle is True

    transition = _resolve_item(
        database,
        project_id="jarvis",
        item_kind="WAIT",
        item_id=result.wait_id,
        from_state="ACTIVE",
    )

    reopened = FoundryDatabase(database.path)
    resumed = project_chief_of_staff(reopened, now=NOW).project("jarvis")

    assert resumed is not None
    assert resumed.waiting_condition is None
    assert resumed.intentional_idle is False
    assert resumed.status == "ACTIVE"
    assert transition.event_id in resumed.source_event_refs


def test_owner_attention_resolution_closes_open_views_and_retains_history(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    result = ChiefOfStaffService(database).consider(
        {
            "change_id": "change-owner-choice",
            "project_id": "jarvis",
            "change_type": "OWNER_DECISION",
            "summary": "The owner must choose one irreversible purchase",
            "occurred_at": "2026-09-04T09:00:00Z",
            "evidence_refs": ["evidence:purchase-options"],
            "attributes": {
                "owner_decision_needed": True,
                "owner_decision_reason": "standing authority cannot choose",
            },
        },
        now=NOW,
    )

    assert result.owner_attention_id
    transition = _resolve_item(
        database,
        project_id="jarvis",
        item_kind="OWNER_ATTENTION",
        item_id=result.owner_attention_id,
    )
    projection = project_chief_of_staff(database, now=NOW)
    project = projection.project("jarvis")
    resolved = next(
        item for item in projection.owner_attention
        if item.attention_id == result.owner_attention_id
    )
    queue = build_owner_attention_queue(database, now=NOW)
    history = next(
        item for item in queue["history"]
        if item["source_event_type"]
        == "chief_of_staff.owner_attention_required"
    )

    assert project is not None
    assert project.owner_decision_needed is False
    assert project.owner_attention_ids == ()
    assert resolved.status == "RESOLVED"
    assert transition.event_id in resolved.source_event_refs
    assert not any(
        item["source_event_id"] == history["source_event_id"]
        for item in queue["open"]
    )
    assert history["status"] == "RESOLVED"
    assert history["resolution_event_id"] == transition.event_id


def test_old_chief_attention_survives_bounded_queue_rebuild_after_restart(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    service = ChiefOfStaffService(database)
    open_result = service.consider(
        {
            "change_id": "change-old-open-attention",
            "project_id": "open-project",
            "change_type": "OWNER_DECISION",
            "summary": "The owner must choose the old open option",
            "occurred_at": "2026-09-04T08:00:00Z",
            "evidence_refs": ["evidence:old-open-attention"],
            "attributes": {
                "owner_decision_needed": True,
                "owner_decision_reason": "standing authority cannot choose",
            },
        },
        now=NOW,
    )
    resolved_result = service.consider(
        {
            "change_id": "change-old-resolved-attention",
            "project_id": "resolved-project",
            "change_type": "OWNER_DECISION",
            "summary": "The owner must choose the old resolved option",
            "occurred_at": "2026-09-04T08:05:00Z",
            "evidence_refs": ["evidence:old-resolved-attention"],
            "attributes": {
                "owner_decision_needed": True,
                "owner_decision_reason": "standing authority cannot choose",
            },
        },
        now=NOW,
    )

    assert open_result.owner_attention_id
    assert resolved_result.owner_attention_id
    attention_events = {
        str(event.payload.get("attention_id")): event
        for event in database.events(
            event_types=("chief_of_staff.owner_attention_required",),
        )
    }
    open_source = attention_events[open_result.owner_attention_id]
    resolved_source = attention_events[resolved_result.owner_attention_id]
    transition = _resolve_item(
        database,
        project_id="resolved-project",
        item_kind="OWNER_ATTENTION",
        item_id=resolved_result.owner_attention_id,
    )
    database.append_events([
        Event.create(
            "supervisor.heartbeat",
            {"noise_index": index},
            source_type="supervisor",
            source_id="v13-chief-lifecycle-test",
            idempotency_key=f"queue-window-noise:{index}",
            occurred_at="2026-09-04T11:30:00Z",
        )
        for index in range(10000)
    ])

    reopened = FoundryDatabase(database.path)
    queue = build_owner_attention_queue(reopened, now=NOW, limit=10)
    open_record = next(
        item for item in queue["open"]
        if item["source_event_id"] == open_source.event_id
    )
    resolved_record = next(
        item for item in queue["history"]
        if item["source_event_id"] == resolved_source.event_id
    )

    assert open_record["status"] == "OPEN"
    assert resolved_record["status"] == "RESOLVED"
    assert resolved_record["resolution_event_id"] == transition.event_id


def test_service_records_wait_and_attention_resolution_receipts(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    service = ChiefOfStaffService(database)
    waiting = service.consider(
        {
            "change_id": "change-wait-before-resolution",
            "project_id": "jarvis",
            "change_type": "WAITING",
            "summary": "Wait for the bounded dependency evidence",
            "occurred_at": "2026-09-04T09:00:00Z",
            "evidence_refs": ["evidence:dependency-wait"],
            "attributes": {
                "intentional_idle": True,
                "waiting_condition": "wait for dependency evidence",
            },
        },
        now=NOW,
    )
    attention = service.consider(
        {
            "change_id": "change-attention-before-resolution",
            "project_id": "jarvis",
            "change_type": "OWNER_DECISION",
            "summary": "The owner must choose the bounded option",
            "occurred_at": "2026-09-04T09:05:00Z",
            "evidence_refs": ["evidence:bounded-options"],
            "attributes": {
                "owner_decision_needed": True,
                "owner_decision_reason": "standing authority cannot choose",
            },
        },
        now=NOW,
    )

    resolved = service.consider(
        {
            "change_id": "change-resolve-wait-and-attention",
            "project_id": "jarvis",
            "change_type": "PROJECT_UPDATE",
            "summary": "Dependency evidence and owner decision arrived",
            "occurred_at": "2026-09-04T11:00:00Z",
            "evidence_refs": ["evidence:resolution"],
            "attributes": {
                "resolved_wait_ids": [waiting.wait_id],
                "resolved_owner_attention_ids": [
                    attention.owner_attention_id,
                ],
                "resolution_reason": "durable conditions changed",
            },
        },
        now=NOW,
    )
    projection = project_chief_of_staff(database, now=NOW)
    project = projection.project("jarvis")
    queue = build_owner_attention_queue(database, now=NOW)

    assert waiting.wait_id
    assert attention.owner_attention_id
    assert resolved.status == "PROJECT_STATE_UPDATED"
    assert project is not None
    assert project.intentional_idle is False
    assert project.waiting_condition is None
    assert project.owner_decision_needed is False
    assert project.owner_attention_ids == ()
    assert len(database.events(
        event_type_prefixes=("chief_of_staff.item_state_changed",),
    )) == 2
    assert not any(
        item["source_event_type"]
        == "chief_of_staff.owner_attention_required"
        for item in queue["open"]
    )


def test_latest_explicit_factors_replace_history_and_terminal_objectives(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _record_priority(
        database,
        context_id="priority:jarvis-old",
        value=90,
        effective_at="2026-09-04T07:00:00Z",
    )
    _record_priority(
        database,
        context_id="priority:jarvis-current",
        value=20,
        effective_at="2026-09-04T10:00:00Z",
    )
    _append_change(
        database,
        change_id="change-high-history",
        project_id="jarvis",
        attributes={"value": 0.9},
        occurred_at="2026-09-04T08:00:00Z",
    )
    _append_change(
        database,
        change_id="change-current-value",
        project_id="jarvis",
        attributes={"value": 0.2},
        occurred_at="2026-09-04T10:30:00Z",
    )
    create_objective(
        database,
        "Completed high-value objective",
        objective_id="objective-terminal-high",
        priority=3.0,
        expected_impact=0.95,
        provenance={"project_id": "jarvis"},
    )
    set_objective_status(
        database,
        "objective-terminal-high",
        "COMPLETED",
        reason="terminal objective must leave current priority",
        evidence_refs=("evidence:objective-terminal-high",),
    )

    factors = _priority_factors(database, "jarvis")

    assert factors["owner_priority"].raw_value == pytest.approx(0.2)
    assert factors["strategic_value"].raw_value == pytest.approx(0.2)
    assert factors["strategic_value"].raw_value != pytest.approx(0.95)


def test_priority_factor_authority_and_semantic_time_select_current_values(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    old_owner_source = _record_priority(
        database,
        context_id="priority:jarvis-authenticated-old",
        value=90,
        effective_at="2026-09-04T07:00:00Z",
        authority="AUTHENTICATED_OWNER",
    )
    current_owner_source = _record_priority(
        database,
        context_id="priority:jarvis-authenticated-current",
        value=20,
        effective_at="2026-09-04T10:00:00Z",
        authority="AUTHENTICATED_OWNER",
    )
    _record_priority(
        database,
        context_id="priority:jarvis-repository-later",
        value=95,
        effective_at="2026-09-04T11:00:00Z",
    )
    current_chief = _append_change(
        database,
        change_id="change-current-chief-factors",
        project_id="jarvis",
        attributes={
            "owner_priority": 0.8,
            "strategic_value": 0.2,
        },
        occurred_at="2026-09-04T10:30:00Z",
    )
    create_objective(
        database,
        "High fallback objective",
        objective_id="objective-high-fallback",
        priority=3.0,
        expected_impact=0.95,
        provenance={"project_id": "jarvis"},
    )
    historical_chief = _append_change(
        database,
        change_id="change-late-arriving-chief-history",
        project_id="jarvis",
        attributes={
            "owner_priority": 0.9,
            "strategic_value": 0.9,
        },
        occurred_at="2026-09-04T08:00:00Z",
    )

    factors = _priority_factors(database, "jarvis")

    assert factors["owner_priority"].raw_value == pytest.approx(0.2)
    assert current_owner_source in factors["owner_priority"].evidence_refs
    assert old_owner_source not in factors["owner_priority"].evidence_refs
    assert factors["strategic_value"].raw_value == pytest.approx(0.2)
    assert current_chief.event_id in factors["strategic_value"].evidence_refs
    assert historical_chief.event_id not in factors["strategic_value"].evidence_refs


def test_current_objective_is_semantic_fallback_when_project_factors_absent(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _record_project(database, project_id="jarvis")
    current = ObjectiveRecord(
        objective_id="objective-current-low",
        objective="Current lower-value objective",
        priority=0.6,
        expected_impact=0.2,
        provenance={"project_id": "jarvis"},
        created_at="2026-09-04T10:00:00Z",
        updated_at="2026-09-04T10:00:00Z",
    )
    historical = ObjectiveRecord(
        objective_id="objective-historical-high",
        objective="Late-arriving historical high-value objective",
        priority=3.0,
        expected_impact=0.95,
        provenance={"project_id": "jarvis"},
        created_at="2026-09-04T07:00:00Z",
        updated_at="2026-09-04T07:00:00Z",
    )
    _append_event(
        database,
        "executive.objective_created",
        current.to_payload(),
        key="executive.objective_created:objective-current-low",
        occurred_at=current.updated_at,
    )
    _append_event(
        database,
        "executive.objective_created",
        historical.to_payload(),
        key="executive.objective_created:objective-historical-high",
        occurred_at=historical.updated_at,
    )

    factors = _priority_factors(database, "jarvis")

    assert factors["owner_priority"].raw_value == pytest.approx(0.2)
    assert factors["strategic_value"].raw_value == pytest.approx(0.2)


def test_priority_disposition_uses_semantic_time_not_append_order(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _append_change(
        database,
        change_id="change-current-high-value",
        project_id="current",
        occurred_at="2026-09-04T10:00:00Z",
        attributes={
            "strategic_value": 0.9,
            "urgency": 1.0,
        },
    )
    _append_event(
        database,
        "chief_of_staff.recommendation_rejected",
        {
            "project_id": "current",
            "change_id": "change-historical-low-value",
            "reason": "LOW_STRATEGIC_VALUE",
            "evidence_refs": ["evidence:historical-rejection"],
        },
        key="chief_of_staff.recommendation_rejected:historical",
        occurred_at="2026-09-04T08:00:00Z",
    )
    _append_change(
        database,
        change_id="change-current-low-value",
        project_id="rejected",
        occurred_at="2026-09-04T10:00:00Z",
        attributes={
            "strategic_value": 0.2,
            "urgency": 1.0,
        },
    )
    _append_event(
        database,
        "chief_of_staff.recommendation_rejected",
        {
            "project_id": "rejected",
            "change_id": "change-current-low-value",
            "reason": "LOW_STRATEGIC_VALUE",
            "evidence_refs": ["evidence:current-rejection"],
        },
        key="chief_of_staff.recommendation_rejected:current",
        occurred_at="2026-09-04T10:00:00Z",
    )

    projection = project_chief_of_staff(database, now=NOW)
    priorities = {
        item.project_id: item
        for item in projection.priorities
    }
    current_urgency = next(
        factor
        for factor in priorities["current"].factors
        if factor.name == "urgency"
    )
    rejected_urgency = next(
        factor
        for factor in priorities["rejected"].factors
        if factor.name == "urgency"
    )

    assert current_urgency.contribution == pytest.approx(
        current_urgency.weight,
    )
    assert rejected_urgency.normalized_value == pytest.approx(1.0)
    assert rejected_urgency.contribution == 0.0


def test_priority_emits_all_mission_factors_and_explicit_unknowns(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _append_change(
        database,
        change_id="change-complete-factor-vector",
        project_id="complete",
        occurred_at="2026-09-04T10:00:00Z",
        attributes={
            "owner_priority": 0.8,
            "strategic_value": 0.7,
            "urgency": 0.6,
            "deadline": 0.5,
            "blocking_value": 0.4,
            "dependency_leverage": 0.3,
            "information_gain": 0.2,
            "cost": 0.25,
            "risk": 0.4,
            "reversibility": 0.9,
            "confidence": 0.8,
            "opportunity_cost": 0.1,
            "staleness": 0.6,
        },
    )
    _append_change(
        database,
        change_id="change-sparse-factor-vector",
        project_id="sparse",
        occurred_at="2026-09-04T10:05:00Z",
        attributes={"strategic_value": 0.4},
    )

    projection = project_chief_of_staff(database, now=NOW)
    complete = next(
        item for item in projection.priorities
        if item.project_id == "complete"
    )
    sparse = next(
        item for item in projection.priorities
        if item.project_id == "sparse"
    )
    complete_factors = {
        factor.name: factor for factor in complete.factors
    }
    sparse_factors = {
        factor.name: factor for factor in sparse.factors
    }

    assert set(complete_factors) == FACTOR_NAMES
    assert {
        name: factor.weight
        for name, factor in complete_factors.items()
    } == pytest.approx(FACTOR_WEIGHTS)
    assert complete.unknown_factors == ()
    assert complete_factors["cost"].raw_value == pytest.approx(0.25)
    assert complete_factors["cost"].normalized_value == pytest.approx(0.75)
    assert complete_factors["opportunity_cost"].raw_value == pytest.approx(0.1)
    assert complete_factors["opportunity_cost"].normalized_value == pytest.approx(
        0.9,
    )
    assert complete.score == pytest.approx(
        sum(factor.contribution for factor in complete.factors),
    )
    assert set(sparse_factors) == FACTOR_NAMES
    assert "cost" in sparse.unknown_factors
    assert "opportunity_cost" in sparse.unknown_factors
    assert sparse_factors["cost"].raw_value is None
    assert sparse_factors["cost"].normalized_value is None
    assert complete.rank is not None
    assert sparse.rank is not None


def test_all_unknown_projects_have_unknown_rank_without_displacing_idle(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _record_project(database, project_id="alpha")
    _record_project(database, project_id="beta")
    _append_event(
        database,
        "chief_of_staff.wait_recorded",
        {
            "project_id": "deliberate-idle",
            "wait_id": "wait:deliberate-idle",
            "reason": "deliberately idle while no work is warranted",
            "intentional_idle": True,
            "source_event_refs": ["source:deliberate-idle"],
            "evidence_refs": ["evidence:deliberate-idle"],
        },
        key="chief_of_staff.wait_recorded:deliberate-idle",
        occurred_at="2026-09-04T09:30:00Z",
    )

    projection = project_chief_of_staff(database, now=NOW)
    priorities = {
        item.project_id: item
        for item in projection.priorities
    }
    serialized = {
        item["project_id"]: item
        for item in projection.as_dict()["priorities"]
    }

    assert priorities["deliberate-idle"].deliberate_idle is True
    assert priorities["deliberate-idle"].rank == 1
    assert priorities["alpha"].rank is None
    assert priorities["beta"].rank is None
    assert serialized["deliberate-idle"]["rank"] == 1
    assert serialized["alpha"]["rank"] == "UNKNOWN"
    assert serialized["beta"]["rank"] == "UNKNOWN"
