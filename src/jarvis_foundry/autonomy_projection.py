"""Bounded, read-only V11 autonomy projection for the Command Center.

The projection joins durable v39 read models and a small, indexed slice of
material ledger events.  It never calls scoring, admission, MissionLoop, or
learning code and therefore cannot create work while serving a GET request.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .database import FoundryDatabase


AUTONOMY_PROJECTION_SCHEMA = "v11.command-center-autonomy.v1"
AUTONOMY_FILTERS = {"all", "eligible", "rejected", "researched", "stale"}
MAX_CANDIDATES = 50
MAX_CANDIDATE_SCAN = 200
MAX_TIMELINE = 100
MAX_SOURCE_SCAN = 256

_TIMELINE_TYPES = (
    "world.observation_recorded",
    "discovery.candidate_generated",
    "research.candidate_completed",
    "discovery.candidate_judged",
    "executive.objective_created",
    "executive.objective_recorded",
    "executive.candidate_admitted",
    "mission.created",
    "agent_run.started",
    "agent_run.completed",
    "agent_run.failed",
    "verification.started",
    "verification.passed",
    "verification.failed",
    "verification.blocked",
    "verification.accepted",
    "discovery.candidate_outcome_recorded",
    "executive.calibration_recorded",
    "world.calibration_recorded",
    "initiative.cycle_completed",
)


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value:
        return fallback
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _now(value: str | None) -> datetime:
    parsed = _iso(value)
    return parsed or datetime.now(timezone.utc)


def _bounded(value: int, *, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def _rows(connection: sqlite3.Connection, query: str,
          args: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, tuple(args)).fetchall()]


def _in_rows(connection: sqlite3.Connection, table: str, key: str,
             values: set[str], *, order: str = "rowid DESC",
             limit: int = MAX_CANDIDATE_SCAN) -> list[dict[str, Any]]:
    if not values:
        return []
    placeholders = ",".join("?" for _ in values)
    return _rows(
        connection,
        f"SELECT * FROM {table} WHERE {key} IN ({placeholders}) "
        f"ORDER BY {order} LIMIT ?",
        (*sorted(values), int(limit)),
    )


def _empty(reason: str, generated_at: str) -> dict[str, Any]:
    unavailable = {"value": None, "status": "UNAVAILABLE"}
    return {
        "schema": AUTONOMY_PROJECTION_SCHEMA,
        "status": "UNAVAILABLE",
        "reason": reason,
        "generated_at": generated_at,
        "executive": {
            "state": "UNAVAILABLE", "cycle_id": None, "stage": None,
            "last_observation_cycle": None, "next_wake": None,
            "wait_reason": None, "active_wip": unavailable,
        },
        "funnel": {
            key: unavailable for key in (
                "observed", "generated", "deduped", "researched",
                "rejected", "eligible", "executing", "verified_outcome",
            )
        },
        "candidates": [],
        "sources": [],
        "timeline": [],
        "current_work": None,
        "limits": {"candidate_limit": MAX_CANDIDATES,
                   "timeline_limit": MAX_TIMELINE},
    }


def _latest_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    meaningful: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (str(row.get("source_class") or "UNKNOWN"),
               str(row.get("source_identity") or "UNKNOWN"))
        if key not in latest:
            latest[key] = row
        if key not in meaningful and (
            int(row.get("observations_count") or 0) > 0
            or int(row.get("candidates_count") or 0) > 0
        ):
            meaningful[key] = str(row.get("updated_at") or "")
    result: list[dict[str, Any]] = []
    for key, row in latest.items():
        raw = str(row.get("status") or "UNKNOWN").upper()
        reason = str(row.get("reason") or "")
        if raw in {"READY", "COMPLETED", "IDLE"}:
            state = "READY"
        elif raw == "BLOCKED":
            state = "BLOCKED"
        else:
            state = "DEGRADED"
        readiness = (
            "DEFERRED" if state == "BLOCKED"
            and (key[0].lower() == "gmail" or "defer" in reason.lower())
            else state
        )
        result.append({
            "source_kind": key[0],
            "source_identity": key[1],
            "state": state,
            "readiness": readiness,
            "last_checked": row.get("updated_at"),
            "last_meaningful_change": meaningful.get(key),
            "next_due": row.get("next_due_at"),
            "last_error": reason if state != "READY" else None,
            "observations": int(row.get("observations_count") or 0),
            "candidates": int(row.get("candidates_count") or 0),
            "event_id": row.get("last_event_id"),
        })
    return sorted(result, key=lambda item: (
        {"BLOCKED": 0, "DEGRADED": 1, "READY": 2}.get(item["state"], 3),
        item["source_kind"], item["source_identity"],
    ))


def _factor_list(factors: dict[str, Any], names: tuple[str, ...]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for name in names:
        raw = factors.get(name)
        if not isinstance(raw, dict):
            continue
        values.append({
            "factor": name,
            "value": raw.get("value"),
            "kind": raw.get("kind", "UNKNOWN"),
            "contribution": raw.get("contribution"),
        })
    return values


def _judgment_evidence(row: dict[str, Any]) -> dict[str, Any]:
    factors = _json(row.get("factors_json"), {})
    factors = factors if isinstance(factors, dict) else {}
    return {
        "owner_relevance": _factor_list(factors, (
            "goal_fit", "owner_priority", "owner_context_goal_relevance",
            "active_project_relevance",
        )),
        "expected_value": _factor_list(factors, ("expected_value", "information_gain")),
        "confidence": _factor_list(factors, ("confidence", "research_confidence")),
        "effort": _factor_list(factors, ("effort", "cost", "research_budget_headroom")),
        "capability_readiness": _factor_list(factors, (
            "fabric_capability_readiness", "dependency_readiness",
            "autonomous_wip_headroom",
        )),
        "opportunity_cost": _factor_list(factors, (
            "opportunity_cost_headroom", "resource_contention",
        )),
        "duplicate_rejection_history": _factor_list(factors, (
            "novelty_history", "standing_rejection_clear",
        )),
        "all_factors": factors,
    }


def _event_stage(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type == "world.observation_recorded":
        return "OBSERVED"
    if event_type == "discovery.candidate_generated":
        return "CANDIDATE"
    if event_type == "research.candidate_completed":
        return "RESEARCHED"
    if event_type == "discovery.candidate_judged":
        outcome = str(payload.get("outcome") or "").upper()
        return outcome if outcome in {"REJECTED", "ELIGIBLE"} else None
    if event_type.startswith("executive.objective") or event_type == "executive.candidate_admitted":
        return "OBJECTIVE"
    if event_type == "mission.created" or event_type.startswith("agent_run."):
        return "BUILD"
    if event_type.startswith("verification.") and event_type != "verification.accepted":
        return "VERIFY"
    if event_type in {"verification.accepted", "discovery.candidate_outcome_recorded"}:
        return "OUTCOME"
    if event_type.endswith("calibration_recorded"):
        return "LEARN"
    if event_type == "initiative.cycle_completed":
        return "WAIT" if str(payload.get("status") or "").upper() in {
            "WAIT", "IDLE", "BLOCKED", "STOPPED",
        } else None
    return None


def _timeline(connection: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in _TIMELINE_TYPES)
    rows = _rows(
        connection,
        "SELECT sequence, event_id, event_type, occurred_at, recorded_at, "
        "source_type, source_id, correlation_id, payload_json FROM events "
        f"WHERE event_type IN ({placeholders}) ORDER BY sequence DESC LIMIT ?",
        (*_TIMELINE_TYPES, int(limit)),
    )
    result: list[dict[str, Any]] = []
    for row in reversed(rows):
        payload = _json(row.pop("payload_json", "{}"), {})
        payload = payload if isinstance(payload, dict) else {}
        stage = _event_stage(str(row["event_type"]), payload)
        if stage is None:
            continue
        summary = (
            payload.get("reason_detail") or payload.get("reason")
            or payload.get("objective") or payload.get("title")
            or payload.get("status") or row["event_type"]
        )
        result.append({
            **row,
            "stage": stage,
            "summary": str(summary)[:500],
            "candidate_id": payload.get("candidate_id"),
            "mission_id": payload.get("mission_id"),
            "objective_id": payload.get("objective_id"),
            "verdict_id": payload.get("verdict_id"),
            "decision": payload.get("decision") or payload.get("outcome")
                        or payload.get("verdict") or payload.get("status"),
        })
    return result


def _accepted_receipts(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = _rows(
        connection,
        "SELECT event_id, occurred_at, payload_json FROM events "
        "WHERE event_type = 'verification.accepted' "
        "ORDER BY sequence DESC LIMIT 500",
    )
    accepted: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _json(row.get("payload_json"), {})
        if not isinstance(payload, dict) or payload.get("decision") != "ACCEPT":
            continue
        verdict_id = str(payload.get("verdict_id") or "")
        if verdict_id and verdict_id not in accepted:
            accepted[verdict_id] = {
                "event_id": row.get("event_id"),
                "accepted_at": row.get("occurred_at"),
                **payload,
            }
    return accepted


def _latest_by(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if value and value not in result:
            result[value] = row
    return result


def _outcome_for(
    candidate: dict[str, Any], mission: dict[str, Any] | None,
    verdict: dict[str, Any] | None, accepted: dict[str, dict[str, Any]],
    outcome: dict[str, Any] | None, calibration: dict[str, Any] | None,
) -> dict[str, Any]:
    mission_state = str((mission or {}).get("state") or "UNAVAILABLE").upper()
    verdict_state = str((verdict or {}).get("status") or "UNAVAILABLE").upper()
    verdict_id = str((verdict or {}).get("verdict_id") or "")
    receipt = accepted.get(verdict_id)
    if mission_state in {"FAILED", "BLOCKED", "CANCELLED"}:
        final_state = mission_state
    elif verdict_state == "PASS" and receipt is not None:
        final_state = "ACCEPTED"
    elif verdict_state == "PASS":
        final_state = "PRELIMINARY_PASS"
    elif verdict_state in {"FAIL", "BLOCKED", "NOT_TESTED", "STARTED"}:
        final_state = verdict_state
    elif mission is None:
        final_state = "UNAVAILABLE"
    else:
        final_state = "PENDING"
    actual = _json((outcome or {}).get("actual_value_json"), {})
    return {
        "state": final_state,
        "mission_state": mission_state,
        "verification": {
            "status": verdict_state,
            "verdict_id": verdict_id or None,
            "acceptance": "ACCEPTED" if receipt is not None else (
                "PENDING" if verdict_state == "PASS" else "NOT_APPLICABLE"
            ),
            "acceptance_event_id": (receipt or {}).get("event_id"),
            "reason": (verdict or {}).get("reason"),
        },
        "repair_attempts": (
            calibration.get("repair_count")
            if calibration and isinstance(calibration.get("repair_count"), int)
            else None
        ),
        "actual": actual if isinstance(actual, dict) and actual else None,
        "measurement_status": (outcome or {}).get("status", "UNAVAILABLE"),
        "learned_calibration": calibration,
        "next_action": (mission or {}).get("next_action"),
        "candidate_id": candidate.get("candidate_id"),
    }


def build_autonomy_projection(
    database: FoundryDatabase, *, candidate_filter: str = "all",
    candidate_limit: int = MAX_CANDIDATES, timeline_limit: int = MAX_TIMELINE,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the V11 owner view in one read-only snapshot.

    All high-cardinality reads carry hard limits. Candidate-related tables are
    loaded in bulk, so API cost does not grow as one query per displayed row.
    """
    selected_filter = str(candidate_filter or "all").lower()
    if selected_filter not in AUTONOMY_FILTERS:
        raise ValueError(f"unsupported autonomy filter: {selected_filter}")
    candidate_limit = _bounded(candidate_limit, low=1, high=MAX_CANDIDATES)
    timeline_limit = _bounded(timeline_limit, low=1, high=MAX_TIMELINE)
    generated_at = (now or datetime.now(timezone.utc).isoformat()).replace("+00:00", "Z")
    clock = _now(now)

    try:
        with database.read_snapshot() as snapshot:
            connection = snapshot.connection
            if connection is None:  # pragma: no cover - context invariant
                raise RuntimeError("read snapshot connection unavailable")
            else:
                schema = int(connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0])
                if schema < 39:
                    return _empty("schema v39 autonomy projections are unavailable", generated_at)
                supervisor_row = connection.execute(
                    "SELECT status, trust_state, last_heartbeat_at FROM supervisor_state "
                    "WHERE singleton = 1"
                ).fetchone()
                cycle_row = connection.execute(
                    "SELECT * FROM initiative_cycles ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                cycle = dict(cycle_row) if cycle_row else None
                source_rows = _rows(
                    connection,
                    "SELECT * FROM initiative_source_runs "
                    "ORDER BY rowid DESC LIMIT ?", (MAX_SOURCE_SCAN,),
                )
                judgment_rows = _rows(
                    connection,
                    "SELECT j.*, c.objective, c.expected_outcome, c.status AS candidate_status, "
                    "c.mission_id AS holding_mission_id, c.duplicate_of, c.expires_at, "
                    "c.updated_at AS candidate_updated_at, c.last_event_id AS candidate_event_id "
                    "FROM candidate_judgments j JOIN candidates c "
                    "ON c.candidate_id = j.candidate_id "
                    "ORDER BY j.rowid DESC LIMIT ?", (MAX_CANDIDATE_SCAN,),
                )
                latest_judgments: list[dict[str, Any]] = []
                seen: set[str] = set()
                for row in judgment_rows:
                    candidate_id = str(row.get("candidate_id") or "")
                    if candidate_id and candidate_id not in seen:
                        latest_judgments.append(row)
                        seen.add(candidate_id)
                candidate_ids = set(seen)
                source_links = _in_rows(
                    connection, "candidate_sources", "candidate_id", candidate_ids,
                    limit=MAX_CANDIDATE_SCAN * 3,
                )
                if candidate_ids:
                    placeholders = ",".join("?" for _ in candidate_ids)
                    research_rows = _rows(
                        connection,
                        "SELECT * FROM research_queue WHERE candidate_id IN "
                        f"({placeholders}) AND pass_number IS NOT NULL "
                        "ORDER BY rowid DESC LIMIT ?",
                        (*sorted(candidate_ids), MAX_CANDIDATE_SCAN * 2),
                    )
                else:
                    research_rows = []
                recent_missions = _rows(
                    connection,
                    "SELECT * FROM missions ORDER BY rowid DESC LIMIT 200",
                )
                mission_by_candidate: dict[str, dict[str, Any]] = {}
                for mission in recent_missions:
                    created_from = str(mission.get("created_from") or "")
                    if created_from.startswith("candidate:"):
                        mission_by_candidate.setdefault(created_from.split(":", 1)[1], mission)
                mission_ids = {
                    str(row.get("mission_id")) for row in mission_by_candidate.values()
                    if row.get("mission_id")
                }
                step_rows = _in_rows(
                    connection, "mission_step_contracts", "mission_id", mission_ids,
                    limit=400,
                )
                run_rows = _in_rows(
                    connection, "agent_runs", "mission_id", mission_ids,
                    limit=400,
                )
                verdict_rows = _in_rows(
                    connection, "verification_verdicts", "mission_id", mission_ids,
                    limit=400,
                )
                outcome_rows = _in_rows(
                    connection, "candidate_outcomes", "candidate_id", candidate_ids,
                    limit=MAX_CANDIDATE_SCAN * 3,
                )
                accepted = _accepted_receipts(connection)
                calibration_events = _rows(
                    connection,
                    "SELECT event_id, occurred_at, payload_json FROM events "
                    "WHERE event_type = 'executive.calibration_recorded' "
                    "ORDER BY sequence DESC LIMIT 200",
                )
                timeline = _timeline(connection, timeline_limit)
    except (OSError, sqlite3.Error) as error:
        return _empty(f"{type(error).__name__}: {error}", generated_at)

    source_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in source_links:
        source_by_candidate.setdefault(str(row.get("candidate_id") or ""), []).append(row)
    research_by_candidate = _latest_by(research_rows, "candidate_id")
    verdict_by_mission = _latest_by(verdict_rows, "mission_id")
    outcome_by_candidate = _latest_by(outcome_rows, "candidate_id")
    step_by_mission = _latest_by(step_rows, "mission_id")
    run_by_mission = _latest_by(run_rows, "mission_id")
    calibration_by_candidate: dict[str, dict[str, Any]] = {}
    for row in calibration_events:
        payload = _json(row.get("payload_json"), {})
        if not isinstance(payload, dict):
            continue
        candidate_id = str(payload.get("candidate_id") or "")
        if candidate_id and candidate_id not in calibration_by_candidate:
            calibration_by_candidate[candidate_id] = {
                **payload, "event_id": row.get("event_id"),
                "occurred_at": row.get("occurred_at"),
            }

    candidates: list[dict[str, Any]] = []
    for row in latest_judgments:
        candidate_id = str(row.get("candidate_id") or "")
        links = source_by_candidate.get(candidate_id, [])
        source = links[0] if links else {}
        research = research_by_candidate.get(candidate_id)
        expires_at = str(row.get("expires_at") or source.get("fresh_until") or "") or None
        observed = _iso(str(source.get("observed_at") or ""))
        expires = _iso(expires_at)
        stale = bool(expires and expires <= clock)
        outcome_state = str(row.get("outcome") or "UNKNOWN").upper()
        mission = mission_by_candidate.get(candidate_id)
        mission_id = str((mission or {}).get("mission_id") or "")
        verdict = verdict_by_mission.get(mission_id)
        final = _outcome_for(
            row, mission, verdict, accepted, outcome_by_candidate.get(candidate_id),
            calibration_by_candidate.get(candidate_id),
        )
        factors = _judgment_evidence(row)
        confidence_values = [
            item.get("value") for item in factors["confidence"]
            if isinstance(item.get("value"), (int, float))
        ]
        candidates.append({
            "candidate_id": candidate_id,
            "title": row.get("objective") or row.get("expected_outcome") or candidate_id,
            "expected_outcome": row.get("expected_outcome"),
            "project": source.get("source_identity") if str(source.get("source_kind") or "").lower() == "project" else None,
            "sources": [{
                "kind": item.get("source_kind"),
                "identity": item.get("source_identity"),
                "ref": item.get("source_ref"),
                "authority": item.get("authority"),
                "event_id": item.get("last_event_id"),
            } for item in links],
            "score": row.get("score"),
            "rank": row.get("rank"),
            "confidence": max(confidence_values) if confidence_values else None,
            "research": {
                "status": (research or {}).get("status", "NOT_RESEARCHED"),
                "research_id": (research or {}).get("item_id"),
                "source_ref": (research or {}).get("source_ref"),
                "budget_used": (research or {}).get("budget_used"),
                "completed_at": (research or {}).get("completed_at"),
                "event_id": (research or {}).get("last_event_id"),
            },
            "state": outcome_state,
            "rejection": {
                "reason_code": row.get("reason_code"),
                "reason": row.get("reason_detail"),
                "event_id": row.get("last_event_id"),
            } if outcome_state == "REJECTED" else None,
            "judgment": {
                "decision_id": row.get("decision_id"),
                "policy_version": row.get("policy_version"),
                "do_nothing_won": bool(row.get("do_nothing_won")),
                "reason_code": row.get("reason_code"),
                "reason": row.get("reason_detail"),
                "evidence": factors,
                "event_id": row.get("last_event_id"),
            },
            "age_seconds": max(0, int((clock - observed).total_seconds())) if observed else None,
            "expires_at": expires_at,
            "ttl_seconds": max(0, int((expires - clock).total_seconds())) if expires else None,
            "stale": stale,
            "current_work": {
                "objective": {"status": "UNAVAILABLE", "objective_id": None},
                "mission": ({
                    "status": str(mission.get("state") or "UNKNOWN"),
                    "mission_id": mission_id,
                    "objective": mission.get("objective"),
                    "event_id": mission.get("last_event_id"),
                } if mission else {"status": "UNAVAILABLE", "mission_id": None}),
                "worktree": ({
                    "status": "PENDING", "path": None,
                    "reason": "A3 worktree contract is not present in durable projections",
                } if mission else {"status": "UNAVAILABLE", "path": None}),
                "current_step": ({
                    "status": "AVAILABLE", "step_id": step_by_mission[mission_id].get("step_id"),
                    "capability": step_by_mission[mission_id].get("capability"),
                    "event_id": step_by_mission[mission_id].get("last_event_id"),
                } if mission_id in step_by_mission else {"status": "UNAVAILABLE", "step_id": None}),
                "worker": ({
                    "status": run_by_mission[mission_id].get("status"),
                    "run_id": run_by_mission[mission_id].get("run_id"),
                    "role": run_by_mission[mission_id].get("role"),
                    "event_id": run_by_mission[mission_id].get("last_event_id"),
                } if mission_id in run_by_mission else {"status": "UNAVAILABLE", "run_id": None}),
                "verifier": ({
                    "status": verdict.get("status"), "verdict_id": verdict.get("verdict_id"),
                    "run_id": verdict.get("verifier_run_id"),
                    "event_id": verdict.get("last_event_id"),
                } if verdict else {"status": "UNAVAILABLE", "verdict_id": None}),
                "final_outcome": final,
            },
        })
    all_candidates = candidates
    candidates = [item for item in all_candidates if (
        selected_filter == "all"
        or (selected_filter == "eligible" and item["state"] == "ELIGIBLE")
        or (selected_filter == "rejected" and item["state"] == "REJECTED")
        or (selected_filter == "researched" and item["research"]["status"] != "NOT_RESEARCHED")
        or (selected_filter == "stale" and item["stale"])
    )][:candidate_limit]

    metrics = _json((cycle or {}).get("metrics_json"), {})
    metrics = metrics if isinstance(metrics, dict) else {}
    bridge_present = bool(mission_by_candidate)
    executing = sum(
        str(row.get("state") or "").upper() in {"ACTIVE", "IN_PROGRESS", "RUNNING", "BLOCKED"}
        for row in mission_by_candidate.values()
    )
    verified = sum(
        item["current_work"]["final_outcome"]["state"] == "ACCEPTED"
        for item in all_candidates
    )
    def metric(key: str) -> dict[str, Any]:
        value = metrics.get(key)
        return {"value": value if isinstance(value, int) else None,
                "status": "AVAILABLE" if isinstance(value, int) else "UNAVAILABLE"}
    latest_cycle = (cycle or {}).get("cycle_id")
    current_candidate = next((item for item in all_candidates
        if item["state"] == "ELIGIBLE" or
        item["current_work"]["mission"]["status"] != "UNAVAILABLE"), None)
    current_work = (current_candidate["current_work"] | {
        "candidate": {
            "candidate_id": current_candidate["candidate_id"],
            "title": current_candidate["title"],
            "state": current_candidate["state"],
            "event_id": current_candidate["judgment"]["event_id"],
        }
    }) if current_candidate else None
    supervisor = dict(supervisor_row) if supervisor_row else {}
    return {
        "schema": AUTONOMY_PROJECTION_SCHEMA,
        "status": "AVAILABLE" if cycle or candidates or source_rows else "EMPTY",
        "reason": None,
        "generated_at": generated_at,
        "executive": {
            "state": (cycle or {}).get("status") or supervisor.get("status") or "UNKNOWN",
            "supervisor_state": supervisor.get("status", "UNKNOWN"),
            "trust_state": supervisor.get("trust_state", "UNKNOWN"),
            "cycle_id": latest_cycle,
            "stage": (cycle or {}).get("stage"),
            "last_observation_cycle": (cycle or {}).get("completed_at") or (cycle or {}).get("started_at"),
            "next_wake": (cycle or {}).get("next_due_at"),
            "wait_reason": (cycle or {}).get("reason") or None,
            "wait_boundary": (
                current_candidate["judgment"].get("reason")
                if current_candidate else None
            ),
            "wait_reason_code": (
                current_candidate["judgment"].get("reason_code")
                if current_candidate else None
            ),
            "owner_action": {"value": None, "status": "UNAVAILABLE"},
            "active_wip": {
                "value": executing if bridge_present else None,
                "status": "AVAILABLE" if bridge_present else "UNAVAILABLE",
            },
        },
        "funnel": {
            "observed": metric("observations_recorded"),
            "generated": metric("raw_candidates_generated"),
            "deduped": metric("unique_candidates"),
            "duplicates_suppressed": metric("candidate_duplicates_suppressed"),
            "researched": metric("candidates_researched"),
            "rejected": metric("candidates_rejected"),
            "eligible": metric("candidates_eligible"),
            "executing": {"value": executing if bridge_present else None,
                          "status": "AVAILABLE" if bridge_present else "UNAVAILABLE"},
            "verified_outcome": {"value": verified if bridge_present else None,
                                 "status": "AVAILABLE" if bridge_present else "UNAVAILABLE"},
        },
        "candidates": candidates,
        "candidate_filter": selected_filter,
        "sources": _latest_sources(source_rows),
        "timeline": timeline,
        "current_work": current_work,
        "limits": {
            "candidate_limit": candidate_limit,
            "candidate_scan_limit": MAX_CANDIDATE_SCAN,
            "timeline_limit": timeline_limit,
            "source_scan_limit": MAX_SOURCE_SCAN,
        },
    }


__all__ = [
    "AUTONOMY_FILTERS", "AUTONOMY_PROJECTION_SCHEMA",
    "build_autonomy_projection",
]
