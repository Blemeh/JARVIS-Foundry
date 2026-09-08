from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import ingest_observed_candidate
from jarvis_foundry.discovery_sources import SourceSpec
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    create_objective,
    rebuild_executive_state,
    set_objective_status,
)
from jarvis_foundry.executive.decisions import ExecutiveDecision
from jarvis_foundry.executive.owner_context import seed_owner_context
from jarvis_foundry.fabric.catalog import record_outcome as record_fabric_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a2 import InitiativeA2Bounds, run_initiative_a2_cycle
from jarvis_foundry.initiative_a3 import (
    A3AdmissionPolicy,
    InitiativeAdmissionAuthority,
    continue_initiative_objective,
    pending_initiative_decision,
)
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle


def _repo(root: Path, note: str = "improve reliability and fix regression with a bounded reversible local test") -> None:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "a3@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "A3 Test"], check=True)
    (root / "work.py").write_text(f"# TODO: {note}\nVALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "work.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed owner repo"], check=True)


def _ready_database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "home" / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-09-02T00:00:00Z", "standing_mandate": True},
        source_type="owner",
        source_id="a3-owner-mandate",
        idempotency_key="system.start-build",
    ))
    seed_builtins(database)
    for capability in A3AdmissionPolicy().required_fabric_capabilities:
        record_fabric_outcome(
            database,
            capability,
            success=True,
            verified=VerifyVerdict.MATCH,
            note="A3 focused readiness proof",
            at="2026-09-02T00:00:00Z",
        )
    seed_owner_context(
        database,
        goals=[{
            "text": "Improve owner repositories with bounded verified reliability fixes",
            "source_ref": "IDEA.md",
            "source_hash": "a" * 64,
        }],
        projects=[{
            "text": "OwnerRepo active project",
            "source_ref": "docs/CURRENT_STATE.md",
            "source_hash": "b" * 64,
        }],
    )
    return database


def _source(root: Path, *, source_id: str = "owner-repo") -> SourceSpec:
    return SourceSpec(
        source_id,
        "project",
        str(root),
        1,
        {
            "root": str(root),
            "project_id": "OwnerRepo",
            "max_files": 20,
            "required_capabilities": list(A3AdmissionPolicy().required_fabric_capabilities),
        },
    )


def _a2(database: FoundryDatabase, root: Path, *, cycle_id: str = "a3-cold", now=None):
    result = run_initiative_a2_cycle(
        database,
        cycle_id=cycle_id,
        sources=[_source(root, source_id=cycle_id)],
        bounds=InitiativeA2Bounds(do_nothing_score=0.10),
        now=now,
    )
    assert result["decision"].kind == "SELECT"
    return result["decision"]


def _authority(database: FoundryDatabase, tmp_path: Path, root: Path, *, stop_path=None):
    return InitiativeAdmissionAuthority(
        database,
        worktree_parent=tmp_path / "a3-worktrees",
        project_roots={"OwnerRepo": root},
        stop_path=stop_path,
    )


def _manual_eligible(
    database: FoundryDatabase,
    objective: str,
    token: str,
    *,
    candidate_type: str = "tracked_work",
) -> ExecutiveDecision:
    candidate = ingest_observed_candidate(
        database,
        "a3-manual-source",
        observation_id=f"observation-{token}",
        source_kind="project",
        source_identity=f"repo://owner/{token}",
        content_hash=f"sha256:{token * 64}",
        objective=objective,
        target_project="OwnerRepo",
        candidate_type=candidate_type,
        confidence=0.8,
        expires_at="2099-01-01T00:00:00Z",
        evidence=[{
            "ref": f"repo://owner/{token}",
            "required_capabilities": list(A3AdmissionPolicy().required_fabric_capabilities),
        }],
    )
    candidate_id = str(candidate["candidate_id"])
    decision_id = f"exec-manual-{token}"
    database.append(Event.create(
        "executive.decision_recorded",
        {
            "decision_id": decision_id,
            "decision": "SELECT",
            "candidate_id": candidate_id,
            "reason": "focused pre-WIP A2 selection",
        },
        source_type="supervisor",
        source_id="a3-test",
        idempotency_key=f"executive.decision_recorded:{decision_id}",
    ))
    database.append(Event.create(
        "discovery.candidate_judged",
        {
            "judgment_id": f"judgment-{token}",
            "candidate_id": candidate_id,
            "fingerprint": candidate["fingerprint"],
            "decision_id": decision_id,
            "cycle_id": f"manual-{token}",
            "stage": "FINAL",
            "outcome": "ELIGIBLE",
            "reason_code": "READY_FOR_ADMISSION",
            "reason_detail": "focused pre-WIP A2 selection",
            "score": 0.8,
            "factors": {
                "confidence": {"value": 0.8},
                "reversibility": {"value": 0.9},
            },
            "evidence": [{"ref": f"repo://owner/{token}"}],
            "rank": 1,
        },
        source_type="supervisor",
        source_id="v11-a2-judgment",
        idempotency_key=f"discovery.candidate_judged:judgment-{token}",
    ))
    return ExecutiveDecision(
        kind="SELECT",
        decision_id=decision_id,
        reason="focused pre-WIP A2 selection",
        candidate_id=candidate_id,
    )


