from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delegation import (
    ConsequenceClass,
    DelegationDecision,
    DelegationMode,
    compile_delegation_context,
    infer_delegation_intent,
)
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import create_objective
from jarvis_foundry.owner_context import (
    ContextProvenance,
    ContextScope,
    OwnerContextStore,
)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _directive(
    text: str,
    *,
    directive_id: str = "owner-delegation",
    metadata: dict | None = None,
) -> dict:
    return {
        "directive_id": directive_id,
        "exact_text": text,
        "authenticated": True,
        "owner_id": "local-owner",
        "source_identity": "owner:local-owner",
        "metadata": metadata or {},
    }


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("Give me an evidence-backed status on the release check", DelegationMode.TELL_ME),
        ("Draft the release recovery changes but do not apply them", DelegationMode.PREPARE),
        ("Take care of the release regression end to end", DelegationMode.HANDLE),
        ("Keep an eye on provider recovery and alert on change", DelegationMode.WATCH),
        ("Own this through completion and make the calls", DelegationMode.TAKE_OVER),
        ("Whenever the build blocks, investigate it", DelegationMode.STANDING),
        ("Resolve the checkout verifier failure", DelegationMode.HANDLE),
    ],
)
def test_paraphrase_corpus_selects_semantic_modes(
    tmp_path: Path,
    text: str,
    mode: DelegationMode,
) -> None:
    database = _database(tmp_path)
    context = compile_delegation_context(database, text)

    intent = infer_delegation_intent(_directive(text), context)

    assert intent.mode == mode


