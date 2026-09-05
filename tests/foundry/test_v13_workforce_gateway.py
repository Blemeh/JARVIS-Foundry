from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import jarvis_foundry.workforce.gateway as gateway_module
from jarvis_foundry.clock import utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    record_step_contract,
)
from jarvis_foundry.mission.loop import PASSED, StepOutcome
from jarvis_foundry.mission.verifier_binding import (
    AcceptanceGate,
    build_terminal_evidence,
    capture_binding,
)
from jarvis_foundry.mission.verification import (
    record_verification_acceptance,
    record_verification_outcome,
)
from jarvis_foundry.workforce import (
    WorkPackage,
    WorktreeManager,
    WorkforceResourceError,
    WorkerSession,
    create_work_package,
    rebuild_workforce_state,
    transition_work_package,
)
from jarvis_foundry.workforce.gateway import (
    OwnedProcessEpoch,
    WorkforceGateway,
    WorkforceGatewayError,
)


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
    root = tmp_path / "source"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "jarvis@example.invalid")
    _git(root, "config", "user.name", "JARVIS Test")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("BASE = True\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _ready_package(
    tmp_path: Path,
    *,
    effect_action_key: str | None = None,
    package_forbidden_paths: tuple[str, ...] | None = None,
    package_overrides: dict | None = None,
) -> tuple[FoundryDatabase, WorkPackage, MissionStepContract]:
    source, source_sha = _repository(tmp_path)
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()
    package_id = "package-gateway"
    mission_id = "mission-gateway"
    step_id = "step-gateway"
    resource_id = f"workforce-resource:{package_id}"
    contract = MissionStepContract(
        mission_id=mission_id,
        step_id=step_id,
        version=1,
        resource_id=resource_id,
        capability="git_local_commit",
        target_relative_path="src/base.py",
        instructions="Make one bounded change and run focused verification.",
        expected_effects=("bounded source change is present",),
        rollback={"method": "git_reset_soft", "reversible": True},
        risk_class="C1",
        verification_criteria=("focused verification passes",),
        expected_artifacts=("src/base.py",),
        forbidden_paths=("live",),
    )
    package_fields = {
        "package_id": package_id,
        "parent_objective_id": "objective-gateway",
        "mission_id": mission_id,
        "step_id": step_id,
        "role": "builder",
        "capability": contract.capability,
        "bounded_goal": "Exercise the canonical workforce gateway.",
        "acceptance_criteria": contract.verification_criteria,
        "source_sha": source_sha,
        "repository_id": "fixture-repository",
        "branch_name": "candidate/package-gateway",
        "worktree_path": str(tmp_path / "worktrees" / package_id),
        "allowed_paths": ("src",),
        "forbidden_paths": (
            contract.forbidden_paths
            if package_forbidden_paths is None
            else package_forbidden_paths
        ),
        "mission_step_contract_id": contract.contract_id,
        "mission_step_contract_hash": contract.content_hash(),
        "expected_artifacts": contract.expected_artifacts,
        "required_tests": ("pytest -q",),
        "effect_action_key": effect_action_key,
        "lease_seconds": 60,
    }
    package_fields.update(package_overrides or {})
    package = WorkPackage(**package_fields)
    create_work_package(database, package)
    prepared = WorktreeManager(database).prepare(
        package.package_id,
        source_repository=source,
    )
    assert prepared.resource_id == resource_id
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": package.bounded_goal,
            "resource_id": resource_id,
            "state": "PLANNED",
            "acceptance_criteria": list(package.acceptance_criteria),
            "next_action": contract.instructions,
        },
        source_type="test",
        source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    for status in ("ORIENTING", "ACTION_SELECTION"):
        database.append(Event.create(
            "mission.state_changed",
            {
                "mission_id": mission_id,
                "state": status,
                "next_action": contract.instructions,
            },
            source_type="test",
            source_id="test-suite",
            idempotency_key=f"mission.state_changed:{mission_id}:{status}",
        ))
    record_step_contract(database, contract)
    return database, package, contract


