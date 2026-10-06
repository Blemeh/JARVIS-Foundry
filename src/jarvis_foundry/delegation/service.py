"""Durable delegation interpretation and Executive admission bridge."""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..clock import isoformat
from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event, EventConflict
from ..executive.autonomy import (
    ExecutiveAutonomyState,
    ObjectiveRecord,
    PlanRecord,
    rebuild_executive_state,
)
from ..executive.continuous import schedule_wait_for_signal
from ..owner_directives import OwnerDirectiveRecord, project_owner_directives
from .context import DelegationContext, compile_delegation_context
from .contracts import (
    ConsequenceClass,
    DelegationDecision,
    DelegationIntent,
    DelegationIntentStatus,
    DelegationMode,
    MethodAssessment,
    Reversibility,
)
from .inference import infer_delegation_intent
from .projection import project_delegation_intents

DELEGATION_ADMISSION_CONTRACT = "jarvis.delegation-admission.v1"
_CONTROL_BLOCK_LABELS = frozenset({"STOP_ACTIVE", "VETO_ACTIVE"})
_TERMINAL_OBJECTIVE_STATUSES = frozenset({
    "COMPLETED",
    "FAILED",
    "CANCELLED",
})
_OBJECTIVE_EVENT_TYPES = (
    "executive.objective_created",
    "executive.objective_updated",
    "executive.objective_status_changed",
    "executive.objective_progress_recorded",
)
_PLAN_EVENT_TYPES = ("executive.plan_created", "executive.plan_updated")
_EVENT_CONFLICT_RETRIES = 8


@dataclass(frozen=True, slots=True)
class DelegationOutcome:
    directive_id: str
    status: str
    reason: str
    intent_id: str | None = None
    objective_id: str | None = None
    plan_id: str | None = None
    step_ids: tuple[str, ...] = ()
    wake_id: str | None = None
    clarification_question: str | None = None
    replayed: bool = False
    owner_visible_note: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract": DELEGATION_ADMISSION_CONTRACT,
            "directive_id": self.directive_id,
            "status": self.status,
            "reason": self.reason,
            "intent_id": self.intent_id,
            "objective_id": self.objective_id,
            "plan_id": self.plan_id,
            "step_ids": list(self.step_ids),
            "wake_id": self.wake_id,
            "clarification_question": self.clarification_question,
            "replayed": self.replayed,
            "owner_visible_note": self.owner_visible_note,
        }


@dataclass(frozen=True, slots=True)
class _AdmissionRefusal:
    reason_code: str
    reason: str
    directive_state: str | None = None
    objective_status: str | None = None


@dataclass(frozen=True, slots=True)
class _ExecutiveBinding:
    objective: ObjectiveRecord
    plan: PlanRecord
    step_ids: tuple[str, ...]
    mission_ids: tuple[str, ...]


class _AdmissionAborted(RuntimeError):
    def __init__(self, refusal: _AdmissionRefusal) -> None:
        super().__init__(refusal.reason)
        self.refusal = refusal


def _suffix(intent_id: str) -> str:
    return intent_id.split(":", 1)[-1]


