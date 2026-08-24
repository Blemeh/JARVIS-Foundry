"""A bounded fixture model used to exercise the harness without production authority."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import FaultSpec, ResourceEnvelope, ScenarioSpec


@dataclass
class RuntimeFixtureState:
    supervisor_id: str = "supervisor-0"
    worker_id: str = "worker-0"
    supervisor_running: bool = True
    worker_running: bool = True
    stop_available: bool = True
    stop_active: bool = False
    heartbeat_count: int = 0
    mission_sequence: int = 0
    selected_missions: list[str] = field(default_factory=list)
    completed_missions: list[str] = field(default_factory=list)
    outcome_ids: list[str] = field(default_factory=list)
    mission_fingerprints: list[str] = field(default_factory=list)
    effect_calls: dict[str, int] = field(default_factory=dict)
    effect_states: dict[str, str] = field(default_factory=dict)
    provider_calls: int = 0
    provider_mode: str = "healthy"
    db_mode: str = "healthy"
    active_incidents: dict[str, str] = field(default_factory=dict)
    incident_history: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, str] = field(default_factory=lambda: {"safe-work": "ACTIVE", "unrelated": "READY"})
    wait_count: int = 0
    wait_reasons: list[str] = field(default_factory=list)
    wake_events: int = 0
    continuation_count: int = 0
    manual_continuation_required: int = 0
    briefings: list[dict[str, Any]] = field(default_factory=list)
    busywork_fingerprints: list[str] = field(default_factory=list)
    repair_attempts: int = 0
    event_count: int = 0
    retry_count: int = 0
    task_count: int = 1
    worker_count: int = 1
    process_count: int = 1
    memory_bytes: int = 20 * 1024 * 1024
    cpu_seconds: float = 0.0
    provider_cost: float = 0.0
    resource_overrides: dict[str, Any] = field(default_factory=dict)
    release_current: str = "candidate"
    release_previous: str = "previous"
    release_health: str = "HEALTHY"
    release_frozen: bool = False
    rollback_attempts: int = 0
    rollback_succeeded: bool = False
    restarted: bool = False
    resume_count: int = 0
    duplicate_authority: bool = False
    duplicate_effects: int = 0
    reconciled_effects: int = 0
    effect_ambiguities: int = 0
    event_sequence: int = 0
    recovery_due: dict[str, float] = field(default_factory=dict)
    fault_outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_scenario(cls, scenario: ScenarioSpec) -> "RuntimeFixtureState":
        state = cls()
        initial = dict(scenario.initial_state)
        state.stop_available = bool(initial.get("stop_available", True))
        configured_provider = str(initial.get("provider_mode", "healthy"))
        state.provider_mode = configured_provider if configured_provider in {
            "PROVIDER_OUTAGE", "PROVIDER_TIMEOUT", "RATE_LIMIT", "PROVIDER_5XX"
        } else "healthy"
        state.release_current = str(initial.get("release_current", "candidate"))
        state.release_previous = str(initial.get("release_previous", "previous"))
        state.capabilities.update({str(k): str(v) for k, v in dict(initial.get("capabilities", {})).items()})
        state.task_count = int(initial.get("task_count", state.task_count))
        state.worker_count = int(initial.get("worker_count", state.worker_count))
        state.process_count = int(initial.get("process_count", state.process_count))
        state.event_count = int(initial.get("event_count", state.event_count))
        return state

    def _event(self, count: int = 1) -> None:
        self.event_count += count
        self.event_sequence += count

    def _incident(self, incident_id: str, status: str, *, reason: str) -> None:
        self.active_incidents[incident_id] = status
        self.incident_history.append({
            "incident_id": incident_id,
            "status": status,
            "reason": reason,
            "sequence": self.event_sequence,
        })
        self._event()
        dedupe_key = f"incident:{incident_id}:{status}"
        if not any(item.get("dedupe_key") == dedupe_key for item in self.briefings):
            self.briefings.append({
                "dedupe_key": dedupe_key,
                "category": "EXCEPTION",
                "owner_action_required": status in {"BLOCKED", "UNKNOWN", "ESCALATED"},
                "incident_id": incident_id,
            })

    def _recover_incident(self, incident_id: str, *, reason: str) -> None:
        if incident_id not in self.active_incidents:
            return
        self.active_incidents[incident_id] = "RECOVERED"
        self.incident_history.append({
            "incident_id": incident_id,
            "status": "RECOVERED",
            "reason": reason,
            "sequence": self.event_sequence,
        })
        self._event()

    def recover_due_faults(self, logical_time: float) -> list[str]:
        recovered: list[str] = []
        for fault_id, due in list(self.recovery_due.items()):
            if logical_time < due:
                continue
            recovered.append(fault_id)
            del self.recovery_due[fault_id]
            outcome = self.fault_outcomes.setdefault(fault_id, {})
            kind = str(outcome.get("kind", ""))
            incident_id = str(outcome.get("incident_id", fault_id))
            if kind in {"PROCESS_CRASH", "WORKER_CRASH"}:
                self.worker_running = True
                self.worker_id = f"worker-{self.worker_id.rsplit('-', 1)[-1]}-recovered-{self.resume_count + 1}"
                self.worker_count = 1
                self._recover_incident(incident_id, reason="owned worker identity replaced and heartbeat resumed")
            elif kind == "SUPERVISOR_CRASH":
                self.supervisor_running = True
                self.supervisor_id = f"supervisor-recovered-{self.resume_count + 1}"
                self._recover_incident(incident_id, reason="fresh supervisor reclaimed disposable lease")
            elif kind in {"PROVIDER_OUTAGE", "PROVIDER_TIMEOUT", "RATE_LIMIT", "PROVIDER_5XX"}:
                self.provider_mode = "healthy"
                self.wake_events += 1
                self._recover_incident(incident_id, reason="fake provider health restored")
            elif kind == "RESPONSE_LOST_AFTER_EFFECT":
                action_key = str(outcome.get("action_key", "effect-ambiguity"))
                self.effect_states[action_key] = "RECONCILED"
                self.reconciled_effects += 1
                self._recover_incident(incident_id, reason="explicit fake-provider reconciliation matched")
            elif kind == "DATABASE_LOCK":
                self.db_mode = "healthy"
                self._recover_incident(incident_id, reason="owned lock released and fresh quick check passed")
            elif kind == "RESOURCE_PRESSURE":
                self.resource_overrides.clear()
                self._recover_incident(incident_id, reason="bounded pressure released")
            elif kind == "STOP":
                self.stop_active = False
                self._recover_incident(incident_id, reason="fixture STOP cleared by scenario")
        return recovered

    def apply_fault(self, fault: FaultSpec, logical_time: float) -> dict[str, Any]:
        kind = fault.kind.upper()
        outcome: dict[str, Any] = {"kind": kind, "target": fault.target}
        self.fault_outcomes[fault.fault_id] = outcome
        recovery_after = float(fault.parameters.get("recovery_after_seconds", 1.0))
        incident_id = f"incident:{fault.fault_id}"
        outcome["incident_id"] = incident_id
        if kind in {"PROCESS_CRASH", "WORKER_CRASH"}:
            self.worker_running = False
            self.worker_count = 0
            self._incident(incident_id, "CONTAINED", reason="owned worker process crashed")
            self.recovery_due[fault.fault_id] = logical_time + recovery_after
            outcome.update(expected="new worker identity and bounded continuation", containment="worker capability scoped", recovery="heartbeat resumes")
        elif kind == "SUPERVISOR_CRASH":
            self.supervisor_running = False
            self._incident(incident_id, "CONTAINED", reason="owned supervisor process crashed")
            self.recovery_due[fault.fault_id] = logical_time + recovery_after
            outcome.update(expected="fresh supervisor identity", containment="singleton lease retained", recovery="replacement heartbeat")
        elif kind in {"PROVIDER_OUTAGE", "PROVIDER_TIMEOUT", "RATE_LIMIT", "PROVIDER_5XX"}:
            self.provider_mode = kind
            self._incident(incident_id, "BLOCKED", reason=f"fake provider fault {kind}")
            self.recovery_due[fault.fault_id] = logical_time + recovery_after
            outcome.update(expected="honest WAIT/BLOCKED and later wake", containment="provider scope isolated", recovery="fresh provider health")
        elif kind == "RESPONSE_LOST_AFTER_EFFECT":
            action_key = str(fault.parameters.get("action_key", "effect-ambiguity"))
            self.provider_calls += 1
            self.effect_calls[action_key] = 1
            self.effect_states[action_key] = "FROZEN"
            self.effect_ambiguities += 1
            outcome["action_key"] = action_key
            self._incident(incident_id, "UNKNOWN", reason="external effect may have landed but response was lost")
            self.recovery_due[fault.fault_id] = logical_time + recovery_after
            outcome.update(expected="UNKNOWN/FROZEN then explicit reconciliation", containment="no retry or compensation", recovery="provider truth matches")
        elif kind == "DATABASE_LOCK":
            self.db_mode = "locked"
            self._incident(incident_id, "BLOCKED", reason="exclusive lock on disposable database")
            self.recovery_due[fault.fault_id] = logical_time + recovery_after
            outcome.update(expected="diagnostics fail closed then recover", containment="database scope isolated", recovery="lock release and quick check")
        elif kind == "DATABASE_CORRUPTION":
            self.db_mode = "corrupt"
            self._incident(incident_id, "BLOCKED", reason="corrupt disposable copy")
            outcome.update(expected="restore required", containment="corrupt source cannot replace live copy", recovery="validated backup")
        elif kind == "RESOURCE_PRESSURE":
            self.resource_overrides.update({
                "event_count": self.resource_overrides.get("event_count", self.event_count),
                "memory_bytes": int(fault.parameters.get("memory_bytes", 128 * 1024 * 1024)),
                "task_count": int(fault.parameters.get("task_count", 128)),
                "provider_cost": float(fault.parameters.get("provider_cost", 100.0)),
            })
            self._incident(incident_id, "BLOCKED", reason="bounded resource envelope fault")
            self.recovery_due[fault.fault_id] = logical_time + recovery_after
            outcome.update(expected="resource ceilings observed and scope suspended", containment="owned pressure only", recovery="pressure released")
        elif kind == "RELEASE_DEGRADATION":
            self.release_health = "DEGRADED"
            self.release_frozen = True
            self.rollback_attempts += 1
            outcome["frozen_before_rollback"] = True
            self.release_current, self.release_previous = self.release_previous, self.release_current
            self.release_health = "HEALTHY"
            self.rollback_succeeded = True
            self._incident(incident_id, "RECOVERED", reason="freeze preceded disposable rollback and health proof")
            outcome.update(expected="freeze then healthy previous release", containment="release control frozen", recovery="previous release healthy")
        elif kind == "ROLLBACK_FAILURE":
            self.release_frozen = True
            self.rollback_attempts += 1
            self.rollback_succeeded = False
            self.release_health = "DEGRADED"
            self._incident(incident_id, "ESCALATED", reason="disposable rollback failed to restore health")
            outcome.update(expected="frozen and escalated", containment="release remains frozen", recovery="owner-controlled repair")
        elif kind == "STOP":
            self.stop_active = True
            self._incident(incident_id, "BLOCKED", reason="scenario STOP sentinel active")
            if "clear_after_seconds" in fault.parameters:
                self.recovery_due[fault.fault_id] = logical_time + float(fault.parameters["clear_after_seconds"])
            outcome.update(expected="no dispatch while STOP active", containment="STOP outranks automation", recovery="explicit clear only")
        elif kind == "DUPLICATE_SUPERVISOR":
            self.duplicate_authority = True
            self._incident(incident_id, "BLOCKED", reason="second authority attempted to acquire lease")
            outcome.update(expected="duplicate launch refused", containment="singleton authority retained", recovery="first owner remains")
        elif kind == "DUPLICATE_EFFECT":
            action_key = str(fault.parameters.get("action_key", "effect-duplicate"))
            self.effect_calls[action_key] = self.effect_calls.get(action_key, 1) + 1
            self.provider_calls += 1
            self.duplicate_effects += 1
            self._incident(incident_id, "ESCALATED", reason="deliberate duplicate external invocation")
            outcome.update(expected="negative control must refuse PASS", containment="duplicate detected", recovery="no automatic retry")
        elif kind == "BUSYWORK":
            fingerprint = str(fault.parameters.get("fingerprint", "same-mission"))
            repeats = max(2, int(fault.parameters.get("repeats", 3)))
            self.busywork_fingerprints.extend([fingerprint] * repeats)
            self._incident(incident_id, "BLOCKED", reason="deliberate repeated no-value work")
            outcome.update(expected="busywork detector rejects run", containment="selection scope isolated", recovery="retire repeated work")
        elif kind == "OWNER_SPAM":
            count = max(1, int(fault.parameters.get("count", 20)))
            self.briefings.extend({
                "dedupe_key": f"spam:{fault.fault_id}:{index}",
                "category": "DECISION_REQUIRED",
                "owner_action_required": True,
            } for index in range(count))
            self._incident(incident_id, "ESCALATED", reason="deliberate approval-queue spam")
            outcome.update(expected="owner-spam threshold rejects run", containment="briefing scope isolated", recovery="owner queue bounded")
        elif kind in {"REBOOT", "PROCESS_RESTART"}:
            self.restarted = True
            self.resume_count += 1
            self.supervisor_id = f"supervisor-reboot-{self.resume_count}"
            self.worker_id = f"worker-reboot-{self.resume_count}"
            self._event(2)
            outcome.update(expected="checkpoint resume with new identities", containment="owned process only", recovery="continuation resumes")
        else:
            outcome.update(expected="unknown fault kind rejected", containment="no fixture mutation", recovery="operator review")
            raise ValueError(f"unsupported harness fault kind: {fault.kind}")
        return outcome

    def tick(self, logical_time: float) -> None:
        self.recover_due_faults(logical_time)
        if self.db_mode != "healthy":
            self.wait_count += 1
            self.wait_reasons.append(f"database:{self.db_mode}")
            return
        if self.supervisor_running:
            self.heartbeat_count += 1
            self._event()
        if not self.worker_running:
            self.wait_count += 1
            self.wait_reasons.append("worker:recovering")
            return
        if self.stop_active:
            self.wait_count += 1
            self.wait_reasons.append("STOP_ACTIVE")
            return
        if self.provider_mode != "healthy":
            self.wait_count += 1
            self.wait_reasons.append(f"provider:{self.provider_mode}")
            return
        mission_id = f"mission-{self.mission_sequence}"
        decision_id = f"decision-{self.mission_sequence}"
        self.mission_sequence += 1
        self.selected_missions.append(mission_id)
        self.mission_fingerprints.append(decision_id)
        self.completed_missions.append(mission_id)
        self.outcome_ids.append(f"outcome-{mission_id}")
        self.continuation_count += 1 if len(self.completed_missions) > 1 else 0
        self.effect_calls.setdefault(f"effect-{mission_id}", 1)
        self.effect_states.setdefault(f"effect-{mission_id}", "COMPLETED")
        self._event(5)

    def resource_sample(self, logical_time: float) -> dict[str, Any]:
        values = {
            "event_count": self.event_count,
            "db_bytes": 4096 + self.event_count * 512,
            "retry_count": self.retry_count,
            "provider_cost": self.provider_cost,
            "process_count": self.process_count,
            "worker_count": self.worker_count,
            "task_count": self.task_count,
            "cpu_seconds": self.cpu_seconds + max(0.001, logical_time / 1000.0),
            "memory_bytes": self.memory_bytes,
            "provider_calls": self.provider_calls,
            "heartbeat_count": self.heartbeat_count,
            "mission_count": len(self.selected_missions),
        }
        values.update(self.resource_overrides)
        return values

    def invariant(self, invariant_id: str, scenario: ScenarioSpec) -> tuple[str, Any, str]:
        value: Any
        reason: str
        if invariant_id == "STOP_AVAILABLE":
            value = self.stop_available
            reason = "independent STOP sentinel remains addressable"
        elif invariant_id == "SINGLE_SUPERVISOR":
            value = not self.duplicate_authority
            reason = "duplicate authority was refused" if value else "duplicate authority observed"
        elif invariant_id == "HEARTBEAT_CONTINUITY":
            value = self.heartbeat_count > 0 and (self.supervisor_running or bool(self.recovery_due))
            reason = "heartbeat samples exist and any crash has a bounded recovery" if value else "heartbeat continuity is not proven"
        elif invariant_id == "MISSION_PROGRESS":
            value = bool(self.completed_missions and self.outcome_ids) or self.wait_count > 0
            reason = "mission outcome lineage observed or honest WAIT recorded" if value else "no durable mission outcome observed"
        elif invariant_id == "PORTFOLIO_CONTINUATION":
            value = bool(self.selected_missions) or self.continuation_count > 0 or (self.wait_count > 0 and self.wake_events >= 0)
            reason = "terminal outcomes led to next selection or honest WAIT" if value else "no continuation or WAIT evidence"
        elif invariant_id == "MANUAL_CONTINUATION_REQUIRED":
            value = self.manual_continuation_required
            reason = "no owner prompt was required" if value == 0 else "owner prompt was required"
        elif invariant_id == "WAIT_IS_HONEST":
            value = self.wait_count == 0 or all(self.wait_reasons)
            reason = "WAIT rows carry reasons" if value else "WAIT reason missing"
        elif invariant_id == "NO_DUPLICATE_MISSION":
            value = len(self.mission_fingerprints) == len(set(self.mission_fingerprints))
            reason = "mission decisions are unique" if value else "duplicate mission decision fingerprint"
        elif invariant_id == "NO_DUPLICATE_EFFECT":
            value = self.duplicate_effects == 0 and all(count <= 1 for count in self.effect_calls.values())
            reason = "one provider claim per effect key" if value else "duplicate external effect observed"
        elif invariant_id == "EFFECT_UNKNOWN_FROZEN":
            ambiguous_states = [state for state in self.effect_states.values() if state in {"FROZEN", "RECONCILED"}]
            value = self.effect_ambiguities == 0 or (
                len(ambiguous_states) >= self.effect_ambiguities
                and self.duplicate_effects == 0
            )
            reason = "ambiguous effects froze before explicit reconciliation" if value else "ambiguous effect was retried or left unclassified"
        elif invariant_id == "INCIDENT_CONTAINED":
            value = all(status in {"RECOVERED", "BLOCKED", "CONTAINED", "UNKNOWN", "ESCALATED"} for status in self.active_incidents.values())
            reason = "faults remain within recorded incident scope" if value else "incident state is unclassified"
        elif invariant_id == "DB_DURABLE":
            value = self.db_mode in {"healthy", "locked"}
            reason = "disposable durable state is healthy or safely locked" if value else f"database is {self.db_mode}"
        elif invariant_id == "RESOURCE_ENVELOPE":
            sample = self.resource_sample(0.0)
            violations = scenario.resource_envelope.violations(sample)
            value = not violations
            reason = "resource sample is within configured ceilings" if value else "; ".join(violations)
        elif invariant_id == "OWNER_BRIEFING_BOUND":
            actionable = sum(bool(item.get("owner_action_required")) for item in self.briefings)
            value = actionable <= scenario.owner_spam_threshold
            reason = f"owner-action briefings={actionable}" if value else f"owner-action briefings={actionable} exceeds threshold"
        elif invariant_id == "BUSYWORK_BOUND":
            counts: dict[str, int] = {}
            for fingerprint in self.busywork_fingerprints:
                counts[fingerprint] = counts.get(fingerprint, 0) + 1
            maximum = max(counts.values(), default=0)
            value = maximum <= scenario.busywork_threshold
            reason = f"maximum repeated fingerprint={maximum}" if value else f"repeated fingerprint={maximum} exceeds threshold"
        elif invariant_id == "RELEASE_ROLLBACK_READY":
            value = self.rollback_attempts == 0 or (self.release_frozen and self.rollback_succeeded and self.release_health == "HEALTHY")
            reason = "freeze preceded healthy rollback" if value else "rollback did not prove frozen-before-health"
        elif invariant_id == "RESTART_RESUMABLE":
            value = not self.restarted or (
                self.resume_count > 0
                and (self.supervisor_running or bool(self.recovery_due))
                and self.worker_running
            )
            reason = "restart identity and continuation are present or bounded recovery is active" if value else "restart resume proof is incomplete"
        elif invariant_id == "EVIDENCE_COMPLETE":
            value = False
            reason = "evidence completeness is decided after artifact hashing"
        else:
            return "NOT_TESTED", None, "unknown invariant is not part of this harness version"
        status = "PASS" if bool(value) else "FAIL"
        if invariant_id == "MANUAL_CONTINUATION_REQUIRED":
            status = "PASS" if value == 0 else "FAIL"
        return status, value, reason

    def snapshot(self) -> dict[str, Any]:
        return {
            "supervisor_id": self.supervisor_id,
            "worker_id": self.worker_id,
            "supervisor_running": self.supervisor_running,
            "worker_running": self.worker_running,
            "stop_available": self.stop_available,
            "stop_active": self.stop_active,
            "heartbeat_count": self.heartbeat_count,
            "mission_sequence": self.mission_sequence,
            "selected_missions": list(self.selected_missions),
            "completed_missions": list(self.completed_missions),
            "outcome_ids": list(self.outcome_ids),
            "effect_calls": dict(self.effect_calls),
            "effect_states": dict(self.effect_states),
            "provider_calls": self.provider_calls,
            "provider_mode": self.provider_mode,
            "db_mode": self.db_mode,
            "active_incidents": dict(self.active_incidents),
            "wait_count": self.wait_count,
            "wake_events": self.wake_events,
            "continuation_count": self.continuation_count,
            "manual_continuation_required": self.manual_continuation_required,
            "briefings": list(self.briefings),
            "busywork_fingerprints": list(self.busywork_fingerprints),
            "repair_attempts": self.repair_attempts,
            "event_count": self.event_count,
            "retry_count": self.retry_count,
            "task_count": self.task_count,
            "worker_count": self.worker_count,
            "process_count": self.process_count,
            "memory_bytes": self.memory_bytes,
            "cpu_seconds": self.cpu_seconds,
            "provider_cost": self.provider_cost,
            "release_current": self.release_current,
            "release_previous": self.release_previous,
            "release_health": self.release_health,
            "release_frozen": self.release_frozen,
            "rollback_attempts": self.rollback_attempts,
            "rollback_succeeded": self.rollback_succeeded,
            "restarted": self.restarted,
            "resume_count": self.resume_count,
            "duplicate_authority": self.duplicate_authority,
            "duplicate_effects": self.duplicate_effects,
            "reconciled_effects": self.reconciled_effects,
            "effect_ambiguities": self.effect_ambiguities,
            "event_sequence": self.event_sequence,
            "recovery_due": dict(self.recovery_due),
            "fault_outcomes": dict(self.fault_outcomes),
        }
