from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

import jarvis_foundry.workforce.gateway as gateway_module
import jarvis_foundry.workforce.runner as runner_module
from jarvis_foundry.clock import isoformat, parse, utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    record_step_contract,
)
from jarvis_foundry.mission.loop import FAILED, PASSED, StepOutcome
from jarvis_foundry.supervisor.worker import (
    WorkerController,
    adapter_pid_table_path,
    load_adapter_pids,
    terminate_owned_tree,
    worker_lease_key,
)
from jarvis_foundry.workforce import (
    WorkPackage,
    WorktreeManager,
    WorkerSession,
    create_work_package,
    rebuild_workforce_state,
    transition_work_package,
)
from jarvis_foundry.workforce.commander import WorkforceCommander
from jarvis_foundry.workforce.gateway import (
    WorkforceGateway,
    WorkforceGatewayError,
)
from jarvis_foundry.workforce.runner import run_package
from jarvis_foundry.workforce.state import WorkforceStateError
from jarvis_foundry.workforce.steering import WorkforceSteering


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "source"
    repository.mkdir(parents=True)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "runner@example.invalid")
    _git(repository, "config", "user.name", "Workforce Runner")
    (repository / "src").mkdir()
    (repository / "src" / "base.py").write_text(
        "BASE = True\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "base")
    return repository, _git(repository, "rev-parse", "HEAD")


def _ready_package(
    tmp_path: Path,
    *,
    duration_seconds: int = 900,
) -> tuple[FoundryDatabase, WorkPackage, MissionStepContract]:
    repository, source_sha = _repository(tmp_path)
    home = tmp_path / "home"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {
            "received_at": "2026-09-04T00:00:00Z",
            "standing_mandate": True,
        },
        source_type="owner",
        source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    package_id = "package-sealed-runner"
    resource_id = f"workforce-resource:{package_id}"
    contract = MissionStepContract(
        mission_id="mission-sealed-runner",
        step_id="step-sealed-runner",
        version=1,
        resource_id=resource_id,
        capability="write_registered_worktree_files",
        target_relative_path="src/result.txt",
        instructions=(
            "Create src/result.txt through the bounded workforce session."
        ),
        expected_effects=("src/result.txt is created and verified",),
        rollback={"method": "git_reset_soft", "reversible": True},
        verification_criteria=(
            "the artifact exists in the package worktree",
            "an independent verifier accepts its exact bytes",
        ),
        expected_artifacts=("src/result.txt",),
        forbidden_paths=("live",),
    )
    package = WorkPackage(
        package_id=package_id,
        parent_objective_id="objective-sealed-runner",
        mission_id=contract.mission_id,
        step_id=contract.step_id,
        role="builder",
        capability=contract.capability,
        bounded_goal="Prove the sealed package runner end to end.",
        acceptance_criteria=contract.verification_criteria,
        source_sha=source_sha,
        repository_id="sealed-runner-fixture",
        branch_name="candidate/sealed-runner",
        worktree_path=str(tmp_path / "worktrees" / package_id),
        allowed_paths=("src/result.txt", ".jarvis-owned-worker.pid"),
        forbidden_paths=contract.forbidden_paths,
        mission_step_contract_id=contract.contract_id,
        mission_step_contract_hash=contract.content_hash(),
        expected_artifacts=contract.expected_artifacts,
        required_tests=("git diff --check",),
        duration_seconds=duration_seconds,
        lease_seconds=120,
    )
    create_work_package(database, package)
    WorktreeManager(database).prepare(
        package.package_id,
        source_repository=repository,
    )
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": package.mission_id,
            "objective": package.bounded_goal,
            "resource_id": resource_id,
            "state": "PLANNED",
            "acceptance_criteria": list(package.acceptance_criteria),
            "next_action": contract.instructions,
        },
        source_type="test",
        source_id="v13-workforce-runner",
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
            source_type="test",
            source_id="v13-workforce-runner",
            idempotency_key=(
                f"mission.state_changed:{package.mission_id}:{status}"
            ),
        ))
    record_step_contract(database, contract)
    return database, package, contract


