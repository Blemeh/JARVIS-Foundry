"""Research evidence contract over the EXISTING knowledge machinery.

This module is the one governed interface between untrusted external
content (GitHub API documents, documentation pages, search results) and
JARVIS's epistemic store. It introduces NO new database, registry, or
scorer: claims are recorded through the canonical
``knowledge.claim_recorded`` event, evidence links through the canonical
append-only ``knowledge.evidence_linked`` event, and contradiction
detection stays exactly the deterministic projection logic that already
exists.

Epistemic kinds map onto ``claim_type`` values:

- ``observation``  — something directly encountered via a connector read.
- ``evidence``     — a preserved source artifact with provenance.
- ``claim``        — a proposition derived from observations/evidence.
- ``unknown``      — an honest open question with information value.
- ``conflict``     — two sources disagree; both remain visible.

DATA_ONLY rule: content retrieved from the outside world is data. It is
hashed and recorded, but it can never become an owner directive, veto,
STOP mutation, capability expansion, or scope change. ``classify_content``
detects control-shaped language so callers can label it; the recording
functions refuse to elevate any content into a directive event type —
there is no code path here that appends anything other than knowledge
and research events.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .database import FoundryDatabase
from .events import Event

CLAIM_KINDS = frozenset(
    {"observation", "evidence", "claim", "unknown", "conflict"})

# Control-shaped phrases that must never be treated as instructions when
# they appear in retrieved content. Detection is lexical and bounded;
# the structural protection is that no retrieval path can append
# directive/veto/stop events at all.
_CONTROL_MARKERS = (
    "ignore previous", "ignore all previous", "disregard the owner",
    "ignore the owner", "disable stop", "disable emergency",
    "push directly to main", "merge to main", "force push",
    "reveal the token", "expose the token", "new owner directive",
    "cancel the veto", "you are now", "act as owner",
    "run this unrelated command", "as the maintainer",
    "use this token instead", "redirect the mission",
)


class ResearchContractError(ValueError):
    """A research record violates the evidence contract."""


def classify_content(text: str) -> dict[str, Any]:
    """Lexically flag control-shaped language in untrusted content."""
    lowered = text.lower()
    matches = [marker for marker in _CONTROL_MARKERS if marker in lowered]
    return {"data_only": True, "control_markers": matches,
            "is_control_shaped": bool(matches)}


def _content_hash(parts: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _provenance_statement(
    *,
    statement: str,
) -> str:
    """The projected claim statement stays CLEAN (canonical contradiction
    detection compares propositions); full provenance travels in the
    immutable claim-event payload under ``provenance``."""
    return statement.strip()


def record_research_item(
    db: FoundryDatabase,
    *,
    mission_id: str,
    claim_kind: str,
    statement: str,
    source_type: str,
    location: str,
    retrieved_at: str,
    title: str | None = None,
    confidence: float = 0.6,
    fresh_until: str | None = None,
) -> dict[str, Any]:
    """Record ONE research item as a canonical knowledge claim.

    Deterministic claim ids (derived from provenance + statement) make
    identical re-recording dedupe on the existing idempotency key.
    Provenance (source type, canonical location, retrieval time, title,
    content hash) is stored in the append-only event payload and linked
    evidence rows — never lost, never merged into the proposition text.
    """
    if claim_kind not in CLAIM_KINDS:
        raise ResearchContractError(
            f"claim_kind must be one of {sorted(CLAIM_KINDS)}")
    if not statement.strip():
        raise ResearchContractError("statement is required")
    if not location.strip() or not source_type.strip():
        raise ResearchContractError("source location/type are required")
    content_hash = _content_hash({
        "source_type": source_type, "location": location,
        "retrieved_at": retrieved_at, "statement": statement})
    claim_id = f"claim-{claim_kind}-{content_hash}"
    applied = db.append(Event.create(
        "knowledge.claim_recorded",
        {"claim_id": claim_id, "mission_id": mission_id,
         "statement": _provenance_statement(statement=statement),
         "claim_type": claim_kind,
         "confidence": max(0.0, min(1.0, float(confidence))),
         "fresh_until": fresh_until,
         "source": {"run_id": f"research-{content_hash}",
                    "event_id": f"{location}:{content_hash}"},
         "provenance": {
             "provider": source_type,
             "canonical_location": location,
             "retrieved_at": retrieved_at,
             "title": title,
             "content_sha256_16": content_hash,
             "data_only": True}},
        source_type="supervisor", source_id="research-contract",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}"))
    return {"claim_id": claim_id, "content_hash": content_hash,
            "created": bool(applied[1]), "classification":
            classify_content(statement)}


def record_unknown(
    db: FoundryDatabase,
    *,
    mission_id: str,
    question: str,
    why_it_matters: str,
    expected_information_value: float = 0.5,
    recommended_experiment: str | None = None,
    location_hint: str | None = None,
    retrieved_at: str,
) -> dict[str, Any]:
    """An unknown stays UNKNOWN until evidence resolves it."""
    statement = (
        f"UNKNOWN: {question.strip()} | why_it_matters="
        f"{why_it_matters.strip()} | expected_information_value="
        f"{max(0.0, min(1.0, float(expected_information_value)))}"
        + (f" | recommended_experiment={recommended_experiment.strip()}"
           if recommended_experiment else ""))
    return record_research_item(
        db, mission_id=mission_id, claim_kind="unknown",
        statement=statement,
        source_type="research_question",
        location=location_hint or f"question:{_content_hash({'q': question})}",
        retrieved_at=retrieved_at,
        confidence=max(0.1, min(1.0, float(expected_information_value))))


def record_conflict(
    db: FoundryDatabase,
    *,
    mission_id: str,
    proposition: str,
    side_a: dict[str, Any],
    side_b: dict[str, Any],
    retrieved_at: str,
    recommended_experiment: str | None = None,
) -> dict[str, Any]:
    """Record both sides of a disagreement; neither side is deleted.

    ``side_a``/``side_b`` each need: statement, source_type, location.
    Returns both claim ids plus the conflict claim id.
    """
    for side_name, side in (("side_a", side_a), ("side_b", side_b)):
        for field in ("statement", "source_type", "location"):
            if not str(side.get(field, "")).strip():
                raise ResearchContractError(
                    f"{side_name}.{field} is required")
    recorded_a = record_research_item(
        db, mission_id=mission_id, claim_kind="evidence",
        statement=str(side_a["statement"]),
        source_type=str(side_a["source_type"]),
        location=str(side_a["location"]),
        retrieved_at=retrieved_at,
        title=side_a.get("title"),
        confidence=float(side_a.get("confidence", 0.5)))
    recorded_b = record_research_item(
        db, mission_id=mission_id, claim_kind="evidence",
        statement=str(side_b["statement"]),
        source_type=str(side_b["source_type"]),
        location=str(side_b["location"]),
        retrieved_at=retrieved_at,
        title=side_b.get("title"),
        confidence=float(side_b.get("confidence", 0.5)))
    conflict = record_research_item(
        db, mission_id=mission_id, claim_kind="conflict",
        statement=(
            f"CONFLICT on '{proposition.strip()}': "
            f"A({recorded_a['claim_id']} from {side_a['location']}) vs "
            f"B({recorded_b['claim_id']} from {side_b['location']})"
            + (f" | recommended_experiment={recommended_experiment.strip()}"
               if recommended_experiment else "")),
        source_type="conflict_record",
        location=(f"conflict:{recorded_a['content_hash']}:"
                  f"{recorded_b['content_hash']}"),
        retrieved_at=retrieved_at,
        confidence=0.9)
    return {"conflict_claim_id": conflict["claim_id"],
            "side_a_claim_id": recorded_a["claim_id"],
            "side_b_claim_id": recorded_b["claim_id"]}


def link_attempt_evidence(
    db: FoundryDatabase,
    *,
    claim_id: str,
    attempt_evidence: dict[str, Any],
) -> str:
    """Append-only link between a claim and a connector attempt record.

    The evidence id derives from the attempt identity; identical
    re-delivery dedupes on the canonical event key.
    """
    attempt_id = str(attempt_evidence.get("attempt_id") or "").strip()
    payload_hash = str(attempt_evidence.get("payload_hash") or "").strip()
    if not attempt_id or not payload_hash:
        raise ResearchContractError(
            "attempt evidence requires attempt_id and payload_hash")
    evidence_id = f"ev-{attempt_id}-{payload_hash[:12]}"
    db.append(Event.create(
        "knowledge.evidence_linked",
        {"claim_id": claim_id, "evidence_id": evidence_id,
         "payload_hash": payload_hash},
        source_type="supervisor", source_id="research-contract",
        idempotency_key=(
            f"knowledge.evidence_linked:{claim_id}:{evidence_id}")))
    return evidence_id


__all__ = [
    "CLAIM_KINDS",
    "ResearchContractError",
    "classify_content",
    "link_attempt_evidence",
    "record_conflict",
    "record_research_item",
    "record_unknown",
]
