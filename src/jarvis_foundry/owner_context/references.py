"""Deterministic evidence-backed contextual reference resolution."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Mapping

from ..clock import isoformat, parse
from .models import OwnerContextQuery, OwnerContextRecord
from .projection import project_owner_context


REFERENCE_RESOLUTION_CONTRACT = "jarvis.owner-reference-resolution.v1"

_TOKEN = re.compile(r"[a-z0-9]+")
_PRONOUNS = frozenset({
    "a", "an", "and", "continue", "fix", "from", "handle", "it", "project",
    "that", "the", "thing", "this", "yesterday",
})


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(value.lower())
        if token not in _PRONOUNS
    }


def _kind_cue_score(
    utterance: str,
    record: OwnerContextRecord,
) -> tuple[float, list[str]]:
    lowered = utterance.lower()
    score = 0.0
    evidence: list[str] = []
    if "project" in lowered and record.kind in {"PROJECT", "PROGRAM"}:
        score += 0.25
        evidence.append("kind_cue:project")
    if "continue" in lowered and record.kind in {
        "ACTIVE_OBJECTIVE",
        "COMMITMENT",
        "UNRESOLVED_ITEM",
        "WAITING_ON_OWNER",
    }:
        score += 0.25
        evidence.append("kind_cue:continuation")
    if "fix" in lowered and record.kind in {
        "ACTIVE_OBJECTIVE",
        "UNRESOLVED_ITEM",
        "HISTORICAL_OUTCOME",
    }:
        score += 0.25
        evidence.append("kind_cue:repair")
    return score, evidence


def _score_record(
    record: OwnerContextRecord,
    *,
    utterance: str,
    context: OwnerContextQuery,
    yesterday_only: bool,
) -> tuple[float, list[str]]:
    now = parse(context.now or isoformat())
    effective = parse(record.effective_at)
    evidence: list[str] = []
    score = 0.0

    if yesterday_only:
        if effective.date() != (now - timedelta(days=1)).date():
            return -1.0, []
        score += 0.55
        evidence.append(f"date:yesterday:{effective.date().isoformat()}")

    if record.context_id in context.recent_context_ids:
        index = context.recent_context_ids.index(record.context_id)
        score += max(0.2, 0.75 - index * 0.25)
        evidence.append(f"recent_context_rank:{index}")

    if context.project_id and context.project_id in record.scope.project_ids:
        score += 0.4
        evidence.append(f"project_scope:{context.project_id}")
    if context.program_id and context.program_id in record.scope.program_ids:
        score += 0.35
        evidence.append(f"program_scope:{context.program_id}")
    if context.objective_id and context.objective_id in record.scope.objective_ids:
        score += 0.45
        evidence.append(f"objective_scope:{context.objective_id}")
    entity_values = {
        value
        for value in (
            context.program_id,
            context.project_id,
            context.objective_id,
            context.referenced_entity,
            *context.entity_refs,
        )
        if value
    }
    entity_overlap = entity_values & set(record.scope.entity_refs)
    if entity_overlap:
        score += 0.4
        evidence.extend(f"entity_scope:{value}" for value in sorted(entity_overlap))

    utterance_tokens = _tokens(utterance)
    record_tokens = _tokens(
        f"{record.text} {' '.join(record.scope.tags)} "
        f"{record.attributes.get('value', '')}",
    )
    overlap = utterance_tokens & record_tokens
    if overlap:
        lexical = len(overlap) / max(1, len(utterance_tokens))
        score += min(0.55, lexical * 0.55)
        evidence.append(f"lexical:{','.join(sorted(overlap))}")

    kind_score, kind_evidence = _kind_cue_score(utterance, record)
    score += kind_score
    evidence.extend(kind_evidence)

    age_hours = max(0.0, (now - effective).total_seconds() / 3600.0)
    if age_hours <= 24:
        score += 0.15
        evidence.append("recency:24h")
    elif age_hours <= 72:
        score += 0.08
        evidence.append("recency:72h")
    if record.current(context.now):
        score += 0.1
        evidence.append("current")
    elif record.state in {"REVOKED", "SUPERSEDED"}:
        score -= 0.25
        evidence.append("inactive_penalty")
    if record.state == "UNKNOWN" or record.unknown_fields:
        score -= 0.25
        evidence.append("unknown_penalty")
    return round(max(0.0, min(1.0, score)), 4), evidence


def resolve_owner_reference(
    database: Any,
    utterance: str,
    *,
    context: OwnerContextQuery | Mapping[str, Any] | None = None,
    current_project: str | None = None,
    recent_context_ids: tuple[str, ...] = (),
    now: str | None = None,
    max_candidates: int = 5,
    minimum_confidence: float = 0.45,
    ambiguity_margin: float = 0.12,
) -> dict[str, Any]:
    """Rank contextual referents and report uncertainty without acting."""

    text = str(utterance or "").strip()
    if not text:
        raise ValueError("reference utterance is required")
    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    if context is None:
        request = OwnerContextQuery(
            project_id=current_project,
            recent_context_ids=tuple(recent_context_ids),
            now=now,
        )
    else:
        request = OwnerContextQuery.from_mapping(context)
        if current_project or recent_context_ids or now:
            request = OwnerContextQuery(
                project_id=current_project or request.project_id,
                program_id=request.program_id,
                objective_id=request.objective_id,
                current_situation=request.current_situation,
                referenced_entity=request.referenced_entity,
                requested_task=request.requested_task,
                entity_refs=request.entity_refs,
                recent_context_ids=(
                    tuple(recent_context_ids) or request.recent_context_ids
                ),
                event_type=request.event_type,
                situation_state=request.situation_state,
                severity=request.severity,
                now=now or request.now,
            )
    projection = project_owner_context(database, now=request.now)
    if request.now is None:
        request = OwnerContextQuery(
            project_id=request.project_id,
            program_id=request.program_id,
            objective_id=request.objective_id,
            current_situation=request.current_situation,
            referenced_entity=request.referenced_entity,
            requested_task=request.requested_task,
            entity_refs=request.entity_refs,
            recent_context_ids=request.recent_context_ids,
            event_type=request.event_type,
            situation_state=request.situation_state,
            severity=request.severity,
            now=projection.generated_at,
        )

    yesterday_only = "yesterday" in text.lower()
    candidates: list[dict[str, Any]] = []
    for record in projection.records:
        score, evidence = _score_record(
            record,
            utterance=text,
            context=request,
            yesterday_only=yesterday_only,
        )
        if score <= 0.0:
            continue
        candidates.append({
            "context_id": record.context_id,
            "kind": record.kind,
            "text": record.text,
            "state": record.state,
            "confidence": score,
            "evidence": evidence,
            "effective_at": record.effective_at,
            "scope": record.scope.as_dict(),
            "provenance": record.provenance.as_dict(),
        })
    candidates.sort(key=lambda value: (
        0 if "recent_context_rank:0" in value["evidence"] else 1,
        -float(value["confidence"]),
        -parse(str(value["effective_at"])).timestamp(),
        str(value["context_id"]),
    ))
    candidates = candidates[:max_candidates]

    explicit_recent_winner = bool(
        candidates
        and "recent_context_rank:0" in candidates[0]["evidence"]
        and not any(
            "recent_context_rank:0" in candidate["evidence"]
            for candidate in candidates[1:]
        )
    )
    if not candidates or float(candidates[0]["confidence"]) < minimum_confidence:
        status = "UNKNOWN"
        selected = None
        confidence = float(candidates[0]["confidence"]) if candidates else 0.0
    elif explicit_recent_winner:
        status = "RESOLVED"
        selected = str(candidates[0]["context_id"])
        confidence = float(candidates[0]["confidence"])
    elif (
        len(candidates) > 1
        and float(candidates[1]["confidence"]) >= minimum_confidence
        and (
            float(candidates[0]["confidence"])
            - float(candidates[1]["confidence"])
        ) < ambiguity_margin
    ):
        status = "AMBIGUOUS"
        selected = None
        confidence = float(candidates[0]["confidence"])
    else:
        status = "RESOLVED"
        selected = str(candidates[0]["context_id"])
        confidence = float(candidates[0]["confidence"])

    return {
        "contract": REFERENCE_RESOLUTION_CONTRACT,
        "utterance": text,
        "status": status,
        "selected_context_id": selected,
        "confidence": confidence,
        "candidates": candidates,
        "evidence": {
            "current_project": request.project_id,
            "recent_context_ids": list(request.recent_context_ids),
            "now": request.now,
            "yesterday_filter": yesterday_only,
            "minimum_confidence": minimum_confidence,
            "ambiguity_margin": ambiguity_margin,
        },
        "decision_authority": "FUTURE_DELEGATION_ENGINE",
        "action_taken": False,
    }


__all__ = [
    "REFERENCE_RESOLUTION_CONTRACT",
    "resolve_owner_reference",
]
