"""Stable structural sequence fingerprints."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from ..events import canonical_json
from .contracts import FINGERPRINT_VERSION, WorkflowDiscoveryConfig


def fingerprint_for_sequence(sequence: Iterable[str],
                             config: WorkflowDiscoveryConfig) -> str:
    config.validate()
    normalized = tuple(str(item) for item in sequence)
    payload = {
        "algorithm_version": FINGERPRINT_VERSION,
        "configuration_hash": config.configuration_hash,
        "sequence": list(normalized),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
