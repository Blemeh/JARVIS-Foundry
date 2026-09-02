"""Durable verification verdicts for the mission loop (WP5).

Verification lifecycle is append-only via ``verification.started`` and
``verification.passed`` / ``verification.failed`` / ``verification.blocked``
events, projected into ``verification_verdicts`` (schema v3). A separate
``verification.accepted`` receipt records the existing AcceptanceGate
decision; raw PASS is never final truth. Independence metadata (builder vs
verifier provider identity, diversity, same-provider limitation) is recorded
at start — never retrofitted.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ..database import FoundryDatabase
from ..events import Event

SUBJECT_STEP = "mission_step"
ACCEPTANCE_EVENT = "verification.accepted"
FINAL_OUTCOME_EVENT = "mission.outcome_finalized"
FINAL_OUTCOME_SCHEMA = "jarvis.v11.outcome.v1"
FINAL_OUTCOME_STATUSES = frozenset({"SUCCEEDED", "FAILED", "BLOCKED", "REJECTED"})


def start_verification(
    database: FoundryDatabase,
    *,
    verdict_id: str,
    mission_id: str,
    step_id: str,
    builder_run_id: str,
    verifier_run_id: str,
    independence: dict[str, Any],
    criteria: list[str],
) -> None:
    """Record the STARTED verdict with full independence metadata."""
    event = Event.create(
        "verification.started",
        {
            "verdict_id": verdict_id,
            "mission_id": mission_id,
            "subject_type": SUBJECT_STEP,
            "subject_id": step_id,
            "verifier_run_id": verifier_run_id,
            "builder_run_id": builder_run_id,
            "independence": independence,
            "criteria": criteria,
        },
        source_type="supervisor",
        source_id="mission-loop",
        idempotency_key=f"verification.started:{verdict_id}",
    )
    database.append(event)


def record_verification_outcome(
    database: FoundryDatabase,
    *,
    verdict_id: str,
    outcome: str,
    findings: list[str],
    evidence: dict[str, Any],
    repair_requirements: list[str] | None = None,
    missing_proof: list[str] | None = None,
    scope_violations: list[str] | None = None,
    reason: str | None = None,
    blocking_dependency: str | None = None,
) -> None:
    """Record the terminal outcome (PASS / FAIL / BLOCKED / NOT_TESTED)."""
    if outcome not in ("PASS", "FAIL", "BLOCKED", "NOT_TESTED"):
        raise ValueError(f"invalid verification outcome: {outcome!r}")
    event_suffix = {
        "PASS": "passed", "FAIL": "failed", "BLOCKED": "blocked",
        "NOT_TESTED": "not_tested",
    }[outcome]
    event_type = f"verification.{event_suffix}"
    payload: dict[str, Any] = {
        "verdict_id": verdict_id,
        "findings": findings,
        "evidence": evidence,
    }
    if repair_requirements is not None:
        payload["repair_requirements"] = repair_requirements
    if missing_proof is not None:
        payload["missing_proof"] = missing_proof
    if scope_violations is not None:
        payload["scope_violations"] = scope_violations
    if reason is not None:
        payload["reason"] = reason
    if blocking_dependency is not None:
        payload["blocking_dependency"] = blocking_dependency
    event = Event.create(
        event_type,
        payload,
        source_type="supervisor",
        source_id="mission-loop",
        idempotency_key=f"{event_type}:{verdict_id}",
    )
    database.append(event)


def record_verification_acceptance(
    database: FoundryDatabase,
    *,
    verdict_id: str,
    mission_id: str,
    step_id: str,
    builder_run_id: str,
    verifier_run_id: str,
    checks: Mapping[str, Any],
    source_id: str = "mission-loop",
) -> None:
    """Record the existing AcceptanceGate decision as an immutable receipt.

    This is deliberately not another gate or readiness authority.  Consumers
    that need final verification truth can require this receipt instead of
    treating the preliminary ``verification.passed`` event as terminal.
    """

    if verdict_id in accepted_verifications(database):
        return

    event = Event.create(
        ACCEPTANCE_EVENT,
        {
            "decision": "ACCEPT",
            "verdict_id": verdict_id,
            "mission_id": mission_id,
            "subject_type": SUBJECT_STEP,
            "subject_id": step_id,
            "builder_run_id": builder_run_id,
            "verifier_run_id": verifier_run_id,
            "checks": dict(checks),
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"{ACCEPTANCE_EVENT}:{verdict_id}",
    )
    database.append(event)


def accepted_verifications(
    database: FoundryDatabase,
    *,
    mission_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return durable AcceptanceGate receipts keyed by verdict id."""

    accepted: dict[str, dict[str, Any]] = {}
    for event in database.events(event_types=(ACCEPTANCE_EVENT,), readonly=True):
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if payload.get("decision") != "ACCEPT":
            continue
        if mission_id is not None and str(payload.get("mission_id")) != str(mission_id):
            continue
        verdict_id = str(payload.get("verdict_id") or "").strip()
        if not verdict_id:
            continue
        accepted[verdict_id] = {
            **dict(payload),
            "acceptance_event_id": event.event_id,
            "accepted_at": event.occurred_at,
        }
    return accepted


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    """Keep verifier evidence useful without allowing unbounded outcome rows."""

    if depth >= 3:
        return str(value)[:500]
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth=depth + 1) for item in value[:30]]
    if isinstance(value, str):
        return value[:2000]
    return value


