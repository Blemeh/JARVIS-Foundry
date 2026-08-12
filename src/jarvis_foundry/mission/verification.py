"""Durable verification verdicts for the mission loop (WP5).

Verification lifecycle is append-only via ``verification.started`` and
``verification.passed`` / ``verification.failed`` / ``verification.blocked``
events, projected into ``verification_verdicts`` (schema v3). Independence
metadata (builder vs verifier provider identity, diversity, same-provider
limitation) is recorded at start — never retrofitted.
"""
from __future__ import annotations

from typing import Any

from ..database import FoundryDatabase
from ..events import Event

SUBJECT_STEP = "mission_step"


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
    """Record the terminal outcome (PASS / FAIL / BLOCKED) for a verdict."""
    if outcome not in ("PASS", "FAIL", "BLOCKED"):
        raise ValueError(f"invalid verification outcome: {outcome!r}")
    event_suffix = {"PASS": "passed", "FAIL": "failed", "BLOCKED": "blocked"}[outcome]
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
