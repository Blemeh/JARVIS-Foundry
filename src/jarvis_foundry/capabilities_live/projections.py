"""Deterministic projections over R9 capability lifecycle events."""

from __future__ import annotations

from typing import Any

from jarvis_foundry.capabilities_live.ledger_events import (
    ADAPTER_KINDS,
    CAPABILITY_KINDS,
    CREDENTIAL_KINDS,
)
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ports import ProjectionHandler
from jarvis_foundry.kernel.projections import ProjectionDispatcher, ProjectionRegistry


class CapabilityLifecycleProjectionHandler(ProjectionHandler[Any]):
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset(CAPABILITY_KINDS)

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        capability_id = str(event.payload["capability_id"])
        current = dict(state.get(capability_id, {}))
        timeline = list(current.get("timeline", []))
        timeline.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "sequence": event.sequence,
                "recorded_at": event.recorded_at,
            }
        )
        current.update(dict(event.payload))
        current["status"] = event.event_type.removeprefix("capability.").upper()
        current["timeline"] = timeline
        state[capability_id] = current
        return state


class AdapterProjectionHandler(ProjectionHandler[Any]):
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset(ADAPTER_KINDS)

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        capability_id = str(event.payload["capability_id"])
        operation = str(event.payload["operation"])
        idempotency_key = str(event.payload["idempotency_key"])
        key = f"{capability_id}:{operation}:{idempotency_key}"
        state[key] = dict(event.payload)
        return state


class CredentialProjectionHandler(ProjectionHandler[Any]):
    def initial_state(self) -> dict[str, Any]:
        return {}

    def version(self) -> int:
        return 1

    def event_types(self) -> frozenset[str]:
        return frozenset(CREDENTIAL_KINDS)

    def apply(self, state: dict[str, Any], event: LedgerEvent) -> dict[str, Any]:
        if event.event_type == "credential.registered":
            credential_id = str(event.payload["credential_id"])
            current = dict(state.get(credential_id, {}))
            current.update(dict(event.payload))
            current["status"] = "ACTIVE"
            state[credential_id] = current
        elif event.event_type == "credential.revoked":
            credential_id = str(event.payload["credential_id"])
            current = dict(state.get(credential_id, {}))
            current["status"] = "REVOKED"
            current["revoke_reason"] = str(event.payload["reason"])
            state[credential_id] = current
        else:
            key = f"{event.event_type}:{event.event_id}"
            state[key] = dict(event.payload)
        return state


def lifecycle_snapshot(events: list[LedgerEvent]) -> dict[str, Any]:
    dispatcher = ProjectionDispatcher(
        ProjectionRegistry(
            {
                "capabilities": CapabilityLifecycleProjectionHandler(),
                "adapters": AdapterProjectionHandler(),
                "credentials": CredentialProjectionHandler(),
            }
        )
    )
    snapshot = dispatcher.replay(events)
    return {
        "states": dict(snapshot.states),
        "versions": dict(snapshot.versions),
    }
