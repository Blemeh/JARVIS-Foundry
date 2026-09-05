from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.verifier_binding import (
    AcceptanceGate,
    build_terminal_evidence,
    capture_binding,
)
from jarvis_foundry.mission.verification import (
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.workforce import (
    ResourceClaim,
    WorkPackage,
    WorkerSession,
    create_work_package,
    rebuild_workforce_state,
    transition_work_package,
    workforce_snapshot,
)
from jarvis_foundry.workforce.contracts import WorkforceContractError
from jarvis_foundry.workforce.state import (
    WorkforceStateError,
    _workforce_verification_criteria,
)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _package(tmp_path: Path, **overrides) -> WorkPackage:
    fields = {
        "package_id": "package-builder",
        "parent_objective_id": "objective-v13",
        "campaign_id": "campaign-v13",
        "mission_id": "mission-v13",
        "step_id": "step-v13",
        "role": "builder",
        "capability": "worker.code",
        "bounded_goal": "Implement one bounded workforce feature.",
        "acceptance_criteria": ("focused tests pass",),
        "source_sha": "7f153851517b5529dd1e582725a0c2a6d96ddc9b",
        "repository_id": "jarvis-foundry",
        "branch_name": "candidate/v13-package-builder",
        "worktree_path": str(tmp_path / "worktree"),
        "allowed_paths": ("src/jarvis_foundry/workforce",),
        "forbidden_paths": ("src/jarvis_foundry/executive",),
        "expected_artifacts": ("src/jarvis_foundry/workforce/gateway.py",),
        "required_tests": ("pytest tests/foundry/test_v13_workforce_gateway.py",),
        "evidence_requirements": ("independent verifier verdict",),
        "integration_target": "candidate/v13-ai-workforce-20260904",
        "owner_constraints": ("no live mutation",),
        "owner_veto_snapshot": {"active": []},
        "lineage": {"decomposition": "objective-v13:package-builder"},
    }
    fields.update(overrides)
    return WorkPackage(**fields)


def _bind_package_session(
    database: FoundryDatabase,
    package: WorkPackage,
    *,
    run_id: str = "workforce:package-builder:a1",
    attempt: int = 1,
) -> str:
    session = WorkerSession(
        session_id=run_id,
        package_id=package.package_id,
        run_id=run_id,
        attempt=attempt,
        framework="hermes",
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        status="COMPLETED",
    )
    database.append(Event.create(
        "workforce.session_bound",
        session.to_payload(),
        source_type="supervisor",
        source_id="workforce-gateway",
        idempotency_key=f"workforce.session_bound:{run_id}",
    ))
    return run_id


def _record_package_acceptance(
    database: FoundryDatabase,
    package: WorkPackage,
    *,
    run_id: str = "workforce:package-builder:a1",
) -> None:
    _bind_package_session(database, package, run_id=run_id)
    verdict_id = f"verdict:{run_id}"
    verifier_run_id = f"{run_id}:verify"
    target = Path(package.worktree_path)
    target.mkdir(parents=True, exist_ok=True)
    capture = capture_binding(
        database,
        verdict_id=verdict_id,
        mission_id=str(package.mission_id),
        step_id=str(package.step_id),
        target=target,
        builder_run_id=run_id,
        verifier_run_id=verifier_run_id,
        independence={"session_independent": True},
        criteria=list(_workforce_verification_criteria(package)),
    )
    record_verification_outcome(
        database,
        verdict_id=verdict_id,
        outcome="PASS",
        findings=[],
        evidence=build_terminal_evidence(
            capture=capture,
            verifier_session_id=verifier_run_id,
            extra={"fixture": True},
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


def test_work_package_round_trip_and_stable_hash(tmp_path: Path) -> None:
    package = _package(tmp_path)
    rebuilt = WorkPackage.from_payload(package.to_payload())
    assert rebuilt == package
    assert rebuilt.content_hash() == package.content_hash()


def test_work_package_rejects_ambiguous_or_unsafe_shape(tmp_path: Path) -> None:
    with pytest.raises(WorkforceContractError, match="40-character"):
        _package(tmp_path, source_sha="short")
    with pytest.raises(WorkforceContractError, match="must not be empty"):
        _package(tmp_path, acceptance_criteria=())
    with pytest.raises(WorkforceContractError, match="must not overlap"):
        _package(
            tmp_path,
            allowed_paths=("src/jarvis_foundry/workforce",),
            forbidden_paths=("src/jarvis_foundry/workforce",),
        )


def test_package_lifecycle_is_event_sourced_and_restart_safe(tmp_path: Path) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)

    created = create_work_package(database, package)
    assert created.status == "PLANNED"
    assert create_work_package(database, package) == created
    assert database.event_count(event_type="workforce.package_created") == 1

    ready = transition_work_package(
        database,
        package.package_id,
        "READY",
        reason="dependencies and resource boundaries validated",
    )
    running = transition_work_package(
        database,
        package.package_id,
        "RUNNING",
        reason="bounded worker session launched",
    )
    verifying = transition_work_package(
        database,
        package.package_id,
        "VERIFYING",
        reason="worker result awaits independent verification",
    )
    _record_package_acceptance(database, package)
    accepted = transition_work_package(
        database,
        package.package_id,
        "ACCEPTED",
        reason="independent verifier accepted observed artifacts",
    )

    assert [ready.version, running.version, verifying.version, accepted.version] == [
        2,
        3,
        4,
        5,
    ]
    assert accepted.terminal_state == "ACCEPTED"

    reopened = FoundryDatabase(database.path)
    state = rebuild_workforce_state(reopened)
    assert state.package(package.package_id).status == "ACCEPTED"
    assert workforce_snapshot(reopened, package.package_id)["packages"][
        package.package_id
    ]["source_sha"] == package.source_sha


def test_package_identity_and_dependency_invariants(tmp_path: Path) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)
    create_work_package(database, package)

    changed = _package(tmp_path, bounded_goal="A different immutable goal.")
    with pytest.raises(WorkforceStateError, match="another immutable contract"):
        create_work_package(database, changed)

    dependent = _package(
        tmp_path,
        package_id="package-dependent",
        dependencies=("package-missing",),
    )
    with pytest.raises(WorkforceStateError, match="dependencies do not exist"):
        create_work_package(database, dependent)


def test_semantic_package_retry_ignores_receipt_time(tmp_path: Path) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)
    first = create_work_package(database, package)

    replay = create_work_package(
        database,
        replace(package, created_at="2099-01-01T00:00:00Z"),
    )

    assert replay == first
    assert database.event_count(event_type="workforce.package_created") == 1


