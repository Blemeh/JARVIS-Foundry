from __future__ import annotations

from ..database import FoundryDatabase
from ..domain import ActionEnvelope, PolicyVerdict
from ..events import Event, payload_hash
from .policy import ActionPolicy


def evaluate_and_record(
    database: FoundryDatabase,
    policy: ActionPolicy,
    envelope: ActionEnvelope,
) -> PolicyVerdict:
    verdict = policy.evaluate(envelope)
    event_type = {
        "ALLOWED": "action.allowed",
        "VETOED": "action.vetoed",
        "NOT_READY": "action.not_ready",
        "STOPPED": "action.stopped",
        "INVALID_TARGET": "action.invalid_target",
    }[verdict.decision.value]
    payload = {
        "action_id": envelope.action_id,
        "mission_id": envelope.mission_id,
        "decision": verdict.decision.value,
        "reason": verdict.reason,
        "target": str(verdict.resolved_target) if verdict.resolved_target else None,
        "matched_veto_ids": list(verdict.matched_veto_ids),
    }
    # Idempotency covers the full decision payload: identical re-delivery dedupes,
    # while the same action_id with a changed reason/target (e.g. capability state
    # moved between deliveries) records a new decision instead of raising
    # EventConflict (verifier finding B6).
    event = Event.create(
        event_type,
        payload,
        source_type="policy",
        source_id="action-policy-v1",
        correlation_id=envelope.mission_id,
        idempotency_key=(
            f"action.decision:{envelope.action_id}:{verdict.decision.value}:"
            f"{payload_hash(payload)}"
        ),
    )
    database.append(event)
    return verdict

