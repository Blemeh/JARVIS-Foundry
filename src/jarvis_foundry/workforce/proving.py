"""Bounded provider-independent proof for the V13 workforce control plane."""
from __future__ import annotations

import json
import os
import subprocess
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from ..control.capabilities import set_capability_state
from ..database import FoundryDatabase
from ..effects import (
    COMPLETED,
    FAILED,
    FROZEN,
    STARTED,
    effect_state,
    execute_fabric_effect,
    finalize_verified_effect,
    freeze_effect,
)
from ..events import Event, canonical_json, payload_hash
from ..mission.contracts import (
    MissionStepContract,
    MissionStepContractError,
    latest_contract,
    record_step_contract,
)
from ..roles.reaper import (
    reap_stale_role_runs,
    recover_stale_mission_state,
    role_lease_key,
)
from ..supervisor.leases import LeaseManager
from ..supervisor.worker import WorkerController, terminate_owned_tree
from .commander import WorkforceCommander
from .contracts import WorkPackage, WorkerSession
from .convergence import (
    CONVERGENCE_PACKAGE_DELTAS_SCHEMA,
    ConvergenceAnalysisError,
    PackageConvergenceEvidence,
    reconstruct_convergence_package_deltas,
    validate_convergence_integration_union,
    validate_convergence_package_deltas,
)
from .state import (
    SUPERVISOR_CONTROLLER,
    WorkforceStateError,
    _acceptance_receipt_on_connection,
    _gate,
    rebuild_workforce_state,
    transition_work_package,
)

PROOF_DATE = "2026-09-04"
PROOF_SCHEMA = "jarvis.workforce.long-run-proof.v2"
PROOF_NOW = f"{PROOF_DATE}T23:59:59Z"

_REPAIR_PACKAGE = "proof-repair-runner"
_STOP_PACKAGE = "proof-stop-runner"
_REASSIGN_PACKAGE = "proof-reassign-runner"
_INTEGRATION_PACKAGE = "proof-integration-verifier"
_INTEGRATION_TARGET = "proof/integration"
_OBJECTIVE_ID = "objective-v13-long-run"
_CONVERGENCE_DECISION_ID = "decision:v13-long-run-proof"
_CONVERGENCE_SOURCE_ID = "v13-long-run-restart"
_GOAL_DRIFT_PACKAGE = "proof-goal-drift"
_SCOPE_ESCAPE_PACKAGE = "proof-scope-escape"
_ENDLESS_ANALYSIS_PACKAGE = "proof-endless-analysis"
_SKIPPED_TESTS_PACKAGE = "proof-skipped-tests"
_FALSE_COMPLETION_PACKAGE = "proof-false-completion"
_STEP_ID = "step-v13-long-run"
_CANDIDATE_COMMIT_SCRIPT = ".jarvis-proof-candidate-commit.py"
_INTEGRATION_CANDIDATE_SCHEMA = (
    "jarvis.workforce.integration-candidate.v1"
)
_OBJECTIVE_COMPLETION_SCHEMA = (
    "jarvis.workforce.objective-completion.v1"
)
_OBJECTIVE_COMPLETION_EVENT = "workforce.objective_completion_recorded"
_INTEGRATION_CANDIDATE_KEY = (
    f"workforce.integration_candidate_created:{_OBJECTIVE_ID}"
)
_OBJECTIVE_COMPLETION_KEY = (
    f"{_OBJECTIVE_COMPLETION_EVENT}:{_OBJECTIVE_ID}"
)
_INTEGRATION_EFFECT_KIND = "git.integration_candidate"
_INTEGRATION_EFFECT_CONNECTOR = "v13-proof-git-integration"
_INTEGRATION_COMMIT_DATE = "2026-09-04T00:00:00Z"
_EXPECTED_CONVERGENCE_MATRIX = {
    _REPAIR_PACKAGE: ("ACCEPTED", "UNIQUE_ACCEPTED_DELTA"),
    _REASSIGN_PACKAGE: ("ACCEPTED", "UNIQUE_ACCEPTED_DELTA"),
    "proof-overlap-a": ("ACCEPTED", "CONFLICTING"),
    "proof-overlap-b": ("ACCEPTED", "CONFLICTING"),
    "proof-weak": ("REJECTED", "FAILED_REJECTED"),
}
_COMPLETION_ORDER = (
    "PACKAGE_RESULTS_ACCEPTED",
    "CONVERGENCE_RECORDED",
    "INTEGRATION_COMMIT_CREATED",
    "INTEGRATION_CANDIDATE_RECORDED",
    "INTEGRATION_VERIFICATION_STARTED",
    "INTEGRATION_ACCEPTED",
    "OBJECTIVE_COMPLETE",
)


class WorkforceLongRunProofError(RuntimeError):
    """The disposable proof failed one of its required invariants."""


class _ObjectiveEvidenceError(WorkforceLongRunProofError):
    """Canonical objective evidence failed with a stable blocker code."""

    def __init__(self, blocker: str, message: str) -> None:
        super().__init__(message)
        self.blocker = blocker


@dataclass(frozen=True, slots=True)
class _ProofLedgerEvent:
    event_id: str
    event_type: str
    sequence: int
    source_type: str
    source_id: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IncidentClassification:
    package_id: str
    expected_kind: str
    observed_kind: str
    observation_id: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterventionProof:
    package_id: str
    action: str
    intervention_id: str
    outcome: str
    replacement_session_id: str | None
    sealed_relaunch: bool
    replayed_without_duplicate: bool
    request_events: int
    completion_events: int


