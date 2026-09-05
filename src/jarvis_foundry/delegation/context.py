"""Bounded adapter from canonical Owner Context into delegation."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import canonical_json
from ..executive.autonomy import rebuild_executive_state
from ..owner_context import (
    OWNER_CONTEXT_PACKAGE_CONTRACT,
    REFERENCE_RESOLUTION_CONTRACT,
    OwnerContextQuery,
    StandingOrderEvaluator,
    compile_owner_context,
    resolve_owner_reference,
)
from ..owner_directives import active_directives_for
from .contracts import ReferentCandidate


DELEGATION_CONTEXT_CONTRACT = "jarvis.delegation-context.v1"

_TOKEN = re.compile(r"[a-z0-9]+")
_GENERIC = frozenset({
    "a", "about", "and", "continue", "do", "fix", "for", "handle", "here",
    "it", "me", "on", "please", "prepare", "something", "take", "tell",
    "that", "the", "this", "useful", "watch", "with",
})
_TERMINAL_OBJECTIVE = frozenset({"COMPLETED", "FAILED", "CANCELLED", "SUSPENDED"})
_FAILURE_EVENTS = (
    "verification.failed",
    "mission.failed",
    "system.action_failed",
    "agent_run.failed",
    "provider.dispatch_failed",
    "mission.state_changed",
)


def _tokens(value: str, *, remove_generic: bool = False) -> set[str]:
    result = set(_TOKEN.findall(value.lower()))
    return result - _GENERIC if remove_generic else result


def _strings(
    values: Any,
    *,
    limit: int,
    item_length: int = 1024,
) -> tuple[str, ...]:
    if values is None:
        return ()
    source: Sequence[Any] = (values,) if isinstance(values, str) else values
    result: list[str] = []
    for value in source:
        text = str(value or "").strip()
        if not text or text in result:
            continue
        result.append(text[:item_length])
        if len(result) >= limit:
            break
    return tuple(result)


def _event_text(payload: Mapping[str, Any]) -> str:
    for key in (
        "reason", "summary", "message", "error", "next_action", "objective",
        "description",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    findings = payload.get("findings")
    if isinstance(findings, Sequence) and not isinstance(findings, str):
        return "; ".join(
            str(value).strip() for value in findings if str(value).strip()
        )
    return ""


@dataclass(frozen=True, slots=True)
class DelegationContext:
    generated_at: str
    resolution_status: str
    selected_context_id: str | None
    candidates: tuple[ReferentCandidate, ...]
    active_constraints: tuple[str, ...] = ()
    active_constraint_ids: tuple[str, ...] = ()
    standing_order_ids: tuple[str, ...] = ()
    standing_order_behaviors: tuple[str, ...] = ()
    project_id: str | None = None
    program_id: str | None = None
    objective_id: str | None = None
    current_situation: str = ""
    recent_context_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    context_contract: str = OWNER_CONTEXT_PACKAGE_CONTRACT
    context_package_hash: str = ""
    reference_resolution_contract: str = REFERENCE_RESOLUTION_CONTRACT
    context_status: str = "UNKNOWN"
    contradiction_ids: tuple[str, ...] = ()
    calibration: dict[str, Any] = field(default_factory=dict)

    @property
    def selected(self) -> ReferentCandidate | None:
        if self.selected_context_id is None:
            return None
        return next(
            (
                candidate
                for candidate in self.candidates
                if candidate.context_id == self.selected_context_id
            ),
            None,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract": DELEGATION_CONTEXT_CONTRACT,
            "generated_at": self.generated_at,
            "resolution_status": self.resolution_status,
            "selected_context_id": self.selected_context_id,
            "candidates": [candidate.to_payload() for candidate in self.candidates],
            "active_constraints": list(self.active_constraints),
            "active_constraint_ids": list(self.active_constraint_ids),
            "standing_order_ids": list(self.standing_order_ids),
            "standing_order_behaviors": list(self.standing_order_behaviors),
            "project_id": self.project_id,
            "program_id": self.program_id,
            "objective_id": self.objective_id,
            "current_situation": self.current_situation,
            "recent_context_ids": list(self.recent_context_ids),
            "evidence_refs": list(self.evidence_refs),
            "warnings": list(self.warnings),
            "context_contract": self.context_contract,
            "context_package_hash": self.context_package_hash,
            "reference_resolution_contract": (
                self.reference_resolution_contract
            ),
            "context_status": self.context_status,
            "contradiction_ids": list(self.contradiction_ids),
            "calibration": dict(self.calibration or {}),
        }


def _canonical_candidates(
    resolution: Mapping[str, Any],
    *,
    allow_inactive: bool,
    current_context_ids: frozenset[str],
) -> list[ReferentCandidate]:
    result: list[ReferentCandidate] = []
    contract = str(
        resolution.get("contract") or REFERENCE_RESOLUTION_CONTRACT,
    )
    for raw in resolution.get("candidates") or ():
        if not isinstance(raw, Mapping):
            continue
        context_id = str(raw.get("context_id") or "").strip()
        if (
            not allow_inactive
            and (
                raw.get("current") is False
                or context_id not in current_context_ids
            )
        ):
            continue
        provenance = (
            dict(raw.get("provenance"))
            if isinstance(raw.get("provenance"), Mapping)
            else {}
        )
        provenance["lifecycle"] = {
            "current": raw.get("current"),
            "freshness": raw.get("freshness"),
            "superseded_by": raw.get("superseded_by"),
            "shadowed_by": raw.get("shadowed_by"),
        }
        evidence_refs = _strings(
            provenance.get("evidence_refs"), limit=16, item_length=512,
        )
        source_ref = str(provenance.get("source_ref") or "").strip()
        try:
            result.append(ReferentCandidate.from_mapping({
                "context_id": raw.get("context_id"),
                "kind": raw.get("kind"),
                "text": raw.get("text"),
                "confidence": raw.get("confidence"),
                "evidence": raw.get("evidence"),
                "source_refs": tuple(filter(None, (
                    source_ref,
                    *evidence_refs,
                ))),
                "state": raw.get("state") or "ACTIVE",
                "effective_at": raw.get("effective_at"),
                "scope": raw.get("scope"),
                "provenance": provenance,
                "resolution_contract": contract,
            }))
        except (TypeError, ValueError):
            continue
    return result


def _historical_reference_requested(utterance: str) -> bool:
    lowered = " ".join(str(utterance or "").lower().split())
    return any(
        cue in lowered
        for cue in (
            "earlier",
            "history",
            "past",
            "previous",
            "prior",
            "what happened",
            "yesterday",
        )
    )


def _supplemental_candidates(
    database: FoundryDatabase,
    *,
    utterance: str,
    recent_context_ids: tuple[str, ...],
) -> list[ReferentCandidate]:
    lowered = utterance.lower()
    utterance_tokens = _tokens(utterance, remove_generic=True)
    result: list[ReferentCandidate] = []
    executive = rebuild_executive_state(database)
    active = [
        objective
        for objective in executive.objectives.values()
        if objective.status not in _TERMINAL_OBJECTIVE
    ]
    active.sort(key=lambda row: (row.updated_at, row.objective_id), reverse=True)
    for index, objective in enumerate(active[:12]):
        context_id = f"objective:{objective.objective_id}"
        score = max(0.5, min(1.0, objective.confidence)) * 0.25
        evidence = ["executive_active_objective"]
        overlap = utterance_tokens & _tokens(
            objective.objective, remove_generic=True,
        )
        if overlap:
            score += min(
                0.55,
                len(overlap) / max(1, len(utterance_tokens)) * 0.55,
            )
            evidence.append(f"lexical:{','.join(sorted(overlap))}")
        if context_id in recent_context_ids:
            rank = recent_context_ids.index(context_id)
            score += max(0.45, 0.82 - rank * 0.16)
            evidence.append(f"recent_context_rank:{rank}")
        if "continue" in lowered:
            score += 0.32
            evidence.append("kind_cue:continuation")
        if any(word in lowered for word in ("fix", "repair", "resolve")):
            score += 0.2
            evidence.append("kind_cue:repair")
        if not utterance_tokens and len(active) == 1:
            score += 0.45
            evidence.append("sole_actionable_context")
        score += max(0.0, 0.12 - index * 0.01)
        result.append(ReferentCandidate(
            context_id=context_id,
            kind="ACTIVE_OBJECTIVE",
            text=objective.objective,
            confidence=round(min(1.0, score), 4),
            evidence=tuple(evidence),
            source_refs=tuple(filter(None, (
                f"objective:{objective.objective_id}",
                str(objective.provenance.get("event_id") or ""),
            ))),
            state=objective.status,
            effective_at=objective.updated_at,
            scope={
                "objective_ids": [objective.objective_id],
                "entity_refs": list(filter(None, (objective.resource_id,))),
            },
            provenance={
                "authority": "EXECUTIVE_EVENT_DERIVED",
                "objective_id": objective.objective_id,
            },
            resolution_contract="jarvis.delegation-supplemental-reference.v1",
        ))

    events = database.events(
        readonly=True,
        newest_first=True,
        limit=100,
        event_types=_FAILURE_EVENTS,
    )
    failures: list[tuple[Any, str]] = []
    for event in events:
        payload = dict(event.payload)
        if (
            event.event_type == "mission.state_changed"
            and str(payload.get("state") or "").upper() not in {"BLOCKED", "FAILED"}
        ):
            continue
        text = _event_text(payload)
        if text:
            failures.append((event, text))
        if len(failures) >= 12:
            break
    for index, (event, text) in enumerate(failures):
        context_id = f"event:{event.event_id}"
        score = 0.82 * 0.25
        evidence = [f"recent_failure:{event.event_type}"]
        overlap = utterance_tokens & _tokens(text, remove_generic=True)
        if overlap:
            score += min(
                0.55,
                len(overlap) / max(1, len(utterance_tokens)) * 0.55,
            )
            evidence.append(f"lexical:{','.join(sorted(overlap))}")
        if context_id in recent_context_ids:
            rank = recent_context_ids.index(context_id)
            score += max(0.45, 0.82 - rank * 0.16)
            evidence.append(f"recent_context_rank:{rank}")
        if any(word in lowered for word in ("fix", "repair", "resolve")):
            score += 0.3
            evidence.append("kind_cue:repair")
        if not utterance_tokens and len(failures) == 1 and not active:
            score += 0.45
            evidence.append("sole_actionable_context")
        score += max(0.0, 0.12 - index * 0.01)
        payload = dict(event.payload)
        scope = {
            key: [str(payload[key])]
            for key in ("mission_id", "objective_id", "project_id", "resource_id")
            if payload.get(key)
        }
        result.append(ReferentCandidate(
            context_id=context_id,
            kind="UNRESOLVED_ITEM",
            text=text,
            confidence=round(min(1.0, score), 4),
            evidence=tuple(evidence),
            source_refs=(f"event:{event.event_id}",),
            state="ACTIVE",
            effective_at=event.occurred_at,
            scope=scope,
            provenance={
                "authority": "VERIFIED_OR_RUNTIME_EVENT",
                "event_id": event.event_id,
                "event_type": event.event_type,
                "source_type": event.source_type,
                "source_id": event.source_id,
            },
            resolution_contract="jarvis.delegation-supplemental-reference.v1",
        ))
    return result


def _merged_resolution(
    candidates: Sequence[ReferentCandidate],
    *,
    recent_context_ids: tuple[str, ...],
    contradiction_ids: set[str],
    minimum_confidence: float = 0.45,
    ambiguity_margin: float = 0.12,
) -> tuple[str, str | None, tuple[ReferentCandidate, ...]]:
    deduped: dict[str, ReferentCandidate] = {}
    for candidate in candidates:
        prior = deduped.get(candidate.context_id)
        if prior is None or candidate.confidence > prior.confidence:
            deduped[candidate.context_id] = candidate
    ordered = tuple(sorted(
        deduped.values(),
        key=lambda row: (-row.confidence, row.context_id),
    )[:8])
    if not ordered or ordered[0].confidence < minimum_confidence:
        return "UNKNOWN", None, ordered
    top = ordered[0]
    if top.context_id in contradiction_ids:
        return "AMBIGUOUS", None, ordered
    explicit_recent = (
        bool(recent_context_ids)
        and top.context_id == recent_context_ids[0]
    )
    if (
        not explicit_recent
        and len(ordered) > 1
        and ordered[1].confidence >= minimum_confidence
        and top.confidence - ordered[1].confidence < ambiguity_margin
    ):
        return "AMBIGUOUS", None, ordered
    return "RESOLVED", top.context_id, ordered


def compile_delegation_context(
    database: FoundryDatabase,
    utterance: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    now: str | None = None,
    stop_path: str | None = None,
) -> DelegationContext:
    """Compile one bounded package without creating context authority."""

    values = dict(metadata or {})
    raw_context = (
        dict(values.get("context"))
        if isinstance(values.get("context"), Mapping)
        else {}
    )
    generated_at = now or isoformat()
    project_id = str(
        raw_context.get("project_id") or values.get("project_id") or "",
    ).strip() or None
    program_id = str(
        raw_context.get("program_id") or values.get("program_id") or "",
    ).strip() or None
    objective_id = str(
        raw_context.get("objective_id") or values.get("objective_id") or "",
    ).strip() or None
    current_situation = str(
        raw_context.get("current_situation")
        or values.get("current_situation")
        or ""
    ).strip()[:4096]
    recent_context_ids = _strings(
        raw_context.get("recent_context_ids")
        or values.get("recent_context_ids"),
        limit=12,
        item_length=256,
    )
    query = OwnerContextQuery(
        project_id=project_id,
        program_id=program_id,
        objective_id=objective_id,
        current_situation=current_situation,
        referenced_entity=(
            str(raw_context.get("referenced_entity") or "").strip() or None
        ),
        requested_task=utterance,
        entity_refs=_strings(
            raw_context.get("entity_refs"), limit=12, item_length=256,
        ),
        recent_context_ids=recent_context_ids,
        event_type=(
            str(raw_context.get("event_type") or "").strip() or None
        ),
        situation_state=(
            str(raw_context.get("situation_state") or "").strip() or None
        ),
        severity=str(raw_context.get("severity") or "").strip() or None,
        now=generated_at,
    )
    package = compile_owner_context(database, query)
    resolution = resolve_owner_reference(database, utterance, context=query)
    current_context_ids = frozenset(
        str(value.get("context_id") or "").strip()
        for value in (package.get("items") or ())
        if isinstance(value, Mapping)
        and value.get("current") is not False
        and str(value.get("context_id") or "").strip()
    )
    contradictions = tuple(
        str(value.get("contradiction_id") or "")
        for value in (package.get("contradictions") or ())
        if isinstance(value, Mapping) and value.get("contradiction_id")
    )
    contradicted_context_ids = {
        str(context_id)
        for conflict in (package.get("contradictions") or ())
        if isinstance(conflict, Mapping)
        for context_id in (conflict.get("context_ids") or ())
    }
    status, selected_context_id, candidates = _merged_resolution(
        [
            *_canonical_candidates(
                resolution,
                allow_inactive=_historical_reference_requested(utterance),
                current_context_ids=current_context_ids,
            ),
            *_supplemental_candidates(
                database,
                utterance=utterance,
                recent_context_ids=recent_context_ids,
            ),
        ],
        recent_context_ids=recent_context_ids,
        contradiction_ids=contradicted_context_ids,
    )
    identities = {
        value for value in (project_id, program_id, objective_id) if value
    }
    selected = next(
        (
            candidate
            for candidate in candidates
            if candidate.context_id == selected_context_id
        ),
        None,
    )
    if selected is not None:
        identities.add(selected.context_id)
    constraints = active_directives_for(
        database,
        command_types=frozenset({"CONSTRAINT", "ADD_CONSTRAINT"}),
        targets=identities or None,
        now=generated_at,
    )
    standing = StandingOrderEvaluator(
        database, stop_path=stop_path,
    ).evaluate(query)
    matches = [
        value
        for value in (standing.get("matched") or ())
        if isinstance(value, Mapping)
    ]
    evidence_refs = tuple(dict.fromkeys(
        ref
        for candidate in candidates
        for ref in candidate.source_refs
    ))[:48]
    warnings = [
        *(
            str(value)
            for value in (package.get("warnings") or ())
            if str(value)
        ),
        *(
            f"standing_order_unknown:{value.get('standing_order_id')}"
            for value in (standing.get("unknown") or ())
            if isinstance(value, Mapping)
        ),
    ]
    package_hash = hashlib.sha256(
        canonical_json(package).encode("utf-8"),
    ).hexdigest()
    from .calibration import build_delegation_calibration

    calibration = build_delegation_calibration(database, now=generated_at)
    return DelegationContext(
        generated_at=generated_at,
        resolution_status=status,
        selected_context_id=selected_context_id,
        candidates=candidates,
        active_constraints=tuple(
            str(record.exact_text or "").strip()
            for record in constraints
            if str(record.exact_text or "").strip()
        )[:24],
        active_constraint_ids=tuple(
            record.directive_id for record in constraints
        )[:24],
        standing_order_ids=tuple(
            str(value.get("directive_id") or "")
            for value in matches
            if value.get("directive_id")
        ),
        standing_order_behaviors=tuple(
            str(value.get("intended_behavior", {}).get("description") or "")
            for value in matches
            if isinstance(value.get("intended_behavior"), Mapping)
            and value.get("intended_behavior", {}).get("description")
        ),
        project_id=project_id,
        program_id=program_id,
        objective_id=objective_id,
        current_situation=current_situation,
        recent_context_ids=recent_context_ids,
        evidence_refs=evidence_refs,
        warnings=tuple(dict.fromkeys(warnings)),
        context_package_hash=package_hash,
        context_status=str(package.get("status") or "UNKNOWN"),
        contradiction_ids=contradictions,
        calibration=calibration,
    )


__all__ = [
    "DELEGATION_CONTEXT_CONTRACT",
    "DelegationContext",
    "compile_delegation_context",
]
