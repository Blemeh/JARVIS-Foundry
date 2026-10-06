from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delegation import (
    ConsequenceClass,
    DelegationDecision,
    DelegationIntent,
    DelegationIntentStatus,
    DelegationMode,
    MaterialAmbiguity,
    MethodAssessment,
    ReferentCandidate,
    Reversibility,
    project_delegation_intents,
    stable_intent_id,
)
from jarvis_foundry.events import Event


def _intent() -> DelegationIntent:
    text = "Handle the failed release check and verify the recovery."
    return DelegationIntent(
        intent_id=stable_intent_id("owner-delegate-1", text),
        owner_directive_id="owner-delegate-1",
        owner_source_identity="owner:local-owner",
        exact_owner_text=text,
        desired_outcome="Restore the failed release check to a verified passing state.",
        mode=DelegationMode.HANDLE,
        decision=DelegationDecision.ACT,
        success_criteria=(
            "The release check passes from a fresh run.",
            "The relevant regression test passes.",
        ),
        consequence_class=ConsequenceClass.LOW,
        reversibility=Reversibility.REVERSIBLE,
        confidence=0.91,
        created_at="2026-09-03T10:00:00Z",
        updated_at="2026-09-03T10:00:00Z",
        status=DelegationIntentStatus.READY,
        resolved_referents=(
            ReferentCandidate(
                context_id="failure:release-check",
                kind="UNRESOLVED_ITEM",
                text="Release verification failed",
                confidence=0.94,
                evidence=("recent_context_rank:0", "kind_cue:repair"),
                source_refs=("event:verification.failed:44",),
            ),
        ),
        explicit_constraints=("Do not promote or deploy.",),
        context_refs=("failure:release-check",),
        evidence_refs=("event:verification.failed:44",),
        method_assessment=MethodAssessment(
            requested_method="Skip the verifier.",
            owner_goal="Finish the release repair truthfully",
            recommended_method=(
                "Repair the failing condition and preserve verifier evidence."
            ),
            reason="Skipping verification would undermine the owner goal.",
            disposition="RECOMMEND_ALTERNATIVE",
            evidence=("verifier_required",),
        ),
        rationale_labels=(
            "REFERENT_RESOLVED",
            "LOW_CONSEQUENCE",
            "REVERSIBLE",
        ),
        project_id="JARVIS-Foundry",
    )


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_contract_round_trips_with_stable_identity() -> None:
    intent = _intent()

    rebuilt = DelegationIntent.from_mapping(intent.to_payload())

    assert rebuilt == intent
    assert stable_intent_id(
        intent.owner_directive_id,
        intent.exact_owner_text,
    ) == intent.intent_id
    assert stable_intent_id(
        intent.owner_directive_id,
        intent.exact_owner_text,
        request_identity="request-2",
    ) != intent.intent_id


def test_overlong_options_clip_instead_of_breaking_projection() -> None:
    from jarvis_foundry.delegation.contracts import MaterialAmbiguity

    long_option = "Choose an approach that preserves the constraint: " + "x" * 900
    ambiguity = MaterialAmbiguity.from_mapping({
        "ambiguity_id": "ambiguity:legacy:method",
        "code": "METHOD_GOAL_MISMATCH",
        "question": "Should I use the recommended approach instead?",
        "options": [long_option, "Keep the requested method"],
        "evidence": ["active_constraint:policy"],
    })
    assert len(ambiguity.options) == 2
    assert all(len(option) <= 512 for option in ambiguity.options)
    assert ambiguity.options[1] == "Keep the requested method"


def test_projection_is_idempotent_and_restart_durable(tmp_path: Path) -> None:
    database = _database(tmp_path)
    intent = _intent()
    event = Event.create(
        "delegation.intent_recorded",
        {"intent_id": intent.intent_id, "intent": intent.to_payload()},
        source_type="supervisor",
        source_id="delegation-engine",
        idempotency_key=f"delegation.intent_recorded:{intent.intent_id}",
        occurred_at=intent.created_at,
    )

    assert database.append(event)[1] is True
    replay = Event.create(
        "delegation.intent_recorded",
        {"intent_id": intent.intent_id, "intent": intent.to_payload()},
        source_type="supervisor",
        source_id="delegation-engine",
        idempotency_key=f"delegation.intent_recorded:{intent.intent_id}",
        occurred_at=intent.created_at,
    )
    assert database.append(replay)[1] is False

    restarted = FoundryDatabase(database.path)
    projection = project_delegation_intents(restarted)
    assert projection.warnings == ()
    assert projection.get(intent.intent_id) == intent
    assert restarted.event_count(event_type="delegation.intent_recorded") == 1


