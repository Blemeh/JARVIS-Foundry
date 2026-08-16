"""Honest capability lifecycle states and deterministic transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from jarvis_foundry.kernel.state_machines import transition_status


class CapabilityStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    PROBING = "PROBING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    NOT_READY = "NOT_READY"
    NOT_TESTED = "NOT_TESTED"
    DEGRADED = "DEGRADED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


CAPABILITY_TRANSITIONS: dict[CapabilityStatus, dict[str, CapabilityStatus]] = {
    CapabilityStatus.DISCOVERED: {
        "probe": CapabilityStatus.PROBING,
        "block": CapabilityStatus.BLOCKED,
        "not_ready": CapabilityStatus.NOT_READY,
        "not_tested": CapabilityStatus.NOT_TESTED,
    },
    CapabilityStatus.PROBING: {
        "ready": CapabilityStatus.READY,
        "block": CapabilityStatus.BLOCKED,
        "not_ready": CapabilityStatus.NOT_READY,
        "not_tested": CapabilityStatus.NOT_TESTED,
        "degrade": CapabilityStatus.DEGRADED,
    },
    CapabilityStatus.READY: {
        "activate": CapabilityStatus.ACTIVE,
        "suspend": CapabilityStatus.SUSPENDED,
        "degrade": CapabilityStatus.DEGRADED,
        "not_ready": CapabilityStatus.NOT_READY,
    },
    CapabilityStatus.ACTIVE: {
        "suspend": CapabilityStatus.SUSPENDED,
        "degrade": CapabilityStatus.DEGRADED,
        "revoke": CapabilityStatus.REVOKED,
    },
    CapabilityStatus.BLOCKED: {
        "probe": CapabilityStatus.PROBING,
        "not_ready": CapabilityStatus.NOT_READY,
        "not_tested": CapabilityStatus.NOT_TESTED,
    },
    CapabilityStatus.NOT_READY: {
        "probe": CapabilityStatus.PROBING,
        "block": CapabilityStatus.BLOCKED,
        "not_tested": CapabilityStatus.NOT_TESTED,
    },
    CapabilityStatus.NOT_TESTED: {
        "probe": CapabilityStatus.PROBING,
        "block": CapabilityStatus.BLOCKED,
        "not_ready": CapabilityStatus.NOT_READY,
    },
    CapabilityStatus.DEGRADED: {
        "ready": CapabilityStatus.READY,
        "suspend": CapabilityStatus.SUSPENDED,
        "block": CapabilityStatus.BLOCKED,
        "revoke": CapabilityStatus.REVOKED,
    },
    CapabilityStatus.SUSPENDED: {
        "ready": CapabilityStatus.READY,
        "degrade": CapabilityStatus.DEGRADED,
        "revoke": CapabilityStatus.REVOKED,
    },
    CapabilityStatus.REVOKED: {},
}


@dataclass(frozen=True, init=False)
class CapabilityMachine:
    """Typed deterministic state machine for the capability lifecycle."""

    status: CapabilityStatus = field(default=CapabilityStatus.DISCOVERED)

    def __init__(self, status: str | CapabilityStatus = "DISCOVERED") -> None:
        object.__setattr__(
            self,
            "status",
            status if isinstance(status, CapabilityStatus) else CapabilityStatus(status),
        )

    def transition(self, action: str) -> CapabilityMachine:
        return CapabilityMachine(transition_status(self.status, action, CAPABILITY_TRANSITIONS))

    def can(self, action: str) -> bool:
        return action in CAPABILITY_TRANSITIONS.get(self.status, {})

    def serialize(self) -> dict[str, str]:
        return {"status": self.status.value}

    @classmethod
    def deserialize(cls, payload: dict[str, str]) -> CapabilityMachine:
        return cls(payload["status"])