def _accept(
    database: FoundryDatabase,
    package: WorkPackage,
    *,
    run_id: str,
    verdict_id: str,
    criteria: tuple[str, ...] | None = None,
) -> None:
    verifier_run_id = f"{run_id}:verify"
    target = Path(package.worktree_path)
    capture = capture_binding(
        database,
        verdict_id=verdict_id,
        mission_id=str(package.mission_id),
        step_id=str(package.step_id),
        target=target,
        builder_run_id=run_id,
        verifier_run_id=verifier_run_id,
        independence={"session_independent": True},
        criteria=list(
            criteria
            if criteria is not None
            else WorkforceGateway._verification_criteria(package)
        ),
    )
    record_verification_outcome(
        database,
        verdict_id=verdict_id,
        outcome="PASS",
        findings=[],
        evidence=build_terminal_evidence(
            capture=capture,
            verifier_session_id=verifier_run_id,
            extra={"gateway_fixture": True},
        ),
    )
    decision = AcceptanceGate(database).evaluate(
        mission_id=str(package.mission_id),
        step_id=str(package.step_id),
        target=target,
        builder_run_id=run_id,
    )
    assert decision.accepted, decision.reason
    record_verification_acceptance(
        database,
        verdict_id=verdict_id,
        mission_id=str(package.mission_id),
        step_id=str(package.step_id),
        builder_run_id=run_id,
        verifier_run_id=verifier_run_id,
        checks=decision.checks,
    )


class _PassingMissionLoop:
    def __init__(
        self,
        database: FoundryDatabase,
        package: WorkPackage,
        *,
        record_acceptance: bool,
    ) -> None:
        self.database = database
        self.package = package
        self.record_acceptance = record_acceptance
        self.calls: list[dict] = []

    def run_step(self, **kwargs) -> StepOutcome:
        self.calls.append(dict(kwargs))
        run_id = str(kwargs["run_id"])
        verdict_id = f"verdict:{run_id}"
        if self.record_acceptance:
            _accept(
                self.database,
                self.package,
                run_id=run_id,
                verdict_id=verdict_id,
                criteria=tuple(kwargs["verification_criteria"]),
            )
        return StepOutcome(
            outcome=PASSED,
            step_id=str(self.package.step_id),
            run_id=run_id,
            verdict_id=verdict_id,
        )


def test_gateway_binds_once_and_replays_accepted_result(tmp_path: Path) -> None:
    database, package, _ = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=True,
    )

    result = gateway.run_inline(package.package_id, mission_loop)

    assert result.package_status == "ACCEPTED"
    assert result.outcome == PASSED
    assert len(mission_loop.calls) == 1
    workforce_context = mission_loop.calls[0]["workforce_context"]
    assert workforce_context == {
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": result.binding.session.session_id,
        "allowed_paths": ["src"],
        "required_tests": ["pytest -q"],
        "duration_seconds": 900,
        "owner_constraints": [],
    }
    assert mission_loop.calls[0]["verification_criteria"] == list(
        WorkforceGateway._verification_criteria(package)
    )
    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "ACCEPTED"
    assert state.active_claims() == ()
    assert database.event_count(event_type="workforce.session_bound") == 1

    replay = gateway.run_inline(package.package_id, mission_loop)

    assert replay.replayed is True
    assert replay.package_status == "ACCEPTED"
    assert replay.outcome == PASSED
    assert len(mission_loop.calls) == 1
    assert database.event_count(event_type="workforce.session_bound") == 1
    assert database.event_count(event_type="verification.accepted") == 1


def test_gateway_passes_package_deadline_to_mission_loop(tmp_path: Path) -> None:
    deadline = "2099-09-04T12:00:00Z"
    database, package, _ = _ready_package(
        tmp_path,
        package_overrides={"deadline": deadline},
    )
    gateway = WorkforceGateway(database)
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=False,
    )

    gateway.run_inline(package.package_id, mission_loop)

    assert mission_loop.calls[0]["workforce_context"]["deadline"] == deadline


def test_gateway_revalidates_deadline_inside_serialized_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline = "2026-09-04T12:00:01Z"
    database, package, _ = _ready_package(
        tmp_path,
        package_overrides={"deadline": deadline},
    )
    observed = iter(
        (
            datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 4, 12, 0, 2, tzinfo=UTC),
        )
    )
    monkeypatch.setattr(gateway_module, "utc_now", lambda: next(observed))

    with pytest.raises(WorkforceGatewayError, match="deadline expired"):
        WorkforceGateway(database).bind(package.package_id)

    assert database.event_count(
        event_type="workforce.session_bound",
        readonly=True,
    ) == 0


