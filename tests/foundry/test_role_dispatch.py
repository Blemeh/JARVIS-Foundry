"""WP4 role dispatcher tests.

Gate (03 spec §16 WP4): a fake mission dispatches three isolated role
sessions (executive, builder, verifier) and records exact context and
provider identity. Plus: leases, heartbeats, provider fallback, structured
results, and the real-Hermes safety boundary.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, EventConflict, canonical_json
from jarvis_foundry.control.registration import (
    inspect_git_resource,
    registration_event,
)
from jarvis_foundry.executive.continuous import (
    canonical_provider_resume_events,
    resume_mission_provider_waits,
    schedule_mission_provider_wait,
    schedule_wait_for_provider,
    trigger_wake,
)
from jarvis_foundry.roles.adapter import (
    AdapterConfigurationError,
    FakeHermesAdapter,
    HermesOneshotAdapter,
    ProviderUnavailable,
)
from jarvis_foundry.roles.dispatcher import RoleDispatcher
from jarvis_foundry.roles.manifest import ContextManifest, verify_manifest_hash
from jarvis_foundry.roles.results import BLOCKED, COMPLETED, FAILED, RoleResult
from jarvis_foundry.roles.router import (
    ProviderRouteCandidate,
    RoleRouter,
    RoutingRequest,
)
from jarvis_foundry.runtime.provider_registry import (
    ProviderReadiness,
    record_provider_readiness,
)
from jarvis_foundry.workforce.contracts import WorkPackage
from jarvis_foundry.workforce.state import create_work_package
from jarvis_foundry.workforce.worktrees import (
    WorkforceResourceError,
    WorktreePreparation,
)


def mission_database(tmp_path: Path, mission_id: str = "mission-1") -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-11T15:33:05Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": "fake mission for WP4 gate",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["three isolated role sessions recorded"]},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    return database


def orient_mission_for_blocking(
    database: FoundryDatabase,
    mission_id: str = "mission-1",
) -> None:
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": mission_id,
            "state": "ORIENTING",
            "next_action": None,
            "reason": "test fixture prepares a valid blocking transition",
        },
        source_type="supervisor",
        source_id="mission-loop",
        correlation_id=mission_id,
        idempotency_key=f"mission.state_changed:{mission_id}:ORIENTING:test",
    ))


def dispatcher(tmp_path, router=None, adapter=None, **kwargs) -> RoleDispatcher:
    database = mission_database(tmp_path)
    return RoleDispatcher(
        database,
        router or RoleRouter(),
        adapter or FakeHermesAdapter(),
        **kwargs,
    )


def run_row(database: FoundryDatabase, run_id: str) -> dict:
    return database.row("agent_runs", "run_id", run_id)


def priced_candidate(
    provider: str,
    cost_usd: float | None,
    *,
    model: str = "muse-spark-1.2-contributor",
) -> ProviderRouteCandidate:
    return ProviderRouteCandidate(
        provider=provider,
        model=model,
        capabilities=(
            "structured_reasoning",
            "code",
            "tools",
            "artifact_inspection",
            "adversarial_verification",
            "adversarial_review",
            "tests",
            "research",
            "multimodal",
        ),
        task_classes=("*",),
        readiness="READY",
        health="READY",
        auth_state="READY",
        availability="AVAILABLE",
        noninteractive_allowed=True,
        cost_usd=cost_usd,
    )


def initialize_git_repository(root: Path) -> tuple[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        subprocess.run(
            ["git", "init", "-b", "main", str(root)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "config", "user.name", "JARVIS Tests"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "config",
                "user.email",
                "jarvis-tests@example.invalid",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "commit", "--allow-empty", "-m", "base"],
            check=True,
            capture_output=True,
            text=True,
        )
    source_sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "-C", str(root), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return source_sha, branch


def _git_path(root: Path, *args: str) -> Path:
    raw = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _linked_package_worktree(
    root: Path,
    *,
    package_id: str,
) -> tuple[Path, Path, str, str]:
    suffix = hashlib.sha256(package_id.encode("utf-8")).hexdigest()[:12]
    target = root
    if target.exists() and any(target.iterdir()):
        target = root / f"worktree-{suffix}"
    source = target.with_name(f"{target.name}-source")
    source_sha, _ = initialize_git_repository(source)
    branch = f"workforce-test-{suffix}"
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "worktree",
            "add",
            "-b",
            branch,
            str(target),
            source_sha,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return source.resolve(), target.resolve(), source_sha, branch


def durable_package(
    database: FoundryDatabase,
    root: Path,
    *,
    package_id: str,
    cost_budget_usd: float | None,
    step_id: str = "step-1",
    role: str = "builder",
    capability: str = "write",
    allowed_paths: tuple[str, ...] = ("result.txt",),
    forbidden_paths: tuple[str, ...] = (),
    expected_artifacts: tuple[str, ...] = (),
    required_tests: tuple[str, ...] = (),
    effect_action_key: str | None = None,
    registered_root: Path | None = None,
    branch_name: str | None = None,
    source_sha: str | None = None,
) -> WorkPackage:
    (
        source_repository,
        worktree_root,
        actual_source_sha,
        actual_branch,
    ) = _linked_package_worktree(root, package_id=package_id)
    registration_root = registered_root or worktree_root
    database.append(registration_event(
        inspect_git_resource(
            registration_root,
            resource_id="resource-1",
        ),
        source_id="test-suite",
    ))
    package = WorkPackage(
        package_id=package_id,
        parent_objective_id="objective-cost-governance",
        mission_id="mission-1",
        step_id=step_id,
        role=role,
        capability=capability,
        bounded_goal="exercise durable cost governance",
        acceptance_criteria=("cost settlement is durable",),
        source_sha=source_sha or actual_source_sha,
        repository_id="resource-1",
        branch_name=branch_name or actual_branch,
        worktree_path=str(worktree_root),
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        expected_artifacts=expected_artifacts,
        required_tests=required_tests,
        cost_budget_usd=cost_budget_usd,
        effect_action_key=effect_action_key,
    )
    create_work_package(database, package)
    common_dir = _git_path(
        worktree_root,
        "rev-parse",
        "--git-common-dir",
    )
    prepared = WorktreePreparation(
        package_id=package.package_id,
        resource_id=package.repository_id,
        source_repository=str(source_repository),
        worktree_path=str(worktree_root),
        branch_name=package.branch_name,
        base_sha=package.source_sha,
        claim_id=f"claim:{package.package_id}:test",
        repository_common_dir=str(common_dir),
        source_git_dir=str(
            _git_path(source_repository, "rev-parse", "--git-dir")
        ),
        worktree_git_dir=str(
            _git_path(worktree_root, "rev-parse", "--git-dir")
        ),
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


class CostedFallbackAdapter:
    def __init__(self):
        self.received = []

    def dispatch(self, manifest, *, heartbeat):
        self.received.append(manifest)
        heartbeat()
        if manifest.provider == "opencode_go":
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=BLOCKED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="BLOCKED",
                error_class="provider_unavailable",
                error_detail="preferred provider failed after usage",
                cost_usd=0.8,
            )
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=COMPLETED,
            provider=manifest.provider,
            model=manifest.model,
            verdict="PASS",
            cost_usd=0.3,
        )


# ---------------------------------------------------------------------------
# GATE: fake mission dispatches three isolated role sessions and records
# exact context and provider identity.
# ---------------------------------------------------------------------------

def test_gate_fake_mission_three_isolated_role_sessions(tmp_path):
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "executive": {"preferred_provider": "opencode_go", "preferred_model": "deepseek-v4-flash"},
        "builder": {"preferred_provider": "opencode_go", "preferred_model": "kimi-k2.7-code"},
        "verifier": {"preferred_provider": "opencode_go", "preferred_model": "deepseek-v4-flash"},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter, source_id="wp4-gate")

    roles = ["executive", "builder", "verifier"]
    results: dict[str, RoleResult] = {}
    for role in roles:
        results[role] = dispatcher.dispatch(
            role=role,
            mission_id="mission-1",
            resource_id="resource-1",
            capability="local_project_write",
            step_id="step-1",
            run_id=f"run-{role}",
            instructions=f"act as the {role} for the fake mission",
        )

    # Structured results: all COMPLETED with verdict PASS and provider identity.
    for role in roles:
        result = results[role]
        assert result.status == COMPLETED
        assert result.verdict == "PASS"
        assert result.provider == "opencode_go"
        assert result.role == role

    # Durable agent_runs: exactly three rows, one per isolated role session,
    # with exact provider identity and a verifiable context manifest hash.
    for role in roles:
        row = run_row(database, f"run-{role}")
        assert row["status"] == "COMPLETED"
        assert row["role"] == role
        assert row["provider"] == "opencode_go"
        assert row["mission_id"] == "mission-1"
        assert row["context_manifest_hash"] == adapter.received[
            roles.index(role)].content_hash()
        assert len(row["context_manifest_hash"]) == 64

    # Exact context recorded: recompute the manifest the adapter received and
    # prove the durable hash matches it exactly.
    for role in roles:
        received = adapter.received[roles.index(role)]
        assert received.mission_id == "mission-1"
        assert received.resource_id == "resource-1"
        assert received.step_id == "step-1"
        assert received.role == role
        assert verify_manifest_hash(received, run_row(database, f"run-{role}")["context_manifest_hash"])

    # Isolation: three distinct run_ids, three distinct lease-free rows.
    assert {r["run_id"] for r in (run_row(database, f"run-{r}") for r in roles)} == {
        "run-executive", "run-builder", "run-verifier"}

    # Heartbeats recorded for every session.
    for role in roles:
        row = run_row(database, f"run-{role}")
        assert row["heartbeat_at"] is not None

    # Structured result payload is durable in result_json.
    for role in roles:
        row = run_row(database, f"run-{role}")
        payload = json.loads(row["result_json"])
        assert payload["status"] == "COMPLETED"
        assert payload["provider"] == "opencode_go"
        assert payload["role"] == role


def test_gate_records_exact_context_and_provider_identity(tmp_path):
    """The gate phrase: 'records exact context and provider identity'."""
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "vertex_ai", "preferred_model": "gemini-x"},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter)

    dispatcher.dispatch(
        role="builder", mission_id="mission-1", resource_id="resource-1",
        capability="local_project_write", step_id="build-1", run_id="run-b1",
        instructions="build smallest correct change",
    )

    received = adapter.received[0]
    assert received.provider == "vertex_ai"
    assert received.model == "gemini-x"
    assert received.capability == "local_project_write"
    row = run_row(database, "run-b1")
    assert row["provider"] == "vertex_ai"          # exact provider identity
    assert row["model"] == "gemini-x"
    assert row["context_manifest_hash"] == received.content_hash()  # exact context
    assert row["step_id"] == "build-1"


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------

def test_dispatch_acquires_and_releases_lease(tmp_path):
    database = mission_database(tmp_path)
    dispatcher = RoleDispatcher(database, RoleRouter(), FakeHermesAdapter())
    dispatcher.dispatch(role="builder", mission_id="mission-1",
                        resource_id="resource-1", capability="write", run_id="run-l1")
    with database.connect() as connection:
        rows = connection.execute("SELECT * FROM leases").fetchall()
    assert rows == []  # released after completion


def test_concurrent_same_step_lease_blocked(tmp_path):
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go", "fallbacks": ()},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter)

    first = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                resource_id="resource-1", capability="write",
                                step_id="step-1", run_id="run-c1")
    assert first.status == COMPLETED

    # Simulate a concurrent holder: acquire the same lease key manually with
    # a different owner before dispatching again.
    from jarvis_foundry.supervisor.leases import LeaseManager
    LeaseManager(database).acquire("role:builder:mission-1:step-1", "other-owner", 300)
    second = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 step_id="step-1", run_id="run-c2")
    assert second.status == BLOCKED
    assert second.error_class == "lease_unavailable"
    # No run row is created (dispatch never started), but the denial is
    # durable as an agent_run.failed event with lease_unavailable class.
    with database.connect() as connection:
        denied = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_run.failed' "
            "AND payload_json LIKE '%lease_unavailable%'"
        ).fetchone()[0]
    assert denied >= 1
    assert run_row(database, "run-c2") is None


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------

def test_heartbeat_events_recorded(tmp_path):
    database = mission_database(tmp_path)
    dispatcher = RoleDispatcher(database, RoleRouter(), FakeHermesAdapter())
    dispatcher.dispatch(role="executive", mission_id="mission-1",
                        resource_id="resource-1", capability="plan", run_id="run-h1")
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_run.heartbeat'"
        ).fetchone()[0]
    assert count >= 1
    row = run_row(database, "run-h1")
    assert row["heartbeat_at"] is not None


@pytest.mark.parametrize(
    ("identity_defect", "message"),
    (
        (
            "resource-root",
            "registered resource root does not match",
        ),
        (
            "branch",
            "branch identity changed",
        ),
        (
            "head",
            "base commit is unavailable",
        ),
    ),
)
def test_workforce_dispatch_binds_the_exact_registered_checkout(
    tmp_path,
    identity_defect,
    message,
):
    database = mission_database(tmp_path)
    package_root = tmp_path / "package-root"
    alternate_root = tmp_path / "alternate-root"
    registered_root = None
    branch_name = None
    source_sha = None
    if identity_defect == "resource-root":
        initialize_git_repository(alternate_root)
        registered_root = alternate_root
    elif identity_defect == "branch":
        branch_name = "wrong-branch"
    else:
        source_sha = "b" * 40
    package = durable_package(
        database,
        package_root,
        package_id=f"package-checkout-{identity_defect}",
        cost_budget_usd=1.0,
        registered_root=registered_root,
        branch_name=branch_name,
        source_sha=source_sha,
    )
    adapter = FakeHermesAdapter()

    with pytest.raises(WorkforceResourceError, match=message):
        RoleDispatcher(
            database,
            RoleRouter(),
            adapter,
            provider_candidates=(priced_candidate("opencode_go", 0.1),),
        ).dispatch(
            role="builder",
            mission_id="mission-1",
            resource_id="resource-1",
            capability="write",
            step_id="step-1",
            run_id=f"workforce:{package.package_id}:a1",
            instructions="stay inside the exact durable checkout",
            budget_usd=1.0,
            package_id=package.package_id,
            package_hash=package.content_hash(),
            attempt_id=f"workforce:{package.package_id}:a1",
            allowed_paths=package.allowed_paths,
        )

    assert adapter.received == []
    assert not [
        event
        for event in database.events()
        if event.event_type == "agent_run.started"
    ]


@pytest.mark.parametrize(
    ("role", "capability", "expected_phase"),
    (
        ("builder", "write", "builder_admission"),
        ("repair", "write", "job_delta"),
        ("verifier", "verification", "job_delta"),
    ),
)
def test_workforce_dispatch_selects_execution_worktree_phase_by_role(
    tmp_path,
    monkeypatch,
    role,
    capability,
    expected_phase,
):
    database = mission_database(tmp_path)
    package_root = tmp_path / "package-root"
    package = durable_package(
        database,
        package_root,
        package_id=f"package-phase-{role}",
        cost_budget_usd=None,
    )
    phases = []
    from jarvis_foundry.workforce import worktrees

    def validate_worktree(
        _database,
        _package,
        *,
        resource_id,
        phase,
    ):
        assert resource_id == package.repository_id
        phases.append(phase)
        return package_root

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        validate_worktree,
    )

    RoleDispatcher(
        database,
        RoleRouter(),
        FakeHermesAdapter(),
    ).validate_workforce_dispatch(
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=f"workforce:{package.package_id}:a1",
        mission_id="mission-1",
        step_id="step-1",
        role=role,
        resource_id=package.repository_id,
        capability=capability,
        duration_seconds=package.duration_seconds,
        allowed_paths=package.allowed_paths,
        forbidden_paths=package.forbidden_paths,
        expected_artifacts=package.expected_artifacts,
        required_tests=package.required_tests,
        effect_action_key=package.effect_action_key,
    )

    assert phases == [expected_phase]


def test_completed_builder_replay_allows_the_existing_job_delta_phase(
    tmp_path,
    monkeypatch,
):
    database = mission_database(tmp_path)
    package_root = tmp_path / "package-root"
    package = durable_package(
        database,
        package_root,
        package_id="package-builder-replay-phase",
        cost_budget_usd=None,
    )
    phases = []
    from jarvis_foundry.workforce import worktrees

    def validate_worktree(
        _database,
        _package,
        *,
        resource_id,
        phase,
    ):
        assert resource_id == package.repository_id
        phases.append(phase)
        return package_root

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        validate_worktree,
    )
    adapter = FakeHermesAdapter()
    role_dispatcher = RoleDispatcher(database, RoleRouter(), adapter)
    run_id = "workforce:package-builder-replay-phase:a1"
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": package.repository_id,
        "capability": package.capability,
        "step_id": "step-1",
        "run_id": run_id,
        "instructions": "replay the completed builder without rerunning it",
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": run_id,
        "allowed_paths": package.allowed_paths,
    }

    first = role_dispatcher.dispatch(**kwargs)
    package_root.joinpath("result.txt").write_text(
        "job-owned delta\n",
        encoding="utf-8",
    )
    replayed = role_dispatcher.dispatch(**kwargs)

    assert replayed == first
    assert phases == ["builder_admission", "job_delta"]
    assert len(adapter.received) == 1
    assert adapter.received[0].worktree_phase == "builder_admission"


def test_existing_failed_workforce_run_requires_fresh_identity(
    tmp_path,
    monkeypatch,
):
    database = mission_database(tmp_path)
    package_root = tmp_path / "package-root"
    package = durable_package(
        database,
        package_root,
        package_id="package-failed-run-reuse",
        cost_budget_usd=None,
    )
    phases = []
    from jarvis_foundry.workforce import worktrees

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        lambda *_args, **kwargs: phases.append(kwargs.get("phase")),
    )
    dispatcher = RoleDispatcher(database, RoleRouter(), FakeHermesAdapter())
    run_id = f"workforce:{package.package_id}:a1"
    config = dispatcher.router.config("builder")
    manifest = dispatcher.manifest_for(
        run_id,
        "builder",
        "mission-1",
        "step-1",
        package.repository_id,
        package.capability,
        "do not reuse terminal workforce identity",
        package.duration_seconds,
        0.0,
        config.preferred_provider,
        config,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=run_id,
        allowed_paths=package.allowed_paths,
    )
    dispatcher._start_run(
        manifest,
        config.preferred_provider,
        config.preferred_model,
    )
    dispatcher._fail_run(run_id, "heartbeat_timeout")

    with pytest.raises(EventConflict, match="requires a fresh run_id"):
        dispatcher.dispatch(
            role="builder",
            mission_id="mission-1",
            resource_id=package.repository_id,
            capability=package.capability,
            step_id="step-1",
            run_id=run_id,
            instructions="do not reuse terminal workforce identity",
            duration_seconds=package.duration_seconds,
            package_id=package.package_id,
            package_hash=package.content_hash(),
            attempt_id=run_id,
            allowed_paths=package.allowed_paths,
        )

    assert phases == []


def test_existing_workforce_run_cannot_relax_different_lineage(
    tmp_path,
    monkeypatch,
):
    database = mission_database(tmp_path)
    package_root = tmp_path / "package-root"
    package = durable_package(
        database,
        package_root,
        package_id="package-stale-run-lineage",
        cost_budget_usd=None,
    )
    phases = []
    from jarvis_foundry.workforce import worktrees

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        lambda *_args, **kwargs: phases.append(kwargs.get("phase")),
    )
    dispatcher = RoleDispatcher(database, RoleRouter(), FakeHermesAdapter())
    run_id = f"workforce:{package.package_id}:a1"
    config = dispatcher.router.config("builder")
    stale = dispatcher.manifest_for(
        run_id,
        "builder",
        "mission-other",
        "step-other",
        package.repository_id,
        package.capability,
        "stale lineage",
        package.duration_seconds,
        0.0,
        config.preferred_provider,
        config,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=run_id,
        allowed_paths=package.allowed_paths,
    )
    dispatcher._start_run(
        stale,
        config.preferred_provider,
        config.preferred_model,
    )

    with pytest.raises(EventConflict, match="different execution lineage"):
        dispatcher.dispatch(
            role="builder",
            mission_id="mission-1",
            resource_id=package.repository_id,
            capability=package.capability,
            step_id="step-1",
            run_id=run_id,
            instructions="new lineage",
            duration_seconds=package.duration_seconds,
            package_id=package.package_id,
            package_hash=package.content_hash(),
            attempt_id=run_id,
            allowed_paths=package.allowed_paths,
        )

    assert phases == []


def test_existing_running_workforce_run_is_never_redispatched(
    tmp_path,
    monkeypatch,
):
    database = mission_database(tmp_path)
    package_root = tmp_path / "package-root"
    package = durable_package(
        database,
        package_root,
        package_id="package-running-run-reuse",
        cost_budget_usd=None,
    )
    phases = []
    from jarvis_foundry.workforce import worktrees

    monkeypatch.setattr(
        worktrees,
        "validate_execution_worktree",
        lambda *_args, **kwargs: (
            phases.append(kwargs.get("phase")) or package_root
        ),
    )
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, RoleRouter(), adapter)
    dispatcher.replay_wait_seconds = 0.01
    run_id = f"workforce:{package.package_id}:a1"
    config = dispatcher.router.config("builder")
    manifest = dispatcher.manifest_for(
        run_id,
        "builder",
        "mission-1",
        "step-1",
        package.repository_id,
        package.capability,
        "keep the live run singular",
        package.duration_seconds,
        0.0,
        config.preferred_provider,
        config,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=run_id,
        allowed_paths=package.allowed_paths,
    )
    dispatcher._start_run(
        manifest,
        config.preferred_provider,
        config.preferred_model,
    )

    result = dispatcher.dispatch(
        role="builder",
        mission_id="mission-1",
        resource_id=package.repository_id,
        capability=package.capability,
        step_id="step-1",
        run_id=run_id,
        instructions="keep the live run singular",
        duration_seconds=package.duration_seconds,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=run_id,
        allowed_paths=package.allowed_paths,
    )

    assert result.status == BLOCKED
    assert result.error_class == "run_in_progress"
    assert phases == ["job_delta"]
    assert adapter.received == []
    assert database.event_count(
        event_type="agent_run.started",
        readonly=True,
    ) == 1


# ---------------------------------------------------------------------------
# Provider fallback
# ---------------------------------------------------------------------------

def test_provider_fallback_on_preferred_failure(tmp_path):
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go",
                    "fallbacks": ("vertex_ai",)},
    })
    adapter = FakeHermesAdapter(fail_provider="opencode_go")
    dispatcher = RoleDispatcher(database, router, adapter)

    result = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 run_id="run-f1")

    assert result.status == COMPLETED
    assert result.provider == "vertex_ai"  # fell back
    row = run_row(database, "run-f1")
    assert row["provider"] == "vertex_ai"
    # The first provider attempt was recorded as a failed run with
    # provider_unavailable, then the fallback completed the same run row.
    assert row["status"] == "COMPLETED"
    # Verifier finding: the completed projection must clear the stale
    # failure_class from the failed attempt (a COMPLETED row never claims
    # provider_unavailable).
    assert row["failure_class"] is None
    with database.connect() as connection:
        failed = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_run.failed'"
        ).fetchone()[0]
    assert failed >= 1
    # Adapter received both manifests with exact provider identity each time.
    assert [m.provider for m in adapter.received] == ["opencode_go", "vertex_ai"]


def test_provider_fallback_aggregates_attempt_cost_once_and_replays(tmp_path):
    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-cost-fallback",
        cost_budget_usd=2.0,
        step_id="step-cost",
    )
    router = RoleRouter().clone_with({
        "builder": {
            "preferred_provider": "opencode_go",
            "fallbacks": ("vertex_ai",),
        },
    })
    adapter = CostedFallbackAdapter()
    candidates = (
        priced_candidate("opencode_go", 0.8),
        priced_candidate("vertex_ai", 0.3),
    )
    dispatcher = RoleDispatcher(
        database,
        router,
        adapter,
        provider_candidates=candidates,
    )
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-cost",
        "run_id": "run-cost-fallback",
        "instructions": "exercise costed provider fallback",
        "budget_usd": 2.0,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-cost-fallback:a1",
        "allowed_paths": package.allowed_paths,
    }

    result = dispatcher.dispatch(**kwargs)

    assert result.status == COMPLETED
    assert result.provider == "vertex_ai"
    assert result.cost_usd == pytest.approx(1.1)
    assert [item.provider for item in adapter.received] == [
        "opencode_go",
        "vertex_ai",
    ]
    row = run_row(database, "run-cost-fallback")
    assert json.loads(row["result_json"])["cost_usd"] == pytest.approx(1.1)
    assert json.loads(row["cost_json"])["usd"] == pytest.approx(1.1)
    cost_events = [
        event for event in database.events()
        if event.event_type == "provider.dispatch_cost_recorded"
    ]
    assert [event.payload["cost_usd"] for event in cost_events] == [0.8, 0.3]
    assert cost_events[0].payload["provider"] == "opencode_go"
    assert cost_events[0].payload["attempt_ordinal"] == 1
    succeeded = next(
        event for event in database.events()
        if event.event_type == "provider.dispatch_succeeded"
    )
    assert succeeded.payload["attempt_cost_usd"] == pytest.approx(0.3)
    assert succeeded.payload["cumulative_cost_usd"] == pytest.approx(1.1)
    assert succeeded.payload["cost_usd"] == pytest.approx(1.1)

    event_count = len(database.events())
    replayed = dispatcher.dispatch(**kwargs)

    assert replayed == result
    assert len(adapter.received) == 2
    assert len(database.events()) == event_count


def test_provider_fallback_cost_survives_restart_without_double_counting(
    tmp_path,
):
    class InterruptBeforeFallback(RoleDispatcher):
        def _record_provider_attempt(self, **kwargs):
            if kwargs["ordinal"] == 2:
                raise RuntimeError("simulated dispatcher restart")
            return super()._record_provider_attempt(**kwargs)

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-cost-resume",
        cost_budget_usd=2.0,
        step_id="step-cost-resume",
    )
    router = RoleRouter().clone_with({
        "builder": {
            "preferred_provider": "opencode_go",
            "fallbacks": ("vertex_ai",),
        },
    })
    adapter = CostedFallbackAdapter()
    candidates = (
        priced_candidate("opencode_go", 0.8),
        priced_candidate("vertex_ai", 0.3),
    )
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-cost-resume",
        "run_id": "run-cost-resume",
        "instructions": "resume fallback cost accounting exactly once",
        "budget_usd": 2.0,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-cost-resume:a1",
        "allowed_paths": package.allowed_paths,
    }

    with pytest.raises(RuntimeError, match="simulated dispatcher restart"):
        InterruptBeforeFallback(
            database,
            router,
            adapter,
            provider_candidates=candidates,
        ).dispatch(**kwargs)

    first_costs = [
        event.payload["cost_usd"]
        for event in database.events()
        if event.event_type == "provider.dispatch_cost_recorded"
    ]
    assert first_costs == [0.8]
    assert [item.provider for item in adapter.received] == ["opencode_go"]

    result = RoleDispatcher(
        database,
        router,
        adapter,
        provider_candidates=candidates,
    ).dispatch(**kwargs)

    assert result.status == COMPLETED
    assert result.cost_usd == pytest.approx(1.1)
    assert [item.provider for item in adapter.received] == [
        "opencode_go",
        "vertex_ai",
    ]
    final_costs = [
        event.payload["cost_usd"]
        for event in database.events()
        if event.event_type == "provider.dispatch_cost_recorded"
    ]
    assert final_costs == [0.8, 0.3]
    assert json.loads(
        run_row(database, "run-cost-resume")["result_json"]
    )["cost_usd"] == pytest.approx(1.1)


def test_package_budget_accumulates_fallback_spend_across_attempts_and_replays(
    tmp_path,
):
    class RestartedFallback:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            if manifest.attempt_id.endswith(":a1"):
                cost = 0.5 if manifest.provider == "opencode_go" else 0.3
                return RoleResult(
                    run_id=manifest.run_id,
                    role=manifest.role,
                    status=BLOCKED,
                    provider=manifest.provider,
                    model=manifest.model,
                    verdict="BLOCKED",
                    error_class="provider_degraded",
                    error_detail="attempt degraded after billable usage",
                    cost_usd=cost,
                )
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                cost_usd=0.3,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-restart-cost",
        cost_budget_usd=1.0,
        step_id="step-package-restart-cost",
    )
    router = RoleRouter().clone_with({
        "builder": {
            "preferred_provider": "opencode_go",
            "fallbacks": ("vertex_ai",),
        },
    })
    adapter = RestartedFallback()
    candidates = (
        priced_candidate("opencode_go", 0.1),
        priced_candidate("vertex_ai", 0.1),
    )
    dispatcher = RoleDispatcher(
        database,
        router,
        adapter,
        provider_candidates=candidates,
    )
    common = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-package-restart-cost",
        "instructions": "enforce one budget across package attempts",
        "budget_usd": 1.0,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "allowed_paths": package.allowed_paths,
    }

    first = dispatcher.dispatch(
        **common,
        run_id="workforce:package-restart-cost:a1",
        attempt_id="workforce:package-restart-cost:a1",
    )
    second = dispatcher.dispatch(
        **common,
        run_id="workforce:package-restart-cost:a2",
        attempt_id="workforce:package-restart-cost:a2",
    )

    assert first.status == BLOCKED
    assert first.cost_usd == pytest.approx(0.8)
    assert second.status == BLOCKED
    assert second.error_class == "workforce_cost_budget_exhausted"
    assert second.cost_usd == pytest.approx(1.1)
    assert [
        (manifest.attempt_id, manifest.provider)
        for manifest in adapter.received
    ] == [
        ("workforce:package-restart-cost:a1", "opencode_go"),
        ("workforce:package-restart-cost:a1", "vertex_ai"),
        ("workforce:package-restart-cost:a2", "opencode_go"),
    ]
    cost_events = [
        event
        for event in database.events()
        if event.event_type == "provider.dispatch_cost_recorded"
    ]
    assert [event.payload["cost_usd"] for event in cost_events] == [0.5, 0.3, 0.3]
    assert {
        event.payload["package_id"] for event in cost_events
    } == {"package-restart-cost"}
    assert {
        event.payload["package_hash"] for event in cost_events
    } == {package.content_hash()}
    second_failures = [
        event
        for event in database.events()
        if event.event_type == "provider.dispatch_failed"
        and event.payload.get("run_id") == "workforce:package-restart-cost:a2"
    ]
    assert len(second_failures) == 1
    assert second_failures[0].payload["failure_class"] == "COST_LIMIT"
    assert second_failures[0].payload["package_cost_usd"] == pytest.approx(1.1)
    assert run_row(
        database,
        "workforce:package-restart-cost:a2",
    )["failure_class"] == "workforce_cost_budget_exhausted"

    event_count = len(database.events())
    replayed = RoleDispatcher(
        FoundryDatabase(database.path),
        router,
        adapter,
        provider_candidates=candidates,
    ).dispatch(
        **common,
        run_id="workforce:package-restart-cost:a2",
        attempt_id="workforce:package-restart-cost:a2",
    )

    assert replayed == second
    assert len(adapter.received) == 3
    assert len(database.events()) == event_count


def test_verifier_attempt_id_cannot_reset_immutable_package_spend(tmp_path):
    class FirstVerifierConsumesBudget:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=BLOCKED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="BLOCKED",
                error_class="provider_degraded",
                error_detail="verification consumed package budget",
                cost_usd=0.3,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-verifier-attempt-reset",
        cost_budget_usd=0.5,
    )
    adapter = FirstVerifierConsumesBudget()
    candidate = priced_candidate("opencode_go", 0.3)
    common = {
        "role": "verifier",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "verification",
        "step_id": "step-1",
        "instructions": "do not reset immutable package spend",
        "budget_usd": 0.5,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "allowed_paths": package.allowed_paths,
    }

    first = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(
        **common,
        run_id="workforce:package-verifier-attempt-reset:a1-verify",
        attempt_id="workforce:package-verifier-attempt-reset:a1",
    )
    second = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(
        **common,
        run_id="workforce:package-verifier-attempt-reset:a2-verify",
        attempt_id="workforce:package-verifier-attempt-reset:a2",
    )

    assert first.status == BLOCKED
    assert first.cost_usd == pytest.approx(0.3)
    assert second.status == BLOCKED
    assert second.cost_usd == pytest.approx(0.3)
    assert len(adapter.received) == 1
    skipped = [
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_skipped"
        and event.payload.get("run_id") == second.run_id
    ]
    assert skipped
    assert any(
        payload.get("failure_class") == "COST_LIMIT"
        for payload in skipped
    )


def test_verifier_replacement_run_keeps_attempt_cumulative_cost(tmp_path):
    class ReplacedVerifier:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            if manifest.role == "builder":
                return RoleResult(
                    run_id=manifest.run_id,
                    role=manifest.role,
                    status=COMPLETED,
                    provider=manifest.provider,
                    model=manifest.model,
                    verdict="PASS",
                    cost_usd=0.6,
                )
            if manifest.run_id.endswith("-verify-r1"):
                return RoleResult(
                    run_id=manifest.run_id,
                    role=manifest.role,
                    status=BLOCKED,
                    provider=manifest.provider,
                    model=manifest.model,
                    verdict="BLOCKED",
                    error_class="provider_degraded",
                    error_detail="first verifier ended after billable usage",
                    cost_usd=0.2,
                )
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                cost_usd=0.3,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-verifier-replacement",
        cost_budget_usd=1.0,
        step_id="step-verifier-replacement-cost",
    )
    router = RoleRouter().clone_with({
        "verifier": {
            "preferred_provider": "opencode_go",
            "fallbacks": (),
        },
    })
    adapter = ReplacedVerifier()
    dispatcher = RoleDispatcher(
        database,
        router,
        adapter,
        provider_candidates=(priced_candidate("opencode_go", 0.1),),
    )
    lineage = {
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "step_id": "step-verifier-replacement-cost",
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-verifier-replacement:a1",
        "allowed_paths": package.allowed_paths,
    }

    builder = dispatcher.dispatch(
        **lineage,
        role="builder",
        capability="write",
        run_id="workforce:package-verifier-replacement:a1",
        instructions="produce the candidate",
        budget_usd=1.0,
    )
    first_verifier = dispatcher.dispatch(
        **lineage,
        role="verifier",
        capability="verification",
        run_id="workforce:package-verifier-replacement:a1-verify-r1",
        instructions="verify the candidate",
        budget_usd=0.4,
    )
    replacement = dispatcher.dispatch(
        **lineage,
        role="verifier",
        capability="verification",
        run_id="workforce:package-verifier-replacement:a1-verify-r2",
        instructions="verify the candidate",
        budget_usd=0.4,
    )

    assert builder.status == COMPLETED
    assert builder.cost_usd == pytest.approx(0.6)
    assert first_verifier.status == BLOCKED
    assert first_verifier.cost_usd == pytest.approx(0.2)
    assert replacement.status == BLOCKED
    assert replacement.error_class == "workforce_cost_budget_exhausted"
    assert replacement.cost_usd == pytest.approx(0.5)
    assert [event.payload["cost_usd"] for event in database.events()
            if event.event_type == "provider.dispatch_cost_recorded"] == [
        0.6,
        0.2,
        0.3,
    ]
    replacement_failure = next(
        event
        for event in database.events()
        if event.event_type == "provider.dispatch_failed"
        and event.payload.get("run_id")
        == "workforce:package-verifier-replacement:a1-verify-r2"
    )
    assert replacement_failure.payload["run_cost_usd"] == pytest.approx(0.3)
    assert replacement_failure.payload["attempt_role_cost_usd"] == pytest.approx(0.5)
    assert replacement_failure.payload["package_cost_usd"] == pytest.approx(1.1)


def test_verifier_exact_remaining_budget_is_accepted(tmp_path):
    class ExactBudgetVerifier:
        def dispatch(self, manifest, *, heartbeat):
            heartbeat()
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                cost_usd=0.4,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-verifier-exact-budget",
        cost_budget_usd=0.4,
        step_id="step-verifier-exact-budget",
    )
    result = RoleDispatcher(
        database,
        RoleRouter(),
        ExactBudgetVerifier(),
        provider_candidates=(priced_candidate("opencode_go", 0.4),),
    ).dispatch(
        role="verifier",
        mission_id="mission-1",
        resource_id="resource-1",
        capability="verification",
        step_id="step-verifier-exact-budget",
        run_id="workforce:package-verifier-exact-budget:a1-verify-r1",
        instructions="verify within the exact remaining package budget",
        budget_usd=0.4,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-verifier-exact-budget:a1",
        allowed_paths=package.allowed_paths,
    )

    assert result.status == COMPLETED
    assert result.verdict == "PASS"
    assert result.cost_usd == pytest.approx(0.4)
    assert not [
        event
        for event in database.events()
        if event.event_type == "provider.dispatch_failed"
        and event.payload.get("failure_class") == "COST_LIMIT"
    ]


def test_completed_verifier_fail_at_exact_budget_remains_a_verdict(tmp_path):
    class ExactBudgetFailVerifier:
        def dispatch(self, manifest, *, heartbeat):
            heartbeat()
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="FAIL",
                evidence=("candidate failed independent verification",),
                cost_usd=0.4,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-verifier-exact-fail",
        cost_budget_usd=0.4,
    )
    result = RoleDispatcher(
        database,
        RoleRouter(),
        ExactBudgetFailVerifier(),
        provider_candidates=(priced_candidate("opencode_go", 0.4),),
    ).dispatch(
        role="verifier",
        mission_id="mission-1",
        resource_id="resource-1",
        capability="verification",
        step_id="step-1",
        run_id="workforce:package-verifier-exact-fail:a1-verify",
        instructions="return the exact independent verdict",
        budget_usd=0.4,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-verifier-exact-fail:a1",
        allowed_paths=package.allowed_paths,
    )

    assert result.status == COMPLETED
    assert result.verdict == "FAIL"
    assert result.cost_usd == pytest.approx(0.4)
    settlement = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
    )
    assert settlement["outcome"] == "COMPLETED"
    assert settlement["result"]["verdict"] == "FAIL"


def test_failed_verifier_exact_budget_blocks_missing_estimate_fallback(tmp_path):
    class ExactBudgetFailure:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            if manifest.provider == "opencode_go":
                return RoleResult(
                    run_id=manifest.run_id,
                    role=manifest.role,
                    status=BLOCKED,
                    provider=manifest.provider,
                    model=manifest.model,
                    verdict="BLOCKED",
                    evidence=("provider receipt: exact-budget failure",),
                    error_class="provider_unavailable",
                    error_detail="verifier failed after consuming the budget",
                    cost_usd=0.4,
                    effect_action_key="effect:exact-budget-verifier",
                    started_at="2026-09-04T10:00:00Z",
                    finished_at="2026-09-04T10:00:01Z",
                )
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                cost_usd=0.0,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-verifier-failed-exact",
        cost_budget_usd=0.4,
        step_id="step-verifier-failed-exact-budget",
    )
    router = RoleRouter().clone_with({
        "verifier": {
            "preferred_provider": "opencode_go",
            "fallbacks": ("vertex_ai",),
        },
    })
    adapter = ExactBudgetFailure()
    candidates = (
        priced_candidate("opencode_go", 0.4),
        priced_candidate("vertex_ai", None),
    )
    kwargs = {
        "role": "verifier",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "verification",
        "step_id": "step-verifier-failed-exact-budget",
        "run_id": "workforce:package-verifier-failed-exact:a1-verify-r1",
        "instructions": "do not retry after failed exact-budget verification",
        "budget_usd": 0.4,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-verifier-failed-exact:a1",
        "allowed_paths": package.allowed_paths,
    }

    result = RoleDispatcher(
        database,
        router,
        adapter,
        provider_candidates=candidates,
    ).dispatch(**kwargs)

    assert result.status == BLOCKED
    assert result.error_class == "workforce_cost_budget_exhausted"
    assert result.cost_usd == pytest.approx(0.4)
    assert [manifest.provider for manifest in adapter.received] == ["opencode_go"]
    cost_events = [
        event
        for event in database.events()
        if event.event_type == "provider.dispatch_cost_recorded"
    ]
    assert [event.payload["cost_usd"] for event in cost_events] == [0.4]
    failures = [
        event
        for event in database.events()
        if event.event_type == "provider.dispatch_failed"
        and event.payload.get("run_id") == kwargs["run_id"]
    ]
    assert len(failures) == 1
    assert failures[0].payload["failure_class"] == "COST_LIMIT"
    assert failures[0].payload["attempt_role_cost_usd"] == pytest.approx(0.4)

    event_count = len(database.events())
    replayed = RoleDispatcher(
        FoundryDatabase(database.path),
        router,
        adapter,
        provider_candidates=candidates,
    ).dispatch(**kwargs)

    assert replayed == result
    assert [manifest.provider for manifest in adapter.received] == ["opencode_go"]
    assert len(database.events()) == event_count


def test_unknown_package_cost_blocks_a_fresh_attempt_before_provider_dispatch(
    tmp_path,
):
    class UnknownCost:
        def dispatch(self, manifest, *, heartbeat):
            heartbeat()
            raise ProviderUnavailable("transport ended without usage telemetry")

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-unknown-cost",
        cost_budget_usd=1.0,
        step_id="step-package-unknown-cost",
    )
    router = RoleRouter().clone_with({
        "builder": {
            "preferred_provider": "opencode_go",
            "fallbacks": (),
        },
    })
    common = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-package-unknown-cost",
        "instructions": "do not reset an indeterminate package budget",
        "budget_usd": 1.0,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "allowed_paths": package.allowed_paths,
    }
    candidates = (priced_candidate("opencode_go", 0.1),)

    first = RoleDispatcher(
        database,
        router,
        UnknownCost(),
        provider_candidates=candidates,
    ).dispatch(
        **common,
        run_id="workforce:package-unknown-cost:a1",
        attempt_id="workforce:package-unknown-cost:a1",
    )
    replacement = FakeHermesAdapter()
    second = RoleDispatcher(
        database,
        router,
        replacement,
        provider_candidates=candidates,
    ).dispatch(
        **common,
        run_id="workforce:package-unknown-cost:a2",
        attempt_id="workforce:package-unknown-cost:a2",
    )

    assert first.status == BLOCKED
    assert first.error_class == "workforce_cost_telemetry_unavailable"
    assert second.status == BLOCKED
    assert second.error_class == "workforce_cost_telemetry_unavailable"
    assert second.cost_usd == pytest.approx(0.0)
    assert replacement.received == []
    unavailable = [
        event
        for event in database.events()
        if event.event_type == "provider.dispatch_cost_unavailable"
    ]
    assert len(unavailable) == 1
    assert unavailable[0].payload["package_id"] == "package-unknown-cost"
    assert unavailable[0].payload["package_hash"] == package.content_hash()


def test_unbudgeted_package_attempts_keep_legacy_per_run_costs(tmp_path):
    class UnbudgetedRestart:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            if manifest.attempt_id.endswith(":a1"):
                return RoleResult(
                    run_id=manifest.run_id,
                    role=manifest.role,
                    status=BLOCKED,
                    provider=manifest.provider,
                    model=manifest.model,
                    verdict="BLOCKED",
                    error_class="provider_unavailable",
                    error_detail="legacy failed attempt",
                    cost_usd=0.8,
                )
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                cost_usd=0.3,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-unbudgeted",
        cost_budget_usd=None,
        step_id="step-unbudgeted-package",
    )
    adapter = UnbudgetedRestart()
    dispatcher = RoleDispatcher(database, RoleRouter(), adapter)
    common = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-unbudgeted-package",
        "instructions": "preserve unbudgeted package behavior",
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "allowed_paths": package.allowed_paths,
    }

    first = dispatcher.dispatch(
        **common,
        run_id="workforce:package-unbudgeted:a1",
        attempt_id="workforce:package-unbudgeted:a1",
    )
    second = dispatcher.dispatch(
        **common,
        run_id="workforce:package-unbudgeted:a2",
        attempt_id="workforce:package-unbudgeted:a2",
    )

    assert first.status == BLOCKED
    assert first.cost_usd == pytest.approx(0.8)
    assert second.status == COMPLETED
    assert second.cost_usd == pytest.approx(0.3)
    assert len(adapter.received) == 2


def test_budgeted_workforce_does_not_fallback_without_attempt_cost_telemetry(
    tmp_path,
):
    class UnknownCostFailure:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            raise ProviderUnavailable("transport failed without usage telemetry")

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-cost-unknown",
        cost_budget_usd=1.0,
        step_id="step-cost-unknown",
    )
    router = RoleRouter().clone_with({
        "builder": {
            "preferred_provider": "opencode_go",
            "fallbacks": ("vertex_ai",),
        },
    })
    adapter = UnknownCostFailure()

    result = RoleDispatcher(
        database,
        router,
        adapter,
        provider_candidates=(priced_candidate("opencode_go", 0.1),),
    ).dispatch(
        role="builder",
        mission_id="mission-1",
        resource_id="resource-1",
        capability="write",
        step_id="step-cost-unknown",
        run_id="run-cost-unknown",
        instructions="remain bounded when attempt spend is unknown",
        budget_usd=1.0,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id="workforce:package-cost-unknown:a1",
        allowed_paths=package.allowed_paths,
    )

    assert result.status == BLOCKED
    assert result.error_class == "workforce_cost_telemetry_unavailable"
    assert [item.provider for item in adapter.received] == ["opencode_go"]
    unavailable = [
        event for event in database.events()
        if event.event_type == "provider.dispatch_cost_unavailable"
    ]
    assert len(unavailable) == 1
    assert unavailable[0].payload["provider"] == "opencode_go"


def test_budgeted_attempt_crash_after_provider_return_replays_fail_closed(
    tmp_path,
):
    class PaidAttempt:
        def __init__(self):
            self.calls = 0

        def dispatch(self, manifest, *, heartbeat):
            self.calls += 1
            heartbeat()
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                evidence=("provider returned a billable result",),
                cost_usd=0.25,
                effect_action_key="effect:paid-attempt",
                started_at="2026-09-04T11:00:00Z",
                finished_at="2026-09-04T11:00:01Z",
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-paid-crash",
        cost_budget_usd=1.0,
        step_id="step-paid-crash",
    )
    adapter = PaidAttempt()
    candidate = priced_candidate("opencode_go", 0.25)
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-paid-crash",
        "run_id": "run-paid-crash",
        "instructions": "do not repeat a possibly paid provider call",
        "budget_usd": 1.0,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-paid-crash:a1",
        "allowed_paths": package.allowed_paths,
    }

    def crash_after_return(point):
        if point == "post provider dispatch":
            raise KeyboardInterrupt("simulated process death")

    with pytest.raises(KeyboardInterrupt, match="simulated process death"):
        RoleDispatcher(
            database,
            RoleRouter(),
            adapter,
            provider_candidates=(candidate,),
            fault_hook=crash_after_return,
        ).dispatch(**kwargs)

    assert adapter.calls == 1
    assert not [
        event
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
    ]

    recovered = RoleDispatcher(
        FoundryDatabase(database.path),
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(**kwargs)

    assert recovered.status == BLOCKED
    assert recovered.error_class == "workforce_cost_telemetry_unavailable"
    assert recovered.provider == "opencode_go"
    assert recovered.model == candidate.model
    assert adapter.calls == 1
    settlement = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
    )
    assert settlement["attempt_generation"] == 0
    assert settlement["attempt_ordinal"] == 1
    assert settlement["attempt_started"] is True
    assert settlement["cost_state"] == "UNAVAILABLE"
    assert settlement["result"] == recovered.to_payload()

    event_count = len(database.events())
    replayed = RoleDispatcher(
        FoundryDatabase(database.path),
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(**kwargs)

    assert replayed == recovered
    assert adapter.calls == 1
    assert len(database.events()) == event_count


def test_missing_provider_estimate_with_remaining_budget_is_terminal(
    tmp_path,
):
    adapter = FakeHermesAdapter()
    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-missing-estimate",
        cost_budget_usd=1.0,
        step_id="step-missing-estimate",
    )
    candidate = priced_candidate("opencode_go", None)
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-missing-estimate",
        "run_id": "run-missing-estimate",
        "instructions": "require a bounded provider estimate",
        "budget_usd": 1.0,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-missing-estimate:a1",
        "allowed_paths": package.allowed_paths,
    }

    result = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(**kwargs)

    assert result.status == BLOCKED
    assert result.error_class == "workforce_cost_estimate_unavailable"
    assert result.cost_usd == 0.0
    assert adapter.received == []
    settlement = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
    )
    assert settlement["attempt_generation"] == 0
    assert settlement["attempt_started"] is False
    assert settlement["estimate_state"] == "UNAVAILABLE"
    assert settlement["cost_state"] == "NOT_INCURRED"

    event_count = len(database.events())
    replayed = RoleDispatcher(
        FoundryDatabase(database.path),
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(**kwargs)

    assert replayed == result
    assert adapter.received == []
    assert len(database.events()) == event_count


def test_legacy_dangling_attempt_marker_is_not_dispatched_again(tmp_path):
    database = mission_database(tmp_path)
    effect_action_key = "effect:package-legacy-dangling"
    package = durable_package(
        database,
        tmp_path,
        package_id="package-legacy-dangling",
        cost_budget_usd=1.0,
        effect_action_key=effect_action_key,
    )
    candidate = priced_candidate("opencode_go", 0.1)
    run_id = "workforce:package-legacy-dangling:a1"
    database.append(Event.create(
        "provider.dispatch_attempted",
        {
            "run_id": run_id,
            "mission_id": "mission-1",
            "step_id": "step-1",
            "role": "builder",
            "provider": candidate.provider,
            "model": candidate.model,
            "attempt_ordinal": 1,
            "estimated_cost_usd": 0.1,
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": run_id,
            "effect_action_key": effect_action_key,
        },
        source_type="supervisor",
        source_id="role-dispatcher",
        idempotency_key="legacy-provider-attempt:package-legacy-dangling:a1",
    ))
    adapter = FakeHermesAdapter()
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-1",
        "run_id": run_id,
        "instructions": "never repeat an ambiguously billable legacy attempt",
        "budget_usd": 1.0,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": run_id,
        "allowed_paths": package.allowed_paths,
        "effect_action_key": effect_action_key,
    }

    result = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(**kwargs)

    assert result.status == BLOCKED
    assert result.error_class == "workforce_cost_telemetry_unavailable"
    assert result.effect_action_key == effect_action_key
    assert adapter.received == []
    settlement = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
    )
    assert settlement["outcome"] == "INTERRUPTED_UNKNOWN"
    assert settlement["cost_state"] == "UNAVAILABLE"
    assert settlement["result"]["effect_action_key"] == effect_action_key
    failure = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_failed"
    )
    assert failure["possible_effect"] is True
    assert failure["effect_action_key"] == effect_action_key


def test_unbudgeted_workforce_dangling_attempt_is_not_dispatched_again(
    tmp_path,
):
    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-unbudgeted-dangling",
        cost_budget_usd=None,
    )
    candidate = priced_candidate("opencode_go", 0.1)
    run_id = "workforce:package-unbudgeted-dangling:a1"
    database.append(Event.create(
        "provider.dispatch_attempted",
        {
            "run_id": run_id,
            "mission_id": "mission-1",
            "step_id": "step-1",
            "role": "builder",
            "provider": candidate.provider,
            "model": candidate.model,
            "attempt_ordinal": 1,
            "estimated_cost_usd": 0.1,
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": run_id,
        },
        source_type="supervisor",
        source_id="role-dispatcher",
        idempotency_key=(
            "legacy-provider-attempt:package-unbudgeted-dangling:a1"
        ),
    ))
    adapter = FakeHermesAdapter()
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-1",
        "run_id": run_id,
        "instructions": "never repeat an ambiguous workforce attempt",
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": run_id,
        "allowed_paths": package.allowed_paths,
    }

    result = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(**kwargs)

    assert result.status == BLOCKED
    assert result.error_class == "provider_attempt_reconciliation_required"
    assert adapter.received == []
    settlement = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
    )
    assert settlement["outcome"] == "INTERRUPTED_UNKNOWN"
    assert settlement["cost_state"] == "UNAVAILABLE"


def test_provider_wait_resume_uses_a_new_settlement_generation(tmp_path):
    class ResumedProvider:
        def __init__(self):
            self.calls = 0

        def dispatch(self, manifest, *, heartbeat):
            self.calls += 1
            heartbeat()
            if self.calls == 1:
                return RoleResult(
                    run_id=manifest.run_id,
                    role=manifest.role,
                    status=BLOCKED,
                    provider=manifest.provider,
                    model=manifest.model,
                    verdict="BLOCKED",
                    error_class="provider_unavailable",
                    error_detail="provider is temporarily unavailable",
                    cost_usd=0.1,
                )
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                effect_action_key="effect:forged-result-identity",
                cost_usd=0.1,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-provider-resume",
        cost_budget_usd=1.0,
    )
    adapter = ResumedProvider()
    candidate = priced_candidate("opencode_go", 0.1)
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-1",
        "run_id": "workforce:package-provider-resume:a1",
        "instructions": "resume only after durable provider recovery",
        "budget_usd": 1.0,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-provider-resume:a1",
        "allowed_paths": package.allowed_paths,
    }

    first = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(**kwargs)
    orient_mission_for_blocking(database)
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": "mission-1",
            "state": "BLOCKED",
            "next_action": None,
            "run_id": kwargs["run_id"],
            "reason": "provider is temporarily unavailable",
        },
        source_type="supervisor",
        source_id="mission-loop",
        correlation_id="mission-1",
        idempotency_key="mission.state_changed:mission-1:BLOCKED:provider-test",
    ))
    wait_payload = schedule_mission_provider_wait(
        database,
        mission_id="mission-1",
        step_id="step-1",
        run_id=kwargs["run_id"],
        provider_ids=(candidate.provider,),
        reason="provider is temporarily unavailable",
        error_class="provider_unavailable",
    )
    wait_id = str(wait_payload["wait_id"])
    record_provider_readiness(
        database,
        provider_id=candidate.provider,
        model=candidate.model,
        readiness=ProviderReadiness.READY,
        reason="provider readiness recovered",
        evidence_refs=("test:provider-readiness-recovered",),
    )
    assert [
        event.payload["wait_id"]
        for event in canonical_provider_resume_events(
            database,
            run_id=kwargs["run_id"],
        )
    ] == [wait_id]
    second = RoleDispatcher(
        FoundryDatabase(database.path),
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    ).dispatch(**kwargs)

    assert first.status == BLOCKED
    assert second.status == COMPLETED
    assert second.verdict == "PASS"
    assert second.cost_usd == pytest.approx(0.2)
    assert adapter.calls == 2
    generations = {
        event.payload["attempt_generation"]
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
    }
    assert generations == {0, 1}


def test_identical_provider_wait_opens_a_new_episode_after_resume(tmp_path):
    database = mission_database(tmp_path)
    run_id = "run-repeated-provider-wait"
    orient_mission_for_blocking(database)
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": "mission-1",
            "state": "BLOCKED",
            "next_action": None,
            "run_id": run_id,
            "reason": "provider unavailable",
        },
        source_type="supervisor",
        source_id="mission-loop",
        correlation_id="mission-1",
        idempotency_key="mission.state_changed:mission-1:BLOCKED:episode-1",
    ))

    first = schedule_mission_provider_wait(
        database,
        mission_id="mission-1",
        step_id="step-1",
        run_id=run_id,
        provider_ids=("opencode_go",),
        reason="same outage",
        error_class="provider_unavailable",
    )
    trigger_wake(
        database,
        str(first["wake_ids"][0]),
        reason="first recovery",
    )
    assert resume_mission_provider_waits(database) == [first["wait_id"]]
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": "mission-1",
            "state": "BLOCKED",
            "next_action": None,
            "run_id": run_id,
            "reason": "provider unavailable again",
        },
        source_type="supervisor",
        source_id="mission-loop",
        correlation_id="mission-1",
        idempotency_key="mission.state_changed:mission-1:BLOCKED:episode-2",
    ))

    second = schedule_mission_provider_wait(
        database,
        mission_id="mission-1",
        step_id="step-1",
        run_id=run_id,
        provider_ids=("opencode_go",),
        reason="same outage",
        error_class="provider_unavailable",
    )
    replay = schedule_mission_provider_wait(
        database,
        mission_id="mission-1",
        step_id="step-1",
        run_id=run_id,
        provider_ids=("opencode_go",),
        reason="same outage",
        error_class="provider_unavailable",
    )

    assert first["wait_generation"] == 1
    assert second["wait_generation"] == 2
    assert first["wait_id"] != second["wait_id"]
    assert replay == second


def test_provider_resume_requires_the_exact_wake_trigger(tmp_path):
    database = mission_database(tmp_path)
    run_id = "run-untriggered-provider-wait"
    orient_mission_for_blocking(database)
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": "mission-1",
            "state": "BLOCKED",
            "next_action": None,
            "run_id": run_id,
            "reason": "provider unavailable",
        },
        source_type="supervisor",
        source_id="mission-loop",
        correlation_id="mission-1",
        idempotency_key="mission.state_changed:mission-1:BLOCKED:untriggered",
    ))
    wait_payload = schedule_mission_provider_wait(
        database,
        mission_id="mission-1",
        step_id="step-1",
        run_id=run_id,
        provider_ids=("opencode_go",),
        reason="provider unavailable",
        error_class="provider_unavailable",
    )
    wait_id = str(wait_payload["wait_id"])
    database.append_events([
        Event.create(
            "mission.provider_wait_resumed",
            {
                **wait_payload,
                "resumed_at": "2026-09-04T12:00:00Z",
                "recovery_provider_id": "opencode_go",
            },
            source_type="supervisor",
            source_id="provider-resilience",
            correlation_id="mission-1",
            idempotency_key=f"mission.provider_wait_resumed:{wait_id}",
        ),
        Event.create(
            "mission.state_changed",
            {
                "mission_id": "mission-1",
                "state": "ACTION_SELECTION",
                "next_action": None,
                "run_id": run_id,
                "reason": "provider wait condition satisfied",
            },
            source_type="supervisor",
            source_id="provider-resilience",
            correlation_id="mission-1",
            idempotency_key=(
                "mission.state_changed:mission-1:ACTION_SELECTION:"
                f"provider-resume:{wait_id}"
            ),
        ),
    ])

    with pytest.raises(
        EventConflict,
        match="lacks exact wake trigger authority",
    ):
        canonical_provider_resume_events(database)


def test_forged_provider_wait_start_cannot_wake_a_mission(tmp_path):
    database = mission_database(tmp_path)
    orient_mission_for_blocking(database)
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": "mission-1",
            "state": "BLOCKED",
            "next_action": None,
            "run_id": "run-forged-provider-wait",
            "reason": "test blocked mission",
        },
        source_type="supervisor",
        source_id="mission-loop",
        correlation_id="mission-1",
        idempotency_key="mission.state_changed:mission-1:BLOCKED:test",
    ))
    material = {
        "mission_id": "mission-1",
        "step_id": "step-1",
        "run_id": "run-forged-provider-wait",
        "provider_ids": [],
        "effect_action_key": "effect:forged-provider-wait",
        "error_class": "provider_effect_reconciliation_required",
    }
    wait_id = "provider-wait-" + hashlib.sha256(
        canonical_json(material).encode("utf-8")
    ).hexdigest()[:24]
    database.append(Event.create(
        "mission.provider_wait_started",
        {
            **material,
            "wait_id": wait_id,
            "objective_id": "mission:mission-1",
            "wake_ids": [],
            "reason": "forged provider wait",
            "created_at": "2026-09-04T12:00:00Z",
        },
        source_type="supervisor",
        source_id="adversarial-writer",
        correlation_id="mission-1",
        idempotency_key=f"mission.provider_wait_started:{wait_id}",
    ))

    with pytest.raises(
        EventConflict,
        match="provider wait start authority is invalid",
    ):
        resume_mission_provider_waits(database)

    assert database.row("missions", "mission_id", "mission-1")["state"] == (
        "BLOCKED"
    )
    assert not [
        event
        for event in database.events()
        if event.event_type == "mission.provider_wait_resumed"
    ]


def test_forged_effect_reconciliation_cannot_resume_provider_wait(tmp_path):
    database = mission_database(tmp_path)
    run_id = "run-forged-effect-reconciliation"
    action_key = "effect:forged-reconciliation"
    orient_mission_for_blocking(database)
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": "mission-1",
            "state": "BLOCKED",
            "next_action": None,
            "run_id": run_id,
            "reason": "effect reconciliation required",
        },
        source_type="supervisor",
        source_id="mission-loop",
        correlation_id="mission-1",
        idempotency_key=(
            "mission.state_changed:mission-1:BLOCKED:"
            "forged-effect-reconciliation"
        ),
    ))
    wait_payload = schedule_mission_provider_wait(
        database,
        mission_id="mission-1",
        step_id="step-1",
        run_id=run_id,
        provider_ids=(),
        reason="effect reconciliation required",
        error_class="provider_effect_reconciliation_required",
        effect_action_key=action_key,
    )
    database.append(Event.create(
        "effect.reconciled",
        {
            "action_key": action_key,
            "provider_claim": {"status": "MATCH", "matches": True},
        },
        source_type="supervisor",
        source_id="adversarial-writer",
        idempotency_key=f"{action_key}.reconciled.forged",
    ))

    assert resume_mission_provider_waits(database) == []
    assert database.row("missions", "mission_id", "mission-1")["state"] == (
        "BLOCKED"
    )
    assert not [
        event
        for event in database.events()
        if event.event_type in {
            "provider.effect_reconciled",
            "mission.provider_wait_resumed",
        }
        and event.payload.get("wait_id") == wait_payload["wait_id"]
    ]


def test_forged_effect_reconciliation_cannot_release_dispatch_fallback(
    tmp_path,
):
    database = mission_database(tmp_path)
    run_id = "run-forged-dispatch-reconciliation"
    action_key = "effect:forged-dispatch-reconciliation"
    provider = "opencode_go"
    database.append_events([
        Event.create(
            "provider.effect_reconciliation_required",
            {
                "run_id": run_id,
                "provider": provider,
                "effect_action_key": action_key,
            },
            source_type="supervisor",
            source_id="role-dispatcher",
            idempotency_key=(
                "provider.effect_reconciliation_required:"
                f"{run_id}:{action_key}"
            ),
        ),
        Event.create(
            "effect.reconciled",
            {
                "action_key": action_key,
                "provider_claim": {"status": "MATCH", "matches": True},
            },
            source_type="supervisor",
            source_id="adversarial-writer",
            idempotency_key=f"{action_key}.reconciled.forged",
        ),
    ])
    dispatcher = RoleDispatcher(
        database,
        RoleRouter(),
        FakeHermesAdapter(),
    )

    assert dispatcher._pending_effect_reconciliation(run_id) == (
        action_key,
        provider,
    )
    assert not [
        event
        for event in database.events()
        if event.event_type == "provider.effect_reconciled"
    ]


def test_old_effect_reconciliation_cannot_release_new_attempt_generation(
    tmp_path,
):
    database = mission_database(tmp_path)
    dispatcher = RoleDispatcher(
        database,
        RoleRouter(),
        FakeHermesAdapter(),
    )
    run_id = "run-effect-generation-bound"
    action_key = "effect:generation-bound"
    provider = "opencode_go"
    first = dispatcher._effect_reconciliation_requirement(
        run_id=run_id,
        mission_id="mission-1",
        step_id="step-1",
        provider=provider,
        model="model-a",
        failure_class="OUTAGE",
        reason="first ambiguous attempt",
        effect_action_key=action_key,
        attempt_generation=0,
        attempt_ordinal=1,
        lineage={},
    )
    database.append(first)
    database.append(Event.create(
        "effect.reconciled",
        {
            "action_key": action_key,
            "provider_claim": {"status": "MATCH", "matches": True},
        },
        source_type="supervisor",
        source_id="v5-effect-driver",
        idempotency_key=f"{action_key}.reconciled.generation-0",
    ))
    assert dispatcher._pending_effect_reconciliation(run_id) is None

    second = dispatcher._effect_reconciliation_requirement(
        run_id=run_id,
        mission_id="mission-1",
        step_id="step-1",
        provider=provider,
        model="model-a",
        failure_class="OUTAGE",
        reason="second ambiguous attempt",
        effect_action_key=action_key,
        attempt_generation=1,
        attempt_ordinal=1,
        lineage={},
    )
    database.append(second)
    assert dispatcher._pending_effect_reconciliation(run_id) == (
        action_key,
        provider,
    )
    orient_mission_for_blocking(database)
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": "mission-1",
            "state": "BLOCKED",
            "next_action": None,
            "run_id": run_id,
            "reason": "second effect reconciliation required",
        },
        source_type="supervisor",
        source_id="mission-loop",
        correlation_id="mission-1",
        idempotency_key=(
            "mission.state_changed:mission-1:BLOCKED:"
            "effect-generation-bound"
        ),
    ))
    wait = schedule_mission_provider_wait(
        database,
        mission_id="mission-1",
        step_id="step-1",
        run_id=run_id,
        provider_ids=(),
        reason="second effect reconciliation required",
        error_class="provider_effect_reconciliation_required",
        effect_action_key=action_key,
    )

    assert wait["effect_requirement_id"] == (
        second.payload["effect_requirement_id"]
    )
    assert resume_mission_provider_waits(database) == []

    database.append(Event.create(
        "effect.reconciled",
        {
            "action_key": action_key,
            "provider_claim": {"status": "MATCH", "matches": True},
        },
        source_type="supervisor",
        source_id="v5-effect-driver",
        idempotency_key=f"{action_key}.reconciled.generation-1",
    ))

    assert resume_mission_provider_waits(database) == [wait["wait_id"]]
    assert canonical_provider_resume_events(
        database,
        run_id=run_id,
    )[0].payload["wait_id"] == wait["wait_id"]


def test_forged_provider_resume_cannot_advance_attempt_generation(tmp_path):
    class UnavailableProvider:
        def __init__(self):
            self.calls = 0

        def dispatch(self, manifest, *, heartbeat):
            self.calls += 1
            heartbeat()
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=BLOCKED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="BLOCKED",
                error_class="provider_unavailable",
                error_detail="provider is temporarily unavailable",
                cost_usd=0.1,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-forged-provider-resume",
        cost_budget_usd=1.0,
    )
    adapter = UnavailableProvider()
    candidate = priced_candidate("opencode_go", 0.1)
    run_id = "workforce:package-forged-provider-resume:a1"
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-1",
        "run_id": run_id,
        "instructions": "reject an unauthorized provider resume",
        "budget_usd": 1.0,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": run_id,
        "allowed_paths": package.allowed_paths,
    }
    dispatcher = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(candidate,),
    )

    first = dispatcher.dispatch(**kwargs)
    database.append(Event.create(
        "mission.provider_wait_resumed",
        {
            "wait_id": "forged-provider-resume",
            "mission_id": "mission-1",
            "step_id": "step-1",
            "run_id": run_id,
            "resumed_at": "2026-09-04T12:00:00Z",
            "recovery_provider_id": candidate.provider,
        },
        source_type="supervisor",
        source_id="adversarial-writer",
        correlation_id="mission-1",
        idempotency_key=(
            "mission.provider_wait_resumed:forged-provider-resume"
        ),
    ))

    assert first.status == BLOCKED
    with pytest.raises(
        EventConflict,
        match="provider wait resume authority is invalid",
    ):
        RoleDispatcher(
            FoundryDatabase(database.path),
            RoleRouter(),
            adapter,
            provider_candidates=(candidate,),
        ).dispatch(**kwargs)
    assert adapter.calls == 1


@pytest.mark.parametrize(
    ("payload_override", "error_match"),
    (
        ({"observed_cost_micros": True}, "lacks observed cost"),
        ({"observed_cost_micros": 1.5}, "lacks observed cost"),
        ({"observed_cost_micros": 100_000.0}, "lacks observed cost"),
        ({"observed_cost_micros": "100000"}, "lacks observed cost"),
        (
            {"attempt_generation": True},
            "generation or ordinal is invalid",
        ),
        (
            {"attempt_generation": 0.5},
            "generation or ordinal is invalid",
        ),
        (
            {"attempt_generation": "0"},
            "generation or ordinal is invalid",
        ),
        (
            {"attempt_ordinal": True},
            "generation or ordinal is invalid",
        ),
        (
            {"attempt_ordinal": 1.5},
            "generation or ordinal is invalid",
        ),
        (
            {"attempt_ordinal": "1"},
            "generation or ordinal is invalid",
        ),
        (
            {
                "_result": {
                    "identity_transition_authorized": "yes",
                },
            },
            "invalid identity transition authority",
        ),
        (
            {"_result": {"cost_usd": "NaN"}},
            "result has invalid cost",
        ),
        (
            {"_result": {"cost_usd": True}},
            "result has invalid cost",
        ),
    ),
)
def test_budget_ledger_rejects_malformed_settlement_scalars(
    tmp_path,
    payload_override,
    error_match,
):
    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-ledger-integrity",
        cost_budget_usd=1.0,
    )
    provider = "opencode_go"
    model = "muse-spark-1.2-contributor"
    forged_run_id = "forged-ledger-run"
    identity = f"{forged_run_id}:0:1:{provider}:{model}"
    forged_result = RoleResult(
        run_id=forged_run_id,
        role="builder",
        status=BLOCKED,
        provider=provider,
        model=model,
        verdict="BLOCKED",
        error_class="provider_degraded",
        error_detail="forged malformed settlement",
        cost_usd=0.1,
    )
    scalar_overrides = dict(payload_override)
    result_overrides = scalar_overrides.pop("_result", {})
    database.append(Event.create(
        "provider.dispatch_attempt_settled",
        {
            "schema": "provider.dispatch-attempt-settlement.v1",
            "settlement_id": identity,
            "run_id": forged_run_id,
            "mission_id": "mission-1",
            "step_id": "step-1",
            "role": "builder",
            "provider": provider,
            "model": model,
            "attempt_generation": 0,
            "attempt_ordinal": 1,
            "attempt_started": True,
            "context_manifest_hash": "b" * 64,
            "estimate_state": "AVAILABLE",
            "estimated_cost_micros": 100_000,
            "estimated_cost_usd": 0.1,
            "cost_state": "OBSERVED",
            "cost_reason": None,
            "observed_cost_micros": 100_000,
            "observed_cost_usd": 0.1,
            "outcome": "DEGRADED",
            "terminal_run": False,
            "result": {
                **forged_result.to_payload(),
                **dict(result_overrides),
            },
            "package_id": package.package_id,
            "package_hash": package.content_hash(),
            "attempt_id": "workforce:package-ledger-integrity:a1",
            **scalar_overrides,
        },
        source_type="supervisor",
        source_id="role-dispatcher",
        idempotency_key=f"provider.dispatch_attempt_settled:{identity}",
    ))
    adapter = FakeHermesAdapter()

    with pytest.raises(EventConflict, match=error_match):
        RoleDispatcher(
            database,
            RoleRouter(),
            adapter,
            provider_candidates=(priced_candidate(provider, 0.1),),
        ).dispatch(
            role="builder",
            mission_id="mission-1",
            resource_id="resource-1",
            capability="write",
            step_id="step-1",
            run_id="workforce:package-ledger-integrity:a2",
            instructions="reject malformed prior spend",
            budget_usd=1.0,
            package_id=package.package_id,
            package_hash=package.content_hash(),
            attempt_id="workforce:package-ledger-integrity:a2",
            allowed_paths=package.allowed_paths,
        )

    assert adapter.received == []


def test_budgeted_no_candidate_outcome_is_atomic_and_replayable(tmp_path):
    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-no-candidate",
        cost_budget_usd=1.0,
    )
    adapter = FakeHermesAdapter()
    request = RoutingRequest(
        task_type="builder",
        required_capabilities=("capability-that-does-not-exist",),
    )
    kwargs = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-1",
        "run_id": "workforce:package-no-candidate:a1",
        "instructions": "record a terminal no-candidate decision",
        "budget_usd": 1.0,
        "routing_request": request,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-no-candidate:a1",
        "allowed_paths": package.allowed_paths,
    }
    dispatcher = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(priced_candidate("opencode_go", 0.1),),
    )

    result = dispatcher.dispatch(**kwargs)

    assert result.status == BLOCKED
    assert adapter.received == []
    event_types = [
        event.event_type
        for event in database.events()
        if event.payload.get("run_id") == kwargs["run_id"]
    ]
    assert "provider.dispatch_attempt_settled" in event_types
    assert "provider.dispatch_skipped" in event_types
    assert "agent_run.failed" in event_types
    event_count = len(database.events())
    replayed = RoleDispatcher(
        FoundryDatabase(database.path),
        RoleRouter(),
        adapter,
        provider_candidates=(priced_candidate("opencode_go", 0.1),),
    ).dispatch(**kwargs)
    assert replayed == result
    assert len(database.events()) == event_count


def test_adapter_cannot_redirect_budgeted_run_or_role_identity(tmp_path):
    class RedirectedIdentity:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            return RoleResult(
                run_id="forged-run",
                role="executive",
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                effect_action_key="effect:forged-result-identity",
                cost_usd=0.1,
            )

    database = mission_database(tmp_path)
    adapter = RedirectedIdentity()
    package = durable_package(
        database,
        tmp_path,
        package_id="package-result-identity",
        cost_budget_usd=1.0,
    )
    run_id = "workforce:package-result-identity:a1"
    result = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(
            priced_candidate("opencode_go", 0.1),
            priced_candidate("vertex_ai", 0.1),
        ),
    ).dispatch(
        role="builder",
        mission_id="mission-1",
        resource_id="resource-1",
        capability="write",
        step_id="step-1",
        run_id=run_id,
        instructions="preserve the requested result identity",
        budget_usd=1.0,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=run_id,
        allowed_paths=package.allowed_paths,
    )

    assert result.status == BLOCKED
    assert result.run_id == run_id
    assert result.role == "builder"
    assert result.error_class == "provider_effect_reconciliation_required"
    assert len(adapter.received) == 1
    assert database.row("agent_runs", "run_id", "forged-run") is None
    mismatch = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.result_identity_mismatch"
    )
    assert mismatch["observed_run_id"] == "forged-run"
    assert mismatch["observed_role"] == "executive"
    settlements = [
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
    ]
    assert settlements
    assert all(
        payload["result"]["run_id"] == run_id
        and payload["result"]["role"] == "builder"
        for payload in settlements
    )
    requirement = next(
        event
        for event in database.events()
        if event.event_type == "provider.effect_reconciliation_required"
    )
    assert requirement.payload["attempt_generation"] == 0
    assert requirement.payload["attempt_ordinal"] == 1
    assert requirement.payload["effect_action_key"] == (
        f"provider-effect:mission-1:step-1:{run_id}"
    )
    assert requirement.payload["observed_effect_action_key"] == (
        "effect:forged-result-identity"
    )
    assert result.effect_action_key == requirement.payload["effect_action_key"]
    assert requirement.payload["effect_requirement_id"] == (
        f"{run_id}:0:1:{result.effect_action_key}"
    )
    assert requirement.idempotency_key == (
        "provider.effect_reconciliation_required:"
        f"{requirement.payload['effect_requirement_id']}"
    )


def test_composite_identity_mismatch_settles_with_one_requirement(tmp_path):
    class RedirectedCompositeIdentity:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            return RoleResult(
                run_id="forged-run",
                role="executive",
                status=COMPLETED,
                provider="forged-provider",
                model="forged-model",
                verdict="PASS",
                effect_action_key="effect:forged-composite-identity",
                cost_usd=0.1,
            )

    database = mission_database(tmp_path)
    adapter = RedirectedCompositeIdentity()
    package = durable_package(
        database,
        tmp_path,
        package_id="package-composite-identity",
        cost_budget_usd=1.0,
    )
    run_id = "workforce:package-composite-identity:a1"

    result = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(
            priced_candidate("opencode_go", 0.1),
            priced_candidate("vertex_ai", 0.1),
        ),
    ).dispatch(
        role="builder",
        mission_id="mission-1",
        resource_id="resource-1",
        capability="write",
        step_id="step-1",
        run_id=run_id,
        instructions="preserve every dispatched identity",
        budget_usd=1.0,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=run_id,
        allowed_paths=package.allowed_paths,
    )

    assert result.status == BLOCKED
    assert result.run_id == run_id
    assert result.role == "builder"
    assert result.provider == "opencode_go"
    assert result.model == "muse-spark-1.2-contributor"
    assert result.error_class == "provider_effect_reconciliation_required"
    assert "observed=forged-run/executive" in str(result.error_detail)
    assert "observed=forged-provider/forged-model" in str(result.error_detail)
    assert len(adapter.received) == 1
    events = database.events()
    assert sum(
        event.event_type == "provider.result_identity_mismatch"
        for event in events
    ) == 1
    assert sum(
        event.event_type == "provider.identity_mismatch"
        for event in events
    ) == 1
    requirements = [
        event
        for event in events
        if event.event_type == "provider.effect_reconciliation_required"
    ]
    assert len(requirements) == 1
    settlements = [
        event
        for event in events
        if event.event_type == "provider.dispatch_attempt_settled"
    ]
    assert len(settlements) == 1
    assert settlements[0].payload["outcome"] == "PROVIDER_IDENTITY_MISMATCH"


def test_effect_identity_mismatch_keeps_immutable_expected_action_key(
    tmp_path,
):
    expected_key = "effect:expected-work-package-key"
    observed_key = "effect:provider-reported-key"

    class ChangedEffectIdentity:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                effect_action_key=observed_key,
                cost_usd=0.1,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-effect-identity-authority",
        cost_budget_usd=1.0,
        effect_action_key=expected_key,
    )
    run_id = "workforce:package-effect-identity-authority:a1"
    adapter = ChangedEffectIdentity()
    result = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(
            priced_candidate("opencode_go", 0.1),
            priced_candidate("vertex_ai", 0.1),
        ),
    ).dispatch(
        role="builder",
        mission_id="mission-1",
        resource_id="resource-1",
        capability="write",
        step_id="step-1",
        run_id=run_id,
        instructions="retain the immutable effect identity",
        budget_usd=1.0,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=run_id,
        allowed_paths=package.allowed_paths,
        effect_action_key=expected_key,
    )

    assert result.status == BLOCKED
    assert result.error_class == "provider_effect_identity_mismatch"
    assert result.effect_action_key == expected_key
    assert len(adapter.received) == 1
    requirement = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.effect_reconciliation_required"
    )
    assert requirement["effect_action_key"] == expected_key
    assert requirement["observed_effect_action_key"] == observed_key
    assert requirement["effect_requirement_id"] == (
        f"{run_id}:0:1:{expected_key}"
    )


def test_blocked_adapter_cannot_redirect_budgeted_provider_identity(tmp_path):
    class RedirectedProviderIdentity:
        def dispatch(self, manifest, *, heartbeat):
            heartbeat()
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=BLOCKED,
                provider="forged-provider",
                model="forged-model",
                verdict="BLOCKED",
                error_class="provider_policy_blocked",
                error_detail="forged provider identity",
                effect_action_key="effect:forged-provider-identity",
                cost_usd=0.1,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-blocked-provider-identity",
        cost_budget_usd=1.0,
    )
    candidate = priced_candidate("opencode_go", 0.1)
    run_id = "workforce:package-blocked-provider-identity:a1"
    result = RoleDispatcher(
        database,
        RoleRouter(),
        RedirectedProviderIdentity(),
        provider_candidates=(candidate,),
    ).dispatch(
        role="builder",
        mission_id="mission-1",
        resource_id="resource-1",
        capability="write",
        step_id="step-1",
        run_id=run_id,
        instructions="preserve the dispatched provider identity",
        budget_usd=1.0,
        package_id=package.package_id,
        package_hash=package.content_hash(),
        attempt_id=run_id,
        allowed_paths=package.allowed_paths,
    )

    assert result.status == BLOCKED
    assert result.provider == candidate.provider
    assert result.model == candidate.model
    assert result.error_class == "provider_effect_reconciliation_required"
    mismatch = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.identity_mismatch"
    )
    assert mismatch["observed_provider"] == "forged-provider"
    assert mismatch["observed_model"] == "forged-model"
    settlement = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
    )
    assert settlement["provider"] == candidate.provider
    assert settlement["model"] == candidate.model
    assert settlement["result"]["provider"] == candidate.provider
    assert settlement["result"]["model"] == candidate.model
    assert settlement["outcome"] == "PROVIDER_IDENTITY_MISMATCH"
    requirement = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.effect_reconciliation_required"
    )
    assert requirement["effect_action_key"] == (
        f"provider-effect:mission-1:step-1:{run_id}"
    )
    assert requirement["observed_effect_action_key"] == (
        "effect:forged-provider-identity"
    )
    assert result.effect_action_key == requirement["effect_action_key"]


def test_durable_package_rejects_forged_hash_and_inflated_budget(tmp_path):
    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-durable-authority",
        cost_budget_usd=0.5,
    )
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(priced_candidate("opencode_go", 0.1),),
    )
    common = {
        "role": "builder",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "write",
        "step_id": "step-1",
        "run_id": "run-durable-authority",
        "instructions": "respect the immutable package",
        "package_id": package.package_id,
        "attempt_id": "workforce:package-durable-authority:a1",
        "allowed_paths": package.allowed_paths,
    }

    with pytest.raises(ValueError, match="immutable durable WorkPackage"):
        dispatcher.dispatch(
            **{
                **common,
                "package_id": "package-missing-authority",
                "attempt_id": "workforce:package-missing-authority:a1",
            },
            package_hash="e" * 64,
            budget_usd=0.5,
        )
    with pytest.raises(ValueError, match="hash does not match"):
        dispatcher.dispatch(
            **common,
            package_hash="d" * 64,
            budget_usd=0.5,
        )
    with pytest.raises(ValueError, match="exceeds the immutable"):
        dispatcher.dispatch(
            **common,
            package_hash=package.content_hash(),
            budget_usd=0.6,
        )
    with pytest.raises(ValueError, match="allowed paths exceed"):
        dispatcher.dispatch(
            **{**common, "allowed_paths": ("outside.txt",)},
            package_hash=package.content_hash(),
            budget_usd=0.5,
        )
    with pytest.raises(ValueError, match="attempt_id is not bound"):
        dispatcher.dispatch(
            **{**common, "attempt_id": "workforce:another-package:a1"},
            package_hash=package.content_hash(),
            budget_usd=0.5,
        )

    assert adapter.received == []
    result = dispatcher.dispatch(
        **common,
        package_hash=package.content_hash(),
        budget_usd=0.4,
    )
    assert result.status == COMPLETED
    assert len(adapter.received) == 1
    assert adapter.received[0].budget_usd == 0.4


def test_fragmented_verifier_charges_can_equal_budget_in_micro_units(tmp_path):
    class FragmentedVerifier:
        def __init__(self):
            self.received = []

        def dispatch(self, manifest, *, heartbeat):
            self.received.append(manifest)
            heartbeat()
            if manifest.run_id.endswith("-r1"):
                return RoleResult(
                    run_id=manifest.run_id,
                    role=manifest.role,
                    status=BLOCKED,
                    provider=manifest.provider,
                    model=manifest.model,
                    verdict="BLOCKED",
                    error_class="provider_degraded",
                    error_detail="first verifier consumed a bounded fragment",
                    cost_usd=0.1,
                )
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict="PASS",
                evidence=("exact fragmented budget verified",),
                cost_usd=0.2,
            )

    database = mission_database(tmp_path)
    package = durable_package(
        database,
        tmp_path,
        package_id="package-fragmented-verifier",
        cost_budget_usd=0.3,
    )
    adapter = FragmentedVerifier()
    common = {
        "role": "verifier",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "capability": "verification",
        "step_id": "step-1",
        "instructions": "verify using fragmented exact-budget charges",
        "budget_usd": 0.3,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": "workforce:package-fragmented-verifier:a1",
        "allowed_paths": package.allowed_paths,
    }

    first = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(priced_candidate("opencode_go", 0.1),),
    ).dispatch(**common, run_id="fragmented-verifier-r1")
    second = RoleDispatcher(
        database,
        RoleRouter(),
        adapter,
        provider_candidates=(priced_candidate("opencode_go", 0.2),),
    ).dispatch(**common, run_id="fragmented-verifier-r2")

    assert first.status == BLOCKED
    assert first.cost_usd == 0.1
    assert second.status == COMPLETED
    assert second.verdict == "PASS"
    assert second.cost_usd == 0.3
    cost_events = [
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_cost_recorded"
        and event.payload.get("package_id") == package.package_id
    ]
    assert [payload["cost_micros"] for payload in cost_events] == [
        100_000,
        200_000,
    ]
    final_settlement = next(
        event.payload
        for event in database.events()
        if event.event_type == "provider.dispatch_attempt_settled"
        and event.payload.get("run_id") == second.run_id
    )
    assert final_settlement["attempt_role_cost_micros"] == 300_000
    assert final_settlement["result"] == second.to_payload()

    event_count = len(database.events())
    replayed = RoleDispatcher(
        FoundryDatabase(database.path),
        RoleRouter(),
        adapter,
        provider_candidates=(priced_candidate("opencode_go", 0.2),),
    ).dispatch(**common, run_id="fragmented-verifier-r2")
    assert replayed == second
    assert len(adapter.received) == 2
    assert len(database.events()) == event_count


def test_all_providers_exhausted_blocks(tmp_path):
    class AllDown(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            raise ProviderUnavailable(f"down: {manifest.provider}")

    db2 = mission_database(tmp_path)
    exhausted_router = RoleRouter().clone_with({
        "verifier": {"preferred_provider": "opencode_go", "fallbacks": ()},
    })
    d2 = RoleDispatcher(db2, exhausted_router, AllDown())
    blocked = d2.dispatch(role="verifier", mission_id="mission-1",
                          resource_id="resource-1", capability="verify",
                          run_id="run-x2")
    assert blocked.status == BLOCKED
    assert blocked.error_class == "provider_unavailable"
    assert "all providers failed" in blocked.error_detail
    row = run_row(db2, "run-x2")
    assert row["status"] == "FAILED"
    assert row["failure_class"] == "provider_unavailable"


def test_adapter_crash_marks_run_failed(tmp_path):
    database = mission_database(tmp_path)

    class Crash(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            raise RuntimeError("boom")

    dispatcher = RoleDispatcher(database, RoleRouter(), Crash())
    result = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 run_id="run-crash")
    assert result.status == FAILED
    assert result.error_class == "adapter_crash"
    row = run_row(database, "run-crash")
    assert row["status"] == "FAILED"
    assert row["failure_class"] == "adapter_crash"


# ---------------------------------------------------------------------------
# Structured results
# ---------------------------------------------------------------------------

def test_verifier_fail_verdict_structured(tmp_path):
    database = mission_database(tmp_path)
    adapter = FakeHermesAdapter(outcome=COMPLETED, verdict="FAIL", evidence=("found a bug",))
    dispatcher = RoleDispatcher(database, RoleRouter(), adapter)
    result = dispatcher.dispatch(role="verifier", mission_id="mission-1",
                                 resource_id="resource-1", capability="verify",
                                 run_id="run-vfail")
    assert result.status == COMPLETED
    assert result.verdict == "FAIL"
    assert result.evidence == ("found a bug",)
    payload = json.loads(run_row(database, "run-vfail")["result_json"])
    assert payload["verdict"] == "FAIL"
    assert payload["evidence"] == ["found a bug"]


# ---------------------------------------------------------------------------
# Real Hermes adapter safety boundary
# ---------------------------------------------------------------------------

def test_hermes_oneshot_adapter_disabled_by_default(tmp_path):
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    adapter = HermesOneshotAdapter(
        hermes_root=hermes_root, hermes_home=tmp_path / "isolated-home",
        enabled=False,
    )
    manifest = ContextManifest(
        run_id="run-1", role="builder", mission_id="mission-1",
        step_id="step-1", resource_id="resource-1", capability="write",
        provider="opencode_go", model="kimi-k2.7-code",
        isolation_group="builder",
    )
    with pytest.raises(ProviderUnavailable):
        adapter.dispatch(manifest, heartbeat=lambda: None)


def test_hermes_oneshot_adapter_rejects_live_hermes_root(tmp_path):
    with pytest.raises(AdapterConfigurationError):
        HermesOneshotAdapter(
            hermes_root=HermesOneshotAdapter.LIVE_HERMES,
            hermes_home=tmp_path / "home",
            enabled=True,
        )
    # Verifier finding (deleg_221bd327): any path INSIDE the live install is
    # equally invalid (parent-containment, mirroring the home check).
    with pytest.raises(AdapterConfigurationError):
        HermesOneshotAdapter(
            hermes_root=HermesOneshotAdapter.LIVE_HERMES / "venv",
            hermes_home=tmp_path / "home",
            enabled=False,
        )


def test_hermes_oneshot_adapter_requires_existing_isolated_home_when_enabled(tmp_path):
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    with pytest.raises(AdapterConfigurationError):
        HermesOneshotAdapter(
            hermes_root=hermes_root,
            hermes_home=tmp_path / "missing-home",
            enabled=True,
        )


def test_hermes_oneshot_adapter_rejects_live_hermes_home(tmp_path):
    # Verifier finding (deleg_f2954000): hermes_home was only checked for
    # existence, never for isolation. The live profile (or anything inside it)
    # must be rejected even when the adapter stays disabled.
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    live_home = HermesOneshotAdapter.LIVE_HERMES_HOME
    for home in (
        live_home,
        live_home / "profiles" / "default",   # child of the live profile
        live_home / "gateway",                # sibling service dir inside live root
    ):
        with pytest.raises(AdapterConfigurationError):
            HermesOneshotAdapter(
                hermes_root=hermes_root, hermes_home=home, enabled=False,
            )


def test_same_run_id_redispatch_replays_identically_and_conflicts_on_change(tmp_path):
    # A crashed caller re-dispatching the SAME run_id with the IDENTICAL
    # context replays from the ledger (no adapter re-run, no new events, one
    # run row); a changed payload under the same run_id conflicts loudly
    # (EventConflict) — first-write integrity (WP3 B6), fail-loud, no
    # corruption.
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go", "fallbacks": ()},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter)

    first = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                resource_id="resource-1", capability="write",
                                step_id="step-1", run_id="run-replay",
                                instructions="do the thing")
    assert first.status == COMPLETED
    events_after_first = len(database.events())

    replay = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 step_id="step-1", run_id="run-replay",
                                 instructions="do the thing")
    assert replay.status == COMPLETED
    assert replay.run_id == "run-replay"
    assert len(database.events()) == events_after_first  # pure ledger replay
    assert len(adapter.received) == 1                     # adapter NOT re-run

    from jarvis_foundry.events import EventConflict
    with pytest.raises(EventConflict):
        dispatcher.dispatch(role="builder", mission_id="mission-1",
                            resource_id="resource-1", capability="write",
                            step_id="step-1", run_id="run-replay",
                            instructions="DO A DIFFERENT THING")


def test_provider_diversity_preferred_and_recorded(tmp_path):
    # Provider diversity (builder != verifier) is the standing preference: the
    # router resolves a verifier provider distinct from the builder's, and the
    # durable ledger records both exact provider identities for comparison.
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go",
                    "preferred_model": "kimi-k2.7-code", "fallbacks": ()},
        "verifier": {"preferred_provider": "opencode_go",
                     "preferred_model": "deepseek-v4-flash",
                     "fallbacks": ("vertex_ai",)},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter)

    builder = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                  resource_id="resource-1", capability="write",
                                  step_id="step-1", run_id="run-dv-b")
    verifier_provider = router.diversity_resolution(builder.provider)
    verifier = dispatcher.dispatch(role="verifier", mission_id="mission-1",
                                   resource_id="resource-1", capability="verify",
                                   step_id="step-1", run_id="run-dv-v",
                                   provider_override=verifier_provider)

    assert router.prefer_diversity() is True
    assert builder.provider == "opencode_go"
    assert verifier_provider == "vertex_ai"
    assert verifier.provider == "vertex_ai"
    # Recorded durably with exact provider identity, distinct per role.
    builder_row = run_row(database, "run-dv-b")
    verifier_row = run_row(database, "run-dv-v")
    assert builder_row["provider"] == "opencode_go"
    assert verifier_row["provider"] == "vertex_ai"
    assert builder_row["provider"] != verifier_row["provider"]


def test_hermes_oneshot_command_surface_matches_real_cli(tmp_path, monkeypatch):
    # The real CLI (.h hermes_cli/_parser.py) accepts the prompt as the -z
    # VALUE, pairs --provider with --model, and has NO --role flag. The
    # adapter must emit exactly that surface; role identity rides in the
    # manifest/instructions. Regression: a --role flag or positional prompt
    # would make argparse exit 2 on every real dispatch.
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    adapter = HermesOneshotAdapter(
        hermes_root=hermes_root, hermes_home=isolated_home,
        enabled=True, timeout_seconds=60, python_exe="py",
    )
    manifest = ContextManifest(
        run_id="run-1", role="builder", mission_id="mission-1",
        step_id="step-1", resource_id="resource-1", capability="write",
        provider="opencode_go", model="kimi-k2.7-code",
        isolation_group="builder",
        instructions="build the smallest correct change",
    )

    captured = {}

    def fake_run(command, *, cwd, env, capture_output, text, timeout, **_kwargs):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env_home"] = env.get("HERMES_HOME")
        captured["timeout"] = timeout
        # Simulate the usage report the real CLI writes.
        import json
        usage_arg = command[command.index("--usage-file") + 1]
        Path(usage_arg).write_text(
            json.dumps({"estimated_cost_usd": 0.0123, "model": "kimi-k2.7-code"}),
            encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    monkeypatch.setattr("jarvis_foundry.roles.adapter.subprocess.run", fake_run)
    result = adapter.dispatch(manifest, heartbeat=lambda: None)

    command = captured["command"]
    assert command[0] == "py" and command[1:3] == ["-m", "hermes_cli.main"]
    assert command[3] == "-z"
    assert command[4] == "build the smallest correct change"  # prompt = -z VALUE
    assert "--role" not in command
    # The router's provider name (opencode_go) is translated to the CLI's
    # canonical id (opencode-go) at the adapter boundary — the raw name is
    # NOT a CLI alias (verified against .h normalize_provider, 2026-08-12).
    assert "--provider" in command and "opencode-go" in command
    assert "opencode_go" not in command
    assert "--model" in command and "kimi-k2.7-code" in command
    assert "--usage-file" in command
    assert captured["env_home"] == str(isolated_home)  # isolated home, never live
    assert captured["cwd"] == str(hermes_root)         # .h checkout, read-only
    assert captured["timeout"] == 60                   # bounded
    assert result.status == COMPLETED
    assert result.cost_usd == 0.0123                   # usage report parsed


def test_hermes_oneshot_translates_router_providers_to_cli_ids(tmp_path, monkeypatch):
    """Regression: every router provider name used by BUILTIN_ROLES must map
    to a resolvable CLI provider id at the adapter boundary. Unknown names
    pass through unchanged (fail-loud at the CLI rather than silent remap)."""
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    adapter = HermesOneshotAdapter(
        hermes_root=hermes_root, hermes_home=isolated_home,
        enabled=True, timeout_seconds=60,
    )

    captured = []

    def fake_run(command, *, cwd, env, capture_output, text, timeout, **_kwargs):
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("jarvis_foundry.roles.adapter.subprocess.run", fake_run)

    for router_name, cli_id in adapter.CLI_PROVIDER_IDS.items():
        manifest = ContextManifest(
            run_id=f"run-{router_name}", role="verifier", mission_id="mission-1",
            step_id=None, resource_id="resource-1", capability="verify",
            provider=router_name, model="deepseek-v4-flash",
            isolation_group="verifier",
        )
        adapter.dispatch(manifest, heartbeat=lambda: None)
        provider_index = captured[-1].index("--provider") + 1
        assert captured[-1][provider_index] == cli_id

    # Unknown router names are NOT silently remapped — the CLI resolves them
    # (or fails loudly) with the name as given.
    manifest = ContextManifest(
        run_id="run-unknown", role="verifier", mission_id="mission-1",
        step_id=None, resource_id="resource-1", capability="verify",
        provider="mystery_provider", model="m", isolation_group="verifier",
    )
    adapter.dispatch(manifest, heartbeat=lambda: None)
    provider_index = captured[-1].index("--provider") + 1
    assert captured[-1][provider_index] == "mystery_provider"


def test_hermes_oneshot_usage_cost_absent_is_zero(tmp_path, monkeypatch):
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    adapter = HermesOneshotAdapter(
        hermes_root=hermes_root, hermes_home=isolated_home,
        enabled=True, timeout_seconds=60,
    )
    manifest = ContextManifest(
        run_id="run-2", role="executive", mission_id="mission-1",
        step_id=None, resource_id="resource-1", capability="plan",
        provider="opencode_go", model="deepseek-v4-flash",
        isolation_group="executive",
    )

    def fake_run(command, *, cwd, env, capture_output, text, timeout, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("jarvis_foundry.roles.adapter.subprocess.run", fake_run)
    result = adapter.dispatch(manifest, heartbeat=lambda: None)
    assert result.status == COMPLETED
    assert result.cost_usd == 0.0


def test_provider_override_for_staging(tmp_path):
    database = mission_database(tmp_path)
    dispatcher = RoleDispatcher(database, RoleRouter(), FakeHermesAdapter())
    result = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 provider_override="anthropic", run_id="run-p1")
    assert result.provider == "anthropic"
    assert run_row(database, "run-p1")["provider"] == "anthropic"
