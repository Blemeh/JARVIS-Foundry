from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import jarvis_foundry.chief_of_staff.service as chief_service_module
from jarvis_foundry.chief_of_staff import (
    ChiefOfStaffService,
    project_chief_of_staff,
)
from jarvis_foundry.briefing_items import project_briefing_items
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_sources import (
    SourceObservation,
    SourceReadResult,
    SourceSpec,
    content_hash,
    run_source_observation_cycle,
)
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    create_objective,
    set_objective_status,
)
from jarvis_foundry.executive.campaigns import (
    create_campaign,
    declare_child_mission,
)
from jarvis_foundry.executive.continuous import (
    rebuild_wake_state,
    tick_wakes,
)
from jarvis_foundry.owner_context import (
    ContextProvenance,
    ContextScope,
    OwnerContextStore,
    compile_standing_order,
)
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.world_model.observations import WorldObservationStore
from jarvis_foundry.initiative_a2 import (
    InitiativeA2Bounds,
    run_continuous_observation_cycle,
)


NOW = "2026-09-04T12:00:00Z"
SECRET = b"v13-chief-of-staff-secret-32-bytes"


def _provenance(text: str, source_ref: str) -> ContextProvenance:
    return ContextProvenance.from_content(
        source_type="repository",
        source_id="v13-chief-of-staff-test",
        source_ref=source_ref,
        content=text,
        captured_at="2026-09-04T08:00:00Z",
        evidence_refs=(f"evidence:{source_ref}",),
        authority="REPOSITORY_AUTHORITY",
    )


def _record(
    store: OwnerContextStore,
    context_id: str,
    kind: str,
    text: str,
    *,
    project_id: str | None = None,
    program_id: str | None = None,
    objective_id: str | None = None,
    priority: float = 0.0,
    attributes: dict | None = None,
    related_context_ids: tuple[str, ...] = (),
    contradicts: tuple[str, ...] = (),
) -> str:
    store.record(
        kind,
        text,
        context_id=context_id,
        scope=ContextScope(
            global_scope=not any((project_id, program_id, objective_id)),
            project_ids=(project_id,) if project_id else (),
            program_ids=(program_id,) if program_id else (),
            objective_ids=(objective_id,) if objective_id else (),
        ),
        provenance=_provenance(text, f"fixture/{context_id}"),
        observed_at="2026-09-04T08:00:00Z",
        effective_at="2026-09-04T08:00:00Z",
        priority=priority,
        attributes=attributes,
        related_context_ids=related_context_ids,
        contradicts=contradicts,
    )
    return context_id


def _standing_order(
    database: FoundryDatabase,
    *,
    project_id: str = "jarvis",
    directive_id: str = "standing-investigate",
) -> OwnerControlService:
    service = OwnerControlService(database, secret=SECRET)
    text = "If a project becomes blocked, investigate it."
    draft = compile_standing_order(
        text,
        scope=ContextScope(project_ids=(project_id,)),
    )
    metadata = draft.as_metadata()
    service.apply(
        "STANDING_ORDER",
        target=project_id,
        exact_text=text,
        nonce=directive_id,
        signature=sign_command(
            SECRET,
            "STANDING_ORDER",
            project_id,
            text,
            directive_id,
            metadata=metadata,
        ),
        directive_id=directive_id,
        metadata=metadata,
    )
    return service


def _database(tmp_path: Path, *, two_projects: bool = False) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    store = OwnerContextStore(database)
    _record(
        store,
        "goal:personal-operator",
        "GOAL",
        "Run the owner's active work as one coherent portfolio",
    )
    _record(
        store,
        "program:jarvis-1.0",
        "PROGRAM",
        "JARVIS 1.0 personal operator program",
        project_id="jarvis",
    )
    _record(
        store,
        "project:jarvis",
        "PROJECT",
        "JARVIS Foundry",
        project_id="jarvis",
        program_id="program:jarvis-1.0",
        attributes={"title": "JARVIS Foundry"},
        related_context_ids=(
            "goal:personal-operator",
            "program:jarvis-1.0",
        ),
    )
    _record(
        store,
        "priority:jarvis",
        "PRIORITY",
        "JARVIS reliability has high owner priority",
        project_id="jarvis",
        priority=90,
    )
    if two_projects:
        _record(
            store,
            "program:revenue",
            "PROGRAM",
            "Revenue quality program",
            project_id="leadgen",
        )
        _record(
            store,
            "project:leadgen",
            "PROJECT",
            "LeadGen",
            project_id="leadgen",
            program_id="program:revenue",
            attributes={"title": "LeadGen"},
            related_context_ids=(
                "goal:personal-operator",
                "program:revenue",
            ),
        )
        _record(
            store,
            "priority:leadgen",
            "PRIORITY",
            "LeadGen quality has high owner priority",
            project_id="leadgen",
            priority=90,
        )
    return database


def _valuable_change(change_id: str = "change-blocked") -> dict:
    return {
        "change_id": change_id,
        "project_id": "jarvis",
        "change_type": "BLOCKED",
        "summary": "The JARVIS project is blocked by a failing reliability test",
        "occurred_at": "2026-09-04T09:00:00Z",
        "situation_state": "BLOCKED",
        "source_event_refs": ["source:reliability-regression"],
        "evidence_refs": ["evidence:failing-test-log"],
        "attributes": {
            "proposed_objective": (
                "Investigate and repair the bounded reliability regression"
            ),
            "expected_outcome": "The focused reliability test passes",
            "candidate_type": "project_reliability",
            "value": 0.95,
            "urgency": 0.35,
            "confidence": 0.9,
            "reversible": True,
            "blocking_value": 0.9,
            "risk": 0.6,
        },
    }


