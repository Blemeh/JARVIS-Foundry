"""Append-only owner-context recording over the existing Foundry ledger."""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Mapping, Sequence

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event
from .models import (
    ContextProvenance,
    ContextScope,
    ITEM_KINDS,
    ITEM_STATES,
    OWNER_CONTEXT_RECORD_VERSION,
    OwnerContextRecord,
    validate_timestamp,
)


_TRANSITIONS = {
    "ACTIVE": frozenset({"RESOLVED", "REVOKED", "SUPERSEDED", "STALE", "UNKNOWN"}),
    "UNKNOWN": frozenset({"REVOKED", "SUPERSEDED"}),
    "STALE": frozenset({"SUPERSEDED", "REVOKED"}),
    "RESOLVED": frozenset({"SUPERSEDED"}),
    "REVOKED": frozenset(),
    "SUPERSEDED": frozenset(),
}


def _context_id(kind: str) -> str:
    return f"oc:{kind.lower()}:{uuid.uuid4().hex[:16]}"


class OwnerContextStore:
    """Writes advisory context facts without creating execution authority."""

    def __init__(self, database: FoundryDatabase) -> None:
        self.database = database

    def get(self, context_id: str) -> OwnerContextRecord | None:
        from .projection import project_owner_context

        projection = project_owner_context(self.database)
        return next(
            (record for record in projection.records if record.context_id == context_id),
            None,
        )

    def record(
        self,
        kind: str,
        text: str,
        *,
        provenance: ContextProvenance | Mapping[str, Any],
        context_id: str | None = None,
        scope: ContextScope | Mapping[str, Any] | None = None,
        state: str = "ACTIVE",
        priority: float = 0.0,
        confidence: float = 1.0,
        observed_at: str | None = None,
        effective_at: str | None = None,
        fresh_until: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        related_context_ids: Sequence[str] = (),
        supersedes: str | None = None,
        contradicts: Sequence[str] = (),
        actor: str = "owner-context",
    ) -> OwnerContextRecord:
        """Record one validated context item.

        The payload is immutable. Corrections create a new item with
        ``supersedes`` or append a lifecycle transition.
        """

        normalized_kind = str(kind or "").upper()
        normalized_state = str(state or "").upper()
        normalized_text = str(text or "").strip()
        if normalized_kind not in ITEM_KINDS:
            raise ValueError(f"unsupported owner-context kind: {normalized_kind}")
        if normalized_kind == "STANDING_ORDER":
            raise ValueError(
                "standing orders must be admitted through OwnerControlService",
            )
        if normalized_state not in ITEM_STATES:
            raise ValueError(f"unsupported owner-context state: {normalized_state}")
        if not normalized_text:
            raise ValueError("owner-context text is required")

        source = ContextProvenance.from_mapping(provenance)
        scope_value = ContextScope.from_mapping(scope)
        observed = validate_timestamp(
            observed_at or source.captured_at, name="observed_at",
        ) or source.captured_at
        effective = validate_timestamp(
            effective_at or observed, name="effective_at",
        ) or observed
        fresh = validate_timestamp(fresh_until, name="fresh_until")
        identifier = context_id or _context_id(normalized_kind)
        if supersedes == identifier:
            raise ValueError("an owner-context item cannot supersede itself")
        if supersedes and self.get(supersedes) is None:
            raise ValueError(f"cannot supersede unknown owner-context item: {supersedes}")

        payload = {
            "record_version": OWNER_CONTEXT_RECORD_VERSION,
            "context_id": identifier,
            "kind": normalized_kind,
            "text": normalized_text,
            "state": normalized_state,
            "scope": scope_value.as_dict(),
            "priority": float(priority),
            "confidence": float(confidence),
            "observed_at": observed,
            "effective_at": effective,
            "fresh_until": fresh,
            "provenance": source.as_dict(),
            "attributes": dict(attributes or {}),
            "related_context_ids": sorted({
                str(value).strip()
                for value in related_context_ids
                if str(value).strip()
            }),
            "supersedes": str(supersedes).strip() if supersedes else None,
            "contradicts": sorted({
                str(value).strip()
                for value in contradicts
                if str(value).strip()
            }),
        }
        event = Event.create(
            "owner_context.item_recorded",
            payload,
            source_type="owner_context",
            source_id=actor,
            occurred_at=effective,
            idempotency_key=f"owner_context.item_recorded:{identifier}",
        )
        self.database.append(event)
        projected = self.get(identifier)
        if projected is None:
            raise RuntimeError("owner-context projection did not reconstruct recorded item")
        return projected

    def transition(
        self,
        context_id: str,
        to_state: str,
        *,
        reason: str,
        changed_at: str | None = None,
        actor: str = "owner-context",
    ) -> OwnerContextRecord:
        current = self.get(context_id)
        if current is None:
            raise KeyError(context_id)
        target = str(to_state or "").upper()
        if target not in ITEM_STATES:
            raise ValueError(f"unsupported owner-context state: {target}")
        if target == current.state:
            return current
        if target not in _TRANSITIONS.get(current.state, frozenset()):
            raise ValueError(f"invalid owner-context transition: {current.state} -> {target}")
        explanation = str(reason or "").strip()
        if not explanation:
            raise ValueError("owner-context transition reason is required")
        changed = validate_timestamp(
            changed_at or isoformat(), name="changed_at",
        ) or isoformat()
        reason_hash = hashlib.sha256(explanation.encode("utf-8")).hexdigest()[:16]
        self.database.append(Event.create(
            "owner_context.item_state_changed",
            {
                "record_version": OWNER_CONTEXT_RECORD_VERSION,
                "context_id": context_id,
                "from_state": current.state,
                "to_state": target,
                "reason": explanation,
                "changed_at": changed,
            },
            source_type="owner_context",
            source_id=actor,
            occurred_at=changed,
            causation_id=current.source_event_id,
            idempotency_key=(
                f"owner_context.item_state_changed:{context_id}:{target}:{reason_hash}"
            ),
        ))
        projected = self.get(context_id)
        if projected is None:
            raise RuntimeError("owner-context projection lost transitioned item")
        return projected

    def resolve(
        self,
        context_id: str,
        *,
        reason: str,
        changed_at: str | None = None,
        actor: str = "owner-context",
    ) -> OwnerContextRecord:
        return self.transition(
            context_id, "RESOLVED", reason=reason,
            changed_at=changed_at, actor=actor,
        )

    def revoke(
        self,
        context_id: str,
        *,
        reason: str,
        changed_at: str | None = None,
        actor: str = "owner-context",
    ) -> OwnerContextRecord:
        return self.transition(
            context_id, "REVOKED", reason=reason,
            changed_at=changed_at, actor=actor,
        )


__all__ = ["OwnerContextStore"]
