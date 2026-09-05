"""Canonical MissionLoop integration through the hybrid worker bridge."""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.control.registration import (
    inspect_git_resource,
    registration_event,
)
from jarvis_foundry.events import Event
from jarvis_foundry.mission.loop import PASSED, MissionLoop
from jarvis_foundry.roles.manifest import ContextManifest
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.workforce.contracts import WorkPackage
from jarvis_foundry.workforce.state import create_work_package
from jarvis_foundry.workforce.worktrees import WorktreePreparation
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.base import ProviderUnavailable
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES, WORKER_OPENHANDS
from jarvis_foundry.workers.evidence import EvidenceReconciler
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.result_contract import RESULT_FILENAME
from jarvis_foundry.workers.router import READY, WorkerRouter

from worker_test_helpers import make_controller, make_repository, mission_database


def durable_bridge_package(
    database,
    repository: Path,
    *,
    package_id: str,
    effect_action_key: str | None = None,
    cost_budget_usd: float | None = 1.0,
    required_tests: tuple[str, ...] = (),
) -> WorkPackage:
    source_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    suffix = hashlib.sha256(package_id.encode("utf-8")).hexdigest()[:12]
    branch_name = f"workforce-test-{suffix}"
    worktree = repository.parent / f"{repository.name}-{suffix}-worktree"
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree),
            source_sha,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    database.append(registration_event(
        inspect_git_resource(worktree, resource_id="resource-1"),
        source_id="test-suite",
    ))

    def git_path(root: Path, *args: str) -> Path:
        raw = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        path = Path(raw)
        return (
            path.resolve()
            if path.is_absolute()
            else (root / path).resolve()
        )

    package = WorkPackage(
        package_id=package_id,
        parent_objective_id="objective-worker-bridge",
        mission_id="mission-1",
        step_id="step-1",
        role="builder",
        capability="git_local_commit",
        bounded_goal="exercise the canonical worker bridge",
        acceptance_criteria=("worker result remains evidence bound",),
        source_sha=source_sha,
        repository_id="resource-1",
        branch_name=branch_name,
        worktree_path=str(worktree.resolve()),
        allowed_paths=(".",),
        cost_budget_usd=cost_budget_usd,
        effect_action_key=effect_action_key,
        required_tests=required_tests,
    )
    create_work_package(database, package)
    database.append(Event.create(
        "workforce.worktree_prepared",
        WorktreePreparation(
            package_id=package.package_id,
            resource_id=package.repository_id,
            source_repository=str(repository.resolve()),
            worktree_path=str(worktree.resolve()),
            branch_name=package.branch_name,
            base_sha=package.source_sha,
            claim_id=f"claim:{package.package_id}:test",
            repository_common_dir=str(
                git_path(worktree, "rev-parse", "--git-common-dir")
            ),
            source_git_dir=str(
                git_path(repository, "rev-parse", "--git-dir")
            ),
            worktree_git_dir=str(
                git_path(worktree, "rev-parse", "--git-dir")
            ),
        ).to_payload(),
        source_type="test",
        source_id="test-suite",
        correlation_id=package.package_id,
        idempotency_key=f"workforce.worktree_prepared:{package.package_id}",
    ))
    return package


class MutatingScriptedAdapter(ScriptedWorkerAdapter):
    def __init__(self, framework, *, write_files=(), content=None,
                 outcome=None):
        super().__init__(framework, outcome=outcome)
        self.write_files = tuple(write_files)
        self.content = content

    def dispatch(self, contract, *, heartbeat):
        for rel in self.write_files:
            path = contract.working_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.content or f"content of {rel}\n",
                            encoding="utf-8")
        return super().dispatch(contract, heartbeat=heartbeat)


class CostedScriptedAdapter(ScriptedWorkerAdapter):
    def __init__(self, framework, *, cost_usd):
        super().__init__(framework)
        self.cost_usd = float(cost_usd)

    def dispatch(self, contract, *, heartbeat):
        envelope = super().dispatch(contract, heartbeat=heartbeat)
        return replace(
            envelope,
            usage_availability="available",
            usage_cost=self.cost_usd,
        )


class MalformedUsageAvailabilityAdapter(ScriptedWorkerAdapter):
    def __init__(self, framework, *, availability, cost=0.1):
        super().__init__(framework)
        self.availability = availability
        self.cost = cost

    def dispatch(self, contract, *, heartbeat):
        envelope = super().dispatch(contract, heartbeat=heartbeat)
        return replace(
            envelope,
            usage_availability=self.availability,
            usage_cost=self.cost,
        )


