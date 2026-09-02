"""Bounded deterministic calibration from recorded outcomes."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..database import FoundryDatabase
from ..knowledge import canonicalize_statement
from ..mission.verification import accepted_verifications

CALIBRATION_VERSION = "1.0.0"
MAX_SOURCE_PENALTY = 0.25
MAX_PATTERN_PENALTY = 0.30
PENALTY_PER_FAILURE = 0.05
REPAIR_BURDEN_PENALTY = 0.04
SUCCESS_BONUS_CAP = 0.10

NEGATIVE_OUTCOMES = frozenset({"FAILED", "FAILED_VERIFICATION", "FAIL"})
NEUTRAL_OUTCOMES = frozenset({
    "BLOCKED", "BLOCKED_EXTERNAL_DEPENDENCY", "BLOCKED_CAPABILITY", "NOT_TESTED",
    "REJECTED",
})
POSITIVE_OUTCOMES = frozenset({"PASSED", "PASS", "SUCCEEDED"})


@dataclass
class CalibrationState:
    version: str = CALIBRATION_VERSION
    source_adjustments: dict[str, float] = field(default_factory=dict)
    pattern_adjustments: dict[str, float] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)
    success_counts: dict[str, int] = field(default_factory=dict)

    def adjustment_for(self, *, source: str, objective: str) -> float:
        canonical = canonicalize_statement(objective)
        pattern_key = " ".join(canonical.split()[:6])
        adj = 0.0
        adj -= self.source_adjustments.get(source, 0.0)
        adj -= self.pattern_adjustments.get(pattern_key, 0.0)
        failures = self.failure_counts.get(canonical, 0)
        adj -= min(MAX_PATTERN_PENALTY, failures * PENALTY_PER_FAILURE)
        successes = self.success_counts.get(canonical, 0)
        adj += min(SUCCESS_BONUS_CAP, successes * 0.02)
        return round(adj, 4)


def _normalize_outcome(outcome: str) -> str:
    upper = str(outcome or "").upper()
    if upper in POSITIVE_OUTCOMES:
        return "PASSED"
    if upper in NEGATIVE_OUTCOMES:
        return "FAILED"
    if upper in NEUTRAL_OUTCOMES:
        return upper
    return upper or "UNKNOWN"


def rebuild_calibration(database: FoundryDatabase) -> CalibrationState:
    state = CalibrationState()
    for event in database.events(
        readonly=True, event_types=("executive.calibration_recorded",),
    ):
        payload = event.payload
        source = str(payload.get("source") or "")
        objective = str(payload.get("objective") or "")
        canonical = canonicalize_statement(objective)
        pattern_key = " ".join(canonical.split()[:6])
        outcome = _normalize_outcome(str(payload.get("outcome") or ""))
        repairs = int(payload.get("repair_count") or 0)
        if outcome == "FAILED":
            state.failure_counts[canonical] = state.failure_counts.get(canonical, 0) + 1
            if source:
                state.source_adjustments[source] = min(
                    MAX_SOURCE_PENALTY,
                    state.source_adjustments.get(source, 0.0) + PENALTY_PER_FAILURE,
                )
            state.pattern_adjustments[pattern_key] = min(
                MAX_PATTERN_PENALTY,
                state.pattern_adjustments.get(pattern_key, 0.0) + PENALTY_PER_FAILURE,
            )
        elif outcome == "PASSED":
            state.success_counts[canonical] = state.success_counts.get(canonical, 0) + 1
        elif outcome in NEUTRAL_OUTCOMES:
            pass  # recorded but does not punish worker/candidate quality
        if repairs > 0 and outcome == "FAILED":
            if source:
                state.source_adjustments[source] = min(
                    MAX_SOURCE_PENALTY,
                    state.source_adjustments.get(source, 0.0) + repairs * REPAIR_BURDEN_PENALTY,
                )
    return state


def record_calibration(
    database: FoundryDatabase,
    *,
    decision_id: str,
    candidate_id: str,
    mission_id: str,
    source: str,
    objective: str,
    outcome: str,
    repair_count: int = 0,
    actual: dict[str, Any] | None = None,
    source_id: str = "executive",
    terminal_event_id: str | None = None,
) -> str:
    from ..events import Event

    normalized = _normalize_outcome(outcome)
    terminal_key = terminal_event_id or _authoritative_terminal_key(
        database, mission_id, normalized,
    )
    payload = {
        "decision_id": decision_id,
        "candidate_id": candidate_id,
        "mission_id": mission_id,
        "source": source,
        "objective": objective,
        "outcome": normalized,
        "repair_count": repair_count,
        "actual": actual or {},
        "calibration_version": CALIBRATION_VERSION,
        "terminal_event_id": terminal_key,
    }
    event = Event.create(
        "executive.calibration_recorded",
        payload,
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"executive.calibration_recorded:{terminal_key}",
    )
    return str(database.append(event)[0])


def _authoritative_terminal_key(
    database: FoundryDatabase,
    mission_id: str,
    outcome: str,
) -> str:
    """Derive idempotency key from the authoritative terminal mission application."""
    for event in database.events(
        readonly=True,
        event_types=(
            "verification.accepted", "verification.failed",
            "verification.blocked", "mission.completed",
        ),
        newest_first=True,
        limit=500,
    ):
        event_type = event.event_type
        if event_type in (
            "verification.accepted", "verification.failed", "verification.blocked",
            "mission.completed",
        ):
            payload = event.payload
            if str(payload.get("mission_id") or "") == mission_id:
                return f"{event_type}:{event.event_id}"
            if event_type.startswith("verification."):
                mission = database.row("missions", "mission_id", mission_id)
                if mission is not None:
                    return f"{event_type}:{event.event_id}"
    return f"mission.terminal:{mission_id}:{outcome}"


def outcome_from_mission(database: FoundryDatabase, mission_id: str) -> dict[str, Any]:
    """Derive actual outcome evidence from durable mission records."""
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        return {"outcome": "UNKNOWN"}
    with database.connect() as connection:
        verdicts = connection.execute(
            "SELECT verdict_id, status, verdict, findings_json "
            "FROM verification_verdicts v "
            "LEFT JOIN events e ON e.event_id = v.last_event_id "
            "WHERE v.mission_id = ? "
            "ORDER BY COALESCE(e.sequence, v.rowid) DESC LIMIT 5",
            (mission_id,),
        ).fetchall()
        runs = connection.execute(
            "SELECT status, started_at, finished_at FROM agent_runs "
            "WHERE mission_id = ? ORDER BY rowid DESC LIMIT 3",
            (mission_id,),
        ).fetchall()
        repairs = connection.execute(
            "SELECT COUNT(1) FROM events WHERE event_type = 'mission.repair_started' "
            "AND payload_json LIKE ?",
            (f'%"mission_id":"{mission_id}"%',),
        ).fetchone()[0]
        blocked_events = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'worker_loop.contract_blocked' AND payload_json LIKE ? LIMIT 1",
            (f'%"mission_id":"{mission_id}"%',),
        ).fetchone()
    accepted = accepted_verifications(database, mission_id=mission_id)
    with database.connect(readonly=True) as connection:
        completion_row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = 'mission.completed' "
            "AND json_extract(payload_json, '$.mission_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (mission_id,),
        ).fetchone()
    completion_passed = False
    if completion_row is not None:
        try:
            completion_payload = json.loads(str(completion_row[0]))
        except (TypeError, ValueError):
            completion_payload = {}
        completion = completion_payload.get("outcome")
        if isinstance(completion, dict):
            completion_passed = any(
                str(value).upper() == "PASSED"
                for value in completion.values()
            )
        else:
            completion_passed = str(completion or "").upper() == "PASSED"
    last_verdict_id = str(verdicts[0][0]) if verdicts else ""
    last_verdict_status = str(verdicts[0][1]) if verdicts else "UNKNOWN"
    last_verdict_value = str(verdicts[0][2]) if verdicts else "UNKNOWN"
    last_verdict = last_verdict_status
    mission_state = str(mission["state"])
    # Acceptance is bound to the exact latest verdict identity. An older
    # accepted PASS must never authorize a newer preliminary PASS.
    accepted_pass = (
        last_verdict_status == "PASS"
        and last_verdict_value == "PASS"
        and last_verdict_id in accepted
    )
    if accepted_pass and mission_state == "COMPLETE" and completion_passed:
        outcome = "PASSED"
    elif last_verdict == "FAIL":
        outcome = "FAILED_VERIFICATION"
    elif mission_state == "BLOCKED":
        reason = ""
        if blocked_events:
            try:
                reason = str(json.loads(blocked_events[0]).get("reason") or "")
            except (TypeError, ValueError):
                reason = ""
        if "external" in reason.lower() or "dependency" in reason.lower():
            outcome = "BLOCKED_EXTERNAL_DEPENDENCY"
        else:
            outcome = "BLOCKED_CAPABILITY"
    elif mission_state in ("FAILED", "REPAIRING"):
        outcome = "FAILED"
    else:
        outcome = mission_state
    duration_s = None
    if runs and runs[0][1] and runs[0][2]:
        try:
            from datetime import datetime
            start = datetime.fromisoformat(str(runs[0][1]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(runs[0][2]).replace("Z", "+00:00"))
            duration_s = max(0.0, (end - start).total_seconds())
        except ValueError:
            duration_s = None
    return {
        "outcome": outcome,
        "mission_state": mission_state,
        "verdict": last_verdict,
        "repair_count": int(repairs),
        "duration_seconds": duration_s,
        "effort_proxy": float(repairs) + (0.0 if outcome == "PASSED" else 1.0),
    }
