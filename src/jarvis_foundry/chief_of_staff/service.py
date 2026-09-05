"""Bounded advisory service for source-backed project changes."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from ..database import FoundryDatabase
from ..discovery_candidates import ingest_observed_candidate
from ..events import Event, canonical_json
from ..owner_context import (
    OwnerContextQuery,
    StandingOrderEvaluator,
    project_owner_context,
)
from .models import (
    CandidateProposal,
    ConsiderResult,
    ProjectChange,
    freeze_mapping,
    thaw_mapping,
)
from .projection import project_chief_of_staff


_CONTROL_BLOCKS = frozenset({
    "BLOCKED_BY_OWNER_CONTROL",
    "DEFERRED_TO_OWNER_CONTROL",
})
_ATTENTION_CHANGE_TYPES = frozenset({
    "OWNER_DECISION",
    "OWNER_AUTHORIZATION",
    "AMBIGUOUS_OWNER_INTENT",
})
_RECOVERABLE_CHANGE_TYPES = frozenset({
    "BLOCKED",
    "DEPENDENCY_WAIT",
    "PROVIDER_UNAVAILABLE",
    "RECOVERABLE_ERROR",
    "WAITING",
})
_JUNK_CHANGE_TYPES = frozenset({"JUNK", "NOISE", "SPAM"})


def _hash(value: Mapping[str, Any] | str, *, length: int = 24) -> str:
    material = value if isinstance(value, str) else canonical_json(dict(value))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:length]


def _bounded(value: Any, *, limit: int = 500) -> str:
    result = str(value or "").replace("\x00", " ").strip()
    return result if len(result) <= limit else result[: limit - 3] + "..."


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return max(0.0, min(1.0, result))


def _event_id_for_key(database: FoundryDatabase, key: str) -> str:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT event_id FROM events WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"event missing after append: {key}")
    return str(row["event_id"])


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    occurred_at: str | None = None,
    causation_id: str | None = None,
) -> tuple[str, bool]:
    event = Event.create(
        event_type,
        dict(payload),
        source_type="supervisor",
        source_id="v13-chief-of-staff",
        occurred_at=occurred_at,
        correlation_id=str(payload.get("project_id") or "") or None,
        causation_id=causation_id,
        idempotency_key=key,
    )
    _, created = database.append(event)
    return (
        event.event_id if created else _event_id_for_key(database, key),
        created,
    )


def _project_has_contradiction(
    database: FoundryDatabase,
    project_id: str,
    *,
    now: str | None,
) -> tuple[bool, tuple[str, ...]]:
    projection = project_owner_context(database, now=now)
    records = {record.context_id: record for record in projection.records}
    refs: list[str] = []
    found = False
    for conflict in projection.contradictions:
        scoped = False
        for context_id in conflict.get("context_ids", ()):
            record = records.get(str(context_id))
            if record is None:
                continue
            if project_id in record.scope.project_ids:
                scoped = True
                if record.source_event_id and record.source_event_id not in refs:
                    refs.append(record.source_event_id)
        found = found or scoped
    return found, tuple(refs)


def _matched_ids(evaluation: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(item.get("standing_order_id"))
        for item in evaluation.get("matched", ())
        if isinstance(item, Mapping) and item.get("standing_order_id")
    )


def _proposal(
    change: ProjectChange,
    evaluation: Mapping[str, Any],
) -> CandidateProposal | None:
    attributes = thaw_mapping(change.attributes)
    objective = _bounded(
        attributes.get("proposed_objective")
        or attributes.get("next_action"),
    )
    matched = [
        item
        for item in evaluation.get("matched", ())
        if isinstance(item, Mapping)
    ]
    if not objective and matched:
        behavior = matched[0].get("intended_behavior")
        if isinstance(behavior, Mapping):
            description = _bounded(behavior.get("description"))
            if description:
                objective = f"{description} for project {change.project_id}"
    if not objective:
        return None
    evidence_refs = tuple(dict.fromkeys((
        *change.source_event_refs,
        *change.evidence_refs,
    )))
    return CandidateProposal(
        objective=objective,
        candidate_type=str(
            attributes.get("candidate_type")
            or change.change_type.lower()
        ),
        expected_outcome=_bounded(
            attributes.get("expected_outcome")
            or f"Verified improvement for {change.project_id}",
        ),
        value=_number(attributes.get("value")),
        urgency=_number(attributes.get("urgency")),
        confidence=_number(attributes.get("confidence")),
        reversible=(
            attributes.get("reversible")
            if isinstance(attributes.get("reversible"), bool)
            else None
        ),
        evidence_refs=evidence_refs,
        standing_order_ids=_matched_ids(evaluation),
        attributes=freeze_mapping({
            key: value
            for key, value in attributes.items()
            if key not in {
                "proposed_objective",
                "next_action",
                "candidate_type",
                "expected_outcome",
                "value",
                "urgency",
                "confidence",
                "reversible",
            }
        }),
    )


def _rejection_reason(
    change: ProjectChange,
    proposal: CandidateProposal | None,
) -> str | None:
    attributes = thaw_mapping(change.attributes)
    if change.change_type in _JUNK_CHANGE_TYPES or attributes.get("junk") is True:
        return "JUNK_OR_NOISE"
    if proposal is None:
        return "NO_VALUABLE_ACTION"
    if not proposal.evidence_refs:
        return "INSUFFICIENT_EVIDENCE"
    if proposal.value is None or proposal.value < 0.55:
        return "LOW_STRATEGIC_VALUE"
    if proposal.confidence is None or proposal.confidence < 0.60:
        return "LOW_CONFIDENCE"
    if proposal.reversible is not True:
        return "REVERSIBILITY_NOT_PROVEN"
    return None


class ChiefOfStaffService:
    """Record semantic receipts and route only inert observed candidates."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        stop_path: str | Path | None = None,
    ) -> None:
        self.database = database
        self.stop_path = Path(stop_path) if stop_path is not None else None

    def snapshot(self, *, now: str | None = None):
        return project_chief_of_staff(self.database, now=now)

    def consider(
        self,
        value: ProjectChange | Mapping[str, Any],
        *,
        now: str | None = None,
    ) -> ConsiderResult:
        change = ProjectChange.from_mapping(value)
        attributes = thaw_mapping(change.attributes)
        query = OwnerContextQuery(
            project_id=change.project_id,
            program_id=change.program_id,
            objective_id=change.objective_id,
            current_situation=change.summary,
            requested_task=_bounded(
                attributes.get("proposed_objective")
                or attributes.get("next_action"),
            ),
            entity_refs=tuple(dict.fromkeys((
                *change.source_event_refs,
                *change.evidence_refs,
            ))),
            event_type=change.change_type,
            situation_state=change.situation_state,
            severity=change.severity,
            now=now,
        )
        evaluation = StandingOrderEvaluator(
            self.database,
            stop_path=self.stop_path,
        ).evaluate(query)
        standing_order_ids = _matched_ids(evaluation)
        if evaluation.get("status") in _CONTROL_BLOCKS:
            return ConsiderResult(
                change_id=change.change_id,
                project_id=change.project_id,
                status=str(evaluation["status"]),
                change_event_ref=None,
                recorded=False,
                rejection_reason=_bounded(
                    evaluation.get("owner_control_reason"),
                ),
                standing_order_ids=standing_order_ids,
                source_event_refs=change.source_event_refs,
            )

        change_key = f"chief_of_staff.change_observed:{change.change_id}"
        change_event_ref, recorded = _append(
            self.database,
            "chief_of_staff.change_observed",
            {
                "project_id": change.project_id,
                "change": change.as_dict(),
            },
            key=change_key,
            occurred_at=change.occurred_at,
        )
        source_refs = tuple(dict.fromkeys((
            change_event_ref,
            *change.source_event_refs,
        )))

        contradicted, contradiction_refs = _project_has_contradiction(
            self.database,
            change.project_id,
            now=now,
        )
        owner_decision_needed = (
            change.change_type in _ATTENTION_CHANGE_TYPES
            or attributes.get("owner_decision_needed") is True
            or contradicted
        )
        if owner_decision_needed:
            attention_id = f"cos-attention-{_hash(change.change_id)}"
            reason = (
                "owner context is contradictory"
                if contradicted
                else _bounded(
                    attributes.get("owner_decision_reason")
                    or "material owner decision is required",
                )
            )
            _append(
                self.database,
                "chief_of_staff.owner_attention_required",
                {
                    "attention_id": attention_id,
                    "project_id": change.project_id,
                    "objective_id": change.objective_id,
                    "category": str(
                        attributes.get("attention_category")
                        or "OWNER_DECISION"
                    ),
                    "summary": change.summary,
                    "reason": reason,
                    "created_at": change.occurred_at,
                    "standing_order_ids": list(standing_order_ids),
                    "source_event_refs": list(dict.fromkeys((
                        *source_refs,
                        *contradiction_refs,
                    ))),
                    "evidence_refs": list(change.evidence_refs),
                },
                key=f"chief_of_staff.owner_attention_required:{attention_id}",
                occurred_at=change.occurred_at,
                causation_id=change_event_ref,
            )
            return ConsiderResult(
                change_id=change.change_id,
                project_id=change.project_id,
                status="OWNER_ATTENTION_REQUIRED",
                change_event_ref=change_event_ref,
                recorded=recorded,
                owner_attention_id=attention_id,
                standing_order_ids=standing_order_ids,
                source_event_refs=source_refs,
            )

        proposal = _proposal(change, evaluation)
        rejection = _rejection_reason(change, proposal)
        if rejection is not None:
            _append(
                self.database,
                "chief_of_staff.recommendation_rejected",
                {
                    "project_id": change.project_id,
                    "change_id": change.change_id,
                    "reason": rejection,
                    "proposal": proposal.as_dict() if proposal else None,
                    "standing_order_ids": list(standing_order_ids),
                    "source_event_refs": list(source_refs),
                    "evidence_refs": list(change.evidence_refs),
                },
                key=(
                    "chief_of_staff.recommendation_rejected:"
                    f"{change.change_id}:{rejection}"
                ),
                occurred_at=change.occurred_at,
                causation_id=change_event_ref,
            )
            status = (
                "WAITING"
                if (
                    change.change_type in _RECOVERABLE_CHANGE_TYPES
                    or attributes.get("recoverable") is True
                    or attributes.get("intentional_idle") is True
                )
                else "REJECTED"
            )
            wait_id = None
            if status == "WAITING":
                wait_id = f"cos-wait-{_hash(change.change_id)}"
                _append(
                    self.database,
                    "chief_of_staff.wait_recorded",
                    {
                        "wait_id": wait_id,
                        "project_id": change.project_id,
                        "objective_id": change.objective_id,
                        "kind": "SEMANTIC_WAIT",
                        "reason": _bounded(
                            attributes.get("waiting_condition")
                            or change.summary,
                        ),
                        "intentional_idle": (
                            attributes.get("intentional_idle") is True
                        ),
                        "source_event_refs": list(source_refs),
                        "evidence_refs": list(change.evidence_refs),
                        "wake_created": False,
                    },
                    key=f"chief_of_staff.wait_recorded:{wait_id}",
                    occurred_at=change.occurred_at,
                    causation_id=change_event_ref,
                )
            return ConsiderResult(
                change_id=change.change_id,
                project_id=change.project_id,
                status=status,
                change_event_ref=change_event_ref,
                recorded=recorded,
                proposal=proposal,
                rejection_reason=rejection,
                wait_id=wait_id,
                standing_order_ids=standing_order_ids,
                source_event_refs=source_refs,
            )

        proactive_authorized = bool(standing_order_ids)
        if not proactive_authorized:
            wait_id = f"cos-wait-{_hash(change.change_id)}"
            _append(
                self.database,
                "chief_of_staff.wait_recorded",
                {
                    "wait_id": wait_id,
                    "project_id": change.project_id,
                    "objective_id": change.objective_id,
                    "kind": "SEMANTIC_WAIT",
                    "reason": (
                        "no authenticated standing order or explicit "
                        "proactive permission applies"
                    ),
                    "intentional_idle": False,
                    "source_event_refs": list(source_refs),
                    "evidence_refs": list(change.evidence_refs),
                    "wake_created": False,
                },
                key=f"chief_of_staff.wait_recorded:{wait_id}",
                occurred_at=change.occurred_at,
                causation_id=change_event_ref,
            )
            return ConsiderResult(
                change_id=change.change_id,
                project_id=change.project_id,
                status="WAITING",
                change_event_ref=change_event_ref,
                recorded=recorded,
                proposal=proposal,
                rejection_reason="PROACTIVE_AUTHORITY_NOT_ESTABLISHED",
                wait_id=wait_id,
                standing_order_ids=standing_order_ids,
                source_event_refs=source_refs,
            )

        assert proposal is not None
        material = {
            "change_id": change.change_id,
            "project_id": change.project_id,
            "objective": proposal.objective,
            "evidence_refs": list(proposal.evidence_refs),
        }
        digest = hashlib.sha256(
            canonical_json(material).encode("utf-8"),
        ).hexdigest()
        result = ingest_observed_candidate(
            self.database,
            f"chief-of-staff-observation:{change.project_id}",
            observation_id=f"cos-observation-{_hash(change.change_id)}",
            source_kind="CHIEF_OF_STAFF",
            source_identity=(
                f"chief-of-staff:{change.project_id}:{change.change_id}"
            ),
            content_hash=f"sha256:{digest}",
            objective=proposal.objective,
            source_ref=(
                proposal.evidence_refs[0]
                if proposal.evidence_refs else None
            ),
            observed_at=change.occurred_at,
            authority="DATA_ONLY",
            target_project=change.project_id,
            alias_key=str(
                attributes.get("alias_key")
                or f"{change.project_id}:{proposal.objective}"
            ),
            evidence=[{
                "change_id": change.change_id,
                "project_id": change.project_id,
                "source_event_refs": list(source_refs),
                "evidence_refs": list(proposal.evidence_refs),
                "standing_order_ids": list(standing_order_ids),
                "authority": "DATA_ONLY",
            }],
            candidate_type=proposal.candidate_type,
            expected_outcome=proposal.expected_outcome,
            confidence=proposal.confidence,
            expires_at=(
                str(attributes["expires_at"])
                if attributes.get("expires_at") else None
            ),
        )
        candidate_id = (
            str(result["candidate_id"])
            if result.get("candidate_id") else None
        )
        _append(
            self.database,
            "chief_of_staff.candidate_routed",
            {
                "project_id": change.project_id,
                "change_id": change.change_id,
                "candidate_id": candidate_id,
                "proposal": proposal.as_dict(),
                "standing_order_ids": list(standing_order_ids),
                "source_event_refs": list(source_refs),
                "evidence_refs": list(proposal.evidence_refs),
                "authority": "EXISTING_CANDIDATE_INGRESS",
            },
            key=f"chief_of_staff.candidate_routed:{change.change_id}",
            occurred_at=change.occurred_at,
            causation_id=change_event_ref,
        )
        return ConsiderResult(
            change_id=change.change_id,
            project_id=change.project_id,
            status="ROUTED_TO_EXISTING_CANDIDATE_PATH",
            change_event_ref=change_event_ref,
            recorded=recorded,
            proposal=proposal,
            candidate_id=candidate_id,
            candidate_status=str(
                result.get("history_status") or "OBSERVED"
            ),
            duplicate=(
                not bool(result.get("created"))
                or not recorded
            ),
            standing_order_ids=standing_order_ids,
            source_event_refs=source_refs,
        )


__all__ = ["ChiefOfStaffService"]
