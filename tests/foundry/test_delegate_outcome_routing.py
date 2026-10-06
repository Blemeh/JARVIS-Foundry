"""Outcome-shaped DELEGATE directives must reach execution without re-prompting.

Covers the live Hoowen edge: a goal-intended outcome recorded with DELEGATE
semantics parked on a false METHOD_GOAL_MISMATCH (three shared function
words: only/owner/when), latched ASKING forever.  The repair dissolves only
the vacuous method interrogation, then admits the outcome as an owner
objective under the same directive so plan/portfolio/mission proceed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delegation import DelegationDecision
from jarvis_foundry.delegation.contracts import DelegationIntentStatus, DelegationMode
from jarvis_foundry.delegation.inference import _method_assessment
from jarvis_foundry.delegation.context import DelegationContext
from jarvis_foundry.delegation.service import DelegationService, reconcile_owner_delegations
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    ADMITTED_AS_MISROUTED_OUTCOME,
    _active_owner_goal_directive_ids,
    rebuild_executive_state,
    sync_owner_goals,
)
from jarvis_foundry.executive.standing_intent import is_discovery_mandate
from jarvis_foundry.owner_controls import OwnerControlService, sign_command

SECRET = b"delegate-routing-test-secret-0123456789abcdef"
SPEND_CONSTRAINT = (
    "No additional spend: paid provider usage beyond included "
    "subscription is not authorized."
)
PROVIDER_REQUEST = "Use a different paid provider for inference"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "delegate-routing.db")
    database.initialize()
    return database


def _controls(db: FoundryDatabase) -> OwnerControlService:
    return OwnerControlService(
        db, secret=SECRET, stop_path=db.path.parent / "STOP", owner_id="test-owner",
    )


def _apply(service: OwnerControlService, command: str, text: str, nonce: str, **kwargs) -> str:
    metadata = kwargs.pop("metadata", None)
    applied = service.apply(
        command, target="GLOBAL", exact_text=text, nonce=nonce,
        signature=sign_command(SECRET, command, "GLOBAL", text, nonce, metadata=metadata),
        metadata=metadata, **kwargs,
    )
    return applied.directive_id


def _clarification_count(db: FoundryDatabase) -> int:
    with db.connect(readonly=True) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = ?",
            ("delegation.clarification_requested",),
        ).fetchone()[0]


def _context(active_constraints: tuple[str, ...]) -> DelegationContext:
    return DelegationContext(
        generated_at="2026-10-06T00:00:00Z",
        resolution_status="UNKNOWN",
        selected_context_id=None,
        candidates=(),
        active_constraints=active_constraints,
    )


def test_function_word_overlap_is_not_a_method_conflict() -> None:
    from jarvis_foundry.delegation.inference import ConsequenceClass

    goal = (
        "Turn the demo workspace into a credible product and ask me only "
        "when there is a genuine owner decision or blocker."
    )
    constraint = (
        "Owner reaffirmation of the existing provider policy: authorize only "
        "included-subscription usage for the existing scope. No additional "
        "billable charges, alternate provider, or paid fallback is authorized. "
        "Fail closed when this exact route is unavailable."
    )
    assessment = _method_assessment(
        goal, context=_context((constraint,)), metadata={},
        consequence=ConsequenceClass.LOW,
    )
    assert assessment is None


def test_regulated_overlap_still_conflicts() -> None:
    from jarvis_foundry.delegation.inference import ConsequenceClass

    assessment = _method_assessment(
        PROVIDER_REQUEST,
        context=_context((SPEND_CONSTRAINT,)),
        metadata={},
        consequence=ConsequenceClass.LOW,
    )
    assert assessment is not None
    assert assessment.disposition == "ASK_OWNER"


def _infer(db: FoundryDatabase, text: str, constraints: tuple[str, ...] = ()):
    from jarvis_foundry.delegation import compile_delegation_context, infer_delegation_intent

    from dataclasses import replace

    context = compile_delegation_context(db, text)
    if constraints:
        context = replace(context, active_constraints=constraints)
    return infer_delegation_intent(
        {
            "directive_id": "owner-delegation-test",
            "exact_text": text,
            "authenticated": True,
            "owner_id": "local-owner",
            "source_identity": "owner:local-owner",
            "metadata": {"mode": "HANDLE", "request_id": "req-test"},
        },
        context,
    )


def test_prohibited_production_is_not_requested_consequence(db: FoundryDatabase) -> None:
    intent = _infer(
        db,
        "Inspect the demo product. Do not expose confidential information "
        "or modify production systems. Report current status.",
    )
    assert intent.decision != DelegationDecision.ASK
    assert intent.ambiguities == ()
    assert all(a.code != "CONSEQUENTIAL_EFFECT_BOUNDARY" for a in intent.ambiguities)


def test_unnegated_production_still_asks(db: FoundryDatabase) -> None:
    intent = _infer(db, "Delete the cache. Ship the build to production.")
    assert intent.decision == DelegationDecision.ASK
    assert any(a.code == "CONSEQUENTIAL_EFFECT_BOUNDARY" for a in intent.ambiguities)


def test_hoowen_shape_infers_clean_with_provider_constraint(db: FoundryDatabase) -> None:
    constraint = (
        "Owner reaffirmation of the existing provider policy: authorize only "
        "included-subscription usage for the existing scope. No additional "
        "billable charges, alternate provider, or paid fallback is authorized. "
        "Fail closed when this exact route is unavailable."
    )
    text = (
        "Turn the demo workspace into a credible workforce product that I could "
        "confidently demonstrate to a real operations team. Inspect the current "
        "product and decide what the highest-value work is. Research when "
        "necessary, choose the work yourself, implement and independently verify "
        "it, and continue to the next highest-value improvement. Do not expose "
        "confidential information or modify production systems. Ask me only when "
        "there is a genuine owner decision or blocker."
    )
    intent = _infer(db, text, constraints=(constraint,))
    assert intent.decision != DelegationDecision.ASK
    assert intent.ambiguities == ()
    assert intent.mode == DelegationMode.HANDLE


def test_hoowen_text_is_not_a_discovery_mandate() -> None:
    assert is_discovery_mandate("Keep improving my active projects while I'm away") is True
    assert is_discovery_mandate(
        "Turn Hoowen into a commercially credible workforce-management product. "
        "Inspect the current product and decide what the highest-value work is."
    ) is False


def test_dissolve_releases_world_change_without_reprompt(db: FoundryDatabase) -> None:
    service = _controls(db)
    constraint_id = _apply(service, "CONSTRAINT", SPEND_CONSTRAINT, "wd-c1")
    delegate_id = _apply(
        service, "DELEGATE", PROVIDER_REQUEST, "wd-d1",
        metadata={"mode": "HANDLE", "request_id": "req-dissolve-1"},
    )
    engine = DelegationService(db)
    first = engine.process_directive(delegate_id)
    assert first.status == "ASKING"
    assert _clarification_count(db) == 1
    service.revoke(
        constraint_id, nonce="wd-r1",
        signature=sign_command(SECRET, "REVOKE", constraint_id, "revoke owner directive", "wd-r1"),
    )
    second = engine.process_directive(delegate_id)
    assert second.status != "ASKING"
    assert _clarification_count(db) == 1
    assert second.status == "WAITING_FOR_EXECUTIVE_PLAN"


def _delegate_outcome(db: FoundryDatabase, text: str, nonce: str, mode: str = "HANDLE") -> str:
    return _apply(
        _controls(db), "DELEGATE", text, nonce,
        metadata={"mode": mode, "request_id": f"req-{nonce}"},
    )


OUTCOME_TEXT = (
    "Turn the demo workspace into a credible workforce product: inspect the "
    "current build, choose the highest-value improvement, implement it with "
    "tests, and report coverage of scheduling, dispatch, and analytics."
)


def test_outcome_delegate_admits_objective_under_same_directive(db: FoundryDatabase) -> None:
    delegate_id = _delegate_outcome(db, OUTCOME_TEXT, "wd-o1")
    reconcile_owner_delegations(db)
    created = sync_owner_goals(db)
    assert [o.objective_id for o in created] == [f"owner-goal:{delegate_id}"]
    objective = rebuild_executive_state(db).objectives[f"owner-goal:{delegate_id}"]
    assert objective.provenance.get("directive_id") == delegate_id
    assert objective.provenance.get("admitted_as") == ADMITTED_AS_MISROUTED_OUTCOME
    assert delegate_id in _active_owner_goal_directive_ids(db)
    before = objective.version
    assert sync_owner_goals(db) == []
    assert rebuild_executive_state(db).objectives[f"owner-goal:{delegate_id}"].version == before


def test_vague_advisory_and_mandate_delegates_stay_out(db: FoundryDatabase) -> None:
    _delegate_outcome(db, "Handle this now", "wd-v1")
    _delegate_outcome(
        db, "What requires my attention across the active projects?", "wd-v2", mode="TELL_ME",
    )
    _delegate_outcome(db, "Keep improving my active projects while I'm away", "wd-v3")
    reconcile_owner_delegations(db)
    assert sync_owner_goals(db) == []
    assert rebuild_executive_state(db).objectives == {}


def test_revoked_outcome_delegate_withdraws_objective(db: FoundryDatabase) -> None:
    service = _controls(db)
    delegate_id = _delegate_outcome(db, OUTCOME_TEXT, "wd-w1")
    reconcile_owner_delegations(db)
    sync_owner_goals(db)
    oid = f"owner-goal:{delegate_id}"
    assert rebuild_executive_state(db).objectives[oid].status == "ACTIVE"
    service.revoke(
        delegate_id, nonce="wd-w2",
        signature=sign_command(SECRET, "REVOKE", delegate_id, "revoke owner directive", "wd-w2"),
    )
    sync_owner_goals(db)
    assert rebuild_executive_state(db).objectives[oid].status == "SUSPENDED"


def test_hierarchy_synthesizes_goal_for_admitted_delegate(db: FoundryDatabase) -> None:
    from jarvis_foundry.command_center_work import project_work_hierarchy

    delegate_id = _delegate_outcome(db, OUTCOME_TEXT, "wd-h1")
    reconcile_owner_delegations(db)
    sync_owner_goals(db)
    state = rebuild_executive_state(db)
    objective = state.objectives[f"owner-goal:{delegate_id}"]
    result = project_work_hierarchy(
        autonomy={"objectives": {objective.objective_id: {
            "objective_id": objective.objective_id,
            "objective": objective.objective,
            "status": objective.status,
            "mission_ids": [],
            "plan_ids": [],
            "provenance": dict(objective.provenance),
            "metadata": dict(objective.metadata),
        }}, "plans": {}, "progress": {}},
        goals=[],
        missions=[],
        workers=[],
        evidence=[],
        verdicts=[],
        mission_steps=[],
    )
    assert result["goals"] and result["goals"][0]["directive_id"] == delegate_id
    assert result["objectives"][0]["goal_id"] == f"goal:{delegate_id}"