def _latest_mission_verdict(
    database: FoundryDatabase, mission_id: str,
) -> dict[str, Any] | None:
    """Return the newest verifier terminal for one mission by ledger order."""

    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT v.*, e.sequence AS terminal_sequence, "
            "e.event_id AS terminal_event_id, e.event_type AS terminal_event_type, "
            "e.occurred_at AS terminal_occurred_at, "
            "e.payload_json AS terminal_payload_json "
            "FROM verification_verdicts v "
            "JOIN events e ON e.event_id = v.last_event_id "
            "WHERE v.mission_id = ? ORDER BY e.sequence DESC LIMIT 1",
            (mission_id,),
        ).fetchone()
    return dict(row) if row else None


def _accepted_verdict_receipt(
    database: FoundryDatabase, *, mission_id: str, verdict_id: str,
) -> dict[str, Any] | None:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT event_id, sequence, occurred_at, payload_json FROM events "
            "WHERE event_type = ? "
            "AND json_extract(payload_json, '$.mission_id') = ? "
            "AND json_extract(payload_json, '$.verdict_id') = ? "
            "AND json_extract(payload_json, '$.decision') = 'ACCEPT' "
            "ORDER BY sequence DESC LIMIT 1",
            (ACCEPTANCE_EVENT, mission_id, verdict_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "event_id": str(row[0]),
        "sequence": int(row[1]),
        "occurred_at": str(row[2]),
        "payload": _json_object(row[3]),
    }


def _latest_mission_event(
    database: FoundryDatabase, *, mission_id: str, event_types: tuple[str, ...],
) -> dict[str, Any] | None:
    placeholders = ",".join("?" for _ in event_types)
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT event_id, sequence, event_type, occurred_at, payload_json "
            f"FROM events WHERE event_type IN ({placeholders}) "
            "AND json_extract(payload_json, '$.mission_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (*event_types, mission_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "event_id": str(row[0]),
        "sequence": int(row[1]),
        "event_type": str(row[2]),
        "occurred_at": str(row[3]),
        "payload": _json_object(row[4]),
    }


