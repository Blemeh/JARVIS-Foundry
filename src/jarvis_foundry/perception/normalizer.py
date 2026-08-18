"""Source-specific records -> JARVIS semantic records (deterministic).

Only safe fields survive: application, redacted window, activity class,
provenance ids, and bounded non-secret fragments. Raw full-text and
accessibility trees are never carried forward.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .privacy import PrivacyPolicy, apply_policy

_IDLE_TRIGGERS = frozenset({"idle", "timed", "interval"})


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    application: str
    window: str
    activity_class: str
    captured_at: str
    source_ids: tuple[int, ...]
    content_hash: str
    text_fragments: tuple[str, ...] = ()
    redaction_state: str = "none"
    privacy_class: str = "public"


def _activity_class(event_type: str | None, capture_trigger: str | None) -> str:
    trigger = (capture_trigger or "").strip().lower()
    if trigger in _IDLE_TRIGGERS:
        return "idle"
    kind = (event_type or "").strip().lower()
    if kind == "app_switch":
        return "focus_change"
    if kind == "window_focus":
        return "window_focus"
    if kind == "visual_change":
        return "active_use"
    return "activity"


def normalize_records(rows: list[dict[str, Any]],
                      policy: PrivacyPolicy,
                      ) -> tuple[list[NormalizedRecord], dict[str, int]]:
    """Privacy firewall then normalization. Returns (records, counts) where
    counts = {"suppressed": int, "redacted": int, "fragments_dropped": int}."""
    records: list[NormalizedRecord] = []
    counts = {"suppressed": 0, "redacted": 0, "fragments_dropped": 0}
    for row in rows:
        outcome = apply_policy(row, policy)
        if outcome.suppressed:
            counts["suppressed"] += 1
            continue
        if outcome.redacted:
            counts["redacted"] += 1
        fragments = outcome.text_fragments
        counts["fragments_dropped"] += (
            len(list(row.get("text_fragments") or ())) - len(fragments))
        records.append(NormalizedRecord(
            application=outcome.application,
            window=outcome.window,
            activity_class=_activity_class(
                row.get("event_type"), row.get("capture_trigger")),
            captured_at=str(row.get("captured_at") or ""),
            source_ids=tuple(sorted(int(i) for i in (row.get("source_ids") or ()))),
            content_hash=str(row.get("content_hash") or ""),
            text_fragments=fragments,
            redaction_state="redacted" if outcome.redacted else "none",
            privacy_class=outcome.reason or "public",
        ))
    records.sort(key=lambda r: (r.captured_at, r.source_ids[:1] or (0,)))
    return records, counts