def test_gateway_reconciles_late_canonical_acceptance(tmp_path: Path) -> None:
    database, package, _ = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=False,
    )

    pending = gateway.run_inline(package.package_id, mission_loop)

    assert pending.package_status == "VERIFYING"
    assert len(rebuild_workforce_state(database).active_claims()) == 1
    _accept(
        database,
        package,
        run_id=pending.binding.session.run_id,
        verdict_id=str(pending.verdict_id),
    )

    accepted = gateway.run_inline(package.package_id, mission_loop)

    assert accepted.package_status == "ACCEPTED"
    assert len(mission_loop.calls) == 1
    assert rebuild_workforce_state(database).active_claims() == ()


def test_gateway_rejects_acceptance_without_complete_package_policy(
    tmp_path: Path,
) -> None:
    database, package, _ = _ready_package(
        tmp_path,
        package_overrides={
            "evidence_requirements": ("independent evidence receipt",),
            "owner_constraints": ("never mutate live state",),
        },
    )
    gateway = WorkforceGateway(database)
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=False,
    )

    pending = gateway.run_inline(package.package_id, mission_loop)
    _accept(
        database,
        package,
        run_id=pending.binding.session.run_id,
        verdict_id=str(pending.verdict_id),
        criteria=package.acceptance_criteria,
    )

    still_pending = gateway.run_inline(package.package_id, mission_loop)

    assert database.event_count(event_type="verification.accepted") == 1
    assert still_pending.package_status == "VERIFYING"
    assert rebuild_workforce_state(database).package(
        package.package_id
    ).status == "VERIFYING"
    assert len(rebuild_workforce_state(database).active_claims()) == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"role": "verifier"}, "support only the builder role"),
        ({"result_schema": "worker-result.v2"}, "unsupported workforce result schema"),
        ({"token_budget": 1}, "token_budget is not observable"),
        ({"cost_budget_usd": 0.0}, "cost_budget_usd must be positive"),
        ({"deadline": "2026-09-03T00:00:00Z"}, "deadline expired"),
        (
            {"owner_veto_snapshot": {"active": True}},
            "owner-veto snapshot is not clear",
        ),
    ),
)
def test_gateway_rejects_unenforceable_package_policy(
    tmp_path: Path,
    overrides: dict,
    message: str,
) -> None:
    database, package, _ = _ready_package(
        tmp_path,
        package_overrides=overrides,
    )
    gateway = WorkforceGateway(database)

    with pytest.raises(WorkforceGatewayError, match=message):
        gateway.bind(package.package_id)

    assert database.event_count(
        event_type="workforce.session_bound",
        readonly=True,
    ) == 0


def test_concurrent_admission_creates_one_session_and_one_resource_lease(
    tmp_path: Path,
) -> None:
    database, package, _ = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)

    with ThreadPoolExecutor(max_workers=2) as pool:
        bindings = list(
            pool.map(lambda _: gateway.bind(package.package_id), range(2))
        )

    assert {binding.session.run_id for binding in bindings} == {
        f"workforce:{package.package_id}:a1"
    }
    assert sorted(binding.replayed for binding in bindings) == [False, True]
    assert database.event_count(event_type="workforce.session_bound") == 1
    assert database.event_count(event_type="workforce.resource_bound") == 1
    resource_bound = database.events(
        event_types=("workforce.resource_bound",),
    )[0]
    assert resource_bound.payload["expected_status"] == "ACTIVE"
    assert resource_bound.payload["expected_version"] == 1
    assert resource_bound.payload["version"] == 2
    lease = database.row(
        "leases",
        "lease_key",
        bindings[0].resource_lease_key,
    )
    assert lease is not None
    assert lease["owner_id"] == bindings[0].session.run_id


def test_gateway_records_internal_type_error_as_one_crash(tmp_path: Path) -> None:
    database, package, _ = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)

    class CrashingMissionLoop:
        calls = 0

        def run_step(self, **kwargs) -> StepOutcome:
            self.calls += 1
            raise TypeError("worker implementation bug")

    mission_loop = CrashingMissionLoop()
    result = gateway.run_inline(package.package_id, mission_loop)

    assert mission_loop.calls == 1
    assert result.package_status == "FAILED"
    assert result.outcome != PASSED
    session = rebuild_workforce_state(database).sessions[
        result.binding.session.session_id
    ]
    assert session.result["error_class"] == "TypeError"


def test_package_forbidden_paths_must_match_contract_before_binding(
    tmp_path: Path,
) -> None:
    database, package, _ = _ready_package(
        tmp_path,
        package_forbidden_paths=("live", "src/protected.py"),
    )
    gateway = WorkforceGateway(database)

    with pytest.raises(
        WorkforceGatewayError,
        match="forbidden paths",
    ):
        gateway.bind(package.package_id)

    assert database.event_count(
        event_type="workforce.session_bound",
        readonly=True,
    ) == 0