class MutatingMalformedUsageAdapter(MutatingScriptedAdapter):
    def dispatch(self, contract, *, heartbeat):
        envelope = super().dispatch(contract, heartbeat=heartbeat)
        return replace(
            envelope,
            usage_availability="unknown",
            usage_cost=None,
        )


class MutatingRaisingAdapter(ScriptedWorkerAdapter):
    def __init__(self, framework, *, provider_failure):
        super().__init__(framework)
        self.provider_failure = provider_failure

    def dispatch(self, contract, *, heartbeat):
        self.received.append(contract)
        heartbeat()
        path = contract.working_root / "observed-after-crash.py"
        path.write_text("partial mutation\n", encoding="utf-8")
        if self.provider_failure:
            raise ProviderUnavailable("worker provider failed after mutation")
        raise RuntimeError("worker crashed after mutation")


class TimedScriptedAdapter(ScriptedWorkerAdapter):
    def __init__(self, framework, *, clock, elapsed_seconds):
        super().__init__(framework)
        self.clock = clock
        self.elapsed_seconds = float(elapsed_seconds)

    def dispatch(self, contract, *, heartbeat):
        envelope = super().dispatch(contract, heartbeat=heartbeat)
        self.clock[0] += self.elapsed_seconds
        return envelope


class DeadlineRecordingReconciler(EvidenceReconciler):
    def __init__(self, *, monotonic):
        super().__init__(monotonic=monotonic)
        self.test_deadline_monotonic = None
        self.test_commands = None

    def reconcile(self, *args, **kwargs):
        self.test_deadline_monotonic = kwargs.get(
            "test_deadline_monotonic"
        )
        self.test_commands = kwargs.get("test_commands")
        return super().reconcile(*args, **kwargs)


def test_bridge_preserves_observed_worker_cost_in_role_result(tmp_path):
    database, _ = mission_database(tmp_path)
    router = WorkerRouter()
    worker = CostedScriptedAdapter(WORKER_HERMES, cost_usd=0.375)
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=router,
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-cost",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="inspect without changing files",
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "COMPLETED"
    assert result.cost_usd == 0.375


def test_bridge_rejects_a_package_bound_to_another_registered_checkout(
    tmp_path,
):
    database, repository = mission_database(tmp_path)
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-wrong-checkout",
    )
    repository = Path(package.worktree_path)
    other_repository = make_repository(tmp_path, "other-repo")
    database.append(registration_event(
        inspect_git_resource(other_repository, resource_id="resource-1"),
        source_id="adversarial-test",
    ))
    worker = CostedScriptedAdapter(WORKER_HERMES, cost_usd=0.1)
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-wrong-checkout",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="reject the substituted checkout",
        budget_usd=0.5,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-wrong-checkout:a1",
    )

    with pytest.raises(
        RuntimeError,
        match="registered resource root does not match",
    ):
        bridge.dispatch(manifest, heartbeat=lambda: None)

    assert worker.received == []


@pytest.mark.parametrize(
    ("role", "manifest_phase", "expected_phase"),
    (
        ("builder", "builder_admission", "builder_admission"),
        ("builder", "job_delta", "job_delta"),
        ("repair", "job_delta", "job_delta"),
        ("verifier", "job_delta", "job_delta"),
    ),
)
def test_package_working_root_consumes_the_manifest_lifecycle_phase(
    tmp_path,
    monkeypatch,
    role,
    manifest_phase,
    expected_phase,
):
    database, repository = mission_database(tmp_path)
    package = durable_bridge_package(
        database,
        repository,
        package_id=f"package-working-root-{role}",
    )
    repository = Path(package.worktree_path)
    phases = []
    from jarvis_foundry.workers import mission_bridge

    def validate_worktree(
        _database,
        _package,
        *,
        resource_id,
        phase,
    ):
        assert resource_id == package.repository_id
        phases.append(phase)
        return repository

    monkeypatch.setattr(
        mission_bridge,
        "validate_execution_worktree",
        validate_worktree,
    )
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({
            WORKER_HERMES: ScriptedWorkerAdapter(WORKER_HERMES),
        }),
    )
    manifest = ContextManifest(
        run_id=f"run-working-root-{role}",
        role=role,
        mission_id="mission-1",
        step_id="step-1",
        resource_id=package.repository_id,
        capability=(
            "verification" if role == "verifier" else package.capability
        ),
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group=role,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=f"workforce:{package.package_id}:a1",
        worktree_phase=manifest_phase,
    )

    assert bridge._working_root(manifest) == repository
    assert phases == [expected_phase]


