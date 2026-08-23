"""Deterministic, disposable V3 fault-injection hooks.

The hook is intentionally lane-local: it raises once at a named durable
transition and then removes that point so a replay of the same loop can
reconcile the partial ledger without a second injected crash.
"""
from __future__ import annotations

from collections.abc import Iterable


class V3InjectedFault(RuntimeError):
    """A deliberate local interruption at a named V3 transition."""

    def __init__(self, point: str) -> None:
        super().__init__(f"V3 fault injected at {point}")
        self.point = point


class FaultInjector:
    """One-shot transition injector suitable for a disposable test DB."""

    def __init__(self, points: Iterable[str] = ()) -> None:
        self.pending = {str(point) for point in points}
        self.triggered: list[str] = []

    def __call__(self, point: str) -> None:
        point = str(point)
        if point not in self.pending:
            return
        self.pending.remove(point)
        self.triggered.append(point)
        raise V3InjectedFault(point)


def invoke_fault_hook(hook, point: str) -> None:
    """Call an optional hook without imposing a production dependency."""
    if hook is not None:
        hook(point)
