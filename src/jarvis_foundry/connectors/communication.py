"""V5-9 local fake communication and calendar contracts."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from ..control.redaction import contains_secret_like
from ..events import canonical_json
from .contract import ConnectorAttempt, ConnectorSpec


class CommunicationConnectorError(ValueError):
    """A representational target or fake-provider request is invalid."""


class _ProviderConflict(RuntimeError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_provider_id(record: dict[str, Any], *, prefix: str, idempotency_key: str) -> None:
    expected = f"{prefix}:{_hash(idempotency_key)[:24]}"
    if record.get("provider_id") != expected:
        raise CommunicationConnectorError("provider id does not match connector evidence")


class FakeMailProvider:
    """In-memory provider; no SMTP, account, or network interface exists."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.send_count = 0

    def send(
        self,
        *,
        recipient: str,
        body: str,
        sender_identity: str,
        content_provenance: dict[str, Any],
        idempotency_key: str,
        payload_hash: str,
        mode: str | None = None,
    ) -> dict[str, Any]:
        if (not isinstance(recipient, str) or recipient != recipient.strip() or
                not recipient or "," in recipient or ";" in recipient or
                "@" not in recipient):
            raise CommunicationConnectorError("exact recipient is required")
        if not isinstance(sender_identity, str) or not sender_identity.strip():
            raise CommunicationConnectorError("transparent sender identity is required")
        if not isinstance(content_provenance, dict) or not content_provenance:
            raise CommunicationConnectorError("content provenance is required")
        if idempotency_key in self.records:
            existing = self.records[idempotency_key]
            if existing["payload_hash"] != payload_hash:
                raise _ProviderConflict("idempotency key carries a different message")
            return dict(existing)
        message_id = f"message:{_hash(idempotency_key)[:24]}"
        record = {
            "provider_id": message_id, "recipient": recipient,
            "sender_identity": sender_identity, "content_hash": _hash(body),
            "provenance_hash": _hash(content_provenance),
            "payload_hash": payload_hash, "status": "SENT",
        }
        self.records[idempotency_key] = record
        self.send_count += 1
        if mode == "timeout-after-effect":
            raise TimeoutError("fake mail provider response lost")
        return dict(record)

    def reconcile(self, idempotency_key: str) -> dict[str, Any] | None:
        record = self.records.get(idempotency_key)
        return None if record is None else dict(record)

    def withdraw(self, idempotency_key: str) -> dict[str, Any]:
        record = self.records.get(idempotency_key)
        if record is None:
            raise CommunicationConnectorError("message is not known to provider")
        record["status"] = "WITHDRAWN"
        return dict(record)


class FakeCalendarProvider:
    """In-memory calendar provider with exact calendar targeting."""

    def __init__(self, *, allowed_calendars: tuple[str, ...] = ("calendar-lab",)) -> None:
        self.allowed_calendars = frozenset(allowed_calendars)
        self.records: dict[str, dict[str, Any]] = {}

    def create(self, *, calendar_id: str, title: str, start: str, end: str,
               sender_identity: str, content_provenance: dict[str, Any],
               idempotency_key: str, payload_hash: str,
               mode: str | None = None) -> dict[str, Any]:
        if calendar_id not in self.allowed_calendars:
            raise CommunicationConnectorError("calendar target is not registered")
        if not title or not start or not end:
            raise CommunicationConnectorError("calendar event target and times are required")
        if not sender_identity.strip() or not content_provenance:
            raise CommunicationConnectorError("calendar provenance and identity are required")
        if idempotency_key in self.records:
            existing = self.records[idempotency_key]
            if existing["payload_hash"] != payload_hash:
                raise _ProviderConflict("idempotency key carries a different event")
            return dict(existing)
        event_id = f"event:{_hash(idempotency_key)[:24]}"
        record = {
            "provider_id": event_id, "calendar_id": calendar_id,
            "title_hash": _hash(title), "start": start, "end": end,
            "sender_identity": sender_identity,
            "provenance_hash": _hash(content_provenance),
            "payload_hash": payload_hash, "status": "CREATED",
        }
        self.records[idempotency_key] = record
        if mode == "timeout-after-effect":
            raise TimeoutError("fake calendar provider response lost")
        return dict(record)

    def reconcile(self, idempotency_key: str) -> dict[str, Any] | None:
        record = self.records.get(idempotency_key)
        return None if record is None else dict(record)

    def cancel(self, idempotency_key: str) -> dict[str, Any]:
        record = self.records.get(idempotency_key)
        if record is None:
            raise CommunicationConnectorError("event is not known to provider")
        record["status"] = "CANCELLED"
        return dict(record)


