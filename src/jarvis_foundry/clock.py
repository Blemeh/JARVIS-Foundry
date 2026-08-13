from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def add_seconds(value: str, seconds: int) -> str:
    """ISO-arithmetic used by the capability broker's TTL (deterministic
    given the same input)."""
    return isoformat(parse(value) + timedelta(seconds=seconds))

