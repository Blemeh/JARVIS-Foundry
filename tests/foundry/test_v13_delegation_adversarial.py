from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delegation import (
    DelegationDecision,
    DelegationMode,
    DelegationService,
    compile_delegation_context,
    infer_delegation_intent,
    project_delegation_intents,
)
from jarvis_foundry.executive.autonomy import (
    PlanStep,
    build_plan,
    create_objective,
    rebuild_executive_state,
)
from jarvis_foundry.executive.continuous import rebuild_wake_state
from jarvis_foundry.owner_context import (
    ContextProvenance,
    ContextScope,
    OwnerContextStore,
)
from jarvis_foundry.owner_controls import (
    OwnerControlService,
    sign_command,
)


_SECRET = b"v13-adversarial-owner-secret-32b"
_NOW = "2026-09-04T12:00:00Z"
_PROJECT = "JARVIS-Foundry"


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _seed_plan(
    database: FoundryDatabase,
    key: str,
) -> tuple[str, str, str]:
    objective_id = f"executive-objective:{key}"
    plan_id = f"executive-plan:{key}"
    step_id = f"executive-step:{key}"
    create_objective(
        database,
        f"Executive objective {key}",
        objective_id=objective_id,
        source_id="test-executive-planner",
    )
    build_plan(
        database,
        objective_id,
        (
            PlanStep(
                step_id=step_id,
                objective_id=objective_id,
                description=f"Executive step {key}",
            ),
        ),
        plan_id=plan_id,
        source_id="test-executive-planner",
    )
    return objective_id, plan_id, step_id


def _with_binding(
    metadata: dict,
    objective_id: str,
    plan_id: str,
    step_id: str,
) -> dict:
    values = dict(metadata)
    context = dict(values.get("context") or {})
    context.update({
        "objective_id": objective_id,
        "plan_id": plan_id,
        "step_id": step_id,
    })
    values["context"] = context
    return values


def _directive(
    text: str,
    *,
    directive_id: str,
    metadata: dict | None = None,
) -> dict:
    return {
        "directive_id": directive_id,
        "exact_text": text,
        "authenticated": True,
        "owner_id": "v13-adversarial-owner",
        "source_identity": "owner:v13-adversarial-owner",
        "metadata": metadata or {},
    }


def _apply(
    database: FoundryDatabase,
    command_type: str,
    text: str,
    *,
    directive_id: str,
    target: str | None = "GLOBAL",
    metadata: dict | None = None,
) -> str:
    values = metadata or {}
    nonce = f"nonce:{directive_id}"
    result = OwnerControlService(
        database,
        secret=_SECRET,
        owner_id="v13-adversarial-owner",
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
            metadata=values,
        ),
        directive_id=directive_id,
        metadata=values,
    )
    return result.directive_id


