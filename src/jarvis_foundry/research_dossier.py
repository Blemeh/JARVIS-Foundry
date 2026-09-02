"""Bounded V11 research dossiers over the existing ResearchQueue.

The dossier is subordinate evidence for the existing Executive scorer.  It is
not a scorer, admission authority, objective creator, or execution path.  The
only durable mutations made by :func:`research_candidate_dossier` are the
existing ``research.candidate_*`` events projected into ``research_queue``.

External and local research content is always ``DATA_ONLY``.  A source can
support or contradict a candidate premise, but it cannot become an owner
directive, widen scope, create work, or change STOP.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .control.redaction import redact_text
from .database import FoundryDatabase
from .events import canonical_json
from .research_contract import classify_content


DOSSIER_CONTRACT = "jarvis.v11.research-dossier.v1"
EVIDENCE_REVISION_CONTRACT = "jarvis.v11.candidate-evidence-revision.v1"

_STANCES = frozenset({"SUPPORTING", "CONTRADICTING", "CONTEXT"})
_EVIDENCE_TYPES = frozenset({
    "EXPECTED_BENEFIT",
    "EFFORT",
    "DEPENDENCY",
    "PROJECT_CONTEXT",
    "FRESHNESS",
    "OTHER",
})
_TERMINAL_RESEARCH = frozenset({
    "COMPLETED", "BLOCKED", "FAILED", "UNKNOWN",
})


def _utc(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | str | None = None) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_text(value: Any, limit: int = 1200) -> str:
    return redact_text(str(value or "").replace("\x00", " ").strip())[:limit]


def _bounded_number(value: Any, *, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


@dataclass(frozen=True, slots=True)
class ResearchDossierPolicy:
    """Hard machine-rate bounds for one candidate dossier.

    Queue budget is the existing A2 abstract research-pass budget.  Source
    reads are separately bounded here because one synthesis pass is expected
    to compare several read-only sources.
    """

    max_initial_source_reads: int = 4
    max_follow_up_source_reads: int = 2
    max_unresolved_unknowns: int = 6
    high_value_unknown_floor: float = 0.70
    default_freshness_hours: float = 168.0
    max_total_queue_passes: int = 4
    max_refreshes_per_revision: int = 1
    contradiction_penalty: float = 0.20
    stale_penalty: float = 0.20
    unknown_penalty: float = 0.05

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_initial_source_reads) <= 12:
            raise ValueError("max_initial_source_reads must be within [1, 12]")
        if not 0 <= int(self.max_follow_up_source_reads) <= 4:
            raise ValueError("max_follow_up_source_reads must be within [0, 4]")
        if not 1 <= int(self.max_unresolved_unknowns) <= 12:
            raise ValueError("max_unresolved_unknowns must be within [1, 12]")
        if not 1 <= int(self.max_total_queue_passes) <= 8:
            raise ValueError("max_total_queue_passes must be within [1, 8]")
        if not 0 <= int(self.max_refreshes_per_revision) <= 2:
            raise ValueError("max_refreshes_per_revision must be within [0, 2]")
        for name in (
            "high_value_unknown_floor", "default_freshness_hours",
            "contradiction_penalty", "stale_penalty", "unknown_penalty",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.high_value_unknown_floor > 1.0:
            raise ValueError("high_value_unknown_floor must be within [0, 1]")

    @property
    def max_source_reads(self) -> int:
        return int(self.max_initial_source_reads + self.max_follow_up_source_reads)


InitialReader = Callable[[dict[str, Any], str, ResearchDossierPolicy], Mapping[str, Any]]
FollowUpReader = Callable[
    [dict[str, Any], dict[str, Any], str, ResearchDossierPolicy],
    Mapping[str, Any],
]


def candidate_identity(
    database: FoundryDatabase,
    candidate_id: str,
) -> dict[str, Any]:
    """Return the bounded identity block used by a dossier."""

    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT candidate_id, fingerprint, candidate_type, objective, "
            "expected_outcome, source, created_from, provenance_hash, "
            "evidence_json FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown candidate {candidate_id}")
    try:
        evidence = json.loads(str(row[8] or "[]"))
    except (TypeError, ValueError):
        evidence = []
    target_project = next((
        str(item.get("target_project") or "")
        for item in evidence
        if isinstance(item, dict) and item.get("target_project")
    ), "")
    return {
        "candidate_id": str(row[0]),
        "fingerprint": str(row[1] or ""),
        "candidate_type": str(row[2] or "unknown"),
        "objective": _bounded_text(row[3], 600),
        "expected_outcome": _bounded_text(row[4], 600),
        "source": str(row[5] or ""),
        "created_from": str(row[6] or ""),
        "provenance_hash": str(row[7] or ""),
        "target_project": target_project,
    }


def candidate_evidence_revision(
    database: FoundryDatabase,
    candidate_id: str,
) -> str:
    """Hash candidate identity plus every linked evidence source.

    Observation timestamps alone do not churn the revision.  A materially new
    source identity/hash, candidate premise, or expiry boundary does.  This is
    the cache key input that prevents unchanged evidence from burning another
    research pass.
    """

    identity = candidate_identity(database, candidate_id)
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT source_kind, source_identity, source_ref, "
            "content_hash, fresh_until, authority "
            "FROM candidate_sources WHERE candidate_id = ? "
            "ORDER BY source_kind, source_identity, IFNULL(content_hash, ''), "
            "IFNULL(source_ref, '')",
            (candidate_id,),
        ).fetchall()
    sources: list[dict[str, Any]] = []
    for row in rows:
        sources.append({
            "source_kind": str(row[0]),
            "source_identity": str(row[1]),
            "source_ref": row[2],
            "content_hash": row[3],
            "fresh_until": row[4],
            "authority": str(row[5] or "DATA_ONLY"),
        })
    material = {
        "contract": EVIDENCE_REVISION_CONTRACT,
        "candidate": identity,
        "sources": sources,
    }
    return f"evr-{_hash(material)}"


def dossier_cache_key(candidate_id: str, evidence_revision: str) -> str:
    return f"research-cache-{_hash({'candidate_id': candidate_id, 'evidence_revision': evidence_revision})[:24]}"


def _independent_key(source_kind: str, source_ref: str) -> str:
    parsed = urlsplit(source_ref)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return f"{source_kind}:{parsed.hostname.lower()}"
    if source_kind.lower() in {"project", "local"}:
        anchor = source_ref.split("#", 1)[0]
        return f"local:{anchor.lower()}"
    return f"{source_kind}:{source_ref.lower()}"


def _normalize_evidence(
    value: Mapping[str, Any],
    *,
    policy: ResearchDossierPolicy,
    generated_at: str,
) -> dict[str, Any]:
    source_kind = str(value.get("source_kind") or value.get("kind") or "unknown").lower()
    source_ref = _bounded_text(value.get("source_ref") or value.get("location"), 1000)
    if not source_ref:
        raise ValueError("research evidence requires source_ref")
    statement = _bounded_text(
        value.get("statement") or value.get("finding") or value.get("summary"),
        1200,
    )
    if not statement:
        raise ValueError("research evidence requires a bounded statement")
    retrieved_at = _iso(value.get("retrieved_at") or generated_at)
    fresh_until_value = value.get("fresh_until")
    if fresh_until_value:
        fresh_until = _iso(str(fresh_until_value))
    else:
        fresh_until = _iso(
            _utc(retrieved_at) + timedelta(hours=policy.default_freshness_hours)
        )
    stance = str(value.get("stance") or "CONTEXT").upper()
    if stance not in _STANCES:
        raise ValueError(f"unsupported research evidence stance {stance!r}")
    types = sorted({
        str(item).upper() for item in (value.get("evidence_types") or ("OTHER",))
        if str(item).upper() in _EVIDENCE_TYPES
    }) or ["OTHER"]
    claim_key = _bounded_text(value.get("claim_key") or "candidate_viability", 160).lower()
    raw_hash = str(value.get("content_hash") or "").lower()
    raw_hash = raw_hash.removeprefix("sha256:")
    if len(raw_hash) != 64 or any(ch not in "0123456789abcdef" for ch in raw_hash):
        raw_hash = _hash({"source_ref": source_ref, "statement": statement})
    evidence_id = str(value.get("evidence_id") or "").strip()
    if not evidence_id:
        evidence_id = f"dossier-evidence-{_hash({'source_ref': source_ref, 'content_hash': raw_hash, 'claim_key': claim_key, 'stance': stance})[:20]}"
    classification = classify_content(statement)
    return {
        "evidence_id": evidence_id,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "source_title": _bounded_text(value.get("source_title") or value.get("title"), 300),
        "independent_source": _bounded_text(
            value.get("independent_source") or _independent_key(source_kind, source_ref),
            500,
        ),
        "retrieved_at": retrieved_at,
        "fresh_until": fresh_until,
        "content_hash": f"sha256:{raw_hash}",
        "authority": "DATA_ONLY",
        "classification": classification,
        "statement": statement,
        "stance": stance,
        "claim_key": claim_key,
        "confidence": _bounded_number(value.get("confidence"), default=0.5),
        "evidence_types": types,
        "outcome": str(value.get("outcome") or "COMPLETED").upper(),
        "resolves_unknown_ids": sorted({
            str(item) for item in (value.get("resolves_unknown_ids") or ()) if str(item)
        }),
    }


def _normalize_unknown(value: Mapping[str, Any]) -> dict[str, Any]:
    question = _bounded_text(value.get("question"), 600)
    if not question:
        raise ValueError("unresolved unknown requires a question")
    unknown_id = str(value.get("unknown_id") or "").strip()
    if not unknown_id:
        unknown_id = f"unknown-{_hash({'question': question})[:16]}"
    status = str(value.get("status") or "UNRESOLVED").upper()
    if status not in {"UNRESOLVED", "RESOLVED"}:
        status = "UNRESOLVED"
    return {
        "unknown_id": unknown_id,
        "question": question,
        "why_it_matters": _bounded_text(value.get("why_it_matters"), 600),
        "expected_information_value": _bounded_number(
            value.get("expected_information_value"), default=0.5,
        ),
        "status": status,
        "follow_up_attempted": bool(value.get("follow_up_attempted", False)),
        "follow_up_status": str(value.get("follow_up_status") or "NOT_ATTEMPTED"),
        "resolution_evidence_ids": sorted({
            str(item) for item in (value.get("resolution_evidence_ids") or ()) if str(item)
        }),
    }


def _dedupe_evidence(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (item["source_kind"], item["source_ref"], item["content_hash"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _contradictions(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_claim: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for item in items:
        if item["stance"] not in {"SUPPORTING", "CONTRADICTING"}:
            continue
        by_claim.setdefault(item["claim_key"], {}).setdefault(item["stance"], []).append(item)
    records: list[dict[str, Any]] = []
    for claim_key in sorted(by_claim):
        supporting = by_claim[claim_key].get("SUPPORTING", [])
        contradicting = by_claim[claim_key].get("CONTRADICTING", [])
        for positive in supporting:
            for negative in contradicting:
                if positive["independent_source"] == negative["independent_source"]:
                    continue
                records.append({
                    "contradiction_id": f"contradiction-{_hash({'claim_key': claim_key, 'positive': positive['evidence_id'], 'negative': negative['evidence_id']})[:16]}",
                    "claim_key": claim_key,
                    "supporting_evidence_id": positive["evidence_id"],
                    "contradicting_evidence_id": negative["evidence_id"],
                    "status": "UNRESOLVED",
                })
    return records


def _evidence_refs(
    items: Sequence[dict[str, Any]],
    *types: str,
) -> list[dict[str, Any]]:
    wanted = set(types)
    return [
        {
            "evidence_id": item["evidence_id"],
            "source_ref": item["source_ref"],
            "statement": item["statement"],
            "confidence": item["confidence"],
        }
        for item in items
        if wanted.intersection(item["evidence_types"])
    ]


def _conclusion(
    supporting: Sequence[dict[str, Any]],
    contradicting: Sequence[dict[str, Any]],
    unresolved: Sequence[dict[str, Any]],
) -> tuple[str, str]:
    high_unknown = any(
        item["expected_information_value"] >= 0.70 for item in unresolved
    )
    if supporting and contradicting:
        verdict = "MIXED"
        summary = (
            "Evidence is mixed: material support and contradiction remain; "
            "treat the candidate as uncertain until the recorded unknowns are resolved."
        )
    elif contradicting:
        verdict = "NOT_SUPPORTED"
        summary = (
            "Current evidence weighs against the candidate premise; retain the "
            "contradictions and do not infer readiness."
        )
    elif supporting and high_unknown:
        verdict = "CONDITIONALLY_SUPPORTED"
        summary = (
            "Evidence supports further consideration, but high-value unknowns "
            "remain and must stay visible to the Executive."
        )
    elif supporting:
        verdict = "SUPPORTED"
        summary = "Available fresh evidence supports further consideration of the candidate."
    else:
        verdict = "INSUFFICIENT_EVIDENCE"
        summary = "The bounded pass did not establish enough evidence for a reliable conclusion."
    return verdict, summary


def build_research_dossier(
    *,
    candidate: Mapping[str, Any],
    research_question: str,
    evidence_revision_identity: str,
    evidence: Sequence[Mapping[str, Any]],
    unresolved_unknowns: Sequence[Mapping[str, Any]] = (),
    policy: ResearchDossierPolicy | None = None,
    generated_at: datetime | str | None = None,
    source_reads_used: int | None = None,
    follow_up_attempts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Create one deterministic, machine-readable evidence synthesis."""

    bounds = policy or ResearchDossierPolicy()
    generated = _iso(generated_at)
    question = _bounded_text(research_question, 1000)
    if not question:
        raise ValueError("research_question is required")
    if not str(evidence_revision_identity).startswith("evr-"):
        raise ValueError("evidence_revision_identity must be an evr-* identity")
    max_items = bounds.max_source_reads
    normalized = _dedupe_evidence([
        _normalize_evidence(item, policy=bounds, generated_at=generated)
        for item in evidence[:max_items]
    ])
    unknowns = [
        _normalize_unknown(item)
        for item in unresolved_unknowns[:bounds.max_unresolved_unknowns]
    ]
    resolved_ids = {
        unknown_id
        for item in normalized
        for unknown_id in item.get("resolves_unknown_ids", ())
    }
    for unknown in unknowns:
        if unknown["unknown_id"] in resolved_ids:
            unknown["status"] = "RESOLVED"
            unknown["resolution_evidence_ids"] = [
                item["evidence_id"] for item in normalized
                if unknown["unknown_id"] in item.get("resolves_unknown_ids", ())
            ]
    unresolved = [item for item in unknowns if item["status"] == "UNRESOLVED"]
    supporting = [item for item in normalized if item["stance"] == "SUPPORTING"]
    contradicting = [item for item in normalized if item["stance"] == "CONTRADICTING"]
    contextual = [item for item in normalized if item["stance"] == "CONTEXT"]
    contradictions = _contradictions(normalized)

    stale_ids = [
        item["evidence_id"] for item in normalized
        if _utc(item["fresh_until"]) <= _utc(generated)
    ]
    fresh_until = (
        min(normalized, key=lambda item: _utc(item["fresh_until"]))["fresh_until"]
        if normalized else None
    )
    freshness_status = (
        "UNKNOWN" if not normalized else "STALE" if stale_ids else "FRESH"
    )
    average_quality = (
        sum(item["confidence"] for item in normalized) / len(normalized)
        if normalized else 0.0
    )
    source_diversity = min(
        1.0,
        len({item["independent_source"] for item in normalized}) / 3.0,
    )
    base_confidence = average_quality * (0.60 + 0.40 * source_diversity)
    # A record is one support/conflict relationship. Several supporting
    # receipts can point at the same contrary source, so penalize each
    # distinct material contradictor once.
    contradiction_units = len({
        (item["claim_key"], item["contradicting_evidence_id"])
        for item in contradictions
    })
    contradiction_discount = min(
        0.50, contradiction_units * bounds.contradiction_penalty,
    )
    freshness_discount = (
        bounds.stale_penalty * len(stale_ids) / len(normalized)
        if normalized else bounds.stale_penalty
    )
    high_unknown_count = sum(
        item["expected_information_value"] >= bounds.high_value_unknown_floor
        for item in unresolved
    )
    unknown_discount = min(0.25, high_unknown_count * bounds.unknown_penalty)
    confidence = round(max(
        0.0,
        min(1.0, base_confidence - contradiction_discount
            - freshness_discount - unknown_discount),
    ), 4)
    verdict, conclusion_summary = _conclusion(
        supporting, contradicting, unresolved,
    )
    human_summary = (
        f"{verdict}: {len(supporting)} supporting, {len(contradicting)} "
        f"contradicting, {len(contradictions)} contradiction(s), "
        f"{len(unresolved)} unresolved unknown(s); confidence {confidence:.2f}. "
        f"{conclusion_summary}"
    )
    provenance = [{
        key: item[key] for key in (
            "evidence_id", "source_kind", "source_ref", "source_title",
            "independent_source", "retrieved_at", "fresh_until",
            "content_hash", "authority", "outcome", "classification",
        )
    } for item in normalized]
    dossier: dict[str, Any] = {
        "contract": DOSSIER_CONTRACT,
        "candidate_identity": dict(candidate),
        "research_question": question,
        "source_provenance": provenance,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "contextual_evidence": contextual,
        "contradictions": contradictions,
        "freshness": {
            "status": freshness_status,
            "evaluated_at": generated,
            "fresh_until": fresh_until,
            "stale_evidence_ids": stale_ids,
        },
        "confidence": {
            "score": confidence,
            "source_quality": round(average_quality, 4),
            "source_diversity": round(source_diversity, 4),
            "contradiction_penalty": round(contradiction_discount, 4),
            "freshness_penalty": round(freshness_discount, 4),
            "unknown_penalty": round(unknown_discount, 4),
            "advisory_only": True,
            "method": "bounded-source-quality-minus-explicit-penalties",
        },
        "unresolved_unknowns": unresolved,
        "expected_benefit_evidence": _evidence_refs(normalized, "EXPECTED_BENEFIT"),
        "effort_dependency_evidence": _evidence_refs(normalized, "EFFORT", "DEPENDENCY"),
        "relevant_project_context": _evidence_refs(normalized, "PROJECT_CONTEXT"),
        "final_research_conclusion": {
            "verdict": verdict,
            "summary": conclusion_summary,
            "admission_authority": False,
            "scoring_authority": False,
        },
        "evidence_revision_identity": evidence_revision_identity,
        "cache_key": dossier_cache_key(
            str(candidate.get("candidate_id") or ""), evidence_revision_identity,
        ),
        "budget": {
            "source_read_limit": bounds.max_source_reads,
            "initial_source_read_limit": bounds.max_initial_source_reads,
            "follow_up_source_read_limit": bounds.max_follow_up_source_reads,
            "source_reads_used": int(
                len(normalized) if source_reads_used is None else source_reads_used
            ),
            "follow_up_attempts": [dict(item) for item in follow_up_attempts],
            "bounded": True,
        },
        "human_summary": human_summary,
        "authority": "DATA_ONLY",
        "generated_at": generated,
    }
    revision_material = dict(dossier)
    revision_material.pop("generated_at", None)
    dossier["dossier_revision"] = _hash(revision_material)
    return dossier


