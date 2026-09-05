"""Governed workforce intervention, retry, reassignment, and evaluation."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase
from ..events import canonical_json
from ..supervisor.leases import LeaseManager, LeaseUnavailable
from ..supervisor.worker import (
    WorkerController,
    adapter_pid_table_path,
    worker_lease_key,
)
from .contracts import (
    INTERVENTION_ACTIONS,
    TERMINAL_SESSION_STATUSES,
    InterventionRecord,
    WorkerObservation,
    WorkerSession,
)
from .gateway import (
    CanonicalMissionLoop,
    ExecutionAdmissionState,
    GatewayBinding,
    GatewayLaunch,
    GatewayResult,
    OwnedProcessEpoch,
    OwnedProcessTerminationResult,
    WorkforceGateway,
    WorkforceGatewayError,
)
from .state import (
    SUPERVISOR_CONTROLLER,
    WorkforceStateError,
    _acceptance_receipt_on_connection,
    _append,
    _event,
    _gate,
    _pending_effect_from_events,
    _pending_effect_on_connection,
    _rebuild_workforce_state_on_connection,
    _stop_reason,
    _transition_work_package_on_connection,
    _versioned_session_payload_on_connection,
    rebuild_workforce_state,
)

RETRY_ACTIONS = frozenset({"REPAIR", "RESTART", "REASSIGN"})
DIRECTIVE_ACTIONS = frozenset({"REDIRECT", "REDUCE_SCOPE"})
INTERVENTION_LEASE_SECONDS = 60
INTERVENTION_LEASE_RENEW_SECONDS = 10.0
INTERVENTION_WAIT_SECONDS = 10.0

RECOMMENDED_ACTION_BY_OBSERVATION = {
    "HEALTHY_PROGRESS": "CONTINUE",
    "NO_PROGRESS_STALL": "RESTART",
    "PROVIDER_OUTAGE": "REASSIGN",
    "WRONG_REPOSITORY": "TERMINATE",
    "WRONG_BRANCH": "TERMINATE",
    "SCOPE_ESCAPE": "TERMINATE",
    "GOAL_DRIFT": "REDIRECT",
    "ENDLESS_ANALYSIS": "REDUCE_SCOPE",
    "MANDATORY_TESTS_SKIPPED": "REPAIR",
    "FALSE_COMPLETION": "REPAIR",
    "SESSION_DEAD": "RESTART",
    "PARTIAL_MUTATION": "REPAIR",
    "REPEATED_FAILURE": "REASSIGN",
    "EXTERNAL_BLOCKER": "WAIT",
}


class WorkforceSteeringError(RuntimeError):
    """A requested workforce intervention is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class InterventionExecution:
    intervention: InterventionRecord
    package_status: str
    session_status: str
    terminated_owned_process: bool = False
    replacement_session_id: str | None = None
    launch: GatewayResult | GatewayLaunch | None = None
    blocked_reason: str | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class RetryPreparation:
    terminated_owned_process: bool
    blocked_outcome: str | None = None
    blocked_reason: str | None = None
    replacement_binding: GatewayBinding | None = None
    launch: GatewayResult | None = None


@dataclass(frozen=True, slots=True)
class _InterventionAuthority:
    lease_key: str
    owner_id: str
    heartbeat_errors: list[BaseException]


