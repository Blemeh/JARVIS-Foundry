from __future__ import annotations

from pathlib import Path

from jarvis_foundry.chief_of_staff import (
    ChiefOfStaffService,
    project_chief_of_staff,
)
from jarvis_foundry.briefing_items import project_briefing_items
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
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
    assert waiting.wait_id in wakes
    assert replay.wait_id == waiting.wait_id
    assert database.event_count() == before_replay
    assert len(wakes) == 1
    assert wakes[waiting.wait_id]["kind"] == "WAIT_FOR_DEPENDENCY"

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

    assert triggered == [waiting.wait_id]
    assert rebuild_wake_state(database)[waiting.wait_id]["status"] == "TRIGGERED"
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
