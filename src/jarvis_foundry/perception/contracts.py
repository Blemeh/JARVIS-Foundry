"""Frozen PerceptionObservation contract (Phase-1 contract section B).

The payload that reaches the append-only ledger carries ONLY semantic,
privacy-cleaned descriptions plus provenance pointers and hashes. It never
carries raw frame bytes, Screenpipe DB blobs, browser profile data, or full
UI trees. ``raw_external=True`` is a durable claim that the raw source
history lives outside Foundry.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from ..events import canonical_json

SOURCE_CONNECTOR_ID = "screenpipe-local"
SOURCE_KIND = "screenpipe-local"
SOURCE_VERSION = "0.4.40"

EVENT_OBSERVED = "perception.observed"
EVENT_REDACTED = "perception.redacted"
EVENT_SOURCE_DEGRADED = "perception.source_degraded"
EVENT_SOURCE_RECOVERED = "perception.source_recovered"

PRIVACY_PUBLIC = "public"
PRIVACY_SENSITIVE = "sensitive"
PRIVACY_EXCLUDED = "excluded"

_OBSERVATION_ID_RE = re.compile(r"^obs-[0-9a-f]{16}$")
_MAX_SUMMARY = 200


@dataclass(frozen=True, slots=True)
class PerceptionObservation:
    """One semantic, privacy-cleaned observation derived from the source."""

    observation_id: str
    source_connector_id: str
    source_pointer: dict[str, Any]
    captured_at: str
    interval_end: str
    recorded_at: str
    application: str
    resource: str
    activity_class: str
    semantic_summary: str
    transitions: tuple[str, ...] = ()
    content_hash: str = ""
    privacy_class: str = PRIVACY_PUBLIC
    redaction_state: str = "none"
    confidence: float = 1.0
    raw_external: bool = True
    source_version: str = SOURCE_VERSION
    retention_class: str = "bounded"
    source_metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not _OBSERVATION_ID_RE.match(self.observation_id):
            raise ValueError(
                f"observation_id must match obs-[0-9a-f]{{16}}, got "
                f"{self.observation_id!r}")
        if not self.application or not self.activity_class:
            raise ValueError("application and activity_class are required")
        if not self.captured_at or not self.interval_end:
            raise ValueError("captured_at and interval_end are required")
        if not self.source_pointer:
            raise ValueError("source_pointer is required (provenance)")
        if not self.content_hash:
            raise ValueError("content_hash is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        if self.raw_external is not True:
            raise ValueError("raw_external must be True (raw history stays "
                             "external)")


def observation_id_for(
    *, application: str, resource: str, activity_class: str,
    captured_at: str, interval_end: str,
) -> str:
    """Deterministic observation identity: the same source interval with the
    same semantic content ALWAYS maps to the same observation id, so replay
    after a crash-before-cursor-advance dedupes at the ledger."""
    seed = canonical_json({
        "connector": SOURCE_CONNECTOR_ID,
        "application": application,
        "resource": resource,
        "activity_class": activity_class,
        "captured_at": captured_at,
        "interval_end": interval_end,
    })
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"obs-{digest}"


def summary_for(application: str, resource: str, activity_class: str,
                *, count: int = 1) -> str:
    """Deterministic bounded semantic summary (no raw screen content)."""
    parts = [application]
    if resource:
        parts.append(resource)
    summary = " - ".join(parts) + f": {activity_class}"
    if count > 1:
        summary += f" ({count} records)"
    return summary[:_MAX_SUMMARY]


def payload_for(observation: PerceptionObservation) -> dict[str, Any]:
    """Canonical ledger payload. Never contains raw text or frame bytes."""
    return {
        "observation_id": observation.observation_id,
        "source_connector_id": observation.source_connector_id,
        "source_pointer": dict(observation.source_pointer),
        "captured_at": observation.captured_at,
        "interval_end": observation.interval_end,
        "recorded_at": observation.recorded_at,
        "application": observation.application,
        "resource": observation.resource,
        "activity_class": observation.activity_class,
        "semantic_summary": observation.semantic_summary,
        "transitions": list(observation.transitions),
        "content_hash": observation.content_hash,
        "privacy_class": observation.privacy_class,
        "redaction_state": observation.redaction_state,
        "confidence": observation.confidence,
        "raw_external": True,
        "source_version": observation.source_version,
        "retention_class": observation.retention_class,
        "source_metadata": dict(observation.source_metadata),
    }
