from __future__ import annotations

import pytest

from jarvis_foundry.kernel.errors import (
    AuthorityError,
    BackpressureError,
    CapabilityError,
    DuplicateLaunchError,
    EffectError,
    EventConflictError,
    EventIntegrityError,
    EvidenceError,
    FinancialActionDeniedError,
    KernelError,
    LedgerError,
    NonceReplayError,
    ProjectionError,
    ProjectionUnknownEventError,
    ReleaseError,
    SecretError,
    SupervisorError,
    TransitionError,
    UnknownEventError,
)

ALL_ERRORS = (
    AuthorityError,
    BackpressureError,
    CapabilityError,
    DuplicateLaunchError,
    EffectError,
    EventConflictError,
    EventIntegrityError,
    EvidenceError,
    FinancialActionDeniedError,
    KernelError,
    LedgerError,
    NonceReplayError,
    ProjectionError,
    ProjectionUnknownEventError,
    ReleaseError,
    SecretError,
    SupervisorError,
    TransitionError,
    UnknownEventError,
)


def test_error_taxonomy_is_hierarchical_and_json_safe() -> None:
    for error_type in ALL_ERRORS:
        assert issubclass(error_type, KernelError) or error_type is KernelError
        instance = error_type("message")
        assert str(instance) == "message"
        payload = {"type": error_type.__name__, "message": str(instance)}
        assert payload["message"] == "message"


def test_named_errors_carry_typed_context() -> None:
    error = NonceReplayError("nonce reused", nonce="n-1")
    assert error.nonce == "n-1"
    assert error.args[0] == "nonce reused"


def test_all_error_instances_raise_and_are_caught_typed() -> None:
    with pytest.raises(KernelError):
        raise TransitionError("no")
    with pytest.raises(LedgerError):
        raise UnknownEventError("kind")
    with pytest.raises(SupervisorError):
        raise DuplicateLaunchError("worker")
    with pytest.raises(SupervisorError):
        raise BackpressureError("full")
    with pytest.raises(AuthorityError):
        raise NonceReplayError("n")