def test_concurrent_package_transitions_have_one_serial_winner(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)
    create_work_package(database, package)
    transition_work_package(
        database,
        package.package_id,
        "READY",
        reason="resource prepared",
    )

    def transition(target: str):
        try:
            return transition_work_package(
                FoundryDatabase(database.path),
                package.package_id,
                target,
                reason=f"concurrent {target}",
            )
        except WorkforceStateError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(transition, ("RUNNING", "REJECTED")))

    assert sum(not isinstance(value, Exception) for value in outcomes) == 1
    final = rebuild_workforce_state(database).package(package.package_id)
    assert final.status in {"RUNNING", "REJECTED"}
    assert final.version == 3
    transitions = database.events(
        event_types=("workforce.package_state_changed",)
    )
    assert len(transitions) == 2
    assert transitions[-1].payload["expected_version"] == 2


def test_stop_blocks_creation_and_mutating_transition(tmp_path: Path) -> None:
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    stop_path.write_text("stop", encoding="utf-8")
    package = _package(tmp_path)
    with pytest.raises(PermissionError, match="workforce stopped"):
        create_work_package(database, package, stop_path=stop_path)

    stop_path.unlink()
    create_work_package(database, package, stop_path=stop_path)
    stop_path.write_text("stop", encoding="utf-8")
    with pytest.raises(PermissionError, match="workforce stopped"):
        transition_work_package(
            database,
            package.package_id,
            "READY",
            reason="should not advance",
            stop_path=stop_path,
        )


def test_invalid_package_transition_is_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)
    create_work_package(database, package)
    with pytest.raises(WorkforceStateError, match="invalid package transition"):
        transition_work_package(
            database,
            package.package_id,
            "ACCEPTED",
            reason="cannot bypass execution and verification",
        )


def test_package_acceptance_requires_canonical_verifier_receipt(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)
    create_work_package(database, package)
    for status in ("READY", "RUNNING", "VERIFYING"):
        transition_work_package(
            database,
            package.package_id,
            status,
            reason=f"advance to {status}",
        )
    _bind_package_session(database, package)

    with pytest.raises(WorkforceStateError, match="verification.accepted"):
        transition_work_package(
            database,
            package.package_id,
            "ACCEPTED",
            reason="worker prose cannot self-certify",
        )