def build_research_dossier_with_follow_up(
    *,
    candidate: Mapping[str, Any],
    research_question: str,
    evidence_revision_identity: str,
    evidence: Sequence[Mapping[str, Any]],
    unresolved_unknowns: Sequence[Mapping[str, Any]] = (),
    follow_up_reader: FollowUpReader | None = None,
    policy: ResearchDossierPolicy | None = None,
    generated_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Synthesize, then research only the highest-value unresolved unknowns."""

    bounds = policy or ResearchDossierPolicy()
    generated = _iso(generated_at)
    bounded_evidence = list(evidence[:bounds.max_initial_source_reads])
    initial_reads_used = len(bounded_evidence)
    follow_up_reads_used = 0
    normalized_unknowns = [
        _normalize_unknown(item)
        for item in unresolved_unknowns[:bounds.max_unresolved_unknowns]
    ]
    follow_up_attempts: list[dict[str, Any]] = []
    if follow_up_reader is not None and bounds.max_follow_up_source_reads:
        eligible = sorted(
            (
                item for item in normalized_unknowns
                if item["status"] == "UNRESOLVED"
                and item["expected_information_value"] >= bounds.high_value_unknown_floor
            ),
            key=lambda item: (-item["expected_information_value"], item["unknown_id"]),
        )
        reads_left = bounds.max_follow_up_source_reads
        for unknown in eligible:
            if reads_left <= 0:
                break
            response = dict(follow_up_reader(
                dict(unknown), dict(candidate), research_question, bounds,
            ) or {})
            returned = list(response.get("evidence") or ())[:reads_left]
            consumed = len(returned) if returned else 1
            reads_left -= consumed
            follow_up_reads_used += consumed
            status = str(response.get("status") or (
                "COMPLETED" if returned else "UNKNOWN"
            )).upper()
            evidence_ids: list[str] = []
            for item in returned:
                value = dict(item)
                resolved = list(value.get("resolves_unknown_ids") or ())
                if bool(response.get("resolved", bool(returned))):
                    resolved.append(unknown["unknown_id"])
                value["resolves_unknown_ids"] = sorted(set(resolved))
                bounded_evidence.append(value)
                evidence_ids.append(_normalize_evidence(
                    value, policy=bounds, generated_at=generated,
                )["evidence_id"])
            unknown["follow_up_attempted"] = True
            unknown["follow_up_status"] = status
            if bool(response.get("resolved", bool(returned))):
                unknown["status"] = "RESOLVED"
            follow_up_attempts.append({
                "unknown_id": unknown["unknown_id"],
                "status": status,
                "sources_returned": len(returned),
                "reason": _bounded_text(response.get("reason"), 300),
                "evidence_ids": evidence_ids,
            })
    return build_research_dossier(
        candidate=candidate,
        research_question=research_question,
        evidence_revision_identity=evidence_revision_identity,
        evidence=bounded_evidence,
        unresolved_unknowns=normalized_unknowns,
        policy=bounds,
        generated_at=generated,
        source_reads_used=initial_reads_used + follow_up_reads_used,
        follow_up_attempts=follow_up_attempts,
    )


def dossier_is_fresh(
    dossier: Mapping[str, Any],
    *,
    now: datetime | str | None = None,
) -> bool:
    freshness = dossier.get("freshness")
    if not isinstance(freshness, Mapping):
        return False
    fresh_until = freshness.get("fresh_until")
    if not fresh_until or freshness.get("stale_evidence_ids"):
        return False
    try:
        return _utc(str(fresh_until)) > _utc(now)
    except ValueError:
        return False


def _row_dossier(row: Mapping[str, Any]) -> dict[str, Any] | None:
    evidence = row.get("evidence")
    if not isinstance(evidence, Mapping):
        return None
    dossier = evidence.get("dossier")
    if not isinstance(dossier, Mapping) or dossier.get("contract") != DOSSIER_CONTRACT:
        return None
    return dict(dossier)


def candidate_dossier_cache(
    database: FoundryDatabase,
    *,
    candidate_id: str,
    evidence_revision_identity: str,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Return HIT/STALE/MISS truth for one candidate evidence revision."""

    from .research_queue import candidate_research_state

    rows = sorted(
        candidate_research_state(database, candidate_id=candidate_id),
        key=lambda row: int(row.get("pass_number") or 0),
        reverse=True,
    )
    prior_revision: str | None = None
    for row in rows:
        evidence = row.get("evidence")
        if isinstance(evidence, Mapping):
            reserved_revision = str(
                evidence.get("evidence_revision_identity") or ""
            )
            if reserved_revision and prior_revision is None:
                prior_revision = reserved_revision
            if (
                reserved_revision == evidence_revision_identity
                and row.get("status") in {"ENQUEUED", "IN_PROGRESS"}
            ):
                return {
                    "status": "IN_PROGRESS",
                    "row": row,
                    "dossier": None,
                    "previous_revision": prior_revision,
                }
        dossier = _row_dossier(row)
        if dossier is None:
            continue
        revision = str(dossier.get("evidence_revision_identity") or "")
        if prior_revision is None:
            prior_revision = revision
        if revision != evidence_revision_identity:
            continue
        status = "HIT" if dossier_is_fresh(dossier, now=now) else "STALE"
        return {
            "status": status,
            "row": row,
            "dossier": dossier,
            "previous_revision": prior_revision,
        }
    return {
        "status": "MISS",
        "row": None,
        "dossier": None,
        "previous_revision": prior_revision,
    }


def _research_question(identity: Mapping[str, Any], question: str | None) -> str:
    if question and str(question).strip():
        return _bounded_text(question, 1000)
    objective = _bounded_text(identity.get("objective"), 700)
    return (
        f"Should JARVIS continue considering this candidate: {objective}? "
        "Assess expected benefit, effort, dependencies, project context, and contrary evidence."
    )


def research_candidate_dossier(
    database: FoundryDatabase,
    *,
    candidate_id: str,
    cycle_id: str,
    initial_reader: InitialReader,
    follow_up_reader: FollowUpReader | None = None,
    research_question: str | None = None,
    policy: ResearchDossierPolicy | None = None,
    reserved_budget: float = 1.0,
    per_candidate_budget: float = 1.0,
    cycle_budget: float = 4.0,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Research or reuse one dossier through the canonical queue.

    A fresh cache hit invokes neither reader and appends no event.  A stale
    cache may receive one bounded refresh for the same evidence revision.  A
    materially changed candidate revision receives one normal pass, subject to
    the total queue-pass cap.  Interrupted ``IN_PROGRESS`` work is never
    blindly retried.
    """

    from .research_queue import (
        candidate_research_state,
        complete_candidate_research,
        enqueue_candidate_research,
        start_candidate_research,
    )

    bounds = policy or ResearchDossierPolicy()
    identity = candidate_identity(database, candidate_id)
    evidence_revision = candidate_evidence_revision(database, candidate_id)
    question = _research_question(identity, research_question)
    cache = candidate_dossier_cache(
        database,
        candidate_id=candidate_id,
        evidence_revision_identity=evidence_revision,
        now=now,
    )
    if cache["status"] == "HIT":
        row = dict(cache["row"] or {})
        return {
            **row,
            "status": "REUSED",
            "cache_status": "HIT",
            "budget_used": 0.0,
            "dossier": cache["dossier"],
            "evidence_revision_identity": evidence_revision,
        }
    if cache["status"] == "IN_PROGRESS":
        return {
            **dict(cache["row"] or {}),
            "cache_status": "IN_PROGRESS",
            "evidence_revision_identity": evidence_revision,
        }

    rows = candidate_research_state(database, candidate_id=candidate_id)
    same_revision = [
        row for row in rows
        if (
            isinstance(row.get("evidence"), Mapping)
            and str(row["evidence"].get("evidence_revision_identity") or "")
            == evidence_revision
        ) or (
            (_row_dossier(row) or {}).get("evidence_revision_identity")
            == evidence_revision
        )
    ]
    if (
        cache["status"] == "STALE"
        and len(same_revision) >= 1 + bounds.max_refreshes_per_revision
    ):
        return {
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "status": "REFRESH_LIMIT",
            "cache_status": "STALE",
            "budget_used": 0.0,
            "dossier": cache["dossier"],
            "evidence_revision_identity": evidence_revision,
        }
    next_pass = max(
        (int(row.get("pass_number") or 0) for row in rows), default=0,
    ) + 1
    if next_pass > bounds.max_total_queue_passes:
        return {
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "pass_number": next_pass,
            "status": "PASS_LIMIT",
            "cache_status": cache["status"],
            "budget_used": 0.0,
            "evidence_revision_identity": evidence_revision,
        }
    cache_status = (
        "REFRESH" if cache["status"] == "STALE"
        else "REVISION_CHANGED" if cache.get("previous_revision")
        and cache.get("previous_revision") != evidence_revision
        else "MISS"
    )
    queued = enqueue_candidate_research(
        database,
        candidate_id=candidate_id,
        cycle_id=cycle_id,
        pass_number=next_pass,
        budget_used=reserved_budget,
        per_candidate_budget=per_candidate_budget,
        cycle_budget=cycle_budget,
        max_normal_passes=bounds.max_total_queue_passes,
        source_ref=f"candidate:{candidate_id}@{evidence_revision}",
        evidence={
            "authority": "DATA_ONLY",
            "dossier_contract": DOSSIER_CONTRACT,
            "evidence_revision_identity": evidence_revision,
            "cache_key": dossier_cache_key(candidate_id, evidence_revision),
            "cache_status": cache_status,
        },
        attempted_at=_iso(now),
        input_hash=evidence_revision,
    )
    if queued.get("status") != "ENQUEUED":
        return {**queued, "cache_status": cache_status}
    started = start_candidate_research(
        database,
        research_id=str(queued["research_id"]),
        started_at=_iso(now),
    )
    if started.get("status") != "IN_PROGRESS":
        return {**started, "cache_status": cache_status}

    receipt = dict(initial_reader(identity, question, bounds) or {})
    raw_evidence = list(
        receipt.get("source_evidence") or receipt.get("evidence_items") or ()
    )
    raw_unknowns = list(receipt.get("unresolved_unknowns") or ())
    dossier_value = receipt.get("dossier")
    if isinstance(dossier_value, Mapping):
        dossier = dict(dossier_value)
        if dossier.get("contract") != DOSSIER_CONTRACT:
            raise ValueError("research reader returned an unsupported dossier contract")
        if dossier.get("evidence_revision_identity") != evidence_revision:
            raise ValueError("research reader dossier evidence revision mismatch")
    else:
        if not raw_evidence:
            legacy = receipt.get("evidence")
            if isinstance(legacy, Mapping) and legacy:
                statement = (
                    legacy.get("finding") or legacy.get("summary")
                    or legacy.get("reason") or legacy.get("outcome")
                    or "Research receipt recorded without a semantic finding."
                )
                raw_evidence = [{
                    "source_kind": legacy.get("source_kind") or "research",
                    "source_ref": receipt.get("source_ref")
                    or legacy.get("source_ref") or f"candidate:{candidate_id}",
                    "statement": statement,
                    "stance": legacy.get("stance") or "CONTEXT",
                    "confidence": legacy.get("confidence", 0.5),
                    "content_hash": receipt.get("content_hash"),
                    "retrieved_at": _iso(now),
                    "evidence_types": legacy.get("evidence_types") or ["OTHER"],
                    "outcome": legacy.get("outcome") or receipt.get("status"),
                }]
        dossier = build_research_dossier_with_follow_up(
            candidate=identity,
            research_question=question,
            evidence_revision_identity=evidence_revision,
            evidence=raw_evidence,
            unresolved_unknowns=raw_unknowns,
            follow_up_reader=follow_up_reader,
            policy=bounds,
            generated_at=now,
        )
    used = min(
        float(reserved_budget),
        max(0.0, float(receipt.get("budget_used", reserved_budget))),
    )
    dossier_budget = dict(dossier.get("budget") or {})
    dossier_budget.update({
        "queue_budget_reserved": float(reserved_budget),
        "queue_budget_used": used,
        "per_pass_budget_limit": float(per_candidate_budget),
        "cycle_budget_limit": float(cycle_budget),
        "queue_pass_limit": int(bounds.max_total_queue_passes),
        "candidate_lifetime_budget_limit": round(
            float(per_candidate_budget) * bounds.max_total_queue_passes, 6,
        ),
    })
    dossier["budget"] = dossier_budget
    revision_material = dict(dossier)
    revision_material.pop("generated_at", None)
    revision_material.pop("dossier_revision", None)
    dossier["dossier_revision"] = _hash(revision_material)
    requested_status = str(receipt.get("status") or "COMPLETED").upper()
    if requested_status not in _TERMINAL_RESEARCH:
        requested_status = "UNKNOWN"
    if requested_status == "COMPLETED" and not dossier["source_provenance"]:
        requested_status = "UNKNOWN"
    evidence_payload = {
        "authority": "DATA_ONLY",
        "confidence": dossier["confidence"]["score"],
        "dossier_contract": DOSSIER_CONTRACT,
        "evidence_revision_identity": evidence_revision,
        "cache_key": dossier["cache_key"],
        "cache_status": cache_status,
        "dossier": dossier,
    }
    completed = complete_candidate_research(
        database,
        research_id=str(queued["research_id"]),
        status=requested_status,
        evidence=evidence_payload,
        budget_used=used,
        source_ref=receipt.get("source_ref") or (
            dossier["source_provenance"][0]["source_ref"]
            if dossier["source_provenance"] else None
        ),
        content_hash=str(dossier["dossier_revision"]),
        completed_at=_iso(now),
    )
    return {
        **completed,
        "cache_status": "REFRESHED" if cache_status == "REFRESH" else cache_status,
        "dossier": dossier,
        "evidence_revision_identity": evidence_revision,
    }


def project_research_dossiers(
    database: FoundryDatabase,
    *,
    limit: int = 20,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Concise read-only dossier summaries for Command Center."""

    from .research_queue import candidate_research_state

    bounded_limit = max(1, min(int(limit), 100))
    latest: dict[str, dict[str, Any]] = {}
    for row in sorted(
        candidate_research_state(database),
        key=lambda item: (
            str(item.get("completed_at") or item.get("recorded_at") or ""),
            int(item.get("pass_number") or 0),
        ),
        reverse=True,
    ):
        dossier = _row_dossier(row)
        candidate_id = str(row.get("candidate_id") or "")
        if dossier is None or not candidate_id or candidate_id in latest:
            continue
        latest[candidate_id] = {
            "research_id": row.get("research_id"),
            "candidate_id": candidate_id,
            "objective": (dossier.get("candidate_identity") or {}).get("objective"),
            "summary": dossier.get("human_summary"),
            "conclusion": (dossier.get("final_research_conclusion") or {}).get("verdict"),
            "confidence": (dossier.get("confidence") or {}).get("score"),
            "freshness": {
                **dict(dossier.get("freshness") or {}),
                "currently_fresh": dossier_is_fresh(dossier, now=now),
            },
            "supporting_sources": len(dossier.get("supporting_evidence") or ()),
            "contradicting_sources": len(dossier.get("contradicting_evidence") or ()),
            "contradictions": len(dossier.get("contradictions") or ()),
            "unresolved_unknowns": len(dossier.get("unresolved_unknowns") or ()),
            "evidence_revision_identity": dossier.get("evidence_revision_identity"),
            "dossier_revision": dossier.get("dossier_revision"),
            "contract": dossier.get("contract"),
            "authority": dossier.get("authority"),
        }
        if len(latest) >= bounded_limit:
            break
    dossiers = list(latest.values())
    return {
        "contract": DOSSIER_CONTRACT,
        "status": "READY" if dossiers else "EMPTY",
        "count": len(dossiers),
        "fresh_count": sum(
            bool(item["freshness"].get("currently_fresh")) for item in dossiers
        ),
        "stale_count": sum(
            not bool(item["freshness"].get("currently_fresh")) for item in dossiers
        ),
        "dossiers": dossiers,
        "authority": "READ_ONLY_PROJECTION",
    }


__all__ = [
    "DOSSIER_CONTRACT",
    "EVIDENCE_REVISION_CONTRACT",
    "ResearchDossierPolicy",
    "build_research_dossier",
    "build_research_dossier_with_follow_up",
    "candidate_dossier_cache",
    "candidate_evidence_revision",
    "candidate_identity",
    "dossier_cache_key",
    "dossier_is_fresh",
    "project_research_dossiers",
    "research_candidate_dossier",
]
