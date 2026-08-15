"""Deterministic canonical serialization and hashing."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def canonical_bytes(value: Any) -> bytes:
    """Serialize any JSON-compatible value byte-identically."""
    normalized = _normalize(value)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        return sha256_bytes(value.encode("utf-8"))
    return sha256_bytes(value)


def canonical_hash(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))
