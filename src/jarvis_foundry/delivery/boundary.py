"""Typed provider/voice/UI delivery with durable, conservative replay.

The boundary is deliberately provider-agnostic. A mission supplies a typed
request and an adapter returns a typed outcome. Only the adapter knows how to
talk to a provider; credentials never enter the request or event ledger. A
request that has started but has no durable outcome is reported UNKNOWN rather
than blindly retried, because the provider may already have accepted it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping, Protocol

from ..control.redaction import contains_secret_like, redact_text
from ..database import FoundryDatabase
from ..events import Event, EventConflict, canonical_json


class DeliveryStatus(str, Enum):
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class CostStatus(str, Enum):
    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class DeliveryError(RuntimeError):
    """Adapter failure with explicit retry safety."""

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.retryable = bool(retryable)


class DeliveryConflict(DeliveryError):
    """The same idempotency key was reused for a different request."""


class DeliveryCancelled(DeliveryError):
    """The provider or owner cancelled before a delivery was confirmed."""

    def __init__(self, reason: str = "delivery cancelled") -> None:
        super().__init__(reason, retryable=False)


@dataclass(frozen=True, slots=True)
class DeliveryRequest:
    request_id: str
    mission_id: str
    action_id: str
    channel: str
    provider: str
    target: str
    payload: str
    idempotency_key: str
    model: str | None = None
    requested_by: str = "jarvis"
    timeout_seconds: float = 30.0
    max_attempts: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", str(self.channel).upper())
        for name in (
            "request_id", "mission_id", "action_id", "channel", "provider",
            "target", "payload", "idempotency_key", "requested_by",
        ):
            value = str(getattr(self, name))
            if not value.strip() or any(char in value for char in "\r\n"):
                raise ValueError(f"delivery {name} must be a non-empty single line")
        if self.channel.upper() not in {"PROVIDER", "VOICE", "UI"}:
            raise ValueError(f"unsupported delivery channel: {self.channel}")
        if contains_secret_like(self.payload):
            raise ValueError("delivery payload contains secret-like content")
        if not 0.1 <= float(self.timeout_seconds) <= 300:
            raise ValueError("delivery timeout_seconds must be between 0.1 and 300")
        if not 1 <= int(self.max_attempts) <= 3:
            raise ValueError("delivery max_attempts must be between 1 and 3")

    @classmethod
    def from_envelope(
        cls,
        envelope: Any,
        *,
        channel: str,
        provider: str,
        target: str | None = None,
        payload: str | None = None,
        request_id: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 1,
    ) -> "DeliveryRequest":
        mission_id = str(envelope.mission_id)
        action_id = str(envelope.action_id)
        resolved_target = str(target or envelope.target_resources[0])
        body = str(payload if payload is not None else envelope.expected_effect or "")
        material = {
            "mission_id": mission_id,
            "action_id": action_id,
            "channel": channel.upper(),
            "provider": provider,
            "target": resolved_target,
            "payload": body,
        }
        key = "delivery-" + hashlib.sha256(
            canonical_json(material).encode("utf-8")).hexdigest()[:32]
        return cls(
            request_id=request_id or key,
            mission_id=mission_id,
            action_id=action_id,
            channel=channel.upper(),
            provider=provider,
            target=resolved_target,
            payload=body,
            idempotency_key=key,
            model=model,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "action_id": self.action_id,
            "channel": self.channel.upper(),
            "provider_requested": self.provider,
            "model_requested": self.model,
            "target": self.target,
            "payload": self.payload,
            "idempotency_key": self.idempotency_key,
            "requested_by": self.requested_by,
            "timeout_seconds": float(self.timeout_seconds),
            "max_attempts": int(self.max_attempts),
        }


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    status: DeliveryStatus
    provider_actual: str | None = None
    provider_request_id: str | None = None
    cost_status: CostStatus = CostStatus.UNKNOWN
    cost_usd: float | None = None
    error_class: str | None = None
    reason: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        status = self.status if isinstance(self.status, DeliveryStatus) else DeliveryStatus(str(self.status))
        object.__setattr__(self, "status", status)
        cost_status = self.cost_status if isinstance(self.cost_status, CostStatus) else CostStatus(str(self.cost_status))
        object.__setattr__(self, "cost_status", cost_status)
        if self.cost_status == CostStatus.UNKNOWN and self.cost_usd is not None:
            raise ValueError("unknown delivery cost must not carry cost_usd")
        if self.cost_status != CostStatus.UNKNOWN:
            if self.cost_usd is None or float(self.cost_usd) < 0:
                raise ValueError("measured or estimated delivery cost must be non-negative")
        if self.provider_actual is not None and not str(self.provider_actual).strip():
            raise ValueError("provider_actual must be non-empty when supplied")

    @classmethod
    def delivered(
        cls, *, provider_actual: str | None = None,
        provider_request_id: str | None = None,
        cost_status: CostStatus = CostStatus.UNKNOWN,
        cost_usd: float | None = None,
    ) -> "DeliveryOutcome":
        return cls(
            DeliveryStatus.DELIVERED,
            provider_actual=provider_actual,
            provider_request_id=provider_request_id,
            cost_status=cost_status,
            cost_usd=cost_usd,
        )


class DeliveryProvider(Protocol):
    provider_id: str

    def deliver(self, request: DeliveryRequest) -> DeliveryOutcome:
        ...


class FakeDeliveryProvider:
    """Local deterministic adapter. It never reads credentials or networks."""

    def __init__(
        self,
        provider_id: str = "fake-local",
        scripted: list[DeliveryOutcome | BaseException] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.scripted = list(scripted or [])
        self.calls: list[DeliveryRequest] = []
        self._confirmed: dict[str, DeliveryOutcome] = {}

    def deliver(self, request: DeliveryRequest) -> DeliveryOutcome:
        self.calls.append(request)
        confirmed = self._confirmed.get(request.idempotency_key)
        if confirmed is not None:
            return confirmed
        scripted = self.scripted.pop(0) if self.scripted else DeliveryOutcome.delivered(
            provider_actual=self.provider_id,
            provider_request_id=f"fake-{len(self.calls)}",
        )
        if isinstance(scripted, BaseException):
            raise scripted
        outcome = scripted
        if outcome.provider_actual is None:
            outcome = replace(outcome, provider_actual=self.provider_id)
        if outcome.status == DeliveryStatus.DELIVERED:
            self._confirmed[request.idempotency_key] = outcome
        return outcome


class DeliveryBoundary:
    """Durable request/outcome boundary with bounded, replay-safe retries."""

    def __init__(
        self,
        database: FoundryDatabase,
        providers: Mapping[str, DeliveryProvider],
    ) -> None:
        self.database = database
        self.providers = dict(providers)

    def _events(self, request: DeliveryRequest, event_type: str) -> list[dict[str, Any]]:
        connection = self.database.connect(readonly=True)
        try:
            rows = connection.execute(
                "SELECT payload_json, sequence FROM events "
                "WHERE event_type = ? AND json_extract(payload_json, '$.idempotency_key') = ? "
                "ORDER BY sequence",
                (event_type, request.idempotency_key),
            ).fetchall()
        finally:
            connection.close()
        values: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row[0])
            except (TypeError, ValueError):
                continue
            payload["_sequence"] = int(row[1])
            values.append(payload)
        return values

    def _append(self, event_type: str, payload: dict[str, Any], key: str) -> None:
        self.database.append(Event.create(
            event_type,
            payload,
            source_type="delivery-boundary",
            source_id=str(payload.get("provider_requested") or "delivery"),
            idempotency_key=key,
        ))

    def _receipt(self, request: DeliveryRequest, outcome: DeliveryOutcome, attempt: int) -> dict[str, Any]:
        return {
            "request_id": request.request_id,
            "idempotency_key": request.idempotency_key,
            "mission_id": request.mission_id,
            "action_id": request.action_id,
            "channel": request.channel.upper(),
            "provider_requested": request.provider,
            "provider_actual": outcome.provider_actual,
            "provider_request_id": outcome.provider_request_id,
            "status": outcome.status.value,
            "attempt": attempt,
            "cost_status": outcome.cost_status.value,
            "cost_usd": outcome.cost_usd,
            "error_class": outcome.error_class,
            "reason": outcome.reason,
            "retryable": bool(outcome.retryable),
        }

    @staticmethod
    def _receipt_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        fields = (
            "request_id", "idempotency_key", "mission_id", "action_id",
            "channel", "provider_requested", "provider_actual",
            "provider_request_id", "status", "attempt", "cost_status",
            "cost_usd", "error_class", "reason", "retryable",
        )
        return {field: payload.get(field) for field in fields}

    def _assert_request_match(
        self, request: DeliveryRequest, payload: Mapping[str, Any],
    ) -> None:
        expected = request.to_payload()
        fields = (
            "request_id", "mission_id", "action_id", "channel",
            "provider_requested", "model_requested", "target", "payload",
            "idempotency_key", "requested_by", "timeout_seconds", "max_attempts",
        )
        for field in fields:
            if payload.get(field) != expected.get(field):
                raise DeliveryConflict(
                    f"delivery idempotency conflict for {request.idempotency_key}")

    def _normalize(self, request: DeliveryRequest, raw: DeliveryOutcome) -> DeliveryOutcome:
        if not isinstance(raw, DeliveryOutcome):
            raise DeliveryError("provider returned an invalid typed outcome")
        if raw.provider_actual not in (None, request.provider):
            return DeliveryOutcome(
                DeliveryStatus.UNKNOWN,
                provider_actual=raw.provider_actual,
                cost_status=CostStatus.UNKNOWN,
                error_class="ProviderIdentityMismatch",
                reason="provider identity did not match the requested provider",
            )
        if raw.provider_actual is None:
            raw = replace(raw, provider_actual=request.provider)
        if raw.reason:
            raw = replace(raw, reason=redact_text(str(raw.reason)))
        return raw

    def _claim(self, request: DeliveryRequest, attempt: int) -> bool:
        """Acquire one durable provider-attempt claim.

        Event idempotency gives us a cross-process compare-and-set without a
        migration. A competing owner sees the conflict and must report
        UNKNOWN rather than calling the provider a second time.
        """

        payload = {
            **request.to_payload(),
            "attempt": int(attempt),
            "claim_id": uuid.uuid4().hex,
        }
        try:
            self._append(
                "delivery.claimed",
                payload,
                f"delivery.claimed:{request.idempotency_key}:{attempt}",
            )
        except EventConflict:
            return False
        return True

    def dispatch(self, request: DeliveryRequest) -> dict[str, Any]:
        previous = self._events(request, "delivery.outcome")
        if previous:
            latest = previous[-1]
            self._assert_request_match(request, latest)
            latest_attempt = int(latest.get("attempt") or 0)
            if (
                latest.get("status") != DeliveryStatus.FAILED.value
                or not bool(latest.get("retryable"))
                or latest_attempt >= request.max_attempts
            ):
                return self._receipt_from_payload(latest)
            start_attempt = latest_attempt + 1
        else:
            requested = self._events(request, "delivery.requested")
            if requested:
                self._assert_request_match(request, requested[-1])
                ambiguous = DeliveryOutcome(
                    DeliveryStatus.UNKNOWN,
                    error_class="DeliveryOutcomeUnknown",
                    reason="delivery request exists without a durable provider outcome",
                )
                self._append(
                    "delivery.ambiguous",
                    {**request.to_payload(), **self._receipt(request, ambiguous, 0)},
                    f"delivery.ambiguous:{request.idempotency_key}",
                )
                return self._receipt(request, ambiguous, 0)
            self._append(
                "delivery.requested",
                request.to_payload(),
                f"delivery.requested:{request.idempotency_key}",
            )
            start_attempt = 1

        provider = self.providers.get(request.provider)
        if provider is None:
            outcome = DeliveryOutcome(
                DeliveryStatus.BLOCKED,
                error_class="ProviderUnavailable",
                reason="requested provider is not registered in this runtime",
            )
            receipt = self._receipt(request, outcome, 0)
            self._append(
                "delivery.outcome",
                {**request.to_payload(), **receipt},
                f"delivery.outcome:{request.idempotency_key}:0",
            )
            return receipt

        for attempt in range(start_attempt, request.max_attempts + 1):
            if not self._claim(request, attempt):
                ambiguous = DeliveryOutcome(
                    DeliveryStatus.UNKNOWN,
                    error_class="DeliveryClaimConflict",
                    reason="another owner is delivering this request",
                )
                return self._receipt(request, ambiguous, attempt)
            try:
                outcome = self._normalize(request, provider.deliver(request))
            except DeliveryCancelled as error:
                outcome = DeliveryOutcome(
                    DeliveryStatus.CANCELLED,
                    error_class=type(error).__name__, reason=redact_text(str(error)),
                )
            except TimeoutError as error:
                outcome = DeliveryOutcome(
                    DeliveryStatus.TIMED_OUT,
                    error_class=type(error).__name__, reason=redact_text(str(error)),
                )
            except DeliveryError as error:
                outcome = DeliveryOutcome(
                    DeliveryStatus.FAILED,
                    error_class=type(error).__name__, reason=redact_text(str(error)),
                    retryable=error.retryable,
                )
            except BaseException as error:  # noqa: BLE001 - boundary is terminal
                outcome = DeliveryOutcome(
                    DeliveryStatus.FAILED,
                    error_class=type(error).__name__, reason=redact_text(str(error)),
                )
            receipt = self._receipt(request, outcome, attempt)
            self._append(
                "delivery.outcome",
                {**request.to_payload(), **receipt},
                f"delivery.outcome:{request.idempotency_key}:{attempt}",
            )
            if not outcome.retryable or attempt >= request.max_attempts:
                return receipt
        return self._receipt(
            request,
            DeliveryOutcome(
                DeliveryStatus.UNKNOWN,
                error_class="RetryBudgetExhausted",
                reason="delivery retry budget exhausted without a terminal outcome",
            ),
            request.max_attempts,
        )

    def dispatch_envelope(
        self,
        envelope: Any,
        *,
        channel: str,
        provider: str,
        target: str | None = None,
        payload: str | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        request = DeliveryRequest.from_envelope(
            envelope,
            channel=channel,
            provider=provider,
            target=target,
            payload=payload,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
        )
        return self.dispatch(request)
