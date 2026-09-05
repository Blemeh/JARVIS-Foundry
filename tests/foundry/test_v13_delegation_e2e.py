from __future__ import annotations

from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delegation import DelegationService, project_delegation_intents
from jarvis_foundry.executive.autonomy import (
    PlanStep,
    build_plan,
    create_objective,
    rebuild_executive_state,
    set_objective_status,
)
from jarvis_foundry.owner_controls import OwnerControlService, sign_command


_SECRET = b"v13-delegation-e2e-secret-32-bytes"


def test_minimal_owner_instruction_binds_and_mirrors_verified_completion(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    objective = create_objective(
        database,
        "Repair the failed release verification",
        objective_id="executive-objective:release-repair",
        success_criteria=("Fresh independent verification passes.",),
        source_id="test-executive-planner",
    )
    plan = build_plan(
        database,
        objective.objective_id,
        (
            PlanStep(
                step_id="executive-step:repair-release",
                objective_id=objective.objective_id,
                description="Repair and independently verify the release check",
                verification_contract=(
                    "Fresh independent verification passes.",
                ),
            ),
        ),
        plan_id="executive-plan:release-repair",
        source_id="test-executive-planner",
    )
    exact_text = "Fix it."
    metadata = {
        "context": {
            "objective_id": objective.objective_id,
            "plan_id": plan.plan_id,
            "step_id": "executive-step:repair-release",
        },
    }
    nonce = "nonce:delegation-e2e"
    directive = OwnerControlService(
        database,
        secret=_SECRET,
        owner_id="delegation-e2e-owner",
    ).apply(
        "DELEGATE",
        target=objective.objective_id,
        exact_text=exact_text,
        nonce=nonce,
        signature=sign_command(
            _SECRET,
            "DELEGATE",
            objective.objective_id,
            exact_text,
            nonce,
            metadata=metadata,
        ),
        directive_id="delegate:e2e-release-repair",
        metadata=metadata,
    )

    admitted = DelegationService(database).process_directive(
        directive.directive_id,
    )
    set_objective_status(
        database,
        objective.objective_id,
        "COMPLETED",
        evidence_refs=("evidence:fresh-independent-verifier",),
    )
    completed = DelegationService(
        FoundryDatabase(database.path),
    ).process_directive(directive.directive_id)
    replay = DelegationService(
        FoundryDatabase(database.path),
    ).process_directive(directive.directive_id)

    intent = project_delegation_intents(database).get(admitted.intent_id)
    state = rebuild_executive_state(database)
    assert admitted.status == "ADMITTED"
    assert completed.status == replay.status == "COMPLETED"
    assert replay.replayed is True
    assert intent is not None
    assert intent.exact_owner_text == exact_text
    assert intent.resulting_objective_id == objective.objective_id
    assert intent.resulting_plan_id == plan.plan_id
    assert intent.resulting_step_ids == ("executive-step:repair-release",)
    assert state.objectives[objective.objective_id].status == "COMPLETED"
    assert database.event_count(event_type="executive.objective_created") == 1
    assert database.event_count(event_type="executive.plan_created") == 1
    assert database.event_count(event_type="delegation.intent_recorded") == 1
    assert database.event_count(event_type="delegation.intent_admitted") == 1
    assert database.event_count(event_type="delegation.intent_completed") == 1
