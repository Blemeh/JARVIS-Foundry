from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import jarvis_foundry.supervisor.worker as worker_module
import jarvis_foundry.workforce.gateway as gateway_module
import jarvis_foundry.workforce.steering as steering_module
from jarvis_foundry.clock import parse, utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.worker import (
    WorkerController,
    adapter_pid_table_path,
    load_adapter_pids,
    worker_lease_key,
)
from jarvis_foundry.workforce import (
    INTERVENTION_ACTIONS,
    OBSERVATION_KINDS,
    WorkerObservation,
    rebuild_workforce_state,
    transition_work_package,
)
from jarvis_foundry.workforce.gateway import (
    WorkforceGateway,
    WorkforceGatewayError,
)
from jarvis_foundry.workforce.state import WorkforceStateError
from jarvis_foundry.workforce.steering import (
    RECOMMENDED_ACTION_BY_OBSERVATION,
    WorkforceSteering,
    WorkforceSteeringError,
)

from test_v13_workforce_gateway import (
    _PassingMissionLoop,
    _accept,
    _ready_package,
)


def _running_binding(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    database, package, contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    transition_work_package(
        database,
        package.package_id,
        "RUNNING",
        reason="steering fixture is active",
    )
    return database, package, contract, gateway, binding


def _observation(
    database: FoundryDatabase,
    *,
    package_id: str,
    session_id: str,
    observation_id: str,
    kind: str,
    fingerprint: str,
) -> WorkerObservation:
    observation = WorkerObservation(
        observation_id=observation_id,
        package_id=package_id,
        session_id=session_id,
        kind=kind,
        evidence=(f"evidence:{observation_id}",),
        progress_fingerprint=fingerprint,
        observed_at="2026-09-04T12:00:00Z",
    )
    database.append(Event.create(
        "workforce.observation_recorded",
        observation.to_payload(),
        source_type="supervisor",
        source_id="workforce-observer",
        correlation_id=package_id,
        idempotency_key=f"workforce.observation_recorded:{observation_id}",
    ))
    return observation


def test_observation_policy_covers_every_kind_and_all_actions_remain_explicit():
    assert set(RECOMMENDED_ACTION_BY_OBSERVATION) == set(OBSERVATION_KINDS)
    assert set(RECOMMENDED_ACTION_BY_OBSERVATION.values()) <= set(
        INTERVENTION_ACTIONS
    )
    assert {
        "CONTINUE",
        "REDIRECT",
        "REPAIR",
        "RESTART",
        "REASSIGN",
        "TERMINATE",
        "REDUCE_SCOPE",
        "ESCALATE",
        "WAIT",
    } == set(INTERVENTION_ACTIONS)


def test_continue_renews_process_independent_resource_ownership(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, binding = _running_binding(tmp_path)
    steering = WorkforceSteering(database, gateway=gateway)

    result = steering.intervene(
        package.package_id,
        "CONTINUE",
        reason="healthy progress remains within scope",
    )

    assert result.intervention.outcome == "CONTINUED"
    assert result.session_status == "HEALTHY"
    state = rebuild_workforce_state(database)
    assert state.claims[binding.claim_id].session_id == binding.session.run_id
    assert database.event_count(event_type="workforce.resource_renewed") == 1


def test_restart_prepares_one_fresh_attempt_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, binding = _running_binding(tmp_path)
    steering = WorkforceSteering(database, gateway=gateway)

    first = steering.intervene(
        package.package_id,
        "RESTART",
        reason="durable heartbeat became stale",
        session_id=binding.session.session_id,
        intervention_id="intervention-restart-once",
    )
    replay = steering.intervene(
        package.package_id,
        "RESTART",
        reason="durable heartbeat became stale",
        session_id=binding.session.session_id,
        intervention_id="intervention-restart-once",
    )
    replacement = gateway.bind(package.package_id)

    assert first.intervention.outcome == "READY_FOR_RELAUNCH"
    assert first.package_status == "READY"
    assert replay.replayed is True
    assert replacement.session.attempt == 2
    assert replacement.session.session_id.endswith(":a2")
    assert first.replacement_session_id == replacement.session.session_id
    assert replay == replace(first, replayed=True)
    assert replay.intervention == first.intervention
    assert replay.replacement_session_id == replacement.session.session_id
    assert database.event_count(event_type="workforce.retry_prepared") == 1
    assert database.event_count(event_type="workforce.intervention_recorded") == 1


def test_stale_terminate_cannot_cancel_or_release_current_attempt(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, first = _running_binding(tmp_path)
    steering = WorkforceSteering(database, gateway=gateway)
    steering.intervene(
        package.package_id,
        "RESTART",
        reason="prepare a newer bounded attempt",
        session_id=first.session.session_id,
        intervention_id="intervention-prepare-current-attempt",
    )
    second = gateway.bind(package.package_id)
    second = gateway._admit_launch(
        second,
        launch_kind="INLINE_MISSION_LOOP",
        reason="second attempt is current",
    )

    with pytest.raises(
        WorkforceSteeringError,
        match="superseded workforce attempt",
    ):
        steering.intervene(
            package.package_id,
            "TERMINATE",
            reason="stale caller must not touch the current attempt",
            session_id=first.session.session_id,
            intervention_id="intervention-stale-terminate",
        )

    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "RUNNING"
    assert state.sessions[second.session.session_id].status == "RUNNING"
    active = state.active_claims()
    assert len(active) == 1
    assert active[0].session_id == second.session.run_id
    assert database.event_count(
        event_type="workforce.intervention_recorded",
        readonly=True,
    ) == 1


def test_terminate_rechecks_attempt_under_gateway_admission_guard(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, first = _running_binding(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class BlockingTerminateSteering(WorkforceSteering):
        def _terminate(
            self,
            requested,
            session,
            workers,
            intervention_authority,
        ):
            entered.set()
            assert release.wait(timeout=5)
            return super()._terminate(
                requested,
                session,
                workers,
                intervention_authority,
            )

    steering = BlockingTerminateSteering(database, gateway=gateway)
    with ThreadPoolExecutor(max_workers=1) as executor:
        termination = executor.submit(
            steering.intervene,
            package.package_id,
            "TERMINATE",
            reason="stale termination must not cancel a newer admission",
            session_id=first.session.session_id,
            intervention_id="intervention-admission-race-terminate",
        )
        assert entered.wait(timeout=5)
        gateway._mark_session(first.session, "TERMINATED")
        gateway.leases.release(
            first.resource_lease_key,
            first.session.run_id,
        )
        transition_work_package(
            database,
            package.package_id,
            "FAILED",
            reason="external recovery closed the first attempt",
        )
        transition_work_package(
            database,
            package.package_id,
            "READY",
            reason="external recovery admitted a newer attempt",
        )
        second = gateway.bind(package.package_id)
        second = gateway._admit_launch(
            second,
            launch_kind="INLINE_MISSION_LOOP",
            reason="newer attempt admitted during stale termination",
        )
        release.set()
        with pytest.raises(
            WorkforceSteeringError,
            match="superseded workforce attempt",
        ):
            termination.result(timeout=10)

    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "RUNNING"
    assert state.sessions[second.session.session_id].status == "RUNNING"
    assert state.active_claims()[0].session_id == second.session.run_id
    intervention = state.interventions[
        "intervention-admission-race-terminate"
    ]
    assert intervention.outcome == "FAILED_WORKFORCESTEERINGERROR"


def test_completed_intervention_replay_is_bound_to_attempt(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, first = _running_binding(tmp_path)
    steering = WorkforceSteering(database, gateway=gateway)
    replay_id = "intervention-attempt-bound-replay"
    reason = "record an intervention against the first exact attempt"
    steering.intervene(
        package.package_id,
        "ESCALATE",
        reason=reason,
        session_id=first.session.session_id,
        intervention_id=replay_id,
    )
    steering.intervene(
        package.package_id,
        "RESTART",
        reason="prepare a newer attempt for replay validation",
        session_id=first.session.session_id,
        intervention_id="intervention-attempt-bound-restart",
    )
    second = gateway.bind(package.package_id)

    with pytest.raises(WorkforceSteeringError, match="replay mismatch"):
        steering.intervene(
            package.package_id,
            "ESCALATE",
            reason=reason,
            session_id=second.session.session_id,
            intervention_id=replay_id,
        )


def test_completed_intervention_replay_is_bound_to_observation(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, binding = _running_binding(tmp_path)
    first = _observation(
        database,
        package_id=package.package_id,
        session_id=binding.session.session_id,
        observation_id="observation-replay-first",
        kind="NO_PROGRESS_STALL",
        fingerprint="fingerprint-first",
    )
    second = _observation(
        database,
        package_id=package.package_id,
        session_id=binding.session.session_id,
        observation_id="observation-replay-second",
        kind="NO_PROGRESS_STALL",
        fingerprint="fingerprint-second",
    )
    steering = WorkforceSteering(database, gateway=gateway)
    replay_id = "intervention-observation-bound-replay"
    reason = "record one observation-bound intervention"
    steering.intervene(
        package.package_id,
        "ESCALATE",
        reason=reason,
        observation_id=first.observation_id,
        intervention_id=replay_id,
    )

    with pytest.raises(WorkforceSteeringError, match="replay mismatch"):
        steering.intervene(
            package.package_id,
            "ESCALATE",
            reason=reason,
            observation_id=second.observation_id,
            intervention_id=replay_id,
        )


def test_unreconciled_possible_effect_blocks_restart_without_duplicate_launch(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, binding = _running_binding(tmp_path)
    database.append(Event.create(
        "provider.effect_reconciliation_required",
        {
            "run_id": binding.session.run_id,
            "provider": "provider-a",
            "effect_action_key": "effect:ambiguous",
            "possible_effect": True,
        },
        source_type="supervisor",
        source_id="role-dispatcher",
        idempotency_key="provider.effect_reconciliation_required:steering",
    ))
    steering = WorkforceSteering(database, gateway=gateway)

    result = steering.intervene(
        package.package_id,
        "RESTART",
        reason="provider timed out after a possible effect",
    )

    assert result.intervention.outcome == "BLOCKED_EFFECT_RECONCILIATION"
    assert result.blocked_reason is not None
    assert result.package_status == "RUNNING"
    assert database.event_count(event_type="workforce.retry_prepared") == 0
    assert database.event_count(event_type="workforce.session_bound") == 1


def test_new_ambiguous_effect_after_old_reconciliation_still_blocks_restart(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, binding = _running_binding(tmp_path)
    action_key = "effect:reused-action-key"
    for event_id, event_type, possible_effect in (
        ("first-failure", "provider.dispatch_failed", True),
        ("old-reconciliation", "provider.effect_reconciled", False),
        ("new-failure", "provider.dispatch_failed", True),
    ):
        database.append(Event.create(
            event_type,
            {
                "run_id": binding.session.run_id,
                "provider": "provider-a",
                "effect_action_key": action_key,
                "possible_effect": possible_effect,
            },
            source_type="supervisor",
            source_id="role-dispatcher",
            idempotency_key=f"{event_type}:{event_id}",
        ))
    steering = WorkforceSteering(database, gateway=gateway)

    result = steering.intervene(
        package.package_id,
        "RESTART",
        reason="the newer possible effect remains unresolved",
    )

    assert result.intervention.outcome == "BLOCKED_EFFECT_RECONCILIATION"
    assert result.blocked_reason == (
        f"effect {action_key} must be reconciled before retry"
    )
    assert result.package_status == "RUNNING"
    assert database.event_count(event_type="workforce.retry_prepared") == 0


def test_reassign_relaunches_through_canonical_loop_with_safe_preference(
    tmp_path: Path,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=False,
    )
    first = gateway.run_inline(package.package_id, mission_loop)
    assert first.package_status == "VERIFYING"
    mission_loop.record_acceptance = True
    steering = WorkforceSteering(database, gateway=gateway)

    reassigned = steering.intervene(
        package.package_id,
        "REASSIGN",
        reason="the first framework produced repeated equivalent failure",
        replacement_framework="hermes",
        mission_loop=mission_loop,
    )

    assert reassigned.intervention.outcome == "RELAUNCHED_ACCEPTED"
    assert reassigned.package_status == "ACCEPTED"
    assert reassigned.replacement_session_id is not None
    assert reassigned.replacement_session_id.endswith(":a2")
    context = mission_loop.calls[-1]["workforce_context"]
    assert context["worker_preference"] == "hermes"
    assert context["worker_preference_evidence"] == [
        f"intervention:{reassigned.intervention.intervention_id}"
    ]
    assert database.event_count(event_type="workforce.session_bound") == 2
    assert database.event_count(event_type="verification.accepted") == 1


@pytest.mark.parametrize(
    ("action", "instruction", "expected_outcome"),
    [
        ("REDIRECT", "Return to the bounded source task.", "EXECUTIVE_REPLAN_REQUIRED"),
        ("REDUCE_SCOPE", "Limit the next package to src/core.", "NEW_PACKAGE_REQUIRED"),
    ],
)
def test_directive_actions_pause_old_attempt_for_immutable_replanning(
    tmp_path: Path,
    action: str,
    instruction: str,
    expected_outcome: str,
) -> None:
    database, package, _contract, gateway, _binding = _running_binding(tmp_path)
    steering = WorkforceSteering(database, gateway=gateway)

    result = steering.intervene(
        package.package_id,
        action,
        reason="the active attempt no longer matches the bounded plan",
        instruction=instruction,
    )

    assert result.intervention.outcome == expected_outcome
    assert result.package_status == "WAITING"
    assert result.session_status == "TERMINATED"
    assert database.event_count(
        event_type="workforce.steering_directive_issued"
    ) == 1


def test_repair_requires_instruction_and_prepares_canonical_retry(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, _binding = _running_binding(tmp_path)
    steering = WorkforceSteering(database, gateway=gateway)

    with pytest.raises(Exception, match="requires a bounded instruction"):
        steering.intervene(
            package.package_id,
            "REPAIR",
            reason="independent verifier rejected the artifact",
        )

    result = steering.intervene(
        package.package_id,
        "REPAIR",
        reason="independent verifier rejected the artifact",
        instruction="Repair the verifier findings and rerun mandatory tests.",
    )

    assert result.intervention.outcome == "READY_FOR_RELAUNCH"
    assert result.package_status == "READY"


def test_retry_rejects_caller_controlled_process_command(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, _binding = _running_binding(tmp_path)
    steering = WorkforceSteering(database, gateway=gateway)

    with pytest.raises(WorkforceSteeringError, match="sealed"):
        steering.intervene(
            package.package_id,
            "RESTART",
            reason="caller attempted to bypass the sealed runner",
            command=[sys.executable, "-c", "raise SystemExit(0)"],
        )

    assert database.event_count(event_type="workforce.intervention_recorded") == 0
    assert database.event_count(event_type="workforce.retry_prepared") == 0


def test_wait_and_escalate_are_durable_without_inventing_work(
    tmp_path: Path,
) -> None:
    wait_db, wait_package, _contract, wait_gateway, _binding = _running_binding(
        tmp_path / "wait"
    )
    wait = WorkforceSteering(wait_db, gateway=wait_gateway).intervene(
        wait_package.package_id,
        "WAIT",
        reason="external dependency remains blocked",
    )
    assert wait.intervention.outcome == "WAITING"
    assert wait.package_status == "WAITING"

    escalation_db, package, _contract, gateway, _binding = _running_binding(
        tmp_path / "escalate"
    )
    escalation = WorkforceSteering(
        escalation_db,
        gateway=gateway,
    ).intervene(
        package.package_id,
        "ESCALATE",
        reason="owner decision is required for a new consequence boundary",
    )
    assert escalation.intervention.outcome == "ESCALATED"
    assert escalation.package_status == "RUNNING"


def test_sealed_spawn_blocks_preemption_until_pid_registration_is_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    process_registered = threading.Event()
    registration_release = threading.Event()
    original_spawn = workers.spawn

    def pause_after_process_registration(*args, **kwargs):
        status = original_spawn(*args, **kwargs)
        process_registered.set()
        assert registration_release.wait(timeout=5)
        return status

    monkeypatch.setattr(workers, "spawn", pause_after_process_registration)
    steering = WorkforceSteering(database, gateway=gateway)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            launch_future = executor.submit(
                gateway.spawn,
                package.package_id,
                workers,
            )
            assert process_registered.wait(timeout=5)
            launching_state = rebuild_workforce_state(database)
            launching_session = launching_state.sessions_for(
                package.package_id
            )[0]
            assert launching_session.result["launch_phase"] == (
                "PROCESS_LAUNCHING"
            )
            termination = executor.submit(
                steering.intervene,
                package.package_id,
                "TERMINATE",
                reason="preemption waits for durable process registration",
                session_id=launching_session.session_id,
                intervention_id="intervention-spawn-registration-fence",
                workers=workers,
            )
            time.sleep(0.1)
            assert termination.done() is False
            registration_release.set()
            launch = launch_future.result(timeout=10)
            result = termination.result(timeout=15)
    finally:
        registration_release.set()
        workers.terminate_all()

    assert launch.spawned is True
    assert result.intervention.outcome == "TERMINATED"
    assert result.terminated_owned_process is True
    assert result.package_status == "CANCELLED"
    state = rebuild_workforce_state(database)
    assert state.sessions[launch.binding.session.session_id].status == (
        "TERMINATED"
    )
    assert state.sessions[
        launch.binding.session.session_id
    ].result["launch_phase"] == "PROCESS_REGISTERED"


def test_sealed_spawn_replay_repairs_durable_process_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    binding = gateway.bind(package.package_id)
    binding = gateway._admit_launch(
        binding,
        launch_kind="SEALED_PACKAGE_RUNNER",
        reason="sealed process launch intent survived before registration",
    )
    process_status = workers.spawn(
        binding.session.run_id,
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=Path(package.worktree_path),
        worker_type="workforce-canonical-mission",
        mission_id=package.mission_id,
        step_id=package.step_id,
    )
    assert process_status.alive is True
    assert binding.session.result["launch_phase"] == "PROCESS_LAUNCHING"
    monkeypatch.setattr(
        workers,
        "spawn",
        lambda *_args, **_kwargs: pytest.fail(
            "replay must not launch a replacement process"
        ),
    )

    try:
        replay = gateway.spawn(package.package_id, workers)
    finally:
        workers.terminate_all()

    assert replay.spawned is False
    assert replay.worker_status is not None
    assert replay.worker_status.alive is True
    assert replay.binding.session.result["launch_phase"] == "PROCESS_REGISTERED"
    assert replay.binding.session.result["pid"] == process_status.process.pid
    state = rebuild_workforce_state(database)
    assert state.sessions[binding.session.session_id].result["launch_phase"] == (
        "PROCESS_REGISTERED"
    )
    assert database.event_count(
        event_type="workforce.session_state_changed"
    ) == 1


def test_sealed_spawn_authority_loss_cleans_the_unregistered_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    admission_key = f"workforce-admission:{package.package_id}"
    intruder = "workforce-control:spawn-registration-authority-loss"
    run_ids: list[str] = []
    original_registration = gateway._record_process_registration

    def steal_before_registration(
        binding,
        status,
        supplied_workers,
        *,
        admission_authority,
    ):
        run_ids.append(binding.session.run_id)
        assert gateway._take_over_admission_lease(
            admission_key,
            observed_owner=admission_authority.owner_id,
            new_owner=intruder,
        )
        return original_registration(
            binding,
            status,
            supplied_workers,
            admission_authority=admission_authority,
        )

    monkeypatch.setattr(
        gateway,
        "_record_process_registration",
        steal_before_registration,
    )
    try:
        with pytest.raises(
            WorkforceGatewayError,
            match="authority was lost",
        ):
            gateway.spawn(package.package_id, workers)
    finally:
        gateway.leases.release(admission_key, intruder)
        workers.terminate_all()

    assert len(run_ids) == 1
    assert workers.status(run_ids[0]).alive is False
    assert database.row(
        "leases",
        "lease_key",
        worker_lease_key(run_ids[0]),
    ) is None
    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "RUNNING"
    assert state.sessions_for(package.package_id)[0].status == "RUNNING"
    assert database.event_count(event_type="workforce.result_linked") == 0


def test_terminate_kills_only_owned_process_and_releases_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gateway_module,
        "_ADMISSION_LEASE_RENEW_SECONDS",
        0.05,
    )
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    termination_entered = threading.Event()
    termination_release = threading.Event()
    original_terminate_epoch = gateway._terminate_process_epoch

    def slow_terminate(
        epoch,
        *,
        control_authority_check=None,
    ) -> bool:
        termination_entered.set()
        assert termination_release.wait(timeout=5)
        return original_terminate_epoch(
            epoch,
            control_authority_check=control_authority_check,
        )

    monkeypatch.setattr(
        gateway,
        "_terminate_process_epoch",
        slow_terminate,
    )
    steering = WorkforceSteering(database, gateway=gateway)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            termination = executor.submit(
                steering.intervene,
                package.package_id,
                "TERMINATE",
                reason="scope escape requires immediate bounded termination",
                workers=workers,
            )
            try:
                assert termination_entered.wait(timeout=5)
                admission_key = f"workforce-admission:{package.package_id}"
                initial = database.row("leases", "lease_key", admission_key)
                assert initial is not None
                assert str(initial["owner_id"]).startswith(
                    "workforce-control:"
                )
                renewal_deadline = time.monotonic() + 2
                renewed = initial
                while renewed["heartbeat_at"] == initial["heartbeat_at"]:
                    assert time.monotonic() < renewal_deadline
                    time.sleep(0.01)
                    renewed = database.row(
                        "leases",
                        "lease_key",
                        admission_key,
                    )
                    assert renewed is not None
                assert renewed["owner_id"] == initial["owner_id"]
                lease_horizon = (
                    parse(str(renewed["expires_at"])) - utc_now()
                ).total_seconds()
                assert (
                    0
                    < lease_horizon
                    <= gateway_module._ADMISSION_LEASE_SECONDS + 1
                )
            finally:
                termination_release.set()
            result = termination.result(timeout=10)
    finally:
        termination_release.set()
        workers.terminate_all()

    assert result.intervention.outcome == "TERMINATED"
    assert result.terminated_owned_process is True
    assert result.package_status == "CANCELLED"
    assert workers.status(launch.binding.session.run_id).alive is False
    assert rebuild_workforce_state(database).active_claims() == ()


def test_exact_epoch_termination_does_not_kill_same_executable_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    command = [
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
    ]
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: list(command),
    )
    launch = gateway.spawn(package.package_id, workers)
    target = launch.worker_status.process
    assert target is not None
    sibling = subprocess.Popen(command)
    try:
        result = WorkforceSteering(
            database,
            gateway=gateway,
        ).intervene(
            package.package_id,
            "TERMINATE",
            reason="terminate only the exact registered worker epoch",
            session_id=launch.binding.session.session_id,
            intervention_id="intervention-exact-epoch-not-sibling",
            workers=workers,
        )

        assert result.terminated_owned_process is True
        assert target.wait(timeout=10) is not None
        assert sibling.poll() is None
    finally:
        workers.terminate_all()
        if sibling.poll() is None:
            sibling.terminate()
            try:
                sibling.wait(timeout=10)
            except subprocess.TimeoutExpired:
                sibling.kill()
                sibling.wait(timeout=10)


def test_control_authority_loss_after_process_stop_blocks_lifecycle_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    steering = WorkforceSteering(database, gateway=gateway)
    admission_key = f"workforce-admission:{package.package_id}"
    intruder = "workforce-control:lost-authority-probe"
    session_events_before = database.event_count(
        event_type="workforce.session_state_changed"
    )
    package_events_before = database.event_count(
        event_type="workforce.package_state_changed"
    )
    original_terminate_owned = steering._terminate_owned

    def terminate_then_steal(
        session,
        supplied_workers,
        *,
        control_authority_check=None,
    ):
        result = original_terminate_owned(
            session,
            supplied_workers,
            control_authority_check=control_authority_check,
        )
        control_lease = database.row(
            "leases",
            "lease_key",
            admission_key,
        )
        assert control_lease is not None
        assert str(control_lease["owner_id"]).startswith(
            "workforce-control:"
        )
        assert gateway._take_over_admission_lease(
            admission_key,
            observed_owner=str(control_lease["owner_id"]),
            new_owner=intruder,
        )
        return result

    monkeypatch.setattr(
        steering,
        "_terminate_owned",
        terminate_then_steal,
    )
    try:
        with pytest.raises(
            WorkforceGatewayError,
            match="authority was lost",
        ):
            steering.intervene(
                package.package_id,
                "TERMINATE",
                reason="lost control authority must block lifecycle writes",
                session_id=launch.binding.session.session_id,
                intervention_id="intervention-control-authority-loss",
                workers=workers,
            )
    finally:
        gateway.leases.release(admission_key, intruder)
        workers.terminate_all()

    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "RUNNING"
    assert state.sessions[launch.binding.session.session_id].status == "RUNNING"
    assert len(state.active_claims()) == 1
    assert database.event_count(
        event_type="workforce.session_state_changed"
    ) == session_events_before
    assert database.event_count(
        event_type="workforce.package_state_changed"
    ) == package_events_before
    assert state.interventions[
        "intervention-control-authority-loss"
    ].outcome == "FAILED_WORKFORCEGATEWAYERROR"


@pytest.mark.parametrize(
    ("action", "extra"),
    [
        ("TERMINATE", {}),
        ("RESTART", {}),
        ("REDIRECT", {"instruction": "Return to the bounded objective."}),
        ("REDUCE_SCOPE", {"instruction": "Create a smaller work package."}),
    ],
)
def test_active_admitted_attempt_requires_confirmed_owned_process_termination(
    tmp_path: Path,
    action: str,
    extra: dict[str, str],
) -> None:
    database, package, _contract, gateway, binding = _running_binding(
        tmp_path / action.lower()
    )
    admitted = gateway._admit_launch(
        binding,
        launch_kind="INLINE_MISSION_LOOP",
        reason="admit active execution without an owned process fixture",
    )
    admission_key = f"workforce-admission:{package.package_id}"
    assert database.row("leases", "lease_key", admission_key) is None
    steering = WorkforceSteering(database, gateway=gateway)
    intervention_id = f"intervention-missing-fence-{action.lower()}"

    with pytest.raises(
        WorkforceGatewayError,
        match="active inline workforce execution cannot be preempted",
    ):
        steering.intervene(
            package.package_id,
            action,
            reason="missing fence cannot stand in for process termination",
            session_id=admitted.session.session_id,
            intervention_id=intervention_id,
            **extra,
        )

    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "RUNNING"
    assert state.sessions[admitted.session.session_id].status == "RUNNING"
    assert state.active_claims()[0].session_id == admitted.session.run_id
    assert state.interventions[intervention_id].outcome == (
        "FAILED_WORKFORCEGATEWAYERROR"
    )
    assert database.event_count(event_type="workforce.retry_prepared") == 0
    assert database.event_count(
        event_type="workforce.steering_directive_issued"
    ) == 0
    assert database.row("leases", "lease_key", admission_key) is None


@pytest.mark.parametrize(
    (
        "hazard",
        "expected_outcome",
        "expected_reason",
        "expected_package_status",
        "expected_claim_count",
    ),
    [
        (
            "acceptance",
            "BLOCKED_CANONICAL_ACCEPTANCE",
            "canonical verifier already accepted",
            "ACCEPTED",
            0,
        ),
        (
            "effect",
            "BLOCKED_EFFECT_RECONCILIATION",
            "must be reconciled before retry",
            "WAITING",
            1,
        ),
    ],
)
def test_retry_rechecks_hazards_after_confirmed_process_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hazard: str,
    expected_outcome: str,
    expected_reason: str,
    expected_package_status: str,
    expected_claim_count: int,
) -> None:
    database, package, _contract = _ready_package(tmp_path / hazard)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    injected = False
    steering = WorkforceSteering(database, gateway=gateway)
    original_terminate_owned = steering._terminate_owned

    def terminate_then_record_hazard(
        session,
        supplied_workers,
        *,
        control_authority_check=None,
    ):
        nonlocal injected
        terminated = original_terminate_owned(
            session,
            supplied_workers,
            control_authority_check=control_authority_check,
        )
        if injected:
            return terminated
        injected = True
        if hazard == "acceptance":
            _accept(
                database,
                package,
                run_id=session.run_id,
                verdict_id=f"verdict:{session.run_id}:post-stop",
            )
        else:
            database.append(Event.create(
                "provider.effect_reconciliation_required",
                {
                    "run_id": session.run_id,
                    "provider": "provider-a",
                    "effect_action_key": (
                        f"effect:{session.run_id}:post-stop"
                    ),
                    "possible_effect": True,
                },
                source_type="supervisor",
                source_id="role-dispatcher",
                idempotency_key=(
                    "provider.effect_reconciliation_required:"
                    f"{session.run_id}:post-stop"
                ),
            ))
        return terminated

    monkeypatch.setattr(
        steering,
        "_terminate_owned",
        terminate_then_record_hazard,
    )
    try:
        result = steering.intervene(
            package.package_id,
            "RESTART",
            reason="post-stop hazards must block retry preparation",
            session_id=launch.binding.session.session_id,
            intervention_id=f"intervention-post-stop-{hazard}",
            workers=workers,
        )
    finally:
        workers.terminate_all()

    assert injected is True
    assert result.intervention.outcome == expected_outcome
    assert expected_reason in str(result.blocked_reason)
    assert result.terminated_owned_process is True
    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == expected_package_status
    assert state.sessions[
        launch.binding.session.session_id
    ].status == "TERMINATED"
    assert len(state.active_claims()) == expected_claim_count
    assert database.event_count(event_type="workforce.retry_prepared") == 0
    assert database.event_count(event_type="workforce.session_bound") == 1


@pytest.mark.parametrize(
    ("action", "extra"),
    [
        ("CONTINUE", {}),
        ("WAIT", {}),
        ("RESTART", {}),
        ("REDIRECT", {"instruction": "Return to the bounded source task."}),
        ("ESCALATE", {}),
    ],
)
def test_stop_blocks_new_retry_and_directive_work(
    tmp_path: Path,
    action: str,
    extra: dict[str, str],
) -> None:
    database, package, _contract, gateway, _binding = _running_binding(
        tmp_path / action.lower()
    )
    stop_path = tmp_path / "STOP"
    stop_path.write_text("owner stop\n", encoding="utf-8")
    steering = WorkforceSteering(
        database,
        gateway=gateway,
        stop_path=stop_path,
    )

    with pytest.raises(PermissionError, match="workforce stopped"):
        steering.intervene(
            package.package_id,
            action,
            reason="must remain blocked under owner STOP",
            **extra,
        )

    assert database.event_count(event_type="workforce.intervention_recorded") == 0
    assert database.event_count(event_type="workforce.retry_prepared") == 0
    assert database.event_count(
        event_type="workforce.steering_directive_issued"
    ) == 0
    assert database.event_count(event_type="workforce.resource_renewed") == 0


def test_stop_permits_exact_owned_termination_and_scope_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(
        tmp_path,
        package_overrides={"duration_seconds": 86_400},
    )
    stop_path = tmp_path / "STOP"
    entered_path = tmp_path / "runner-entered"
    source_root = Path(__file__).resolve().parents[2] / "src"
    gateway = WorkforceGateway(database, stop_path=stop_path)
    workers = WorkerController(database, lease_seconds=60)

    child_code = "\n".join([
        "import sys",
        "import time",
        "from pathlib import Path",
        (
            "source_root, database_path, package_id, run_id, "
            "stop_path, entered_path = sys.argv[1:]"
        ),
        "sys.path.insert(0, source_root)",
        "from jarvis_foundry.database import FoundryDatabase",
        "from jarvis_foundry.workforce.gateway import WorkforceGateway",
        "class BlockingLoop:",
        "    def run_step(self, **kwargs):",
        (
            "        Path(entered_path).write_text("
            "'entered\\n', encoding='utf-8')"
        ),
        "        while True:",
        "            time.sleep(1)",
        "database = FoundryDatabase(Path(database_path))",
        "database.initialize()",
        "gateway = WorkforceGateway(database, stop_path=Path(stop_path))",
        (
            "gateway.run_inline(package_id, BlockingLoop(), "
            "expected_run_id=run_id)"
        ),
    ])

    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda package_id, run_id: [
            sys.executable,
            "-c",
            child_code,
            str(source_root),
            str(database.path),
            package_id,
            run_id,
            str(stop_path),
            str(entered_path),
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    process = launch.worker_status.process
    assert process is not None
    entered_deadline = time.monotonic() + 10
    while not entered_path.is_file():
        assert process.poll() is None
        assert time.monotonic() < entered_deadline
        time.sleep(0.05)

    admission_key = f"workforce-admission:{package.package_id}"
    execution_lease = database.row("leases", "lease_key", admission_key)
    assert execution_lease is not None
    assert gateway._execution_guard_owner_matches(
        str(execution_lease["owner_id"]),
        launch.binding.session.run_id,
    )
    execution_owner = str(execution_lease["owner_id"])
    lease_horizon = (
        parse(str(execution_lease["expires_at"])) - utc_now()
    ).total_seconds()
    assert 0 < lease_horizon <= 31

    owners_during_termination: list[str] = []
    original_terminate_epoch = gateway._terminate_process_epoch

    def terminate_after_authority_transfer(
        epoch,
        *,
        control_authority_check=None,
    ) -> bool:
        control_lease = database.row(
            "leases",
            "lease_key",
            admission_key,
        )
        assert control_lease is not None
        owners_during_termination.append(str(control_lease["owner_id"]))
        return original_terminate_epoch(
            epoch,
            control_authority_check=control_authority_check,
        )

    monkeypatch.setattr(
        gateway,
        "_terminate_process_epoch",
        terminate_after_authority_transfer,
    )
    stop_path.write_text("owner stop\n", encoding="utf-8")
    steering = WorkforceSteering(
        database,
        gateway=gateway,
        stop_path=stop_path,
    )
    started = time.monotonic()
    try:
        result = steering.intervene(
            package.package_id,
            "TERMINATE",
            reason="owner STOP requires exact owned cleanup",
            workers=workers,
            intervention_id="intervention-stop-owned-terminate",
        )
    finally:
        workers.terminate_all()
    elapsed = time.monotonic() - started

    assert result.intervention.outcome == "TERMINATED"
    assert result.terminated_owned_process is True
    assert result.package_status == "CANCELLED"
    assert elapsed < 10
    assert len(owners_during_termination) == 1
    assert owners_during_termination[0].startswith("workforce-control:")
    assert owners_during_termination[0] != execution_owner
    assert workers.status(launch.binding.session.run_id).alive is False
    assert rebuild_workforce_state(database).active_claims() == ()
    assert database.row("leases", "lease_key", admission_key) is None
    assert database.row(
        "leases",
        "lease_key",
        f"workforce-intervention-package:{package.package_id}",
    ) is None


def test_retry_takes_over_terminal_attempts_stale_execution_fence(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, first = _running_binding(tmp_path)
    gateway._mark_session(first.session, "TERMINATED")
    admission_key = f"workforce-admission:{package.package_id}"
    stale_owner = (
        gateway._execution_guard_owner_prefix(first.session.run_id)
        + "stale-after-crash"
    )
    gateway.leases.acquire(admission_key, stale_owner, 30)
    stale_worker_key = worker_lease_key(first.session.run_id)
    gateway.leases.acquire(stale_worker_key, stale_worker_key, 60)
    workers = WorkerController(database, lease_seconds=60)
    steering = WorkforceSteering(database, gateway=gateway)

    result = steering.intervene(
        package.package_id,
        "RESTART",
        reason="reconciled dead attempt may release its stale execution fence",
        session_id=first.session.session_id,
        intervention_id="intervention-terminal-stale-execution-fence",
        workers=workers,
    )

    assert result.intervention.outcome == "READY_FOR_RELAUNCH"
    assert result.terminated_owned_process is False
    assert result.package_status == "READY"
    assert database.row("leases", "lease_key", admission_key) is None
    assert database.row("leases", "lease_key", stale_worker_key) is None


def test_retry_reconciles_an_already_dead_registered_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "pass",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    process = launch.worker_status.process
    assert process is not None
    process.wait(timeout=10)
    assert workers.status(launch.binding.session.run_id).alive is False

    result = WorkforceSteering(
        database,
        gateway=gateway,
    ).intervene(
        package.package_id,
        "RESTART",
        reason="dead registered worker can be reconciled before retry",
        session_id=launch.binding.session.session_id,
        intervention_id="intervention-dead-registered-worker",
        workers=workers,
    )

    assert result.intervention.outcome == "READY_FOR_RELAUNCH"
    assert result.terminated_owned_process is False
    assert result.package_status == "READY"
    state = rebuild_workforce_state(database)
    assert state.sessions[
        launch.binding.session.session_id
    ].status == "TERMINATED"
    assert database.row(
        "leases",
        "lease_key",
        worker_lease_key(launch.binding.session.run_id),
    ) is None


def test_requested_only_intervention_replays_after_crash(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, _binding = _running_binding(tmp_path)

    class CrashOnceSteering(WorkforceSteering):
        crashed = False

        def _directive(self, intervention, intervention_authority):
            if not self.crashed:
                self.crashed = True
                raise KeyboardInterrupt("simulated crash after durable request")
            return super()._directive(
                intervention,
                intervention_authority,
            )

    crashed = CrashOnceSteering(database, gateway=gateway)
    with pytest.raises(KeyboardInterrupt, match="simulated crash"):
        crashed.intervene(
            package.package_id,
            "ESCALATE",
            reason="resume the exact requested intervention",
            intervention_id="intervention-requested-crash",
        )

    state = rebuild_workforce_state(database)
    assert state.interventions[
        "intervention-requested-crash"
    ].outcome == "REQUESTED"
    assert database.event_count(event_type="workforce.intervention_completed") == 0

    replay = WorkforceSteering(database, gateway=gateway).intervene(
        package.package_id,
        "ESCALATE",
        reason="resume the exact requested intervention",
        intervention_id="intervention-requested-crash",
    )

    assert replay.intervention.outcome == "ESCALATED"
    assert replay.replayed is True
    assert database.event_count(event_type="workforce.intervention_recorded") == 1
    assert database.event_count(
        event_type="workforce.steering_directive_issued"
    ) == 1
    assert database.event_count(event_type="workforce.intervention_completed") == 1
    assert database.row(
        "leases",
        "lease_key",
        f"workforce-intervention-package:{package.package_id}",
    ) is None


def test_concurrent_same_intervention_serializes_one_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(steering_module, "INTERVENTION_LEASE_SECONDS", 1)
    monkeypatch.setattr(
        steering_module,
        "INTERVENTION_LEASE_RENEW_SECONDS",
        0.1,
    )
    database, package, _contract, gateway, _binding = _running_binding(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    directive_calls = 0
    call_lock = threading.Lock()

    class BlockingSteering(WorkforceSteering):
        def _directive(self, intervention, intervention_authority):
            nonlocal directive_calls
            with call_lock:
                directive_calls += 1
                call_number = directive_calls
            if call_number == 1:
                entered.set()
                assert release.wait(timeout=5)
            return super()._directive(
                intervention,
                intervention_authority,
            )

    steering = BlockingSteering(database, gateway=gateway)
    kwargs = {
        "reason": "serialize the exact concurrent intervention",
        "intervention_id": "intervention-concurrent-once",
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            steering.intervene,
            package.package_id,
            "ESCALATE",
            **kwargs,
        )
        assert entered.wait(timeout=5)
        time.sleep(1.2)
        second = executor.submit(
            steering.intervene,
            package.package_id,
            "ESCALATE",
            **kwargs,
        )
        time.sleep(0.1)
        assert second.done() is False
        release.set()
        results = (first.result(timeout=5), second.result(timeout=5))

    assert {result.intervention.outcome for result in results} == {"ESCALATED"}
    assert sum(result.replayed for result in results) == 1
    assert directive_calls == 1
    assert database.event_count(event_type="workforce.intervention_recorded") == 1
    assert database.event_count(
        event_type="workforce.steering_directive_issued"
    ) == 1
    assert database.event_count(event_type="workforce.intervention_completed") == 1
    assert database.row(
        "leases",
        "lease_key",
        f"workforce-intervention-package:{package.package_id}",
    ) is None


def test_distinct_interventions_serialize_before_stale_attempt_check(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, first = _running_binding(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=False,
    )

    class BlockingRetrySteering(WorkforceSteering):
        def _prepare_retry(
            self,
            requested,
            session,
            workers,
            *,
            mission_loop,
            intervention_authority,
        ):
            terminated = super()._prepare_retry(
                requested,
                session,
                workers,
                mission_loop=mission_loop,
                intervention_authority=intervention_authority,
            )
            entered.set()
            assert release.wait(timeout=5)
            return terminated

    steering = BlockingRetrySteering(database, gateway=gateway)
    with ThreadPoolExecutor(max_workers=2) as executor:
        restart = executor.submit(
            steering.intervene,
            package.package_id,
            "RESTART",
            reason="serialize retry before another package intervention",
            session_id=first.session.session_id,
            mission_loop=mission_loop,
            intervention_id="intervention-package-restart",
        )
        assert entered.wait(timeout=5)
        stale_terminate = executor.submit(
            steering.intervene,
            package.package_id,
            "TERMINATE",
            reason="stale termination must wait for package serialization",
            session_id=first.session.session_id,
            intervention_id="intervention-package-stale-terminate",
        )
        time.sleep(0.1)
        assert stale_terminate.done() is False
        release.set()
        restarted = restart.result(timeout=10)
        with pytest.raises(
            WorkforceSteeringError,
            match="superseded workforce attempt",
        ):
            stale_terminate.result(timeout=10)

    state = rebuild_workforce_state(database)
    latest = max(
        state.sessions_for(package.package_id),
        key=lambda item: item.attempt,
    )
    assert restarted.replacement_session_id == latest.session_id
    assert latest.attempt == 2
    assert state.package(package.package_id).status == "VERIFYING"
    assert state.active_claims()[0].session_id == latest.session_id
    assert database.event_count(event_type="workforce.intervention_recorded") == 1


def test_completed_intervention_id_rejects_mismatched_replay(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, _binding = _running_binding(tmp_path)
    steering = WorkforceSteering(database, gateway=gateway)
    steering.intervene(
        package.package_id,
        "ESCALATE",
        reason="record one exact idempotent intervention",
        intervention_id="intervention-replay-contract",
    )

    with pytest.raises(WorkforceSteeringError, match="replay mismatch"):
        steering.intervene(
            package.package_id,
            "WAIT",
            reason="attempt to reuse the completed intervention identity",
            intervention_id="intervention-replay-contract",
        )


def test_accepted_package_cannot_be_restarted(tmp_path: Path) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=True,
    )
    accepted = gateway.run_inline(package.package_id, mission_loop)
    steering = WorkforceSteering(database, gateway=gateway)

    result = steering.intervene(
        package.package_id,
        "RESTART",
        reason="duplicate delivery after acceptance",
        session_id=accepted.binding.session.session_id,
    )

    assert result.intervention.outcome == "BLOCKED_TERMINAL_PACKAGE"
    assert result.package_status == "ACCEPTED"
    assert len(mission_loop.calls) == 1


def test_canonical_acceptance_crash_window_blocks_duplicate_retry(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, binding = _running_binding(tmp_path)
    transition_work_package(
        database,
        package.package_id,
        "VERIFYING",
        reason="canonical verifier is finalizing",
    )
    _accept(
        database,
        package,
        run_id=binding.session.run_id,
        verdict_id=f"verdict:{binding.session.run_id}",
    )
    steering = WorkforceSteering(database, gateway=gateway)

    result = steering.intervene(
        package.package_id,
        "RESTART",
        reason="caller replayed before workforce acceptance projection",
    )

    assert result.intervention.outcome == "BLOCKED_CANONICAL_ACCEPTANCE"
    assert result.package_status == "VERIFYING"
    assert database.event_count(event_type="workforce.retry_prepared") == 0
    assert database.event_count(event_type="workforce.session_bound") == 1


def test_intervention_evaluation_records_observed_improvement(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, binding = _running_binding(tmp_path)
    before = _observation(
        database,
        package_id=package.package_id,
        session_id=binding.session.session_id,
        observation_id="observation-before",
        kind="NO_PROGRESS_STALL",
        fingerprint="stalled-fingerprint",
    )
    steering = WorkforceSteering(database, gateway=gateway)
    intervention = steering.intervene(
        package.package_id,
        "ESCALATE",
        reason="record a bounded escalation before reassignment",
        observation_id=before.observation_id,
    )
    with pytest.raises(
        WorkforceSteeringError,
        match="post-completion observation",
    ):
        steering.evaluate(
            intervention.intervention.intervention_id,
            before.observation_id,
        )
    after = _observation(
        database,
        package_id=package.package_id,
        session_id=binding.session.session_id,
        observation_id="observation-after",
        kind="HEALTHY_PROGRESS",
        fingerprint="healthy-fingerprint",
    )

    evaluated = steering.evaluate(
        intervention.intervention.intervention_id,
        after.observation_id,
    )
    replay = steering.evaluate(
        intervention.intervention.intervention_id,
        after.observation_id,
    )
    conflicting = _observation(
        database,
        package_id=package.package_id,
        session_id=binding.session.session_id,
        observation_id="observation-conflicting-after",
        kind="NO_PROGRESS_STALL",
        fingerprint="different-after-fingerprint",
    )
    with pytest.raises(
        WorkforceSteeringError,
        match="immutable evaluation",
    ):
        steering.evaluate(
            intervention.intervention.intervention_id,
            conflicting.observation_id,
        )

    assert evaluated.outcome == "IMPROVED"
    assert evaluated.improved is True
    assert evaluated.before_progress_fingerprint == "stalled-fingerprint"
    assert evaluated.after_progress_fingerprint == "healthy-fingerprint"
    assert replay == evaluated
    assert database.event_count(
        event_type="workforce.intervention_evaluated"
    ) == 1


@pytest.mark.parametrize("identity_mutation", ["changed", "missing"])
def test_termination_refuses_a_swapped_pid_creation_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_mutation: str,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    process = launch.worker_status.process
    assert process is not None
    pid_path = adapter_pid_table_path(database)
    original_records = load_adapter_pids(pid_path)
    changed_records = json.loads(json.dumps(original_records))
    if identity_mutation == "changed":
        changed_records[launch.binding.session.run_id][
            "creation_identity"
        ]["create_time"] += 1
    else:
        changed_records[launch.binding.session.run_id].pop(
            "creation_identity",
            None,
        )
    pid_path.write_text(
        json.dumps(changed_records, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    terminator_called = False

    def reject_epoch_termination(
        _epoch,
        *,
        control_authority_check=None,
    ):
        nonlocal terminator_called
        terminator_called = True
        pytest.fail("a mismatched process epoch must never be terminated")

    monkeypatch.setattr(
        gateway,
        "_terminate_process_epoch",
        reject_epoch_termination,
    )
    try:
        with pytest.raises(
            WorkforceGatewayError,
            match="exact controllable owned process",
        ):
            WorkforceSteering(
                database,
                gateway=gateway,
            ).intervene(
                package.package_id,
                "TERMINATE",
                reason="PID reuse evidence must fail closed",
                session_id=launch.binding.session.session_id,
                intervention_id="intervention-pid-epoch-swap",
                workers=workers,
            )
        assert process.poll() is None
        assert terminator_called is False
    finally:
        pid_path.write_text(
            json.dumps(
                original_records,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        workers.terminate_all()


def test_termination_fails_closed_when_live_epoch_identity_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    run_id = launch.binding.session.run_id
    process = launch.worker_status.process
    assert process is not None
    original_gateway_identity = gateway_module.process_creation_identity
    original_worker_identity = worker_module.process_creation_identity
    workers._processes.pop(run_id)

    def unreadable_identity(pid: int):
        if pid == process.pid:
            return {"pid": pid}
        return original_gateway_identity(pid)

    monkeypatch.setattr(
        gateway_module,
        "process_creation_identity",
        unreadable_identity,
    )
    monkeypatch.setattr(
        worker_module,
        "process_creation_identity",
        unreadable_identity,
    )
    try:
        with pytest.raises(
            WorkforceGatewayError,
            match="exact controllable owned process",
        ):
            WorkforceSteering(
                database,
                gateway=gateway,
            ).intervene(
                package.package_id,
                "TERMINATE",
                reason="unreadable live epoch identity must fail closed",
                session_id=launch.binding.session.session_id,
                intervention_id="intervention-unreadable-live-epoch",
                workers=workers,
            )

        assert process.poll() is None
        state = rebuild_workforce_state(database)
        assert state.package(package.package_id).status == "RUNNING"
        assert state.sessions[
            launch.binding.session.session_id
        ].status == "RUNNING"
        assert database.row(
            "leases",
            "lease_key",
            worker_lease_key(run_id),
        ) is not None
        assert load_adapter_pids(
            adapter_pid_table_path(database)
        ).get(run_id) is not None
    finally:
        monkeypatch.setattr(
            gateway_module,
            "process_creation_identity",
            original_gateway_identity,
        )
        monkeypatch.setattr(
            worker_module,
            "process_creation_identity",
            original_worker_identity,
        )
        workers._processes[run_id] = process
        workers.terminate_all()


def test_termination_retries_a_transient_owned_process_identity_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    steering = WorkforceSteering(database, gateway=gateway)
    original_can_terminate_owned = steering._can_terminate_owned
    observations = 0

    def transient_can_terminate_owned(session, supplied_workers):
        nonlocal observations
        observations += 1
        if observations == 1:
            return False
        return original_can_terminate_owned(session, supplied_workers)

    monkeypatch.setattr(
        steering,
        "_can_terminate_owned",
        transient_can_terminate_owned,
    )
    try:
        result = steering.intervene(
            package.package_id,
            "TERMINATE",
            reason="transient identity observation must be retried",
            session_id=launch.binding.session.session_id,
            intervention_id="intervention-transient-process-identity",
            workers=workers,
        )
    finally:
        workers.terminate_all()

    assert observations >= 2
    assert result.intervention.outcome == "TERMINATED"
    assert result.terminated_owned_process is True
    assert result.package_status == "CANCELLED"


@pytest.mark.parametrize(
    ("action", "expected_outcome", "expected_package_status"),
    [
        ("RESTART", "BLOCKED_STOPPED", "STOPPED"),
        ("TERMINATE", "TERMINATED", "CANCELLED"),
    ],
)
def test_stop_after_physical_termination_projects_terminal_without_relaunch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    expected_outcome: str,
    expected_package_status: str,
) -> None:
    database, package, _contract = _ready_package(
        tmp_path / action.lower()
    )
    stop_path = tmp_path / "STOP"
    gateway = WorkforceGateway(database, stop_path=stop_path)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    steering = WorkforceSteering(
        database,
        gateway=gateway,
        stop_path=stop_path,
    )
    original_terminate_owned = steering._terminate_owned

    def terminate_then_stop(
        session,
        supplied_workers,
        *,
        control_authority_check=None,
    ):
        result = original_terminate_owned(
            session,
            supplied_workers,
            control_authority_check=control_authority_check,
        )
        stop_path.write_text("owner stop after termination\n", encoding="utf-8")
        return result

    monkeypatch.setattr(
        steering,
        "_terminate_owned",
        terminate_then_stop,
    )
    try:
        result = steering.intervene(
            package.package_id,
            action,
            reason="STOP wins after exact physical termination",
            session_id=launch.binding.session.session_id,
            intervention_id=(
                f"intervention-{action.lower()}-stop-after-termination"
            ),
            workers=workers,
        )
        state = rebuild_workforce_state(database)
        assert result.intervention.outcome == expected_outcome
        assert result.terminated_owned_process is True
        assert result.package_status == expected_package_status
        assert state.sessions[
            launch.binding.session.session_id
        ].status == "TERMINATED"
        assert len(state.sessions_for(package.package_id)) == 1
        assert state.active_claims() == ()
        assert database.event_count(
            event_type="workforce.retry_prepared"
        ) == 0
        assert workers.status(
            launch.binding.session.run_id
        ).alive is False
        assert database.row(
            "leases",
            "lease_key",
            worker_lease_key(launch.binding.session.run_id),
        ) is None
        assert load_adapter_pids(
            adapter_pid_table_path(database)
        ).get(launch.binding.session.run_id) is None
    finally:
        workers.terminate_all()


@pytest.mark.parametrize(
    ("action", "hazard", "expected_outcome", "expected_status"),
    [
        (
            "REDIRECT",
            "acceptance",
            "BLOCKED_CANONICAL_ACCEPTANCE",
            "ACCEPTED",
        ),
        (
            "REDIRECT",
            "effect",
            "BLOCKED_EFFECT_RECONCILIATION",
            "WAITING",
        ),
        (
            "REDUCE_SCOPE",
            "acceptance",
            "BLOCKED_CANONICAL_ACCEPTANCE",
            "ACCEPTED",
        ),
        (
            "REDUCE_SCOPE",
            "effect",
            "BLOCKED_EFFECT_RECONCILIATION",
            "WAITING",
        ),
    ],
)
def test_replan_directive_rechecks_hazards_after_exact_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    hazard: str,
    expected_outcome: str,
    expected_status: str,
) -> None:
    database, package, _contract = _ready_package(
        tmp_path / f"{action.lower()}-{hazard}"
    )
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    steering = WorkforceSteering(database, gateway=gateway)
    original_terminate_owned = steering._terminate_owned
    injected = False

    def terminate_then_inject(
        session,
        supplied_workers,
        *,
        control_authority_check=None,
    ):
        nonlocal injected
        result = original_terminate_owned(
            session,
            supplied_workers,
            control_authority_check=control_authority_check,
        )
        if not injected:
            injected = True
            if hazard == "acceptance":
                _accept(
                    database,
                    package,
                    run_id=session.run_id,
                    verdict_id=f"verdict:{session.run_id}:directive",
                )
            else:
                database.append(Event.create(
                    "provider.effect_reconciliation_required",
                    {
                        "run_id": session.run_id,
                        "provider": "provider-a",
                        "effect_action_key": (
                            f"effect:{session.run_id}:directive"
                        ),
                        "possible_effect": True,
                    },
                    source_type="supervisor",
                    source_id="role-dispatcher",
                    idempotency_key=(
                        "provider.effect_reconciliation_required:"
                        f"{session.run_id}:directive"
                    ),
                ))
        return result

    monkeypatch.setattr(
        steering,
        "_terminate_owned",
        terminate_then_inject,
    )
    try:
        result = steering.intervene(
            package.package_id,
            action,
            reason="late hazards must suppress replanning directives",
            instruction="Return to the exact bounded package.",
            session_id=launch.binding.session.session_id,
            intervention_id=(
                f"intervention-{action.lower()}-{hazard}"
            ),
            workers=workers,
        )
    finally:
        workers.terminate_all()

    assert injected is True
    assert result.intervention.outcome == expected_outcome
    assert result.package_status == expected_status
    assert result.terminated_owned_process is True
    assert database.event_count(
        event_type="workforce.steering_directive_issued"
    ) == 0
    assert database.event_count(event_type="workforce.retry_prepared") == 0


@pytest.mark.parametrize(
    ("hazard", "expected_outcome", "expected_status"),
    [
        (
            "acceptance",
            "BLOCKED_CANONICAL_ACCEPTANCE",
            "ACCEPTED",
        ),
        (
            "effect",
            "BLOCKED_EFFECT_RECONCILIATION",
            "WAITING",
        ),
    ],
)
def test_retry_rechecks_predecessor_hazards_after_replacement_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hazard: str,
    expected_outcome: str,
    expected_status: str,
) -> None:
    database, package, _contract, gateway, first = _running_binding(
        tmp_path / hazard
    )
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=False,
    )
    injected = False
    original_admit = gateway._admit_launch

    def admit_after_late_hazard(
        replacement,
        *,
        launch_kind,
        reason,
        admission_authority=None,
        control_authority_check=None,
    ):
        nonlocal injected
        if not injected:
            injected = True
            predecessor_run_id = first.session.run_id
            if hazard == "acceptance":
                _accept(
                    database,
                    package,
                    run_id=predecessor_run_id,
                    verdict_id=(
                        f"verdict:{predecessor_run_id}:post-bind"
                    ),
                )
            else:
                database.append(Event.create(
                    "provider.effect_reconciliation_required",
                    {
                        "run_id": predecessor_run_id,
                        "provider": "provider-a",
                        "effect_action_key": (
                            f"effect:{predecessor_run_id}:post-bind"
                        ),
                        "possible_effect": True,
                    },
                    source_type="supervisor",
                    source_id="role-dispatcher",
                    idempotency_key=(
                        "provider.effect_reconciliation_required:"
                        f"{predecessor_run_id}:post-bind"
                    ),
                ))
        return original_admit(
            replacement,
            launch_kind=launch_kind,
            reason=reason,
            admission_authority=admission_authority,
            control_authority_check=control_authority_check,
        )

    monkeypatch.setattr(
        gateway,
        "_admit_launch",
        admit_after_late_hazard,
    )
    result = WorkforceSteering(
        database,
        gateway=gateway,
    ).intervene(
        package.package_id,
        "RESTART",
        reason="late predecessor hazard must fence relaunch admission",
        session_id=first.session.session_id,
        intervention_id=f"intervention-post-bind-{hazard}",
        mission_loop=mission_loop,
    )

    state = rebuild_workforce_state(database)
    latest = max(
        state.sessions_for(package.package_id),
        key=lambda item: item.attempt,
    )
    assert injected is True
    assert result.intervention.outcome == expected_outcome
    assert result.package_status == expected_status
    assert result.replacement_session_id == latest.session_id
    assert latest.attempt == 2
    assert latest.status == "TERMINATED"
    assert state.sessions[first.session.session_id].status == "TERMINATED"
    assert mission_loop.calls == []
    assert database.event_count(event_type="workforce.session_bound") == 2
    assert database.event_count(event_type="workforce.retry_prepared") == 1


def test_intervention_lease_loss_blocks_the_pending_directive_and_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        steering_module,
        "INTERVENTION_LEASE_RENEW_SECONDS",
        0.05,
    )
    database, package, _contract, gateway, _binding = _running_binding(
        tmp_path
    )
    entered = threading.Event()
    release = threading.Event()

    class BlockingDirectiveSteering(WorkforceSteering):
        def _directive(self, intervention, intervention_authority):
            entered.set()
            assert release.wait(timeout=5)
            return super()._directive(
                intervention,
                intervention_authority,
            )

    steering = BlockingDirectiveSteering(database, gateway=gateway)
    lease_key = f"workforce-intervention-package:{package.package_id}"
    intruder = "workforce-intervention:stolen-authority"
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                steering.intervene,
                package.package_id,
                "ESCALATE",
                reason="lease loss must stop the pending directive",
                intervention_id="intervention-lease-loss",
            )
            try:
                assert entered.wait(timeout=5)
                lease = database.row("leases", "lease_key", lease_key)
                assert lease is not None
                assert gateway._take_over_admission_lease(
                    lease_key,
                    observed_owner=str(lease["owner_id"]),
                    new_owner=intruder,
                )
            finally:
                release.set()
            with pytest.raises(
                WorkforceSteeringError,
                match="intervention authority",
            ):
                future.result(timeout=10)
    finally:
        release.set()
        gateway.leases.release(lease_key, intruder)

    state = rebuild_workforce_state(database)
    assert state.interventions[
        "intervention-lease-loss"
    ].outcome == "REQUESTED"
    assert database.event_count(
        event_type="workforce.steering_directive_issued"
    ) == 0
    assert database.event_count(
        event_type="workforce.intervention_completed"
    ) == 0


def test_continue_lease_loss_cannot_renew_session_or_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        steering_module,
        "INTERVENTION_LEASE_RENEW_SECONDS",
        0.05,
    )
    database, package, _contract, gateway, _binding = _running_binding(
        tmp_path
    )
    entered = threading.Event()
    release = threading.Event()
    original_heartbeat = gateway.heartbeat
    lease_key = f"workforce-intervention-package:{package.package_id}"
    intruder = "workforce-intervention:continue-theft"

    def pause_before_heartbeat(package_id, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original_heartbeat(package_id, **kwargs)

    monkeypatch.setattr(
        gateway,
        "heartbeat",
        pause_before_heartbeat,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                WorkforceSteering(
                    database,
                    gateway=gateway,
                ).intervene,
                package.package_id,
                "CONTINUE",
                reason="lost authority must block ownership renewal",
                intervention_id="intervention-continue-lease-loss",
            )
            try:
                assert entered.wait(timeout=5)
                lease = database.row("leases", "lease_key", lease_key)
                assert lease is not None
                assert gateway._take_over_admission_lease(
                    lease_key,
                    observed_owner=str(lease["owner_id"]),
                    new_owner=intruder,
                )
            finally:
                release.set()
            with pytest.raises(
                WorkforceSteeringError,
                match="intervention authority",
            ):
                future.result(timeout=10)
    finally:
        release.set()
        gateway.leases.release(lease_key, intruder)

    state = rebuild_workforce_state(database)
    assert state.interventions[
        "intervention-continue-lease-loss"
    ].outcome == "REQUESTED"
    assert database.event_count(
        event_type="workforce.session_heartbeat"
    ) == 0
    assert database.event_count(
        event_type="workforce.resource_renewed"
    ) == 0


def test_intervention_lease_loss_during_process_termination_stops_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        steering_module,
        "INTERVENTION_LEASE_RENEW_SECONDS",
        0.05,
    )
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)
    process = launch.worker_status.process
    assert process is not None
    steering = WorkforceSteering(database, gateway=gateway)
    entered = threading.Event()
    release = threading.Event()
    original_terminate_epoch = gateway._terminate_process_epoch
    lease_key = f"workforce-intervention-package:{package.package_id}"
    intruder = "workforce-intervention:termination-theft"
    session_events_before = database.event_count(
        event_type="workforce.session_state_changed"
    )
    package_events_before = database.event_count(
        event_type="workforce.package_state_changed"
    )

    def pause_before_exact_kill(
        epoch,
        *,
        control_authority_check=None,
    ):
        assert control_authority_check is not None
        entered.set()
        assert release.wait(timeout=5)
        control_authority_check()
        return original_terminate_epoch(
            epoch,
            control_authority_check=control_authority_check,
        )

    monkeypatch.setattr(
        gateway,
        "_terminate_process_epoch",
        pause_before_exact_kill,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                steering.intervene,
                package.package_id,
                "TERMINATE",
                reason="lost intervention authority must stop the exact kill",
                session_id=launch.binding.session.session_id,
                intervention_id="intervention-termination-lease-loss",
                workers=workers,
            )
            try:
                assert entered.wait(timeout=5)
                lease = database.row("leases", "lease_key", lease_key)
                assert lease is not None
                assert gateway._take_over_admission_lease(
                    lease_key,
                    observed_owner=str(lease["owner_id"]),
                    new_owner=intruder,
                )
            finally:
                release.set()
            with pytest.raises(
                WorkforceSteeringError,
                match="intervention authority",
            ):
                future.result(timeout=10)

        assert process.poll() is None
        state = rebuild_workforce_state(database)
        assert state.package(package.package_id).status == "RUNNING"
        assert state.sessions[
            launch.binding.session.session_id
        ].status == "RUNNING"
        assert state.interventions[
            "intervention-termination-lease-loss"
        ].outcome == "REQUESTED"
        assert database.event_count(
            event_type="workforce.session_state_changed"
        ) == session_events_before
        assert database.event_count(
            event_type="workforce.package_state_changed"
        ) == package_events_before
    finally:
        release.set()
        gateway.leases.release(lease_key, intruder)
        workers.terminate_all()


def test_conflicting_intervention_completion_fails_closed(
    tmp_path: Path,
) -> None:
    database, package, _contract, gateway, _binding = _running_binding(
        tmp_path
    )
    result = WorkforceSteering(
        database,
        gateway=gateway,
    ).intervene(
        package.package_id,
        "ESCALATE",
        reason="record the immutable terminal intervention result",
        intervention_id="intervention-immutable-completion",
    )
    conflicting = replace(
        result.intervention,
        outcome="WAITING",
    )
    database.append(Event.create(
        "workforce.intervention_completed",
        conflicting.to_payload(),
        source_type="supervisor",
        source_id="adversarial-completion",
        correlation_id=package.package_id,
        idempotency_key=(
            "workforce.intervention_completed:"
            "intervention-immutable-completion:conflict"
        ),
    ))

    with pytest.raises(
        WorkforceStateError,
        match="conflicting terminal workforce intervention outcomes",
    ):
        rebuild_workforce_state(database)


@pytest.mark.parametrize("action", ["CONTINUE", "WAIT"])
def test_continue_and_wait_cannot_mutate_a_replacement_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    database, package, _contract, gateway, first = _running_binding(
        tmp_path / action.lower()
    )
    steering = WorkforceSteering(database, gateway=gateway)
    replacement: dict[str, object] = {}

    def install_replacement() -> None:
        if replacement:
            return
        current = rebuild_workforce_state(database).sessions[
            first.session.session_id
        ]
        gateway._mark_session(current, "TERMINATED")
        gateway.leases.release(
            first.resource_lease_key,
            first.session.run_id,
        )
        transition_work_package(
            database,
            package.package_id,
            "FAILED",
            reason="race fixture retired the first attempt",
        )
        transition_work_package(
            database,
            package.package_id,
            "READY",
            reason="race fixture prepared the replacement",
        )
        second = gateway.bind(package.package_id)
        replacement["binding"] = gateway._admit_launch(
            second,
            launch_kind="INLINE_MISSION_LOOP",
            reason="race fixture admitted the replacement",
        )

    if action == "CONTINUE":
        original_heartbeat = gateway.heartbeat

        def heartbeat_after_replacement(package_id, **kwargs):
            install_replacement()
            return original_heartbeat(package_id, **kwargs)

        monkeypatch.setattr(
            gateway,
            "heartbeat",
            heartbeat_after_replacement,
        )
    else:
        original_wait = steering._move_to_waiting

        def wait_after_replacement(requested, intervention_authority):
            install_replacement()
            return original_wait(requested, intervention_authority)

        monkeypatch.setattr(
            steering,
            "_move_to_waiting",
            wait_after_replacement,
        )

    with pytest.raises(
        (WorkforceGatewayError, WorkforceSteeringError),
        match="superseded",
    ):
        steering.intervene(
            package.package_id,
            action,
            reason="the action must remain fenced to the first attempt",
            session_id=first.session.session_id,
            intervention_id=f"intervention-{action.lower()}-replacement-race",
        )

    second = replacement["binding"]
    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "RUNNING"
    assert state.sessions[second.session.session_id].status == "RUNNING"
    assert state.sessions[first.session.session_id].status == "TERMINATED"
    assert database.event_count(
        event_type="workforce.session_heartbeat"
    ) == 0
    waiting_events = [
        event
        for event in database.events(
            event_types=("workforce.package_state_changed",),
            readonly=True,
        )
        if event.payload.get("status") == "WAITING"
    ]
    assert waiting_events == []
