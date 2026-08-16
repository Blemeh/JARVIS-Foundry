"""Honest unavailable connectors: recorded, never blocking other gates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnavailableConnector:
    capability_id: str
    domain: str
    name: str
    status: str
    reason: str


UNAVAILABLE_CONNECTORS: tuple[UnavailableConnector, ...] = (
    UnavailableConnector(
        capability_id="capability.browser",
        domain="browser",
        name="Browser automation",
        status="NOT_TESTED",
        reason="no configured browser driver and network is disabled",
    ),
    UnavailableConnector(
        capability_id="capability.communication",
        domain="communication",
        name="Communication delivery",
        status="BLOCKED",
        reason="no real configured provider and network is disabled",
    ),
    UnavailableConnector(
        capability_id="capability.calendar",
        domain="calendar",
        name="Calendar write",
        status="BLOCKED",
        reason="no real configured provider and network is disabled",
    ),
)