def _repair_history(
    database: FoundryDatabase, *, mission_id: str, limit: int = 25,
) -> list[dict[str, Any]]:
    """Return bounded, verifier-owned repair evidence for a mission."""

    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT v.verdict_id, v.status, v.verifier_run_id, v.builder_run_id, "
            "v.findings_json, e.event_id, e.event_type, e.occurred_at, e.payload_json "
            "FROM verification_verdicts v "
            "JOIN events e ON e.event_id = v.last_event_id "
            "WHERE v.mission_id = ? AND v.status = 'FAIL' "
            "ORDER BY e.sequence ASC LIMIT ?",
            (mission_id, max(1, min(int(limit), 100))),
        ).fetchall()
    attempts: list[dict[str, Any]] = []
    for row in rows:
        terminal = _json_object(row[8])
        findings = terminal.get("findings")
        if not isinstance(findings, list):
            try:
                parsed = json.loads(str(row[4] or "[]"))
            except (TypeError, ValueError):
                parsed = []
            findings = parsed if isinstance(parsed, list) else []
        attempts.append({
            "verdict_id": str(row[0]),
            "status": str(row[1]),
            "verifier_run_id": str(row[2] or ""),
            "builder_run_id": str(row[3] or ""),
            "terminal_event_id": str(row[5]),
            "terminal_event_type": str(row[6]),
            "occurred_at": str(row[7]),
            "reason": str(terminal.get("reason") or ""),
            "findings": [str(item) for item in findings[:20]],
        })
    return attempts


def _candidate_context(
    database: FoundryDatabase, *, mission_id: str, created_from: str,
) -> dict[str, Any]:
    candidate_id = ""
    if created_from.startswith("candidate:"):
        candidate_id = created_from.split(":", 1)[1]
    candidate: dict[str, Any] = {}
    if candidate_id:
        candidate = database.row("candidates", "candidate_id", candidate_id) or {}

    with database.connect(readonly=True) as connection:
        decision_row = connection.execute(
            "SELECT event_id, payload_json FROM events WHERE event_type IN "
            "('executive.decision_recorded', 'executive.decision_materialized') "
            "AND (json_extract(payload_json, '$.mission_id') = ? "
            "OR (? <> '' AND json_extract(payload_json, '$.candidate_id') = ?)) "
            "ORDER BY sequence DESC LIMIT 1",
            (mission_id, candidate_id, candidate_id),
        ).fetchone()
    decision = (
        {**_json_object(decision_row[1]), "event_id": str(decision_row[0])}
        if decision_row else {}
    )

    decision_id = str(decision.get("decision_id") or "")
    with database.connect(readonly=True) as connection:
        prediction_row = connection.execute(
            "SELECT payload_json FROM events "
            "WHERE event_type = 'executive.prediction_recorded' "
            "AND ((? <> '' AND json_extract(payload_json, '$.candidate_id') = ?) "
            "OR (? <> '' AND json_extract(payload_json, '$.decision_id') = ?)) "
            "ORDER BY sequence DESC LIMIT 1",
            (candidate_id, candidate_id, decision_id, decision_id),
        ).fetchone()
    prediction_payload = _json_object(prediction_row[0]) if prediction_row else {}
    raw_prediction = prediction_payload.get("prediction")
    prediction = dict(raw_prediction) if isinstance(raw_prediction, Mapping) else {}
    if not prediction and candidate.get("expected_outcome"):
        prediction = {"expected_outcome": candidate.get("expected_outcome")}

    objective_id = ""
    project_id = ""
    with database.connect(readonly=True) as connection:
        objective_row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type IN "
            "('executive.plan_updated', 'executive.objective_updated', "
            "'executive.objective_created') AND ("
            "json_extract(payload_json, '$.mission_id') = ? OR "
            "EXISTS (SELECT 1 FROM json_each(payload_json, '$.mission_ids') "
            "WHERE value = ?) OR "
            "EXISTS (SELECT 1 FROM json_each(payload_json, '$.changes.mission_ids') "
            "WHERE value = ?) OR "
            "EXISTS (SELECT 1 FROM json_each(payload_json, '$.steps') "
            "WHERE json_extract(value, '$.mission_id') = ?)) "
            "ORDER BY sequence DESC LIMIT 1",
            (mission_id, mission_id, mission_id, mission_id),
        ).fetchone()
    objective_payload = _json_object(objective_row[0]) if objective_row else {}
    changes = (
        dict(objective_payload.get("changes") or {})
        if isinstance(objective_payload.get("changes"), Mapping) else {}
    )
    objective_id = str(
        objective_payload.get("objective_id") or changes.get("objective_id") or "")
    metadata = objective_payload.get("metadata") or changes.get("metadata") or {}
    if isinstance(metadata, Mapping):
        project_id = str(metadata.get("project_id") or "")

    evidence: list[Any] = []
    try:
        parsed_evidence = json.loads(str(candidate.get("evidence_json") or "[]"))
    except (TypeError, ValueError):
        parsed_evidence = []
    if isinstance(parsed_evidence, list):
        evidence = parsed_evidence[:20]
    return {
        "candidate_id": candidate_id,
        "decision_id": str(decision.get("decision_id") or ""),
        "objective_id": objective_id,
        "project_id": project_id,
        "source": str(candidate.get("source") or decision.get("source") or ""),
        "why": str(decision.get("reason") or candidate.get("created_from") or ""),
        "estimated": prediction,
        "candidate_evidence": evidence,
        "decision_event_id": str(decision.get("event_id") or ""),
    }


