from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import jarvis_foundry.delegation.service as delegation_service
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delegation import (
    DelegationIntentStatus,
    DelegationService,
    project_delegation_intents,
)
from jarvis_foundry.executive.autonomy import (
    PlanStep,
    build_plan,
    create_objective,
    generate_missions_for_objective,
    rebuild_executive_state,
    set_objective_status,
)
from jarvis_foundry.executive.continuous import rebuild_wake_state
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.supervisor.estop import EmergencyStop


_SECRET = b"v13-delegation-owner-secret-32-bytes"


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _apply(
    database: FoundryDatabase,
    command_type: str,
    text: str,
    *,
    directive_id: str,
    target: str | None = "GLOBAL",
    metadata: dict | None = None,
    supersedes: str | None = None,
) -> str:
    values = dict(metadata or {})
    nonce = f"nonce:{directive_id}"
    result = OwnerControlService(
        database,
        secret=_SECRET,
        owner_id="delegation-test-owner",
    ).apply(
        command_type,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=sign_command(
            _SECRET,
            command_type,
            target,
            text,
            nonce,
            supersedes=supersedes,
            metadata=values,
        ),
        directive_id=directive_id,
        supersedes_directive_id=supersedes,
        metadata=values,
    )
    return result.directive_id


def _revoke(
    database: FoundryDatabase,
    directive_id: str,
    *,
    token: str,
) -> None:
    text = f"revoke {directive_id}"
    nonce = f"nonce:revoke:{token}"
    OwnerControlService(
        database,
        secret=_SECRET,
        owner_id="delegation-test-owner",
    ).apply(
        "REVOKE",
        target=directive_id,
        exact_text=text,
        nonce=nonce,
        signature=sign_command(
            _SECRET,
            "REVOKE",
            directive_id,
            text,
            nonce,
        ),
    )


def _seed_plan(
    database: FoundryDatabase,
    key: str,
    *,
    objective_id: str | None = None,
    plan_id: str | None = None,
    steps: int = 1,
) -> tuple[str, str, tuple[str, ...]]:
    objective_id = objective_id or f"executive-objective:{key}"
    plan_id = plan_id or f"executive-plan:{key}"
    step_ids = tuple(
        f"executive-step:{key}:{index}"
        for index in range(steps)
    )
    state = rebuild_executive_state(database)
    if objective_id not in state.objectives:
        create_objective(
            database,
            f"Executive objective {key}",
            objective_id=objective_id,
            success_criteria=("Independent verification accepts the outcome.",),
            source_id="test-executive-planner",
        )
    build_plan(
        database,
        objective_id,
        tuple(
            PlanStep(
                step_id=value,
                objective_id=objective_id,
                description=f"Executive-authored step {index}",
                output_contract=("Produce the bounded result.",),
                verification_contract=("Verify the bounded result.",),
            )
            for index, value in enumerate(step_ids)
        ),
        plan_id=plan_id,
        source_id="test-executive-planner",
    )
    return objective_id, plan_id, step_ids


def _binding_metadata(
    objective_id: str,
    plan_id: str,
    step_id: str,
    **extra: object,
) -> dict:
    return {
        **extra,
        "context": {
            "objective_id": objective_id,
            "plan_id": plan_id,
            "step_id": step_id,
        },
    }


def test_delegation_has_no_planner_import_or_plan_event_writer() -> None:
    source = inspect.getsource(delegation_service)

    assert "PlanStep" not in source
    assert "build_plan(" not in source
    assert "create_objective(" not in source
    assert 'Event.create(\n                "executive.plan_updated"' not in source


def test_delegation_binds_existing_executive_plan_without_plan_events(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(database, "existing")
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle the Executive-authored release step",
        directive_id="delegate-existing-plan",
        metadata=_binding_metadata(objective_id, plan_id, step_ids[0]),
    )
    before = {
        event_type: database.event_count(event_type=event_type)
        for event_type in (
            "executive.objective_created",
            "executive.objective_updated",
            "executive.plan_created",
            "executive.plan_updated",
        )
    }

    outcome = DelegationService(database).process_directive(directive_id)

    assert outcome.status == "ADMITTED"
    assert outcome.objective_id == objective_id
    assert outcome.plan_id == plan_id
    assert outcome.step_ids == (step_ids[0],)
    assert all(
        database.event_count(event_type=event_type) == count
        for event_type, count in before.items()
    )
    intent = project_delegation_intents(database).get(outcome.intent_id)
    assert intent is not None
    assert intent.resulting_step_ids == (step_ids[0],)


