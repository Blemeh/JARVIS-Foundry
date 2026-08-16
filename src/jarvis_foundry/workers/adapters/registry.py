"""Worker registry: framework -> adapter + observed readiness."""
from __future__ import annotations

from ..contracts import WORKER_HERMES, WORKER_OPENHANDS
from ..router import WorkerReadiness
from .base import WorkerAdapter


class WorkerRegistry:
    """Map worker identities to adapters; readiness lives in the router."""

    def __init__(self, adapters: dict[str, WorkerAdapter] | None = None) -> None:
        self._adapters: dict[str, WorkerAdapter] = {}
        if adapters:
            for framework, adapter in adapters.items():
                self.register(framework, adapter)

    def register(self, framework: str, adapter: WorkerAdapter) -> None:
        if adapter.framework != framework:
            raise ValueError(
                f"adapter framework {adapter.framework!r} != registered identity {framework!r}")
        self._adapters[framework] = adapter

    def get(self, framework: str) -> WorkerAdapter:
        if framework not in self._adapters:
            raise KeyError(f"no adapter registered for worker {framework!r}")
        return self._adapters[framework]

    def known(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def readiness_snapshot(self) -> dict[str, WorkerReadiness]:
        """Baseline readiness for the router: Hermes primary, OpenHands
        NOT_READY unless a containment proof explicitly marks it READY."""
        snapshot: dict[str, WorkerReadiness] = {}
        for framework in (WORKER_HERMES, WORKER_OPENHANDS):
            if framework in self._adapters:
                snapshot[framework] = WorkerReadiness(framework)
        return snapshot
