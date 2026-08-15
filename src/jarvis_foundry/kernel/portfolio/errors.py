"""R7 portfolio error taxonomy on top of the trusted kernel errors."""

from __future__ import annotations

from jarvis_foundry.kernel.errors import KernelError


class PortfolioError(KernelError):
    """Base class for R7 portfolio failures."""


class ConflictError(PortfolioError):
    """An idempotency key was reused with different content."""


class IntegrityError(PortfolioError):
    """Portfolio state or source content failed integrity checks."""


class DuplicateError(PortfolioError):
    """A duplicate candidate or effect was refused."""


class BudgetExceeded(PortfolioError):
    """A research/monitor budget limit was exhausted."""


class TargetMismatchError(PortfolioError):
    """The execution-time canonical target differs from the selected target."""


class SourceUnavailableError(PortfolioError):
    """A real source could not be captured within the bounded policy."""


class ControlVetoError(PortfolioError):
    """An owner STOP, pause, or veto blocks the requested action."""
