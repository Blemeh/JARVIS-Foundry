"""Core parsing and scoring behavior for the micro target."""

from __future__ import annotations

from config import values as config


def split_tags(value: str) -> list[str]:
    parts = value.split(",")
    if config.DROP_EMPTY:
        parts = [part for part in parts if part]
    if config.STRIP_TOKENS:
        parts = [part.strip() for part in parts]
    return parts


def score(record: dict[str, float]) -> float:
    raw = float(record.get("score", 0.0))
    if config.STRICT_MODE:
        raw = min(raw, config.FEATURE_THRESHOLD)
    if config.CLAMP_NON_NEGATIVE:
        raw = max(0.0, raw)
    return raw
