"""Pipeline workflow behavior for the micro target."""

from __future__ import annotations

from typing import Any


def run_pipeline(records: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: float(record["score"]),
        reverse=True,
    )