def _final_outcome_row(
    database: FoundryDatabase, *, outcome_id: str,
) -> dict[str, Any] | None:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT event_id, sequence, occurred_at, payload_json, "
            "source_type, source_id FROM events "
            "WHERE event_type = ? AND source_type = 'supervisor' "
            "AND source_id = 'outcome-reconciler' "
            "AND json_extract(payload_json, '$.outcome_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (FINAL_OUTCOME_EVENT, outcome_id),
        ).fetchone()
    if row is None:
        return None
    return {
        **_json_object(row[3]),
        "outcome_event_id": str(row[0]),
        "outcome_sequence": int(row[1]),
        "recorded_at": str(row[2]),
        "outcome_source_type": str(row[4]),
        "outcome_source_id": str(row[5]),
    }


def validate_final_outcome_contract(
    database: FoundryDatabase, final: Mapping[str, Any],
) -> bool:
    """Validate that a final contract resolves to its durable truth authority."""

    mission_id = str(final.get("mission_id") or "")
    status = str(final.get("status") or "").upper()
    acceptance_id = str(final.get("acceptance_identity") or "")
    acceptance_kind = str(final.get("acceptance_kind") or "")
    outcome_id = str(final.get("outcome_id") or "")
    if not (
        final.get("schema") == FINAL_OUTCOME_SCHEMA
        and final.get("truth_accepted") is True
        and final.get("outcome_source_type") == "supervisor"
        and final.get("outcome_source_id") == "outcome-reconciler"
        and mission_id and status in FINAL_OUTCOME_STATUSES
        and acceptance_id and acceptance_kind and outcome_id
    ):
        return False
    expected_id = "outcome-" + hashlib.sha256(
        f"{mission_id}:{status}:{acceptance_id}".encode("utf-8")
    ).hexdigest()[:24]
    if outcome_id != expected_id:
        return False
    with database.connect(readonly=True) as connection:
        authority = connection.execute(
            "SELECT event_type, source_type, payload_json FROM events "
            "WHERE event_id = ? LIMIT 1",
            (acceptance_id,),
        ).fetchone()
    if authority is None or str(authority[0]) != acceptance_kind:
        return False
    if str(authority[1]) != "supervisor":
        return False
    authority_payload = _json_object(authority[2])
    verification = final.get("verification")
    verification = dict(verification) if isinstance(verification, Mapping) else {}
    verdict_id = str(verification.get("verdict_id") or "")
    if status == "SUCCEEDED":
        return bool(
            acceptance_kind == ACCEPTANCE_EVENT
            and authority_payload.get("decision") == "ACCEPT"
            and str(authority_payload.get("mission_id") or "") == mission_id
            and str(authority_payload.get("verdict_id") or "") == verdict_id
        )
    if status in {"FAILED", "BLOCKED"} and acceptance_kind.startswith(
        "verification."
    ):
        expected = "FAIL" if status == "FAILED" else str(
            verification.get("verdict_status") or "")
        with database.connect(readonly=True) as connection:
            verdict = connection.execute(
                "SELECT mission_id, status FROM verification_verdicts "
                "WHERE verdict_id = ? LIMIT 1",
                (verdict_id,),
            ).fetchone()
        return bool(
            verdict is not None
            and str(verdict[0]) == mission_id
            and str(verdict[1]) == expected
            and str(authority_payload.get("verdict_id") or "") == verdict_id
        )
    if acceptance_kind == "mission.state_changed":
        expected_state = "CANCELLED" if status == "REJECTED" else "BLOCKED"
        return bool(
            str(authority_payload.get("mission_id") or "") == mission_id
            and str(authority_payload.get("state") or "") == expected_state
        )
    return False