@dataclass(frozen=True, slots=True)
class ObjectiveProof:
    objective_id: str
    before_status: str
    status: str
    accepted_packages: tuple[str, ...]
    stronger_run_ids: tuple[str, ...]
    verifier_run_ids: tuple[str, ...]
    integration_sha: str | None
    integration_verifier_run_id: str | None
    integration_receipt_id: str | None
    completion_event_id: str | None
    completion_sequence: int | None
    completion_order: tuple[str, ...]
    independently_verified: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkforceLongRunReport:
    schema: str
    proof_date: str
    report_path: str
    source_repository: str
    database_path: str
    duration_seconds: float
    overlap: dict[str, Any]
    recovery: dict[str, Any]
    incidents: tuple[IncidentClassification, ...]
    interventions: tuple[InterventionProof, ...]
    stop: dict[str, Any]
    convergence: dict[str, Any]
    integration: dict[str, Any]
    restart: dict[str, Any]
    objective: ObjectiveProof
    event_counts: dict[str, int]
    limitations: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), sort_keys=True))

    def write_json(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.report_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target


def run_long_run_proof(root: str | Path) -> WorkforceLongRunReport:
    """Run the bounded V13 proof entirely inside ``root``."""

    started = time.perf_counter()
    proof_root = Path(root).expanduser().resolve()
    proof_root.mkdir(parents=True, exist_ok=True)
    source_repository, source_sha = _create_repository(proof_root)
    home = proof_root / "home"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    _record_start(database)
    stop_path = home / "EMERGENCY_STOP"
    crash_config_path = _write_lab_config(
        proof_root,
        name="worker-crash.yaml",
        crash_after_write=True,
    )
    success_config_path = _write_lab_config(
        proof_root,
        name="worker-success.yaml",
        crash_after_write=False,
    )
    workers = WorkerController(database, lease_seconds=120)
    controllers = [workers]
    commander = WorkforceCommander(
        database,
        workers=workers,
        stop_path=stop_path,
        worker_config_path=crash_config_path,
        source_id="v13-long-run-proof",
    )
    package_ids = (
        _REPAIR_PACKAGE,
        _STOP_PACKAGE,
        _REASSIGN_PACKAGE,
        _GOAL_DRIFT_PACKAGE,
        _SCOPE_ESCAPE_PACKAGE,
        _ENDLESS_ANALYSIS_PACKAGE,
        _SKIPPED_TESTS_PACKAGE,
        _FALSE_COMPLETION_PACKAGE,
    )
    packages: dict[str, WorkPackage] = {}
    contracts: dict[str, MissionStepContract] = {}
    for package_id in package_ids:
        package, contract = _build_package(
            proof_root,
            source_sha,
            package_id,
            allow_crash_marker=package_id == _REPAIR_PACKAGE,
        )
        packages[package_id] = package
        contracts[package_id] = contract

    admitted = commander.admit(
        tuple(packages[package_id] for package_id in package_ids),
        source_repositories=source_repository,
        max_parallel=1,
    )
    _require(
        len(admitted) == len(package_ids)
        and all(item.package_state.status == "READY" for item in admitted)
        and all(item.preparation is not None for item in admitted),
        "all initial packages must own prepared READY worktrees",
    )
    for package_id in package_ids:
        _record_mission(database, packages[package_id], contracts[package_id])

    report_path = proof_root / f"v13-workforce-proof-{PROOF_DATE}.json"
    try:
        with _child_pythonpath():
            first_launch = commander.launch_processes(
                (_REPAIR_PACKAGE, _STOP_PACKAGE)
            )
            first_runs = {
                launch.binding.package_id: launch.binding.session.run_id
                for launch in first_launch.launches
            }
            _wait_for(
                lambda: all(
                    workers.status(run_id).alive
                    for run_id in first_runs.values()
                )
                and all(
                    _artifact(packages[package_id]).is_file()
                    for package_id in first_runs
                ),
                timeout=12,
                description="two sealed runners to overlap after partial writes",
            )
            overlap_processes = tuple(
                {
                    "package_id": launch.binding.package_id,
                    "run_id": launch.binding.session.run_id,
                    "pid": int(launch.binding.session.result["pid"]),
                    "launch_kind": str(
                        rebuild_workforce_state(database)
                        .sessions[launch.binding.session.session_id]
                        .result.get("launch_kind")
                    ),
                    "alive": workers.status(
                        launch.binding.session.run_id
                    ).alive,
                }
                for launch in first_launch.launches
            )
            _require(
                first_launch.peak_concurrency >= 2
                and len(first_launch.live_run_ids) == 2
                and all(item["alive"] for item in overlap_processes)
                and all(
                    item["launch_kind"] == "SEALED_PACKAGE_RUNNER"
                    for item in overlap_processes
                ),
                "two real owned sealed runners must overlap",
            )

            repair_launch = next(
                launch
                for launch in first_launch.launches
                if launch.binding.package_id == _REPAIR_PACKAGE
            )
            repair_process = repair_launch.worker_status.process
            _require(
                repair_process is not None,
                "repair runner must expose its owned process handle",
            )
            killed_pids = terminate_owned_tree(
                repair_process.pid,
                process=repair_process,
            )
            repair_process.wait(timeout=10)
            _require(
                not workers.status(first_runs[_REPAIR_PACKAGE]).alive,
                "simulated runner crash must leave the owned process dead",
            )

            reopened = FoundryDatabase(database.path)
            reopened.initialize()
            restart_workers = WorkerController(reopened, lease_seconds=120)
            controllers.append(restart_workers)
            restarted = WorkforceCommander(
                reopened,
                workers=restart_workers,
                stop_path=stop_path,
                worker_config_path=success_config_path,
                source_id=_CONVERGENCE_SOURCE_ID,
            )

            blocked_package, _blocked_contract = _build_package(
                proof_root,
                source_sha,
                "proof-stop-blocked",
            )
            events_before_stop_probe = reopened.event_count(readonly=True)
            stop_path.write_text(
                "owner stop for bounded V13 proof\n",
                encoding="utf-8",
            )
            stop_error = ""
            try:
                restarted.admit(
                    (blocked_package,),
                    source_repositories=source_repository,
                )
            except PermissionError as error:
                stop_error = str(error)
            stop_event_count_unchanged = (
                reopened.event_count(readonly=True)
                == events_before_stop_probe
            )
            _require(
                bool(stop_error)
                and stop_event_count_unchanged
                and not Path(blocked_package.worktree_path).exists(),
                "STOP must block package creation and worktree mutation",
            )
            stop_termination = restarted.intervene(
                _STOP_PACKAGE,
                "TERMINATE",
                reason="owner STOP requires exact owned process termination",
                session_id=first_runs[_STOP_PACKAGE],
                intervention_id="intervention:proof-stop-terminate",
            )
            _require(
                stop_termination.terminated_owned_process
                and stop_termination.intervention.outcome == "TERMINATED"
                and stop_termination.package_status == "CANCELLED"
                and not restart_workers.status(
                    first_runs[_STOP_PACKAGE]
                ).alive,
                "STOP must permit exact termination of the owned runner",
            )
            stop_path.unlink(missing_ok=True)

            dead_audit = restarted.audit_restart(
                package_id=_REPAIR_PACKAGE,
                now=PROOF_NOW,
            )
            dead_session = dead_audit.package(_REPAIR_PACKAGE).sessions[0]
            _require(
                dead_session.category == "DEAD"
                and dead_session.process_backed
                and dead_session.process_identity_valid
                and dead_session.process_alive is False,
                "restart audit must reconstruct the killed runner as DEAD",
            )
            reconciled = restarted.reconcile_restart(
                package_id=_REPAIR_PACKAGE
            )
            _require(
                tuple(item.outcome for item in reconciled.actions)
                == ("RECONCILED",)
                and reconciled.after.package(
                    _REPAIR_PACKAGE
                ).sessions[0].category
                == "TERMINAL",
                "dead runner must reconcile once to terminal failure",
            )
            reaped_roles, recovered_missions = _recover_dead_role(
                reopened,
                packages[_REPAIR_PACKAGE],
                first_runs[_REPAIR_PACKAGE],
            )
            _recover_dead_role(
                reopened,
                packages[_STOP_PACKAGE],
                first_runs[_STOP_PACKAGE],
            )
            restart_workers.reap()
            partial_incident = _observe_expected(
                restarted,
                _REPAIR_PACKAGE,
                "PARTIAL_MUTATION",
            )

            provider_incident = _prepare_incident(
                restarted,
                packages[_REASSIGN_PACKAGE],
                session_status="FAILED",
                result={},
                expected_kind="PROVIDER_OUTAGE",
                extra_event=(
                    "provider.dispatch_failed",
                    {
                        "failure_class": "OUTAGE",
                        "provider": "lab_scripted_hermes",
                        "possible_effect": False,
                    },
                ),
            )
            incident_results = [
                partial_incident,
                provider_incident,
                _prepare_incident(
                    restarted,
                    packages[_GOAL_DRIFT_PACKAGE],
                    session_status="HEALTHY",
                    result={"goal_alignment": False},
                    expected_kind="GOAL_DRIFT",
                ),
                _prepare_incident(
                    restarted,
                    packages[_SCOPE_ESCAPE_PACKAGE],
                    session_status="HEALTHY",
                    result={},
                    expected_kind="SCOPE_ESCAPE",
                    mutate_relative_path="outside/escaped.txt",
                ),
                _prepare_incident(
                    restarted,
                    packages[_ENDLESS_ANALYSIS_PACKAGE],
                    session_status="HEALTHY",
                    result={"analysis_rounds": 3},
                    expected_kind="ENDLESS_ANALYSIS",
                ),
                _prepare_incident(
                    restarted,
                    packages[_SKIPPED_TESTS_PACKAGE],
                    session_status="COMPLETED",
                    result={"tests_skipped": True},
                    expected_kind="MANDATORY_TESTS_SKIPPED",
                ),
                _prepare_incident(
                    restarted,
                    packages[_FALSE_COMPLETION_PACKAGE],
                    session_status="COMPLETED",
                    result={"status": "COMPLETED"},
                    expected_kind="FALSE_COMPLETION",
                ),
            ]

            repair_execution = restarted.intervene(
                _REPAIR_PACKAGE,
                "REPAIR",
                reason="dead runner left a bounded partial artifact",
                observation_id=partial_incident.observation_id,
                instruction="Replace the partial artifact and rerun all criteria.",
                intervention_id="intervention:proof-repair",
            )
            repair_replay = restarted.intervene(
                _REPAIR_PACKAGE,
                "REPAIR",
                reason="dead runner left a bounded partial artifact",
                observation_id=partial_incident.observation_id,
                instruction="Replace the partial artifact and rerun all criteria.",
                intervention_id="intervention:proof-repair",
            )
            reassign_execution = restarted.intervene(
                _REASSIGN_PACKAGE,
                "REASSIGN",
                reason="durable provider outage requires the safe lab route",
                observation_id=provider_incident.observation_id,
                replacement_framework="hermes",
                intervention_id="intervention:proof-reassign",
            )
            reassign_replay = restarted.intervene(
                _REASSIGN_PACKAGE,
                "REASSIGN",
                reason="durable provider outage requires the safe lab route",
                observation_id=provider_incident.observation_id,
                replacement_framework="hermes",
                intervention_id="intervention:proof-reassign",
            )
            _require(
                repair_execution.intervention.outcome
                == "READY_FOR_RELAUNCH"
                and repair_replay.replayed
                and reassign_execution.intervention.outcome
                == "READY_FOR_RELAUNCH"
                and reassign_replay.replayed,
                "REPAIR and REASSIGN must be governed and replay-safe",
            )
            _crash_marker(packages[_REPAIR_PACKAGE]).unlink(
                missing_ok=True
            )
            before_objective = _objective_state(
                reopened,
                (_REPAIR_PACKAGE, _REASSIGN_PACKAGE),
            )
            _require(
                before_objective["status"] == "INCOMPLETE",
                "objective must remain incomplete before stronger results",
            )

            for package_id in (_REPAIR_PACKAGE, _REASSIGN_PACKAGE):
                set_capability_state(
                    reopened,
                    contracts[package_id].resource_id,
                    "git_local_commit",
                    "DEGRADED",
                    source_id="v13-long-run-bound-candidate",
                    evidence={
                        "package_id": package_id,
                        "reason": (
                            "JARVIS-side required-test effect creates the "
                            "candidate commit before verifier binding"
                        ),
                    },
                )
            successful_launch = restarted.launch_processes(
                (_REPAIR_PACKAGE, _REASSIGN_PACKAGE)
            )
            return_codes = _wait_for_launches(
                successful_launch.launches,
                timeout=75,
            )
            restart_workers.reap()
            _require(
                set(return_codes.values()) == {0},
                "attempt-two sealed runners must exit successfully",
            )
            final_state = restarted.snapshot()
            _require(
                final_state.package(_REPAIR_PACKAGE).status == "ACCEPTED"
                and final_state.package(_REASSIGN_PACKAGE).status
                == "ACCEPTED",
                "stronger repaired and reassigned results must be accepted",
            )

            package_acceptance_state = _objective_state(
                reopened,
                (_REPAIR_PACKAGE, _REASSIGN_PACKAGE),
            )
            _require(
                package_acceptance_state["status"] == "INCOMPLETE"
                and package_acceptance_state["packages_verified"]
                and package_acceptance_state["accepted_packages"]
                == [_REPAIR_PACKAGE, _REASSIGN_PACKAGE],
                (
                    "accepted package results are necessary but cannot "
                    "complete the objective before convergence, integration, "
                    "and integrated verification"
                ),
            )

            repair_sha = _git(
                Path(packages[_REPAIR_PACKAGE].worktree_path),
                "rev-parse",
                "HEAD",
            )
            reassign_sha = _git(
                Path(packages[_REASSIGN_PACKAGE].worktree_path),
                "rev-parse",
                "HEAD",
            )
            overlap_a_sha = _commit_branch(
                source_repository,
                branch="candidate/proof-overlap-a",
                parent=source_sha,
                changes={"src/overlap.txt": "overlap-a\n"},
            )
            overlap_b_sha = _commit_branch(
                source_repository,
                branch="candidate/proof-overlap-b",
                parent=source_sha,
                changes={"src/overlap.txt": "overlap-b\n"},
            )
            weak_sha = _commit_branch(
                source_repository,
                branch="candidate/proof-weak",
                parent=source_sha,
                changes={"src/weak.txt": "weak and rejected\n"},
            )
            convergence_candidates = (
                _convergence_evidence(
                    _REPAIR_PACKAGE,
                    source_sha,
                    repair_sha,
                    status="ACCEPTED",
                ),
                _convergence_evidence(
                    _REASSIGN_PACKAGE,
                    source_sha,
                    reassign_sha,
                    status="ACCEPTED",
                ),
                _convergence_evidence(
                    "proof-overlap-a",
                    source_sha,
                    overlap_a_sha,
                    status="ACCEPTED",
                ),
                _convergence_evidence(
                    "proof-overlap-b",
                    source_sha,
                    overlap_b_sha,
                    status="ACCEPTED",
                ),
                _convergence_evidence(
                    "proof-weak",
                    source_sha,
                    weak_sha,
                    status="REJECTED",
                ),
            )
            convergence = restarted.converge(
                source_repository,
                decision_id=_CONVERGENCE_DECISION_ID,
                integration_target=_INTEGRATION_TARGET,
                target_sha=source_sha,
                packages=convergence_candidates,
            )
            _require(
                convergence.classifications["proof-overlap-a"]
                == "CONFLICTING"
                and convergence.classifications["proof-overlap-b"]
                == "CONFLICTING"
                and convergence.classifications["proof-weak"]
                == "FAILED_REJECTED"
                and convergence.classifications[_REPAIR_PACKAGE]
                == "UNIQUE_ACCEPTED_DELTA"
                and convergence.classifications[_REASSIGN_PACKAGE]
                == "UNIQUE_ACCEPTED_DELTA",
                "semantic convergence must separate strong, conflicting, and rejected results",
            )

            convergence_event = _event_for(
                reopened,
                "workforce.convergence_recorded",
                "decision_id",
                convergence.decision_id,
            )
            integration = _create_integration_candidate(
                reopened,
                source_repository,
                target=_INTEGRATION_TARGET,
                base_sha=source_sha,
                candidates=convergence_candidates,
                classifications=convergence.classifications,
                convergence_event_id=convergence_event.event_id,
                stop_path=stop_path,
            )
            integration_event = _record_integration_candidate(
                reopened,
                integration,
                convergence_event_id=convergence_event.event_id,
                stop_path=stop_path,
            )
            integration_package, integration_contract = (
                _build_integration_verification_package(
                    proof_root,
                    integration_sha=str(integration["integration_sha"]),
                    integrated_artifacts=tuple(
                        packages[package_id].expected_artifacts[0]
                        for package_id in integration["included_packages"]
                    ),
                    excluded_deltas=tuple(
                        integration["excluded_deltas"]
                    ),
                )
            )
            packages[_INTEGRATION_PACKAGE] = integration_package
            integration_admission = restarted.admit(
                (integration_package,),
                source_repositories=source_repository,
            )
            _require(
                len(integration_admission) == 1
                and integration_admission[0].package_state.status == "READY"
                and integration_admission[0].preparation is not None,
                "integration verifier must own a prepared READY worktree",
            )
            _record_mission(
                reopened,
                integration_package,
                integration_contract,
            )
            integration_verifier = _verify_integration_candidate(
                restarted,
                integration_package,
                config_path=success_config_path,
                expected_sha=str(integration["integration_sha"]),
                integrated_artifacts=tuple(
                    packages[package_id].expected_artifacts[0]
                    for package_id in integration["included_packages"]
                ),
            )

            _require(
                max(package_acceptance_state["receipt_sequences"])
                < convergence_event.sequence
                < integration_event.sequence
                < int(integration_verifier["started_sequence"])
                < int(integration_verifier["receipt_sequence"]),
                "durable completion evidence is out of order",
            )
            integration = {
                **integration,
                "convergence_event_id": convergence_event.event_id,
                "convergence_sequence": convergence_event.sequence,
                "candidate_event_id": integration_event.event_id,
                "candidate_sequence": integration_event.sequence,
                "verifier": integration_verifier,
            }
            precompletion_state = _objective_state(
                reopened,
                (_REPAIR_PACKAGE, _REASSIGN_PACKAGE),
                integration=integration,
            )
            _require(
                precompletion_state["status"] == "INCOMPLETE"
                and precompletion_state["integration_verified"]
                and precompletion_state["blockers"]
                == ["objective:completion-event-missing"],
                "pure objective reconstruction must await an explicit command",
            )
            completion_record = _record_objective_completion(
                reopened,
                (_REPAIR_PACKAGE, _REASSIGN_PACKAGE),
                stop_path=stop_path,
            )
            objective_state = _objective_state(
                reopened,
                (_REPAIR_PACKAGE, _REASSIGN_PACKAGE),
                integration=integration,
            )
            _require(
                objective_state["status"] == "COMPLETE"
                and objective_state["independently_verified"]
                and objective_state["completion_event_id"]
                == completion_record.event_id,
                (
                    "objective can complete only after the integrated SHA "
                    "receives canonical independent acceptance"
                ),
            )
            completion_order = tuple(objective_state["completion_order"])
            _require(
                int(integration_verifier["receipt_sequence"])
                < int(objective_state["completion_sequence"]),
                "objective completion must follow integration acceptance",
            )
            integration = {
                **integration,
                "independently_verified": bool(
                    objective_state["integration_verified"]
                ),
                "completion_event_id": str(
                    objective_state["completion_event_id"]
                ),
                "completion_sequence": int(
                    objective_state["completion_sequence"]
                ),
                "completion_order": list(completion_order),
            }
            objective = ObjectiveProof(
                objective_id=_OBJECTIVE_ID,
                before_status=str(before_objective["status"]),
                status=str(objective_state["status"]),
                accepted_packages=tuple(
                    objective_state["accepted_packages"]
                ),
                stronger_run_ids=tuple(objective_state["run_ids"]),
                verifier_run_ids=tuple(
                    objective_state["verifier_run_ids"]
                ),
                integration_sha=str(objective_state["integration_sha"]),
                integration_verifier_run_id=str(
                    objective_state["integration_verifier_run_id"]
                ),
                integration_receipt_id=str(
                    objective_state["integration_receipt_id"]
                ),
                completion_event_id=str(
                    objective_state["completion_event_id"]
                ),
                completion_sequence=int(
                    objective_state["completion_sequence"]
                ),
                completion_order=completion_order,
                independently_verified=bool(
                    objective_state["independently_verified"]
                ),
                blockers=tuple(objective_state["blockers"]),
            )

            final_audit = restarted.audit_restart(now=PROOF_NOW)
            effect_evidence = _effect_evidence(reopened)
            state = rebuild_workforce_state(reopened)
            _require(
                final_audit.duplicate_issues == ()
                and effect_evidence["duplicate_events"] == []
                and reopened.event_count(
                    event_type="workforce.package_created",
                    readonly=True,
                )
                == len(state.packages)
                and reopened.event_count(
                    event_type="workforce.worktree_prepared",
                    readonly=True,
                )
                == len(state.packages),
                "restart audit must find no duplicate package, worktree, or effect identity",
            )

            interventions = (
                _intervention_proof(
                    reopened,
                    repair_execution,
                    repair_replay,
                ),
                _intervention_proof(
                    reopened,
                    reassign_execution,
                    reassign_replay,
                ),
            )
            event_counts = {
                event_type: reopened.event_count(
                    event_type=event_type,
                    readonly=True,
                )
                for event_type in (
                    "verification.accepted",
                    "workforce.convergence_recorded",
                    "workforce.integration_candidate_created",
                    _OBJECTIVE_COMPLETION_EVENT,
                    "workforce.intervention_completed",
                    "workforce.intervention_recorded",
                    "workforce.observation_recorded",
                    "workforce.package_created",
                    "workforce.session_bound",
                    "workforce.worktree_prepared",
                )
            }
            report = WorkforceLongRunReport(
                schema=PROOF_SCHEMA,
                proof_date=PROOF_DATE,
                report_path=str(report_path),
                source_repository=str(source_repository),
                database_path=str(reopened.path),
                duration_seconds=round(time.perf_counter() - started, 3),
                overlap={
                    "peak_concurrency": first_launch.peak_concurrency,
                    "live_run_ids": list(first_launch.live_run_ids),
                    "processes": list(overlap_processes),
                },
                recovery={
                    "package_id": _REPAIR_PACKAGE,
                    "run_id": first_runs[_REPAIR_PACKAGE],
                    "killed_pids": killed_pids,
                    "before_category": dead_session.category,
                    "reconciliation_actions": [
                        item.outcome for item in reconciled.actions
                    ],
                    "after_category": reconciled.after.package(
                        _REPAIR_PACKAGE
                    ).sessions[0].category,
                    "role_runs_reaped": reaped_roles,
                    "missions_recovered": recovered_missions,
                },
                incidents=tuple(incident_results),
                interventions=interventions,
                stop={
                    "blocked_new_mutation": bool(stop_error),
                    "blocked_reason": stop_error,
                    "event_count_unchanged": stop_event_count_unchanged,
                    "termination_outcome": (
                        stop_termination.intervention.outcome
                    ),
                    "terminated_owned_process": (
                        stop_termination.terminated_owned_process
                    ),
                    "package_status": stop_termination.package_status,
                },
                convergence={
                    "decision_id": convergence.decision_id,
                    "classifications": dict(convergence.classifications),
                    "conflicts": list(convergence.conflicts),
                    "recommendation": convergence.recommendation,
                },
                integration=integration,
                restart={
                    "package_count": len(state.packages),
                    "package_creation_events": event_counts[
                        "workforce.package_created"
                    ],
                    "worktree_preparation_events": event_counts[
                        "workforce.worktree_prepared"
                    ],
                    "effect_action_count": effect_evidence[
                        "effect_action_count"
                    ],
                    "completed_effect_actions": effect_evidence[
                        "completed_actions"
                    ],
                    "duplicate_effect_events": effect_evidence[
                        "duplicate_events"
                    ],
                    "duplicate_issue_codes": [
                        item.code for item in final_audit.duplicate_issues
                    ],
                },
                objective=objective,
                event_counts=event_counts,
                limitations=(
                    "Provider outage, goal drift, skipped tests, and false completion are deterministic lab evidence, not live provider claims.",
                    "JARVIS-side required-test receipts read the integrated artifacts and exact Git delta; the canonical independent verifier accepted the isolated proof artifact and AcceptanceGate sealed it against the exact integration HEAD.",
                    "Integration verification leaves one uncommitted proof artifact in its separate disposable worktree; the proof/integration target remains clean at the reported integration SHA.",
                    "This is a bounded disposable-repository control-plane and integration proof, not production endurance, reboot, promotion, or provider-trust evidence.",
                ),
            )
            report.write_json()
            return report
    finally:
        stop_path.unlink(missing_ok=True)
        for controller in reversed(controllers):
            controller.terminate_all()


def _record_start(database: FoundryDatabase) -> None:
    database.append(Event.create(
        "system.start_build_recorded",
        {
            "received_at": f"{PROOF_DATE}T00:00:00Z",
            "standing_mandate": True,
            "scope": "disposable-v13-long-run-proof",
        },
        source_type="owner",
        source_id="v13-long-run-proof",
        idempotency_key="system.start-build:v13-long-run-proof",
    ))


def _create_repository(root: Path) -> tuple[Path, str]:
    repository = root / "source"
    repository.mkdir(parents=True, exist_ok=True)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "v13-proof@example.invalid")
    _git(repository, "config", "user.name", "JARVIS V13 Proof")
    _git(repository, "branch", "-M", "proof/base")
    source = repository / "src"
    source.mkdir()
    (source / "base.txt").write_text("base\n", encoding="utf-8")
    (source / "overlap.txt").write_text("base\n", encoding="utf-8")
    (source / "weak.txt").write_text("base\n", encoding="utf-8")
    (repository / ".jarvis-proof-check.py").write_text(
        "\n".join((
            "from pathlib import Path",
            "import sys",
            "",
            "artifact = Path(sys.argv[1])",
            "assert artifact.is_file(), artifact",
            "assert 'VERIFIED_BY executive-worker' in artifact.read_text(",
            "    encoding='utf-8'",
            ")",
            "",
        )),
        encoding="utf-8",
    )
    (repository / _CANDIDATE_COMMIT_SCRIPT).write_text(
        "\n".join((
            "from __future__ import annotations",
            "",
            "import hashlib",
            "import subprocess",
            "import sys",
            "from pathlib import Path",
            "",
            "from jarvis_foundry.database import FoundryDatabase",
            "from jarvis_foundry.effects import execute_fabric_effect",
            "",
            "artifact = sys.argv[1].replace('\\\\', '/')",
            "package_id = sys.argv[2]",
            "database_path = (Path.cwd() / sys.argv[3]).resolve()",
            "root = Path.cwd().resolve()",
            "",
            "def git(*args: str) -> str:",
            "    completed = subprocess.run(",
            "        ['git', '-C', str(root), *args],",
            "        capture_output=True,",
            "        text=True,",
            "        encoding='utf-8',",
            "        errors='replace',",
            "        timeout=30,",
            "        check=False,",
            "    )",
            "    if completed.returncode != 0:",
            "        raise RuntimeError(",
            "            completed.stderr.strip()",
            "            or f\"git {' '.join(args)} failed\"",
            "        )",
            "    return completed.stdout.strip()",
            "",
            "before = git('rev-parse', 'HEAD')",
            "action_material = f'v13-bound-candidate:{package_id}:a2'",
            "digest = hashlib.sha256(",
            "    action_material.encode('utf-8')",
            ").hexdigest()",
            "",
            "def perform() -> dict[str, object]:",
            "    git('add', '--', artifact)",
            "    staged = tuple(filter(",
            "        None,",
            "        git(",
            "            'diff', '--cached', '--name-only', '--no-renames'",
            "        ).replace('\\\\', '/').splitlines(),",
            "    ))",
            "    if staged != (artifact,):",
            "        raise RuntimeError(",
            "            f'candidate commit scope mismatch: {staged!r}'",
            "        )",
            "    git('commit', '-qm', f'proof candidate: {package_id}')",
            "    after = git('rev-parse', 'HEAD')",
            "    parent = git('rev-parse', f'{after}^')",
            "    changed = tuple(filter(",
            "        None,",
            "        git(",
            "            'diff', '--name-only', '--no-renames',",
            "            f'{parent}..{after}',",
            "        ).replace('\\\\', '/').splitlines(),",
            "    ))",
            "    if parent != before or changed != (artifact,):",
            "        raise RuntimeError(",
            "            'candidate commit is not one exact artifact delta'",
            "        )",
            "    return {",
            "        'claimed': 'COMPLETED',",
            "        'performed': True,",
            "        'baseline_commit': before,",
            "        'commit_head': after,",
            "        'files_changed': list(changed),",
            "    }",
            "",
            "database = FoundryDatabase(database_path)",
            "result = execute_fabric_effect(",
            "    database,",
            "    action_key=f'effect:{digest}',",
            "    system_id=f'workforce-resource:{package_id}',",
            "    target_ref=str(root),",
            "    payload_hash=digest,",
            "    kind='git_candidate_commit',",
            "    connector_id='v13-proof-git-candidate',",
            "    before_state_capture={",
            "        'head': before,",
            "        'artifact': artifact,",
            "    },",
            "    expected_effects=(",
            "        'create one exact package candidate commit',",
            "    ),",
            "    action_id=f'candidate-commit:{package_id}:a2',",
            "    request_identity={",
            "        'package_id': package_id,",
            "        'attempt': 2,",
            "        'artifact': artifact,",
            "    },",
            "    perform=perform,",
            ")",
            "if result.get('claimed') != 'COMPLETED':",
            "    raise RuntimeError(",
            "        f'candidate commit effect did not complete: {result}'",
            "    )",
            "print(str(result.get('commit_head') or 'replayed'))",
            "",
        )),
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "proof base")
    return repository, _git(repository, "rev-parse", "HEAD")


