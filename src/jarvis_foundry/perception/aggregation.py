"""High-volume source records -> meaningful activity intervals.

Deterministic grouping: consecutive records with the same application,
window and activity class belong to one interval while the gap stays within
``idle_floor_s``. Transitions are bounded. Output summaries are semantic
descriptions, never raw content.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..events import canonical_json
from .contracts import summary_for
from .normalizer import NormalizedRecord


@dataclass(frozen=True, slots=True)
class ActivityInterval:
    application: str
    resource: str
    activity_class: str
    captured_at: str
    interval_end: str
    source_ids: tuple[int, ...]
    transitions: tuple[str, ...]
    semantic_summary: str
    content_hash: str


def _content_hash(interval: ActivityInterval) -> str:
    seed = canonical_json({
        "application": interval.application,
        "resource": interval.resource,
        "activity_class": interval.activity_class,
        "captured_at": interval.captured_at,
        "interval_end": interval.interval_end,
        "source_ids": list(interval.source_ids),
    })
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def aggregate(records: list[NormalizedRecord], *,
              idle_floor_s: float = 30.0,
              max_transitions: int = 8,
              ) -> list[ActivityInterval]:
    """Group normalized records into bounded activity intervals."""
    ordered = sorted(
        records, key=lambda r: (r.captured_at, r.source_ids[:1] or (0,)))
    intervals: list[ActivityInterval] = []
    current: list[NormalizedRecord] = []
    previous_end: str | None = None

    def _flush() -> None:
        nonlocal current
        if not current:
            return
        first = current[0]
        last = current[-1]
        intervals.append(ActivityInterval(
            application=first.application,
            resource=first.window,
            activity_class=first.activity_class,
            captured_at=first.captured_at,
            interval_end=last.captured_at,
            source_ids=tuple(sorted(
                sid for r in current for sid in r.source_ids)),
            transitions=(),
            semantic_summary=summary_for(
                first.application, first.window, first.activity_class,
                count=len(current)),
            content_hash="",
        ))
        current = []

    for record in ordered:
        if current:
            first = current[0]
            same_context = (
                record.application == first.application
                and record.window == first.window
                and record.activity_class == first.activity_class)
            within_gap = _gap_within(previous_end, record.captured_at,
                                     idle_floor_s)
            if not (same_context and within_gap):
                _flush()
        current.append(record)
        previous_end = record.captured_at
    _flush()

    built: list[ActivityInterval] = []
    for index, interval in enumerate(intervals):
        if index == 0:
            transitions: tuple[str, ...] = ()
        else:
            prev = intervals[index - 1]
            raw: list[str] = []
            if prev.application != interval.application:
                raw.append(f"{prev.application} -> {interval.application}")
            elif prev.resource != interval.resource:
                raw.append(f"{prev.resource} -> {interval.resource}")
            transitions = tuple(raw[-(max_transitions - 1):] if raw else ())
        built.append(ActivityInterval(
            application=interval.application,
            resource=interval.resource,
            activity_class=interval.activity_class,
            captured_at=interval.captured_at,
            interval_end=interval.interval_end,
            source_ids=interval.source_ids,
            transitions=transitions,
            semantic_summary=interval.semantic_summary,
            content_hash="",
        ))
    for interval in built:
        object.__setattr__(
            interval, "content_hash",
            _content_hash(interval))
    return built


def _gap_within(previous: str | None, current: str,
                idle_floor_s: float) -> bool:
    if previous is None or not current:
        return False
    try:
        from ..clock import parse as parse_ts
        delta = (parse_ts(current) - parse_ts(previous)).total_seconds()
        return delta >= 0 and delta <= idle_floor_s
    except Exception:
        return False