def record_final_outcome(
    database: FoundryDatabase,
    *,
    mission_id: str,
    source_id: str = "outcome-reconciler",
) -> dict[str, Any]:
    """Accept one terminal mission fact and persist its durable V11 contract.

    Success requires the exact newest verifier PASS to have an immutable
    ``verification.accepted`` receipt. Failure, block, and rejection are
    accepted only from durable mission/verifier terminal state. A returned
    worker/provider assertion is never an input to the status decision.
    """

    mission_id = str(mission_id or "").strip()
    if not mission_id:
        return {"finalized": False, "reason": "mission_id is required"}
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        return {"finalized": False, "reason": f"unknown mission {mission_id}"}

    state = str(mission.get("state") or "UNKNOWN").upper()
    verdict = _latest_mission_verdict(database, mission_id)
    verdict_id = str((verdict or {}).get("verdict_id") or "")
    verdict_status = str((verdict or {}).get("status") or "UNKNOWN").upper()
    terminal_verdict_payload = _json_object(
        (verdict or {}).get("terminal_payload_json"))
    acceptance = (
        _accepted_verdict_receipt(
            database, mission_id=mission_id, verdict_id=verdict_id)
        if verdict_id else None
    )
    completed = _latest_mission_event(
        database, mission_id=mission_id, event_types=("mission.completed",))
    completion_outcome = str(
        ((completed or {}).get("payload") or {}).get("outcome") or ""
    ).upper()

    status = ""
    finality_event_id = ""
    finality_event_type = ""
    finality_occurred_at = ""
    if state == "COMPLETE":
        if not (
            completed and completion_outcome == "PASSED"
            and verdict_status == "PASS" and acceptance is not None
        ):
            return {
                "finalized": False,
                "reason": (
                    "completed mission lacks final AcceptanceGate acceptance "
                    "for its latest verifier PASS"),
            }
        status = "SUCCEEDED"
        finality_event_id = str(acceptance["event_id"])
        finality_event_type = ACCEPTANCE_EVENT
        finality_occurred_at = str(acceptance["occurred_at"])
    elif state == "BLOCKED":
        status = "FAILED" if verdict_status == "FAIL" else "BLOCKED"
        if verdict and verdict_status in {"FAIL", "BLOCKED", "NOT_TESTED"}:
            finality_event_id = str(verdict.get("terminal_event_id") or "")
            finality_event_type = str(verdict.get("terminal_event_type") or "")
            finality_occurred_at = str(verdict.get("terminal_occurred_at") or "")
        else:
            finality_event_id = str(mission.get("last_event_id") or "")
            finality_event_type = "mission.state_changed"
            finality_occurred_at = str(mission.get("updated_at") or "")
    elif state == "CANCELLED":
        status = "REJECTED"
        finality_event_id = str(mission.get("last_event_id") or "")
        finality_event_type = "mission.state_changed"
        finality_occurred_at = str(mission.get("updated_at") or "")
    else:
        return {
            "finalized": False,
            "reason": f"mission state {state} is not a final outcome boundary",
        }
    if status not in FINAL_OUTCOME_STATUSES or not finality_event_id:
        return {"finalized": False, "reason": "terminal authority identity is missing"}

    identity_material = f"{mission_id}:{status}:{finality_event_id}"
    outcome_id = "outcome-" + hashlib.sha256(
        identity_material.encode("utf-8")).hexdigest()[:24]
    existing = _final_outcome_row(database, outcome_id=outcome_id)
    if existing is not None:
        return {**existing, "finalized": True, "created": False}

    context = _candidate_context(
        database,
        mission_id=mission_id,
        created_from=str(mission.get("created_from") or ""),
    )
    resource = (
        database.row("resources", "resource_id", str(mission.get("resource_id") or ""))
        if mission.get("resource_id") else None
    ) or {}
    repairs = _repair_history(database, mission_id=mission_id)
    findings = terminal_verdict_payload.get("findings")
    if not isinstance(findings, list):
        findings = []
    failure_reason = str(terminal_verdict_payload.get("reason") or "")
    if not failure_reason and findings:
        failure_reason = str(findings[0])
    if not failure_reason and status in {"BLOCKED", "REJECTED"}:
        final_state_event = _latest_mission_event(
            database, mission_id=mission_id,
            event_types=("mission.state_changed", "mission.stop_blocked"),
        )
        failure_reason = str(
            ((final_state_event or {}).get("payload") or {}).get("reason")
            or ((final_state_event or {}).get("payload") or {}).get("next_action")
            or ""
        )

    evidence_refs = [
        value for value in (
            finality_event_id,
            verdict_id,
            str((completed or {}).get("event_id") or ""),
            str(context.get("decision_event_id") or ""),
            str(mission.get("last_event_id") or ""),
        ) if value
    ]
    for value in terminal_verdict_payload.get("evidence_refs") or []:
        if isinstance(value, str) and value:
            evidence_refs.append(value)
    evidence_refs = list(dict.fromkeys(evidence_refs))[:30]

    if status == "SUCCEEDED":
        learned = "accepted success reinforces evidence for similar future decisions"
    elif status == "FAILED":
        learned = "accepted failure is negative evidence for similar future decisions"
    elif status == "BLOCKED":
        learned = "blocker recorded separately from execution failure; no negative quality inference"
    else:
        learned = "rejection recorded separately from execution failure; no success inferred"
    next_action = str(mission.get("next_action") or "") or "WAIT"
    verification_summary = {
        "verdict_id": verdict_id or None,
        "verdict_status": verdict_status,
        "verifier_run_id": str((verdict or {}).get("verifier_run_id") or "") or None,
        "builder_run_id": str((verdict or {}).get("builder_run_id") or "") or None,
        "acceptance_event_id": str((acceptance or {}).get("event_id") or "") or None,
        "acceptance": "ACCEPTED" if acceptance is not None else "FINAL_NEGATIVE",
        "checks": dict(((acceptance or {}).get("payload") or {}).get("checks") or {}),
        "findings": [str(item) for item in findings[:20]],
    }
    actual_observed = {
        "outcome": status,
        "mission_state": state,
        "mission_completion": completion_outcome or None,
        "verifier_status": verdict_status,
        "verifier_evidence": _bounded_value(
            terminal_verdict_payload.get("evidence") or {}),
        "failure_reason": failure_reason or None,
        "repair_count": len(repairs),
    }
    with database.connect(readonly=True) as connection:
        prior_count = int(connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = ? "
            "AND json_extract(payload_json, '$.mission_id') = ?",
            (FINAL_OUTCOME_EVENT, mission_id),
        ).fetchone()[0])
    payload: dict[str, Any] = {
        "schema": FINAL_OUTCOME_SCHEMA,
        "outcome_id": outcome_id,
        "revision": prior_count + 1,
        "mission_id": mission_id,
        "objective_id": context.get("objective_id") or None,
        "candidate_id": context.get("candidate_id") or None,
        "decision_id": context.get("decision_id") or None,
        "status": status,
        "truth_accepted": True,
        "acceptance_identity": finality_event_id,
        "acceptance_kind": finality_event_type,
        "attempted": str(mission.get("objective") or mission_id),
        "why": context.get("why") or context.get("source") or "durable mission objective",
        "verification": verification_summary,
        "evidence_refs": evidence_refs,
        "actual_observed": actual_observed,
        "estimated_outcome": dict(context.get("estimated") or {}),
        "failure_reason": failure_reason or None,
        "repair_attempts": repairs,
        "project_id": context.get("project_id") or None,
        "resource_id": mission.get("resource_id"),
        "system_target": resource.get("resolved_root") or resource.get("root_path"),
        "timestamps": {
            "mission_updated_at": mission.get("updated_at"),
            "finality_occurred_at": finality_occurred_at,
        },
        "provenance": {
            "authority": "ledger+mission_projection+latest_verifier+AcceptanceGate",
            "reconciler": source_id,
            "source_event_ids": evidence_refs,
            "source": context.get("source") or None,
            "candidate_evidence": context.get("candidate_evidence") or [],
        },
        "briefing": {
            "what_was_attempted": str(mission.get("objective") or mission_id),
            "why": context.get("why") or context.get("source") or "durable mission objective",
            "result": status,
            "verification": verification_summary,
            "repair_or_failure": {
                "reason": failure_reason or None,
                "attempts": len(repairs),
            },
            "what_jarvis_learned": learned,
            "what_happened_next": next_action,
        },
    }
    event = Event.create(
        FINAL_OUTCOME_EVENT,
        payload,
        source_type="supervisor",
        source_id="outcome-reconciler",
        correlation_id=context.get("objective_id") or mission_id,
        causation_id=finality_event_id,
        idempotency_key=f"{FINAL_OUTCOME_EVENT}:{outcome_id}",
    )
    sequence, created = database.append(event)
    persisted = _final_outcome_row(database, outcome_id=outcome_id) or payload
    return {
        **persisted,
        "outcome_sequence": int(sequence),
        "finalized": True,
        "created": bool(created),
    }


