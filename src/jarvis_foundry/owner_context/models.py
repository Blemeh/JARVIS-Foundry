"""Typed contracts for the advisory owner-context read model."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Any, Mapping, Sequence

from ..clock import isoformat, parse


OWNER_CONTEXT_CONTRACT = "jarvis.owner-context.v1"
OWNER_CONTEXT_RECORD_VERSION = "1.0"

ITEM_KINDS = frozenset({
    "GOAL",
    "PRIORITY",
    "PROGRAM",
    "PROJECT",
    "ACTIVE_OBJECTIVE",
    "STANDING_ORDER",
    "DECISION",
    "VETO",
    "CONSTRAINT",
    "PERMISSION",
    "PREFERENCE",
    "UNRESOLVED_ITEM",
    "COMMITMENT",
    "WAITING_ON_OWNER",
    "REJECTED_APPROACH",
    "HISTORICAL_OUTCOME",
})

ITEM_STATES = frozenset({
    "ACTIVE",
    "RESOLVED",
    "REVOKED",
    "SUPERSEDED",
    "STALE",
    "UNKNOWN",
})

PROVENANCE_AUTHORITIES = frozenset({
    "AUTHENTICATED_OWNER",
    "REPOSITORY_AUTHORITY",
    "WORLD_OBSERVATION",
    "VERIFIED_OUTCOME",
    "ADVISORY",
    "UNKNOWN",
})

_HASH = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{1,159}$")


def _text(value: Any, *, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    raw: Sequence[Any]
    if isinstance(value, str):
        raw = (value,)
    elif isinstance(value, Sequence):
        raw = value
    else:
        raise ValueError("scope and provenance lists must be strings")
    return tuple(sorted({
        str(item).strip()
        for item in raw
        if str(item).strip()
    }))


def _ordered_strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    raw: Sequence[Any]
    if isinstance(value, str):
        raw = (value,)
    elif isinstance(value, Sequence):
        raw = value
    else:
        raise ValueError("ordered context lists must be strings")
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        normalized = str(item).strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


def validate_timestamp(value: str | None, *, name: str) -> str | None:
    if value is None:
        return None
    normalized = _text(value, name=name)
    try:
        parse(normalized)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    return normalized


@dataclass(frozen=True, slots=True)
class ContextScope:
    """Deterministic scope labels; an empty scope becomes explicit GLOBAL."""

    global_scope: bool = False
    program_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()
    objective_ids: tuple[str, ...] = ()
    entity_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls,
        value: "ContextScope | Mapping[str, Any] | None",
    ) -> "ContextScope":
        if isinstance(value, cls):
            return value
        raw = dict(value or {})
        scope = cls(
            global_scope=bool(raw.get("global") or raw.get("global_scope")),
            program_ids=_strings(raw.get("program_ids")),
            project_ids=_strings(raw.get("project_ids")),
            objective_ids=_strings(raw.get("objective_ids")),
            entity_refs=_strings(raw.get("entity_refs")),
            tags=_strings(raw.get("tags")),
        )
        if not any((
            scope.global_scope,
            scope.program_ids,
            scope.project_ids,
            scope.objective_ids,
            scope.entity_refs,
            scope.tags,
        )):
            return replace(scope, global_scope=True)
        return scope

    def as_dict(self) -> dict[str, Any]:
        return {
            "global": self.global_scope,
            "program_ids": list(self.program_ids),
            "project_ids": list(self.project_ids),
            "objective_ids": list(self.objective_ids),
            "entity_refs": list(self.entity_refs),
            "tags": list(self.tags),
        }

    def key(self) -> tuple[Any, ...]:
        return (
            self.global_scope,
            self.program_ids,
            self.project_ids,
            self.objective_ids,
            self.entity_refs,
            self.tags,
        )


@dataclass(frozen=True, slots=True)
class ContextProvenance:
    """Required evidence and source identity for one durable context item."""

    source_type: str
    source_id: str
    source_ref: str
    content_hash: str
    captured_at: str
    evidence_refs: tuple[str, ...] = ()
    authority: str = "ADVISORY"
    owner_directive_id: str | None = None

    @classmethod
    def from_content(
        cls,
        *,
        source_type: str,
        source_id: str,
        source_ref: str,
        content: str,
        captured_at: str | None = None,
        evidence_refs: Sequence[str] = (),
        authority: str = "ADVISORY",
        owner_directive_id: str | None = None,
    ) -> "ContextProvenance":
        return cls(
            source_type=_text(source_type, name="provenance source_type"),
            source_id=_text(source_id, name="provenance source_id"),
            source_ref=_text(source_ref, name="provenance source_ref"),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            captured_at=validate_timestamp(
                captured_at or isoformat(), name="provenance captured_at",
            ) or isoformat(),
            evidence_refs=_strings(evidence_refs),
            authority=str(authority or "ADVISORY").upper(),
            owner_directive_id=_optional_text(owner_directive_id),
        ).validated()

    @classmethod
    def from_mapping(
        cls,
        value: "ContextProvenance | Mapping[str, Any]",
    ) -> "ContextProvenance":
        if isinstance(value, cls):
            return value.validated()
        raw = dict(value)
        return cls(
            source_type=_text(raw.get("source_type"), name="provenance source_type"),
            source_id=_text(raw.get("source_id"), name="provenance source_id"),
            source_ref=_text(raw.get("source_ref"), name="provenance source_ref"),
            content_hash=_text(
                raw.get("content_hash") or raw.get("source_hash"),
                name="provenance content_hash",
            ).lower(),
            captured_at=validate_timestamp(
                raw.get("captured_at") or raw.get("observed_at"),
                name="provenance captured_at",
            ) or isoformat(),
            evidence_refs=_strings(raw.get("evidence_refs")),
            authority=str(raw.get("authority") or "ADVISORY").upper(),
            owner_directive_id=_optional_text(raw.get("owner_directive_id")),
        ).validated()

    def validated(self) -> "ContextProvenance":
        if not _HASH.fullmatch(self.content_hash.lower()):
            raise ValueError("provenance content_hash must be sha256 hex")
        if self.authority not in PROVENANCE_AUTHORITIES:
            raise ValueError(f"unsupported provenance authority: {self.authority}")
        if self.owner_directive_id and self.authority != "AUTHENTICATED_OWNER":
            raise ValueError(
                "owner_directive_id requires AUTHENTICATED_OWNER provenance",
            )
        if self.authority == "AUTHENTICATED_OWNER" and not self.owner_directive_id:
            raise ValueError(
                "AUTHENTICATED_OWNER provenance requires owner_directive_id",
            )
        validate_timestamp(self.captured_at, name="provenance captured_at")
        return replace(self, content_hash=self.content_hash.lower())

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "content_hash": self.content_hash,
            "captured_at": self.captured_at,
            "evidence_refs": list(self.evidence_refs),
            "authority": self.authority,
            "owner_directive_id": self.owner_directive_id,
        }


@dataclass(frozen=True, slots=True)
class OwnerContextRecord:
    """One projected owner-context fact, instruction, state, or outcome."""

    context_id: str
    kind: str
    text: str
    state: str
    scope: ContextScope
    priority: float
    confidence: float
    observed_at: str
    effective_at: str
    recorded_at: str
    updated_at: str
    fresh_until: str | None
    provenance: ContextProvenance
    attributes: dict[str, Any] = field(default_factory=dict)
    related_context_ids: tuple[str, ...] = ()
    supersedes: str | None = None
    superseded_by: str | None = None
    contradicts: tuple[str, ...] = ()
    shadowed_by: str | None = None
    source_event_id: str | None = None
    source_event_type: str | None = None
    unknown_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.context_id):
            raise ValueError(f"invalid owner-context id: {self.context_id!r}")
        if self.kind not in ITEM_KINDS:
            raise ValueError(f"unsupported owner-context kind: {self.kind}")
        if self.state not in ITEM_STATES:
            raise ValueError(f"unsupported owner-context state: {self.state}")
        if not self.text.strip():
            raise ValueError("owner-context text is required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("owner-context confidence must be within [0, 1]")
        if not isfinite(float(self.priority)):
            raise ValueError("owner-context priority must be finite")
        for name, value in (
            ("observed_at", self.observed_at),
            ("effective_at", self.effective_at),
            ("recorded_at", self.recorded_at),
            ("updated_at", self.updated_at),
            ("fresh_until", self.fresh_until),
        ):
            validate_timestamp(value, name=name)

    @property
    def fact_key(self) -> str | None:
        for name in ("fact_key", "preference_key", "subject_key"):
            value = self.attributes.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @property
    def fact_value(self) -> Any:
        if "value" in self.attributes:
            return self.attributes["value"]
        if "preference_value" in self.attributes:
            return self.attributes["preference_value"]
        return self.text

    def freshness(self, now: str | None = None) -> str:
        if self.state == "STALE":
            return "STALE"
        if not self.fresh_until:
            return "UNBOUNDED"
        try:
            return (
                "STALE"
                if parse(self.fresh_until) <= parse(now or isoformat())
                else "FRESH"
            )
        except (TypeError, ValueError, OverflowError):
            return "UNKNOWN"

    def current(self, now: str | None = None) -> bool:
        return (
            self.state == "ACTIVE"
            and self.freshness(now) != "STALE"
            and not self.superseded_by
            and not self.shadowed_by
        )

    def with_changes(self, **changes: Any) -> "OwnerContextRecord":
        return replace(self, **changes)

    def as_dict(self, *, now: str | None = None) -> dict[str, Any]:
        return {
            "contract": OWNER_CONTEXT_CONTRACT,
            "context_id": self.context_id,
            "kind": self.kind,
            "text": self.text,
            "state": self.state,
            "current": self.current(now),
            "freshness": self.freshness(now),
            "scope": self.scope.as_dict(),
            "priority": self.priority,
            "confidence": self.confidence,
            "observed_at": self.observed_at,
            "effective_at": self.effective_at,
            "recorded_at": self.recorded_at,
            "updated_at": self.updated_at,
            "fresh_until": self.fresh_until,
            "provenance": self.provenance.as_dict(),
            "attributes": dict(self.attributes),
            "related_context_ids": list(self.related_context_ids),
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "contradicts": list(self.contradicts),
            "shadowed_by": self.shadowed_by,
            "source_event_id": self.source_event_id,
            "source_event_type": self.source_event_type,
            "unknown_fields": list(self.unknown_fields),
        }


@dataclass(frozen=True, slots=True)
class OwnerContextQuery:
    """Task-specific context inputs shared by compilation and resolution."""

    project_id: str | None = None
    program_id: str | None = None
    objective_id: str | None = None
    current_situation: str = ""
    referenced_entity: str | None = None
    requested_task: str = ""
    entity_refs: tuple[str, ...] = ()
    recent_context_ids: tuple[str, ...] = ()
    event_type: str | None = None
    situation_state: str | None = None
    severity: str | None = None
    now: str | None = None

    @classmethod
    def from_mapping(
        cls,
        value: "OwnerContextQuery | Mapping[str, Any]",
    ) -> "OwnerContextQuery":
        if isinstance(value, cls):
            return value
        raw = dict(value)
        now = validate_timestamp(raw.get("now"), name="query now")
        return cls(
            project_id=_optional_text(raw.get("project_id") or raw.get("project")),
            program_id=_optional_text(raw.get("program_id") or raw.get("program")),
            objective_id=_optional_text(
                raw.get("objective_id") or raw.get("objective"),
            ),
            current_situation=str(raw.get("current_situation") or "").strip(),
            referenced_entity=_optional_text(
                raw.get("referenced_entity") or raw.get("entity"),
            ),
            requested_task=str(raw.get("requested_task") or raw.get("task") or "").strip(),
            entity_refs=_strings(raw.get("entity_refs")),
            recent_context_ids=_ordered_strings(raw.get("recent_context_ids")),
            event_type=_optional_text(raw.get("event_type")),
            situation_state=_optional_text(raw.get("situation_state")),
            severity=_optional_text(raw.get("severity")),
            now=now,
        )

    def query_text(self) -> str:
        return " ".join(filter(None, (
            self.project_id,
            self.program_id,
            self.objective_id,
            self.current_situation,
            self.referenced_entity,
            self.requested_task,
            " ".join(self.entity_refs),
        )))

    def identities(self) -> set[str]:
        return {
            value
            for value in (
                "GLOBAL",
                self.project_id,
                self.program_id,
                self.objective_id,
                self.referenced_entity,
                *self.entity_refs,
            )
            if value
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "program_id": self.program_id,
            "objective_id": self.objective_id,
            "current_situation": self.current_situation,
            "referenced_entity": self.referenced_entity,
            "requested_task": self.requested_task,
            "entity_refs": list(self.entity_refs),
            "recent_context_ids": list(self.recent_context_ids),
            "event_type": self.event_type,
            "situation_state": self.situation_state,
            "severity": self.severity,
            "now": self.now,
        }


__all__ = [
    "ContextProvenance",
    "ContextScope",
    "ITEM_KINDS",
    "ITEM_STATES",
    "OWNER_CONTEXT_CONTRACT",
    "OWNER_CONTEXT_RECORD_VERSION",
    "OwnerContextQuery",
    "OwnerContextRecord",
    "PROVENANCE_AUTHORITIES",
    "validate_timestamp",
]
