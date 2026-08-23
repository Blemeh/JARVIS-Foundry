"""V5-10 exact cross-system connector/effect lineage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash


class LineageError(ValueError):
    """A lineage identity is invalid or an exact lookup is ambiguous."""


_OUTCOMES = frozenset({"COMPLETED", "FAILED", "UNKNOWN", "CANCELLED", "COMPENSATED"})


@dataclass(frozen=True, slots=True)
class LineageRecord:
    lineage_id: str
    resource_id: str
    mission_id: str
    connector_request_id: str
    effect_ticket_id: str
    external_object_id: str | None
    evidence_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    outcome: str
    confidence: float
    ambiguity_reason: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "lineage_id": self.lineage_id,
            "resource_id": self.resource_id,
            "mission_id": self.mission_id,
            "connector_request_id": self.connector_request_id,
            "effect_ticket_id": self.effect_ticket_id,
            "external_object_id": self.external_object_id,
            "evidence_refs": list(self.evidence_refs),
            "source_refs": list(self.source_refs),
            "outcome": self.outcome,
            "confidence": self.confidence,
            "ambiguity_reason": self.ambiguity_reason,
        }


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LineageError(f"{name} must be a non-empty exact identifier")
    return value


def _lineage_id(resource_id: str, mission_id: str, request_id: str, effect_id: str) -> str:
    core = canonical_json({
        "resource_id": resource_id, "mission_id": mission_id,
        "connector_request_id": request_id, "effect_ticket_id": effect_id,
    })
    return f"lineage-{hashlib.sha256(core.encode('utf-8')).hexdigest()[:24]}"


def _refs(values: Iterable[str], name: str) -> tuple[str, ...]:
    result = tuple(_required(value, name) for value in values)
    if len(set(result)) != len(result):
        raise LineageError(f"{name} must not contain duplicates")
    return result


def record_lineage(
    database: FoundryDatabase,
    *,
    resource_id: str,
    mission_id: str,
    connector_request_id: str,
    effect_ticket_id: str,
    external_object_id: str | None,
    evidence_refs: Iterable[str],
    outcome: str,
    source_refs: Iterable[str] = (),
    confidence: float = 1.0,
    ambiguity_reason: str | None = None,
) -> LineageRecord:
    resource_id = _required(resource_id, "resource_id")
    mission_id = _required(mission_id, "mission_id")
    connector_request_id = _required(connector_request_id, "connector_request_id")
    effect_ticket_id = _required(effect_ticket_id, "effect_ticket_id")
    if external_object_id is not None:
        external_object_id = _required(external_object_id, "external_object_id")
    if outcome not in _OUTCOMES:
        raise LineageError(f"unsupported lineage outcome: {outcome!r}")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.0 <= float(confidence) <= 1.0:
        raise LineageError("lineage confidence must be between 0 and 1")
    effective_outcome = outcome
    effective_reason = ambiguity_reason
    effective_confidence = float(confidence)
    if outcome == "COMPLETED" and external_object_id is None:
        effective_outcome = "UNKNOWN"
        effective_reason = "missing_provider_id"
        effective_confidence = 0.0
    record = LineageRecord(
        lineage_id=_lineage_id(resource_id, mission_id, connector_request_id, effect_ticket_id),
        resource_id=resource_id, mission_id=mission_id,
        connector_request_id=connector_request_id, effect_ticket_id=effect_ticket_id,
        external_object_id=external_object_id,
        evidence_refs=_refs(evidence_refs, "evidence_ref"),
        source_refs=_refs(source_refs, "source_ref"),
        outcome=effective_outcome, confidence=effective_confidence,
        ambiguity_reason=effective_reason,
    )
    payload = record.payload()
    database.append(Event.create(
        "correlation.lineage_recorded", payload,
        source_type="supervisor", source_id="v5-correlation",
        correlation_id=record.lineage_id,
        idempotency_key=f"correlation.lineage_recorded:{record.lineage_id}:{payload_hash(payload)}",
    ))
    return record


def _latest(database: FoundryDatabase) -> dict[str, dict[str, Any]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'correlation.lineage_recorded' ORDER BY sequence"
        ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row[0])
        latest[str(payload["lineage_id"])] = payload
    return latest


def lineage_for_mission(database: FoundryDatabase, mission_id: str) -> list[dict[str, Any]]:
    mission_id = _required(mission_id, "mission_id")
    records = [payload for payload in _latest(database).values()
               if payload.get("mission_id") == mission_id]
    return sorted(records, key=lambda payload: str(payload["lineage_id"]))


def find_external_object(database: FoundryDatabase, external_object_id: str) -> dict[str, Any] | None:
    external_object_id = _required(external_object_id, "external_object_id")
    matches = [payload for payload in _latest(database).values()
               if payload.get("external_object_id") == external_object_id]
    if len(matches) > 1:
        raise LineageError(f"external object {external_object_id!r} is ambiguous")
    return matches[0] if matches else None


__all__ = ["LineageError", "LineageRecord", "find_external_object", "lineage_for_mission", "record_lineage"]
