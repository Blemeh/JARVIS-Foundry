"""V7B-10: reversible transaction plans and irreversible gates."""
from __future__ import annotations

import pytest

from jarvis_foundry.operations.planning import (
    Checkpoint,
    EffectIntent,
    PlanningError,
    ReversibleTransactionPlanner,
    RollbackOption,
    TransactionPlan,
)


def _plan() -> TransactionPlan:
    return TransactionPlan(
        operation_id="operation:office",
        mission_id="mission:office",
        target="lab:office",
        before_state={"identity": "lab:office:v1", "hash": "before"},
        intended_effects=(
            EffectIntent("effect:stage", "stage", "lab:slot", True, "unstage"),
            EffectIntent("effect:send", "send", "fake:message", False),
        ),
        irreversible_boundary=1,
        rollback_options=(RollbackOption("effect:stage", "remove staged artifact"),),
        checkpoints=(Checkpoint("readback", {"ready": True}),),
        evidence_requirements=("before-state", "provider-receipt"),
        abort_conditions=("stop", "target-changed"),
    )


def test_plan_requires_before_state_and_represents_irreversible_boundary() -> None:
    plan = _plan()
    assert plan.irreversible
    assert len(plan.plan_hash) == 64
    result = ReversibleTransactionPlanner.checkpoint(
        plan, "readback", {"ready": True}, evidence_refs=("evidence:readback",)
    )
    assert ReversibleTransactionPlanner.can_cross_irreversible_boundary(
        plan, checkpoint_results=(result,)
    )
    assert not ReversibleTransactionPlanner.can_cross_irreversible_boundary(
        plan, checkpoint_results=(result,), stop_requested=True
    )


def test_checkpoint_oracle_and_abort_signals_fail_closed() -> None:
    plan = _plan()
    with pytest.raises(PlanningError, match="oracle"):
        ReversibleTransactionPlanner.checkpoint(
            plan, "readback", {"ready": False}, evidence_refs=("evidence:x",)
        )
    assert ReversibleTransactionPlanner.abort_reasons(
        plan, {"stop": True, "target-changed": False}
    ) == ("stop",)
    with pytest.raises(PlanningError, match="evidence"):
        ReversibleTransactionPlanner.checkpoint(plan, "readback", {"ready": True}, evidence_refs=())


def test_irreversible_boundary_must_not_be_mislabeled() -> None:
    with pytest.raises(PlanningError, match="irreversible"):
        TransactionPlan(
            operation_id="operation:bad", mission_id="mission:bad", target="lab:bad",
            before_state={"identity": "before"},
            intended_effects=(EffectIntent("effect:1", "write", "lab:target", True),),
            irreversible_boundary=0, rollback_options=(), checkpoints=(),
            evidence_requirements=(), abort_conditions=(),
        )