class _ContractBase:
    def __init__(self) -> None:
        self._pending: dict[str, str] = {}
        self._completed: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _attempt_id(prefix: str) -> str:
        return f"{prefix}-attempt-{uuid.uuid4().hex}"

    def _attempt(
        self, *, prefix: str, connector_id: str, action_kind: str,
        target_ref: str, idempotency_key: str, request_hash: str,
        outcome: str, after_state: dict[str, Any] | None = None,
        provider_ref: str | None = None, error_code: str | None = None,
        reconciled: bool = False, replayed: bool = False,
    ) -> ConnectorAttempt:
        return ConnectorAttempt(
            attempt_id=self._attempt_id(prefix), connector_id=connector_id,
            action_kind=action_kind, target_ref=target_ref,
            idempotency_key=idempotency_key, payload_hash=request_hash,
            outcome=outcome, after_state=after_state,
            provider_ref=provider_ref, error_code=error_code,
            evidence_refs=(f"{prefix}:{target_ref}",),
            reconciled=reconciled, replayed=replayed,
        )


class CommunicationConnector(_ContractBase):
    spec = ConnectorSpec(
        connector_id="conn-mail-v5", kind="communication", version="1.0.0",
        actions={"communication.send": "communicate", "communication.withdraw": "external_effect"},
        capabilities={"send": "READY", "withdraw": "READY", "live_account": "BLOCKED"},
        retry_policy={"max_attempts": 1, "backoff_s": []},
        target_kinds=("message_recipient",), idempotency_strategy="provider-key",
        reconciliation_strategy="provider-query", supports_cancellation=True,
        cost_metadata={"unit": "fake_message", "cost_known": True},
        evidence_fields=("attempt_id", "provider_ref", "outcome", "reconciled"),
    )

    def __init__(self, provider: FakeMailProvider) -> None:
        super().__init__()
        self.provider = provider

    @staticmethod
    def _validate_identity(sender_identity: str, provenance: dict[str, Any]) -> None:
        if not isinstance(sender_identity, str) or not sender_identity.strip():
            raise CommunicationConnectorError("transparent sender identity is required")
        if not isinstance(provenance, dict) or not provenance:
            raise CommunicationConnectorError("content provenance is required")
        if contains_secret_like(canonical_json(provenance)):
            raise CommunicationConnectorError("secret-like provenance is refused")

    def send(
        self, *, recipient: str, body: str, sender_identity: str,
        content_provenance: dict[str, Any], idempotency_key: str,
        mode: str | None = None,
    ) -> ConnectorAttempt:
        self._validate_identity(sender_identity, content_provenance)
        if not isinstance(body, str) or contains_secret_like(body):
            raise CommunicationConnectorError("message body is invalid or secret-like")
        request_hash = _hash({"recipient": recipient, "body": body,
                              "sender_identity": sender_identity,
                              "content_provenance": content_provenance})
        prior = self._completed.get(idempotency_key)
        if prior is not None:
            if prior["payload_hash"] != request_hash:
                return self._attempt(prefix="message", connector_id=self.spec.connector_id,
                                     action_kind="communication.send", target_ref=recipient,
                                     idempotency_key=idempotency_key, request_hash=request_hash,
                                     outcome="FAILED", error_code="idempotency_conflict")
            return self._attempt(prefix="message", connector_id=self.spec.connector_id,
                                 action_kind="communication.send", target_ref=recipient,
                                 idempotency_key=idempotency_key, request_hash=request_hash,
                                 outcome="COMPLETED", after_state={"status": prior["status"]},
                                 provider_ref=prior["provider_id"], reconciled=True, replayed=True)
        if idempotency_key in self._pending:
            return self.reconcile(idempotency_key)
        self._pending[idempotency_key] = request_hash
        try:
            record = self.provider.send(
                recipient=recipient, body=body, sender_identity=sender_identity,
                content_provenance=content_provenance, idempotency_key=idempotency_key,
                payload_hash=request_hash, mode=mode,
            )
        except TimeoutError:
            return self._attempt(prefix="message", connector_id=self.spec.connector_id,
                                 action_kind="communication.send", target_ref=recipient,
                                 idempotency_key=idempotency_key, request_hash=request_hash,
                                 outcome="UNKNOWN", error_code="timeout")
        except _ProviderConflict:
            self._pending.pop(idempotency_key, None)
            return self._attempt(prefix="message", connector_id=self.spec.connector_id,
                                 action_kind="communication.send", target_ref=recipient,
                                 idempotency_key=idempotency_key, request_hash=request_hash,
                                 outcome="FAILED", error_code="idempotency_conflict")
        except CommunicationConnectorError:
            self._pending.pop(idempotency_key, None)
            raise
        _validate_provider_id(record, prefix="message", idempotency_key=idempotency_key)
        self._pending.pop(idempotency_key, None)
        self._completed[idempotency_key] = record
        return self._attempt(prefix="message", connector_id=self.spec.connector_id,
                             action_kind="communication.send", target_ref=recipient,
                             idempotency_key=idempotency_key, request_hash=request_hash,
                             outcome="COMPLETED", after_state={"status": record["status"]},
                             provider_ref=record["provider_id"])

    def reconcile(self, idempotency_key: str) -> ConnectorAttempt:
        record = self.provider.reconcile(idempotency_key)
        request_hash = self._pending.get(idempotency_key, _hash(idempotency_key))
        if record is None:
            return self._attempt(prefix="message", connector_id=self.spec.connector_id,
                                 action_kind="communication.send", target_ref="unknown",
                                 idempotency_key=idempotency_key, request_hash=request_hash,
                                 outcome="UNKNOWN", error_code="not_observed")
        _validate_provider_id(record, prefix="message", idempotency_key=idempotency_key)
        self._pending.pop(idempotency_key, None)
        self._completed[idempotency_key] = record
        return self._attempt(prefix="message", connector_id=self.spec.connector_id,
                             action_kind="communication.send", target_ref=record["recipient"],
                             idempotency_key=idempotency_key, request_hash=record["payload_hash"],
                             outcome="COMPLETED", after_state={"status": record["status"]},
                             provider_ref=record["provider_id"], reconciled=True)

    def withdraw(self, idempotency_key: str) -> ConnectorAttempt:
        try:
            record = self.provider.withdraw(idempotency_key)
        except CommunicationConnectorError:
            return self._attempt(prefix="message", connector_id=self.spec.connector_id,
                                 action_kind="communication.withdraw", target_ref="unknown",
                                 idempotency_key=idempotency_key, request_hash=_hash(idempotency_key),
                                 outcome="UNKNOWN", error_code="not_observed")
        _validate_provider_id(record, prefix="message", idempotency_key=idempotency_key)
        self._completed[idempotency_key] = record
        return self._attempt(prefix="message", connector_id=self.spec.connector_id,
                             action_kind="communication.withdraw", target_ref=record["recipient"],
                             idempotency_key=idempotency_key, request_hash=record["payload_hash"],
                             outcome="COMPLETED", after_state={"status": record["status"]},
                             provider_ref=record["provider_id"], reconciled=True)


