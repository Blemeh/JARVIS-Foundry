"""Read-only owner-attention queue derived from briefing items and events."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .briefing_items import BriefingItem, project_briefing_items
from .database import FoundryDatabase
from .owner_directives import owner_directive_snapshot

QUEUE_VERSION = "1.0"


def _classification(item: BriefingItem, *, unknown_directive: bool = False) -> str:
    if unknown_directive:
        return "UNKNOWN"
    if item.owner_action_required in {
        "DECISION_REQUIRED",
        "BLOCKED_EXTERNAL_DEPENDENCY",
        "OWNER_VETO_ACTIVE",
        "UNKNOWN",
    }:
        return item.owner_action_required
    # Consequential informational items remain visible without being turned
    # into an approval request.
    if item.category in {"DECISION", "RELEASE", "COST"}:
        return "INFORM"
    return ""


def _consequence(classification: str, item: BriefingItem) -> str:
    if classification == "OWNER_VETO_ACTIVE":
        return "matching actions remain blocked until the owner revokes or supersedes the veto"
    if classification == "BLOCKED_EXTERNAL_DEPENDENCY":
        return "the selected work cannot materialize until the recorded dependency is resolved"
    if classification == "DECISION_REQUIRED":
        return "the institution has recorded a consequential change that needs owner attention"
    if classification == "UNKNOWN":
        return "the source is present but its authority or semantics are not trustworthy"
    return "the event is recorded for owner awareness; no approval is inferred"


def _options(event: Any) -> list[Any]:
    payload = getattr(event, "payload", {})
    raw = payload.get("options") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    return [value for value in raw[:5] if isinstance(value, (str, dict))]


def build_owner_attention_queue(
    database: FoundryDatabase,
    *,
    now: datetime | str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return open attention plus resolved/superseded history.

    No queue row is written.  Resolution is reconstructed from later durable
    events so a restart or projection rebuild cannot lose owner context.
    """
    events = list(database.events(readonly=True))
    event_by_id = {event.event_id: event for event in events}
    directive_status: dict[str, tuple[str, str]] = {}
    decision_status: dict[str, tuple[str, str]] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.event_type == "owner_directive.revoked":
            directive_id = payload.get("directive_id")
            if isinstance(directive_id, str):
                directive_status[directive_id] = ("RESOLVED", event.event_id)
        elif event.event_type == "owner_directive.superseded":
            directive_id = payload.get("directive_id")
            if isinstance(directive_id, str):
                directive_status[directive_id] = ("SUPERSEDED", event.event_id)
        elif event.event_type in {"executive.decision_materialized", "executive.decision_resolved"}:
            decision_id = payload.get("decision_id")
            if isinstance(decision_id, str):
                decision_status[decision_id] = ("RESOLVED", event.event_id)

    owner_snapshot = owner_directive_snapshot(database, now=now)
    unknown_ids = {
        str(value.get("directive_id"))
        for value in owner_snapshot.get("unknown", ())
        if isinstance(value, dict) and value.get("directive_id")
    }
    items = project_briefing_items(
        database,
        after_sequence=0,
        now=now,
        limit=max(1, int(limit) * 4),
    )
    records: list[dict[str, Any]] = []
    for item in items:
        event = event_by_id.get(item.source_event_id)
        event_payload = event.payload if event is not None and isinstance(event.payload, dict) else {}
        unknown_directive = bool(
            event is not None
            and event.event_type == "owner_directive.recorded"
            and str(event_payload.get("directive_id") or "") in unknown_ids
        )
        classification = _classification(item, unknown_directive=unknown_directive)
        if not classification:
            continue
        status = "OPEN"
        resolution_event_id: str | None = None
        if item.entity_kind == "directive" and item.entity_id in directive_status:
            status, resolution_event_id = directive_status[item.entity_id]
        elif item.entity_kind == "decision" and item.entity_id in decision_status:
            status, resolution_event_id = decision_status[item.entity_id]
        payload = event_payload
        record = {
            "queue_version": QUEUE_VERSION,
            "queue_id": f"attention:{item.dedupe_key}",
            "dedupe_key": item.dedupe_key,
            "status": status,
            "classification": classification,
            "summary": item.summary,
            "consequence": _consequence(classification, item),
            "options": _options(event) if event is not None else [],
            "item": item.to_dict(),
            "source_event_id": item.source_event_id,
            "source_event_type": item.source_event_type,
            "evidence_refs": list(item.evidence_refs),
            "entity_kind": item.entity_kind,
            "entity_id": item.entity_id,
            "mission_id": item.mission_id,
            "resolution_event_id": resolution_event_id,
            "opened_at": item.occurred_at,
            "metadata": dict(item.metadata),
            "payload_shape": sorted(payload.keys()) if isinstance(payload, dict) else [],
        }
        records.append(record)

    records.sort(key=lambda value: (
        value["status"] != "OPEN",
        -int(value["item"].get("importance") or 0),
        -int(value["item"].get("source_sequence") or 0),
        value["queue_id"],
    ))
    records = records[: max(1, int(limit))]
    open_records = [record for record in records if record["status"] == "OPEN"]
    return {
        "queue_version": QUEUE_VERSION,
        "open": open_records,
        "history": records,
        "open_count": len(open_records),
        "history_count": len(records),
        "counts_by_classification": {
            classification: sum(
                record["classification"] == classification
                for record in records
            )
            for classification in (
                "INFORM",
                "DECISION_REQUIRED",
                "BLOCKED_EXTERNAL_DEPENDENCY",
                "OWNER_VETO_ACTIVE",
                "UNKNOWN",
            )
        },
    }
