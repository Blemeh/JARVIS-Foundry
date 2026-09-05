"""Event-derived delegation intent lifecycle projection."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..clock import isoformat
from ..database import FoundryDatabase
from .contracts import DelegationIntent, DelegationIntentStatus

_EVENT_TYPES = (
    "delegation.intent_recorded",
    "delegation.intent_updated",
    "delegation.intent_admitted",
    "delegation.intent_blocked",
    "delegation.intent_superseded",
    "delegation.intent_completed",
    "delegation.intent_failed",
    "delegation.intent_cancelled",
)
_EXECUTIVE_TERMINAL_EVENTS = {
    "delegation.intent_completed": DelegationIntentStatus.COMPLETED,
    "delegation.intent_failed": DelegationIntentStatus.FAILED,
    "delegation.intent_cancelled": DelegationIntentStatus.CANCELLED,
}
_TERMINAL_STATUSES = frozenset(_EXECUTIVE_TERMINAL_EVENTS.values())


@dataclass(frozen=True, slots=True)
class DelegationProjection:
    generated_at: str
    intents: tuple[DelegationIntent, ...]
    warnings: tuple[str, ...] = ()

    def get(self, intent_id: str) -> DelegationIntent | None:
        return next(
            (intent for intent in self.intents if intent.intent_id == intent_id),
            None,
        )


def _intent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("intent", payload)
    return dict(value) if isinstance(value, dict) else {}


def project_delegation_intents(
    database: FoundryDatabase,
    *,
    now: str | None = None,
) -> DelegationProjection:
    """Reconstruct delegation state without adding a mutable state authority."""

    records: dict[str, DelegationIntent] = {}
    warnings: list[str] = []
    for event in database.events(event_types=_EVENT_TYPES, readonly=True):
        payload = dict(event.payload)
        intent_id = str(payload.get("intent_id") or "").strip()
        try:
            if event.event_type in {
                "delegation.intent_recorded",
                "delegation.intent_updated",
            }:
                intent = DelegationIntent.from_mapping(_intent_payload(payload))
                current = records.get(intent.intent_id)
                if current is not None and current.status in _TERMINAL_STATUSES:
                    continue
                records[intent.intent_id] = intent
                continue
            if not intent_id or intent_id not in records:
                warnings.append(f"{event.event_type}:{intent_id or 'missing'}")
                continue
            current = records[intent_id]
            if current.status in _TERMINAL_STATUSES:
                continue
            updated_at = str(payload.get("updated_at") or event.occurred_at)
            if event.event_type == "delegation.intent_admitted":
                records[intent_id] = replace(
                    current,
                    status=DelegationIntentStatus.ADMITTED,
                    resulting_objective_id=(
                        str(payload.get("objective_id") or "").strip() or None
                    ),
                    resulting_plan_id=(
                        str(payload.get("plan_id") or "").strip() or None
                    ),
                    resulting_step_ids=tuple(
                        str(value)
                        for value in (payload.get("step_ids") or ())
                        if str(value)
                    ),
                    resulting_mission_ids=tuple(
                        str(value)
                        for value in (payload.get("mission_ids") or ())
                        if str(value)
                    ),
                    updated_at=updated_at,
                )
            elif event.event_type == "delegation.intent_blocked":
                reason = str(payload.get("reason_code") or "").strip()
                records[intent_id] = replace(
                    current,
                    status=DelegationIntentStatus.BLOCKED,
                    resulting_objective_id=(
                        str(payload.get("objective_id") or "").strip()
                        or current.resulting_objective_id
                    ),
                    resulting_plan_id=(
                        str(payload.get("plan_id") or "").strip()
                        or current.resulting_plan_id
                    ),
                    resulting_step_ids=tuple(
                        str(value)
                        for value in (
                            payload.get("step_ids")
                            or current.resulting_step_ids
                        )
                        if str(value)
                    ),
                    resulting_mission_ids=tuple(
                        str(value)
                        for value in (
                            payload.get("mission_ids")
                            or current.resulting_mission_ids
                        )
                        if str(value)
                    ),
                    rationale_labels=tuple(dict.fromkeys((
                        *current.rationale_labels,
                        *((reason,) if reason else ()),
                    ))),
                    updated_at=updated_at,
                )
            elif event.event_type == "delegation.intent_superseded":
                records[intent_id] = replace(
                    current,
                    status=DelegationIntentStatus.SUPERSEDED,
                    superseded_by_intent_id=(
                        str(payload.get("superseded_by_intent_id") or "").strip()
                        or None
                    ),
                    updated_at=updated_at,
                )
            elif event.event_type in _EXECUTIVE_TERMINAL_EVENTS:
                reason = str(payload.get("reason_code") or "").strip()
                records[intent_id] = replace(
                    current,
                    status=_EXECUTIVE_TERMINAL_EVENTS[event.event_type],
                    resulting_objective_id=(
                        str(payload.get("objective_id") or "").strip()
                        or current.resulting_objective_id
                    ),
                    resulting_plan_id=(
                        str(payload.get("plan_id") or "").strip()
                        or current.resulting_plan_id
                    ),
                    resulting_step_ids=tuple(
                        str(value)
                        for value in (
                            payload.get("step_ids")
                            or current.resulting_step_ids
                        )
                        if str(value)
                    ),
                    resulting_mission_ids=tuple(
                        str(value)
                        for value in (
                            payload.get("mission_ids")
                            or current.resulting_mission_ids
                        )
                        if str(value)
                    ),
                    rationale_labels=tuple(dict.fromkeys((
                        *current.rationale_labels,
                        *((reason,) if reason else ()),
                    ))),
                    updated_at=updated_at,
                )
        except (TypeError, ValueError) as error:
            warnings.append(
                f"{event.event_type}:{intent_id or 'missing'}:{type(error).__name__}",
            )
    return DelegationProjection(
        generated_at=now or isoformat(),
        intents=tuple(sorted(records.values(), key=lambda row: row.intent_id)),
        warnings=tuple(warnings),
    )


__all__ = ["DelegationProjection", "project_delegation_intents"]