def _write_lab_config(
    root: Path,
    *,
    name: str,
    crash_after_write: bool,
) -> Path:
    path = root / name
    runtime_root = (root / "runtime").as_posix()
    path.write_text(
        "\n".join((
            "enabled: true",
            "status: ACTIVE",
            "lab_scripted_hermes: true",
            (
                "lab_crash_after_write: true"
                if crash_after_write
                else "lab_crash_after_write: false"
            ),
            f"lab_crash_step_id: {_STEP_ID}",
            f"runtime_state_root: {runtime_root}",
            "verification:",
            "  max_repairs: 1",
            "",
        )),
        encoding="utf-8",
    )
    return path


def _build_package(
    root: Path,
    source_sha: str,
    package_id: str,
    *,
    allow_crash_marker: bool = False,
) -> tuple[WorkPackage, MissionStepContract]:
    artifact = f"src/{package_id}.txt"
    mission_id = f"mission-{package_id}"
    resource_id = f"workforce-resource:{package_id}"
    contract = MissionStepContract(
        mission_id=mission_id,
        step_id=_STEP_ID,
        version=1,
        resource_id=resource_id,
        capability="write_registered_worktree_files",
        target_relative_path=artifact,
        instructions=f"Create {artifact} for the bounded V13 proof.",
        expected_effects=(f"{artifact} is created and independently verified",),
        rollback={"method": "preserve_worktree", "reversible": True},
        risk_class="C1",
        verification_criteria=(
            f"{artifact} exists in the owned package worktree",
            "an independent verifier accepts the exact artifact bytes",
        ),
        expected_artifacts=(artifact,),
        forbidden_paths=("live",),
    )
    allowed_paths = (
        (artifact, ".jarvis-owned-worker.pid")
        if allow_crash_marker
        else (artifact,)
    )
    package = WorkPackage(
        package_id=package_id,
        parent_objective_id="objective-v13-long-run",
        mission_id=mission_id,
        step_id=_STEP_ID,
        role="builder",
        capability=contract.capability,
        bounded_goal=f"Complete the isolated {package_id} proof package.",
        acceptance_criteria=contract.verification_criteria,
        source_sha=source_sha,
        repository_id="v13-long-run-disposable",
        branch_name=f"proof/{package_id}",
        worktree_path=str(root / "worktrees" / package_id),
        allowed_paths=allowed_paths,
        forbidden_paths=contract.forbidden_paths,
        mission_step_contract_id=contract.contract_id,
        mission_step_contract_hash=contract.content_hash(),
        expected_artifacts=contract.expected_artifacts,
        required_tests=(
            _artifact_required_test(artifact),
            "git diff --check",
        ) + (
            (_candidate_commit_test(artifact, package_id),)
            if package_id in {_REPAIR_PACKAGE, _REASSIGN_PACKAGE}
            else ()
        ),
        evidence_requirements=("independent canonical verifier receipt",),
        duration_seconds=120,
        lease_seconds=120,
        integration_target="proof/integration",
        owner_constraints=("never mutate live, main, or origin",),
    )
    return package, contract


def _build_integration_verification_package(
    root: Path,
    *,
    integration_sha: str,
    integrated_artifacts: tuple[str, ...],
    excluded_deltas: tuple[Mapping[str, Any], ...],
) -> tuple[WorkPackage, MissionStepContract]:
    integrated_artifacts = tuple(sorted(set(integrated_artifacts)))
    exclusion_contract = tuple(sorted(
        (
            {
                "package_id": str(delta.get("package_id") or ""),
                "classification": str(
                    delta.get("classification") or ""
                ),
                "base_sha": str(delta.get("base_sha") or "").lower(),
                "result_sha": str(delta.get("result_sha") or "").lower(),
                "changed_paths": sorted({
                    str(relative)
                    for relative in delta.get("changed_paths") or ()
                }),
            }
            for delta in excluded_deltas
        ),
        key=lambda delta: delta["package_id"],
    ))
    excluded_artifacts = tuple(sorted({
        relative
        for delta in exclusion_contract
        for relative in delta["changed_paths"]
    }))
    excluded_only_artifacts = tuple(sorted(
        set(excluded_artifacts) - set(integrated_artifacts)
    ))
    _require(
        bool(integrated_artifacts)
        and all(
            delta["package_id"]
            and delta["classification"]
            and _is_full_sha(delta["base_sha"])
            and _is_full_sha(delta["result_sha"])
            for delta in exclusion_contract
        ),
        "integration verification requires accepted artifacts",
    )
    mission_id = f"mission-{_INTEGRATION_PACKAGE}"
    resource_id = f"workforce-resource:{_INTEGRATION_PACKAGE}"
    proof_artifact = ".jarvis-integration-verification.txt"
    exclusion_criteria = tuple(
        (
            "classified excluded delta remains absent: "
            f"package_id={delta['package_id']}; "
            f"classification={delta['classification']}; "
            f"delta_hash={payload_hash(delta)}"
        )
        for delta in exclusion_contract
    )
    criteria = (
        f"the owned worktree HEAD remains exactly {integration_sha}",
        "JARVIS-side evidence commands re-read every integrated artifact",
        *exclusion_criteria,
        "an independent verifier accepts the isolated integration proof artifact",
    )
    forbidden_paths = ("live",) + excluded_only_artifacts
    contract = MissionStepContract(
        mission_id=mission_id,
        step_id=_STEP_ID,
        version=1,
        resource_id=resource_id,
        capability="write_registered_worktree_files",
        target_relative_path=".jarvis-integration-verification-target",
        instructions=(
            "Re-read the already integrated artifacts without changing their "
            "bytes, then submit them to the canonical independent verifier."
        ),
        expected_effects=(
            "the exact integration candidate receives canonical independent "
            "verification without changing its Git tree",
        ),
        rollback={"method": "preserve_worktree", "reversible": True},
        risk_class="C1",
        verification_criteria=criteria,
        expected_artifacts=(proof_artifact,),
        forbidden_paths=forbidden_paths,
    )
    package = WorkPackage(
        package_id=_INTEGRATION_PACKAGE,
        parent_objective_id="objective-v13-long-run",
        mission_id=mission_id,
        step_id=_STEP_ID,
        role="builder",
        capability=contract.capability,
        bounded_goal=(
            "Independently verify the exact coherent integration candidate."
        ),
        acceptance_criteria=contract.verification_criteria,
        source_sha=integration_sha,
        repository_id="v13-long-run-disposable",
        branch_name="proof/integration-verifier",
        worktree_path=str(root / "worktrees" / _INTEGRATION_PACKAGE),
        allowed_paths=(proof_artifact,),
        forbidden_paths=forbidden_paths,
        mission_step_contract_id=contract.contract_id,
        mission_step_contract_hash=contract.content_hash(),
        expected_artifacts=contract.expected_artifacts,
        required_tests=tuple(
            _artifact_required_test(relative)
            for relative in integrated_artifacts
        ) + (
            (
                f"git diff --exit-code {integration_sha} -- "
                + " ".join(integrated_artifacts)
            ),
            "git diff --check",
        ),
        evidence_requirements=(
            "canonical MissionLoop/AcceptanceGate receipt bound to exact HEAD",
        ),
        duration_seconds=120,
        lease_seconds=120,
        integration_target=_INTEGRATION_TARGET,
        owner_constraints=("never mutate live, main, or origin",),
    )
    return package, contract


def _artifact_required_test(relative: str) -> str:
    return f"python .jarvis-proof-check.py {relative}"


def _candidate_commit_test(relative: str, package_id: str) -> str:
    return (
        f"python {_CANDIDATE_COMMIT_SCRIPT} "
        f"{relative} {package_id} ../../home/foundry.db"
    )


def _record_mission(
    database: FoundryDatabase,
    package: WorkPackage,
    contract: MissionStepContract,
) -> None:
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": package.mission_id,
            "objective": package.bounded_goal,
            "resource_id": contract.resource_id,
            "state": "PLANNED",
            "acceptance_criteria": list(package.acceptance_criteria),
            "next_action": contract.instructions,
        },
        source_type="supervisor",
        source_id="v13-long-run-proof",
        idempotency_key=f"mission.created:{package.mission_id}",
    ))
    for status in ("ORIENTING", "ACTION_SELECTION"):
        database.append(Event.create(
            "mission.state_changed",
            {
                "mission_id": package.mission_id,
                "state": status,
                "next_action": contract.instructions,
            },
            source_type="supervisor",
            source_id="v13-long-run-proof",
            idempotency_key=(
                f"mission.state_changed:{package.mission_id}:{status}"
            ),
        ))
    record_step_contract(database, contract)


def _verify_integration_candidate(
    commander: WorkforceCommander,
    package: WorkPackage,
    *,
    config_path: Path,
    expected_sha: str,
    integrated_artifacts: tuple[str, ...],
) -> dict[str, Any]:
    from ..paths import FoundryPaths
    from ..runtime._stack import build_worker_stack
    from ..runtime.config import WorkerRuntimeConfig
    from ..runtime.mission_session import build_canonical_mission_loop
    from ..runtime.worker_loop import (
        _adapter_runtime_environment,
        _pin_routing_to_hermes,
    )

    worktree = Path(package.worktree_path)
    _require(
        _git(worktree, "rev-parse", "HEAD") == expected_sha
        and _git(worktree, "status", "--porcelain=v1") == "",
        "integration verification worktree must begin at the clean exact SHA",
    )
    for relative in integrated_artifacts:
        artifact = worktree / relative
        _require(
            artifact.is_file()
            and "VERIFIED_BY executive-worker"
            in artifact.read_text(encoding="utf-8"),
            f"integration artifact is unavailable before verification: {relative}",
        )

    binding = commander.gateway.bind(package.package_id)
    paths = FoundryPaths.discover(
        Path(commander.database.path).resolve().parent
    )
    config = WorkerRuntimeConfig.from_yaml(config_path)
    stack = build_worker_stack(
        commander.database,
        paths,
        config,
        run_id=binding.session.run_id,
    )
    try:
        _pin_routing_to_hermes(stack, config)
        loop = build_canonical_mission_loop(
            commander.database,
            paths,
            config,
            stack=stack,
            leases=LeaseManager(commander.database),
            source_id="v13-long-run-integration-verifier",
        )
        with _adapter_runtime_environment(paths, config):
            result = commander.gateway.run_inline(
                package.package_id,
                loop,
                commit_envelope=None,
            )
    finally:
        stack.close()

    _require(
        result.outcome == "PASSED"
        and result.package_status == "ACCEPTED",
        "canonical integration verification did not accept the candidate",
    )
    status_lines = tuple(filter(
        None,
        _git(worktree, "status", "--porcelain=v1").splitlines(),
    ))
    expected_probe = package.expected_artifacts[0]
    _require(
        _git(worktree, "rev-parse", "HEAD") == expected_sha
        and status_lines == (f"?? {expected_probe}",),
        (
            "canonical integration verification must leave only its isolated "
            "uncommitted proof artifact"
        ),
    )
    receipt = _canonical_acceptance_for_package(
        commander.database,
        package.package_id,
    )
    _require(
        receipt is not None
        and receipt["source_type"] == "supervisor"
        and receipt["source_id"] == "mission-loop"
        and receipt["decision"] == "ACCEPT"
        and receipt["binding_head"] == expected_sha
        and receipt["verifier_run_id"] != receipt["builder_run_id"],
        "integration acceptance is not bound to the exact canonical verifier",
    )
    return {
        **receipt,
        "package_id": package.package_id,
        "worktree_path": str(worktree),
        "worktree_head": expected_sha,
        "worktree_clean": False,
        "verification_delta": [expected_probe],
        "outcome": result.outcome,
    }


