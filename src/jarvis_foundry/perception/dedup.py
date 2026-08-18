"""Deterministic deduplication.

Two layers:
1. record fingerprint - exact source identity (ids + captured_at + hash);
2. interval identity - the semantic observation key used by aggregation.
Repeated source records therefore never duplicate semantic truth, and a
crash-before-cursor-advance replay produces the SAME observation id and
payload (ledger idempotency dedupes it).
"""
from __future__ import annotations

import hashlib

from ..events import canonical_json
from .contracts import SOURCE_CONNECTOR_ID
from .normalizer import NormalizedRecord


def record_fingerprint(record: NormalizedRecord) -> str:
    seed = canonical_json({
        "connector": SOURCE_CONNECTOR_ID,
        "source_ids": list(record.source_ids),
        "captured_at": record.captured_at,
        "content_hash": record.content_hash,
    })
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class RecordDedup:
    """In-cycle guard against repeated source records."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_new(self, record: NormalizedRecord) -> bool:
        key = record_fingerprint(record)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True