def _strings(
    value: Any,
    *,
    field: str,
    limit: int = 32,
    item_length: int = 256,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        source: Sequence[Any] = (value,)
    elif (
        isinstance(value, Sequence)
        and not isinstance(value, (bytes, bytearray))
    ):
        source = value
    else:
        raise TypeError(f"{field} must be a string or sequence of strings")
    if len(source) > limit:
        raise ValueError(f"{field} exceeds {limit} items")
    result: list[str] = []
    for item in source:
        if not isinstance(item, str):
            raise TypeError(f"{field} entries must be strings")
        text = item.strip()
        if not text or text in result:
            continue
        if len(text) > item_length:
            raise ValueError(
                f"{field} entries must not exceed {item_length} characters",
            )
        result.append(text)
    return tuple(result)


def _directive_identity(record: OwnerDirectiveRecord) -> str:
    return f"owner:{record.owner_id or record.source_id or 'local-owner'}"


def _finite_metadata_number(
    value: Any,
    *,
    field: str,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(number) or number < minimum:
        raise ValueError(f"{field} must be finite and >= {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field} must be <= {maximum:g}")
    return number


def _admission_metadata(
    metadata: Mapping[str, Any],
) -> tuple[dict[str, float], float, float, int]:
    raw_budget = metadata.get("budget")
    if raw_budget is None:
        budget: dict[str, float] = {}
    elif not isinstance(raw_budget, Mapping):
        raise ValueError("budget must be a mapping")
    else:
        if len(raw_budget) > 32:
            raise ValueError("budget exceeds 32 entries")
        budget = {}
        for raw_key, raw_value in raw_budget.items():
            key = str(raw_key).strip()
            if not key or len(key) > 128:
                raise ValueError("budget keys must be non-empty and bounded")
            budget[key] = _finite_metadata_number(
                raw_value,
                field=f"budget.{key}",
            )
    budget.setdefault("max_missions", 1.0)
    budget.setdefault("max_retries", 1.0)
    for field in ("max_missions", "max_retries"):
        if not budget[field].is_integer():
            raise ValueError(f"budget.{field} must be a whole number")
    expected_impact = _finite_metadata_number(
        metadata.get("expected_impact", 0.5),
        field="expected_impact",
        maximum=1.0,
    )
    effort = _finite_metadata_number(
        metadata.get("effort", 0.5),
        field="effort",
        maximum=1.0,
    )
    return budget, expected_impact, effort, int(budget["max_retries"])


def _metadata_text(
    metadata: Mapping[str, Any],
    field: str,
    *,
    maximum: int,
) -> None:
    value = metadata.get(field)
    if value is None:
        return
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if len(value.strip()) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")


def _validate_delegation_metadata(metadata: Mapping[str, Any]) -> None:
    """Reject malformed authenticated metadata before inference or admission."""

    raw_context = metadata.get("context")
    if raw_context is not None and not isinstance(raw_context, Mapping):
        raise ValueError("context must be a mapping")
    context = dict(raw_context or {})
    for source, prefix in ((metadata, ""), (context, "context.")):
        for field in ("recent_context_ids", "entity_refs"):
            if source.get(field) is not None:
                _strings(
                    source.get(field),
                    field=f"{prefix}{field}",
                    limit=12,
                )
    if metadata.get("required_capabilities") is not None:
        _strings(
            metadata.get("required_capabilities"),
            field="required_capabilities",
            limit=32,
        )
    for field, limit in (("success_criteria", 16), ("constraints", 24)):
        value = metadata.get(field)
        if value is not None:
            _strings(
                value,
                field=field,
                limit=limit,
                item_length=1024,
            )
    method = metadata.get("method_assessment")
    if method is not None:
        if not isinstance(method, Mapping):
            raise ValueError("method_assessment must be a mapping")
        if method.get("evidence") is not None:
            _strings(
                method.get("evidence"),
                field="method_assessment.evidence",
                limit=16,
                item_length=512,
            )
        MethodAssessment.from_mapping(method)
    for field, maximum in (
        ("request_id", 256),
        ("desired_outcome", 2048),
        ("actual_goal", 1024),
        ("resource_id", 256),
        ("watch_signal", 512),
        ("plan_id", 256),
        ("step_id", 256),
    ):
        _metadata_text(metadata, field, maximum=maximum)
    for field, maximum in (
        ("project_id", 256),
        ("program_id", 256),
        ("objective_id", 256),
        ("plan_id", 256),
        ("step_id", 256),
        ("current_situation", 4096),
        ("referenced_entity", 256),
        ("event_type", 128),
        ("situation_state", 128),
        ("severity", 128),
    ):
        _metadata_text(context, field, maximum=maximum)
    for field in ("consequential_effect_approved", "owner_approval"):
        value = metadata.get(field)
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{field} must be boolean")
    for field in ("mode", "mode_hint"):
        value = metadata.get(field)
        if value is not None:
            try:
                DelegationMode(str(value).upper())
            except ValueError as error:
                raise ValueError(f"{field} is unsupported") from error
    if metadata.get("consequence_class") is not None:
        try:
            ConsequenceClass(
                str(metadata.get("consequence_class") or "").upper(),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("consequence_class is unsupported") from error
    if metadata.get("reversibility") is not None:
        try:
            Reversibility(str(metadata.get("reversibility") or "").upper())
        except (TypeError, ValueError) as error:
            raise ValueError("reversibility is unsupported") from error
    _admission_metadata(metadata)


def _safe_rejection_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Retain only a valid stable request identity for a blocked intent."""

    request_id = metadata.get("request_id")
    if (
        isinstance(request_id, str)
        and request_id.strip()
        and len(request_id.strip()) <= 256
    ):
        return {"request_id": request_id.strip()}
    return {}


class DelegationService:
    """Interpret and bind owner delegation without owning execution."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        stop_path: str | Path | None = None,
        controller: str = "supervisor",
    ) -> None:
        self.database = database
        self.stop_path = (
            Path(stop_path)
            if stop_path is not None
            else database.path.parent / "EMERGENCY_STOP"
        )
        self.controller = controller

    def _directive_record(self, directive_id: str) -> OwnerDirectiveRecord:
        record = next(
            (
                value
                for value in project_owner_directives(self.database)
                if value.directive_id == directive_id
            ),
            None,
        )
        if record is None:
            raise KeyError(directive_id)
        return record

    def _directive(self, directive_id: str) -> OwnerDirectiveRecord:
        record = self._directive_record(directive_id)
        if record.command_type != "DELEGATE":
            raise ValueError(
                f"directive {directive_id} is not a DELEGATE command",
            )
        if record.authenticated is not True or record.unknown_fields:
            raise PermissionError(
                f"directive {directive_id} lacks authenticated owner truth",
            )
        return record

    def _existing_for_directive(
        self,
        directive_id: str,
    ) -> DelegationIntent | None:
        return next(
            (
                intent
                for intent in project_delegation_intents(
                    self.database,
                ).intents
                if intent.owner_directive_id == directive_id
            ),
            None,
        )

    def _control_reason(
        self,
        intent: DelegationIntent,
        record: OwnerDirectiveRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[str, str] | None:
        identities = {
            "GLOBAL",
            "delegation",
            "executive",
            "objectives",
            intent.intent_id,
            record.directive_id,
            *intent.context_refs,
        }
        if record.target:
            identities.add(record.target)
        for value in (
            intent.program_id,
            intent.project_id,
            intent.context_objective_id,
            intent.resulting_objective_id,
        ):
            if value:
                identities.add(value)
        stopped = emergency_stop_reason(
            self.database,
            self.stop_path,
            identities=identities,
            connection=connection,
        )
        if stopped:
            return "STOP_ACTIVE", stopped
        vetoed = matching_veto_reason(
            self.database,
            identities,
            connection=connection,
        )
        if vetoed:
            return "VETO_ACTIVE", vetoed
        return None

    def _executive_state_on_connection(
        self,
        connection: sqlite3.Connection,
    ) -> ExecutiveAutonomyState:
        event_types = (*_OBJECTIVE_EVENT_TYPES, *_PLAN_EVENT_TYPES)
        placeholders = ", ".join("?" for _ in event_types)
        rows = connection.execute(
            "SELECT * FROM events "
            f"WHERE event_type IN ({placeholders}) ORDER BY sequence",
            event_types,
        ).fetchall()
        events = tuple(
            FoundryDatabase._row_to_event(row)
            for row in rows
        )
        return rebuild_executive_state(
            self.database,
            _events=events,
        )

    def _objective_on_connection(
        self,
        connection: sqlite3.Connection,
        objective_id: str,
    ) -> ObjectiveRecord | None:
        return self._executive_state_on_connection(
            connection,
        ).objectives.get(objective_id)

    @staticmethod
    def _referent_objective_ids(intent: DelegationIntent) -> tuple[str, ...]:
        values: list[str] = []
        for referent in intent.resolved_referents:
            if referent.context_id.startswith("objective:"):
                values.append(referent.context_id.split(":", 1)[1])
            provenance_id = str(
                referent.provenance.get("objective_id") or "",
            ).strip()
            if provenance_id:
                values.append(provenance_id)
            scope_ids = referent.scope.get("objective_ids") or ()
            if isinstance(scope_ids, str):
                scope_ids = (scope_ids,)
            values.extend(str(value).strip() for value in scope_ids if str(value).strip())
        return tuple(dict.fromkeys(values))

    def _executive_binding(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
        preferred_objective_id: str | None = None,
        require_newer_plan_than: str | None = None,
    ) -> tuple[_ExecutiveBinding | None, _AdmissionRefusal]:
        metadata = dict(record.metadata)
        context = dict(metadata.get("context") or {})
        state = rebuild_executive_state(self.database)
        requested_objective_ids = tuple(dict.fromkeys(
            value
            for value in (
                preferred_objective_id,
                str(metadata.get("objective_id") or "").strip() or None,
                str(context.get("objective_id") or "").strip() or None,
                intent.context_objective_id,
                (
                    str(record.target).strip()
                    if record.target and record.target != "GLOBAL"
                    else None
                ),
                *self._referent_objective_ids(intent),
            )
            if value
        ))
        objectives = [
            state.objectives[value]
            for value in requested_objective_ids
            if value in state.objectives
            and state.objectives[value].status not in _TERMINAL_OBJECTIVE_STATUSES
        ]
        if requested_objective_ids and not objectives:
            return None, _AdmissionRefusal(
                "EXECUTIVE_OBJECTIVE_REQUIRED",
                "delegation is waiting for the referenced Executive objective",
            )
        if not requested_objective_ids:
            objectives = [
                objective
                for objective in state.objectives.values()
                if objective.status not in _TERMINAL_OBJECTIVE_STATUSES
            ]
        if len(objectives) != 1:
            reason = (
                "delegation is waiting for an Executive-authored objective"
                if not objectives
                else "delegation cannot choose among multiple Executive objectives"
            )
            return None, _AdmissionRefusal(
                "EXECUTIVE_OBJECTIVE_REQUIRED",
                reason,
            )
        objective = objectives[0]

        explicit_plan_id = (
            str(metadata.get("plan_id") or "").strip()
            or str(context.get("plan_id") or "").strip()
        )
        plans = [
            plan
            for plan in state.plans.values()
            if plan.objective_id == objective.objective_id
            and plan.status == "ACTIVE"
        ]
        if explicit_plan_id:
            plans = [plan for plan in plans if plan.plan_id == explicit_plan_id]
        elif require_newer_plan_than:
            plans = [
                plan for plan in plans
                if plan.plan_id != require_newer_plan_than
            ]
        plan = max(
            plans,
            key=lambda value: (value.version, value.updated_at, value.plan_id),
            default=None,
        )
        if plan is None:
            return None, _AdmissionRefusal(
                (
                    "EXECUTIVE_REPLAN_REQUIRED"
                    if require_newer_plan_than
                    else "EXECUTIVE_PLAN_REQUIRED"
                ),
                (
                    "delegation is waiting for an Executive-authored replacement plan"
                    if require_newer_plan_than
                    else "delegation is waiting for an Executive-authored active plan"
                ),
            )

        explicit_step_id = (
            str(metadata.get("step_id") or "").strip()
            or str(context.get("step_id") or "").strip()
        )
        eligible = [
            step
            for step in plan.steps
            if step.status.upper() not in {
                "COMPLETE",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "SKIPPED",
                "SUPERSEDED",
            }
        ]
        if explicit_step_id:
            eligible = [
                step for step in eligible if step.step_id == explicit_step_id
            ]
        if len(eligible) != 1:
            reason = (
                "delegation is waiting for an Executive-authored executable step"
                if not eligible
                else "delegation requires an explicit Executive step binding"
            )
            return None, _AdmissionRefusal(
                "EXECUTIVE_STEP_REQUIRED",
                reason,
            )
        step = eligible[0]
        mission_ids = tuple(
            value for value in (step.mission_id,) if value
        )
        return _ExecutiveBinding(
            objective=objective,
            plan=plan,
            step_ids=(step.step_id,),
            mission_ids=mission_ids,
        ), _AdmissionRefusal("", "")

    def _record_intent(
        self,
        intent: DelegationIntent,
        *,
        context: DelegationContext,
        record: OwnerDirectiveRecord,
        extra_events: Sequence[Event] = (),
    ) -> None:
        event = Event.create(
            "delegation.intent_recorded",
            {
                "intent_id": intent.intent_id,
                "intent": intent.to_payload(),
                "context": context.to_payload(),
                "owner_directive_event_id": record.event_id,
                "owner_directive_state": record.state,
            },
            source_type="supervisor",
            source_id="delegation-engine",
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=f"delegation.intent_recorded:{intent.intent_id}",
            occurred_at=intent.created_at,
        )
        self.database.append_events([event, *extra_events])

    def _request_clarification(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
    ) -> DelegationOutcome:
        event = Event.create(
            "delegation.clarification_requested",
            {
                "intent_id": intent.intent_id,
                "directive_id": record.directive_id,
                "revision": intent.clarification_revision,
                "question": intent.clarification_question,
                "ambiguities": [
                    ambiguity.to_payload() for ambiguity in intent.ambiguities
                ],
            },
            source_type="supervisor",
            source_id="delegation-engine",
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=(
                f"delegation.clarification_requested:{intent.intent_id}:"
                f"{intent.clarification_revision}"
            ),
        )
        self.database.append(event)
        return DelegationOutcome(
            directive_id=record.directive_id,
            status="ASKING",
            reason="material ambiguity requires one owner clarification",
            intent_id=intent.intent_id,
            objective_id=intent.resulting_objective_id,
            plan_id=intent.resulting_plan_id,
            clarification_question=intent.clarification_question,
            owner_visible_note=(
                intent.method_assessment.reason
                if intent.method_assessment is not None
                else None
            ),
        )

    def _block(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
        reason_code: str,
        reason: str,
        objective_id: str | None = None,
        plan_id: str | None = None,
        step_ids: Sequence[str] = (),
        mission_ids: Sequence[str] = (),
        transition_at: str | None = None,
        transition_identity: str | None = None,
    ) -> DelegationOutcome:
        effective_objective_id = (
            objective_id or intent.resulting_objective_id
        )
        effective_plan_id = plan_id or intent.resulting_plan_id
        effective_step_ids = tuple(step_ids or intent.resulting_step_ids)
        effective_mission_ids = tuple(
            mission_ids or intent.resulting_mission_ids
        )
        updated_at = transition_at or intent.updated_at
        transition = transition_identity or intent.updated_at
        self.database.append(Event.create(
            "delegation.intent_blocked",
            {
                "intent_id": intent.intent_id,
                "directive_id": record.directive_id,
                "reason_code": reason_code,
                "reason": reason,
                "objective_id": effective_objective_id,
                "plan_id": effective_plan_id,
                "step_ids": list(effective_step_ids),
                "mission_ids": list(effective_mission_ids),
                "updated_at": updated_at,
            },
            source_type="supervisor",
            source_id="delegation-engine",
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=(
                f"delegation.intent_blocked:{intent.intent_id}:{reason_code}:"
                f"{transition}"
            ),
        ))
        return DelegationOutcome(
            directive_id=record.directive_id,
            status="BLOCKED",
            reason=reason,
            intent_id=intent.intent_id,
            objective_id=effective_objective_id,
            plan_id=effective_plan_id,
            step_ids=effective_step_ids,
        )

    def _admission_event(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
        objective_id: str,
        plan_id: str,
        step_ids: Sequence[str],
        mission_ids: Sequence[str],
        wake_id: str | None = None,
    ) -> Event:
        return Event.create(
            "delegation.intent_admitted",
            {
                "intent_id": intent.intent_id,
                "directive_id": record.directive_id,
                "objective_id": objective_id,
                "plan_id": plan_id,
                "step_ids": list(step_ids),
                "mission_ids": list(mission_ids),
                "wake_id": wake_id,
                "updated_at": intent.updated_at,
            },
            source_type="supervisor",
            source_id="delegation-engine",
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=(
                f"delegation.intent_admitted:{intent.intent_id}:"
                f"{intent.updated_at}"
            ),
        )

    def _commit_admission(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
        event: Event,
        objective_id: str,
        plan_id: str,
        step_ids: Sequence[str],
    ) -> _AdmissionRefusal | None:
        """Linearize current authority, controls, and admission in one write."""

        current = self._directive_record(record.directive_id)
        if current.state != "ACTIVE":
            return _AdmissionRefusal(
                "OWNER_AUTHORITY_WITHDRAWN",
                f"owner directive is {current.state}",
                current.state,
            )
        refusal: _AdmissionRefusal | None = None
        try:
            with self.database.transaction() as connection:
                directive = connection.execute(
                    "SELECT active FROM directives WHERE directive_id = ?",
                    (record.directive_id,),
                ).fetchone()
                if directive is None or int(directive["active"]) != 1:
                    refusal = _AdmissionRefusal(
                        "OWNER_AUTHORITY_WITHDRAWN",
                        "owner directive lost active authority before admission",
                    )
                else:
                    state = self._executive_state_on_connection(connection)
                    objective = state.objectives.get(objective_id)
                    if (
                        objective is not None
                        and objective.status in _TERMINAL_OBJECTIVE_STATUSES
                    ):
                        refusal = _AdmissionRefusal(
                            f"EXECUTIVE_{objective.status}",
                            (
                                "existing Executive objective became "
                                f"{objective.status} before delegation admission"
                            ),
                            objective_status=objective.status,
                        )
                    elif objective is None:
                        refusal = _AdmissionRefusal(
                            "EXECUTIVE_OBJECTIVE_REQUIRED",
                            "Executive objective disappeared before delegation admission",
                        )
                    else:
                        plan = state.plans.get(plan_id)
                        bound_steps = (
                            {
                                step.step_id
                                for step in plan.steps
                            }
                            if plan is not None
                            else set()
                        )
                        if (
                            plan is None
                            or plan.objective_id != objective_id
                            or plan.status != "ACTIVE"
                            or not set(step_ids).issubset(bound_steps)
                        ):
                            refusal = _AdmissionRefusal(
                                "EXECUTIVE_PLAN_REQUIRED",
                                (
                                    "Executive plan or step changed before "
                                    "delegation admission"
                                ),
                            )
                    if refusal is None:
                        control = self._control_reason(
                            intent,
                            record,
                            connection=connection,
                        )
                        if control is not None:
                            refusal = _AdmissionRefusal(control[0], control[1])
                        else:
                            def final_control_guard(
                                active_connection: sqlite3.Connection,
                            ) -> None:
                                final_control = self._control_reason(
                                    intent,
                                    record,
                                    connection=active_connection,
                                )
                                if final_control is not None:
                                    raise _AdmissionAborted(
                                        _AdmissionRefusal(
                                            final_control[0],
                                            final_control[1],
                                        ),
                                    )

                            self.database.register_precommit_guard(
                                connection,
                                final_control_guard,
                            )
                            self.database.append_events_on_connection(
                                connection,
                                [event],
                            )
        except _AdmissionAborted as error:
            refusal = error.refusal
        if (
            refusal is not None
            and refusal.reason_code == "OWNER_AUTHORITY_WITHDRAWN"
            and refusal.directive_state is None
        ):
            refreshed = self._directive_record(record.directive_id)
            refusal = replace(refusal, directive_state=refreshed.state)
        return refusal

    def _terminal_admission_outcome(
        self,
        refusal: _AdmissionRefusal,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
        objective_id: str,
        plan_id: str,
        step_ids: Sequence[str],
        mission_ids: Sequence[str],
    ) -> DelegationOutcome | None:
        if refusal.objective_status not in _TERMINAL_OBJECTIVE_STATUSES:
            return None
        materialized = replace(
            intent,
            resulting_objective_id=objective_id,
            resulting_plan_id=plan_id,
            resulting_step_ids=tuple(step_ids),
            resulting_mission_ids=tuple(mission_ids),
        )
        return self._terminal_from_executive(
            materialized,
            record=record,
            allow_unadmitted=True,
        )

    def _withdraw_and_block(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
        reason_code: str,
        reason: str,
        objective_id: str | None = None,
        plan_id: str | None = None,
        step_ids: Sequence[str] = (),
        mission_ids: Sequence[str] = (),
        transition_at: str | None = None,
        transition_identity: str | None = None,
    ) -> DelegationOutcome:
        return self._block(
            intent,
            record=record,
            reason_code=reason_code,
            reason=reason,
            objective_id=objective_id,
            plan_id=plan_id,
            step_ids=step_ids,
            mission_ids=mission_ids,
            transition_at=transition_at,
            transition_identity=transition_identity,
        )

    def _clear_control_block(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
    ) -> DelegationIntent:
        cleared = replace(
            intent,
            status=DelegationIntentStatus.READY,
            updated_at=isoformat(),
            rationale_labels=tuple(
                value
                for value in intent.rationale_labels
                if value not in _CONTROL_BLOCK_LABELS
            ),
        )
        self.database.append(Event.create(
            "delegation.intent_updated",
            {
                "intent_id": cleared.intent_id,
                "intent": cleared.to_payload(),
                "reason": "current owner controls allow admission",
            },
            source_type="supervisor",
            source_id="delegation-engine",
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=(
                f"delegation.intent_updated:{cleared.intent_id}:"
                f"control-cleared:{intent.updated_at}"
            ),
        ))
        return cleared

    def _terminal_from_executive(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
        allow_unadmitted: bool = False,
    ) -> DelegationOutcome | None:
        """Mirror terminal Executive truth without becoming its authority."""

        mirrors_objective = (
            intent.status == DelegationIntentStatus.ADMITTED
            or (
                intent.status == DelegationIntentStatus.BLOCKED
                and bool(
                    set(intent.rationale_labels) & _CONTROL_BLOCK_LABELS
                )
            )
            or (
                allow_unadmitted
                and intent.status == DelegationIntentStatus.READY
            )
        )
        if not mirrors_objective or not intent.resulting_objective_id:
            return None
        state = rebuild_executive_state(self.database)
        objective = state.objectives.get(intent.resulting_objective_id)
        terminal = {
            "COMPLETED": (
                "delegation.intent_completed",
                (
                    "existing Executive and Independent Verifier recorded a "
                    "terminal accepted outcome"
                ),
            ),
            "FAILED": (
                "delegation.intent_failed",
                (
                    "existing Executive recorded terminal objective failure; "
                    "delegation success is not claimed"
                ),
            ),
            "CANCELLED": (
                "delegation.intent_cancelled",
                (
                    "existing Executive recorded terminal objective "
                    "cancellation; delegation success is not claimed"
                ),
            ),
        }
        if objective is None or objective.status not in terminal:
            return None
        progress = state.progress.get(objective.objective_id)
        evidence_refs = (
            list(progress.evidence_refs)
            if progress is not None
            else []
        )
        event_type, outcome_reason = terminal[objective.status]
        reason_code = f"EXECUTIVE_{objective.status}"
        event = Event.create(
            event_type,
            {
                "intent_id": intent.intent_id,
                "directive_id": record.directive_id,
                "objective_id": objective.objective_id,
                "plan_id": intent.resulting_plan_id,
                "mission_ids": list(objective.mission_ids),
                "evidence_refs": evidence_refs,
                "reason_code": reason_code,
                "terminal_authority": (
                    "EXISTING_EXECUTIVE_AND_VERIFIER"
                    if objective.status == "COMPLETED"
                    else "EXISTING_EXECUTIVE"
                ),
                "completion_authority": (
                    "EXISTING_EXECUTIVE_AND_VERIFIER"
                    if objective.status == "COMPLETED"
                    else None
                ),
                "updated_at": objective.updated_at,
            },
            source_type="supervisor",
            source_id="delegation-engine",
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=f"{event_type}:{intent.intent_id}",
        )
        self.database.append(event)
        return DelegationOutcome(
            directive_id=record.directive_id,
            status=objective.status,
            reason=outcome_reason,
            intent_id=intent.intent_id,
            objective_id=objective.objective_id,
            plan_id=intent.resulting_plan_id,
            replayed=True,
        )

    def _wait_for_executive(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
        refusal: _AdmissionRefusal,
        objective_id: str | None = None,
        plan_id: str | None = None,
    ) -> DelegationOutcome:
        blocked = self._block(
            intent,
            record=record,
            reason_code=refusal.reason_code,
            reason=refusal.reason,
            objective_id=objective_id,
            plan_id=plan_id,
        )
        return replace(
            blocked,
            status="WAITING_FOR_EXECUTIVE_PLAN",
        )

    def _admit(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
        preferred_objective_id: str | None = None,
        require_newer_plan_than: str | None = None,
    ) -> DelegationOutcome:
        control = self._control_reason(intent, record)
        if control is not None:
            return self._block(
                intent,
                record=record,
                reason_code=control[0],
                reason=control[1],
            )
        metadata = dict(record.metadata)
        try:
            _validate_delegation_metadata(metadata)
        except (TypeError, ValueError) as error:
            return self._block(
                intent,
                record=record,
                reason_code="INVALID_DELEGATION_METADATA",
                reason=f"delegation metadata rejected: {error}",
            )
        binding, refusal = self._executive_binding(
            intent,
            record=record,
            preferred_objective_id=preferred_objective_id,
            require_newer_plan_than=require_newer_plan_than,
        )
        if binding is None:
            return self._wait_for_executive(
                intent,
                record=record,
                refusal=refusal,
                objective_id=preferred_objective_id or intent.context_objective_id,
                plan_id=require_newer_plan_than,
            )
        objective = binding.objective
        plan = binding.plan
        step_ids = binding.step_ids
        materialized_missions = binding.mission_ids
        wake_id = None
        if intent.mode == DelegationMode.WATCH:
            referent_id = (
                intent.resolved_referents[0].context_id
                if intent.resolved_referents
                else intent.intent_id
            )
            signal = str(
                metadata.get("watch_signal")
                or f"material-change:{referent_id}"
            )
            wake = schedule_wait_for_signal(
                self.database,
                objective.objective_id,
                signal=signal,
                reason=intent.desired_outcome,
                controller=self.controller,
            )
            wake_id = str(wake.get("wake_id") or "") or None
        control = self._control_reason(intent, record)
        if control is not None:
            return self._withdraw_and_block(
                intent,
                record=record,
                reason_code=control[0],
                reason=control[1],
                objective_id=objective.objective_id,
                plan_id=plan.plan_id,
                mission_ids=materialized_missions,
            )
        refusal = self._commit_admission(
            intent,
            record=record,
            objective_id=objective.objective_id,
            plan_id=plan.plan_id,
            step_ids=step_ids,
            event=self._admission_event(
                intent,
                record=record,
                objective_id=objective.objective_id,
                plan_id=plan.plan_id,
                step_ids=step_ids,
                mission_ids=materialized_missions,
                wake_id=wake_id,
            ),
        )
        if refusal is not None:
            terminal = self._terminal_admission_outcome(
                refusal,
                intent,
                record=record,
                objective_id=objective.objective_id,
                plan_id=plan.plan_id,
                step_ids=step_ids,
                mission_ids=materialized_missions,
            )
            if terminal is not None:
                return terminal
            return self._withdraw_and_block(
                intent,
                record=record,
                reason_code=refusal.reason_code,
                reason=refusal.reason,
                objective_id=objective.objective_id,
                plan_id=plan.plan_id,
                step_ids=step_ids,
                mission_ids=materialized_missions,
                transition_identity=(
                    f"authority:{refusal.directive_state}"
                    if refusal.directive_state is not None
                    else None
                ),
            )
        status = "WATCHING" if wake_id else "ADMITTED"
        return DelegationOutcome(
            directive_id=record.directive_id,
            status=status,
            reason=(
                "bound to existing Executive and Continuous Executive wake"
                if wake_id
                else "bound to existing Executive objective and plan"
            ),
            intent_id=intent.intent_id,
            objective_id=objective.objective_id,
            plan_id=plan.plan_id,
            step_ids=step_ids,
            wake_id=wake_id,
            owner_visible_note=(
                (
                    f"{intent.method_assessment.reason} "
                    f"Selected approach: "
                    f"{intent.method_assessment.recommended_method}"
                )
                if intent.method_assessment is not None
                else None
            ),
        )

    def _inferred(
        self,
        record: OwnerDirectiveRecord,
        *,
        exact_text: str | None = None,
        clarification_revision: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[DelegationIntent, DelegationContext]:
        text = exact_text or str(record.exact_text or "")
        values = dict(record.metadata if metadata is None else metadata)
        context = compile_delegation_context(
            self.database,
            text,
            metadata=values,
            now=record.occurred_at,
            stop_path=str(self.stop_path),
        )
        intent = infer_delegation_intent({
            "directive_id": record.directive_id,
            "exact_text": text,
            "authenticated": True,
            "owner_id": record.owner_id,
            "source_identity": _directive_identity(record),
            "metadata": values,
        }, context, now=record.occurred_at)
        if clarification_revision:
            intent = replace(
                intent,
                clarification_revision=clarification_revision,
                rationale_labels=tuple(dict.fromkeys((
                    *intent.rationale_labels,
                    "CLARIFICATION_APPLIED",
                ))),
            )
        return intent, context

    def _invalid_metadata_outcome(
        self,
        record: OwnerDirectiveRecord,
        error: TypeError | ValueError,
    ) -> DelegationOutcome:
        existing = self._existing_for_directive(record.directive_id)
        if (
            existing is not None
            and existing.status == DelegationIntentStatus.BLOCKED
            and "INVALID_DELEGATION_METADATA" in existing.rationale_labels
        ):
            return DelegationOutcome(
                directive_id=record.directive_id,
                status="BLOCKED",
                reason=f"delegation metadata rejected: {error}",
                intent_id=existing.intent_id,
                objective_id=existing.resulting_objective_id,
                plan_id=existing.resulting_plan_id,
                replayed=True,
            )
        if existing is None:
            intent, context = self._inferred(
                record,
                metadata=_safe_rejection_metadata(record.metadata),
            )
            self._record_intent(intent, context=context, record=record)
        else:
            intent = existing
        reason = f"delegation metadata rejected: {error}"
        block = self._withdraw_and_block if intent.resulting_objective_id else (
            self._block
        )
        return block(
            intent,
            record=record,
            reason_code="INVALID_DELEGATION_METADATA",
            reason=reason,
            transition_at=record.occurred_at,
            transition_identity=f"metadata:{record.event_id}",
        )

    def _process_clarification(
        self,
        record: OwnerDirectiveRecord,
    ) -> DelegationOutcome:
        intent_id = str(record.metadata.get("clarifies_intent_id") or "")
        projection = project_delegation_intents(self.database)
        prior = projection.get(intent_id)
        existing = self._existing_for_directive(record.directive_id)
        if existing is not None:
            completed = self._terminal_from_executive(
                existing,
                record=record,
            )
            if completed is not None:
                return completed
            if existing.status == DelegationIntentStatus.READY:
                if (
                    prior is not None
                    and "AUTHENTICATED_REDIRECT"
                    in prior.rationale_labels
                    and prior.resulting_objective_id
                ):
                    return self._admit_redirect(
                        existing,
                        prior=prior,
                        record=record,
                    )
                return self._admit(existing, record=record)
            if (
                existing.status
                == DelegationIntentStatus.AWAITING_CLARIFICATION
            ):
                if (
                    prior is not None
                    and "AUTHENTICATED_REDIRECT"
                    in prior.rationale_labels
                    and existing.resulting_objective_id
                ):
                    self._pause_redirect_for_clarification(
                        existing,
                        record=record,
                    )
                return replace(
                    self._request_clarification(existing, record=record),
                    replayed=True,
                )
            return DelegationOutcome(
                directive_id=record.directive_id,
                status=(
                    "ASKING"
                    if existing.status
                    == DelegationIntentStatus.AWAITING_CLARIFICATION
                    else existing.status.value
                ),
                reason="clarification delivery already reconciled",
                intent_id=existing.intent_id,
                objective_id=existing.resulting_objective_id,
                plan_id=existing.resulting_plan_id,
                clarification_question=existing.clarification_question,
                replayed=True,
            )
        try:
            expected_revision = int(
                record.metadata.get("expected_revision"),
            )
        except (TypeError, ValueError):
            expected_revision = -1
        invalid = (
            prior is None
            or prior.status
            != DelegationIntentStatus.AWAITING_CLARIFICATION
            or expected_revision != prior.clarification_revision
            or record.supersedes != prior.owner_directive_id
        )
        if invalid:
            self.database.append(Event.create(
                "delegation.clarification_rejected",
                {
                    "directive_id": record.directive_id,
                    "clarifies_intent_id": intent_id,
                    "expected_revision": expected_revision,
                    "reason": "stale, unknown, or non-superseding clarification",
                },
                source_type="supervisor",
                source_id="delegation-engine",
                correlation_id=record.correlation_id,
                causation_id=record.event_id,
                idempotency_key=(
                    f"delegation.clarification_rejected:{record.directive_id}"
                ),
            ))
            return DelegationOutcome(
                directive_id=record.directive_id,
                status="CLARIFICATION_REJECTED",
                reason="stale, unknown, or non-superseding clarification",
                intent_id=intent_id or None,
            )
        combined = (
            f"{prior.exact_owner_text}\n"
            f"Owner clarification: {record.exact_text or ''}"
        )
        intent, context = self._inferred(
            record,
            exact_text=combined,
            clarification_revision=prior.clarification_revision + 1,
        )
        redirect_clarification = bool(
            "AUTHENTICATED_REDIRECT" in prior.rationale_labels
            and prior.resulting_objective_id
        )
        if redirect_clarification:
            intent = replace(
                intent,
                mode=prior.mode,
                success_criteria=prior.success_criteria,
                explicit_constraints=tuple(dict.fromkeys((
                    *prior.explicit_constraints,
                    *intent.explicit_constraints,
                ))),
                resulting_objective_id=prior.resulting_objective_id,
                resulting_plan_id=prior.resulting_plan_id,
                resulting_mission_ids=prior.resulting_mission_ids,
                rationale_labels=tuple(dict.fromkeys((
                    *intent.rationale_labels,
                    "AUTHENTICATED_REDIRECT",
                    "REDIRECT_CLARIFICATION_APPLIED",
                    "REUSES_EXISTING_EXECUTIVE_OBJECTIVE",
                ))),
            )
        superseded = Event.create(
            "delegation.intent_superseded",
            {
                "intent_id": prior.intent_id,
                "superseded_by_intent_id": intent.intent_id,
                "owner_directive_id": prior.owner_directive_id,
                "superseded_by_directive_id": record.directive_id,
                "updated_at": record.occurred_at,
            },
            source_type="owner",
            source_id=record.owner_id or record.source_id,
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=(
                f"delegation.intent_superseded:{prior.intent_id}:"
                f"{intent.intent_id}"
            ),
        )
        applied = Event.create(
            "delegation.clarification_applied",
            {
                "intent_id": intent.intent_id,
                "prior_intent_id": prior.intent_id,
                "directive_id": record.directive_id,
                "revision": intent.clarification_revision,
            },
            source_type="supervisor",
            source_id="delegation-engine",
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=(
                f"delegation.clarification_applied:{intent.intent_id}"
            ),
        )
        self._record_intent(
            intent,
            context=context,
            record=record,
            extra_events=(superseded, applied),
        )
        if intent.decision == DelegationDecision.ASK:
            return self._request_clarification(intent, record=record)
        if redirect_clarification:
            return self._admit_redirect(intent, prior=prior, record=record)
        return self._admit(intent, record=record)

    def _dissolve_stale_method_mismatch(
        self,
        existing: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
    ) -> DelegationIntent | None:
        """Supersede a stale false method-route interrogation without owner input.

        A parked METHOD_GOAL_MISMATCH whose goal text requests no method can
        never be answered by the owner (both options are vacuous).  When
        current inference finds no ambiguity at all, the question was never
        genuine: record the dissolve on the same intent identity (READY
        status, dissolved codes in rationale) and let admission proceed.
        Any remaining ambiguity keeps replaying ASKING untouched.
        """
        try:
            intent, _context = self._inferred(record)
        except Exception:
            return None
        if intent.decision == DelegationDecision.ASK or intent.ambiguities:
            return None
        dissolved = sorted({
            ambiguity.code
            for ambiguity in existing.ambiguities
            if ambiguity.code == "METHOD_GOAL_MISMATCH"
        })
        if not dissolved:
            return None
        cleared = replace(
            intent,
            status=DelegationIntentStatus.READY,
            updated_at=isoformat(),
            rationale_labels=tuple(dict.fromkeys((
                *intent.rationale_labels,
                "STALE_FALSE_AMBIGUITY_DISSOLVED",
                *(f"DISSOLVED_{code}" for code in dissolved),
            ))),
        )
        self.database.append(Event.create(
            "delegation.intent_updated",
            {
                "intent_id": cleared.intent_id,
                "intent": cleared.to_payload(),
                "reason": (
                    "stale false method-route interrogation dissolved under "
                    "current inference; no owner clarification was provided "
                    f"or needed: {', '.join(dissolved)}"
                ),
            },
            source_type="supervisor",
            source_id="delegation-engine",
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=(
                f"delegation.intent_updated:{cleared.intent_id}:"
                f"ambiguity-dissolved:{existing.updated_at}"
            ),
        ))
        return cleared

    def _process_directive_once(self, directive_id: str) -> DelegationOutcome:
        record = self._directive(directive_id)
        if record.state != "ACTIVE":
            return DelegationOutcome(
                directive_id=directive_id,
                status="IGNORED",
                reason=f"owner directive is {record.state}",
            )
        try:
            _validate_delegation_metadata(record.metadata)
        except (TypeError, ValueError) as error:
            return self._invalid_metadata_outcome(record, error)
        if record.metadata.get("clarifies_intent_id"):
            return self._process_clarification(record)
        existing = self._existing_for_directive(directive_id)
        if existing is not None:
            completed = self._terminal_from_executive(
                existing,
                record=record,
            )
            if completed is not None:
                return completed
            if existing.status == DelegationIntentStatus.ADMITTED:
                control = self._control_reason(existing, record)
                if control is not None:
                    outcome = self._withdraw_and_block(
                        existing,
                        record=record,
                        reason_code=control[0],
                        reason=control[1],
                    )
                    return replace(outcome, replayed=True)
            if existing.status == DelegationIntentStatus.READY:
                outcome = self._admit(existing, record=record)
                return replace(outcome, replayed=True)
            if existing.status == DelegationIntentStatus.BLOCKED:
                control = self._control_reason(existing, record)
                labels = set(existing.rationale_labels)
                if control is None and labels & _CONTROL_BLOCK_LABELS:
                    ready = self._clear_control_block(existing, record=record)
                    outcome = self._admit(ready, record=record)
                    return replace(outcome, replayed=True)
                if control is None and labels & {
                    "EXECUTIVE_OBJECTIVE_REQUIRED",
                    "EXECUTIVE_PLAN_REQUIRED",
                    "EXECUTIVE_REPLAN_REQUIRED",
                    "EXECUTIVE_STEP_REQUIRED",
                }:
                    ready = replace(
                        existing,
                        status=DelegationIntentStatus.READY,
                        rationale_labels=tuple(
                            value
                            for value in existing.rationale_labels
                            if not value.startswith("EXECUTIVE_")
                        ),
                        updated_at=isoformat(),
                    )
                    outcome = self._admit(ready, record=record)
                    return replace(outcome, replayed=True)
            if existing.status == DelegationIntentStatus.AWAITING_CLARIFICATION:
                dissolved = self._dissolve_stale_method_mismatch(
                    existing, record=record,
                )
                if dissolved is not None:
                    outcome = self._admit(dissolved, record=record)
                    return replace(outcome, replayed=True)
            return DelegationOutcome(
                directive_id=directive_id,
                status=(
                    "ASKING"
                    if existing.status
                    == DelegationIntentStatus.AWAITING_CLARIFICATION
                    else existing.status.value
                ),
                reason="delegation directive already reconciled",
                intent_id=existing.intent_id,
                objective_id=existing.resulting_objective_id,
                plan_id=existing.resulting_plan_id,
                step_ids=existing.resulting_step_ids,
                clarification_question=existing.clarification_question,
                replayed=True,
            )
        intent, context = self._inferred(record)
        self._record_intent(intent, context=context, record=record)
        if intent.decision == DelegationDecision.ASK:
            return self._request_clarification(intent, record=record)
        control = self._control_reason(intent, record)
        if control is not None:
            return self._block(
                intent,
                record=record,
                reason_code=control[0],
                reason=control[1],
            )
        return self._admit(intent, record=record)

    def process_directive(self, directive_id: str) -> DelegationOutcome:
        conflict: EventConflict | None = None
        for _ in range(_EVENT_CONFLICT_RETRIES):
            try:
                return self._process_directive_once(directive_id)
            except EventConflict as error:
                conflict = error
        if conflict is not None:
            raise conflict
        raise RuntimeError("delegation conflict retry loop did not execute")

    @staticmethod
    def _redirect_target_intent(
        record: OwnerDirectiveRecord,
        intents: Sequence[DelegationIntent],
    ) -> DelegationIntent | None:
        if record.supersedes:
            return next(
                (
                    intent
                    for intent in intents
                    if intent.owner_directive_id == record.supersedes
                ),
                None,
            )
        target = str(record.target or "").strip()
        if not target or target == "GLOBAL":
            return None
        candidates = []
        for intent in intents:
            if intent.owner_directive_id == record.directive_id:
                continue
            identities = {
                intent.intent_id,
                intent.owner_directive_id,
                *intent.context_refs,
                *intent.resulting_mission_ids,
            }
            for value in (
                intent.resulting_objective_id,
                intent.resulting_plan_id,
                intent.context_objective_id,
                intent.project_id,
                intent.program_id,
            ):
                if value:
                    identities.add(value)
            if target in identities:
                candidates.append(intent)
        if len(candidates) != 1:
            return None
        return candidates[0]

    @staticmethod
    def _redirect_metadata(
        record: OwnerDirectiveRecord,
        prior: DelegationIntent,
    ) -> dict[str, Any]:
        metadata = dict(record.metadata)
        context = dict(metadata.get("context") or {})
        recent = list(_strings(
            context.get("recent_context_ids"),
            field="context.recent_context_ids",
            limit=12,
        ))
        if prior.resulting_objective_id:
            objective_ref = f"objective:{prior.resulting_objective_id}"
            recent = [
                objective_ref,
                *(value for value in recent if value != objective_ref),
            ]
            context.setdefault(
                "objective_id",
                prior.resulting_objective_id,
            )
        context["recent_context_ids"] = recent[:12]
        metadata["context"] = context
        return metadata

    def _record_unmaterialized_redirect_predecessor(
        self,
        record: OwnerDirectiveRecord,
    ) -> DelegationIntent | None:
        if not record.supersedes:
            return None
        predecessor = next(
            (
                value
                for value in project_owner_directives(self.database)
                if value.directive_id == record.supersedes
            ),
            None,
        )
        if (
            predecessor is None
            or predecessor.command_type != "DELEGATE"
            or predecessor.authenticated is not True
            or predecessor.unknown_fields
        ):
            return None
        try:
            _validate_delegation_metadata(predecessor.metadata)
        except (TypeError, ValueError):
            return None
        intent, context = self._inferred(predecessor)
        self._record_intent(
            intent,
            context=context,
            record=predecessor,
        )
        return intent

    def _pause_redirect_for_clarification(
        self,
        intent: DelegationIntent,
        *,
        record: OwnerDirectiveRecord,
    ) -> None:
        del intent, record

    def _admit_redirect(
        self,
        intent: DelegationIntent,
        *,
        prior: DelegationIntent,
        record: OwnerDirectiveRecord,
    ) -> DelegationOutcome:
        outcome = self._admit(
            intent,
            record=record,
            preferred_objective_id=prior.resulting_objective_id,
            require_newer_plan_than=prior.resulting_plan_id,
        )
        if outcome.status in {"ADMITTED", "WATCHING"}:
            return replace(
                outcome,
                reason=(
                    "authenticated redirect bound to an Executive-authored "
                    "replacement plan and step"
                ),
            )
        return outcome

    def _process_redirect_once(
        self,
        record: OwnerDirectiveRecord,
    ) -> DelegationOutcome:
        try:
            _validate_delegation_metadata(record.metadata)
        except (TypeError, ValueError) as error:
            return self._invalid_metadata_outcome(record, error)
        intents = project_delegation_intents(self.database).intents
        prior = self._redirect_target_intent(record, intents)
        if prior is None:
            prior = self._record_unmaterialized_redirect_predecessor(record)
        if prior is None:
            return DelegationOutcome(
                directive_id=record.directive_id,
                status="IGNORED",
                reason=(
                    "redirect does not identify exactly one delegation intent"
                ),
            )
        objective = (
            rebuild_executive_state(self.database).objectives.get(
                prior.resulting_objective_id,
            )
            if prior.resulting_objective_id
            else None
        )
        target_terminal = bool(
            prior.status in {
                DelegationIntentStatus.COMPLETED,
                DelegationIntentStatus.FAILED,
                DelegationIntentStatus.CANCELLED,
            }
            or (
                prior.resulting_objective_id
                and (
                    objective is None
                    or objective.status in {
                        "COMPLETED",
                        "FAILED",
                        "CANCELLED",
                    }
                )
            )
        )
        if (
            objective is not None
            and objective.status in {"COMPLETED", "FAILED", "CANCELLED"}
            and prior.status == DelegationIntentStatus.ADMITTED
        ):
            predecessor_record = next(
                (
                    value
                    for value in project_owner_directives(self.database)
                    if value.directive_id == prior.owner_directive_id
                ),
                None,
            )
            if predecessor_record is not None:
                self._terminal_from_executive(
                    prior,
                    record=predecessor_record,
                )
                prior = (
                    project_delegation_intents(self.database).get(
                        prior.intent_id,
                    )
                    or prior
                )
        existing = self._existing_for_directive(record.directive_id)
        if existing is not None:
            completed = self._terminal_from_executive(
                existing,
                record=record,
            )
            if completed is not None:
                return completed
            if (
                target_terminal
                and existing.status
                in {
                    DelegationIntentStatus.READY,
                    DelegationIntentStatus.AWAITING_CLARIFICATION,
                }
            ):
                return replace(
                    self._block(
                        existing,
                        record=record,
                        reason_code="REDIRECT_TARGET_TERMINAL",
                        reason=(
                            "authenticated redirect target is absent or "
                            "terminal"
                        ),
                    ),
                    replayed=True,
                )
            if existing.status == DelegationIntentStatus.ADMITTED:
                control = self._control_reason(existing, record)
                if control is not None:
                    outcome = self._withdraw_and_block(
                        existing,
                        record=record,
                        reason_code=control[0],
                        reason=control[1],
                    )
                    return replace(outcome, replayed=True)
            if existing.status == DelegationIntentStatus.READY:
                outcome = self._admit_redirect(
                    existing,
                    prior=prior,
                    record=record,
                )
                return replace(outcome, replayed=True)
            if existing.status == DelegationIntentStatus.BLOCKED:
                control = self._control_reason(existing, record)
                labels = set(existing.rationale_labels)
                if (
                    control is None
                    and labels & _CONTROL_BLOCK_LABELS
                ):
                    ready = self._clear_control_block(
                        existing,
                        record=record,
                    )
                    outcome = self._admit_redirect(
                        ready,
                        prior=prior,
                        record=record,
                    )
                    return replace(outcome, replayed=True)
                if control is None and labels & {
                    "EXECUTIVE_OBJECTIVE_REQUIRED",
                    "EXECUTIVE_PLAN_REQUIRED",
                    "EXECUTIVE_REPLAN_REQUIRED",
                    "EXECUTIVE_STEP_REQUIRED",
                }:
                    ready = replace(
                        existing,
                        status=DelegationIntentStatus.READY,
                        rationale_labels=tuple(
                            value
                            for value in existing.rationale_labels
                            if not value.startswith("EXECUTIVE_")
                        ),
                        updated_at=isoformat(),
                    )
                    outcome = self._admit_redirect(
                        ready,
                        prior=prior,
                        record=record,
                    )
                    return replace(outcome, replayed=True)
            if (
                existing.status
                == DelegationIntentStatus.AWAITING_CLARIFICATION
            ):
                self._pause_redirect_for_clarification(
                    existing,
                    record=record,
                )
                return replace(
                    self._request_clarification(existing, record=record),
                    replayed=True,
                )
            return DelegationOutcome(
                directive_id=record.directive_id,
                status=(
                    "ASKING"
                    if existing.status
                    == DelegationIntentStatus.AWAITING_CLARIFICATION
                    else existing.status.value
                ),
                reason="redirect directive already reconciled",
                intent_id=existing.intent_id,
                objective_id=existing.resulting_objective_id,
                plan_id=existing.resulting_plan_id,
                step_ids=existing.resulting_step_ids,
                clarification_question=existing.clarification_question,
                replayed=True,
            )
        metadata = self._redirect_metadata(record, prior)
        inferred, context = self._inferred(record, metadata=metadata)
        decision = (
            DelegationDecision.ASK
            if inferred.decision == DelegationDecision.ASK
            else prior.decision
        )
        status = (
            DelegationIntentStatus.AWAITING_CLARIFICATION
            if decision == DelegationDecision.ASK
            else DelegationIntentStatus.READY
        )
        intent = replace(
            inferred,
            desired_outcome=(
                inferred.desired_outcome
                if inferred.method_assessment is not None
                else (
                    f"{prior.desired_outcome.rstrip('.')}. Owner redirect: "
                    f"{str(record.exact_text or '').strip()}"
                )[:2048]
            ),
            mode=prior.mode,
            decision=decision,
            status=status,
            success_criteria=prior.success_criteria,
            explicit_constraints=tuple(dict.fromkeys((
                *prior.explicit_constraints,
                *inferred.explicit_constraints,
            ))),
            rationale_labels=tuple(dict.fromkeys((
                *(
                    label
                    for label in inferred.rationale_labels
                    if not label.startswith("MODE_")
                ),
                f"MODE_{prior.mode.value}",
                "AUTHENTICATED_REDIRECT",
                "REUSES_EXISTING_EXECUTIVE_OBJECTIVE",
            ))),
            resulting_objective_id=prior.resulting_objective_id,
            resulting_plan_id=prior.resulting_plan_id,
            resulting_mission_ids=prior.resulting_mission_ids,
        )
        if target_terminal:
            self._record_intent(
                intent,
                context=context,
                record=record,
            )
            return self._block(
                intent,
                record=record,
                reason_code="REDIRECT_TARGET_TERMINAL",
                reason="authenticated redirect target is absent or terminal",
                objective_id=prior.resulting_objective_id,
                plan_id=prior.resulting_plan_id,
                mission_ids=prior.resulting_mission_ids,
            )
        superseded = Event.create(
            "delegation.intent_superseded",
            {
                "intent_id": prior.intent_id,
                "superseded_by_intent_id": intent.intent_id,
                "owner_directive_id": prior.owner_directive_id,
                "superseded_by_directive_id": record.directive_id,
                "updated_at": record.occurred_at,
            },
            source_type="owner",
            source_id=record.owner_id or record.source_id,
            correlation_id=record.correlation_id,
            causation_id=record.event_id,
            idempotency_key=(
                f"delegation.intent_superseded:{prior.intent_id}:"
                f"{intent.intent_id}"
            ),
        )
        self._record_intent(
            intent,
            context=context,
            record=record,
            extra_events=(superseded,),
        )
        if intent.decision == DelegationDecision.ASK:
            self._pause_redirect_for_clarification(
                intent,
                record=record,
            )
            return self._request_clarification(intent, record=record)
        return self._admit_redirect(intent, prior=prior, record=record)

    def _process_redirect(
        self,
        record: OwnerDirectiveRecord,
    ) -> DelegationOutcome:
        conflict: EventConflict | None = None
        for _ in range(_EVENT_CONFLICT_RETRIES):
            try:
                return self._process_redirect_once(record)
            except EventConflict as error:
                conflict = error
        if conflict is not None:
            raise conflict
        raise RuntimeError("redirect conflict retry loop did not execute")

    def reconcile(self) -> list[DelegationOutcome]:
        records = [
            record
            for record in project_owner_directives(self.database)
            if record.command_type in {"DELEGATE", "REDIRECT"}
        ]
        outcomes: list[DelegationOutcome] = []
        active = sorted(
            (record for record in records if record.state == "ACTIVE"),
            key=lambda record: (record.occurred_at, record.event_id),
        )
        for record in active:
            if record.command_type == "DELEGATE":
                outcomes.append(self.process_directive(record.directive_id))
        for record in active:
            if record.command_type == "REDIRECT":
                outcomes.append(self._process_redirect(record))
        intents = project_delegation_intents(self.database).intents
        by_directive = {record.directive_id: record for record in records}
        by_intent_directive = {
            intent.owner_directive_id: intent for intent in intents
        }
        for intent in intents:
            record = by_directive.get(intent.owner_directive_id)
            if record is None or record.state == "ACTIVE":
                continue
            if intent.status in {
                DelegationIntentStatus.SUPERSEDED,
                DelegationIntentStatus.COMPLETED,
                DelegationIntentStatus.FAILED,
                DelegationIntentStatus.CANCELLED,
            }:
                continue
            superseding = (
                by_intent_directive.get(record.superseded_by)
                if record.superseded_by
                else None
            )
            trigger = (
                by_directive.get(record.superseded_by)
                if record.superseded_by
                else None
            )
            transition_at = (
                trigger.occurred_at
                if trigger is not None
                else record.revoked_at
                or record.expires_at
                or record.occurred_at
            )
            transition_identity = (
                trigger.event_id
                if trigger is not None
                else f"{record.state}:{transition_at}"
            )
            if superseding is not None:
                self.database.append(Event.create(
                    "delegation.intent_superseded",
                    {
                        "intent_id": intent.intent_id,
                        "superseded_by_intent_id": superseding.intent_id,
                        "owner_directive_id": record.directive_id,
                        "superseded_by_directive_id": record.superseded_by,
                        "updated_at": transition_at,
                    },
                    source_type="owner",
                    source_id=(
                        trigger.owner_id or trigger.source_id
                        if trigger is not None
                        else record.owner_id or record.source_id
                    ),
                    correlation_id=record.correlation_id,
                    causation_id=(
                        trigger.event_id if trigger is not None
                        else record.event_id
                    ),
                    idempotency_key=(
                        f"delegation.intent_superseded:{intent.intent_id}:"
                        f"{superseding.intent_id}"
                    ),
                ))
            else:
                self.database.append(Event.create(
                    "delegation.intent_blocked",
                    {
                        "intent_id": intent.intent_id,
                        "directive_id": record.directive_id,
                        "reason_code": "OWNER_AUTHORITY_WITHDRAWN",
                        "reason": f"owner directive is {record.state}",
                        "objective_id": intent.resulting_objective_id,
                        "plan_id": intent.resulting_plan_id,
                        "step_ids": list(intent.resulting_step_ids),
                        "mission_ids": list(intent.resulting_mission_ids),
                        "updated_at": transition_at,
                    },
                    source_type="owner",
                    source_id=record.owner_id or record.source_id,
                    correlation_id=record.correlation_id,
                    causation_id=record.event_id,
                    idempotency_key=(
                        f"delegation.intent_blocked:{intent.intent_id}:"
                        "OWNER_AUTHORITY_WITHDRAWN:"
                        f"{transition_identity}"
                    ),
                ))
            outcomes.append(DelegationOutcome(
                directive_id=record.directive_id,
                status=record.state,
                reason="owner authority reconciled",
                intent_id=intent.intent_id,
                objective_id=intent.resulting_objective_id,
                plan_id=intent.resulting_plan_id,
                step_ids=intent.resulting_step_ids,
            ))
        return outcomes


def reconcile_owner_delegations(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    controller: str = "supervisor",
) -> list[DelegationOutcome]:
    return DelegationService(
        database,
        stop_path=stop_path,
        controller=controller,
    ).reconcile()


__all__ = [
    "DELEGATION_ADMISSION_CONTRACT",
    "DelegationOutcome",
    "DelegationService",
    "reconcile_owner_delegations",
]
