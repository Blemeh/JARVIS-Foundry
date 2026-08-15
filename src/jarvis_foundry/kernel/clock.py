"""Injectable deterministic clocks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class Clock:
    """Port: current UTC timestamp in stable Zulu form."""

    def now_utc(self) -> str:
        raise NotImplementedError


class SystemClock(Clock):
    def now_utc(self) -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FixedClock(Clock):
    def __init__(self, iso: str) -> None:
        self._value = datetime.fromisoformat(iso)

    def now_utc(self) -> str:
        return self._value.isoformat(timespec="seconds").replace("+00:00", "Z")

    def advance(self, *, seconds: int) -> None:
        self._value = self._value + timedelta(seconds=seconds)
