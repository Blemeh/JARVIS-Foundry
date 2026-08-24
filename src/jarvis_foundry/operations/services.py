"""Deterministic local service, communication, and calendar contracts.

These providers are deliberately offline fakes.  They model the hard parts
of external work—identity, idempotency, quota, cost uncertainty, timeout
ambiguity, reconciliation, and compensation—without real accounts,
credentials, messages, calendars, or cloud resources.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from ..events import canonical_json
from .procedure import OperationError


class ServiceError(OperationError):
    pass


class ProviderConflict(ServiceError):
    pass


class RateLimited(ServiceError):
    def __init__(self, message: str = "provider rate limit", *, retry_after: float = 1.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServiceTimeout(ServiceError):
    """The provider did not confirm whether the effect happened."""


class ServiceTimeoutAfterCommit(ServiceTimeout):
    def __init__(self, message: str, *, provider_reference: str) -> None:
        super().__init__(message)
        self.provider_reference = provider_reference


def _exact(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(f"{field_name} must be exact and non-empty")
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise ServiceError(f"{field_name} contains a control character")
    if any(token in result for token in ("*", "${", "%", "..")):
        raise ServiceError(f"{field_name} must not contain a wildcard or traversal")
    return result


def _body_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(payload)).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    service_id: str
    resource_id: str
    state: dict[str, Any]
    version: int
    provider_reference: str

    @property
    def identity(self) -> str:
        return f"{self.service_id}:{self.resource_id}"


@dataclass(frozen=True, slots=True)
class ServiceReceipt:
    action: str
    resource_id: str
    provider_reference: str
    state_hash: str
    idempotency_key: str
    cost_estimate: float | None


class FakeResourceService:
    """Offline CRUD service with explicit failure injection."""

    def __init__(self, service_id: str = "fake-cloud") -> None:
        self.service_id = _exact(service_id, "service_id")
        self._resources: dict[str, ResourceRecord] = {}
        self._idempotency: dict[str, tuple[str, ServiceReceipt]] = {}
        self.quota_remaining: int | None = 100
        self.cost_known = True
        self.cost_per_operation = 0.01
        self._timeout_before = False
        self._timeout_after_commit = False
        self._rate_limit_next = False

    def inject_timeout(self, *, after_commit: bool) -> None:
        self._timeout_before = not after_commit
        self._timeout_after_commit = after_commit

    def inject_rate_limit(self) -> None:
        self._rate_limit_next = True

    def estimate_cost(self, action: str, resource_id: str) -> float | None:
        _exact(action, "action")
        _exact(resource_id, "resource_id")
        return self.cost_per_operation if self.cost_known else None

    def read(self, resource_id: str) -> ResourceRecord | None:
        return self._resources.get(_exact(resource_id, "resource_id"))

    def create(
        self,
        resource_id: str,
        state: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> ServiceReceipt:
        return self._mutate("create", resource_id, state, idempotency_key=idempotency_key)

    def update(
        self,
        resource_id: str,
        state: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> ServiceReceipt:
        return self._mutate("update", resource_id, state, idempotency_key=idempotency_key)

    def delete(self, resource_id: str, *, idempotency_key: str) -> ServiceReceipt:
        resource_id = _exact(resource_id, "resource_id")
        return self._mutate("delete", resource_id, {}, idempotency_key=idempotency_key)

    def reconcile(self, resource_id: str, *, expected_state: Mapping[str, Any] | None = None) -> ResourceRecord | None:
        record = self.read(resource_id)
        if record is not None and expected_state is not None:
            if record.state != dict(expected_state):
                raise ProviderConflict(
                    f"resource {resource_id} exists with a different state")
        return record

    def snapshot(self) -> dict[str, Any]:
        return {
            key: {
                "service_id": value.service_id,
                "resource_id": value.resource_id,
                "state": dict(value.state),
                "version": value.version,
                "provider_reference": value.provider_reference,
            }
            for key, value in sorted(self._resources.items())
        }

    def _mutate(
        self,
        action: str,
        resource_id: str,
        state: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> ServiceReceipt:
        action = _exact(action, "action")
        resource_id = _exact(resource_id, "resource_id")
        idempotency_key = _exact(idempotency_key, "idempotency_key")
        payload = {"action": action, "resource_id": resource_id, "state": dict(state)}
        digest = _body_hash(payload)
        previous = self._idempotency.get(idempotency_key)
        if previous is not None:
            if previous[0] != digest:
                raise ProviderConflict("idempotency key was reused with a different request")
            return previous[1]
        if self._rate_limit_next:
            self._rate_limit_next = False
            raise RateLimited(retry_after=2.0)
        if self.quota_remaining is not None and self.quota_remaining <= 0:
            raise RateLimited("fake service quota exhausted", retry_after=10.0)
        if self._timeout_before:
            self._timeout_before = False
            raise ServiceTimeout("provider timeout before effect")
        current = self._resources.get(resource_id)
        if action == "create" and current is not None:
            raise ProviderConflict(f"resource {resource_id} already exists")
        if action in {"update", "delete"} and current is None:
            raise ProviderConflict(f"resource {resource_id} does not exist")
        version = 1 if current is None else current.version + 1
        provider_reference = f"{self.service_id}:receipt:{resource_id}:{version}"
        next_state = dict(state) if action != "delete" else {"deleted": True}
        record = ResourceRecord(
            service_id=self.service_id, resource_id=resource_id,
            state=next_state, version=version,
            provider_reference=provider_reference,
        )
        self._resources[resource_id] = record
        if self.quota_remaining is not None:
            self.quota_remaining -= 1
        receipt = ServiceReceipt(
            action=action, resource_id=resource_id,
            provider_reference=provider_reference,
            state_hash=_body_hash(next_state), idempotency_key=idempotency_key,
            cost_estimate=self.estimate_cost(action, resource_id),
        )
        self._idempotency[idempotency_key] = (digest, receipt)
        if self._timeout_after_commit:
            self._timeout_after_commit = False
            raise ServiceTimeoutAfterCommit(
                "provider committed the effect but the response was lost",
                provider_reference=provider_reference,
            )
        return receipt


@dataclass(frozen=True, slots=True)
class HttpResponse:
    url: str
    status: int
    body: dict[str, Any]


class FakeHttpService:
    """Offline HTTP/health contract used by the digital-office lab."""

    def __init__(self, base_url: str = "http://synthetic.service") -> None:
        self.base_url = _exact(base_url, "base_url")
        self.healthy = True
        self.version = "v1"
        self._timeout = False

    def inject_timeout(self) -> None:
        self._timeout = True

    def set_health(self, healthy: bool, *, version: str | None = None) -> None:
        self.healthy = bool(healthy)
        if version is not None:
            self.version = _exact(version, "version")

    def get(self, path: str, *, expected_url: str | None = None) -> HttpResponse:
        path = _exact(path, "path")
        url = f"{self.base_url}{path}"
        if expected_url is not None and url != _exact(expected_url, "expected_url"):
            raise ProviderConflict(f"unexpected HTTP redirect: {url}")
        if self._timeout:
            self._timeout = False
            raise ServiceTimeout("synthetic HTTP response timed out")
        if path != "/health":
            return HttpResponse(url=url, status=404, body={"error": "not found"})
        return HttpResponse(
            url=url, status=200 if self.healthy else 503,
            body={"status": "ok" if self.healthy else "degraded", "version": self.version},
        )


@dataclass(frozen=True, slots=True)
class Recipient:
    recipient_id: str
    address: str
    display_name: str


@dataclass(frozen=True, slots=True)
class MessageReceipt:
    provider_message_id: str
    recipient_id: str
    sender_identity: str
    body_hash: str
    state: str


class FakeCommunicationProvider:
    def __init__(self, provider_id: str = "fake-mail") -> None:
        self.provider_id = _exact(provider_id, "provider_id")
        self._recipients: dict[str, Recipient] = {}
        self._drafts: dict[str, dict[str, Any]] = {}
        self._sent: dict[str, MessageReceipt] = {}
        self._idempotency: dict[str, tuple[str, MessageReceipt]] = {}
        self._timeout_after_commit = False
        self._register_defaults()

    def _register_defaults(self) -> None:
        self._recipients["test:owner"] = Recipient(
            "test:owner", "owner@example.invalid", "Synthetic Owner")
        self._recipients["test:reviewer"] = Recipient(
            "test:reviewer", "reviewer@example.invalid", "Synthetic Reviewer")

    def resolve_recipient(self, recipient_id: str, *, exact_address: str | None = None) -> Recipient:
        recipient_id = _exact(recipient_id, "recipient_id")
        recipient = self._recipients.get(recipient_id)
        if recipient is None:
            raise ProviderConflict(f"unknown recipient {recipient_id}")
        if exact_address is not None and recipient.address != _exact(exact_address, "exact_address"):
            raise ProviderConflict("recipient address does not match exact identity")
        return recipient

    def draft(self, recipient_id: str, body: str, *, subject: str) -> str:
        recipient = self.resolve_recipient(recipient_id)
        body = _exact(body, "body")
        subject = _exact(subject, "subject")
        draft_id = f"draft:{self.provider_id}:{len(self._drafts) + 1}"
        self._drafts[draft_id] = {
            "recipient_id": recipient.recipient_id,
            "subject": subject,
            "body": body,
        }
        return draft_id

    def inject_timeout(self, *, after_commit: bool) -> None:
        if not after_commit:
            raise ServiceError("fake communication only models post-send ambiguity")
        self._timeout_after_commit = True

    def send(
        self,
        draft_id: str,
        *,
        idempotency_key: str,
        sender_identity: str = "JARVIS (synthetic test provider)",
    ) -> MessageReceipt:
        draft_id = _exact(draft_id, "draft_id")
        idempotency_key = _exact(idempotency_key, "idempotency_key")
        sender_identity = _exact(sender_identity, "sender_identity")
        if "JARVIS" not in sender_identity.upper():
            raise ServiceError("sender identity must transparently identify JARVIS")
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise ProviderConflict("draft does not exist")
        digest = _body_hash({"draft_id": draft_id, "sender_identity": sender_identity})
        existing = self._idempotency.get(idempotency_key)
        if existing is not None:
            if existing[0] != digest:
                raise ProviderConflict("communication idempotency conflict")
            return existing[1]
        provider_id = f"{self.provider_id}:message:{len(self._sent) + 1}"
        receipt = MessageReceipt(
            provider_message_id=provider_id,
            recipient_id=draft["recipient_id"],
            sender_identity=sender_identity,
            body_hash=_body_hash({"subject": draft["subject"], "body": draft["body"]}),
            state="SENT",
        )
        self._sent[provider_id] = receipt
        self._idempotency[idempotency_key] = (digest, receipt)
        if self._timeout_after_commit:
            self._timeout_after_commit = False
            raise ServiceTimeoutAfterCommit(
                "fake provider committed a send before response loss",
                provider_reference=provider_id,
            )
        return receipt

    def reconcile(self, provider_message_id: str) -> MessageReceipt | None:
        return self._sent.get(_exact(provider_message_id, "provider_message_id"))

    def withdraw(self, provider_message_id: str) -> MessageReceipt:
        provider_message_id = _exact(provider_message_id, "provider_message_id")
        current = self._sent.get(provider_message_id)
        if current is None:
            raise ProviderConflict("message does not exist")
        withdrawn = MessageReceipt(
            provider_message_id=current.provider_message_id,
            recipient_id=current.recipient_id,
            sender_identity=current.sender_identity,
            body_hash=current.body_hash,
            state="WITHDRAWN",
        )
        self._sent[provider_message_id] = withdrawn
        return withdrawn


@dataclass(frozen=True, slots=True)
class CalendarEventReceipt:
    provider_event_id: str
    calendar_id: str
    title: str
    attendees: tuple[str, ...]
    start: str
    end: str
    state: str


class FakeCalendarProvider:
    def __init__(self, provider_id: str = "fake-calendar") -> None:
        self.provider_id = _exact(provider_id, "provider_id")
        self._calendars = {"test:team", "test:personal"}
        self._events: dict[str, CalendarEventReceipt] = {}
        self._idempotency: dict[str, tuple[str, CalendarEventReceipt]] = {}
        self._timeout_after_commit = False

    def inject_timeout(self, *, after_commit: bool) -> None:
        if not after_commit:
            raise ServiceError("fake calendar only models post-create ambiguity")
        self._timeout_after_commit = True

    def availability(self, calendar_id: str, start: str, end: str) -> bool:
        self._check_calendar(calendar_id)
        _exact(start, "start")
        _exact(end, "end")
        return not any(
            event.calendar_id == calendar_id and event.state == "ACTIVE"
            and event.start < end and start < event.end
            for event in self._events.values()
        )

    def create_event(
        self,
        calendar_id: str,
        *,
        title: str,
        attendees: tuple[str, ...],
        start: str,
        end: str,
        idempotency_key: str,
    ) -> CalendarEventReceipt:
        calendar_id = self._check_calendar(calendar_id)
        title = _exact(title, "title")
        start = _exact(start, "start")
        end = _exact(end, "end")
        attendees = tuple(_exact(item, "attendee") for item in attendees)
        if len(set(attendees)) != len(attendees):
            raise ProviderConflict("calendar attendees must be unique")
        idempotency_key = _exact(idempotency_key, "idempotency_key")
        payload = {"calendar_id": calendar_id, "title": title, "attendees": attendees,
                   "start": start, "end": end}
        digest = _body_hash(payload)
        existing = self._idempotency.get(idempotency_key)
        if existing is not None:
            if existing[0] != digest:
                raise ProviderConflict("calendar idempotency conflict")
            return existing[1]
        if not self.availability(calendar_id, start, end):
            raise ProviderConflict("calendar interval is not available")
        provider_id = f"{self.provider_id}:event:{len(self._events) + 1}"
        receipt = CalendarEventReceipt(
            provider_event_id=provider_id, calendar_id=calendar_id,
            title=title, attendees=attendees, start=start, end=end, state="ACTIVE",
        )
        self._events[provider_id] = receipt
        self._idempotency[idempotency_key] = (digest, receipt)
        if self._timeout_after_commit:
            self._timeout_after_commit = False
            raise ServiceTimeoutAfterCommit(
                "fake calendar committed before response loss",
                provider_reference=provider_id,
            )
        return receipt

    def update_event(self, provider_event_id: str, *, title: str) -> CalendarEventReceipt:
        provider_event_id = _exact(provider_event_id, "provider_event_id")
        current = self._events.get(provider_event_id)
        if current is None:
            raise ProviderConflict("calendar event does not exist")
        updated = CalendarEventReceipt(
            provider_event_id=current.provider_event_id, calendar_id=current.calendar_id,
            title=_exact(title, "title"), attendees=current.attendees,
            start=current.start, end=current.end, state=current.state,
        )
        self._events[provider_event_id] = updated
        return updated

    def cancel_event(self, provider_event_id: str) -> CalendarEventReceipt:
        provider_event_id = _exact(provider_event_id, "provider_event_id")
        current = self._events.get(provider_event_id)
        if current is None:
            raise ProviderConflict("calendar event does not exist")
        cancelled = CalendarEventReceipt(
            provider_event_id=current.provider_event_id, calendar_id=current.calendar_id,
            title=current.title, attendees=current.attendees,
            start=current.start, end=current.end, state="CANCELLED",
        )
        self._events[provider_event_id] = cancelled
        return cancelled

    def reconcile(self, provider_event_id: str) -> CalendarEventReceipt | None:
        return self._events.get(_exact(provider_event_id, "provider_event_id"))

    def _check_calendar(self, calendar_id: str) -> str:
        calendar_id = _exact(calendar_id, "calendar_id")
        if calendar_id not in self._calendars:
            raise ProviderConflict(f"unknown exact calendar {calendar_id}")
        return calendar_id


__all__ = [
    "CalendarEventReceipt", "FakeCalendarProvider", "FakeCommunicationProvider",
    "FakeHttpService", "FakeResourceService", "HttpResponse", "MessageReceipt", "ProviderConflict", "RateLimited",
    "Recipient", "ResourceRecord", "ServiceError", "ServiceReceipt",
    "ServiceTimeout", "ServiceTimeoutAfterCommit",
]