@pytest.mark.parametrize(
    ("seed_objective", "expected_reason"),
    [
        (False, "EXECUTIVE_OBJECTIVE_REQUIRED"),
        (True, "EXECUTIVE_PLAN_REQUIRED"),
    ],
)
def test_delegation_waits_when_executive_state_is_incomplete(
    tmp_path: Path,
    seed_objective: bool,
    expected_reason: str,
) -> None:
    database = _database(tmp_path)
    objective_id = "executive-objective:await-plan"
    if seed_objective:
        create_objective(
            database,
            "Release objective awaiting Executive planning",
            objective_id=objective_id,
            source_id="test-executive-planner",
        )
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle the release objective",
        directive_id=f"delegate-await-plan:{seed_objective}",
        metadata={"context": {"objective_id": objective_id}},
    )

    waiting = DelegationService(database).process_directive(directive_id)

    assert waiting.status == "WAITING_FOR_EXECUTIVE_PLAN"
    intent = project_delegation_intents(database).get(waiting.intent_id)
    assert intent is not None
    assert intent.status == DelegationIntentStatus.BLOCKED
    assert expected_reason in intent.rationale_labels
    assert database.event_count(event_type="executive.plan_created") == 0


def test_waiting_directive_replays_after_executive_authors_plan(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id = "executive-objective:await-replay"
    create_objective(
        database,
        "Await plan",
        objective_id=objective_id,
        source_id="test-executive-planner",
    )
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle it",
        directive_id="delegate-await-replay",
        metadata={"context": {"objective_id": objective_id}},
    )
    first = DelegationService(database).process_directive(directive_id)
    _, plan_id, step_ids = _seed_plan(
        database,
        "await-replay",
        objective_id=objective_id,
    )

    replay = DelegationService(database).process_directive(directive_id)

    assert first.status == "WAITING_FOR_EXECUTIVE_PLAN"
    assert replay.status == "ADMITTED"
    assert replay.replayed is True
    assert replay.plan_id == plan_id
    assert replay.step_ids == (step_ids[0],)
    assert database.event_count(event_type="delegation.intent_recorded") == 1
    assert database.event_count(event_type="delegation.intent_admitted") == 1


def test_multiple_executive_steps_require_explicit_binding(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(
        database,
        "multiple-steps",
        steps=2,
    )
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle one step",
        directive_id="delegate-multiple-steps",
        metadata={"context": {
            "objective_id": objective_id,
            "plan_id": plan_id,
        }},
    )

    waiting = DelegationService(database).process_directive(directive_id)

    assert waiting.status == "WAITING_FOR_EXECUTIVE_PLAN"
    intent = project_delegation_intents(database).get(waiting.intent_id)
    assert intent is not None
    assert "EXECUTIVE_STEP_REQUIRED" in intent.rationale_labels
    assert set(step_ids) == {
        step.step_id
        for step in rebuild_executive_state(database).plans[plan_id].steps
    }


def test_duplicate_processing_has_one_durable_admission(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(database, "duplicate")
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle once",
        directive_id="delegate-duplicate",
        metadata=_binding_metadata(objective_id, plan_id, step_ids[0]),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(
            lambda _: DelegationService(
                FoundryDatabase(database.path),
            ).process_directive(directive_id),
            range(2),
        ))

    assert {outcome.status for outcome in outcomes} == {"ADMITTED"}
    assert database.event_count(event_type="delegation.intent_recorded") == 1
    assert database.event_count(event_type="delegation.intent_admitted") == 1


@pytest.mark.parametrize("control_type", ["VETO_ACTION", "STOP_EVERYTHING"])
def test_owner_control_blocks_without_mutating_executive_plan(
    tmp_path: Path,
    control_type: str,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(database, control_type)
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle under owner control",
        directive_id=f"delegate-control:{control_type}",
        metadata=_binding_metadata(objective_id, plan_id, step_ids[0]),
    )
    before_plan = rebuild_executive_state(database).plans[plan_id]
    if control_type == "VETO_ACTION":
        control_id = _apply(
            database,
            control_type,
            "Block delegation",
            directive_id="control:veto",
            target="delegation",
        )
    else:
        control_id = _apply(
            database,
            control_type,
            "Stop delegation",
            directive_id="control:stop",
        )

    blocked = DelegationService(database).process_directive(directive_id)

    assert blocked.status == "BLOCKED"
    assert rebuild_executive_state(database).plans[plan_id] == before_plan
    _revoke(database, control_id, token=control_type)
    if control_type == "STOP_EVERYTHING":
        EmergencyStop(database.path.parent / "EMERGENCY_STOP").clear()
    resumed = DelegationService(database).process_directive(directive_id)
    assert resumed.status == "ADMITTED"
    assert resumed.replayed is True


