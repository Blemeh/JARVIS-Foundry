"""WP-24H-V2KB (V2 Knowledge Brain): typed epistemic records.

Bounded deterministic helpers for the six canonical record kinds
(Observation/Evidence/Claim/Belief/Decision/Unknown) and the evidence
status rules. No LLM inference; string/row logic only. Import-safe leaf:
no FoundryDatabase imports here (the projector imports this module).
"""
from __future__ import annotations

import hashlib

RECORD_KINDS = ("OBSERVATION", "EVIDENCE", "CLAIM", "BELIEF", "DECISION",
                "UNKNOWN")
RECORD_KIND_DEFAULT = "CLAIM"

ENTITY_TYPES = ("goal", "project", "person", "organization", "system")
ENTITY_TYPE_DEFAULT = "system"

EVIDENCE_STATUSES = ("SUPPORTED", "UNSUPPORTED")

# Roadmap exit rule: every durable claim links to evidence or is marked
# unsupported. New claims START unsupported and flip to SUPPORTED only via
# an immutable knowledge.evidence_linked event.
UNSUPPORTED_REASON_DEFAULT = "no evidence link recorded"


def content_hash(*, statement: str, claim_type: str, mission_id: str,
                 claim_id: str) -> str:
    """Deterministic sha256 over the record's identifying content."""
    payload = {
        "claim_id": claim_id,
        "claim_type": claim_type,
        "mission_id": mission_id,
        "statement": statement,
    }
    return hashlib.sha256(
        json_dumps(payload).encode("utf-8")).hexdigest()


def json_dumps(payload: dict) -> str:
    import json
    return json.dumps(payload, sort_keys=True)


def validate_record_kind(kind: str) -> str:
    if kind not in RECORD_KINDS:
        raise ValueError(
            f"invalid record_kind {kind!r}; expected one of "
            f"{', '.join(RECORD_KINDS)}")
    return kind


def validate_entity_type(entity_type: str) -> str:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(
            f"invalid entity_type {entity_type!r}; expected one of "
            f"{', '.join(ENTITY_TYPES)}")
    return entity_type
