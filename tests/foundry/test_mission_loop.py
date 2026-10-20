"""WP5 mission loop tests.

Gate (03 spec §16 WP5): a fake mission with a deliberately failing builder
step is repaired and continues to the next useful action without owner
prompting — plus honest waiting, proposal validation, and repair-budget
blocking.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis_foundry.clock import isoformat
from jarvis_foundry.control.action_envelope import evaluate_and_record
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.domain import ActionEnvelope
from jarvis_foundry.events import Event, EventConflict
from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    record_step_contract,
)
from jarvis_foundry.mission.loop import (
    BLOCKED_OUTCOME,
    FAILED,
    MissionLoop,
    PASSED,
    WAITING_OUTCOME,
)
from jarvis_foundry.mission.proposal import ProposalError, propose_next_action
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.results import COMPLETED, RoleResult
from jarvis_foundry.roles.router import (
    ProviderRouteCandidate,
    RoleRouter,
    RoutingRequest,
)
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.workforce.contracts import WorkPackage, WorkerSession
from jarvis_foundry.workforce.state import create_work_package
from jarvis_foundry.workforce.worktrees import WorktreePreparation


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def git_out(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def make_repository(tmp_path: Path) -> Path:
    source = tmp_path / "source-repo"
    source.mkdir()
    git("init", cwd=source)
    git("config", "user.email", "jarvis-test@example.invalid", cwd=source)
    git("config", "user.name", "JARVIS Test", cwd=source)
    (source / "README.md").write_text("baseline\n", encoding="utf-8")
    git("add", "README.md", cwd=source)
    git("commit", "-m", "baseline", cwd=source)
    repository = tmp_path / "repo"
    git(
        "worktree",
        "add",
        "-b",
        "test-worktree",
        str(repository),
        "HEAD",
        cwd=source,
    )
    return repository


def mission_fixture(tmp_path, *, next_action: str | None = "deliver feature X",
                    mission_id: str = "mission-1", objective: str = "gate mission"):
    """Start + registered git resource + mission + policy + READY commit
    capability — the full staging surface the loop governs."""
    repository = make_repository(tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-11T15:33:05Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    database.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-1")))
    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": objective,
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["step is repaired and verified"],
         "next_action": next_action},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    # The executive/orientation layer prepares the mission to the dispatch
    # domain (PLANNED -> ORIENTING -> ACTION_SELECTION), carrying the
    # scheduled requirement explicitly on every state change.
    for state in ("ORIENTING", "ACTION_SELECTION"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": next_action},
            source_type="test", source_id="test-suite",
            idempotency_key=f"mission.state_changed:{mission_id}:{state}",
        ))
    set_capability_state(database, "resource-1", "git_local_commit", "READY",
                         evidence={"source": "wp5-test"})
    policy = ActionPolicy(database, tmp_path / "STOP")
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go", "fallbacks": ()},
        "verifier": {"preferred_provider": "opencode_go",
                     "fallbacks": ("vertex_ai",)},
        "repair": {"preferred_provider": "opencode_go", "fallbacks": ()},
    })
    return database, repository, policy, router


def commit_envelope(repository: Path, mission_id: str = "mission-1") -> ActionEnvelope:
    return ActionEnvelope(
        action_id="commit-step-1",
        mission_id=mission_id,
        resource_id="resource-1",
        action_kind="git_commit",
        target=repository,
        capability="git_local_commit",
        expected_effects=("create local commit",),
        risk_class="C1",
        verification_required=True,
        rollback={"kind": "git_reset_soft"},
    )


class ScriptedAdapter(FakeHermesAdapter):
    """Deterministic gate adapter: the builder omits the artifact on its
    first attempt (deliberate failure), the verifier inspects the worktree
    and FAILs on the missing artifact, the repair role delivers it, and the
    verifier then PASSes."""

    def __init__(self, worktree: Path, artifact_name: str = "result.txt",
                 *, always_fail: bool = False) -> None:
        super().__init__()
        self.worktree = Path(worktree)
        self.artifact_name = artifact_name
        self.always_fail = always_fail
        self.attempts = {"builder": 0, "repair": 0, "verifier": 0}

    def dispatch(self, manifest, *, heartbeat) -> RoleResult:
        role = manifest.role
        self.attempts[role] = self.attempts.get(role, 0) + 1
        heartbeat()
        artifact = self.worktree / self.artifact_name
        if role == "verifier":
            if artifact.exists() and not self.always_fail:
                return RoleResult(
                    run_id=manifest.run_id, role=role, status=COMPLETED,
                    provider=manifest.provider, model=manifest.model,
                    verdict="PASS", evidence=("artifact present",))
            return RoleResult(
                run_id=manifest.run_id, role=role, status=COMPLETED,
                provider=manifest.provider, model=manifest.model,
                verdict="FAIL", evidence=(f"artifact {self.artifact_name} missing",))
        if role == "repair":
            artifact.write_text("repaired\n", encoding="utf-8")
            return RoleResult(
                run_id=manifest.run_id, role=role, status=COMPLETED,
                provider=manifest.provider, model=manifest.model,
                verdict="PASS", evidence=("repair delivered artifact",))
        # builder: first attempt omits the artifact (deliberate failure)
        if self.attempts[role] == 1:
            return RoleResult(
                run_id=manifest.run_id, role=role, status=COMPLETED,
                provider=manifest.provider, model=manifest.model,
                verdict="PASS", evidence=("built (artifact omitted)",))
        artifact.write_text("delivered\n", encoding="utf-8")
        return RoleResult(
            run_id=manifest.run_id, role=role, status=COMPLETED,
            provider=manifest.provider, model=manifest.model,
            verdict="PASS", evidence=("built",))


class LifecycleClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.started_at = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.elapsed

    def utc_now(self) -> datetime:
        return self.started_at + timedelta(seconds=self.elapsed)

    def advance(self, seconds: float) -> None:
        self.elapsed += float(seconds)


class LifecycleBudgetAdapter(FakeHermesAdapter):
    def __init__(
        self,
        worktree: Path,
        clock: LifecycleClock,
        *,
        builder_seconds: float,
        builder_cost_usd: float,
        verifier_cost_usd: float,
    ) -> None:
        super().__init__(working_root=worktree)
        self.clock = clock
        self.builder_seconds = float(builder_seconds)
        self.builder_cost_usd = float(builder_cost_usd)
        self.verifier_cost_usd = float(verifier_cost_usd)

    def dispatch(self, manifest, *, heartbeat) -> RoleResult:
        result = super().dispatch(manifest, heartbeat=heartbeat)
        if manifest.role != "verifier":
            self.clock.advance(self.builder_seconds)
            return replace(result, cost_usd=self.builder_cost_usd)
        return replace(result, cost_usd=self.verifier_cost_usd)


class ProviderFallbackCostAdapter(FakeHermesAdapter):
    def __init__(self, worktree: Path) -> None:
        super().__init__(working_root=worktree)

    def dispatch(self, manifest, *, heartbeat) -> RoleResult:
        self.received.append(manifest)
        heartbeat()
        if manifest.role == "builder" and manifest.provider == "opencode_go":
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status="BLOCKED",
                provider=manifest.provider,
                model=manifest.model,
                verdict="BLOCKED",
                error_class="provider_unavailable",
                error_detail="preferred provider failed after usage",
                cost_usd=0.8,
            )
        if manifest.role == "builder":
            (self.working_root / "result.txt").write_text(
                "fallback delivered\n",
                encoding="utf-8",
            )
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                evidence=("fallback delivered result.txt",),
                cost_usd=0.3,
            )
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=COMPLETED,
            provider=manifest.provider,
            model=manifest.model,
            verdict="PASS",
            evidence=("result.txt independently verified",),
            cost_usd=0.2,
        )


class PackageRestartCostAdapter(FakeHermesAdapter):
    def __init__(self, worktree: Path) -> None:
        super().__init__(working_root=worktree)

    def dispatch(self, manifest, *, heartbeat) -> RoleResult:
        self.received.append(manifest)
        heartbeat()
        if manifest.role == "builder":
            (self.working_root / "result.txt").write_text(
                "first attempt\n",
                encoding="utf-8",
            )
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                evidence=("first attempt produced result.txt",),
                cost_usd=0.5,
            )
        if (
            manifest.role == "verifier"
            and manifest.attempt_id.endswith(":a1")
        ):
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="FAIL",
                evidence=("first attempt requires repair",),
                cost_usd=0.3,
            )
        if manifest.role == "repair":
            (self.working_root / "result.txt").write_text(
                "second attempt\n",
                encoding="utf-8",
            )
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                evidence=("second attempt repaired result.txt",),
                cost_usd=0.3,
            )
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=COMPLETED,
            provider=manifest.provider,
            model=manifest.model,
            verdict="PASS",
            evidence=("second attempt independently verified",),
            cost_usd=0.0,
        )


def lifecycle_package(
    database: FoundryDatabase,
    repository: Path,
    *,
    package_id: str,
    duration_seconds: int,
    deadline: str | None,
    cost_budget_usd: float | None,
    effect_action_key: str | None = None,
    owner_constraints: tuple[str, ...] = (),
    required_tests: tuple[str, ...] = (),
) -> WorkPackage:
    source_sha = git_out("rev-parse", "HEAD", cwd=repository)
    branch_name = git_out("branch", "--show-current", cwd=repository)
    package = WorkPackage(
        package_id=package_id,
        parent_objective_id="objective-1",
        mission_id="mission-1",
        step_id="step-1",
        role="builder",
        capability="write_registered_worktree_files",
        bounded_goal="deliver result.txt",
        acceptance_criteria=("result.txt is verified",),
        source_sha=source_sha,
        repository_id="resource-1",
        branch_name=branch_name,
        worktree_path=str(repository),
        allowed_paths=("result.txt",),
        expected_artifacts=("result.txt",),
        duration_seconds=duration_seconds,
        cost_budget_usd=cost_budget_usd,
        deadline=deadline,
        effect_action_key=effect_action_key,
        owner_constraints=owner_constraints,
        required_tests=required_tests,
    )
    create_work_package(database, package)
    common_dir = Path(
        git_out("rev-parse", "--git-common-dir", cwd=repository)
    )
    if not common_dir.is_absolute():
        common_dir = (repository / common_dir).resolve()
    source_repository = common_dir.parent.resolve()
    source_git_dir = Path(
        git_out("rev-parse", "--git-dir", cwd=source_repository)
    )
    if not source_git_dir.is_absolute():
        source_git_dir = (source_repository / source_git_dir).resolve()
    worktree_git_dir = Path(
        git_out("rev-parse", "--git-dir", cwd=repository)
    )
    if not worktree_git_dir.is_absolute():
        worktree_git_dir = (repository / worktree_git_dir).resolve()
    prepared = WorktreePreparation(
        package_id=package.package_id,
        resource_id=package.repository_id,
        source_repository=str(source_repository),
        worktree_path=str(repository.resolve()),
        branch_name=branch_name,
        base_sha=source_sha,
        claim_id=f"claim:{package.package_id}:test",
        repository_common_dir=str(common_dir),
        source_git_dir=str(source_git_dir),
        worktree_git_dir=str(worktree_git_dir),
    )
    database.append(Event.create(
        "workforce.worktree_prepared",
        prepared.to_payload(),
        source_type="test",
        source_id="test-suite",
        correlation_id=package.package_id,
        idempotency_key=f"workforce.worktree_prepared:{package.package_id}",
    ))
    return package


def record_bound_workforce_session(
    database: FoundryDatabase,
    package: WorkPackage,
    *,
    attempt: int,
    status: str = "STARTING",
) -> str:
    session_id = f"workforce:{package.package_id}:a{attempt}"
    started = WorkerSession(
        session_id=session_id,
        package_id=package.package_id,
        run_id=session_id,
        attempt=attempt,
        framework="PENDING",
        provider="PENDING",
        model="PENDING",
        status="STARTING",
        result={
            "package_hash": package.content_hash(),
            "resource_id": package.repository_id,
            "claim_id": f"claim:{package.package_id}",
            "mission_step_contract_id": f"contract:{package.package_id}",
            "mission_step_contract_hash": "c" * 64,
        },
    )
    database.append(Event.create(
        "workforce.session_bound",
        {
            **started.to_payload(),
            "expected_session_version": 0,
            "session_version": 1,
        },
        source_type="supervisor",
        source_id="workforce-gateway",
        correlation_id=package.package_id,
        idempotency_key=f"workforce.session_bound:{session_id}",
    ))
    if status != "STARTING":
        finished = replace(
            started,
            status=status,
            finished_at=isoformat(),
        )
        database.append(Event.create(
            "workforce.session_state_changed",
            {
                **finished.to_payload(),
                "expected_session_version": 1,
                "session_version": 2,
            },
            source_type="supervisor",
            source_id="workforce-gateway",
            correlation_id=package.package_id,
            idempotency_key=(
                f"workforce.session_state_changed:{session_id}:{status}"
            ),
        ))
    return session_id


def record_result_contract(database: FoundryDatabase) -> MissionStepContract:
    contract = MissionStepContract(
        mission_id="mission-1",
        step_id="step-1",
        version=1,
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        target_relative_path="result.txt",
        instructions="build result.txt for the fake mission",
        expected_effects=("create result.txt",),
        rollback={"kind": "delete_created_file"},
        verification_criteria=("result.txt is independently verified",),
        expected_artifacts=("result.txt",),
    )
    record_step_contract(database, contract)
    return contract


def priced_candidate(
    provider: str = "opencode_go",
    *,
    cost_usd: float = 0.1,
) -> ProviderRouteCandidate:
    return ProviderRouteCandidate(
        provider=provider,
        model="muse-spark-1.2-contributor",
        capabilities=(
            "code",
            "tools",
            "artifact_inspection",
            "adversarial_verification",
            "tests",
        ),
        task_classes=("*",),
        readiness="READY",
        health="READY",
        auth_state="READY",
        availability="AVAILABLE",
        noninteractive_allowed=True,
        cost_usd=cost_usd,
    )


def step_kwargs(**overrides):
    values = {
        "mission_id": "mission-1",
        "step_id": "step-1",
        "capability": "write_registered_worktree_files",
        "target": Path("result.txt"),
        "instructions": "build result.txt for the fake mission",
        "expected_effects": ("create result.txt",),
        "rollback": {"kind": "delete_created_file"},
        "risk_class": "C1",
    }
    values.update(overrides)
    return values


# ---------------------------------------------------------------------------
# D2: envelope contract validated BEFORE dispatch (verifier finding F3)
# ---------------------------------------------------------------------------

def test_unknown_capability_rejected_before_dispatch(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    with pytest.raises(ProposalError, match="not registered"):
        loop.run_step(**step_kwargs(capability="teleport_into_canonical_db"))

    assert len(adapter.received) == 0  # nothing was dispatched


def test_workforce_effect_identity_reaches_canonical_dispatch(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter(working_root=repository)
    effect_action_key = "effect:workforce:package-1"
    package = lifecycle_package(
        database,
        repository,
        package_id="package-1",
        duration_seconds=300,
        deadline=None,
        cost_budget_usd=None,
        effect_action_key=effect_action_key,
    )

    outcome = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
    ).run_step(
        **step_kwargs(expected_artifacts=("result.txt",)),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-1:a1",
            "allowed_paths": ["result.txt"],
            "effect_action_key": effect_action_key,
        },
    )

    assert outcome.outcome == PASSED
    builder = next(
        manifest for manifest in adapter.received
        if manifest.role == "builder"
    )
    assert builder.effect_action_key == effect_action_key


def test_workforce_uses_durable_deadline_and_owner_constraints_when_omitted(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    clock = LifecycleClock()
    deadline = isoformat(clock.utc_now() + timedelta(minutes=5))
    package = lifecycle_package(
        database,
        repository,
        package_id="package-durable-authority",
        duration_seconds=300,
        deadline=deadline,
        cost_budget_usd=None,
        owner_constraints=(
            "preserve README.md",
            "do not expand beyond result.txt",
        ),
    )
    from jarvis_foundry.workforce import worktrees

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        lambda *_args, **_kwargs: repository,
    )
    adapter = FakeHermesAdapter(working_root=repository)

    outcome = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    ).run_step(
        **step_kwargs(expected_artifacts=("result.txt",)),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-durable-authority:a1",
            "allowed_paths": ["result.txt"],
            "duration_seconds": package.duration_seconds,
        },
    )

    assert outcome.outcome == PASSED
    builder = next(
        manifest for manifest in adapter.received
        if manifest.role == "builder"
    )
    assert deadline in builder.instructions
    assert "preserve README.md" in builder.instructions
    assert "do not expand beyond result.txt" in builder.instructions


def test_workforce_rejects_caller_deadline_and_constraint_drift(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    clock = LifecycleClock()
    package = lifecycle_package(
        database,
        repository,
        package_id="package-authority-drift",
        duration_seconds=300,
        deadline=isoformat(clock.utc_now() + timedelta(minutes=5)),
        cost_budget_usd=None,
        owner_constraints=("preserve README.md",),
    )
    from jarvis_foundry.workforce import worktrees

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        lambda *_args, **_kwargs: repository,
    )
    adapter = FakeHermesAdapter(working_root=repository)
    loop = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    )
    context = {
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-authority-drift:a1",
        "allowed_paths": ["result.txt"],
        "duration_seconds": package.duration_seconds,
    }

    with pytest.raises(
        ValueError,
        match="deadline does not match the immutable WorkPackage",
    ):
        loop.run_step(
            **step_kwargs(expected_artifacts=("result.txt",)),
            workforce_context={
                **context,
                "deadline": isoformat(
                    clock.utc_now() + timedelta(minutes=10)
                ),
            },
        )
    with pytest.raises(
        ValueError,
        match="owner_constraints do not match the immutable WorkPackage",
    ):
        loop.run_step(
            **step_kwargs(expected_artifacts=("result.txt",)),
            workforce_context={
                **context,
                "owner_constraints": [
                    "preserve README.md",
                    "caller-added authority",
                ],
            },
        )

    assert adapter.received == []
    assert database.row("missions", "mission_id", "mission-1")["state"] == (
        "ACTION_SELECTION"
    )
    assert not [
        event
        for event in database.events()
        if event.event_type == "agent_run.started"
    ]


def test_accepted_workforce_replay_preflights_every_package_fence(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    contract = record_result_contract(database)
    clock = LifecycleClock()
    deadline = isoformat(clock.utc_now() + timedelta(minutes=1))
    package = lifecycle_package(
        database,
        repository,
        package_id="package-accepted-replay",
        duration_seconds=300,
        deadline=deadline,
        cost_budget_usd=None,
        owner_constraints=("preserve README.md",),
    )
    alternate_package = lifecycle_package(
        database,
        repository,
        package_id="package-accepted-replay-alternate",
        duration_seconds=300,
        deadline=deadline,
        cost_budget_usd=None,
        owner_constraints=("different package authority",),
    )
    from jarvis_foundry.mission import loop as loop_module
    from jarvis_foundry.workforce import worktrees

    reject_worktree = {"value": False}
    worktree_phases = []

    def validate_worktree(*_args, **_kwargs):
        worktree_phases.append(_kwargs.get("phase"))
        if reject_worktree["value"]:
            raise ValueError("accepted replay worktree contamination")
        return repository

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        validate_worktree,
    )
    adapter = FakeHermesAdapter(working_root=repository)
    loop = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    )
    run_id = "workforce:package-accepted-replay:a1"
    context = {
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": run_id,
        "allowed_paths": ["result.txt"],
        "duration_seconds": package.duration_seconds,
    }
    kwargs = step_kwargs(
        run_id=run_id,
        expected_artifacts=contract.expected_artifacts,
        verification_criteria=list(contract.verification_criteria),
    )

    first = loop.run_step(**kwargs, workforce_context=context)
    assert first.outcome == PASSED
    assert database.row("missions", "mission_id", "mission-1")["state"] == (
        "COMPLETE"
    )
    assert worktree_phases[:2] == [
        "builder_admission",
        "builder_admission",
    ]
    assert worktree_phases[-1] == "job_delta"
    event_count = len(database.events())
    replay_effects: list[str] = []

    def unexpected_effect(name):
        def fail(*_args, **_kwargs):
            replay_effects.append(name)
            raise AssertionError(
                f"{name} ran before accepted replay preflight completed"
            )

        return fail

    monkeypatch.setattr(
        loop_module,
        "commit_after_pass",
        unexpected_effect("commit"),
    )
    monkeypatch.setattr(
        loop_module,
        "record_verification_acceptance",
        unexpected_effect("acceptance"),
    )
    monkeypatch.setattr(
        loop_module.AcceptanceGate,
        "evaluate",
        unexpected_effect("acceptance_gate"),
    )
    monkeypatch.setattr(
        loop,
        "_record_evidence",
        unexpected_effect("evidence"),
    )
    monkeypatch.setattr(
        loop,
        "_continue_after_pass",
        unexpected_effect("mission_advance"),
    )

    with pytest.raises(ValueError, match="package hash does not match"):
        loop.run_step(
            **kwargs,
            commit_envelope=commit_envelope(repository),
            workforce_context={**context, "package_hash": "0" * 64},
        )
    with pytest.raises(ValueError, match="step does not match"):
        loop.run_step(
            **{**kwargs, "step_id": "step-other"},
            commit_envelope=commit_envelope(repository),
            workforce_context=context,
        )
    with pytest.raises(ValueError, match="deadline does not match"):
        loop.run_step(
            **kwargs,
            commit_envelope=commit_envelope(repository),
            workforce_context={
                **context,
                "deadline": isoformat(
                    clock.utc_now() + timedelta(minutes=10)
                ),
            },
        )
    with pytest.raises(ValueError, match="owner_constraints do not match"):
        loop.run_step(
            **kwargs,
            commit_envelope=commit_envelope(repository),
            workforce_context={
                **context,
                "owner_constraints": ["caller replacement"],
            },
        )
    with pytest.raises(
        ValueError,
        match="lineage does not match the completed builder run",
    ):
        loop.run_step(
            **kwargs,
            commit_envelope=commit_envelope(repository),
            workforce_context={
                **context,
                "package_id": alternate_package.package_id,
                "package_hash": alternate_package.content_hash(),
                "attempt_id": (
                    "workforce:package-accepted-replay-alternate:a1"
                ),
            },
        )
    with pytest.raises(
        ValueError,
        match="requires complete package lineage",
    ):
        loop.run_step(
            **kwargs,
            commit_envelope=commit_envelope(repository),
        )
    worktree_phases.clear()
    reject_worktree["value"] = True
    with pytest.raises(ValueError, match="worktree contamination"):
        loop.run_step(
            **kwargs,
            commit_envelope=commit_envelope(repository),
            workforce_context=context,
        )
    assert worktree_phases == ["job_delta"]
    reject_worktree["value"] = False
    clock.advance(61)
    expired = loop.run_step(
        **kwargs,
        commit_envelope=commit_envelope(repository),
        workforce_context=context,
    )

    assert expired.outcome == BLOCKED_OUTCOME
    assert "exhausted before accepted-run replay" in str(expired.reason)
    assert replay_effects == []
    assert len(database.events()) == event_count
    assert database.row("missions", "mission_id", "mission-1")["state"] == (
        "COMPLETE"
    )


def test_new_workforce_builder_preflights_strictly_before_state_transition(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    package = lifecycle_package(
        database,
        repository,
        package_id="package-strict-builder-preflight",
        duration_seconds=300,
        deadline=None,
        cost_budget_usd=None,
    )
    from jarvis_foundry.workforce import worktrees

    phases = []

    def reject_worktree(*_args, **kwargs):
        phases.append(kwargs.get("phase"))
        raise worktrees.WorkforceResourceError(
            "strict builder preflight rejected the checkout"
        )

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        reject_worktree,
    )
    before_state = database.row("missions", "mission_id", "mission-1")[
        "state"
    ]
    run_id = "workforce:package-strict-builder-preflight:a1"

    with pytest.raises(
        worktrees.WorkforceResourceError,
        match="strict builder preflight",
    ):
        MissionLoop(
            database,
            router,
            FakeHermesAdapter(working_root=repository),
            policy=policy,
        ).run_step(
            **step_kwargs(
                run_id=run_id,
                expected_artifacts=("result.txt",),
            ),
            workforce_context={
                "package_id": package.package_id,
                "package_hash": package.content_hash(),
                "attempt_id": run_id,
                "allowed_paths": list(package.allowed_paths),
                "duration_seconds": package.duration_seconds,
            },
        )

    assert phases == ["builder_admission"]
    assert database.row("missions", "mission_id", "mission-1")["state"] == (
        before_state
    )
    assert not [
        event
        for event in database.events()
        if event.event_type == "agent_run.started"
        and event.payload.get("run_id") == run_id
    ]


def test_workforce_session_loss_recovery_preserves_job_delta_phase(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    contract = record_result_contract(database)
    package = lifecycle_package(
        database,
        repository,
        package_id="package-session-loss-recovery",
        duration_seconds=300,
        deadline=None,
        cost_budget_usd=None,
    )
    from jarvis_foundry.workforce import worktrees

    phases = []

    def validate_worktree(*_args, **kwargs):
        phases.append(kwargs.get("phase"))
        return repository

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        validate_worktree,
    )
    prior_run_id = f"workforce:{package.package_id}:a1"
    database.append(Event.create(
        "agent_run.started",
        {
            "run_id": prior_run_id,
            "mission_id": "mission-1",
            "step_id": "step-1",
            "role": "builder",
            "provider": "opencode_go",
            "model": "test-model",
            "context_manifest_hash": "a" * 64,
            "tool_policy": [],
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": prior_run_id,
            "worktree_phase": "builder_admission",
        },
        source_type="supervisor",
        source_id="role-dispatcher",
        idempotency_key=f"agent_run.started:{prior_run_id}:opencode_go",
    ))
    database.append(Event.create(
        "agent_run.failed",
        {
            "run_id": prior_run_id,
            "failure_class": "heartbeat_timeout",
        },
        source_type="supervisor",
        source_id="role-reaper",
        idempotency_key=(
            f"agent_run.failed:{prior_run_id}:heartbeat_timeout"
        ),
    ))
    database.append_events([
        Event.create(
            "mission.state_changed",
            {
                "mission_id": "mission-1",
                "state": "BLOCKED",
                "next_action": "deliver feature X",
                "reason": "session_lost",
            },
            source_type="supervisor",
            source_id="role-reaper",
            idempotency_key=(
                "mission.state_changed:mission-1:BLOCKED:"
                "session-loss-recovery-test"
            ),
        ),
        Event.create(
            "mission.state_changed",
            {
                "mission_id": "mission-1",
                "state": "ACTION_SELECTION",
                "next_action": "deliver feature X",
                "reason": "resume_after_session_loss",
            },
            source_type="supervisor",
            source_id="role-reaper",
            idempotency_key=(
                "mission.state_changed:mission-1:ACTION_SELECTION:"
                "session-loss-recovery-test"
            ),
        ),
    ])
    adapter = FakeHermesAdapter(working_root=repository)
    record_bound_workforce_session(
        database,
        package,
        attempt=1,
        status="FAILED",
    )
    fresh_run_id = record_bound_workforce_session(
        database,
        package,
        attempt=2,
    )

    outcome = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
    ).run_step(
        **step_kwargs(
            run_id=fresh_run_id,
            expected_artifacts=contract.expected_artifacts,
            verification_criteria=list(contract.verification_criteria),
        ),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": fresh_run_id,
            "allowed_paths": list(package.allowed_paths),
            "duration_seconds": package.duration_seconds,
        },
    )

    assert outcome.outcome == PASSED
    assert phases[:2] == ["job_delta", "job_delta"]
    assert adapter.received[0].role == "builder"
    assert adapter.received[0].worktree_phase == "job_delta"


def test_unbound_replacement_attempt_cannot_relax_builder_admission(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    package = lifecycle_package(
        database,
        repository,
        package_id="package-unbound-replacement",
        duration_seconds=300,
        deadline=None,
        cost_budget_usd=None,
    )
    from jarvis_foundry.workforce import worktrees

    phases = []

    def reject_worktree(*_args, **kwargs):
        phases.append(kwargs.get("phase"))
        raise worktrees.WorkforceResourceError(
            "replacement lacks canonical session binding"
        )

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        reject_worktree,
    )
    run_id = f"workforce:{package.package_id}:a2"

    with pytest.raises(
        EventConflict,
        match="no canonical session binding",
    ):
        MissionLoop(
            database,
            router,
            FakeHermesAdapter(working_root=repository),
            policy=policy,
        ).run_step(
            **step_kwargs(run_id=run_id),
            workforce_context={
                "package_id": package.package_id,
                "package_hash": package.content_hash(),
                "attempt_id": run_id,
                "allowed_paths": list(package.allowed_paths),
                "duration_seconds": package.duration_seconds,
            },
        )

    assert phases == []


def test_bound_reassignment_attempt_preserves_job_delta_without_reaper_reason(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    package = lifecycle_package(
        database,
        repository,
        package_id="package-bound-reassignment",
        duration_seconds=300,
        deadline=None,
        cost_budget_usd=None,
    )
    from jarvis_foundry.workforce import worktrees

    phases = []
    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        lambda *_args, **kwargs: (
            phases.append(kwargs.get("phase")) or repository
        ),
    )
    record_bound_workforce_session(
        database,
        package,
        attempt=1,
        status="TERMINATED",
    )
    run_id = record_bound_workforce_session(
        database,
        package,
        attempt=2,
    )
    adapter = FakeHermesAdapter(working_root=repository)

    outcome = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
    ).run_step(
        **step_kwargs(
            run_id=run_id,
            expected_artifacts=("result.txt",),
        ),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": run_id,
            "allowed_paths": list(package.allowed_paths),
            "duration_seconds": package.duration_seconds,
        },
    )

    assert outcome.outcome == PASSED
    assert phases[:2] == ["job_delta", "job_delta"]
    assert adapter.received[0].worktree_phase == "job_delta"


def test_workforce_lineage_rejects_zero_attempt_ordinal(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    loop = MissionLoop(
        database,
        router,
        FakeHermesAdapter(working_root=repository),
        policy=policy,
    )
    run_id = "run-zero-attempt-ordinal"
    provider = "opencode_go"
    model = "test-model"
    database.append(Event.create(
        "provider.dispatch_attempted",
        {
            "run_id": run_id,
            "mission_id": "mission-1",
            "step_id": "step-1",
            "role": "builder",
            "package_id": "package-zero-attempt-ordinal",
            "package_hash": "a" * 64,
            "attempt_id": "workforce:package-zero-attempt-ordinal:a1",
            "provider_id": provider,
            "provider": provider,
            "model_id": model,
            "model": model,
            "attempt_generation": 0,
            "attempt_ordinal": 0,
            "estimate_state": "UNAVAILABLE",
            "estimated_cost_micros": None,
            "estimated_cost_usd": None,
        },
        source_type=loop.dispatcher.source_type,
        source_id=loop.dispatcher.source_id,
        idempotency_key=(
            f"provider.dispatch_attempted:{run_id}:0:0:{provider}:{model}"
        ),
    ))

    with pytest.raises(ValueError, match="identity is not canonical"):
        loop._workforce_lineage_for_run(run_id)


@pytest.mark.parametrize(
    ("error_class", "expect_wait"),
    [
        ("provider_effect_identity_mismatch", True),
        ("provider_effect_reconciliation_required", True),
        ("provider_unavailable", False),
    ],
)
def test_effect_identity_mismatch_uses_effect_reconciliation_wait_only(
    tmp_path,
    monkeypatch,
    error_class,
    expect_wait,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    from jarvis_foundry.executive import continuous

    scheduled = []
    monkeypatch.setattr(
        continuous,
        "schedule_mission_provider_wait",
        lambda *_args, **kwargs: scheduled.append(kwargs),
    )
    loop = MissionLoop(
        database,
        router,
        FakeHermesAdapter(working_root=repository),
        policy=policy,
    )
    effect_action_key = "effect:mission-loop:identity"
    monkeypatch.setattr(
        loop,
        "_dispatch_with_lifecycle_deadline",
        lambda **kwargs: RoleResult(
            run_id=str(kwargs["run_id"]),
            role=str(kwargs["role"]),
            status="BLOCKED",
            provider="opencode_go",
            model="test-model",
            verdict="BLOCKED",
            error_class=error_class,
            error_detail="provider effect identity requires reconciliation",
            effect_action_key=effect_action_key,
        ),
    )

    outcome = loop.run_step(
        **step_kwargs(run_id=f"run-{error_class}"),
    )

    assert outcome.outcome == BLOCKED_OUTCOME
    assert bool(scheduled) is expect_wait
    if expect_wait:
        assert scheduled[0]["error_class"] == error_class
        assert scheduled[0]["effect_action_key"] == effect_action_key
        assert scheduled[0]["provider_ids"] == ()


def test_workforce_duration_and_cost_are_shared_with_verifier(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    clock = LifecycleClock()
    adapter = LifecycleBudgetAdapter(
        repository,
        clock,
        builder_seconds=4,
        builder_cost_usd=0.4,
        verifier_cost_usd=0.2,
    )
    package = lifecycle_package(
        database,
        repository,
        package_id="package-budget",
        duration_seconds=6,
        deadline=isoformat(clock.utc_now() + timedelta(seconds=30)),
        cost_budget_usd=1.0,
    )
    loop = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        provider_candidates=(priced_candidate(),),
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    )

    outcome = loop.run_step(
        **step_kwargs(expected_artifacts=("result.txt",)),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-budget:a1",
            "allowed_paths": ["result.txt"],
            "duration_seconds": package.duration_seconds,
            "cost_budget_usd": package.cost_budget_usd,
        },
    )

    assert outcome.outcome == PASSED
    builder, verifier = adapter.received
    assert builder.duration_seconds == 6
    assert verifier.duration_seconds == 2
    assert verifier.budget_usd == pytest.approx(0.6)
    assert "Absolute UTC deadline" in builder.instructions
    assert package.deadline in builder.instructions


def test_verifier_prompt_requires_one_concrete_verdict_line(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter(working_root=repository)
    loop = MissionLoop(database, router, adapter, policy=policy)

    outcome = loop.run_step(
        **step_kwargs(expected_artifacts=("result.txt",)),
    )

    assert outcome.outcome == PASSED
    verifier = next(item for item in adapter.received if item.role == "verifier")
    assert "choose exactly one verdict" in verifier.instructions
    assert "VERDICT: PASS" in verifier.instructions
    assert "PASS | FAIL | BLOCKED | NOT_TESTED" not in verifier.instructions
    assert "do not list the alternatives" in verifier.instructions


def test_workforce_verifier_preserves_immutable_required_test_identity(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    from jarvis_foundry.workforce import worktrees

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        lambda *_args, **_kwargs: repository,
    )
    required_tests = ("jarvis-required-test",)
    adapter = FakeHermesAdapter(
        evidence=("[jarvis-test] jarvis-required-test exit=0",),
        working_root=repository,
    )
    package = lifecycle_package(
        database,
        repository,
        package_id="package-verifier-required-tests",
        duration_seconds=30,
        deadline=None,
        cost_budget_usd=None,
        required_tests=required_tests,
    )

    outcome = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
    ).run_step(
        **step_kwargs(expected_artifacts=("result.txt",)),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-verifier-required-tests:a1",
            "allowed_paths": ["result.txt"],
            "required_tests": list(required_tests),
            "duration_seconds": package.duration_seconds,
        },
    )

    assert outcome.outcome == PASSED
    builder, verifier = adapter.received
    assert builder.required_tests == required_tests
    assert verifier.required_tests == required_tests


def test_workforce_exact_decimal_cost_budget_reaches_verifier_pass(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    clock = LifecycleClock()
    adapter = LifecycleBudgetAdapter(
        repository,
        clock,
        builder_seconds=1,
        builder_cost_usd=0.1,
        verifier_cost_usd=0.2,
    )
    package = lifecycle_package(
        database,
        repository,
        package_id="package-decimal-budget",
        duration_seconds=10,
        deadline=isoformat(clock.utc_now() + timedelta(seconds=30)),
        cost_budget_usd=0.3,
    )

    outcome = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        provider_candidates=(priced_candidate(),),
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    ).run_step(
        **step_kwargs(expected_artifacts=("result.txt",)),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-decimal-budget:a1",
            "allowed_paths": ["result.txt"],
            "duration_seconds": package.duration_seconds,
            "cost_budget_usd": package.cost_budget_usd,
        },
    )

    assert outcome.outcome == PASSED
    builder, verifier = adapter.received
    assert verifier.budget_usd == pytest.approx(0.2)
    cost_events = [
        event.payload["cost_usd"]
        for event in database.events()
        if event.event_type == "provider.dispatch_cost_recorded"
        and event.payload.get("package_id") == package.package_id
    ]
    assert cost_events == pytest.approx([0.1, 0.2])


def test_workforce_context_deadline_blocks_verifier_after_builder(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    clock = LifecycleClock()
    adapter = LifecycleBudgetAdapter(
        repository,
        clock,
        builder_seconds=3,
        builder_cost_usd=0.1,
        verifier_cost_usd=0.0,
    )
    package = lifecycle_package(
        database,
        repository,
        package_id="package-deadline",
        duration_seconds=10,
        deadline=isoformat(clock.utc_now() + timedelta(seconds=3)),
        cost_budget_usd=1.0,
    )

    outcome = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        provider_candidates=(priced_candidate(),),
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    ).run_step(
        **step_kwargs(expected_artifacts=("result.txt",)),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-deadline:a1",
            "allowed_paths": ["result.txt"],
            "duration_seconds": package.duration_seconds,
            "cost_budget_usd": package.cost_budget_usd,
            "deadline": package.deadline,
        },
    )

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "exhausted before independent verification" in str(outcome.reason)
    assert [manifest.role for manifest in adapter.received] == ["builder"]
    assert package.deadline in adapter.received[0].instructions


def test_observed_verifier_cost_cannot_exceed_package_budget(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    clock = LifecycleClock()
    adapter = LifecycleBudgetAdapter(
        repository,
        clock,
        builder_seconds=1,
        builder_cost_usd=0.8,
        verifier_cost_usd=0.3,
    )
    package = lifecycle_package(
        database,
        repository,
        package_id="package-cost-overrun",
        duration_seconds=10,
        deadline=isoformat(clock.utc_now() + timedelta(seconds=30)),
        cost_budget_usd=1.0,
    )

    outcome = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        provider_candidates=(priced_candidate(),),
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    ).run_step(
        **step_kwargs(expected_artifacts=("result.txt",)),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-cost-overrun:a1",
            "allowed_paths": ["result.txt"],
            "duration_seconds": package.duration_seconds,
            "cost_budget_usd": package.cost_budget_usd,
        },
    )

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "observed cost exceeded" in str(outcome.reason)
    verifier = next(
        manifest for manifest in adapter.received
        if manifest.role == "verifier"
    )
    assert verifier.budget_usd == pytest.approx(0.2)


def test_provider_fallback_spend_is_included_in_builder_and_verifier_budget(
    tmp_path,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    router = router.clone_with({
        "builder": {
            "preferred_provider": "opencode_go",
            "fallbacks": ("vertex_ai",),
        },
    })
    clock = LifecycleClock()
    adapter = ProviderFallbackCostAdapter(repository)
    package = lifecycle_package(
        database,
        repository,
        package_id="package-fallback-cost",
        duration_seconds=30,
        deadline=isoformat(clock.utc_now() + timedelta(minutes=5)),
        cost_budget_usd=1.2,
    )

    outcome = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        provider_candidates=(
            priced_candidate("opencode_go"),
            priced_candidate("vertex_ai"),
        ),
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    ).run_step(
        **step_kwargs(
            run_id="run-fallback-cost",
            expected_artifacts=("result.txt",),
        ),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-fallback-cost:a1",
            "allowed_paths": ["result.txt"],
            "duration_seconds": package.duration_seconds,
            "cost_budget_usd": package.cost_budget_usd,
        },
    )

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "observed cost exceeded" in str(outcome.reason)
    builder_manifests = [
        manifest for manifest in adapter.received
        if manifest.role == "builder"
    ]
    assert [manifest.provider for manifest in builder_manifests] == [
        "opencode_go",
        "vertex_ai",
    ]
    verifier_manifest = next(
        manifest for manifest in adapter.received
        if manifest.role == "verifier"
    )
    assert verifier_manifest.budget_usd == pytest.approx(0.1)
    builder_row = database.row("agent_runs", "run_id", "run-fallback-cost")
    assert json.loads(builder_row["result_json"])["cost_usd"] == pytest.approx(1.1)


def test_package_restart_carries_prior_builder_and_verifier_cost(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    clock = LifecycleClock()
    adapter = PackageRestartCostAdapter(repository)
    package = lifecycle_package(
        database,
        repository,
        package_id="package-restart-lifetime-cost",
        duration_seconds=30,
        deadline=isoformat(clock.utc_now() + timedelta(minutes=5)),
        cost_budget_usd=1.0,
    )
    from jarvis_foundry.workforce import worktrees

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        lambda *_args, **_kwargs: repository,
    )

    first = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        provider_candidates=(priced_candidate(),),
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    ).run_step(
        **step_kwargs(
            run_id="workforce:package-restart-lifetime-cost:a1",
            expected_artifacts=("result.txt",),
        ),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-restart-lifetime-cost:a1",
            "allowed_paths": ["result.txt"],
            "duration_seconds": package.duration_seconds,
            "cost_budget_usd": package.cost_budget_usd,
        },
    )

    assert first.outcome == FAILED
    assert [manifest.role for manifest in adapter.received] == [
        "builder",
        "verifier",
    ]
    record_bound_workforce_session(
        database,
        package,
        attempt=1,
        status="FAILED",
    )
    record_bound_workforce_session(
        database,
        package,
        attempt=2,
    )

    second = MissionLoop(
        database,
        router,
        adapter,
        policy=policy,
        provider_candidates=(priced_candidate(),),
        monotonic_clock=clock.monotonic,
        utc_clock=clock.utc_now,
    ).run_step(
        **step_kwargs(
            run_id="workforce:package-restart-lifetime-cost:a2",
            expected_artifacts=("result.txt",),
        ),
        workforce_context={
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-restart-lifetime-cost:a2",
            "allowed_paths": ["result.txt"],
            "duration_seconds": package.duration_seconds,
            "cost_budget_usd": package.cost_budget_usd,
        },
    )

    assert second.outcome == BLOCKED_OUTCOME
    assert "workforce_cost_budget_exhausted" in str(second.reason)
    assert [manifest.role for manifest in adapter.received] == [
        "builder",
        "verifier",
        "repair",
    ]
    cost_events = [
        event
        for event in database.events()
        if event.event_type == "provider.dispatch_cost_recorded"
        and event.payload.get("package_id") == package.package_id
    ]
    assert [event.payload["cost_usd"] for event in cost_events] == [0.5, 0.3, 0.3]
    repair_row = database.row(
        "agent_runs",
        "run_id",
        "workforce:package-restart-lifetime-cost:a2",
    )
    assert repair_row["status"] == "FAILED"
    assert repair_row["failure_class"] == "workforce_cost_budget_exhausted"


def test_conflicting_campaign_and_workforce_effect_identities_fail_closed(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    database.append(Event.create(
        "campaign.created",
        {
            "campaign_id": "campaign-1",
            "objective_id": "objective-1",
        },
        source_type="test",
        source_id="test-suite",
        idempotency_key="campaign.created:campaign-1",
    ))
    from jarvis_foundry.executive import campaigns

    monkeypatch.setattr(
        campaigns,
        "campaign_dispatch_context",
        lambda *_args, **_kwargs: {
            "objective_id": "objective-1",
            "campaign_id": "campaign-1",
            "child_id": "child-1",
            "child_status": "RUNNING",
            "routing_request": None,
            "isolation_key": "campaign-1:child-1",
            "effect_action_key": "effect:campaign:child-1",
        },
    )
    adapter = FakeHermesAdapter(working_root=repository)

    with pytest.raises(
        ValueError,
        match="campaign and workforce effect identities disagree",
    ):
        MissionLoop(
            database,
            router,
            adapter,
            policy=policy,
        ).run_step(
            **step_kwargs(),
            workforce_context={
                "package_id": "package-1",
                "package_hash": "1" * 64,
                "attempt_id": "workforce:package-1:a1",
                "allowed_paths": ["result.txt"],
                "effect_action_key": "effect:workforce:package-1",
            },
        )

    assert adapter.received == []


def test_invalid_workforce_package_is_rejected_before_durable_admission(
    tmp_path,
    monkeypatch,
):
    database, repository, policy, router = mission_fixture(tmp_path)
    package = lifecycle_package(
        database,
        repository,
        package_id="package-preflight",
        duration_seconds=300,
        deadline=None,
        cost_budget_usd=1.0,
    )
    from jarvis_foundry.executive import campaigns

    campaign_transitions = []
    monkeypatch.setattr(
        campaigns,
        "campaign_dispatch_context",
        lambda *_args, **_kwargs: {
            "objective_id": "objective-preflight",
            "campaign_id": "campaign-preflight",
            "child_id": "child-preflight",
            "child_status": "READY",
            "routing_request": RoutingRequest(task_type="builder"),
            "isolation_key": "campaign-preflight:child-preflight",
            "effect_action_key": None,
        },
    )
    monkeypatch.setattr(
        campaigns,
        "record_child_state",
        lambda *_args, **kwargs: campaign_transitions.append(kwargs),
    )
    adapter = FakeHermesAdapter(working_root=repository)
    before_state = database.row("missions", "mission_id", "mission-1")["state"]

    with pytest.raises(
        ValueError,
        match="workforce package hash does not match",
    ):
        MissionLoop(
            database,
            router,
            adapter,
            policy=policy,
            provider_candidates=(priced_candidate(),),
        ).run_step(
            **step_kwargs(),
            run_id="workforce:package-preflight:a1",
            workforce_context={
                "package_id": package.package_id,
                "package_hash": "1" * 64,
                "attempt_id": "workforce:package-preflight:a1",
                "allowed_paths": list(package.allowed_paths),
                "duration_seconds": package.duration_seconds,
                "cost_budget_usd": package.cost_budget_usd,
            },
        )

    assert database.row("missions", "mission_id", "mission-1")["state"] == (
        before_state
    )
    assert campaign_transitions == []
    assert adapter.received == []
    assert not [
        event
        for event in database.events()
        if event.event_type == "agent_run.started"
        and event.payload.get("run_id") == "workforce:package-preflight:a1"
    ]


def test_authenticated_redirect_reaches_mission_loop_dispatch(tmp_path):
    """R10: a durable owner redirect changes the existing loop context."""
    database, repository, policy, router = mission_fixture(tmp_path)
    secret = b"r10-mission-loop-secret"
    service = OwnerControlService(database, secret=secret, stop_path=tmp_path / "STOP")
    signature = sign_command(
        secret, "REDIRECT", "mission-1", "use the evidence-first path",
        "r10-loop-redirect",
    )
    directive = service.redirect(
        target="mission-1", exact_text="use the evidence-first path",
        nonce="r10-loop-redirect", signature=signature,
        directive_id="directive-r10-loop-redirect",
    )

    adapter = FakeHermesAdapter(working_root=repository)
    outcome = MissionLoop(database, router, adapter, policy=policy).run_step(
        **step_kwargs())

    assert outcome.outcome == PASSED
    builder = next(manifest for manifest in adapter.received if manifest.role == "builder")
    assert "Canonical mission next action: redirect: use the evidence-first path" in builder.instructions
    interpreted = [
        event for event in database.events()
        if event.event_type == "mission.directive_interpreted"
    ]
    assert len(interpreted) == 1
    assert interpreted[0].payload["directive_id"] == directive.directive_id
    assert database.quick_check() == "ok"


def test_duplicate_pass_with_mutated_tree_gets_fresh_verifier_revision(tmp_path):
    """A replayed mission delivery must not trust a stale PASS binding.

    The builder result may be safely replayed, but the verifier session and
    acceptance seal must be renewed after the artifact changes.
    """
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"

    class StableArtifactAdapter(FakeHermesAdapter):
        def __init__(self):
            super().__init__(working_root=repository)
            self.verifier_calls = 0

        def dispatch(self, manifest, *, heartbeat):
            if manifest.role == "builder":
                path = repository / "step-out" / "report.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("stable\n", encoding="utf-8")
            if manifest.role == "verifier":
                self.verifier_calls += 1
            return super().dispatch(manifest, heartbeat=heartbeat)

    adapter = StableArtifactAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)
    kwargs = step_kwargs(
        run_id="run-stale-replay",
        target=target,
        expected_artifacts=("report.md",),
        verification_criteria=("report exists",),
    )
    first = loop.run_step(**kwargs)
    assert first.outcome == PASSED
    first_verdict = first.verdict_id
    (target / "report.md").write_text("mutated after PASS\n", encoding="utf-8")
    # Re-open the durable action slot to model a duplicate delivery after a
    # caller restart; the stale PASS must be checked before this retry runs.
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-1", "state": "READY",
         "next_action": "retry stale artifact"},
        source_type="supervisor", source_id="test-retry",
        idempotency_key="mission.state_changed:mission-1:ready-retry-stale",
    ))

    second = loop.run_step(**kwargs)
    assert second.outcome == PASSED
    assert second.verdict_id != first_verdict
    assert adapter.verifier_calls == 2
    started = [
        event for event in database.events()
        if event.event_type == "verification.started"
    ]
    assert len(started) == 2
    assert started[0].payload["verifier_run_id"] != started[1].payload["verifier_run_id"]


def test_stop_arriving_after_builder_blocks_verifier_and_completion(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    stop_path = tmp_path / "STOP"

    class StopAfterBuilder(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            result = super().dispatch(manifest, heartbeat=heartbeat)
            if manifest.role == "builder":
                EmergencyStop(stop_path).activate("builder boundary race")
            return result

    adapter = StopAfterBuilder(working_root=repository)
    outcome = MissionLoop(
        database, router, adapter, policy=policy, stop_path=stop_path,
    ).run_step(**step_kwargs())

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "STOP active" in (outcome.reason or "")
    assert not [m for m in adapter.received if m.role == "verifier"]
    assert not [e for e in database.events()
                if e.event_type == "mission.completed"]
    assert database.row("missions", "mission_id", "mission-1")["state"] == "BLOCKED"


def test_empty_rollback_rejected_before_dispatch(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    with pytest.raises(ProposalError, match="rollback contract is required"):
        loop.run_step(**step_kwargs(rollback={}))

    assert len(adapter.received) == 0


def test_out_of_root_target_rejected_before_dispatch(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    with pytest.raises(ProposalError, match="escapes the registered resource root"):
        loop.run_step(**step_kwargs(target=Path("..") / "outside.txt"))

    assert len(adapter.received) == 0


def test_missing_step_id_rejected_before_dispatch(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    with pytest.raises(ProposalError, match="step_id is required"):
        loop.run_step(**step_kwargs(step_id=""))

    assert len(adapter.received) == 0


def test_repair_requires_existing_fail_verdict(tmp_path):
    # Verifier finding F6: a hand-crafted REPAIRING state must not invent a
    # repair pass without a durable FAIL verdict for the step.
    database, repository, policy, router = mission_fixture(tmp_path)
    loop = MissionLoop(database, router, FakeHermesAdapter(), policy=policy)
    loop._advance("mission-1", "READY")
    loop._advance("mission-1", "EXECUTING")
    loop._advance("mission-1", "VERIFYING")
    loop._advance("mission-1", "REPAIRING")

    with pytest.raises(ProposalError, match="no FAIL verdict"):
        loop.run_step(**step_kwargs())


def test_fresh_loop_instance_resumes_repairing_mission(tmp_path):
    # Verifier finding F2: a FRESH MissionLoop instance resuming a mission
    # left in REPAIRING must re-walk the state machine cleanly (run_id-scoped
    # advance keys), never collide on instance-local pass counters.
    database, repository, policy, router = mission_fixture(tmp_path)
    first = MissionLoop(
        database, router, ScriptedAdapter(repository, always_fail=True),
        policy=policy, max_repairs=5)
    failed = first.run_step(**step_kwargs())
    assert failed.outcome == FAILED
    assert database.row("missions", "mission_id", "mission-1")["state"] == "REPAIRING"

    # Brand-new instance resumes: repair delivers, verifier PASSes, commit.
    resume = MissionLoop(
        database, router, ScriptedAdapter(repository),
        policy=policy, max_repairs=5)
    outcome = resume.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == PASSED
    assert outcome.commit_head is not None
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert (repository / "result.txt").exists()


# ---------------------------------------------------------------------------
# GATE: a fake mission with a deliberately failing builder step is repaired
# and continues to the next useful action without owner prompting.
# ---------------------------------------------------------------------------

def test_gate_deliberate_failure_repaired_and_committed_without_owner(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository)
    loop = MissionLoop(database, router, adapter, policy=policy, max_repairs=2)

    outcome = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    # Repaired: PASSED after the repair cycle, no owner prompting anywhere.
    assert outcome.outcome == PASSED
    assert outcome.step_id == "step-1"
    assert outcome.commit_head is not None
    assert (repository / "result.txt").read_text(encoding="utf-8") == "repaired\n"

    # The deliberate failure was caught by the verifier and repaired.
    assert adapter.attempts["builder"] == 1
    assert adapter.attempts["repair"] == 1
    assert adapter.attempts["verifier"] == 2  # FAIL then PASS

    # Durable ledger: 4 agent runs, 2 verdicts (FAIL then PASS), 1 evidence
    # bundle, and a local commit created only after the verified PASS.
    with database.connect() as connection:
        runs = connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
        verdicts = connection.execute(
            """SELECT v.status FROM verification_verdicts v
               JOIN events e ON e.event_id = v.last_event_id
               WHERE v.mission_id = 'mission-1'
               ORDER BY e.sequence"""
        ).fetchall()
        bundles = connection.execute("SELECT COUNT(*) FROM evidence_bundles").fetchone()[0]
    assert runs == 4
    assert [row["status"] for row in verdicts] == ["FAIL", "PASS"]
    assert bundles == 1
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "2"
    message = git_out("log", "-1", "--format=%s", cwd=repository)
    assert message.startswith("jarvis: mission-1 step-1 verified PASS (run ")
    assert git_out("diff", "--name-only", "HEAD~1..HEAD", cwd=repository) == "result.txt"

    # Mission continues to the next action deterministically: with the
    # requirement consumed, the loop records honest waiting — no invented work.
    second = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))
    assert second.outcome == WAITING_OUTCOME
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert mission["next_action"] is None


def test_proposal_validation_rejects_unknown_and_nondispatchable(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    with pytest.raises(ProposalError):
        propose_next_action(database, "mission-forged")

    # Advance to EXECUTING -> not dispatchable until the loop returns it.
    from jarvis_foundry.mission.loop import MissionLoop as _Loop
    loop = _Loop(database, router, FakeHermesAdapter(), policy=policy)
    loop._advance("mission-1", "READY")
    loop._advance("mission-1", "EXECUTING")
    with pytest.raises(ProposalError):
        propose_next_action(database, "mission-1")


def test_honest_waiting_when_no_next_action(tmp_path):
    database, repository, policy, router = mission_fixture(
        tmp_path, next_action=None)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    outcome = loop.run_step(**step_kwargs())

    assert outcome.outcome == WAITING_OUTCOME
    assert len(adapter.received) == 0          # nothing was dispatched
    with database.connect() as connection:
        runs = connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
    assert runs == 0                           # zero events, zero invented work
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"


def test_repair_budget_exhaustion_blocks_precisely(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository, always_fail=True)
    loop = MissionLoop(database, router, adapter, policy=policy, max_repairs=2)

    outcome = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "repair budget exhausted" in outcome.reason
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"
    # No commit was ever created after the failed verifications.
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "1"
    # The loop is bounded: exactly max_repairs + 1 dispatch cycles, no hang.
    assert adapter.attempts["builder"] + adapter.attempts["repair"] <= 4


def test_builder_session_failure_blocks_mission(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)

    class BuilderDown(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            if manifest.role == "builder":
                from jarvis_foundry.roles.adapter import ProviderUnavailable
                raise ProviderUnavailable("builder provider down")
            return super().dispatch(manifest, heartbeat=heartbeat)

    loop = MissionLoop(database, router, BuilderDown(), policy=policy)
    outcome = loop.run_until_settled(**step_kwargs())

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "provider_unavailable" in (outcome.reason or "")
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"


def test_verifier_outage_blocks_mission_precisely(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)

    class VerifierDown(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            if manifest.role == "verifier":
                from jarvis_foundry.roles.adapter import ProviderUnavailable
                raise ProviderUnavailable("verifier provider down")
            return super().dispatch(manifest, heartbeat=heartbeat)

    loop = MissionLoop(database, router, VerifierDown(), policy=policy)
    outcome = loop.run_until_settled(**step_kwargs())

    assert outcome.outcome == BLOCKED_OUTCOME
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"
    # The verification verdict is durably BLOCKED with the dependency named.
    verdict = database.row("verification_verdicts", "verdict_id", outcome.verdict_id)
    assert verdict is not None
    assert verdict["status"] == "BLOCKED"
    assert verdict["blocking_dependency"] == "provider_unavailable"


def test_loop_continues_to_second_scheduled_action(tmp_path):
    # After the first step PASSes, the executive schedules the next action;
    # the loop picks it up and completes it without any owner prompting.
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository)
    loop = MissionLoop(database, router, adapter, policy=policy)

    first = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))
    assert first.outcome == PASSED
    assert adapter.attempts["builder"] == 1  # first step passed first try

    # Executive schedules the next requirement (direct projection update
    # simulates the scheduler; the ledger-based scheduling path is executive
    # layer work beyond this gate).
    with database.transaction() as connection:
        connection.execute(
            "UPDATE missions SET next_action = ? WHERE mission_id = 'mission-1'",
            ("deliver the second feature",))

    second = loop.run_until_settled(
        **step_kwargs(step_id="step-2"), commit_envelope=commit_envelope(repository))

    assert second.outcome == PASSED
    assert second.step_id == "step-2"
    assert (repository / "result.txt").read_text(encoding="utf-8") == "delivered\n"
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "3"
    third = loop.run_until_settled(
        **step_kwargs(step_id="step-2"), commit_envelope=commit_envelope(repository))
    assert third.outcome == WAITING_OUTCOME


def test_commit_governed_by_policy_denial_blocks(tmp_path):
    # Commit is an action: when the policy denies it (capability not ready),
    # the loop must NOT commit — it blocks precisely instead.
    database, repository, policy, router = mission_fixture(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE resources SET capabilities_json = ? WHERE resource_id = 'resource-1'",
            (json.dumps({
                "read_registered_files": "READY",
                "write_registered_worktree_files": "READY",
                "run_registered_local_commands": "READY",
                "git_local_commit": "PROBING",
                "git_push": "DISCOVERED",
            }),))
    adapter = ScriptedAdapter(repository)
    loop = MissionLoop(database, router, adapter, policy=policy)

    outcome = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "commit after PASS failed" in outcome.reason
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "1"
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"


def test_f3_builder_killed_after_mutation_step_reclaimed_without_duplicates(tmp_path):
    """F3 (plan): builder killed after mutation, before result.

    The killed builder's durable aftermath: mission in EXECUTING, its run
    row RUNNING with an expired role lease, and partial work left in the
    worktree. The supervisor's deterministic recovery (reaper + mission
    resume) must make the step re-claimable by a FRESH loop instance: one
    fresh dispatch, one verified PASS, one commit, no duplicate events, and
    the partial work preserved in the committed artifact.
    """
    from jarvis_foundry.roles.reaper import (
        reap_stale_role_runs,
        recover_stale_mission_state,
        role_lease_key,
    )

    database, repository, policy, router = mission_fixture(tmp_path)

    # --- durable aftermath of the killed builder -------------------------
    # (exactly what a crashed dispatcher leaves: mission advanced to
    # EXECUTING, agent_run.started appended, role lease held, then death)
    for state, next_action in (("READY", "deliver feature X"),
                               ("EXECUTING", "deliver feature X")):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": "mission-1", "state": state, "next_action": next_action},
            source_type="supervisor", source_id="mission-loop",
            idempotency_key=f"mission.state_changed:mission-1:{state}:run-killed",
        ))
    database.append(Event.create(
        "agent_run.started",
        {"run_id": "run-killed", "mission_id": "mission-1", "step_id": "step-1",
         "role": "builder", "provider": "opencode_go", "model": "test-model",
         "context_manifest_hash": "0" * 64, "tool_policy": []},
        source_type="supervisor", source_id="role-dispatcher",
        idempotency_key="agent_run.started:run-killed:opencode_go",
    ))
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (role_lease_key("builder", "mission-1", "step-1"), "run-killed",
             "2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z"),
        )
    # partial mutation: the builder's unfinished work survives on disk
    (repository / "result.txt").write_text("partial\\n", encoding="utf-8")
    assert git_out("status", "--porcelain", cwd=repository) != ""

    # --- deterministic recovery ------------------------------------------
    reaped = reap_stale_role_runs(database)
    assert reaped == ["run-killed"]
    recovered = recover_stale_mission_state(database)
    assert recovered == ["mission-1"]
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert mission["next_action"] == "deliver feature X"

    # --- fresh loop instance re-claims the step --------------------------
    fresh = MissionLoop(database, router, ScriptedAdapter(repository), policy=policy)
    outcome = fresh.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == PASSED
    assert outcome.step_id == "step-1"
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    # exactly one commit (baseline + the fresh worker's verified commit)
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "2"
    # the partial work was preserved and finished deliberately
    assert (repository / "result.txt").exists()
    # exactly ONE completed BUILDER run for the step; the killed run FAILED.
    # (repair/verifier role runs for the step are legitimate and separate)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT run_id, status FROM agent_runs "
            "WHERE step_id = 'step-1' AND role = 'builder' ORDER BY run_id"
        ).fetchall()
    assert len(rows) == 2
    statuses = {r["run_id"]: r["status"] for r in rows}
    assert statuses["run-killed"] == "FAILED"
    fresh_builder_runs = [rid for rid, status in statuses.items()
                          if rid != "run-killed" and status == "COMPLETED"]
    assert len(fresh_builder_runs) == 1  # one fresh worker, one completion
    # no duplicate recovery events: exactly ONE session-loss resume recorded
    events = [e for e in database.events() if e.event_type == "mission.state_changed"]
    session_loss_resumes = [e for e in events
                            if e.payload.get("reason") == "resume_after_session_loss"
                            and e.payload.get("state") == "ACTION_SELECTION"]
    assert len(session_loss_resumes) == 1
    assert database.quick_check() == "ok"
