"""V5-14 append-only safe connector attempt evidence."""

from __future__ import annotations

import json
from typing import Any

from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash
from .contract import ConnectorAttempt


class AttemptEvidenceError(ValueError):
    """Attempt evidence contains unsafe or oversized data."""


def record_attempt(
    database: FoundryDatabase,
    attempt: ConnectorAttempt,
    *,
    mission_id: str,
    resource_id: str,
    effect_ticket_id: str,
) -> dict[str, Any]:
    if not mission_id or not resource_id or not effect_ticket_id:
        raise AttemptEvidenceError("mission, resource, and effect ids are required")
    evidence = attempt.to_evidence()
    payload: dict[str, Any] = {
        "attempt_id": attempt.attempt_id,
        "mission_id": mission_id,
        "resource_id": resource_id,
        "effect_ticket_id": effect_ticket_id,
        **evidence,
    }
    encoded = canonical_json(payload)
    if len(encoded.encode("utf-8")) > 128 * 1024:
        raise AttemptEvidenceError("attempt evidence exceeds maximum size")
    if contains_secret_like(encoded):
        raise AttemptEvidenceError("secret-like attempt evidence is refused")
    database.append(Event.create(
        "connector.attempt_recorded", payload,
        source_type="supervisor", source_id="v5-connector-evidence",
        correlation_id=mission_id,
        idempotency_key=f"connector.attempt_recorded:{attempt.attempt_id}:{payload_hash(payload)}",
    ))
    return payload


def attempts_for_mission(database: FoundryDatabase, mission_id: str) -> list[dict[str, Any]]:
    if not mission_id or not mission_id.strip():
        raise AttemptEvidenceError("mission_id is required")
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'connector.attempt_recorded' AND json_extract(payload_json, "
            "'$.mission_id') = ? ORDER BY sequence",
            (mission_id,),
        ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row[0])
        latest[str(payload["attempt_id"])] = payload
    return sorted(latest.values(), key=lambda payload: str(payload["attempt_id"]))


__all__ = ["AttemptEvidenceError", "attempts_for_mission", "record_attempt"]