def final_outcome_history(
    database: FoundryDatabase,
    *,
    mission_id: str | None = None,
    objective_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Read a newest-first bounded outcome history from the indexed event type."""

    clauses = [
        "event_type = ?", "source_type = 'supervisor'",
        "source_id = 'outcome-reconciler'",
    ]
    args: list[Any] = [FINAL_OUTCOME_EVENT]
    if mission_id is not None:
        clauses.append("json_extract(payload_json, '$.mission_id') = ?")
        args.append(str(mission_id))
    if objective_id is not None:
        clauses.append("json_extract(payload_json, '$.objective_id') = ?")
        args.append(str(objective_id))
    args.append(max(1, min(int(limit), 500)))
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT event_id, sequence, occurred_at, payload_json, "
            "source_type, source_id FROM events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence DESC LIMIT ?",
            tuple(args),
        ).fetchall()
    return [
        {
            **_json_object(row[3]),
            "outcome_event_id": str(row[0]),
            "outcome_sequence": int(row[1]),
            "recorded_at": str(row[2]),
            "outcome_source_type": str(row[4]),
            "outcome_source_id": str(row[5]),
        }
        for row in rows
    ]


def final_outcome_projection(
    database: FoundryDatabase, *, limit: int = 20,
) -> dict[str, Any]:
    """Build the bounded owner-facing current truth plus preserved revisions."""

    bounded_limit = max(1, min(int(limit), 500))
    history = final_outcome_history(database, limit=bounded_limit)
    with database.connect(readonly=True) as connection:
        current_rows = connection.execute(
            "WITH ranked AS ("
            "SELECT event_id, sequence, occurred_at, payload_json, "
            "source_type, source_id, "
            "ROW_NUMBER() OVER (PARTITION BY "
            "json_extract(payload_json, '$.mission_id') "
            "ORDER BY sequence DESC) AS outcome_rank "
            "FROM events WHERE event_type = ? "
            "AND source_type = 'supervisor' "
            "AND source_id = 'outcome-reconciler') "
            "SELECT event_id, sequence, occurred_at, payload_json, "
            "source_type, source_id "
            "FROM ranked WHERE outcome_rank = 1 "
            "ORDER BY sequence DESC LIMIT ?",
            (FINAL_OUTCOME_EVENT, bounded_limit),
        ).fetchall()
    current = [
        {
            **_json_object(row[3]),
            "outcome_event_id": str(row[0]),
            "outcome_sequence": int(row[1]),
            "recorded_at": str(row[2]),
            "outcome_source_type": str(row[4]),
            "outcome_source_id": str(row[5]),
        }
        for row in current_rows
    ]
    counts = {status: 0 for status in sorted(FINAL_OUTCOME_STATUSES)}
    for item in current:
        status = str(item.get("status") or "UNKNOWN")
        if status in counts:
            counts[status] += 1
    return {
        "schema": FINAL_OUTCOME_SCHEMA,
        "latest": current[0] if current else None,
        "current": current,
        "history": history,
        "status_counts": counts,
        "source": FINAL_OUTCOME_EVENT,
    }


def latest_verdict_for_step(database: FoundryDatabase, mission_id: str, step_id: str) -> dict | None:
    """The most recent verdict row for a mission step.

    Ordered by LEDGER SEQUENCE (via the terminal outcome event), never by
    verdict_id or wall-clock text: identical timestamps (frozen clock,
    same-microsecond ties) must not make the selection random (verifier
    finding F1). The row whose terminal outcome was recorded latest wins.
    """
    with database.connect() as connection:
        row = connection.execute(
            """SELECT v.* FROM verification_verdicts v
               JOIN events e ON e.event_id = v.last_event_id
               WHERE v.mission_id = ? AND v.subject_type = ? AND v.subject_id = ?
               ORDER BY e.sequence DESC
               LIMIT 1""",
            (mission_id, SUBJECT_STEP, step_id),
        ).fetchone()
    return dict(row) if row else None


def failed_verdict_count(database: FoundryDatabase, mission_id: str, step_id: str) -> int:
    """Durable repair-budget accounting: FAIL verdicts for this step."""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM verification_verdicts WHERE "
            "mission_id = ? AND subject_type = ? AND subject_id = ? AND status = 'FAIL'",
            (mission_id, SUBJECT_STEP, step_id),
        ).fetchone()
    return int(row["count"])


def dangling_verdict_for_run(database: FoundryDatabase,
                             verifier_run_id: str) -> dict | None:
    """The STARTED verdict whose verifier run never reached a terminal.

    DIR-2026-08-12 item 3 (exactly-once recovery): a verification pass
    interrupted between start and outcome leaves a STARTED verdict row
    that would otherwise dangle forever. The resumed pass corrects it
    additively (verification.blocked: interrupted; superseded)."""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT verdict_id, mission_id, subject_type, subject_id "
            "FROM verification_verdicts "
            "WHERE verifier_run_id = ? AND status = 'STARTED' "
            "ORDER BY rowid LIMIT 1", (verifier_run_id,)).fetchone()
    return dict(row) if row else None


def dangling_verdicts_for_run_prefix(
    database: FoundryDatabase, verifier_run_prefix: str,
) -> list[dict]:
    """Return every interrupted verifier pass for a builder's run family.

    A verifier contract revision may move a resumed pass from the legacy
    ``<builder>-verify`` id to a content-addressed ``-verify-v<digest>`` id.
    Recovery must supersede *all* prior STARTED rows in that family, not only
    the legacy id, while preserving each row and its evidence as history.
    """
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT verdict_id, mission_id, subject_type, subject_id, "
            "verifier_run_id FROM verification_verdicts "
            "WHERE verifier_run_id LIKE ? AND status = 'STARTED' "
            "ORDER BY rowid ASC",
            (f"{verifier_run_prefix}%",),
        ).fetchall()
    return [dict(row) for row in rows]


def verdict_for_verifier_run(
    database: FoundryDatabase,
    *,
    mission_id: str,
    step_id: str,
    verifier_run_id: str,
) -> dict | None:
    """Find the durable verdict bound to one verifier session.

    Existing releases used random verdict ids, so replay first consults this
    lookup before deriving a new deterministic id.  That preserves the
    original verdict identity and its immutable evidence on exact replay.
    """
    with database.connect() as connection:
        row = connection.execute(
            "SELECT v.* FROM verification_verdicts v "
            "JOIN events e ON e.event_id = v.last_event_id "
            "WHERE v.mission_id = ? AND v.subject_type = ? "
            "AND v.subject_id = ? AND v.verifier_run_id = ? "
            "ORDER BY e.sequence DESC LIMIT 1",
            (mission_id, SUBJECT_STEP, step_id, verifier_run_id),
        ).fetchone()
    return dict(row) if row else None
