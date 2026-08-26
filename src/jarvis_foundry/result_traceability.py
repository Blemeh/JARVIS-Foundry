"""Read-only result traceability for the V4 owner surface.

The ledger already stores the pieces of a result in several projections.  This
module joins those pieces by durable IDs for inspection; it never promotes a
claim, rewrites a projection, or turns a missing record into a success.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .database import FoundryDatabase
from .owner_directives import owner_directive_snapshot


TRACEABILITY_SCHEMA = "v4.result-traceability.v1"


def _unknown(reason: str) -> dict[str, Any]:
    return {"status": "UNKNOWN", "value": None, "reason": reason}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _event_sequence(database: FoundryDatabase) -> list[tuple[int, Any]]:
    events = list(reversed(database.events(readonly=True, limit=10000, newest_first=True)))
    return list(enumerate(events, start=1))


def _projection_rows(
    database: FoundryDatabase, table: str, mission_id: str,
) -> list[dict[str, Any]]:
    """Read one additive projection for compatibility callers.

    ``build_result_traceability`` uses the bulk form below.  Keeping this
    narrow helper preserves the defensive behavior for older schemas and
    avoids making a generic ``FoundryDatabase`` table allowlist broader.
    """
    try:
        with database.connect(readonly=True) as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE mission_id = ? ORDER BY rowid",
                (mission_id,),
            ).fetchall()
    except Exception:  # noqa: BLE001 - an older candidate may lack an additive table
        return []
    return [dict(row) for row in rows]


def _bulk_projection_rows(
    database: FoundryDatabase, table: str, mission_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Load a bounded projection once, grouped by mission.

    The traceability packet is an owner-facing read model.  Opening several
    writable SQLite connections for every outcome made a high-cardinality
    ledger scale as O(results * connections), and on Windows each connection
    also paid WAL/handle setup cost.  A single read-only query per projection
    keeps the same rows and ordering while making the cost proportional to the
    bounded packet rather than the number of results times the number of
    joins.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not mission_ids:
        return grouped
    # SQLite's default variable limit is at least 999 on supported builds;
    # traceability itself is capped at 500 rows, so this remains one query.
    placeholders = ", ".join("?" for _ in mission_ids)
    try:
        with database.connect(readonly=True) as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE mission_id IN ({placeholders}) ORDER BY rowid",
                tuple(sorted(mission_ids)),
            ).fetchall()
    except Exception:  # noqa: BLE001 - an older candidate may lack an additive table
        return grouped
    for row in rows:
        item = dict(row)
        mission_id = str(item.get("mission_id") or "")
        grouped.setdefault(mission_id, []).append(item)
    return grouped


def _bulk_rows(
    database: FoundryDatabase, table: str, key: str, values: set[str],
) -> dict[str, dict[str, Any]]:
    """Load projection rows keyed by an explicit identifier in one read."""
    result: dict[str, dict[str, Any]] = {}
    if not values:
        return result
    placeholders = ", ".join("?" for _ in values)
    try:
        with database.connect(readonly=True) as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE {key} IN ({placeholders}) ORDER BY rowid",
                tuple(sorted(values)),
            ).fetchall()
    except Exception:  # noqa: BLE001 - an older candidate may lack a projection
        return result
    for row in rows:
        item = dict(row)
        identifier = str(item.get(key) or "")
        if identifier and identifier not in result:
            result[identifier] = item
    return result


def _payload_matches(payload: dict[str, Any], *, candidate_id: str,
                     mission_id: str) -> bool:
    return any(
        str(payload.get(key) or "") in {candidate_id, mission_id}
        for key in ("candidate_id", "mission_id", "subject_id", "entity_id")
    )


def _event_ref(sequence: int, event: Any, *, summary: str | None = None) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at,
        "summary": summary or event.payload.get("reason") or event.event_type,
    }


def _latest_decision(events: Iterable[tuple[int, Any]], *, candidate_id: str,
                     mission_id: str) -> dict[str, Any]:
    matched: list[tuple[int, Any]] = []
    for sequence, event in events:
        if event.event_type not in {"executive.decision_recorded", "executive.decision_materialized"}:
            continue
        if _payload_matches(event.payload, candidate_id=candidate_id, mission_id=mission_id):
            matched.append((sequence, event))
    if not matched:
        return _unknown("no executive decision event names this candidate or mission")
    sequence, event = matched[-1]
    payload = event.payload
    return {
        "status": "EVIDENCED",
        "decision_id": payload.get("decision_id"),
        "decision": payload.get("decision"),
        "reason": payload.get("reason"),
        "event": _event_ref(sequence, event),
        "candidate_id": payload.get("candidate_id"),
        "mission_id": payload.get("mission_id"),
    }


def _events_for_mission(events: Iterable[tuple[int, Any]], *, candidate_id: str,
                        mission_id: str, event_types: set[str] | None = None,
                        prefixes: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sequence, event in events:
        if event_types is not None and event.event_type not in event_types:
            continue
        if prefixes and not event.event_type.startswith(prefixes):
            continue
        if not _payload_matches(event.payload, candidate_id=candidate_id, mission_id=mission_id):
            continue
        rows.append(_event_ref(sequence, event, summary=(
            event.payload.get("failure_class")
            or event.payload.get("verdict")
            or event.payload.get("outcome")
            or event.payload.get("reason")
            or event.event_type
        )))
    return rows


def _owner_decisions_from_snapshot(
    snapshot: dict[str, Any], *, candidate_id: str, mission_id: str,
) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    identifiers = {candidate_id, mission_id, "GLOBAL", "global"}
    for record in snapshot.get("records", ()):  # history is useful for why/what changed
        if not isinstance(record, dict):
            continue
        target = str(record.get("target") or record.get("resource_id") or "")
        exact_text = str(record.get("exact_text") or "")
        if target in identifiers or candidate_id in exact_text or mission_id in exact_text:
            relevant.append({
                "directive_id": record.get("directive_id"),
                "command_type": record.get("command_type"),
                "target": record.get("target"),
                "status": record.get("state") or ("ACTIVE" if record.get("active") else "INACTIVE"),
                "exact_text": record.get("exact_text"),
                "event_id": record.get("event_id"),
                "occurred_at": record.get("occurred_at"),
                "provenance": record.get("provenance"),
            })
    return relevant


def _owner_decisions(database: FoundryDatabase, *, candidate_id: str,
                     mission_id: str, now: str | None) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers that trace one row directly."""
    return _owner_decisions_from_snapshot(
        owner_directive_snapshot(database, now=now),
        candidate_id=candidate_id,
        mission_id=mission_id,
    )


