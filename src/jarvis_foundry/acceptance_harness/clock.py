"""Injectable clocks for logical and wall-time harness runs."""
from __future__ import annotations

import time
from datetime import UTC, datetime

from .contracts import ClockMode


class HarnessClock:
    def __init__(self, mode: ClockMode, *, duration_seconds: float, logical_start: float = 0.0, wall_elapsed_offset: float = 0.0) -> None:
        self.mode = mode
        self.duration_seconds = float(duration_seconds)
        self.logical_seconds = float(logical_start)
        self._wall_started = time.time()
        self._monotonic_started = time.monotonic()
        self._wall_elapsed_offset = max(0.0, float(wall_elapsed_offset))

    @property
    def wall_started_at(self) -> str:
        return datetime.fromtimestamp(self._wall_started, UTC).isoformat().replace("+00:00", "Z")

    @property
    def wall_elapsed_seconds(self) -> float:
        return self._wall_elapsed_offset + max(0.0, time.monotonic() - self._monotonic_started)

    def now(self) -> float:
        if self.mode is ClockMode.ACCELERATED_DISPOSABLE:
            return min(self.duration_seconds, self.logical_seconds)
        return min(self.duration_seconds, self.wall_elapsed_seconds)

    def advance_to(self, target_seconds: float) -> float:
        target = min(self.duration_seconds, max(self.now(), float(target_seconds)))
        if self.mode is ClockMode.ACCELERATED_DISPOSABLE:
            self.logical_seconds = target
            return target
        while self.now() < target:
            time.sleep(min(0.25, max(0.001, target - self.now())))
        return self.now()

    def advance_by(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        return self.advance_to(self.now() + seconds)

    def at_end(self) -> bool:
        return self.now() >= self.duration_seconds

    def timestamp(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