def test_projection_tracks_admission_and_supersession(tmp_path: Path) -> None:
    database = _database(tmp_path)
    intent = _intent()
    database.append(Event.create(
        "delegation.intent_recorded",
        {"intent_id": intent.intent_id, "intent": intent.to_payload()},
        source_type="supervisor",
        source_id="delegation-engine",
        idempotency_key=f"delegation.intent_recorded:{intent.intent_id}",
    ))
    database.append(Event.create(
        "delegation.intent_admitted",
        {
            "intent_id": intent.intent_id,
            "objective_id": "delegation-objective:one",
            "plan_id": "delegation-plan:one",
            "step_ids": ["executive-step:one"],
            "updated_at": "2026-09-03T10:01:00Z",
        },
        source_type="supervisor",
        source_id="delegation-engine",
        idempotency_key=f"delegation.intent_admitted:{intent.intent_id}",
    ))

    admitted = project_delegation_intents(database).get(intent.intent_id)
    assert admitted is not None
    assert admitted.status == DelegationIntentStatus.ADMITTED
    assert admitted.resulting_objective_id == "delegation-objective:one"
    assert admitted.resulting_plan_id == "delegation-plan:one"
    assert admitted.resulting_step_ids == ("executive-step:one",)

    database.append(Event.create(
        "delegation.intent_superseded",
        {
            "intent_id": intent.intent_id,
            "superseded_by_intent_id": "delegation-intent:new",
            "updated_at": "2026-09-03T10:02:00Z",
        },
        source_type="owner",
        source_id="local-owner",
        idempotency_key=f"delegation.intent_superseded:{intent.intent_id}",
    ))
    superseded = project_delegation_intents(database).get(intent.intent_id)
    assert superseded is not None
    assert superseded.status == DelegationIntentStatus.SUPERSEDED
    assert superseded.superseded_by_intent_id == "delegation-intent:new"


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("delegation.intent_completed", DelegationIntentStatus.COMPLETED),
        ("delegation.intent_failed", DelegationIntentStatus.FAILED),
        ("delegation.intent_cancelled", DelegationIntentStatus.CANCELLED),
    ],
)
def test_projection_preserves_executive_terminal_history(
    tmp_path: Path,
    event_type: str,
    status: DelegationIntentStatus,
) -> None:
    database = _database(tmp_path)
    intent = replace(
        _intent(),
        status=DelegationIntentStatus.ADMITTED,
        resulting_objective_id="delegation-objective:terminal",
        resulting_plan_id="delegation-plan:terminal",
        resulting_step_ids=("executive-step:terminal",),
    )
    database.append(Event.create(
        "delegation.intent_recorded",
        {"intent_id": intent.intent_id, "intent": intent.to_payload()},
        source_type="supervisor",
        source_id="delegation-engine",
        idempotency_key=f"delegation.intent_recorded:{intent.intent_id}",
    ))
    database.append(Event.create(
        event_type,
        {
            "intent_id": intent.intent_id,
            "objective_id": intent.resulting_objective_id,
            "plan_id": intent.resulting_plan_id,
            "step_ids": list(intent.resulting_step_ids),
            "reason_code": f"EXECUTIVE_{status.value}",
            "updated_at": "2026-09-04T10:01:00Z",
        },
        source_type="supervisor",
        source_id="delegation-engine",
        idempotency_key=f"{event_type}:{intent.intent_id}",
    ))
    database.append(Event.create(
        "delegation.intent_updated",
        {
            "intent_id": intent.intent_id,
            "intent": intent.to_payload(),
            "reason": "stale reconciler update",
        },
        source_type="supervisor",
        source_id="stale-reconciler",
        idempotency_key=(
            f"delegation.intent_updated:{intent.intent_id}:late"
        ),
    ))
    database.append(Event.create(
        "delegation.intent_admitted",
        {
            "intent_id": intent.intent_id,
            "objective_id": intent.resulting_objective_id,
            "plan_id": intent.resulting_plan_id,
            "mission_ids": [],
            "updated_at": "2026-09-04T10:02:00Z",
        },
        source_type="supervisor",
        source_id="stale-reconciler",
        idempotency_key=(
            f"delegation.intent_admitted:{intent.intent_id}:late"
        ),
    ))
    database.append(Event.create(
        "delegation.intent_blocked",
        {
            "intent_id": intent.intent_id,
            "reason_code": "LATE_STALE_CONTROL",
            "reason": "stale reconciler result",
            "objective_id": intent.resulting_objective_id,
            "plan_id": intent.resulting_plan_id,
            "mission_ids": [],
            "updated_at": "2026-09-04T10:03:00Z",
        },
        source_type="supervisor",
        source_id="stale-reconciler",
        idempotency_key=(
            f"delegation.intent_blocked:{intent.intent_id}:late"
        ),
    ))
    database.append(Event.create(
        "delegation.intent_superseded",
        {
            "intent_id": intent.intent_id,
            "superseded_by_intent_id": "delegation-intent:late",
            "updated_at": "2026-09-04T10:04:00Z",
        },
        source_type="owner",
        source_id="local-owner",
        idempotency_key=(
            f"delegation.intent_superseded:{intent.intent_id}:late"
        ),
    ))

    terminal = project_delegation_intents(database).get(intent.intent_id)

    assert terminal is not None
    assert terminal.status == status
    assert terminal.superseded_by_intent_id is None
    assert f"EXECUTIVE_{status.value}" in terminal.rationale_labels


def test_contract_rejects_unbounded_or_incoherent_values() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        replace(_intent(), confidence=1.1)
    with pytest.raises(ValueError, match="clarification_question"):
        replace(
            _intent(),
            status=DelegationIntentStatus.AWAITING_CLARIFICATION,
            decision=DelegationDecision.ASK,
            clarification_question=None,
        )
    with pytest.raises(ValueError, match="resolved_referents"):
        replace(
            _intent(),
            resolved_referents=tuple(
                ReferentCandidate(
                    context_id=f"context-{index}",
                    kind="PROJECT",
                    text="Project",
                    confidence=0.5,
                )
                for index in range(9)
            ),
        )
    ambiguity = MaterialAmbiguity(
        ambiguity_id="ambiguity:target",
        code="TARGET",
        question="Which repository should I change?",
        options=("repo-a", "repo-b"),
    )
    assert MaterialAmbiguity.from_mapping(ambiguity.to_payload()) == ambiguity