def test_package_acceptance_rejects_self_certification(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)
    create_work_package(database, package)
    for status in ("READY", "RUNNING", "VERIFYING"):
        transition_work_package(
            database,
            package.package_id,
            status,
            reason=f"advance to {status}",
        )
    run_id = _bind_package_session(database, package)
    verdict_id = f"verdict:{run_id}:self"
    start_verification(
        database,
        verdict_id=verdict_id,
        mission_id=str(package.mission_id),
        step_id=str(package.step_id),
        builder_run_id=run_id,
        verifier_run_id=run_id,
        independence={"session_independent": False},
        criteria=list(package.acceptance_criteria),
    )
    record_verification_outcome(
        database,
        verdict_id=verdict_id,
        outcome="PASS",
        findings=[],
        evidence={"fabrication_review": "performed"},
    )
    record_verification_acceptance(
        database,
        verdict_id=verdict_id,
        mission_id=str(package.mission_id),
        step_id=str(package.step_id),
        builder_run_id=run_id,
        verifier_run_id=run_id,
        checks={
            "stop_veto": "clear",
            "verdict_status": "PASS",
            "binding_present": "yes",
            "ordering": "ok",
            "binding_integrity": "ok",
            "identity": "ok",
            "isolation": "fresh-session",
            "evidence_complete": "yes",
            "post_verdict_mutation": "none",
        },
    )

    with pytest.raises(WorkforceStateError, match="verification.accepted"):
        transition_work_package(
            database,
            package.package_id,
            "ACCEPTED",
            reason="builder cannot certify itself",
        )


def test_superseded_attempt_cannot_accept_current_attempt(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)
    create_work_package(database, package)
    for status in ("READY", "RUNNING", "VERIFYING"):
        transition_work_package(
            database,
            package.package_id,
            status,
            reason=f"advance to {status}",
        )
    _record_package_acceptance(database, package)
    _bind_package_session(
        database,
        package,
        run_id="workforce:package-builder:a2",
        attempt=2,
    )

    with pytest.raises(WorkforceStateError, match="verification.accepted"):
        transition_work_package(
            database,
            package.package_id,
            "ACCEPTED",
            reason="stale attempt must not accept current work",
        )


def test_released_claim_absorbs_a_delayed_renewal_event(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)
    create_work_package(database, package)
    claim = ResourceClaim(
        claim_id="claim:package-builder:g1",
        package_id=package.package_id,
        session_id="workforce:package-builder:a1",
        resource_id="workforce-resource:package-builder",
        worktree_path=package.worktree_path,
        mutable_paths=package.allowed_paths,
        lease_expires_at="2026-09-04T12:00:00Z",
    )
    database.append(Event.create(
        "workforce.resource_claimed",
        claim.to_payload(),
        source_type="supervisor",
        source_id="test-suite",
        idempotency_key=f"workforce.resource_claimed:{claim.claim_id}",
    ))
    released = replace(claim, status="RELEASED")
    database.append(Event.create(
        "workforce.resource_released",
        released.to_payload(),
        source_type="supervisor",
        source_id="test-suite",
        idempotency_key=f"workforce.resource_released:{claim.claim_id}",
    ))
    delayed = replace(
        claim,
        lease_expires_at="2026-09-04T13:00:00Z",
    )
    database.append(Event.create(
        "workforce.resource_renewed",
        delayed.to_payload(),
        source_type="supervisor",
        source_id="test-suite",
        idempotency_key=f"workforce.resource_renewed:{claim.claim_id}:delayed",
    ))

    rebuilt = rebuild_workforce_state(database)

    assert rebuilt.claims[claim.claim_id].status == "RELEASED"
    assert rebuilt.active_claims() == ()


def test_stale_active_claim_version_cannot_overwrite_newer_renewal(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    package = _package(tmp_path)
    create_work_package(database, package)
    claim = ResourceClaim(
        claim_id="claim:package-builder:g1",
        package_id=package.package_id,
        session_id="workforce:package-builder:a1",
        resource_id="workforce-resource:package-builder",
        worktree_path=package.worktree_path,
        mutable_paths=package.allowed_paths,
        lease_expires_at="2026-09-04T12:00:00Z",
    )
    database.append(Event.create(
        "workforce.resource_claimed",
        {
            **claim.to_payload(),
            "expected_version": 0,
            "version": 1,
        },
        source_type="supervisor",
        source_id="test-suite",
        idempotency_key=f"workforce.resource_claimed:{claim.claim_id}",
    ))
    current = replace(
        claim,
        lease_expires_at="2026-09-04T13:00:00Z",
    )
    database.append(Event.create(
        "workforce.resource_renewed",
        {
            **current.to_payload(),
            "expected_status": "ACTIVE",
            "expected_version": 1,
            "version": 2,
        },
        source_type="supervisor",
        source_id="test-suite",
        idempotency_key=f"workforce.resource_renewed:{claim.claim_id}:current",
    ))
    stale = replace(
        claim,
        lease_expires_at="2026-09-04T14:00:00Z",
    )
    database.append(Event.create(
        "workforce.resource_renewed",
        {
            **stale.to_payload(),
            "expected_status": "ACTIVE",
            "expected_version": 1,
            "version": 2,
        },
        source_type="supervisor",
        source_id="test-suite",
        idempotency_key=f"workforce.resource_renewed:{claim.claim_id}:stale",
    ))

    rebuilt = rebuild_workforce_state(database)

    assert (
        rebuilt.claims[claim.claim_id].lease_expires_at
        == "2026-09-04T13:00:00Z"
    )