class CalendarConnector(_ContractBase):
    spec = ConnectorSpec(
        connector_id="conn-calendar-v5", kind="calendar", version="1.0.0",
        actions={"calendar.create": "communicate", "calendar.cancel": "external_effect"},
        capabilities={"create": "READY", "cancel": "READY", "live_account": "BLOCKED"},
        retry_policy={"max_attempts": 1, "backoff_s": []},
        target_kinds=("calendar_id",), idempotency_strategy="provider-key",
        reconciliation_strategy="provider-query", supports_cancellation=True,
        cost_metadata={"unit": "fake_calendar_event", "cost_known": True},
        evidence_fields=("attempt_id", "provider_ref", "outcome", "reconciled"),
    )

    def __init__(self, provider: FakeCalendarProvider) -> None:
        super().__init__()
        self.provider = provider

    def create(
        self, *, calendar_id: str, title: str, start: str, end: str,
        sender_identity: str, content_provenance: dict[str, Any],
        idempotency_key: str, mode: str | None = None,
    ) -> ConnectorAttempt:
        if contains_secret_like(canonical_json(content_provenance)):
            raise CommunicationConnectorError("secret-like provenance is refused")
        request_hash = _hash({"calendar_id": calendar_id, "title": title,
                              "start": start, "end": end,
                              "sender_identity": sender_identity,
                              "content_provenance": content_provenance})
        prior = self._completed.get(idempotency_key)
        if prior is not None:
            if prior["payload_hash"] != request_hash:
                return self._attempt(prefix="event", connector_id=self.spec.connector_id,
                                     action_kind="calendar.create", target_ref=calendar_id,
                                     idempotency_key=idempotency_key, request_hash=request_hash,
                                     outcome="FAILED", error_code="idempotency_conflict")
            return self._attempt(prefix="event", connector_id=self.spec.connector_id,
                                 action_kind="calendar.create", target_ref=calendar_id,
                                 idempotency_key=idempotency_key, request_hash=request_hash,
                                 outcome="COMPLETED", after_state={"status": prior["status"]},
                                 provider_ref=prior["provider_id"], reconciled=True, replayed=True)
        self._pending[idempotency_key] = request_hash
        try:
            record = self.provider.create(
                calendar_id=calendar_id, title=title, start=start, end=end,
                sender_identity=sender_identity, content_provenance=content_provenance,
                idempotency_key=idempotency_key, payload_hash=request_hash, mode=mode,
            )
        except TimeoutError:
            return self._attempt(prefix="event", connector_id=self.spec.connector_id,
                                 action_kind="calendar.create", target_ref=calendar_id,
                                 idempotency_key=idempotency_key, request_hash=request_hash,
                                 outcome="UNKNOWN", error_code="timeout")
        except _ProviderConflict:
            self._pending.pop(idempotency_key, None)
            return self._attempt(prefix="event", connector_id=self.spec.connector_id,
                                 action_kind="calendar.create", target_ref=calendar_id,
                                 idempotency_key=idempotency_key, request_hash=request_hash,
                                 outcome="FAILED", error_code="idempotency_conflict")
        _validate_provider_id(record, prefix="event", idempotency_key=idempotency_key)
        self._pending.pop(idempotency_key, None)
        self._completed[idempotency_key] = record
        return self._attempt(prefix="event", connector_id=self.spec.connector_id,
                             action_kind="calendar.create", target_ref=calendar_id,
                             idempotency_key=idempotency_key, request_hash=request_hash,
                             outcome="COMPLETED", after_state={"status": record["status"]},
                             provider_ref=record["provider_id"])

    def reconcile(self, idempotency_key: str) -> ConnectorAttempt:
        record = self.provider.reconcile(idempotency_key)
        request_hash = self._pending.get(idempotency_key, _hash(idempotency_key))
        if record is None:
            return self._attempt(prefix="event", connector_id=self.spec.connector_id,
                                 action_kind="calendar.create", target_ref="unknown",
                                 idempotency_key=idempotency_key, request_hash=request_hash,
                                 outcome="UNKNOWN", error_code="not_observed")
        _validate_provider_id(record, prefix="event", idempotency_key=idempotency_key)
        self._pending.pop(idempotency_key, None)
        self._completed[idempotency_key] = record
        return self._attempt(prefix="event", connector_id=self.spec.connector_id,
                             action_kind="calendar.create", target_ref=record["calendar_id"],
                             idempotency_key=idempotency_key, request_hash=record["payload_hash"],
                             outcome="COMPLETED", after_state={"status": record["status"]},
                             provider_ref=record["provider_id"], reconciled=True)

    def cancel(self, idempotency_key: str) -> ConnectorAttempt:
        try:
            record = self.provider.cancel(idempotency_key)
        except CommunicationConnectorError:
            return self._attempt(prefix="event", connector_id=self.spec.connector_id,
                                 action_kind="calendar.cancel", target_ref="unknown",
                                 idempotency_key=idempotency_key, request_hash=_hash(idempotency_key),
                                 outcome="UNKNOWN", error_code="not_observed")
        _validate_provider_id(record, prefix="event", idempotency_key=idempotency_key)
        self._completed[idempotency_key] = record
        return self._attempt(prefix="event", connector_id=self.spec.connector_id,
                             action_kind="calendar.cancel", target_ref=record["calendar_id"],
                             idempotency_key=idempotency_key, request_hash=record["payload_hash"],
                             outcome="COMPLETED", after_state={"status": record["status"]},
                             provider_ref=record["provider_id"], reconciled=True)


__all__ = [
    "CalendarConnector", "CommunicationConnector", "CommunicationConnectorError",
    "FakeCalendarProvider", "FakeMailProvider",
]
