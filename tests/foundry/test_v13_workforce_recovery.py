from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    record_step_contract,
)
from jarvis_foundry.mission.loop import PASSED, StepOutcome
from jarvis_foundry.mission.verification import start_verification
from jarvis_foundry.supervisor import worker as worker_module
from jarvis_foundry.supervisor.worker import WorkerController
from jarvis_foundry.workforce.contracts import (
    ResourceClaim,
    WorkPackage,
    WorkerSession,
)
from jarvis_foundry.workforce.gateway import WorkforceGateway
from jarvis_foundry.workforce.recovery import WorkforceRecovery
from jarvis_foundry.workforce.state import (
    create_work_package,
    rebuild_workforce_state,
    transition_work_package,
)
from jarvis_foundry.workforce.worktrees import (
    WorktreeManager,
    WorktreePreparation,
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
    _git(root, "config", "user.email", "recovery@example.invalid")
    _git(root, "config", "user.name", "Workforce Recovery")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text(
        "BASE = True\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _fixture(
    tmp_path: Path,
    *,
    stop_path: Path | None = None,
) -> tuple[
    FoundryDatabase,
    WorkPackage,
    WorktreePreparation,
    Path,
]:
    source, source_sha = _repository(tmp_path)
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()
    package_id = "package-recovery"
    mission_id = "mission-recovery"
    step_id = "step-recovery"
    resource_id = f"workforce-resource:{package_id}"
    contract = MissionStepContract(
        mission_id=mission_id,
        step_id=step_id,
        version=1,
        resource_id=resource_id,
        capability="git_local_commit",
        target_relative_path="src/base.py",
        instructions="Make one bounded recovery fixture change.",
        expected_effects=("bounded fixture result exists",),
        rollback={"method": "preserve_worktree", "reversible": True},
        risk_class="C1",
        verification_criteria=("focused recovery tests pass",),
        expected_artifacts=("src/base.py",),
        forbidden_paths=("live",),
    )
    package = WorkPackage(
        package_id=package_id,
        parent_objective_id="objective-recovery",
        mission_id=mission_id,
        step_id=step_id,
        role="builder",
        capability=contract.capability,
        bounded_goal="Exercise restart audit and reconciliation.",
        acceptance_criteria=contract.verification_criteria,
        source_sha=source_sha,
        repository_id="fixture-repository",
        branch_name="candidate/package-recovery",
        worktree_path=str(tmp_path / "worktrees" / package_id),
        allowed_paths=("src",),
        forbidden_paths=contract.forbidden_paths,
        mission_step_contract_id=contract.contract_id,
        mission_step_contract_hash=contract.content_hash(),
        expected_artifacts=contract.expected_artifacts,
        required_tests=(
            "pytest -q tests/foundry/test_v13_workforce_recovery.py",
        ),
        lease_seconds=300,
    )
    create_work_package(database, package, stop_path=stop_path)
    prepared = WorktreeManager(
        database,
        stop_path=stop_path,
    ).prepare(
        package.package_id,
        source_repository=source,
    )
    _append(
        database,
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": package.bounded_goal,
            "resource_id": resource_id,
            "state": "PLANNED",
            "acceptance_criteria": list(package.acceptance_criteria),
            "next_action": contract.instructions,
        },
    )
    for status in ("ORIENTING", "ACTION_SELECTION"):
        _append(
            database,
            "mission.state_changed",
            {
                "mission_id": mission_id,
                "state": status,
                "next_action": contract.instructions,
            },
        )
    record_step_contract(database, contract)
    return database, package, prepared, source


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: dict,
    *,
    source_type: str = "test",
    source_id: str = "v13-workforce-recovery",
) -> Event:
    event = Event.create(
        event_type,
        payload,
        source_type=source_type,
        source_id=source_id,
        correlation_id=str(
            payload.get("package_id")
            or payload.get("mission_id")
            or payload.get("run_id")
            or "recovery"
        ),
        idempotency_key=(
            f"{event_type}:recovery:{database.event_count(readonly=True)}"
        ),
    )
    database.append(event)
    return event


def _record_agent_completed(
    database: FoundryDatabase,
    package: WorkPackage,
    *,
    run_id: str,
) -> None:
    _append(
        database,
        "agent_run.started",
        {
            "run_id": run_id,
            "mission_id": package.mission_id,
            "step_id": package.step_id,
            "role": package.role,
            "provider": "fixture-provider",
            "model": "fixture-model",
            "context_manifest_hash": "a" * 64,
            "tool_policy": {},
        },
    )
    _append(
        database,
        "agent_run.completed",
        {
            "run_id": run_id,
            "result": {
                "artifact": "src/base.py",
                "partial_evidence": True,
            },
            "cost": {},
        },
    )


