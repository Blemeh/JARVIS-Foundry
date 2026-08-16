"""Registered, replayable projections for the R11 lifecycle."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ports import ProjectionHandler
from jarvis_foundry.kernel.projections import (
    ProjectionDispatcher,
    ProjectionRegistry,
    ProjectionSnapshot,
)

CYCLE_EVENT_TYPES = frozenset(
    {
        "evolution_live.observed",
        "evolution_live.hypothesized",
        "evolution_live.candidate_isolated",
        "evolution_live.evaluated",
        "evolution_live.independently_verified",
        "evolution_live.shadowed",
        "evolution_live.canary_ready",
        "evolution_live.promoted",
        "evolution_live.rejected",
        "evolution_live.outcome_measured",
        "evolution_live.learned",
        "evolution_live.rolled_back",
        "evolution_live.restored",
    }
)


class CycleProjectionHandler(ProjectionHandler[dict[str, Any]]):
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return CYCLE_EVENT_TYPES

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        cycle_id = str(event.payload["cycle_id"])
        current = dict(state.get(cycle_id, {}))
        current.update(dict(event.payload))
        current["last_event"] = event.event_type
        current["occurred_at"] = event.occurred_at
        state[cycle_id] = current
        return state


class RefusalProjectionHandler(ProjectionHandler[dict[str, Any]]):
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"evolution_live.refused"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        probe_id = str(event.payload["probe_id"])
        state[probe_id] = dict(event.payload)
        state[probe_id]["event_id"] = event.event_id
        return state


class WaitingProjectionHandler(ProjectionHandler[dict[str, Any]]):
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"evolution_live.waiting"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        key = str(event.payload["cadence_key"])
        state[key] = dict(event.payload)
        state[key]["event_id"] = event.event_id
        return state


def r11_projection_registry() -> ProjectionRegistry:
    registry = ProjectionRegistry()
    registry.register("cycles", CycleProjectionHandler())
    registry.register("refusals", RefusalProjectionHandler())
    registry.register("waiting", WaitingProjectionHandler())
    return registry


def replay_snapshot(events: Iterable[LedgerEvent]) -> ProjectionSnapshot:
    dispatcher = ProjectionDispatcher(r11_projection_registry())
    return dispatcher.replay(events)
