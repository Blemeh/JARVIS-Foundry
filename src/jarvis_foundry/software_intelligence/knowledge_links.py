"""Thin governed wrapper over the canonical knowledge evidence link.

The projection owns the rule: a link flips a claim from UNSUPPORTED to
SUPPORTED and is immutable (no unlink event exists). This wrapper adds the
locator provenance JARVIS software research needs without new event types.
"""
from __future__ import annotations

from typing import Any

from ..database import FoundryDatabase
from ..events import Event


def link_evidence(db: FoundryDatabase, *, claim_id: str,
                  evidence_id: str,
                  locator: str | None = None,
                  payload_hash_text: str | None = None) -> None:
    """Bind one immutable claim<->evidence link (canonical event type)."""
    payload: dict[str, Any] = {"claim_id": claim_id,
                               "evidence_id": evidence_id}
    if payload_hash_text:
        payload["payload_hash"] = payload_hash_text
    if locator:
        import hashlib
        digest = hashlib.sha256(locator.encode("utf-8")).hexdigest()[:16]
        existing = payload.get("payload_hash") or ""
        payload["payload_hash"] = f"{existing}:{digest}" if existing else digest
    db.append(Event.create(
        "knowledge.evidence_linked",
        payload,
        source_type="supervisor", source_id="software-intelligence",
        idempotency_key=(
            "knowledge.evidence_linked:"
            f"{claim_id}:{evidence_id}")))


__all__ = ["link_evidence"]
