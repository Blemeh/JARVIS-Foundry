"""Typed error taxonomy for the JARVIS remaster trusted kernel.

R3 adds this module additively; no existing module is refactored here.
The taxonomy gives every failure a stable code, a domain, and structured
context so the observability layer can record it without string parsing.
"""

from __future__ import annotations

from typing import Any


class JarvisError(Exception):
    """Base class for all JARVIS remaster domain errors."""

    code: str
    domain: str
    context: dict[str, Any]
    correlation_id: str | None

    def __init__(
        self,
        message: str,
        *,
        code: str,
        domain: str,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.domain = domain
        self.context = dict(context or {})
        self.correlation_id = correlation_id

    @property
    def message(self) -> str:
        return str(super().__str__())

    def as_record(self) -> dict[str, Any]:
        """Structured, JSON-safe failure record for observability sinks."""
        return {
            "kind": type(self).__name__,
            "code": self.code,
            "domain": self.domain,
            "message": self.message,
            "context": self.context,
            "correlation_id": self.correlation_id,
        }


class IntegrityError(JarvisError):
    """A deterministic invariant was violated (ledger, schema, hash, replay)."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            code="INTEGRITY",
            domain="trusted_kernel",
            context=context,
            correlation_id=correlation_id,
        )


class AuthorityError(JarvisError):
    """An action was refused because authority, authentication, or a veto failed."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            code="AUTHORITY",
            domain="owner_controls",
            context=context,
            correlation_id=correlation_id,
        )


class EffectError(JarvisError):
    """An external effect could not be performed, replayed, or reconciled safely."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            code="EFFECT",
            domain="effect_pipeline",
            context=context,
            correlation_id=correlation_id,
        )


class RecoveryError(JarvisError):
    """A recovery, restore, or rollback step failed and must fail loudly."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            code="RECOVERY",
            domain="recovery",
            context=context,
            correlation_id=correlation_id,
        )


class BlockedError(JarvisError):
    """A capability is blocked by external state and must never be guessed."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            code="BLOCKED",
            domain="capability",
            context=context,
            correlation_id=correlation_id,
        )


class UnsupportedError(JarvisError, NotImplementedError):
    """A requested feature is intentionally unsupported on this platform/stage.

    Inherits ``NotImplementedError`` so legacy callers that catch the
    builtin variant keep working while new callers get the structured fields.
    """

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        JarvisError.__init__(
            self,
            message,
            code="UNSUPPORTED",
            domain="capability",
            context=context,
            correlation_id=correlation_id,
        )
