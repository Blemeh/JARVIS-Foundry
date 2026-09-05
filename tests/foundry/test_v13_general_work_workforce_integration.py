from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import jarvis_foundry.fabric.general_work as general_work_module
from jarvis_foundry.connectors.general_work_files import (
    GeneralWorkFileConnector,
    InjectedGeneralWorkFileFailure,
)
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import (
    inspect_git_resource,
    registration_event,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import build_plan, create_objective
from jarvis_foundry.executive.campaigns import create_campaign, startup_resume_pass
from jarvis_foundry.fabric.adapter import (
    FabricRoleAdapter,
    resolve_general_work_dispatch,
)
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.general_work import (
    ArtifactAssertion,
    DeliverableSpec,
    GeneralWorkPlan,
    GeneralWorkWorkforceError,
    SourceIdentity,
    WorkStep,
    create_general_work_work_package,
    general_work_campaign_contract,
    general_work_work_package_id,
    materialize_general_work_plan,
)
from jarvis_foundry.general_work.materialization import GeneralWorkMaterialization
from jarvis_foundry.mission.contracts import MissionStepContract, latest_contract
from jarvis_foundry.mission.loop import MissionLoop, PASSED
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.dispatcher import RoleDispatcher
from jarvis_foundry.roles.manifest import ContextManifest
from jarvis_foundry.roles.results import COMPLETED
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.workforce.gateway import GatewayBinding, WorkforceGateway
from jarvis_foundry.workforce.worktrees import WorktreeManager


PLAN_ID = "general-work-workforce-integration"
SOURCE_STEP_ID = "copy-owner-source"
EXECUTIVE_PLAN_ID = "executive-general-work-workforce"
EXECUTIVE_STEP_ID = "executive-general-work-workforce-step"


@dataclass(frozen=True)
class Lane:
    database: FoundryDatabase
    source_repository: Path | None
    root: Path
    source_sha: str | None
    branch_name: str | None
    plan: GeneralWorkPlan
    materialized: GeneralWorkMaterialization
    contract: MissionStepContract
    package_binding: object | None = None
    prepared: object | None = None


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan(root: Path, resource_id: str) -> GeneralWorkPlan:
    source = root / "inputs" / "owner.txt"
    return GeneralWorkPlan(
        plan_id=PLAN_ID,
        objective_id="objective-general-work-workforce",
        campaign_id="campaign-general-work-workforce",
        resource_id=resource_id,
        desired_outcome="Copy one immutable owner source into delivery",
        delivery_root="delivery",
        sources=(
            SourceIdentity(
                "owner-source",
                "inputs/owner.txt",
                _sha256(source),
                "txt",
                source.stat().st_size,
                "2026-09-05T10:00:00Z",
            ),
        ),
        deliverables=(
            DeliverableSpec(
                "owner-copy",
                "delivery/owner.txt",
                "txt",
                ("owner-source",),
                SOURCE_STEP_ID,
                ("owner-copy-content",),
            ),
        ),
        steps=(
            WorkStep(
                SOURCE_STEP_ID,
                "Copy the immutable owner source",
                "file.copy",
                {
                    "source": "inputs/owner.txt",
                    "target": "delivery/owner.txt",
                },
                (),
                ("owner-source",),
                ("owner-copy",),
                ("owner-copy-content",),
                max_retries=1,
            ),
        ),
        artifact_assertions=(
            ArtifactAssertion(
                "owner-copy-content",
                "owner-copy",
                "text_contains",
                "owner metric: 42",
                "",
            ),
        ),
        consistency_assertions=(),
        created_at="2026-09-05T10:01:00Z",
    )


def _register_general_work_resource(
    database: FoundryDatabase,
    root: Path,
    resource_id: str,
    *,
    git_resource: bool,
) -> None:
    if git_resource:
        database.append(
            registration_event(
                inspect_git_resource(root, resource_id=resource_id),
                source_id="general-work-workforce-test",
            )
        )
    else:
        database.append(
            Event.create(
                "project.registered",
                {
                    "resource_id": resource_id,
                    "kind": "directory",
                    "root_path": str(root),
                    "resolved_root": str(root.resolve()),
                    "baseline": {},
                    "capabilities": {},
                    "readiness_state": "READY",
                },
                source_type="test",
                source_id="general-work-workforce-test",
                idempotency_key=f"project.registered:{resource_id}",
            )
        )
    set_capability_state(
        database,
        resource_id,
        "cap.file.copy",
        "READY",
        source_id="general-work-workforce-test",
        evidence={"source": "cross-lane-test"},
    )
    set_capability_state(
        database,
        resource_id,
        "cap.general_work.verify",
        "READY",
        source_id="general-work-workforce-test",
        evidence={"source": "cross-lane-test"},
    )


def _authorize_general_work(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
) -> None:
    build_plan(
        database,
        plan.objective_id,
        (
            {
                "step_id": EXECUTIVE_STEP_ID,
                "description": plan.desired_outcome,
                "resource_id": plan.resource_id,
                "required_capabilities": (
                    "cap.file.copy",
                    "cap.general_work.verify",
                ),
                "output_contract": tuple(
                    deliverable.path for deliverable in plan.deliverables
                ),
                "verification_contract": (
                    "the General Work package passes fresh verification",
                ),
            },
        ),
        plan_id=EXECUTIVE_PLAN_ID,
        source_id="v13-general-work-workforce-executive-test",
    )
    contract = general_work_campaign_contract(
        database,
        plan,
        executive_step_id=EXECUTIVE_STEP_ID,
    )
    create_campaign(
        database,
        plan.objective_id,
        **contract.create_kwargs(),
    )


def _materialized_lane(
    tmp_path: Path,
    *,
    git_backed: bool,
    create_package: bool = False,
) -> Lane:
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()
    seed_builtins(database)
    source_repository: Path | None = None
    source_sha: str | None = None
    branch_name: str | None = None
    if git_backed:
        source_repository = tmp_path / "source"
        source_repository.mkdir()
        _git(source_repository, "init", "-b", "main")
        _git(source_repository, "config", "user.email", "jarvis@example.invalid")
        _git(source_repository, "config", "user.name", "JARVIS Test")
        (source_repository / "inputs").mkdir()
        (source_repository / "inputs" / "owner.txt").write_text(
            "owner metric: 42\n",
            encoding="utf-8",
        )
        _git(source_repository, "add", ".")
        _git(source_repository, "commit", "-m", "owner source")
        source_sha = _git(source_repository, "rev-parse", "HEAD")
        root = tmp_path / "owned"
        branch_name = "candidate/general-work-workforce"
        _git(
            source_repository,
            "worktree",
            "add",
            "-b",
            branch_name,
            str(root),
            source_sha,
        )
        package_id = general_work_work_package_id(PLAN_ID, SOURCE_STEP_ID)
        resource_id = f"workforce-resource:{package_id}"
    else:
        root = tmp_path / "owned"
        root.mkdir()
        (root / "inputs").mkdir()
        (root / "inputs" / "owner.txt").write_text(
            "owner metric: 42\n",
            encoding="utf-8",
        )
        resource_id = "resource-general-work-local"

    _register_general_work_resource(
        database,
        root,
        resource_id,
        git_resource=git_backed,
    )
    create_objective(
        database,
        "Deliver a bounded General Work artifact",
        objective_id="objective-general-work-workforce",
        resource_id=resource_id,
        success_criteria=("the copied owner source is independently verified",),
    )
    plan = _plan(root, resource_id)
    _authorize_general_work(database, plan)
    materialized = materialize_general_work_plan(
        database,
        plan,
        executive_step_id=EXECUTIVE_STEP_ID,
    )
    first = materialized.steps[0]
    contract = latest_contract(
        database,
        first.mission_id,
        step_id=first.runtime_step_id,
        allow_invalid=False,
    )
    assert contract is not None

    package_binding = None
    prepared = None
    if create_package:
        assert source_repository is not None
        assert source_sha is not None
        assert branch_name is not None
        package_binding = create_general_work_work_package(
            database,
            plan_id=plan.plan_id,
            source_step_id=SOURCE_STEP_ID,
            ai_delegated=True,
            source_repository=source_repository,
            source_sha=source_sha,
            repository_id="fixture-general-work-repository",
            branch_name=branch_name,
            worktree_path=root,
            required_tests=("python -m pytest -q",),
            lease_seconds=60,
        )
        assert package_binding is not None
        prepared = WorktreeManager(database).prepare(
            package_binding.package.package_id,
            source_repository=source_repository,
        )
        assert prepared.resource_id == resource_id
    return Lane(
        database=database,
        source_repository=source_repository,
        root=root,
        source_sha=source_sha,
        branch_name=branch_name,
        plan=plan,
        materialized=materialized,
        contract=contract,
        package_binding=package_binding,
        prepared=prepared,
    )


def _bind(lane: Lane) -> GatewayBinding:
    assert lane.package_binding is not None
    return WorkforceGateway(lane.database).bind(
        lane.package_binding.package.package_id
    )


def _dispatcher(lane: Lane) -> tuple[RoleDispatcher, FakeHermesAdapter]:
    inner = FakeHermesAdapter(working_root=lane.root)
    adapter = FabricRoleAdapter(
        lane.database,
        owned_root=lane.root,
        inner=inner,
        structured_only=True,
    )
    return RoleDispatcher(lane.database, RoleRouter(), adapter), inner


def _dispatch_kwargs(
    lane: Lane,
    binding: GatewayBinding,
    *,
    role: str,
    run_id: str,
    attempt: int,
) -> dict[str, object]:
    assert lane.package_binding is not None
    assert lane.prepared is not None
    package = lane.package_binding.package
    return {
        "role": role,
        "mission_id": package.mission_id,
        "resource_id": lane.prepared.resource_id,
        "capability": package.capability,
        "step_id": package.step_id,
        "run_id": run_id,
        "instructions": lane.contract.instructions,
        "duration_seconds": package.duration_seconds,
        "expected_artifacts": package.expected_artifacts,
        "verification_criteria": package.acceptance_criteria,
        "allowed_paths": package.allowed_paths,
        "forbidden_paths": package.forbidden_paths,
        "required_tests": package.required_tests,
        "attempt": attempt,
        "effect_action_key": package.effect_action_key,
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": binding.session.session_id,
    }


def _accepted_for(database: FoundryDatabase, mission_id: str) -> list[Event]:
    return [
        event
        for event in database.events(event_types=("verification.accepted",))
        if event.payload.get("mission_id") == mission_id
    ]


def _event_sequence(database: FoundryDatabase, event: Event) -> int:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT sequence FROM events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
    assert row is not None
    return int(row["sequence"])


def test_local_fabric_route_preserves_workforce_attempt_lineage(
    tmp_path: Path,
) -> None:
    lane = _materialized_lane(tmp_path, git_backed=True, create_package=True)
    assert resolve_general_work_dispatch(
        lane.database,
        role="builder",
        mission_id="personal-operator-mission",
        step_id="copy",
        capability="cap.file.copy",
    ) is None
    binding = _bind(lane)
    dispatcher, inner = _dispatcher(lane)

    result = dispatcher.dispatch(
        **_dispatch_kwargs(
            lane,
            binding,
            role="builder",
            run_id=binding.session.run_id,
            attempt=1,
        )
    )

    package = lane.package_binding.package
    assert result.status == COMPLETED
    assert result.provider == "fabric"
    assert result.effect_action_key == package.effect_action_key
    assert inner.received == []
    decisions = lane.database.events(
        event_types=("provider.route_decided",)
    )
    assert len(decisions) == 1
    decision = decisions[0].payload
    assert decision["selected"]["provider"] == "fabric"
    assert decision["package_id"] == package.package_id
    assert decision["package_hash"] == package.content_hash()
    assert decision["attempt_id"] == binding.session.session_id
    package_event = lane.database.events(
        event_types=("workforce.package_created",)
    )[0]
    assert package_event.payload["lineage"]["general_work_plan_id"] == PLAN_ID


def test_target_claim_precedes_workforce_lease_and_replays_once(
    tmp_path: Path,
) -> None:
    lane = _materialized_lane(tmp_path, git_backed=True, create_package=True)
    first_binding = _bind(lane)

    materialized_replay = materialize_general_work_plan(
        lane.database,
        lane.plan,
    )
    package_replay = create_general_work_work_package(
        lane.database,
        plan_id=lane.plan.plan_id,
        source_step_id=SOURCE_STEP_ID,
        ai_delegated=True,
        source_repository=lane.source_repository,
        source_sha=lane.source_sha,
        repository_id="fixture-general-work-repository",
        branch_name=lane.branch_name,
        worktree_path=lane.root,
        required_tests=("python -m pytest -q",),
        lease_seconds=60,
    )
    prepared_replay = WorktreeManager(lane.database).prepare(
        package_replay.package.package_id,
        source_repository=lane.source_repository,
    )
    binding_replay = WorkforceGateway(lane.database).bind(
        package_replay.package.package_id
    )

    target_claim = lane.database.events(
        event_types=("general_work.target_claimed",)
    )[0]
    session_bound = lane.database.events(
        event_types=("workforce.session_bound",)
    )[0]
    assert _event_sequence(
        lane.database,
        target_claim,
    ) < _event_sequence(lane.database, session_bound)
    assert materialized_replay.replayed is True
    assert package_replay.replayed is True
    assert prepared_replay == lane.prepared
    assert first_binding.session.session_id == binding_replay.session.session_id
    assert binding_replay.replayed is True
    assert lane.database.event_count(
        event_type="workforce.package_created"
    ) == 1
    assert lane.database.event_count(
        event_type="workforce.resource_claimed"
    ) == 1
    assert lane.database.event_count(
        event_type="workforce.session_bound"
    ) == 1


def test_frozen_effect_reconciles_under_same_package_action_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = _materialized_lane(tmp_path, git_backed=True, create_package=True)
    binding = _bind(lane)
    publication_stages: list[str] = []
    real_connector = GeneralWorkFileConnector

    def connector_factory(owned_root: str | Path) -> GeneralWorkFileConnector:
        def fail_once(stage: str) -> None:
            if stage == "file.copy.after_publish":
                publication_stages.append(stage)
                if len(publication_stages) == 1:
                    raise InjectedGeneralWorkFileFailure(
                        "simulated post-publication restart"
                    )

        return real_connector(owned_root, fault_injector=fail_once)

    monkeypatch.setattr(
        general_work_module,
        "GeneralWorkFileConnector",
        connector_factory,
    )
    dispatcher, _inner = _dispatcher(lane)
    frozen = dispatcher.dispatch(
        **_dispatch_kwargs(
            lane,
            binding,
            role="builder",
            run_id=f"{binding.session.run_id}:builder",
            attempt=1,
        )
    )
    target = lane.root / "delivery" / "owner.txt"
    landed_hash = _sha256(target)
    repaired = dispatcher.dispatch(
        **_dispatch_kwargs(
            lane,
            binding,
            role="repair",
            run_id=f"{binding.session.run_id}:repair",
            attempt=2,
        )
    )

    package = lane.package_binding.package
    assert frozen.status == "FAILED"
    assert frozen.effect_action_key == package.effect_action_key
    assert repaired.status == COMPLETED
    assert repaired.effect_action_key == package.effect_action_key
    assert _sha256(target) == landed_hash
    assert publication_stages == ["file.copy.after_publish"]
    assert lane.database.event_count(event_type="system.action_started") == 1
    assert lane.database.event_count(event_type="effect.reconciled") == 1


def test_general_work_verifier_remains_subordinate_to_mission_loop_acceptance(
    tmp_path: Path,
) -> None:
    lane = _materialized_lane(tmp_path, git_backed=False)
    inner = FakeHermesAdapter(working_root=lane.root)
    adapter = FabricRoleAdapter(
        lane.database,
        owned_root=lane.root,
        inner=inner,
        structured_only=True,
    )
    loop = MissionLoop(
        lane.database,
        RoleRouter(),
        adapter,
        max_repairs=0,
        stop_path=tmp_path / "STOP",
    )
    producer = loop.run_until_settled(
        **lane.contract.runtime_kwargs(lane.database)
    )
    assert producer.outcome == PASSED
    startup_resume_pass(
        lane.database,
        stop_path=tmp_path / "STOP",
    )

    verifier_step = lane.materialized.verifier
    verifier_contract = latest_contract(
        lane.database,
        verifier_step.mission_id,
        step_id=verifier_step.runtime_step_id,
        allow_invalid=False,
    )
    assert verifier_contract is not None
    direct = adapter.dispatch(
        ContextManifest(
            run_id="direct-general-work-verifier",
            role="verifier",
            mission_id=verifier_contract.mission_id,
            step_id=verifier_contract.step_id,
            resource_id=verifier_contract.resource_id,
            capability=verifier_contract.capability,
            provider="fabric",
            model="local",
            isolation_group="general-work-verifier",
            instructions=verifier_contract.instructions,
            expected_artifacts=verifier_contract.expected_artifacts,
            verification_criteria=verifier_contract.verification_criteria,
        ),
        heartbeat=lambda: None,
    )

    assert direct.status == COMPLETED
    assert direct.verdict == "PASS"
    assert _accepted_for(lane.database, verifier_contract.mission_id) == []
    accepted = loop.run_until_settled(
        **verifier_contract.runtime_kwargs(lane.database)
    )
    assert accepted.outcome == PASSED
    assert len(_accepted_for(lane.database, verifier_contract.mission_id)) == 1


def test_non_git_local_fabric_step_does_not_require_work_package(
    tmp_path: Path,
) -> None:
    lane = _materialized_lane(tmp_path, git_backed=False)

    package = create_general_work_work_package(
        lane.database,
        plan_id=lane.plan.plan_id,
        source_step_id=SOURCE_STEP_ID,
        ai_delegated=False,
    )
    loop = MissionLoop(
        lane.database,
        RoleRouter(),
        FabricRoleAdapter(
            lane.database,
            owned_root=lane.root,
            inner=FakeHermesAdapter(working_root=lane.root),
            structured_only=True,
        ),
        max_repairs=0,
        stop_path=tmp_path / "STOP",
    )
    outcome = loop.run_until_settled(
        **lane.contract.runtime_kwargs(lane.database)
    )

    assert package is None
    assert outcome.outcome == PASSED
    assert lane.database.event_count(event_type="workforce.package_created") == 0
    with pytest.raises(
        GeneralWorkWorkforceError,
        match="requires a Git repository|reserved Workforce resource",
    ):
        create_general_work_work_package(
            lane.database,
            plan_id=lane.plan.plan_id,
            source_step_id=SOURCE_STEP_ID,
            ai_delegated=True,
            source_repository=lane.root,
            source_sha="0" * 40,
            repository_id="not-git",
            branch_name="not-git",
            worktree_path=lane.root,
        )


def test_restart_reuses_plan_mission_package_attempt_and_effect_identities(
    tmp_path: Path,
) -> None:
    lane = _materialized_lane(tmp_path, git_backed=True, create_package=True)
    first_binding = _bind(lane)
    first_package = lane.package_binding.package
    first_mission = lane.materialized.steps[0].mission_id

    restarted_database = FoundryDatabase(lane.database.path)
    materialized_replay = materialize_general_work_plan(
        restarted_database,
        lane.plan,
    )
    package_replay = create_general_work_work_package(
        restarted_database,
        plan_id=lane.plan.plan_id,
        source_step_id=SOURCE_STEP_ID,
        ai_delegated=True,
        source_repository=lane.source_repository,
        source_sha=lane.source_sha,
        repository_id="fixture-general-work-repository",
        branch_name=lane.branch_name,
        worktree_path=lane.root,
        required_tests=("python -m pytest -q",),
        lease_seconds=60,
    )
    prepared_replay = WorktreeManager(restarted_database).prepare(
        package_replay.package.package_id,
        source_repository=lane.source_repository,
    )
    binding_replay = WorkforceGateway(restarted_database).bind(
        package_replay.package.package_id
    )
    restarted_lane = Lane(
        database=restarted_database,
        source_repository=lane.source_repository,
        root=lane.root,
        source_sha=lane.source_sha,
        branch_name=lane.branch_name,
        plan=lane.plan,
        materialized=materialized_replay,
        contract=lane.contract,
        package_binding=package_replay,
        prepared=prepared_replay,
    )
    dispatcher, _inner = _dispatcher(restarted_lane)
    kwargs = _dispatch_kwargs(
        restarted_lane,
        binding_replay,
        role="builder",
        run_id=binding_replay.session.run_id,
        attempt=1,
    )
    first_result = dispatcher.dispatch(**kwargs)
    replayed_result = _dispatcher(restarted_lane)[0].dispatch(**kwargs)

    assert materialized_replay.plan_id == lane.materialized.plan_id
    assert materialized_replay.plan_hash == lane.materialized.plan_hash
    assert materialized_replay.steps[0].mission_id == first_mission
    assert package_replay.package.package_id == first_package.package_id
    assert package_replay.package.content_hash() == first_package.content_hash()
    assert binding_replay.session.session_id == first_binding.session.session_id
    assert package_replay.package.effect_action_key == (
        first_package.effect_action_key
    )
    assert first_result.effect_action_key == first_package.effect_action_key
    assert replayed_result == first_result
    assert restarted_database.event_count(
        event_type="general_work.plan_materialized"
    ) == 1
    assert restarted_database.event_count(
        event_type="workforce.package_created"
    ) == 1
    assert restarted_database.event_count(
        event_type="workforce.session_bound"
    ) == 1
    assert restarted_database.event_count(event_type="system.action_started") == 1
    assert restarted_database.event_count(event_type="provider.route_decided") == 1
