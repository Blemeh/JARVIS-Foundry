"""Shared helper: record an ACCEPTED step verdict through the production API.

The acceptance seam is `record_verification_acceptance`
(mission/verification.py).  Its receipt is what `accepted_verifications`
reads, and `next_executable_contract` only advances past a step when the
step's verdict_id appears in that set.

Both halves are written through the production functions
(`record_verification_started` -> `record_verification_outcome` ->
`record_verification_acceptance`).  A test that hand-rolls an event of the
right TYPE but the wrong payload shape projects nothing into
`verification_verdicts`, so `latest_verdict_for_step` returns None and the
step silently never advances — a vacuous green.  This helper exists to make
that impossible.
"""

from __future__ import annotations

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.mission.verification import (
    SUBJECT_STEP,
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)


def accept_step(
    database: FoundryDatabase,
    *,
    mission_id: str,
    step_id: str,
    verdict_id: str | None = None,
    builder_run_id: str = "run-builder",
    verifier_run_id: str = "run-verifier",
) -> str:
    """Record a PASS verdict for `step_id` plus its AcceptanceGate receipt.

    Returns the verdict_id.
    """
    verdict_id = verdict_id or f"verdict-{step_id}"
    start_verification(
        database,
        verdict_id=verdict_id,
        mission_id=mission_id,
        step_id=step_id,
        builder_run_id=builder_run_id,
        verifier_run_id=verifier_run_id,
        independence={"provider_diverse": True},
        criteria=["step verified"],
    )
    record_verification_outcome(
        database,
        verdict_id=verdict_id,
        outcome="PASS",
        findings=[],
        evidence={"check": "re-derived by the runtime"},
    )
    record_verification_acceptance(
        database,
        verdict_id=verdict_id,
        mission_id=mission_id,
        step_id=step_id,
        builder_run_id=builder_run_id,
        verifier_run_id=verifier_run_id,
        checks={"accepted": True},
    )
    assert SUBJECT_STEP == "mission_step"
    return verdict_id
