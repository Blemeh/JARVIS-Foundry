"""Event-sourced workforce state and package lifecycle."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..clock import isoformat
from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event, canonical_json
from .contracts import (
    ConvergenceDecision,
    InterventionRecord,
    PACKAGE_STATUSES,
    ResourceClaim,
    SuitabilitySample,
    TERMINAL_PACKAGE_STATUSES,
    TERMINAL_SESSION_STATUSES,
    WorkPackage,
    WorkerObservation,
    WorkerSession,
)

SUPERVISOR_CONTROLLER = "supervisor"

_CANONICAL_ACCEPTANCE_CHECKS = {
    "stop_veto": "clear",
    "verdict_status": "PASS",
    "binding_present": "yes",
    "ordering": "ok",
    "binding_integrity": "ok",
    "identity": "ok",
    "isolation": "fresh-session",
    "evidence_complete": "yes",
    "post_verdict_mutation": "none",
}

def _workforce_verification_criteria(
    package: WorkPackage,
) -> tuple[str, ...]:
    criteria = list(package.acceptance_criteria)
    criteria.extend(
        f"required test exits zero: {command}"
        for command in package.required_tests
    )
    criteria.extend(
        f"evidence requirement is satisfied: {requirement}"
        for requirement in package.evidence_requirements
    )
    criteria.extend(
        f"owner constraint remains satisfied: {constraint}"
        for constraint in package.owner_constraints
    )
    return tuple(dict.fromkeys(criteria))


_SESSION_EVENT_TYPES = frozenset({
    "workforce.session_bound",
    "workforce.session_started",
    "workforce.session_state_changed",
    "workforce.session_heartbeat",
    "workforce.session_completed",
    "workforce.result_linked",
})

_PACKAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "PLANNED": frozenset({"READY", "REJECTED", "CANCELLED", "STOPPED"}),
    "READY": frozenset({
        "RUNNING",
        "WAITING",
        "VERIFYING",
        "REJECTED",
        "CANCELLED",
        "STOPPED",
    }),
    "RUNNING": frozenset({"WAITING", "VERIFYING", "FAILED", "CANCELLED", "STOPPED"}),
    "WAITING": frozenset({"READY", "RUNNING", "FAILED", "CANCELLED", "STOPPED"}),
    "VERIFYING": frozenset({"ACCEPTED", "REJECTED", "FAILED", "WAITING", "STOPPED"}),
    "FAILED": frozenset({"READY", "REJECTED", "CANCELLED", "STOPPED"}),
    "STOPPED": frozenset({"READY", "CANCELLED"}),
    "ACCEPTED": frozenset(),
    "REJECTED": frozenset(),
    "CANCELLED": frozenset(),
}


class WorkforceStateError(ValueError):
    """A workforce event or lifecycle transition is invalid."""


@dataclass(frozen=True, slots=True)
class WorkPackageState:
    package: WorkPackage
    status: str = "PLANNED"
    reason: str = ""
    version: int = 1
    updated_at: str = field(default_factory=isoformat)
    terminal_state: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            **self.package.to_payload(),
            "status": self.status,
            "reason": self.reason,
            "version": self.version,
            "updated_at": self.updated_at,
            "terminal_state": self.terminal_state,
        }


@dataclass(slots=True)
class WorkforceState:
    packages: dict[str, WorkPackageState] = field(default_factory=dict)
    sessions: dict[str, WorkerSession] = field(default_factory=dict)
    claims: dict[str, ResourceClaim] = field(default_factory=dict)
    observations: dict[str, WorkerObservation] = field(default_factory=dict)
    interventions: dict[str, InterventionRecord] = field(default_factory=dict)
    suitability_samples: dict[str, SuitabilitySample] = field(default_factory=dict)
    convergence_decisions: dict[str, ConvergenceDecision] = field(default_factory=dict)

    def package(self, package_id: str) -> WorkPackageState:
        try:
            return self.packages[package_id]
        except KeyError as error:
            raise WorkforceStateError(f"unknown work package: {package_id}") from error

    def active_claims(self) -> tuple[ResourceClaim, ...]:
        return tuple(
            claim for claim in self.claims.values() if claim.status == "ACTIVE"
        )

    def sessions_for(self, package_id: str) -> tuple[WorkerSession, ...]:
        return tuple(
            session
            for session in self.sessions.values()
            if session.package_id == package_id
        )


def _stop_reason(
    database: FoundryDatabase,
    stop_path: str | Path | None,
    *,
    package: WorkPackage | None = None,
) -> str | None:
    identities = {"GLOBAL", "workforce"}
    if package is not None:
        identities.update({
            package.package_id,
            package.parent_objective_id,
            package.repository_id,
        })
        if package.campaign_id:
            identities.add(package.campaign_id)
        if package.mission_id:
            identities.add(package.mission_id)
    effective_path = (
        Path(stop_path).expanduser().resolve()
        if stop_path is not None
        else database.path.parent / "EMERGENCY_STOP"
    )
    reason = emergency_stop_reason(database, effective_path, identities=identities)
    if reason:
        return reason
    try:
        return matching_veto_reason(database, identities)
    except Exception as error:
        return f"workforce owner-control read failed: {type(error).__name__}"


def _gate(
    database: FoundryDatabase,
    controller: str,
    *,
    stop_path: str | Path | None = None,
    package: WorkPackage | None = None,
    allow_stop: bool = False,
) -> None:
    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("workforce state is Supervisor-owned")
    reason = _stop_reason(database, stop_path, package=package)
    if reason and not allow_stop:
        raise PermissionError(f"workforce stopped: {reason}")


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    correlation_id: str,
    source_id: str,
) -> tuple[int, bool]:
    return database.append(_event(
        event_type,
        payload,
        key=key,
        correlation_id=correlation_id,
        source_id=source_id,
    ))


def _event(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    correlation_id: str,
    source_id: str,
) -> Event:
    return Event.create(
        event_type,
        dict(payload),
        source_type="supervisor",
        source_id=source_id,
        correlation_id=correlation_id,
        idempotency_key=key,
    )


def create_work_package(
    database: FoundryDatabase,
    package: WorkPackage,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
    source_id: str = "workforce-gateway",
) -> WorkPackageState:
    _gate(database, controller, stop_path=stop_path, package=package)
    with database.transaction() as connection:
        state = _rebuild_workforce_state_on_connection(database, connection)
        existing = state.packages.get(package.package_id)
        if existing is not None:
            if existing.package.content_hash() != package.content_hash():
                raise WorkforceStateError(
                    "package id already names another immutable contract: "
                    f"{package.package_id}"
                )
            return existing
        missing = tuple(
            dependency
            for dependency in package.dependencies
            if dependency not in state.packages
        )
        if missing:
            raise WorkforceStateError(
                f"package dependencies do not exist: {list(missing)}"
            )
        event = _event(
            "workforce.package_created",
            {
                **package.to_payload(),
                "status": "PLANNED",
                "reason": "immutable work package accepted",
                "version": 1,
                "updated_at": package.created_at,
                "terminal_state": None,
            },
            key=f"workforce.package_created:{package.package_id}",
            correlation_id=package.package_id,
            source_id=source_id,
        )
        database.append_events_on_connection(connection, [event])
    return rebuild_workforce_state(database).package(package.package_id)


def transition_work_package(
    database: FoundryDatabase,
    package_id: str,
    status: str,
    *,
    reason: str,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
    source_id: str = "workforce-gateway",
    allow_stop: bool = False,
    acceptance_builder_run_id: str | None = None,
) -> WorkPackageState:
    with database.transaction() as connection:
        current = _transition_work_package_on_connection(
            database,
            connection,
            package_id,
            status,
            reason=reason,
            controller=controller,
            stop_path=stop_path,
            source_id=source_id,
            allow_stop=allow_stop,
            acceptance_builder_run_id=acceptance_builder_run_id,
        )
    return current


def _transition_work_package_on_connection(
    database: FoundryDatabase,
    connection,
    package_id: str,
    status: str,
    *,
    reason: str,
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
    source_id: str = "workforce-gateway",
    allow_stop: bool = False,
    acceptance_builder_run_id: str | None = None,
) -> WorkPackageState:
    """Apply one package transition in a caller-owned transaction."""

    target = str(status).upper()
    if target not in PACKAGE_STATUSES:
        raise WorkforceStateError(f"unsupported package status: {status}")
    state = _rebuild_workforce_state_on_connection(database, connection)
    current = state.package(package_id)
    _gate(
        database,
        controller,
        stop_path=stop_path,
        package=current.package,
        allow_stop=allow_stop,
    )
    if target == current.status:
        return current
    if target not in _PACKAGE_TRANSITIONS[current.status]:
        raise WorkforceStateError(
            f"invalid package transition: {current.status} -> {target}"
        )
    acceptance_transition = (
        target == "ACCEPTED"
        or (
            current.status == "READY"
            and target == "VERIFYING"
            and acceptance_builder_run_id is not None
        )
    )
    if (
        current.status == "READY"
        and target == "VERIFYING"
        and not acceptance_builder_run_id
    ):
        raise WorkforceStateError(
            "READY -> VERIFYING requires exact canonical acceptance evidence"
        )
    acceptance = (
        _acceptance_receipt_on_connection(
            connection,
            state,
            current,
            builder_run_id=acceptance_builder_run_id,
        )
        if acceptance_transition
        else None
    )
    updated_at = isoformat()
    version = current.version + 1
    terminal = target if target in TERMINAL_PACKAGE_STATUSES else None
    event = _event(
        "workforce.package_state_changed",
        {
            "package_id": package_id,
            "previous_status": current.status,
            "expected_version": current.version,
            "status": target,
            "reason": str(reason),
            "version": version,
            "updated_at": updated_at,
            "terminal_state": terminal,
            **(acceptance or {}),
        },
        key=f"workforce.package_state_changed:{package_id}:v{version}:{target}",
        correlation_id=package_id,
        source_id=source_id,
    )
    database.append_events_on_connection(connection, [event])
    return replace(
        current,
        status=target,
        reason=str(reason),
        version=version,
        updated_at=updated_at,
        terminal_state=terminal,
    )


def rebuild_workforce_state(database: FoundryDatabase) -> WorkforceState:
    return _reduce_workforce_events(
        database.events(event_type_prefixes=("workforce.",))
    )


def _rebuild_workforce_state_on_connection(
    database: FoundryDatabase,
    connection,
) -> WorkforceState:
    rows = connection.execute(
        "SELECT * FROM events WHERE event_type LIKE 'workforce.%' ORDER BY sequence"
    ).fetchall()
    return _reduce_workforce_events(
        database._row_to_event(row) for row in rows
    )


def _session_version_on_connection(connection, session_id: str) -> int:
    placeholders = ", ".join("?" for _ in _SESSION_EVENT_TYPES)
    row = connection.execute(
        "SELECT COUNT(*) AS event_count FROM events "
        f"WHERE event_type IN ({placeholders}) "
        "AND json_extract(payload_json, '$.session_id') = ?",
        (*sorted(_SESSION_EVENT_TYPES), str(session_id)),
    ).fetchone()
    return int(row["event_count"] if row is not None else 0)


def _versioned_session_payload_on_connection(
    connection,
    session: WorkerSession,
) -> dict[str, Any]:
    current_version = _session_version_on_connection(
        connection,
        session.session_id,
    )
    return {
        **session.to_payload(),
        "expected_session_version": current_version,
        "session_version": current_version + 1,
    }


def _pending_effect_from_events(
    events: Iterable[Event],
    run_id: str,
    package_effect_action_key: str | None,
) -> str | None:
    pending: dict[str, int] = {}
    for sequence, event in enumerate(events, start=1):
        action_key = str(
            event.payload.get("effect_action_key")
            or event.payload.get("action_key")
            or ""
        )
        if not action_key:
            continue
        if (
            event.event_type == "provider.effect_reconciliation_required"
            and str(event.payload.get("run_id") or "") == run_id
        ):
            pending[action_key] = sequence
        elif (
            event.event_type == "provider.dispatch_failed"
            and str(event.payload.get("run_id") or "") == run_id
            and bool(event.payload.get("possible_effect"))
        ):
            pending[action_key] = sequence
        elif (
            event.event_type
            in {"effect.frozen", "effect.reconcile_pending", "effect.unknown"}
            and (
                action_key == str(package_effect_action_key or "")
                or str(event.payload.get("run_id") or "") == run_id
            )
        ):
            pending[action_key] = sequence
        elif event.event_type in {
            "provider.effect_reconciled",
            "effect.reconciled",
        }:
            pending.pop(action_key, None)
    if not pending:
        return None
    return max(pending, key=pending.__getitem__)


def _pending_effect_on_connection(
    database: FoundryDatabase,
    connection,
    run_id: str,
    package_effect_action_key: str | None,
) -> str | None:
    rows = connection.execute(
        "SELECT * FROM events ORDER BY sequence"
    ).fetchall()
    return _pending_effect_from_events(
        (database._row_to_event(row) for row in rows),
        run_id,
        package_effect_action_key,
    )


def _reduce_workforce_events(events: Iterable[Event]) -> WorkforceState:
    state = WorkforceState()
    claim_versions: dict[str, int] = {}
    session_versions: dict[str, int] = {}
    for event in events:
        payload = event.payload
        if event.event_type == "workforce.package_created":
            package = WorkPackage.from_payload(payload)
            existing = state.packages.get(package.package_id)
            if existing is not None:
                if existing.package.content_hash() != package.content_hash():
                    raise WorkforceStateError(
                        "conflicting package creation events: "
                        f"{package.package_id}"
                    )
                continue
            state.packages[package.package_id] = WorkPackageState(
                package=package,
                status=str(payload.get("status") or "PLANNED"),
                reason=str(payload.get("reason") or ""),
                version=int(payload.get("version") or 1),
                updated_at=str(payload.get("updated_at") or event.recorded_at),
                terminal_state=payload.get("terminal_state"),
            )
        elif event.event_type == "workforce.package_state_changed":
            package_id = str(payload.get("package_id") or "")
            current = state.packages.get(package_id)
            if current is None:
                raise WorkforceStateError(
                    f"package state event references unknown package: {package_id}"
                )
            target = str(payload.get("status") or "")
            if target not in PACKAGE_STATUSES:
                raise WorkforceStateError(f"unsupported package status: {target}")
            previous = payload.get("previous_status")
            if previous is not None and str(previous) != current.status:
                raise WorkforceStateError(
                    "package transition previous status mismatch: "
                    f"{package_id} expected {previous}, found {current.status}"
                )
            expected_version = payload.get("expected_version")
            if (
                expected_version is not None
                and int(expected_version) != current.version
            ):
                raise WorkforceStateError(
                    "package transition version mismatch: "
                    f"{package_id} expected {expected_version}, found {current.version}"
                )
            if (
                target != current.status
                and target not in _PACKAGE_TRANSITIONS[current.status]
            ):
                raise WorkforceStateError(
                    f"invalid replayed package transition: {current.status} -> {target}"
                )
            version = int(payload.get("version") or current.version + 1)
            if version != current.version + 1:
                raise WorkforceStateError(
                    "package transition version is not sequential: "
                    f"{package_id} expected {current.version + 1}, found {version}"
                )
            if (
                target == "ACCEPTED"
                or (
                    current.status == "READY"
                    and target == "VERIFYING"
                )
            ) and not all(
                payload.get(name)
                for name in (
                    "acceptance_event_id",
                    "verdict_id",
                    "builder_run_id",
                )
            ):
                raise WorkforceStateError(
                    "accepted package transition lacks canonical verifier evidence"
                )
            state.packages[package_id] = replace(
                current,
                status=target,
                reason=str(payload.get("reason") or ""),
                version=version,
                updated_at=str(payload.get("updated_at") or event.recorded_at),
                terminal_state=payload.get("terminal_state"),
            )
        elif event.event_type in _SESSION_EVENT_TYPES:
            session = WorkerSession.from_payload(payload)
            if session.package_id not in state.packages:
                raise WorkforceStateError(
                    f"session references unknown package: {session.package_id}"
                )
            prior = state.sessions.get(session.session_id)
            prior_version = session_versions.get(session.session_id, 0)
            immutable_identity = (
                session.session_id,
                session.package_id,
                session.run_id,
                session.attempt,
                session.started_at,
            )
            if prior is not None and immutable_identity != (
                prior.session_id,
                prior.package_id,
                prior.run_id,
                prior.attempt,
                prior.started_at,
            ):
                raise WorkforceStateError(
                    "session event changes immutable identity: "
                    f"{session.session_id}"
                )
            expected_version = payload.get("expected_session_version")
            declared_version = payload.get("session_version")
            try:
                expected_version_value = (
                    int(expected_version)
                    if expected_version is not None
                    else None
                )
                next_version = (
                    int(declared_version)
                    if declared_version is not None
                    else prior_version + 1
                )
            except (TypeError, ValueError) as error:
                raise WorkforceStateError(
                    f"session event has an invalid version: {session.session_id}"
                ) from error
            if (
                expected_version_value is not None
                and expected_version_value != prior_version
            ):
                raise WorkforceStateError(
                    "session event expected version mismatch: "
                    f"{session.session_id} expected {expected_version}, "
                    f"found {prior_version}"
                )
            if next_version != prior_version + 1:
                raise WorkforceStateError(
                    "session event version is not sequential: "
                    f"{session.session_id} expected {prior_version + 1}, "
                    f"found {next_version}"
                )
            if (
                prior is not None
                and prior.status in TERMINAL_SESSION_STATUSES
            ):
                raise WorkforceStateError(
                    "terminal workforce session is immutable: "
                    f"{session.session_id}:{prior.status}"
                )
            state.sessions[session.session_id] = session
            session_versions[session.session_id] = next_version
        elif event.event_type in {
            "workforce.resource_claimed",
            "workforce.resource_bound",
            "workforce.resource_renewed",
            "workforce.resource_released",
            "workforce.resource_expired",
        }:
            claim = ResourceClaim.from_payload(payload)
            if claim.package_id not in state.packages:
                raise WorkforceStateError(
                    f"resource claim references unknown package: {claim.package_id}"
                )
            prior = state.claims.get(claim.claim_id)
            prior_version = claim_versions.get(claim.claim_id, 0)
            if prior is not None and prior.status in {"RELEASED", "EXPIRED"}:
                continue
            expected_status = payload.get("expected_status")
            if (
                prior is not None
                and expected_status is not None
                and str(expected_status) != prior.status
            ):
                continue
            expected_version = payload.get("expected_version")
            try:
                if (
                    expected_version is not None
                    and int(expected_version) != prior_version
                ):
                    continue
                declared_version = payload.get("version")
                next_version = (
                    int(declared_version)
                    if declared_version is not None
                    else prior_version + 1
                )
            except (TypeError, ValueError) as error:
                raise WorkforceStateError(
                    f"resource claim has an invalid version: {claim.claim_id}"
                ) from error
            if next_version != prior_version + 1:
                continue
            state.claims[claim.claim_id] = claim
            claim_versions[claim.claim_id] = next_version
        elif event.event_type == "workforce.observation_recorded":
            observation = WorkerObservation.from_payload(payload)
            prior = state.observations.get(observation.observation_id)
            if prior is not None and prior != observation:
                raise WorkforceStateError(
                    "conflicting workforce observation events: "
                    f"{observation.observation_id}"
                )
            state.observations[observation.observation_id] = observation
        elif event.event_type in {
            "workforce.intervention_recorded",
            "workforce.intervention_completed",
            "workforce.intervention_evaluated",
        }:
            intervention = InterventionRecord.from_payload(payload)
            prior = state.interventions.get(intervention.intervention_id)
            if event.event_type == "workforce.intervention_recorded":
                if prior is not None and prior != intervention:
                    raise WorkforceStateError(
                        "conflicting workforce intervention events: "
                        f"{intervention.intervention_id}"
                    )
            elif prior is None:
                raise WorkforceStateError(
                    "intervention outcome references unknown intervention: "
                    f"{intervention.intervention_id}"
                )
            elif (
                prior.package_id,
                prior.session_id,
                prior.action,
                prior.reason,
                prior.observation_id,
                prior.instruction,
                prior.replacement_framework,
                prior.created_at,
            ) != (
                intervention.package_id,
                intervention.session_id,
                intervention.action,
                intervention.reason,
                intervention.observation_id,
                intervention.instruction,
                intervention.replacement_framework,
                intervention.created_at,
            ):
                raise WorkforceStateError(
                    "intervention outcome changes immutable identity: "
                    f"{intervention.intervention_id}"
                )
            if (
                event.event_type == "workforce.intervention_completed"
                and prior is not None
                and prior.outcome != "REQUESTED"
                and prior != intervention
            ):
                raise WorkforceStateError(
                    "conflicting terminal workforce intervention outcomes: "
                    f"{intervention.intervention_id}"
                )
            state.interventions[intervention.intervention_id] = intervention
        elif event.event_type == "workforce.suitability_sample_recorded":
            sample = SuitabilitySample.from_payload(payload)
            prior = state.suitability_samples.get(sample.sample_id)
            if prior is not None and prior != sample:
                raise WorkforceStateError(
                    "conflicting workforce suitability events: "
                    f"{sample.sample_id}"
                )
            state.suitability_samples[sample.sample_id] = sample
        elif event.event_type == "workforce.convergence_recorded":
            decision = ConvergenceDecision.from_payload(payload)
            prior = state.convergence_decisions.get(decision.decision_id)
            if prior is not None and prior != decision:
                raise WorkforceStateError(
                    "conflicting workforce convergence events: "
                    f"{decision.decision_id}"
                )
            state.convergence_decisions[decision.decision_id] = decision
    return state


def _acceptance_receipt_on_connection(
    connection,
    state: WorkforceState,
    package_state: WorkPackageState,
    *,
    builder_run_id: str | None = None,
) -> dict[str, Any]:
    package = package_state.package
    if package.mission_id is None or package.step_id is None:
        raise WorkforceStateError(
            "accepted package must be bound to a canonical mission step"
        )
    sessions = state.sessions_for(package.package_id)
    if not sessions:
        raise WorkforceStateError(
            "accepted package has no bound canonical worker run"
        )
    current_session = max(sessions, key=lambda session: session.attempt)
    expected_builder_run_id = str(
        builder_run_id or current_session.run_id
    )
    rows = connection.execute(
        "SELECT event_id, sequence, payload_json FROM events "
        "WHERE event_type = 'verification.accepted' "
        "AND source_type = 'supervisor' AND source_id = 'mission-loop' "
        "ORDER BY sequence DESC"
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        builder_run_id = str(payload.get("builder_run_id") or "")
        verifier_run_id = str(payload.get("verifier_run_id") or "")
        verdict_id = str(payload.get("verdict_id") or "")
        if not (
            payload.get("decision") == "ACCEPT"
            and str(payload.get("mission_id") or "") == package.mission_id
            and str(payload.get("subject_id") or "") == package.step_id
            and builder_run_id == expected_builder_run_id
            and verifier_run_id
            and verifier_run_id != builder_run_id
            and verdict_id
        ):
            continue
        checks = payload.get("checks")
        if not isinstance(checks, Mapping) or any(
            str(checks.get(name) or "") != expected
            for name, expected in _CANONICAL_ACCEPTANCE_CHECKS.items()
        ):
            continue
        latest = connection.execute(
            "SELECT v.verdict_id FROM verification_verdicts v "
            "JOIN events e ON e.event_id = v.last_event_id "
            "WHERE v.mission_id = ? AND v.subject_type = 'mission_step' "
            "AND v.subject_id = ? ORDER BY e.sequence DESC LIMIT 1",
            (package.mission_id, package.step_id),
        ).fetchone()
        if latest is None or str(latest["verdict_id"]) != verdict_id:
            continue
        verdict = connection.execute(
            "SELECT status, mission_id, subject_id, builder_run_id, "
            "verifier_run_id, last_event_id "
            "FROM verification_verdicts WHERE verdict_id = ?",
            (verdict_id,),
        ).fetchone()
        if verdict is None or not (
            str(verdict["status"]) == "PASS"
            and str(verdict["mission_id"]) == package.mission_id
            and str(verdict["subject_id"]) == package.step_id
            and str(verdict["builder_run_id"]) == builder_run_id
            and str(verdict["verifier_run_id"]) == verifier_run_id
            and str(verdict["verifier_run_id"]) != builder_run_id
        ):
            continue
        started_row = connection.execute(
            "SELECT sequence, payload_json FROM events "
            "WHERE event_type = 'verification.started' "
            "AND source_type = 'supervisor' AND source_id = 'mission-loop' "
            "AND json_extract(payload_json, '$.verdict_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (verdict_id,),
        ).fetchone()
        terminal_row = connection.execute(
            "SELECT sequence, source_type, source_id, payload_json "
            "FROM events WHERE event_id = ?",
            (str(verdict["last_event_id"]),),
        ).fetchone()
        if started_row is None or terminal_row is None:
            continue
        if not (
            int(started_row["sequence"]) < int(terminal_row["sequence"])
            < int(row["sequence"])
            and str(terminal_row["source_type"]) == "supervisor"
            and str(terminal_row["source_id"]) == "mission-loop"
        ):
            continue
        try:
            started = json.loads(str(started_row["payload_json"]))
            terminal = json.loads(str(terminal_row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if tuple(started.get("criteria") or ()) != (
            _workforce_verification_criteria(package)
        ):
            continue
        binding = started.get("binding")
        evidence = terminal.get("evidence")
        if not isinstance(binding, Mapping) or not isinstance(evidence, Mapping):
            continue
        stored_hash = str(binding.get("binding_hash") or "")
        material = {
            str(name): value
            for name, value in binding.items()
            if str(name) != "binding_hash"
        }
        recomputed_hash = hashlib.sha256(
            canonical_json(material).encode("utf-8")
        ).hexdigest()
        if not stored_hash or stored_hash != recomputed_hash:
            continue
        if not (
            str(started.get("builder_run_id") or "") == builder_run_id
            and str(started.get("verifier_run_id") or "") == verifier_run_id
            and str(binding.get("builder_run_id") or "") == builder_run_id
            and str(binding.get("verifier_run_id") or "") == verifier_run_id
            and str(evidence.get("builder_run_id") or "") == builder_run_id
            and str(evidence.get("verifier_run_id") or "") == verifier_run_id
            and str(evidence.get("binding_hash") or "") == stored_hash
            and evidence.get("fabrication_review") == "performed"
        ):
            continue
        return {
            "acceptance_event_id": str(row["event_id"]),
            "verdict_id": verdict_id,
            "builder_run_id": builder_run_id,
            "verifier_run_id": verifier_run_id,
        }
    raise WorkforceStateError(
        "accepted package lacks a canonical verification.accepted receipt"
    )


def workforce_snapshot(
    database: FoundryDatabase,
    package_id: str | None = None,
) -> dict[str, Any]:
    state = rebuild_workforce_state(database)
    packages = {
        key: value.to_payload()
        for key, value in state.packages.items()
        if package_id is None or key == package_id
    }
    package_keys = set(packages)
    return {
        "packages": packages,
        "sessions": {
            key: value.to_payload()
            for key, value in state.sessions.items()
            if value.package_id in package_keys
        },
        "claims": {
            key: value.to_payload()
            for key, value in state.claims.items()
            if value.package_id in package_keys
        },
        "observations": {
            key: value.to_payload()
            for key, value in state.observations.items()
            if value.package_id in package_keys
        },
        "interventions": {
            key: value.to_payload()
            for key, value in state.interventions.items()
            if value.package_id in package_keys
        },
        "suitability_samples": {
            key: value.to_payload()
            for key, value in state.suitability_samples.items()
            if value.package_id in package_keys
        },
        "convergence_decisions": {
            key: value.to_payload()
            for key, value in state.convergence_decisions.items()
            if package_id is None or package_id in value.classifications
        },
    }
