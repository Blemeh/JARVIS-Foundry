from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from .clock import isoformat


class EventIntegrityError(ValueError):
    pass


class EventConflict(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    event_type: str
    occurred_at: str
    recorded_at: str
    source_type: str
    source_id: str
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    schema_version: int
    payload: dict[str, Any]
    payload_hash: str

    @classmethod
    def create(
        cls,
        event_type: str,
        payload: dict[str, Any],
        *,
        source_type: str,
        source_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        idempotency_key: str | None = None,
        occurred_at: str | None = None,
    ) -> "Event":
        event_id = str(uuid.uuid4())
        timestamp = occurred_at or isoformat()
        return cls(
            event_id=event_id,
            event_type=event_type,
            occurred_at=timestamp,
            recorded_at=isoformat(),
            source_type=source_type,
            source_id=source_id,
            correlation_id=correlation_id or event_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key or event_id,
            schema_version=1,
            payload=payload,
            payload_hash=payload_hash(payload),
        )

    def validate(self) -> None:
        if not self.event_id or not self.event_type or "." not in self.event_type:
            raise EventIntegrityError("event identity or type is invalid")
        if self.schema_version < 1:
            raise EventIntegrityError("schema_version must be positive")
        actual = payload_hash(self.payload)
        if actual != self.payload_hash:
            raise EventIntegrityError(
                f"payload hash mismatch for {self.event_id}: expected {self.payload_hash}, got {actual}"
            )

