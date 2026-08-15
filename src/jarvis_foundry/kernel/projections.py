"""Versioned, registered, focused projection handlers with deterministic replay."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_bytes
from jarvis_foundry.kernel.errors import ProjectionError, ProjectionUnknownEventError
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ports import ProjectionHandler


class ProjectionRegistry:
    def __init__(self, handlers: Mapping[str, ProjectionHandler[Any]] | None = None) -> None:
        self._handlers: dict[str, ProjectionHandler[Any]] = dict(handlers or {})

    def register(self, name: str, handler: ProjectionHandler[Any]) -> None:
        self._handlers[name] = handler

    def handler(self, name: str) -> ProjectionHandler[Any]:
        handler = self._handlers.get(name)
        if handler is None:
            raise ProjectionError(f"no projection registered: {name}")
        return handler

    def names(self) -> set[str]:
        return set(self._handlers)

    def versions(self) -> dict[str, int]:
        return {name: handler.version() for name, handler in self._handlers.items()}

    def handlers(self) -> dict[str, ProjectionHandler[Any]]:
        return dict(self._handlers)


@dataclass(frozen=True)
class ProjectionSnapshot:
    states: Mapping[str, Any]
    versions: Mapping[str, int] = field(default_factory=dict)


def snapshot_bytes(snapshot: ProjectionSnapshot) -> bytes:
    return canonical_bytes({"versions": snapshot.versions, "states": snapshot.states})


class ProjectionDispatcher:
    """Replays events through every registered handler with exhaustive refusal."""

    def __init__(self, registry: ProjectionRegistry) -> None:
        self.registry = registry

    def replay(self, events: Iterable[LedgerEvent]) -> ProjectionSnapshot:
        handlers = self.registry.handlers()
        states: dict[str, Any] = {
            name: handler.initial_state() for name, handler in handlers.items()
        }
        for event in events:
            handled = False
            for name, handler in handlers.items():
                if event.event_type in handler.event_types():
                    states[name] = handler.apply(states[name], event)
                    handled = True
            if not handled:
                raise ProjectionUnknownEventError(None, event.event_type)
        return ProjectionSnapshot(states=states, versions=self.registry.versions())


class GoalProjectionHandler:
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"goal.created"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        goal_id = str(event.payload["goal_id"])
        state[goal_id] = dict(event.payload)
        return state


class MissionProjectionHandler:
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"mission.created"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        mission_id = str(event.payload["mission_id"])
        state[mission_id] = dict(event.payload)
        return state


class OwnerDirectiveProjectionHandler:
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"owner.directive.recorded"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        directive_id = str(event.payload["directive_id"])
        state[directive_id] = dict(event.payload)
        return state


class ReleaseProjectionHandler:
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"release.created", "release.transitioned"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        release_id = str(event.payload["release_id"])
        current = dict(state.get(release_id, {}))
        current.update(dict(event.payload))
        state[release_id] = current
        return state


class IncidentProjectionHandler:
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"incident.opened", "incident.updated"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        incident_id = str(event.payload["incident_id"])
        current = dict(state.get(incident_id, {}))
        current.update(dict(event.payload))
        state[incident_id] = current
        return state


class EffectProjectionHandler:
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"effect.intent", "effect.receipt"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        if event.event_type == "effect.intent":
            intent_id = str(event.payload["intent_id"])
            state[intent_id] = dict(event.payload)
        else:
            intent_id = str(event.payload["intent_id"])
            receipts = list(state.get(f"{intent_id}:receipts", []))
            receipts.append(dict(event.payload))
            state[f"{intent_id}:receipts"] = receipts
        return state


class SupervisorProjectionHandler:
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset({"supervisor.started", "supervisor.heartbeat", "supervisor.lease"})

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        if event.event_type == "supervisor.started":
            state[str(event.payload["instance_id"])] = dict(event.payload)
        else:
            key = f"{event.event_type}:{event.event_id}"
            state[key] = dict(event.payload)
        return state


DEFAULT_PROJECTION_HANDLERS: dict[str, ProjectionHandler[Any]] = {
    "goals": GoalProjectionHandler(),
    "missions": MissionProjectionHandler(),
    "owner_directives": OwnerDirectiveProjectionHandler(),
    "releases": ReleaseProjectionHandler(),
    "incidents": IncidentProjectionHandler(),
    "effects": EffectProjectionHandler(),
    "supervisor": SupervisorProjectionHandler(),
}
