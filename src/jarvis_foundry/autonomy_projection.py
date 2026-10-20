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
from .research_dossier import DOSSIER_CONTRACT


AUTONOMY_PROJECTION_SCHEMA = "v11.command-center-autonomy.v1"
AUTONOMY_FILTERS = {"all", "eligible", "rejected", "researched", "stale"}
MAX_CANDIDATES = 50
MAX_CANDIDATE_SCAN = 200
MAX_TIMELINE = 100
MAX_SOURCE_SCAN = 256
MAX_EVENT_SCAN = 500
MAX_ESCALATION_SCAN = 500
MAX_OWNER_ACTIONS = 20
MAX_OWNER_ACTION_OPTIONS = 12
MAX_OWNER_ACTION_TEXT = 1_200
MAX_OWNER_ACTION_OPTION_TEXT = 240
MAX_OWNER_ACTION_ID = 240
MAX_DETAIL_ITEMS = 24

# These are additive read contracts.  They deliberately remain optional: A3
# and the observation/research branches can publish the payloads when they are
# present, while an older v39 ledger returns an explicit unavailable state.
SOURCE_DIGEST_CONTRACT = "jarvis.source-digest.v1"
RESEARCH_DOSSIER_CONTRACT = DOSSIER_CONTRACT
CAPABILITY_REQUIREMENTS_CONTRACT = "jarvis.v11.capability-requirements.v1"
PROJECT_PROFILE_CONTRACT = "jarvis.project-profile.v1"
PROJECT_CONTEXT_CONTRACT = "jarvis.actionable-project-context.v1"
VERIFICATION_PLAN_CONTRACT = "jarvis.verification-plan.v1"