def _prepare_incident(
    commander: WorkforceCommander,
    package: WorkPackage,
    *,
    session_status: str,
    result: Mapping[str, Any],
    expected_kind: str,
    extra_event: tuple[str, Mapping[str, Any]] | None = None,
    mutate_relative_path: str | None = None,
) -> IncidentClassification:
    binding = commander.gateway.bind(package.package_id)
    transition_work_package(
        commander.database,
        package.package_id,
        "RUNNING",
        reason=f"durable lab evidence prepared for {expected_kind}",
        stop_path=commander.stop_path,
        source_id="v13-long-run-proof",
    )
    updated = replace(
        binding.session,
        status=session_status,
        heartbeat_at=f"{PROOF_DATE}T12:00:00Z",
        result={**binding.session.result, **dict(result)},
    )
    _append(
        commander.database,
        "workforce.session_state_changed",
        updated.to_payload(),
        key=(
            f"workforce.session_state_changed:{updated.session_id}:"
            f"proof:{expected_kind}"
        ),
        correlation_id=package.package_id,
    )
    if mutate_relative_path is not None:
        target = Path(package.worktree_path) / mutate_relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("escaped durable proof mutation\n", encoding="utf-8")
    if extra_event is not None:
        event_type, payload = extra_event
        _append(
            commander.database,
            event_type,
            {
                **dict(payload),
                "run_id": updated.run_id,
                "mission_id": package.mission_id,
                "step_id": package.step_id,
            },
            key=f"{event_type}:{updated.run_id}:proof",
            correlation_id=package.package_id,
        )
    return _observe_expected(
        commander,
        package.package_id,
        expected_kind,
    )


def _observe_expected(
    commander: WorkforceCommander,
    package_id: str,
    expected_kind: str,
) -> IncidentClassification:
    report = commander.observe(package_id=package_id, now=PROOF_NOW)
    _require(
        len(report.snapshots) == 1,
        f"{package_id} must have exactly one observable session",
    )
    snapshot = report.snapshots[0]
    observation = next(
        (
            item for item in report.recorded
            if item.session_id == snapshot.session_id
        ),
        None,
    )
    _require(
        snapshot.kind == expected_kind and observation is not None,
        (
            f"{package_id} expected {expected_kind}, "
            f"observed {snapshot.kind}"
        ),
    )
    return IncidentClassification(
        package_id=package_id,
        expected_kind=expected_kind,
        observed_kind=snapshot.kind,
        observation_id=observation.observation_id,
        evidence=tuple(snapshot.evidence),
    )


