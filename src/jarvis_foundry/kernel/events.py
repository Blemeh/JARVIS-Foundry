"""Ledger event envelope, kind registry, and header hash chaining."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.errors import EventIntegrityError, UnknownEventError


def payload_hash(payload: Mapping[str, Any]) -> str:
    return canonical_hash(payload)


@dataclass(frozen=True)
class KindSpec:
    kind: str
    version: int
    required_keys: tuple[str, ...]
    financial: bool


class EventKindRegistry:
    """Registered event kinds with schema versions and financial flags."""

    def __init__(self) -> None:
        self._kinds: dict[str, KindSpec] = {}

    def register(
        self,
        kind: str,
        *,
        version: int = 1,
        required_keys: tuple[str, ...] | list[str] = (),
        financial: bool = False,
    ) -> EventKindRegistry:
        self._kinds[kind] = KindSpec(
            kind=kind,
            version=version,
            required_keys=tuple(required_keys),
            financial=financial,
        )
        return self

    def kinds(self) -> frozenset[str]:
        return frozenset(self._kinds)

    def entries(self) -> dict[str, KindSpec]:
        return dict(self._kinds)

    def is_known(self, kind: str) -> bool:
        return kind in self._kinds

    def spec(self, kind: str) -> KindSpec:
        spec = self._kinds.get(kind)
        if spec is None:
            raise UnknownEventError(kind)
        return spec

    def validate(self, event: LedgerEvent) -> None:
        spec = self.spec(event.event_type)
        if event.schema_version != spec.version:
            raise EventIntegrityError(
                f"event {event.event_id} schema version {event.schema_version} "
                f"does not match registered version {spec.version}"
            )
        missing = [key for key in spec.required_keys if key not in event.payload]
        if missing:
            raise EventIntegrityError(
                f"event {event.event_id} missing required payload keys: {sorted(missing)}"
            )
        if event.payload_hash != payload_hash(event.payload):
            raise EventIntegrityError(f"event {event.event_id} payload hash mismatch")


@dataclass(frozen=True)
class LedgerEvent:
    """An immutable event envelope with content-derived hashes."""

    event_id: str
    event_type: str
    occurred_at: str
    recorded_at: str
    source_type: str
    source_id: str
    correlation_id: str | None
    causation_id: str | None
    idempotency_key: str
    schema_version: int
    payload: Mapping[str, Any]
    payload_hash: str
    previous_hash: str | None = None
    header_hash: str | None = None
    sequence: int | None = None
    kind_version: int = 1

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        payload: Mapping[str, Any],
        source_type: str,
        source_id: str,
        idempotency_key: str,
        event_id: str | None = None,
        occurred_at: str | None = None,
        recorded_at: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        schema_version: int = 1,
    ) -> LedgerEvent:
        if event_id is None:
            event_id = f"{event_type}:{idempotency_key}"
        if occurred_at is None:
            occurred_at = "1970-01-01T00:00:00Z"
        if recorded_at is None:
            recorded_at = occurred_at
        return cls(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            source_type=source_type,
            source_id=source_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
            schema_version=schema_version,
            payload=dict(payload),
            payload_hash=payload_hash(payload),
        )

    def header_hash_for(self, sequence: int, previous_hash: str | None) -> str:
        return canonical_hash(
            {
                "sequence": sequence,
                "event_id": self.event_id,
                "event_type": self.event_type,
                "occurred_at": self.occurred_at,
                "recorded_at": self.recorded_at,
                "source_type": self.source_type,
                "source_id": self.source_id,
                "correlation_id": self.correlation_id,
                "causation_id": self.causation_id,
                "idempotency_key": self.idempotency_key,
                "schema_version": self.schema_version,
                "kind_version": self.kind_version,
                "payload_hash": self.payload_hash,
                "previous_hash": previous_hash,
            }
        )

    def with_idempotency_key(self, idempotency_key: str) -> LedgerEvent:
        return replace(self, idempotency_key=idempotency_key)

    def with_payload_hash(self, payload_hash_value: str) -> LedgerEvent:
        return replace(self, payload_hash=payload_hash_value)

    def with_event_type(self, event_type: str) -> LedgerEvent:
        return replace(self, event_type=event_type)

    def with_recorded_at(self, recorded_at: str) -> LedgerEvent:
        return replace(self, recorded_at=recorded_at)

    def with_kind_version(self, kind_version: int) -> LedgerEvent:
        return replace(self, kind_version=kind_version)

    def with_chain(
        self,
        *,
        sequence: int,
        previous_hash: str | None,
        header_hash: str,
    ) -> LedgerEvent:
        return replace(
            self,
            sequence=sequence,
            previous_hash=previous_hash,
            header_hash=header_hash,
        )
