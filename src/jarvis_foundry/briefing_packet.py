"""Stable structured briefing packet and local export contract."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .briefings import BriefingPlanner, _period_key
from .command_center import build_snapshot
from .database import FoundryDatabase
from .decision_queue import build_owner_attention_queue


PACKET_SCHEMA = "v4.briefing-packet.v1"


def _clock(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _item_refs(item: dict[str, Any]) -> list[str]:
    refs = item.get("evidence_refs")
    if not isinstance(refs, list):
        return []
    return [str(ref) for ref in refs if isinstance(ref, (str, int, float))]


def build_briefing_packet(
    database: FoundryDatabase,
    *,
    kind: str = "TWICE_DAILY",
    period_key: str | None = None,
    now: datetime | None = None,
    limit: int = 50,
    stop_path: str | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic packet without writing an outbox or contacting a sink."""
    if kind not in {"TWICE_DAILY", "EXCEPTION", "MISSION_SUMMARY"}:
        raise ValueError("unsupported briefing packet kind")
    clock = _clock(now)
    period = period_key or (_period_key(clock) if kind == "TWICE_DAILY" else clock.isoformat())
    planner = BriefingPlanner(database, stop_path=stop_path)
    selection = planner._select_items(kind, period, now=clock, limit=max(1, min(int(limit), 200)))
    snapshot = snapshot or build_snapshot(database, stop_path=stop_path, now=clock.isoformat())
    items = list(selection.get("items") or [])
    attention = build_owner_attention_queue(database, now=clock.isoformat(), limit=100)
    attention_by_event = {
        str(item.get("source_event_id")): item
        for item in attention.get("open", [])
        if item.get("source_event_id")
    }
    decision_required: list[dict[str, Any]] = []
    source_refs: list[str] = []
    next_actions: list[dict[str, Any]] = []
    uncertainty: list[dict[str, Any]] = []
    for item in items:
        source_refs.extend(_item_refs(item))
        if item.get("owner_action_required") != "INFORM":
            queue_item = attention_by_event.get(str(item.get("source_event_id") or ""))
            decision_required.append(queue_item or {
                "item_id": item.get("item_id"),
                "classification": "UNKNOWN",
                "status": "OPEN",
                "summary": item.get("summary"),
                "evidence_refs": _item_refs(item),
            })
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        next_action = metadata.get("next_action") or item.get("summary")
        if next_action:
            next_actions.append({
                "item_id": item.get("item_id"),
                "action": str(next_action),
                "status": "EVIDENCED" if metadata.get("next_action") else "DERIVED",
                "source_event_id": item.get("source_event_id"),
            })
        freshness = str(item.get("freshness") or "UNKNOWN")
        if freshness in {"UNKNOWN", "FUTURE", "LATE"}:
            uncertainty.append({
                "item_id": item.get("item_id"),
                "kind": "freshness",
                "value": freshness,
                "reason": "event timing cannot be treated as current without an explicit clock/evidence match",
            })
    source_refs = list(dict.fromkeys(source_refs))
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    discovery = snapshot.get("discovery") if isinstance(snapshot.get("discovery"), dict) else {}
    operational = discovery.get("operational_intelligence") if isinstance(
        discovery.get("operational_intelligence"), dict
    ) else {}
    executive = snapshot.get("executive") if isinstance(snapshot.get("executive"), dict) else {}
    autonomy = executive.get("autonomy") if isinstance(executive.get("autonomy"), dict) else {}
    outcome_learning = autonomy.get("outcome_learning") if isinstance(
        autonomy.get("outcome_learning"), dict
    ) else {}
    # Operational follow-ups are a read-only extension of the packet. They
    # never become owner approvals or execution commands.
    for followup in operational.get("followups") or []:
        if not isinstance(followup, dict):
            continue
        source_refs.extend(_item_refs(followup))
        if followup.get("state") == "OPEN":
            decision_required.append({
                "follow_up_id": followup.get("follow_up_id"),
                "classification": followup.get("salience", "UNKNOWN"),
                "status": "OPEN",
                "summary": followup.get("summary"),
                "consequence": "follow-up is evidence-backed; execution remains with existing authorities",
                "evidence_refs": _item_refs(followup),
            })
            next_actions.append({
                "follow_up_id": followup.get("follow_up_id"),
                "action": followup.get("next_action", "UNKNOWN"),
                "status": "EVIDENCED",
                "source_event_ids": followup.get("source_event_ids", []),
            })
    source_refs = list(dict.fromkeys(source_refs))
    for unknown in (operational.get("context") or {}).get("unknowns") or []:
        if isinstance(unknown, dict):
            uncertainty.append({
                "kind": "operational_unknown",
                "subject": unknown.get("subject"),
                "reason": unknown.get("reason", "UNKNOWN"),
                "evidence_refs": unknown.get("evidence_refs", []),
            })
    external_status = "UNKNOWN" if not snapshot.get("source_of_truth") else "NOT_IMPLEMENTED"
    packet = {
        "schema": PACKET_SCHEMA,
        "packet_id": f"{kind}:{period}",
        "window": {"kind": kind, "period_key": period, "generated_at": snapshot.get("generated_at")},
        "items": items,
        # These additive signals let the owner see V5 terminal effect state
        # and V6 gate/evidence state in the same packet as V4 decisions.
        # They are read-only projections; neither grants authority.
        "effect_truth": dict(snapshot.get("effect_truth") or {}),
        "evolution_truth": dict(snapshot.get("evolution_truth") or {}),
        "operational_intelligence": dict(operational),
        "autonomy_briefing": dict(outcome_learning),
        "decision_required": decision_required,
        "source_refs": source_refs,
        "next_action_summary": next_actions,
        "uncertainty": uncertainty,
        "selection": selection,
        "delivery_eligibility": {
            "local_export": {"status": "ELIGIBLE", "format": "application/json"},
            "external_sinks": {"status": external_status, "reason": "connector delivery is outside V4 packet scope"},
            "runtime_health": health.get("supervisor_status", "UNKNOWN"),
        },
    }
    packet["content_digest"] = hashlib.sha256(_stable_json({
        "window": packet["window"], "items": items, "decision_required": decision_required,
        "source_refs": source_refs, "uncertainty": uncertainty,
        "operational_intelligence": operational,
        "autonomy_briefing": outcome_learning,
    }).encode("utf-8")).hexdigest()
    return packet


def serialize_briefing_packet(packet: dict[str, Any]) -> str:
    """Return canonical JSON suitable for a local file or future connector."""
    if packet.get("schema") != PACKET_SCHEMA:
        raise ValueError("unsupported briefing packet schema")
    return _stable_json(packet) + "\n"


def export_briefing_packet(packet: dict[str, Any], path: str | Path) -> Path:
    """Write only a caller-selected local path; no connector or scheduler is used."""
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(serialize_briefing_packet(packet), encoding="utf-8")
    return destination