def test_bridge_blocks_budgeted_workforce_when_cost_telemetry_is_unavailable(
    tmp_path,
):
    database, repository = mission_database(tmp_path)
    effect_action_key = "effect:package-cost-unavailable"
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-cost-unavailable",
        effect_action_key=effect_action_key,
    )
    repository = Path(package.worktree_path)
    worker = ScriptedWorkerAdapter(WORKER_HERMES)
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-cost-unavailable",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="inspect without changing files",
        budget_usd=0.01,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-cost-unavailable:a1",
        effect_action_key=effect_action_key,
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "BLOCKED"
    assert result.error_class == "workforce_cost_telemetry_unavailable"
    assert "requires observed usage cost" in str(result.error_detail)
    assert result.cost_usd == 0.0
    assert result.effect_action_key == effect_action_key


def test_bridge_blocks_zero_dollar_package_without_cost_telemetry(tmp_path):
    database, repository = mission_database(tmp_path)
    effect_action_key = "effect:package-zero-cost-unavailable"
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-zero-cost-unavailable",
        effect_action_key=effect_action_key,
        cost_budget_usd=0.0,
    )
    repository = Path(package.worktree_path)
    worker = ScriptedWorkerAdapter(WORKER_HERMES)
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-zero-cost-unavailable",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="do not treat unavailable usage as observed zero",
        budget_usd=0.0,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-zero-cost-unavailable:a1",
        effect_action_key=effect_action_key,
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "BLOCKED"
    assert result.verdict == "NOT_TESTED"
    assert result.error_class == "workforce_cost_telemetry_unavailable"
    assert "returned no usable cost telemetry" in str(result.error_detail)
    assert result.effect_action_key == effect_action_key


def test_bridge_accepts_observed_zero_cost_for_zero_dollar_package(tmp_path):
    database, repository = mission_database(tmp_path)
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-zero-cost-observed",
        cost_budget_usd=0.0,
    )
    repository = Path(package.worktree_path)
    worker = CostedScriptedAdapter(WORKER_HERMES, cost_usd=0.0)
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-zero-cost-observed",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="accept zero only when telemetry reports zero",
        budget_usd=0.0,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-zero-cost-observed:a1",
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "COMPLETED"
    assert result.error_class is None
    assert result.cost_usd == 0.0


def test_bridge_keeps_no_budget_package_behavior_without_cost_telemetry(
    tmp_path,
):
    database, repository = mission_database(tmp_path)
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-no-cost-budget",
        cost_budget_usd=None,
    )
    repository = Path(package.worktree_path)
    worker = ScriptedWorkerAdapter(WORKER_HERMES)
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-no-cost-budget",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="preserve package behavior without a cost budget",
        budget_usd=0.0,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-no-cost-budget:a1",
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "COMPLETED"
    assert result.error_class is None
    assert result.cost_usd == 0.0


@pytest.mark.parametrize(
    ("availability", "cost"),
    [
        (True, 0.1),
        ("available-ish", 0.1),
        ("unknown", 0.1),
        ("available", True),
    ],
)
def test_bridge_rejects_malformed_cost_availability(
    tmp_path,
    availability,
    cost,
):
    database, repository = mission_database(tmp_path)
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-malformed-cost-availability",
    )
    repository = Path(package.worktree_path)
    worker = MalformedUsageAvailabilityAdapter(
        WORKER_HERMES,
        availability=availability,
        cost=cost,
    )
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-malformed-cost-availability",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="reject ambiguous usage telemetry",
        budget_usd=0.5,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-malformed-cost-availability:a1",
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "BLOCKED"
    assert result.error_class == "workforce_cost_telemetry_unavailable"
    assert result.cost_usd == 0.0


def test_bridge_reconciles_worker_delta_before_blocking_unknown_cost(
    tmp_path,
):
    database, repository = mission_database(tmp_path)
    effect_action_key = "effect:package-unknown-cost-delta"
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-unknown-cost-delta",
        effect_action_key=effect_action_key,
    )
    repository = Path(package.worktree_path)
    worker = MutatingMalformedUsageAdapter(
        WORKER_HERMES,
        write_files=("observed.py",),
        outcome=ScriptedOutcome(files_changed=("observed.py",)),
    )
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-unknown-cost-delta",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="observe the worker delta before blocking",
        budget_usd=0.5,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-unknown-cost-delta:a1",
        effect_action_key=effect_action_key,
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "BLOCKED"
    assert result.error_class == "workforce_cost_telemetry_unavailable"
    assert result.effect_action_key == effect_action_key
    assert repository.joinpath("observed.py").is_file()
    assert "[jarvis-observed] changed=observed.py" in result.evidence
    assert "JARVIS observed changed files: observed.py" in str(
        result.error_detail
    )