def test_sealed_runner_executes_canonical_package_in_owned_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    config = tmp_path / "worker.yaml"
    config.write_text(
        "\n".join([
            "enabled: true",
            "status: ACTIVE",
            "lab_scripted_hermes: true",
            f"runtime_state_root: {tmp_path / 'runtime'}",
            "verification:",
            "  max_repairs: 1",
            "",
        ]),
        encoding="utf-8",
    )
    repository_src = Path(__file__).resolve().parents[2] / "src"
    inherited = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            part
            for part in (str(repository_src), inherited)
            if part
        ),
    )
    workers = WorkerController(database, lease_seconds=120)
    gateway = WorkforceGateway(
        database,
        worker_config_path=config,
    )
    launch = gateway.spawn(package.package_id, workers)
    process = launch.worker_status.process
    assert launch.spawned is True
    assert process is not None

    try:
        assert process.wait(timeout=60) == 0
    finally:
        if process.poll() is None:
            workers.terminate(launch.binding.session.run_id)
        workers.reap()

    state = rebuild_workforce_state(database)
    package_state = state.package(package.package_id)
    session = state.sessions_for(package.package_id)[0]
    worktree = Path(package.worktree_path)
    artifact = worktree / "src" / "result.txt"

    assert package_state.status == "ACCEPTED"
    assert session.status == "COMPLETED"
    assert session.result["canonical_outcome"] == "PASSED"
    process_identity = session.result["process_creation_identity"]
    assert process_identity["pid"] == process.pid
    assert int(process_identity["create_time"]) > 0
    assert artifact.read_text(encoding="utf-8") == (
        "Objective report\nVERIFIED_BY executive-worker\n"
    )
    assert _git(worktree, "status", "--porcelain=v1") == ""
    assert _git(worktree, "rev-parse", "HEAD") != package.source_sha
    assert database.event_count(
        event_type="verification.accepted",
        readonly=True,
    ) == 1
    assert state.active_claims() == ()


def test_sealed_runner_preserves_gateway_pid_authority_during_nested_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    config = tmp_path / "worker-crash.yaml"
    config.write_text(
        "\n".join([
            "enabled: true",
            "status: ACTIVE",
            "lab_scripted_hermes: true",
            "lab_crash_after_write: true",
            "lab_crash_step_id: step-sealed-runner",
            f"runtime_state_root: {tmp_path / 'runtime'}",
            "",
        ]),
        encoding="utf-8",
    )
    repository_src = Path(__file__).resolve().parents[2] / "src"
    inherited = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            part
            for part in (str(repository_src), inherited)
            if part
        ),
    )
    workers = WorkerController(database, lease_seconds=120)
    gateway = WorkforceGateway(
        database,
        worker_config_path=config,
    )
    launch = gateway.spawn(package.package_id, workers)
    process = launch.worker_status.process
    assert process is not None
    marker = Path(package.worktree_path) / ".jarvis-owned-worker.pid"
    private_root = Path(database.path).resolve().parent / "workforce-adapter-pids"
    deadline = time.monotonic() + 20
    private_records: dict[str, dict[str, object]] = {}
    while time.monotonic() < deadline:
        private_tables = tuple(private_root.glob("*.json"))
        if marker.is_file() and private_tables:
            private_records = load_adapter_pids(private_tables[0])
            if f"lab-crash:{launch.binding.session.run_id}" in private_records:
                break
        time.sleep(0.05)
    else:
        pytest.fail("nested adapter PID registration did not become observable")

    canonical_records = load_adapter_pids(adapter_pid_table_path(database))
    canonical = canonical_records[launch.binding.session.run_id]
    assert canonical["pid"] == process.pid
    assert canonical["worker_type"] == "workforce-canonical-mission"
    nested = private_records[f"lab-crash:{launch.binding.session.run_id}"]
    assert nested["pid"] != process.pid
    assert launch.binding.session.run_id not in private_records

    terminate_owned_tree(process.pid, process=process)
    process.wait(timeout=10)
    restarted_workers = WorkerController(database, lease_seconds=120)
    restart = WorkforceCommander(
        database,
        workers=restarted_workers,
    ).audit_restart(
        package_id=package.package_id,
        now=isoformat(utc_now()),
    )
    recovered = restart.package(package.package_id).sessions[0]
    assert recovered.category == "DEAD"
    assert recovered.process_backed is True
    assert recovered.process_identity_valid is True
    assert recovered.process_alive is False

    reconciled = WorkforceCommander(
        database,
        workers=restarted_workers,
    ).reconcile_restart(package_id=package.package_id)
    assert [item.outcome for item in reconciled.actions] == ["RECONCILED"]
    assert (
        reconciled.after.package(package.package_id).sessions[0].category
        == "TERMINAL"
    )

    workers.detach(launch.binding.session.run_id)
    restarted_workers.terminate_all()


