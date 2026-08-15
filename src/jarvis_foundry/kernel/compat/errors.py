"""Failure taxonomy for the R5 historical-compatibility layer."""

from __future__ import annotations

from jarvis_foundry.kernel.errors import KernelError


class CompatError(KernelError):
    """Base class for historical-compatibility failures."""


class CompatSchemaError(CompatError):
    """A legacy database schema is missing, unknown, or unsupported."""


class CompatReadError(CompatError):
    """A legacy row cannot be read or mapped without mutation."""

    def __init__(self, reason: str, *, sequence: int | None = None) -> None:
        self.reason = reason
        self.sequence = sequence
        suffix = f" (sequence {sequence})" if sequence is not None else ""
        super().__init__(f"{reason}{suffix}")


class CompatReplayError(CompatError):
    """Replay failed deterministically and closed rather than guessing."""


class CompatMigrationError(CompatError):
    """A rehearsal migration step failed or was left incomplete."""
