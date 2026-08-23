"""Event-derived resilience control for the V7 lane.

The module deliberately sits above the existing Supervisor, release, effect,
and capability primitives. Incidents are durable ``incident.*`` events, so a
fresh process reconstructs the same state without a second database authority.
Automatic repair is callback-driven and bounded. No callback means honest
``BLOCKED`` rather than an invented repair.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..clock import isoformat, parse
from ..control.capabilities import set_capability_state
from ..control.redaction import redact_payload, redact_text
from ..database import FoundryDatabase
from ..events import Event, canonical_json


class IncidentStatus(StrEnum):
    DETECTED = "DETECTED"
    TRIAGED = "TRIAGED"
    CONTAINING = "CONTAINING"
    CONTAINED = "CONTAINED"
    DIAGNOSING = "DIAGNOSING"
    REPAIRING = "REPAIRING"
    VERIFYING = "VERIFYING"
    RECOVERED = "RECOVERED"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    UNKNOWN = "UNKNOWN"


class IncidentSeverity(StrEnum):
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"


class EffectAmbiguity(StrEnum):
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    RECONCILED = "RECONCILED"
    MISMATCH = "MISMATCH"


class AnomalyKind(StrEnum):
    SUPERVISOR_HEARTBEAT_MISSING = "supervisor_heartbeat_missing"
    DUPLICATE_AUTHORITY = "duplicate_authority"
    WORKER_CRASH_LOOP = "worker_crash_loop"
    PROVIDER_OUTAGE = "provider_outage"
    CONNECTOR_OUTAGE = "connector_outage"
    EFFECT_UNKNOWN = "effect_unknown"
    DATABASE_FAULT = "database_fault"
    RELEASE_DEGRADATION = "release_degradation"
    CANARY_DEGRADATION = "canary_degradation"
    COMMAND_CENTER_STALE = "command_center_stale"
    REPAIR_FAILURE_LOOP = "repair_failure_loop"
    RESOURCE_ANOMALY = "resource_anomaly"
    COST_ANOMALY = "cost_anomaly"


_TERMINAL = frozenset({
    IncidentStatus.RECOVERED,
    IncidentStatus.BLOCKED,
    IncidentStatus.ESCALATED,
    IncidentStatus.UNKNOWN,
})

_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.DETECTED: frozenset({
        IncidentStatus.TRIAGED, IncidentStatus.BLOCKED, IncidentStatus.UNKNOWN,
    }),
    IncidentStatus.TRIAGED: frozenset({
        IncidentStatus.CONTAINING, IncidentStatus.BLOCKED, IncidentStatus.UNKNOWN,
    }),
    IncidentStatus.CONTAINING: frozenset({
        IncidentStatus.CONTAINED, IncidentStatus.BLOCKED, IncidentStatus.UNKNOWN,
    }),
    IncidentStatus.CONTAINED: frozenset({
        IncidentStatus.DIAGNOSING, IncidentStatus.BLOCKED, IncidentStatus.UNKNOWN,
    }),
    IncidentStatus.DIAGNOSING: frozenset({
        IncidentStatus.REPAIRING, IncidentStatus.VERIFYING,
        IncidentStatus.BLOCKED, IncidentStatus.UNKNOWN,
    }),
    IncidentStatus.REPAIRING: frozenset({
        IncidentStatus.DIAGNOSING, IncidentStatus.VERIFYING,
        IncidentStatus.BLOCKED, IncidentStatus.ESCALATED,
        IncidentStatus.UNKNOWN,
    }),
    IncidentStatus.VERIFYING: frozenset({
        IncidentStatus.RECOVERED, IncidentStatus.DIAGNOSING,
        IncidentStatus.BLOCKED, IncidentStatus.ESCALATED,
        IncidentStatus.UNKNOWN,
    }),
    IncidentStatus.RECOVERED: frozenset(),
    IncidentStatus.BLOCKED: frozenset({
        IncidentStatus.DIAGNOSING, IncidentStatus.REPAIRING,
    }),
    IncidentStatus.ESCALATED: frozenset({IncidentStatus.DIAGNOSING}),
    IncidentStatus.UNKNOWN: frozenset({IncidentStatus.DIAGNOSING}),
}


class IncidentLifecycleError(ValueError):
    """A transition or record violates the durable incident contract."""


def _text(value: Any, *, limit: int = 600) -> str:
    return redact_text(str(value or "")).replace("\r", " ").replace("\n", " ")[:limit]


def _safe_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    cleaned = redact_payload(dict(value))
    return cleaned if isinstance(cleaned, dict) else {"value": _text(cleaned)}


def _unique_strings(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_text(value, limit=240) for value in values if value))


@dataclass(frozen=True, slots=True)
class HealthSignal:
    signal_id: str
    kind: str
    severity: str = IncidentSeverity.S3.value
    observed_at: str = field(default_factory=isoformat)
    correlation_key: str = ""
    resource_id: str | None = None
    connector_id: str | None = None
    capability: str | None = None
    mission_id: str | None = None
    effect_action_key: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    suspected_cause: str | None = None
    effect_ambiguity: str = EffectAmbiguity.NONE.value
    recovery_criteria: tuple[str, ...] = ()
    owner_relevance: str = "material"

    def __post_init__(self) -> None:
        if not self.signal_id or not self.kind:
            raise IncidentLifecycleError("health signal requires id and kind")
        severity = str(self.severity).upper()
        if severity not in {item.value for item in IncidentSeverity}:
            raise IncidentLifecycleError(f"unknown incident severity: {self.severity}")
        ambiguity = str(self.effect_ambiguity).upper()
        if ambiguity not in {item.value for item in EffectAmbiguity}:
            raise IncidentLifecycleError(f"unknown effect ambiguity: {self.effect_ambiguity}")
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "effect_ambiguity", ambiguity)
        object.__setattr__(self, "correlation_key", self.correlation_key or self.kind)

    def to_payload(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "kind": _text(self.kind, limit=120),
            "severity": self.severity,
            "observed_at": self.observed_at,
            "correlation_key": _text(self.correlation_key, limit=240),
            "resource_id": _text(self.resource_id, limit=240) if self.resource_id else None,
            "connector_id": _text(self.connector_id, limit=240) if self.connector_id else None,
            "capability": _text(self.capability, limit=240) if self.capability else None,
            "mission_id": _text(self.mission_id, limit=240) if self.mission_id else None,
            "effect_action_key": _text(self.effect_action_key, limit=320)
            if self.effect_action_key else None,
            "evidence": _safe_mapping(self.evidence),
            "suspected_cause": _text(self.suspected_cause)
            if self.suspected_cause else None,
            "effect_ambiguity": self.effect_ambiguity,
            "recovery_criteria": list(_unique_strings(self.recovery_criteria)),
            "owner_relevance": _text(self.owner_relevance, limit=120),
        }


@dataclass(frozen=True, slots=True)
class IncidentRecord:
    incident_id: str
    correlation_key: str
    severity: str
    status: str
    first_signal_at: str
    last_signal_at: str
    signal_kinds: tuple[str, ...] = ()
    affected_resources: tuple[str, ...] = ()
    affected_capabilities: tuple[str, ...] = ()
    affected_missions: tuple[str, ...] = ()
    evidence: tuple[Mapping[str, Any], ...] = ()
    suspected_causes: tuple[str, ...] = ()
    proven_causes: tuple[str, ...] = ()
    actions: tuple[Mapping[str, Any], ...] = ()
    effect_ambiguity: str = EffectAmbiguity.NONE.value
    recovery_criteria: tuple[str, ...] = ()
    owner_relevance: str = "material"
    repair_attempts: int = 0
    cost_usd: float = 0.0
    blocker: str | None = None
    last_event_id: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {item.value for item in _TERMINAL}

    def to_payload(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "correlation_key": self.correlation_key,
            "severity": self.severity,
            "status": self.status,
            "first_signal_at": self.first_signal_at,
            "last_signal_at": self.last_signal_at,
            "signal_kinds": list(self.signal_kinds),
            "affected_resources": list(self.affected_resources),
            "affected_capabilities": list(self.affected_capabilities),
            "affected_missions": list(self.affected_missions),
            "evidence": list(self.evidence),
            "suspected_causes": list(self.suspected_causes),
            "proven_causes": list(self.proven_causes),
            "actions": list(self.actions),
            "effect_ambiguity": self.effect_ambiguity,
            "recovery_criteria": list(self.recovery_criteria),
            "owner_relevance": self.owner_relevance,
            "repair_attempts": self.repair_attempts,
            "cost_usd": self.cost_usd,
            "blocker": self.blocker,
            "last_event_id": self.last_event_id,
        }


def _incident_id(signal: HealthSignal) -> str:
    raw = f"{signal.correlation_key}|{signal.signal_id}"
    return "inc-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _incident_from_state(state: Mapping[str, Any]) -> IncidentRecord:
    status = str(state.get("status") or IncidentStatus.UNKNOWN.value)
    if status not in {item.value for item in IncidentStatus}:
        status = IncidentStatus.UNKNOWN.value
    return IncidentRecord(
        incident_id=str(state["incident_id"]),
        correlation_key=str(state.get("correlation_key") or "unknown"),
        severity=str(state.get("severity") or IncidentSeverity.S3.value),
        status=status,
        first_signal_at=str(state.get("first_signal_at") or ""),
        last_signal_at=str(state.get("last_signal_at") or ""),
        signal_kinds=tuple(state.get("signal_kinds") or ()),
        affected_resources=tuple(state.get("affected_resources") or ()),
        affected_capabilities=tuple(state.get("affected_capabilities") or ()),
        affected_missions=tuple(state.get("affected_missions") or ()),
        evidence=tuple(state.get("evidence") or ()),
        suspected_causes=tuple(state.get("suspected_causes") or ()),
        proven_causes=tuple(state.get("proven_causes") or ()),
        actions=tuple(state.get("actions") or ()),
        effect_ambiguity=str(state.get("effect_ambiguity") or EffectAmbiguity.NONE.value),
        recovery_criteria=tuple(state.get("recovery_criteria") or ()),
        owner_relevance=str(state.get("owner_relevance") or "material"),
        repair_attempts=int(state.get("repair_attempts") or 0),
        cost_usd=float(state.get("cost_usd") or 0.0),
        blocker=state.get("blocker"),
        last_event_id=state.get("last_event_id"),
    )


def rebuild_incidents(database: FoundryDatabase) -> tuple[IncidentRecord, ...]:
    """Rebuild incidents from the append-only event stream."""
    connection = database.connect(readonly=True)
    try:
        rows = connection.execute(
            "SELECT * FROM events WHERE event_type LIKE 'incident.%' ORDER BY sequence"
        ).fetchall()
    finally:
        connection.close()
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        event = database._row_to_event(row)
        payload = event.payload
        incident_id = payload.get("incident_id")
        if not incident_id:
            continue
        incident_id = str(incident_id)
        state = states.setdefault(incident_id, {
            "incident_id": incident_id,
            "correlation_key": "unknown",
            "severity": IncidentSeverity.S3.value,
            "status": IncidentStatus.UNKNOWN.value,
            "first_signal_at": event.recorded_at,
            "last_signal_at": event.recorded_at,
            "signal_kinds": [],
            "affected_resources": [],
            "affected_capabilities": [],
            "affected_missions": [],
            "evidence": [],
            "suspected_causes": [],
            "proven_causes": [],
            "actions": [],
            "effect_ambiguity": EffectAmbiguity.NONE.value,
            "recovery_criteria": [],
            "owner_relevance": "material",
            "repair_attempts": 0,
            "cost_usd": 0.0,
            "blocker": None,
            "last_event_id": event.event_id,
        })
        state["last_event_id"] = event.event_id
        if event.event_type == "incident.detected":
            state.update({
                "correlation_key": str(payload.get("correlation_key") or "unknown"),
                "severity": str(payload.get("severity") or IncidentSeverity.S3.value),
                "status": IncidentStatus.DETECTED.value,
                "first_signal_at": str(payload.get("observed_at") or event.recorded_at),
                "last_signal_at": str(payload.get("observed_at") or event.recorded_at),
                "owner_relevance": str(payload.get("owner_relevance") or "material"),
            })
        if event.event_type in {"incident.detected", "incident.signal_observed"}:
            state["last_signal_at"] = str(payload.get("observed_at") or event.recorded_at)
            kind = payload.get("kind")
            if kind and kind not in state["signal_kinds"]:
                state["signal_kinds"].append(str(kind))
            for key, field_name in (
                ("resource_id", "affected_resources"),
                ("capability", "affected_capabilities"),
                ("mission_id", "affected_missions"),
            ):
                value = payload.get(key)
                if value and str(value) not in state[field_name]:
                    state[field_name].append(str(value))
            evidence = payload.get("evidence")
            if evidence and evidence not in state["evidence"]:
                state["evidence"].append(evidence)
            cause = payload.get("suspected_cause")
            if cause and str(cause) not in state["suspected_causes"]:
                state["suspected_causes"].append(str(cause))
            criteria = payload.get("recovery_criteria") or ()
            for criterion in criteria:
                if str(criterion) not in state["recovery_criteria"]:
                    state["recovery_criteria"].append(str(criterion))
            ambiguity = str(payload.get("effect_ambiguity") or EffectAmbiguity.NONE.value)
            if ambiguity != EffectAmbiguity.NONE.value:
                state["effect_ambiguity"] = ambiguity
            severity = str(payload.get("severity") or IncidentSeverity.S3.value)
            if severity in {item.value for item in IncidentSeverity}:
                rank = {"S1": 1, "S2": 2, "S3": 3, "S4": 4}
                if rank[severity] < rank.get(str(state["severity"]), 4):
                    state["severity"] = severity
        elif event.event_type == "incident.transitioned":
            state["status"] = str(payload.get("status") or IncidentStatus.UNKNOWN.value)
            evidence = payload.get("evidence")
            if evidence and evidence not in state["evidence"]:
                state["evidence"].append(evidence)
            if payload.get("proven_cause"):
                cause = str(payload["proven_cause"])
                if cause not in state["proven_causes"]:
                    state["proven_causes"].append(cause)
            if payload.get("reason"):
                state["blocker"] = (
                    str(payload["reason"])
                    if state["status"] in {
                        IncidentStatus.BLOCKED.value,
                        IncidentStatus.ESCALATED.value,
                        IncidentStatus.UNKNOWN.value,
                    } else state["blocker"]
                )
            if payload.get("effect_ambiguity"):
                state["effect_ambiguity"] = str(payload["effect_ambiguity"])
            state["repair_attempts"] = max(
                int(state["repair_attempts"]), int(payload.get("repair_attempt") or 0)
            )
        elif event.event_type == "incident.action_recorded":
            action = _safe_mapping(payload.get("action"))
            if action and action not in state["actions"]:
                state["actions"].append(action)
            if payload.get("repair_attempt"):
                state["repair_attempts"] = max(
                    int(state["repair_attempts"]), int(payload["repair_attempt"])
                )
            try:
                state["cost_usd"] += float(payload.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                pass
        elif event.event_type == "incident.cause_proven":
            cause = str(payload.get("cause") or "")
            if cause and cause not in state["proven_causes"]:
                state["proven_causes"].append(cause)
            evidence = payload.get("evidence")
            if evidence and evidence not in state["evidence"]:
                state["evidence"].append(evidence)
        elif event.event_type == "incident.effect_reconciled":
            state["effect_ambiguity"] = str(
                payload.get("effect_ambiguity") or EffectAmbiguity.RECONCILED.value
            )
            evidence = payload.get("evidence")
            if evidence and evidence not in state["evidence"]:
                state["evidence"].append(evidence)
    return tuple(_incident_from_state(states[key]) for key in sorted(states))


class IncidentStore:
    """Append and rebuild the durable incident lifecycle."""

    def __init__(self, database: FoundryDatabase, *, source_id: str = "v7-resilience") -> None:
        self.database = database
        self.source_id = source_id

    def _append(self, event_type: str, payload: dict[str, Any], key: str) -> None:
        self.database.append(Event.create(
            event_type,
            redact_payload(payload),
            source_type="supervisor",
            source_id=self.source_id,
            correlation_id=str(payload.get("incident_id") or self.source_id),
            idempotency_key=key,
        ))

    def list(self) -> tuple[IncidentRecord, ...]:
        return rebuild_incidents(self.database)

    def get(self, incident_id: str) -> IncidentRecord | None:
        return next((item for item in self.list() if item.incident_id == incident_id), None)

    def detect(self, signal: HealthSignal) -> IncidentRecord:
        existing_incident: str | None = None
        connection = self.database.connect(readonly=True)
        try:
            row = connection.execute(
                "SELECT payload_json FROM events WHERE event_type IN "
                "('incident.detected', 'incident.signal_observed') "
                "AND json_extract(payload_json, '$.signal_id') = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (signal.signal_id,),
            ).fetchone()
            if row is not None:
                existing_incident = str(json.loads(row[0]).get("incident_id") or "") or None
        finally:
            connection.close()
        if existing_incident:
            existing = self.get(existing_incident)
            if existing is not None:
                return existing
        active = next((item for item in self.list()
                       if item.correlation_key == signal.correlation_key and not item.terminal), None)
        if active is not None:
            payload = {"incident_id": active.incident_id, **signal.to_payload()}
            self._append(
                "incident.signal_observed", payload,
                f"incident.signal_observed:{active.incident_id}:{signal.signal_id}",
            )
            return self.get(active.incident_id) or active
        incident_id = _incident_id(signal)
        payload = {"incident_id": incident_id, **signal.to_payload()}
        self._append("incident.detected", payload, f"incident.detected:{incident_id}")
        return self.get(incident_id) or _incident_from_state({
            **payload, "status": IncidentStatus.DETECTED.value,
            "first_signal_at": signal.observed_at,
            "last_signal_at": signal.observed_at,
        })

    def transition(
        self,
        incident_id: str,
        status: IncidentStatus | str,
        *,
        reason: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        proven_cause: str | None = None,
        effect_ambiguity: EffectAmbiguity | str | None = None,
        repair_attempt: int = 0,
    ) -> IncidentRecord:
        current = self.get(incident_id)
        if current is None:
            raise IncidentLifecycleError(f"unknown incident: {incident_id}")
        target = IncidentStatus(str(status))
        if target == IncidentStatus(current.status):
            return current
        if target not in _TRANSITIONS[IncidentStatus(current.status)]:
            raise IncidentLifecycleError(
                f"invalid incident transition: {current.status} -> {target.value}"
            )
        ambiguity = str(effect_ambiguity) if effect_ambiguity is not None else None
        payload = {
            "incident_id": incident_id,
            "from": current.status,
            "status": target.value,
            "reason": _text(reason) if reason else None,
            "evidence": _safe_mapping(evidence),
            "proven_cause": _text(proven_cause) if proven_cause else None,
            "effect_ambiguity": ambiguity,
            "repair_attempt": max(0, int(repair_attempt)),
        }
        key_material = canonical_json(payload)
        self._append(
            "incident.transitioned", payload,
            "incident.transitioned:" + incident_id + ":" +
            hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24],
        )
        return self.get(incident_id) or current

    def record_action(
        self,
        incident_id: str,
        action: Mapping[str, Any],
        *,
        action_id: str | None = None,
        repair_attempt: int = 0,
        cost_usd: float = 0.0,
    ) -> IncidentRecord:
        current = self.get(incident_id)
        if current is None:
            raise IncidentLifecycleError(f"unknown incident: {incident_id}")
        safe_action = _safe_mapping(action)
        action_key = action_id or hashlib.sha256(
            canonical_json({"action": safe_action,
                            "repair_attempt": max(0, int(repair_attempt))}).encode("utf-8")
        ).hexdigest()[:24]
        self._append(
            "incident.action_recorded",
            {
                "incident_id": incident_id,
                "action_id": action_key,
                "action": safe_action,
                "repair_attempt": max(0, int(repair_attempt)),
                "cost_usd": max(0.0, float(cost_usd)),
            },
            f"incident.action_recorded:{incident_id}:{action_key}",
        )
        return self.get(incident_id) or current

    def prove_cause(self, incident_id: str, cause: str, *, evidence: Mapping[str, Any]) -> IncidentRecord:
        current = self.get(incident_id)
        if current is None:
            raise IncidentLifecycleError(f"unknown incident: {incident_id}")
        self._append(
            "incident.cause_proven",
            {"incident_id": incident_id, "cause": _text(cause), "evidence": _safe_mapping(evidence)},
            f"incident.cause_proven:{incident_id}:{hashlib.sha256(_text(cause).encode()).hexdigest()[:16]}",
        )
        return self.get(incident_id) or current

    def reconcile_effect(self, incident_id: str, *, classification: EffectAmbiguity | str,
                         evidence: Mapping[str, Any]) -> IncidentRecord:
        current = self.get(incident_id)
        if current is None:
            raise IncidentLifecycleError(f"unknown incident: {incident_id}")
        value = str(classification)
        if value not in {EffectAmbiguity.RECONCILED.value, EffectAmbiguity.MISMATCH.value}:
            raise IncidentLifecycleError(
                "effect reconciliation must prove RECONCILED or record MISMATCH"
            )
        safe_evidence = _safe_mapping(evidence)
        if not safe_evidence:
            raise IncidentLifecycleError("effect reconciliation requires evidence")
        self._append(
            "incident.effect_reconciled",
            {"incident_id": incident_id, "effect_ambiguity": value,
             "evidence": safe_evidence},
             f"incident.effect_reconciled:{incident_id}:{hashlib.sha256(canonical_json(safe_evidence).encode()).hexdigest()[:20]}",
        )
        return self.get(incident_id) or current


@dataclass(frozen=True, slots=True)
class Anomaly:
    anomaly_id: str
    kind: str
    severity: str
    correlation_key: str
    observed_at: str
    evidence: Mapping[str, Any]
    resource_id: str | None = None
    connector_id: str | None = None
    capability: str | None = None
    mission_id: str | None = None
    effect_action_key: str | None = None
    effect_ambiguity: str = EffectAmbiguity.NONE.value
    owner_relevance: str = "material"

    def to_signal(self) -> HealthSignal:
        return HealthSignal(
            signal_id=self.anomaly_id,
            kind=self.kind,
            severity=self.severity,
            observed_at=self.observed_at,
            correlation_key=self.correlation_key,
            resource_id=self.resource_id,
            connector_id=self.connector_id,
            capability=self.capability,
            mission_id=self.mission_id,
            effect_action_key=self.effect_action_key,
            evidence=self.evidence,
            effect_ambiguity=self.effect_ambiguity,
            owner_relevance=self.owner_relevance,
        )


@dataclass(frozen=True, slots=True)
class AnomalyThresholds:
    heartbeat_stale_seconds: float = 120.0
    worker_failures: int = 3
    provider_failures: int = 3
    connector_failures: int = 3
    unknown_effects: int = 1
    database_errors: int = 1
    release_failures: int = 1
    canary_failures: int = 2
    repair_failures: int = 2
    command_center_stale_seconds: float = 300.0
    task_count: int = 100
    process_count: int = 100
    memory_bytes: int = 2_000_000_000
    cost_usd: float = 25.0
    retry_cost_usd: float = 10.0
    lease_churn: int = 2
    window_seconds: float = 900.0


class AnomalyDetector:
    """Deterministic rules over durable events and supplied metrics."""

    def __init__(self, thresholds: AnomalyThresholds | None = None) -> None:
        self.thresholds = thresholds or AnomalyThresholds()

    @staticmethod
    def _events(database: FoundryDatabase, limit: int = 5000) -> list[Event]:
        connection = database.connect(readonly=True)
        try:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY sequence DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        finally:
            connection.close()
        return [database._row_to_event(row) for row in reversed(rows)]

    @staticmethod
    def _count(events: Iterable[Event], predicate: Callable[[Event], bool]) -> int:
        return sum(1 for event in events if predicate(event))

    @staticmethod
    def _metric(metrics: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in metrics:
                return metrics[key]
        return None

    @staticmethod
    def _anomaly(kind: AnomalyKind | str, *, severity: IncidentSeverity | str,
                 observed_at: str, correlation_key: str,
                 evidence: Mapping[str, Any], **scope: Any) -> Anomaly:
        kind_value = str(kind)
        safe = _safe_mapping(evidence)
        identity = canonical_json({"kind": kind_value, "correlation": correlation_key,
                                   "evidence": safe, **scope})
        anomaly_id = "an-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return Anomaly(
            anomaly_id=anomaly_id,
            kind=kind_value,
            severity=str(severity),
            correlation_key=correlation_key,
            observed_at=observed_at,
            evidence=safe,
            **scope,
        )

    def detect(
        self,
        database: FoundryDatabase,
        *,
        now: str | None = None,
        metrics: Mapping[str, Any] | None = None,
    ) -> tuple[Anomaly, ...]:
        observed_at = now or isoformat()
        metrics = metrics or {}
        events = self._events(database)
        anomalies: list[Anomaly] = []
        try:
            now_value = parse(observed_at)
        except (TypeError, ValueError, OverflowError):
            now_value = parse(isoformat())
        def in_window(event: Event) -> bool:
            try:
                age = (now_value - parse(event.recorded_at)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return False
            return 0.0 <= age <= self.thresholds.window_seconds

        recent_events = [event for event in events if in_window(event)]

        heartbeat_age = self._metric(metrics, "heartbeat_age_seconds")
        if heartbeat_age is None:
            heartbeats = [event for event in events
                          if event.event_type in {"supervisor.started", "supervisor.heartbeat"}]
            if heartbeats:
                try:
                    heartbeat_age = max(
                        0.0, (now_value - parse(heartbeats[-1].recorded_at)).total_seconds()
                    )
                except (TypeError, ValueError, OverflowError):
                    heartbeat_age = None
        if heartbeat_age is not None and float(heartbeat_age) > self.thresholds.heartbeat_stale_seconds:
            anomalies.append(self._anomaly(
                AnomalyKind.SUPERVISOR_HEARTBEAT_MISSING,
                severity=IncidentSeverity.S2,
                observed_at=observed_at,
                correlation_key="supervisor",
                evidence={"heartbeat_age_seconds": float(heartbeat_age),
                          "threshold_seconds": self.thresholds.heartbeat_stale_seconds},
                capability="supervisor",
            ))

        if bool(self._metric(metrics, "duplicate_authority")):
            anomalies.append(self._anomaly(
                AnomalyKind.DUPLICATE_AUTHORITY, severity=IncidentSeverity.S1,
                observed_at=observed_at, correlation_key="supervisor",
                evidence={"duplicate_authority": True}, capability="supervisor",
            ))

        worker_failures = self._metric(metrics, "worker_failures")
        if worker_failures is None:
            worker_failures = self._count(
                recent_events,
                lambda event: event.event_type == "worker.crashed"
                or (event.event_type == "agent_run.failed"
                    and event.payload.get("failure_class") != "provider_unavailable"),
            )
        if int(worker_failures or 0) >= self.thresholds.worker_failures:
            run_id = self._metric(metrics, "worker_id", "run_id")
            anomalies.append(self._anomaly(
                AnomalyKind.WORKER_CRASH_LOOP, severity=IncidentSeverity.S3,
                observed_at=observed_at, correlation_key=f"worker:{run_id or 'all'}",
                evidence={"failure_count": int(worker_failures),
                          "threshold": self.thresholds.worker_failures},
                capability=self._metric(metrics, "capability"),
                resource_id=self._metric(metrics, "resource_id"),
                mission_id=self._metric(metrics, "mission_id"),
            ))

        provider_failures = self._metric(metrics, "provider_failures")
        if provider_failures is None:
            provider_failures = self._count(
                recent_events,
                lambda event: (
                    ("provider" in event.event_type
                     and any(token in event.event_type for token in
                             ("failed", "timeout", "unavailable")))
                    or (event.event_type == "agent_run.failed"
                        and event.payload.get("failure_class") == "provider_unavailable")
                ),
            )
        if int(provider_failures or 0) >= self.thresholds.provider_failures:
            provider = str(self._metric(metrics, "provider") or "all")
            anomalies.append(self._anomaly(
                AnomalyKind.PROVIDER_OUTAGE, severity=IncidentSeverity.S3,
                observed_at=observed_at, correlation_key=f"provider:{provider}",
                evidence={"failure_count": int(provider_failures),
                          "threshold": self.thresholds.provider_failures},
                capability="provider",
            ))

        connector_failures = self._metric(metrics, "connector_failures")
        if connector_failures is None:
            connector_failures = self._count(
                recent_events,
                lambda event: (
                    ("connector" in event.event_type
                     and any(token in event.event_type for token in
                             ("failed", "timeout", "unavailable")))
                    or (event.event_type == "system.probe_recorded"
                        and any(value in {"FAILED", "UNREADY"}
                                for value in event.payload.get("results", {}).values()))
                ),
            )
        if int(connector_failures or 0) >= self.thresholds.connector_failures:
            connector = str(self._metric(metrics, "connector_id", "connector") or "all")
            anomalies.append(self._anomaly(
                AnomalyKind.CONNECTOR_OUTAGE, severity=IncidentSeverity.S3,
                observed_at=observed_at, correlation_key=f"connector:{connector}",
                evidence={"failure_count": int(connector_failures),
                          "threshold": self.thresholds.connector_failures},
                connector_id=connector,
            ))

        # Effect ambiguity is durable state, not a recent-window metric. Query
        # the complete action journal event subset so an old STARTED effect
        # cannot disappear merely because the ledger has grown past the
        # bounded anomaly-event sample.
        unknown_effect_keys = self._unknown_effect_keys(self._effect_events(database))
        metric_unknown = self._metric(metrics, "unknown_effects")
        if metric_unknown is not None and int(metric_unknown) > len(unknown_effect_keys):
            unknown_effect_keys.extend([None] * (int(metric_unknown) - len(unknown_effect_keys)))
        if len(unknown_effect_keys) >= self.thresholds.unknown_effects:
            for action_key in unknown_effect_keys[:20]:
                key = str(action_key or "unknown")
                anomalies.append(self._anomaly(
                    AnomalyKind.EFFECT_UNKNOWN, severity=IncidentSeverity.S2,
                    observed_at=observed_at, correlation_key=f"effect:{key}",
                    evidence={"action_key": action_key, "classification": EffectAmbiguity.UNKNOWN.value},
                    effect_action_key=action_key,
                    effect_ambiguity=EffectAmbiguity.UNKNOWN.value,
                ))

        db_errors = self._metric(metrics, "database_errors")
        if db_errors is None:
            db_errors = self._count(
                recent_events,
                lambda event: event.event_type in {
                    "database.error", "database.locked", "migration.failed", "db.error",
                },
            )
        if int(db_errors or 0) >= self.thresholds.database_errors:
            anomalies.append(self._anomaly(
                AnomalyKind.DATABASE_FAULT, severity=IncidentSeverity.S2,
                observed_at=observed_at, correlation_key="database",
                evidence={"error_count": int(db_errors)}, capability="database",
            ))

        release_failures = self._metric(metrics, "release_failures", "release_degraded")
        if release_failures is None:
            release_failures = self._count(
                recent_events,
                lambda event: event.event_type in {
                    "release.degraded", "release.health_failed", "release.rollback_requested",
                },
            )
        if bool(self._metric(metrics, "release_degraded")):
            release_failures = max(int(release_failures or 0), 1)
        if int(release_failures or 0) >= self.thresholds.release_failures:
            release_id = str(self._metric(metrics, "release_id") or "current")
            anomalies.append(self._anomaly(
                AnomalyKind.RELEASE_DEGRADATION, severity=IncidentSeverity.S2,
                observed_at=observed_at, correlation_key=f"release:{release_id}",
                evidence={"failure_count": int(release_failures),
                          "threshold": self.thresholds.release_failures,
                          "release_id": release_id},
                capability="release",
            ))

        canary_failures = self._metric(metrics, "canary_failures")
        if canary_failures is None:
            canary_failures = self._count(
                recent_events,
                lambda event: event.event_type == "canary.tick"
                and not bool(event.payload.get("healthy")),
            )
        if int(canary_failures or 0) >= self.thresholds.canary_failures:
            canary_id = str(self._metric(metrics, "canary_id") or "all")
            anomalies.append(self._anomaly(
                AnomalyKind.CANARY_DEGRADATION, severity=IncidentSeverity.S2,
                observed_at=observed_at, correlation_key=f"canary:{canary_id}",
                evidence={"failure_count": int(canary_failures)}, capability="release",
            ))

        command_age = self._metric(metrics, "command_center_age_seconds", "snapshot_age_seconds")
        if command_age is not None and float(command_age) > self.thresholds.command_center_stale_seconds:
            anomalies.append(self._anomaly(
                AnomalyKind.COMMAND_CENTER_STALE, severity=IncidentSeverity.S4,
                observed_at=observed_at, correlation_key="command-center",
                evidence={"age_seconds": float(command_age),
                          "threshold_seconds": self.thresholds.command_center_stale_seconds},
                capability="command-center",
            ))

        repair_failures = self._metric(metrics, "repair_failures")
        if repair_failures is None:
            repair_failures = self._count(
                recent_events,
                lambda event: event.event_type == "incident.action_recorded"
                and event.payload.get("action", {}).get("kind") == "repair_failed",
            )
        if int(repair_failures or 0) >= self.thresholds.repair_failures:
            anomalies.append(self._anomaly(
                AnomalyKind.REPAIR_FAILURE_LOOP, severity=IncidentSeverity.S2,
                observed_at=observed_at, correlation_key="repair:all",
                evidence={"failure_count": int(repair_failures)}, capability="repair",
            ))

        lease_churn = self._metric(metrics, "lease_churn")
        if lease_churn is None:
            lease_churn = self._count(
                recent_events,
                lambda event: event.event_type in {
                    "runtime.control.lease_reclaimed", "lease.reclaimed",
                },
            )
        if int(lease_churn or 0) >= self.thresholds.lease_churn:
            anomalies.append(self._anomaly(
                AnomalyKind.DUPLICATE_AUTHORITY, severity=IncidentSeverity.S1,
                observed_at=observed_at, correlation_key="supervisor",
                evidence={"lease_churn": int(lease_churn),
                          "threshold": self.thresholds.lease_churn}, capability="supervisor",
            ))

        task_count = self._metric(metrics, "task_count")
        process_count = self._metric(metrics, "process_count")
        memory_bytes = self._metric(metrics, "memory_bytes")
        if (
            (task_count is not None and int(task_count) > self.thresholds.task_count)
            or (process_count is not None and int(process_count) > self.thresholds.process_count)
            or (memory_bytes is not None and int(memory_bytes) > self.thresholds.memory_bytes)
        ):
            anomalies.append(self._anomaly(
                AnomalyKind.RESOURCE_ANOMALY, severity=IncidentSeverity.S2,
                observed_at=observed_at, correlation_key="resources",
                evidence={"task_count": task_count, "process_count": process_count,
                          "memory_bytes": memory_bytes}, capability="resources",
            ))

        cost_usd = self._metric(metrics, "cost_usd", "provider_cost_usd")
        retry_cost_usd = self._metric(metrics, "retry_cost_usd")
        if (
            (cost_usd is not None and float(cost_usd) > self.thresholds.cost_usd)
            or (retry_cost_usd is not None and float(retry_cost_usd) > self.thresholds.retry_cost_usd)
        ):
            anomalies.append(self._anomaly(
                AnomalyKind.COST_ANOMALY, severity=IncidentSeverity.S2,
                observed_at=observed_at, correlation_key="cost",
                evidence={"cost_usd": cost_usd, "retry_cost_usd": retry_cost_usd},
                capability="provider",
            ))
        unique: dict[str, Anomaly] = {}
        for anomaly in anomalies:
            unique.setdefault(anomaly.anomaly_id, anomaly)
        return tuple(sorted(unique.values(), key=lambda item: (item.severity, item.kind, item.anomaly_id)))

    @staticmethod
    def _unknown_effect_keys(events: Iterable[Event]) -> list[str]:
        started: dict[str, Event] = {}
        terminal = {"system.action_completed", "system.action_failed", "effect.frozen"}
        for event in events:
            action_key = event.payload.get("action_key")
            if not action_key:
                continue
            action_key = str(action_key)
            if event.event_type == "system.action_started":
                started[action_key] = event
            elif event.event_type in terminal:
                started.pop(action_key, None)
        return sorted(started)

    @staticmethod
    def _effect_events(database: FoundryDatabase) -> list[Event]:
        connection = database.connect(readonly=True)
        try:
            rows = connection.execute(
                "SELECT * FROM events WHERE event_type IN "
                "('system.action_started', 'system.action_completed', "
                "'system.action_failed', 'effect.frozen') ORDER BY sequence"
            ).fetchall()
        finally:
            connection.close()
        return [database._row_to_event(row) for row in rows]


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    resource_id: str
    capability: str
    connector_id: str | None = None
    mission_ids: tuple[str, ...] = ()

    @property
    def node_id(self) -> str:
        return f"capability:{self.resource_id}:{self.capability}"


class DependencyGraph:
    """Small downstream dependency graph used for scoped containment."""

    def __init__(self) -> None:
        self._edges: dict[str, set[str]] = defaultdict(set)
        self._bindings: dict[str, CapabilityBinding] = {}

    @staticmethod
    def _node(kind: str, value: str) -> str:
        value = str(value)
        return value if value.startswith(f"{kind}:") else f"{kind}:{value}"

    def add_edge(self, upstream: str, downstream: str) -> None:
        self._edges[str(upstream)].add(str(downstream))

    def add_binding(self, binding: CapabilityBinding) -> None:
        self._bindings[binding.node_id] = binding
        resource = self._node("resource", binding.resource_id)
        capability = binding.node_id
        self.add_edge(resource, capability)
        if binding.connector_id:
            connector = self._node("connector", binding.connector_id)
            self.add_edge(connector, capability)
        for mission_id in binding.mission_ids:
            self.add_edge(capability, self._node("mission", mission_id))

    def bind(self, resource_id: str, capability: str, *, connector_id: str | None = None,
             mission_ids: Iterable[str] = ()) -> CapabilityBinding:
        binding = CapabilityBinding(
            resource_id=str(resource_id), capability=str(capability),
            connector_id=str(connector_id) if connector_id else None,
            mission_ids=tuple(str(item) for item in mission_ids),
        )
        self.add_binding(binding)
        return binding

    def impacted(self, failed_node: str) -> tuple[CapabilityBinding, ...]:
        starts = {str(failed_node)}
        for kind in ("resource", "connector", "capability", "mission", "worker", "release"):
            starts.add(self._node(kind, str(failed_node)))
        queue = deque(starts)
        seen: set[str] = set()
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            queue.extend(sorted(self._edges.get(node, ())))
        bindings = [binding for node, binding in self._bindings.items() if node in seen]
        if not bindings:
            raw = str(failed_node)
            bindings = [binding for binding in self._bindings.values()
                        if binding.capability == raw or binding.resource_id == raw
                        or binding.connector_id == raw]
        return tuple(sorted(bindings, key=lambda item: (item.resource_id, item.capability)))


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    name: str
    status: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    mutating: bool = False


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    incident_id: str
    results: tuple[DiagnosticResult, ...]
    observed_at: str

    @property
    def all_observed(self) -> bool:
        return bool(self.results) and all(result.status in {"PASS", "NOT_CONFIGURED"}
                                          for result in self.results)

    def to_payload(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "observed_at": self.observed_at,
            "results": [{"name": item.name, "status": item.status,
                          "evidence": dict(item.evidence), "mutating": item.mutating}
                         for item in self.results],
        }


class DiagnosticPlaybook:
    """Read-only checks. Repair callbacks run only after this report exists."""

    def run(
        self,
        database: FoundryDatabase,
        incident: IncidentRecord,
        *,
        stop_path: str | Path | None = None,
        release_root: str | Path | None = None,
        foundry_home: str | Path | None = None,
        effect_action_key: str | None = None,
    ) -> DiagnosticReport:
        results: list[DiagnosticResult] = []
        try:
            quick = database.quick_check(readonly=True)
            results.append(DiagnosticResult("database_integrity", "PASS" if quick == "ok" else "FAIL",
                                            {"quick_check": quick}))
        except Exception as error:  # noqa: BLE001 - diagnostic boundary must report
            results.append(DiagnosticResult("database_integrity", "FAIL",
                                            {"error_class": type(error).__name__, "error": _text(error)}))
        try:
            from ..reconcile import reconcile
            report = reconcile(database, readonly=True)
            results.append(DiagnosticResult(
                "projection_reconciliation", "PASS" if report.consistent else "FAIL",
                {"consistent": report.consistent, "mismatches": len(report.mismatches),
                 "integrity_failures": len(report.integrity_failures)},
            ))
        except Exception as error:  # noqa: BLE001
            results.append(DiagnosticResult("projection_reconciliation", "FAIL",
                                            {"error_class": type(error).__name__, "error": _text(error)}))
        try:
            from ..supervisor.control import inspect_control
            state = inspect_control(database, stop_path=str(stop_path) if stop_path else None)
            results.append(DiagnosticResult(
                "process_identity", "PASS" if state.status in {"RUNNING", "STOPPED", "BLOCKED"} else "UNKNOWN",
                {"status": state.status, "instance_id": state.instance_id,
                 "pid": state.pid, "lease_owner": state.lease_owner},
            ))
        except Exception as error:  # noqa: BLE001
            results.append(DiagnosticResult("process_identity", "UNKNOWN",
                                            {"error_class": type(error).__name__}))
        try:
            from ..evolution.scheduler import scheduler_state
            results.append(DiagnosticResult("scheduler", "PASS", scheduler_state(database)))
        except Exception as error:  # noqa: BLE001
            results.append(DiagnosticResult("scheduler", "UNKNOWN",
                                            {"error_class": type(error).__name__}))
        try:
            with database.connect(readonly=True) as connection:
                rows = connection.execute(
                    "SELECT event_type, sequence FROM events ORDER BY sequence DESC LIMIT 40"
                ).fetchall()
            results.append(DiagnosticResult(
                "evidence_log", "PASS",
                {"recent_event_count": len(rows),
                 "event_types": [str(row[0]) for row in reversed(rows)]},
            ))
        except Exception as error:  # noqa: BLE001
            results.append(DiagnosticResult("evidence_log", "FAIL",
                                            {"error_class": type(error).__name__}))
        if release_root is None and foundry_home is None:
            results.append(DiagnosticResult("release", "NOT_CONFIGURED"))
        else:
            try:
                from ..deployment.release import release_authority_report
                report = release_authority_report(
                    root=Path(release_root) if release_root else None,
                    foundry_home=Path(foundry_home) if foundry_home else None,
                )
                results.append(DiagnosticResult(
                    "release", "PASS" if report.get("status") in {"CONSISTENT", "FILESYSTEM_ONLY_AUTHORITY"}
                    else "FAIL", report,
                ))
            except Exception as error:  # noqa: BLE001
                results.append(DiagnosticResult("release", "FAIL",
                                                {"error_class": type(error).__name__, "error": _text(error)}))
        if effect_action_key:
            try:
                from ..effects import effect_state
                state = effect_state(database, effect_action_key)
                results.append(DiagnosticResult(
                    "effect_reconciliation", "PASS" if state.get("status") in {"COMPLETED", "FAILED", "FROZEN"}
                    else "UNKNOWN", state,
                ))
            except Exception as error:  # noqa: BLE001
                results.append(DiagnosticResult("effect_reconciliation", "UNKNOWN",
                                                {"error_class": type(error).__name__}))
        return DiagnosticReport(incident.incident_id, tuple(results), isoformat())


@dataclass(frozen=True, slots=True)
class RepairResult:
    succeeded: bool
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    effect_ambiguity: str = EffectAmbiguity.NONE.value
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class ResilienceRun:
    observed_at: str
    anomalies: tuple[Anomaly, ...]
    incidents: tuple[IncidentRecord, ...]
    recovered_ids: tuple[str, ...] = ()
    blocked_ids: tuple[str, ...] = ()
    errors: tuple[Mapping[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "anomalies": [dict(item.evidence, anomaly_id=item.anomaly_id, kind=item.kind)
                          for item in self.anomalies],
            "incidents": [item.to_payload() for item in self.incidents],
            "recovered_ids": list(self.recovered_ids),
            "blocked_ids": list(self.blocked_ids),
            "errors": list(self.errors),
        }


def _current_capability_state(database: FoundryDatabase, resource_id: str,
                              capability: str) -> str | None:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT capabilities_json FROM resources WHERE resource_id = ?", (resource_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        capabilities = json.loads(str(row[0]))
    except (TypeError, ValueError):
        return None
    value = capabilities.get(capability)
    return str(value) if value is not None else None


def _latest_capability_change(database: FoundryDatabase, resource_id: str,
                              capability: str) -> Event | None:
    """Return the durable owner of the latest projected capability state."""
    connection = database.connect(readonly=True)
    try:
        row = connection.execute(
            "SELECT * FROM events WHERE event_type = 'capability.state_changed' "
            "AND json_extract(payload_json, '$.resource_id') = ? "
            "AND json_extract(payload_json, '$.capability') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (resource_id, capability),
        ).fetchone()
    finally:
        connection.close()
    return None if row is None else database._row_to_event(row)


def health_truth(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return component health without a fabricated healthy default."""
    metrics = metrics or {}
    result: dict[str, Any] = {"status": "UNKNOWN", "components": {}}
    try:
        result["components"]["database"] = {"status": database.quick_check(readonly=True)}
    except Exception as error:  # noqa: BLE001
        result["components"]["database"] = {"status": "UNKNOWN", "error_class": type(error).__name__}
    try:
        from ..reconcile import reconcile
        report = reconcile(database, readonly=True)
        result["components"]["ledger"] = {
            "status": "PASS" if report.consistent else "DEGRADED",
            "mismatches": len(report.mismatches),
            "integrity_failures": len(report.integrity_failures),
        }
    except Exception as error:  # noqa: BLE001
        result["components"]["ledger"] = {"status": "UNKNOWN", "error_class": type(error).__name__}
    try:
        from ..supervisor.control import inspect_control
        state = inspect_control(database, stop_path=str(stop_path) if stop_path else None)
        result["components"]["supervisor"] = state.to_json()
    except Exception as error:  # noqa: BLE001
        result["components"]["supervisor"] = {"status": "UNKNOWN", "error_class": type(error).__name__}
    try:
        with database.connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT resource_id, capabilities_json FROM resources ORDER BY resource_id"
            ).fetchall()
        capability_components: dict[str, Any] = {}
        healthy_states = {"READY", "ACTIVE"}
        degraded_states = {"DEGRADED", "STOPPED", "VETOED"}
        for row in rows:
            resource_id = str(row[0])
            states = json.loads(str(row[1]))
            details: dict[str, Any] = {}
            statuses: list[str] = []
            for capability, state in states.items():
                state_value = str(state)
                if state_value in healthy_states:
                    capability_status = "HEALTHY"
                elif state_value in degraded_states:
                    capability_status = "DEGRADED"
                elif state_value in {"DISCOVERED", "PROBING"}:
                    capability_status = "NOT_READY"
                else:
                    capability_status = "UNKNOWN"
                details[str(capability)] = {
                    "state": state_value,
                    "status": capability_status,
                }
                statuses.append(capability_status)
            aggregate = "UNKNOWN"
            if statuses and all(status == "HEALTHY" for status in statuses):
                aggregate = "HEALTHY"
            elif any(status == "DEGRADED" for status in statuses):
                aggregate = "DEGRADED"
            elif any(status == "NOT_READY" for status in statuses):
                aggregate = "NOT_READY"
            capability_components[resource_id] = {
                "status": aggregate,
                "capabilities": details,
                # Preserve the compact state map for callers that only need
                # the projected state, while exposing an honest status beside
                # each capability for health decisions.
                "states": {str(key): str(value) for key, value in states.items()},
            }
        resource_statuses = [str(item.get("status"))
                             for item in capability_components.values()]
        capability_status = "UNKNOWN"
        if resource_statuses and all(status == "HEALTHY" for status in resource_statuses):
            capability_status = "HEALTHY"
        elif any(status == "DEGRADED" for status in resource_statuses):
            capability_status = "DEGRADED"
        elif any(status == "NOT_READY" for status in resource_statuses):
            capability_status = "NOT_READY"
        result["components"]["capabilities"] = {
            "status": capability_status,
            "resources": capability_components,
        }
    except Exception as error:  # noqa: BLE001
        result["components"]["capabilities"] = {"status": "UNKNOWN", "error_class": type(error).__name__}
    result["components"]["metrics"] = _safe_mapping(metrics)
    statuses = [str(value.get("status")) for value in result["components"].values()
                if isinstance(value, dict) and "status" in value]
    if any(status in {"DEGRADED", "FAIL", "UNKNOWN"} for status in statuses):
        result["status"] = "DEGRADED" if any(status == "DEGRADED" for status in statuses) else "UNKNOWN"
    elif statuses and all(status in {"ok", "PASS", "RUNNING", "STOPPED", "BLOCKED"} for status in statuses):
        result["status"] = "HEALTHY"
    return result