def _sleep_command(seconds: int = 60) -> list[str]:
    return [
        sys.executable,
        "-c",
        f"import time; time.sleep({seconds})",
    ]


def _exit_command() -> list[str]:
    return [sys.executable, "-c", "raise SystemExit(0)"]


def test_restart_audit_reopens_database_and_reconstructs_binding(
    tmp_path: Path,
) -> None:
    database, package, prepared, _source = _fixture(tmp_path)
    binding = WorkforceGateway(database).bind(package.package_id)
    event_count = database.event_count(readonly=True)

    reopened = FoundryDatabase(database.path)
    report = WorkforceRecovery(reopened).audit(
        package_id=package.package_id,
        now="2026-09-04T12:00:00Z",
    )

    recovered = report.package(package.package_id)
    assert recovered.status == "READY"
    assert recovered.version == 2
    assert len(recovered.worktrees) == 1
    worktree = recovered.worktrees[0]
    assert worktree.resource_id == prepared.resource_id
    assert worktree.valid is True
    assert worktree.base_is_ancestor is True
    assert worktree.dirty is False
    assert len(recovered.sessions) == 1
    assert recovered.sessions[0].session.run_id == binding.session.run_id
    assert recovered.sessions[0].category == "UNBACKED"
    assert [claim.claim_id for claim in recovered.claims] == [binding.claim_id]
    assert binding.resource_lease_key in {
        lease.lease_key for lease in recovered.leases
    }
    assert report.duplicate_issues == ()
    assert reopened.event_count(readonly=True) == event_count


def test_restart_audit_ignores_a_stale_claim_version(
    tmp_path: Path,
) -> None:
    database, package, _prepared, _source = _fixture(tmp_path)
    binding = WorkforceGateway(database).bind(package.package_id)
    claim = rebuild_workforce_state(database).claims[binding.claim_id]
    current_expiry = "2026-09-04T13:00:00Z"
    _append(
        database,
        "workforce.resource_renewed",
        {
            **claim.to_payload(),
            "lease_expires_at": current_expiry,
            "expected_status": "ACTIVE",
            "expected_version": 2,
            "version": 3,
        },
    )
    _append(
        database,
        "workforce.resource_released",
        {
            **claim.to_payload(),
            "status": "RELEASED",
            "expected_status": "ACTIVE",
            "expected_version": 2,
            "version": 3,
        },
    )

    canonical = rebuild_workforce_state(database).claims[binding.claim_id]
    recovered = WorkforceRecovery(
        FoundryDatabase(database.path)
    ).audit(
        package_id=package.package_id,
        now="2026-09-04T12:00:00Z",
    ).package(package.package_id).claims[0]

    assert canonical.status == recovered.status == "ACTIVE"
    assert canonical.lease_expires_at == recovered.lease_expires_at
    assert recovered.lease_expires_at == current_expiry


def test_live_process_truth_survives_restart_and_stop_only_blocks_reconcile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_path = tmp_path / "EMERGENCY_STOP"
    database, package, _prepared, _source = _fixture(
        tmp_path,
        stop_path=stop_path,
    )
    gateway = WorkforceGateway(database, stop_path=stop_path)
    owner = WorkerController(database, lease_seconds=300)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: _sleep_command(),
    )
    launch = gateway.spawn(package.package_id, owner)
    run_id = launch.binding.session.run_id
    assert launch.worker_status is not None
    assert launch.worker_status.process is not None
    monkeypatch.setattr(
        worker_module,
        "_record_is_live",
        lambda record: worker_module.pid_alive(int(record["pid"])),
    )
    try:
        reopened = FoundryDatabase(database.path)
        restarted = WorkerController(reopened, lease_seconds=300)
        recovery = WorkforceRecovery(
            reopened,
            stop_path=stop_path,
            workers=restarted,
        )
        audited = recovery.audit(package_id=package.package_id)
        session = audited.package(package.package_id).sessions[0]
        assert session.category == "ACTIVE"
        assert session.process_backed is True
        assert session.process_identity_valid is True
        assert session.process_alive is True

        before = reopened.event_count(readonly=True)
        live = recovery.reconcile_spawned(package_id=package.package_id)
        assert [item.outcome for item in live.actions] == ["ALIVE"]
        assert reopened.event_count(readonly=True) == before

        stop_path.write_text("STOP\n", encoding="utf-8")
        stopped = recovery.reconcile_spawned(package_id=package.package_id)
        assert [item.outcome for item in stopped.actions] == ["SKIPPED_STOP"]
        assert owner.status(run_id).alive is True
        observed = recovery.observe(
            package_id=package.package_id,
            now="2026-09-04T12:01:00Z",
        )
        assert observed.scanned == 1
        assert Path(package.worktree_path).is_dir()
    finally:
        stop_path.unlink(missing_ok=True)
        owner.terminate(run_id)


