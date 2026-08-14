from __future__ import annotations

import pytest

from jarvis_foundry.errors import IntegrityError
from jarvis_foundry.observability import (
    StructuredLogger,
    StructuredRecord,
    child_correlation,
    new_correlation_id,
    record_from_error,
)


def test_structured_record_as_dict() -> None:
    record = StructuredRecord(
        record_id="r1",
        ts="2026-08-14T00:00:00Z",
        level="INFO",
        domain="kernel",
        message="started",
        fields={"count": 1},
        correlation_id="c1",
    )
    payload = record.as_dict()
    assert payload["record_id"] == "r1"
    assert payload["fields"]["count"] == 1
    assert payload["error_code"] is None


def test_correlation_helpers() -> None:
    parent = new_correlation_id()
    child = child_correlation(parent)
    assert child != parent
    assert child.startswith(parent + ":")
    assert new_correlation_id() != parent


def test_logger_emit_and_no_silent_failure() -> None:
    logger = StructuredLogger()
    emitted = logger.emit(
        level="WARNING",
        domain="owner_controls",
        message="veto applied",
        fields={"directive_id": "d1"},
        correlation_id="c1",
    )
    assert len(logger.records()) == 1
    assert logger.records()[0] == emitted
    logger.require_all_recorded(1)
    with pytest.raises(RuntimeError, match="no-silent-failure"):
        logger.require_all_recorded(2)


def test_logger_rejects_invalid_level() -> None:
    logger = StructuredLogger()
    with pytest.raises(ValueError, match="invalid structured level"):
        logger.emit(level="NOISY", domain="kernel", message="bad")


def test_error_sink_failure_propagates() -> None:
    def broken_sink(_record: StructuredRecord) -> None:
        raise OSError("durable sink unavailable")

    logger = StructuredLogger(sink=broken_sink)
    with pytest.raises(OSError, match="durable sink unavailable"):
        logger.emit(level="ERROR", domain="kernel", message="sink test")


def test_record_from_error_uses_structured_fields() -> None:
    error = IntegrityError("tampered", correlation_id="c1")
    record = record_from_error(error, domain="kernel", correlation_id="c1")
    assert record.error_code == "INTEGRITY"
    assert record.error_domain == "trusted_kernel"
    assert record.message == "tampered"
