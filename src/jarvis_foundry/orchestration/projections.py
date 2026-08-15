"""Deterministic projections over R8 mission lifecycle ledger events."""

from __future__ import annotations

from typing import Any

from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ports import ProjectionHandler
from jarvis_foundry.kernel.projections import ProjectionDispatcher, ProjectionRegistry
from jarvis_foundry.orchestration.ledger_events import LIFECYCLE_KINDS


class MissionLifecycleProjectionHandler(ProjectionHandler[Any]):
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset(LIFECYCLE_KINDS)

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        mission_id = str(event.payload.get("mission_id") or event.payload.get("source_id"))
        current = dict(state.get(mission_id, {}))
        timeline = list(current.get("events", []))
        timeline.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "sequence": event.sequence,
                "recorded_at": event.recorded_at,
            }
        )
        current.update(dict(event.payload))
        current["status"] = event.event_type.removeprefix("mission.")
        current["events"] = timeline
        state[mission_id] = current
        return state


class LineageProjectionHandler(ProjectionHandler[Any]):
    def initial_state(self) -> dict[str, Any]:
        return {"candidates": {}, "decisions": {}}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"mission.dispatched", "mission.repair_dispatched", "mission.accepted"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        payload = dict(event.payload)
        candidate_id = str(payload.get("candidate_id", ""))
        decision_id = str(payload.get("decision_id", ""))
        mission_id = str(payload["mission_id"])
        if candidate_id:
            missions = list(state["candidates"].get(candidate_id, []))
            if mission_id not in missions:
                missions.append(mission_id)
            state["candidates"][candidate_id] = missions
        if decision_id:
            missions = list(state["decisions"].get(decision_id, []))
            if mission_id not in missions:
                missions.append(mission_id)
            state["decisions"][decision_id] = missions
        return state


def lifecycle_snapshot(events: list[LedgerEvent]) -> dict[str, Any]:
    dispatcher = ProjectionDispatcher(
        ProjectionRegistry(
            {
                "missions": MissionLifecycleProjectionHandler(),
                "lineage": LineageProjectionHandler(),
            }
        )
    )
    snapshot = dispatcher.replay(events)
    return {
        "states": dict(snapshot.states),
        "versions": dict(snapshot.versions),
    }
