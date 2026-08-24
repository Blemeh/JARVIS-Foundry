"""V7B-7/V7B-8/V7B-9/V7B-11: offline service-effect contracts."""
from __future__ import annotations

import pytest

from jarvis_foundry.operations.services import (
    FakeCalendarProvider,
    FakeCommunicationProvider,
    FakeResourceService,
    ProviderConflict,
    RateLimited,
    ServiceError,
    ServiceTimeoutAfterCommit,
)


def test_resource_service_reconciles_post_commit_timeout_and_models_quota() -> None:
    service = FakeResourceService()
    service.inject_timeout(after_commit=True)
    with pytest.raises(ServiceTimeoutAfterCommit) as error:
        service.create("bucket:lab", {"size": 1}, idempotency_key="effect:create:1")
    record = service.reconcile("bucket:lab", expected_state={"size": 1})
    assert record is not None
    assert record.provider_reference == error.value.provider_reference
    # A repeated delivery is deduplicated, but the proof did not rely on it
    # to decide whether the ambiguous effect happened.
    assert service.create(
        "bucket:lab", {"size": 1}, idempotency_key="effect:create:1"
    ).provider_reference == error.value.provider_reference

    service.cost_known = False
    assert service.estimate_cost("update", "bucket:lab") is None
    service.inject_rate_limit()
    with pytest.raises(RateLimited) as limited:
        service.update("bucket:lab", {"size": 2}, idempotency_key="effect:update:1")
    assert limited.value.retry_after > 0


def test_resource_idempotency_conflict_and_compensation_shape() -> None:
    service = FakeResourceService()
    service.create("resource:1", {"enabled": True}, idempotency_key="create:1")
    with pytest.raises(ProviderConflict, match="idempotency"):
        service.create("resource:1", {"enabled": False}, idempotency_key="create:1")
    updated = service.update("resource:1", {"enabled": False}, idempotency_key="update:1")
    assert service.read("resource:1").state == {"enabled": False}  # type: ignore[union-attr]
    deleted = service.delete("resource:1", idempotency_key="delete:1")
    assert deleted.action == "delete"
    assert service.read("resource:1").state == {"deleted": True}  # type: ignore[union-attr]


def test_communication_requires_exact_recipient_and_transparent_identity() -> None:
    provider = FakeCommunicationProvider()
    recipient = provider.resolve_recipient(
        "test:owner", exact_address="owner@example.invalid"
    )
    assert recipient.display_name == "Synthetic Owner"
    draft = provider.draft(
        "test:owner", "The disposable operation completed.", subject="Lab result"
    )
    with pytest.raises(ServiceError, match="transparently"):
        provider.send(draft, idempotency_key="send:bad", sender_identity="Automation")
    provider.inject_timeout(after_commit=True)
    with pytest.raises(ServiceTimeoutAfterCommit) as error:
        provider.send(draft, idempotency_key="send:1")
    sent = provider.reconcile(error.value.provider_reference)
    assert sent is not None and sent.recipient_id == "test:owner"
    assert provider.send(draft, idempotency_key="send:1").provider_message_id == sent.provider_message_id
    assert provider.withdraw(sent.provider_message_id).state == "WITHDRAWN"


def test_calendar_exact_identity_duplicate_prevention_and_cancel() -> None:
    provider = FakeCalendarProvider()
    assert provider.availability("test:team", "2026-08-23T10:00Z", "2026-08-23T11:00Z")
    event = provider.create_event(
        "test:team", title="Synthetic review", attendees=("test:owner", "test:reviewer"),
        start="2026-08-23T10:00Z", end="2026-08-23T11:00Z", idempotency_key="event:1",
    )
    assert provider.create_event(
        "test:team", title="Synthetic review", attendees=("test:owner", "test:reviewer"),
        start="2026-08-23T10:00Z", end="2026-08-23T11:00Z", idempotency_key="event:1",
    ).provider_event_id == event.provider_event_id
    with pytest.raises(ProviderConflict, match="available"):
        provider.create_event(
            "test:team", title="Duplicate", attendees=("test:owner",),
            start="2026-08-23T10:30Z", end="2026-08-23T11:30Z", idempotency_key="event:2",
        )
    assert provider.update_event(event.provider_event_id, title="Renamed").title == "Renamed"
    assert provider.cancel_event(event.provider_event_id).state == "CANCELLED"
    assert provider.availability("test:team", "2026-08-23T10:30Z", "2026-08-23T11:30Z")