def _objective_state(
    database: FoundryDatabase,
    package_ids: Iterable[str],
    *,
    integration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # ``integration`` is retained for compatibility with proof/report callers,
    # but it is never an authority. Completion is rebuilt from the ledger and
    # the Git objects named by the canonical integration-candidate event.
    _ = integration
    requested = tuple(dict.fromkeys(
        str(package_id).strip()
        for package_id in package_ids
        if str(package_id).strip()
    ))
    canonical_requested = tuple(sorted(requested))
    state = rebuild_workforce_state(database)
    accepted: list[str] = []
    run_ids: list[str] = []
    verifier_ids: list[str] = []
    receipt_sequences: list[int] = []
    package_receipts: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    objective_package_universe = tuple(sorted(
        package_id
        for package_id, package_state in state.packages.items()
        if (
            package_id != _INTEGRATION_PACKAGE
            and package_state.status == "ACCEPTED"
            and package_state.package.parent_objective_id == _OBJECTIVE_ID
        )
    ))
    if canonical_requested != objective_package_universe:
        blockers.append("objective:accepted-package-universe-mismatch")
    for package_id in requested:
        package_state = state.packages.get(package_id)
        if package_state is None:
            blockers.append(f"{package_id}:status=UNKNOWN;independent=false")
            continue
        sessions = state.sessions_for(package_id)
        if not sessions:
            blockers.append(
                f"{package_id}:status={package_state.status};"
                "independent=false"
            )
            continue
        latest = max(sessions, key=lambda item: item.attempt)
        receipt = _canonical_acceptance_for_package(
            database,
            package_id,
        )
        independent = bool(
            receipt is not None
            and receipt["source_type"] == "supervisor"
            and receipt["source_id"] == "mission-loop"
            and receipt["decision"] == "ACCEPT"
            and receipt["builder_run_id"] == latest.run_id
            and receipt["verifier_run_id"]
            and receipt["verifier_run_id"] != latest.run_id
            and receipt["binding_hash"]
            and receipt["context_manifest_hash"]
        )
        if package_state.status == "ACCEPTED" and independent:
            accepted.append(package_id)
            run_ids.append(latest.run_id)
            verifier_ids.append(str(receipt["verifier_run_id"]))
            receipt_sequences.append(int(receipt["receipt_sequence"]))
            package_receipts[package_id] = receipt
        else:
            blockers.append(
                f"{package_id}:status={package_state.status};"
                f"independent={str(independent).lower()}"
            )
    packages_verified = bool(
        accepted
        and len(accepted) == len(requested)
        and canonical_requested == objective_package_universe
    )
    candidate: dict[str, Any] | None = None
    candidate_blocker = "integration:canonical-candidate-missing"
    if packages_verified:
        candidate, candidate_blocker = _canonical_integration_candidate(
            database,
            canonical_requested,
            minimum_sequence=max(receipt_sequences),
        )
    integration_receipt: dict[str, Any] | None = None
    integration_blocker = "integration:canonical-verification-incomplete"
    package_binding_blocker = ""
    if candidate is not None:
        package_binding_blocker = _accepted_package_binding_blocker(
            canonical_requested,
            package_receipts,
            candidate,
            state=state,
        )
        if not package_binding_blocker:
            integration_receipt, integration_blocker = (
                _canonical_integration_verification(
                    database,
                    state,
                    candidate,
                )
            )
    integration_verified = bool(
        candidate is not None
        and not package_binding_blocker
        and integration_receipt is not None
    )
    if packages_verified and not integration_verified:
        blockers.append(
            candidate_blocker
            if candidate is None
            else package_binding_blocker or integration_blocker
        )

    completion: _ProofLedgerEvent | None = None
    if packages_verified and integration_verified and not blockers:
        _require(
            candidate is not None and integration_receipt is not None,
            "integration verification state is internally inconsistent",
        )
        completion_payload = _objective_completion_payload(
            requested=canonical_requested,
            package_receipts=package_receipts,
            candidate=candidate,
            integration_receipt=integration_receipt,
        )
        completion, completion_blocker = _read_objective_completion(
            database,
            payload=completion_payload,
            integration_receipt=integration_receipt,
        )
        if completion is None:
            blockers.append(completion_blocker)

    completed = not blockers and completion is not None
    return {
        "status": "COMPLETE" if completed else "INCOMPLETE",
        "accepted_packages": accepted,
        "run_ids": run_ids,
        "verifier_run_ids": verifier_ids,
        "receipt_sequences": receipt_sequences,
        "packages_verified": packages_verified,
        "integration_verified": integration_verified,
        "integration_sha": (
            str(candidate["integration_sha"])
            if candidate is not None
            else None
        ),
        "integration_verifier_run_id": (
            str(integration_receipt["verifier_run_id"])
            if integration_receipt is not None
            else None
        ),
        "integration_receipt_id": (
            str(integration_receipt["receipt_id"])
            if integration_receipt is not None
            else None
        ),
        "completion_event_id": (
            completion.event_id if completion is not None else None
        ),
        "completion_sequence": (
            completion.sequence if completion is not None else None
        ),
        "completion_order": _COMPLETION_ORDER if completed else (),
        "independently_verified": completed,
        "blockers": blockers,
        "_requested": canonical_requested,
        "_objective_package_universe": objective_package_universe,
        "_package_receipts": package_receipts,
        "_candidate": candidate,
        "_integration_receipt": integration_receipt,
    }


def _canonical_integration_candidate(
    database: FoundryDatabase,
    requested: tuple[str, ...],
    *,
    minimum_sequence: int,
) -> tuple[dict[str, Any] | None, str]:
    events = [
        event
        for event in database.events(
            event_types=("workforce.integration_candidate_created",),
            readonly=True,
        )
        if _event_identifies_objective(
            event,
            idempotency_key=_INTEGRATION_CANDIDATE_KEY,
        )
    ]
    if not events:
        return None, "integration:canonical-candidate-missing"
    if len(events) != 1:
        return None, "integration:multiple-canonical-candidates"
    event = events[0]
    payload = event.payload
    if not (
        event.source_type == "supervisor"
        and event.source_id == "v13-long-run-proof"
        and event.correlation_id == _OBJECTIVE_ID
        and event.idempotency_key == _INTEGRATION_CANDIDATE_KEY
        and str(payload.get("objective_id") or "") == _OBJECTIVE_ID
        and payload.get("schema") == _INTEGRATION_CANDIDATE_SCHEMA
    ):
        return None, "integration:canonical-candidate-invalid"
    try:
        return _validate_integration_candidate(
            database,
            event,
            requested=requested,
            minimum_sequence=minimum_sequence,
        ), ""
    except _ObjectiveEvidenceError as error:
        return None, error.blocker
    except (OSError, TypeError, ValueError, WorkforceLongRunProofError):
        return None, "integration:canonical-candidate-invalid"


def _event_identifies_objective(
    event: Event,
    *,
    idempotency_key: str,
) -> bool:
    return bool(
        str(event.payload.get("objective_id") or "") == _OBJECTIVE_ID
        or event.correlation_id == _OBJECTIVE_ID
        or event.idempotency_key == idempotency_key
    )


def _convergence_package_deltas(
    payload: Mapping[str, Any],
    repository: Path,
    *,
    target_sha: str,
) -> dict[str, dict[str, Any]]:
    raw_records = payload.get("package_deltas")
    if not (
        payload.get("package_deltas_schema")
        == CONVERGENCE_PACKAGE_DELTAS_SCHEMA
        and isinstance(raw_records, list)
    ):
        raise _ObjectiveEvidenceError(
            "integration:convergence-evidence-invalid",
            "convergence event lacks typed package delta evidence",
        )
    if not all(isinstance(raw, Mapping) for raw in raw_records):
        raise _ObjectiveEvidenceError(
            "integration:convergence-evidence-invalid",
            "convergence package delta is not an object",
        )
    try:
        candidates = tuple(
            PackageConvergenceEvidence(
                package_id=str(raw.get("package_id") or ""),
                base_sha=str(raw.get("base_sha") or ""),
                result_sha=str(raw.get("result_sha") or ""),
                status=str(raw.get("status") or ""),
                evidence=("durable-convergence-record",),
            )
            for raw in raw_records
        )
        validated = validate_convergence_package_deltas(
            repository,
            raw_records,
            target_sha=target_sha,
            packages=candidates,
        )
    except ConvergenceAnalysisError as error:
        raise _ObjectiveEvidenceError(
            "integration:convergence-evidence-invalid",
            str(error),
        ) from error
    return {
        str(record["package_id"]): record
        for record in validated
    }


def _validated_candidate_delta(
    repository: Path,
    raw: Mapping[str, Any],
    *,
    package_id: str,
    base_sha: str,
    classification: str,
    convergence_delta: Mapping[str, Any],
    included: bool,
) -> dict[str, Any]:
    fields = {
        "package_id",
        "base_sha",
        "result_sha",
        "changed_paths",
    }
    if not included:
        fields.add("classification")
    if set(raw) != fields:
        raise _ObjectiveEvidenceError(
            f"integration:candidate-delta-invalid:{package_id}",
            f"candidate delta has an invalid shape: {package_id}",
        )
    delta_base = str(raw.get("base_sha") or "").lower()
    result_sha = str(raw.get("result_sha") or "").lower()
    raw_paths = raw.get("changed_paths")
    if not isinstance(raw_paths, list):
        raise _ObjectiveEvidenceError(
            f"integration:candidate-delta-invalid:{package_id}",
            f"candidate changed paths are not a list: {package_id}",
        )
    delta_paths = tuple(str(relative) for relative in raw_paths)
    if not (
        str(raw.get("package_id") or "") == package_id
        and delta_base == base_sha
        and _is_full_sha(result_sha)
        and delta_paths == tuple(sorted(set(delta_paths)))
    ):
        raise _ObjectiveEvidenceError(
            f"integration:candidate-delta-invalid:{package_id}",
            f"candidate delta identity is invalid: {package_id}",
        )
    try:
        delta_changes = _candidate_changes(
            repository,
            base_sha=delta_base,
            result_sha=result_sha,
        )
    except WorkforceLongRunProofError as error:
        raise _ObjectiveEvidenceError(
            f"integration:candidate-delta-invalid:{package_id}",
            str(error),
        ) from error
    actual_paths = tuple(relative for _status, relative in delta_changes)
    exact = bool(
        delta_paths == actual_paths
        and all(status in {"A", "M"} for status, _path in delta_changes)
    )
    if included:
        exact = bool(
            exact
            and delta_paths
            and _git(repository, "rev-parse", f"{result_sha}^")
            == delta_base
            and _git(
                repository,
                "rev-list",
                "--count",
                f"{delta_base}..{result_sha}",
            )
            == "1"
        )
    else:
        exact = bool(
            exact
            and str(raw.get("classification") or "") == classification
        )
    if not exact:
        raise _ObjectiveEvidenceError(
            f"integration:candidate-delta-invalid:{package_id}",
            f"candidate delta does not match Git: {package_id}",
        )
    if not (
        str(convergence_delta.get("package_id") or "") == package_id
        and str(convergence_delta.get("classification") or "")
        == classification
        and str(convergence_delta.get("base_sha") or "").lower()
        == delta_base
        and str(convergence_delta.get("result_sha") or "").lower()
        == result_sha
        and tuple(convergence_delta.get("changed_paths") or ())
        == delta_paths
    ):
        raise _ObjectiveEvidenceError(
            f"integration:convergence-delta-mismatch:{package_id}",
            (
                "trusted convergence package/base/result evidence does not "
                f"match the candidate delta for {package_id}"
            ),
        )
    return {
        "package_id": package_id,
        "classification": classification,
        "base_sha": delta_base,
        "result_sha": result_sha,
        "changed_paths": delta_paths,
    }


def _validate_integration_effect(
    database: FoundryDatabase,
    payload: Mapping[str, Any],
    *,
    minimum_sequence: int,
    candidate_sequence: int,
) -> None:
    action_key = str(
        payload.get("integration_effect_action_key") or ""
    )
    effect_payload_hash = str(
        payload.get("integration_effect_payload_hash") or ""
    )
    intent = payload.get("integration_effect_intent")
    if not (
        action_key == f"effect:{effect_payload_hash}"
        and len(effect_payload_hash) == 64
        and all(
            character in "0123456789abcdef"
            for character in effect_payload_hash
        )
        and isinstance(intent, Mapping)
        and payload_hash(dict(intent)) == effect_payload_hash
    ):
        raise _ObjectiveEvidenceError(
            "integration:effect-authority-invalid",
            "integration candidate lacks an exact effect identity",
        )
    target_ref = (
        f"{payload['repository_path']}#refs/heads/{payload['target']}"
    )
    events = [
        event
        for event in database.events(
            event_types=(
                "system.action_started",
                "system.action_completed",
                "system.action_failed",
                "effect.frozen",
                "effect.compensated",
                "effect.reconcile_mismatch",
                "effect.terminal_truth",
            ),
            readonly=True,
        )
        if str(event.payload.get("action_key") or "") == action_key
    ]
    started = [
        event for event in events
        if event.event_type == "system.action_started"
    ]
    if len(started) != 1:
        raise _ObjectiveEvidenceError(
            "integration:effect-authority-invalid",
            "integration effect must have one canonical start",
        )
    start = started[0]
    start_record = _event_record(database, start)
    if not (
        start.source_type == "supervisor"
        and start.source_id == "v5-effect-driver"
        and start.idempotency_key == f"{action_key}.started"
        and str(start.payload.get("system_id") or "")
        == f"workforce-objective:{_OBJECTIVE_ID}"
        and str(start.payload.get("target_ref") or "") == target_ref
        and str(start.payload.get("payload_hash") or "")
        == effect_payload_hash
        and str(start.payload.get("kind") or "")
        == _INTEGRATION_EFFECT_KIND
        and str(start.payload.get("connector_id") or "")
        == _INTEGRATION_EFFECT_CONNECTOR
        and str(start.payload.get("action_id") or "")
        == f"integration-candidate:{_OBJECTIVE_ID}"
        and canonical_json(start.payload.get("request_identity") or {})
        == canonical_json(dict(intent))
    ):
        raise _ObjectiveEvidenceError(
            "integration:effect-authority-invalid",
            "integration effect start does not match its exact intent",
        )

    failed = [
        event for event in events
        if event.event_type == "system.action_failed"
    ]
    mismatched = [
        event for event in events
        if event.event_type == "effect.reconcile_mismatch"
    ]
    compensated = [
        event for event in events
        if event.event_type == "effect.compensated"
    ]
    if failed or mismatched or compensated:
        raise _ObjectiveEvidenceError(
            "integration:effect-authority-invalid",
            "integration effect contains contradictory terminal evidence",
        )

    completed: list[_ProofLedgerEvent] = []
    frozen: list[_ProofLedgerEvent] = []
    terminal_truth: list[_ProofLedgerEvent] = []
    invalid_terminal = False
    for event in events:
        if event.event_type == "effect.frozen":
            valid = bool(
                event.source_type == "supervisor"
                and event.source_id == "v5-effect-driver"
                and event.idempotency_key is not None
                and event.idempotency_key.startswith(
                    f"{action_key}.frozen."
                )
            )
            invalid_terminal = invalid_terminal or not valid
            if valid:
                frozen.append(_event_record(database, event))
            continue
        if event.event_type not in {
            "system.action_completed",
            "effect.terminal_truth",
        }:
            continue
        result = event.payload.get("result")
        if not isinstance(result, Mapping):
            invalid_terminal = True
            continue
        canonical_event = bool(
            event.source_type == "supervisor"
            and event.source_id == "v5-effect-driver"
        )
        if event.event_type == "system.action_completed":
            canonical_event = bool(
                canonical_event
                and event.idempotency_key == f"{action_key}.completed"
                and str(event.payload.get("system_id") or "")
                == f"workforce-objective:{_OBJECTIVE_ID}"
                and str(event.payload.get("target_ref") or "")
                == target_ref
                and str(event.payload.get("payload_hash") or "")
                == effect_payload_hash
                and str(event.payload.get("kind") or "")
                == _INTEGRATION_EFFECT_KIND
                and str(event.payload.get("connector_id") or "")
                == _INTEGRATION_EFFECT_CONNECTOR
            )
        else:
            canonical_event = bool(
                canonical_event
                and event.idempotency_key.startswith(
                    f"{action_key}.terminal-truth."
                )
                and str(event.payload.get("provider_id") or "")
                == "local-git"
                and str(event.payload.get("provider_ref") or "")
                == f"refs/heads/{payload['target']}"
            )
        canonical_result = bool(
            canonical_event
            and str(result.get("objective_id") or "") == _OBJECTIVE_ID
            and str(result.get("repository_path") or "")
            == str(payload["repository_path"])
            and str(result.get("target") or "") == str(payload["target"])
            and str(result.get("base_sha") or "")
            == str(payload["base_sha"])
            and str(result.get("integration_sha") or "")
            == str(payload["integration_sha"])
            and str(result.get("integration_tree") or "")
            == str(payload["integration_tree"])
        )
        if event.event_type == "effect.terminal_truth":
            canonical_result = bool(
                canonical_result
                and event.payload.get("accepted") is True
                and event.payload.get("reconciled") is True
                and str(event.payload.get("outcome") or "") == COMPLETED
            )
        if canonical_result:
            record = _event_record(database, event)
            if event.event_type == "system.action_completed":
                completed.append(record)
            else:
                terminal_truth.append(record)
        else:
            invalid_terminal = True

    exact_terminal_shape = bool(
        not invalid_terminal
        and len(completed) <= 1
        and len(frozen) <= 1
        and len(terminal_truth) <= 1
        and (
            (
                len(completed) == 1
                and not frozen
                and (
                    not terminal_truth
                    or completed[0].sequence
                    < terminal_truth[0].sequence
                )
            )
            or (
                not completed
                and len(frozen) == 1
                and len(terminal_truth) == 1
                and frozen[0].sequence < terminal_truth[0].sequence
            )
        )
    )
    terminal_records = (
        [*completed, *frozen, *terminal_truth]
        if exact_terminal_shape
        else []
    )
    if not (
        terminal_records
        and minimum_sequence
        < start_record.sequence
        < min(item.sequence for item in terminal_records)
        and max(item.sequence for item in terminal_records)
        < candidate_sequence
    ):
        raise _ObjectiveEvidenceError(
            "integration:effect-authority-invalid",
            "integration candidate lacks one consistent terminal effect truth",
        )


def _validate_integration_candidate(
    database: FoundryDatabase,
    event: Event,
    *,
    requested: tuple[str, ...],
    minimum_sequence: int,
) -> dict[str, Any]:
    payload = dict(event.payload)
    record = _event_record(database, event)
    convergence_event_id = str(payload.get("convergence_event_id") or "")
    _require(
        bool(convergence_event_id)
        and event.causation_id == convergence_event_id,
        "integration candidate is not caused by its convergence decision",
    )
    convergence_event = next(
        (
            item
            for item in database.events(
                event_types=("workforce.convergence_recorded",),
                readonly=True,
            )
            if item.event_id == convergence_event_id
        ),
        None,
    )
    if not (
        convergence_event is not None
        and convergence_event.source_type == "supervisor"
        and convergence_event.source_id == _CONVERGENCE_SOURCE_ID
        and convergence_event.correlation_id == _CONVERGENCE_DECISION_ID
    ):
        raise _ObjectiveEvidenceError(
            "integration:convergence-source-untrusted",
            "integration candidate lacks trusted convergence evidence",
        )
    convergence = _event_record(database, convergence_event)
    repository_text = str(payload.get("repository_path") or "").strip()
    repository = Path(repository_text).expanduser().resolve()
    target = str(payload.get("target") or "")
    base_sha = str(payload.get("base_sha") or "").lower()
    integration_sha = str(payload.get("integration_sha") or "").lower()
    integration_tree = str(payload.get("integration_tree") or "").lower()
    _require(
        repository_text
        and repository.is_dir()
        and target == _INTEGRATION_TARGET
        and payload.get("verification_package_id") == _INTEGRATION_PACKAGE
        and _is_full_sha(base_sha)
        and _is_full_sha(integration_sha)
        and _is_full_sha(integration_tree)
        and base_sha != integration_sha
        and int(payload.get("commit_count") or 0) == 1
        and payload.get("target_repository_clean") is True,
        "integration candidate identity or repository evidence is incomplete",
    )
    raw_classifications = convergence.payload.get("classifications")
    if not isinstance(raw_classifications, Mapping):
        raise _ObjectiveEvidenceError(
            "integration:convergence-evidence-invalid",
            "convergence classifications are not a mapping",
        )
    classifications = {
        str(package_id): str(classification)
        for package_id, classification in raw_classifications.items()
    }
    if not (
        classifications
        and str(convergence.payload.get("decision_id") or "")
        == _CONVERGENCE_DECISION_ID
        and str(convergence.payload.get("integration_target") or "")
        == _INTEGRATION_TARGET
        and minimum_sequence < convergence.sequence < record.sequence
    ):
        raise _ObjectiveEvidenceError(
            "integration:convergence-evidence-invalid",
            "integration candidate is not ordered after trusted convergence",
        )
    convergence_deltas = _convergence_package_deltas(
        convergence.payload,
        repository,
        target_sha=base_sha,
    )
    if (
        set(convergence_deltas) != set(classifications)
        or any(
            convergence_deltas[package_id]["classification"]
            != classification
            for package_id, classification in classifications.items()
        )
    ):
        raise _ObjectiveEvidenceError(
            "integration:convergence-evidence-invalid",
            "typed package deltas do not match convergence classifications",
        )
    if (
        set(classifications) != set(_EXPECTED_CONVERGENCE_MATRIX)
        or any(
            (
                str(convergence_deltas[package_id]["status"]),
                str(classifications[package_id]),
            )
            != expected
            for package_id, expected in (
                _EXPECTED_CONVERGENCE_MATRIX.items()
            )
        )
    ):
        raise _ObjectiveEvidenceError(
            "integration:convergence-evidence-invalid",
            "convergence evidence omits or changes the required proof matrix",
        )
    convergence_candidates = tuple(
        PackageConvergenceEvidence(
            package_id=package_id,
            base_sha=str(delta["base_sha"]),
            result_sha=str(delta["result_sha"]),
            status=str(delta["status"]),
            evidence=(f"convergence:{convergence.event_id}",),
        )
        for package_id, delta in sorted(convergence_deltas.items())
    )
    expected_effect_intent = _integration_effect_intent(
        repository,
        target=target,
        base_sha=base_sha,
        convergence_event_id=convergence_event_id,
        candidates=convergence_candidates,
        classifications=classifications,
    )
    if canonical_json(payload.get("integration_effect_intent") or {}) != (
        canonical_json(expected_effect_intent)
    ):
        raise _ObjectiveEvidenceError(
            "integration:effect-authority-invalid",
            "integration effect intent is not bound to convergence truth",
        )

    state = rebuild_workforce_state(database)
    objective_candidates: list[PackageConvergenceEvidence] = []
    for package_id in requested:
        package_state = state.packages.get(package_id)
        receipt = _canonical_acceptance_for_package(database, package_id)
        if not (
            package_state is not None
            and package_state.status == "ACCEPTED"
            and package_state.package.parent_objective_id == _OBJECTIVE_ID
            and receipt is not None
            and receipt["decision"] == "ACCEPT"
            and receipt["binding_head"]
        ):
            raise _ObjectiveEvidenceError(
                "integration:accepted-objective-universe-mismatch",
                "integration is not bound to every accepted objective package",
            )
        objective_candidates.append(PackageConvergenceEvidence(
            package_id=package_id,
            base_sha=package_state.package.source_sha,
            result_sha=str(receipt["binding_head"]),
            status="ACCEPTED",
            evidence=(f"acceptance:{receipt['receipt_id']}",),
        ))
    try:
        reconstructed_objective = {
            str(item["package_id"]): item
            for item in reconstruct_convergence_package_deltas(
                repository,
                target_sha=base_sha,
                packages=tuple(objective_candidates),
            )
        }
    except ConvergenceAnalysisError as error:
        raise _ObjectiveEvidenceError(
            "integration:accepted-objective-universe-mismatch",
            str(error),
        ) from error
    if set(reconstructed_objective) != set(requested):
        raise _ObjectiveEvidenceError(
            "integration:accepted-objective-universe-mismatch",
            "semantic reconstruction omitted an accepted objective package",
        )
    for package_id, expected in reconstructed_objective.items():
        durable = convergence_deltas.get(package_id)
        if not (
            durable is not None
            and expected["classification"] == "UNIQUE_ACCEPTED_DELTA"
            and durable["classification"] == expected["classification"]
            and durable["status"] == "ACCEPTED"
            and durable["base_sha"] == expected["base_sha"]
            and durable["result_sha"] == expected["result_sha"]
            and durable["target_sha"] == expected["target_sha"]
        ):
            raise _ObjectiveEvidenceError(
                "integration:accepted-objective-universe-mismatch",
                (
                    "durable convergence does not match the accepted "
                    f"objective package: {package_id}"
                ),
            )

    unique_packages = tuple(sorted(
        package_id
        for package_id, classification in classifications.items()
        if classification == "UNIQUE_ACCEPTED_DELTA"
    ))
    if unique_packages != requested:
        raise _ObjectiveEvidenceError(
            "integration:unique-package-set-mismatch",
            "requested packages are not the complete unique accepted set",
        )
    _require(
        _git(repository, "rev-parse", "--verify", f"{base_sha}^{{commit}}")
        == base_sha
        and _git(
            repository, "rev-parse", "--verify",
            f"{integration_sha}^{{commit}}",
        ) == integration_sha
        and _git(
            repository, "rev-parse", "--verify", f"refs/heads/{target}",
        ) == integration_sha
        and _git(repository, "rev-parse", f"{integration_sha}^")
        == base_sha
        and _git(
            repository, "rev-list", "--count",
            f"{base_sha}..{integration_sha}",
        ) == "1"
        and _git(
            repository, "rev-parse", f"{integration_sha}^{{tree}}",
        ) == integration_tree
        and _git(
            repository, "show", "-s", "--format=%s", integration_sha,
        ) == "proof integration: unique accepted deltas"
        and _git(repository, "status", "--porcelain=v1") == "",
        "integration candidate does not name one clean exact-base commit",
    )
    actual_changes = _candidate_changes(
        repository,
        base_sha=base_sha,
        result_sha=integration_sha,
    )
    actual_paths = tuple(relative for _status, relative in actual_changes)
    declared_paths = tuple(payload.get("changed_paths") or ())
    _require(
        actual_changes
        and all(status in {"A", "M"} for status, _path in actual_changes)
        and declared_paths == tuple(sorted(set(declared_paths)))
        and declared_paths == actual_paths,
        "integration candidate changed paths do not match its exact commit",
    )

    included_deltas = payload.get("included_deltas")
    _require(
        isinstance(included_deltas, list),
        "integration candidate lacks included delta evidence",
    )
    included_by_package: dict[str, Mapping[str, Any]] = {}
    for raw in included_deltas:
        _require(
            isinstance(raw, Mapping),
            "integration included delta is malformed",
        )
        package_id = str(raw.get("package_id") or "")
        _require(
            package_id
            and package_id not in included_by_package,
            "integration included delta identity is duplicated",
        )
        included_by_package[package_id] = raw
    if not (
        set(included_by_package) == set(requested)
        and tuple(payload.get("included_packages") or ()) == requested
    ):
        raise _ObjectiveEvidenceError(
            "integration:unique-package-set-mismatch",
            "integration included deltas do not match the complete unique set",
        )
    raw_convergence_accepted = convergence.payload.get("accepted_packages")
    if not isinstance(raw_convergence_accepted, list):
        raise _ObjectiveEvidenceError(
            "integration:convergence-evidence-invalid",
            "convergence accepted packages are not a list",
        )
    convergence_accepted = tuple(raw_convergence_accepted)
    accepted_by_status = tuple(sorted(
        package_id
        for package_id, delta in convergence_deltas.items()
        if delta["status"] == "ACCEPTED"
    ))
    if (
        convergence_accepted != accepted_by_status
        or len(convergence_accepted) != len(set(convergence_accepted))
    ):
        raise _ObjectiveEvidenceError(
            "integration:convergence-evidence-invalid",
            "accepted package identities do not match typed delta statuses",
        )
    included_candidates: list[PackageConvergenceEvidence] = []
    owned_paths: set[str] = set()
    for package_id in requested:
        convergence_delta = convergence_deltas[package_id]
        delta = _validated_candidate_delta(
            repository,
            included_by_package[package_id],
            package_id=package_id,
            base_sha=base_sha,
            classification="UNIQUE_ACCEPTED_DELTA",
            convergence_delta=convergence_delta,
            included=True,
        )
        delta_paths = tuple(delta["changed_paths"])
        if not (
            convergence_accepted.count(package_id) == 1
            and convergence_delta.get("status") == "ACCEPTED"
        ):
            raise _ObjectiveEvidenceError(
                f"integration:convergence-delta-mismatch:{package_id}",
                (
                    "trusted convergence package/base/result evidence does "
                    f"not match the included delta for {package_id}"
                ),
            )
        included_candidates.append(PackageConvergenceEvidence(
            package_id=package_id,
            base_sha=str(delta["base_sha"]),
            result_sha=str(delta["result_sha"]),
            status="ACCEPTED",
            evidence=(f"integration-candidate:{record.event_id}",),
        ))
        owned_paths.update(delta_paths)
    try:
        union = validate_convergence_integration_union(
            repository,
            target_sha=base_sha,
            integration_sha=integration_sha,
            packages=tuple(included_candidates),
        )
    except ConvergenceAnalysisError as error:
        raise _ObjectiveEvidenceError(
            "integration:semantic-union-mismatch",
            str(error),
        ) from error
    _require(
        owned_paths == set(actual_paths)
        and tuple(union["changed_paths"]) == actual_paths
        and str(union["integration_tree"]) == integration_tree,
        "integration commit is not exactly the union of accepted deltas",
    )
    _validate_integration_effect(
        database,
        payload,
        minimum_sequence=convergence.sequence,
        candidate_sequence=record.sequence,
    )

    excluded_deltas = payload.get("excluded_deltas")
    excluded_packages = tuple(payload.get("excluded_packages") or ())
    expected_excluded = tuple(sorted(set(classifications) - set(requested)))
    if not (
        isinstance(excluded_deltas, list)
        and excluded_packages == expected_excluded
        and len(excluded_packages) == len(set(excluded_packages))
    ):
        raise _ObjectiveEvidenceError(
            "integration:excluded-package-set-mismatch",
            "integration exclusions do not cover every non-unique package",
        )
    excluded_by_package: dict[str, Mapping[str, Any]] = {}
    for raw in excluded_deltas:
        _require(
            isinstance(raw, Mapping),
            "integration excluded delta is malformed",
        )
        package_id = str(raw.get("package_id") or "")
        _require(
            package_id
            and package_id not in excluded_by_package,
            "integration excluded delta identity is duplicated",
        )
        excluded_by_package[package_id] = raw
    if set(excluded_by_package) != set(expected_excluded):
        raise _ObjectiveEvidenceError(
            "integration:excluded-package-set-mismatch",
            "integration excluded deltas do not match excluded packages",
        )
    expected_conflicting = tuple(sorted(
        package_id
        for package_id, classification in classifications.items()
        if classification == "CONFLICTING"
    ))
    expected_rejected = tuple(sorted(
        package_id
        for package_id, classification in classifications.items()
        if classification == "FAILED_REJECTED"
    ))
    if not (
        tuple(payload.get("conflicting_packages") or ())
        == expected_conflicting
        and tuple(payload.get("rejected_packages") or ())
        == expected_rejected
    ):
        raise _ObjectiveEvidenceError(
            "integration:excluded-package-set-mismatch",
            "candidate exclusion summaries do not match classifications",
        )
    excluded_paths: set[str] = set()
    for package_id in expected_excluded:
        classification = classifications[package_id]
        convergence_delta = convergence_deltas[package_id]
        delta = _validated_candidate_delta(
            repository,
            excluded_by_package[package_id],
            package_id=package_id,
            base_sha=base_sha,
            classification=classification,
            convergence_delta=convergence_delta,
            included=False,
        )
        status_is_accepted = convergence_delta["status"] == "ACCEPTED"
        if status_is_accepted != (package_id in convergence_accepted):
            raise _ObjectiveEvidenceError(
                f"integration:convergence-delta-mismatch:{package_id}",
                f"convergence acceptance status is inconsistent: {package_id}",
            )
        excluded_paths.update(str(relative) for relative in delta["changed_paths"])
    return {
        **payload,
        "candidate_event_id": record.event_id,
        "candidate_sequence": record.sequence,
        "convergence_sequence": convergence.sequence,
        "repository_path": str(repository),
        "classified_packages": tuple(sorted(classifications)),
        "unique_packages": unique_packages,
        "excluded_paths": tuple(sorted(excluded_paths)),
    }


def _accepted_package_binding_blocker(
    requested: tuple[str, ...],
    package_receipts: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, Any],
    *,
    state,
) -> str:
    included = {
        str(delta.get("package_id") or ""): delta
        for delta in candidate.get("included_deltas") or ()
        if isinstance(delta, Mapping)
    }
    for package_id in requested:
        delta = included.get(package_id)
        receipt = package_receipts.get(package_id)
        package_state = state.packages.get(package_id)
        result_sha = (
            str(delta.get("result_sha") or "").lower()
            if delta is not None
            else ""
        )
        base_sha = (
            str(delta.get("base_sha") or "").lower()
            if delta is not None
            else ""
        )
        binding_head = (
            str(receipt.get("binding_head") or "").lower()
            if receipt is not None
            else ""
        )
        if (
            delta is None
            or package_state is None
            or package_state.package.parent_objective_id != _OBJECTIVE_ID
            or package_state.package.integration_target
            != str(candidate.get("target") or "")
            or package_state.package.source_sha.lower() != base_sha
        ):
            return f"integration:package-base-mismatch:{package_id}"
        if (
            receipt is None
            or not _is_full_sha(result_sha)
            or binding_head != result_sha
        ):
            return (
                "integration:package-receipt-head-mismatch:"
                f"{package_id}"
            )
    return ""


