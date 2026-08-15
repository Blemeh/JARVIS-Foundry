"""Additive R5 projection handlers used with the unmodified R4 dispatcher."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.projections import (
    DEFAULT_PROJECTION_HANDLERS,
    ProjectionRegistry,
)


class HistoricalLedgerProjectionHandler:
    """Deterministic aggregate state over the complete historical stream."""

    def __init__(self, event_types: Iterable[str]) -> None:
        self._event_types = frozenset(event_types)

    def initial_state(self) -> dict[str, Any]:
        return {
            "event_count": 0,
            "type_counts": {},
            "first_sequence": None,
            "last_sequence": None,
        }

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return self._event_types

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        type_counts = dict(state["type_counts"])
        type_counts[event.event_type] = type_counts.get(event.event_type, 0) + 1
        first = state["first_sequence"]
        if first is None and event.sequence is not None:
            first = event.sequence
        return {
            "event_count": int(state["event_count"]) + 1,
            "type_counts": type_counts,
            "first_sequence": first,
            "last_sequence": event.sequence,
        }


def build_projection_registry(
    event_types: Iterable[str],
    *,
    r4_handlers: dict[str, Any] | None = None,
) -> ProjectionRegistry:
    """R4 default handlers plus the additive historical aggregate handler."""
    handlers = dict(r4_handlers) if r4_handlers is not None else dict(DEFAULT_PROJECTION_HANDLERS)
    registry = ProjectionRegistry(handlers)
    registry.register(
        "legacy_history",
        HistoricalLedgerProjectionHandler(event_types=event_types),
    )
    return registry
