"""Typed error taxonomy for the trusted kernel."""

from __future__ import annotations


class KernelError(Exception):
    """Base class for every trusted-kernel failure."""


class LedgerError(KernelError):
    """Base class for ledger failures."""


class UnknownEventError(LedgerError):
    """An event kind is not registered with the ledger."""


class EventIntegrityError(LedgerError):
    """An event fails payload, schema, or hash validation."""


class EventConflictError(LedgerError):
    """An idempotency key conflicts with a different event."""


class FinancialActionDeniedError(KernelError):
    """Financial action classes are absent or deterministically denied."""


class ProjectionError(KernelError):
    """Base class for projection failures."""


class ProjectionUnknownEventError(ProjectionError):
    """A projection handler does not declare a replayed event type."""

    def __init__(self, projection_name: str | None = None, event_type: str | None = None) -> None:
        if event_type is None:
            super().__init__(projection_name or "")
        else:
            super().__init__(
                f"projection {projection_name or '<none>'} refuses event type {event_type}"
            )
        self.projection_name = projection_name
        self.event_type = event_type or ""


class TransitionError(KernelError):
    """A state machine refuses the requested transition."""


class AuthorityError(KernelError):
    """An authenticated authority requirement was not satisfied."""


class NonceReplayError(AuthorityError):
    """A signed owner command nonce was reused."""

    def __init__(self, message: str, *, nonce: str = "") -> None:
        super().__init__(message)
        self.nonce = nonce


class CapabilityError(KernelError):
    """A capability grant is missing, expired, revoked, or substituted."""


class SecretError(KernelError):
    """Secret metadata lookup or redaction failed."""


class EffectError(KernelError):
    """An effect could not be prepared, executed, or compensated."""


class EvidenceError(KernelError):
    """Evidence binding or verifier identity is invalid."""


class ReleaseError(KernelError):
    """A release transition is invalid or failed injection."""


class SupervisorError(KernelError):
    """Base class for supervisor failures."""


class DuplicateLaunchError(SupervisorError):
    """A worker already holds an active lease."""


class BackpressureError(SupervisorError):
    """The supervisor is at capacity and refuses immediate launch."""
