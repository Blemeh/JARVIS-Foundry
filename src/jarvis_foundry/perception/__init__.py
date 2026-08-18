"""JARVIS Eyes - Phase 2 of 6 (canonical reconciliation + perception).

Screenpipe is an EXTERNAL sidecar sensor. This package turns its high-volume
history into low-volume, privacy-clean, deterministic ``perception.observed``
ledger events. It is an INPUT capability: screen content is UNTRUSTED DATA
that never directly creates claims, candidates, missions, owner directives,
or computer control.
"""
from __future__ import annotations

from .contracts import (
    EVENT_OBSERVED,
    EVENT_REDACTED,
    EVENT_SOURCE_DEGRADED,
    EVENT_SOURCE_RECOVERED,
    SOURCE_CONNECTOR_ID,
    SOURCE_KIND,
    SOURCE_VERSION,
    PerceptionObservation,
    payload_for,
)
from .cursor import advance_cursor, read_cursor
from .privacy import PrivacyClass, PrivacyPolicy
from .run import PerceptionConfig, register_perception_connector, run_cycle

__all__ = [
    "EVENT_OBSERVED",
    "EVENT_REDACTED",
    "EVENT_SOURCE_DEGRADED",
    "EVENT_SOURCE_RECOVERED",
    "SOURCE_CONNECTOR_ID",
    "SOURCE_KIND",
    "SOURCE_VERSION",
    "PerceptionObservation",
    "PrivacyClass",
    "PrivacyPolicy",
    "PerceptionConfig",
    "advance_cursor",
    "payload_for",
    "read_cursor",
    "register_perception_connector",
    "run_cycle",
]