def test_dead_process_reconciles_once_without_duplicate_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, package, _prepared, _source = _fixture(tmp_path)
    gateway = WorkforceGateway(database)
    owner = WorkerController(database, lease_seconds=300)
    monkeypatch.setattr(
        gateway,
        "_runner_command",
        lambda _package_id, _run_id: _exit_command(),
    )
    launch = gateway.spawn(package.package_id, owner)
    run_id = launch.binding.session.run_id
    assert launch.worker_status is not None
    assert launch.worker_status.process is not None
    launch.worker_status.process.wait(timeout=10)

    reopened = FoundryDatabase(database.path)
    restarted = WorkerController(reopened, lease_seconds=300)
    recovery = WorkforceRecovery(reopened, workers=restarted)
    before = recovery.audit(package_id=package.package_id)
    assert before.package(package.package_id).sessions[0].category == "DEAD"

    first = recovery.reconcile_spawned(package_id=package.package_id)
    assert [item.outcome for item in first.actions] == ["RECONCILED"]
    assert first.after.package(package.package_id).status == "FAILED"
    assert (
        first.after.package(package.package_id).sessions[0].category
        == "TERMINAL"
    )
    second = recovery.reconcile_spawned(package_id=package.package_id)
    assert second.actions == ()

    assert reopened.event_count(
        event_type="workforce.package_created",
        readonly=True,
    ) == 1
    assert reopened.event_count(
        event_type="workforce.worktree_prepared",
        readonly=True,
    ) == 1
    assert reopened.event_count(
        event_type="workforce.session_bound",
        readonly=True,
    ) == 1
    crash_events = [
        event for event in reopened.events(
            event_types=("workforce.result_linked",),
            readonly=True,
        )
        if (
            isinstance(event.payload.get("result"), dict)
            and event.payload["result"].get("error_class") == "RuntimeError"
        )
    ]
    assert len(crash_events) == 1
    assert second.after.duplicate_issues == ()
    owner.terminate(run_id)


def test_dirty_partial_work_is_valid_and_preserved_without_launch(
    tmp_path: Path,
) -> None:
    database, package, _prepared, _source = _fixture(tmp_path)
    gateway = WorkforceGateway(database)
    gateway.bind(package.package_id)
    partial = Path(package.worktree_path) / "src" / "base.py"
    expected = "BASE = True\nRECOVER_ME = True\n"
    partial.write_text(expected, encoding="utf-8")
    event_count = database.event_count(readonly=True)

    recovery = WorkforceRecovery(FoundryDatabase(database.path))
    first = recovery.audit(package_id=package.package_id)
    worktree = first.package(package.package_id).worktrees[0]
    session = first.package(package.package_id).sessions[0]

    assert worktree.valid is True
    assert worktree.base_is_ancestor is True
    assert worktree.dirty is True
    assert worktree.preserve_required is True
    assert worktree.changed_paths == ("src/base.py",)
    assert session.partial_result is True
    skipped = recovery.reconcile_spawned(package_id=package.package_id)
    assert [item.outcome for item in skipped.actions] == [
        "SKIPPED_NOT_PROCESS_BACKED"
    ]
    assert partial.read_text(encoding="utf-8") == expected
    assert Path(package.worktree_path).is_dir()
    assert database.event_count(readonly=True) == event_count


