"""V7-S8 — owner briefing + command surface.

Briefings are generated from the ledger truth (improvements, cycles,
promotions, decisions, unknowns, WAITING items, release slots, directives)
and recorded as durable events. Rendering supports English and Albanian.
Owner commands (STOP/pause/resume/reprieve) are recorded with provenance —
the owner's authority, never the supervisor's.
"""
from __future__ import annotations

import hashlib

import json
from typing import Any

from jarvis_foundry.clock import isoformat, utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event

BriefingError = RuntimeError

_LOCALES = ("en", "sq")

_SQ_HEAD = ("Përmbledhja e Supervizorit — JARVIS Foundry",)
_SQ_SECTIONS = {
    "improvements": "Përmirësimet",
    "cycles": "Ciklet",
    "decisions": "Vendimet",
    "unknowns": "Të panjohurat",
    "waiting": "Në pritje",
}


def _briefing_content(database: FoundryDatabase) -> str:
    """Deterministic ledger-derived content (the dedup basis)."""
    counts: dict[str, int] = {}
    for event in database.events():
        if event.event_type == "briefing.recorded":
            continue  # a briefing must never observe its own records
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    slots: dict[str, str | None] = {}
    from jarvis_foundry.evolution.registry import slot_pointers
    try:
        slots = slot_pointers(database)
    except Exception:
        slots = {}
    decisions = [e for e in database.events()
                 if e.event_type == "portfolio.decision"]
    unknowns = [e for e in database.events()
                if e.event_type == "portfolio.unknown"]
    cycles = [e for e in database.events()
              if e.event_type == "cycle.completed"]
    content = {
        "counts": counts, "slots": slots,
        "decisions": [e.payload for e in decisions],
        "unknowns": [e.payload for e in unknowns],
        "cycles": [e.payload.get("outcome") for e in cycles],
    }
    return json.dumps(content, sort_keys=True)


def generate_briefing(database: FoundryDatabase, *, locale: str,
                      source_type: str, source_id: str) -> str:
    if locale not in _LOCALES:
        raise BriefingError(f"unsupported locale: {locale}")
    content = _briefing_content(database)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    text = _render(database, locale)
    payload = {"locale": locale, "text": text, "content_hash": digest}
    event = Event.create(
        "briefing.recorded", payload,
        idempotency_key=f"briefing.recorded:{locale}:{digest}",
        source_type=source_type, source_id=source_id)
    database.append(event)
    return text


def _render(database: FoundryDatabase, locale: str) -> str:
    improvements = sum(
        1 for e in database.events() if e.event_type == "improvement.recorded")
    promotions = sum(
        1 for e in database.events() if e.event_type == "release.promoted")
    rejections = sum(
        1 for e in database.events() if e.event_type == "cycle.completed"
        and e.payload.get("outcome") == "rejected")
    unknowns = sum(
        1 for e in database.events() if e.event_type == "portfolio.unknown")
    waiting = sum(
        1 for e in database.events() if e.event_type == "scheduler.item_deferred")
    from jarvis_foundry.evolution.registry import slot_pointers
    try:
        slots = slot_pointers(database)
    except Exception:
        slots = {}
    if locale == "sq":
        return (
            f"Përmbledhja e Supervizorit — JARVIS Foundry\n"
            f"Përmirësimet: {improvements} · Promovimet: {promotions} · "
            f"Refuzimet: {rejections}\n"
            f"Të panjohurat: {unknowns} · Në pritje: {waiting}\n"
            f"Slots: live={slots.get('live')} candidate={slots.get('candidate')}")
    return (
        f"Supervisor briefing — JARVIS Foundry\n"
        f"Improvements: {improvements} · Promotions: {promotions} · "
        f"Rejections: {rejections}\n"
        f"Unknowns: {unknowns} · Waiting: {waiting}\n"
        f"Slots: live={slots.get('live')} candidate={slots.get('candidate')}")


def briefing_count(database: FoundryDatabase) -> int:
    return sum(1 for e in database.events()
               if e.event_type == "briefing.recorded")


def latest_briefing(database: FoundryDatabase) -> dict[str, Any]:
    briefings = [e for e in database.events()
                 if e.event_type == "briefing.recorded"]
    if not briefings:
        raise BriefingError("no briefings recorded")
    payload = dict(briefings[-1].payload)
    payload["recorded_at"] = briefings[-1].recorded_at
    return payload


def record_owner_command(database: FoundryDatabase, *, command_id: str,
                         command: str, reason: str,
                         source_type: str, source_id: str) -> str:
    payload = {"command_id": command_id, "command": command,
               "reason": reason, "source_type": source_type,
               "source_id": source_id, "recorded_at": isoformat()}
    event = Event.create(
        "owner.command_recorded", payload,
        idempotency_key=f"owner.command_recorded:{command_id}",
        source_type=source_type, source_id=source_id)
    return str(database.append(event)[0])


def owner_commands(database: FoundryDatabase) -> list[dict[str, Any]]:
    return [e.payload for e in database.events()
            if e.event_type == "owner.command_recorded"]