@pytest.mark.parametrize("provider_failure", (False, True))
def test_bridge_reconciles_delta_when_worker_raises_before_envelope(
    tmp_path,
    provider_failure,
):
    database, repository = mission_database(tmp_path)
    package_id = f"package-unknown-cost-raise-{provider_failure}"
    effect_action_key = f"effect:{package_id}"
    package = durable_bridge_package(
        database,
        repository,
        package_id=package_id,
        effect_action_key=effect_action_key,
    )
    repository = Path(package.worktree_path)
    worker = MutatingRaisingAdapter(
        WORKER_HERMES,
        provider_failure=provider_failure,
    )
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id=f"run-unknown-cost-raise-{provider_failure}",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="preserve partial mutation evidence after a crash",
        budget_usd=0.5,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=f"workforce:unknown-cost-raise-{provider_failure}:a1",
        effect_action_key=effect_action_key,
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "BLOCKED"
    assert result.error_class == "workforce_cost_telemetry_unavailable"
    assert result.effect_action_key == effect_action_key
    assert repository.joinpath("observed-after-crash.py").is_file()
    assert (
        "[jarvis-observed] changed=observed-after-crash.py"
        in result.evidence
    )
    assert "JARVIS observed changed files: observed-after-crash.py" in str(
        result.error_detail
    )


def test_bridge_stops_internal_fallback_when_worker_cost_is_unknown(tmp_path):
    database, repository = mission_database(tmp_path)
    effect_action_key = "effect:package-internal-cost-unknown"
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-internal-cost-unknown",
        effect_action_key=effect_action_key,
    )
    repository = Path(package.worktree_path)
    router = WorkerRouter()
    router.mark_ready(
        WORKER_OPENHANDS,
        state=READY,
        note="bounded worker ready",
        evidence=("test readiness",),
    )
    primary = ScriptedWorkerAdapter(WORKER_OPENHANDS, fail_provider=True)
    fallback = CostedScriptedAdapter(WORKER_HERMES, cost_usd=0.1)
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=router,
        registry=WorkerRegistry({
            WORKER_OPENHANDS: primary,
            WORKER_HERMES: fallback,
        }),
        role_signals={
            "builder": {
                "bounded": True,
                "explicit_acceptance": True,
                "tests_available": True,
                "narrow_scope": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-internal-cost-unknown",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="do not hide an unknown internal worker charge",
        budget_usd=0.5,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-internal-cost-unknown:a1",
        effect_action_key=effect_action_key,
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "BLOCKED"
    assert result.error_class == "workforce_cost_telemetry_unavailable"
    assert result.provider == manifest.provider
    assert result.model == manifest.model
    assert result.effect_action_key == effect_action_key
    assert result.evidence[0] == f"worker={WORKER_OPENHANDS}"
    assert any(
        item.startswith(
            "[jarvis-reconciliation] worker did not complete"
        )
        for item in result.evidence
    )
    assert len(primary.received) == 1
    assert fallback.received == []
    assert not [
        event
        for event in database.events()
        if event.event_type == "worker.fallback_triggered"
    ]


def test_bridge_keeps_unknown_cost_legacy_behavior_without_workforce_budget(
    tmp_path,
):
    database, _ = mission_database(tmp_path)
    worker = ScriptedWorkerAdapter(WORKER_HERMES)
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-cost-legacy",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        instructions="inspect without changing files",
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "COMPLETED"
    assert result.error_class is None
    assert result.cost_usd == 0.0


def test_bridge_caps_worker_and_reconciler_to_shared_lifecycle_deadline(
    tmp_path,
):
    database, _ = mission_database(tmp_path)
    clock = [0.0]
    router = WorkerRouter()
    worker = TimedScriptedAdapter(
        WORKER_HERMES,
        clock=clock,
        elapsed_seconds=3,
    )
    reconciler = DeadlineRecordingReconciler(
        monotonic=lambda: clock[0]
    )
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=router,
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        reconciler=reconciler,
        monotonic=lambda: clock[0],
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-deadline",
        role="builder",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="builder",
        duration_seconds=10,
        instructions="inspect without changing files",
    )
    bridge.set_lifecycle_deadline("run-deadline", 5.0)

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "COMPLETED"
    assert worker.received[0].duration_seconds == 5
    assert reconciler.test_deadline_monotonic == 5.0


def test_verifier_carries_required_tests_without_executing_them(
    tmp_path,
    monkeypatch,
):
    database, repository = mission_database(tmp_path)
    from jarvis_foundry.workers import mission_bridge

    monkeypatch.setattr(
        mission_bridge,
        "validate_execution_worktree",
        lambda *_args, **_kwargs: repository,
    )
    required_tests = ("python -c \"raise SystemExit(99)\"",)
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-verifier-test-identity",
        required_tests=required_tests,
    )
    repository = Path(package.worktree_path)
    worker = CostedScriptedAdapter(WORKER_HERMES, cost_usd=0.1)
    reconciler = DeadlineRecordingReconciler(
        monotonic=lambda: 0.0,
    )
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        reconciler=reconciler,
        role_signals={
            "verifier": {
                "bounded": True,
                "explicit_acceptance": True,
                "tests_available": True,
                "narrow_scope": True,
            },
        },
    )
    manifest = ContextManifest(
        run_id="run-verifier-test-identity",
        role="verifier",
        mission_id="mission-1",
        step_id="step-1",
        resource_id="resource-1",
        capability="verification",
        provider="opencode_go",
        model="deepseek-v4-flash",
        isolation_group="verifier",
        instructions="inspect the frozen result without mutation",
        allowed_paths=(".",),
        required_tests=required_tests,
        budget_usd=0.5,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-verifier-test-identity:a1",
    )

    result = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "COMPLETED"
    assert worker.received[0].required_tests == required_tests
    assert reconciler.test_commands == ()