def _canonical_integration_verification(
    database: FoundryDatabase,
    state,
    candidate: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    package_state = state.packages.get(_INTEGRATION_PACKAGE)
    if package_state is None:
        return None, "integration:verification-package-missing"
    sessions = state.sessions_for(_INTEGRATION_PACKAGE)
    if not sessions:
        return None, "integration:verification-session-missing"
    latest = max(sessions, key=lambda item: item.attempt)
    receipt = _canonical_acceptance_for_package(
        database,
        _INTEGRATION_PACKAGE,
    )
    package = package_state.package
    exact_sha = str(candidate.get("integration_sha") or "")
    repository = Path(str(candidate.get("repository_path") or "")).resolve()
    expected_package, expected_contract = (
        _build_integration_verification_package(
            repository.parent,
            integration_sha=exact_sha,
            integrated_artifacts=tuple(
                str(relative)
                for relative in candidate.get("changed_paths") or ()
            ),
            excluded_deltas=tuple(
                delta
                for delta in candidate.get("excluded_deltas") or ()
                if isinstance(delta, Mapping)
            ),
        )
    )
    try:
        contract = latest_contract(
            database,
            package.mission_id or "",
            step_id=package.step_id,
            allow_invalid=False,
        )
    except (MissionStepContractError, TypeError, ValueError):
        contract = None
    contract_is_exact = bool(
        contract is not None
        and contract.contract_id == expected_contract.contract_id
        and contract.content_hash() == expected_contract.content_hash()
        and canonical_json(contract.canonical_payload())
        == canonical_json(expected_contract.canonical_payload())
        and package.mission_step_contract_id == contract.contract_id
        and package.mission_step_contract_hash == contract.content_hash()
    )
    session_contract_is_exact = bool(
        latest.result.get("package_hash") == expected_package.content_hash()
        and latest.result.get("mission_step_contract_id")
        == expected_contract.contract_id
        and latest.result.get("mission_step_contract_hash")
        == expected_contract.content_hash()
    )
    package_key = f"workforce.package_created:{_INTEGRATION_PACKAGE}"
    package_events = [
        event
        for event in database.events(
            event_types=("workforce.package_created",),
            readonly=True,
        )
        if (
            str(event.payload.get("package_id") or "")
            == _INTEGRATION_PACKAGE
            or event.correlation_id == _INTEGRATION_PACKAGE
            or event.idempotency_key == package_key
        )
    ]
    contract_key = (
        "mission.step_contract_recorded:"
        f"{expected_contract.contract_id}:"
        f"{expected_contract.content_hash()}"
    )
    contract_events = [
        event
        for event in database.events(
            event_types=("mission.step_contract_recorded",),
            readonly=True,
        )
        if (
            str(event.payload.get("contract_id") or "")
            == expected_contract.contract_id
            or event.idempotency_key == contract_key
        )
    ]
    package_event = package_events[0] if len(package_events) == 1 else None
    contract_event = contract_events[0] if len(contract_events) == 1 else None
    durable_contract_is_exact = bool(
        package_event is not None
        and package_event.source_type == "supervisor"
        and package_event.source_id == _CONVERGENCE_SOURCE_ID
        and package_event.correlation_id == _INTEGRATION_PACKAGE
        and package_event.idempotency_key == package_key
        and contract_event is not None
        and contract_event.source_type == "supervisor"
        and contract_event.source_id == SUPERVISOR_CONTROLLER
        and contract_event.idempotency_key == contract_key
        and canonical_json(contract_event.payload)
        == canonical_json(expected_contract.to_payload())
    )
    package_is_exact = bool(
        package.content_hash() == expected_package.content_hash()
        and package.acceptance_criteria
        == expected_contract.verification_criteria
        and package.required_tests == expected_package.required_tests
        and package.forbidden_paths == expected_package.forbidden_paths
        and package.source_sha == exact_sha
        and package.integration_target == _INTEGRATION_TARGET
    )
    if not (
        package_is_exact
        and contract_is_exact
        and session_contract_is_exact
        and durable_contract_is_exact
    ):
        return None, "integration:verification-contract-mismatch"
    package_record = _event_record(database, package_event)
    contract_record = _event_record(database, contract_event)
    canonical = bool(
        package_state.status == "ACCEPTED"
        and receipt is not None
        and receipt["decision"] == "ACCEPT"
        and receipt["source_type"] == "supervisor"
        and receipt["source_id"] == "mission-loop"
        and receipt["started_source_type"] == "supervisor"
        and receipt["started_source_id"] == "mission-loop"
        and receipt["builder_run_id"] == latest.run_id
        and receipt["verifier_run_id"]
        and receipt["verifier_run_id"] != latest.run_id
        and receipt["binding_head"] == exact_sha
        and receipt["binding_hash"]
        and receipt["context_manifest_hash"]
        and int(candidate["candidate_sequence"])
        < package_record.sequence
        < contract_record.sequence
        < int(receipt["started_sequence"])
        < int(receipt["receipt_sequence"])
    )
    if not canonical:
        return None, "integration:canonical-verification-incomplete"
    return {
        **receipt,
        "verification_package_hash": package.content_hash(),
        "verification_contract_id": contract.contract_id,
        "verification_contract_hash": contract.content_hash(),
        "verification_package_event_id": package_record.event_id,
        "verification_package_sequence": package_record.sequence,
        "verification_contract_event_id": contract_record.event_id,
        "verification_contract_sequence": contract_record.sequence,
        "verification_required_tests": list(package.required_tests),
        "verification_acceptance_criteria": list(
            package.acceptance_criteria
        ),
        "verification_forbidden_paths": list(package.forbidden_paths),
    }, ""


def _objective_completion_payload(
    *,
    requested: tuple[str, ...],
    package_receipts: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, Any],
    integration_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    included_by_package = {
        str(delta.get("package_id") or ""): delta
        for delta in candidate.get("included_deltas") or ()
        if isinstance(delta, Mapping)
    }
    return {
        "schema": _OBJECTIVE_COMPLETION_SCHEMA,
        "objective_id": _OBJECTIVE_ID,
        "package_ids": list(requested),
        "package_run_ids": {
            package_id: str(package_receipts[package_id]["builder_run_id"])
            for package_id in requested
        },
        "package_verifier_run_ids": {
            package_id: str(package_receipts[package_id]["verifier_run_id"])
            for package_id in requested
        },
        "package_receipt_ids": {
            package_id: str(package_receipts[package_id]["receipt_id"])
            for package_id in requested
        },
        "package_binding_heads": {
            package_id: str(
                package_receipts[package_id]["binding_head"]
            )
            for package_id in requested
        },
        "package_base_shas": {
            package_id: str(
                included_by_package[package_id]["base_sha"]
            )
            for package_id in requested
        },
        "package_result_shas": {
            package_id: str(
                included_by_package[package_id]["result_sha"]
            )
            for package_id in requested
        },
        "convergence_event_id": str(
            candidate["convergence_event_id"]
        ),
        "integration_candidate_event_id": str(
            candidate["candidate_event_id"]
        ),
        "integration_sha": str(candidate["integration_sha"]),
        "integration_tree": str(candidate["integration_tree"]),
        "integration_target": str(candidate["target"]),
        "integration_package_id": _INTEGRATION_PACKAGE,
        "integration_builder_run_id": str(
            integration_receipt["builder_run_id"]
        ),
        "integration_verifier_run_id": str(
            integration_receipt["verifier_run_id"]
        ),
        "integration_receipt_id": str(
            integration_receipt["receipt_id"]
        ),
        "integration_package_hash": str(
            integration_receipt["verification_package_hash"]
        ),
        "integration_package_event_id": str(
            integration_receipt["verification_package_event_id"]
        ),
        "integration_package_sequence": int(
            integration_receipt["verification_package_sequence"]
        ),
        "integration_contract_id": str(
            integration_receipt["verification_contract_id"]
        ),
        "integration_contract_hash": str(
            integration_receipt["verification_contract_hash"]
        ),
        "integration_contract_event_id": str(
            integration_receipt["verification_contract_event_id"]
        ),
        "integration_contract_sequence": int(
            integration_receipt["verification_contract_sequence"]
        ),
        "integration_required_tests": list(
            integration_receipt["verification_required_tests"]
        ),
        "integration_acceptance_criteria": list(
            integration_receipt["verification_acceptance_criteria"]
        ),
        "integration_forbidden_paths": list(
            integration_receipt["verification_forbidden_paths"]
        ),
        "completion_order": list(_COMPLETION_ORDER),
    }


def _read_objective_completion(
    database: FoundryDatabase,
    *,
    payload: Mapping[str, Any],
    integration_receipt: Mapping[str, Any],
) -> tuple[_ProofLedgerEvent | None, str]:
    existing = _objective_completion_events(database)
    if not existing:
        return None, "objective:completion-event-missing"
    if len(existing) != 1:
        return None, "objective:completion-event-count-invalid"
    event = existing[0]
    record = _event_record(database, event)
    if not (
        event.source_type == "supervisor"
        and event.source_id == "v13-long-run-proof"
        and event.correlation_id == _OBJECTIVE_ID
        and event.idempotency_key == _OBJECTIVE_COMPLETION_KEY
        and event.causation_id == str(integration_receipt["receipt_id"])
        and canonical_json(event.payload) == canonical_json(payload)
        and int(integration_receipt["receipt_sequence"])
        < record.sequence
    ):
        return None, "objective:completion-verifier-binding-invalid"
    return record, ""


def _record_objective_completion(
    database: FoundryDatabase,
    package_ids: Iterable[str],
    *,
    stop_path: str | Path | None,
    controller: str = SUPERVISOR_CONTROLLER,
) -> _ProofLedgerEvent:
    requested_ids = tuple(package_ids)
    state = rebuild_workforce_state(database)
    integration_state = state.packages.get(_INTEGRATION_PACKAGE)
    if integration_state is None:
        raise WorkforceLongRunProofError(
            "objective completion requires the integration package"
        )
    _gate(
        database,
        controller,
        stop_path=stop_path,
        package=integration_state.package,
    )
    with database.transaction() as connection:
        state = rebuild_workforce_state(database)
        integration_state = state.packages.get(_INTEGRATION_PACKAGE)
        if integration_state is None:
            raise WorkforceLongRunProofError(
                "objective completion requires the integration package"
            )
        _gate(
            database,
            controller,
            stop_path=stop_path,
            package=integration_state.package,
        )
        reconstruction = _objective_state(database, requested_ids)
        if not (
            reconstruction["packages_verified"]
            and reconstruction["integration_verified"]
            and reconstruction["blockers"]
            in ([], ["objective:completion-event-missing"])
        ):
            raise WorkforceLongRunProofError(
                "objective completion prerequisites are not canonical: "
                + ", ".join(reconstruction["blockers"])
            )
        requested = tuple(reconstruction["_requested"])
        package_receipts = reconstruction["_package_receipts"]
        candidate = reconstruction["_candidate"]
        integration_receipt = reconstruction["_integration_receipt"]
        _require(
            isinstance(package_receipts, Mapping)
            and isinstance(candidate, Mapping)
            and isinstance(integration_receipt, Mapping),
            "objective completion reconstruction is internally inconsistent",
        )
        payload = _objective_completion_payload(
            requested=requested,
            package_receipts=package_receipts,
            candidate=candidate,
            integration_receipt=integration_receipt,
        )
        existing, blocker = _read_objective_completion(
            database,
            payload=payload,
            integration_receipt=integration_receipt,
        )
        if existing is not None:
            return existing
        if blocker != "objective:completion-event-missing":
            raise WorkforceLongRunProofError(blocker)
        _gate(
            database,
            controller,
            stop_path=stop_path,
            package=integration_state.package,
        )
        database.append_events_on_connection(connection, [
            Event.create(
                _OBJECTIVE_COMPLETION_EVENT,
                payload,
                source_type="supervisor",
                source_id="v13-long-run-proof",
                correlation_id=_OBJECTIVE_ID,
                causation_id=str(integration_receipt["receipt_id"]),
                idempotency_key=_OBJECTIVE_COMPLETION_KEY,
                occurred_at=PROOF_NOW,
            ),
        ])
    recorded, blocker = _read_objective_completion(
        database,
        payload=payload,
        integration_receipt=integration_receipt,
    )
    if recorded is None:
        raise WorkforceLongRunProofError(blocker)
    return recorded


def _objective_completion_events(
    database: FoundryDatabase,
) -> list[Event]:
    return [
        event
        for event in database.events(
            event_types=(_OBJECTIVE_COMPLETION_EVENT,),
            readonly=True,
        )
        if _event_identifies_objective(
            event,
            idempotency_key=_OBJECTIVE_COMPLETION_KEY,
        )
    ]


def _is_full_sha(value: str) -> bool:
    return (
        len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _intervention_proof(
    database: FoundryDatabase,
    execution,
    replay,
) -> InterventionProof:
    intervention_id = execution.intervention.intervention_id
    state = rebuild_workforce_state(database)
    latest = max(
        state.sessions_for(execution.intervention.package_id),
        key=lambda item: item.attempt,
    )
    sealed_relaunch = (
        latest.attempt >= 2
        and latest.result.get("launch_kind") == "SEALED_PACKAGE_RUNNER"
    )
    request_events = _event_count_for_intervention(
        database,
        "workforce.intervention_recorded",
        intervention_id,
    )
    completion_events = _event_count_for_intervention(
        database,
        "workforce.intervention_completed",
        intervention_id,
    )
    _require(
        replay.replayed
        and request_events == 1
        and completion_events == 1,
        f"intervention replay duplicated durable effects: {intervention_id}",
    )
    _require(
        sealed_relaunch,
        f"intervention did not relaunch through the sealed gateway: {intervention_id}",
    )
    return InterventionProof(
        package_id=execution.intervention.package_id,
        action=execution.intervention.action,
        intervention_id=intervention_id,
        outcome=execution.intervention.outcome,
        replacement_session_id=execution.replacement_session_id,
        sealed_relaunch=sealed_relaunch,
        replayed_without_duplicate=True,
        request_events=request_events,
        completion_events=completion_events,
    )


def _event_count_for_intervention(
    database: FoundryDatabase,
    event_type: str,
    intervention_id: str,
) -> int:
    return sum(
        str(event.payload.get("intervention_id") or "")
        == intervention_id
        for event in database.events(
            event_types=(event_type,),
            readonly=True,
        )
    )


def _effect_evidence(database: FoundryDatabase) -> dict[str, Any]:
    terminal_types = (
        "effect.frozen",
        "effect.reconciled",
        "provider.effect_reconciled",
        "provider.effect_reconciliation_required",
        "system.action_completed",
        "system.action_failed",
        "system.action_started",
    )
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for event in database.events(
        event_types=terminal_types,
        readonly=True,
    ):
        action_key = str(
            event.payload.get("action_key")
            or event.payload.get("effect_action_key")
            or ""
        )
        if action_key:
            counts[action_key][event.event_type] += 1
    duplicates = sorted(
        f"{action_key}:{event_type}:{count}"
        for action_key, event_counts in counts.items()
        for event_type, count in event_counts.items()
        if count > 1
    )
    completed = sorted(
        action_key
        for action_key, event_counts in counts.items()
        if event_counts["system.action_completed"] == 1
    )
    return {
        "effect_action_count": len(counts),
        "completed_actions": completed,
        "duplicate_events": duplicates,
    }


def _event_record(
    database: FoundryDatabase,
    event: Event,
) -> _ProofLedgerEvent:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT sequence FROM events WHERE event_id = ? LIMIT 1",
            (event.event_id,),
        ).fetchone()
    _require(row is not None, f"ledger event disappeared: {event.event_id}")
    return _ProofLedgerEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        sequence=int(row[0]),
        source_type=event.source_type,
        source_id=event.source_id,
        payload=dict(event.payload),
    )


