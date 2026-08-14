from __future__ import annotations

import pytest

from jarvis_foundry.errors import (
    AuthorityError,
    BlockedError,
    EffectError,
    IntegrityError,
    JarvisError,
    RecoveryError,
    UnsupportedError,
)


def test_errors_taxonomy() -> None:
    errors = [
        IntegrityError("bad ledger"),
        AuthorityError("not allowed"),
        EffectError("effect failed"),
        RecoveryError("restore failed"),
        BlockedError("provider unavailable"),
        UnsupportedError("not on windows"),
    ]
    for error in errors:
        assert isinstance(error, JarvisError)
        assert isinstance(error, Exception)
        assert error.code
        assert error.domain
        assert isinstance(error.context, dict)
    assert isinstance(UnsupportedError("x"), NotImplementedError)


def test_error_record_is_structured_and_json_safe() -> None:
    error = IntegrityError(
        "hash mismatch",
        context={"event_id": "abc", "nested": {"ok": True}},
        correlation_id="corr-1",
    )
    record = error.as_record()
    assert record["kind"] == "IntegrityError"
    assert record["code"] == "INTEGRITY"
    assert record["domain"] == "trusted_kernel"
    assert record["message"] == "hash mismatch"
    assert record["correlation_id"] == "corr-1"
    assert record["context"]["event_id"] == "abc"


def test_error_can_be_raised_and_caught_typed() -> None:
    with pytest.raises(IntegrityError) as exc_info:
        raise IntegrityError("ledger tampered")
    assert exc_info.value.code == "INTEGRITY"
    assert "ledger tampered" in str(exc_info.value)