def _event_count(database: FoundryDatabase, prefix: str) -> int:
    return len(database.events(event_type_prefixes=(prefix,)))


def test_hierarchy_projects_existing_objective_and_campaign_truth(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    store = OwnerContextStore(database)
    _record(
        store,
        "objective:jarvis-v13",
        "ACTIVE_OBJECTIVE",
        "Build the V13 Chief of Staff semantic layer",
        project_id="jarvis",
        objective_id="objective-v13",
        priority=95,
    )
    create_objective(
        database,
        "Build the V13 Chief of Staff semantic layer",
        objective_id="objective-v13",
        progress=0.5,
        expected_impact=0.95,
        urgency=0.4,
        risk=0.2,
        provenance={"project_id": "jarvis"},
    )
    create_campaign(
        database,
        "objective-v13",
        campaign_id="campaign-v13",
        metadata={"project_id": "jarvis"},
    )
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": "mission-v13",
            "objective": "Implement the bounded Chief of Staff layer",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["focused proof passes"],
            "next_action": "run focused proof",
        },
        source_type="supervisor",
        source_id="test",
        idempotency_key="mission.created:mission-v13",
    ))
    declare_child_mission(
        database,
        "campaign-v13",
        child_id="child-v13",
        mission_id="mission-v13",
        effect_action_key="action-v13",
        effect_keys=("action-v13",),
    )
    database.append(Event.create(
        "action.allowed",
        {
            "action_id": "action-v13",
            "mission_id": "mission-v13",
            "decision": "ALLOW",
            "reason": "bounded local proof action",
            "target": "disposable-proof",
        },
        source_type="supervisor",
        source_id="test",
        idempotency_key="action.allowed:action-v13",
    ))

    projection = project_chief_of_staff(database, now=NOW)
    project = projection.project("jarvis")

    assert project is not None
    assert project.goal_id == "goal:personal-operator"
    assert project.program_id == "program:jarvis-1.0"
    assert project.objective_ids == ("objective-v13",)
    assert project.campaign_ids == ("campaign-v13",)
    assert project.mission_ids == ("mission-v13",)
    assert project.action_ids == ("action-v13",)
    assert project.progress_completed == 0
    assert project.progress_total == 1
    links = {
        (link.parent_kind, link.parent_id, link.child_kind, link.child_id)
        for link in project.hierarchy_links
    }
    assert (
        "OWNER_GOAL",
        "goal:personal-operator",
        "PROGRAM",
        "program:jarvis-1.0",
    ) in links
    assert (
        "PROJECT",
        "jarvis",
        "OBJECTIVE",
        "objective-v13",
    ) in links
    assert (
        "OBJECTIVE",
        "objective-v13",
        "CAMPAIGN",
        "campaign-v13",
    ) in links
    assert (
        "CAMPAIGN",
        "campaign-v13",
        "MISSION",
        "mission-v13",
    ) in links
    assert (
        "MISSION",
        "mission-v13",
        "ACTION",
        "action-v13",
    ) in links