def test_canonical_mission_loop_dispatches_through_hybrid_workers(tmp_path):
    """The existing MissionLoop executes a step through the worker bridge
    (Hermes primary path) and returns to next-action selection."""
    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    router.mark_ready(WORKER_OPENHANDS, state=READY,
                      note="containment proven in test",
                      evidence=("test-proof",))
    hermes = MutatingScriptedAdapter(
        WORKER_HERMES, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    verifier = ScriptedWorkerAdapter(
        WORKER_OPENHANDS, outcome=ScriptedOutcome(verdict="PASS"))
    registry = WorkerRegistry(
        {WORKER_HERMES: hermes, WORKER_OPENHANDS: verifier})
    adapter = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        role_signals={"builder": {"ambiguous": True,
                                  "root_cause_required": True},
                       "verifier": {"bounded": True,
                                    "explicit_acceptance": True,
                                    "tests_available": True,
                                    "narrow_scope": True}},
    )
    loop = MissionLoop(database, router=RoleRouter(), adapter=adapter)

    from jarvis_foundry.domain import ActionEnvelope
    envelope = ActionEnvelope(
        action_id="commit-step-1", mission_id="mission-1",
        resource_id="resource-1", action_kind="git_commit",
        target=repository, capability="git_local_commit",
        expected_effects=("create local commit",),
        risk_class="C1", verification_required=True,
        rollback={"kind": "git_reset_soft"},
    )
    outcome = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=repository,
        instructions="implement feature.py and run the tests",
        expected_effects=("feature.py created",),
        rollback={"kind": "git_reset_soft"},
        commit_envelope=envelope,
    )
    assert outcome.outcome == PASSED
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert hermes.received[0].run_id == outcome.run_id


def test_canonical_mission_loop_rejects_false_success_from_bridge(tmp_path):
    """A worker claiming success with no real change must NOT PASS through
    the canonical loop: the bridge returns NOT_TESTED/evidence_mismatch."""
    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=(),
        outcome=ScriptedOutcome(claimed_outcome="done",
                                files_changed=("feature.py",)))
    registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    router.mark_ready(WORKER_OPENHANDS, state=READY, note="containment proven",
                      evidence=("test-proof",))
    adapter = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        role_signals={"builder": {"bounded": True, "explicit_acceptance": True,
                                  "tests_available": True, "narrow_scope": True}},
    )
    loop = MissionLoop(database, router=RoleRouter(), adapter=adapter)
    outcome = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=repository,
        instructions="implement feature.py",
        expected_effects=("feature.py created",),
        rollback={"kind": "git_reset_soft"},
    )
    # The loop must NOT accept: either FAIL (repair) or BLOCKED, never PASS.
    assert outcome.outcome != PASSED
    assert outcome.outcome in ("FAILED", "BLOCKED")