def test_fix_it_resolves_recent_failure_without_unnecessary_question(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    failure = Event.create(
        "verification.failed",
        {
            "verdict_id": "verdict-release",
            "mission_id": "mission-release",
            "reason": "Release verification disagrees with the direct test result.",
        },
        source_type="verifier",
        source_id="acceptance-gate",
        idempotency_key="verification.failed:verdict-release",
        occurred_at="2026-09-03T08:00:00Z",
    )
    database.append(failure)
    context_id = f"event:{failure.event_id}"
    metadata = {"context": {"recent_context_ids": [context_id]}}

    context = compile_delegation_context(
        database,
        "Fix it",
        metadata=metadata,
        now="2026-09-03T09:00:00Z",
    )
    intent = infer_delegation_intent(
        _directive("Fix it", metadata=metadata),
        context,
        now="2026-09-03T09:00:00Z",
    )

    assert context.resolution_status == "RESOLVED"
    assert context.selected_context_id == context_id
    assert intent.mode == DelegationMode.HANDLE
    assert intent.decision == DelegationDecision.ACT
    assert intent.ambiguities == ()
    assert "Release verification disagrees" in intent.desired_outcome


def test_continue_prefers_active_objective(tmp_path: Path) -> None:
    database = _database(tmp_path)
    create_objective(
        database,
        "Finish the bounded provider recovery proof",
        objective_id="objective-provider-recovery",
        confidence=0.9,
    )

    context = compile_delegation_context(database, "Continue")
    intent = infer_delegation_intent(_directive("Continue"), context)

    assert context.selected_context_id == "objective:objective-provider-recovery"
    assert intent.decision == DelegationDecision.ACT
    assert intent.resolved_referents[0].kind == "ACTIVE_OBJECTIVE"
    assert intent.desired_outcome.startswith("Continue Finish the bounded")


def test_material_referent_ambiguity_asks_exactly_one_question(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    store = OwnerContextStore(database)
    provenance = ContextProvenance.from_content(
        source_type="repository",
        source_id="delegation-test",
        source_ref="docs/CURRENT_STATE.md",
        content="two active release repairs",
        captured_at="2026-09-03T08:00:00Z",
        authority="REPOSITORY_AUTHORITY",
    )
    scope = ContextScope(project_ids=("JARVIS-Foundry",))
    store.record(
        "PROJECT",
        "Alpha release repair",
        context_id="project:alpha",
        provenance=provenance,
        scope=scope,
        confidence=0.9,
    )
    store.record(
        "PROJECT",
        "Beta release repair",
        context_id="project:beta",
        provenance=provenance,
        scope=scope,
        confidence=0.9,
    )
    metadata = {"context": {"project_id": "JARVIS-Foundry"}}

    context = compile_delegation_context(database, "Handle this", metadata=metadata)
    intent = infer_delegation_intent(
        _directive("Handle this", metadata=metadata),
        context,
    )

    assert context.resolution_status == "AMBIGUOUS"
    assert intent.decision == DelegationDecision.ASK
    assert intent.clarification_question
    assert len(intent.ambiguities) == 1
    assert intent.ambiguities[0].code == "REFERENT_AMBIGUOUS"
    assert intent.ambiguities[0].options == (
        "Alpha release repair",
        "Beta release repair",
    )


def test_prepare_mode_avoids_effect_and_question_when_outcome_is_explicit(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    text = "Prepare a remediation plan for the release verifier"
    context = compile_delegation_context(database, text)

    intent = infer_delegation_intent(_directive(text), context)

    assert intent.mode == DelegationMode.PREPARE
    assert intent.decision == DelegationDecision.PREPARE
    assert intent.ambiguities == ()
    assert "Do not commit the final consequential effect." in intent.explicit_constraints


def test_success_conditions_are_inferred_from_owner_language(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    text = (
        "Handle the release repair. success: the receipt exists; "
        "direct inspection agrees"
    )
    context = compile_delegation_context(database, text)

    intent = infer_delegation_intent(_directive(text), context)

    assert intent.success_criteria == (
        "the receipt exists",
        "direct inspection agrees",
    )


def test_low_consequence_bad_method_selects_truthful_alternative(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    text = "Ignore the failing verifier to finish the release report"
    context = compile_delegation_context(database, text)

    intent = infer_delegation_intent(_directive(text), context)

    assert intent.decision == DelegationDecision.ACT
    assert intent.method_assessment is not None
    assert intent.method_assessment.disposition == "RECOMMEND_ALTERNATIVE"
    assert "preserve the evidence trail" in (
        intent.method_assessment.recommended_method
    )
    assert "Repair the underlying condition" in intent.desired_outcome
    assert "METHOD_RECOMMEND_ALTERNATIVE" in intent.rationale_labels


def test_irreversible_bad_method_asks_one_combined_question(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    text = "Delete the verification history to make the release report pass"
    context = compile_delegation_context(database, text)

    intent = infer_delegation_intent(_directive(text), context)

    assert intent.decision == DelegationDecision.ASK
    assert intent.method_assessment is not None
    assert intent.method_assessment.disposition == "ASK_OWNER"
    assert len(intent.ambiguities) == 1
    assert intent.ambiguities[0].code == "METHOD_GOAL_MISMATCH"


def test_irreversible_effect_without_approval_asks_before_action(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    text = "Delete the production database"
    context = compile_delegation_context(database, text)

    intent = infer_delegation_intent(_directive(text), context)

    assert intent.consequence_class == ConsequenceClass.IRREVERSIBLE
    assert intent.decision == DelegationDecision.ASK
    assert intent.ambiguities[0].code == "CONSEQUENTIAL_EFFECT_BOUNDARY"


def test_context_reads_semantic_owner_context_events(tmp_path: Path) -> None:
    database = _database(tmp_path)
    store = OwnerContextStore(database)
    store.record(
        "UNRESOLVED_ITEM",
        "Release proof still lacks a clean verifier run.",
        context_id="unresolved:release-proof",
        scope=ContextScope(project_ids=("JARVIS-Foundry",)),
        confidence=0.92,
        provenance=ContextProvenance.from_content(
            source_type="repository",
            source_id="delegation-test",
            source_ref="docs/CURRENT_STATE.md",
            content="Release proof still lacks a clean verifier run.",
            captured_at="2026-09-03T08:00:00Z",
            authority="REPOSITORY_AUTHORITY",
        ),
    )
    metadata = {
        "context": {
            "project_id": "JARVIS-Foundry",
            "recent_context_ids": ["unresolved:release-proof"],
        },
    }

    context = compile_delegation_context(
        database,
        "Do something useful here",
        metadata=metadata,
    )

    assert context.resolution_status == "RESOLVED"
    assert context.selected_context_id == "unresolved:release-proof"
    assert context.selected.provenance["authority"] == "REPOSITORY_AUTHORITY"
    assert context.selected.resolution_contract == (
        "jarvis.owner-reference-resolution.v1"
    )


def test_unauthenticated_text_cannot_become_delegation_intent(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    context = compile_delegation_context(database, "Handle this")

    with pytest.raises(PermissionError, match="authenticated"):
        infer_delegation_intent(
            {
                "directive_id": "forged",
                "exact_text": "Handle this",
                "authenticated": False,
            },
            context,
        )