def _event_for(
    database: FoundryDatabase,
    event_type: str,
    payload_key: str,
    payload_value: str,
) -> _ProofLedgerEvent:
    for event in reversed(
        database.events(event_types=(event_type,), readonly=True)
    ):
        if str(event.payload.get(payload_key) or "") == str(payload_value):
            return _event_record(database, event)
    raise WorkforceLongRunProofError(
        f"missing {event_type} for {payload_key}={payload_value}"
    )


def _canonical_acceptance_for_package(
    database: FoundryDatabase,
    package_id: str,
) -> dict[str, Any] | None:
    state = rebuild_workforce_state(database)
    try:
        package_state = state.package(package_id)
    except WorkforceStateError:
        return None
    sessions = state.sessions_for(package_id)
    if not sessions:
        return None
    latest = max(sessions, key=lambda item: item.attempt)
    package = package_state.package
    with database.connect(readonly=True) as connection:
        try:
            canonical = _acceptance_receipt_on_connection(
                connection,
                state,
                package_state,
            )
        except WorkforceStateError:
            return None
    receipt_event = next(
        (
            event
            for event in database.events(
                event_types=("verification.accepted",),
                readonly=True,
            )
            if event.event_id == canonical["acceptance_event_id"]
        ),
        None,
    )
    if receipt_event is None:
        return None
    receipt = _event_record(database, receipt_event)
    verdict_id = str(canonical["verdict_id"])
    try:
        started = _event_for(
            database,
            "verification.started",
            "verdict_id",
            verdict_id,
        )
    except WorkforceLongRunProofError:
        return None
    binding = dict(started.payload.get("binding") or {})
    if not (
        receipt.source_type == "supervisor"
        and receipt.source_id == "mission-loop"
        and receipt.payload.get("decision") == "ACCEPT"
        and str(canonical["builder_run_id"]) == latest.run_id
        and str(canonical["verifier_run_id"]) != latest.run_id
        and started.source_type == "supervisor"
        and started.source_id == "mission-loop"
        and started.sequence < receipt.sequence
    ):
        return None
    return {
        "decision": "ACCEPT",
        "verdict_id": verdict_id,
        "builder_run_id": str(canonical["builder_run_id"]),
        "verifier_run_id": str(canonical["verifier_run_id"]),
        "receipt_id": receipt.event_id,
        "receipt_sequence": receipt.sequence,
        "source_type": receipt.source_type,
        "source_id": receipt.source_id,
        "started_event_id": started.event_id,
        "started_sequence": started.sequence,
        "started_source_type": started.source_type,
        "started_source_id": started.source_id,
        "binding_head": str(binding.get("head") or ""),
        "binding_hash": str(binding.get("binding_hash") or ""),
        "context_manifest_hash": str(
            binding.get("context_manifest_hash") or ""
        ),
        "target": str(binding.get("target") or ""),
        "checks": dict(receipt.payload.get("checks") or {}),
    }


def _candidate_changes(
    repository: Path,
    *,
    base_sha: str,
    result_sha: str,
) -> tuple[tuple[str, str], ...]:
    raw = _run_git_binary(
        repository,
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        base_sha,
        result_sha,
    ).stdout
    parts = raw.split(b"\0")
    _require(
        parts[-1:] == [b""] and (len(parts) - 1) % 2 == 0,
        f"malformed Git name-status output for {result_sha}",
    )
    return tuple(
        (
            parts[index].decode("utf-8", errors="surrogateescape"),
            parts[index + 1].decode(
                "utf-8",
                errors="surrogateescape",
            ),
        )
        for index in range(0, len(parts) - 1, 2)
    )


def _integration_effect_intent(
    repository: Path,
    *,
    target: str,
    base_sha: str,
    convergence_event_id: str,
    candidates: Iterable[PackageConvergenceEvidence],
    classifications: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "schema": "jarvis.workforce.integration-effect.v1",
        "objective_id": _OBJECTIVE_ID,
        "repository_path": str(repository.resolve()),
        "target": target,
        "base_sha": base_sha,
        "convergence_event_id": convergence_event_id,
        "packages": [
            {
                "package_id": candidate.package_id,
                "status": candidate.status,
                "base_sha": candidate.base_sha,
                "result_sha": candidate.result_sha,
            }
            for candidate in sorted(
                candidates,
                key=lambda item: item.package_id,
            )
        ],
        "classifications": {
            package_id: classifications[package_id]
            for package_id in sorted(classifications)
        },
    }


def _integration_commit_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "GIT_AUTHOR_NAME": "JARVIS V13 Integration",
        "GIT_AUTHOR_EMAIL": "v13-integration@example.invalid",
        "GIT_AUTHOR_DATE": _INTEGRATION_COMMIT_DATE,
        "GIT_COMMITTER_NAME": "JARVIS V13 Integration",
        "GIT_COMMITTER_EMAIL": "v13-integration@example.invalid",
        "GIT_COMMITTER_DATE": _INTEGRATION_COMMIT_DATE,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "LANG": "C",
        "LC_ALL": "C",
    })
    return environment


def _git_ref(repository: Path, target: str) -> str | None:
    result = _run_git_binary(
        repository,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/heads/{target}^{{commit}}",
        allowed_returncodes=(0, 1),
    )
    if result.returncode == 1:
        return None
    value = result.stdout.decode(
        "utf-8",
        errors="replace",
    ).strip().lower()
    _require(_is_full_sha(value), f"invalid Git target ref: {target}")
    return value


def _build_integration_payload(
    repository: Path,
    *,
    target: str,
    base_sha: str,
    evidence: Mapping[str, PackageConvergenceEvidence],
    classifications: Mapping[str, str],
    included: tuple[str, ...],
    conflicting: tuple[str, ...],
    rejected: tuple[str, ...],
    excluded: tuple[str, ...],
    update_ref: bool,
    gate,
) -> dict[str, Any]:
    environment = _integration_commit_environment()
    current_sha = base_sha
    final_tree = ""
    included_deltas: list[dict[str, Any]] = []
    for index, package_id in enumerate(included, start=1):
        candidate = evidence[package_id]
        changes = _candidate_changes(
            repository,
            base_sha=candidate.base_sha,
            result_sha=candidate.result_sha,
        )
        _require(changes, f"accepted delta is empty: {package_id}")
        _require(
            candidate.base_sha == base_sha
            and all(
                status in {"A", "M"}
                for status, _relative in changes
            ),
            (
                "bounded integration requires added or modified changes "
                f"on the exact base: {package_id}"
            ),
        )
        gate()
        merged = _git(
            repository,
            "merge-tree",
            "--write-tree",
            f"--merge-base={base_sha}",
            current_sha,
            candidate.result_sha,
            environment=environment,
        ).splitlines()
        _require(
            len(merged) == 1 and _is_full_sha(merged[0].lower()),
            f"semantic tree merge failed for {package_id}",
        )
        final_tree = merged[0].lower()
        gate()
        if index < len(included):
            current_sha = _git(
                repository,
                "commit-tree",
                final_tree,
                "-p",
                base_sha,
                "-m",
                f"proof integration stage {index}: {package_id}",
                environment=environment,
            ).lower()
            _require(
                _is_full_sha(current_sha),
                f"invalid integration stage commit for {package_id}",
            )
        included_deltas.append({
            "package_id": package_id,
            "base_sha": candidate.base_sha,
            "result_sha": candidate.result_sha,
            "changed_paths": [
                relative for _status, relative in changes
            ],
        })

    _require(_is_full_sha(final_tree), "integration tree was not produced")
    gate()
    integration_sha = _git(
        repository,
        "commit-tree",
        final_tree,
        "-p",
        base_sha,
        "-m",
        "proof integration: unique accepted deltas",
        environment=environment,
    ).lower()
    _require(
        _is_full_sha(integration_sha)
        and _git(repository, "rev-parse", f"{integration_sha}^")
        == base_sha,
        "integration commit is not deterministic on the exact base",
    )
    try:
        union = validate_convergence_integration_union(
            repository,
            target_sha=base_sha,
            integration_sha=integration_sha,
            packages=tuple(evidence[package_id] for package_id in included),
        )
    except ConvergenceAnalysisError as error:
        raise WorkforceLongRunProofError(str(error)) from error
    changed_paths = tuple(union["changed_paths"])
    actual_changes = _candidate_changes(
        repository,
        base_sha=base_sha,
        result_sha=integration_sha,
    )
    _require(
        actual_changes
        and all(status in {"A", "M"} for status, _path in actual_changes)
        and changed_paths
        == tuple(relative for _status, relative in actual_changes),
        "integration commit changed an unsupported or undeclared path",
    )

    excluded_deltas: list[dict[str, Any]] = []
    for package_id in excluded:
        candidate = evidence[package_id]
        changes = _candidate_changes(
            repository,
            base_sha=candidate.base_sha,
            result_sha=candidate.result_sha,
        )
        excluded_deltas.append({
            "package_id": package_id,
            "classification": classifications[package_id],
            "base_sha": candidate.base_sha,
            "result_sha": candidate.result_sha,
            "changed_paths": [
                relative for _status, relative in changes
            ],
        })

    if update_ref:
        existing = _git_ref(repository, target)
        if existing is None:
            gate()
            _git(
                repository,
                "update-ref",
                f"refs/heads/{target}",
                integration_sha,
                "0" * 40,
                environment=environment,
            )
        elif existing != integration_sha:
            raise WorkforceLongRunProofError(
                "integration target already names a different commit"
            )

    _require(
        _git_ref(repository, target) in (
            integration_sha,
            None if not update_ref else integration_sha,
        )
        and _git(repository, "status", "--porcelain=v1") == "",
        "integration repository changed outside the exact target ref",
    )
    return {
        "objective_id": _OBJECTIVE_ID,
        "repository_path": str(repository.resolve()),
        "verification_package_id": _INTEGRATION_PACKAGE,
        "target": target,
        "base_sha": base_sha,
        "integration_sha": integration_sha,
        "integration_tree": final_tree,
        "commit_count": 1,
        "included_packages": list(included),
        "included_deltas": included_deltas,
        "excluded_packages": list(excluded),
        "excluded_deltas": excluded_deltas,
        "conflicting_packages": list(conflicting),
        "rejected_packages": list(rejected),
        "changed_paths": list(changed_paths),
        "target_repository_clean": True,
    }