class ResilienceCoordinator:
    """Detect, contain, diagnose, repair, and verify bounded incidents."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        stop_path: str | Path | None = None,
        graph: DependencyGraph | None = None,
        detector: AnomalyDetector | None = None,
        playbook: DiagnosticPlaybook | None = None,
        repair_handlers: Mapping[str, Callable[[IncidentRecord, DiagnosticReport], Any]] | None = None,
        health_probe: Callable[[IncidentRecord, DiagnosticReport], Any] | None = None,
        max_repair_attempts: int = 2,
        source_id: str = "v7-resilience",
    ) -> None:
        self.database = database
        self.stop_path = Path(stop_path).expanduser().resolve() if stop_path else None
        self.graph = graph or DependencyGraph()
        self.detector = detector or AnomalyDetector()
        self.playbook = playbook or DiagnosticPlaybook()
        self.repair_handlers = dict(repair_handlers or {})
        self.health_probe = health_probe
        self.max_repair_attempts = max(1, int(max_repair_attempts))
        self.store = IncidentStore(database, source_id=source_id)

    def _stop_active(self) -> bool:
        if self.stop_path is not None and self.stop_path.exists():
            return True
        try:
            from ..supervisor.estop import EmergencyStop
            return EmergencyStop(self.stop_path or self.database.path.parent / "EMERGENCY_STOP").active()
        except OSError:
            return False

    def _scope(self, anomaly: Anomaly) -> tuple[CapabilityBinding, ...]:
        bindings: list[CapabilityBinding] = []
        if anomaly.resource_id and anomaly.capability:
            bindings.append(CapabilityBinding(anomaly.resource_id, anomaly.capability,
                                               anomaly.connector_id,
                                               (anomaly.mission_id,) if anomaly.mission_id else ()))
        for value in (anomaly.resource_id, anomaly.connector_id, anomaly.capability,
                      anomaly.mission_id):
            if value:
                bindings.extend(self.graph.impacted(value))
        unique = {item.node_id: item for item in bindings}
        return tuple(sorted(unique.values(), key=lambda item: (item.resource_id, item.capability)))

    def _contain(self, incident: IncidentRecord, anomaly: Anomaly) -> IncidentRecord:
        incident = self.store.transition(incident.incident_id, IncidentStatus.CONTAINING,
                                         reason="freeze affected capability before diagnosis")
        if self._stop_active():
            self.store.record_action(incident.incident_id, {
                "kind": "stop_observed", "status": "PASS",
                "scope": "global", "reason": "STOP remains stronger than automation",
            })
        if anomaly.kind == AnomalyKind.RELEASE_DEGRADATION.value:
            if self._stop_active():
                self.store.record_action(incident.incident_id, {
                    "kind": "release_freeze", "status": "BLOCKED",
                    "reason": "STOP is active; release control remains owner-frozen",
                })
            else:
                try:
                    from ..evolution.promote import freeze_active
                    from ..supervisor.evolution_control import SUPERVISOR_CONTROLLER
                    freeze_active(
                        self.database,
                        reason="resilience containment before release rollback",
                        controller=SUPERVISOR_CONTROLLER,
                    )
                    self.store.record_action(incident.incident_id, {
                        "kind": "release_freeze", "status": "PASS",
                        "reason": "release freeze precedes any rollback callback",
                    })
                except Exception as error:  # noqa: BLE001 — diagnosis remains honest
                    self.store.record_action(incident.incident_id, {
                        "kind": "release_freeze", "status": "BLOCKED",
                        "error_class": type(error).__name__, "error": _text(error),
                    })
        for binding in self._scope(anomaly):
            previous = _current_capability_state(
                self.database, binding.resource_id, binding.capability)
            if previous is None:
                self.store.record_action(incident.incident_id, {
                    "kind": "capability_suspended", "status": "BLOCKED",
                    "resource_id": binding.resource_id, "capability": binding.capability,
                    "reason": "resource or capability projection is unavailable",
                })
                continue
            if previous in {"VETOED", "STOPPED"}:
                self.store.record_action(incident.incident_id, {
                    "kind": "capability_suspended", "status": "PASS",
                    "resource_id": binding.resource_id, "capability": binding.capability,
                    "previous_state": previous, "state": previous, "owned": False,
                })
                continue
            try:
                set_capability_state(
                    self.database, binding.resource_id, binding.capability, "STOPPED",
                    source_id=f"incident:{incident.incident_id}",
                    evidence={"incident_id": incident.incident_id,
                              "reason": "incident containment", "previous_state": previous},
                )
                self.store.record_action(incident.incident_id, {
                    "kind": "capability_suspended", "status": "PASS",
                    "resource_id": binding.resource_id, "capability": binding.capability,
                    "previous_state": previous, "state": "STOPPED", "owned": True,
                })
            except Exception as error:  # noqa: BLE001
                self.store.record_action(incident.incident_id, {
                    "kind": "capability_suspended", "status": "BLOCKED",
                    "resource_id": binding.resource_id, "capability": binding.capability,
                    "error_class": type(error).__name__, "error": _text(error),
                })
        if anomaly.effect_action_key:
            try:
                from ..effects import EffectStateError, effect_state, freeze_effect
                state = effect_state(self.database, anomaly.effect_action_key)
                if state.get("status") == "STARTED":
                    freeze_effect(self.database, anomaly.effect_action_key,
                                  reason="incident containment: external effect is ambiguous")
                    self.store.record_action(incident.incident_id, {
                        "kind": "effect_frozen", "status": "PASS",
                        "action_key": anomaly.effect_action_key,
                    })
                else:
                    self.store.record_action(incident.incident_id, {
                        "kind": "effect_frozen", "status": "UNKNOWN",
                        "action_key": anomaly.effect_action_key,
                        "observed_state": state.get("status"),
                    })
            except Exception as error:  # noqa: BLE001
                self.store.record_action(incident.incident_id, {
                    "kind": "effect_frozen", "status": "BLOCKED",
                    "action_key": anomaly.effect_action_key,
                    "error_class": type(error).__name__, "error": _text(error),
                })
        return self.store.transition(
            incident.incident_id, IncidentStatus.CONTAINED,
            reason="affected capability and ambiguous effects are frozen",
            effect_ambiguity=(anomaly.effect_ambiguity
                              if anomaly.effect_ambiguity != EffectAmbiguity.NONE.value else None),
        )

    def _diagnose(self, incident: IncidentRecord, anomaly: Anomaly) -> DiagnosticReport:
        report = self.playbook.run(
            self.database, incident, stop_path=self.stop_path,
            effect_action_key=anomaly.effect_action_key,
        )
        self.store.transition(incident.incident_id, IncidentStatus.DIAGNOSING,
                              reason="read-only diagnostics completed",
                              evidence=report.to_payload())
        self.store.record_action(incident.incident_id, {
            "kind": "diagnostics", "status": "PASS", "report": report.to_payload(),
        })
        return report

    @staticmethod
    def _repair_result(value: Any) -> RepairResult:
        if isinstance(value, RepairResult):
            return value
        if isinstance(value, bool):
            return RepairResult(value, "repair callback returned boolean")
        if isinstance(value, Mapping):
            return RepairResult(
                bool(value.get("succeeded" if "succeeded" in value else "ok")),
                _text(value.get("reason") or value.get("message") or "repair callback result"),
                _safe_mapping(value.get("evidence") if isinstance(value.get("evidence"), Mapping) else {}),
                str(value.get("effect_ambiguity") or EffectAmbiguity.NONE.value),
                float(value.get("cost_usd") or 0.0),
            )
        return RepairResult(False, "repair callback returned an unsupported result")

    def _resume_capabilities(self, incident: IncidentRecord) -> bool:
        if self._stop_active():
            return False
        try:
            from ..control.broker import matching_veto_reason
        except ImportError:
            matching_veto_reason = None
        for action in incident.actions:
            if (action.get("kind") != "capability_suspended"
                    or action.get("status") != "PASS"
                    or action.get("owned") is not True):
                continue
            resource_id = str(action.get("resource_id") or "")
            capability = str(action.get("capability") or "")
            if not resource_id or not capability:
                continue
            if matching_veto_reason is not None:
                veto = matching_veto_reason(self.database, {resource_id, capability, "GLOBAL"})
                if veto:
                    self.store.record_action(incident.incident_id, {
                        "kind": "capability_resume", "status": "BLOCKED",
                        "resource_id": resource_id, "capability": capability,
                        "reason": veto,
                    })
                    return False
            latest = _latest_capability_change(self.database, resource_id, capability)
            expected_owner = f"incident:{incident.incident_id}"
            if (latest is None
                    or latest.source_id != expected_owner
                    or str(latest.payload.get("state")) != "STOPPED"):
                self.store.record_action(incident.incident_id, {
                    "kind": "capability_resume", "status": "BLOCKED",
                    "resource_id": resource_id, "capability": capability,
                    "reason": "containment ownership was lost before recovery",
                })
                return False
            target = str(action.get("previous_state") or "UNKNOWN")
            current = _current_capability_state(self.database, resource_id, capability)
            if current != "STOPPED":
                self.store.record_action(incident.incident_id, {
                    "kind": "capability_resume", "status": "BLOCKED",
                    "resource_id": resource_id, "capability": capability,
                    "reason": "capability changed outside this incident during recovery",
                    "observed_state": current,
                })
                return False
            if current != target:
                try:
                    set_capability_state(
                        self.database, resource_id, capability, target,
                        source_id=f"incident:{incident.incident_id}:recovery",
                        evidence={"incident_id": incident.incident_id,
                                  "reason": "observed recovery health"},
                    )
                except Exception as error:  # noqa: BLE001
                    self.store.record_action(incident.incident_id, {
                        "kind": "capability_resume", "status": "BLOCKED",
                        "resource_id": resource_id, "capability": capability,
                        "error_class": type(error).__name__, "error": _text(error),
                    })
                    return False
            self.store.record_action(incident.incident_id, {
                "kind": "capability_resume", "status": "PASS",
                "resource_id": resource_id, "capability": capability, "state": target,
            })
        return True

    def _repair(self, incident: IncidentRecord, anomaly: Anomaly,
                report: DiagnosticReport) -> IncidentRecord:
        if self._stop_active():
            return self.store.transition(
                incident.incident_id, IncidentStatus.BLOCKED,
                reason="emergency STOP is active; repair is forbidden",
            )
        if not report.all_observed:
            self.store.record_action(incident.incident_id, {
                "kind": "diagnostic_gate", "status": "BLOCKED",
                "reason": "repair requires a passing read-only diagnostic report",
                "report": report.to_payload(),
            })
            return self.store.transition(
                incident.incident_id, IncidentStatus.BLOCKED,
                reason="diagnostics did not support a bounded repair",
            )
        if incident.effect_ambiguity in {
            EffectAmbiguity.UNKNOWN.value, EffectAmbiguity.RECONCILE_REQUIRED.value,
            EffectAmbiguity.MISMATCH.value,
        }:
            return self.store.transition(
                incident.incident_id, IncidentStatus.UNKNOWN,
                reason="external effect remains ambiguous; reconciliation is required",
                effect_ambiguity=incident.effect_ambiguity,
            )
        handler = self.repair_handlers.get(anomaly.kind)
        if handler is None:
            return self.store.transition(
                incident.incident_id, IncidentStatus.BLOCKED,
                reason="no bounded repair handler is registered",
            )
        attempt = incident.repair_attempts + 1
        if attempt > self.max_repair_attempts:
            return self.store.transition(
                incident.incident_id, IncidentStatus.ESCALATED,
                reason="bounded repair attempts exhausted", repair_attempt=incident.repair_attempts,
            )
        incident = self.store.transition(
            incident.incident_id, IncidentStatus.REPAIRING,
            reason="bounded repair started", repair_attempt=attempt,
        )
        if self._stop_active():
            return self.store.transition(
                incident.incident_id, IncidentStatus.BLOCKED,
                reason="emergency STOP became active before repair callback",
                repair_attempt=attempt,
            )
        try:
            result = self._repair_result(handler(incident, report))
        except Exception as error:  # noqa: BLE001 - repair failure is incident evidence
            result = RepairResult(False, f"repair callback failed: {type(error).__name__}",
                                  {"error": _text(error), "error_class": type(error).__name__})
        self.store.record_action(
            incident.incident_id,
            {"kind": "repair" if result.succeeded else "repair_failed",
             "status": "PASS" if result.succeeded else "FAIL",
             "reason": result.reason, "evidence": result.evidence},
            repair_attempt=attempt, cost_usd=result.cost_usd,
        )
        if result.effect_ambiguity != EffectAmbiguity.NONE.value:
            return self.store.transition(
                incident.incident_id, IncidentStatus.UNKNOWN,
                reason="repair returned an ambiguous external effect",
                effect_ambiguity=result.effect_ambiguity, repair_attempt=attempt,
            )
        if not result.succeeded:
            if attempt >= self.max_repair_attempts:
                return self.store.transition(
                    incident.incident_id, IncidentStatus.ESCALATED,
                    reason="bounded repair attempts exhausted", repair_attempt=attempt,
                )
            return self.store.transition(
                incident.incident_id, IncidentStatus.DIAGNOSING,
                reason="repair failed; another bounded attempt is permitted",
                repair_attempt=attempt,
            )
        incident = self.store.transition(
            incident.incident_id, IncidentStatus.VERIFYING,
            reason="repair returned success; observed health is required",
            evidence=result.evidence, repair_attempt=attempt,
        )
        if self.health_probe is None:
            return self.store.transition(
                incident.incident_id, IncidentStatus.BLOCKED,
                reason="recovery health was not observed", repair_attempt=attempt,
            )
        try:
            observed = self.health_probe(incident, report)
            healthy = bool(observed.get("healthy")) if isinstance(observed, Mapping) else bool(observed)
        except Exception as error:  # noqa: BLE001
            healthy = False
            self.store.record_action(incident.incident_id, {
                "kind": "recovery_health", "status": "FAIL",
                "error_class": type(error).__name__, "error": _text(error),
            })
        if not healthy:
            if attempt >= self.max_repair_attempts:
                return self.store.transition(
                    incident.incident_id, IncidentStatus.ESCALATED,
                    reason="recovery health remained degraded", repair_attempt=attempt,
                )
            return self.store.transition(
                incident.incident_id, IncidentStatus.DIAGNOSING,
                reason="repair succeeded but recovery health is not proven",
                repair_attempt=attempt,
            )
        if not self._resume_capabilities(incident):
            return self.store.transition(
                incident.incident_id, IncidentStatus.BLOCKED,
                reason="capability resume is blocked by STOP, veto, or projection failure",
                repair_attempt=attempt,
            )
        return self.store.transition(
            incident.incident_id, IncidentStatus.RECOVERED,
            reason="repair and observed recovery health passed", repair_attempt=attempt,
        )

    def run_once(
        self,
        *,
        now: str | None = None,
        metrics: Mapping[str, Any] | None = None,
        auto_repair: bool = True,
    ) -> ResilienceRun:
        observed_at = now or isoformat()
        anomalies = self.detector.detect(self.database, now=observed_at, metrics=metrics)
        final: list[IncidentRecord] = []
        errors: list[Mapping[str, Any]] = []
        for anomaly in anomalies:
            try:
                incident = self.store.detect(anomaly.to_signal())
                if incident.status == IncidentStatus.DETECTED.value:
                    incident = self.store.transition(incident.incident_id, IncidentStatus.TRIAGED,
                                                      reason="deterministic anomaly rule matched")
                if incident.status == IncidentStatus.TRIAGED.value:
                    incident = self._contain(incident, anomaly)
                if incident.status == IncidentStatus.CONTAINED.value:
                    report = self._diagnose(incident, anomaly)
                else:
                    report = DiagnosticReport(incident.incident_id, (), observed_at)
                incident = self.store.get(incident.incident_id) or incident
                if auto_repair and incident.status == IncidentStatus.DIAGNOSING.value:
                    incident = self._repair(incident, anomaly, report)
                final.append(self.store.get(incident.incident_id) or incident)
            except Exception as error:  # noqa: BLE001 - one incident cannot stop unrelated detection
                errors.append({"anomaly_id": anomaly.anomaly_id,
                               "error_class": type(error).__name__, "error": _text(error)})
        recovered = tuple(item.incident_id for item in final if item.status == IncidentStatus.RECOVERED.value)
        blocked = tuple(item.incident_id for item in final
                        if item.status in {IncidentStatus.BLOCKED.value,
                                           IncidentStatus.ESCALATED.value,
                                           IncidentStatus.UNKNOWN.value})
        return ResilienceRun(observed_at, anomalies, tuple(final), recovered, blocked,
                             tuple(errors))