def _revoke(
    database: FoundryDatabase,
    directive_id: str,
) -> None:
    text = f"revoke {directive_id}"
    nonce = f"nonce:revoke:{directive_id}"
    OwnerControlService(
        database,
        secret=_SECRET,
        owner_id="v13-adversarial-owner",
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


def _record_context(
    database: FoundryDatabase,
    context_id: str,
    text: str,
    *,
    captured_at: str = "2026-09-04T08:00:00Z",
    fresh_until: str | None = None,
    attributes: dict | None = None,
    supersedes: str | None = None,
    contradicts: tuple[str, ...] = (),
) -> str:
    OwnerContextStore(database).record(
        "UNRESOLVED_ITEM",
        text,
        context_id=context_id,
        scope=ContextScope(project_ids=(_PROJECT,)),
        confidence=0.95,
        provenance=ContextProvenance.from_content(
            source_type="repository",
            source_id="v13-adversarial-suite",
            source_ref=f"fixture/{context_id}",
            content=text,
            captured_at=captured_at,
            authority="REPOSITORY_AUTHORITY",
        ),
        observed_at=captured_at,
        effective_at=captured_at,
        fresh_until=fresh_until,
        attributes=attributes,
        supersedes=supersedes,
        contradicts=contradicts,
    )
    return context_id


def _context_metadata(context_id: str) -> dict:
    return {
        "context": {
            "project_id": _PROJECT,
            "recent_context_ids": [context_id],
        },
    }


@pytest.mark.parametrize(
    ("text", "mode", "decision"),
    [
        ("Fix it.", DelegationMode.HANDLE, DelegationDecision.ACT),
        ("Prepare this.", DelegationMode.PREPARE, DelegationDecision.PREPARE),
        ("Watch this.", DelegationMode.WATCH, DelegationDecision.WATCH),
        ("Take over.", DelegationMode.TAKE_OVER, DelegationDecision.ACT),
        (
            "Do something useful here.",
            DelegationMode.HANDLE,
            DelegationDecision.ACT,
        ),
    ],
)
def test_required_short_phrases_resolve_strong_current_context(
    tmp_path: Path,
    text: str,
    mode: DelegationMode,
    decision: DelegationDecision,
) -> None:
    database = _database(tmp_path)
    context_id = _record_context(
        database,
        "unresolved:release-verifier",
        "Release verifier still disagrees with direct inspection.",
    )
    metadata = _context_metadata(context_id)

    context = compile_delegation_context(
        database,
        text,
        metadata=metadata,
        now=_NOW,
    )
    intent = infer_delegation_intent(
        _directive(
            text,
            directive_id=f"delegate:{mode.value.lower()}",
            metadata=metadata,
        ),
        context,
        now=_NOW,
    )

    assert context.selected_context_id == context_id
    assert intent.mode == mode
    assert intent.decision == decision
    assert intent.ambiguities == ()
    assert intent.clarification_question is None


def test_continue_exact_phrase_selects_active_objective(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective = create_objective(
        database,
        "Finish the bounded provider recovery proof",
        objective_id="objective-provider-recovery",
        confidence=0.9,
    )
    context_id = f"objective:{objective.objective_id}"
    metadata = {"context": {"recent_context_ids": [context_id]}}

    context = compile_delegation_context(
        database,
        "Continue.",
        metadata=metadata,
        now=_NOW,
    )
    intent = infer_delegation_intent(
        _directive(
            "Continue.",
            directive_id="delegate:continue",
            metadata=metadata,
        ),
        context,
        now=_NOW,
    )

    assert context.selected_context_id == context_id
    assert intent.mode == DelegationMode.HANDLE
    assert intent.decision == DelegationDecision.ACT
    assert intent.ambiguities == ()
    assert intent.desired_outcome.startswith("Continue Finish the bounded")


def test_prepare_watch_and_take_over_bind_expected_existing_authorities(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "Prepare this.",
            "delegate:prepare-authority",
            DelegationMode.PREPARE,
            "ADMITTED",
            "PREPARE_ONLY",
        ),
        (
            "Watch this.",
            "delegate:watch-authority",
            DelegationMode.WATCH,
            "WATCHING",
            "EXISTING_SCHEDULER_AND_WAKE_ONLY",
        ),
        (
            "Take over.",
            "delegate:take-over-authority",
            DelegationMode.TAKE_OVER,
            "ADMITTED",
            "INDEPENDENT_VERIFICATION_REQUIRED",
        ),
    )
    for index, (text, directive_id, mode, status, boundary) in enumerate(cases):
        database = FoundryDatabase(tmp_path / f"foundry-{index}.db")
        database.initialize()
        context_id = _record_context(
            database,
            f"unresolved:authority-{index}",
            "Release verification remains incomplete.",
        )
        metadata = _context_metadata(context_id)
        objective_id, plan_id, step_id = _seed_plan(
            database,
            f"authority-{index}",
        )
        metadata = _with_binding(
            metadata,
            objective_id,
            plan_id,
            step_id,
        )
        directive = _apply(
            database,
            "DELEGATE",
            text,
            directive_id=directive_id,
            metadata=metadata,
        )

        outcome = DelegationService(database).process_directive(directive)
        intent = project_delegation_intents(database).get(outcome.intent_id)
        objective = rebuild_executive_state(database).objectives[objective_id]

        assert outcome.status == status
        assert intent is not None
        assert intent.mode == mode
        assert outcome.objective_id == objective_id
        assert outcome.plan_id == plan_id
        assert outcome.step_ids == (step_id,)
        assert objective.autonomy_boundaries == ()
        if mode == DelegationMode.PREPARE:
            assert database.event_count(
                event_type="executive.mission_created",
            ) == 0
        if mode == DelegationMode.WATCH:
            assert outcome.wake_id
            assert objective.status == "ACTIVE"
            assert rebuild_wake_state(database)[outcome.wake_id][
                "kind"
            ] == "WAIT_FOR_SIGNAL"


def test_stale_recent_pointer_cannot_override_newer_superseding_truth(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    old_id = _record_context(
        database,
        "unresolved:release-target-old",
        "Repair the obsolete release candidate.",
        captured_at="2026-09-02T08:00:00Z",
        fresh_until="2026-09-03T00:00:00Z",
    )
    new_id = _record_context(
        database,
        "unresolved:release-target-current",
        "Repair the current release verifier.",
        captured_at="2026-09-04T08:00:00Z",
        supersedes=old_id,
    )
    metadata = _context_metadata(old_id)

    context = compile_delegation_context(
        database,
        "Fix it.",
        metadata=metadata,
        now=_NOW,
    )
    intent = infer_delegation_intent(
        _directive(
            "Fix it.",
            directive_id="delegate:stale-pointer",
            metadata=metadata,
        ),
        context,
        now=_NOW,
    )

    assert context.resolution_status == "RESOLVED"
    assert context.selected_context_id == new_id
    assert old_id not in {
        candidate.context_id for candidate in context.candidates
    }
    assert intent.resolved_referents[0].context_id == new_id
    assert "current release verifier" in intent.desired_outcome


def test_conflicting_current_truth_requires_exactly_one_question(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    first = _record_context(
        database,
        "unresolved:release-alpha",
        "Repair Alpha before release.",
        attributes={
            "fact_key": "jarvis:release:repair-target",
            "value": "Alpha",
        },
    )
    second = _record_context(
        database,
        "unresolved:release-beta",
        "Repair Beta before release.",
        attributes={
            "fact_key": "jarvis:release:repair-target",
            "value": "Beta",
        },
        contradicts=(first,),
    )
    metadata = _context_metadata(second)
    directive = _apply(
        database,
        "DELEGATE",
        "Handle this.",
        directive_id="delegate:contradictory-context",
        metadata=metadata,
    )

    first_outcome = DelegationService(database).process_directive(directive)
    replay = DelegationService(database).process_directive(directive)
    intent = project_delegation_intents(database).get(first_outcome.intent_id)

    assert first_outcome.status == replay.status == "ASKING"
    assert first_outcome.clarification_question
    assert replay.replayed is True
    assert intent is not None
    assert len(intent.ambiguities) == 1
    assert intent.ambiguities[0].code == "REFERENT_AMBIGUOUS"
    assert database.event_count(
        event_type="delegation.clarification_requested",
    ) == 1
    assert database.event_count(event_type="executive.objective_created") == 0


def test_veto_blocks_admission_then_same_intent_resumes_once(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    context_id = _record_context(
        database,
        "unresolved:veto-release",
        "Release verifier repair remains incomplete.",
    )
    directive = _apply(
        database,
        "DELEGATE",
        "Handle this.",
        directive_id="delegate:veto-release",
        metadata=_with_binding(
            _context_metadata(context_id),
            *_seed_plan(database, "veto-release"),
        ),
    )
    veto = _apply(
        database,
        "VETO_ACTION",
        "Do not admit delegated work",
        directive_id="veto:delegation-global",
    )
    service = DelegationService(database)

    blocked = service.process_directive(directive)
    assert blocked.status == "BLOCKED"
    assert database.event_count(event_type="executive.objective_created") == 1

    _revoke(database, veto)
    resumed = service.process_directive(directive)
    replay = DelegationService(database).process_directive(directive)

    assert resumed.status == replay.status == "ADMITTED"
    assert resumed.replayed is True
    assert replay.replayed is True
    assert resumed.intent_id == blocked.intent_id
    assert database.event_count(event_type="delegation.intent_recorded") == 1
    assert database.event_count(event_type="executive.objective_created") == 1
    assert database.event_count(event_type="executive.plan_created") == 1


def test_crash_after_intent_record_recovers_without_duplicate_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    context_id = _record_context(
        database,
        "unresolved:crash-release",
        "Release verifier repair remains incomplete.",
    )
    directive = _apply(
        database,
        "DELEGATE",
        "Fix it.",
        directive_id="delegate:crash-after-intent",
        metadata=_with_binding(
            _context_metadata(context_id),
            *_seed_plan(database, "crash-after-intent"),
        ),
    )
    interrupted = DelegationService(database)

    def crash_before_admission(*args, **kwargs):
        raise RuntimeError("simulated crash after durable intent")

    monkeypatch.setattr(interrupted, "_admit", crash_before_admission)
    with pytest.raises(
        RuntimeError,
        match="simulated crash after durable intent",
    ):
        interrupted.process_directive(directive)

    assert database.event_count(event_type="delegation.intent_recorded") == 1
    assert database.event_count(event_type="executive.objective_created") == 1

    recovered_database = FoundryDatabase(database.path)
    recovered = DelegationService(recovered_database).process_directive(
        directive,
    )
    replay = DelegationService(recovered_database).process_directive(
        directive,
    )

    assert recovered.status == replay.status == "ADMITTED"
    assert recovered.replayed is True
    assert replay.replayed is True
    assert recovered.intent_id == replay.intent_id
    assert recovered.objective_id == replay.objective_id
    assert recovered.plan_id == replay.plan_id
    assert recovered_database.event_count(
        event_type="delegation.intent_recorded",
    ) == 1
    assert recovered_database.event_count(
        event_type="executive.objective_created",
    ) == 1
    assert recovered_database.event_count(
        event_type="executive.plan_created",
    ) == 1
