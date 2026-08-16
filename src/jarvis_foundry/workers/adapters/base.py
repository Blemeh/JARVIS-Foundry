"""Worker adapter protocol (framework-neutral boundary)."""
from __future__ import annotations

from typing import Callable, Protocol

from ..contracts import WorkerStepContract
from ..envelope import ResultEnvelope


class ProviderUnavailable(RuntimeError):
    """The requested provider cannot serve a worker session right now."""


class AdapterConfigurationError(RuntimeError):
    """The adapter was configured in a way that violates a safety invariant."""


class WorkerAdapter(Protocol):
    """One framework adapter. ``dispatch`` returns a normalized envelope."""

    framework: str

    def dispatch(
        self,
        contract: WorkerStepContract,
        *,
        heartbeat: Callable[[], None],
    ) -> ResultEnvelope:
        """Run one isolated worker session; call ``heartbeat`` periodically."""
        ...