def test_pending_verifier_and_effect_truth_remain_explicit_and_read_only(
    tmp_path: Path,
) -> None:
    database, package, _prepared, _source = _fixture(tmp_path)
    gateway = WorkforceGateway(database)
    binding = gateway.bind(package.package_id)
    transition_work_package(
        database,
        package.package_id,
        "RUNNING",
        reason="canonical fixture run started",
    )
    _record_agent_completed(
        database,
        package,
        run_id=binding.session.run_id,
    )
    verdict_id = f"verdict:{binding.session.run_id}"
    start_verification(
        database,
        verdict_id=verdict_id,
        mission_id=str(package.mission_id),
        step_id=str(package.step_id),
        builder_run_id=binding.session.run_id,
        verifier_run_id=f"{binding.session.run_id}:verify",
        independence={"session_independent": True},
        criteria=list(package.acceptance_criteria),
    )
    gateway.link_outcome(
        package.package_id,
        StepOutcome(
            outcome=PASSED,
            step_id=str(package.step_id),
            run_id=binding.session.run_id,
            verdict_id=verdict_id,
        ),
    )
    action_key = "effect:recovery-fixture"
    _append(
        database,
        "provider.effect_reconciliation_required",
        {
            "run_id": binding.session.run_id,
            "mission_id": package.mission_id,
            "step_id": package.step_id,
            "provider": "fixture-provider",
            "effect_action_key": action_key,
            "reason": "timeout after possible provider effect",
        },
    )
    _append(
        database,
        "effect.frozen",
        {
            "action_key": action_key,
            "reason": "provider outcome is ambiguous",
        },
        source_type="supervisor",
        source_id="v5-effect-driver",
    )

    recovery = WorkforceRecovery(FoundryDatabase(database.path))
    event_count = database.event_count(readonly=True)
    pending = recovery.audit(package_id=package.package_id)
    recovered = pending.package(package.package_id)

    assert recovered.status == "VERIFYING"
    assert recovered.sessions[0].category == "TERMINAL"
    assert recovered.sessions[0].agent_result["partial_evidence"] is True
    assert recovered.sessions[0].partial_result is True
    assert len(recovered.verifiers) == 1
    assert recovered.verifiers[0].status == "STARTED"
    assert recovered.verifiers[0].pending is True
    assert recovered.verifiers[0].accepted is False
    assert len(recovered.effects) == 1
    assert recovered.effects[0].action_key == action_key
    assert recovered.effects[0].status == "FROZEN"
    assert recovered.effects[0].pending is True
    assert pending.duplicate_issues == ()
    assert database.event_count(readonly=True) == event_count

    _append(
        database,
        "effect.reconciled",
        {
            "action_key": action_key,
            "reconciled": True,
            "provider_state": "COMPLETED",
        },
        source_type="supervisor",
        source_id="v5-effect-driver",
    )
    resolved_count = database.event_count(readonly=True)
    resolved = recovery.audit(package_id=package.package_id)
    assert resolved.package(package.package_id).effects[0].pending is False
    assert resolved.package(package.package_id).effects[0].reconciled is True
    assert resolved.package(package.package_id).effects[0].status == "RECONCILED"
    assert database.event_count(
        event_type="provider.effect_reconciled",
        readonly=True,
    ) == 0
    assert database.event_count(readonly=True) == resolved_count


def test_duplicate_active_identities_are_detected_and_not_reconciled(
    tmp_path: Path,
) -> None:
    database, package, prepared, _source = _fixture(tmp_path)
    first = WorkforceGateway(database).bind(package.package_id)
    second = WorkerSession(
        session_id="workforce:package-recovery:a2",
        package_id=package.package_id,
        run_id="workforce:package-recovery:a2",
        attempt=2,
        framework="PENDING",
        provider="PENDING",
        model="PENDING",
        status="STARTING",
        result={
            **first.session.result,
            "claim_id": "claim:package-recovery:g2",
        },
    )
    _append(database, "workforce.session_bound", second.to_payload())
    duplicate_claim = ResourceClaim(
        claim_id="claim:package-recovery:g2",
        package_id=package.package_id,
        session_id=second.session_id,
        resource_id=prepared.resource_id,
        worktree_path=prepared.worktree_path,
        mutable_paths=("src",),
        lease_expires_at="2026-09-04T13:00:00Z",
    )
    _append(
        database,
        "workforce.resource_claimed",
        duplicate_claim.to_payload(),
    )
    _append(
        database,
        "workforce.worktree_prepared",
        prepared.to_payload(),
    )
    action_key = "effect:duplicate-recovery"
    for ordinal in range(2):
        _append(
            database,
            "provider.effect_reconciliation_required",
            {
                "run_id": first.session.run_id,
                "mission_id": package.mission_id,
                "step_id": package.step_id,
                "provider": "fixture-provider",
                "effect_action_key": action_key,
                "ordinal": ordinal,
            },
        )

    recovery = WorkforceRecovery(FoundryDatabase(database.path))
    report = recovery.audit(package_id=package.package_id)
    codes = {issue.code for issue in report.duplicate_issues}

    assert "DUPLICATE_ACTIVE_SESSION" in codes
    assert "DUPLICATE_ACTIVE_CLAIM" in codes
    assert "DUPLICATE_WORKTREE_IDENTITY" in codes
    assert "DUPLICATE_EFFECT_REQUIREMENT" in codes
    blocked = recovery.reconcile_spawned(package_id=package.package_id)
    assert {item.outcome for item in blocked.actions} == {"SKIPPED_UNSAFE"}
    assert rebuild_workforce_state(database).package(
        package.package_id
    ).status == "READY"