def test_eligible_candidate_admits_once_with_stable_objective_and_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    decision = _a2(database, root)
    authority = _authority(database, tmp_path, root)

    first = authority.admit(decision)
    replay = authority.admit(decision)

    assert first.admitted and replay.admitted and replay.replayed
    assert first.objective_id == replay.objective_id == f"initiative-objective:{decision.candidate_id}"
    assert first.worktree == replay.worktree
    assert Path(first.worktree).is_dir()
    assert database.row("candidates", "candidate_id", decision.candidate_id)["status"] == "APPROVED"
    assert database.event_count(event_type="initiative.candidate_admitted") == 1
    assert database.event_count(event_type="initiative.worktree_prepared") == 1
    assert database.event_count(event_type="executive.objective_created") == 1


def test_durable_select_is_resumed_once_after_pre_admission_restart(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    decision = _a2(database, root)

    recovered = pending_initiative_decision(database)
    assert recovered is not None
    assert recovered.kind == "SELECT"
    assert recovered.decision_id == decision.decision_id
    assert recovered.candidate_id == decision.candidate_id

    authority = _authority(database, tmp_path, root)
    first = authority.admit(recovered)
    replay = authority.admit(recovered)

    assert first.admitted and replay.admitted and replay.replayed
    assert pending_initiative_decision(database) is None
    assert database.event_count(event_type="initiative.candidate_admitted") == 1
    assert database.event_count(event_type="executive.objective_created") == 1
    assert database.event_count(event_type="initiative.worktree_prepared") == 1


def test_wait_stop_stale_equivalent_and_missing_capability_never_admit(tmp_path: Path) -> None:
    # Rejected/deferred A2 decision: never crosses the boundary.
    database = _ready_database(tmp_path / "wait")
    authority = InitiativeAdmissionAuthority(
        database,
        worktree_parent=tmp_path / "wait" / "worktrees",
        project_roots={},
    )
    wait = authority.admit(ExecutiveDecision(kind="WAIT", decision_id="wait", reason="none"))
    assert wait.status == "WAIT"
    assert database.event_count(event_type="executive.objective_created") == 0

    # STOP after A2 selection.
    stop_root = tmp_path / "stop" / "repo"
    _repo(stop_root)
    stop_db = _ready_database(tmp_path / "stop")
    stop_decision = _a2(stop_db, stop_root)
    stop = tmp_path / "stop" / "EMERGENCY_STOP"
    stop.write_text("owner STOP", encoding="utf-8")
    assert _authority(stop_db, tmp_path / "stop", stop_root, stop_path=stop).admit(stop_decision).status == "DEFERRED"
    assert stop_db.event_count(event_type="executive.objective_created") == 0

    # Candidate becomes stale after the A2 judgment.
    stale_root = tmp_path / "stale" / "repo"
    _repo(stale_root)
    stale_db = _ready_database(tmp_path / "stale")
    observed = datetime.now(timezone.utc).replace(microsecond=0)
    stale_decision = _a2(stale_db, stale_root, now=observed)
    stale = _authority(stale_db, tmp_path / "stale", stale_root).admit(
        stale_decision,
        now=observed + timedelta(hours=169),
    )
    assert stale.status == "DEFERRED" and "stale" in stale.reason
    assert stale_db.event_count(event_type="executive.objective_created") == 0

    # Equivalent objective pre-exists.
    duplicate_root = tmp_path / "duplicate" / "repo"
    _repo(duplicate_root)
    duplicate_db = _ready_database(tmp_path / "duplicate")
    duplicate_decision = _a2(duplicate_db, duplicate_root)
    objective = duplicate_db.row("candidates", "candidate_id", duplicate_decision.candidate_id)["objective"]
    create_objective(duplicate_db, objective, objective_id="existing-owner-objective")
    duplicate = _authority(duplicate_db, tmp_path / "duplicate", duplicate_root).admit(duplicate_decision)
    assert duplicate.status == "DEFERRED" and "equivalent" in duplicate.reason
    assert duplicate_db.event_count(event_type="executive.objective_created") == 1

    # Fabric readiness regresses between A2 and admission.
    capability_root = tmp_path / "capability" / "repo"
    _repo(capability_root)
    capability_db = _ready_database(tmp_path / "capability")
    capability_decision = _a2(capability_db, capability_root)
    record_fabric_outcome(
        capability_db,
        "cap.git.commit",
        success=False,
        verified=VerifyVerdict.MISMATCH,
        note="capability disappeared before admission",
        at="2026-09-02T00:01:00Z",
    )
    missing = _authority(capability_db, tmp_path / "capability", capability_root).admit(capability_decision)
    assert missing.status == "DEFERRED" and "cap.git.commit" in missing.reason
    assert capability_db.event_count(event_type="executive.objective_created") == 0


def test_unsafe_top_candidate_defers_so_next_eligible_can_be_evaluated(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    unsafe = _manual_eligible(
        database,
        "Review an external page without a bounded repository build",
        "3",
        candidate_type="web_watch",
    )
    safe = _manual_eligible(
        database, "Fix the next bounded owner repository regression", "4")
    authority = _authority(database, tmp_path, root)

    blocked = authority.admit(unsafe)
    admitted = authority.admit(safe)

    assert blocked.status == "DEFERRED"
    assert database.row("candidates", "candidate_id", unsafe.candidate_id)["status"] == "DEFERRED"
    assert admitted.admitted
    assert database.event_count(event_type="executive.objective_created") == 1
    assert database.event_count(event_type="initiative.worktree_prepared") == 1


def test_wip_cap_one_blocks_second_preselected_candidate(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    first = _manual_eligible(database, "Improve first bounded owner repo regression", "1")
    second = _manual_eligible(database, "Improve second bounded owner repo regression", "2")
    authority = _authority(database, tmp_path, root)

    assert authority.admit(first).admitted
    blocked = authority.admit(second)

    assert blocked.status == "DEFERRED"
    assert "WIP cap 1" in blocked.reason
    state = rebuild_executive_state(database)
    assert len([row for row in state.objectives.values() if row.owner_authority != "owner"]) == 1
    assert database.event_count(event_type="initiative.worktree_prepared") == 1


def test_consumed_build_budget_returns_to_wait_without_second_objective(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    first = _manual_eligible(database, "Improve one bounded owner repo regression", "5")
    second = _manual_eligible(database, "Improve another bounded owner repo regression", "6")
    authority = _authority(database, tmp_path, root)

    admitted = authority.admit(first)
    assert admitted.objective_id
    set_objective_status(
        database,
        admitted.objective_id,
        "COMPLETED",
        reason="focused budget proof",
    )
    blocked = authority.admit(second)

    assert blocked.status == "DEFERRED"
    assert "budget" in blocked.reason
    assert database.event_count(event_type="executive.objective_created") == 1
    assert database.event_count(event_type="initiative.worktree_prepared") == 1


def test_existing_executive_selection_cannot_be_linked_to_a3_objective(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    decision = _manual_eligible(database, "Fix bounded initiative regression", "9")
    admission = _authority(database, tmp_path, root).admit(decision)
    assert admission.admitted and admission.objective_id and admission.resource_id

    # Preserve owner priority when the existing Executive has another
    # executable objective on the same registered resource.  The A3 bridge
    # must wait rather than misattribute that mission to its candidate.
    create_objective(
        database,
        "Owner urgent objective",
        objective_id="owner-urgent",
        priority=3.0,
        urgency=1.0,
        expected_impact=1.0,
        resource_id=admission.resource_id,
        required_capabilities=("write_registered_worktree_files",),
    )

    resumed = continue_initiative_objective(
        database,
        admission.objective_id,
        cycle_id="a3-owner-priority",
    )

    assert resumed.status == "WAIT"
    assert resumed.mission_id is None
    assert "owner-urgent" in resumed.reason
    assert database.event_count(event_type="mission.created") == 1
    assert database.event_count(event_type="initiative.execution_linked") == 0


def test_runtime_full_path_accepts_once_then_waits_without_public_writes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        lab_scripted_hermes=True,
        lab_false_success_step_id=None,
        runtime_state_root=str(tmp_path / "runtime"),
        initiative_a3_enabled=True,
        initiative_a3={
            "enabled": True,
            "sources": [{
                "source_id": "owner-repo",
                "source_kind": "project",
                "source_identity": str(root),
                "poll_interval_s": 1,
                "config": dict(_source(root).config),
            }],
            "bounds": {"do_nothing_score": 0.10},
            "worktree_parent": str(tmp_path / "worktrees"),
            "project_roots": {"OwnerRepo": str(root)},
        },
    )
    config.validate()

    reports = [
        run_cycle(database, FoundryPaths(database.path.parent), config, loop_id=f"a3-{index}", instance_id=f"worker-{index}")
        for index in range(1, 5)
    ]

    assert [report["outcome"] for report in reports[:2]] == ["PASSED", "PASSED"]
    assert reports[-1]["outcome"] == "WAITING"
    assert database.event_count(event_type="initiative.candidate_admitted") == 1
    assert database.event_count(event_type="executive.objective_created") == 1
    assert database.event_count(event_type="mission.created") == 1
    assert database.event_count(event_type="initiative.worktree_prepared") == 1
    terminals = [event.payload for event in database.events(event_types=("initiative.candidate_terminal",))]
    assert len(terminals) == 1
    assert terminals[0]["status"] == "ACCEPTED"
    assert terminals[0]["verification_accepted"] is True
    assert terminals[0]["acceptance_event_id"]
    assert database.event_count(event_type="action.allowed") == 2
    worktree = Path(terminals[0]["worktree"])
    assert not subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain=v1"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert not any("push" in event.event_type for event in database.events())


def test_failed_build_never_passes_and_replay_creates_no_second_effect(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        lab_scripted_hermes=True,
        lab_false_success_step_id="02-execute",
        max_repairs=0,
        runtime_state_root=str(tmp_path / "runtime"),
        initiative_a3_enabled=True,
        initiative_a3={
            "enabled": True,
            "sources": [{
                "source_id": "owner-repo",
                "source_kind": "project",
                "source_identity": str(root),
                "poll_interval_s": 1,
                "config": dict(_source(root).config),
            }],
            "bounds": {"do_nothing_score": 0.10},
            "worktree_parent": str(tmp_path / "worktrees"),
            "project_roots": {"OwnerRepo": str(root)},
        },
    )
    config.validate()

    reports = [
        run_cycle(database, FoundryPaths(database.path.parent), config, loop_id=f"fail-{index}", instance_id=f"fail-worker-{index}")
        for index in range(1, 8)
    ]
    assert "BLOCKED" in [report["outcome"] for report in reports]
    assert database.event_count(event_type="initiative.candidate_admitted") == 1
    assert database.event_count(event_type="initiative.worktree_prepared") == 1
    assert database.event_count(event_type="executive.objective_created") == 1
    assert not any(
        event.payload.get("status") == "ACCEPTED"
        for event in database.events(event_types=("initiative.candidate_terminal",))
    )
    assert any(
        event.payload.get("status") == "FAILED"
        for event in database.events(event_types=("initiative.candidate_terminal",))
    )
    rejected = list(database.events(event_types=("verification.failed",)))
    assert rejected
    assert not any(
        receipt.payload.get("verdict_id") in {row.payload.get("verdict_id") for row in rejected}
        for receipt in database.events(event_types=("verification.accepted",))
    )
    before = {
        "objectives": database.event_count(event_type="executive.objective_created"),
        "missions": database.event_count(event_type="mission.created"),
        "worktrees": database.event_count(event_type="initiative.worktree_prepared"),
        "effects": database.event_count(event_type="action.allowed"),
    }
    run_cycle(database, FoundryPaths(database.path.parent), config, loop_id="fail-replay", instance_id="fail-replay-worker")
    after = {
        "objectives": database.event_count(event_type="executive.objective_created"),
        "missions": database.event_count(event_type="mission.created"),
        "worktrees": database.event_count(event_type="initiative.worktree_prepared"),
        "effects": database.event_count(event_type="action.allowed"),
    }
    assert after == before
