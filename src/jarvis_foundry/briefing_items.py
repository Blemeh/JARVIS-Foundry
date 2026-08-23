"""Deterministic briefing-item projection from the append-only event ledger.

The item model is intentionally read-only.  It gives every owner-facing
briefing line a stable source event, dedupe identity, entity link, freshness,
and explicit owner-action classification without making a mutable UI table an
authority.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .database import FoundryDatabase
from .events import Event, payload_hash

ITEM_VERSION = "1.0"
DEFAULT_ITEM_LIMIT = 50

_SKIP_EVENT_TYPES = frozenset({
    "owner_control.nonce_used",
    "briefing.planned",
    "briefing.claimed",
    "briefing.delivered",
    "briefing.failed",
    "supervisor.heartbeat",
})


@dataclass(frozen=True, slots=True)
class BriefingItem:
    item_id: str
    dedupe_key: str
    item_version: str
    category: str
    importance: int
    freshness: str
    entity_kind: str | None
    entity_id: str | None
    mission_id: str | None
    source_event_id: str
    source_event_type: str
    source_sequence: int
    occurred_at: str
    summary: str
    owner_action_required: str
    evidence_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "dedupe_key": self.dedupe_key,
            "item_version": self.item_version,
            "category": self.category,
            "importance": self.importance,
            "freshness": self.freshness,
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "mission_id": self.mission_id,
            "source_event_id": self.source_event_id,
            "source_event_type": self.source_event_type,
            "source_sequence": self.source_sequence,
            "occurred_at": self.occurred_at,
            "summary": self.summary,
            "owner_action_required": self.owner_action_required,
            "evidence_refs": list(self.evidence_refs),
            "metadata": dict(self.metadata),
        }


def _now(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _freshness(occurred_at: str, now: datetime) -> str:
    try:
        occurred = datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return "UNKNOWN"
    if occurred > now:
        return "FUTURE"
    age_days = max(0.0, (now - occurred).total_seconds() / 86400.0)
    return "FRESH" if age_days <= 2.0 else "LATE"


def _text(value: Any, *, limit: int = 320) -> str:
    if value is None:
        return ""
    raw = str(value).replace("\x00", " ").strip()
    return raw if len(raw) <= limit else raw[: limit - 1] + "…"


def _payload_summary(event: Event) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for key in (
        "reason", "exact_text", "objective", "summary", "error",
        "failure_class", "next_action", "status", "question",
    ):
        value = _text(payload.get(key))
        if value:
            return value
    try:
        return _text(json.dumps(payload, sort_keys=True, ensure_ascii=True))
    except (TypeError, ValueError):
        return "event payload is not serializable"


def _entity(event: Event) -> tuple[str | None, str | None, str | None]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for key, kind in (
        ("mission_id", "mission"),
        ("decision_id", "decision"),
        ("directive_id", "directive"),
        ("candidate_id", "candidate"),
        ("run_id", "agent_run"),
        ("resource_id", "resource"),
        ("question_id", "question"),
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            mission_id = payload.get("mission_id")
            return kind, value.strip(), mission_id if isinstance(mission_id, str) else None
    return None, None, None


def _rule(event: Event) -> tuple[str, int, str]:
    event_type = event.event_type
    if event_type in {"owner_directive.recorded", "owner_directive.revoked", "owner_directive.superseded"}:
        command = str(event.payload.get("command_type") or "")
        if command in {"STOP_EVERYTHING", "PAUSE", "VETO_ACTION", "STOP_MISSION"}:
            return "OWNER_CONTROL", 5, "OWNER_VETO_ACTIVE"
        if event_type != "owner_directive.recorded":
            return "OWNER_CONTROL", 4, "INFORM"
        return "OWNER_CONTROL", 4, "DECISION_REQUIRED"
    if event_type in {"executive.materialization_blocked", "mission.blocked", "mission.blocked_recorded"}:
        return "BLOCKER", 5, "BLOCKED_EXTERNAL_DEPENDENCY"
    if event_type in {"supervisor.degraded", "worker.failed", "agent_run.failed", "mission.failed"}:
        return "EXCEPTION", 5, "DECISION_REQUIRED"
    if event_type == "executive.decision_recorded":
        decision = str(event.payload.get("decision") or "")
        return "DECISION", 4 if decision in {"SELECT", "REJECT", "DEFER"} else 3, "INFORM"
    if event_type.startswith("knowledge.") or event_type.startswith("memory."):
        return "KNOWLEDGE", 2, "INFORM"
    if event_type in {"resource.usage_recorded", "provider.usage_recorded"}:
        return "COST", 3, "INFORM"
    if event_type.startswith("mission.") or event_type.startswith("agent_run."):
        return "MISSION", 3, "INFORM"
    if event_type.startswith("release.") or event_type.startswith("rollback."):
        return "RELEASE", 4, "INFORM"
    if event_type.startswith("portfolio.") or event_type.startswith("discovery."):
        return "DISCOVERY", 2, "INFORM"
    return "EVENT", 1, "UNKNOWN"


def _evidence_refs(event: Event) -> tuple[str, ...]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    refs: list[str] = [event.event_id]
    for key in ("evidence_ref", "evidence_id", "last_event_id", "verdict_id"):
        value = payload.get(key)
        if isinstance(value, str) and value and value not in refs:
            refs.append(value)
    raw = payload.get("evidence_refs")
    if isinstance(raw, list):
        for value in raw:
            if isinstance(value, str) and value and value not in refs:
                refs.append(value)
    return tuple(refs[:12])


def event_to_item(event: Event, sequence: int, *, now: datetime | str | None = None) -> BriefingItem | None:
    """Convert one authoritative event to a bounded item, or skip noise."""
    if event.event_type in _SKIP_EVENT_TYPES:
        return None
    category, importance, action = _rule(event)
    entity_kind, entity_id, mission_id = _entity(event)
    payload = event.payload if isinstance(event.payload, dict) else {}
    target = _text(payload.get("target"))
    dedupe_key = f"{event.event_type}:{event.event_id}"
    item_id = f"brief-item-{payload_hash({'event_id': event.event_id, 'event_type': event.event_type})[:24]}"
    metadata = {
        "source_type": event.source_type,
        "source_id": event.source_id,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
    }
    if target:
        metadata["target"] = target
    return BriefingItem(
        item_id=item_id,
        dedupe_key=dedupe_key,
        item_version=ITEM_VERSION,
        category=category,
        importance=importance,
        freshness=_freshness(event.occurred_at, _now(now)),
        entity_kind=entity_kind,
        entity_id=entity_id,
        mission_id=mission_id,
        source_event_id=event.event_id,
        source_event_type=event.event_type,
        source_sequence=sequence,
        occurred_at=event.occurred_at,
        summary=_payload_summary(event),
        owner_action_required=action,
        evidence_refs=_evidence_refs(event),
        metadata=metadata,
    )


def project_briefing_items(
    database: FoundryDatabase,
    *,
    after_sequence: int = 0,
    now: datetime | str | None = None,
    limit: int = DEFAULT_ITEM_LIMIT,
) -> list[BriefingItem]:
    """Project deterministic items after a ledger cursor in ledger order."""
    items: list[BriefingItem] = []
    for sequence, event in enumerate(database.events(readonly=True), start=1):
        if sequence <= max(0, int(after_sequence)):
            continue
        item = event_to_item(event, sequence, now=now)
        if item is not None:
            items.append(item)
    # Priority is a presentation decision; source sequence remains the stable
    # cursor and is retained in each item for replay/debugging.
    items.sort(key=lambda item: (-item.importance, item.source_sequence, item.item_id))
    return items[: max(1, int(limit))]


def exception_item(item: BriefingItem) -> bool:
    return item.owner_action_required != "INFORM" or item.importance >= 4


def briefing_item_keys(items: Iterable[dict[str, Any] | BriefingItem]) -> set[str]:
    keys: set[str] = set()
    for item in items:
        value = item.to_dict() if isinstance(item, BriefingItem) else item
        key = value.get("dedupe_key") if isinstance(value, dict) else None
        if isinstance(key, str) and key:
            keys.add(key)
    return keys
