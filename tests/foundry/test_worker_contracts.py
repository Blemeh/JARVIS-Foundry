"""Hybrid worker contracts: envelope + step contract integrity (H4)."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.workers.contracts import (
    DIRECTIVE_MODEL,
    DIRECTIVE_PROVIDER,
    VALID_WORKERS,
    WorkerStepContract,
)
from jarvis_foundry.workers.envelope import (
    COMPLETED,
    FAILED,
    NOT_AVAILABLE,
    EnvelopeValidationError,
    ResultEnvelope,
)


def contract(tmp_path: Path, **overrides) -> WorkerStepContract:
    fields = {
        "run_id": "run-1",
        "mission_id": "mission-1",
        "step_id": "step-1",
        "attempt": 1,
        "role": "builder",
        "objective": "objective",
        "instructions": "instructions",
        "working_root": tmp_path / "work",
    }
    fields.update(overrides)
    return WorkerStepContract(**fields)


def test_valid_worker_identities():
    assert VALID_WORKERS == {"hermes", "openhands"}


def test_contract_round_trip_and_stable_hash(tmp_path):
    step = contract(tmp_path, task_signals={"bounded": True})
    payload = step.to_payload()
    rebuilt = WorkerStepContract.from_payload(payload)
    assert rebuilt == step
    assert rebuilt.content_hash() == step.content_hash()


def test_envelope_from_contract_records_requested_identity(tmp_path):
    step = contract(tmp_path)
    envelope = ResultEnvelope.from_contract(step, framework="hermes")
    assert envelope.framework == "hermes"
    assert envelope.provider_requested == DIRECTIVE_PROVIDER
    assert envelope.model_requested == DIRECTIVE_MODEL
    assert envelope.provider_actual == DIRECTIVE_PROVIDER
    assert envelope.model_actual == DIRECTIVE_MODEL
    assert envelope.worker_status == COMPLETED


def test_envelope_round_trip_preserves_all_fields(tmp_path):
    step = contract(tmp_path, provider="opencode_go", model="deepseek-v4-flash")
    envelope = ResultEnvelope.from_contract(
        step, framework="openhands",
        claimed_outcome="done",
        observed_touched_paths=("a.py",),
        files_changed=("a.py",),
        usage_availability="available",
        usage_calls=3,
        usage_total_tokens=100,
        usage_cost=0.01,
        warnings=("warn",),
    )
    rebuilt = ResultEnvelope.from_payload(envelope.to_payload())
    assert rebuilt == envelope


def test_envelope_rejects_invented_usage_when_unavailable(tmp_path):
    step = contract(tmp_path)
    with pytest.raises(EnvelopeValidationError):
        ResultEnvelope.from_contract(
            step, framework="hermes",
            usage_availability=NOT_AVAILABLE,
            usage_total_tokens=1234,
        )


def test_envelope_rejects_invalid_status_and_verdict(tmp_path):
    step = contract(tmp_path)
    with pytest.raises(EnvelopeValidationError):
        ResultEnvelope.from_contract(step, framework="hermes",
                                     worker_status="MAYBE")
    with pytest.raises(EnvelopeValidationError):
        ResultEnvelope.from_contract(step, framework="hermes",
                                     verdict="PASSED")


def test_failed_envelope_round_trip(tmp_path):
    step = contract(tmp_path)
    envelope = ResultEnvelope.from_contract(
        step, framework="hermes", worker_status=FAILED,
        verdict="NOT_TESTED", claimed_outcome="failure",
        blockers=("provider error",),
    )
    rebuilt = ResultEnvelope.from_payload(envelope.to_payload())
    assert rebuilt.worker_status == FAILED
    assert rebuilt.verdict == "NOT_TESTED"
