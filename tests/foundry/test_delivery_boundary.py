from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delivery import (
    CostStatus,
    DeliveryBoundary,
    DeliveryCancelled,
    DeliveryConflict,
    DeliveryError,
    DeliveryOutcome,
    DeliveryRequest,
    DeliveryStatus,
    FakeDeliveryProvider,
)
from jarvis_foundry.events import Event
from jarvis_foundry.execution.pipeline import ActionCandidate, deliver_candidate


def _database(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _request(*, key: str = "delivery-key", max_attempts: int = 1) -> DeliveryRequest:
    return DeliveryRequest(
        request_id=f"request-{key}",
        mission_id="mission-1",
        action_id="action-1",
        channel="VOICE",
        provider="fake-local",
        target="owner-ui",
        payload="The mission is complete.",
        idempotency_key=key,
        max_attempts=max_attempts,
    )


def test_delivery_success_is_typed_and_idempotent(tmp_path):
    database = _database(tmp_path)
    provider = FakeDeliveryProvider()
    boundary = DeliveryBoundary(database, {provider.provider_id: provider})
    request = _request()

    first = boundary.dispatch(request)
    replay = boundary.dispatch(request)

    assert first["status"] == DeliveryStatus.DELIVERED.value
    assert first["cost_status"] == CostStatus.UNKNOWN.value
    assert replay == first
    assert len(provider.calls) == 1
    assert database.event_count(event_type="delivery.requested") == 1
    assert database.event_count(event_type="delivery.outcome") == 1


def test_retryable_provider_failure_is_bounded_then_delivered(tmp_path):
    database = _database(tmp_path)
    provider = FakeDeliveryProvider(scripted=[
        DeliveryError("temporary provider outage", retryable=True),
        DeliveryOutcome.delivered(
            provider_request_id="fake-accepted",
            cost_status=CostStatus.MEASURED, cost_usd=0.0),
    ])
    boundary = DeliveryBoundary(database, {provider.provider_id: provider})

    result = boundary.dispatch(_request(key="retry-key", max_attempts=2))

    assert result["status"] == DeliveryStatus.DELIVERED.value
    assert result["attempt"] == 2
    assert result["cost_status"] == CostStatus.MEASURED.value
    assert len(provider.calls) == 2
    assert database.event_count(event_type="delivery.outcome") == 2


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (TimeoutError("provider timeout"), DeliveryStatus.TIMED_OUT),
        (DeliveryCancelled("owner cancelled"), DeliveryStatus.CANCELLED),
    ],
)
def test_timeout_and_cancellation_are_terminal_and_replay_safe(tmp_path, error, status):
    database = _database(tmp_path)
    provider = FakeDeliveryProvider(scripted=[error])
    boundary = DeliveryBoundary(database, {provider.provider_id: provider})
    request = _request(key=f"{status.value.lower()}-key")

    first = boundary.dispatch(request)
    replay = boundary.dispatch(request)

    assert first["status"] == status.value
    assert replay == first
    assert len(provider.calls) == 1


def test_request_without_outcome_is_unknown_and_never_blindly_retried(tmp_path):
    database = _database(tmp_path)
    provider = FakeDeliveryProvider()
    boundary = DeliveryBoundary(database, {provider.provider_id: provider})
    request = _request(key="crash-window")
    database.append(Event.create(
        "delivery.requested", request.to_payload(),
        source_type="delivery-boundary", source_id=provider.provider_id,
        idempotency_key=f"delivery.requested:{request.idempotency_key}"))

    result = boundary.dispatch(request)

    assert result["status"] == DeliveryStatus.UNKNOWN.value
    assert "without a durable" in result["reason"]
    assert provider.calls == []


def test_provider_identity_mismatch_is_unknown_and_cost_stays_unknown(tmp_path):
    database = _database(tmp_path)
    provider = FakeDeliveryProvider(scripted=[
        DeliveryOutcome.delivered(provider_actual="unexpected-provider", cost_status=CostStatus.MEASURED, cost_usd=12.0),
    ])
    boundary = DeliveryBoundary(database, {provider.provider_id: provider})

    result = boundary.dispatch(_request(key="identity-mismatch"))

    assert result["status"] == DeliveryStatus.UNKNOWN.value
    assert result["error_class"] == "ProviderIdentityMismatch"
    assert result["cost_status"] == CostStatus.UNKNOWN.value
    assert result["cost_usd"] is None


def test_unknown_provider_is_blocked_without_external_call(tmp_path):
    database = _database(tmp_path)
    boundary = DeliveryBoundary(database, {})

    result = boundary.dispatch(_request(key="missing-provider"))

    assert result["status"] == DeliveryStatus.BLOCKED.value
    assert result["error_class"] == "ProviderUnavailable"


def test_same_idempotency_key_with_changed_payload_fails_closed(tmp_path):
    database = _database(tmp_path)
    provider = FakeDeliveryProvider()
    boundary = DeliveryBoundary(database, {provider.provider_id: provider})
    request = _request(key="conflict")
    boundary.dispatch(request)
    changed = replace(request, payload="different message")

    with pytest.raises(DeliveryConflict):
        boundary.dispatch(changed)


def test_delivery_request_from_envelope_derives_stable_key_and_rejects_secrets():
    envelope = SimpleNamespace(
        mission_id="m-1", action_id="a-1", target_resources=("owner-ui",),
        expected_effect="speak complete",
    )
    first = DeliveryRequest.from_envelope(
        envelope, channel="voice", provider="fake-local")
    second = DeliveryRequest.from_envelope(
        envelope, channel="voice", provider="fake-local")
    assert first == second
    assert first.channel == "VOICE"
    with pytest.raises(ValueError, match="secret-like"):
        replace(first, payload="api_key=secret")


def test_selected_candidate_becomes_a_provider_delivery_request(tmp_path):
    database = _database(tmp_path)
    provider = FakeDeliveryProvider()
    boundary = DeliveryBoundary(database, {provider.provider_id: provider})
    candidate = ActionCandidate(
        action_id="speak-1", action_kind="voice.speak",
        target_resources=("owner-ui",), capability="voice",
        expected_effect="mission complete", connector_id="unused",
    )

    result = deliver_candidate(
        database, "mission-voice", "run-voice", candidate,
        delivery_boundary=boundary, channel="voice", provider="fake-local",
    )

    assert result["outcome"] == DeliveryStatus.DELIVERED.value
    assert result["channel"] == "VOICE"
    assert result["mission_id"] == "mission-voice"
    assert len(provider.calls) == 1
