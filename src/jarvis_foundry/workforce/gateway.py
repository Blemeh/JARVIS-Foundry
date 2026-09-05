"""Thin workforce binding over the canonical MissionLoop execution path."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase
from ..domain import ActionEnvelope
from ..mission.contracts import (
    MissionStepContract,
    MissionStepContractError,
    latest_contract,
)
from ..mission.loop import (
    BLOCKED_OUTCOME,
    FAILED,
    PASSED,
    WAITING_OUTCOME,
    StepOutcome,
)
from ..supervisor.leases import LeaseManager, LeaseUnavailable
from ..supervisor.worker import (
    WorkerController,
    WorkerStatus,
    list_process_tree,
    pid_alive,
    process_creation_identity,
    worker_lease_key,
)
from .contracts import (
    TERMINAL_SESSION_STATUSES,
    ResourceClaim,
    WorkerSession,
)
from .state import (
    SUPERVISOR_CONTROLLER,
    WorkforceStateError,
    _acceptance_receipt_on_connection,
    _append,
    _event,
    _gate,
    _pending_effect_on_connection,
    _rebuild_workforce_state_on_connection,
    _stop_reason,
    _transition_work_package_on_connection,
    _versioned_session_payload_on_connection,
    _workforce_verification_criteria,
    rebuild_workforce_state,
)
from .worktrees import (
    WorkforceResourceError,
    WorktreeManager,
    WorktreePreparation,
)


_ADMISSION_LEASE_SECONDS = 30
_ADMISSION_LEASE_RENEW_SECONDS = 10.0


class WorkforceGatewayError(RuntimeError):
    """A package cannot be safely bound to canonical execution."""


class CanonicalMissionLoop(Protocol):
    def run_step(self, **kwargs: Any) -> StepOutcome:
        """Execute one step through the existing MissionLoop authority."""


@dataclass(frozen=True, slots=True)
class GatewayBinding:
    package_id: str
    package_hash: str
    session: WorkerSession
    resource_id: str
    claim_id: str
    resource_lease_key: str
    contract_id: str
    contract_hash: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class GatewayResult:
    binding: GatewayBinding
    package_status: str
    outcome: str
    verdict_id: str | None = None
    reason: str | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class GatewayLaunch:
    binding: GatewayBinding
    worker_status: WorkerStatus | None
    spawned: bool


@dataclass(frozen=True, slots=True)
class OwnedProcessTerminationResult:
    terminated_live_process: bool
    cleanup_succeeded: bool


@dataclass(frozen=True, slots=True)
class OwnedProcessEpoch:
    root_pid: int
    identities: tuple[tuple[int, int, str | None], ...]


@dataclass(frozen=True, slots=True)
class ExecutionAdmissionState:
    terminal: bool
    process_termination_required: bool
    externally_controllable: bool
    process_registration_complete: bool


@dataclass(frozen=True, slots=True)
class _AdmissionAuthority:
    package_id: str
    lease_key: str
    owner_id: str
    heartbeat_errors: list[BaseException]


@dataclass(frozen=True, slots=True)
class AdmissionPreemption:
    authority: _AdmissionAuthority
    observed_execution_owner: str | None
    termination: OwnedProcessTerminationResult


class WorkforceGateway:
    """Bind packages to canonical runs without becoming another MissionLoop."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        stop_path: str | Path | None = None,
        controller: str = SUPERVISOR_CONTROLLER,
        source_id: str = "workforce-gateway",
        worker_config_path: str | Path | None = None,
    ) -> None:
        self.database = database
        self.stop_path = Path(stop_path).expanduser().resolve() if stop_path else None
        self.controller = controller
        self.source_id = source_id
        self.worker_config_path = (
            Path(worker_config_path).expanduser().resolve()
            if worker_config_path is not None
            else None
        )
        self.leases = LeaseManager(database)
        self.worktrees = WorktreeManager(
            database,
            stop_path=self.stop_path,
            controller=controller,
            source_id=source_id,
        )

    def bind(
        self,
        package_id: str,
        *,
        admission_authority: _AdmissionAuthority | None = None,
    ) -> GatewayBinding:
        """Create or recover one deterministic package-to-run binding."""

        state = rebuild_workforce_state(self.database)
        package_state = state.package(package_id)
        package = package_state.package
        self._validate_package_policy(package)
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=package,
        )
        if package_state.status != "READY":
            existing = self._latest_session(state, package_id)
            if existing is not None:
                return self._binding_from_session(package, existing, replayed=True)
            raise WorkforceGatewayError(
                f"package {package_id} is not ready for launch: {package_state.status}"
            )

        if admission_authority is None:
            with self._admission_guard(package_id) as authority:
                with self.database.transaction() as connection:
                    return self._bind_on_connection(
                        connection,
                        package_id,
                        admission_authority=authority,
                    )
        with self.database.transaction() as connection:
            return self._bind_on_connection(
                connection,
                package_id,
                admission_authority=admission_authority,
            )

    def _bind_on_connection(
        self,
        connection,
        package_id: str,
        *,
        admission_authority: _AdmissionAuthority,
        predecessor_session: WorkerSession | None = None,
        retry_intervention_id: str | None = None,
    ) -> GatewayBinding:
        self._assert_admission_authority(
            admission_authority,
            connection=connection,
        )
        state = _rebuild_workforce_state_on_connection(
            self.database,
            connection,
        )
        package_state = state.package(package_id)
        package = package_state.package
        self._validate_package_policy(package)
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=package,
        )
        existing = self._active_session(state, package_id)
        if existing is not None:
            return self._binding_from_session(
                package,
                existing,
                replayed=True,
            )
        if package_state.status != "READY":
            raise WorkforceGatewayError(
                "package changed state during admission: "
                f"{package_state.status}"
            )
        prepared = self._prepared(package_id)
        self._verify_prepared(package, prepared)
        contract = self._canonical_contract(package, prepared)
        claim = self._active_claim(state, package_id)
        attempt = 1 + max(
            (session.attempt for session in state.sessions_for(package_id)),
            default=0,
        )
        run_id = f"workforce:{package_id}:a{attempt}"
        session = WorkerSession(
            session_id=run_id,
            package_id=package_id,
            run_id=run_id,
            attempt=attempt,
            framework="PENDING",
            provider="PENDING",
            model="PENDING",
            status="STARTING",
            result={
                "package_hash": package.content_hash(),
                "resource_id": prepared.resource_id,
                "claim_id": claim.claim_id,
                "mission_step_contract_id": contract.contract_id,
                "mission_step_contract_hash": contract.content_hash(),
                **(
                    {
                        "predecessor_session_id": (
                            predecessor_session.session_id
                        ),
                        "predecessor_run_id": predecessor_session.run_id,
                        "retry_intervention_id": str(
                            retry_intervention_id or ""
                        ),
                    }
                    if predecessor_session is not None
                    else {}
                ),
            },
        )
        resource_lease_key = f"workforce-resource:{prepared.resource_id}"
        self._acquire_resource_lease_on_connection(
            connection,
            resource_lease_key,
            run_id,
            package.lease_seconds,
        )
        bound_claim = replace(claim, session_id=run_id)
        claim_version = self.worktrees._claim_version_on_connection(
            connection,
            claim,
        )
        events = [
            _event(
                "workforce.session_bound",
                _versioned_session_payload_on_connection(
                    connection,
                    session,
                ),
                key=f"workforce.session_bound:{run_id}",
                correlation_id=package_id,
                source_id=self.source_id,
            ),
            _event(
                "workforce.resource_bound",
                {
                    **bound_claim.to_payload(),
                    "resource_lease_key": resource_lease_key,
                    "expected_status": claim.status,
                    "expected_version": claim_version,
                    "version": claim_version + 1,
                },
                key=f"workforce.resource_bound:{claim.claim_id}:{run_id}",
                correlation_id=package_id,
                source_id=self.source_id,
            ),
        ]
        self.database.append_events_on_connection(connection, events)
        self._assert_admission_authority(
            admission_authority,
            connection=connection,
        )
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=package,
        )
        return GatewayBinding(
            package_id=package_id,
            package_hash=package.content_hash(),
            session=session,
            resource_id=prepared.resource_id,
            claim_id=claim.claim_id,
            resource_lease_key=resource_lease_key,
            contract_id=contract.contract_id,
            contract_hash=contract.content_hash(),
            replayed=False,
        )
    def run_inline(
        self,
        package_id: str,
        mission_loop: CanonicalMissionLoop,
        *,
        expected_run_id: str | None = None,
        worker_preference: str | None = None,
        worker_preference_evidence: Sequence[str] = (),
        commit_envelope: ActionEnvelope | None = None,
        admission_authority: _AdmissionAuthority | None = None,
        control_authority_check: Callable[[Any | None], None] | None = None,
    ) -> GatewayResult:
        """Run one package through an already-constructed MissionLoop."""

        worker_preference = str(worker_preference or "").strip() or None
        if isinstance(worker_preference_evidence, str):
            raise WorkforceGatewayError(
                "worker preference evidence must be a sequence of references"
            )
        worker_preference_evidence = tuple(
            str(item).strip() for item in worker_preference_evidence
        )
        if worker_preference and (
            not worker_preference_evidence
            or any(not item for item in worker_preference_evidence)
        ):
            raise WorkforceGatewayError(
                "worker preference requires non-empty evidence references"
            )
        if not worker_preference and worker_preference_evidence:
            raise WorkforceGatewayError(
                "worker preference evidence requires a worker preference"
            )
        binding = (
            self.bind(package_id)
            if expected_run_id is None
            else self._binding_for_expected_run(
                package_id,
                str(expected_run_id),
                allow_unadmitted=admission_authority is not None,
            )
        )
        package = rebuild_workforce_state(self.database).package(package_id).package
        canonical_outcome = str(
            binding.session.result.get("canonical_outcome") or ""
        )
        if canonical_outcome:
            package_status = rebuild_workforce_state(self.database).package(
                package_id
            ).status
            if canonical_outcome == PASSED and package_status == "VERIFYING":
                return self.link_outcome(
                    package_id,
                    StepOutcome(
                        outcome=PASSED,
                        step_id=package.step_id,
                        run_id=binding.session.run_id,
                        verdict_id=(
                            str(binding.session.result["verdict_id"])
                            if binding.session.result.get("verdict_id")
                            else None
                        ),
                        commit_head=(
                            str(binding.session.result["commit_head"])
                            if binding.session.result.get("commit_head")
                            else None
                        ),
                        reason=(
                            str(binding.session.result["reason"])
                            if binding.session.result.get("reason")
                            else None
                        ),
                    ),
                    admission_authority=admission_authority,
                    control_authority_check=control_authority_check,
                )
            return GatewayResult(
                binding=binding,
                package_status=package_status,
                outcome=canonical_outcome,
                verdict_id=(
                    str(binding.session.result["verdict_id"])
                    if binding.session.result.get("verdict_id")
                    else None
                ),
                reason=(
                    str(binding.session.result["reason"])
                    if binding.session.result.get("reason")
                    else "existing canonical session recovered"
                ),
                replayed=True,
            )
        if binding.session.status in TERMINAL_SESSION_STATUSES:
            return GatewayResult(
                binding=binding,
                package_status=rebuild_workforce_state(
                    self.database
                ).package(package_id).status,
                outcome=binding.session.status,
                reason="existing terminal workforce session recovered",
                replayed=True,
            )
        prepared = self._prepared(package_id)
        contract = self._canonical_contract(package, prepared)
        launch_kind = str(
            binding.session.result.get("launch_kind")
            or "INLINE_MISSION_LOOP"
        )
        runtime = contract.runtime_kwargs(self.database)
        runtime["verification_criteria"] = list(
            self._verification_criteria(package)
        )
        if admission_authority is None:
            with self._admission_guard(
                package_id,
                renew=True,
                execution_run_id=binding.session.run_id,
            ) as authority:
                return self._run_inline_under_authority(
                    binding,
                    package,
                    mission_loop,
                    runtime=runtime,
                    launch_kind=launch_kind,
                    worker_preference=worker_preference,
                    worker_preference_evidence=worker_preference_evidence,
                    commit_envelope=commit_envelope,
                    admission_authority=authority,
                    control_authority_check=control_authority_check,
                )
        return self._run_inline_under_authority(
            binding,
            package,
            mission_loop,
            runtime=runtime,
            launch_kind=launch_kind,
            worker_preference=worker_preference,
            worker_preference_evidence=worker_preference_evidence,
            commit_envelope=commit_envelope,
            admission_authority=admission_authority,
            control_authority_check=control_authority_check,
        )

    def _run_inline_under_authority(
        self,
        binding: GatewayBinding,
        package,
        mission_loop: CanonicalMissionLoop,
        *,
        runtime: dict[str, Any],
        launch_kind: str,
        worker_preference: str | None,
        worker_preference_evidence: Sequence[str],
        commit_envelope: ActionEnvelope | None,
        admission_authority: _AdmissionAuthority,
        control_authority_check: Callable[[Any | None], None] | None,
    ) -> GatewayResult:
        if control_authority_check is not None:
            control_authority_check(None)
        binding = self._admit_launch(
            binding,
            launch_kind=launch_kind,
            reason="canonical MissionLoop launch admitted",
            admission_authority=admission_authority,
            control_authority_check=control_authority_check,
        )
        binding = self._binding_for_expected_run(
            binding.package_id,
            binding.session.run_id,
        )
        if control_authority_check is not None:
            control_authority_check(None)
        try:
            outcome = mission_loop.run_step(
                **runtime,
                run_id=binding.session.run_id,
                commit_envelope=commit_envelope,
                workforce_context={
                    "package_id": package.package_id,
                    "package_hash": package.content_hash(),
                    "attempt_id": binding.session.session_id,
                    "allowed_paths": list(package.allowed_paths),
                    "required_tests": list(package.required_tests),
                    "duration_seconds": package.duration_seconds,
                    **(
                        {"cost_budget_usd": package.cost_budget_usd}
                        if package.cost_budget_usd is not None
                        else {}
                    ),
                    **(
                        {"deadline": package.deadline}
                        if package.deadline is not None
                        else {}
                    ),
                    "owner_constraints": list(package.owner_constraints),
                    **(
                        {"effect_action_key": package.effect_action_key}
                        if package.effect_action_key
                        else {}
                    ),
                    **(
                        {
                            "worker_preference": worker_preference,
                            "worker_preference_evidence": list(
                                worker_preference_evidence
                            ),
                        }
                        if worker_preference
                        else {}
                    ),
                },
            )
        except Exception as error:
            if control_authority_check is not None:
                control_authority_check(None)
            return self._record_crash(
                binding,
                error,
                admission_authority=admission_authority,
                control_authority_check=control_authority_check,
            )
        if control_authority_check is not None:
            control_authority_check(None)
        return self.link_outcome(
            binding.package_id,
            outcome,
            admission_authority=admission_authority,
            control_authority_check=control_authority_check,
        )

    def spawn(
        self,
        package_id: str,
        workers: WorkerController,
    ) -> GatewayLaunch:
        """Start the sealed JARVIS package runner through WorkerController."""

        binding = self.bind(package_id)
        if binding.replayed:
            status = workers.status(binding.session.run_id)
            if (
                binding.session.status in TERMINAL_SESSION_STATUSES
                or binding.session.result.get("canonical_outcome")
            ):
                return GatewayLaunch(binding, status, spawned=False)
        package = rebuild_workforce_state(self.database).package(package_id).package
        with self._admission_guard(
            package_id,
            renew=True,
            execution_run_id=binding.session.run_id,
        ) as authority:
            binding = self._admit_launch(
                binding,
                launch_kind="SEALED_PACKAGE_RUNNER",
                reason="sealed JARVIS package runner admitted",
                admission_authority=authority,
            )
            binding = self._binding_for_expected_run(
                package_id,
                binding.session.run_id,
            )
            status = workers.status(binding.session.run_id)
            if status.alive:
                binding = self._record_process_registration(
                    binding,
                    status,
                    workers,
                    admission_authority=authority,
                )
                return GatewayLaunch(binding, status, spawned=False)
            worker_lease = self.database.row(
                "leases",
                "lease_key",
                worker_lease_key(binding.session.run_id),
            )
            if (
                worker_lease is not None
                and parse(str(worker_lease["expires_at"])) > utc_now()
            ):
                raise WorkforceGatewayError(
                    "worker launch identity is leased but no owned live process "
                    f"can be proven: {binding.session.run_id}"
                )
            _gate(
                self.database,
                stop_path=self.stop_path,
                controller=self.controller,
                package=package,
            )
            spawned_status: WorkerStatus | None = None
            try:
                spawned_status = workers.spawn(
                    binding.session.run_id,
                    self._runner_command(
                        package_id,
                        binding.session.run_id,
                    ),
                    cwd=Path(package.worktree_path),
                    worker_type="workforce-canonical-mission",
                    mission_id=package.mission_id,
                    step_id=package.step_id,
                    stop_path=self.stop_path,
                )
                binding = self._record_process_registration(
                    binding,
                    spawned_status,
                    workers,
                    admission_authority=authority,
                )
            except BaseException as error:
                try:
                    self._cleanup_failed_spawn(
                        binding.session.run_id,
                        workers,
                        spawned_status=spawned_status,
                    )
                except BaseException as cleanup_failure:
                    raise WorkforceGatewayError(
                        "sealed workforce process cleanup was not proven after "
                        "registration failure"
                    ) from cleanup_failure
                try:
                    self._record_crash(
                        binding,
                        error,
                        admission_authority=authority,
                    )
                except BaseException:
                    raise
                raise
            return GatewayLaunch(
                binding,
                spawned_status,
                spawned=True,
            )

    def spawn_many(
        self,
        package_ids: Iterable[str],
        workers: WorkerController,
    ) -> tuple[GatewayLaunch, ...]:
        """Admit several independent processes without an in-process thread fan-out."""

        return tuple(
            self.spawn(package_id, workers)
            for package_id in package_ids
        )

    def heartbeat(
        self,
        package_id: str,
        *,
        workers: WorkerController | None = None,
        expected_session_id: str | None = None,
        control_authority_check: (
            Callable[[Any | None], None] | None
        ) = None,
    ) -> GatewayBinding:
        """Renew process and mutable-resource ownership for one live session."""

        if control_authority_check is not None:
            control_authority_check(None)
        state = rebuild_workforce_state(self.database)
        package = state.package(package_id).package
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=package,
        )
        session = self._active_session(state, package_id)
        if session is None:
            raise WorkforceGatewayError(
                f"package has no active session to heartbeat: {package_id}"
            )
        if (
            expected_session_id is not None
            and session.session_id != str(expected_session_id)
        ):
            raise WorkforceGatewayError(
                "workforce heartbeat references a superseded attempt: "
                f"{expected_session_id}"
            )
        binding = self._binding_from_session(
            package,
            session,
            replayed=True,
        )
        sealed_process = (
            session.result.get("launch_kind") == "SEALED_PACKAGE_RUNNER"
        )
        if sealed_process and workers is None:
            raise WorkforceGatewayError(
                "sealed workforce heartbeat requires its WorkerController: "
                f"{session.run_id}"
            )
        worker_status: WorkerStatus | None = None
        if workers is not None:
            worker_status = (
                self._require_sealed_process_status(session, workers)
                if sealed_process
                else workers.status(session.run_id)
            )
            if not worker_status.alive:
                raise WorkforceGatewayError(
                    "cannot heartbeat a sealed workforce process that is not "
                    f"provably alive: {session.run_id}"
                )
        observed = utc_now()
        heartbeat_at = isoformat(observed)
        claim_expires_at = isoformat(
            observed + timedelta(seconds=package.lease_seconds)
        )
        with self.database.transaction() as connection:
            if control_authority_check is not None:
                control_authority_check(connection)
            current_state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            current_session = self._active_session(
                current_state,
                package_id,
            )
            if (
                current_session is None
                or current_session.session_id != session.session_id
            ):
                raise WorkforceGatewayError(
                    "workforce heartbeat references a superseded attempt: "
                    f"{session.run_id}"
                )
            current_package = current_state.package(package_id).package
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=current_package,
            )
            current_sealed_process = (
                current_session.result.get("launch_kind")
                == "SEALED_PACKAGE_RUNNER"
            )
            if current_sealed_process and workers is None:
                raise WorkforceGatewayError(
                    "sealed workforce heartbeat requires its "
                    f"WorkerController: {current_session.run_id}"
                )
            if current_sealed_process:
                self._require_sealed_process_status(
                    current_session,
                    workers,
                )
            if current_session.result.get("launch_intent") == "ADMITTED":
                admission = connection.execute(
                    "SELECT owner_id, expires_at FROM leases "
                    "WHERE lease_key = ?",
                    (f"workforce-admission:{package_id}",),
                ).fetchone()
                if (
                    admission is None
                    or not self._execution_guard_owner_matches(
                        str(admission["owner_id"]),
                        current_session.run_id,
                    )
                    or parse(str(admission["expires_at"])) <= observed
                ):
                    raise WorkforceGatewayError(
                        "workforce execution authority changed before "
                        f"heartbeat: {current_session.run_id}"
                    )
            if workers is not None:
                worker_key = worker_lease_key(current_session.run_id)
                worker_lease = connection.execute(
                    "SELECT owner_id, expires_at FROM leases "
                    "WHERE lease_key = ?",
                    (worker_key,),
                ).fetchone()
                if (
                    worker_lease is None
                    or str(worker_lease["owner_id"]) != worker_key
                    or parse(str(worker_lease["expires_at"])) <= utc_now()
                ):
                    raise WorkforceGatewayError(
                        "sealed workforce worker lease is absent, expired, "
                        f"or changed before heartbeat: {current_session.run_id}"
                    )
                worker_expires_at = isoformat(
                    observed + timedelta(seconds=workers.lease_seconds)
                )
                cursor = connection.execute(
                    "UPDATE leases SET expires_at = ?, heartbeat_at = ? "
                    "WHERE lease_key = ? AND owner_id = ?",
                    (
                        worker_expires_at,
                        heartbeat_at,
                        worker_key,
                        worker_key,
                    ),
                )
                if cursor.rowcount != 1:
                    raise WorkforceGatewayError(
                        "sealed workforce worker lease changed before "
                        f"heartbeat: {current_session.run_id}"
                    )
            cursor = connection.execute(
                "UPDATE leases SET expires_at = ?, heartbeat_at = ? "
                "WHERE lease_key = ? AND owner_id = ?",
                (
                    claim_expires_at,
                    heartbeat_at,
                    binding.resource_lease_key,
                    current_session.run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise WorkforceGatewayError(
                    "workforce mutable-resource lease changed before "
                    f"heartbeat: {current_session.run_id}"
                )
            try:
                claim = current_state.claims[binding.claim_id]
            except KeyError as error:
                raise WorkforceGatewayError(
                    f"workforce resource claim disappeared: {binding.claim_id}"
                ) from error
            if (
                claim.status != "ACTIVE"
                or claim.session_id
                not in {None, current_session.run_id}
            ):
                raise WorkforceGatewayError(
                    "workforce resource claim no longer belongs to the "
                    f"current attempt: {binding.claim_id}"
                )
            events = []
            renewed_claim = claim
            if parse(claim.lease_expires_at) < parse(claim_expires_at):
                expected_version = (
                    self.worktrees._claim_version_on_connection(
                        connection,
                        claim,
                    )
                )
                renewed_claim = replace(
                    claim,
                    session_id=current_session.run_id,
                    lease_expires_at=claim_expires_at,
                )
                events.append(_event(
                    "workforce.resource_renewed",
                    {
                        **renewed_claim.to_payload(),
                        "expected_status": claim.status,
                        "expected_version": expected_version,
                        "version": expected_version + 1,
                    },
                    key=(
                        "workforce.resource_renewed:"
                        f"{claim.claim_id}:v{expected_version + 1}:"
                        f"{claim_expires_at}"
                    ),
                    correlation_id=claim.package_id,
                    source_id=self.source_id,
                ))
            healthy = replace(
                current_session,
                status="HEALTHY",
                heartbeat_at=heartbeat_at,
                result={
                    **current_session.result,
                    "claim_lease_expires_at": (
                        renewed_claim.lease_expires_at
                    ),
                },
            )
            events.append(_event(
                "workforce.session_heartbeat",
                _versioned_session_payload_on_connection(
                    connection,
                    healthy,
                ),
                key=(
                    "workforce.session_heartbeat:"
                    f"{healthy.session_id}:{heartbeat_at}"
                ),
                correlation_id=healthy.package_id,
                source_id=self.source_id,
            ))
            self.database.append_events_on_connection(connection, events)
            if current_session.result.get("launch_intent") == "ADMITTED":
                admission = connection.execute(
                    "SELECT owner_id, expires_at FROM leases "
                    "WHERE lease_key = ?",
                    (f"workforce-admission:{package_id}",),
                ).fetchone()
                if (
                    admission is None
                    or not self._execution_guard_owner_matches(
                        str(admission["owner_id"]),
                        current_session.run_id,
                    )
                    or parse(str(admission["expires_at"])) <= utc_now()
                ):
                    raise WorkforceGatewayError(
                        "workforce execution authority changed during "
                        f"heartbeat: {current_session.run_id}"
                    )
            if control_authority_check is not None:
                control_authority_check(connection)
            if current_sealed_process:
                self._require_sealed_process_status(
                    current_session,
                    workers,
                )
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=current_package,
            )
        return replace(binding, session=healthy, replayed=False)

    def link_outcome(
        self,
        package_id: str,
        outcome: StepOutcome,
        *,
        admission_authority: _AdmissionAuthority | None = None,
        control_authority_check: (
            Callable[[Any | None], None] | None
        ) = None,
    ) -> GatewayResult:
        """Project one canonical MissionLoop outcome into workforce state."""

        if admission_authority is None:
            with self._admission_guard(
                package_id,
                renew=True,
            ) as authority:
                return self._link_outcome_under_authority(
                    package_id,
                    outcome,
                    authority,
                    control_authority_check=control_authority_check,
                )
        return self._link_outcome_under_authority(
            package_id,
            outcome,
            admission_authority,
            control_authority_check=control_authority_check,
        )

    def _link_outcome_under_authority(
        self,
        package_id: str,
        outcome: StepOutcome,
        admission_authority: _AdmissionAuthority,
        *,
        control_authority_check: (
            Callable[[Any | None], None] | None
        ) = None,
    ) -> GatewayResult:
        self._assert_admission_authority(admission_authority)
        if control_authority_check is not None:
            control_authority_check(None)
        initial_state = rebuild_workforce_state(self.database)
        initial_session = self._session_for_outcome(
            initial_state,
            package_id,
            outcome,
        )
        stop_reason = _stop_reason(
            self.database,
            self.stop_path,
            package=initial_state.package(package_id).package,
        )
        if stop_reason:
            session_status = "STOPPED"
            package_target = "STOPPED"
        elif outcome.outcome == PASSED:
            session_status = "COMPLETED"
            package_target = "VERIFYING"
        elif outcome.outcome == FAILED:
            session_status = "FAILED"
            package_target = "FAILED"
        elif outcome.outcome in {BLOCKED_OUTCOME, WAITING_OUTCOME}:
            session_status = "BLOCKED"
            package_target = "WAITING"
        else:
            raise WorkforceGatewayError(
                f"unsupported canonical outcome: {outcome.outcome}"
            )
        route = self._route_for_run(initial_session.run_id)
        linked_inserted = False
        with self.database.transaction() as connection:
            self._assert_admission_authority(
                admission_authority,
                connection=connection,
            )
            if control_authority_check is not None:
                control_authority_check(connection)
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            session = self._session_for_outcome(state, package_id, outcome)
            current = state.package(package_id)
            if session.status in TERMINAL_SESSION_STATUSES:
                prior = session.result
                same_outcome = bool(
                    prior.get("canonical_outcome") == outcome.outcome
                    and str(
                        prior.get("canonical_run_id") or session.run_id
                    )
                    == str(outcome.run_id or session.run_id)
                    and prior.get("verdict_id") == outcome.verdict_id
                    and prior.get("commit_head") == outcome.commit_head
                    and prior.get("reason") == outcome.reason
                )
                if not same_outcome:
                    raise WorkforceGatewayError(
                        "canonical outcome references a terminal workforce "
                        f"attempt: {session.run_id}:{session.status}"
                    )
                if outcome.outcome == PASSED and current.status == "VERIFYING":
                    try:
                        current = _transition_work_package_on_connection(
                            self.database,
                            connection,
                            package_id,
                            "ACCEPTED",
                            reason=(
                                "late canonical MissionLoop verifier receipt "
                                "accepted the package result"
                            ),
                            controller=self.controller,
                            stop_path=self.stop_path,
                            source_id=self.source_id,
                        )
                    except WorkforceStateError as error:
                        if (
                            "lacks a canonical verification.accepted receipt"
                            not in str(error)
                        ):
                            raise
                        current = _rebuild_workforce_state_on_connection(
                            self.database,
                            connection,
                        ).package(package_id)
                if current.status == "ACCEPTED":
                    self._release_on_connection(
                        connection,
                        package_id,
                        run_id=session.run_id,
                        reason="late accepted package completed",
                    )
                self._assert_admission_authority(
                    admission_authority,
                    connection=connection,
                )
                if control_authority_check is not None:
                    control_authority_check(connection)
                return GatewayResult(
                    binding=self._binding_from_session(
                        current.package,
                        session,
                        replayed=True,
                    ),
                    package_status=current.status,
                    outcome=outcome.outcome,
                    verdict_id=outcome.verdict_id,
                    reason=outcome.reason or stop_reason,
                    replayed=True,
                )
            if current.status in {"ACCEPTED", "REJECTED", "CANCELLED"}:
                raise WorkforceGatewayError(
                    "active workforce session belongs to a terminal package: "
                    f"{package_id}:{current.status}"
                )
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (session.run_id,),
            ).fetchone()
            run_row = dict(row) if row is not None else {}
            framework = str(
                route.get("selected_worker")
                or route.get("framework")
                or session.framework
            )
            provider = str(run_row.get("provider") or session.provider)
            model = str(run_row.get("model") or session.model)
            linked = replace(
                session,
                framework=framework,
                provider=provider,
                model=model,
                status=session_status,
                finished_at=(
                    session.finished_at or isoformat()
                    if session_status in TERMINAL_SESSION_STATUSES
                    else None
                ),
                result={
                    **session.result,
                    "canonical_outcome": outcome.outcome,
                    "canonical_run_id": outcome.run_id or session.run_id,
                    "verdict_id": outcome.verdict_id,
                    "commit_head": outcome.commit_head,
                    "reason": outcome.reason,
                    "agent_run_status": run_row.get("status"),
                    "agent_run_event_id": run_row.get("last_event_id"),
                    "context_manifest_hash": run_row.get(
                        "context_manifest_hash"
                    ),
                },
            )
            result_event = _event(
                "workforce.result_linked",
                _versioned_session_payload_on_connection(
                    connection,
                    linked,
                ),
                key=(
                    f"workforce.result_linked:{linked.session_id}:"
                    f"{outcome.outcome}:"
                    f"{outcome.verdict_id or 'no-verdict'}"
                ),
                correlation_id=linked.package_id,
                source_id=self.source_id,
            )
            linked_inserted = self.database.append_events_on_connection(
                connection,
                [result_event],
            )[0][1]
            current = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            ).package(package_id)
            terminal_before_projection = current.status in {
                "ACCEPTED",
                "REJECTED",
                "CANCELLED",
            }
            if not terminal_before_projection and current.status != package_target:
                current = _transition_work_package_on_connection(
                    self.database,
                    connection,
                    package_id,
                    package_target,
                    reason=(
                        stop_reason
                        or outcome.reason
                        or f"canonical MissionLoop outcome {outcome.outcome}"
                    ),
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                    allow_stop=bool(stop_reason),
                )
            if outcome.outcome == PASSED and current.status == "VERIFYING":
                try:
                    current = _transition_work_package_on_connection(
                        self.database,
                        connection,
                        package_id,
                        "ACCEPTED",
                        reason=(
                            "canonical MissionLoop verifier receipt accepted "
                            "the package result"
                        ),
                        controller=self.controller,
                        stop_path=self.stop_path,
                        source_id=self.source_id,
                    )
                except WorkforceStateError as error:
                    if (
                        "lacks a canonical verification.accepted receipt"
                        not in str(error)
                    ):
                        raise
                    current = _rebuild_workforce_state_on_connection(
                        self.database,
                        connection,
                    ).package(package_id)
            if current.status in {"ACCEPTED", "STOPPED"}:
                self._release_on_connection(
                    connection,
                    package_id,
                    run_id=linked.run_id,
                    reason=(
                        "accepted package completed"
                        if current.status == "ACCEPTED"
                        else "stopped package released mutable scope"
                    ),
                )
            self._assert_admission_authority(
                admission_authority,
                connection=connection,
            )
            if control_authority_check is not None:
                control_authority_check(connection)
        return GatewayResult(
            binding=self._binding_from_session(
                current.package,
                linked,
                replayed=terminal_before_projection or not linked_inserted,
            ),
            package_status=current.status,
            outcome=outcome.outcome,
            verdict_id=outcome.verdict_id,
            reason=outcome.reason or stop_reason,
            replayed=terminal_before_projection or not linked_inserted,
        )

    def reconcile_process(
        self,
        package_id: str,
        workers: WorkerController,
    ) -> GatewayResult | WorkerStatus:
        """Reconcile one spawned process from durable run and PID truth."""

        state = rebuild_workforce_state(self.database)
        session = self._active_session(state, package_id)
        if session is None:
            sessions = state.sessions_for(package_id)
            if not sessions:
                raise WorkforceGatewayError(
                    f"package has no workforce session: {package_id}"
                )
            session = max(sessions, key=lambda value: value.attempt)
        status = workers.status(session.run_id)
        row = self.database.row("agent_runs", "run_id", session.run_id)
        if row is not None and str(row.get("status")) == "COMPLETED":
            verdict = self._accepted_receipt(
                state.package(package_id).package,
                run_id=session.run_id,
            )
            return self.link_outcome(
                package_id,
                StepOutcome(
                    outcome=PASSED if verdict else WAITING_OUTCOME,
                    step_id=state.package(package_id).package.step_id,
                    run_id=session.run_id,
                    verdict_id=(
                        str(verdict.get("verdict_id")) if verdict else None
                    ),
                    reason=(
                        None
                        if verdict
                        else "canonical run completed; verifier acceptance pending"
                    ),
                ),
            )
        if row is not None and str(row.get("status")) == "FAILED":
            return self.link_outcome(
                package_id,
                StepOutcome(
                    outcome=FAILED,
                    step_id=state.package(package_id).package.step_id,
                    run_id=session.run_id,
                    reason=str(row.get("failure_class") or "canonical run failed"),
                ),
            )
        if status.alive:
            return status
        package = state.package(package_id).package
        expected_run_id = session.run_id

        def validate_current(connection) -> ExecutionAdmissionState:
            current_state = (
                _rebuild_workforce_state_on_connection(
                    self.database,
                    connection,
                )
                if connection is not None
                else rebuild_workforce_state(self.database)
            )
            current_sessions = current_state.sessions_for(package_id)
            if not current_sessions:
                raise WorkforceGatewayError(
                    f"package has no workforce session: {package_id}"
                )
            current = max(
                current_sessions,
                key=lambda candidate: candidate.attempt,
            )
            if current.run_id != expected_run_id:
                raise WorkforceGatewayError(
                    "dead-process reconciliation references a superseded "
                    f"attempt: {expected_run_id}"
                )
            current_package = current_state.package(package_id).package
            record = self._load_pid_records(
                workers.pid_table_path()
            ).get(expected_run_id)
            try:
                pid = int(record.get("pid") or 0) if record else 0
                session_pid = int(current.result.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
                session_pid = 0
            recorded_identity = self._process_identity_payload(
                record.get("creation_identity") if record else None,
                expected_pid=pid if pid > 0 else None,
            )
            session_identity = self._process_identity_payload(
                current.result.get("process_creation_identity"),
                expected_pid=pid if pid > 0 else None,
            )
            try:
                working_directory_matches = bool(
                    record
                    and record.get("working_directory")
                    and Path(str(record["working_directory"]))
                    .expanduser()
                    .resolve()
                    == Path(current_package.worktree_path)
                    .expanduser()
                    .resolve()
                )
            except OSError:
                working_directory_matches = False
            registration_complete = bool(
                current.result.get("launch_kind")
                == "SEALED_PACKAGE_RUNNER"
                and current.result.get("launch_phase")
                == "PROCESS_REGISTERED"
                and pid > 0
                and pid == session_pid
                and record
                and str(record.get("run_id") or "") == expected_run_id
                and str(record.get("worker_type") or "")
                == "workforce-canonical-mission"
                and working_directory_matches
                and recorded_identity is not None
                and recorded_identity == session_identity
                and str(current.result.get("worker_lease_key") or "")
                == worker_lease_key(expected_run_id)
            )
            observed_identity = (
                self._process_identity_payload(
                    process_creation_identity(pid),
                    expected_pid=pid,
                )
                if pid > 0 and pid_alive(pid)
                else None
            )
            exact_process_alive = bool(
                recorded_identity is not None
                and observed_identity == recorded_identity
            )
            return ExecutionAdmissionState(
                terminal=current.status in TERMINAL_SESSION_STATUSES,
                process_termination_required=exact_process_alive,
                externally_controllable=(
                    current.result.get("launch_kind")
                    == "SEALED_PACKAGE_RUNNER"
                ),
                process_registration_complete=registration_complete,
            )

        execution = validate_current(None)
        if (
            not execution.externally_controllable
            or not execution.process_registration_complete
        ):
            raise WorkforceGatewayError(
                "dead workforce process lacks its exact durable sealed-runner "
                f"registration: {expected_run_id}"
            )

        def preserve_dead_process(
            _authority: _AdmissionAuthority,
        ) -> OwnedProcessTerminationResult:
            current = validate_current(None)
            return OwnedProcessTerminationResult(
                terminated_live_process=False,
                cleanup_succeeded=not current.process_termination_required,
            )

        with self._preemptible_admission_guard(
            package_id,
            expected_run_id,
            validate_current=validate_current,
            can_terminate_owned=lambda: False,
            terminate_owned=preserve_dead_process,
        ) as preemption:
            current = validate_current(None)
            if (
                not current.externally_controllable
                or not current.process_registration_complete
                or current.process_termination_required
            ):
                raise WorkforceGatewayError(
                    "dead workforce process changed before canonical "
                    f"reconciliation: {expected_run_id}"
                )
            return self._record_crash(
                self._binding_from_session(
                    package,
                    session,
                    replayed=True,
                ),
                RuntimeError(
                    "owned worker process ended without a canonical terminal run"
                ),
                admission_authority=preemption.authority,
            )

    def release(
        self,
        package_id: str,
        *,
        reason: str,
        run_id: str | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            self._release_on_connection(
                connection,
                package_id,
                reason=reason,
                run_id=run_id,
            )

    def _release_on_connection(
        self,
        connection,
        package_id: str,
        *,
        reason: str,
        run_id: str | None = None,
    ) -> None:
        state = _rebuild_workforce_state_on_connection(
            self.database,
            connection,
        )
        package = state.package(package_id).package
        sessions = state.sessions_for(package_id)
        if not sessions:
            return
        if run_id is None:
            session = max(sessions, key=lambda value: value.attempt)
        else:
            session = next(
                (
                    candidate
                    for candidate in sessions
                    if candidate.run_id == run_id
                ),
                None,
            )
            if session is None:
                raise WorkforceGatewayError(
                    f"cannot release unknown package run: {run_id}"
                )
        if session.status not in TERMINAL_SESSION_STATUSES:
            raise WorkforceGatewayError(
                "cannot release mutable ownership for a nonterminal "
                f"workforce session: {session.run_id}:{session.status}"
            )
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=package,
            allow_stop=True,
        )
        claims = tuple(
            claim
            for claim in state.active_claims()
            if (
                claim.package_id == package.package_id
                and claim.session_id in {
                    session.session_id,
                    session.run_id,
                }
            )
        )
        try:
            for claim in claims:
                self.worktrees._assert_claim_releasable_on_connection(
                    connection,
                    state,
                    claim,
                    action="released",
                )
        except WorkforceResourceError as error:
            raise WorkforceGatewayError(str(error)) from error
        resource_id = str(session.result.get("resource_id") or "")
        if resource_id:
            connection.execute(
                "DELETE FROM leases WHERE lease_key = ? AND owner_id = ?",
                (f"workforce-resource:{resource_id}", session.run_id),
            )
        release_events = []
        for claim in claims:
            expected_version = (
                self.worktrees._claim_version_on_connection(
                    connection,
                    claim,
                )
            )
            released = replace(claim, status="RELEASED")
            release_events.append(_event(
                "workforce.resource_released",
                {
                    **released.to_payload(),
                    "reason": str(reason),
                    "expected_status": claim.status,
                    "expected_version": expected_version,
                    "version": expected_version + 1,
                },
                key=(
                    f"workforce.resource_released:{claim.claim_id}:"
                    f"v{expected_version + 1}"
                ),
                correlation_id=claim.package_id,
                source_id=self.source_id,
            ))
        if release_events:
            self.database.append_events_on_connection(
                connection,
                release_events,
            )

    def _canonical_contract(
        self,
        package,
        prepared: WorktreePreparation,
    ) -> MissionStepContract:
        if package.mission_id is None or package.step_id is None:
            raise WorkforceGatewayError(
                "launchable work package requires mission_id and step_id"
            )
        if (
            package.mission_step_contract_id is None
            or package.mission_step_contract_hash is None
        ):
            raise WorkforceGatewayError(
                "launchable package must pin its mission step contract"
            )
        try:
            contract = latest_contract(
                self.database,
                package.mission_id,
                step_id=package.step_id,
                allow_invalid=False,
            )
        except MissionStepContractError as error:
            raise WorkforceGatewayError(str(error)) from error
        if contract is None:
            raise WorkforceGatewayError(
                f"mission step contract is missing: {package.mission_id}/{package.step_id}"
            )
        checks = {
            "contract id": contract.contract_id == package.mission_step_contract_id,
            "contract hash": contract.content_hash()
            == package.mission_step_contract_hash,
            "resource": contract.resource_id == prepared.resource_id,
            "capability": contract.capability == package.capability,
            "risk class": contract.risk_class == package.risk_class,
            "acceptance criteria": tuple(contract.verification_criteria)
            == tuple(package.acceptance_criteria),
            "expected artifacts": tuple(contract.expected_artifacts)
            == tuple(package.expected_artifacts),
            "forbidden paths": tuple(contract.forbidden_paths)
            == tuple(package.forbidden_paths),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise WorkforceGatewayError(
                "work package diverges from canonical mission contract: "
                + ", ".join(failed)
            )
        return contract

    def _validate_package_policy(self, package) -> None:
        if package.role != "builder":
            raise WorkforceGatewayError(
                "canonical workforce packages currently support only the "
                f"builder role, not {package.role}"
            )
        if package.result_schema != "worker-result.v1":
            raise WorkforceGatewayError(
                f"unsupported workforce result schema: {package.result_schema}"
            )
        if package.token_budget is not None:
            raise WorkforceGatewayError(
                "token_budget is not observable in the canonical worker "
                "runtime and cannot be enforced"
            )
        if (
            package.cost_budget_usd is not None
            and package.cost_budget_usd <= 0
        ):
            raise WorkforceGatewayError(
                "cost_budget_usd must be positive when supplied"
            )
        if package.deadline is not None:
            try:
                deadline = parse(package.deadline)
            except (TypeError, ValueError) as error:
                raise WorkforceGatewayError(
                    "workforce deadline must be an ISO-8601 timestamp"
                ) from error
            if deadline <= utc_now():
                raise WorkforceGatewayError(
                    f"workforce package deadline expired: {package.deadline}"
                )
        snapshot = package.owner_veto_snapshot
        active = snapshot.get("active") if snapshot else None
        status = str(snapshot.get("status") or "").upper() if snapshot else ""
        if active or status in {"ACTIVE", "BLOCKED", "STOPPED", "VETOED"}:
            raise WorkforceGatewayError(
                "workforce package owner-veto snapshot is not clear"
            )

    @staticmethod
    def _verification_criteria(package) -> tuple[str, ...]:
        return _workforce_verification_criteria(package)

    def _prepared(self, package_id: str) -> WorktreePreparation:
        for event in reversed(
            self.database.events(
                event_types=("workforce.worktree_prepared",)
            )
        ):
            if str(event.payload.get("package_id") or "") == package_id:
                return WorktreePreparation.from_payload(event.payload)
        raise WorkforceGatewayError(
            f"package has no prepared worktree: {package_id}"
        )

    def _verify_prepared(
        self,
        package,
        prepared: WorktreePreparation,
    ) -> None:
        if Path(package.worktree_path).resolve() != Path(
            prepared.worktree_path
        ).resolve():
            raise WorkforceGatewayError("prepared worktree identity changed")
        if package.source_sha != prepared.base_sha:
            raise WorkforceGatewayError("prepared worktree base SHA changed")
        if package.branch_name != prepared.branch_name:
            raise WorkforceGatewayError("prepared worktree branch changed")
        self.worktrees._verify_existing(prepared, package)

    @staticmethod
    def _active_claim(state, package_id: str) -> ResourceClaim:
        claims = tuple(
            claim
            for claim in state.active_claims()
            if claim.package_id == package_id
        )
        if len(claims) != 1:
            raise WorkforceGatewayError(
                f"package requires exactly one active resource claim: {package_id}"
            )
        return claims[0]

    @staticmethod
    def _active_session(state, package_id: str) -> WorkerSession | None:
        active = [
            session
            for session in state.sessions_for(package_id)
            if session.status not in TERMINAL_SESSION_STATUSES
        ]
        if len(active) > 1:
            raise WorkforceGatewayError(
                f"package has multiple active sessions: {package_id}"
            )
        return active[0] if active else None

    @staticmethod
    def _latest_session(state, package_id: str) -> WorkerSession | None:
        sessions = state.sessions_for(package_id)
        return max(sessions, key=lambda value: value.attempt) if sessions else None

    def _binding_from_session(
        self,
        package,
        session: WorkerSession,
        *,
        replayed: bool,
    ) -> GatewayBinding:
        result = session.result
        required = {
            "resource_id": result.get("resource_id"),
            "claim_id": result.get("claim_id"),
            "mission_step_contract_id": result.get(
                "mission_step_contract_id"
            ),
            "mission_step_contract_hash": result.get(
                "mission_step_contract_hash"
            ),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise WorkforceGatewayError(
                "workforce session binding is incomplete: "
                + ", ".join(missing)
            )
        resource_id = str(required["resource_id"])
        return GatewayBinding(
            package_id=package.package_id,
            package_hash=package.content_hash(),
            session=session,
            resource_id=resource_id,
            claim_id=str(required["claim_id"]),
            resource_lease_key=f"workforce-resource:{resource_id}",
            contract_id=str(required["mission_step_contract_id"]),
            contract_hash=str(required["mission_step_contract_hash"]),
            replayed=replayed,
        )

    def _binding_for_expected_run(
        self,
        package_id: str,
        expected_run_id: str,
        *,
        allow_unadmitted: bool = False,
    ) -> GatewayBinding:
        state = rebuild_workforce_state(self.database)
        package_state = state.package(package_id)
        package = package_state.package
        self._validate_package_policy(package)
        _gate(
            self.database,
            self.controller,
            stop_path=self.stop_path,
            package=package,
        )
        session = next(
            (
                candidate
                for candidate in state.sessions_for(package_id)
                if candidate.run_id == expected_run_id
            ),
            None,
        )
        latest = self._latest_session(state, package_id)
        if (
            session is None
            or latest is None
            or latest.session_id != session.session_id
        ):
            observed = latest.run_id if latest is not None else "none"
            raise WorkforceGatewayError(
                "canonical execution rebound to another workforce attempt: "
                f"expected {expected_run_id}, observed {observed}"
            )
        allowed_session_statuses = {"RUNNING", "HEALTHY"}
        allowed_package_statuses = {"RUNNING"}
        if allow_unadmitted:
            allowed_session_statuses.add("STARTING")
            allowed_package_statuses.add("READY")
        if session.status not in allowed_session_statuses:
            raise WorkforceGatewayError(
                "exact workforce attempt is no longer executable: "
                f"{session.run_id}:{session.status}"
            )
        if package_state.status not in allowed_package_statuses:
            raise WorkforceGatewayError(
                "exact workforce package is not executable: "
                f"{package_id}:{package_state.status}"
            )
        return self._binding_from_session(
            package,
            session,
            replayed=True,
        )

    def _mark_session(
        self,
        session: WorkerSession,
        status: str,
    ) -> WorkerSession:
        updated = replace(
            session,
            status=status,
            heartbeat_at=isoformat(),
        )
        self._append_session_event(
            "workforce.session_state_changed",
            updated,
            suffix=status.lower(),
        )
        return updated

    def _admit_launch(
        self,
        binding: GatewayBinding,
        *,
        launch_kind: str,
        reason: str,
        admission_authority: _AdmissionAuthority | None = None,
        control_authority_check: (
            Callable[[Any | None], None] | None
        ) = None,
    ) -> GatewayBinding:
        """Persist one replayable launch intent and package transition."""

        if admission_authority is None:
            with self._admission_guard(
                binding.package_id,
            ) as authority:
                return self._admit_launch(
                    binding,
                    launch_kind=launch_kind,
                    reason=reason,
                    admission_authority=authority,
                    control_authority_check=control_authority_check,
                )
        with self.database.transaction() as connection:
            self._assert_admission_authority(
                admission_authority,
                connection=connection,
            )
            if control_authority_check is not None:
                control_authority_check(connection)
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            package_state = state.package(binding.package_id)
            package = package_state.package
            try:
                session = state.sessions[binding.session.session_id]
            except KeyError as error:
                raise WorkforceGatewayError(
                    "workforce launch session disappeared during admission"
                ) from error
            latest = self._latest_session(state, binding.package_id)
            if latest is None or latest.session_id != session.session_id:
                raise WorkforceGatewayError(
                    "only the current workforce attempt may launch"
                )
            prior_kind = str(session.result.get("launch_kind") or "")
            if prior_kind and prior_kind != launch_kind:
                raise WorkforceGatewayError(
                    "workforce attempt already names another launch kind: "
                    f"{prior_kind}"
                )
            if session.status not in {"STARTING", "RUNNING", "HEALTHY"}:
                raise WorkforceGatewayError(
                    "terminal workforce attempt cannot launch: "
                    f"{session.status}"
                )
            if package_state.status not in {"READY", "RUNNING"}:
                raise WorkforceGatewayError(
                    "package cannot admit execution from state "
                    f"{package_state.status}"
                )
            self._assert_retry_launch_hazards_clear_on_connection(
                connection,
                state,
                package_state,
                session,
            )
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package,
            )
            events = []
            admitted = session
            if session.status == "STARTING" or not prior_kind:
                admitted = replace(
                    session,
                    status="RUNNING",
                    heartbeat_at=isoformat(),
                    result={
                        **session.result,
                        "launch_kind": launch_kind,
                        "launch_intent": "ADMITTED",
                        "launch_phase": (
                            "PROCESS_LAUNCHING"
                            if launch_kind == "SEALED_PACKAGE_RUNNER"
                            else "INLINE_ACTIVE"
                        ),
                    },
                )
                events.append(_event(
                    "workforce.session_started",
                    _versioned_session_payload_on_connection(
                        connection,
                        admitted,
                    ),
                    key=(
                        "workforce.session_started:"
                        f"{session.session_id}:{launch_kind}"
                    ),
                    correlation_id=binding.package_id,
                    source_id=self.source_id,
                ))
            if package_state.status == "READY":
                updated_at = isoformat()
                events.append(_event(
                    "workforce.package_state_changed",
                    {
                        "package_id": binding.package_id,
                        "previous_status": package_state.status,
                        "expected_version": package_state.version,
                        "status": "RUNNING",
                        "reason": reason,
                        "version": package_state.version + 1,
                        "updated_at": updated_at,
                        "terminal_state": None,
                    },
                    key=(
                        "workforce.package_state_changed:"
                        f"{binding.package_id}:v{package_state.version + 1}:RUNNING"
                    ),
                    correlation_id=binding.package_id,
                    source_id=self.source_id,
                ))
            if events:
                self.database.append_events_on_connection(
                    connection,
                    events,
                )
            self._assert_admission_authority(
                admission_authority,
                connection=connection,
            )
            if control_authority_check is not None:
                control_authority_check(connection)
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package,
            )
        return replace(binding, session=admitted, replayed=binding.replayed)

    def _assert_retry_launch_hazards_clear_on_connection(
        self,
        connection,
        state,
        package_state,
        session: WorkerSession,
    ) -> None:
        predecessor_run_id = str(
            session.result.get("predecessor_run_id") or ""
        )
        if not predecessor_run_id:
            return
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
                "replacement launch is blocked by canonical acceptance of "
                f"the predecessor run: {predecessor_run_id}"
            )
        pending_effect = _pending_effect_on_connection(
            self.database,
            connection,
            predecessor_run_id,
            package_state.package.effect_action_key,
        )
        if pending_effect is not None:
            raise WorkforceGatewayError(
                "replacement launch is blocked until predecessor effect "
                f"{pending_effect} is reconciled"
            )

    def _append_session_event(
        self,
        event_type: str,
        session: WorkerSession,
        *,
        suffix: str,
        admission_authority: _AdmissionAuthority | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            if admission_authority is not None:
                self._assert_admission_authority(
                    admission_authority,
                    connection=connection,
                )
            event = _event(
                event_type,
                _versioned_session_payload_on_connection(
                    connection,
                    session,
                ),
                key=f"{event_type}:{session.session_id}:{suffix}",
                correlation_id=session.package_id,
                source_id=self.source_id,
            )
            self.database.append_events_on_connection(connection, [event])

    def _session_for_outcome(
        self,
        state,
        package_id: str,
        outcome: StepOutcome,
    ) -> WorkerSession:
        sessions = state.sessions_for(package_id)
        if not sessions:
            raise WorkforceGatewayError(
                f"canonical outcome has no package session: {package_id}"
            )
        latest = max(sessions, key=lambda value: value.attempt)
        run_id = str(outcome.run_id or "")
        if not run_id:
            if len(sessions) > 1:
                raise WorkforceGatewayError(
                    "canonical outcome must identify the current attempt"
                )
            return latest
        if run_id != latest.run_id:
            raise WorkforceGatewayError(
                "canonical outcome references a superseded attempt: "
                f"{run_id}"
            )
        return latest

    @staticmethod
    def _process_identity_payload(
        raw: Mapping[str, Any] | None,
        *,
        expected_pid: int | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(raw, Mapping):
            return None
        raw_pid = raw.get("pid")
        raw_create_time = raw.get("create_time")
        if (
            isinstance(raw_pid, bool)
            or isinstance(raw_create_time, bool)
            or raw_pid is None
            or raw_create_time is None
        ):
            return None
        try:
            pid = int(raw_pid)
            create_time = int(raw_create_time)
        except (TypeError, ValueError):
            return None
        if pid <= 0 or create_time <= 0:
            return None
        if expected_pid is not None and pid != int(expected_pid):
            return None
        executable = raw.get("exe")
        return {
            "pid": pid,
            "create_time": create_time,
            "exe": str(executable) if executable else None,
        }

    @staticmethod
    def _load_pid_records(path: Path) -> dict[str, dict[str, Any]]:
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkforceGatewayError(
                f"worker PID table is unreadable: {path}"
            ) from error
        if not isinstance(raw, dict):
            raise WorkforceGatewayError(
                f"worker PID table is not an object: {path}"
            )
        records: dict[str, dict[str, Any]] = {}
        for run_id, record in raw.items():
            if not isinstance(record, dict) or "pid" not in record:
                raise WorkforceGatewayError(
                    f"worker PID table contains an invalid record: {path}"
                )
            records[str(run_id)] = dict(record)
        return records

    @classmethod
    def _same_process_identity(
        cls,
        left: Mapping[str, Any] | None,
        right: Mapping[str, Any] | None,
        *,
        expected_pid: int | None = None,
    ) -> bool:
        normalized_left = cls._process_identity_payload(
            left,
            expected_pid=expected_pid,
        )
        normalized_right = cls._process_identity_payload(
            right,
            expected_pid=expected_pid,
        )
        return (
            normalized_left is not None
            and normalized_right is not None
            and normalized_left == normalized_right
        )

    def _require_sealed_process_status(
        self,
        session: WorkerSession,
        workers: WorkerController,
    ) -> WorkerStatus:
        if session.result.get("launch_phase") != "PROCESS_REGISTERED":
            raise WorkforceGatewayError(
                "sealed workforce process is not durably registered: "
                f"{session.run_id}"
            )
        status = workers.status(session.run_id)
        record = self._load_pid_records(workers.pid_table_path()).get(
            session.run_id
        )
        try:
            pid = int(record["pid"]) if record is not None else 0
            session_pid = int(session.result.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
            session_pid = 0
        recorded_identity = self._process_identity_payload(
            record.get("creation_identity") if record is not None else None,
            expected_pid=pid if pid > 0 else None,
        )
        session_identity = self._process_identity_payload(
            session.result.get("process_creation_identity"),
            expected_pid=pid if pid > 0 else None,
        )
        observed_identity = self._process_identity_payload(
            process_creation_identity(pid) if pid > 0 else None,
            expected_pid=pid if pid > 0 else None,
        )
        if (
            not status.alive
            or status.owner_id != worker_lease_key(session.run_id)
            or status.expires_at is None
            or status.lease_expired
            or parse(status.expires_at) <= utc_now()
            or record is None
            or pid <= 0
            or session_pid != pid
            or recorded_identity is None
            or session_identity != recorded_identity
            or observed_identity != recorded_identity
            or (
                status.process is not None
                and (
                    status.process.pid != pid
                    or status.process.poll() is not None
                )
            )
        ):
            raise WorkforceGatewayError(
                "sealed workforce process is dead or changed identity before "
                f"heartbeat: {session.run_id}"
            )
        return status

    @classmethod
    def _root_process_epoch(
        cls,
        root_pid: int,
        identity: Mapping[str, Any] | None,
    ) -> OwnedProcessEpoch | None:
        normalized = cls._process_identity_payload(
            identity,
            expected_pid=root_pid,
        )
        if normalized is None:
            return None
        return OwnedProcessEpoch(
            root_pid=root_pid,
            identities=((
                normalized["pid"],
                normalized["create_time"],
                normalized["exe"],
            ),),
        )

    @classmethod
    def _capture_process_epoch(
        cls,
        root_pid: int,
        *,
        expected_identity: Mapping[str, Any],
    ) -> OwnedProcessEpoch | None:
        expected = cls._process_identity_payload(
            expected_identity,
            expected_pid=root_pid,
        )
        if expected is None or not pid_alive(root_pid):
            return None
        observed = cls._process_identity_payload(
            process_creation_identity(root_pid),
            expected_pid=root_pid,
        )
        if observed != expected:
            return None
        for _attempt in range(3):
            first_tree = tuple(dict.fromkeys(list_process_tree(root_pid)))
            if root_pid not in first_tree:
                return None
            captured: dict[int, dict[str, Any]] = {}
            unstable = False
            for pid in first_tree:
                identity = cls._process_identity_payload(
                    process_creation_identity(pid),
                    expected_pid=pid,
                )
                if identity is None:
                    if pid_alive(pid):
                        unstable = True
                        break
                    continue
                if (
                    pid != root_pid
                    and identity["create_time"] < expected["create_time"]
                ):
                    return None
                captured[pid] = identity
            if unstable:
                continue
            second_tree = tuple(dict.fromkeys(list_process_tree(root_pid)))
            if set(second_tree) != set(first_tree):
                continue
            verified: list[tuple[int, int, str | None]] = []
            for pid in first_tree:
                identity = captured.get(pid)
                if identity is None:
                    unstable = True
                    break
                observed_identity = cls._process_identity_payload(
                    process_creation_identity(pid),
                    expected_pid=pid,
                )
                if observed_identity != identity:
                    unstable = True
                    break
                verified.append((
                    identity["pid"],
                    identity["create_time"],
                    identity["exe"],
                ))
            if unstable:
                continue
            root = next(
                (item for item in verified if item[0] == root_pid),
                None,
            )
            if root != (
                expected["pid"],
                expected["create_time"],
                expected["exe"],
            ):
                return None
            ordered = (root, *(
                item for item in verified if item[0] != root_pid
            ))
            return OwnedProcessEpoch(
                root_pid=root_pid,
                identities=tuple(ordered),
            )
        return None

    @classmethod
    def _process_epoch_is_alive(cls, epoch: OwnedProcessEpoch) -> bool:
        for pid, create_time, executable in epoch.identities:
            if not pid_alive(pid):
                continue
            observed = cls._process_identity_payload(
                process_creation_identity(pid),
                expected_pid=pid,
            )
            if observed is None:
                return True
            if observed["create_time"] != create_time:
                continue
            observed_executable = observed.get("exe")
            if (
                executable
                and (
                    not observed_executable
                    or executable.casefold()
                    != str(observed_executable).casefold()
                )
            ):
                return True
            return True
        return False

    @classmethod
    def _capture_orphaned_descendants(
        cls,
        root_pid: int,
        *,
        root_create_time: int,
    ) -> tuple[tuple[int, int, str | None], ...] | None:
        for _attempt in range(3):
            first_tree = tuple(
                pid
                for pid in dict.fromkeys(list_process_tree(root_pid))
                if pid != root_pid
            )
            captured: dict[int, dict[str, Any]] = {}
            unstable = False
            for pid in first_tree:
                identity = cls._process_identity_payload(
                    process_creation_identity(pid),
                    expected_pid=pid,
                )
                if identity is None:
                    if pid_alive(pid):
                        unstable = True
                        break
                    continue
                if identity["create_time"] < root_create_time:
                    return None
                captured[pid] = identity
            if unstable:
                continue
            second_tree = tuple(
                pid
                for pid in dict.fromkeys(list_process_tree(root_pid))
                if pid != root_pid
            )
            if set(second_tree) != set(first_tree):
                continue
            verified: list[tuple[int, int, str | None]] = []
            for pid in first_tree:
                identity = captured.get(pid)
                if identity is None:
                    unstable = True
                    break
                if (
                    cls._process_identity_payload(
                        process_creation_identity(pid),
                        expected_pid=pid,
                    )
                    != identity
                ):
                    unstable = True
                    break
                verified.append((
                    identity["pid"],
                    identity["create_time"],
                    identity["exe"],
                ))
            if not unstable:
                return tuple(verified)
        return None

    @classmethod
    def _terminate_process_epoch(
        cls,
        epoch: OwnedProcessEpoch,
        *,
        control_authority_check: Callable[[], None] | None = None,
    ) -> bool:
        if os.name != "nt":
            raise WorkforceGatewayError(
                "exact process-epoch termination is unavailable on this host"
            )
        root = next(
            (
                identity
                for identity in epoch.identities
                if identity[0] == epoch.root_pid
            ),
            None,
        )
        if root is None:
            raise WorkforceGatewayError(
                "owned process epoch lacks its root identity"
            )
        root_pid, root_create_time, root_executable = root
        root_identity = {
            "pid": root_pid,
            "create_time": root_create_time,
            "exe": root_executable,
        }
        root_epoch = OwnedProcessEpoch(
            root_pid=root_pid,
            identities=(root,),
        )
        known = {
            pid: (pid, create_time, executable)
            for pid, create_time, executable in epoch.identities
        }
        terminated = False
        deadline = time.monotonic() + 5.0
        quiet_scans = 0
        while time.monotonic() < deadline:
            if control_authority_check is not None:
                control_authority_check()
            refreshed = cls._capture_process_epoch(
                root_pid,
                expected_identity=root_identity,
            )
            if refreshed is None:
                if cls._process_epoch_is_alive(root_epoch):
                    time.sleep(0.01)
                    continue
                break
            for identity in refreshed.identities:
                known[identity[0]] = identity
            descendants = tuple(
                identity
                for identity in reversed(refreshed.identities)
                if identity[0] != root_pid
            )
            if descendants:
                quiet_scans = 0
            else:
                quiet_scans += 1
                if quiet_scans >= 2:
                    break
            for pid, create_time, executable in descendants:
                if control_authority_check is not None:
                    control_authority_check()
                terminated = (
                    cls._terminate_windows_process_identity(
                        pid,
                        create_time,
                        executable,
                        control_authority_check=control_authority_check,
                    )
                    or terminated
                )
            time.sleep(0.01)
        if cls._process_epoch_is_alive(root_epoch):
            if control_authority_check is not None:
                control_authority_check()
            terminated = (
                cls._terminate_windows_process_identity(
                    root_pid,
                    root_create_time,
                    root_executable,
                    control_authority_check=control_authority_check,
                )
                or terminated
            )
        quiet_scans = 0
        while time.monotonic() < deadline:
            if control_authority_check is not None:
                control_authority_check()
            if pid_alive(root_pid):
                observed_root = cls._process_identity_payload(
                    process_creation_identity(root_pid),
                    expected_pid=root_pid,
                )
                if observed_root != root_identity:
                    raise WorkforceGatewayError(
                        "owned process root PID was reused before descendant "
                        f"reconciliation: {root_pid}"
                    )
            descendants = cls._capture_orphaned_descendants(
                root_pid,
                root_create_time=root_create_time,
            )
            if descendants is None:
                time.sleep(0.01)
                continue
            for identity in descendants:
                known[identity[0]] = identity
            live_descendants = tuple(
                identity
                for pid, identity in known.items()
                if pid != root_pid
                and cls._process_epoch_is_alive(OwnedProcessEpoch(
                    root_pid=root_pid,
                    identities=(identity,),
                ))
            )
            if not live_descendants and not cls._process_epoch_is_alive(
                root_epoch
            ):
                quiet_scans += 1
                if quiet_scans >= 2:
                    break
                time.sleep(0.01)
                continue
            quiet_scans = 0
            for pid, create_time, executable in reversed(live_descendants):
                if control_authority_check is not None:
                    control_authority_check()
                terminated = (
                    cls._terminate_windows_process_identity(
                        pid,
                        create_time,
                        executable,
                        control_authority_check=control_authority_check,
                    )
                    or terminated
                )
            if cls._process_epoch_is_alive(root_epoch):
                terminated = (
                    cls._terminate_windows_process_identity(
                        root_pid,
                        root_create_time,
                        root_executable,
                        control_authority_check=control_authority_check,
                    )
                    or terminated
                )
            time.sleep(0.01)
        final_epoch = OwnedProcessEpoch(
            root_pid=root_pid,
            identities=tuple(known.values()),
        )
        if control_authority_check is not None:
            control_authority_check()
        if cls._process_epoch_is_alive(final_epoch):
            raise WorkforceGatewayError(
                "exact owned process epoch remained alive after termination: "
                f"{epoch.root_pid}"
            )
        remaining = cls._capture_orphaned_descendants(
            root_pid,
            root_create_time=root_create_time,
        )
        if remaining is None or any(
            cls._process_epoch_is_alive(OwnedProcessEpoch(
                root_pid=root_pid,
                identities=(identity,),
            ))
            for identity in remaining
        ):
            raise WorkforceGatewayError(
                "owned process descendants remained ambiguous after "
                f"termination: {epoch.root_pid}"
            )
        if control_authority_check is not None:
            control_authority_check()
        return terminated

    @staticmethod
    def _terminate_windows_process_identity(
        pid: int,
        create_time: int,
        executable: str | None,
        *,
        control_authority_check: Callable[[], None] | None = None,
    ) -> bool:
        import ctypes
        from ctypes import wintypes

        process_terminate = 0x0001
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        still_active = 259
        wait_timeout = 258
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
        ]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(
            process_terminate | process_query_limited_information | synchronize,
            False,
            int(pid),
        )
        if not handle:
            return False
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return False
            observed_create_time = (
                creation.dwHighDateTime << 32
            ) | creation.dwLowDateTime
            if observed_create_time != int(create_time):
                return False
            if executable:
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                if not kernel32.QueryFullProcessImageNameW(
                    handle,
                    0,
                    buffer,
                    ctypes.byref(size),
                ):
                    return False
                if str(buffer.value).casefold() != executable.casefold():
                    return False
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            if exit_code.value != still_active:
                return False
            if control_authority_check is not None:
                control_authority_check()
            if not kernel32.TerminateProcess(handle, 1):
                raise WorkforceGatewayError(
                    "failed to terminate exact owned process identity: "
                    f"{pid}"
                )
            if kernel32.WaitForSingleObject(handle, 5000) == wait_timeout:
                raise WorkforceGatewayError(
                    "timed out terminating exact owned process identity: "
                    f"{pid}"
                )
            if control_authority_check is not None:
                control_authority_check()
            return True
        finally:
            kernel32.CloseHandle(handle)

    def _runner_command(
        self,
        package_id: str,
        run_id: str,
    ) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "jarvis_foundry.workforce.runner",
            "--home",
            str(Path(self.database.path).resolve().parent),
            "--package-id",
            str(package_id),
            "--run-id",
            str(run_id),
        ]
        if self.worker_config_path is not None:
            command.extend(("--config", str(self.worker_config_path)))
        return command

    def _record_process_registration(
        self,
        binding: GatewayBinding,
        status: WorkerStatus,
        workers: WorkerController,
        *,
        admission_authority: _AdmissionAuthority,
    ) -> GatewayBinding:
        pid_record = self._load_pid_records(workers.pid_table_path()).get(
            binding.session.run_id
        )
        if not status.alive:
            raise WorkforceGatewayError(
                "sealed workforce process exited before durable registration: "
                f"{binding.session.run_id}"
            )
        if pid_record is None or pid_record.get("pid") is None:
            raise WorkforceGatewayError(
                "live sealed workforce process lacks durable PID registration: "
                f"{binding.session.run_id}"
            )
        pid = int(pid_record["pid"])
        recorded_identity = self._process_identity_payload(
            pid_record.get("creation_identity"),
            expected_pid=pid,
        )
        observed_identity = self._process_identity_payload(
            process_creation_identity(pid),
            expected_pid=pid,
        )
        if recorded_identity is None or observed_identity != recorded_identity:
            raise WorkforceGatewayError(
                "live sealed workforce process lacks an exact durable creation "
                f"identity: {binding.session.run_id}"
            )
        if status.process is not None and status.process.pid != pid:
            raise WorkforceGatewayError(
                "live sealed workforce process disagrees with its durable PID "
                f"registration: {binding.session.run_id}"
            )
        process_pid_path = workers.pid_table_path()
        with self.database.transaction() as connection:
            self._assert_admission_authority(
                admission_authority,
                connection=connection,
            )
            current_pid_record = self._load_pid_records(
                process_pid_path
            ).get(binding.session.run_id)
            current_identity = self._process_identity_payload(
                process_creation_identity(pid),
                expected_pid=pid,
            )
            if (
                current_pid_record is None
                or int(current_pid_record.get("pid") or 0) != pid
                or not self._same_process_identity(
                    current_pid_record.get("creation_identity"),
                    recorded_identity,
                    expected_pid=pid,
                )
                or not pid_alive(pid)
                or current_identity != recorded_identity
                or (
                    status.process is not None
                    and status.process.poll() is not None
                )
            ):
                raise WorkforceGatewayError(
                    "sealed workforce process exited or changed identity "
                    f"during durable registration: {binding.session.run_id}"
                )
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            package = state.package(binding.package_id).package
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package,
            )
            session = state.sessions[binding.session.session_id]
            latest = self._latest_session(state, binding.package_id)
            if latest is None or latest.session_id != session.session_id:
                raise WorkforceGatewayError(
                    "sealed process registration references a superseded attempt: "
                    f"{session.run_id}"
                )
            if (
                session.status in TERMINAL_SESSION_STATUSES
                or session.result.get("canonical_outcome")
            ):
                raise WorkforceGatewayError(
                    "terminal sealed workforce attempt cannot register a process: "
                    f"{session.run_id}"
                )
            worker_lease = connection.execute(
                "SELECT owner_id FROM leases WHERE lease_key = ?",
                (status.lease_key,),
            ).fetchone()
            if (
                worker_lease is None
                or str(worker_lease["owner_id"]) != status.lease_key
            ):
                raise WorkforceGatewayError(
                    "live sealed workforce process lacks its exact durable "
                    f"worker lease: {session.run_id}"
                )
            if (
                session.result.get("launch_phase") == "PROCESS_REGISTERED"
                and session.result.get("pid") is not None
                and session.result.get("worker_lease_key")
            ):
                if (
                    int(session.result["pid"]) != pid
                    or str(session.result["worker_lease_key"])
                    != status.lease_key
                    or not self._same_process_identity(
                        session.result.get("process_creation_identity"),
                        recorded_identity,
                        expected_pid=pid,
                    )
                ):
                    raise WorkforceGatewayError(
                        "sealed workforce process registration changed identity: "
                        f"{session.run_id}"
                    )
                return replace(binding, session=session)
            running = replace(
                session,
                status="RUNNING",
                heartbeat_at=isoformat(),
                result={
                    **session.result,
                    "launch_phase": "PROCESS_REGISTERED",
                    "pid": pid,
                    "worker_lease_key": status.lease_key,
                    "process_creation_identity": recorded_identity,
                },
            )
            event = _event(
                "workforce.session_state_changed",
                _versioned_session_payload_on_connection(
                    connection,
                    running,
                ),
                key=(
                    "workforce.session_state_changed:"
                    f"{session.session_id}:process-registered"
                ),
                correlation_id=session.package_id,
                source_id=self.source_id,
            )
            self.database.append_events_on_connection(connection, [event])
            current_pid_record = self._load_pid_records(
                process_pid_path
            ).get(binding.session.run_id)
            current_identity = self._process_identity_payload(
                process_creation_identity(pid),
                expected_pid=pid,
            )
            if (
                current_pid_record is None
                or int(current_pid_record.get("pid") or 0) != pid
                or not self._same_process_identity(
                    current_pid_record.get("creation_identity"),
                    recorded_identity,
                    expected_pid=pid,
                )
                or not pid_alive(pid)
                or current_identity != recorded_identity
                or (
                    status.process is not None
                    and status.process.poll() is not None
                )
            ):
                raise WorkforceGatewayError(
                    "sealed workforce process exited or changed identity "
                    f"before registration commit: {session.run_id}"
                )
            self._assert_admission_authority(
                admission_authority,
                connection=connection,
            )
            _gate(
                self.database,
                self.controller,
                stop_path=self.stop_path,
                package=package,
            )
        return replace(binding, session=running)

    def _cleanup_failed_spawn(
        self,
        run_id: str,
        workers: WorkerController,
        *,
        spawned_status: WorkerStatus | None,
    ) -> None:
        current_status = workers.status(run_id)
        tracked_process = (
            spawned_status.process
            if spawned_status is not None
            and spawned_status.process is not None
            else current_status.process
        )
        pid_record = self._load_pid_records(
            workers.pid_table_path()
        ).get(run_id)
        root_pid = (
            int(tracked_process.pid)
            if tracked_process is not None
            else (
                int(pid_record["pid"])
                if pid_record is not None and pid_record.get("pid") is not None
                else None
            )
        )
        epoch: OwnedProcessEpoch | None = None
        root_epoch: OwnedProcessEpoch | None = None
        tracked_alive = bool(
            tracked_process is not None
            and tracked_process.poll() is None
        )
        if (
            tracked_process is not None
            and not tracked_alive
            and current_status.alive
        ):
            raise WorkforceGatewayError(
                "failed spawn PID state names a live epoch after the exact "
                "spawned process exited"
            )
        recorded_identity = (
            self._process_identity_payload(
                pid_record.get("creation_identity"),
                expected_pid=root_pid,
            )
            if isinstance(pid_record, Mapping) and root_pid is not None
            else None
        )
        if recorded_identity is not None and root_pid is not None:
            root_epoch = self._root_process_epoch(
                root_pid,
                recorded_identity,
            )
        if tracked_alive:
            if root_pid is None:
                raise WorkforceGatewayError(
                    "live failed spawn has no process identity"
                )
            observed_identity = self._process_identity_payload(
                process_creation_identity(root_pid),
                expected_pid=root_pid,
            )
            if (
                recorded_identity is not None
                and observed_identity != recorded_identity
            ):
                raise WorkforceGatewayError(
                    "live failed spawn disagrees with its durable creation "
                    "identity"
                )
            normalized = recorded_identity or observed_identity
            if normalized is None:
                raise WorkforceGatewayError(
                    "live failed spawn has no exact creation identity"
                )
            root_epoch = self._root_process_epoch(root_pid, normalized)
            epoch = self._capture_process_epoch(
                root_pid,
                expected_identity=normalized,
            )
            if epoch is None:
                raise WorkforceGatewayError(
                    "live failed spawn changed process epoch before cleanup"
                )
            if tracked_process.poll() is not None:
                raise WorkforceGatewayError(
                    "exact failed-spawn process exited before epoch termination"
                )
            tracked = getattr(workers, "_processes", {}).get(run_id)
            if (
                tracked_process is not None
                and tracked is not tracked_process
            ):
                raise WorkforceGatewayError(
                    "failed spawn process tracking changed before cleanup"
                )
            self._terminate_process_epoch(epoch)
        elif (
            root_epoch is not None
            and self._process_epoch_is_alive(root_epoch)
        ):
            if tracked_process is not None:
                raise WorkforceGatewayError(
                    "failed spawn PID state may name a live epoch after the "
                    "exact spawned process exited"
                )
            epoch = self._capture_process_epoch(
                root_pid,
                expected_identity=recorded_identity,
            )
            if epoch is None:
                raise WorkforceGatewayError(
                    "failed spawn live process identity is ambiguous"
                )
            self._terminate_process_epoch(epoch)
        elif tracked_process is None and current_status.alive:
            raise WorkforceGatewayError(
                "live failed spawn has no exact creation identity"
            )
        current_record = self._load_pid_records(
            workers.pid_table_path()
        ).get(run_id)
        if current_record != pid_record:
            raise WorkforceGatewayError(
                "failed spawn PID registration changed before cleanup"
            )
        if epoch is not None and current_record is not None:
            root_identity = {
                "pid": epoch.identities[0][0],
                "create_time": epoch.identities[0][1],
                "exe": epoch.identities[0][2],
            }
            if not self._same_process_identity(
                current_record.get("creation_identity"),
                root_identity,
                expected_pid=epoch.root_pid,
            ):
                raise WorkforceGatewayError(
                    "failed spawn PID registration changed before cleanup"
                )
        workers.detach(run_id)
        leased_runs = getattr(workers, "_leased", None)
        if leased_runs is not None:
            leased_runs.discard(run_id)
        worker_key = worker_lease_key(run_id)
        lease = self.database.row("leases", "lease_key", worker_key)
        if lease is not None:
            if str(lease["owner_id"]) != worker_key:
                raise WorkforceGatewayError(
                    "failed spawn worker lease changed owner before cleanup"
                )
            workers.leases.release(worker_key, worker_key)
        if (
            workers.status(run_id).alive
            or self.database.row("leases", "lease_key", worker_key) is not None
            or (
                leased_runs is not None
                and run_id in leased_runs
            )
            or self._load_pid_records(
                workers.pid_table_path()
            ).get(run_id) is not None
            or (
                root_epoch is not None
                and self._process_epoch_is_alive(root_epoch)
            )
            or (epoch is not None and self._process_epoch_is_alive(epoch))
        ):
            raise WorkforceGatewayError(
                "failed spawn retained live process, worker lease, or PID state"
            )

    def _record_crash(
        self,
        binding: GatewayBinding,
        error: BaseException,
        *,
        admission_authority: _AdmissionAuthority | None = None,
        control_authority_check: (
            Callable[[Any | None], None] | None
        ) = None,
    ) -> GatewayResult:
        if admission_authority is None:
            with self._admission_guard(
                binding.package_id,
                renew=True,
            ) as authority:
                return self._record_crash_under_authority(
                    binding,
                    error,
                    authority,
                    control_authority_check=control_authority_check,
                )
        return self._record_crash_under_authority(
            binding,
            error,
            admission_authority,
            control_authority_check=control_authority_check,
        )

    def _record_crash_under_authority(
        self,
        binding: GatewayBinding,
        error: BaseException,
        admission_authority: _AdmissionAuthority,
        *,
        control_authority_check: (
            Callable[[Any | None], None] | None
        ) = None,
    ) -> GatewayResult:
        with self.database.transaction() as connection:
            self._assert_admission_authority(
                admission_authority,
                connection=connection,
            )
            if control_authority_check is not None:
                control_authority_check(connection)
            state = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            )
            sessions = state.sessions_for(binding.package_id)
            if not sessions:
                raise WorkforceGatewayError(
                    "canonical crash has no package session: "
                    f"{binding.package_id}"
                )
            session = max(sessions, key=lambda candidate: candidate.attempt)
            if session.run_id != binding.session.run_id:
                raise WorkforceGatewayError(
                    "canonical crash references a superseded attempt: "
                    f"{binding.session.run_id}"
                )
            current = state.package(binding.package_id)
            if session.status in TERMINAL_SESSION_STATUSES:
                prior = session.result
                same_crash = bool(
                    session.status in {"FAILED", "STOPPED"}
                    and prior.get("error_class") == type(error).__name__
                    and prior.get("error_detail") == str(error)[-500:]
                )
                if not same_crash:
                    raise WorkforceGatewayError(
                        "canonical crash references a terminal workforce "
                        f"attempt: {session.run_id}:{session.status}"
                    )
                return GatewayResult(
                    binding=self._binding_from_session(
                        current.package,
                        session,
                        replayed=True,
                    ),
                    package_status=current.status,
                    outcome=FAILED,
                    reason=(
                        f"{type(error).__name__}: {str(error)[-300:]}"
                    ),
                    replayed=True,
                )
            if current.status in {"ACCEPTED", "REJECTED", "CANCELLED"}:
                raise WorkforceGatewayError(
                    "active workforce session belongs to a terminal package: "
                    f"{binding.package_id}:{current.status}"
                )
            stop_reason = _stop_reason(
                self.database,
                self.stop_path,
                package=current.package,
            )
            crashed = replace(
                session,
                status="STOPPED" if stop_reason else "FAILED",
                finished_at=session.finished_at or isoformat(),
                result={
                    **session.result,
                    "error_class": type(error).__name__,
                    "error_detail": str(error)[-500:],
                    **(
                        {"stop_reason": stop_reason}
                        if stop_reason
                        else {}
                    ),
                },
            )
            crash_event = _event(
                "workforce.result_linked",
                _versioned_session_payload_on_connection(
                    connection,
                    crashed,
                ),
                key=(
                    f"workforce.result_linked:{crashed.session_id}:"
                    f"crash:{type(error).__name__}"
                ),
                correlation_id=crashed.package_id,
                source_id=self.source_id,
            )
            self.database.append_events_on_connection(
                connection,
                [crash_event],
            )
            current = _rebuild_workforce_state_on_connection(
                self.database,
                connection,
            ).package(binding.package_id)
            if stop_reason and current.status not in {
                "ACCEPTED",
                "REJECTED",
                "CANCELLED",
                "STOPPED",
            }:
                current = _transition_work_package_on_connection(
                    self.database,
                    connection,
                    binding.package_id,
                    "STOPPED",
                    reason=stop_reason,
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                    allow_stop=True,
                )
            elif current.status == "READY":
                current = _transition_work_package_on_connection(
                    self.database,
                    connection,
                    binding.package_id,
                    "RUNNING",
                    reason="canonical worker launch began before crash",
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                )
            if current.status == "RUNNING":
                current = _transition_work_package_on_connection(
                    self.database,
                    connection,
                    binding.package_id,
                    "FAILED",
                    reason=f"canonical worker crashed: {type(error).__name__}",
                    controller=self.controller,
                    stop_path=self.stop_path,
                    source_id=self.source_id,
                )
            if current.status == "STOPPED":
                self._release_on_connection(
                    connection,
                    binding.package_id,
                    run_id=crashed.run_id,
                    reason="stopped worker cleanup released mutable scope",
                )
            self._assert_admission_authority(
                admission_authority,
                connection=connection,
            )
            if control_authority_check is not None:
                control_authority_check(connection)
        return GatewayResult(
            binding=replace(binding, session=crashed),
            package_status=current.status,
            outcome=FAILED,
            reason=(
                stop_reason
                or f"{type(error).__name__}: {str(error)[-300:]}"
            ),
        )

    def _route_for_run(self, run_id: str) -> dict[str, Any]:
        for event in reversed(
            self.database.events(
                event_types=("worker.route_decided",)
            )
        ):
            payload = event.payload
            if str(payload.get("run_id") or "") == run_id:
                return dict(payload)
        return {}

    def _accepted_receipt(
        self,
        package,
        *,
        run_id: str,
        verdict_id: str | None = None,
    ) -> dict[str, Any] | None:
        expected_criteria = self._verification_criteria(package)
        started_by_verdict: dict[str, dict[str, Any]] = {}
        for event in self.database.events(
            event_types=("verification.started",),
            readonly=True,
        ):
            payload = event.payload
            candidate_verdict = str(payload.get("verdict_id") or "")
            if (
                event.source_type == "supervisor"
                and event.source_id == "mission-loop"
                and candidate_verdict
                and str(payload.get("mission_id") or "")
                == str(package.mission_id)
                and str(payload.get("subject_id") or "")
                == str(package.step_id)
                and str(payload.get("builder_run_id") or "") == run_id
                and tuple(payload.get("criteria") or ()) == expected_criteria
            ):
                started_by_verdict[candidate_verdict] = dict(payload)
        for event in reversed(
            self.database.events(
                event_types=("verification.accepted",),
                readonly=True,
            )
        ):
            payload = event.payload
            candidate_verdict = str(payload.get("verdict_id") or "")
            if (
                event.source_type == "supervisor"
                and event.source_id == "mission-loop"
                and payload.get("decision") == "ACCEPT"
                and candidate_verdict in started_by_verdict
                and (
                    verdict_id is None
                    or candidate_verdict == str(verdict_id)
                )
                and str(payload.get("mission_id") or "")
                == str(package.mission_id)
                and str(payload.get("subject_id") or "")
                == str(package.step_id)
                and str(payload.get("builder_run_id") or "") == run_id
            ):
                return {
                    **dict(payload),
                    "criteria": list(expected_criteria),
                    "acceptance_event_id": event.event_id,
                }
        return None

    @contextmanager
    def _admission_guard(
        self,
        package_id: str,
        *,
        renew: bool = False,
        execution_run_id: str | None = None,
    ):
        lock_key = f"workforce-admission:{package_id}"
        lock_owner = (
            f"{self._execution_guard_owner_prefix(execution_run_id)}"
            f"{uuid.uuid4().hex}"
            if execution_run_id is not None
            else f"workforce-control:{package_id}:{uuid.uuid4().hex}"
        )
        lease_seconds = _ADMISSION_LEASE_SECONDS
        for attempt in range(100):
            try:
                self.leases.acquire(lock_key, lock_owner, lease_seconds)
                break
            except LeaseUnavailable as error:
                if attempt == 99:
                    raise WorkforceGatewayError(
                        f"package admission remained busy: {package_id}"
                    ) from error
                time.sleep(0.05)
        heartbeat_errors: list[BaseException] = []
        authority = _AdmissionAuthority(
            package_id=package_id,
            lease_key=lock_key,
            owner_id=lock_owner,
            heartbeat_errors=heartbeat_errors,
        )
        heartbeat_stop: threading.Event | None = None
        heartbeat_thread: threading.Thread | None = None
        if renew:
            (
                heartbeat_stop,
                heartbeat_thread,
                heartbeat_errors,
            ) = self._start_admission_heartbeat(
                authority,
                lease_seconds=lease_seconds,
            )
        try:
            yield authority
            if heartbeat_errors:
                raise WorkforceGatewayError(
                    f"package admission authority was lost: {package_id}"
                ) from heartbeat_errors[0]
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join()
            self.leases.release(lock_key, lock_owner)

    @contextmanager
    def _preemptible_admission_guard(
        self,
        package_id: str,
        expected_run_id: str,
        *,
        validate_current: Callable[[Any | None], ExecutionAdmissionState],
        can_terminate_owned: Callable[[], bool],
        terminate_owned: Callable[
            [_AdmissionAuthority],
            OwnedProcessTerminationResult,
        ],
    ):
        """Take package authority, preempting only its exact owned execution."""

        lock_key = f"workforce-admission:{package_id}"
        lock_owner = f"workforce-control:{package_id}:{uuid.uuid4().hex}"
        deadline = time.monotonic() + 5.0
        acquired = False
        observed_execution_owner: str | None = None
        last_error: LeaseUnavailable | None = None

        while not acquired:
            execution = validate_current(None)
            if (
                not execution.terminal
                and execution.process_termination_required
            ):
                if not execution.externally_controllable:
                    raise WorkforceGatewayError(
                        "active inline workforce execution cannot be "
                        f"preempted by process control: {expected_run_id}"
                    )
                if not execution.process_registration_complete:
                    if time.monotonic() >= deadline:
                        raise WorkforceGatewayError(
                            "sealed workforce process registration remained "
                            f"incomplete: {expected_run_id}"
                        )
                    time.sleep(0.05)
                    continue
                if not can_terminate_owned():
                    if time.monotonic() < deadline:
                        time.sleep(0.05)
                        continue
                    raise WorkforceGatewayError(
                        "active workforce execution cannot be preempted "
                        "without an exact controllable owned process: "
                        f"{expected_run_id}"
                    )
            try:
                observed_execution_owner = (
                    self._claim_preemption_authority(
                        lock_key,
                        expected_run_id=expected_run_id,
                        new_owner=lock_owner,
                        validate_current=validate_current,
                    )
                )
                acquired = True
            except LeaseUnavailable as error:
                last_error = error
                if time.monotonic() >= deadline:
                    raise WorkforceGatewayError(
                        f"package admission remained busy: {package_id}"
                    ) from last_error
                time.sleep(0.05)
        heartbeat_errors: list[BaseException] = []
        authority = _AdmissionAuthority(
            package_id=package_id,
            lease_key=lock_key,
            owner_id=lock_owner,
            heartbeat_errors=heartbeat_errors,
        )
        heartbeat_stop, heartbeat_thread, heartbeat_errors = (
            self._start_admission_heartbeat(
                authority,
                lease_seconds=_ADMISSION_LEASE_SECONDS,
            )
        )
        try:
            self._assert_admission_authority(authority)
            validate_current(None)
            termination = terminate_owned(authority)
            self._assert_admission_authority(authority)
            execution = validate_current(None)
            if not termination.cleanup_succeeded:
                raise WorkforceGatewayError(
                    "owned workforce process cleanup did not complete: "
                    f"{expected_run_id}"
                )
            yield AdmissionPreemption(
                authority=authority,
                observed_execution_owner=observed_execution_owner,
                termination=termination,
            )
            self._assert_admission_authority(authority)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()
            if acquired:
                self.leases.release(lock_key, lock_owner)

    def _claim_preemption_authority(
        self,
        lease_key: str,
        *,
        expected_run_id: str,
        new_owner: str,
        validate_current: Callable[[Any | None], ExecutionAdmissionState],
    ) -> str | None:
        with self.database.transaction() as connection:
            now = utc_now()
            expires = isoformat(
                now + timedelta(seconds=_ADMISSION_LEASE_SECONDS)
            )
            execution = validate_current(connection)
            if (
                not execution.terminal
                and execution.process_termination_required
                and (
                    not execution.externally_controllable
                    or not execution.process_registration_complete
                )
            ):
                raise WorkforceGatewayError(
                    "workforce execution is not durably preemptible: "
                    f"{expected_run_id}"
                )
            row = connection.execute(
                "SELECT owner_id, expires_at FROM leases "
                "WHERE lease_key = ?",
                (lease_key,),
            ).fetchone()
            observed_owner = str(row["owner_id"]) if row is not None else ""
            observed_execution_owner = (
                observed_owner
                if self._execution_guard_owner_matches(
                    observed_owner,
                    expected_run_id,
                )
                else None
            )
            if (
                row is not None
                and observed_owner != new_owner
                and parse(str(row["expires_at"])) > now
                and observed_execution_owner is None
            ):
                raise LeaseUnavailable(
                    f"lease {lease_key} is held by another live owner"
                )
            if row is None:
                connection.execute(
                    "INSERT INTO leases("
                    "lease_key, owner_id, expires_at, heartbeat_at"
                    ") VALUES (?, ?, ?, ?)",
                    (lease_key, new_owner, expires, isoformat(now)),
                )
            else:
                cursor = connection.execute(
                    "UPDATE leases SET owner_id = ?, expires_at = ?, "
                    "heartbeat_at = ? WHERE lease_key = ? AND owner_id = ?",
                    (
                        new_owner,
                        expires,
                        isoformat(now),
                        lease_key,
                        observed_owner,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LeaseUnavailable(
                        f"lease {lease_key} owner epoch changed"
                    )
            return observed_execution_owner

    def _start_admission_heartbeat(
        self,
        authority: _AdmissionAuthority,
        *,
        lease_seconds: int,
    ) -> tuple[threading.Event, threading.Thread, list[BaseException]]:
        stop = threading.Event()
        errors = authority.heartbeat_errors

        def heartbeat() -> None:
            interval = max(
                0.05,
                min(
                    _ADMISSION_LEASE_RENEW_SECONDS,
                    lease_seconds / 3,
                ),
            )
            while not stop.wait(interval):
                try:
                    self.leases.renew(
                        authority.lease_key,
                        authority.owner_id,
                        lease_seconds,
                    )
                except BaseException as error:
                    errors.append(error)
                    return

        thread = threading.Thread(
            target=heartbeat,
            name=f"workforce-admission-lease:{authority.package_id}",
            daemon=True,
        )
        thread.start()
        return stop, thread, errors

    def _assert_admission_authority(
        self,
        authority: _AdmissionAuthority,
        *,
        connection: Any | None = None,
    ) -> None:
        if authority.heartbeat_errors:
            raise WorkforceGatewayError(
                "package admission authority heartbeat failed before durable "
                f"result linkage: {authority.package_id}"
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
            raise WorkforceGatewayError(
                "package admission authority was lost before durable result "
                f"linkage: {authority.package_id}"
            )

    @staticmethod
    def _execution_guard_owner_prefix(run_id: str) -> str:
        digest = hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()
        return f"workforce-execution:{digest}:"

    @classmethod
    def _execution_guard_owner_matches(
        cls,
        owner_id: str,
        run_id: str,
    ) -> bool:
        return str(owner_id).startswith(
            cls._execution_guard_owner_prefix(run_id)
        )

    def _take_over_admission_lease(
        self,
        lease_key: str,
        *,
        observed_owner: str,
        new_owner: str,
    ) -> bool:
        now = utc_now()
        expires = isoformat(
            now + timedelta(seconds=_ADMISSION_LEASE_SECONDS)
        )
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT owner_id FROM leases WHERE lease_key = ?",
                (lease_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO leases("
                    "lease_key, owner_id, expires_at, heartbeat_at"
                    ") VALUES (?, ?, ?, ?)",
                    (lease_key, new_owner, expires, isoformat(now)),
                )
                return True
            if str(row["owner_id"]) != observed_owner:
                return False
            connection.execute(
                "UPDATE leases SET owner_id = ?, expires_at = ?, "
                "heartbeat_at = ? WHERE lease_key = ? AND owner_id = ?",
                (
                    new_owner,
                    expires,
                    isoformat(now),
                    lease_key,
                    observed_owner,
                ),
            )
            return True

    @staticmethod
    def _acquire_resource_lease_on_connection(
        connection,
        lease_key: str,
        owner_id: str,
        duration_seconds: int,
    ) -> None:
        now = utc_now()
        row = connection.execute(
            "SELECT owner_id, expires_at FROM leases WHERE lease_key = ?",
            (lease_key,),
        ).fetchone()
        if (
            row is not None
            and str(row["owner_id"]) != owner_id
            and parse(str(row["expires_at"])) > now
        ):
            raise WorkforceGatewayError(
                f"mutable resource is leased by another run: {lease_key}"
            )
        expires = isoformat(now + timedelta(seconds=duration_seconds))
        connection.execute(
            "INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(lease_key) DO UPDATE SET "
            "owner_id=excluded.owner_id, expires_at=excluded.expires_at, "
            "heartbeat_at=excluded.heartbeat_at",
            (lease_key, owner_id, expires, isoformat(now)),
        )


__all__ = [
    "AdmissionPreemption",
    "CanonicalMissionLoop",
    "ExecutionAdmissionState",
    "GatewayBinding",
    "GatewayLaunch",
    "GatewayResult",
    "OwnedProcessEpoch",
    "OwnedProcessTerminationResult",
    "WorkforceGateway",
    "WorkforceGatewayError",
]
