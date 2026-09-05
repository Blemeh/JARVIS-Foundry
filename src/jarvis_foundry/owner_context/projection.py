"""Rebuildable owner-context projections over ledger and owner controls."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event, canonical_json
from ..owner_directives import OwnerDirectiveRecord, project_owner_directives
from .models import (
    ContextProvenance,
    ContextScope,
    ITEM_KINDS,
    ITEM_STATES,
    OWNER_CONTEXT_CONTRACT,
    OwnerContextRecord,
    validate_timestamp,
)


_EVENT_TYPES = (
    "owner_context.item_recorded",
    "owner_context.item_state_changed",
)

_DIRECTIVE_KIND = {
    "GOAL": "GOAL",
    "REPRIORITIZE": "PRIORITY",
    "VETO_ACTION": "VETO",
    "STOP_MISSION": "VETO",
    "STOP_EVERYTHING": "VETO",
    "PAUSE": "VETO",
    "CONSTRAINT": "CONSTRAINT",
    "ADD_CONSTRAINT": "CONSTRAINT",
    "REDIRECT": "DECISION",
    "CANCEL_MISSION": "DECISION",
    "GATE_REEVALUATE": "DECISION",
    "RESTART_RECOVERY": "DECISION",
    "STANDING_ORDER": "STANDING_ORDER",
}

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


@dataclass(frozen=True, slots=True)
class OwnerContextProjection:
    """Bounded-friendly read model; the ledger remains the source of truth."""

    generated_at: str
    records: tuple[OwnerContextRecord, ...]
    current_ids: tuple[str, ...]
    contradictions: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    @property
    def current(self) -> tuple[OwnerContextRecord, ...]:
        wanted = set(self.current_ids)
        return tuple(record for record in self.records if record.context_id in wanted)

    def get(self, context_id: str) -> OwnerContextRecord | None:
        return next(
            (record for record in self.records if record.context_id == context_id),
            None,
        )

    def by_kind(
        self,
        kind: str,
        *,
        current_only: bool = False,
    ) -> tuple[OwnerContextRecord, ...]:
        source = self.current if current_only else self.records
        normalized = str(kind or "").upper()
        return tuple(record for record in source if record.kind == normalized)

    def as_dict(self) -> dict[str, Any]:
        current = set(self.current_ids)
        sections: dict[str, list[dict[str, Any]]] = {
            name: [] for name in _SECTION_NAMES.values()
        }
        for record in self.records:
            sections[_SECTION_NAMES[record.kind]].append(
                record.as_dict(now=self.generated_at),
            )
        return {
            "contract": OWNER_CONTEXT_CONTRACT,
            "generated_at": self.generated_at,
            "authority": {
                "model": "ADVISORY_EVENT_DERIVED",
                "owner_controls": "AUTHORITATIVE_AND_HIGHER_PRECEDENCE",
                "execution": "NONE",
            },
            "sections": sections,
            "current": [
                record.as_dict(now=self.generated_at)
                for record in self.records
                if record.context_id in current
            ],
            "history": [
                record.as_dict(now=self.generated_at)
                for record in self.records
            ],
            "current_count": len(current),
            "history_count": len(self.records),
            "contradictions": list(self.contradictions),
            "unknown": [
                record.as_dict(now=self.generated_at)
                for record in self.records
                if record.state == "UNKNOWN" or record.unknown_fields
            ],
            "warnings": list(self.warnings),
        }


def _fallback_record(event: Event, error: Exception) -> OwnerContextRecord:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw_id = str(payload.get("context_id") or "").strip()
    identifier = f"unknown:{event.event_id}"
    kind = str(payload.get("kind") or "UNRESOLVED_ITEM").upper()
    if kind not in ITEM_KINDS:
        kind = "UNRESOLVED_ITEM"
    text = str(payload.get("text") or "malformed owner-context record").strip()
    provenance = ContextProvenance(
        source_type=event.source_type or "unknown",
        source_id=event.source_id or "unknown",
        source_ref=f"event:{event.event_id}",
        content_hash=event.payload_hash,
        captured_at=event.occurred_at,
        authority="UNKNOWN",
    ).validated()
    return OwnerContextRecord(
        context_id=identifier,
        kind=kind,
        text=text or "malformed owner-context record",
        state="UNKNOWN",
        scope=ContextScope(global_scope=True),
        priority=0.0,
        confidence=0.0,
        observed_at=event.occurred_at,
        effective_at=event.occurred_at,
        recorded_at=event.recorded_at,
        updated_at=event.recorded_at,
        fresh_until=None,
        provenance=provenance,
        attributes={
            "malformed_context_id": raw_id or None,
            "malformed_payload_shape": sorted(payload),
        },
        source_event_id=event.event_id,
        source_event_type=event.event_type,
        unknown_fields=(f"payload:{type(error).__name__}",),
    )


def _record_from_event(event: Event) -> OwnerContextRecord:
    payload = event.payload
    try:
        kind = str(payload["kind"]).upper()
        state = str(payload.get("state") or "ACTIVE").upper()
        if kind not in ITEM_KINDS:
            raise ValueError(f"unsupported kind {kind}")
        if state not in ITEM_STATES:
            raise ValueError(f"unsupported state {state}")
        observed_at = validate_timestamp(
            payload.get("observed_at"), name="observed_at",
        ) or event.occurred_at
        effective_at = validate_timestamp(
            payload.get("effective_at"), name="effective_at",
        ) or observed_at
        return OwnerContextRecord(
            context_id=str(payload["context_id"]),
            kind=kind,
            text=str(payload["text"]).strip(),
            state=state,
            scope=ContextScope.from_mapping(payload.get("scope")),
            priority=float(payload.get("priority") or 0.0),
            confidence=float(payload.get("confidence", 1.0)),
            observed_at=observed_at,
            effective_at=effective_at,
            recorded_at=event.recorded_at,
            updated_at=event.recorded_at,
            fresh_until=validate_timestamp(
                payload.get("fresh_until"), name="fresh_until",
            ),
            provenance=ContextProvenance.from_mapping(payload["provenance"]),
            attributes=dict(payload.get("attributes") or {}),
            related_context_ids=tuple(payload.get("related_context_ids") or ()),
            supersedes=(
                str(payload["supersedes"]).strip()
                if payload.get("supersedes") else None
            ),
            contradicts=tuple(payload.get("contradicts") or ()),
            source_event_id=event.event_id,
            source_event_type=event.event_type,
        )
    except (KeyError, TypeError, ValueError) as error:
        return _fallback_record(event, error)


def _directive_scope(record: OwnerDirectiveRecord) -> ContextScope:
    if record.command_type == "STANDING_ORDER":
        standing = record.metadata.get("standing_order")
        if isinstance(standing, dict) and isinstance(standing.get("scope"), dict):
            return ContextScope.from_mapping(standing["scope"])
    if not record.target or record.target == "GLOBAL":
        return ContextScope(global_scope=True)
    return ContextScope(entity_refs=(record.target,))


def _directive_kind(record: OwnerDirectiveRecord) -> str | None:
    command_type = str(record.command_type or "")
    kind = _DIRECTIVE_KIND.get(command_type)
    if kind == "CONSTRAINT":
        mode = str(record.metadata.get("mode") or "").upper()
        if mode == "ALLOW":
            return "PERMISSION"
    return kind


def _directive_priority(record: OwnerDirectiveRecord) -> float:
    if record.command_type == "STANDING_ORDER":
        standing = record.metadata.get("standing_order")
        if isinstance(standing, dict):
            try:
                return float(standing.get("priority") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    try:
        return float(record.metadata.get("priority") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _directive_attributes(record: OwnerDirectiveRecord, kind: str) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "command_type": record.command_type,
        "target": record.target,
        "owner_directive_metadata": dict(record.metadata),
    }
    if kind == "STANDING_ORDER":
        standing = record.metadata.get("standing_order")
        if isinstance(standing, dict):
            attributes.update(dict(standing))
            attributes["fact_key"] = f"standing_order:{record.directive_id}"
    elif kind in {"CONSTRAINT", "PERMISSION"}:
        key = record.metadata.get("constraint_key")
        if isinstance(key, str) and key.strip():
            attributes["fact_key"] = f"constraint:{key.strip()}"
            attributes["value"] = str(record.metadata.get("mode") or kind)
    elif kind == "PRIORITY":
        attributes["fact_key"] = f"priority:{record.target or 'GLOBAL'}"
        attributes["value"] = record.exact_text
    elif kind == "VETO":
        attributes["fact_key"] = f"veto:{record.target or 'GLOBAL'}"
        attributes["value"] = record.command_type
    return attributes


def _record_from_directive(record: OwnerDirectiveRecord) -> OwnerContextRecord | None:
    kind = _directive_kind(record)
    if kind is None:
        return None
    exact_text = str(record.exact_text or "").strip()
    hash_value = str(record.command_hash or "").lower()
    if len(hash_value) != 64 or any(ch not in "0123456789abcdef" for ch in hash_value):
        hash_value = hashlib.sha256(exact_text.encode("utf-8")).hexdigest()
    authority = (
        "AUTHENTICATED_OWNER"
        if record.authenticated is True and not record.unknown_fields
        else "UNKNOWN"
    )
    unknown = list(record.unknown_fields)
    if kind == "STANDING_ORDER":
        standing = record.metadata.get("standing_order")
        if not isinstance(standing, dict) or (
            standing.get("contract") != "jarvis.standing-order.v1"
        ):
            unknown.append("standing_order_metadata")
    state = record.state if record.state in ITEM_STATES else "UNKNOWN"
    if unknown:
        state = "UNKNOWN"
    provenance = ContextProvenance(
        source_type=record.source_type,
        source_id=record.source_id,
        source_ref=f"owner-directive:{record.directive_id}",
        content_hash=hash_value,
        captured_at=record.occurred_at,
        evidence_refs=tuple(filter(None, (
            f"event:{record.event_id}",
            f"nonce:{record.nonce}" if record.nonce else None,
        ))),
        authority=authority,
        owner_directive_id=(
            record.directive_id if authority == "AUTHENTICATED_OWNER" else None
        ),
    ).validated()
    return OwnerContextRecord(
        context_id=f"directive:{record.directive_id}",
        kind=kind,
        text=exact_text or "(owner directive text unavailable)",
        state=state,
        scope=_directive_scope(record),
        priority=_directive_priority(record),
        confidence=1.0 if authority == "AUTHENTICATED_OWNER" else 0.0,
        observed_at=record.occurred_at,
        effective_at=record.occurred_at,
        recorded_at=record.recorded_at,
        updated_at=record.revoked_at or record.recorded_at,
        fresh_until=record.expires_at,
        provenance=provenance,
        attributes=_directive_attributes(record, kind),
        supersedes=(
            f"directive:{record.supersedes}" if record.supersedes else None
        ),
        superseded_by=(
            f"directive:{record.superseded_by}" if record.superseded_by else None
        ),
        source_event_id=record.event_id,
        source_event_type="owner_directive.recorded",
        unknown_fields=tuple(dict.fromkeys(unknown)),
    )


def _apply_lifecycle(
    records: dict[str, OwnerContextRecord],
    events: list[Event],
    warnings: list[str],
) -> None:
    for event in events:
        if event.event_type != "owner_context.item_state_changed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        context_id = str(payload.get("context_id") or "")
        record = records.get(context_id)
        if record is None:
            warnings.append(f"state_change_unknown:{context_id or event.event_id}")
            continue
        target = str(payload.get("to_state") or "").upper()
        expected = str(payload.get("from_state") or "").upper()
        if target not in ITEM_STATES or (expected and expected != record.state):
            records[context_id] = record.with_changes(
                state="UNKNOWN",
                updated_at=event.recorded_at,
                unknown_fields=tuple(dict.fromkeys((
                    *record.unknown_fields,
                    "invalid_state_transition",
                ))),
            )
            continue
        changed_at = str(payload.get("changed_at") or event.occurred_at)
        try:
            validate_timestamp(changed_at, name="changed_at")
        except ValueError:
            changed_at = event.recorded_at
        records[context_id] = record.with_changes(
            state=target,
            updated_at=changed_at,
        )


def _apply_supersession(
    records: dict[str, OwnerContextRecord],
    warnings: list[str],
) -> None:
    for context_id, record in tuple(records.items()):
        if not record.supersedes:
            continue
        previous = records.get(record.supersedes)
        if previous is None:
            warnings.append(f"supersedes_unknown:{context_id}:{record.supersedes}")
            records[context_id] = record.with_changes(
                state="UNKNOWN",
                unknown_fields=tuple(dict.fromkeys((
                    *record.unknown_fields,
                    "supersedes_unknown_context",
                ))),
            )
            continue
        if record.state == "ACTIVE":
            records[record.supersedes] = previous.with_changes(
                state="SUPERSEDED",
                superseded_by=context_id,
                updated_at=max(previous.updated_at, record.recorded_at),
            )


def _contradiction_groups(
    records: dict[str, OwnerContextRecord],
    *,
    now: str,
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(first: str, second: str) -> None:
        left, right = find(first), find(second)
        if left != right:
            parent[right] = left

    for record in records.values():
        if not record.current(now):
            continue
        for target in record.contradicts:
            target_record = records.get(target)
            if target_record is not None and target_record.current(now):
                union(record.context_id, target)

    by_fact: dict[tuple[str, tuple[Any, ...]], list[OwnerContextRecord]] = {}
    for record in records.values():
        if not record.fact_key or not record.current(now):
            continue
        by_fact.setdefault((record.fact_key, record.scope.key()), []).append(record)
    for values in by_fact.values():
        distinct = {
            canonical_json(record.fact_value)
            for record in values
        }
        if len(distinct) <= 1:
            continue
        for record in values[1:]:
            union(values[0].context_id, record.context_id)

    grouped: dict[str, set[str]] = {}
    for context_id in tuple(parent):
        grouped.setdefault(find(context_id), set()).add(context_id)
    groups = [ids for ids in grouped.values() if len(ids) > 1]

    contradictions: list[dict[str, Any]] = []
    memberships: dict[str, set[str]] = {}
    for ids in sorted(groups, key=lambda values: tuple(sorted(values))):
        ordered = sorted(ids)
        group_id = hashlib.sha256("|".join(ordered).encode("utf-8")).hexdigest()[:16]
        facts = sorted({
            records[context_id].fact_key
            for context_id in ordered
            if records[context_id].fact_key
        })
        contradictions.append({
            "contradiction_id": f"owner-context-conflict:{group_id}",
            "status": "CONFLICTING",
            "context_ids": ordered,
            "fact_keys": facts,
            "values": [
                {
                    "context_id": context_id,
                    "value": records[context_id].fact_value,
                    "provenance": records[context_id].provenance.as_dict(),
                    "effective_at": records[context_id].effective_at,
                    "freshness": records[context_id].freshness(now),
                }
                for context_id in ordered
            ],
        })
        for context_id in ordered:
            memberships[context_id] = set(ordered) - {context_id}
    return contradictions, memberships


def _shadow_repeated_facts(
    records: dict[str, OwnerContextRecord],
    *,
    contradiction_membership: dict[str, set[str]],
    now: str,
) -> None:
    by_fact: dict[tuple[str, tuple[Any, ...]], list[OwnerContextRecord]] = {}
    for record in records.values():
        if (
            not record.fact_key
            or not record.current(now)
            or record.context_id in contradiction_membership
        ):
            continue
        by_fact.setdefault((record.fact_key, record.scope.key()), []).append(record)
    for values in by_fact.values():
        if len(values) < 2:
            continue
        newest = sorted(
            values,
            key=lambda record: (
                record.effective_at,
                record.recorded_at,
                record.context_id,
            ),
            reverse=True,
        )[0]
        for record in values:
            if record.context_id == newest.context_id:
                continue
            records[record.context_id] = record.with_changes(
                shadowed_by=newest.context_id,
            )


def project_owner_context(
    database: FoundryDatabase,
    *,
    now: str | None = None,
) -> OwnerContextProjection:
    """Reconstruct owner context without trusting a mutable secondary store."""

    generated_at = validate_timestamp(now, name="projection now") or isoformat()
    warnings: list[str] = []
    records: dict[str, OwnerContextRecord] = {}
    context_events = database.events(
        readonly=True,
        event_types=_EVENT_TYPES,
    )
    for event in context_events:
        if event.event_type != "owner_context.item_recorded":
            continue
        record = _record_from_event(event)
        if record.context_id in records:
            duplicate_id = f"duplicate:{record.context_id}:{event.event_id}"
            record = record.with_changes(
                context_id=duplicate_id,
                state="UNKNOWN",
                unknown_fields=tuple(dict.fromkeys((
                    *record.unknown_fields,
                    "duplicate_context_id",
                ))),
            )
            warnings.append(f"duplicate_context_id:{duplicate_id}")
        records[record.context_id] = record

    _apply_lifecycle(records, context_events, warnings)
    for directive in project_owner_directives(database, now=generated_at):
        projected = _record_from_directive(directive)
        if projected is None:
            continue
        if projected.context_id in records:
            warnings.append(f"directive_context_collision:{projected.context_id}")
            continue
        records[projected.context_id] = projected
    _apply_supersession(records, warnings)

    for context_id, record in tuple(records.items()):
        if record.state == "ACTIVE" and record.freshness(generated_at) == "STALE":
            records[context_id] = record.with_changes(state="STALE")

    contradictions, membership = _contradiction_groups(
        records, now=generated_at,
    )
    for context_id, others in membership.items():
        record = records[context_id]
        records[context_id] = record.with_changes(
            contradicts=tuple(sorted(set(record.contradicts) | others)),
        )
    _shadow_repeated_facts(
        records,
        contradiction_membership=membership,
        now=generated_at,
    )

    ordered = tuple(sorted(
        records.values(),
        key=lambda record: (
            record.effective_at,
            record.recorded_at,
            record.context_id,
        ),
        reverse=True,
    ))
    current_ids = tuple(
        record.context_id
        for record in ordered
        if record.current(generated_at)
    )
    return OwnerContextProjection(
        generated_at=generated_at,
        records=ordered,
        current_ids=current_ids,
        contradictions=tuple(contradictions),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def owner_context_read_projection(
    database: FoundryDatabase,
    *,
    now: str | None = None,
    owner_directives: dict[str, Any] | None = None,
    decision_queue: dict[str, Any] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return the bounded, current-only Command Center owner-context surface."""

    projection = project_owner_context(database, now=now)
    generated_at = projection.generated_at
    bound = max(1, min(int(limit), 500))

    # Canonical owner directives are already projected from the ledger by
    # project_owner_context. The optional materialized projection is accepted
    # only to keep this read adapter compatible with Command Center callers.
    del owner_directives

    current = [
        record.as_dict(now=generated_at)
        for record in projection.current
    ]
    known_ids = {
        str(record.get("context_id") or "")
        for record in current
    }
    for item in (decision_queue or {}).get("open", []):
        if not isinstance(item, dict):
            continue
        queue_id = str(item.get("queue_id") or "").strip()
        if not queue_id:
            continue
        context_id = (
            queue_id if queue_id.startswith("attention:")
            else f"attention:{queue_id}"
        )
        if context_id in known_ids:
            continue
        summary = str(
            item.get("summary")
            or item.get("classification")
            or "Owner attention required"
        ).strip()
        opened_at = str(item.get("opened_at") or generated_at)
        source_event_id = str(item.get("source_event_id") or "").strip()
        entity_refs = [
            str(value)
            for value in (item.get("mission_id"), item.get("entity_id"))
            if value
        ]
        objective_ids = (
            [str(item["entity_id"])]
            if item.get("entity_kind") == "objective" and item.get("entity_id")
            else []
        )
        current.append({
            "contract": OWNER_CONTEXT_CONTRACT,
            "context_id": context_id,
            "kind": "WAITING_ON_OWNER",
            "text": summary,
            "state": "ACTIVE",
            "current": True,
            "freshness": "UNBOUNDED",
            "scope": {
                "global": not entity_refs and not objective_ids,
                "program_ids": [],
                "project_ids": [],
                "objective_ids": objective_ids,
                "entity_refs": entity_refs,
                "tags": [],
            },
            "priority": float(
                (item.get("item") or {}).get("importance") or 0.0
            ),
            "confidence": 1.0 if source_event_id else 0.0,
            "observed_at": opened_at,
            "effective_at": opened_at,
            "recorded_at": opened_at,
            "updated_at": opened_at,
            "fresh_until": None,
            "provenance": {
                "source_type": "decision_queue",
                "source_id": queue_id,
                "source_ref": (
                    f"event:{source_event_id}"
                    if source_event_id else f"queue:{queue_id}"
                ),
                "content_hash": hashlib.sha256(
                    summary.encode("utf-8")
                ).hexdigest(),
                "captured_at": opened_at,
                "evidence_refs": list(item.get("evidence_refs") or []),
                "authority": "ADVISORY",
                "owner_directive_id": None,
            },
            "attributes": {
                "classification": item.get("classification"),
                "consequence": item.get("consequence"),
            },
            "related_context_ids": [],
            "supersedes": None,
            "superseded_by": None,
            "contradicts": [],
            "shadowed_by": None,
            "source_event_id": source_event_id or None,
            "source_event_type": item.get("source_event_type"),
            "unknown_fields": [] if source_event_id else ["source_event_id"],
        })
        known_ids.add(context_id)

    current.sort(
        key=lambda record: (
            str(record.get("effective_at") or ""),
            str(record.get("recorded_at") or ""),
            str(record.get("context_id") or ""),
        ),
        reverse=True,
    )
    bounded_current = current[:bound]
    sections: dict[str, list[dict[str, Any]]] = {
        name: [] for name in _SECTION_NAMES.values()
    }
    for record in bounded_current:
        sections[_SECTION_NAMES[str(record["kind"])]].append(record)
    unknown = [
        record.as_dict(now=generated_at)
        for record in projection.records
        if record.state == "UNKNOWN" or record.unknown_fields
    ]
    unknown.extend(
        record for record in bounded_current
        if record.get("unknown_fields")
    )
    return {
        "contract": OWNER_CONTEXT_CONTRACT,
        "status": "AVAILABLE" if bounded_current else "EMPTY",
        "generated_at": generated_at,
        "authority": {
            "model": "ADVISORY_EVENT_DERIVED",
            "owner_controls": "AUTHORITATIVE_AND_HIGHER_PRECEDENCE",
            "execution": "NONE",
        },
        "sections": sections,
        "current": bounded_current,
        "current_count": len(bounded_current),
        "history_count": len(projection.records),
        "history": [],
        "history_surface": "/activity",
        "contradictions": list(projection.contradictions),
        "unknown": unknown[:bound],
        "warnings": list(projection.warnings),
        "bounds": {
            "limit": bound,
            "returned_current": len(bounded_current),
            "history_included": False,
            "truncated": len(current) > bound,
        },
    }


__all__ = [
    "OwnerContextProjection",
    "owner_context_read_projection",
    "project_owner_context",
]
