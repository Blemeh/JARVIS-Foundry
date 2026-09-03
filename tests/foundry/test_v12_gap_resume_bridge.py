"""V12 capability-gap pause/expand/register/resume bridge proofs."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import (
    PlanStep,
    build_plan,
    create_objective,
    rebuild_executive_state,
    run_autonomy_cycle,
)
from jarvis_foundry.executive.continuous import rebuild_wake_state
from jarvis_foundry.fabric.catalog import (
    observed_readiness,
    record_outcome,
    register_capability,
)
from jarvis_foundry.fabric.gap_bridge import (
    ATTEMPT_STARTED,
    GAP_DEFERRED,
    GAP_OPENED,
    RESUMED,
    RECURSION_BLOCKED,
    WAKE_FIRED,
    CapabilityGapStatus,
    GapBridgeStopped,
    GapPolicy,
    bridge_missing_plan_capabilities,
    fail_expansion_attempt,
    rebuild_gap_state,
    reconcile_ready_gaps,
    request_alternate_replan,
    stable_gap_id,
    start_expansion_attempt,
)
from jarvis_foundry.fabric.model import (
    CapabilitySpec,
    CapabilityTrust,
    Kind,
    Readiness,
    Semantic,
    UnattendedMode,
    VerifyVerdict,
)
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle


MISSING = "cap.fake.report.render"


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _objective_with_gap(
    database: FoundryDatabase,
    *,
    objective_id: str = "objective-gap-proof",
    capability_id: str = MISSING,
    parent_id: str | None = None,
    metadata: dict | None = None,
    resource_id: str | None = None,
) -> tuple[str, str]:
    create_objective(
        database,
        "Produce the bounded proof artifact",
        objective_id=objective_id,
        parent_id=parent_id,
        resource_id=resource_id,
        required_capabilities=(capability_id,),
        budget={"max_missions": 2, "max_retries": 1},
        metadata=metadata or {},
    )
    plan = build_plan(
        database,
        objective_id,
        (PlanStep(
            step_id="render",
            objective_id=objective_id,
            description="Render the proof artifact",
            required_capabilities=(capability_id,),
            resource_id=resource_id,
            output_contract=("proof artifact exists",),
            verification_contract=("proof artifact was inspected",),
        ),),
    )
    return objective_id, plan.plan_id


def _register_ready(
    database: FoundryDatabase,
    capability_id: str,
    *,
    trust_state: CapabilityTrust = CapabilityTrust.ESTABLISHED,
) -> None:
    register_capability(
        database,
        CapabilitySpec(
            capability_id=capability_id,
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind=capability_id.removeprefix("cap."),
            semantic=Semantic.READ,
            executor="fake-proof-adapter",
            unattended=UnattendedMode.ALLOWED,
            limitations=("disposable proof only",),
        ),
        note="disposable V12 proof capability",
        trust_state=trust_state,
    )
    record_outcome(
        database,
        capability_id,
        success=True,
        verified=VerifyVerdict.MATCH,
        note="disposable independent verification matched",
    )


def _events(database: FoundryDatabase, event_type: str) -> list:
    return [event for event in database.events() if event.event_type == event_type]


def _row_count(database: FoundryDatabase, table: str) -> int:
    with database.connect(readonly=True) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_gap_identity_dedupe_candidate_link_and_wait_are_durable(tmp_path: Path) -> None:
    database = _database(tmp_path)
    objective_id, _plan_id = _objective_with_gap(database)

    opened = bridge_missing_plan_capabilities(database, objective_id)
    assert len(opened) == 1
    gap = opened[0]
    assert gap.gap_id == stable_gap_id(objective_id, MISSING)
    assert gap.candidate_id
    assert gap.work_id == f"expansion-work:{gap.gap_id}"
    assert gap.required_capability_id == MISSING

    objective = rebuild_executive_state(database).objectives[objective_id]
    assert objective.status == "WAITING"
    assert gap.gap_id in objective.metadata["capability_gap_ids"]
    wake = rebuild_wake_state(database)[str(gap.wake_id)]
    assert wake["kind"] == "WAIT_FOR_CAPABILITY"
    assert wake["status"] == "ACTIVE"
    candidate = database.row("candidates", "candidate_id", str(gap.candidate_id))
    assert candidate is not None
    assert candidate["candidate_type"] == "capability_gap"
    assert candidate["mission_id"] == objective_id
    assert len(_events(database, GAP_OPENED)) == 1

    event_count = len(database.events())
    replay = bridge_missing_plan_capabilities(database, objective_id)
    assert replay[0].gap_id == gap.gap_id
    assert len(database.events()) == event_count
    assert _row_count(database, "candidates") == 1


def test_restart_probation_registration_wakes_replans_and_resumes_once(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id, _plan_id = _objective_with_gap(database)
    gap = bridge_missing_plan_capabilities(database, objective_id)[0]
    attempt = start_expansion_attempt(
        database, gap.gap_id, strategy_id="session6.fake-adapter"
    )
    assert attempt.active_attempt is not None

    reopened = FoundryDatabase(database.path)
    persisted = rebuild_gap_state(reopened)[gap.gap_id]
    assert persisted.active_attempt is not None
    assert persisted.active_attempt.attempt_id == attempt.active_attempt.attempt_id

    _register_ready(reopened, MISSING, trust_state=CapabilityTrust.PROBATION)
    readiness = observed_readiness(reopened, MISSING)
    assert readiness.state is Readiness.READY
    assert readiness.trust_state is CapabilityTrust.PROBATION
    resumed = reconcile_ready_gaps(reopened, capability_id=MISSING)
    assert [item.gap_id for item in resumed] == [gap.gap_id]

    final = rebuild_gap_state(reopened)[gap.gap_id]
    assert final.status is CapabilityGapStatus.RESUMED
    assert final.effective_capability_state == "PROBATION"
    assert final.active_attempt is None
    objective = rebuild_executive_state(reopened).objectives[objective_id]
    assert objective.status == "ACTIVE"
    plan = rebuild_executive_state(reopened).plans[final.resumed_plan_id]
    assert plan.version == 2
    step = next(item for item in plan.steps if item.step_id == "render")
    assert MISSING in step.probation_capabilities
    assert len(_events(reopened, WAKE_FIRED)) == 1
    assert len(_events(reopened, RESUMED)) == 1
    assert rebuild_wake_state(reopened)[str(gap.wake_id)]["status"] == "TRIGGERED"

    event_count = len(reopened.events())
    assert reconcile_ready_gaps(reopened, capability_id=MISSING) == ()
    assert len(reopened.events()) == event_count


def test_restart_redelivers_started_but_undispatched_attempt_once(tmp_path: Path) -> None:
    database = _database(tmp_path)
    objective_id, _ = _objective_with_gap(database)
    gap = bridge_missing_plan_capabilities(database, objective_id)[0]
    started = start_expansion_attempt(
        database,
        gap.gap_id,
        strategy_id="session6.crash-window",
    )
    assert started.active_attempt is not None
    attempt_id = started.active_attempt.attempt_id
    assert started.active_attempt.dispatched_at is None

    receipts = []

    class RecordingPort:
        def dispatch(self, request):
            receipts.append(request.as_dict())
            return {"status": "ACCEPTED"}

    reopened = FoundryDatabase(database.path)
    delivered = start_expansion_attempt(
        reopened,
        gap.gap_id,
        strategy_id="ignored-because-attempt-is-durable",
        port=RecordingPort(),
    )
    assert len(receipts) == 1
    assert receipts[0]["attempt_id"] == attempt_id
    assert receipts[0]["attempt_ordinal"] == 1
    assert delivered.active_attempt is not None
    assert delivered.active_attempt.attempt_id == attempt_id
    assert delivered.active_attempt.dispatched_at is not None
    assert len(_events(reopened, ATTEMPT_STARTED)) == 1

    event_count = len(reopened.events())
    replay = start_expansion_attempt(
        reopened,
        gap.gap_id,
        strategy_id="still-ignored",
        port=RecordingPort(),
    )
    assert replay.active_attempt is not None
    assert len(receipts) == 1
    assert len(reopened.events()) == event_count


def test_probation_does_not_wake_when_policy_disallows_it(tmp_path: Path) -> None:
    database = _database(tmp_path)
    objective_id, _ = _objective_with_gap(database)
    gap = bridge_missing_plan_capabilities(
        database,
        objective_id,
        policy=GapPolicy(allow_probation=False),
    )[0]
    _register_ready(database, MISSING, trust_state=CapabilityTrust.PROBATION)

    assert reconcile_ready_gaps(database, capability_id=MISSING) == ()
    assert rebuild_gap_state(database)[gap.gap_id].status is CapabilityGapStatus.EXPANSION_PENDING
    assert rebuild_wake_state(database)[str(gap.wake_id)]["status"] == "ACTIVE"
    assert rebuild_executive_state(database).objectives[objective_id].status == "WAITING"


def test_multiple_gaps_keep_objective_waiting_until_every_capability_is_ready(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id = "objective-two-capability-gaps"
    second_capability = "cap.fake.report.sign"
    create_objective(
        database,
        "Produce and sign the bounded proof artifact",
        objective_id=objective_id,
        required_capabilities=(MISSING, second_capability),
        budget={"max_missions": 1, "max_retries": 0},
    )
    build_plan(
        database,
        objective_id,
        (PlanStep(
            step_id="render-and-sign",
            objective_id=objective_id,
            description="Render and sign the proof artifact",
            required_capabilities=(MISSING, second_capability),
            output_contract=("signed proof artifact exists",),
            verification_contract=("signed proof artifact was inspected",),
        ),),
    )
    gaps = bridge_missing_plan_capabilities(database, objective_id)
    assert {item.required_capability_id for item in gaps} == {
        MISSING,
        second_capability,
    }

    _register_ready(database, MISSING)
    first = reconcile_ready_gaps(database, capability_id=MISSING)
    assert len(first) == 1
    assert first[0].status is CapabilityGapStatus.RESUMED
    assert rebuild_executive_state(database).objectives[objective_id].status == "WAITING"
    assert len(_events(database, WAKE_FIRED)) == 1

    _register_ready(database, second_capability)
    second = reconcile_ready_gaps(database, capability_id=second_capability)
    assert len(second) == 1
    assert second[0].status is CapabilityGapStatus.RESUMED
    assert rebuild_executive_state(database).objectives[objective_id].status == "ACTIVE"
    assert len(_events(database, WAKE_FIRED)) == 2
    assert len(_events(database, RESUMED)) == 2


def test_open_gap_budget_defers_excess_without_aborting_autonomy(tmp_path: Path) -> None:
    database = _database(tmp_path)
    objective_id = "objective-open-gap-budget"
    second_capability = "cap.fake.report.publish"
    create_objective(
        database,
        "Render and publish one bounded proof",
        objective_id=objective_id,
        required_capabilities=(MISSING, second_capability),
    )
    build_plan(
        database,
        objective_id,
        (PlanStep(
            step_id="render-publish",
            objective_id=objective_id,
            description="Render and publish the proof",
            required_capabilities=(MISSING, second_capability),
        ),),
    )
    policy = GapPolicy(max_open_gaps_per_objective=1)
    first = bridge_missing_plan_capabilities(database, objective_id, policy=policy)
    assert len(first) == 1
    assert len(_events(database, GAP_DEFERRED)) == 1
    assert rebuild_executive_state(database).objectives[objective_id].status == "WAITING"

    _register_ready(database, first[0].required_capability_id)
    assert len(reconcile_ready_gaps(
        database, capability_id=first[0].required_capability_id
    )) == 1
    second = bridge_missing_plan_capabilities(database, objective_id, policy=policy)
    active = [item for item in second if not item.terminal]
    assert len(active) == 1
    assert active[0].required_capability_id == second_capability
    assert rebuild_executive_state(database).objectives[objective_id].status == "WAITING"


def test_port_claim_cannot_infer_registration_or_wake(tmp_path: Path) -> None:
    database = _database(tmp_path)
    objective_id, _ = _objective_with_gap(database)
    gap = bridge_missing_plan_capabilities(database, objective_id)[0]

    class ClaimingPort:
        def dispatch(self, request):
            assert request.gap_id == gap.gap_id
            return {"status": "REGISTERED", "claim": "tool exists now"}

    state = start_expansion_attempt(
        database,
        gap.gap_id,
        strategy_id="session6.untrusted-claim",
        port=ClaimingPort(),
    )
    assert state.status is CapabilityGapStatus.EXPANDING
    assert observed_readiness(database, MISSING).state is Readiness.MISSING
    assert len(_events(database, WAKE_FIRED)) == 0
    assert rebuild_executive_state(database).objectives[objective_id].status == "WAITING"


def test_failed_expansion_exhausts_budget_then_ready_alternate_replans(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id, _ = _objective_with_gap(database)
    gap = bridge_missing_plan_capabilities(
        database,
        objective_id,
        policy=GapPolicy(max_attempts_per_gap=1),
    )[0]
    started = start_expansion_attempt(
        database, gap.gap_id, strategy_id="session7.failed-trial"
    )
    failed = fail_expansion_attempt(
        database,
        gap.gap_id,
        attempt_id=started.active_attempt.attempt_id,
        reason="isolated trial verifier mismatched",
        reason_code="TRIAL_VERIFICATION_FAILED",
        retryable=True,
    )
    assert failed.status is CapabilityGapStatus.BUDGET_EXHAUSTED
    assert failed.attempts[0].retryable is False
    assert rebuild_executive_state(database).objectives[objective_id].status == "WAITING"

    alternate = "cap.fake.report.existing"
    _register_ready(database, alternate)
    replanned = request_alternate_replan(
        database,
        gap.gap_id,
        alternate_capability_id=alternate,
        reason="reuse the already verified renderer",
    )
    assert replanned.status is CapabilityGapStatus.ALTERNATE_RESUMED
    objective = rebuild_executive_state(database).objectives[objective_id]
    assert objective.status == "ACTIVE"
    plan = rebuild_executive_state(database).plans[replanned.resumed_plan_id]
    step = next(item for item in plan.steps if item.step_id == "render")
    assert alternate in step.required_capabilities
    assert MISSING not in step.required_capabilities
    assert rebuild_wake_state(database)[str(gap.wake_id)]["status"] == "CLEARED"


def test_expansion_objective_cannot_spawn_recursive_expansion(tmp_path: Path) -> None:
    database = _database(tmp_path)
    root_id, _ = _objective_with_gap(
        database,
        objective_id="root-objective",
        capability_id="cap.root.existing",
    )
    expansion_id, _ = _objective_with_gap(
        database,
        objective_id="expansion-objective",
        capability_id=MISSING,
        parent_id=root_id,
        metadata={
            "objective_class": "CAPABILITY_EXPANSION",
            "expansion_depth": 1,
            "origin_objective_id": root_id,
        },
    )
    rows_before = _row_count(database, "candidates")
    result = bridge_missing_plan_capabilities(database, expansion_id)
    assert len(result) == 1
    assert result[0].status is CapabilityGapStatus.RECURSION_BLOCKED
    assert _row_count(database, "candidates") == rows_before
    assert len(_events(database, RECURSION_BLOCKED)) == 1
    assert len(_events(database, ATTEMPT_STARTED)) == 0


def test_stop_blocks_gap_and_attempt_without_any_bridge_mutation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    objective_id, _ = _objective_with_gap(database)
    stop = tmp_path / "EMERGENCY_STOP"
    stop.write_text("owner stop\n", encoding="utf-8")
    count = len(database.events())
    with pytest.raises(GapBridgeStopped, match="STOP active"):
        bridge_missing_plan_capabilities(database, objective_id)
    assert len(database.events()) == count
    assert len(_events(database, GAP_OPENED)) == 0

    stop.unlink()
    gap = bridge_missing_plan_capabilities(database, objective_id)[0]
    stop.write_text("owner stop\n", encoding="utf-8")
    count = len(database.events())
    with pytest.raises(GapBridgeStopped, match="STOP active"):
        start_expansion_attempt(
            database, gap.gap_id, strategy_id="session6.blocked-by-stop"
        )
    assert len(database.events()) == count


def _git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "v12@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "V12 Proof"],
        check=True,
    )
    (repository / "README.md").write_text("# V12 proof\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "README.md"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )
    return repository


def test_real_fabric_and_mission_machinery_resume_end_to_end(tmp_path: Path) -> None:
    """Disposable A5 bridge proof without search/trial implementation."""

    database = _database(tmp_path)
    repository = _git_repository(tmp_path)
    resource_id = "resource-v12-proof"
    database.append(registration_event(
        inspect_git_resource(repository, resource_id=resource_id)
    ))
    set_capability_state(
        database,
        resource_id,
        "write_registered_worktree_files",
        "READY",
        evidence={"source": "v12-gap-resume-proof"},
    )
    objective_id = "objective-v12-real-mission"
    create_objective(
        database,
        "Create the resumed mission proof report",
        objective_id=objective_id,
        resource_id=resource_id,
        required_capabilities=(MISSING, "write_registered_worktree_files"),
        success_criteria=("resumed report exists",),
        budget={"max_missions": 2, "max_retries": 1},
    )

    first = run_autonomy_cycle(
        database,
        cycle_id="v12-gap-detected",
        resource_id=resource_id,
        capability="write_registered_worktree_files",
    )
    assert first.materialization["materialized"] is False
    gap = next(iter(rebuild_gap_state(database).values()))
    assert rebuild_executive_state(database).objectives[objective_id].status == "WAITING"
    assert _row_count(database, "missions") == 0

    start_expansion_attempt(
        database, gap.gap_id, strategy_id="session6.fake-real-registration"
    )
    _register_ready(database, MISSING, trust_state=CapabilityTrust.PROBATION)
    assert len(reconcile_ready_gaps(database, capability_id=MISSING)) == 1

    second = run_autonomy_cycle(
        database,
        cycle_id="v12-gap-resumed",
        resource_id=resource_id,
        capability="write_registered_worktree_files",
    )
    assert second.materialization["materialized"] is True, second.to_payload()
    mission_id = str(second.materialization["mission_id"])
    assert database.row("missions", "mission_id", mission_id) is not None

    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        resource_id=resource_id,
        lab_scripted_hermes=True,
    )
    outcomes = []
    mission = None
    for ordinal in range(1, 4):
        outcomes.append(run_cycle(
            database,
            FoundryPaths(tmp_path),
            config,
            loop_id=f"v12-resumed-mission-executes-{ordinal}",
            instance_id=f"v12-proof-worker-{ordinal}",
        ))
        mission = database.row("missions", "mission_id", mission_id)
        if mission is not None and mission["state"] == "COMPLETE":
            break
    assert outcomes[0]["outcome"] == "PASSED", outcomes
    assert mission is not None
    assert mission["state"] == "COMPLETE", outcomes
    assert any(
        event.event_type == "mission.completed"
        and event.payload.get("mission_id") == mission_id
        for event in database.events()
    )
    with database.connect(readonly=True) as connection:
        pass_verdicts = int(connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts "
            "WHERE mission_id = ? AND status = 'PASS'",
            (mission_id,),
        ).fetchone()[0])
    assert pass_verdicts >= 1
    assert len(_events(database, WAKE_FIRED)) == 1
    assert len(_events(database, RESUMED)) == 1
    assert database.quick_check() == "ok"
