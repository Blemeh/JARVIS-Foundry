"""V7B-2: durable resumable broad-operation procedure contract."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.operations import (
    OperationError,
    OperationStep,
    ProcedurePlan,
    complete_step,
    compensate_step,
    create_procedure,
    fail_step,
    load_procedure,
    reconcile_step,
    reopen_step,
    resume_procedure,
    start_step,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _step(step_id: str, *, target: str = "system:synthetic-office") -> OperationStep:
    return OperationStep(
        step_id=step_id,
        action_kind="synthetic.update",
        target=target,
        preconditions=("target identity is exact",),
        capabilities=("synthetic.write",),
        risk_class="R1",
        reversibility="reversible",
        postconditions=("record is readable",),
        rollback={"method": "restore-before-state"},
        effect_ticket=f"ticket:{step_id}",
        evidence_refs=("evidence:plan",),
    )


def _plan(*steps: OperationStep) -> ProcedurePlan:
    return ProcedurePlan(
        procedure_id="procedure-office-1",
        mission_id="mission-office-1",
        target="system:synthetic-office",
        steps=tuple(steps),
        preconditions=("synthetic service is available",),
        capabilities=("synthetic.write",),
        evidence_refs=("evidence:plan",),
        metadata={"environment": "disposable"},
    )


def _start(db: FoundryDatabase, procedure_id: str, step_id: str):
    return start_step(
        db, procedure_id, step_id,
        before_state={"identity": f"before:{step_id}", "hash": "before-hash"},
        evidence_refs=(f"evidence:before:{step_id}",),
    )


def _complete(db: FoundryDatabase, procedure_id: str, step_id: str):
    return complete_step(
        db, procedure_id, step_id,
        after_state={"identity": f"after:{step_id}", "hash": "after-hash"},
        evidence_refs=(f"evidence:after:{step_id}",),
        postcondition_results={"record is readable": True},
    )


def test_create_is_idempotent_and_plan_is_hash_bound(db: FoundryDatabase) -> None:
    plan = _plan(_step("inspect"))
    first = create_procedure(db, plan)
    second = create_procedure(db, plan)
    assert first.plan.plan_hash == plan.plan_hash
    assert second.history_sequences == first.history_sequences
    with db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='operation.procedure.created'"
        ).fetchone()[0] == 1
    changed = _plan(replace(_step("inspect"), target="system:other"))
    with pytest.raises(OperationError, match="different plan"):
        create_procedure(db, changed)


def test_steps_are_ordered_and_require_before_and_postcondition_evidence(
    db: FoundryDatabase,
) -> None:
    plan = _plan(_step("inspect"), _step("write"))
    create_procedure(db, plan)
    with pytest.raises(OperationError, match="before_state"):
        start_step(db, plan.procedure_id, "inspect", before_state={}, evidence_refs=("e",))
    with pytest.raises(OperationError, match="in order"):
        start_step(
            db, plan.procedure_id, "write",
            before_state={"identity": "x"}, evidence_refs=("e",),
        )
    _start(db, plan.procedure_id, "inspect")
    with pytest.raises(OperationError, match="postcondition"):
        complete_step(
            db, plan.procedure_id, "inspect",
            after_state={"hash": "after"}, evidence_refs=("e",),
        )


def test_full_procedure_completes_atomically_and_replays_after_restart(
    db: FoundryDatabase,
) -> None:
    plan = _plan(_step("inspect"), _step("write"))
    create_procedure(db, plan)
    _start(db, plan.procedure_id, "inspect")
    state = _complete(db, plan.procedure_id, "inspect")
    assert state.status == "READY"
    assert state.next_step is not None and state.next_step.step_id == "write"
    _start(db, plan.procedure_id, "write")
    done = _complete(db, plan.procedure_id, "write")
    assert done.status == "COMPLETED"
    assert done.terminal_outcome == "COMPLETED"
    reopened = load_procedure(db, plan.procedure_id)
    assert reopened.status == "COMPLETED"
    assert reopened.step("inspect").status.value == "COMPLETED"
    assert reopened.step("write").status.value == "COMPLETED"
    with db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='operation.procedure.terminal'"
        ).fetchone()[0] == 1


def test_restart_freezes_started_step_and_reconcile_prevents_blind_retry(
    db: FoundryDatabase,
) -> None:
    plan = _plan(_step("write"))
    create_procedure(db, plan)
    _start(db, plan.procedure_id, "write")
    frozen = resume_procedure(db, plan.procedure_id)
    assert frozen.status == "FROZEN"
    assert frozen.step("write").status.value == "FROZEN"
    assert resume_procedure(db, plan.procedure_id).history_sequences == frozen.history_sequences
    with pytest.raises(OperationError, match="not STARTED"):
        complete_step(
            db, plan.procedure_id, "write",
            after_state={"hash": "late"}, evidence_refs=("evidence:late",),
            postcondition_results={"record is readable": True},
        )
    reconciled = reconcile_step(
        db, plan.procedure_id, "write", outcome="COMPLETED",
        after_state={"identity": "provider:write", "hash": "provider-hash"},
        evidence_refs=("evidence:provider-readback",),
        provider_reference="fake-provider:receipt-1",
    )
    assert reconciled.status == "COMPLETED"
    assert reconciled.terminal_outcome == "COMPLETED"


def test_failed_step_can_be_compensated_reopened_and_retried(db: FoundryDatabase) -> None:
    plan = _plan(_step("write"))
    create_procedure(db, plan)
    _start(db, plan.procedure_id, "write")
    failed = fail_step(
        db, plan.procedure_id, "write", reason="fixture outage",
        evidence_refs=("evidence:failure",), after_state={"hash": "partial"},
    )
    assert failed.status == "FAILED"
    compensated = compensate_step(
        db, plan.procedure_id, "write", compensation_ref="fake:restore-1",
        evidence_refs=("evidence:compensation",),
    )
    assert compensated.step("write").status.value == "COMPENSATED"
    reopened = reopen_step(
        db, plan.procedure_id, "write", reason="retry after repair",
        evidence_refs=("evidence:repair",),
    )
    assert reopened.status == "READY"
    retried = _start(db, plan.procedure_id, "write")
    assert retried.step("write").attempt == 2
    assert _complete(db, plan.procedure_id, "write").terminal_outcome == "COMPLETED"


@pytest.mark.parametrize("target", ["", "system:*", "${OWNER_TARGET}", "%TARGET%"])
def test_ambiguous_target_identity_is_rejected(target: str) -> None:
    with pytest.raises(OperationError):
        _plan(_step("inspect", target=target))


def test_high_risk_or_irreversible_step_requires_recovery() -> None:
    with pytest.raises(OperationError, match="rollback or compensation"):
        OperationStep(
            step_id="danger",
            action_kind="synthetic.delete",
            target="system:synthetic-office",
            risk_class="C3",
            reversibility="irreversible",
            effect_ticket="ticket:danger",
        )
