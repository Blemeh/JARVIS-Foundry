"""V5-2: normalized connector request and attempt contract."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis_foundry.connectors.contract import (
    ATTEMPT_OUTCOMES,
    ConnectorAttempt,
    ConnectorRequest,
    ConnectorSpec,
    latest_spec,
    validate_connector_attempt,
    validate_connector_request,
)
from jarvis_foundry.connectors.fakes import ContractFakeConnector
from jarvis_foundry.connectors.registry import register_connector
from jarvis_foundry.database import FoundryDatabase


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _spec() -> ConnectorSpec:
    return ConnectorSpec(
        connector_id="conn-contract-fake",
        kind="service",
        version="1.0.0",
        actions={"record.write": "write", "record.read": "read"},
        capabilities={"write": "READY", "read": "READY"},
        retry_policy={"max_attempts": 3, "backoff_s": [0, 1, 2]},
        target_kinds=("record",),
        idempotency_strategy="effect-key",
        reconciliation_strategy="system-readback",
        supports_cancellation=True,
        cost_metadata={"unit": "local-operation", "known": True},
        evidence_fields=("attempt_id", "outcome", "after_state"),
        stop_policy="broker-and-driver",
    )


def _request(*, key: str = "effect-1", payload: str = "hello",
             action: str = "record.write") -> ConnectorRequest:
    return ConnectorRequest(
        connector_id="conn-contract-fake",
        action_kind=action,
        target_ref="record:1",
        idempotency_key=key,
        payload=payload,
        payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
        before_state={"hash": "sha256:before"},
        capability="write",
    )


def test_spec_metadata_round_trips_through_registry(
        db: FoundryDatabase) -> None:
    spec = _spec()
    register_connector(db, spec)

    loaded = latest_spec(db, spec.connector_id)

    assert loaded == spec
    assert loaded is not None
    assert loaded.reconciliation_strategy == "system-readback"
    assert loaded.cost_metadata == {"unit": "local-operation", "known": True}


def test_request_validation_requires_target_and_idempotency() -> None:
    spec = _spec()
    request = _request()
    assert validate_connector_request(spec, request) is None

    assert validate_connector_request(
        spec, ConnectorRequest(
            connector_id=request.connector_id,
            action_kind=request.action_kind,
            target_ref="",
            idempotency_key=request.idempotency_key,
            payload=request.payload,
            payload_hash=request.payload_hash,
        )) is not None
    assert validate_connector_request(
        spec, ConnectorRequest(
            connector_id=request.connector_id,
            action_kind=request.action_kind,
            target_ref=request.target_ref,
            idempotency_key="",
            payload=request.payload,
            payload_hash=request.payload_hash,
        )) is not None


def test_attempt_validation_is_bound_to_request_and_safe_for_evidence() -> None:
    spec = _spec()
    request = _request()
    attempt = ConnectorAttempt(
        attempt_id="attempt-1",
        connector_id=request.connector_id,
        action_kind=request.action_kind,
        target_ref=request.target_ref,
        idempotency_key=request.idempotency_key,
        payload_hash=request.payload_hash,
        outcome="COMPLETED",
        before_state=request.before_state,
        after_state={"hash": "sha256:after"},
        evidence_refs=("fixture:record:1",),
        cost={"units": 1},
    )
    assert validate_connector_attempt(spec, request, attempt) is None
    evidence = attempt.to_evidence()
    assert evidence["payload_hash"] == request.payload_hash
    assert "hello" not in str(evidence)
    assert "payload" not in evidence


def test_fake_connector_dedupes_and_rejects_conflicting_payload() -> None:
    connector = ContractFakeConnector(_spec())
    request = _request()

    first = connector.execute(request)
    replay = connector.execute(request)

    assert first.outcome == "COMPLETED"
    assert replay.replayed is True
    assert connector.invocation_count == 1

    with pytest.raises(ValueError, match="idempotency key conflict"):
        connector.execute(_request(key=request.idempotency_key,
                                   payload="different"))


def test_fake_connector_timeout_reconciles_without_second_invocation() -> None:
    connector = ContractFakeConnector(_spec())
    connector.fail_next = True
    request = _request()

    unknown = connector.execute(request)
    reconciled = connector.reconcile(request, unknown)

    assert unknown.outcome == "UNKNOWN"
    assert reconciled.outcome == "COMPLETED"
    assert reconciled.reconciled is True
    assert connector.invocation_count == 1


def test_cancellation_is_explicit_and_contract_bounded() -> None:
    spec = _spec()
    request = _request()
    cancelled = ContractFakeConnector(spec).cancel(request)

    assert cancelled.outcome == "CANCELLED"
    assert "CANCELLED" in ATTEMPT_OUTCOMES
