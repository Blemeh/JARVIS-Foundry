"""Significance filter: only useful semantic activity reaches the ledger.

Deterministic and conservative: sub-threshold flicker intervals and short
idle runs are dropped so an unchanged screen cannot flood the ledger.
"""
from __future__ import annotations

from .aggregation import ActivityInterval


def is_significant(interval: ActivityInterval, *,
                   min_duration_s: float = 2.0,
                   unchanged_floor_s: float = 60.0,
                   ) -> tuple[bool, str]:
    duration = _duration_s(interval)
    if interval.activity_class == "idle":
        if duration < unchanged_floor_s:
            return False, "idle below unchanged floor"
        return True, "idle run above floor"
    if duration < min_duration_s:
        return False, "duration below minimum (flicker)"
    if not interval.transitions and not interval.source_ids:
        return False, "empty interval"
    return True, "significant"


def _duration_s(interval: ActivityInterval) -> float:
    try:
        from ..clock import parse as parse_ts
        return max(0.0, (parse_ts(interval.interval_end) -
                         parse_ts(interval.captured_at)).total_seconds())
    except Exception:
        return 0.0