def test_standing_order_routes_one_observed_candidate_and_replay_is_zero_delta(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    service = ChiefOfStaffService(database)

    first = service.consider(_valuable_change(), now=NOW)
    after_first = database.event_count()
    replay = service.consider(_valuable_change(), now=NOW)

    assert first.status == "ROUTED_TO_EXISTING_CANDIDATE_PATH"
    assert first.candidate_id
    assert first.duplicate is False
    assert replay.candidate_id == first.candidate_id
    assert replay.duplicate is True
    assert database.event_count() == after_first
    assert database.count("candidates") == 1
    assert _event_count(database, "executive.objective_") == 0
    assert _event_count(database, "mission.") == 0
    assert _event_count(database, "effect.") == 0
    assert rebuild_wake_state(database) == {}


def test_low_value_urgency_does_not_outrank_strategic_value(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path, two_projects=True)
    service = ChiefOfStaffService(database)
    low_value = {
        "change_id": "change-urgent-low-value",
        "project_id": "jarvis",
        "change_type": "OPPORTUNITY",
        "summary": "A cosmetic cleanup looks urgent",
        "occurred_at": "2026-09-04T09:00:00Z",
        "evidence_refs": ["evidence:cosmetic"],
        "attributes": {
            "proposed_objective": "Polish a cosmetic label",
            "value": 0.2,
            "urgency": 1.0,
            "confidence": 0.9,
            "reversible": True,
        },
    }
    strategic = {
        "change_id": "change-strategic",
        "project_id": "leadgen",
        "change_type": "OPPORTUNITY",
        "summary": "Contact evidence can be made materially more reliable",
        "occurred_at": "2026-09-04T09:05:00Z",
        "evidence_refs": ["evidence:contact-quality"],
        "attributes": {
            "proposed_objective": "Repair contact evidence lineage",
            "value": 0.9,
            "urgency": 0.1,
            "confidence": 0.2,
            "reversible": True,
        },
    }

    assert service.consider(low_value, now=NOW).status == "REJECTED"
    assert service.consider(strategic, now=NOW).status == "REJECTED"
    projection = service.snapshot(now=NOW)

    assert projection.priorities[0].project_id == "leadgen"
    assert projection.priorities[0].score > projection.priorities[1].score
    assert database.count("candidates") == 0


def test_recoverable_wait_is_quiet_and_creates_no_wake(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    service = ChiefOfStaffService(database)
    result = service.consider({
        "change_id": "change-provider-wait",
        "project_id": "jarvis",
        "change_type": "PROVIDER_UNAVAILABLE",
        "summary": "A provider is temporarily unavailable and can be retried",
        "occurred_at": "2026-09-04T09:00:00Z",
        "situation_state": "WAITING",
        "evidence_refs": ["evidence:provider-health"],
        "attributes": {
            "recoverable": True,
            "waiting_condition": "wait for a positive provider health sample",
        },
    }, now=NOW)
    projection = service.snapshot(now=NOW)
    project = projection.project("jarvis")

    assert result.status == "WAITING"
    assert result.wait_id
    assert project is not None
    assert project.waiting_condition == (
        "wait for a positive provider health sample"
    )
    assert project.owner_decision_needed is False
    assert projection.owner_attention == ()
    assert rebuild_wake_state(database) == {}
    assert _event_count(database, "executive.wake_") == 0


def test_existing_objective_dependency_wait_uses_canonical_wake_and_unlocks(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    create_objective(
        database,
        "Dependency prerequisite",
        objective_id="objective-dependency",
        provenance={"project_id": "jarvis"},
    )
    create_objective(
        database,
        "Continue after dependency",
        objective_id="objective-waiting",
        provenance={"project_id": "jarvis"},
    )
    service = ChiefOfStaffService(database)
    waiting_change = {
        "change_id": "change-dependency-wait",
        "project_id": "jarvis",
        "objective_id": "objective-waiting",
        "change_type": "DEPENDENCY_WAIT",
        "summary": "The project is waiting for its prerequisite objective",
        "occurred_at": "2026-09-04T09:00:00Z",
        "evidence_refs": ["evidence:dependency-edge"],
        "attributes": {
            "recoverable": True,
            "wait_kind": "DEPENDENCY",
            "dependency_id": "objective-dependency",
            "waiting_condition": "wait for objective-dependency",
        },
    }

    waiting = service.consider(waiting_change, now=NOW)
    before_replay = database.event_count()
    replay = service.consider(waiting_change, now=NOW)
    wakes = rebuild_wake_state(database)

    assert waiting.status == "WAITING"
    assert waiting.wait_id
    assert waiting.canonical_wake_id in wakes
    assert waiting.wait_id not in wakes
    assert replay.wait_id == waiting.wait_id
    assert replay.canonical_wake_id == waiting.canonical_wake_id
    assert database.event_count() == before_replay
    assert len(wakes) == 1
    assert wakes[waiting.canonical_wake_id]["kind"] == "WAIT_FOR_DEPENDENCY"

    set_objective_status(
        database,
        "objective-dependency",
        "COMPLETED",
        reason="dependency proof completed",
        evidence_refs=("evidence:dependency-complete",),
    )
    triggered = tick_wakes(database)
    continued = service.consider(
        _valuable_change("change-after-dependency"),
        now=NOW,
    )
    resumed_project = service.snapshot(now=NOW).project("jarvis")

    assert triggered == [waiting.canonical_wake_id]
    assert rebuild_wake_state(database)[
        waiting.canonical_wake_id
    ]["status"] == "TRIGGERED"
    assert continued.status == "ROUTED_TO_EXISTING_CANDIDATE_PATH"
    assert resumed_project is not None
    assert resumed_project.waiting_condition is None
    assert resumed_project.status == "ACTIVE"


def test_material_owner_decision_is_attention_not_execution(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    service = ChiefOfStaffService(database)
    result = service.consider({
        "change_id": "change-owner-decision",
        "project_id": "jarvis",
        "change_type": "OWNER_DECISION",
        "summary": "Two mutually exclusive irreversible purchases remain",
        "occurred_at": "2026-09-04T09:00:00Z",
        "evidence_refs": ["evidence:purchase-options"],
        "attributes": {
            "owner_decision_needed": True,
            "owner_decision_reason": (
                "the standing authority does not choose between purchases"
            ),
        },
    }, now=NOW)
    projection = service.snapshot(now=NOW)

    assert result.status == "OWNER_ATTENTION_REQUIRED"
    assert result.owner_attention_id
    assert len(projection.owner_attention) == 1
    assert projection.owner_attention[0].attention_id == (
        result.owner_attention_id
    )
    assert database.count("candidates") == 0
    assert _event_count(database, "executive.objective_") == 0
    assert _event_count(database, "mission.") == 0


def test_stop_and_veto_prevent_all_chief_of_staff_writes(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    stop_path = tmp_path / "EMERGENCY_STOP"
    stop_path.write_text("stop\n", encoding="utf-8")
    service = ChiefOfStaffService(database, stop_path=stop_path)
    before_stop = database.event_count()

    stopped = service.consider(_valuable_change("change-stop"), now=NOW)

    assert stopped.status == "BLOCKED_BY_OWNER_CONTROL"
    assert stopped.recorded is False
    assert database.event_count() == before_stop

    stop_path.unlink()
    controls = OwnerControlService(database, secret=SECRET)
    text = "Veto project investigation"
    controls.apply(
        "VETO_ACTION",
        target="jarvis",
        exact_text=text,
        nonce="veto-jarvis",
        signature=sign_command(
            SECRET,
            "VETO_ACTION",
            "jarvis",
            text,
            "veto-jarvis",
        ),
        directive_id="veto-jarvis",
    )
    before_veto = database.event_count()

    vetoed = service.consider(_valuable_change("change-veto"), now=NOW)

    assert vetoed.status == "BLOCKED_BY_OWNER_CONTROL"
    assert vetoed.recorded is False
    assert database.event_count() == before_veto
    assert database.count("candidates") == 0


def test_revoked_standing_order_never_authorizes_candidate(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    controls = _standing_order(database)
    text = "Owner revoked the investigation standing order"
    controls.apply(
        "REVOKE",
        target="standing-investigate",
        exact_text=text,
        nonce="revoke-investigate",
        signature=sign_command(
            SECRET,
            "REVOKE",
            "standing-investigate",
            text,
            "revoke-investigate",
        ),
    )

    result = ChiefOfStaffService(database).consider(
        _valuable_change("change-revoked"),
        now=NOW,
    )

    assert result.status == "WAITING"
    assert result.rejection_reason == "PROACTIVE_AUTHORITY_NOT_ESTABLISHED"
    assert result.standing_order_ids == ()
    assert database.count("candidates") == 0
    assert rebuild_wake_state(database) == {}


def test_intentional_idle_remains_idle_and_unranked(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    result = ChiefOfStaffService(database).consider({
        "change_id": "change-idle",
        "project_id": "jarvis",
        "change_type": "WAITING",
        "summary": "The project is deliberately idle until the review window",
        "occurred_at": "2026-09-04T09:00:00Z",
        "evidence_refs": ["evidence:review-window"],
        "attributes": {
            "intentional_idle": True,
            "waiting_condition": "wait for the scheduled owner review",
        },
    }, now=NOW)
    projection = project_chief_of_staff(database, now=NOW)
    project = projection.project("jarvis")

    assert result.status == "WAITING"
    assert project is not None
    assert project.intentional_idle is True
    assert project.status == "IDLE"
    assert project.health == "GREEN"
    assert projection.priorities[0].score == 0.0
    assert projection.owner_attention == ()


def test_restart_rebuild_is_identical_and_projection_is_read_only(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    service = ChiefOfStaffService(database)
    service.consider(_valuable_change(), now=NOW)
    before = database.event_count()

    first = project_chief_of_staff(database, now=NOW).as_dict()
    reopened = FoundryDatabase(database.path)
    second = project_chief_of_staff(reopened, now=NOW).as_dict()
    third = project_chief_of_staff(reopened, now=NOW).as_dict()

    assert second == first
    assert third == first
    assert database.event_count() == before
    assert database.quick_check() == "ok"


def test_command_center_and_packet_expose_the_same_read_only_project_truth(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    ChiefOfStaffService(database).consider(_valuable_change(), now=NOW)
    before = database.event_count()

    snapshot = build_snapshot(database, now=NOW)
    chief = snapshot["executive"]["chief_of_staff"]
    packet = snapshot["briefing_packet"]

    assert chief["contract"] == "jarvis.chief-of-staff.v1"
    assert chief["projects"][0]["project_id"] == "jarvis"
    assert packet["chief_of_staff"] == chief
    assert packet["content_digest"]
    assert database.event_count() == before


def test_owner_attention_uses_existing_briefing_and_decision_queue_rules(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    result = ChiefOfStaffService(database).consider({
        "change_id": "change-owner-attention-briefing",
        "project_id": "jarvis",
        "change_type": "OWNER_DECISION",
        "summary": "The owner must choose between two irreversible purchases",
        "occurred_at": "2026-09-04T09:00:00Z",
        "evidence_refs": ["evidence:purchase-options"],
        "attributes": {
            "owner_decision_needed": True,
            "owner_decision_reason": "no standing authority selects a purchase",
        },
    }, now=NOW)
    before = database.event_count()

    items = project_briefing_items(database, now=NOW, limit=100)
    snapshot = build_snapshot(database, now=NOW)
    attention_item = next(
        item
        for item in items
        if item.source_event_type
        == "chief_of_staff.owner_attention_required"
    )
    queue = snapshot["decision_queue"]
    packet = snapshot["briefing_packet"]

    assert attention_item.category == "PROJECT"
    assert attention_item.entity_kind == "project"
    assert attention_item.entity_id == "jarvis"
    assert attention_item.owner_action_required == "DECISION_REQUIRED"
    matching_queue_items = [
        item
        for item in queue["open"]
        if item["source_event_id"] == attention_item.source_event_id
    ]
    assert len(matching_queue_items) == 1
    assert matching_queue_items[0]["classification"] == "DECISION_REQUIRED"
    assert any(
        item.get("attention_id") == result.owner_attention_id
        or item.get("source_event_id") == attention_item.source_event_id
        for item in packet["decision_required"]
    )
    assert database.event_count() == before


def _chief_source_spec(
    *,
    interval: int = 1,
    chief_overrides: dict | None = None,
) -> SourceSpec:
    chief_configuration = {
        "enabled": True,
        "change_type": "BLOCKED",
        "summary": (
            "The JARVIS project is blocked by a reliability regression"
        ),
        "value": 0.95,
        "strategic_value": 0.95,
        "urgency": 0.35,
        "deadline": 0.25,
        "blocking_value": 0.90,
        "dependency_leverage": 0.85,
        "information_gain": 0.80,
        "cost": 0.20,
        "risk": 0.30,
        "reversible": True,
        "reversibility": 1.0,
        "confidence": 0.90,
        "opportunity_cost": 0.15,
        "staleness": 0.10,
    }
    chief_configuration.update(chief_overrides or {})
    return SourceSpec(
        "v13-project-watch",
        "project",
        "project:jarvis:v13-runtime",
        interval,
        config={"chief_of_staff": chief_configuration},
    )


def _chief_source_observation() -> SourceObservation:
    payload = {
        "summary": "The bounded reliability test is failing",
        "state": "BLOCKED",
        "revision": "regression-1",
    }
    return SourceObservation(
        "project",
        "project:jarvis:reliability-regression",
        "evidence:v13-runtime-reliability-log",
        payload,
        content_hash(payload),
        proposed_outcome=(
            "Investigate and repair the bounded reliability regression"
        ),
        target_project="jarvis",
        alias_key="jarvis:bounded-reliability-regression",
        candidate_type="project_reliability",
        confidence=0.90,
    )


def _chief_source_reader(_spec, stop_check):
    assert stop_check() is False
    return SourceReadResult("READY", (_chief_source_observation(),))


def _start_source_cycle(database: FoundryDatabase, cycle_id: str) -> None:
    database.append(Event.create(
        "initiative.cycle_started",
        {"cycle_id": cycle_id, "status": "RUNNING"},
        source_type="test",
        source_id="v13-chief-runtime-test",
        idempotency_key=f"initiative.cycle_started:{cycle_id}",
    ))


def test_continuous_project_observation_routes_through_chief_once(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    bounds = InitiativeA2Bounds(
        source_limit=1,
        candidate_limit=2,
        research_candidate_limit=0,
        research_budget_per_candidate=0.0,
        research_budget_per_cycle=0.0,
    )
    current = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)

    first = run_continuous_observation_cycle(
        database,
        sources=[_chief_source_spec()],
        bounds=bounds,
        source_readers={"project": _chief_source_reader},
        now=current,
    )
    chief_events_after_first = _event_count(database, "chief_of_staff.")
    replay = run_continuous_observation_cycle(
        database,
        sources=[_chief_source_spec()],
        bounds=bounds,
        source_readers={"project": _chief_source_reader},
        now=current + timedelta(seconds=1),
    )

    assert first["cycles_dispatched"] == 1
    assert first["metrics"]["unique_candidates"] == 1
    assert database.count("world_observations") == 1
    assert database.count("candidates") == 1
    assert _event_count(database, "chief_of_staff.change_observed") == 1
    assert _event_count(database, "chief_of_staff.candidate_routed") == 1
    assert replay["cycles_dispatched"] == 1
    assert replay["metrics"]["raw_candidates_generated"] == 1
    assert replay["metrics"]["unique_candidates"] == 0
    assert _event_count(database, "chief_of_staff.") == chief_events_after_first
    assert _event_count(database, "executive.objective_") == 0
    assert _event_count(database, "mission.") == 0
    assert _event_count(database, "effect.") == 0


def test_unchanged_replay_repairs_missing_chief_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    cycle_id = "v13-runtime-repair-observation"
    _start_source_cycle(database, cycle_id)
    original = ChiefOfStaffService.consider
    calls = {"count": 0}

    def fail_once(self, value, *, now=None, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("fault after durable world observation")
        return original(self, value, now=now, **kwargs)

    monkeypatch.setattr(ChiefOfStaffService, "consider", fail_once)
    with pytest.raises(
        RuntimeError,
        match="fault after durable world observation",
    ):
        run_source_observation_cycle(
            database,
            cycle_id,
            [_chief_source_spec()],
            readers={"project": _chief_source_reader},
            now=NOW,
        )

    assert database.count("world_observations") == 1
    assert _event_count(database, "chief_of_staff.") == 0

    repaired = run_source_observation_cycle(
        database,
        cycle_id,
        [_chief_source_spec()],
        readers={"project": _chief_source_reader},
        now=NOW,
    )

    assert repaired["sources"][0]["chief_of_staff_repairs"] == 1
    assert repaired["candidates_created"] == 1
    assert _event_count(database, "chief_of_staff.change_observed") == 1
    assert _event_count(database, "chief_of_staff.candidate_routed") == 1
    assert database.count("candidates") == 1


@pytest.mark.parametrize("control_kind", ("stop", "veto", "revoke"))
def test_late_stop_veto_and_revocation_block_transactional_ingress(
    tmp_path: Path,
    control_kind: str,
) -> None:
    case_root = tmp_path / control_kind
    case_root.mkdir()
    database = _database(case_root)
    controls = _standing_order(database)
    stop_path = case_root / "EMERGENCY_STOP"
    injected = {"done": False}
    points: list[str] = []

    def inject(point: str) -> None:
        points.append(point)
        if point != "before_candidate_transaction" or injected["done"]:
            return
        injected["done"] = True
        if control_kind == "stop":
            stop_path.write_text("late owner stop\n", encoding="utf-8")
            return
        if control_kind == "veto":
            text = "Veto the late project investigation"
            controls.apply(
                "VETO_ACTION",
                target="jarvis",
                exact_text=text,
                nonce="late-veto",
                signature=sign_command(
                    SECRET,
                    "VETO_ACTION",
                    "jarvis",
                    text,
                    "late-veto",
                ),
                directive_id="late-veto",
            )
            return
        text = "Revoke investigation authority before candidate commit"
        controls.apply(
            "REVOKE",
            target="standing-investigate",
            exact_text=text,
            nonce="late-revoke",
            signature=sign_command(
                SECRET,
                "REVOKE",
                "standing-investigate",
                text,
                "late-revoke",
            ),
        )

    result = ChiefOfStaffService(
        database,
        stop_path=stop_path,
        fault_hook=inject,
    ).consider(
        _valuable_change(f"change-late-{control_kind}"),
        now=NOW,
    )

    assert injected["done"] is True
    assert "candidate_transaction_guard" in points
    assert result.status == (
        "WAITING" if control_kind == "revoke"
        else "BLOCKED_BY_OWNER_CONTROL"
    )
    assert database.count("candidates") == 0
    assert _event_count(database, "chief_of_staff.candidate_routed") == 0
    assert _event_count(database, "executive.objective_") == 0
    assert _event_count(database, "mission.") == 0
    assert _event_count(database, "effect.") == 0


def test_candidate_commit_before_route_receipt_is_replay_repaired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    cycle_id = "v13-runtime-repair-route"
    _start_source_cycle(database, cycle_id)
    original_fault = ChiefOfStaffService._fault
    raised = {"done": False}
    second_payload = {
        "summary": "A second bounded reliability test is failing",
        "state": "BLOCKED",
        "revision": "regression-2",
    }
    second_observation = replace(
        _chief_source_observation(),
        source_identity="project:jarvis:second-reliability-regression",
        source_ref="evidence:v13-runtime-second-reliability-log",
        payload=second_payload,
        content_hash=content_hash(second_payload),
        alias_key="jarvis:second-bounded-reliability-regression",
    )

    def two_candidate_reader(_spec, stop_check):
        assert stop_check() is False
        return SourceReadResult(
            "READY",
            (_chief_source_observation(), second_observation),
        )

    def fail_after_candidate(self, point: str) -> None:
        if point == "after_candidate_ingress" and not raised["done"]:
            raised["done"] = True
            raise RuntimeError("fault after candidate commit")
        original_fault(self, point)

    monkeypatch.setattr(
        ChiefOfStaffService,
        "_fault",
        fail_after_candidate,
    )
    with pytest.raises(RuntimeError, match="fault after candidate commit"):
        run_source_observation_cycle(
            database,
            cycle_id,
            [_chief_source_spec()],
            candidate_limit=1,
            readers={"project": two_candidate_reader},
            now=NOW,
        )

    assert database.count("world_observations") == 1
    assert database.count("candidates") == 1
    assert _event_count(database, "chief_of_staff.change_observed") == 1
    assert _event_count(database, "chief_of_staff.candidate_routed") == 0

    repaired = run_source_observation_cycle(
        database,
        cycle_id,
        [_chief_source_spec()],
        candidate_limit=1,
        readers={"project": two_candidate_reader},
        now=NOW,
    )

    assert repaired["sources"][0]["chief_of_staff_repairs"] == 1
    assert repaired["candidates_created"] == 0
    assert repaired["duplicates_suppressed"] == 1
    assert repaired["sources"][0]["chief_of_staff_results"][1]["status"] == (
        "CANDIDATE_LIMIT_REACHED"
    )
    assert database.count("world_observations") == 2
    assert database.count("candidates") == 1
    assert _event_count(database, "chief_of_staff.change_observed") == 2
    assert _event_count(database, "chief_of_staff.candidate_routed") == 1


def test_owner_control_is_rechecked_immediately_before_candidate_insert(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    stop_path = tmp_path / "EMERGENCY_STOP"
    guard_calls = {"count": 0}

    def stop_on_preinsert_guard(point: str) -> None:
        if point != "candidate_transaction_guard":
            return
        guard_calls["count"] += 1
        if guard_calls["count"] == 2:
            stop_path.write_text("owner stopped candidate ingress\n", encoding="utf-8")

    result = ChiefOfStaffService(
        database,
        stop_path=stop_path,
        fault_hook=stop_on_preinsert_guard,
    ).consider(
        _valuable_change("change-stop-at-preinsert"),
        now=NOW,
    )

    assert guard_calls["count"] == 2
    assert result.status == "BLOCKED_BY_OWNER_CONTROL"
    assert database.count("candidates") == 0
    assert _event_count(database, "chief_of_staff.candidate_routed") == 0


@pytest.mark.parametrize(
    "configured_objective",
    (False, True),
    ids=("standing-order-objective", "configured-objective"),
)
def test_chief_derived_candidates_share_the_cycle_candidate_limit(
    tmp_path: Path,
    configured_objective: bool,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    cycle_id = (
        "v13-standing-order-limit"
        if not configured_objective
        else "v13-configured-objective-limit"
    )
    _start_source_cycle(database, cycle_id)
    overrides = (
        {
            "proposed_objective": (
                "Investigate the configured project reliability regression"
            ),
        }
        if configured_objective else {}
    )
    spec = _chief_source_spec(chief_overrides=overrides)
    observations = []
    for suffix in ("one", "two"):
        payload = {
            "summary": f"Bounded reliability signal {suffix}",
            "state": "BLOCKED",
            "revision": suffix,
        }
        observations.append(replace(
            _chief_source_observation(),
            source_identity=f"project:jarvis:limit-{suffix}",
            source_ref=f"evidence:v13-limit-{suffix}",
            payload=payload,
            content_hash=content_hash(payload),
            proposed_outcome=None,
            alias_key=f"jarvis:limit-{suffix}",
        ))

    def reader(_spec, stop_check):
        assert stop_check() is False
        return SourceReadResult("READY", tuple(observations))

    result = run_source_observation_cycle(
        database,
        cycle_id,
        [spec],
        candidate_limit=1,
        readers={"project": reader},
        now=NOW,
    )

    statuses = [
        item["status"]
        for item in result["sources"][0]["chief_of_staff_results"]
    ]
    assert result["sources"][0]["raw_candidates"] == 2
    assert result["candidates_created"] == 1
    assert database.count("candidates") == 1
    assert statuses == [
        "ROUTED_TO_EXISTING_CANDIDATE_PATH",
        "CANDIDATE_LIMIT_REACHED",
    ]


def test_partial_rejection_replay_adds_missing_wait_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    cycle_id = "v13-partial-rejection-wait-repair"
    _start_source_cycle(database, cycle_id)
    spec = _chief_source_spec(chief_overrides={
        "value": 0.20,
        "strategic_value": 0.20,
    })
    original_schedule = chief_service_module._schedule_existing_objective_wait
    raised = {"done": False}

    def fail_once(*args, **kwargs):
        if not raised["done"]:
            raised["done"] = True
            raise RuntimeError("fault after recommendation rejection")
        return original_schedule(*args, **kwargs)

    monkeypatch.setattr(
        chief_service_module,
        "_schedule_existing_objective_wait",
        fail_once,
    )
    with pytest.raises(
        RuntimeError,
        match="fault after recommendation rejection",
    ):
        run_source_observation_cycle(
            database,
            cycle_id,
            [spec],
            candidate_limit=1,
            readers={"project": _chief_source_reader},
            now=NOW,
        )

    assert _event_count(
        database,
        "chief_of_staff.recommendation_rejected",
    ) == 1
    assert _event_count(database, "chief_of_staff.wait_recorded") == 0

    repaired = run_source_observation_cycle(
        database,
        cycle_id,
        [spec],
        candidate_limit=1,
        readers={"project": _chief_source_reader},
        now=NOW,
    )

    assert repaired["sources"][0]["chief_of_staff_repairs"] == 1
    assert repaired["sources"][0]["chief_of_staff_results"][0]["status"] == (
        "WAITING"
    )
    assert repaired["candidates_created"] == 0
    assert _event_count(database, "chief_of_staff.wait_recorded") == 1


def test_partial_multi_item_lifecycle_replay_finishes_every_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    cycle_id = "v13-partial-multi-item-repair"
    _start_source_cycle(database, cycle_id)
    spec = _chief_source_spec(chief_overrides={
        "change_type": "PROJECT_UPDATE",
        "resolved_wait_ids": ["wait-one", "wait-two"],
        "resolution_reason": "both dependency waits are complete",
    })
    observation = replace(
        _chief_source_observation(),
        proposed_outcome=None,
    )
    original_append = chief_service_module._append
    state_calls = {"count": 0}
    raised = {"done": False}

    def fail_second_state_change(
        database_arg,
        event_type,
        payload,
        **kwargs,
    ):
        if event_type == "chief_of_staff.item_state_changed":
            state_calls["count"] += 1
            if state_calls["count"] == 2 and not raised["done"]:
                raised["done"] = True
                raise RuntimeError("fault between lifecycle transitions")
        return original_append(
            database_arg,
            event_type,
            payload,
            **kwargs,
        )

    def reader(_spec, stop_check):
        assert stop_check() is False
        return SourceReadResult("READY", (observation,))

    monkeypatch.setattr(
        chief_service_module,
        "_append",
        fail_second_state_change,
    )
    with pytest.raises(
        RuntimeError,
        match="fault between lifecycle transitions",
    ):
        run_source_observation_cycle(
            database,
            cycle_id,
            [spec],
            candidate_limit=1,
            readers={"project": reader},
            now=NOW,
        )

    assert _event_count(
        database,
        "chief_of_staff.item_state_changed",
    ) == 1

    repaired = run_source_observation_cycle(
        database,
        cycle_id,
        [spec],
        candidate_limit=1,
        readers={"project": reader},
        now=NOW,
    )

    assert repaired["sources"][0]["chief_of_staff_repairs"] == 1
    assert repaired["sources"][0]["chief_of_staff_results"][0]["status"] == (
        "PROJECT_STATE_UPDATED"
    )
    assert _event_count(
        database,
        "chief_of_staff.item_state_changed",
    ) == 2
    assert database.count("candidates") == 0


def test_change_receipt_replay_does_not_mark_new_candidate_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    cycle_id = "v13-change-receipt-before-candidate"
    _start_source_cycle(database, cycle_id)
    original_fault = ChiefOfStaffService._fault
    raised = {"done": False}

    def fail_before_candidate(self, point: str) -> None:
        if point == "before_candidate_ingress" and not raised["done"]:
            raised["done"] = True
            raise RuntimeError("fault after change receipt")
        original_fault(self, point)

    monkeypatch.setattr(
        ChiefOfStaffService,
        "_fault",
        fail_before_candidate,
    )
    with pytest.raises(RuntimeError, match="fault after change receipt"):
        run_source_observation_cycle(
            database,
            cycle_id,
            [_chief_source_spec()],
            candidate_limit=1,
            readers={"project": _chief_source_reader},
            now=NOW,
        )

    assert _event_count(database, "chief_of_staff.change_observed") == 1
    assert database.count("candidates") == 0

    repaired = run_source_observation_cycle(
        database,
        cycle_id,
        [_chief_source_spec()],
        candidate_limit=1,
        readers={"project": _chief_source_reader},
        now=NOW,
    )
    considered = repaired["sources"][0]["chief_of_staff_results"][0]

    assert considered["duplicate"] is False
    assert repaired["candidates_created"] == 1
    assert repaired["duplicates_suppressed"] == 0
    assert database.count("candidates") == 1


@pytest.mark.parametrize(
    ("field", "initial", "corrected"),
    (
        ("alias_key", "jarvis:alias-old", "jarvis:alias-corrected"),
        ("candidate_type", "reliability-old", "reliability-corrected"),
        ("confidence", 0.70, 0.90),
        (
            "expires_at",
            "2026-09-05T12:00:00Z",
            "2026-09-06T12:00:00Z",
        ),
    ),
)
def test_corrected_item_routing_inputs_get_a_new_change_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    initial,
    corrected,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    cycle_id = f"v13-routing-identity-{field}"
    _start_source_cycle(database, cycle_id)
    spec = _chief_source_spec()
    if field == "confidence":
        chief_configuration = dict(spec.config["chief_of_staff"])
        chief_configuration.pop("confidence")
        spec = replace(
            spec,
            config={"chief_of_staff": chief_configuration},
        )
    current = {
        "observation": replace(
            _chief_source_observation(),
            **{field: initial},
        ),
    }
    original_fault = ChiefOfStaffService._fault
    raised = {"done": False}

    def reader(_spec, stop_check):
        assert stop_check() is False
        return SourceReadResult("READY", (current["observation"],))

    def fail_before_candidate(self, point: str) -> None:
        if point == "before_candidate_ingress" and not raised["done"]:
            raised["done"] = True
            raise RuntimeError("fault before corrected routing replay")
        original_fault(self, point)

    monkeypatch.setattr(
        ChiefOfStaffService,
        "_fault",
        fail_before_candidate,
    )
    with pytest.raises(
        RuntimeError,
        match="fault before corrected routing replay",
    ):
        run_source_observation_cycle(
            database,
            cycle_id,
            [spec],
            candidate_limit=1,
            readers={"project": reader},
            now=NOW,
        )

    current["observation"] = replace(
        current["observation"],
        **{field: corrected},
    )
    repaired = run_source_observation_cycle(
        database,
        cycle_id,
        [spec],
        candidate_limit=1,
        readers={"project": reader},
        now=NOW,
    )
    change_events = database.events(
        event_types=("chief_of_staff.change_observed",),
    )
    change_ids = {
        str(event.payload["change"]["change_id"])
        for event in change_events
    }

    assert repaired["candidates_created"] == 1
    assert database.count("candidates") == 1
    assert len(change_events) == 2
    assert len(change_ids) == 2
    assert _event_count(database, "chief_of_staff.candidate_routed") == 1


def test_unchanged_replay_finds_identity_beyond_recent_500_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    _standing_order(database)
    cycle_id = "v13-runtime-deep-history-repair"
    _start_source_cycle(database, cycle_id)
    original = ChiefOfStaffService.consider
    calls = {"count": 0}

    def fail_once(self, value, *, now=None, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("fault before deep-history Chief receipt")
        return original(self, value, now=now, **kwargs)

    monkeypatch.setattr(ChiefOfStaffService, "consider", fail_once)
    with pytest.raises(
        RuntimeError,
        match="fault before deep-history Chief receipt",
    ):
        run_source_observation_cycle(
            database,
            cycle_id,
            [_chief_source_spec()],
            readers={"project": _chief_source_reader},
            now=NOW,
        )

    assert database.count("world_observations") == 1
    assert _event_count(database, "chief_of_staff.") == 0

    store = WorldObservationStore(database)
    base = datetime(2026, 9, 4, 12, 1, tzinfo=timezone.utc)
    for index in range(501):
        source_identity = f"project:filler:{index:03d}"
        payload = {"summary": f"Unrelated filler observation {index}"}
        store.record(
            "project",
            f"evidence:v13-filler-{index:03d}",
            payload,
            observation_id=f"v13-filler-{index:03d}",
            observed_at=(
                base + timedelta(seconds=index)
            ).isoformat().replace("+00:00", "Z"),
            provenance={
                "authority": "REPOSITORY_AUTHORITY",
                "content_hash": content_hash(payload),
                "semantic_content_hash": content_hash(payload),
                "source_identity": source_identity,
            },
            source_id="v13-deep-history-fixture",
            idempotency_key=f"world.observation_recorded:v13-filler-{index:03d}",
        )

    repaired = run_source_observation_cycle(
        database,
        cycle_id,
        [_chief_source_spec()],
        readers={"project": _chief_source_reader},
        now=NOW,
    )

    assert repaired["sources"][0]["chief_of_staff_repairs"] == 1
    assert repaired["sources"][0]["raw_candidates"] == 1
    assert repaired["candidates_created"] == 1
    assert database.count("world_observations") == 502
    assert database.count("candidates") == 1
    assert _event_count(database, "chief_of_staff.change_observed") == 1
    assert _event_count(database, "chief_of_staff.candidate_routed") == 1
