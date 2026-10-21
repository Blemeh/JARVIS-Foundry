"""Read-only owner-attention queue derived from briefing items and events."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .briefing_items import BriefingItem, event_to_item, project_briefing_items
from .database import FoundryDatabase
from .owner_directives import owner_directive_snapshot

QUEUE_VERSION = "1.0"
_TERMINAL_ATTENTION_STATES = frozenset({
    "RESOLVED",
    "SUPERSEDED",
    "REVOKED",
})
_CHIEF_ATTENTION_EVENT_TYPES = (
    "chief_of_staff.owner_attention_required",
    "chief_of_staff.item_state_changed",
)
#: How many of the newest ledger events feed the windowed part of the queue.
_RECENT_SCAN_LIMIT = 10000
#: Owner-facing items whose *state* (not age) decides whether they are open. They
#: are read regardless of the window so a quiet owner never loses them as the
#: ledger grows, and they are resolved from durable state below.
_ESCALATION_EVENT_TYPES = ("executive.escalation_opened", "executive.escalation_resolved")
_DELEGATION_ATTENTION_EVENT_TYPES = (
    "delegation.clarification_requested",
    "delegation.intent_blocked",
)
_STATEFUL_EVENT_TYPES = frozenset((*_ESCALATION_EVENT_TYPES, *_DELEGATION_ATTENTION_EVENT_TYPES))
_OPEN_DELEGATION_STATUSES = frozenset({"AWAITING_CLARIFICATION", "BLOCKED"})
#: An escalation asks the owner to unblock an objective; once that objective has ended
#: (or been suspended) the question is moot and must not keep nagging.
_MOOT_OBJECTIVE_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "SUSPENDED"})


def _classification(
    item: BriefingItem, *, unknown_directive: bool = False, wellformed_payload: bool = True,
) -> str:
    if unknown_directive:
        return "UNKNOWN"
    if wellformed_payload and item.category == "EVENT" and item.owner_action_required == "UNKNOWN":
        # The catch-all for well-formed event types nobody classified. It carries no owner action;
        # queueing it turned every new internal event type into owner attention (the
        # live queue was 100 of these). Real uncertainty (effects, evolution) and malformed
        # payloads / directives still fail honestly as UNKNOWN and surface.
        return ""
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


def _stateful_attention_items(
    database: FoundryDatabase,
    *,
    now: datetime | str | None,
) -> tuple[list[BriefingItem], list[Any], dict[str, str]]:
    """Owner-facing items decided by durable state, independent of ledger age.

    * escalations: every ``executive.escalation_opened`` ever recorded, closed by a
      later ``executive.escalation_resolved`` (returned as ``escalation_id -> event``);
    * delegation: only the *latest* clarification/block of each intent, and only
      while the intent is still AWAITING_CLARIFICATION or BLOCKED. A stalled intent
      that re-evaluates every cycle is one owner item, not thousands, and it
      disappears when the intent is admitted, completed or superseded.
    """
    from .delegation.projection import project_delegation_intents

    rows = database.events_with_sequences(
        readonly=True,
        event_types=(*_ESCALATION_EVENT_TYPES, *_DELEGATION_ATTENTION_EVENT_TYPES),
    )
    resolved: dict[str, str] = {}
    latest_for_intent: dict[str, tuple[int, Any]] = {}
    opened: list[tuple[int, Any]] = []
    for sequence, event in rows:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.event_type == "executive.escalation_resolved":
            escalation_id = payload.get("escalation_id")
            if isinstance(escalation_id, str) and escalation_id:
                resolved[escalation_id] = event.event_id
        elif event.event_type == "executive.escalation_opened":
            opened.append((sequence, event))
        else:
            intent_id = payload.get("intent_id")
            if isinstance(intent_id, str) and intent_id:
                latest_for_intent[intent_id] = (sequence, event)  # ascending: last wins

    statuses: dict[str, str] | None
    try:
        statuses = {
            intent.intent_id: getattr(intent.status, "value", str(intent.status))
            for intent in project_delegation_intents(database).intents
        }
    except Exception:  # noqa: BLE001 - never hide an owner item because a projection failed
        statuses = None  # conservative: show each intent's latest block/clarification

    candidates = list(opened)
    candidates += [
        pair for intent_id, pair in latest_for_intent.items()
        if statuses is None or statuses.get(intent_id) in _OPEN_DELEGATION_STATUSES
    ]
    items: list[BriefingItem] = []
    events: list[Any] = []
    for sequence, event in candidates:
        item = event_to_item(event, sequence, now=now)
        if item is not None:
            items.append(item)
            events.append(event)
    return items, events, resolved

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
    events = list(
        reversed(
            database.events(
                readonly=True,
                limit=_RECENT_SCAN_LIMIT,
                newest_first=True,
            )
        )
    )
    chief_attention_events = database.events(
        readonly=True,
        event_types=_CHIEF_ATTENTION_EVENT_TYPES,
    )
    event_by_id = {event.event_id: event for event in events}
    event_by_id.update({
        event.event_id: event
        for event in chief_attention_events
    })
    directive_status: dict[str, tuple[str, str]] = {}
    decision_status: dict[str, tuple[str, str]] = {}
    attention_status: dict[str, tuple[str, str]] = {}
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
    for event in chief_attention_events:
        if event.event_type == "chief_of_staff.item_state_changed":
            payload = event.payload if isinstance(event.payload, dict) else {}
            item_id = payload.get("item_id")
            to_state = str(payload.get("to_state") or "").upper()
            if (
                str(payload.get("item_kind") or "").upper()
                == "OWNER_ATTENTION"
                and isinstance(item_id, str)
                and item_id
                and to_state in _TERMINAL_ATTENTION_STATES
            ):
                attention_status[item_id] = (to_state, event.event_id)

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
        max_scan=_RECENT_SCAN_LIMIT,
    )
    # Escalations and delegation blocks/clarifications are stateful: handled below
    # from durable state instead of one item per (possibly repeated) event.
    items = [item for item in items if item.source_event_type not in _STATEFUL_EVENT_TYPES]
    source_event_ids = {
        item.source_event_id
        for item in items
    }
    for chief_sequence, event in enumerate(chief_attention_events, start=1):
        if (
            event.event_type != "chief_of_staff.owner_attention_required"
            or event.event_id in source_event_ids
        ):
            continue
        item = event_to_item(event, chief_sequence, now=now)
        if item is not None:
            items.append(item)
            source_event_ids.add(event.event_id)
    stateful_items, stateful_events, escalation_resolved = _stateful_attention_items(database, now=now)
    for event in stateful_events:
        event_by_id.setdefault(event.event_id, event)
    for stateful in stateful_items:
        if stateful.source_event_id not in source_event_ids:
            items.append(stateful)
            source_event_ids.add(stateful.source_event_id)
    objective_status: dict[str, str] = {}
    if any(item.source_event_type == "executive.escalation_opened" for item in items):
        # Replay the objective events (a few hundred) instead of a full Executive
        # rebuild (~0.6 s per queue build, and the queue is built twice per Command
        # Center snapshot). Status-bearing events (created/updated/status_changed/
        # progress_recorded) replayed in ledger order reproduce the Executive's own
        # objective status exactly (verified 43/43 on the live ledger).
        for event in database.events(
            readonly=True, event_type_prefixes=("executive.objective",),
        ):
            payload = event.payload if isinstance(event.payload, dict) else {}
            objective_id, status = payload.get("objective_id"), payload.get("status")
            if isinstance(objective_id, str) and isinstance(status, str) and status:
                objective_status[objective_id] = status
    cycle_rows = database.events_with_sequences(
        readonly=True, event_types=("worker_loop.cycle_started",), newest_first=True, limit=1,
    )
    latest_cycle_sequence = cycle_rows[0][0] if cycle_rows else 0
    records: list[dict[str, Any]] = []
    for item in items:
        event = event_by_id.get(item.source_event_id)
        event_payload = event.payload if event is not None and isinstance(event.payload, dict) else {}
        unknown_directive = bool(
            event is not None
            and event.event_type == "owner_directive.recorded"
            and str(event_payload.get("directive_id") or "") in unknown_ids
        )
        classification = _classification(
            item,
            unknown_directive=unknown_directive,
            wellformed_payload=bool(event is not None and isinstance(event.payload, dict)),
        )
        if not classification:
            continue
        status = "OPEN"
        resolution_event_id: str | None = None
        if item.entity_kind == "directive" and item.entity_id in directive_status:
            status, resolution_event_id = directive_status[item.entity_id]
        elif item.entity_kind == "decision" and item.entity_id in decision_status:
            status, resolution_event_id = decision_status[item.entity_id]
        elif (
            event is not None
            and event.event_type == "worker_loop.activation_blocked"
            and latest_cycle_sequence > int(item.source_sequence or 0)
        ):
            # A later worker cycle started: activation recovered by itself.
            status, resolution_event_id = "RESOLVED", None
        elif (
            event is not None
            and event.event_type == "executive.escalation_opened"
            and str(event_payload.get("escalation_id") or "") in escalation_resolved
        ):
            status, resolution_event_id = (
                "RESOLVED", escalation_resolved[str(event_payload.get("escalation_id"))],
            )
        elif (
            event is not None
            and event.event_type == "executive.escalation_opened"
            and objective_status.get(str(event_payload.get("objective_id") or "")) in _MOOT_OBJECTIVE_STATUSES
        ):
            status, resolution_event_id = "SUPERSEDED", None
        elif (
            event is not None
            and event.event_type == "chief_of_staff.owner_attention_required"
        ):
            attention_id = event_payload.get("attention_id")
            if isinstance(attention_id, str) and attention_id in attention_status:
                status, resolution_event_id = attention_status[attention_id]
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