def test_gateway_heartbeat_renews_session_and_mutable_scope(
    tmp_path: Path,
) -> None:
    database, package, _ = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    transition_work_package(
        database,
        package.package_id,
        "RUNNING",
        reason="heartbeat fixture is active",
    )

    healthy = gateway.heartbeat(package.package_id)

    assert healthy.session.session_id == binding.session.session_id
    assert healthy.session.status == "HEALTHY"
    state = rebuild_workforce_state(database)
    claim = state.claims[healthy.claim_id]
    assert claim.session_id == healthy.session.run_id
    assert healthy.session.result["claim_lease_expires_at"] == (
        claim.lease_expires_at
    )
    assert database.event_count(event_type="workforce.resource_claimed") == 1
    assert database.event_count(event_type="workforce.resource_renewed") == 1
    assert database.event_count(event_type="workforce.session_heartbeat") == 1


def test_sealed_heartbeat_requires_exact_live_process_inside_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _ = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    workers = gateway_module.WorkerController(database, lease_seconds=60)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: [
            gateway_module.sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    launch = gateway.spawn(package.package_id, workers)

    try:
        with pytest.raises(
            WorkforceGatewayError,
            match="requires its WorkerController",
        ):
            gateway.heartbeat(package.package_id)

        original_status = workers.status
        status_calls = 0

        def die_after_initial_check(run_id: str):
            nonlocal status_calls
            status_calls += 1
            status = original_status(run_id)
            if status_calls >= 2:
                return replace(status, alive=False)
            return status

        monkeypatch.setattr(workers, "status", die_after_initial_check)
        with pytest.raises(
            WorkforceGatewayError,
            match="dead or changed identity",
        ):
            gateway.heartbeat(
                package.package_id,
                workers=workers,
                expected_session_id=launch.binding.session.session_id,
            )
    finally:
        workers.terminate_all()

    assert status_calls >= 2
    assert database.event_count(
        event_type="workforce.session_heartbeat",
        readonly=True,
    ) == 0


def test_exact_epoch_termination_recaptures_child_spawned_during_root_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_pid = 41001
    child_pid = 41002
    root_identity = {
        "pid": root_pid,
        "create_time": 100,
        "exe": r"C:\Python\python.exe",
    }
    child_identity = {
        "pid": child_pid,
        "create_time": 101,
        "exe": r"C:\Python\python.exe",
    }
    alive = {root_pid: root_identity}
    terminated: list[int] = []

    monkeypatch.setattr(
        gateway_module,
        "pid_alive",
        lambda pid: pid in alive,
    )
    monkeypatch.setattr(
        gateway_module,
        "process_creation_identity",
        lambda pid: dict(alive.get(pid, {"pid": pid})),
    )
    monkeypatch.setattr(
        gateway_module,
        "list_process_tree",
        lambda _root_pid: [
            root_pid,
            *([child_pid] if child_pid in alive else []),
        ],
    )

    def terminate_identity(
        pid: int,
        create_time: int,
        executable: str | None,
        *,
        control_authority_check=None,
    ) -> bool:
        if control_authority_check is not None:
            control_authority_check()
        identity = alive.get(pid)
        assert identity is not None
        assert identity["create_time"] == create_time
        assert identity["exe"] == executable
        terminated.append(pid)
        alive.pop(pid)
        if pid == root_pid:
            alive[child_pid] = child_identity
        return True

    monkeypatch.setattr(
        WorkforceGateway,
        "_terminate_windows_process_identity",
        staticmethod(terminate_identity),
    )

    result = WorkforceGateway._terminate_process_epoch(OwnedProcessEpoch(
        root_pid=root_pid,
        identities=((
            root_pid,
            root_identity["create_time"],
            root_identity["exe"],
        ),),
    ))

    assert result is True
    assert terminated == [root_pid, child_pid]
    assert alive == {}


def test_nonterminal_session_fences_claim_release_and_expiry(
    tmp_path: Path,
) -> None:
    database, package, _ = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    admitted = gateway._admit_launch(
        binding,
        launch_kind="INLINE_MISSION_LOOP",
        reason="release fence fixture admitted",
    )

    with pytest.raises(WorkforceResourceError, match="nonterminal"):
        gateway.worktrees.release(
            admitted.claim_id,
            reason="must not release a live writer",
        )
    with pytest.raises(WorkforceGatewayError, match="nonterminal"):
        gateway.release(
            package.package_id,
            run_id=admitted.session.run_id,
            reason="must not release a live writer",
        )
    assert gateway.worktrees.expire_claims(
        now=utc_now() + timedelta(minutes=5),
    ) == ()

    state = rebuild_workforce_state(database)
    assert state.claims[admitted.claim_id].status == "ACTIVE"
    lease = database.row(
        "leases",
        "lease_key",
        admitted.resource_lease_key,
    )
    assert lease is not None
    assert lease["owner_id"] == admitted.session.run_id
    assert database.event_count(
        event_type="workforce.resource_released",
        readonly=True,
    ) == 0
    assert database.event_count(
        event_type="workforce.resource_expired",
        readonly=True,
    ) == 0


def test_replay_after_launch_intent_still_enters_mission_loop(
    tmp_path: Path,
) -> None:
    database, package, _ = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    admitted = gateway._admit_launch(
        binding,
        launch_kind="INLINE_MISSION_LOOP",
        reason="fault fixture admitted before process crash",
    )
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=True,
    )

    result = gateway.run_inline(package.package_id, mission_loop)

    assert admitted.session.status == "RUNNING"
    assert result.package_status == "ACCEPTED"
    assert len(mission_loop.calls) == 1
    assert database.event_count(
        event_type="workforce.session_started",
        readonly=True,
    ) == 1


