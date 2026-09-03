"""Disposable end-to-end proof for the V12 capability-gap resume bridge.

The proof uses a temporary Foundry database, a temporary Git resource, the
real Fabric Catalog, Executive autonomy cycle, Supervisor wake machinery, and
runtime worker loop.  Expansion search/trial is represented only by the
published port seam.  Nothing here addresses a live provider or installation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

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
from jarvis_foundry.executive.continuous import on_capability_changed, rebuild_wake_state
from jarvis_foundry.fabric.catalog import (
    observed_readiness,
    record_outcome,
    register_capability,
)
from jarvis_foundry.fabric.gap_bridge import (
    CONTRACT_VERSION,
    ATTEMPT_DISPATCHED,
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
from jarvis_foundry.reconcile import reconcile
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle


MISSING_CAPABILITY = "cap.fake.report.render"
ALTERNATE_CAPABILITY = "cap.fake.report.existing"


def _assert(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _events(database: FoundryDatabase, event_type: str) -> list[Any]:
    return [event for event in database.events() if event.event_type == event_type]


def _row_count(database: FoundryDatabase, table: str) -> int:
    with database.connect(readonly=True) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _database(root: Path, name: str) -> FoundryDatabase:
    database = FoundryDatabase(root / name / "foundry.db")
    database.initialize()
    return database


def _git_repository(root: Path) -> Path:
    repository = root / "repository"
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
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )
    return repository


def _register_ready(
    database: FoundryDatabase,
    capability_id: str,
    *,
    trust_state: CapabilityTrust,
) -> None:
    register_capability(
        database,
        CapabilitySpec(
            capability_id=capability_id,
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind=capability_id.removeprefix("cap."),
            semantic=Semantic.READ,
            executor="disposable-proof-adapter",
            unattended=UnattendedMode.ALLOWED,
            limitations=("disposable proof only",),
        ),
        note="disposable V12 bridge proof registration",
        trust_state=trust_state,
    )
    record_outcome(
        database,
        capability_id,
        success=True,
        verified=VerifyVerdict.MATCH,
        note="disposable verifier matched",
    )


def _simple_gap(
    database: FoundryDatabase,
    *,
    objective_id: str,
    capability_id: str,
    parent_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    policy: GapPolicy | None = None,
) -> str:
    create_objective(
        database,
        "Produce one bounded proof artifact",
        objective_id=objective_id,
        parent_id=parent_id,
        required_capabilities=(capability_id,),
        budget={"max_missions": 1, "max_retries": 0},
        metadata=metadata or {},
    )
    build_plan(
        database,
        objective_id,
        (PlanStep(
            step_id="render",
            objective_id=objective_id,
            description="Render the proof artifact",
            required_capabilities=(capability_id,),
            output_contract=("proof artifact exists",),
            verification_contract=("proof artifact was inspected",),
        ),),
    )
    states = bridge_missing_plan_capabilities(database, objective_id, policy=policy)
    _assert(len(states) == 1, "expected one canonical capability gap")
    return states[0].gap_id


def _real_resume_proof(root: Path) -> dict[str, Any]:
    database = _database(root, "real-resume")
    repository = _git_repository(root)
    resource_id = "resource-v12-gap-resume-proof"
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
    objective_id = "objective-v12-gap-resume-proof"
    create_objective(
        database,
        "Create the resumed mission proof report",
        objective_id=objective_id,
        resource_id=resource_id,
        required_capabilities=(
            MISSING_CAPABILITY,
            "write_registered_worktree_files",
        ),
        success_criteria=("resumed report exists",),
        budget={"max_missions": 2, "max_retries": 1},
    )

    first = run_autonomy_cycle(
        database,
        cycle_id="v12-gap-detected",
        resource_id=resource_id,
        capability="write_registered_worktree_files",
    )
    _assert(first.materialization["materialized"] is False, "gap cycle materialized")
    gaps = rebuild_gap_state(database)
    _assert(len(gaps) == 1, "autonomy did not create exactly one gap")
    gap = next(iter(gaps.values()))
    objective = rebuild_executive_state(database).objectives[objective_id]
    wake = rebuild_wake_state(database)[str(gap.wake_id)]
    _assert(objective.status == "WAITING", "objective did not enter WAIT")
    _assert(wake["status"] == "ACTIVE", "capability wake is not active")
    _assert(_row_count(database, "missions") == 0, "mission escaped while waiting")

    dedupe_count = database.event_count()
    replay = bridge_missing_plan_capabilities(database, objective_id)
    _assert(replay[0].gap_id == gap.gap_id, "gap identity changed on replay")
    _assert(database.event_count() == dedupe_count, "gap replay wrote duplicates")

    started = start_expansion_attempt(
        database,
        gap.gap_id,
        strategy_id="session6.disposable-registration",
    )
    _assert(started.active_attempt is not None, "attempt was not durable")
    attempt_id = started.active_attempt.attempt_id
    _assert(started.active_attempt.dispatched_at is None, "attempt dispatched unexpectedly")

    reopened = FoundryDatabase(database.path)
    persisted = rebuild_gap_state(reopened)[gap.gap_id]
    _assert(
        persisted.active_attempt is not None
        and persisted.active_attempt.attempt_id == attempt_id,
        "attempt did not survive database reopen",
    )

    class DataOnlyClaimPort:
        def dispatch(self, request: Any) -> dict[str, Any]:
            _assert(request.gap_id == gap.gap_id, "port received wrong gap")
            _assert(request.attempt_id == attempt_id, "port received wrong attempt")
            return {"status": "REGISTERED", "claim": "external text says ready"}

    delivered = start_expansion_attempt(
        reopened,
        gap.gap_id,
        strategy_id="ignored-on-restart-redelivery",
        port=DataOnlyClaimPort(),
    )
    _assert(
        delivered.active_attempt is not None
        and delivered.active_attempt.dispatched_at is not None,
        "restarted attempt was not dispatched",
    )
    _assert(
        observed_readiness(reopened, MISSING_CAPABILITY).state is Readiness.MISSING,
        "expansion receipt overrode Fabric authority",
    )
    _assert(len(_events(reopened, WAKE_FIRED)) == 0, "data-only claim fired wake")
    receipt = _events(reopened, ATTEMPT_DISPATCHED)[0].payload
    _assert(receipt["receipt_authority"] == "DATA_ONLY", "receipt gained authority")
    _register_ready(
        reopened,
        MISSING_CAPABILITY,
        trust_state=CapabilityTrust.PROBATION,
    )
    stop_file = Path(reopened.path).resolve().parent / "EMERGENCY_STOP"
    stop_file.write_text("disposable owner stop proof\n", encoding="utf-8")
    before_stopped_reconcile = reopened.event_count()
    stopped = False
    try:
        reconcile_ready_gaps(reopened, capability_id=MISSING_CAPABILITY)
    except GapBridgeStopped:
        stopped = True
    _assert(stopped, "STOP did not block ready-gap reconciliation")
    _assert(
        reopened.event_count() == before_stopped_reconcile,
        "STOP allowed bridge mutation",
    )
    stop_file.unlink()

    resumed = on_capability_changed(reopened)
    _assert(len(resumed) == 1, "Fabric registration did not resume the gap")
    final_gap = resumed[0]
    _assert(final_gap.status is CapabilityGapStatus.RESUMED, "gap not resumed")
    _assert(final_gap.effective_capability_state == "PROBATION", "policy not applied")
    _assert(
        rebuild_executive_state(reopened).objectives[objective_id].status == "ACTIVE",
        "objective did not return to ACTIVE",
    )
    _assert(
        rebuild_wake_state(reopened)[str(gap.wake_id)]["status"] == "TRIGGERED",
        "Supervisor wake did not trigger",
    )
    once_count = reopened.event_count()
    _assert(
        reconcile_ready_gaps(reopened, capability_id=MISSING_CAPABILITY) == (),
        "terminal gap reconciled twice",
    )
    _assert(reopened.event_count() == once_count, "wake replay wrote events")

    second = run_autonomy_cycle(
        reopened,
        cycle_id="v12-gap-resumed",
        resource_id=resource_id,
        capability="write_registered_worktree_files",
    )
    _assert(second.materialization["materialized"] is True, "replan did not materialize")
    mission_id = str(second.materialization["mission_id"])
    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        resource_id=resource_id,
        lab_scripted_hermes=True,
    )
    outcomes: list[str] = []
    mission = None
    for ordinal in range(1, 4):
        result = run_cycle(
            reopened,
            FoundryPaths(root),
            config,
            loop_id=f"v12-resumed-execution-{ordinal}",
            instance_id=f"v12-proof-worker-{ordinal}",
        )
        outcomes.append(str(result["outcome"]))
        mission = reopened.row("missions", "mission_id", mission_id)
        if mission is not None and mission["state"] == "COMPLETE":
            break
    _assert(mission is not None and mission["state"] == "COMPLETE", "mission incomplete")
    with reopened.connect(readonly=True) as connection:
        pass_verdicts = int(connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts "
            "WHERE mission_id = ? AND status = 'PASS'",
            (mission_id,),
        ).fetchone()[0])
    _assert(pass_verdicts >= 1, "resumed mission lacks a PASS verdict")
    _assert(len(_events(reopened, WAKE_FIRED)) == 1, "wake was not exactly once")
    _assert(len(_events(reopened, RESUMED)) == 1, "resume was not exactly once")
    _assert(reopened.quick_check() == "ok", "SQLite quick_check failed")
    report = reconcile(reopened, readonly=True)

    candidate = reopened.row("candidates", "candidate_id", str(gap.candidate_id))
    _assert(candidate is not None, "gap candidate linkage disappeared")
    return {
        "gap_id": gap.gap_id,
        "candidate_id": gap.candidate_id,
        "work_id": gap.work_id,
        "attempt_id": attempt_id,
        "wake_id": gap.wake_id,
        "origin_objective_id": objective_id,
        "mission_id": mission_id,
        "wait_status_before_registration": "WAITING",
        "catalog_before_registration": "MISSING",
        "catalog_after_registration": observed_readiness(
            reopened, MISSING_CAPABILITY
        ).as_dict(),
        "effective_resume_state": final_gap.effective_capability_state,
        "wake_events": len(_events(reopened, WAKE_FIRED)),
        "resume_events": len(_events(reopened, RESUMED)),
        "replan_version": rebuild_executive_state(reopened).plans[
            str(final_gap.resumed_plan_id)
        ].version,
        "mission_state": mission["state"],
        "worker_outcomes": outcomes,
        "verification_passes": pass_verdicts,
        "dedupe_event_delta": 0,
        "repeat_wake_event_delta": 0,
        "stop_event_delta": 0,
        "restart_rebuilt_attempt": True,
        "external_claim_authority": receipt["receipt_authority"],
        "database_quick_check": reopened.quick_check(),
        "reconciliation_consistent": report.consistent,
    }


def _failure_and_alternate_proof(root: Path) -> dict[str, Any]:
    database = _database(root, "failure-alternate")
    gap_id = _simple_gap(
        database,
        objective_id="objective-v12-failure-proof",
        capability_id=MISSING_CAPABILITY,
        policy=GapPolicy(max_attempts_per_gap=1),
    )
    started = start_expansion_attempt(
        database,
        gap_id,
        strategy_id="session7.disposable-failed-trial",
    )
    failed = fail_expansion_attempt(
        database,
        gap_id,
        attempt_id=started.active_attempt.attempt_id if started.active_attempt else None,
        reason="disposable isolated verifier mismatch",
        reason_code="TRIAL_VERIFICATION_FAILED",
        retryable=True,
    )
    _assert(failed.status is CapabilityGapStatus.BUDGET_EXHAUSTED, "budget not terminal")
    _register_ready(
        database,
        ALTERNATE_CAPABILITY,
        trust_state=CapabilityTrust.ESTABLISHED,
    )
    alternate = request_alternate_replan(
        database,
        gap_id,
        alternate_capability_id=ALTERNATE_CAPABILITY,
        reason="reuse verified existing adapter",
    )
    _assert(
        alternate.status is CapabilityGapStatus.ALTERNATE_RESUMED,
        "verified alternate did not resume",
    )
    objective = rebuild_executive_state(database).objectives[
        "objective-v12-failure-proof"
    ]
    _assert(objective.status == "ACTIVE", "alternate did not activate objective")
    return {
        "gap_id": gap_id,
        "failed_attempt_id": failed.attempts[0].attempt_id,
        "failed_status": failed.status.value,
        "failure_reason": failed.failure_reason,
        "alternate_capability_id": ALTERNATE_CAPABILITY,
        "alternate_state": alternate.status.value,
        "objective_state": objective.status,
    }


def _recursion_proof(root: Path) -> dict[str, Any]:
    database = _database(root, "recursion")
    create_objective(
        database,
        "Root objective for recursion proof",
        objective_id="objective-v12-recursion-root",
    )
    candidate_count = _row_count(database, "candidates")
    gap_id = _simple_gap(
        database,
        objective_id="objective-v12-expansion-child",
        capability_id="cap.fake.nested.expansion",
        parent_id="objective-v12-recursion-root",
        metadata={
            "objective_class": "CAPABILITY_EXPANSION",
            "expansion_depth": 1,
            "origin_objective_id": "objective-v12-recursion-root",
        },
    )
    state = rebuild_gap_state(database)[gap_id]
    _assert(
        state.status is CapabilityGapStatus.RECURSION_BLOCKED,
        "nested expansion was not blocked",
    )
    _assert(_row_count(database, "candidates") == candidate_count, "nested candidate created")
    _assert(len(_events(database, RECURSION_BLOCKED)) == 1, "recursion event missing")
    return {
        "gap_id": gap_id,
        "status": state.status.value,
        "candidate_delta": 0,
        "attempt_count": len(state.attempts),
    }


def build_proof() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="jarvis-v12-gap-resume-proof-") as raw:
        root = Path(raw)
        real = _real_resume_proof(root)
        failure = _failure_and_alternate_proof(root)
        recursion = _recursion_proof(root)
        return {
            "verdict": "V12_GAP_RESUME_BRIDGE_PASS",
            "contract_version": CONTRACT_VERSION,
            "scope": "DISPOSABLE_DATABASE_AND_GIT_RESOURCE_NOT_LIVE",
            "authority": "CAPABILITY_FABRIC_CATALOG",
            "external_source_text_can_establish_readiness": False,
            "real_resume": real,
            "failure_and_alternate": failure,
            "recursion_protection": recursion,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    proof = build_proof()
    rendered = json.dumps(proof, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