def _integration_payload_from_effect(
    result: Mapping[str, Any],
) -> dict[str, Any]:
    fields = (
        "objective_id",
        "repository_path",
        "verification_package_id",
        "target",
        "base_sha",
        "integration_sha",
        "integration_tree",
        "commit_count",
        "included_packages",
        "included_deltas",
        "excluded_packages",
        "excluded_deltas",
        "conflicting_packages",
        "rejected_packages",
        "changed_paths",
        "target_repository_clean",
    )
    payload = {
        field: result[field]
        for field in fields
        if field in result
    }
    _require(
        set(payload) == set(fields),
        "integration effect result is incomplete",
    )
    return payload


def _create_integration_candidate(
    database: FoundryDatabase,
    repository: Path,
    *,
    target: str,
    base_sha: str,
    candidates: Iterable[PackageConvergenceEvidence],
    classifications: Mapping[str, str],
    convergence_event_id: str,
    stop_path: str | Path | None,
    controller: str = SUPERVISOR_CONTROLLER,
) -> dict[str, Any]:
    repository = repository.expanduser().resolve()
    candidates = tuple(candidates)
    evidence = {
        candidate.package_id: candidate
        for candidate in candidates
    }
    _require(
        len(evidence) == len(candidates),
        "integration package identities must be unique",
    )
    try:
        reconstructed = reconstruct_convergence_package_deltas(
            repository,
            target_sha=base_sha,
            packages=candidates,
        )
    except ConvergenceAnalysisError as error:
        raise WorkforceLongRunProofError(str(error)) from error
    canonical_classifications = {
        str(record["package_id"]): str(record["classification"])
        for record in reconstructed
    }
    _require(
        dict(classifications) == canonical_classifications,
        "integration classifications do not match semantic reconstruction",
    )
    convergence_events = [
        event
        for event in database.events(
            event_types=("workforce.convergence_recorded",),
            readonly=True,
        )
        if event.event_id == convergence_event_id
    ]
    _require(
        len(convergence_events) == 1
        and convergence_events[0].source_type == "supervisor"
        and convergence_events[0].source_id == _CONVERGENCE_SOURCE_ID
        and convergence_events[0].correlation_id
        == _CONVERGENCE_DECISION_ID
        and str(
            convergence_events[0].payload.get("decision_id") or ""
        )
        == _CONVERGENCE_DECISION_ID
        and str(
            convergence_events[0].payload.get("integration_target") or ""
        )
        == target
        and dict(
            convergence_events[0].payload.get("classifications") or {}
        )
        == canonical_classifications,
        "integration requires one exact durable convergence decision",
    )
    included = tuple(sorted(
        package_id
        for package_id, classification in canonical_classifications.items()
        if classification == "UNIQUE_ACCEPTED_DELTA"
    ))
    conflicting = tuple(sorted(
        package_id
        for package_id, classification in canonical_classifications.items()
        if classification == "CONFLICTING"
    ))
    rejected = tuple(sorted(
        package_id
        for package_id, classification in canonical_classifications.items()
        if classification == "FAILED_REJECTED"
    ))
    excluded = tuple(sorted(set(evidence) - set(included)))
    _require(
        included == tuple(sorted((_REPAIR_PACKAGE, _REASSIGN_PACKAGE))),
        "integration inputs must be exactly the two unique accepted deltas",
    )
    _require(
        {
            package_id: (
                evidence[package_id].status,
                canonical_classifications[package_id],
            )
            for package_id in sorted(evidence)
        }
        == {
            package_id: expected
            for package_id, expected in sorted(
                _EXPECTED_CONVERGENCE_MATRIX.items()
            )
        },
        "weak or conflicting convergence candidates were not excluded",
    )
    state = rebuild_workforce_state(database)
    accepted_objective_packages = tuple(sorted(
        package_id
        for package_id, package_state in state.packages.items()
        if (
            package_id != _INTEGRATION_PACKAGE
            and package_state.status == "ACCEPTED"
            and package_state.package.parent_objective_id == _OBJECTIVE_ID
        )
    ))
    _require(
        accepted_objective_packages == included,
        "integration inputs do not match the accepted objective universe",
    )
    gate_packages: list[WorkPackage] = []
    for package_id in included:
        package_state = state.packages.get(package_id)
        receipt = _canonical_acceptance_for_package(
            database,
            package_id,
        )
        candidate = evidence[package_id]
        expected_targets = tuple(
            os.path.normcase(str(
                (
                    Path(package_state.package.worktree_path)
                    / Path(*relative.split("/"))
                ).resolve()
            ))
            for relative in (
                package_state.package.expected_artifacts
                if package_state is not None
                else ()
            )
        )
        _require(
            package_state is not None
            and package_state.status == "ACCEPTED"
            and package_state.package.parent_objective_id == _OBJECTIVE_ID
            and package_state.package.source_sha == base_sha
            and candidate.status == "ACCEPTED"
            and candidate.base_sha == base_sha
            and receipt is not None
            and receipt["decision"] == "ACCEPT"
            and str(receipt["binding_head"]).lower()
            == candidate.result_sha
            and len(expected_targets) == 1
            and os.path.normcase(str(
                Path(str(receipt["target"])).resolve()
            )) == expected_targets[0]
            and bool(receipt["binding_hash"])
            and bool(receipt["context_manifest_hash"])
            and _git(
                repository,
                "rev-parse",
                f"{candidate.result_sha}^",
            )
            == base_sha
            and _git(
                repository,
                "rev-list",
                "--count",
                f"{base_sha}..{candidate.result_sha}",
            )
            == "1",
            (
                "integration input lacks exact durable acceptance authority: "
                f"{package_id}"
            ),
        )
        gate_packages.append(package_state.package)

    def gate() -> None:
        for package in gate_packages:
            _gate(
                database,
                controller,
                stop_path=stop_path,
                package=package,
            )

    gate()
    intent = _integration_effect_intent(
        repository,
        target=target,
        base_sha=base_sha,
        convergence_event_id=convergence_event_id,
        candidates=candidates,
        classifications=canonical_classifications,
    )
    effect_payload_hash = payload_hash(intent)
    action_key = f"effect:{effect_payload_hash}"
    target_ref = f"{repository}#refs/heads/{target}"
    current_effect = effect_state(database, action_key)
    current_ref = _git_ref(repository, target)

    if current_effect.get("status") in {STARTED, FAILED, FROZEN}:
        if current_ref is not None:
            gate()
            expected = _build_integration_payload(
                repository,
                target=target,
                base_sha=base_sha,
                evidence=evidence,
                classifications=canonical_classifications,
                included=included,
                conflicting=conflicting,
                rejected=rejected,
                excluded=excluded,
                update_ref=False,
                gate=gate,
            )
            if current_ref == expected["integration_sha"]:
                if current_effect.get("status") == STARTED:
                    freeze_effect(
                        database,
                        action_key,
                        reason=(
                            "integration process ended after the exact Git "
                            "ref landed; reconciling from local readback"
                        ),
                    )
                reconciled = finalize_verified_effect(
                    database,
                    action_key,
                    provider_receipt={
                        "provider_id": "local-git",
                        "provider_ref": f"refs/heads/{target}",
                        "integration_sha": current_ref,
                    },
                    readback={
                        "status": "MATCH",
                        "matches": True,
                        "integration_sha": current_ref,
                        "integration_tree": expected["integration_tree"],
                    },
                    result={
                        "claimed": COMPLETED,
                        "performed": True,
                        **expected,
                    },
                    acceptance_evidence={
                        "objective_id": _OBJECTIVE_ID,
                        "convergence_event_id": convergence_event_id,
                        "effect_payload_hash": effect_payload_hash,
                    },
                    reconciled=True,
                )
                return {
                    **_integration_payload_from_effect(reconciled),
                    "integration_effect_action_key": action_key,
                    "integration_effect_payload_hash": effect_payload_hash,
                    "integration_effect_intent": intent,
                }
        if current_effect.get("status") in {FAILED, FROZEN}:
            raise WorkforceLongRunProofError(
                "integration effect is terminal without an exact Git result"
            )
    elif current_effect.get("status") == "UNKNOWN" and current_ref is not None:
        raise WorkforceLongRunProofError(
            "integration target exists without a canonical effect claim"
        )

    def stopped() -> bool:
        try:
            gate()
        except PermissionError:
            return True
        return False

    result = execute_fabric_effect(
        database,
        action_key=action_key,
        system_id=f"workforce-objective:{_OBJECTIVE_ID}",
        target_ref=target_ref,
        payload_hash=effect_payload_hash,
        kind=_INTEGRATION_EFFECT_KIND,
        connector_id=_INTEGRATION_EFFECT_CONNECTOR,
        before_state_capture={
            "base_sha": base_sha,
            "target_ref": current_ref,
        },
        expected_effects=(
            "create one deterministic integration commit on the exact base",
            "atomically point only the disposable integration target at it",
        ),
        action_id=f"integration-candidate:{_OBJECTIVE_ID}",
        request_identity=intent,
        perform=lambda: {
            "claimed": COMPLETED,
            "performed": True,
            **_build_integration_payload(
                repository,
                target=target,
                base_sha=base_sha,
                evidence=evidence,
                classifications=canonical_classifications,
                included=included,
                conflicting=conflicting,
                rejected=rejected,
                excluded=excluded,
                update_ref=True,
                gate=gate,
            ),
        },
        stop_check=stopped,
        claim_ttl_s=5.0,
    )
    if result.get("claimed") == "BLOCKED":
        raise PermissionError("workforce stopped before integration")
    _require(
        result.get("claimed") == COMPLETED
        and _git_ref(repository, target)
        == result.get("integration_sha"),
        "integration effect did not complete with exact Git readback",
    )
    return {
        **_integration_payload_from_effect(result),
        "integration_effect_action_key": action_key,
        "integration_effect_payload_hash": effect_payload_hash,
        "integration_effect_intent": intent,
    }


def _record_integration_candidate(
    database: FoundryDatabase,
    integration: Mapping[str, Any],
    *,
    convergence_event_id: str,
    stop_path: str | Path | None,
    controller: str = SUPERVISOR_CONTROLLER,
) -> _ProofLedgerEvent:
    state = rebuild_workforce_state(database)
    gate_package = next(
        (
            state.packages[package_id].package
            for package_id in integration.get("included_packages") or ()
            if str(package_id) in state.packages
        ),
        None,
    )
    _gate(
        database,
        controller,
        stop_path=stop_path,
        package=gate_package,
    )
    payload = {
        "schema": _INTEGRATION_CANDIDATE_SCHEMA,
        **dict(integration),
        "convergence_event_id": convergence_event_id,
    }
    event = Event.create(
        "workforce.integration_candidate_created",
        payload,
        source_type="supervisor",
        source_id="v13-long-run-proof",
        correlation_id=_OBJECTIVE_ID,
        causation_id=convergence_event_id,
        idempotency_key=_INTEGRATION_CANDIDATE_KEY,
        occurred_at=PROOF_NOW,
    )
    with database.transaction() as connection:
        _gate(
            database,
            controller,
            stop_path=stop_path,
            package=gate_package,
        )
        database.append_events_on_connection(connection, [event])
    recorded = [
        item
        for item in database.events(
            event_types=("workforce.integration_candidate_created",),
            readonly=True,
        )
        if _event_identifies_objective(
            item,
            idempotency_key=_INTEGRATION_CANDIDATE_KEY,
        )
    ]
    _require(
        len(recorded) == 1
        and recorded[0].idempotency_key == _INTEGRATION_CANDIDATE_KEY
        and canonical_json(recorded[0].payload) == canonical_json(payload),
        "integration candidate did not replay as one canonical event",
    )
    return _event_record(database, recorded[0])


def _convergence_evidence(
    package_id: str,
    base_sha: str,
    result_sha: str,
    *,
    status: str,
) -> PackageConvergenceEvidence:
    return PackageConvergenceEvidence(
        package_id=package_id,
        base_sha=base_sha,
        result_sha=result_sha,
        status=status,
        evidence=(f"v13-long-run:{package_id}:{status.lower()}",),
    )


def _commit_branch(
    repository: Path,
    *,
    branch: str,
    parent: str,
    changes: Mapping[str, str],
) -> str:
    _git(repository, "checkout", "-qB", branch, parent)
    for relative, content in changes.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-qm", branch)
    return _git(repository, "rev-parse", "HEAD")


def _wait_for_launches(
    launches,
    *,
    timeout: float,
) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    values: dict[str, int] = {}
    for launch in launches:
        process = launch.worker_status.process
        _require(
            process is not None,
            f"sealed runner has no process: {launch.binding.package_id}",
        )
        remaining = max(0.1, deadline - time.monotonic())
        values[launch.binding.package_id] = process.wait(timeout=remaining)
    return values


def _wait_for(
    predicate,
    *,
    timeout: float,
    description: str,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise WorkforceLongRunProofError(f"timed out waiting for {description}")


def _artifact(package: WorkPackage) -> Path:
    return Path(package.worktree_path) / package.expected_artifacts[0]


def _crash_marker(package: WorkPackage) -> Path:
    return Path(package.worktree_path) / ".jarvis-owned-worker.pid"


def _recover_dead_role(
    database: FoundryDatabase,
    package: WorkPackage,
    run_id: str,
) -> tuple[list[str], list[str]]:
    LeaseManager(database).release(
        role_lease_key("builder", str(package.mission_id), package.step_id),
        run_id,
    )
    reaped = [
        reaped_run_id
        for reaped_run_id in reap_stale_role_runs(database)
        if reaped_run_id == run_id
    ]
    mission_id = str(package.mission_id)
    recovered = [
        recovered_mission_id
        for recovered_mission_id in recover_stale_mission_state(database)
        if recovered_mission_id == mission_id
    ]
    return reaped, recovered


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    correlation_id: str,
) -> None:
    database.append(Event.create(
        event_type,
        dict(payload),
        source_type="supervisor",
        source_id="v13-long-run-proof",
        correlation_id=correlation_id,
        idempotency_key=key,
        occurred_at=PROOF_NOW,
    ))


def _git(
    root: Path,
    *args: str,
    environment: Mapping[str, str] | None = None,
) -> str:
    from ..platform.windows_subprocess import headless_run_kwargs

    command_environment = os.environ.copy()
    command_environment.update(dict(environment or {}))
    command_environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env=command_environment,
        **headless_run_kwargs(),
    )
    if result.returncode != 0:
        raise WorkforceLongRunProofError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"git {' '.join(args)} failed"
        )
    return result.stdout.strip()


def _run_git_binary(
    root: Path,
    *args: str,
    allowed_returncodes: tuple[int, ...] = (0,),
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    from ..platform.windows_subprocess import headless_run_kwargs

    command_environment = os.environ.copy()
    command_environment.update(dict(environment or {}))
    command_environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        timeout=30,
        check=False,
        env=command_environment,
        **headless_run_kwargs(),
    )
    if result.returncode not in allowed_returncodes:
        detail = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
            or f"git {' '.join(args)} failed"
        )
        raise WorkforceLongRunProofError(detail)
    return result


@contextmanager
def _child_pythonpath() -> Iterator[None]:
    source_root = Path(__file__).resolve().parents[2]
    prior = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(source_root), prior)
        if value
    )
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = prior


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkforceLongRunProofError(message)


__all__ = [
    "IncidentClassification",
    "InterventionProof",
    "ObjectiveProof",
    "PROOF_DATE",
    "PROOF_SCHEMA",
    "WorkforceLongRunProofError",
    "WorkforceLongRunReport",
    "run_long_run_proof",
]