def test_stop_raised_at_atomic_boundary_prevents_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(database, "atomic-stop")
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle atomically",
        directive_id="delegate-atomic-stop",
        metadata=_binding_metadata(objective_id, plan_id, step_ids[0]),
    )
    service = DelegationService(database)
    original = service._commit_admission
    stop = EmergencyStop(database.path.parent / "EMERGENCY_STOP")

    def commit_after_stop(*args: object, **kwargs: object):
        stop.activate("atomic test stop")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_commit_admission", commit_after_stop)
    outcome = service.process_directive(directive_id)

    assert outcome.status == "BLOCKED"
    assert database.event_count(event_type="delegation.intent_admitted") == 0
    assert rebuild_executive_state(database).plans[plan_id].status == "ACTIVE"


def test_revocation_at_atomic_boundary_prevents_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(database, "atomic-revoke")
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle atomically",
        directive_id="delegate-atomic-revoke",
        metadata=_binding_metadata(objective_id, plan_id, step_ids[0]),
    )
    service = DelegationService(database)
    original = service._commit_admission

    def commit_after_revoke(*args: object, **kwargs: object):
        _revoke(database, directive_id, token="atomic")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_commit_admission", commit_after_revoke)
    outcome = service.process_directive(directive_id)

    assert outcome.status == "BLOCKED"
    assert database.event_count(event_type="delegation.intent_admitted") == 0
    assert rebuild_executive_state(database).plans[plan_id].status == "ACTIVE"


