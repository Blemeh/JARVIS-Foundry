"""V5-9 fake communication/calendar contract proof."""

from __future__ import annotations

import pytest

from jarvis_foundry.connectors.communication import (
    FakeCalendarProvider,
    FakeMailProvider,
    CalendarConnector,
    CommunicationConnector,
    CommunicationConnectorError,
)


def test_mail_contract_has_exact_target_identity_and_duplicate_proof() -> None:
    provider = FakeMailProvider()
    connector = CommunicationConnector(provider)
    first = connector.send(
        recipient="recipient@example.invalid",
        body="A bounded update",
        sender_identity="JARVIS (automated)",
        content_provenance={"source_refs": ["mission:m-1"], "content_hash": "h1"},
        idempotency_key="mail-1",
    )
    assert first.outcome == "COMPLETED"
    assert first.provider_ref and first.provider_ref.startswith("message:")
    replay = connector.send(
        recipient="recipient@example.invalid",
        body="A bounded update",
        sender_identity="JARVIS (automated)",
        content_provenance={"source_refs": ["mission:m-1"], "content_hash": "h1"},
        idempotency_key="mail-1",
    )
    assert replay.replayed is True
    assert provider.send_count == 1
    with pytest.raises(CommunicationConnectorError, match="exact recipient"):
        connector.send(
            recipient="recipient@example.invalid,other@example.invalid",
            body="bad",
            sender_identity="JARVIS (automated)",
            content_provenance={"source_refs": ["mission:m-1"]},
            idempotency_key="mail-2",
        )


def test_mail_timeout_reconciles_and_withdraws_without_resend() -> None:
    provider = FakeMailProvider()
    connector = CommunicationConnector(provider)
    timed_out = connector.send(
        recipient="recipient@example.invalid",
        body="timeout proof",
        sender_identity="JARVIS (automated)",
        content_provenance={"source_refs": ["mission:m-2"], "content_hash": "h2"},
        idempotency_key="mail-timeout",
        mode="timeout-after-effect",
    )
    assert timed_out.outcome == "UNKNOWN"
    resolved = connector.reconcile("mail-timeout")
    assert resolved.outcome == "COMPLETED"
    retry = connector.send(
        recipient="recipient@example.invalid",
        body="timeout proof",
        sender_identity="JARVIS (automated)",
        content_provenance={"source_refs": ["mission:m-2"], "content_hash": "h2"},
        idempotency_key="mail-timeout",
    )
    assert retry.replayed is True
    assert provider.send_count == 1
    withdrawn = connector.withdraw("mail-timeout")
    assert withdrawn.outcome == "COMPLETED"
    assert provider.reconcile("mail-timeout")["status"] == "WITHDRAWN"


def test_calendar_contract_exact_target_and_cancellation() -> None:
    provider = FakeCalendarProvider()
    connector = CalendarConnector(provider)
    event = connector.create(
        calendar_id="calendar-lab",
        title="Bounded review",
        start="2026-08-23T15:00:00Z",
        end="2026-08-23T15:30:00Z",
        sender_identity="JARVIS (automated)",
        content_provenance={"source_refs": ["mission:m-3"]},
        idempotency_key="event-1",
    )
    assert event.outcome == "COMPLETED"
    assert event.provider_ref and event.provider_ref.startswith("event:")
    cancelled = connector.cancel("event-1")
    assert cancelled.outcome == "COMPLETED"
    assert provider.reconcile("event-1")["status"] == "CANCELLED"
    with pytest.raises(CommunicationConnectorError, match="calendar target"):
        connector.create(
            calendar_id="calendar-other",
            title="wrong target",
            start="2026-08-23T15:00:00Z",
            end="2026-08-23T15:30:00Z",
            sender_identity="JARVIS (automated)",
            content_provenance={"source_refs": ["mission:m-3"]},
            idempotency_key="event-2",
        )

