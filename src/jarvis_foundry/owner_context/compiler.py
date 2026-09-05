"""Bounded task-specific owner-context compilation."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Mapping

from ..events import canonical_json
from .models import (
    OWNER_CONTEXT_CONTRACT,
    ContextScope,
    OwnerContextQuery,
    OwnerContextRecord,
)
from .projection import project_owner_context


OWNER_CONTEXT_PACKAGE_CONTRACT = "jarvis.owner-context-package.v1"

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have if in is it of on or that the "
    "this to was were will with my me your our their thing handle continue fix"
    .split()
)
_TOKEN = re.compile(r"[a-z0-9]+")
_HISTORICAL_KINDS = frozenset({
    "DECISION",
    "REJECTED_APPROACH",
    "HISTORICAL_OUTCOME",
    "UNRESOLVED_ITEM",
    "WAITING_ON_OWNER",
    "COMMITMENT",
})
_ALWAYS_RELEVANT_GLOBAL = frozenset({
    "GOAL",
    "PRIORITY",
    "CONSTRAINT",
    "PERMISSION",
    "VETO",
    "STANDING_ORDER",
})
_SECTION_NAMES = {
    "GOAL": "goals",
    "PRIORITY": "priorities",
    "PROGRAM": "programs",
    "PROJECT": "projects",
    "ACTIVE_OBJECTIVE": "active_objectives",
    "STANDING_ORDER": "standing_orders",
    "DECISION": "decisions",
    "VETO": "vetoes",
    "CONSTRAINT": "constraints",
    "PERMISSION": "permissions",
    "PREFERENCE": "preferences",
    "UNRESOLVED_ITEM": "unresolved_items",
    "COMMITMENT": "commitments",
    "WAITING_ON_OWNER": "waiting_on_owner",
    "REJECTED_APPROACH": "rejected_approaches",
    "HISTORICAL_OUTCOME": "historical_outcomes",
}
_QUERY_TEXT_LIMIT = 160
_QUERY_LIST_LIMIT = 4
_QUERY_LIST_ITEM_LIMIT = 80
_WARNING_LIMIT = 12
_WARNING_TEXT_LIMIT = 160


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(value.lower())
        if token not in _STOPWORDS
    }


def _scope_relevance(
    scope: ContextScope,
    query: OwnerContextQuery,
) -> tuple[float | None, list[str]]:
    if scope.global_scope:
        return 0.15, ["global_scope"]
    score = 0.0
    evidence: list[str] = []
    dimensions = (
        (query.program_id, scope.program_ids, "program"),
        (query.project_id, scope.project_ids, "project"),
        (query.objective_id, scope.objective_ids, "objective"),
    )
    for current, allowed, label in dimensions:
        if not allowed:
            continue
        if current not in allowed:
            return None, []
        score += 1.0
        evidence.append(f"{label}:{current}")
    query_entities = {
        value
        for value in (
            query.program_id,
            query.project_id,
            query.objective_id,
            query.referenced_entity,
            *query.entity_refs,
        )
        if value
    }
    if scope.entity_refs:
        overlap = set(scope.entity_refs) & query_entities
        if not overlap:
            return None, []
        score += 0.8 + 0.1 * min(2, len(overlap))
        evidence.extend(f"entity:{value}" for value in sorted(overlap))
    if scope.tags:
        query_tags = _tokens(query.query_text())
        overlap = set(scope.tags) & query_tags
        if overlap:
            score += 0.2
            evidence.extend(f"tag:{value}" for value in sorted(overlap))
    return (score, evidence) if evidence else (None, [])


def _record_relevance(
    record: OwnerContextRecord,
    query: OwnerContextQuery,
) -> tuple[float, list[str]]:
    scope_score, evidence = _scope_relevance(record.scope, query)
    if scope_score is None and record.context_id not in query.recent_context_ids:
        return -1.0, []
    score = max(0.0, scope_score or 0.0)
    reasons = list(evidence)

    if record.context_id in query.recent_context_ids:
        index = query.recent_context_ids.index(record.context_id)
        score += max(0.8, 1.6 - index * 0.15)
        reasons.append(f"recent_context_rank:{index}")

    query_tokens = _tokens(query.query_text())
    record_tokens = _tokens(
        " ".join((
            record.text,
            canonical_json(record.attributes),
            " ".join(record.scope.tags),
        )),
    )
    if query_tokens and record_tokens:
        overlap = query_tokens & record_tokens
        if overlap:
            lexical = len(overlap) / max(1, min(len(query_tokens), len(record_tokens)))
            score += min(1.2, lexical * 1.2)
            reasons.append(f"lexical:{','.join(sorted(overlap))}")

    if (
        record.scope.global_scope
        and record.kind in _ALWAYS_RELEVANT_GLOBAL
    ):
        score += 0.2
        reasons.append("global_governance_context")
    if record.current(query.now):
        score += 0.15
        reasons.append("current")
    elif record.kind in _HISTORICAL_KINDS:
        score += 0.05
        reasons.append("historical_context")
    if record.freshness(query.now) == "STALE":
        score -= 0.35
        reasons.append("stale_penalty")
    if record.state == "UNKNOWN" or record.unknown_fields:
        score -= 0.4
        reasons.append("unknown_penalty")
    score += min(0.1, max(0.0, record.confidence) * 0.1)
    return round(score, 4), reasons


def _eligible_record(record: OwnerContextRecord, query: OwnerContextQuery) -> bool:
    if record.current(query.now):
        return True
    if record.kind in _HISTORICAL_KINDS and record.state in {
        "ACTIVE", "RESOLVED", "STALE", "UNKNOWN",
    }:
        return True
    return record.context_id in query.recent_context_ids


def _bounded_query(query: OwnerContextQuery) -> tuple[dict[str, Any], bool]:
    original = query.as_dict()
    value: dict[str, Any] = {}
    truncated = False
    for key, item in original.items():
        if isinstance(item, str):
            clipped = item[:_QUERY_TEXT_LIMIT]
            value[key] = clipped
            truncated = truncated or clipped != item
            continue
        if isinstance(item, list):
            clipped_items = [
                str(entry)[:_QUERY_LIST_ITEM_LIMIT]
                for entry in item[:_QUERY_LIST_LIMIT]
            ]
            value[key] = clipped_items
            truncated = truncated or clipped_items != item
            continue
        value[key] = item
    value["input_hash"] = hashlib.sha256(
        canonical_json(original).encode("utf-8"),
    ).hexdigest()
    value["truncated"] = truncated
    return value, truncated


def _bounded_warnings(values: list[str]) -> list[str]:
    unique = list(dict.fromkeys(str(value) for value in values))
    result = [
        value[:_WARNING_TEXT_LIMIT]
        for value in unique[:_WARNING_LIMIT]
    ]
    if len(unique) > _WARNING_LIMIT:
        result.append(f"warnings_truncated:{len(unique) - _WARNING_LIMIT}")
    return result


def _set_package_size(package: dict[str, Any]) -> int:
    """Set ``used_chars`` to the exact canonical serialized package size."""

    for _ in range(12):
        size = len(canonical_json(package))
        if package["bounds"]["used_chars"] == size:
            return size
        package["bounds"]["used_chars"] = size
    raise RuntimeError("owner-context package size did not stabilize")


def compile_owner_context(
    database: Any,
    query: OwnerContextQuery | Mapping[str, Any],
    *,
    max_items: int = 24,
    max_chars: int = 6000,
    per_kind_limit: int = 6,
    minimum_relevance: float = 0.3,
) -> dict[str, Any]:
    """Return only context relevant to the requested task and situation."""

    if max_items < 1 or max_chars < 4000 or per_kind_limit < 1:
        raise ValueError("owner-context bounds must be positive")
    request = OwnerContextQuery.from_mapping(query)
    projection = project_owner_context(database, now=request.now)
    scored: dict[str, tuple[float, list[str]]] = {}
    for record in projection.records:
        if not _eligible_record(record, request):
            continue
        score, reasons = _record_relevance(record, request)
        if score >= minimum_relevance:
            scored[record.context_id] = (score, reasons)

    contradiction_by_id: dict[str, dict[str, Any]] = {}
    for conflict in projection.contradictions:
        for context_id in conflict.get("context_ids") or ():
            contradiction_by_id[str(context_id)] = conflict

    units: dict[str, set[str]] = {}
    for context_id in scored:
        conflict = contradiction_by_id.get(context_id)
        if conflict is None:
            units[context_id] = {context_id}
            continue
        ids = {
            str(value)
            for value in conflict.get("context_ids") or ()
            if projection.get(str(value)) is not None
        }
        unit_id = str(conflict["contradiction_id"])
        units[unit_id] = ids
        for member in ids:
            if member not in scored:
                record = projection.get(member)
                if record is not None:
                    score, reasons = _record_relevance(record, request)
                    scored[member] = (
                        max(minimum_relevance, score),
                        [*reasons, "contradiction_counterpart"],
                    )

    ranked_units = sorted(
        units.items(),
        key=lambda item: (
            -max(scored[context_id][0] for context_id in item[1]),
            item[0],
        ),
    )
    included: list[
        tuple[OwnerContextRecord, float, list[str], str]
    ] = []
    selected_units: list[str] = []
    kind_counts: Counter[str] = Counter()
    truncated = False
    warnings = list(projection.warnings)
    for unit_id, context_ids in ranked_units:
        records = [
            projection.get(context_id)
            for context_id in sorted(context_ids)
        ]
        records = [record for record in records if record is not None]
        if not records:
            continue
        counts = Counter(record.kind for record in records)
        exceeds_kind = any(
            kind_counts[kind] + count > per_kind_limit
            for kind, count in counts.items()
        )
        if (
            len(included) + len(records) > max_items
            or exceeds_kind
        ):
            truncated = True
            if unit_id.startswith("owner-context-conflict:"):
                warnings.append(f"contradiction_group_excluded_by_bounds:{unit_id}")
            continue
        for record in records:
            score, reasons = scored[record.context_id]
            included.append((record, score, reasons, unit_id))
        selected_units.append(unit_id)
        kind_counts.update(counts)

    query_value, query_truncated = _bounded_query(request)
    if query_truncated:
        warnings.append("query_echo_truncated")

    def materialize(
        active_units: list[str],
        *,
        was_truncated: bool,
    ) -> dict[str, Any]:
        active = set(active_units)
        active_items = [
            value for value in included if value[3] in active
        ]
        active_items.sort(key=lambda value: (
            -value[1],
            -value[0].priority,
            value[0].kind,
            value[0].context_id,
        ))
        sections: dict[str, list[str]] = {
            name: [] for name in _SECTION_NAMES.values()
        }
        items: list[dict[str, Any]] = []
        for record, score, reasons, _unit_id in active_items:
            value = record.as_dict(now=projection.generated_at)
            value["relevance"] = {
                "score": score,
                "evidence": reasons,
            }
            items.append(value)
            sections[_SECTION_NAMES[record.kind]].append(record.context_id)

        included_conflicts = {
            unit_id
            for unit_id in active_units
            if unit_id.startswith("owner-context-conflict:")
        }
        contradictions = [
            conflict
            for conflict in projection.contradictions
            if str(conflict.get("contradiction_id")) in included_conflicts
        ]
        status = (
            "CONFLICTING"
            if contradictions
            else "READY"
            if items
            else "UNKNOWN"
        )
        package = {
            "contract": OWNER_CONTEXT_PACKAGE_CONTRACT,
            "owner_context_contract": OWNER_CONTEXT_CONTRACT,
            "generated_at": projection.generated_at,
            "status": status,
            "authority": {
                "owner_controls": "AUTHORITATIVE_AND_HIGHER_PRECEDENCE",
                "context": "ADVISORY_ONLY",
                "execution": "NONE",
            },
            "query": query_value,
            "sections": sections,
            "items": items,
            "contradictions": contradictions,
            "bounds": {
                "max_items": max_items,
                "max_chars": max_chars,
                "per_kind_limit": per_kind_limit,
                "used_items": len(items),
                "used_chars": 0,
                "truncated": was_truncated,
            },
            "excluded_count": max(
                0, len(projection.records) - len(items),
            ),
            "warnings": _bounded_warnings(warnings),
        }
        _set_package_size(package)
        return package

    package = materialize(selected_units, was_truncated=truncated)
    while package["bounds"]["used_chars"] > max_chars and selected_units:
        removed = selected_units.pop()
        truncated = True
        if removed.startswith("owner-context-conflict:"):
            warnings.append(
                f"contradiction_group_excluded_by_bounds:{removed}",
            )
        package = materialize(selected_units, was_truncated=True)
    if package["bounds"]["used_chars"] > max_chars:
        raise ValueError(
            "max_chars is too small for the bounded owner-context envelope",
        )
    return package


__all__ = [
    "OWNER_CONTEXT_PACKAGE_CONTRACT",
    "compile_owner_context",
]