def test_stop_rechecked_after_binding_blocks_launch_intent(
    tmp_path: Path,
) -> None:
    database, package, _ = _ready_package(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    gateway = WorkforceGateway(database, stop_path=stop_path)
    gateway.bind(package.package_id)
    stop_path.write_text("owner stop\n", encoding="utf-8")
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=False,
    )

    with pytest.raises(PermissionError, match="workforce stopped"):
        gateway.run_inline(package.package_id, mission_loop)

    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "READY"
    assert state.sessions_for(package.package_id)[0].status == "STARTING"
    assert database.event_count(
        event_type="workforce.session_started",
        readonly=True,
    ) == 0


def test_superseded_outcome_cannot_release_current_ownership(
    tmp_path: Path,
) -> None:
    database, package, _ = _ready_package(tmp_path)
    gateway = WorkforceGateway(database)
    first = gateway.bind(package.package_id)
    gateway._admit_launch(
        first,
        launch_kind="INLINE_MISSION_LOOP",
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

    with pytest.raises(
        WorkforceGatewayError,
        match="superseded attempt",
    ):
        gateway.link_outcome(
            package.package_id,
            StepOutcome(
                outcome=PASSED,
                step_id=package.step_id,
                run_id=first.session.run_id,
                verdict_id=f"verdict:{first.session.run_id}",
            ),
        )

    lease = database.row(
        "leases",
        "lease_key",
        first.resource_lease_key,
    )
    assert lease is not None
    assert lease["owner_id"] == first.session.run_id
    assert len(rebuild_workforce_state(database).active_claims()) == 1


def test_workforce_effect_identity_reaches_canonical_mission_call(
    tmp_path: Path,
) -> None:
    effect_action_key = "effect:package-gateway"
    database, package, _ = _ready_package(
        tmp_path,
        effect_action_key=effect_action_key,
    )
    gateway = WorkforceGateway(database)
    mission_loop = _PassingMissionLoop(
        database,
        package,
        record_acceptance=False,
    )

    gateway.run_inline(package.package_id, mission_loop)

    assert mission_loop.calls[0]["workforce_context"][
        "effect_action_key"
    ] == effect_action_key


def test_process_command_is_sealed_to_the_package_runner(
    tmp_path: Path,
) -> None:
    database, package, _ = _ready_package(tmp_path)
    config = tmp_path / "worker.yaml"
    gateway = WorkforceGateway(
        database,
        worker_config_path=config,
    )

    run_id = f"workforce:{package.package_id}:a1"
    command = gateway._runner_command(package.package_id, run_id)

    assert command[:3] == [
        command[0],
        "-m",
        "jarvis_foundry.workforce.runner",
    ]
    assert command[command.index("--package-id") + 1] == package.package_id
    assert command[command.index("--run-id") + 1] == run_id
    assert command[command.index("--home") + 1] == str(
        Path(database.path).resolve().parent
    )
    assert command[command.index("--config") + 1] == str(config.resolve())
