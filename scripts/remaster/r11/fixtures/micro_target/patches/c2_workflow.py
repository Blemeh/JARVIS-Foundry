"""Candidate C2: real pipeline fixes (R2 workflow surface)."""

from __future__ import annotations

from typing import Any


def run_pipeline(records: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    cleaned = [record for record in records if record is not None]
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, float]] = set()
    for record in cleaned:
        key = (int(record["id"]), float(record["score"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return sorted(unique, key=lambda record: float(record["score"]), reverse=True)
