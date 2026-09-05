"""Transparent event-derived calibration for delegation interpretation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..clock import isoformat
from ..database import FoundryDatabase
from ..owner_directives import project_owner_directives
from .contracts import DelegationIntent, DelegationIntentStatus, DelegationMode
from .projection import project_delegation_intents


DELEGATION_CALIBRATION_CONTRACT = "jarvis.delegation-calibration.v1"
_MIN_EVALUATED = 2
_MIN_ADJUSTMENT = -0.20
_MAX_ADJUSTMENT = 0.08
_CALIBRATION_EVENTS = (
    "delegation.clarification_requested",
    "delegation.clarification_applied",
    "delegation.clarification_rejected",
    "delegation.feedback_recorded",
    "delegation.intent_completed",
    "delegation.intent_superseded",
    "owner_control.duplicate_received",
)


def _bounded_adjustment(
    *,
    confirmed: int,
    corrected: int,
    rejected: int,
) -> float:
    evaluated = confirmed + corrected + rejected
    if evaluated < _MIN_EVALUATED:
        return 0.0
    value = confirmed * 0.03 - corrected * 0.08 - rejected * 0.05
    return round(max(_MIN_ADJUSTMENT, min(_MAX_ADJUSTMENT, value)), 4)


def _resolution_status(intent: DelegationIntent) -> str:
    for label in intent.rationale_labels:
        if label.startswith("REFERENT_"):
            return label.removeprefix("REFERENT_")
    if intent.resolved_referents:
        return "RESOLVED"
    return "UNKNOWN"


def _empty_stats() -> dict[str, int]:
    return {
        "interpreted": 0,
        "confirmed": 0,
        "corrected": 0,
        "rejected": 0,
        "questions": 0,
    }


def build_delegation_calibration(
    database: FoundryDatabase,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Measure interpretation outcomes without creating a learning authority."""

    projection = project_delegation_intents(database, now=now)
    intents = projection.intents
    events = database.events(
        readonly=True,
        event_types=_CALIBRATION_EVENTS,
    )
    owner_records = {
        record.directive_id: record
        for record in project_owner_directives(database, now=now)
    }
    clarification_requested = {
        str(event.payload.get("intent_id") or "")
        for event in events
        if event.event_type == "delegation.clarification_requested"
    }
    clarification_applied = {
        str(event.payload.get("prior_intent_id") or "")
        for event in events
        if event.event_type == "delegation.clarification_applied"
    }
    explicit_unnecessary = sum(
        1
        for event in events
        if event.event_type == "delegation.feedback_recorded"
        and str(event.payload.get("question_usefulness") or "").upper()
        == "UNNECESSARY"
    )
    explicit_mode_incorrect = {
        str(event.payload.get("intent_id") or "")
        for event in events
        if event.event_type == "delegation.feedback_recorded"
        and str(event.payload.get("mode_correctness") or "").upper()
        == "INCORRECT"
    }
    explicit_interpretation_incorrect = {
        str(event.payload.get("intent_id") or "")
        for event in events
        if event.event_type == "delegation.feedback_recorded"
        and str(event.payload.get("interpretation_correctness") or "").upper()
        == "INCORRECT"
    }
    mode_stats: dict[str, dict[str, int]] = defaultdict(_empty_stats)
    resolution_stats: dict[str, dict[str, int]] = defaultdict(_empty_stats)
    owner_corrections = 0
    owner_rejections = 0
    verified_outcomes = 0
    method_assessed = 0
    method_verified = 0
    method_corrected = 0
    success_conditions_verified = 0
    success_conditions_rejected = 0
    objective_ids: list[str] = []
    directive_ids: list[str] = []
    for intent in intents:
        mode = intent.mode.value
        resolution = _resolution_status(intent)
        mode_stats[mode]["interpreted"] += 1
        resolution_stats[resolution]["interpreted"] += 1
        if intent.intent_id in clarification_requested:
            mode_stats[mode]["questions"] += 1
            resolution_stats[resolution]["questions"] += 1
        record = owner_records.get(intent.owner_directive_id)
        revoked = bool(record is not None and record.state == "REVOKED")
        clarified = intent.intent_id in clarification_applied
        corrected = (
            (
                intent.status == DelegationIntentStatus.SUPERSEDED
                and not clarified
            )
            or intent.intent_id in explicit_mode_incorrect
            or intent.intent_id in explicit_interpretation_incorrect
        )
        completed = intent.status == DelegationIntentStatus.COMPLETED
        if completed:
            mode_stats[mode]["confirmed"] += 1
            resolution_stats[resolution]["confirmed"] += 1
            verified_outcomes += 1
            success_conditions_verified += 1
        elif corrected:
            mode_stats[mode]["corrected"] += 1
            resolution_stats[resolution]["corrected"] += 1
            owner_corrections += 1
            success_conditions_rejected += 1
        elif revoked:
            mode_stats[mode]["rejected"] += 1
            resolution_stats[resolution]["rejected"] += 1
            owner_rejections += 1
            success_conditions_rejected += 1
        if intent.method_assessment is not None:
            method_assessed += 1
            if completed:
                method_verified += 1
            elif corrected or revoked:
                method_corrected += 1
        if intent.resulting_objective_id:
            objective_ids.append(intent.resulting_objective_id)
        directive_ids.append(intent.owner_directive_id)
    mode_adjustments = {
        mode.value: _bounded_adjustment(
            confirmed=mode_stats[mode.value]["confirmed"],
            corrected=mode_stats[mode.value]["corrected"],
            rejected=mode_stats[mode.value]["rejected"],
        )
        for mode in DelegationMode
    }
    resolution_adjustments = {
        status: _bounded_adjustment(
            confirmed=stats["confirmed"],
            corrected=stats["corrected"],
            rejected=stats["rejected"],
        )
        for status, stats in sorted(resolution_stats.items())
    }
    duplicate_deliveries = sum(
        event.event_type == "owner_control.duplicate_received"
        for event in events
    )
    return {
        "contract": DELEGATION_CALIBRATION_CONTRACT,
        "generated_at": now or isoformat(),
        "metrics": {
            "total_intents": len(intents),
            "reference_resolutions_confirmed": sum(
                stats["confirmed"] for stats in resolution_stats.values()
            ),
            "owner_corrections": owner_corrections,
            "owner_rejections": owner_rejections,
            "questions_asked": len(clarification_requested),
            "useful_questions": len(clarification_applied),
            "unnecessary_questions": explicit_unnecessary,
            "unnecessary_questions_status": (
                "OBSERVED" if explicit_unnecessary else "NOT_OBSERVED"
            ),
            "verified_final_outcomes": verified_outcomes,
            "success_conditions_verified": success_conditions_verified,
            "success_conditions_rejected": success_conditions_rejected,
            "method_assessments": method_assessed,
            "method_assessments_verified": method_verified,
            "method_assessments_corrected": method_corrected,
            "duplicate_deliveries": duplicate_deliveries,
            "duplicate_intents": len(directive_ids) - len(set(directive_ids)),
            "duplicate_objectives": len(objective_ids) - len(set(objective_ids)),
        },
        "mode_stats": {
            mode.value: dict(mode_stats[mode.value])
            for mode in DelegationMode
        },
        "resolution_stats": {
            status: dict(stats)
            for status, stats in sorted(resolution_stats.items())
        },
        "mode_adjustments": mode_adjustments,
        "resolution_adjustments": resolution_adjustments,
        "policy": {
            "minimum_evaluated": _MIN_EVALUATED,
            "minimum_adjustment": _MIN_ADJUSTMENT,
            "maximum_adjustment": _MAX_ADJUSTMENT,
            "opaque_training": False,
            "authority": "EVENT_DERIVED_ONLY",
        },
        "warnings": list(projection.warnings),
    }


__all__ = [
    "DELEGATION_CALIBRATION_CONTRACT",
    "build_delegation_calibration",
]
