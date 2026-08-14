"""Structured logging, metrics, and correlation helpers for the remaster kernel.

R3 adds this module additively. The no-silent-failure contract lives here:
``emit_error`` raises when the configured sink itself fails, and
``StructuredLogger.require_all_recorded`` fails loudly when a caller
claimed to record work that produced no record.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, ClassVar


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_record_id() -> str:
    return str(uuid.uuid4())


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def child_correlation(parent: str) -> str:
    """Deterministic-looking child correlation id derived from a parent."""
    return f"{parent}:{new_record_id()}"


@dataclass(frozen=True, slots=True)
class StructuredRecord:
    """One immutable, JSON-safe observability record."""

    record_id: str
    ts: str
    level: str
    domain: str
    message: str
    fields: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: str | None = None
    error_code: str | None = None
    error_domain: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "ts": self.ts,
            "level": self.level,
            "domain": self.domain,
            "message": self.message,
            "fields": self.fields,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "error_code": self.error_code,
            "error_domain": self.error_domain,
        }

    def with_parent(self, parent: str) -> StructuredRecord:
        return replace(self, correlation_id=parent)


Sink = Callable[[StructuredRecord], None]


class StructuredLogger:
    """In-memory structured sink with an optional durable sink hook."""

    _LEVELS: ClassVar[set[str]] = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    def __init__(self, sink: Sink | None = None) -> None:
        self._sink = sink
        self._records: list[StructuredRecord] = []

    def emit(
        self,
        *,
        level: str,
        domain: str,
        message: str,
        fields: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        error_code: str | None = None,
        error_domain: str | None = None,
    ) -> StructuredRecord:
        if level not in self._LEVELS:
            raise ValueError(f"invalid structured level: {level}")
        record = StructuredRecord(
            record_id=new_record_id(),
            ts=utc_now_iso(),
            level=level,
            domain=domain,
            message=message,
            fields=dict(fields or {}),
            correlation_id=correlation_id or new_correlation_id(),
            causation_id=causation_id,
            error_code=error_code,
            error_domain=error_domain,
        )
        self._records.append(record)
        if self._sink is not None:
            # No silent failure: a broken durable sink propagates.
            self._sink(record)
        return record

    def emit_error(self, record: StructuredRecord) -> StructuredRecord:
        """Persist a failure record; sink failures must never be swallowed."""
        if record.level not in {"ERROR", "CRITICAL"}:
            raise ValueError("emit_error requires ERROR or CRITICAL level")
        self._records.append(record)
        if self._sink is not None:
            self._sink(record)
        return record

    def records(self) -> tuple[StructuredRecord, ...]:
        return tuple(self._records)

    def clear(self) -> None:
        self._records.clear()

    def require_all_recorded(self, expected: int) -> tuple[StructuredRecord, ...]:
        """Fail loudly when durable work claimed more records than observed."""
        records = self.records()
        if len(records) != expected:
            raise RuntimeError(
                f"no-silent-failure: expected {expected} records, observed {len(records)}"
            )
        return records


def record_from_error(
    error: BaseException,
    *,
    domain: str,
    level: str = "ERROR",
    correlation_id: str | None = None,
    fields: dict[str, Any] | None = None,
) -> StructuredRecord:
    """Build a StructuredRecord from an exception without string parsing."""
    code = getattr(error, "code", type(error).__name__)
    error_domain = getattr(error, "domain", None)
    return StructuredRecord(
        record_id=new_record_id(),
        ts=utc_now_iso(),
        level=level,
        domain=domain,
        message=str(error),
        fields=dict(fields or {}),
        correlation_id=correlation_id or new_correlation_id(),
        error_code=str(code),
        error_domain=str(error_domain) if error_domain else domain,
    )
