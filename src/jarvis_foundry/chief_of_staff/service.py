"""Bounded advisory service for source-backed project changes."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
_TERMINAL_ITEM_STATES = frozenset({"RESOLVED", "SUPERSEDED", "REVOKED"})


class _CandidateIngressDenied(RuntimeError):
    def __init__(
        self,
        status: str,
        reason: str,
        standing_order_ids: Sequence[str] = (),
    ) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.standing_order_ids = tuple(str(item) for item in standing_order_ids)


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


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    values = (value,) if isinstance(value, str) else value
    if not isinstance(values, Sequence):
        return ()
    return tuple(dict.fromkeys(
        str(item).strip()
        for item in values
        if str(item).strip()
    ))


def _assert_candidate_ingress_authorized(
    evaluation: Mapping[str, Any],
    *,
    initially_authorized_by: Sequence[str],
) -> None:
    current_ids = _matched_ids(evaluation)
    status = str(evaluation.get("status") or "UNKNOWN")
    if status in _CONTROL_BLOCKS:
        raise _CandidateIngressDenied(
            status,
            _bounded(
                evaluation.get("owner_control_reason")
                or "owner control now blocks candidate ingress",
            ),
            current_ids,
        )
    retired = sorted(set(initially_authorized_by) - set(current_ids))
    if retired:
        raise _CandidateIngressDenied(
            "WAITING",
            "standing order authorization retired before candidate ingress: "
            + ", ".join(retired),
            current_ids,
        )
    if not current_ids:
        raise _CandidateIngressDenied(
            "WAITING",
            "proactive authority is no longer established",
            current_ids,
        )


def _item_state_changes(
    change: ProjectChange,
    attributes: Mapping[str, Any],
) -> tuple[tuple[str, str, str], ...]:
    changes: list[tuple[str, str, str]] = []
    for state, prefix in (
        ("RESOLVED", "resolved"),
        ("SUPERSEDED", "superseded"),
        ("REVOKED", "revoked"),
    ):
        for item_kind, suffix in (
            ("WAIT", "wait_ids"),
            ("OWNER_ATTENTION", "owner_attention_ids"),
        ):
            for item_id in _strings(attributes.get(f"{prefix}_{suffix}")):
                changes.append((item_kind, item_id, state))
    generic = attributes.get("item_state_changes")
    if isinstance(generic, Sequence) and not isinstance(generic, (str, bytes)):
        for item in generic:
            if not isinstance(item, Mapping):
                continue
            item_kind = str(item.get("item_kind") or "").strip().upper()
            item_id = str(item.get("item_id") or "").strip()
            to_state = str(item.get("to_state") or "").strip().upper()
            if (
                item_kind in {"WAIT", "OWNER_ATTENTION"}
                and item_id
                and to_state in _TERMINAL_ITEM_STATES
            ):
                changes.append((item_kind, item_id, to_state))
    return tuple(dict.fromkeys(changes))


def _record_item_state_changes(
    database: FoundryDatabase,
    change: ProjectChange,
    attributes: Mapping[str, Any],
    *,
    change_event_ref: str,
    source_refs: Sequence[str],
) -> tuple[str, ...]:
    event_refs: list[str] = []
    reason = _bounded(
        attributes.get("resolution_reason")
        or attributes.get("state_change_reason")
        or change.summary,
    )
    for item_kind, item_id, to_state in _item_state_changes(change, attributes):
        event_ref, _ = _append(
            database,
            "chief_of_staff.item_state_changed",
            {
                "project_id": change.project_id,
                "item_kind": item_kind,
                "item_id": item_id,
                "from_state": "OPEN",
                "to_state": to_state,
                "reason": reason,
                "change_id": change.change_id,
                "source_event_refs": list(source_refs),
                "evidence_refs": list(change.evidence_refs),
            },
            key=(
                "chief_of_staff.item_state_changed:"
                f"{item_kind}:{item_id}:{to_state}:{change.change_id}"
            ),
            occurred_at=change.occurred_at,
            causation_id=change_event_ref,
        )
        event_refs.append(event_ref)
    return tuple(event_refs)


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


def _schedule_existing_objective_wait(
    database: FoundryDatabase,
    change: ProjectChange,
    attributes: Mapping[str, Any],
    *,
    stop_path: Path | None,
) -> str | None:
    if not change.objective_id:
        return None
    from ..control.broker import emergency_stop_reason, matching_veto_reason

    identities = {
        "GLOBAL",
        "executive",
        "objectives",
        change.project_id,
        change.objective_id,
    }
    if (
        emergency_stop_reason(database, stop_path, identities=identities)
        or matching_veto_reason(database, identities)
    ):
        return None
    from ..executive.autonomy import rebuild_executive_state

    if change.objective_id not in rebuild_executive_state(database).objectives:
        return None
    from ..executive.continuous import (
        schedule_wait_for_capability,
        schedule_wait_for_dependency,
        schedule_wait_for_owner,
        schedule_wait_for_provider,
        schedule_wait_for_resource,
        schedule_wait_for_signal,
        schedule_wait_until,
    )

    kind = str(attributes.get("wait_kind") or "").upper()
    reason = _bounded(
        attributes.get("waiting_condition") or change.summary,
    )
    wake: Mapping[str, Any] | None = None
    if kind == "DEPENDENCY" and attributes.get("dependency_id"):
        wake = schedule_wait_for_dependency(
            database,
            change.objective_id,
            dependency_id=str(attributes["dependency_id"]),
            reason=reason,
        )
    elif kind == "PROVIDER" and attributes.get("provider_id"):
        wake = schedule_wait_for_provider(
            database,
            change.objective_id,
            provider_id=str(attributes["provider_id"]),
            reason=reason,
        )
    elif kind == "OWNER":
        wake = schedule_wait_for_owner(
            database,
            change.objective_id,
            signal=str(attributes.get("signal") or "owner_directive"),
            reason=reason,
        )
    elif kind == "TIME" and attributes.get("not_before"):
        wake = schedule_wait_until(
            database,
            change.objective_id,
            not_before=str(attributes["not_before"]),
            reason=reason,
        )
    elif kind == "SIGNAL" and attributes.get("signal"):
        wake = schedule_wait_for_signal(
            database,
            change.objective_id,
            signal=str(attributes["signal"]),
            reason=reason,
        )
    elif kind == "RESOURCE" and attributes.get("resource_id"):
        wake = schedule_wait_for_resource(
            database,
            change.objective_id,
            resource_id=str(attributes["resource_id"]),
            reason=reason,
        )
    elif kind == "CAPABILITY" and attributes.get("capability"):
        wake = schedule_wait_for_capability(
            database,
            change.objective_id,
            capability=str(attributes["capability"]),
            resource_id=(
                str(attributes["resource_id"])
                if attributes.get("resource_id") else None
            ),
            reason=reason,
        )
    wake_id = wake.get("wake_id") if isinstance(wake, Mapping) else None
    return str(wake_id) if wake_id else None


class ChiefOfStaffService:
    """Record semantic receipts and route only inert observed candidates."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        stop_path: str | Path | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.database = database
        self.stop_path = Path(stop_path) if stop_path is not None else None
        self.fault_hook = fault_hook

    def _fault(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)

    def snapshot(self, *, now: str | None = None):
        return project_chief_of_staff(self.database, now=now)

    def consider(
        self,
        value: ProjectChange | Mapping[str, Any],
        *,
        now: str | None = None,
        candidate_creation_allowed: bool = True,
        candidate_cycle_id: str | None = None,
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
        state_change_refs = _record_item_state_changes(
            self.database,
            change,
            attributes,
            change_event_ref=change_event_ref,
            source_refs=source_refs,
        )
        if (
            state_change_refs
            and not (
                attributes.get("proposed_objective")
                or attributes.get("next_action")
                or attributes.get("owner_decision_needed") is True
                or change.change_type in _ATTENTION_CHANGE_TYPES
                or change.change_type in _RECOVERABLE_CHANGE_TYPES
                or attributes.get("recoverable") is True
                or attributes.get("intentional_idle") is True
            )
        ):
            return ConsiderResult(
                change_id=change.change_id,
                project_id=change.project_id,
                status="PROJECT_STATE_UPDATED",
                change_event_ref=change_event_ref,
                recorded=recorded,
                standing_order_ids=standing_order_ids,
                source_event_refs=tuple(dict.fromkeys((
                    *source_refs,
                    *state_change_refs,
                ))),
            )

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
                    "change_id": change.change_id,
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
            semantic_wait_id = None
            canonical_wake_id = None
            if status == "WAITING":
                canonical_wake_id = _schedule_existing_objective_wait(
                    self.database,
                    change,
                    attributes,
                    stop_path=self.stop_path,
                )
                semantic_wait_id = f"cos-wait-{_hash(change.change_id)}"
                _append(
                    self.database,
                    "chief_of_staff.wait_recorded",
                    {
                        "wait_id": semantic_wait_id,
                        "project_id": change.project_id,
                        "change_id": change.change_id,
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
                        "wake_created": canonical_wake_id is not None,
                        "canonical_wake_id": canonical_wake_id,
                    },
                    key=f"chief_of_staff.wait_recorded:{semantic_wait_id}",
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
                wait_id=semantic_wait_id,
                canonical_wake_id=canonical_wake_id,
                standing_order_ids=standing_order_ids,
                source_event_refs=source_refs,
            )

        proactive_authorized = bool(standing_order_ids)
        if not proactive_authorized:
            canonical_wake_id = _schedule_existing_objective_wait(
                self.database,
                change,
                attributes,
                stop_path=self.stop_path,
            )
            semantic_wait_id = f"cos-wait-{_hash(change.change_id)}"
            _append(
                self.database,
                "chief_of_staff.wait_recorded",
                {
                    "wait_id": semantic_wait_id,
                    "project_id": change.project_id,
                    "change_id": change.change_id,
                    "objective_id": change.objective_id,
                    "kind": "SEMANTIC_WAIT",
                    "reason": (
                        "no authenticated standing order or explicit "
                        "proactive permission applies"
                    ),
                    "intentional_idle": False,
                    "source_event_refs": list(source_refs),
                    "evidence_refs": list(change.evidence_refs),
                    "wake_created": canonical_wake_id is not None,
                    "canonical_wake_id": canonical_wake_id,
                },
                key=f"chief_of_staff.wait_recorded:{semantic_wait_id}",
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
                wait_id=semantic_wait_id,
                canonical_wake_id=canonical_wake_id,
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
        evaluator = StandingOrderEvaluator(
            self.database,
            stop_path=self.stop_path,
        )
        self._fault("before_candidate_ingress")
        latest_evaluation = evaluator.evaluate(query)
        try:
            _assert_candidate_ingress_authorized(
                latest_evaluation,
                initially_authorized_by=standing_order_ids,
            )
        except _CandidateIngressDenied as denied:
            return ConsiderResult(
                change_id=change.change_id,
                project_id=change.project_id,
                status=denied.status,
                change_event_ref=change_event_ref,
                recorded=recorded,
                proposal=proposal,
                rejection_reason=denied.reason,
                standing_order_ids=denied.standing_order_ids,
                source_event_refs=source_refs,
            )
        self._fault("before_candidate_transaction")

        guarded_evaluation: dict[str, Any] = {}

        def candidate_guard() -> None:
            self._fault("candidate_transaction_guard")
            current = evaluator.evaluate(query)
            guarded_evaluation.update(current)
            _assert_candidate_ingress_authorized(
                current,
                initially_authorized_by=standing_order_ids,
            )

        try:
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
                prewrite_guard=candidate_guard,
                allow_create=candidate_creation_allowed,
                observation_cycle_id=candidate_cycle_id,
            )
        except _CandidateIngressDenied as denied:
            return ConsiderResult(
                change_id=change.change_id,
                project_id=change.project_id,
                status=denied.status,
                change_event_ref=change_event_ref,
                recorded=recorded,
                proposal=proposal,
                rejection_reason=denied.reason,
                standing_order_ids=denied.standing_order_ids,
                source_event_refs=source_refs,
            )
        if result.get("creation_blocked"):
            return ConsiderResult(
                change_id=change.change_id,
                project_id=change.project_id,
                status="CANDIDATE_LIMIT_REACHED",
                change_event_ref=change_event_ref,
                recorded=recorded,
                proposal=proposal,
                rejection_reason="CANDIDATE_LIMIT_REACHED",
                standing_order_ids=(
                    _matched_ids(guarded_evaluation)
                    if guarded_evaluation else standing_order_ids
                ),
                source_event_refs=source_refs,
            )
        self._fault("after_candidate_ingress")
        routed_standing_order_ids = (
            _matched_ids(guarded_evaluation)
            if guarded_evaluation else standing_order_ids
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
                "standing_order_ids": list(routed_standing_order_ids),
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
            ),
            standing_order_ids=routed_standing_order_ids,
            source_event_refs=source_refs,
        )


__all__ = ["ChiefOfStaffService"]
