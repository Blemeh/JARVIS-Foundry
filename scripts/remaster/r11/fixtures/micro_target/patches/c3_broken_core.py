"""Candidate C3: a genuinely broken core change (must be rejected)."""

from __future__ import annotations


def split_tags(value: str) -> list[str]:
    return value.split(",")


def score(record: dict[str, float]) -> float:
    raw = float(record.get("score", 0.0))
    if raw < 0.0:
        raise ValueError("negative score is rejected")
    return raw