def test_watch_uses_existing_objective_and_continuous_wake(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(database, "watch")
    directive_id = _apply(
        database,
        "DELEGATE",
        "Watch this",
        directive_id="delegate-watch",
        metadata=_binding_metadata(
            objective_id,
            plan_id,
            step_ids[0],
            mode="WATCH",
            watch_signal="release-proof-changed",
        ),
    )

    outcome = DelegationService(database).process_directive(directive_id)

    assert outcome.status == "WATCHING"
    assert outcome.wake_id
    wake = rebuild_wake_state(database)[outcome.wake_id]
    assert wake["kind"] == "WAIT_FOR_SIGNAL"
    assert wake["signal"] == "release-proof-changed"
    assert rebuild_executive_state(database).objectives[
        objective_id
    ].status == "ACTIVE"


def test_invalid_metadata_fails_closed_without_executive_mutation(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle invalid metadata",
        directive_id="delegate-invalid",
        metadata={"budget": {"max_retries": float("inf")}},
    )

    outcome = DelegationService(database).process_directive(directive_id)

    assert outcome.status == "BLOCKED"
    assert "metadata rejected" in outcome.reason
    assert database.event_count(event_type="executive.objective_created") == 0
    assert database.event_count(event_type="executive.plan_created") == 0


def test_redirect_waits_for_executive_replan_then_binds_replacement(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id, original_plan_id, original_steps = _seed_plan(
        database,
        "redirect-original",
    )
    original_directive = _apply(
        database,
        "DELEGATE",
        "Handle the original plan",
        directive_id="delegate-redirect-original",
        metadata=_binding_metadata(
            objective_id,
            original_plan_id,
            original_steps[0],
        ),
    )
    service = DelegationService(database)
    original = service.process_directive(original_directive)
    redirect_id = _apply(
        database,
        "REDIRECT",
        "Use the evidence-first replacement",
        directive_id="delegate-redirect-replacement",
        target=objective_id,
        supersedes=original_directive,
        metadata={"context": {"objective_id": objective_id}},
    )

    waiting = next(
        outcome
        for outcome in service.reconcile()
        if outcome.directive_id == redirect_id
    )
    assert waiting.status == "WAITING_FOR_EXECUTIVE_PLAN"
    assert waiting.plan_id == original.plan_id

    _, replacement_plan_id, replacement_steps = _seed_plan(
        database,
        "redirect-replacement",
        objective_id=objective_id,
    )
    admitted = next(
        outcome
        for outcome in service.reconcile()
        if outcome.directive_id == redirect_id
    )

    assert admitted.status == "ADMITTED"
    assert admitted.objective_id == objective_id
    assert admitted.plan_id == replacement_plan_id
    assert admitted.step_ids == (replacement_steps[0],)
    state = rebuild_executive_state(database)
    assert state.plans[original_plan_id].status == "ACTIVE"
    assert state.plans[replacement_plan_id].status == "ACTIVE"


def test_revoked_delegation_leaves_executive_state_untouched(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(database, "revoke")
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle until revoked",
        directive_id="delegate-revoke",
        metadata=_binding_metadata(objective_id, plan_id, step_ids[0]),
    )
    service = DelegationService(database)
    service.process_directive(directive_id)
    before = rebuild_executive_state(database)
    _revoke(database, directive_id, token="ordinary")

    service.reconcile()

    after = rebuild_executive_state(database)
    assert after.objectives[objective_id] == before.objectives[objective_id]
    assert after.plans[plan_id] == before.plans[plan_id]
    intent = project_delegation_intents(database).intents[0]
    assert intent.status == DelegationIntentStatus.BLOCKED
    assert "OWNER_AUTHORITY_WITHDRAWN" in intent.rationale_labels


@pytest.mark.parametrize(
    ("objective_status", "delegation_status"),
    [
        ("COMPLETED", DelegationIntentStatus.COMPLETED),
        ("FAILED", DelegationIntentStatus.FAILED),
        ("CANCELLED", DelegationIntentStatus.CANCELLED),
    ],
)
def test_delegation_mirrors_terminal_executive_truth(
    tmp_path: Path,
    objective_status: str,
    delegation_status: DelegationIntentStatus,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(
        database,
        f"terminal-{objective_status}",
    )
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle terminal truth",
        directive_id=f"delegate-terminal:{objective_status}",
        metadata=_binding_metadata(objective_id, plan_id, step_ids[0]),
    )
    service = DelegationService(database)
    admitted = service.process_directive(directive_id)
    set_objective_status(
        database,
        objective_id,
        objective_status,
        evidence_refs=(f"evidence:{objective_status.lower()}",),
    )

    terminal = service.process_directive(directive_id)

    assert admitted.status == "ADMITTED"
    assert terminal.status == objective_status
    intent = project_delegation_intents(database).get(admitted.intent_id)
    assert intent is not None
    assert intent.status == delegation_status
    assert intent.resulting_step_ids == (step_ids[0],)


def test_execution_generation_uses_only_executive_authored_step(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id, plan_id, step_ids = _seed_plan(database, "route")
    directive_id = _apply(
        database,
        "DELEGATE",
        "Route the existing step",
        directive_id="delegate-route",
        metadata=_binding_metadata(objective_id, plan_id, step_ids[0]),
    )
    DelegationService(database).process_directive(directive_id)

    requests = generate_missions_for_objective(database, objective_id)

    assert len(requests) == 1
    assert requests[0]["step_id"] == step_ids[0]
    assert database.event_count(event_type="executive.plan_created") == 1

def test_reevaluating_a_waiting_directive_writes_one_block_event(tmp_path: Path) -> None:
    """A stalled intent must not append an identical block every cycle.

    Live evidence: one waiting delegation wrote 3,301 ``delegation.intent_blocked``
    events (a fresh timestamp in the key made each one unique) while the owner was
    asked for clarification once.
    """
    database = _database(tmp_path)
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle the release objective",
        directive_id="delegate-await-dedupe",
        metadata={"context": {"objective_id": "executive-objective:missing"}},
    )
    service = DelegationService(database)
    outcomes = [service.process_directive(directive_id) for _ in range(5)]

    assert {outcome.status for outcome in outcomes} == {"WAITING_FOR_EXECUTIVE_PLAN"}
    assert database.event_count(event_type="delegation.intent_blocked") == 1


def test_a_changed_block_reason_is_a_new_transition(tmp_path: Path) -> None:
    database = _database(tmp_path)
    objective_id = "executive-objective:await-reason-change"
    directive_id = _apply(
        database,
        "DELEGATE",
        "Handle the release objective",
        directive_id="delegate-await-reason-change",
        metadata={"context": {"objective_id": objective_id}},
    )
    service = DelegationService(database)
    service.process_directive(directive_id)
    service.process_directive(directive_id)
    assert database.event_count(event_type="delegation.intent_blocked") == 1

    # The Executive now has the objective but no plan yet: a different reason.
    create_objective(
        database,
        "Release objective awaiting Executive planning",
        objective_id=objective_id,
        source_id="test-executive-planner",
    )
    waiting = service.process_directive(directive_id)
    service.process_directive(directive_id)

    assert waiting.status == "WAITING_FOR_EXECUTIVE_PLAN"
    reasons = [
        event.payload["reason_code"]
        for event in database.events(event_types=("delegation.intent_blocked",))
    ]
    assert reasons == ["EXECUTIVE_OBJECTIVE_REQUIRED", "EXECUTIVE_PLAN_REQUIRED"]