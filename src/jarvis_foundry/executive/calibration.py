"""Bounded deterministic calibration from recorded outcomes."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..database import FoundryDatabase
from ..knowledge import canonicalize_statement

CALIBRATION_VERSION = "1.0.0"
MAX_SOURCE_PENALTY = 0.25
MAX_PATTERN_PENALTY = 0.30
PENALTY_PER_FAILURE = 0.05
REPAIR_BURDEN_PENALTY = 0.04
SUCCESS_BONUS_CAP = 0.10

NEGATIVE_OUTCOMES = frozenset({"FAILED", "FAILED_VERIFICATION", "FAIL"})
NEUTRAL_OUTCOMES = frozenset({
    "BLOCKED", "BLOCKED_EXTERNAL_DEPENDENCY", "BLOCKED_CAPABILITY", "NOT_TESTED",
})
POSITIVE_OUTCOMES = frozenset({"PASSED", "PASS"})


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
    for event in database.events():
        if event.event_type != "executive.calibration_recorded":
            continue
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
) -> str:
    from ..events import Event

    normalized = _normalize_outcome(outcome)
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
    }
    event = Event.create(
        "executive.calibration_recorded",
        payload,
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"executive.calibration_recorded:{mission_id}:{normalized}",
    )
    return str(database.append(event)[0])


def outcome_from_mission(database: FoundryDatabase, mission_id: str) -> dict[str, Any]:
    """Derive actual outcome evidence from durable mission records."""
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        return {"outcome": "UNKNOWN"}
    with database.connect() as connection:
        verdicts = connection.execute(
            "SELECT status, findings_json FROM verification_verdicts "
            "WHERE mission_id = ? ORDER BY rowid DESC LIMIT 5",
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
    last_verdict = str(verdicts[0][0]) if verdicts else "UNKNOWN"
    mission_state = str(mission["state"])
    if last_verdict == "PASS":
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