class WorkforceSteering:
    """Apply bounded actions without replacing Executive or MissionLoop."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        gateway: WorkforceGateway | None = None,
        stop_path: str | Path | None = None,
        controller: str = SUPERVISOR_CONTROLLER,
        source_id: str = "workforce-steering",
    ) -> None:
        self.database = database
        self.stop_path = (
            Path(stop_path).expanduser().resolve()
            if stop_path is not None
            else None
        )
        self.controller = controller
        self.source_id = source_id
        self.leases = LeaseManager(database)
        self.gateway = gateway or WorkforceGateway(
            database,
            stop_path=self.stop_path,
            controller=controller,
            source_id=source_id,
        )

    @staticmethod
    def recommend(observation: WorkerObservation | str) -> str:
        kind = observation.kind if isinstance(observation, WorkerObservation) else str(
            observation
        )
        try:
            return RECOMMENDED_ACTION_BY_OBSERVATION[kind]
        except KeyError as error:
            raise WorkforceSteeringError(
                f"unsupported workforce observation: {kind}"
            ) from error

    def intervene(
        self,
        package_id: str,
        action: str,
        *,
        reason: str,
        session_id: str | None = None,
        observation_id: str | None = None,
        instruction: str = "",
        replacement_framework: str | None = None,
        workers: WorkerController | None = None,
        mission_loop: CanonicalMissionLoop | None = None,
        command: Sequence[str] | None = None,
        intervention_id: str | None = None,
    ) -> InterventionExecution:
        """Record and apply one exact intervention at most once."""

        normalized_action = str(action).upper()
        if normalized_action not in INTERVENTION_ACTIONS:
            raise WorkforceSteeringError(
                f"unsupported intervention action: {action}"
            )
        reason = str(reason).strip()
        instruction = str(instruction).strip()
        replacement_framework = (
            str(replacement_framework).strip()
            if replacement_framework is not None
            else None
        )
        if not reason:
            raise WorkforceSteeringError("intervention reason is required")
        if normalized_action in {"REDIRECT", "REPAIR", "REDUCE_SCOPE"} and (
            not instruction
        ):
            raise WorkforceSteeringError(
                f"{normalized_action} requires a bounded instruction"
            )
        if normalized_action == "REASSIGN" and not replacement_framework:
            raise WorkforceSteeringError(
                "REASSIGN requires a replacement framework"
            )
        if command is not None:
            raise WorkforceSteeringError(
                "worker process commands are sealed by WorkforceGateway"
            )
        if (
            normalized_action not in RETRY_ACTIONS
            and mission_loop is not None
        ):
            raise WorkforceSteeringError(
                f"{normalized_action} does not accept a replacement launch"
            )

        state = rebuild_workforce_state(self.database)
        package_state = state.package(package_id)
        if intervention_id is not None:
            completed_replay = state.interventions.get(intervention_id)
            if (
                completed_replay is not None
                and completed_replay.outcome != "REQUESTED"
            ):
                replay_session_id = str(
                    session_id or completed_replay.session_id
                )
                replay_session = self._session(
                    state,
                    package_id,
                    replay_session_id,
                )
                replay_observation_id = (
                    observation_id
                    if observation_id is not None
                    else completed_replay.observation_id
                )
                if replay_observation_id is not None:
                    self._observation(
                        state,
                        package_id,
                        replay_session.session_id,
                        replay_observation_id,
                    )
                self._validate_replay(
                    completed_replay,
                    package_id=package_id,
                    session_id=replay_session.session_id,
                    observation_id=replay_observation_id,
                    action=normalized_action,
                    reason=reason,
                    instruction=instruction,
                    replacement_framework=replacement_framework,
                )
                return self._execution(completed_replay, replayed=True)
        session = self._session(state, package_id, session_id)
        observation = self._observation(
            state,
            package_id,
            session.session_id,
            observation_id,
        )
        expected_session_id = session.session_id
        expected_observation_id = (
            observation.observation_id
            if observation is not None
            else None
        )
        resolved_id = intervention_id or self._intervention_id(
            package_id=package_id,
            session=session,
            action=normalized_action,
            reason=reason,
            observation=observation,
            instruction=instruction,
            replacement_framework=replacement_framework,
        )
        existing = state.interventions.get(resolved_id)
        if existing is not None and existing.outcome != "REQUESTED":
            self._validate_replay(
                existing,
                package_id=package_id,
                session_id=expected_session_id,
                observation_id=expected_observation_id,
                action=normalized_action,
                reason=reason,
                instruction=instruction,
                replacement_framework=replacement_framework,
            )
            return self._execution(existing, replayed=True)

        lease = self._acquire_intervention(package_id, resolved_id)
        if lease is None:
            completed = rebuild_workforce_state(self.database).interventions[
                resolved_id
            ]
            self._validate_replay(
                completed,
                package_id=package_id,
                session_id=expected_session_id,
                observation_id=expected_observation_id,
                action=normalized_action,
                reason=reason,
                instruction=instruction,
                replacement_framework=replacement_framework,
            )
            return self._execution(completed, replayed=True)
        lease_key, lease_owner = lease
        authority = _InterventionAuthority(
            lease_key=lease_key,
            owner_id=lease_owner,
            heartbeat_errors=[],
        )
        heartbeat_stop, heartbeat_thread = (
            self._start_intervention_heartbeat(authority)
        )
        try:
            self._assert_intervention_authority(authority)
            state = rebuild_workforce_state(self.database)
            package_state = state.package(package_id)
            session = self._session(
                state,
                package_id,
                expected_session_id,
            )
            existing = state.interventions.get(resolved_id)
            if existing is not None and existing.outcome != "REQUESTED":
                self._validate_replay(
                    existing,
                    package_id=package_id,
                    session_id=expected_session_id,
                    observation_id=expected_observation_id,
                    action=normalized_action,
                    reason=reason,
                    instruction=instruction,
                    replacement_framework=replacement_framework,
                )
                return self._execution(existing, replayed=True)
            replayed = existing is not None
            if existing is not None:
                self._validate_replay(
                    existing,
                    package_id=package_id,
                    session_id=expected_session_id,
                    observation_id=expected_observation_id,
                    action=normalized_action,
                    reason=reason,
                    instruction=instruction,
                    replacement_framework=replacement_framework,
                )
                requested = existing
                session = state.sessions[requested.session_id]
            else:
                requested = InterventionRecord(
                    intervention_id=resolved_id,
                    package_id=package_id,
                    session_id=expected_session_id,
                    action=normalized_action,
                    reason=reason,
                    observation_id=(
                        expected_observation_id
                    ),
                    instruction=instruction,
                    replacement_framework=replacement_framework,
                    before_progress_fingerprint=(
                        observation.progress_fingerprint
                        if observation is not None
                        else session.progress_fingerprint
                    ),
                )

            self._require_current_session(state, package_id, session)
            allow_stop = normalized_action == "TERMINATE"
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package_state.package,
                allow_stop=allow_stop,
            )
            if existing is None:
                self._record_requested(requested, authority)
            self._assert_intervention_authority(authority)
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package_state.package,
                allow_stop=allow_stop,
            )
            result = self._apply_requested(
                requested,
                session,
                package_status=package_state.status,
                workers=workers,
                mission_loop=mission_loop,
                intervention_authority=authority,
            )
            self._assert_intervention_authority(authority)
            return replace(result, replayed=True) if replayed else result
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()
            self.leases.release(lease_key, lease_owner)

    def _apply_requested(
        self,
        requested: InterventionRecord,
        session: WorkerSession,
        *,
        package_status: str,
        workers: WorkerController | None,
        mission_loop: CanonicalMissionLoop | None,
        intervention_authority: _InterventionAuthority,
    ) -> InterventionExecution:
        self._assert_intervention_authority(intervention_authority)
        if package_status in {"ACCEPTED", "REJECTED", "CANCELLED"}:
            completed = self._complete(
                requested,
                outcome="BLOCKED_TERMINAL_PACKAGE",
                intervention_authority=intervention_authority,
            )
            return self._execution(
                completed,
                blocked_reason=f"package is terminal: {package_status}",
            )

        try:
            if requested.action == "CONTINUE":
                return self._continue(
                    requested,
                    workers,
                    intervention_authority,
                )
            if requested.action == "WAIT":
                self._move_to_waiting(
                    requested,
                    intervention_authority,
                )
                return self._execution(
                    self._complete(
                        requested,
                        outcome="WAITING",
                        intervention_authority=intervention_authority,
                    ),
                )
            if requested.action == "ESCALATE":
                self._directive(requested, intervention_authority)
                return self._execution(
                    self._complete(
                        requested,
                        outcome="ESCALATED",
                        intervention_authority=intervention_authority,
                    ),
                )
            if requested.action in DIRECTIVE_ACTIONS:
                preparation = self._pause_for_replan(
                    requested,
                    session,
                    workers,
                    intervention_authority,
                )
                outcome = preparation.blocked_outcome or (
                    "NEW_PACKAGE_REQUIRED"
                    if requested.action == "REDUCE_SCOPE"
                    else "EXECUTIVE_REPLAN_REQUIRED"
                )
                return self._execution(
                    self._complete(
                        requested,
                        outcome=outcome,
                        intervention_authority=intervention_authority,
                    ),
                    terminated_owned_process=(
                        preparation.terminated_owned_process
                    ),
                    blocked_reason=preparation.blocked_reason,
                )
            if requested.action == "TERMINATE":
                terminated = self._terminate(
                    requested,
                    session,
                    workers,
                    intervention_authority,
                )
                return self._execution(
                    self._complete(
                        requested,
                        outcome="TERMINATED",
                        intervention_authority=intervention_authority,
                    ),
                    terminated_owned_process=terminated,
                )
            if requested.action in RETRY_ACTIONS:
                return self._retry(
                    requested,
                    session,
                    workers=workers,
                    mission_loop=mission_loop,
                    intervention_authority=intervention_authority,
                )
        except Exception as error:
            self._complete_failure_if_authorized(
                requested,
                error,
                intervention_authority,
            )
            raise
        raise AssertionError(f"unhandled workforce action: {requested.action}")

    def evaluate(
        self,
        intervention_id: str,
        observation_id: str,
    ) -> InterventionRecord:
        """Record whether an intervention produced observable improvement."""

        state = rebuild_workforce_state(self.database)
        try:
            intervention = state.interventions[intervention_id]
        except KeyError as error:
            raise WorkforceSteeringError(
                f"unknown intervention: {intervention_id}"
            ) from error
        try:
            observation = state.observations[observation_id]
        except KeyError as error:
            raise WorkforceSteeringError(
                f"unknown workforce observation: {observation_id}"
            ) from error
        lease = self._acquire_intervention(
            intervention.package_id,
            intervention_id,
            terminal_replay_shortcut=False,
        )
        if lease is None:
            raise WorkforceSteeringError(
                "intervention evaluation could not acquire authority: "
                f"{intervention_id}"
            )
        lease_key, lease_owner = lease
        authority = _InterventionAuthority(
            lease_key=lease_key,
            owner_id=lease_owner,
            heartbeat_errors=[],
        )
        heartbeat_stop, heartbeat_thread = (
            self._start_intervention_heartbeat(authority)
        )
        try:
            with self.database.transaction() as connection:
                self._assert_intervention_authority(
                    authority,
                    connection=connection,
                )
                state = _rebuild_workforce_state_on_connection(
                    self.database,
                    connection,
                )
                intervention = state.interventions[intervention_id]
                observation = state.observations[observation_id]
                if intervention.outcome == "REQUESTED":
                    raise WorkforceSteeringError(
                        "intervention cannot be evaluated while its action "
                        "is still applying"
                    )
                if (
                    intervention.after_progress_fingerprint
                    == observation.progress_fingerprint
                    and intervention.outcome
                    in {"IMPROVED", "UNCHANGED", "EVALUATED"}
                ):
                    return intervention
                if intervention.outcome in {
                    "IMPROVED",
                    "UNCHANGED",
                    "EVALUATED",
                }:
                    raise WorkforceSteeringError(
                        "intervention already has another immutable "
                        f"evaluation: {intervention_id}"
                    )
                expected_session_id = (
                    intervention.replacement_session_id
                    or intervention.session_id
                )
                if (
                    observation.package_id != intervention.package_id
                    or observation.session_id != expected_session_id
                ):
                    raise WorkforceSteeringError(
                        "intervention evaluation references another package "
                        "or session"
                    )
                package = state.package(intervention.package_id).package
                _gate(
                    self.database,
                    self.controller,
                    stop_path=self.stop_path,
                    package=package,
                )
                completed_rows = connection.execute(
                    "SELECT sequence, source_type, correlation_id, "
                    "idempotency_key FROM events "
                    "WHERE event_type = 'workforce.intervention_completed' "
                    "AND json_extract(payload_json, '$.intervention_id') = ? "
                    "ORDER BY sequence",
                    (intervention_id,),
                ).fetchall()
                observation_rows = connection.execute(
                    "SELECT sequence, source_type, correlation_id FROM events "
                    "WHERE event_type = 'workforce.observation_recorded' "
                    "AND json_extract(payload_json, '$.observation_id') = ? "
                    "ORDER BY sequence",
                    (observation_id,),
                ).fetchall()
                if not (
                    len(completed_rows) == 1
                    and len(observation_rows) == 1
                    and str(completed_rows[0]["source_type"])
                    == "supervisor"
                    and str(completed_rows[0]["correlation_id"])
                    == intervention.package_id
                    and str(completed_rows[0]["idempotency_key"])
                    == (
                        "workforce.intervention_completed:"
                        f"{intervention_id}"
                    )
                    and str(observation_rows[0]["source_type"])
                    == "supervisor"
                    and str(observation_rows[0]["correlation_id"])
                    == intervention.package_id
                    and int(completed_rows[0]["sequence"])
                    < int(observation_rows[0]["sequence"])
                ):
                    raise WorkforceSteeringError(
                        "intervention evaluation requires one canonical "
                        "post-completion observation"
                    )
                prior = (
                    state.observations.get(intervention.observation_id)
                    if intervention.observation_id
                    else None
                )
                if (
                    prior is not None
                    and prior.progress_fingerprint
                    == observation.progress_fingerprint
                ):
                    improved: bool | None = False
                    outcome = "UNCHANGED"
                elif observation.kind == "HEALTHY_PROGRESS" and (
                    prior is None or prior.kind != "HEALTHY_PROGRESS"
                ):
                    improved = True
                    outcome = "IMPROVED"
                else:
                    improved = None
                    outcome = "EVALUATED"
                evaluated = replace(
                    intervention,
                    after_progress_fingerprint=(
                        observation.progress_fingerprint
                    ),
                    improved=improved,
                    outcome=outcome,
                )
                event = _event(
                    "workforce.intervention_evaluated",
                    evaluated.to_payload(),
                    key=(
                        "workforce.intervention_evaluated:"
                        f"{intervention_id}:{observation.observation_id}"
                    ),
                    correlation_id=intervention.package_id,
                    source_id=self.source_id,
                )
                self.database.append_events_on_connection(
                    connection,
                    [event],
                )
                self._assert_intervention_authority(
                    authority,
                    connection=connection,
                )
                _gate(
                    self.database,
                    self.controller,
                    stop_path=self.stop_path,
                    package=package,
                )
            return evaluated
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()
            self.leases.release(lease_key, lease_owner)

    def _continue(
        self,
        requested: InterventionRecord,
        workers: WorkerController | None,
        intervention_authority: _InterventionAuthority,
    ) -> InterventionExecution:
        self._assert_intervention_authority(intervention_authority)
        state = rebuild_workforce_state(self.database)
        session = state.sessions[requested.session_id]
        self._require_current_session(
            state,
            requested.package_id,
            session,
        )
        if session.status in TERMINAL_SESSION_STATUSES:
            return self._execution(
                self._complete(
                    requested,
                    outcome="NO_ACTIVE_SESSION",
                    intervention_authority=intervention_authority,
                ),
                blocked_reason="session is already terminal",
            )
        binding = self.gateway.heartbeat(
            requested.package_id,
            workers=workers,
            expected_session_id=requested.session_id,
            control_authority_check=lambda connection=None: (
                self._assert_intervention_authority(
                    intervention_authority,
                    connection=connection,
                )
            ),
        )
        self._assert_intervention_authority(intervention_authority)
        return self._execution(
            self._complete(
                requested,
                outcome="CONTINUED",
                intervention_authority=intervention_authority,
            ),
            replacement_session_id=binding.session.session_id,
        )

    def _pause_for_replan(
        self,
        requested: InterventionRecord,
        session: WorkerSession,
        workers: WorkerController | None,
        intervention_authority: _InterventionAuthority,
    ) -> RetryPreparation:
        def validate_current(connection: Any | None) -> ExecutionAdmissionState:
            return self._validate_current_for_preemption(
                requested,
                connection=connection,
            )

        def terminate_owned(
            admission_authority,
        ) -> OwnedProcessTerminationResult:
            current_session = self._current_session_for_preemption(
                requested,
                connection=None,
            )

            def assert_authority() -> None:
                self._assert_control_authorities(
                    intervention_authority,
                    admission_authority,
                )

            assert_authority()
            result = self._terminate_owned(
                current_session,
                workers,
                control_authority_check=assert_authority,
            )
            assert_authority()
            return result

        with self.gateway._preemptible_admission_guard(
            requested.package_id,
            session.run_id,
            validate_current=validate_current,
            can_terminate_owned=lambda: self._can_terminate_owned(
                self._current_session_for_preemption(
                    requested,
                    connection=None,
                ),
                workers,
            ),
            terminate_owned=terminate_owned,
        ) as preemption:
            try:
                with self.database.transaction() as connection:
                    self._assert_control_authorities(
                        intervention_authority,
                        preemption.authority,
                        connection=connection,
                    )
                    state = _rebuild_workforce_state_on_connection(
                        self.database,
                        connection,
                    )
                    current_session = state.sessions[requested.session_id]
                    self._require_current_session(
                        state,
                        requested.package_id,
                        current_session,
                    )
                    settled = (
                        self._settle_post_termination_hazard_on_connection(
                            connection,
                            requested,
                            current_session,
                            preemption.termination,
                            intervention_authority,
                            preemption.authority,
                        )
                    )
                    if settled is not None:
                        return settled
                    self._mark_terminated_on_connection(
                        connection,
                        current_session,
                        requested,
                        reason=(
                            f"{requested.action} paused the prior worker"
                        ),
                    )
                    current = state.package(requested.package_id)
                    if current.status in {
                        "READY",
                        "RUNNING",
                        "VERIFYING",
                    }:
                        _transition_work_package_on_connection(
                            self.database,
                            connection,
                            requested.package_id,
                            "WAITING",
                            reason=requested.reason,
                            controller=self.controller,
                            stop_path=self.stop_path,
                            source_id=self.source_id,
                        )
                    self._assert_control_authorities(
                        intervention_authority,
                        preemption.authority,
                        connection=connection,
                    )
                    current = _rebuild_workforce_state_on_connection(
                        self.database,
                        connection,
                    ).package(requested.package_id)
                    _gate(
                        self.database,
                        self.controller,
                        stop_path=self.stop_path,
                        package=current.package,
                    )
                    self._directive_on_connection(
                        connection,
                        requested,
                        intervention_authority,
                    )
                    self._assert_control_authorities(
                        intervention_authority,
                        preemption.authority,
                        connection=connection,
                    )
                    _gate(
                        self.database,
                        self.controller,
                        stop_path=self.stop_path,
                        package=current.package,
                    )
                return RetryPreparation(
                    terminated_owned_process=(
                        preemption.termination.terminated_live_process
                    ),
                )
            except PermissionError:
                stop_reason = self._current_stop_reason(
                    requested.package_id
                )
                if stop_reason is None:
                    raise
                return self._project_stop_after_termination(
                    requested,
                    session,
                    preemption.termination,
                    stop_reason,
                    intervention_authority,
                    preemption.authority,
                )

    def _terminate(
        self,
        requested: InterventionRecord,
        session: WorkerSession,
        workers: WorkerController | None,
        intervention_authority: _InterventionAuthority,
    ) -> bool:
        def validate_current(connection: Any | None) -> ExecutionAdmissionState:
            return self._validate_current_for_preemption(
                requested,
                connection=connection,
            )

        def terminate_owned(
            admission_authority,
        ) -> OwnedProcessTerminationResult:
            current_session = self._current_session_for_preemption(
                requested,
                connection=None,
            )

            def assert_authority() -> None:
                self._assert_control_authorities(
                    intervention_authority,
                    admission_authority,
                )

            assert_authority()
            result = self._terminate_owned(
                current_session,
                workers,
                control_authority_check=assert_authority,
            )
            assert_authority()
            return result

        with self.gateway._preemptible_admission_guard(
            requested.package_id,
            session.run_id,
            validate_current=validate_current,
            can_terminate_owned=lambda: self._can_terminate_owned(
                self._current_session_for_preemption(
                    requested,
                    connection=None,
                ),
                workers,
            ),
            terminate_owned=terminate_owned,
        ) as preemption:
            with self.database.transaction() as connection:
                self._assert_control_authorities(
                    intervention_authority,
                    preemption.authority,
                    connection=connection,
                )
                state = _rebuild_workforce_state_on_connection(
                    self.database,
                    connection,
                )
                session = state.sessions[requested.session_id]
                self._require_current_session(
                    state,
                    requested.package_id,
                    session,
                )
                self._assert_control_authorities(
                    intervention_authority,
                    preemption.authority,
                    connection=connection,
                )
                self._mark_terminated_on_connection(
                    connection,
                    session,
                    requested,
                    reason="owned worker terminated by governed intervention",
                )
                package_status = state.package(requested.package_id).status
                target = (
                    "REJECTED"
                    if package_status == "VERIFYING"
                    else "CANCELLED"
                )
                _transition_work_package_on_connection(
                    self.database,
                    connection,
                    requested.package_id,
                    target,
                    reason=requested.reason,
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                    allow_stop=True,
                )
                self.gateway._release_on_connection(
                    connection,
                    requested.package_id,
                    run_id=session.run_id,
                    reason="terminated package released mutable scope",
                )
                self._assert_control_authorities(
                    intervention_authority,
                    preemption.authority,
                    connection=connection,
                )
            return preemption.termination.terminated_live_process

    def _retry(
        self,
        requested: InterventionRecord,
        session: WorkerSession,
        *,
        workers: WorkerController | None,
        mission_loop: CanonicalMissionLoop | None,
        intervention_authority: _InterventionAuthority,
    ) -> InterventionExecution:
        self._assert_intervention_authority(intervention_authority)
        package = rebuild_workforce_state(self.database).package(
            requested.package_id
        ).package
        if self._canonically_accepted(package, session.run_id):
            completed = self._complete(
                requested,
                outcome="BLOCKED_CANONICAL_ACCEPTANCE",
                intervention_authority=intervention_authority,
            )
            return self._execution(
                completed,
                blocked_reason=(
                    "canonical verifier already accepted the prior worker run"
                ),
            )
        pending_effect = self._pending_effect(
            session.run_id,
            package.effect_action_key,
        )
        if pending_effect is not None:
            completed = self._complete(
                requested,
                outcome="BLOCKED_EFFECT_RECONCILIATION",
                intervention_authority=intervention_authority,
            )
            return self._execution(
                completed,
                blocked_reason=(
                    f"effect {pending_effect} must be reconciled before retry"
                ),
            )
        preparation = self._prepare_retry(
            requested,
            session,
            workers,
            mission_loop=mission_loop,
            intervention_authority=intervention_authority,
        )
        if preparation.blocked_outcome is not None:
            completed = self._complete(
                requested,
                outcome=preparation.blocked_outcome,
                replacement_session_id=(
                    preparation.replacement_binding.session.session_id
                    if preparation.replacement_binding is not None
                    else None
                ),
                intervention_authority=intervention_authority,
            )
            return self._execution(
                completed,
                terminated_owned_process=(
                    preparation.terminated_owned_process
                ),
                blocked_reason=preparation.blocked_reason,
            )
        terminated = preparation.terminated_owned_process
        replacement_session_id = (
            preparation.replacement_binding.session.session_id
            if preparation.replacement_binding is not None
            else None
        )
        if mission_loop is None:
            completed = self._complete(
                requested,
                outcome="READY_FOR_RELAUNCH",
                replacement_session_id=replacement_session_id,
                intervention_authority=intervention_authority,
            )
            return self._execution(
                completed,
                terminated_owned_process=terminated,
                replacement_session_id=replacement_session_id,
            )

        launch = preparation.launch
        if launch is None or replacement_session_id is None:
            raise WorkforceSteeringError(
                "retry relaunch completed without an exact replacement result"
            )
        outcome = f"RELAUNCHED_{launch.package_status}"
        completed = self._complete(
            requested,
            outcome=outcome,
            replacement_session_id=replacement_session_id,
            intervention_authority=intervention_authority,
        )
        return self._execution(
            completed,
            terminated_owned_process=terminated,
            replacement_session_id=replacement_session_id,
            launch=launch,
        )

    def _prepare_retry(
        self,
        requested: InterventionRecord,
        session: WorkerSession,
        workers: WorkerController | None,
        *,
        mission_loop: CanonicalMissionLoop | None,
        intervention_authority: _InterventionAuthority,
    ) -> RetryPreparation:
        def validate_current(connection: Any | None) -> ExecutionAdmissionState:
            return self._validate_current_for_preemption(
                requested,
                connection=connection,
            )

        def terminate_owned(
            admission_authority,
        ) -> OwnedProcessTerminationResult:
            current_session = self._current_session_for_preemption(
                requested,
                connection=None,
            )

            def assert_authority() -> None:
                self._assert_control_authorities(
                    intervention_authority,
                    admission_authority,
                )

            assert_authority()
            result = self._terminate_owned(
                current_session,
                workers,
                control_authority_check=assert_authority,
            )
            assert_authority()
            return result

        with self.gateway._preemptible_admission_guard(
            requested.package_id,
            session.run_id,
            validate_current=validate_current,
            can_terminate_owned=lambda: self._can_terminate_owned(
                self._current_session_for_preemption(
                    requested,
                    connection=None,
                ),
                workers,
            ),
            terminate_owned=terminate_owned,
        ) as preemption:
            replacement: GatewayBinding | None = None
            try:
                with self.database.transaction() as connection:
                    self._assert_control_authorities(
                        intervention_authority,
                        preemption.authority,
                        connection=connection,
                    )
                    state = _rebuild_workforce_state_on_connection(
                        self.database,
                        connection,
                    )
                    current_session = state.sessions[requested.session_id]
                    self._require_current_session(
                        state,
                        requested.package_id,
                        current_session,
                    )
                    settled = (
                        self._settle_post_termination_hazard_on_connection(
                            connection,
                            requested,
                            current_session,
                            preemption.termination,
                            intervention_authority,
                            preemption.authority,
                        )
                    )
                    if settled is not None:
                        return settled
                    self._mark_terminated_on_connection(
                        connection,
                        current_session,
                        requested,
                        reason=(
                            f"{requested.action} superseded the prior attempt"
                        ),
                    )
                    resource_id = str(
                        current_session.result.get("resource_id") or ""
                    )
                    if resource_id:
                        connection.execute(
                            "DELETE FROM leases "
                            "WHERE lease_key = ? AND owner_id = ?",
                            (
                                f"workforce-resource:{resource_id}",
                                current_session.run_id,
                            ),
                        )
                    current = state.package(requested.package_id)
                    if current.status == "PLANNED":
                        raise WorkforceSteeringError(
                            "package worktree must be prepared before retry"
                        )
                    if current.status in {
                        "RUNNING",
                        "WAITING",
                        "VERIFYING",
                    }:
                        current = _transition_work_package_on_connection(
                            self.database,
                            connection,
                            requested.package_id,
                            "FAILED",
                            reason=(
                                f"{requested.action} prepared a fresh attempt"
                            ),
                            controller=self.controller,
                            stop_path=self.stop_path,
                            source_id=self.source_id,
                        )
                    if current.status in {"FAILED", "STOPPED"}:
                        current = _transition_work_package_on_connection(
                            self.database,
                            connection,
                            requested.package_id,
                            "READY",
                            reason=(
                                f"{requested.action} admitted a fresh bounded "
                                "attempt"
                            ),
                            controller=self.controller,
                            stop_path=self.stop_path,
                            source_id=self.source_id,
                        )
                    if current.status != "READY":
                        raise WorkforceSteeringError(
                            "package cannot retry from state "
                            f"{current.status}"
                        )
                    retry_event = _event(
                        "workforce.retry_prepared",
                        {
                            "package_id": requested.package_id,
                            "prior_session_id": current_session.session_id,
                            "prior_run_id": current_session.run_id,
                            "action": requested.action,
                            "intervention_id": requested.intervention_id,
                            "replacement_framework": (
                                requested.replacement_framework
                            ),
                            "package_status": current.status,
                        },
                        key=(
                            "workforce.retry_prepared:"
                            f"{requested.intervention_id}:"
                            f"{current_session.session_id}"
                        ),
                        correlation_id=requested.package_id,
                        source_id=self.source_id,
                    )
                    self.database.append_events_on_connection(
                        connection,
                        [retry_event],
                    )
                    self._assert_control_authorities(
                        intervention_authority,
                        preemption.authority,
                        connection=connection,
                    )
                    replacement = self.gateway._bind_on_connection(
                        connection,
                        requested.package_id,
                        admission_authority=preemption.authority,
                        predecessor_session=current_session,
                        retry_intervention_id=requested.intervention_id,
                    )
                    self._assert_control_authorities(
                        intervention_authority,
                        preemption.authority,
                        connection=connection,
                    )
                    _gate(
                        self.database,
                        self.controller,
                        stop_path=self.stop_path,
                        package=current.package,
                    )
                preparation = RetryPreparation(
                    terminated_owned_process=(
                        preemption.termination.terminated_live_process
                    ),
                    replacement_binding=replacement,
                )
                if mission_loop is None:
                    return preparation
                try:
                    launch = self.gateway.run_inline(
                        requested.package_id,
                        mission_loop,
                        expected_run_id=replacement.session.run_id,
                        worker_preference=(
                            requested.replacement_framework
                            if requested.action == "REASSIGN"
                            else None
                        ),
                        worker_preference_evidence=(
                            (f"intervention:{requested.intervention_id}",)
                            if requested.action == "REASSIGN"
                            else ()
                        ),
                        admission_authority=preemption.authority,
                        control_authority_check=lambda connection=None: (
                            self._assert_retry_control_authority(
                                requested,
                                current_session.run_id,
                                replacement.session,
                                intervention_authority,
                                preemption.authority,
                                connection=connection,
                            )
                        ),
                    )
                except (PermissionError, WorkforceGatewayError):
                    with self.database.transaction() as connection:
                        settled = (
                            self._settle_post_termination_hazard_on_connection(
                                connection,
                                requested,
                                current_session,
                                preemption.termination,
                                intervention_authority,
                                preemption.authority,
                                replacement_session_id=(
                                    replacement.session.session_id
                                ),
                            )
                        )
                    if settled is not None:
                        return replace(
                            settled,
                            replacement_binding=replacement,
                        )
                    raise
                return replace(preparation, launch=launch)
            except PermissionError:
                stop_reason = self._current_stop_reason(
                    requested.package_id
                )
                if stop_reason is None:
                    raise
                return self._project_stop_after_termination(
                    requested,
                    session,
                    preemption.termination,
                    stop_reason,
                    intervention_authority,
                    preemption.authority,
                    replacement_session_id=(
                        replacement.session.session_id
                        if replacement is not None
                        else None
                    ),
                )

    def _move_to_waiting(
        self,
        requested: InterventionRecord,
        intervention_authority: _InterventionAuthority,
    ) -> None:
        with self.database.transaction() as connection:
            self._assert_intervention_authority(
                intervention_authority,
                connection=connection,
            )
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            session = state.sessions[requested.session_id]
            self._require_current_session(
                state,
                requested.package_id,
                session,
            )
            current = state.package(requested.package_id)
            if current.status in {"READY", "RUNNING", "VERIFYING"}:
                _transition_work_package_on_connection(
                    self.database,
                    connection,
                    requested.package_id,
                    "WAITING",
                    reason=requested.reason,
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                )
            self._assert_intervention_authority(
                intervention_authority,
                connection=connection,
            )
            current = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            ).package(requested.package_id)
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=current.package,
            )

    def _directive(
        self,
        intervention: InterventionRecord,
        intervention_authority: _InterventionAuthority,
    ) -> None:
        with self.database.transaction() as connection:
            self._assert_intervention_authority(
                intervention_authority,
                connection=connection,
            )
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            session = state.sessions[intervention.session_id]
            self._require_current_session(
                state,
                intervention.package_id,
                session,
            )
            package = state.package(intervention.package_id).package
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package,
            )
            self._assert_intervention_authority(
                intervention_authority,
                connection=connection,
            )
            self._directive_on_connection(
                connection,
                intervention,
                intervention_authority,
            )
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package,
            )

    def _directive_on_connection(
        self,
        connection,
        intervention: InterventionRecord,
        intervention_authority: _InterventionAuthority,
    ) -> None:
        self._assert_intervention_authority(
            intervention_authority,
            connection=connection,
        )
        event = _event(
            "workforce.steering_directive_issued",
            intervention.to_payload(),
            key=(
                f"workforce.steering_directive_issued:"
                f"{intervention.intervention_id}"
            ),
            correlation_id=intervention.package_id,
            source_id=self.source_id,
        )
        self.database.append_events_on_connection(connection, [event])
        self._assert_intervention_authority(
            intervention_authority,
            connection=connection,
        )

    def _mark_terminated(
        self,
        session: WorkerSession,
        intervention: InterventionRecord,
        *,
        reason: str,
    ) -> WorkerSession:
        with self.database.transaction() as connection:
            return self._mark_terminated_on_connection(
                connection,
                session,
                intervention,
                reason=reason,
            )

    def _mark_terminated_on_connection(
        self,
        connection,
        session: WorkerSession,
        intervention: InterventionRecord,
        *,
        reason: str,
    ) -> WorkerSession:
        if session.status in TERMINAL_SESSION_STATUSES:
            return session
        terminated = replace(
            session,
            status="TERMINATED",
            finished_at=session.finished_at or isoformat(),
            result={
                **session.result,
                "termination_reason": reason,
                "intervention_id": intervention.intervention_id,
            },
        )
        event = _event(
            "workforce.session_state_changed",
            _versioned_session_payload_on_connection(
                connection,
                terminated,
            ),
            key=(
                f"workforce.session_state_changed:{session.session_id}:"
                f"terminated:{intervention.intervention_id}"
            ),
            correlation_id=session.package_id,
            source_id=self.source_id,
        )
        self.database.append_events_on_connection(connection, [event])
        return terminated

    def _terminate_owned(
        self,
        session: WorkerSession,
        workers: WorkerController | None,
        *,
        control_authority_check: Callable[[], None] | None = None,
    ) -> OwnedProcessTerminationResult:
        if control_authority_check is not None:
            control_authority_check()
        worker_key = worker_lease_key(session.run_id)
        lease_before = self.database.row("leases", "lease_key", worker_key)
        pid_path = adapter_pid_table_path(self.database)
        pid_before = self.gateway._load_pid_records(pid_path).get(
            session.run_id
        )
        if workers is None:
            if lease_before is not None or pid_before is not None:
                raise WorkforceSteeringError(
                    "owned process evidence exists but no WorkerController "
                    "was supplied"
                )
            return OwnedProcessTerminationResult(
                terminated_live_process=False,
                cleanup_succeeded=True,
            )
        status_before = workers.status(session.run_id)
        if lease_before is not None and (
            str(lease_before["owner_id"]) != worker_key
        ):
            raise WorkforceSteeringError(
                "worker lease changed owner before exact cleanup: "
                f"{session.run_id}"
            )
        if (
            pid_before is not None
            and not self._pid_record_matches_session(session, pid_before)
        ):
            raise WorkforceSteeringError(
                "worker PID registration no longer names the session's "
                f"process epoch: {session.run_id}"
            )
        root_epoch: OwnedProcessEpoch | None = None
        if pid_before is not None:
            root_pid = int(pid_before["pid"])
            root_epoch = self.gateway._root_process_epoch(
                root_pid,
                session.result.get("process_creation_identity"),
            )
            if root_epoch is None:
                raise WorkforceSteeringError(
                    "worker process epoch is not durably identifiable: "
                    f"{session.run_id}"
                )
        process_before = status_before.process
        epoch = (
            self._owned_process_epoch(
                session,
                workers,
                status=status_before,
                pid_record=pid_before,
            )
            if pid_before is not None
            else None
        )
        terminated_live_process = False
        if epoch is not None:
            if lease_before is None:
                raise WorkforceSteeringError(
                    "live workforce process lacks exact durable lease and PID "
                    f"registration: {session.run_id}"
                )
            terminated_live_process = self.gateway._terminate_process_epoch(
                epoch,
                control_authority_check=control_authority_check,
            )
        elif status_before.alive or (
            root_epoch is not None
            and self.gateway._process_epoch_is_alive(root_epoch)
        ):
            raise WorkforceSteeringError(
                "live workforce process epoch changed or became ambiguous "
                f"before termination: {session.run_id}"
            )
        tracked = getattr(workers, "_processes", {}).get(session.run_id)
        if (
            (process_before is not None and tracked is not process_before)
            or (process_before is None and tracked is not None)
        ):
            raise WorkforceSteeringError(
                "worker process tracking changed before exact cleanup: "
                f"{session.run_id}"
            )
        pid_current = self.gateway._load_pid_records(pid_path).get(
            session.run_id
        )
        if pid_current != pid_before:
            raise WorkforceSteeringError(
                "worker PID registration changed before exact cleanup: "
                f"{session.run_id}"
            )
        if control_authority_check is not None:
            control_authority_check()
        workers.detach(session.run_id)
        if control_authority_check is not None:
            control_authority_check()
        leased_runs = getattr(workers, "_leased", None)
        if leased_runs is not None:
            leased_runs.discard(session.run_id)
        lease_current = self.database.row(
            "leases",
            "lease_key",
            worker_key,
        )
        if lease_current is not None:
            if str(lease_current["owner_id"]) != worker_key:
                raise WorkforceSteeringError(
                    "worker lease changed owner during exact cleanup: "
                    f"{session.run_id}"
                )
            if control_authority_check is not None:
                control_authority_check()
            workers.leases.release(worker_key, worker_key)
            if control_authority_check is not None:
                control_authority_check()
        status_after = workers.status(session.run_id)
        lease_after = self.database.row("leases", "lease_key", worker_key)
        pid_after = self.gateway._load_pid_records(pid_path).get(
            session.run_id
        )
        exact_identity_confirmed_dead = (
            (
                root_epoch is None
                or not self.gateway._process_epoch_is_alive(root_epoch)
            )
            and (
                epoch is None
                or not self.gateway._process_epoch_is_alive(epoch)
            )
        )
        cleanup_succeeded = (
            not status_after.alive
            and exact_identity_confirmed_dead
            and lease_after is None
            and pid_after is None
            and (
                leased_runs is None
                or session.run_id not in leased_runs
            )
        )
        if not cleanup_succeeded:
            raise WorkforceSteeringError(
                "owned process termination did not clear live process, lease, "
                f"and PID state: {session.run_id}"
            )
        if control_authority_check is not None:
            control_authority_check()
        return OwnedProcessTerminationResult(
            terminated_live_process=bool(
                epoch is not None
                and terminated_live_process
                and exact_identity_confirmed_dead
            ),
            cleanup_succeeded=True,
        )

    def _can_terminate_owned(
        self,
        session: WorkerSession,
        workers: WorkerController | None,
    ) -> bool:
        if workers is None:
            return False
        worker_key = worker_lease_key(session.run_id)
        lease = self.database.row("leases", "lease_key", worker_key)
        if lease is not None and str(lease["owner_id"]) != worker_key:
            return False
        pid_record = self.gateway._load_pid_records(
            adapter_pid_table_path(self.database)
        ).get(session.run_id)
        if (
            pid_record is not None
            and not self._pid_record_matches_session(session, pid_record)
        ):
            return False
        status = workers.status(session.run_id)
        epoch = (
            self._owned_process_epoch(
                session,
                workers,
                status=status,
                pid_record=pid_record,
            )
            if pid_record is not None
            else None
        )
        if epoch is not None:
            return lease is not None
        if pid_record is not None:
            root_epoch = self.gateway._root_process_epoch(
                int(pid_record["pid"]),
                session.result.get("process_creation_identity"),
            )
            if (
                root_epoch is None
                or self.gateway._process_epoch_is_alive(root_epoch)
            ):
                return False
        if status.alive:
            return False
        return bool(lease is not None or pid_record is not None)

    def _owned_process_epoch(
        self,
        session: WorkerSession,
        workers: WorkerController,
        *,
        status=None,
        pid_record: Mapping[str, Any] | None = None,
    ) -> OwnedProcessEpoch | None:
        observed_status = status or workers.status(session.run_id)
        record = (
            pid_record
            if pid_record is not None
            else self.gateway._load_pid_records(
                adapter_pid_table_path(self.database)
            ).get(session.run_id)
        )
        if not isinstance(record, Mapping):
            return None
        try:
            root_pid = int(record["pid"])
        except (KeyError, TypeError, ValueError):
            return None
        session_pid = session.result.get("pid")
        if isinstance(session_pid, bool) or session_pid is None:
            return None
        try:
            if int(session_pid) != root_pid:
                return None
        except (TypeError, ValueError):
            return None
        if (
            observed_status.process is not None
            and observed_status.process.pid != root_pid
        ):
            return None
        session_identity = self.gateway._process_identity_payload(
            session.result.get("process_creation_identity"),
            expected_pid=root_pid,
        )
        record_identity = self.gateway._process_identity_payload(
            record.get("creation_identity"),
            expected_pid=root_pid,
        )
        if (
            session_identity is None
            or record_identity is None
            or session_identity != record_identity
        ):
            return None
        return self.gateway._capture_process_epoch(
            root_pid,
            expected_identity=session_identity,
        )

    def _pid_record_matches_session(
        self,
        session: WorkerSession,
        pid_record: Mapping[str, Any],
    ) -> bool:
        try:
            pid = int(pid_record["pid"])
        except (KeyError, TypeError, ValueError):
            return False
        session_pid = session.result.get("pid")
        if isinstance(session_pid, bool) or session_pid is None:
            return False
        try:
            if int(session_pid) != pid:
                return False
        except (TypeError, ValueError):
            return False
        return self.gateway._same_process_identity(
            session.result.get("process_creation_identity"),
            pid_record.get("creation_identity"),
            expected_pid=pid,
        )

    def _assert_control_authorities(
        self,
        intervention_authority: _InterventionAuthority,
        admission_authority,
        *,
        connection: Any | None = None,
    ) -> None:
        self._assert_intervention_authority(
            intervention_authority,
            connection=connection,
        )
        self.gateway._assert_admission_authority(
            admission_authority,
            connection=connection,
        )

    def _current_stop_reason(self, package_id: str) -> str | None:
        package = rebuild_workforce_state(self.database).package(
            package_id
        ).package
        return _stop_reason(
            self.database,
            self.stop_path,
            package=package,
        )

    def _assert_retry_control_authority(
        self,
        requested: InterventionRecord,
        predecessor_run_id: str,
        replacement_session: WorkerSession,
        intervention_authority: _InterventionAuthority,
        admission_authority,
        *,
        connection: Any | None = None,
    ) -> None:
        if connection is None:
            self._assert_control_authorities(
                intervention_authority,
                admission_authority,
            )
            with self.database.connect(readonly=True) as read_connection:
                self._assert_retry_control_authority_on_connection(
                    read_connection,
                    requested,
                    predecessor_run_id,
                    replacement_session,
                    intervention_authority,
                    admission_authority,
                )
            return
        self._assert_retry_control_authority_on_connection(
            connection,
            requested,
            predecessor_run_id,
            replacement_session,
            intervention_authority,
            admission_authority,
        )

    def _assert_retry_control_authority_on_connection(
        self,
        connection,
        requested: InterventionRecord,
        predecessor_run_id: str,
        replacement_session: WorkerSession,
        intervention_authority: _InterventionAuthority,
        admission_authority,
    ) -> None:
        self._assert_control_authorities(
            intervention_authority,
            admission_authority,
            connection=connection,
        )
        state = _rebuild_workforce_state_on_connection(
            self.database,
            connection,
        )
        package_state = state.package(requested.package_id)
        stop_reason = _stop_reason(
            self.database,
            self.stop_path,
            package=package_state.package,
        )
        if stop_reason is not None:
            raise PermissionError(f"workforce stopped: {stop_reason}")
        sessions = state.sessions_for(requested.package_id)
        latest = max(sessions, key=lambda item: item.attempt)
        if (
            latest.session_id != replacement_session.session_id
            or latest.run_id != replacement_session.run_id
            or str(latest.result.get("predecessor_run_id") or "")
            != predecessor_run_id
            or str(latest.result.get("retry_intervention_id") or "")
            != requested.intervention_id
        ):
            raise WorkforceGatewayError(
                "replacement relaunch no longer names the exact "
                f"intervention epoch: {requested.intervention_id}"
            )
        try:
            receipt = _acceptance_receipt_on_connection(
                connection,
                state,
                package_state,
                builder_run_id=predecessor_run_id,
            )
        except WorkforceStateError:
            receipt = None
        if receipt is not None:
            raise WorkforceGatewayError(
                "replacement relaunch is blocked by canonical acceptance "
                f"of the predecessor run: {predecessor_run_id}"
            )
        pending_effect = _pending_effect_on_connection(
            self.database,
            connection,
            predecessor_run_id,
            package_state.package.effect_action_key,
        )
        if pending_effect is not None:
            raise WorkforceGatewayError(
                "replacement relaunch is blocked until predecessor "
                f"effect {pending_effect} is reconciled"
            )

    def _settle_post_termination_hazard_on_connection(
        self,
        connection,
        requested: InterventionRecord,
        predecessor_session: WorkerSession,
        termination: OwnedProcessTerminationResult,
        intervention_authority: _InterventionAuthority,
        admission_authority,
        *,
        replacement_session_id: str | None = None,
    ) -> RetryPreparation | None:
        self._assert_control_authorities(
            intervention_authority,
            admission_authority,
            connection=connection,
        )
        state = _rebuild_workforce_state_on_connection(
            self.database,
            connection,
        )
        predecessor = state.sessions[predecessor_session.session_id]
        if predecessor.run_id != predecessor_session.run_id:
            raise WorkforceSteeringError(
                "predecessor session identity changed during intervention"
            )
        replacement_session = None
        if replacement_session_id is None:
            self._require_current_session(
                state,
                requested.package_id,
                predecessor,
            )
        else:
            try:
                replacement_session = state.sessions[
                    replacement_session_id
                ]
            except KeyError as error:
                raise WorkforceSteeringError(
                    "replacement session disappeared during hazard "
                    f"settlement: {replacement_session_id}"
                ) from error
            self._require_current_session(
                state,
                requested.package_id,
                replacement_session,
            )
            if (
                str(
                    replacement_session.result.get(
                        "predecessor_run_id"
                    )
                    or ""
                )
                != predecessor.run_id
                or str(
                    replacement_session.result.get(
                        "retry_intervention_id"
                    )
                    or ""
                )
                != requested.intervention_id
            ):
                raise WorkforceSteeringError(
                    "replacement session does not belong to the exact retry "
                    f"intervention: {requested.intervention_id}"
                )
        package_state = state.package(requested.package_id)
        stop_reason = _stop_reason(
            self.database,
            self.stop_path,
            package=package_state.package,
        )
        if stop_reason is not None:
            return self._project_stop_after_termination_on_connection(
                connection,
                requested,
                predecessor,
                termination,
                stop_reason,
                intervention_authority,
                admission_authority,
                replacement_session=replacement_session,
            )
        try:
            receipt = _acceptance_receipt_on_connection(
                connection,
                state,
                package_state,
                builder_run_id=predecessor.run_id,
            )
        except WorkforceStateError:
            receipt = None
        if receipt is not None:
            self._mark_hazard_sessions_on_connection(
                connection,
                requested,
                predecessor,
                replacement_session,
                reason=(
                    "canonical acceptance closed the preempted attempt"
                ),
            )
            current = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            ).package(requested.package_id)
            if current.status in {"WAITING", "FAILED", "STOPPED"}:
                current = _transition_work_package_on_connection(
                    self.database,
                    connection,
                    requested.package_id,
                    "READY",
                    reason=(
                        "canonical acceptance resumed lifecycle projection "
                        "without relaunch"
                    ),
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                )
            if current.status in {"READY", "RUNNING"}:
                current = _transition_work_package_on_connection(
                    self.database,
                    connection,
                    requested.package_id,
                    "VERIFYING",
                    reason=(
                        "canonical verifier accepted the preempted worker "
                        "result"
                    ),
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                    acceptance_builder_run_id=predecessor.run_id,
                )
            if current.status == "VERIFYING":
                current = _transition_work_package_on_connection(
                    self.database,
                    connection,
                    requested.package_id,
                    "ACCEPTED",
                    reason=(
                        "canonical verifier accepted the preempted worker "
                        "result"
                    ),
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                    acceptance_builder_run_id=predecessor.run_id,
                )
            if current.status != "ACCEPTED":
                raise WorkforceSteeringError(
                    "canonical acceptance cannot settle package from state "
                    f"{current.status}"
                )
            released_run_id = (
                replacement_session.run_id
                if replacement_session is not None
                else predecessor.run_id
            )
            self.gateway._release_on_connection(
                connection,
                requested.package_id,
                run_id=released_run_id,
                reason=(
                    "accepted preempted package released mutable scope"
                ),
            )
            self._assert_control_authorities(
                intervention_authority,
                admission_authority,
                connection=connection,
            )
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=current.package,
            )
            return RetryPreparation(
                terminated_owned_process=(
                    termination.terminated_live_process
                ),
                blocked_outcome="BLOCKED_CANONICAL_ACCEPTANCE",
                blocked_reason=(
                    "canonical verifier already accepted the prior worker run"
                ),
            )
        pending_effect = _pending_effect_on_connection(
            self.database,
            connection,
            predecessor.run_id,
            package_state.package.effect_action_key,
        )
        if pending_effect is None:
            return None
        self._mark_hazard_sessions_on_connection(
            connection,
            requested,
            predecessor,
            replacement_session,
            reason=(
                "pending irreversible effect closed the preempted attempt"
            ),
        )
        current = _rebuild_workforce_state_on_connection(
            self.database,
            connection,
        ).package(requested.package_id)
        if current.status in {"READY", "RUNNING", "VERIFYING"}:
            _transition_work_package_on_connection(
                self.database,
                connection,
                requested.package_id,
                "WAITING",
                reason=(
                    f"effect {pending_effect} requires reconciliation before "
                    "retry"
                ),
                controller=self.controller,
                stop_path=self.stop_path,
                source_id=self.source_id,
            )
        current = _rebuild_workforce_state_on_connection(
            self.database,
            connection,
        ).package(requested.package_id)
        self._assert_control_authorities(
            intervention_authority,
            admission_authority,
            connection=connection,
        )
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=current.package,
        )
        return RetryPreparation(
            terminated_owned_process=termination.terminated_live_process,
            blocked_outcome="BLOCKED_EFFECT_RECONCILIATION",
            blocked_reason=(
                f"effect {pending_effect} must be reconciled before retry"
            ),
        )

    def _project_stop_after_termination(
        self,
        requested: InterventionRecord,
        predecessor_session: WorkerSession,
        termination: OwnedProcessTerminationResult,
        stop_reason: str,
        intervention_authority: _InterventionAuthority,
        admission_authority,
        *,
        replacement_session_id: str | None = None,
    ) -> RetryPreparation:
        with self.database.transaction() as connection:
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            predecessor = state.sessions[
                predecessor_session.session_id
            ]
            if (
                predecessor.package_id != requested.package_id
                or predecessor.run_id != predecessor_session.run_id
            ):
                raise WorkforceSteeringError(
                    "STOP projection predecessor identity changed during "
                    f"intervention: {requested.intervention_id}"
                )
            replacement_session = None
            if replacement_session_id is None:
                self._require_current_session(
                    state,
                    requested.package_id,
                    predecessor,
                )
            else:
                try:
                    replacement_session = state.sessions[
                        replacement_session_id
                    ]
                except KeyError as error:
                    raise WorkforceSteeringError(
                        "STOP projection replacement session disappeared: "
                        f"{replacement_session_id}"
                    ) from error
                self._require_current_session(
                    state,
                    requested.package_id,
                    replacement_session,
                )
                if (
                    str(
                        replacement_session.result.get(
                            "predecessor_run_id"
                        )
                        or ""
                    )
                    != predecessor.run_id
                    or str(
                        replacement_session.result.get(
                            "retry_intervention_id"
                        )
                        or ""
                    )
                    != requested.intervention_id
                ):
                    raise WorkforceSteeringError(
                        "replacement STOP projection does not belong to the "
                        f"exact intervention: {requested.intervention_id}"
                    )
            return self._project_stop_after_termination_on_connection(
                connection,
                requested,
                predecessor,
                termination,
                stop_reason,
                intervention_authority,
                admission_authority,
                replacement_session=replacement_session,
            )

    def _project_stop_after_termination_on_connection(
        self,
        connection,
        requested: InterventionRecord,
        predecessor_session: WorkerSession,
        termination: OwnedProcessTerminationResult,
        stop_reason: str,
        intervention_authority: _InterventionAuthority,
        admission_authority,
        *,
        replacement_session: WorkerSession | None = None,
    ) -> RetryPreparation:
        self._assert_control_authorities(
            intervention_authority,
            admission_authority,
            connection=connection,
        )
        self._mark_hazard_sessions_on_connection(
            connection,
            requested,
            predecessor_session,
            replacement_session,
            reason=f"owner STOP closed the preempted attempt: {stop_reason}",
        )
        current = _rebuild_workforce_state_on_connection(
            self.database,
            connection,
        ).package(requested.package_id)
        if current.status not in {
            "ACCEPTED",
            "REJECTED",
            "CANCELLED",
            "STOPPED",
        }:
            _transition_work_package_on_connection(
                self.database,
                connection,
                requested.package_id,
                "STOPPED",
                reason=stop_reason,
                controller=self.controller,
                stop_path=self.stop_path,
                source_id=self.source_id,
                allow_stop=True,
            )
        released: set[str] = set()
        for candidate in (replacement_session, predecessor_session):
            if candidate is None or candidate.run_id in released:
                continue
            self.gateway._release_on_connection(
                connection,
                requested.package_id,
                run_id=candidate.run_id,
                reason="owner STOP released preempted mutable scope",
            )
            released.add(candidate.run_id)
        self._assert_control_authorities(
            intervention_authority,
            admission_authority,
            connection=connection,
        )
        return RetryPreparation(
            terminated_owned_process=termination.terminated_live_process,
            blocked_outcome="BLOCKED_STOPPED",
            blocked_reason=(
                "owner STOP arrived after physical termination: "
                f"{stop_reason}"
            ),
        )

    def _mark_hazard_sessions_on_connection(
        self,
        connection,
        requested: InterventionRecord,
        predecessor_session: WorkerSession,
        replacement_session: WorkerSession | None,
        *,
        reason: str,
    ) -> None:
        for session in (predecessor_session, replacement_session):
            if session is None:
                continue
            self._mark_terminated_on_connection(
                connection,
                session,
                requested,
                reason=reason,
            )

    def _validate_current_for_preemption(
        self,
        requested: InterventionRecord,
        *,
        connection: Any | None,
    ) -> ExecutionAdmissionState:
        current_session = self._current_session_for_preemption(
            requested,
            connection=connection,
        )
        return self._execution_admission_state(current_session)

    def _current_session_for_preemption(
        self,
        requested: InterventionRecord,
        *,
        connection: Any | None,
    ) -> WorkerSession:
        state = (
            _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            if connection is not None
            else rebuild_workforce_state(self.database)
        )
        current_session = state.sessions[requested.session_id]
        self._require_current_session(
            state,
            requested.package_id,
            current_session,
        )
        return current_session

    @staticmethod
    def _execution_admission_state(
        session: WorkerSession,
    ) -> ExecutionAdmissionState:
        launch_kind = str(session.result.get("launch_kind") or "")
        launch_intent = str(session.result.get("launch_intent") or "")
        process_termination_required = bool(
            launch_intent == "ADMITTED" or launch_kind
        )
        externally_controllable = launch_kind == "SEALED_PACKAGE_RUNNER"
        raw_pid = session.result.get("pid")
        try:
            registered_pid = (
                None
                if isinstance(raw_pid, bool) or raw_pid is None
                else int(raw_pid)
            )
        except (TypeError, ValueError):
            registered_pid = None
        creation_identity = (
            WorkforceGateway._process_identity_payload(
                session.result.get("process_creation_identity"),
                expected_pid=registered_pid,
            )
            if registered_pid is not None
            else None
        )
        process_registration_complete = bool(
            not process_termination_required
            or (
                externally_controllable
                and session.result.get("launch_phase")
                == "PROCESS_REGISTERED"
                and registered_pid is not None
                and session.result.get("worker_lease_key")
                and creation_identity is not None
            )
        )
        return ExecutionAdmissionState(
            terminal=session.status in TERMINAL_SESSION_STATUSES,
            process_termination_required=process_termination_required,
            externally_controllable=externally_controllable,
            process_registration_complete=process_registration_complete,
        )

    def _pending_effect(
        self,
        run_id: str,
        package_effect_action_key: str | None,
    ) -> str | None:
        return _pending_effect_from_events(
            self.database.events(readonly=True),
            run_id,
            package_effect_action_key,
        )

    def _pending_effect_on_connection(
        self,
        connection,
        run_id: str,
        package_effect_action_key: str | None,
    ) -> str | None:
        return _pending_effect_on_connection(
            self.database,
            connection,
            run_id,
            package_effect_action_key,
        )

    def _acquire_intervention(
        self,
        package_id: str,
        intervention_id: str,
        *,
        terminal_replay_shortcut: bool = True,
    ) -> tuple[str, str] | None:
        lease_key = f"workforce-intervention-package:{package_id}"
        lease_owner = (
            f"{self.source_id}:{os.getpid()}:{threading.get_ident()}:"
            f"{time.monotonic_ns()}"
        )
        deadline = time.monotonic() + INTERVENTION_WAIT_SECONDS
        while True:
            try:
                self.leases.acquire(
                    lease_key,
                    lease_owner,
                    INTERVENTION_LEASE_SECONDS,
                )
                return lease_key, lease_owner
            except LeaseUnavailable as error:
                existing = rebuild_workforce_state(
                    self.database
                ).interventions.get(intervention_id)
                if (
                    terminal_replay_shortcut
                    and existing is not None
                    and existing.outcome != "REQUESTED"
                ):
                    return None
                if time.monotonic() >= deadline:
                    raise WorkforceSteeringError(
                        f"intervention is already applying: {intervention_id}"
                    ) from error
                time.sleep(0.05)

    def _start_intervention_heartbeat(
        self,
        authority: _InterventionAuthority,
    ) -> tuple[threading.Event, threading.Thread]:
        stop = threading.Event()

        def renew() -> None:
            interval = max(
                0.05,
                min(
                    INTERVENTION_LEASE_RENEW_SECONDS,
                    INTERVENTION_LEASE_SECONDS / 2,
                ),
            )
            while not stop.wait(interval):
                try:
                    self.leases.renew(
                        authority.lease_key,
                        authority.owner_id,
                        INTERVENTION_LEASE_SECONDS,
                    )
                except BaseException as error:
                    authority.heartbeat_errors.append(error)
                    return

        thread = threading.Thread(
            target=renew,
            name=(
                "workforce-intervention-lease:"
                f"{authority.owner_id}"
            ),
            daemon=True,
        )
        thread.start()
        return stop, thread

    def _assert_intervention_authority(
        self,
        authority: _InterventionAuthority,
        *,
        connection: Any | None = None,
    ) -> None:
        if authority.heartbeat_errors:
            raise WorkforceSteeringError(
                "intervention authority heartbeat failed before its next "
                "durable effect"
            ) from authority.heartbeat_errors[0]
        if connection is None:
            row = self.database.row(
                "leases",
                "lease_key",
                authority.lease_key,
            )
        else:
            row = connection.execute(
                "SELECT owner_id, expires_at FROM leases "
                "WHERE lease_key = ?",
                (authority.lease_key,),
            ).fetchone()
        if (
            row is None
            or str(row["owner_id"]) != authority.owner_id
            or parse(str(row["expires_at"])) <= utc_now()
        ):
            raise WorkforceSteeringError(
                "intervention authority was lost before its next durable "
                "effect"
            )

    @staticmethod
    def _validate_replay(
        intervention: InterventionRecord,
        *,
        package_id: str,
        session_id: str,
        observation_id: str | None,
        action: str,
        reason: str,
        instruction: str,
        replacement_framework: str | None,
    ) -> None:
        expected = (
            package_id,
            session_id,
            observation_id,
            action,
            reason,
            instruction,
            replacement_framework,
        )
        actual = (
            intervention.package_id,
            intervention.session_id,
            intervention.observation_id,
            intervention.action,
            intervention.reason,
            intervention.instruction,
            intervention.replacement_framework,
        )
        if actual != expected:
            raise WorkforceSteeringError(
                "intervention replay mismatch: id already names another "
                "immutable request: "
                f"{intervention.intervention_id}"
            )

    def _canonically_accepted(self, package, run_id: str) -> bool:
        with self.database.connect(readonly=True) as connection:
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            try:
                receipt = _acceptance_receipt_on_connection(
                    connection,
                    state,
                    state.package(package.package_id),
                    builder_run_id=run_id,
                )
            except WorkforceStateError:
                return False
        return str(receipt.get("builder_run_id") or "") == run_id

    def _canonically_accepted_on_connection(
        self,
        connection,
        package,
        run_id: str,
    ) -> bool:
        state = _rebuild_workforce_state_on_connection(
            self.database,
            connection,
        )
        try:
            receipt = _acceptance_receipt_on_connection(
                connection,
                state,
                state.package(package.package_id),
                builder_run_id=run_id,
            )
        except WorkforceStateError:
            return False
        return str(receipt.get("builder_run_id") or "") == run_id

    def _record_requested(
        self,
        intervention: InterventionRecord,
        intervention_authority: _InterventionAuthority,
    ) -> InterventionRecord:
        with self.database.transaction() as connection:
            self._assert_intervention_authority(
                intervention_authority,
                connection=connection,
            )
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            prior = state.interventions.get(
                intervention.intervention_id
            )
            if prior is not None:
                if prior != intervention:
                    raise WorkforceSteeringError(
                        "intervention id already names another immutable "
                        f"request: {intervention.intervention_id}"
                    )
                return prior
            event = _event(
                "workforce.intervention_recorded",
                intervention.to_payload(),
                key=(
                    "workforce.intervention_recorded:"
                    f"{intervention.intervention_id}"
                ),
                correlation_id=intervention.package_id,
                source_id=self.source_id,
            )
            self.database.append_events_on_connection(
                connection,
                [event],
            )
            self._assert_intervention_authority(
                intervention_authority,
                connection=connection,
            )
        return intervention

    def _complete(
        self,
        intervention: InterventionRecord,
        *,
        outcome: str,
        replacement_session_id: str | None = None,
        intervention_authority: _InterventionAuthority,
    ) -> InterventionRecord:
        with self.database.transaction() as connection:
            self._assert_intervention_authority(
                intervention_authority,
                connection=connection,
            )
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            try:
                prior = state.interventions[
                    intervention.intervention_id
                ]
            except KeyError as error:
                raise WorkforceSteeringError(
                    "cannot complete an unrecorded intervention: "
                    f"{intervention.intervention_id}"
                ) from error
            completed = replace(
                prior,
                replacement_session_id=replacement_session_id,
                outcome=outcome,
            )
            if prior.outcome != "REQUESTED":
                if prior == completed:
                    return prior
                raise WorkforceSteeringError(
                    "intervention already has another immutable terminal "
                    f"outcome: {intervention.intervention_id}"
                )
            event = _event(
                "workforce.intervention_completed",
                completed.to_payload(),
                key=(
                    "workforce.intervention_completed:"
                    f"{intervention.intervention_id}"
                ),
                correlation_id=intervention.package_id,
                source_id=self.source_id,
            )
            self.database.append_events_on_connection(
                connection,
                [event],
            )
            self._assert_intervention_authority(
                intervention_authority,
                connection=connection,
            )
        return completed

    def _complete_failure_if_authorized(
        self,
        intervention: InterventionRecord,
        error: Exception,
        intervention_authority: _InterventionAuthority,
    ) -> None:
        self._assert_intervention_authority(intervention_authority)
        existing = rebuild_workforce_state(
            self.database
        ).interventions.get(intervention.intervention_id)
        if existing is not None and existing.outcome != "REQUESTED":
            return
        self._complete(
            intervention,
            outcome=f"FAILED_{type(error).__name__.upper()}",
            intervention_authority=intervention_authority,
        )

    def _execution(
        self,
        intervention: InterventionRecord,
        *,
        terminated_owned_process: bool = False,
        replacement_session_id: str | None = None,
        launch: GatewayResult | GatewayLaunch | None = None,
        blocked_reason: str | None = None,
        replayed: bool = False,
    ) -> InterventionExecution:
        state = rebuild_workforce_state(self.database)
        package_status = state.package(intervention.package_id).status
        resolved_replacement_session_id = (
            replacement_session_id
            or intervention.replacement_session_id
        )
        session = state.sessions.get(
            resolved_replacement_session_id or intervention.session_id
        )
        return InterventionExecution(
            intervention=state.interventions.get(
                intervention.intervention_id,
                intervention,
            ),
            package_status=package_status,
            session_status=session.status if session is not None else "UNKNOWN",
            terminated_owned_process=terminated_owned_process,
            replacement_session_id=resolved_replacement_session_id,
            launch=launch,
            blocked_reason=blocked_reason,
            replayed=replayed,
        )

    @staticmethod
    def _session(state, package_id: str, session_id: str | None) -> WorkerSession:
        sessions = state.sessions_for(package_id)
        if not sessions:
            raise WorkforceSteeringError(
                f"package has no worker session: {package_id}"
            )
        if session_id is not None:
            try:
                session = state.sessions[session_id]
            except KeyError as error:
                raise WorkforceSteeringError(
                    f"unknown worker session: {session_id}"
                ) from error
            if session.package_id != package_id:
                raise WorkforceSteeringError(
                    "worker session belongs to another package"
                )
            return session
        return max(sessions, key=lambda value: value.attempt)

    @staticmethod
    def _require_current_session(
        state,
        package_id: str,
        session: WorkerSession,
    ) -> None:
        sessions = state.sessions_for(package_id)
        latest = max(sessions, key=lambda value: value.attempt)
        if latest.session_id != session.session_id:
            raise WorkforceSteeringError(
                "intervention references a superseded workforce attempt: "
                f"{session.session_id}"
            )

    @staticmethod
    def _observation(
        state,
        package_id: str,
        session_id: str,
        observation_id: str | None,
    ) -> WorkerObservation | None:
        if observation_id is not None:
            try:
                observation = state.observations[observation_id]
            except KeyError as error:
                raise WorkforceSteeringError(
                    f"unknown workforce observation: {observation_id}"
                ) from error
            if (
                observation.package_id != package_id
                or observation.session_id != session_id
            ):
                raise WorkforceSteeringError(
                    "workforce observation does not match the target session"
                )
            return observation
        observations = [
            observation
            for observation in state.observations.values()
            if (
                observation.package_id == package_id
                and observation.session_id == session_id
            )
        ]
        return observations[-1] if observations else None

    @staticmethod
    def _intervention_id(
        *,
        package_id: str,
        session: WorkerSession,
        action: str,
        reason: str,
        observation: WorkerObservation | None,
        instruction: str,
        replacement_framework: str | None,
    ) -> str:
        digest = hashlib.sha256(
            canonical_json({
                "package_id": package_id,
                "session_id": session.session_id,
                "action": action,
                "reason": reason,
                "observation_id": (
                    observation.observation_id if observation is not None else None
                ),
                "observation_fingerprint": (
                    observation.progress_fingerprint
                    if observation is not None
                    else session.progress_fingerprint
                ),
                "instruction": instruction,
                "replacement_framework": replacement_framework,
            }).encode("utf-8")
        ).hexdigest()
        return f"intervention:{package_id}:{digest[:24]}"


__all__ = [
    "DIRECTIVE_ACTIONS",
    "RECOMMENDED_ACTION_BY_OBSERVATION",
    "RETRY_ACTIONS",
    "InterventionExecution",
    "WorkforceSteering",
    "WorkforceSteeringError",
]
