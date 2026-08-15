"""Small deterministic time helpers used by durable kernel stores."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def add_seconds_to_iso(iso: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(iso)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (
        (parsed + timedelta(seconds=seconds))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def iso_compare(left: str, right: str) -> int:
    left_value = datetime.fromisoformat(left)
    right_value = datetime.fromisoformat(right)
    if left_value == right_value:
        return 0
    return -1 if left_value < right_value else 1