def _next_action(mission: dict[str, Any] | None, outcome: dict[str, Any],
                 failures: list[dict[str, Any]]) -> dict[str, Any]:
    if mission and mission.get("next_action"):
        return {"status": "EVIDENCED", "value": mission["next_action"], "source": "missions.next_action"}
    state = str((mission or {}).get("state") or "UNKNOWN").upper()
    if state in {"BLOCKED", "FAILED"}:
        return {"status": "DERIVED", "value": "RECONCILE_OR_REPAIR", "basis": f"mission state is {state}"}
    if state == "COMPLETE" and outcome.get("status") == "UNKNOWN":
        return {"status": "DERIVED", "value": "RECONCILE_OUTCOME", "basis": "terminal mission lacks measured actual outcome"}
    if failures and state not in {"COMPLETE", "FAILED", "BLOCKED"}:
        return {"status": "DERIVED", "value": "REVIEW_FAILURE_OR_REPAIR", "basis": "failure/repair events remain without terminal mission state"}
    return _unknown("no durable next_action or safe derived transition is available")


def _trace_for_row(
    database: FoundryDatabase, row: dict[str, Any],
    events: list[tuple[int, Any]], *, now: str | None,
    candidates: dict[str, dict[str, Any]] | None = None,
    missions: dict[str, dict[str, Any]] | None = None,
    workers_by_mission: dict[str, list[dict[str, Any]]] | None = None,
    verifiers_by_mission: dict[str, list[dict[str, Any]]] | None = None,
    evidence_by_mission: dict[str, list[dict[str, Any]]] | None = None,
    owner_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_id = str(row.get("candidate_id") or "")
    mission_id = str(row.get("mission_id") or "")
    candidate = ((candidates or {}).get(candidate_id)
                 if candidates is not None else
                 database.row("candidates", "candidate_id", candidate_id))
    mission = ((missions or {}).get(mission_id)
               if missions is not None else
               database.row("missions", "mission_id", mission_id))
    workers = [
        {
            "run_id": item.get("run_id"), "role": item.get("role"),
            "provider": item.get("provider"), "model": item.get("model"),
            "status": item.get("status"), "last_event_id": item.get("last_event_id"),
        }
        for item in ((workers_by_mission or {}).get(mission_id, [])
                     if workers_by_mission is not None else
                     _projection_rows(database, "agent_runs", mission_id))
    ]
    verifiers = [
        {
            "verdict_id": item.get("verdict_id"), "verdict": item.get("verdict"),
            "status": item.get("status"), "verifier_run_id": item.get("verifier_run_id"),
            "builder_run_id": item.get("builder_run_id"),
            "last_event_id": item.get("last_event_id"),
        }
        for item in ((verifiers_by_mission or {}).get(mission_id, [])
                     if verifiers_by_mission is not None else
                     _projection_rows(database, "verification_verdicts", mission_id))
    ]
    evidence = [
        {
            "bundle_id": item.get("bundle_id"), "status": item.get("status"),
            "kind": item.get("kind"), "last_event_id": item.get("last_event_id"),
        }
        for item in ((evidence_by_mission or {}).get(mission_id, [])
                     if evidence_by_mission is not None else
                     _projection_rows(database, "evidence_bundles", mission_id))
    ]
    failures = _events_for_mission(
        events, candidate_id=candidate_id, mission_id=mission_id,
        prefixes=("verification.failed", "verification.blocked", "agent_run.failed", "mission.failed", "repair.", "mission.repair"),
    )
    external = _events_for_mission(
        events, candidate_id=candidate_id, mission_id=mission_id,
        event_types={"system.action_started", "system.action_completed", "system.action_failed", "effect.frozen", "effect.compensated", "effect.reconciled", "effect.reconcile_mismatch"},
    )
    outcome_status = str(row.get("status") or "UNKNOWN")
    actual = _json_object(row.get("actual_value_json"))
    outcome = {
        "status": "EVIDENCED" if outcome_status == "RECORDED" else "UNKNOWN",
        "value": actual if outcome_status == "RECORDED" else None,
        "source_event_id": row.get("last_event_id"),
        "reason": None if outcome_status == "RECORDED" else "actual measurement is absent from the durable outcome row",
    }
    evidence_refs = [
        str(item.get("last_event_id")) for item in evidence if item.get("last_event_id")
    ] + [str(item.get("last_event_id")) for item in verifiers if item.get("last_event_id")]
    if row.get("last_event_id"):
        evidence_refs.append(str(row["last_event_id"]))
    if candidate and candidate.get("last_event_id"):
        evidence_refs.append(str(candidate["last_event_id"]))
    if mission and mission.get("last_event_id"):
        evidence_refs.append(str(mission["last_event_id"]))
    decision = _latest_decision(events, candidate_id=candidate_id, mission_id=mission_id)
    decision_event = decision.get("event") if isinstance(decision, dict) else None
    if isinstance(decision_event, dict) and decision_event.get("event_id"):
        evidence_refs.append(str(decision_event["event_id"]))
    evidence_refs.extend(str(item["event_id"]) for item in failures if item.get("event_id"))
    evidence_refs.extend(str(item["event_id"]) for item in external if item.get("event_id"))
    owner_decisions = (
        _owner_decisions_from_snapshot(
            owner_snapshot, candidate_id=candidate_id, mission_id=mission_id,
        )
        if owner_snapshot is not None else _owner_decisions(
            database, candidate_id=candidate_id, mission_id=mission_id, now=now,
        )
    )
    evidence_refs.extend(str(item["event_id"]) for item in owner_decisions if item.get("event_id"))
    return {
        "trace_id": f"result:{row.get('outcome_id') or candidate_id + ':' + mission_id}",
        "schema": TRACEABILITY_SCHEMA,
        "candidate": candidate or _unknown(f"candidate {candidate_id!r} is not projected"),
        "mission": mission or _unknown(f"mission {mission_id!r} is not projected"),
        "decision": decision,
        "evidence": evidence or _unknown(f"no evidence bundle is projected for mission {mission_id!r}"),
        "workers": workers or _unknown(f"no worker run is projected for mission {mission_id!r}"),
        "verifiers": verifiers or _unknown(f"no verifier verdict is projected for mission {mission_id!r}"),
        "failures_repairs": failures or [],
        "actual_outcome": outcome,
        "external_changes": external or _unknown("no external effect event names this result"),
        "owner_decisions": owner_decisions,
        "next_action": _next_action(mission, {"status": outcome_status}, failures),
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
        "uncertainty": [
            label
            for label, missing in (
                ("candidate", not candidate),
                ("mission", not mission),
                ("actual_outcome", outcome_status != "RECORDED"),
                ("external_changes", not external),
            )
            if missing
        ],
    }


def build_result_traceability(database: FoundryDatabase, *, now: str | None = None,
                              limit: int = 100) -> dict[str, Any]:
    """Build bounded, restart-safe result lineage from local durable truth."""
    limit = max(1, min(int(limit), 500))
    try:
        with database.connect(readonly=True) as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM candidate_outcomes ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()]
    except Exception:  # noqa: BLE001 - older schemas remain honestly empty
        rows = []
    events = _event_sequence(database)
    candidate_ids = {str(row.get("candidate_id") or "") for row in rows if row.get("candidate_id")}
    mission_ids = {str(row.get("mission_id") or "") for row in rows if row.get("mission_id")}
    candidates = _bulk_rows(database, "candidates", "candidate_id", candidate_ids)
    missions = _bulk_rows(database, "missions", "mission_id", mission_ids)
    workers_by_mission = _bulk_projection_rows(database, "agent_runs", mission_ids)
    verifiers_by_mission = _bulk_projection_rows(database, "verification_verdicts", mission_ids)
    evidence_by_mission = _bulk_projection_rows(database, "evidence_bundles", mission_ids)
    owner_snapshot = owner_directive_snapshot(database, now=now)
    results = [
        _trace_for_row(
            database, row, events, now=now,
            candidates=candidates, missions=missions,
            workers_by_mission=workers_by_mission,
            verifiers_by_mission=verifiers_by_mission,
            evidence_by_mission=evidence_by_mission,
            owner_snapshot=owner_snapshot,
        )
        for row in rows
    ]
    return {
        "schema": TRACEABILITY_SCHEMA,
        "status": "FRESH" if results else "UNKNOWN",
        "results": results,
        "count": len(results),
    }
