"""V8 operational-intelligence read model.

The projection composes the existing event ledger, capability registry,
connector registry, role router, memory, and evolution authorities.  It is
deliberately read-only: no queue, mission, provider, or attention state is
written here.  Rebuilding it from the same ledger therefore produces the
same bounded answer after restart or projection rebuild.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .capabilities.registry import discover_capabilities
from .connectors.operations import list_operational_statuses
from .database import FoundryDatabase
from .events import Event, canonical_json
from .roles.router import (
    ProviderRouteCandidate,
    RoleRouter,
    RoutingRequest,
)
from .runtime.provider_registry import ProviderService, list_services
from .supervisor.estop import EmergencyStop

OPERATIONAL_INTELLIGENCE_SCHEMA = "v8.operational-intelligence.v1"
OPERATIONAL_INTELLIGENCE_VERSION = "1.0"
DEFAULT_EVENT_SCAN = 4000
DEFAULT_SIGNAL_LIMIT = 40
DEFAULT_FOLLOWUP_LIMIT = 40
DEFAULT_CONTEXT_LIMIT = 24

SALIENCE = (
    "IGNORE", "REMEMBER", "MONITOR", "RECOMMEND", "CREATE_WORK",
    "OWNER_ATTENTION", "URGENT",
)
SIGNAL_STATES = ("OPEN", "RESOLVED", "SUPPRESSED", "UNKNOWN")
FOLLOWUP_STATES = ("OPEN", "RESOLVED", "UNKNOWN")

_FAILURE_WORDS = (
    "fail", "error", "timeout", "outage", "mismatch", "quota",
    "rejected", "rolled_back", "rollback", "crash", "unavailable",
    "invalid", "exception",
)
_BLOCK_WORDS = ("blocked", "vetoed", "stopped", "paused", "hold")
_PENDING_WORDS = ("pending", "waiting", "planned", "incomplete", "queued")
_STALE_WORDS = ("stale", "expired", "overdue")
_DEGRADED_WORDS = ("degraded", "rate_limited", "limited")
_UNKNOWN_WORDS = ("unknown", "not_tested", "unconfigured", "missing")
_RESOLUTION_WORDS = (
    "completed", "complete", "resolved", "recovered", "restored",
    "healthy", "enabled", "accepted", "succeeded", "success", "verified",
    "materialized", "resumed",
)


def _text(value: Any, limit: int = 320) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _payload(event: Event) -> dict[str, Any]:
    return dict(event.payload) if isinstance(event.payload, dict) else {}


def _number(value: Any, *, minimum: float = 0.0, maximum: float = 1.0) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return max(minimum, min(maximum, number))


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    else:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _clock(value: datetime | str | None) -> datetime:
    parsed = _parse_time(value)
    return parsed or datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_summary(event: Event) -> str:
    payload = _payload(event)
    for key in (
        "reason", "detail", "exact_text", "objective", "summary", "error",
        "failure_class", "next_action", "status", "outcome", "question",
    ):
        value = _text(payload.get(key))
        if value:
            return value
    try:
        return _text(json.dumps(payload, sort_keys=True, ensure_ascii=True))
    except (TypeError, ValueError):
        return "event payload is not serializable"


def _refs(event: Event) -> tuple[str, ...]:
    payload = _payload(event)
    refs: list[str] = [event.event_id]
    for key in (
        "evidence_ref", "evidence_id", "attempt_id", "verdict_id",
        "effect_ticket_id", "lineage_id", "connector_request_id",
        "last_event_id", "resolves_event_id", "cause_event_id",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and value not in refs:
            refs.append(value.strip())
    raw = payload.get("evidence_refs")
    if isinstance(raw, (list, tuple)):
        for value in raw:
            if isinstance(value, str) and value.strip() and value not in refs:
                refs.append(value.strip())
    return tuple(refs[:16])


def _entity(event: Event) -> tuple[str, str] | None:
    payload = _payload(event)
    fields = (
        ("provider_id", "provider"), ("provider", "provider"),
        ("connector_id", "connector"), ("capability_id", "capability"),
        ("mission_id", "mission"), ("run_id", "agent_run"),
        ("system_id", "system"), ("resource_id", "resource"),
        ("candidate_id", "candidate"), ("memory_id", "memory"),
        ("effect_ticket_id", "effect"), ("briefing_id", "briefing"),
    )
    for key, kind in fields:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return kind, value.strip()
    return None


def _status_text(event: Event) -> str:
    payload = _payload(event)
    for key in ("failure_class", "status", "outcome", "health", "availability", "state", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _concern_kind(event: Event) -> str | None:
    event_type = str(event.event_type or "").lower()
    status = _status_text(event)
    combined = f"{event_type} {status}"
    if event_type.startswith(("intelligence.", "operational_intelligence.")):
        return None
    if any(word in combined for word in _BLOCK_WORDS):
        return "BLOCKED"
    if any(word in combined for word in _FAILURE_WORDS):
        return "FAILED"
    if any(word in combined for word in _STALE_WORDS):
        return "STALE"
    if any(word in combined for word in _PENDING_WORDS):
        return "PENDING"
    if any(word in combined for word in _DEGRADED_WORDS):
        return "DEGRADED"
    if any(word in combined for word in _UNKNOWN_WORDS):
        return "UNKNOWN"
    # Explicit health samples are operational observations even when their
    # event name is otherwise neutral.
    if event_type.endswith(("health_sample", "health_recorded", "availability_sample")):
        value = status.upper()
        if value in {"DEGRADED", "UNAVAILABLE", "UNKNOWN", "DISABLED", "BLOCKED"}:
            return "DEGRADED" if value == "DEGRADED" else value
    return None


def _is_resolution(event: Event) -> bool:
    event_type = str(event.event_type or "").lower()
    status = _status_text(event)
    return any(word in f"{event_type} {status}" for word in _RESOLUTION_WORDS)


def _is_relevant_resolution(event: Event) -> bool:
    # A generic completed event without an entity should not resolve every
    # concern in the ledger.
    return _is_resolution(event) and _entity(event) is not None


def _fingerprint(event: Event, kind: str) -> str:
    payload = _payload(event)
    entity = _entity(event)
    failure = _text(payload.get("failure_class") or payload.get("error_code") or "")
    action = _text(payload.get("action_kind") or payload.get("action") or "")
    material = {
        "kind": kind,
        "entity": entity,
        "failure_class": failure.lower(),
        "action": action.lower(),
        "event_family": event.event_type.split(".", 1)[0].lower(),
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()[:32]


def _dimension_values(
    event: Event,
    kind: str,
    *,
    occurrence_count: int,
    evidence_count: int,
) -> dict[str, float]:
    payload = _payload(event)
    novelty = 1.0 if occurrence_count == 1 else max(0.2, 1.0 / occurrence_count)
    recurrence = min(1.0, max(0, occurrence_count - 1) / 3.0)
    impact_defaults = {
        "BLOCKED": 0.92, "FAILED": 0.84, "DEGRADED": 0.72,
        "STALE": 0.64, "UNKNOWN": 0.58, "PENDING": 0.5,
    }
    urgency = _number(payload.get("urgency"), minimum=0, maximum=1)
    if urgency is None:
        urgency = 0.9 if kind == "BLOCKED" else 0.78 if kind == "FAILED" else 0.45
    confidence = _number(payload.get("confidence"), minimum=0, maximum=1)
    if confidence is None:
        confidence = 0.72 if event.source_type in {"supervisor", "system", "owner"} else 0.55
    relevance = _number(
        payload.get("relevance") or payload.get("owner_relevance"),
        minimum=0, maximum=1,
    )
    if relevance is None:
        relevance = 0.84 if payload.get("mission_id") else 0.58 if _entity(event) else 0.4
    reversible = payload.get("reversible")
    reversibility = 0.68 if reversible is None else (0.9 if bool(reversible) else 0.25)
    coverage = min(1.0, evidence_count / 3.0) if evidence_count else 0.2
    return {
        "novelty": novelty,
        "impact": _number(payload.get("impact"), minimum=0, maximum=1) or impact_defaults[kind],
        "urgency": urgency,
        "confidence": confidence,
        "relevance": relevance,
        "recurrence": recurrence,
        "reversibility": reversibility,
        "coverage": coverage,
    }


def _score(dimensions: Mapping[str, float]) -> float:
    weights = {
        "novelty": 0.10, "impact": 0.20, "urgency": 0.18,
        "confidence": 0.14, "relevance": 0.12, "recurrence": 0.10,
        "reversibility": 0.06, "coverage": 0.10,
    }
    return round(sum(weights[key] * float(dimensions.get(key, 0.0)) for key in weights) * 100, 2)


def _salience(kind: str, score: float, dimensions: Mapping[str, float], count: int) -> str:
    if score >= 86 or (kind == "BLOCKED" and dimensions["urgency"] >= 0.95):
        return "URGENT"
    if kind in {"FAILED", "BLOCKED"} and count >= 2:
        return "CREATE_WORK"
    if score >= 70:
        return "OWNER_ATTENTION"
    if score >= 52:
        return "RECOMMEND"
    if score >= 31:
        return "MONITOR"
    if score >= 15:
        return "REMEMBER"
    return "IGNORE"


def _next_action(kind: str, *, entity_kind: str | None = None) -> str:
    if kind in {"FAILED", "DEGRADED"} and entity_kind in {"provider", "connector", "capability"}:
        return "recheck capability health and route only to an explicitly eligible fallback"
    if kind == "BLOCKED":
        return "resolve the recorded dependency before materializing work"
    if kind == "STALE":
        return "refresh or reconcile the stale source before acting"
    if kind == "PENDING":
        return "revisit the pending work when its dependency or deadline changes"
    if kind == "UNKNOWN":
        return "collect missing evidence; do not infer success or availability"
    if kind == "FAILED":
        return "inspect evidence and use the existing verifier, repair, or retry authority"
    return "monitor the next observed state transition"


@dataclass(frozen=True, slots=True)
class OperationalSignal:
    signal_id: str
    fingerprint: str
    salience: str
    score: float
    state: str
    kind: str
    summary: str
    source_event_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    entity_kind: str | None
    entity_id: str | None
    mission_id: str | None
    observed_at: str
    last_seen: str
    recurrence_count: int
    suppressed_count: int
    dimensions: dict[str, float]
    next_action: str
    resolution_event_id: str | None = None
    due_at: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "fingerprint": self.fingerprint,
            "salience": self.salience,
            "score": self.score,
            "state": self.state,
            "kind": self.kind,
            "summary": self.summary,
            "source_event_ids": list(self.source_event_ids),
            "evidence_refs": list(self.evidence_refs),
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "mission_id": self.mission_id,
            "observed_at": self.observed_at,
            "last_seen": self.last_seen,
            "recurrence_count": self.recurrence_count,
            "dedupe_count": self.recurrence_count,
            "suppressed_count": self.suppressed_count,
            "dimensions": dict(self.dimensions),
            "next_action": self.next_action,
            "resolution_event_id": self.resolution_event_id,
            "due_at": self.due_at,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class OperationalFollowUp:
    follow_up_id: str
    fingerprint: str
    state: str
    kind: str
    salience: str
    summary: str
    next_action: str
    source_event_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    mission_id: str | None
    due_at: str | None
    recurrence_count: int
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "follow_up_id": self.follow_up_id,
            "fingerprint": self.fingerprint,
            "state": self.state,
            "kind": self.kind,
            "salience": self.salience,
            "summary": self.summary,
            "next_action": self.next_action,
            "source_event_ids": list(self.source_event_ids),
            "evidence_refs": list(self.evidence_refs),
            "mission_id": self.mission_id,
            "due_at": self.due_at,
            "due_state": "KNOWN" if self.due_at else "UNKNOWN",
            "recurrence_count": self.recurrence_count,
            "provenance": dict(self.provenance),
        }


def _table_observations(database: FoundryDatabase, now: datetime) -> list[dict[str, Any]]:
    """Convert current durable rows into bounded concerns without writing."""
    observations: list[dict[str, Any]] = []
    try:
        with database.connect(readonly=True) as connection:
            tables = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "missions" in tables:
                rows = connection.execute(
                    "SELECT mission_id, state, objective, updated_at, last_event_id "
                    "FROM missions ORDER BY updated_at DESC LIMIT 100"
                ).fetchall()
                for row in rows:
                    state = str(row[1] or "").upper()
                    kind = (
                        "BLOCKED" if state in {"BLOCKED", "HALTED"}
                        else "FAILED" if state in {"FAILED", "CANCELLED"}
                        else "PENDING" if state in {"WAITING", "PLANNED", "ACTION_SELECTION"}
                        else None
                    )
                    updated = _parse_time(row[3])
                    if (
                        kind is None
                        and updated is not None
                        and state in {"READY", "EXECUTING", "REPAIRING", "ORIENTING"}
                        and now - updated > timedelta(days=2)
                    ):
                        kind = "STALE"
                    if kind:
                        observations.append({
                            "kind": kind, "entity_kind": "mission",
                            "entity_id": str(row[0]), "mission_id": str(row[0]),
                            "summary": f"mission {row[0]} is {state}",
                            "source_event_id": str(row[4]) if row[4] else None,
                            "occurred_at": str(row[3] or _iso(now)),
                            "source_type": "mission_projection",
                        })
            if "agent_runs" in tables:
                rows = connection.execute(
                    "SELECT run_id, mission_id, status, failure_class, updated_at, last_event_id "
                    "FROM agent_runs ORDER BY updated_at DESC LIMIT 100"
                ).fetchall()
                for row in rows:
                    status = str(row[2] or "").upper()
                    kind = "FAILED" if status in {"FAILED", "CANCELLED"} else None
                    updated = _parse_time(row[4])
                    if (
                        kind is None
                        and status == "RUNNING"
                        and updated is not None
                        and now - updated > timedelta(hours=2)
                    ):
                        kind = "STALE"
                    if kind:
                        observations.append({
                            "kind": kind, "entity_kind": "agent_run",
                            "entity_id": str(row[0]),
                            "mission_id": str(row[1]) if row[1] else None,
                            "summary": f"agent run {row[0]} failed",
                            "source_event_id": str(row[5]) if row[5] else None,
                            "occurred_at": str(row[4] or _iso(now)),
                            "source_type": "agent_run_projection",
                            "failure_class": str(row[3] or "agent_run_failed"),
                        })
            if "tier_c_memories" in tables:
                rows = connection.execute(
                    "SELECT memory_id, state, scope, updated_at, last_event_id "
                    "FROM tier_c_memories ORDER BY updated_at DESC LIMIT 100"
                ).fetchall()
                for row in rows:
                    state = str(row[1] or "").upper()
                    if state not in {"STALE", "SUPERSEDED"}:
                        continue
                    observations.append({
                        "kind": "STALE",
                        "entity_kind": "memory",
                        "entity_id": str(row[0]),
                        "summary": f"memory {row[0]} is {state}",
                        "source_event_id": str(row[4]) if row[4] else None,
                        "occurred_at": str(row[3] or _iso(now)),
                        "source_type": "tier_c_memory_projection",
                    })
    except Exception:  # noqa: BLE001 - optional read model degrades honestly
        # The top-level projection reports the read as partial/unknown; a
        # missing optional table must not turn into a fabricated signal.
        return observations
    return observations


def _build_signals(
    database: FoundryDatabase,
    *,
    now: datetime,
    max_scan: int,
    limit: int,
) -> tuple[list[OperationalSignal], dict[str, dict[str, Any]], list[Event]]:
    events = list(reversed(database.events(
        readonly=True,
        limit=max(1, int(max_scan)),
        newest_first=True,
    )))
    groups: dict[str, dict[str, Any]] = {}
    event_to_fingerprint: dict[str, str] = {}
    entity_to_fingerprints: dict[tuple[str, str], set[str]] = {}
    for event in events:
        kind = _concern_kind(event)
        if kind is not None:
            fp = _fingerprint(event, kind)
            entity = _entity(event)
            group = groups.setdefault(fp, {
                "kind": kind,
                "first": event,
                "latest": event,
                "source_event_ids": [],
                "evidence_refs": [],
                "entity": entity,
                "mission_id": _payload(event).get("mission_id"),
                "resolution_event_id": None,
                "source_types": set(),
            })
            group["latest"] = event
            group["kind"] = kind if kind in {"BLOCKED", "FAILED", "URGENT"} else group["kind"]
            group["source_event_ids"].append(event.event_id)
            group["evidence_refs"].extend(_refs(event))
            group["source_types"].add(event.source_type)
            event_to_fingerprint[event.event_id] = fp
            if entity:
                entity_to_fingerprints.setdefault(entity, set()).add(fp)
        elif _is_relevant_resolution(event):
            payload = _payload(event)
            targets: set[str] = set()
            explicit = payload.get("resolves_fingerprint")
            if isinstance(explicit, str) and explicit in groups:
                targets.add(explicit)
            for key in ("resolves_event_id", "cause_event_id", "failure_event_id"):
                value = payload.get(key)
                if isinstance(value, str) and value in event_to_fingerprint:
                    targets.add(event_to_fingerprint[value])
            entity = _entity(event)
            if entity:
                targets.update(entity_to_fingerprints.get(entity, set()))
            for fp in targets:
                groups[fp]["resolution_event_id"] = event.event_id
    # Table rows fill a real current-state gap (blocked/pending projections)
    # while retaining their last_event_id as provenance when available.
    for observation in _table_observations(database, now):
        fp_material = {
            "kind": observation["kind"],
            "entity": (observation.get("entity_kind"), observation.get("entity_id")),
            "failure_class": observation.get("failure_class", ""),
            "event_family": "projection",
        }
        fp = hashlib.sha256(canonical_json(fp_material).encode("utf-8")).hexdigest()[:32]
        source_id = observation.get("source_event_id")
        group = groups.setdefault(fp, {
            "kind": observation["kind"], "first": None, "latest": None,
            "source_event_ids": [], "evidence_refs": [],
            "entity": (observation.get("entity_kind"), observation.get("entity_id")),
            "mission_id": observation.get("mission_id"),
            "resolution_event_id": None, "source_types": set(),
            "projection_summary": observation.get("summary", ""),
            "projection_occurred_at": observation.get("occurred_at"),
        })
        if source_id:
            group["source_event_ids"].append(str(source_id))
            group["evidence_refs"].append(str(source_id))
        group["source_types"].add(str(observation.get("source_type") or "projection"))
    signals: list[OperationalSignal] = []
    for fp, group in groups.items():
        unique_source_ids = tuple(dict.fromkeys(
            str(value) for value in group["source_event_ids"] if value
        ))
        source_ids = unique_source_ids[:16]
        evidence_refs = tuple(dict.fromkeys(str(value) for value in group["evidence_refs"] if value))[:16]
        latest: Event | None = group.get("latest")
        first: Event | None = group.get("first")
        count = max(1, len(unique_source_ids))
        if latest is not None:
            kind = str(group["kind"])
            dimensions = _dimension_values(
                latest, kind, occurrence_count=count, evidence_count=len(evidence_refs)
            )
            summary = _event_summary(latest)
            observed_at = first.occurred_at if first else latest.occurred_at
            last_seen = latest.occurred_at
            entity = _entity(latest) or group.get("entity")
            payload = _payload(latest)
            mission_id = payload.get("mission_id") or group.get("mission_id")
            source_types = sorted(group["source_types"])
        else:
            # Projection-only state has no event payload to trust; dimensions
            # are conservative and provenance says exactly where it came from.
            kind = str(group["kind"])
            dimensions = {
                "novelty": 1.0, "impact": 0.68, "urgency": 0.55,
                "confidence": 0.68, "relevance": 0.82,
                "recurrence": min(1.0, max(0, count - 1) / 3.0),
                "reversibility": 0.6,
                "coverage": 1.0 if evidence_refs else 0.2,
            }
            summary = _text(group.get("projection_summary") or "projection state requires attention")
            observed_at = str(group.get("projection_occurred_at") or _iso(now))
            last_seen = observed_at
            entity = group.get("entity")
            mission_id = group.get("mission_id")
            source_types = sorted(group["source_types"])
        score = _score(dimensions)
        salience = _salience(kind, score, dimensions, count)
        resolution_event_id = group.get("resolution_event_id")
        state = "RESOLVED" if resolution_event_id else "OPEN"
        if kind == "UNKNOWN" and not source_ids:
            state = "UNKNOWN"
        entity_kind, entity_id = entity if entity else (None, None)
        signal = OperationalSignal(
            signal_id=f"signal-{fp[:24]}",
            fingerprint=fp,
            salience=salience,
            score=score,
            state=state,
            kind=kind,
            summary=summary,
            source_event_ids=source_ids,
            evidence_refs=evidence_refs,
            entity_kind=entity_kind,
            entity_id=entity_id,
            mission_id=str(mission_id) if mission_id else None,
            observed_at=observed_at,
            last_seen=last_seen,
            recurrence_count=count,
            suppressed_count=max(0, count - 1),
            dimensions=dimensions,
            next_action=_next_action(kind, entity_kind=entity_kind),
            resolution_event_id=str(resolution_event_id) if resolution_event_id else None,
            due_at=(
                str(_payload(latest).get("next_review_at") or
                    _payload(latest).get("due_at") or
                    _payload(latest).get("deadline"))
                if latest is not None and (
                    _payload(latest).get("next_review_at") or
                    _payload(latest).get("due_at") or
                    _payload(latest).get("deadline")
                ) else None
            ),
            provenance={
                "projection": "append_only_event_ledger_and_durable_projections",
                "source_types": source_types,
                "latest_event_id": latest.event_id if latest else None,
                "resolution_event_id": resolution_event_id,
            },
        )
        signals.append(signal)
    signals.sort(key=lambda item: (
        item.state != "OPEN", -item.score, -item.recurrence_count,
        item.last_seen, item.fingerprint,
    ))
    return signals[: max(1, int(limit))], groups, events


def _followups(signals: Iterable[OperationalSignal], *, limit: int) -> list[OperationalFollowUp]:
    records: list[OperationalFollowUp] = []
    for signal in signals:
        if signal.salience == "IGNORE":
            continue
        if signal.state == "RESOLVED":
            state = "RESOLVED"
        elif signal.state == "UNKNOWN":
            state = "UNKNOWN"
        else:
            state = "OPEN"
        records.append(OperationalFollowUp(
            follow_up_id=f"followup-{signal.fingerprint[:24]}",
            fingerprint=signal.fingerprint,
            state=state,
            kind=signal.kind,
            salience=signal.salience,
            summary=signal.summary,
            next_action=signal.next_action,
            source_event_ids=signal.source_event_ids,
            evidence_refs=signal.evidence_refs,
            mission_id=signal.mission_id,
            due_at=signal.due_at,
            recurrence_count=signal.recurrence_count,
            provenance={
                "signal_id": signal.signal_id,
                "source": "operational_signal_projection",
                "owner_action_is_inferred": False,
            },
        ))
    records.sort(key=lambda item: (
        item.state != "OPEN", SALIENCE.index(item.salience), -item.recurrence_count,
        item.follow_up_id,
    ))
    return records[: max(1, int(limit))]


def _recurring_work(signals: Iterable[OperationalSignal]) -> dict[str, Any]:
    patterns = []
    for signal in signals:
        if signal.recurrence_count < 2:
            continue
        times = [_parse_time(signal.observed_at), _parse_time(signal.last_seen)]
        cadence = None
        if all(times):
            seconds = max(0.0, (times[1] - times[0]).total_seconds())
            cadence = round(seconds, 2)
        patterns.append({
            "pattern_id": f"pattern-{signal.fingerprint[:24]}",
            "fingerprint": signal.fingerprint,
            "kind": signal.kind,
            "entity_kind": signal.entity_kind,
            "entity_id": signal.entity_id,
            "occurrences": signal.recurrence_count,
            "cadence_seconds": cadence,
            "automation_state": "NOT_CONFIGURED",
            "recommendation": "review this repeated signal for a bounded scheduled workflow",
            "evidence_refs": list(signal.evidence_refs),
        })
    return {
        "status": "OBSERVED" if patterns else "NONE_OBSERVED",
        "patterns": patterns[:20],
        "scheduler_mutated": False,
        "next_action": "review repeated patterns before registering automation" if patterns else None,
    }


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return list(parsed)
    return []


def _normalized_text(value: Any, *, limit: int = 4000) -> str:
    return " ".join(_text(value, limit=limit).lower().split())


def _memory_and_knowledge_projection(
    database: FoundryDatabase,
    signals: Iterable[OperationalSignal],
    *,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read Tier C memory and Tier B knowledge quality without promoting it.

    The existing lifecycle, curation, and knowledge projectors remain the
    authorities.  This function only reports their current quality shape and
    bounded consolidation candidates so the owner can choose a governed
    operation later.  It deliberately does not append events or mark rows.
    """
    del signals  # reserved for future cross-source memory links
    memory_rows: list[dict[str, Any]] = []
    knowledge_rows: list[dict[str, Any]] = []
    contradiction_rows: list[dict[str, Any]] = []
    evidence_links: dict[str, list[str]] = {}
    memory_table_present = False
    knowledge_table_present = False
    try:
        with database.connect(readonly=True) as connection:
            tables = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            memory_table_present = "tier_c_memories" in tables
            knowledge_table_present = "knowledge_claims" in tables
            if "tier_c_memories" in tables:
                cursor = connection.execute(
                    "SELECT * FROM tier_c_memories "
                    "ORDER BY updated_at DESC, rowid DESC LIMIT 200"
                )
                memory_rows = [dict(row) for row in cursor.fetchall()]
            if "knowledge_claims" in tables:
                cursor = connection.execute(
                    "SELECT * FROM knowledge_claims "
                    "ORDER BY updated_at DESC, rowid DESC LIMIT 200"
                )
                knowledge_rows = [dict(row) for row in cursor.fetchall()]
            if "knowledge_contradictions" in tables:
                cursor = connection.execute(
                    "SELECT * FROM knowledge_contradictions "
                    "ORDER BY updated_at DESC, rowid DESC LIMIT 100"
                )
                contradiction_rows = [dict(row) for row in cursor.fetchall()]
            if "knowledge_claim_evidence" in tables:
                cursor = connection.execute(
                    "SELECT claim_id, evidence_id FROM knowledge_claim_evidence "
                    "ORDER BY linked_at, rowid"
                )
                for row in cursor.fetchall():
                    evidence_links.setdefault(str(row[0]), []).append(str(row[1]))
    except Exception:  # noqa: BLE001 - optional quality surface is truthful
        return (
            {"schema": "v8.memory-intelligence.v1", "status": "UNKNOWN",
             "items": [], "quality": {}, "consolidation_candidates": [],
             "stale": [], "truth_notes": ["memory projection unavailable"]},
            {"schema": "v8.knowledge-intelligence.v1", "status": "UNKNOWN",
             "claims": [], "quality": {}, "consolidation_candidates": [],
             "contradictions": [], "truth_notes": ["knowledge projection unavailable"]},
        )

    memory_items: list[dict[str, Any]] = []
    memory_states: dict[str, int] = {}
    stale_memories: list[dict[str, Any]] = []
    memory_groups: dict[str, list[dict[str, Any]]] = {}
    provenance_count = 0
    relation_count = 0
    for row in memory_rows:
        memory_id = str(row.get("memory_id") or "")
        state = str(row.get("state") or "UNKNOWN").upper()
        memory_states[state] = memory_states.get(state, 0) + 1
        source_ids = _json_list(row.get("source_ids_json"))
        evidence_ids = _json_list(row.get("evidence_ids_json"))
        has_provenance = bool(source_ids or evidence_ids or row.get("last_event_id"))
        # last_event_id proves the row's ledger lineage; source/evidence refs
        # are still reported separately because they prove content origin.
        if source_ids or evidence_ids:
            provenance_count += 1
        expiry = _parse_time(row.get("expires_at"))
        freshness = (
            "STALE" if state in {"STALE", "SUPERSEDED"}
            or (expiry is not None and expiry <= now)
            else "FRESH" if expiry is not None else "NO_EXPIRY"
        )
        item = {
            "memory_id": memory_id,
            "memory_type": row.get("mem_type"),
            "scope": row.get("scope"),
            "state": state,
            "summary": _text(row.get("content"), limit=240),
            "confidence": row.get("confidence"),
            "importance": row.get("importance"),
            "freshness": freshness,
            "expires_at": row.get("expires_at"),
            "source_ids": [str(item) for item in source_ids[:16]],
            "evidence_refs": [str(item) for item in evidence_ids[:16]],
            "supersedes": row.get("supersedes"),
            "contradicts": row.get("contradicts"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "last_event_id": row.get("last_event_id"),
            "provenance_state": "PRESENT" if has_provenance else "MISSING",
        }
        memory_items.append(item)
        if freshness == "STALE":
            stale_memories.append(item)
        if row.get("contradicts") or row.get("supersedes"):
            relation_count += 1
        if state in {
            "OBSERVED", "CANDIDATE_MEMORY", "VALIDATING",
            "ACCEPTED_EXPERIENCE", "CORE", "EXTERNAL", "PROCEDURAL",
        }:
            normalized = _normalized_text(row.get("content"))
            if normalized:
                memory_groups.setdefault(normalized, []).append(item)

    memory_candidates: list[dict[str, Any]] = []
    for normalized, items in sorted(memory_groups.items()):
        if len(items) < 2:
            continue
        refs = [ref for item in items for ref in item["evidence_refs"]]
        memory_candidates.append({
            "candidate_id": "memory-duplicate-" + hashlib.sha256(
                normalized.encode("utf-8")).hexdigest()[:16],
            "kind": "DUPLICATE_MEMORY",
            "memory_ids": [item["memory_id"] for item in items],
            "summary": "active memories share the same normalized content",
            "evidence_refs": list(dict.fromkeys(refs))[:16],
            "authority": "hybrid_memory.CurationPipeline",
            "action": "review through curation before governed consolidate_duplicates",
            "state": "RECOMMEND",
        })
    memory_total = len(memory_rows)
    memory_projection = {
        "schema": "v8.memory-intelligence.v1",
        "status": (
            "AVAILABLE" if memory_total
            else "EMPTY" if memory_table_present else "UNKNOWN"
        ),
        "source": "tier_c_memories_event_projection",
        "items": memory_items,
        "stale": stale_memories[:40],
        "quality": {
            "total": memory_total,
            "states": memory_states,
            "with_content_provenance": provenance_count,
            "provenance_coverage": round(provenance_count / memory_total, 4)
            if memory_total else None,
            "relation_links": relation_count,
            "active_duplicate_groups": len(memory_candidates),
        },
        "consolidation_candidates": memory_candidates[:40],
        "truth_notes": [
            "Tier C memory is advisory and cannot outrank Tier A/B",
            "duplicate candidates are recommendations; no memory was mutated",
            "stale means explicit lifecycle/expiry state, not inferred truth",
        ],
    }

    claim_items: list[dict[str, Any]] = []
    claim_states: dict[str, int] = {}
    stale_claims: list[dict[str, Any]] = []
    claim_groups: dict[str, list[dict[str, Any]]] = {}
    claims_with_evidence = 0
    for row in knowledge_rows:
        claim_id = str(row.get("claim_id") or "")
        status = str(row.get("status") or "UNKNOWN").upper()
        claim_states[status] = claim_states.get(status, 0) + 1
        source_refs = [
            str(value) for value in (
                row.get("source_event_id"), row.get("source_run_id")
            ) if value
        ]
        source_refs.extend(evidence_links.get(claim_id, [])[:16])
        if source_refs:
            claims_with_evidence += 1
        expiry = _parse_time(row.get("fresh_until"))
        freshness = "STALE" if expiry is not None and expiry <= now else "FRESH" if expiry else "NO_EXPIRY"
        item = {
            "claim_id": claim_id,
            "mission_id": row.get("mission_id"),
            "record_kind": row.get("record_kind") or "CLAIM",
            "claim_type": row.get("claim_type"),
            "statement": _text(row.get("statement"), limit=400),
            "confidence": row.get("confidence"),
            "status": status,
            "freshness": freshness,
            "fresh_until": row.get("fresh_until"),
            "source_refs": list(dict.fromkeys(source_refs))[:16],
            "last_event_id": row.get("last_event_id"),
            "updated_at": row.get("updated_at"),
        }
        claim_items.append(item)
        if freshness == "STALE" and status == "ACCEPTED":
            stale_claims.append(item)
        if status == "ACCEPTED":
            normalized = _normalized_text(row.get("statement"))
            if normalized:
                claim_groups.setdefault(normalized, []).append(item)

    knowledge_candidates: list[dict[str, Any]] = []
    for normalized, items in sorted(claim_groups.items()):
        if len(items) < 2:
            continue
        refs = [ref for item in items for ref in item["source_refs"]]
        knowledge_candidates.append({
            "candidate_id": "claim-duplicate-" + hashlib.sha256(
                normalized.encode("utf-8")).hexdigest()[:16],
            "kind": "DUPLICATE_CLAIM",
            "claim_ids": [item["claim_id"] for item in items],
            "summary": "accepted claims share the same normalized statement",
            "evidence_refs": list(dict.fromkeys(refs))[:16],
            "authority": "knowledge projection and governed ingestion",
            "action": "review confidence, freshness, and evidence before consolidation",
            "state": "RECOMMEND",
        })
    contradiction_items = [
        {
            "contradiction_id": row.get("contradiction_id"),
            "claim_a_id": row.get("claim_a_id"),
            "claim_b_id": row.get("claim_b_id"),
            "claim_type": row.get("claim_type"),
            "reason": _text(row.get("reason"), limit=400),
            "last_event_id": row.get("last_event_id"),
            "updated_at": row.get("updated_at"),
        }
        for row in contradiction_rows
    ]
    claim_total = len(knowledge_rows)
    knowledge_projection = {
        "schema": "v8.knowledge-intelligence.v1",
        "status": (
            "AVAILABLE" if claim_total or contradiction_items
            else "EMPTY" if knowledge_table_present else "UNKNOWN"
        ),
        "source": "knowledge_claims_and_contradiction_projection",
        "claims": claim_items,
        "stale": stale_claims[:40],
        "contradictions": contradiction_items,
        "quality": {
            "total": claim_total,
            "states": claim_states,
            "accepted": claim_states.get("ACCEPTED", 0),
            "with_evidence": claims_with_evidence,
            "evidence_coverage": round(claims_with_evidence / claim_total, 4)
            if claim_total else None,
            "stale_accepted": len(stale_claims),
            "contradiction_count": len(contradiction_items),
            "accepted_duplicate_groups": len(knowledge_candidates),
        },
        "consolidation_candidates": knowledge_candidates[:40],
        "truth_notes": [
            "claims remain Tier B and never outrank ledger evidence",
            "freshness and evidence are reported from stored fields only",
            "duplicate and contradiction candidates require governed review",
        ],
    }
    return memory_projection, knowledge_projection


def _evolution_projection(
    database: FoundryDatabase,
    signals: Iterable[OperationalSignal],
    events: Iterable[Event],
) -> dict[str, Any]:
    """Expose existing V6/V7 evolution evidence and bounded recommendations."""
    prefixes = (
        "improvement.", "measurement.", "experiment.", "benchmark.",
        "verifier.", "eligibility.", "cycle.", "feedback.",
        "evolution.", "discovery.", "candidate.", "shadow.", "canary.",
        "promotion.", "release.", "restore.",
    )
    relevant = [
        event for event in events
        if str(event.event_type or "").startswith(prefixes)
    ]
    records: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for event in relevant[-200:]:
        payload = _payload(event)
        status = str(
            payload.get("status") or payload.get("outcome")
            or payload.get("verdict") or payload.get("decision") or "RECORDED"
        ).upper()
        status_counts[status] = status_counts.get(status, 0) + 1
        records.append({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "status": status,
            "improvement_id": payload.get("improvement_id"),
            "candidate_id": payload.get("candidate_id"),
            "candidate_release_id": payload.get("candidate_release_id"),
            "evidence_refs": list(dict.fromkeys(
                str(value) for value in (
                    _json_list(payload.get("evidence_refs"))
                    + _json_list(payload.get("artifacts"))
                ) if value
            ))[:16],
        })

    improvements: list[dict[str, Any]] = []
    try:
        with database.connect(readonly=True) as connection:
            tables = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "improvement_records" in tables:
                rows = connection.execute(
                    "SELECT improvement_id, state, problem, hypothesis, "
                    "risk_class, evidence_refs_json, created_at, updated_at, "
                    "last_event_id FROM improvement_records "
                    "ORDER BY updated_at DESC, rowid DESC LIMIT 100"
                ).fetchall()
                for row in rows:
                    improvements.append({
                        "improvement_id": row[0],
                        "state": row[1],
                        "problem": _text(row[2], limit=400),
                        "hypothesis": _text(row[3], limit=400),
                        "risk_class": row[4],
                        "evidence_refs": _json_list(row[5])[:16],
                        "created_at": row[6],
                        "updated_at": row[7],
                        "last_event_id": row[8],
                    })
    except Exception:  # noqa: BLE001 - optional table remains UNKNOWN below
        improvements = []

    existing_refs = {
        str(ref)
        for improvement in improvements
        for ref in improvement.get("evidence_refs", [])
    }
    recommendations: list[dict[str, Any]] = []
    for signal in signals:
        if signal.state != "OPEN" or signal.recurrence_count < 2:
            continue
        if signal.kind not in {"FAILED", "BLOCKED", "DEGRADED", "STALE"}:
            continue
        refs = list(signal.evidence_refs)
        recommendations.append({
            "recommendation_id": "evolution-recommend-" + signal.fingerprint[:16],
            "kind": "IMPROVEMENT_HYPOTHESIS",
            "signal_id": signal.signal_id,
            "fingerprint": signal.fingerprint,
            "summary": f"repeated {signal.kind.lower()} signal merits a governed improvement hypothesis",
            "expected_benefit": "reduce recurrence while preserving correctness and safety guardrails",
            "evidence_refs": refs,
            "already_recorded": bool(existing_refs.intersection(refs)),
            "authority": "Supervisor EvolutionControl -> existing V6 discovery/cycle gates",
            "state": "RECORDED" if existing_refs.intersection(refs) else "RECOMMEND",
            "auto_mutation": False,
        })
    mode = None
    for event in relevant:
        if event.event_type == "evolution.mode_changed":
            value = _payload(event).get("mode")
            if isinstance(value, str) and value:
                mode = value.upper()
    status = "AVAILABLE" if records or improvements else "EMPTY"
    return {
        "schema": "v8.evolution-intelligence.v1",
        "status": status,
        "source": "existing_v6_v7_evolution_ledger_and_records",
        "mode": mode or "UNKNOWN",
        "records": records,
        "improvements": improvements,
        "candidate_recommendations": recommendations[:40],
        "summary": {
            "recorded_events": len(records),
            "improvements": len(improvements),
            "recommendations": len(recommendations),
            "status_counts": status_counts,
        },
        "authority": "EvolutionControl / Supervisor",
        "auto_mutation": False,
        "truth_notes": [
            "recommendations never create or promote an improvement",
            "candidate build, measurement, verification, canary, promotion, and rollback remain existing V6 authorities",
            "missing experiment evidence is UNKNOWN, not a pass",
        ],
    }


def _latest_provider_observations(events: Iterable[Event]) -> dict[tuple[str, str], dict[str, Any]]:
    observations: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        if event.event_type not in {
            "provider.health_sample", "provider.health_recorded",
            "provider.observed", "provider.availability_sample",
        }:
            continue
        payload = _payload(event)
        provider = payload.get("provider_id") or payload.get("provider")
        model = payload.get("model_id") or payload.get("model") or ""
        if not isinstance(provider, str) or not provider.strip():
            continue
        key = (provider.strip(), str(model).strip())
        current = dict(observations.get(key) or {})
        current.update({
            key_name: payload[key_name] for key_name in (
                "health", "status", "auth_state", "availability", "latency_ms",
                "reliability", "cost_usd", "limits", "capabilities", "tool_support",
                "context_window_tokens", "evidence_refs",
            ) if key_name in payload
        })
        current["event_id"] = event.event_id
        current["observed_at"] = event.occurred_at
        observations[key] = current
    return observations


def _provider_projection(events: Iterable[Event]) -> dict[str, Any]:
    observed = _latest_provider_observations(events)
    items: list[dict[str, Any]] = []
    for service in list_services():
        models = list(service.known_models or (service.default_model,))
        if service.free_test_model and service.free_test_model not in models:
            models.append(service.free_test_model)
        for model in models:
            sample = dict(observed.get((service.provider_id, model)) or observed.get((service.provider_id, "")) or {})
            health = str(sample.get("health") or sample.get("status") or "UNKNOWN").upper()
            auth = str(sample.get("auth_state") or "UNKNOWN").upper()
            availability = str(sample.get("availability") or "UNKNOWN").upper()
            items.append({
                "provider": service.provider_id,
                "model": model,
                "family": service.family_id,
                "display_name": service.display_name,
                "health": health,
                "auth_state": auth,
                "availability": availability,
                "latency_ms": sample.get("latency_ms"),
                "reliability": sample.get("reliability"),
                "cost_usd": sample.get("cost_usd"),
                "limits": sample.get("limits", dict(service.limits) or None),
                "permissions": None,
                "context_window_tokens": sample.get("context_window_tokens", service.context_window_tokens),
                "tool_support": sample.get("tool_support", list(service.tool_support) or None),
                "capabilities": sample.get("capabilities", list(service.capabilities) or None),
                "required_authority": service.required_authority,
                "known": bool(sample),
                "source_event_id": sample.get("event_id"),
                "observed_at": sample.get("observed_at"),
                "evidence_refs": list(sample.get("evidence_refs") or ([sample["event_id"]] if sample.get("event_id") else [])),
            })
    known_items = [item for item in items if item["known"]]
    ready_items = [item for item in known_items if (
        item["health"] in {"HEALTHY", "READY"}
        and item["auth_state"] in {"READY", "CONFIGURED"}
        and item["availability"] in {"AVAILABLE", "READY"}
    )]
    return {
        "status": "AVAILABLE" if ready_items else "OBSERVED" if known_items else "UNKNOWN",
        "items": items,
        "unknown_count": sum(not item["known"] for item in items),
        "source": "runtime_provider_registry_plus_provider_observation_events",
        "note": "missing health/auth/capability samples remain UNKNOWN",
    }


def _capability_projection(database: FoundryDatabase) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    try:
        for capability in discover_capabilities(database):
            row = dict(capability)
            row["source"] = "capability_registry"
            items.append(row)
    except Exception:  # noqa: BLE001 - registry failure is surfaced as UNKNOWN
        return {"status": "UNKNOWN", "items": [], "reason": "capability registry read failed"}
    try:
        for connector in list_operational_statuses(database):
            items.append({
                "capability_id": f"connector:{connector['connector_id']}",
                "name": connector["connector_id"],
                "family": "connector",
                "surfaces": sorted(connector.get("actions", {})),
                "effect_class": "connector",
                "risk": "UNKNOWN",
                "verification_hint": connector.get("version", "UNKNOWN"),
                "health": connector.get("health", "UNKNOWN"),
                "auth_state": "UNKNOWN",
                "availability": connector.get("readiness", "UNKNOWN"),
                "latency_ms": None,
                "reliability": None,
                "limits": connector.get("quota"),
                "permissions": None,
                "context": None,
                "tool_support": sorted(connector.get("actions", {})),
                "required_authority": "SUPERVISOR_CONTROLLER",
                "source": "connector_registry",
            })
    except Exception:  # noqa: BLE001 - optional connector registry
        # Capability records remain useful even if an optional connector
        # registry is unavailable; report that partiality at the caller.
        pass
    items.sort(key=lambda item: str(item.get("capability_id") or ""))
    return {
        "status": "AVAILABLE" if items else "EMPTY",
        "items": items[:200],
        "unknown_count": sum(
            str(item.get("health") or "UNKNOWN").upper() == "UNKNOWN"
            for item in items
        ),
        "source": "canonical_capability_and_connector_registries",
    }


def _routing_projection(provider_projection: Mapping[str, Any]) -> dict[str, Any]:
    router = RoleRouter()
    provider_items = list(provider_projection.get("items") or [])
    candidates: list[ProviderRouteCandidate] = []
    for item in provider_items:
        candidates.append(ProviderRouteCandidate(
            provider=str(item.get("provider") or ""),
            model=str(item.get("model") or ""),
            capabilities=tuple(str(value) for value in (item.get("capabilities") or ()) if value),
            context_window_tokens=(int(item["context_window_tokens"]) if isinstance(item.get("context_window_tokens"), (int, float)) else None),
            latency_ms=(float(item["latency_ms"]) if isinstance(item.get("latency_ms"), (int, float)) else None),
            reliability=(float(item["reliability"]) if isinstance(item.get("reliability"), (int, float)) else None),
            cost_usd=(float(item["cost_usd"]) if isinstance(item.get("cost_usd"), (int, float)) else None),
            health=str(item.get("health") or "UNKNOWN"),
            auth_state=str(item.get("auth_state") or "UNKNOWN"),
            availability=str(item.get("availability") or "UNKNOWN"),
            evidence_refs=tuple(str(value) for value in (item.get("evidence_refs") or ()) if value),
        ))
    roles: list[dict[str, Any]] = []
    for role in router.known_roles():
        decision = router.route(
            RoutingRequest(task_type=f"role:{role}"),
            candidates,
            role=role,
        )
        roles.append({"role": role, **decision.to_dict()})
    return {
        "status": "AVAILABLE" if any(item["status"] in {"SELECTED", "FALLBACK"} for item in roles) else "UNKNOWN",
        "roles": roles,
        "policy": {
            "strict_internal_identity": {
                "provider": router.provider_settings.provider,
                "model": router.provider_settings.model,
                "fallbacks": False,
            },
            "no_silent_incapable_routing": True,
        },
    }


def _context(
    signals: Iterable[OperationalSignal],
    capability_projection: Mapping[str, Any],
    provider_projection: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    for signal in list(signals)[:DEFAULT_CONTEXT_LIMIT]:
        fact = {
            "subject": signal.entity_id or signal.fingerprint,
            "assertion": signal.summary,
            "kind": signal.kind,
            "salience": signal.salience,
            "confidence": signal.dimensions.get("confidence"),
            "source_event_ids": list(signal.source_event_ids),
            "evidence_refs": list(signal.evidence_refs),
            "provenance": signal.provenance,
        }
        facts.append(fact)
        if signal.state == "UNKNOWN" or signal.kind == "UNKNOWN":
            unknowns.append({
                "subject": signal.entity_id or signal.fingerprint,
                "reason": signal.summary,
                "evidence_refs": list(signal.evidence_refs),
            })
    for item in list(provider_projection.get("items") or []):
        if not item.get("known"):
            unknowns.append({
                "subject": f"{item.get('provider')}:{item.get('model')}",
                "reason": "provider health/auth/capability observation is UNKNOWN",
                "evidence_refs": [],
            })
        else:
            facts.append({
                "subject": f"{item.get('provider')}:{item.get('model')}",
                "assertion": (
                    f"provider health={item.get('health', 'UNKNOWN')} "
                    f"auth={item.get('auth_state', 'UNKNOWN')} "
                    f"availability={item.get('availability', 'UNKNOWN')}"
                ),
                "kind": "PROVIDER_OBSERVATION",
                "salience": "MONITOR",
                "confidence": None,
                "source_event_ids": [item["source_event_id"]]
                if item.get("source_event_id") else [],
                "evidence_refs": list(item.get("evidence_refs") or []),
                "provenance": {
                    "source": "runtime_provider_registry_plus_observation",
                    "model_inference": False,
                },
            })
    for item in list(capability_projection.get("items") or []):
        if str(item.get("health") or "UNKNOWN").upper() == "UNKNOWN":
            unknowns.append({
                "subject": item.get("capability_id"),
                "reason": "capability has no health observation",
                "evidence_refs": [],
            })
        else:
            facts.append({
                "subject": item.get("capability_id"),
                "assertion": (
                    f"capability health={item.get('health', 'UNKNOWN')} "
                    f"availability={item.get('availability', 'UNKNOWN')}"
                ),
                "kind": "CAPABILITY_OBSERVATION",
                "salience": "MONITOR",
                "confidence": None,
                "source_event_ids": [item["observation_event_id"]]
                if item.get("observation_event_id") else [],
                "evidence_refs": list(item.get("evidence_refs") or []),
                "provenance": {
                    "source": item.get("source", "capability_registry"),
                    "model_inference": False,
                },
            })
    # A fingerprint with both an unresolved failure and an explicit recovery
    # remains a coherent resolved fact; conflicting latest statuses are the
    # only contradictions surfaced here, never silently flattened.
    by_entity: dict[str, set[str]] = {}
    for signal in signals:
        if signal.entity_id:
            by_entity.setdefault(signal.entity_id, set()).add(
                "RESOLVED" if signal.state == "RESOLVED" else signal.kind
            )
    for subject, kinds in sorted(by_entity.items()):
        if "FAILED" in kinds and "DEGRADED" in kinds and "RESOLVED" not in kinds:
            contradictions.append({
                "subject": subject,
                "claims": sorted(kinds),
                "reason": "multiple unresolved operational states coexist",
            })
    return {
        "schema": "v8.operational-context.v1",
        "status": "AVAILABLE" if facts else "EMPTY_LEDGER",
        "generated_at": _iso(now),
        "facts": facts[:DEFAULT_CONTEXT_LIMIT],
        "unknowns": unknowns[:80],
        "contradictions": contradictions[:40],
        "provenance": {
            "source": "event_ledger_plus_canonical_projections",
            "model_inference": False,
        },
    }


def project_operational_intelligence(
    database: FoundryDatabase,
    *,
    now: datetime | str | None = None,
    stop_path: str | Path | None = None,
    max_scan: int = DEFAULT_EVENT_SCAN,
    signal_limit: int = DEFAULT_SIGNAL_LIMIT,
    followup_limit: int = DEFAULT_FOLLOWUP_LIMIT,
) -> dict[str, Any]:
    """Build the bounded V8 operational-intelligence projection."""
    clock = _clock(now)
    stop_record = None
    if stop_path is not None:
        stop_record = EmergencyStop(stop_path).read()
    errors: list[str] = []
    try:
        signals, _groups, events = _build_signals(
            database, now=clock, max_scan=max_scan, limit=signal_limit
        )
    except Exception as error:  # noqa: BLE001 - owner surface must remain honest
        signals, events = [], []
        errors.append(f"signal projection unavailable: {type(error).__name__}")
    followups = _followups(signals, limit=followup_limit)
    try:
        capability_projection = _capability_projection(database)
    except Exception as error:  # noqa: BLE001 - additive surface
        capability_projection = {"status": "UNKNOWN", "items": [], "unknown_count": 0}
        errors.append(f"capability projection unavailable: {type(error).__name__}")
    try:
        memory_projection, knowledge_projection = _memory_and_knowledge_projection(
            database, signals, now=clock
        )
    except Exception as error:  # noqa: BLE001 - additive surface
        memory_projection = {
            "schema": "v8.memory-intelligence.v1", "status": "UNKNOWN",
            "items": [], "quality": {}, "consolidation_candidates": [],
        }
        knowledge_projection = {
            "schema": "v8.knowledge-intelligence.v1", "status": "UNKNOWN",
            "claims": [], "quality": {}, "consolidation_candidates": [],
        }
        errors.append(f"memory/knowledge projection unavailable: {type(error).__name__}")
    try:
        provider_projection = _provider_projection(events)
        routing_projection = _routing_projection(provider_projection)
    except Exception as error:  # noqa: BLE001 - additive surface
        provider_projection = {"status": "UNKNOWN", "items": [], "unknown_count": 0}
        routing_projection = {"status": "UNKNOWN", "roles": [], "policy": {}}
        errors.append(f"routing projection unavailable: {type(error).__name__}")
    try:
        evolution_projection = _evolution_projection(database, signals, events)
    except Exception as error:  # noqa: BLE001 - additive surface
        evolution_projection = {
            "schema": "v8.evolution-intelligence.v1", "status": "UNKNOWN",
            "records": [], "improvements": [], "candidate_recommendations": [],
        }
        errors.append(f"evolution projection unavailable: {type(error).__name__}")
    context = _context(signals, capability_projection, provider_projection, now=clock)
    recurring = _recurring_work(signals)
    counts = {salience: 0 for salience in SALIENCE}
    state_counts = {state: 0 for state in SIGNAL_STATES}
    for signal in signals:
        counts[signal.salience] = counts.get(signal.salience, 0) + 1
        state_counts[signal.state] = state_counts.get(signal.state, 0) + 1
    if (
        not signals
        and not capability_projection["items"]
        and memory_projection.get("status") in {"EMPTY", "UNKNOWN"}
        and knowledge_projection.get("status") in {"EMPTY", "UNKNOWN"}
        and evolution_projection.get("status") in {"EMPTY", "UNKNOWN"}
    ):
        status = "EMPTY_LEDGER"
    elif errors:
        status = "PARTIAL"
    else:
        status = "AVAILABLE"
    return {
        "schema": OPERATIONAL_INTELLIGENCE_SCHEMA,
        "version": OPERATIONAL_INTELLIGENCE_VERSION,
        "status": status,
        "generated_at": _iso(clock),
        "source_of_truth": {
            "kind": "append_only_event_ledger",
            "bounded_event_scan": max(1, int(max_scan)),
            "event_count": database.event_count(readonly=True),
        },
        "controls": {
            "stop_active": bool(stop_record),
            "stop_record": stop_record,
            "can_execute": False if stop_record else "UNKNOWN",
            "execution_authority": "existing_action_broker_and_owner_controls",
        },
        "capabilities": capability_projection,
        "providers": provider_projection,
        "routing": routing_projection,
        "memory": memory_projection,
        "knowledge": knowledge_projection,
        "evolution": evolution_projection,
        "signals": [signal.to_dict() for signal in signals],
        "attention": {
            "counts_by_salience": counts,
            "counts_by_state": state_counts,
            "open_count": sum(signal.state == "OPEN" for signal in signals),
            "owner_attention_count": sum(
                signal.state == "OPEN" and signal.salience in {"OWNER_ATTENTION", "URGENT"}
                for signal in signals
            ),
            "deduped_occurrences": sum(signal.suppressed_count for signal in signals),
        },
        "followups": [followup.to_dict() for followup in followups],
        "recurring_work": recurring,
        "context": context,
        "errors": errors,
        "truth_notes": [
            "UNKNOWN means no trustworthy observation was recorded",
            "routing decisions are read-only and do not authorize execution",
            "recurring_work never mutates the shared scheduler",
        ],
    }


__all__ = [
    "OperationalFollowUp", "OperationalSignal", "OPERATIONAL_INTELLIGENCE_SCHEMA",
    "project_operational_intelligence",
]