_TIMELINE_TYPES = (
    "initiative.cycle_started",
    "initiative.cycle_stage_changed",
    "initiative.source_recorded",
    "world.observation_recorded",
    "discovery.candidate_generated",
    "discovery.candidate_source_linked",
    "discovery.candidate_scored",
    "research.candidate_enqueued",
    "research.candidate_started",
    "research.candidate_completed",
    "discovery.candidate_judged",
    "executive.objective_created",
    "executive.objective_recorded",
    "executive.candidate_admitted",
    "executive.escalation_opened",
    "executive.escalation_resolved",
    "mission.created",
    "mission.step_contract_recorded",
    "mission.step_started",
    "mission.step_completed",
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


def _mapping(value: Any) -> dict[str, Any]:
    parsed = _json(value, {})
    return dict(parsed) if isinstance(parsed, dict) else {}


def _bounded_list(value: Any, limit: int = MAX_DETAIL_ITEMS) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(value[:max(0, int(limit))])


def _owner_action_text(value: Any, limit: int, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value)[:limit]


def _find_mapping(value: Any, keys: tuple[str, ...], *, depth: int = 0) -> dict[str, Any] | None:
    """Find a named mapping in durable evidence without trusting arbitrary depth."""
    if depth > 4:
        return None
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, dict):
                return dict(candidate)
        for child in value.values():
            found = _find_mapping(child, keys, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value[:MAX_DETAIL_ITEMS]:
            found = _find_mapping(child, keys, depth=depth + 1)
            if found is not None:
                return found
    return None


def _find_value(value: Any, keys: tuple[str, ...], *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        for key in keys:
            if key in value and value[key] not in (None, ""):
                return value[key]
        for child in value.values():
            found = _find_value(child, keys, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value[:MAX_DETAIL_ITEMS]:
            found = _find_value(child, keys, depth=depth + 1)
            if found is not None:
                return found
    return None


def _compact_evidence(value: Any) -> list[dict[str, Any]]:
    """Keep dossier evidence inspectable while bounding response size."""
    result: list[dict[str, Any]] = []
    for raw in _bounded_list(value):
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {}
        for key in (
            "evidence_id", "claim_key", "source_kind", "source_ref",
            "source_title", "statement", "stance", "confidence",
            "content_hash", "retrieved_at", "fresh_until", "outcome",
            "classification", "independent_source",
        ):
            if raw.get(key) is not None:
                item[key] = raw[key]
        if item:
            result.append(item)
    return result


def _compact_mapping(value: Any, *, keys: tuple[str, ...] = ()) -> dict[str, Any]:
    """Project a small JSON object, retaining only bounded scalar/list fields."""
    if not isinstance(value, dict):
        return {}
    if not keys:
        keys = tuple(value.keys())
    result: dict[str, Any] = {}
    for key in keys:
        raw = value.get(key)
        if raw is None:
            continue
        if isinstance(raw, (str, int, float, bool)):
            result[key] = raw
        elif isinstance(raw, list):
            result[key] = _bounded_list(raw)
        elif isinstance(raw, dict):
            result[key] = _compact_mapping(raw)
    return result


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
    owner_action_unavailable = {
        "value": None,
        "status": "UNAVAILABLE",
        "items": [],
        "count": 0,
        "open_count": None,
        "scan_truncated": False,
        "items_truncated": False,
        "scan_limit": MAX_ESCALATION_SCAN,
    }
    return {
        "schema": AUTONOMY_PROJECTION_SCHEMA,
        "status": "UNAVAILABLE",
        "reason": reason,
        "generated_at": generated_at,
        "executive": {
            "state": "UNAVAILABLE", "cycle_id": None, "stage": None,
            "last_observation_cycle": None, "next_wake": None,
            "wait_reason": None, "wait_boundary": None,
            "wait_reason_code": None,
            "owner_action": owner_action_unavailable,
            "active_wip": unavailable,
        },
        "funnel": {
            key: unavailable for key in (
                "observed", "generated", "deduped", "researched",
                "rejected", "eligible", "executing", "verified_outcome",
            )
        },
        "candidates": [],
        "sources": [],
        "source_digest": _unavailable_contract(
            SOURCE_DIGEST_CONTRACT,
            "No schema v39 source run or digest record is present",
            authority="DATA_ONLY",
        ),
        "timeline": [],
        "current_work": None,
        "learning": {"status": "UNAVAILABLE", "authority": "DATA_ONLY", "latest": None, "history": [],
                      "reason": "No durable calibration or outcome learning record is present"},
        "wait": {"status": "UNAVAILABLE", "state": "UNAVAILABLE", "cycle_id": None,
                 "stage": None, "reason": None, "boundary": None,
                 "reason_code": None, "next_wake": None,
                 "continuation": "No durable wake is recorded.",
                 "authority": "READ_ONLY_PROJECTION"},
        "overview": {"status": "UNAVAILABLE", "authority": "READ_ONLY_PROJECTION"},
        "contract_versions": {
            "source_digest": SOURCE_DIGEST_CONTRACT,
            "research_dossier": RESEARCH_DOSSIER_CONTRACT,
            "capability_requirements": CAPABILITY_REQUIREMENTS_CONTRACT,
            "project_profile": PROJECT_PROFILE_CONTRACT,
            "project_context": PROJECT_CONTEXT_CONTRACT,
            "verification_plan": VERIFICATION_PLAN_CONTRACT,
        },
        "limits": {"candidate_limit": MAX_CANDIDATES,
                   "timeline_limit": MAX_TIMELINE,
                   "event_scan_limit": MAX_EVENT_SCAN},
    }


def _source_event_details(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    details: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        if record.get("event_type") != "initiative.source_recorded":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        key = (str(payload.get("source_kind") or "UNKNOWN"),
               str(payload.get("source_identity") or "UNKNOWN"))
        details.setdefault(key, {
            **payload,
            "occurred_at": record.get("occurred_at"),
            "event_id": record.get("event_id"),
        })
    return details


def _latest_sources(
    rows: list[dict[str, Any]],
    event_details: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    meaningful: dict[tuple[str, str], str] = {}
    changes: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("source_class") or "UNKNOWN"),
               str(row.get("source_identity") or "UNKNOWN"))
        if key not in latest:
            latest[key] = row
        detail = (event_details or {}).get(key) or {}
        provenance = _mapping(row.get("provenance_json"))
        explicitly_meaningful = bool(
            detail.get("meaningful_change")
            or provenance.get("meaningful_change") is True
        )
        if key not in meaningful and explicitly_meaningful:
            meaningful[key] = str(detail.get("occurred_at") or row.get("updated_at") or "")
            raw_changes = detail.get("meaningful_observations") or provenance.get("meaningful_observations")
            changes.setdefault(key, []).extend(
                item for item in _bounded_list(raw_changes)
                if isinstance(item, dict)
            )
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
            "meaningful_changes": changes.get(key, [])[:MAX_DETAIL_ITEMS],
            "noise_suppressed": int(detail.get("noise_suppressed") or provenance.get("noise_suppressed") or 0),
            "candidate_worthy_signals": _bounded_list(
                detail.get("candidate_worthy_signals") or provenance.get("candidate_worthy_signals")
            ),
            "event_id": row.get("last_event_id"),
        })
    return sorted(result, key=lambda item: (
        {"BLOCKED": 0, "DEGRADED": 1, "READY": 2}.get(item["state"], 3),
        item["source_kind"], item["source_identity"],
    ))


def _event_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        payload = _json(row.get("payload_json"), {})
        if not isinstance(payload, dict):
            continue
        records.append({**row, "payload": payload})
    return records


def _owner_action_projection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Project the latest bounded state of Executive escalations."""
    truncated = len(rows) > MAX_ESCALATION_SCAN
    latest: list[dict[str, Any]] = []
    open_count = 0
    seen: set[str] = set()
    for row in rows[:MAX_ESCALATION_SCAN]:
        payload = _json(row.get("payload_json"), {})
        if not isinstance(payload, dict):
            continue
        escalation_id = str(payload.get("escalation_id") or "")
        if not escalation_id or escalation_id in seen:
            continue
        seen.add(escalation_id)
        state = str(
            payload.get("status")
            or ("RESOLVED" if row.get("event_type") == "executive.escalation_resolved" else "OPEN")
        ).upper()
        if state != "OPEN":
            continue
        open_count += 1
        if len(latest) >= MAX_OWNER_ACTIONS:
            continue
        options = [
            _owner_action_text(option, MAX_OWNER_ACTION_OPTION_TEXT)
            for option in _bounded_list(
                payload.get("options"), MAX_OWNER_ACTION_OPTIONS
            )
            if isinstance(option, str) and option
        ]
        created_at = payload.get("created_at")
        latest.append({
            "escalation_id": _owner_action_text(
                escalation_id, MAX_OWNER_ACTION_ID
            ),
            "objective_id": (
                _owner_action_text(
                    payload.get("objective_id"), MAX_OWNER_ACTION_ID
                ) or None
            ),
            "decision": _owner_action_text(
                payload.get("decision"), MAX_OWNER_ACTION_TEXT,
                "Owner decision needed",
            ),
            "why": _owner_action_text(payload.get("why"), MAX_OWNER_ACTION_TEXT),
            "recommendation": _owner_action_text(
                payload.get("recommendation"), MAX_OWNER_ACTION_TEXT
            ),
            "options": options,
            "delay_consequence": (
                _owner_action_text(
                    payload.get("delay_consequence"), MAX_OWNER_ACTION_TEXT
                ) or None
            ),
            "created_at": (
                _owner_action_text(created_at, 64) if created_at else None
            ),
            "event_id": _owner_action_text(
                row.get("event_id"), MAX_OWNER_ACTION_ID
            ) or None,
        })
    items_truncated = open_count > MAX_OWNER_ACTIONS
    status = "PARTIAL" if truncated or items_truncated else "AVAILABLE"
    return {
        "value": latest[0] if latest else None,
        "status": status,
        "items": latest,
        "count": len(latest),
        "open_count": open_count,
        "scan_truncated": truncated,
        "items_truncated": items_truncated,
        "scan_limit": MAX_ESCALATION_SCAN,
    }


def _matching_payload(
    records: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    candidate_id: str | None = None,
    mission_id: str | None = None,
) -> dict[str, Any] | None:
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_candidate = str(_find_value(payload, ("candidate_id",)) or "")
        if candidate_id and payload_candidate and payload_candidate != candidate_id:
            continue
        if candidate_id and not payload_candidate and not mission_id:
            continue
        if mission_id and str(_find_value(payload, ("mission_id",)) or "") not in {"", mission_id}:
            continue
        found = _find_mapping(payload, keys)
        if found is not None:
            return {**found, "event_id": record.get("event_id"),
                    "occurred_at": record.get("occurred_at")}
    return None


def _unavailable_contract(contract: str, reason: str, *, authority: str = "READ_ONLY_PROJECTION") -> dict[str, Any]:
    return {
        "status": "UNAVAILABLE",
        "contract": contract,
        "authority": authority,
        "reason": reason,
    }


def _source_digest_projection(
    source_rows: list[dict[str, Any]], records: list[dict[str, Any]],
    cycle: dict[str, Any] | None,
) -> dict[str, Any]:
    digest_payload: dict[str, Any] = {}
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        candidate = payload.get("source_digest")
        if isinstance(candidate, dict):
            digest_payload = dict(candidate)
            break
    event_details = _source_event_details(records)
    latest = _latest_sources(source_rows, event_details)
    changes = _bounded_list(digest_payload.get("meaningful_changes"))
    if not changes:
        changes = [item for source in latest
                   for item in source.get("meaningful_changes", [])][:MAX_DETAIL_ITEMS]
    if not changes:
        changes = [
            item
            for detail in event_details.values()
            if detail.get("meaningful_change")
            for item in _bounded_list(detail.get("meaningful_observations"))
            if isinstance(item, dict)
        ][:MAX_DETAIL_ITEMS]
    sources_checked = _bounded_list(digest_payload.get("sources_checked"))
    if not sources_checked:
        sources_checked = [{
            "source_kind": source["source_kind"],
            "source_identity": source["source_identity"],
            "status": source["state"],
            "observations_recorded": source["observations"],
            "candidate_worthy": source["candidates"],
            "noise_suppressed": source.get("noise_suppressed"),
            "reason": source.get("last_error"),
        } for source in latest]
    blocked = _bounded_list(digest_payload.get("blocked_sources"))
    if not blocked:
        blocked = [{
            "source_identity": source["source_identity"],
            "status": source["state"],
            "reason": source["last_error"],
        } for source in latest if source["state"] in {"BLOCKED", "DEGRADED"}]
    unchanged = _bounded_list(digest_payload.get("unchanged_sources"))
    if not unchanged:
        unchanged = [source["source_identity"] for source in latest
                     if source["observations"] == 0 and source["candidates"] == 0]
    candidate_signals = _bounded_list(digest_payload.get("candidate_worthy_signals"))
    if not candidate_signals:
        candidate_signals = [
            item
            for detail in event_details.values()
            if detail.get("meaningful_change")
            for item in _bounded_list(detail.get("candidate_worthy_signals"))
            if isinstance(item, dict)
        ][:MAX_DETAIL_ITEMS]
    if not candidate_signals:
        candidate_signals = [item for item in changes
                             if isinstance(item, dict) and item.get("candidate_worthy")][:MAX_DETAIL_ITEMS]
    noise_suppressed = digest_payload.get("noise_suppressed")
    if not isinstance(noise_suppressed, int):
        noise_values = [int(detail.get("noise_suppressed") or 0) for detail in event_details.values()]
        noise_suppressed = sum(noise_values) if noise_values else None
    has_truth = bool(digest_payload or source_rows)
    return {
        "status": "AVAILABLE" if has_truth else "UNAVAILABLE",
        "contract": str(digest_payload.get("schema") or SOURCE_DIGEST_CONTRACT),
        "cycle_id": digest_payload.get("cycle_id") or (cycle or {}).get("cycle_id"),
        "sources_checked": sources_checked,
        "meaningful_changes": changes,
        "unchanged_sources": unchanged,
        "blocked_sources": blocked,
        "candidate_worthy_signals": candidate_signals,
        "noise_suppressed": noise_suppressed,
        "evaluated_at": (cycle or {}).get("completed_at") or (cycle or {}).get("updated_at"),
        "authority": "DATA_ONLY",
    }


def _research_projection(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return _unavailable_contract(
            RESEARCH_DOSSIER_CONTRACT,
            "No durable candidate research record is present",
            authority="DATA_ONLY",
        )
    evidence = _mapping(row.get("evidence_json"))
    result = _mapping(row.get("result_json"))
    dossier = result.get("dossier") if isinstance(result.get("dossier"), dict) else evidence.get("dossier")
    status = str(row.get("status") or "UNKNOWN")
    if not isinstance(dossier, dict):
        return {
            "status": status,
            "contract": RESEARCH_DOSSIER_CONTRACT,
            "available": False,
            "research_id": row.get("item_id"),
            "authority": "DATA_ONLY",
            "reason": "Research status is durable, but no dossier payload was recorded",
            "evidence": _compact_mapping(evidence),
        }
    confidence = dossier.get("confidence")
    freshness = dossier.get("freshness")
    conclusion = dossier.get("final_research_conclusion")
    return {
        "status": status,
        "contract": str(dossier.get("contract") or RESEARCH_DOSSIER_CONTRACT),
        "available": True,
        "research_id": row.get("item_id"),
        "authority": dossier.get("authority") or "DATA_ONLY",
        "candidate_identity": _compact_mapping(dossier.get("candidate_identity")),
        "research_question": dossier.get("research_question"),
        "source_provenance": _compact_evidence(dossier.get("source_provenance")),
        "supporting_evidence": _compact_evidence(dossier.get("supporting_evidence")),
        "contradicting_evidence": _compact_evidence(dossier.get("contradicting_evidence")),
        "contextual_evidence": _compact_evidence(dossier.get("contextual_evidence")),
        "contradictions": _bounded_list(dossier.get("contradictions")),
        "source_quality": _compact_mapping(dossier.get("source_quality")),
        "freshness": _compact_mapping(freshness),
        "confidence": _compact_mapping(confidence),
        "unresolved_unknowns": _bounded_list(dossier.get("unresolved_unknowns")),
        "expected_benefit_evidence": _compact_evidence(dossier.get("expected_benefit_evidence")),
        "effort_dependency_evidence": _compact_evidence(dossier.get("effort_dependency_evidence")),
        "relevant_project_context": _compact_evidence(dossier.get("relevant_project_context")),
        "final_research_conclusion": _compact_mapping(conclusion),
        "human_summary": dossier.get("human_summary"),
        "evidence_revision_identity": dossier.get("evidence_revision_identity"),
        "dossier_revision": dossier.get("dossier_revision"),
        "budget": _compact_mapping(dossier.get("budget")),
        "generated_at": dossier.get("generated_at"),
    }


def _capability_projection(
    evidence: Any, records: list[dict[str, Any]] | None = None,
    *, candidate_id: str | None = None,
) -> dict[str, Any]:
    raw = _find_mapping(evidence, (
        "capability_requirements", "capability_requirement_set",
        "requirement_set", "capability_requirement_context",
    ))
    required = _find_value(evidence, (
        "required_capability_ids", "required_capabilities", "required_actions",
    ))
    optional = _find_value(evidence, ("optional_capability_ids", "optional_capabilities", "optional_actions"))
    if raw is None and records:
        raw = _matching_payload(
            records,
            ("capability_requirements", "capability_requirement_set", "requirements"),
            candidate_id=candidate_id,
        )
        if raw is None:
            for record in records:
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                payload_candidate = str(payload.get("candidate_id") or "")
                if candidate_id and payload_candidate and payload_candidate != candidate_id:
                    continue
                if candidate_id and not payload_candidate:
                    continue
                if payload.get("contract_version") == CAPABILITY_REQUIREMENTS_CONTRACT \
                        or payload.get("requirement_set_id"):
                    raw = {**payload, "event_id": record.get("event_id")}
                    break
    if raw is None and required is None and optional is None:
        return _unavailable_contract(
            CAPABILITY_REQUIREMENTS_CONTRACT,
            "No durable capability requirement or Fabric readiness record is present",
            authority="FABRIC_CATALOG_READ_ONLY",
        )
    raw = dict(raw or {})
    readiness = raw.get("readiness")
    readiness_state = (
        readiness.get("state") if isinstance(readiness, dict) else readiness
    )
    if not isinstance(readiness_state, str) or not readiness_state:
        readiness_state = "DECLARED"
    return {
        "status": readiness_state,
        "contract": str(raw.get("contract_version") or raw.get("contract") or CAPABILITY_REQUIREMENTS_CONTRACT),
        "authority": raw.get("authority") or "FABRIC_CATALOG_READ_ONLY",
        "requirement_set_id": raw.get("requirement_set_id"),
        "snapshot_id": raw.get("snapshot_id"),
        "required_capability_ids": _bounded_list(raw.get("required_capability_ids") or required),
        "optional_capability_ids": _bounded_list(raw.get("optional_capability_ids") or optional),
        "missing_capabilities": _bounded_list(raw.get("missing_capabilities")),
        "blocked_capabilities": _bounded_list(raw.get("blocked_capabilities")),
        "optional_unavailable": _bounded_list(raw.get("optional_unavailable")),
        "unresolved_actions": _bounded_list(raw.get("unresolved_actions")),
        "dependencies": _bounded_list(raw.get("dependencies")),
        "capability_evidence": _bounded_list(raw.get("capability_evidence")),
        "blockers": _bounded_list(raw.get("blockers")),
        "action_classes": _bounded_list(raw.get("action_classes")),
        "executable": raw.get("executable"),
        "decision": raw.get("decision"),
        "recommended_disposition": raw.get("recommended_disposition"),
        "evaluated_at": raw.get("evaluated_at"),
        "evidence": _bounded_list(raw.get("evidence")),
        "provenance": _compact_mapping(raw.get("provenance")),
        "requirement_plan": _compact_mapping(raw.get("requirement_plan")),
    }


def _project_context_projection(
    evidence: Any, records: list[dict[str, Any]] | None = None,
    *, candidate_id: str | None = None,
) -> dict[str, Any]:
    context = _find_mapping(evidence, ("project_context", "actionable_project_context"))
    profile = _find_mapping(evidence, ("project_profile",))
    if records:
        context = context or _matching_payload(
            records, ("project_context", "actionable_project_context"),
            candidate_id=candidate_id,
        )
        profile = profile or _matching_payload(
            records, ("project_profile",), candidate_id=candidate_id,
        )
        if profile is None:
            for record in records:
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                payload_candidate = str(payload.get("candidate_id") or "")
                if candidate_id and payload_candidate and payload_candidate != candidate_id:
                    continue
                if candidate_id and not payload_candidate:
                    continue
                if payload.get("schema") == PROJECT_PROFILE_CONTRACT and payload.get("profile_id"):
                    profile = {**payload, "event_id": record.get("event_id")}
                    break
    if context is None and profile is None:
        return _unavailable_contract(
            PROJECT_CONTEXT_CONTRACT,
            "No durable project profile or actionable context is present",
            authority="READ_ONLY_PROJECTION",
        )
    context = dict(context or {})
    profile = dict(profile or {})
    context_schema = str(context.get("schema") or PROJECT_CONTEXT_CONTRACT)
    profile_schema = str(profile.get("schema") or PROJECT_PROFILE_CONTRACT)
    return {
        "status": "AVAILABLE" if context.get("schema") or profile.get("profile_id") else "EMBEDDED",
        "contract": context_schema,
        "profile_contract": profile_schema,
        "authority": context.get("authority") or profile.get("authority") or "READ_ONLY_PROJECTION",
        "profile": _compact_mapping(profile),
        "context": _compact_mapping(context),
        "profile_ref": _compact_mapping(context.get("profile_ref")),
        "context_gaps": _bounded_list(context.get("context_gaps") or profile.get("unknowns")),
        "repository_state": _compact_mapping(profile.get("repository_state")),
        "stack": _compact_mapping(profile.get("stack")),
        "commands": _compact_mapping(profile.get("commands")),
        "known_constraints": _bounded_list(profile.get("known_constraints")),
        "recent_relevant_changes": _bounded_list(profile.get("recent_relevant_changes")),
    }


def _verification_plan_projection(
    candidate_id: str,
    candidate: dict[str, Any],
    mission: dict[str, Any] | None,
    step: dict[str, Any] | None,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = _matching_payload(
        records, ("verification_plan", "verification-plan", "plan"),
        candidate_id=candidate_id,
        mission_id=str((mission or {}).get("mission_id") or "") or None,
    )
    if payload is None:
        mission_id = str((mission or {}).get("mission_id") or "")
        for record in records:
            raw = record.get("payload")
            if not isinstance(raw, dict) or (mission_id and str(raw.get("mission_id") or "") not in {"", mission_id}):
                continue
            payload_candidate = str(_find_value(raw, ("candidate_id",)) or "")
            if candidate_id and payload_candidate and payload_candidate != candidate_id:
                continue
            if candidate_id and not payload_candidate and not mission_id:
                continue
            if raw.get("schema") == VERIFICATION_PLAN_CONTRACT or raw.get("plan_id"):
                payload = {**raw, "event_id": record.get("event_id"), "occurred_at": record.get("occurred_at")}
                break
    if payload is not None:
        payload["status"] = payload.get("status") or "AVAILABLE"
        payload["contract"] = payload.get("schema") or payload.get("contract") or VERIFICATION_PLAN_CONTRACT
        payload["authority"] = payload.get("authority") or "READ_ONLY_PROJECTION"
        return _compact_mapping(payload)
    if not step:
        return _unavailable_contract(
            VERIFICATION_PLAN_CONTRACT,
            "No durable verification plan or mission step contract is present",
        )
    criteria = _json(step.get("verification_criteria_json"), [])
    artifacts = _json(step.get("expected_artifacts_json"), [])
    forbidden = _json(step.get("forbidden_paths_json"), [])
    expected_effects = _json(step.get("expected_effects_json"), [])
    rollback = _json(step.get("rollback_json"), {})
    return {
        "status": "PARTIAL",
        "contract": VERIFICATION_PLAN_CONTRACT,
        "authority": "READ_ONLY_PROJECTION",
        "candidate_ref": candidate_id,
        "objective_ref": None,
        "objective": mission.get("objective") if mission else candidate.get("title"),
        "target_project": candidate.get("project"),
        "project_context_status": "UNAVAILABLE",
        "expected_outcome": candidate.get("expected_outcome"),
        "required_postconditions": _bounded_list(expected_effects),
        "tests_to_run": [],
        "checks_to_run": _bounded_list(criteria),
        "expected_artifacts": _bounded_list(artifacts),
        "forbidden_side_effects": [],
        "forbidden_paths": _bounded_list(forbidden),
        "independent_readback": {"status": "UNAVAILABLE", "reason": "full V11 plan payload not recorded"},
        "rollback_cleanup_expectations": rollback if isinstance(rollback, dict) else {"items": _bounded_list(rollback)},
        "evidence_requirements": _bounded_list(criteria),
        "acceptance_criteria": _bounded_list(criteria),
        "verifier_capabilities": [],
        "source_contract_refs": [step.get("contract_id")] if step.get("contract_id") else [],
        "context_gaps": ["full V11 verification plan payload not recorded"],
        "contract_hash": step.get("contract_hash"),
        "step_contract_id": step.get("contract_id"),
    }


def _objective_projection(
    candidate_id: str, mission: dict[str, Any] | None,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = _matching_payload(
        records, ("objective", "objective_record", "objective_projection"),
        candidate_id=candidate_id,
        mission_id=str((mission or {}).get("mission_id") or "") or None,
    )
    if payload is None:
        mission_id = str((mission or {}).get("mission_id") or "")
        for record in records:
            raw = record.get("payload")
            if not isinstance(raw, dict) or (mission_id and str(raw.get("mission_id") or "") not in {"", mission_id}):
                continue
            payload_candidate = str(_find_value(raw, ("candidate_id",)) or "")
            if candidate_id and payload_candidate and payload_candidate != candidate_id:
                continue
            if candidate_id and not payload_candidate and not mission_id:
                continue
            if record.get("event_type") in {"executive.objective_created", "executive.objective_recorded"} \
                    and (raw.get("objective_id") or raw.get("objective")):
                payload = {**raw, "event_id": record.get("event_id"), "occurred_at": record.get("occurred_at")}
                break
    if payload is not None:
        payload["status"] = payload.get("status") or "AVAILABLE"
        payload["authority"] = payload.get("authority") or "READ_ONLY_PROJECTION"
        return _compact_mapping(payload)
    return {
        "status": "UNAVAILABLE",
        "objective_id": None,
        "reason": "A3 objective record is not present in the durable projection",
    }


def _worktree_projection(
    mission: dict[str, Any] | None,
    resource: dict[str, Any] | None,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = _matching_payload(
        records, ("worktree", "worktree_binding", "worktree_context"),
        mission_id=str((mission or {}).get("mission_id") or "") or None,
    )
    if payload is not None:
        payload["status"] = payload.get("status") or "AVAILABLE"
        payload["authority"] = payload.get("authority") or "READ_ONLY_PROJECTION"
        return _compact_mapping(payload)
    if resource:
        baseline = _mapping(resource.get("baseline_json"))
        return {
            "status": resource.get("readiness_state") or "AVAILABLE",
            "resource_id": resource.get("resource_id"),
            "kind": resource.get("kind"),
            "path": resource.get("resolved_root") or resource.get("root_path"),
            "root_path": resource.get("root_path"),
            "baseline": baseline,
            "capabilities": _bounded_list(_json(resource.get("capabilities_json"), [])),
            "event_id": resource.get("last_event_id"),
            "updated_at": resource.get("updated_at"),
            "authority": "READ_ONLY_PROJECTION",
        }
    if mission:
        return {
            "status": "PENDING", "path": None,
            "resource_id": mission.get("resource_id"),
            "reason": "A3 worktree binding is not present in durable projections",
        }
    return {"status": "UNAVAILABLE", "path": None}


def _step_projection(step: dict[str, Any] | None) -> dict[str, Any]:
    if not step:
        return {"status": "UNAVAILABLE", "step_id": None}
    return {
        "status": "AVAILABLE",
        "contract_id": step.get("contract_id"),
        "step_id": step.get("step_id"),
        "version": step.get("version"),
        "resource_id": step.get("resource_id"),
        "capability": step.get("capability"),
        "target_relative_path": step.get("target_relative_path"),
        "risk_class": step.get("risk_class"),
        "instructions": step.get("instructions"),
        "expected_effects": _json(step.get("expected_effects_json"), []),
        "rollback": _json(step.get("rollback_json"), {}),
        "verification_criteria": _json(step.get("verification_criteria_json"), []),
        "expected_artifacts": _json(step.get("expected_artifacts_json"), []),
        "forbidden_paths": _json(step.get("forbidden_paths_json"), []),
        "contract_hash": step.get("contract_hash"),
        "event_id": step.get("last_event_id"),
    }


def _worker_projection(run: dict[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {"status": "UNAVAILABLE", "run_id": None}
    return {
        "status": run.get("status"),
        "run_id": run.get("run_id"),
        "step_id": run.get("step_id"),
        "role": run.get("role"),
        "provider": run.get("provider"),
        "model": run.get("model"),
        "started_at": run.get("started_at"),
        "heartbeat_at": run.get("heartbeat_at"),
        "finished_at": run.get("finished_at"),
        "failure_class": run.get("failure_class"),
        "result": _compact_mapping(_json(run.get("result_json"), {})),
        "event_id": run.get("last_event_id"),
    }


def _verifier_projection(verdict: dict[str, Any] | None) -> dict[str, Any]:
    if not verdict:
        return {"status": "UNAVAILABLE", "verdict_id": None}
    return {
        "status": verdict.get("status"),
        "verdict": verdict.get("verdict"),
        "verdict_id": verdict.get("verdict_id"),
        "mission_id": verdict.get("mission_id"),
        "subject_type": verdict.get("subject_type"),
        "subject_id": verdict.get("subject_id"),
        "verifier_run_id": verdict.get("verifier_run_id"),
        "builder_run_id": verdict.get("builder_run_id"),
        "independence": _compact_mapping(_json(verdict.get("independence_json"), {})),
        "criteria": _bounded_list(_json(verdict.get("criteria_json"), [])),
        "findings": _bounded_list(_json(verdict.get("findings_json"), [])),
        "evidence": _compact_mapping(_json(verdict.get("evidence_json"), {})),
        "missing_proof": _bounded_list(_json(verdict.get("missing_proof_json"), [])),
        "scope_violations": _bounded_list(_json(verdict.get("scope_violations_json"), [])),
        "repair_requirements": _bounded_list(_json(verdict.get("repair_requirements_json"), [])),
        "reason": verdict.get("reason"),
        "blocking_dependency": verdict.get("blocking_dependency"),
        "issued_at": verdict.get("issued_at"),
        "event_id": verdict.get("last_event_id"),
    }


def _mission_projection(
    mission: dict[str, Any] | None,
    *,
    objective: dict[str, Any],
) -> dict[str, Any]:
    if not mission:
        return {"status": "UNAVAILABLE", "mission_id": None}
    return {
        "status": str(mission.get("state") or "UNKNOWN"),
        "mission_id": mission.get("mission_id"),
        "objective": mission.get("objective"),
        "objective_id": objective.get("objective_id"),
        "resource_id": mission.get("resource_id"),
        "acceptance": _json(mission.get("acceptance_json"), {}),
        "next_action": mission.get("next_action"),
        "created_from": mission.get("created_from"),
        "event_id": mission.get("last_event_id"),
        "updated_at": mission.get("updated_at"),
    }


def _learning_projection(
    calibration_events: list[dict[str, Any]],
    outcome_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    for row in calibration_events:
        payload = _json(row.get("payload_json"), {})
        if not isinstance(payload, dict):
            continue
        history.append({
            **_compact_mapping(payload),
            "event_id": row.get("event_id"),
            "occurred_at": row.get("occurred_at"),
        })
    for row in outcome_rows:
        if str(row.get("status") or "").upper() != "RECORDED":
            continue
        history.append({
            "candidate_id": row.get("candidate_id"),
            "outcome_id": row.get("outcome_id"),
            "status": row.get("status"),
            "actual": _json(row.get("actual_value_json"), {}),
            "predicted": _json(row.get("predicted_value_json"), {}),
            "confidence": row.get("confidence"),
            "event_id": row.get("last_event_id"),
            "occurred_at": row.get("updated_at"),
        })
    history.sort(key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
    return {
        "status": "AVAILABLE" if history else "UNAVAILABLE",
        "authority": "DATA_ONLY",
        "latest": history[0] if history else None,
        "history": history[:MAX_DETAIL_ITEMS],
        "reason": None if history else "No durable calibration or outcome learning record is present",
    }


def _wait_projection(executive: dict[str, Any], *, cycle_id: str | None) -> dict[str, Any]:
    state = str(executive.get("state") or "UNAVAILABLE").upper()
    waiting = state in {"WAIT", "IDLE", "STOPPED", "BLOCKED"}
    return {
        "status": "WAITING" if waiting else ("CONTINUING" if state != "UNAVAILABLE" else "UNAVAILABLE"),
        "state": state,
        "cycle_id": cycle_id,
        "stage": executive.get("stage"),
        "reason": executive.get("wait_reason"),
        "boundary": executive.get("wait_boundary"),
        "reason_code": executive.get("wait_reason_code"),
        "next_wake": executive.get("next_wake"),
        "continuation": (
            "JARVIS will resume at the durable next wake."
            if waiting and executive.get("next_wake")
            else "No durable wake is recorded."
            if waiting
            else "Autonomous work is still in progress."
        ),
        "authority": "READ_ONLY_PROJECTION",
    }


def _overview_projection(
    funnel: dict[str, Any], candidates: list[dict[str, Any]],
    current_work: dict[str, Any] | None, learning: dict[str, Any],
    wait: dict[str, Any],
) -> dict[str, Any]:
    eligible = next((item for item in candidates if item.get("state") == "ELIGIBLE"), None)
    outcome = (current_work or {}).get("final_outcome") if current_work else None
    return {
        "status": "AVAILABLE" if any(item.get("status") == "AVAILABLE" for item in funnel.values()) else "UNAVAILABLE",
        "authority": "READ_ONLY_PROJECTION",
        "observed": funnel.get("observed"),
        "opportunities": funnel.get("generated"),
        "deduped": funnel.get("deduped"),
        "researched": funnel.get("researched"),
        "rejected": funnel.get("rejected"),
        "chosen": {
            "status": "AVAILABLE" if eligible else "UNAVAILABLE",
            "candidate_id": eligible.get("candidate_id") if eligible else None,
            "title": eligible.get("title") if eligible else None,
        },
        "active": funnel.get("executing"),
        "verification": {
            "status": outcome.get("state") if isinstance(outcome, dict) else "UNAVAILABLE",
            "acceptance": ((outcome or {}).get("verification") or {}).get("acceptance") if isinstance(outcome, dict) else "NOT_APPLICABLE",
        },
        "learned": learning.get("status"),
        "waiting": wait.get("status"),
    }


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
    if event_type == "initiative.cycle_completed":
        return "WAIT" if str(payload.get("status") or "").upper() in {
            "WAIT", "IDLE", "BLOCKED", "STOPPED",
        } else None
    if event_type.startswith("initiative.") or event_type == "world.observation_recorded":
        return "OBSERVED"
    if event_type in {
        "discovery.candidate_generated", "discovery.candidate_source_linked",
        "discovery.candidate_scored",
    }:
        return "CANDIDATE"
    if event_type.startswith("research.candidate_"):
        return "RESEARCHED"
    if event_type == "discovery.candidate_judged":
        outcome = str(payload.get("outcome") or "").upper()
        return outcome if outcome in {"REJECTED", "ELIGIBLE"} else None
    if event_type.startswith("executive.objective") or event_type == "executive.candidate_admitted":
        return "OBJECTIVE"
    if event_type in {
        "executive.escalation_opened", "executive.escalation_resolved",
    }:
        return "OWNER_ACTION"
    if event_type == "mission.created" or event_type.startswith("mission.step_") \
            or event_type.startswith("agent_run."):
        return "BUILD"
    if event_type.startswith("verification.") and event_type != "verification.accepted":
        return "VERIFY"
    if event_type in {"verification.accepted", "discovery.candidate_outcome_recorded"}:
        return "OUTCOME"
    if event_type.endswith("calibration_recorded"):
        return "LEARN"
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
            "acceptance_receipt": (
                {
                    "event_id": receipt.get("event_id"),
                    "accepted_at": receipt.get("accepted_at"),
                    "decision": receipt.get("decision"),
                    "checks": _compact_mapping(receipt.get("checks")),
                } if receipt is not None else None
            ),
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
                    "c.evidence_json AS candidate_evidence_json, c.candidate_type, "
                    "c.fingerprint AS candidate_fingerprint, c.provenance_hash, c.created_from, "
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
                resource_ids = {
                    str(row.get("resource_id")) for row in mission_by_candidate.values()
                    if row.get("resource_id")
                }
                step_rows = _in_rows(
                    connection, "mission_step_contracts", "mission_id", mission_ids,
                    limit=400,
                )
                resource_rows = _in_rows(
                    connection, "resources", "resource_id", resource_ids,
                    limit=200,
                )
                evidence_rows = _in_rows(
                    connection, "evidence_bundles", "mission_id", mission_ids,
                    limit=200,
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
                    "SELECT event_id, occurred_at, event_type, payload_json FROM events "
                    "WHERE event_type IN ('executive.calibration_recorded', 'world.calibration_recorded') "
                    "ORDER BY sequence DESC LIMIT 200",
                )
                escalation_rows = _rows(
                    connection,
                    "SELECT event_id, event_type, occurred_at, payload_json "
                    "FROM events WHERE event_type IN "
                    "('executive.escalation_opened', 'executive.escalation_resolved') "
                    "ORDER BY sequence DESC LIMIT ?",
                    (MAX_ESCALATION_SCAN + 1,),
                )
                recent_event_rows = _rows(
                    connection,
                    "SELECT sequence, event_id, event_type, occurred_at, payload_json "
                    "FROM events ORDER BY sequence DESC LIMIT ?",
                    (MAX_EVENT_SCAN,),
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
    resource_by_id = _latest_by(resource_rows, "resource_id")
    evidence_by_mission = _latest_by(evidence_rows, "mission_id")
    event_records = _event_records(recent_event_rows)
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
        research_projection = _research_projection(research)
        candidate_evidence = _json(row.get("candidate_evidence_json"), [])
        capability = _capability_projection(candidate_evidence, event_records, candidate_id=candidate_id)
        project_context = _project_context_projection(candidate_evidence, event_records, candidate_id=candidate_id)
        expires_at = str(row.get("expires_at") or source.get("fresh_until") or "") or None
        observed = _iso(str(source.get("observed_at") or ""))
        expires = _iso(expires_at)
        stale = bool(expires and expires <= clock)
        outcome_state = str(row.get("outcome") or "UNKNOWN").upper()
        mission = mission_by_candidate.get(candidate_id)
        mission_id = str((mission or {}).get("mission_id") or "")
        verdict = verdict_by_mission.get(mission_id)
        objective = _objective_projection(candidate_id, mission, event_records)
        step = step_by_mission.get(mission_id)
        resource = resource_by_id.get(str((mission or {}).get("resource_id") or ""))
        worktree = _worktree_projection(mission, resource, event_records)
        verification_plan = _verification_plan_projection(
            candidate_id, row, mission, step, event_records,
        )
        mission_projection = _mission_projection(mission, objective=objective)
        current_step = _step_projection(step)
        worker = _worker_projection(run_by_mission.get(mission_id))
        verifier_projection = _verifier_projection(verdict)
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
                "dossier": research_projection,
            },
            "capability_readiness": capability,
            "project_context": project_context,
            "verification_plan": verification_plan,
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
                "objective": objective,
                "mission": mission_projection,
                "worktree": worktree,
                "current_step": current_step,
                "worker": worker,
                "verifier": verifier_projection,
                "verification_plan": verification_plan,
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
    source_projection = _latest_sources(source_rows, _source_event_details(event_records))
    source_digest = _source_digest_projection(source_rows, event_records, cycle)
    learning = _learning_projection(calibration_events, outcome_rows)
    owner_action = _owner_action_projection(escalation_rows)
    funnel = {
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
    }
    executive = {
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
        "owner_action": owner_action,
        "active_wip": {
            "value": executing if bridge_present else None,
            "status": "AVAILABLE" if bridge_present else "UNAVAILABLE",
        },
    }
    wait = _wait_projection(executive, cycle_id=latest_cycle)
    return {
        "schema": AUTONOMY_PROJECTION_SCHEMA,
        "status": "AVAILABLE" if cycle or candidates or source_rows else "EMPTY",
        "reason": None,
        "generated_at": generated_at,
        "executive": executive,
        "overview": _overview_projection(funnel, all_candidates, current_work, learning, wait),
        "funnel": funnel,
        "candidates": candidates,
        "candidate_filter": selected_filter,
        "sources": source_projection,
        "source_digest": source_digest,
        "timeline": timeline,
        "current_work": current_work,
        "learning": learning,
        "wait": wait,
        "contract_versions": {
            "source_digest": SOURCE_DIGEST_CONTRACT,
            "research_dossier": RESEARCH_DOSSIER_CONTRACT,
            "capability_requirements": CAPABILITY_REQUIREMENTS_CONTRACT,
            "project_profile": PROJECT_PROFILE_CONTRACT,
            "project_context": PROJECT_CONTEXT_CONTRACT,
            "verification_plan": VERIFICATION_PLAN_CONTRACT,
        },
        "limits": {
            "candidate_limit": candidate_limit,
            "candidate_scan_limit": MAX_CANDIDATE_SCAN,
            "timeline_limit": timeline_limit,
            "source_scan_limit": MAX_SOURCE_SCAN,
            "event_scan_limit": MAX_EVENT_SCAN,
            "owner_action_scan_limit": MAX_ESCALATION_SCAN,
            "owner_action_limit": MAX_OWNER_ACTIONS,
        },
    }


__all__ = [
    "AUTONOMY_FILTERS", "AUTONOMY_PROJECTION_SCHEMA",
    "SOURCE_DIGEST_CONTRACT", "RESEARCH_DOSSIER_CONTRACT",
    "CAPABILITY_REQUIREMENTS_CONTRACT", "PROJECT_PROFILE_CONTRACT",
    "PROJECT_CONTEXT_CONTRACT", "VERIFICATION_PLAN_CONTRACT",
    "build_autonomy_projection",
]