def test_evidence_mismatch_is_not_accepted_even_if_verifier_would_pass(tmp_path):
    """Builder mutates the tree but claims files_changed=[] while an eligible
    verifier is scripted to PASS. The loop must BLOCK before verification;
    a PASS wash-out is the live accept hole."""
    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    router.mark_ready(WORKER_OPENHANDS, state=READY,
                      note="containment proven in test",
                      evidence=("test-proof",))
    router.mark_ready(WORKER_HERMES, state=READY,
                      note="containment proven in test",
                      evidence=("test-proof",))
    builder = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=()))
    verifier = ScriptedWorkerAdapter(
        WORKER_HERMES, outcome=ScriptedOutcome(verdict="PASS"))
    registry = WorkerRegistry(
        {WORKER_OPENHANDS: builder, WORKER_HERMES: verifier})
    adapter = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        role_signals={
            "builder": {"bounded": True, "explicit_acceptance": True,
                        "tests_available": True, "narrow_scope": True,
                        "reversible": True},
            "verifier": {"bounded": True, "explicit_acceptance": True,
                         "tests_available": True, "narrow_scope": True},
        },
    )
    loop = MissionLoop(database, router=RoleRouter(), adapter=adapter)
    outcome = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=repository,
        instructions="implement feature.py",
        expected_effects=("feature.py created",),
        rollback={"kind": "git_reset_soft"},
    )
    assert outcome.outcome != PASSED
    assert outcome.outcome in ("FAILED", "BLOCKED")
    assert verifier.received == []


def test_bridge_refuses_dispatch_when_stop_sentinel_is_active(tmp_path):
    database, repository = mission_database(tmp_path)
    stop = tmp_path / "EMERGENCY_STOP"
    stop.write_text("{}", encoding="utf-8")
    router = WorkerRouter()
    router.mark_ready(WORKER_HERMES, state=READY, note="x", evidence=("e",))
    builder = ScriptedWorkerAdapter(WORKER_HERMES)
    registry = WorkerRegistry({WORKER_HERMES: builder})
    adapter = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        stop_path=stop,
        role_signals={"builder": {"ambiguous": True,
                                  "root_cause_required": True}},
    )
    from jarvis_foundry.roles.results import BLOCKED

    manifest = ContextManifest(
        run_id="run-stop", role="builder", mission_id="mission-1",
        step_id="step-1", resource_id="resource-1",
        capability="write_registered_worktree_files",
        provider="opencode_go", model="deepseek-v4-flash",
        isolation_group="g", tool_policy=("bounded",),
        budget_usd=0.0, duration_seconds=30,
        instructions="do not run",
    )
    result = adapter.dispatch(manifest, heartbeat=lambda: None)
    assert result.status == BLOCKED
    assert result.error_class == "emergency_stop"
    assert builder.received == []


def test_mission_bridge_enriches_instructions_with_result_contract(tmp_path):
    """The live MissionLoop -> RoleDispatcher -> HybridWorkerRoleAdapter
    path must append the machine-ingestable result requirement so real
    provider builders can return a durable files_changed claim."""
    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    router.mark_ready(WORKER_OPENHANDS, state=READY,
                      note="containment proven in test",
                      evidence=("test-proof",))
    builder = ScriptedWorkerAdapter(WORKER_OPENHANDS)
    registry = WorkerRegistry({WORKER_OPENHANDS: builder})
    bridge = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        role_signals={"builder": {"bounded": True,
                                  "explicit_acceptance": True,
                                  "tests_available": True,
                                  "narrow_scope": True,
                                  "reversible": True}},
    )
    manifest = ContextManifest(
        run_id="run-bridge-result", role="builder",
        mission_id="mission-1", step_id="step-1",
        resource_id="resource-1", capability="git_local_commit",
        provider="opencode_go", model="deepseek-v4-flash",
        isolation_group="ind", instructions="implement feature.py",
    )
    outcome = bridge.dispatch(manifest, heartbeat=lambda: None)
    assert outcome is not None
    assert builder.received, "bridge did not dispatch the builder"
    prompt = builder.received[0].instructions
    assert RESULT_FILENAME in prompt
    marker = str((repository / RESULT_FILENAME).resolve())
    assert marker in prompt
    assert prompt.index("JARVIS RESULT CONTRACT") < prompt.index("implement feature.py")