def test_spawn_registration_cleanup_fails_closed_when_pid_state_survives(
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
    captured: dict[str, object] = {}
    original_detach = workers.detach

    def fail_registration(
        binding,
        status,
        _workers,
        *,
        admission_authority,
    ):
        captured["run_id"] = binding.session.run_id
        captured["process"] = status.process
        raise RuntimeError("simulated registration failure")

    monkeypatch.setattr(
        gateway,
        "_record_process_registration",
        fail_registration,
    )
    monkeypatch.setattr(workers, "detach", lambda _run_id: None)
    try:
        with pytest.raises(
            WorkforceGatewayError,
            match="cleanup was not proven",
        ):
            gateway.spawn(package.package_id, workers)
        run_id = str(captured["run_id"])
        process = captured["process"]
        assert process is not None
        assert process.wait(timeout=10) is not None
        assert workers.status(run_id).alive is False
        assert database.row(
            "leases",
            "lease_key",
            worker_lease_key(run_id),
        ) is None
        assert run_id not in workers._leased
        assert load_adapter_pids(
            adapter_pid_table_path(database)
        ).get(run_id) is not None
        state = rebuild_workforce_state(database)
        assert state.package(package.package_id).status == "RUNNING"
        assert state.sessions_for(package.package_id)[0].status == "RUNNING"
        assert database.event_count(
            event_type="workforce.result_linked"
        ) == 0
    finally:
        monkeypatch.setattr(workers, "detach", original_detach)
        if captured.get("run_id") is not None:
            original_detach(str(captured["run_id"]))
        workers.terminate_all()


def test_stop_after_failed_spawn_cleanup_projects_stopped_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
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
    captured: dict[str, object] = {}
    original_cleanup = gateway._cleanup_failed_spawn

    def fail_registration(
        binding,
        status,
        _workers,
        *,
        admission_authority,
    ):
        captured["run_id"] = binding.session.run_id
        captured["process"] = status.process
        raise RuntimeError("simulated registration failure before STOP")

    def cleanup_then_stop(
        run_id,
        supplied_workers,
        *,
        spawned_status,
    ):
        original_cleanup(
            run_id,
            supplied_workers,
            spawned_status=spawned_status,
        )
        stop_path.write_text(
            "owner stop after physical cleanup\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        gateway,
        "_record_process_registration",
        fail_registration,
    )
    monkeypatch.setattr(
        gateway,
        "_cleanup_failed_spawn",
        cleanup_then_stop,
    )
    try:
        with pytest.raises(
            RuntimeError,
            match="simulated registration failure",
        ):
            gateway.spawn(package.package_id, workers)
        run_id = str(captured["run_id"])
        process = captured["process"]
        assert process is not None
        assert process.wait(timeout=10) is not None
        state = rebuild_workforce_state(database)
        assert state.package(package.package_id).status == "STOPPED"
        assert state.sessions_for(package.package_id)[0].status == "STOPPED"
        assert state.active_claims() == ()
        assert database.row(
            "leases",
            "lease_key",
            worker_lease_key(run_id),
        ) is None
        assert run_id not in workers._leased
        assert load_adapter_pids(
            adapter_pid_table_path(database)
        ).get(run_id) is None
    finally:
        workers.terminate_all()


def test_sealed_runner_rejects_superseded_attempt_identity(
    tmp_path: Path,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    first = gateway.bind(package.package_id)
    gateway._admit_launch(
        first,
        launch_kind="SEALED_PACKAGE_RUNNER",
        reason="first attempt admitted",
    )
    second = WorkerSession(
        session_id=f"workforce:{package.package_id}:a2",
        package_id=package.package_id,
        run_id=f"workforce:{package.package_id}:a2",
        attempt=2,
        framework="PENDING",
        provider="PENDING",
        model="PENDING",
        status="RUNNING",
        result=dict(first.session.result),
    )
    database.append(Event.create(
        "workforce.session_bound",
        second.to_payload(),
        source_type="supervisor",
        source_id="test-suite",
        idempotency_key=f"workforce.session_bound:{second.session_id}",
    ))
    config = tmp_path / "worker.yaml"
    config.write_text(
        "enabled: true\nstatus: ACTIVE\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="superseded package attempt"):
        run_package(
            home=Path(database.path).parent,
            package_id=package.package_id,
            run_id=first.session.run_id,
            config_path=config,
        )


def test_sealed_runner_cannot_rebind_after_initial_attempt_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    first = gateway.bind(package.package_id)
    first = gateway._admit_launch(
        first,
        launch_kind="SEALED_PACKAGE_RUNNER",
        reason="first attempt admitted before stack construction",
    )
    config = tmp_path / "worker.yaml"
    config.write_text(
        "enabled: true\nstatus: ACTIVE\n",
        encoding="utf-8",
    )
    executed: list[str] = []

    class DummyStack:
        def close(self) -> None:
            return None

    class ForbiddenLoop:
        def run_step(self, **kwargs):
            executed.append(str(kwargs["run_id"]))
            raise AssertionError("superseded runner reached MissionLoop")

    def rebind_during_stack_construction(*_args, **_kwargs):
        second = WorkerSession(
            session_id=f"workforce:{package.package_id}:a2",
            package_id=package.package_id,
            run_id=f"workforce:{package.package_id}:a2",
            attempt=2,
            framework="PENDING",
            provider="PENDING",
            model="PENDING",
            status="RUNNING",
            result=dict(first.session.result),
        )
        database.append(Event.create(
            "workforce.session_bound",
            second.to_payload(),
            source_type="supervisor",
            source_id="attempt-race-probe",
            idempotency_key=f"workforce.session_bound:{second.session_id}",
        ))
        return DummyStack()

    monkeypatch.setattr(
        runner_module,
        "build_worker_stack",
        rebind_during_stack_construction,
    )
    monkeypatch.setattr(
        runner_module,
        "_pin_routing_to_hermes",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        runner_module,
        "build_canonical_mission_loop",
        lambda *_args, **_kwargs: ForbiddenLoop(),
    )

    with pytest.raises(
        WorkforceGatewayError,
        match="rebound to another workforce attempt",
    ):
        run_package(
            home=Path(database.path).parent,
            package_id=package.package_id,
            run_id=first.session.run_id,
            config_path=config,
        )

    assert executed == []
    state = rebuild_workforce_state(database)
    assert max(
        state.sessions_for(package.package_id),
        key=lambda item: item.attempt,
    ).attempt == 2


def test_inline_runner_launch_fence_rejects_uncontrollable_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(
        tmp_path,
        duration_seconds=86_400,
    )
    runner_gateway = WorkforceGateway(database)
    first = runner_gateway.bind(package.package_id)
    first = runner_gateway._admit_launch(
        first,
        launch_kind="INLINE_MISSION_LOOP",
        reason="first attempt admitted before the dispatch race",
    )
    steering = WorkforceSteering(database, gateway=runner_gateway)
    first_admitted = threading.Event()
    intervention_finished = threading.Event()
    effects: list[str] = []
    lease_horizons: list[float] = []
    renew_durations: list[int] = []
    intervention_errors: list[BaseException] = []
    original_admit = runner_gateway._admit_launch
    original_expected_binding = runner_gateway._binding_for_expected_run
    original_renew = runner_gateway.leases.renew
    expected_binding_checks = 0

    def pause_after_first_admission(
        binding,
        *,
        launch_kind,
        reason,
        admission_authority=None,
        control_authority_check=None,
    ):
        admitted = original_admit(
            binding,
            launch_kind=launch_kind,
            reason=reason,
            admission_authority=admission_authority,
            control_authority_check=control_authority_check,
        )
        if admitted.session.run_id == first.session.run_id:
            first_admitted.set()
            assert intervention_finished.wait(timeout=5)
        return admitted

    def observe_renewal(
        lease_key: str,
        owner_id: str,
        duration_seconds: int,
    ) -> None:
        original_renew(lease_key, owner_id, duration_seconds)
        renew_durations.append(duration_seconds)

    def observe_expected_binding(
        package_id: str,
        expected_run_id: str,
        *,
        allow_unadmitted: bool = False,
    ):
        nonlocal expected_binding_checks
        expected_binding_checks += 1
        if expected_binding_checks == 2:
            lease_key = f"workforce-admission:{package_id}"
            initial = database.row("leases", "lease_key", lease_key)
            assert initial is not None
            assert runner_gateway._execution_guard_owner_matches(
                str(initial["owner_id"]),
                expected_run_id,
            )
            lease_horizons.append(
                (parse(str(initial["expires_at"])) - utc_now()).total_seconds()
            )
            renewal_count = len(renew_durations)
            wait_deadline = time.monotonic() + 2
            while len(renew_durations) <= renewal_count:
                assert time.monotonic() < wait_deadline
                time.sleep(0.01)
            renewed = database.row("leases", "lease_key", lease_key)
            assert renewed is not None
            assert renewed["heartbeat_at"] != initial["heartbeat_at"]
            lease_horizons.append(
                (parse(str(renewed["expires_at"])) - utc_now()).total_seconds()
            )
        return original_expected_binding(
            package_id,
            expected_run_id,
            allow_unadmitted=allow_unadmitted,
        )

    class EffectRecordingLoop:
        def run_step(self, **kwargs):
            effects.append(str(kwargs["run_id"]))
            return StepOutcome(
                outcome=FAILED,
                step_id=package.step_id,
                run_id=str(kwargs["run_id"]),
                reason="stale dispatch probe",
            )

    def reject_uncontrollable_restart() -> None:
        try:
            assert first_admitted.wait(timeout=5)
            with pytest.raises(
                WorkforceGatewayError,
                match="active inline workforce execution cannot be preempted",
            ) as raised:
                steering.intervene(
                    package.package_id,
                    "RESTART",
                    reason="inline execution has no controllable process",
                    session_id=first.session.session_id,
                    intervention_id="intervention-post-admission-runner-race",
                )
            intervention_errors.append(raised.value)
        finally:
            intervention_finished.set()

    monkeypatch.setattr(
        runner_gateway,
        "_admit_launch",
        pause_after_first_admission,
    )
    monkeypatch.setattr(
        runner_gateway,
        "_binding_for_expected_run",
        observe_expected_binding,
    )
    monkeypatch.setattr(
        runner_gateway.leases,
        "renew",
        observe_renewal,
    )
    monkeypatch.setattr(
        gateway_module,
        "_ADMISSION_LEASE_RENEW_SECONDS",
        0.05,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        intervention = executor.submit(reject_uncontrollable_restart)
        result = runner_gateway.run_inline(
            package.package_id,
            EffectRecordingLoop(),
            expected_run_id=first.session.run_id,
        )
        intervention.result(timeout=10)

    assert len(intervention_errors) == 1
    assert effects == [first.session.run_id]
    assert expected_binding_checks == 2
    assert renew_durations
    assert set(renew_durations) == {gateway_module._ADMISSION_LEASE_SECONDS}
    assert len(lease_horizons) == 2
    assert all(
        0 < horizon <= gateway_module._ADMISSION_LEASE_SECONDS + 1
        for horizon in lease_horizons
    )
    assert database.row(
        "leases",
        "lease_key",
        f"workforce-admission:{package.package_id}",
    ) is None
    state = rebuild_workforce_state(database)
    sessions = state.sessions_for(package.package_id)
    assert len(sessions) == 1
    assert sessions[0].session_id == first.session.session_id
    assert sessions[0].status == "FAILED"
    assert state.package(package.package_id).status == "FAILED"
    assert result.package_status == "FAILED"
    assert state.interventions[
        "intervention-post-admission-runner-race"
    ].outcome == "FAILED_WORKFORCEGATEWAYERROR"


def test_inline_runner_losing_execution_authority_cannot_link_passed_outcome(
    tmp_path: Path,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    binding = gateway._admit_launch(
        binding,
        launch_kind="INLINE_MISSION_LOOP",
        reason="admit the exact attempt before authority-loss proof",
    )
    entered = threading.Event()
    release = threading.Event()
    admission_key = f"workforce-admission:{package.package_id}"
    result_events_before = database.event_count(
        event_type="workforce.result_linked"
    )

    class BlockingPassingLoop:
        def run_step(self, **kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return StepOutcome(
                outcome=PASSED,
                step_id=package.step_id,
                run_id=str(kwargs["run_id"]),
                reason="authority-loss result must not link",
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        execution = executor.submit(
            gateway.run_inline,
            package.package_id,
            BlockingPassingLoop(),
            expected_run_id=binding.session.run_id,
        )
        try:
            assert entered.wait(timeout=5)
            execution_lease = database.row(
                "leases",
                "lease_key",
                admission_key,
            )
            assert execution_lease is not None
            assert gateway._execution_guard_owner_matches(
                str(execution_lease["owner_id"]),
                binding.session.run_id,
            )
            stolen_owner = "workforce-control:authority-loss-proof"
            assert gateway._take_over_admission_lease(
                admission_key,
                observed_owner=str(execution_lease["owner_id"]),
                new_owner=stolen_owner,
            )
            gateway._mark_session(binding.session, "TERMINATED")
            gateway.leases.release(
                binding.resource_lease_key,
                binding.session.run_id,
            )
            transition_work_package(
                database,
                package.package_id,
                "FAILED",
                reason="control takeover closed the stale first attempt",
            )
            transition_work_package(
                database,
                package.package_id,
                "READY",
                reason="control takeover made the replacement admissible",
            )
            assert gateway.leases.release(admission_key, stolen_owner)
            second = gateway.bind(package.package_id)
            second = gateway._admit_launch(
                second,
                launch_kind="INLINE_MISSION_LOOP",
                reason="replacement attempt admitted before stale terminal result",
            )
        finally:
            release.set()
        with pytest.raises(
            WorkforceGatewayError,
            match="authority was lost before durable result linkage",
        ):
            execution.result(timeout=10)

    assert database.event_count(
        event_type="workforce.result_linked"
    ) == result_events_before
    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "RUNNING"
    assert state.sessions[binding.session.session_id].status == "TERMINATED"
    assert state.sessions[second.session.session_id].status == "RUNNING"
    assert state.active_claims()[0].session_id == second.session.run_id
    assert database.row("leases", "lease_key", admission_key) is None


def test_inline_runner_heartbeat_failure_cannot_link_passed_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    binding = gateway._admit_launch(
        binding,
        launch_kind="INLINE_MISSION_LOOP",
        reason="admit the exact attempt before heartbeat-loss proof",
    )
    renewal_failed = threading.Event()
    result_events_before = database.event_count(
        event_type="workforce.result_linked"
    )

    def fail_renewal(
        _lease_key: str,
        _owner_id: str,
        _duration_seconds: int,
    ) -> None:
        renewal_failed.set()
        raise RuntimeError("simulated admission heartbeat failure")

    class PassingAfterRenewalFailure:
        def run_step(self, **kwargs):
            assert renewal_failed.wait(timeout=5)
            return StepOutcome(
                outcome=PASSED,
                step_id=package.step_id,
                run_id=str(kwargs["run_id"]),
                reason="failed heartbeat result must not link",
            )

    monkeypatch.setattr(
        gateway_module,
        "_ADMISSION_LEASE_RENEW_SECONDS",
        0.05,
    )
    monkeypatch.setattr(gateway.leases, "renew", fail_renewal)

    with pytest.raises(
        WorkforceGatewayError,
        match="heartbeat failed before durable result linkage",
    ):
        gateway.run_inline(
            package.package_id,
            PassingAfterRenewalFailure(),
            expected_run_id=binding.session.run_id,
        )

    assert database.event_count(
        event_type="workforce.result_linked"
    ) == result_events_before
    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "RUNNING"
    assert state.sessions[binding.session.session_id].status == "RUNNING"


def test_heartbeat_rejects_a_replaced_execution_epoch(
    tmp_path: Path,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    binding = gateway._admit_launch(
        binding,
        launch_kind="INLINE_MISSION_LOOP",
        reason="admit exact heartbeat epoch",
    )
    admission_key = f"workforce-admission:{package.package_id}"
    execution_owner = (
        gateway._execution_guard_owner_prefix(binding.session.run_id)
        + "heartbeat-epoch"
    )
    intruder = "workforce-control:heartbeat-epoch-replaced"
    gateway.leases.acquire(admission_key, execution_owner, 30)
    assert gateway._take_over_admission_lease(
        admission_key,
        observed_owner=execution_owner,
        new_owner=intruder,
    )

    try:
        with pytest.raises(
            WorkforceGatewayError,
            match="execution authority changed before heartbeat",
        ):
            gateway.heartbeat(package.package_id)
    finally:
        gateway.leases.release(admission_key, intruder)

    state = rebuild_workforce_state(database)
    assert state.sessions[binding.session.session_id].status == "RUNNING"
    assert database.event_count(
        event_type="workforce.session_heartbeat"
    ) == 0
    assert database.event_count(
        event_type="workforce.resource_renewed"
    ) == 0


@pytest.mark.parametrize("terminal_kind", ["result", "crash"])
def test_late_terminal_linkage_cannot_reopen_a_preempted_attempt(
    tmp_path: Path,
    terminal_kind: str,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    binding = gateway._admit_launch(
        binding,
        launch_kind="INLINE_MISSION_LOOP",
        reason="admit attempt before preemption",
    )
    gateway._mark_session(binding.session, "TERMINATED")
    transition_work_package(
        database,
        package.package_id,
        "CANCELLED",
        reason="governed preemption closed the attempt",
    )

    with pytest.raises(
        WorkforceGatewayError,
        match="terminal workforce attempt",
    ):
        if terminal_kind == "result":
            gateway.link_outcome(
                package.package_id,
                StepOutcome(
                    outcome=FAILED,
                    step_id=str(package.step_id),
                    run_id=binding.session.run_id,
                    reason="late stale outcome",
                ),
            )
        else:
            gateway._record_crash(
                binding,
                RuntimeError("late stale crash"),
            )

    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "CANCELLED"
    assert state.sessions[binding.session.session_id].status == "TERMINATED"
    assert database.event_count(event_type="workforce.result_linked") == 0


@pytest.mark.parametrize("terminal_kind", ["result", "crash"])
def test_terminal_linkage_and_package_projection_share_admission_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_kind: str,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    projection_entered = threading.Event()
    projection_release = threading.Event()
    original_transition = (
        gateway_module._transition_work_package_on_connection
    )

    def pause_package_projection(*args, **kwargs):
        if len(args) >= 4 and args[3] == "FAILED":
            projection_entered.set()
            assert projection_release.wait(timeout=5)
        return original_transition(*args, **kwargs)

    class TerminalLoop:
        def run_step(self, **kwargs):
            if terminal_kind == "crash":
                raise RuntimeError("atomic crash projection probe")
            return StepOutcome(
                outcome=FAILED,
                step_id=package.step_id,
                run_id=str(kwargs["run_id"]),
                reason="atomic result projection probe",
            )

    monkeypatch.setattr(
        gateway_module,
        "_transition_work_package_on_connection",
        pause_package_projection,
    )
    admission_key = f"workforce-admission:{package.package_id}"
    intruder = "workforce-control:atomic-projection-probe"

    with ThreadPoolExecutor(max_workers=2) as executor:
        execution = executor.submit(
            gateway.run_inline,
            package.package_id,
            TerminalLoop(),
        )
        assert projection_entered.wait(timeout=5)
        execution_lease = database.row(
            "leases",
            "lease_key",
            admission_key,
        )
        assert execution_lease is not None
        observed_owner = str(execution_lease["owner_id"])
        takeover = executor.submit(
            gateway._take_over_admission_lease,
            admission_key,
            observed_owner=observed_owner,
            new_owner=intruder,
        )
        time.sleep(0.1)
        assert takeover.done() is False
        projection_release.set()
        result = execution.result(timeout=10)
        assert takeover.result(timeout=10) is True

    try:
        state = rebuild_workforce_state(database)
        assert result.package_status == "FAILED"
        assert state.package(package.package_id).status == "FAILED"
        assert state.sessions[result.binding.session.session_id].status == "FAILED"
        if terminal_kind == "crash":
            assert state.sessions[
                result.binding.session.session_id
            ].result["error_class"] == "RuntimeError"
        workforce_events = [
            event
            for event in database.events(
                event_type_prefixes=("workforce.",),
                readonly=True,
            )
            if event.correlation_id == package.package_id
        ]
        result_index = next(
            index
            for index, event in enumerate(workforce_events)
            if event.event_type == "workforce.result_linked"
        )
        assert workforce_events[result_index + 1].event_type == (
            "workforce.package_state_changed"
        )
        assert workforce_events[result_index + 1].payload["status"] == "FAILED"
    finally:
        gateway.leases.release(admission_key, intruder)


def test_stale_sealed_runner_cannot_admit_its_own_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _contract = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    first = gateway.bind(package.package_id)
    first = gateway._admit_launch(
        first,
        launch_kind="SEALED_PACKAGE_RUNNER",
        reason="first attempt admitted before retry preparation",
    )
    config = tmp_path / "worker.yaml"
    config.write_text(
        "enabled: true\nstatus: ACTIVE\n",
        encoding="utf-8",
    )

    class DummyStack:
        def close(self) -> None:
            return None

    def prepare_retry_without_binding(*_args, **_kwargs):
        gateway._mark_session(first.session, "TERMINATED")
        gateway.leases.release(
            first.resource_lease_key,
            first.session.run_id,
        )
        transition_work_package(
            database,
            package.package_id,
            "FAILED",
            reason="retry preparation closed the first exact attempt",
        )
        transition_work_package(
            database,
            package.package_id,
            "READY",
            reason="retry preparation awaits a new owner",
        )
        return DummyStack()

    monkeypatch.setattr(
        runner_module,
        "build_worker_stack",
        prepare_retry_without_binding,
    )
    monkeypatch.setattr(
        runner_module,
        "_pin_routing_to_hermes",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        runner_module,
        "build_canonical_mission_loop",
        lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(
        WorkforceGatewayError,
        match="exact workforce attempt is no longer executable",
    ):
        run_package(
            home=Path(database.path).parent,
            package_id=package.package_id,
            run_id=first.session.run_id,
            config_path=config,
        )

    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "READY"
    assert len(state.sessions_for(package.package_id)) == 1
    assert database.event_count(event_type="workforce.session_bound") == 1


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("terminal-reopen", "terminal workforce session is immutable"),
        ("terminal-version-advance", "terminal workforce session is immutable"),
        ("identity-change", "session event changes immutable identity"),
        ("version-gap", "session event expected version mismatch"),
    ],
)
def test_session_reducer_rejects_terminal_reopen_identity_change_and_version_gap(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    database, package, _contract = _ready_package(
        tmp_path / mutation
    )
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    payload = binding.session.to_payload()
    if mutation in {"terminal-reopen", "terminal-version-advance"}:
        terminal = gateway._mark_session(
            binding.session,
            "TERMINATED",
        )
        payload = (
            replace(
                terminal,
                status="HEALTHY",
                finished_at=None,
            ).to_payload()
            if mutation == "terminal-reopen"
            else terminal.to_payload()
        )
        payload.update({
            "expected_session_version": 2,
            "session_version": 3,
        })
    elif mutation == "identity-change":
        payload["run_id"] = f"{binding.session.run_id}:other"
        payload.update({
            "expected_session_version": 1,
            "session_version": 2,
        })
    else:
        payload.update({
            "expected_session_version": 7,
            "session_version": 8,
        })
    database.append(Event.create(
        "workforce.session_heartbeat",
        payload,
        source_type="supervisor",
        source_id="adversarial-session-reducer",
        correlation_id=package.package_id,
        idempotency_key=(
            "workforce.session_heartbeat:"
            f"{binding.session.session_id}:{mutation}"
        ),
    ))

    with pytest.raises(WorkforceStateError, match=expected_error):
        rebuild_workforce_state(database)


@pytest.mark.parametrize(
    "event_type",
    [
        "workforce.session_heartbeat",
        "workforce.session_started",
        "workforce.session_state_changed",
    ],
)
def test_terminal_session_rejects_every_late_lifecycle_event(
    tmp_path: Path,
    event_type: str,
) -> None:
    database, package, _contract = _ready_package(
        tmp_path / event_type.rsplit(".", 1)[-1]
    )
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    terminal = gateway._mark_session(
        binding.session,
        "TERMINATED",
    )
    payload = replace(
        terminal,
        status="RUNNING",
        finished_at=None,
    ).to_payload()
    payload.update({
        "expected_session_version": 2,
        "session_version": 3,
    })
    database.append(Event.create(
        event_type,
        payload,
        source_type="supervisor",
        source_id="adversarial-terminal-session",
        correlation_id=package.package_id,
        idempotency_key=(
            f"{event_type}:{binding.session.session_id}:late"
        ),
    ))

    with pytest.raises(
        WorkforceStateError,
        match="terminal workforce session is immutable",
    ):
        rebuild_workforce_state(database)
