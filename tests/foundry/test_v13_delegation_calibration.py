from __future__ import annotations

from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delegation import (
    ConsequenceClass,
    DelegationDecision,
    DelegationIntent,
    DelegationIntentStatus,
    DelegationMode,
    Reversibility,
    build_delegation_calibration,
    compile_delegation_context,
    infer_delegation_intent,
    stable_intent_id,
)
from jarvis_foundry.events import Event


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _intent(
    key: str,
    *,
    status: DelegationIntentStatus = DelegationIntentStatus.READY,
    decision: DelegationDecision = DelegationDecision.ACT,
) -> DelegationIntent:
    text = f"Handle bounded repair {key}"
    objective_id = (
        f"objective:{key}"
        if status in {
            DelegationIntentStatus.ADMITTED,
            DelegationIntentStatus.COMPLETED,
        }
        else None
    )
    plan_id = f"plan:{key}" if objective_id else None
    step_ids = (f"step:{key}",) if objective_id else ()
    return DelegationIntent(
        intent_id=stable_intent_id(f"directive:{key}", text),
        owner_directive_id=f"directive:{key}",
        owner_source_identity="owner:test",
        exact_owner_text=text,
        desired_outcome=f"Repair {key}",
        mode=DelegationMode.HANDLE,
        decision=decision,
        success_criteria=("Independent verification passes.",),
        consequence_class=ConsequenceClass.LOW,
        reversibility=Reversibility.REVERSIBLE,
        confidence=0.8,
        created_at="2026-09-04T08:00:00Z",
        updated_at="2026-09-04T08:00:00Z",
        status=status,
        resulting_objective_id=objective_id,
        resulting_plan_id=plan_id,
        resulting_step_ids=step_ids,
        rationale_labels=("REFERENT_RESOLVED",),
        clarification_question=(
            "Which repair?"
            if status == DelegationIntentStatus.AWAITING_CLARIFICATION
            else None
        ),
    )


def _record(database: FoundryDatabase, intent: DelegationIntent) -> None:
    database.append(Event.create(
        "delegation.intent_recorded",
        {"intent_id": intent.intent_id, "intent": intent.to_payload()},
        source_type="supervisor",
        source_id="delegation-test",
        idempotency_key=f"delegation.intent_recorded:{intent.intent_id}",
        occurred_at=intent.created_at,
    ))


def test_calibration_measures_questions_corrections_and_outcomes(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    completed = _intent(
        "completed",
        status=DelegationIntentStatus.ADMITTED,
    )
    corrected = _intent("corrected")
    asked = _intent(
        "asked",
        status=DelegationIntentStatus.AWAITING_CLARIFICATION,
        decision=DelegationDecision.ASK,
    )
    for intent in (completed, corrected, asked):
        _record(database, intent)
    database.append_events([
        Event.create(
            "delegation.intent_completed",
            {
                "intent_id": completed.intent_id,
                "objective_id": completed.resulting_objective_id,
                "plan_id": completed.resulting_plan_id,
                "step_ids": list(completed.resulting_step_ids),
                "mission_ids": ["mission:completed"],
                "updated_at": "2026-09-04T08:01:00Z",
            },
            source_type="supervisor",
            source_id="delegation-test",
            idempotency_key=(
                f"delegation.intent_completed:{completed.intent_id}"
            ),
        ),
        Event.create(
            "delegation.intent_superseded",
            {
                "intent_id": corrected.intent_id,
                "superseded_by_intent_id": "intent:new",
                "updated_at": "2026-09-04T08:02:00Z",
            },
            source_type="owner",
            source_id="owner:test",
            idempotency_key=(
                f"delegation.intent_superseded:{corrected.intent_id}"
            ),
        ),
        Event.create(
            "delegation.clarification_requested",
            {
                "intent_id": asked.intent_id,
                "directive_id": asked.owner_directive_id,
                "question": asked.clarification_question,
            },
            source_type="supervisor",
            source_id="delegation-test",
            idempotency_key=(
                f"delegation.clarification_requested:{asked.intent_id}:0"
            ),
        ),
        Event.create(
            "delegation.clarification_applied",
            {
                "intent_id": "intent:clarified",
                "prior_intent_id": asked.intent_id,
                "directive_id": "directive:clarified",
                "revision": 1,
            },
            source_type="supervisor",
            source_id="delegation-test",
            idempotency_key="delegation.clarification_applied:intent:clarified",
        ),
        Event.create(
            "owner_control.duplicate_received",
            {
                "directive_id": completed.owner_directive_id,
                "request_id": "request:completed",
            },
            source_type="owner",
            source_id="owner:test",
            idempotency_key="owner_control.duplicate_received:test",
        ),
    ])

    calibration = build_delegation_calibration(
        database,
        now="2026-09-04T09:00:00Z",
    )

    assert calibration["metrics"]["verified_final_outcomes"] == 1
    assert calibration["metrics"]["owner_corrections"] == 1
    assert calibration["metrics"]["questions_asked"] == 1
    assert calibration["metrics"]["useful_questions"] == 1
    assert calibration["metrics"]["duplicate_deliveries"] == 1
    assert calibration["metrics"]["duplicate_intents"] == 0
    assert calibration["metrics"]["duplicate_objectives"] == 0
    assert calibration["mode_adjustments"]["HANDLE"] < 0
    assert calibration["policy"]["opaque_training"] is False


def test_prior_outcomes_apply_bounded_future_mode_calibration(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    completed = _intent(
        "completed",
        status=DelegationIntentStatus.ADMITTED,
    )
    corrected = _intent("corrected")
    _record(database, completed)
    _record(database, corrected)
    database.append(Event.create(
        "delegation.intent_completed",
        {
            "intent_id": completed.intent_id,
            "objective_id": completed.resulting_objective_id,
            "updated_at": "2026-09-04T08:01:00Z",
        },
        source_type="supervisor",
        source_id="delegation-test",
        idempotency_key=f"delegation.intent_completed:{completed.intent_id}",
    ))
    database.append(Event.create(
        "delegation.intent_superseded",
        {
            "intent_id": corrected.intent_id,
            "superseded_by_intent_id": "intent:new",
            "updated_at": "2026-09-04T08:02:00Z",
        },
        source_type="owner",
        source_id="owner:test",
        idempotency_key=(
            f"delegation.intent_superseded:{corrected.intent_id}"
        ),
    ))
    text = "Take care of the next bounded repair"
    context = compile_delegation_context(
        database,
        text,
        now="2026-09-04T09:00:00Z",
    )

    intent = infer_delegation_intent(
        {
            "directive_id": "directive:future",
            "exact_text": text,
            "authenticated": True,
            "owner_id": "test",
            "metadata": {},
        },
        context,
        now="2026-09-04T09:00:00Z",
    )

    assert context.calibration["mode_adjustments"]["HANDLE"] < 0
    assert intent.mode == DelegationMode.HANDLE
    assert "CALIBRATION_APPLIED" in intent.rationale_labels
