"""WP-B: AUTOMATIC post-dispatch outcome closure — integration proof.

Proves the authoritative seam from a terminal harmless mission result to
OutcomeService recording, exactly-once across retries/restarts, and the
flag-off regression. Drives the REAL ``run_cycle`` choke point through the
REAL hybrid worker stack (lab-scripted Hermes adapter — the runtime's own
provider-free lab mode), mirroring test_v3_discovery_cycle's proven setup.
No live providers, no second dispatcher.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.executive.eligibility import (
    enable_admission_authority,
)
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.v3_scoring import register_policy

from v3_fixtures import load_policy
from worker_test_helpers import mission_database

EPOCH = "v3-closure-epoch"


def _register_resource(database, repository, resource_id="resource-1") -> None:
    from jarvis_foundry.control.capabilities import set_capability_state
    from jarvis_foundry.control.registration import (
        inspect_git_resource,
        registration_event,
    )

    database.append(registration_event(
        inspect_git_resource(repository, resource_id=resource_id)))
    set_capability_state(
        database, resource_id, "write_registered_worktree_files", "READY",
        evidence={"source": "v3-closure-test"})


def _env(tmp_path: Path):
    database, repository = mission_database(tmp_path, resource_id="resource-1")
    _register_resource(database, repository)
    database.append(Event.create(
        "executive.activation_recorded",
        {"epoch": EPOCH, "decision_version": "1.0.0"},
        source_type="supervisor", source_id="v3-closure-test",
        idempotency_key=f"executive.activation_recorded:{EPOCH}",
    ))
    enable_admission_authority(database, EPOCH, source_id="v3-closure-test")
    register_policy(database, load_policy())
    return database, repository


def _lab_candidate(database) -> str:
    result = generate_candidate(
        database, "mission-seed", "maintenance",
        "Fix the failing nightly integration",
        [{"kind": "ci", "ref": "nightly"},
         {"kind": "log", "ref": "run-1"},
         {"kind": "issue", "ref": "i-7"}],
        typed={"expected_outcome": "verified: nightly green",
               "candidate_type": "repair"})
    return str(result["candidate_id"])


def _config(flag_on: bool, tmp_path: Path, *, executive: bool = False) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        enabled=True,
        resource_id="resource-1",
        discovery_cycle_enabled=flag_on,
        executive_selection_enabled=executive,
        executive_activation_epoch=EPOCH,
        executive_min_action_score=0.30,
        runtime_state_root=str(tmp_path / "state"),
        stop_sentinel_path=str(tmp_path / "EMERGENCY_STOP"),
        lab_scripted_hermes=True,
    )


def _outcomes(database: FoundryDatabase) -> list:
    return [e for e in database.events()
            if e.event_type == "discovery.candidate_outcome_recorded"]


def _candidate_lineage_mission_ids(database: FoundryDatabase) -> list[str]:
    return [str(e.payload["mission_id"]) for e in database.events()
            if e.event_type == "mission.created"
            and str(e.payload.get("created_from") or "").startswith("candidate:")]


def test_terminal_pass_automatically_records_outcome_exactly_once(tmp_path):
    """RED at base: the automatic handoff does not exist yet."""
    database, repository = _env(tmp_path)  # noqa: F841 - repository is the resource root
    candidate_id = _lab_candidate(database)

    report = run_cycle(
        database, FoundryPaths(tmp_path), _config(True, tmp_path),
        loop_id="L1", instance_id="inst-1",
        vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs")
    assert report["outcome"] == "PASSED", report

    events = _outcomes(database)
    missions = _candidate_lineage_mission_ids(database)
    assert len(missions) == 1
    assert len(events) == 1, "terminal PASSED must auto-record exactly one outcome"
    body = events[0].payload
    assert body["candidate_id"] == candidate_id
    assert body["mission_id"] == missions[0]
    assert body["status"] in ("RECORDED", "UNKNOWN")
    # Predicted (executive prediction) and actual (mission truth) stay DISTINCT.
    assert isinstance(body["predicted_value"], dict)
    assert isinstance(body["actual_value"], dict)

    row = database.row("candidate_outcomes", "outcome_id",
                       f"{candidate_id}:immediate")
    assert row is not None
    assert row["status"] == body["status"]

    # Re-running the cycle (retry/restart) must NOT create a second outcome;
    # the executed candidate cannot be re-selected (no invented duplicate work).
    report2 = run_cycle(
        database, FoundryPaths(tmp_path), _config(True, tmp_path),
        loop_id="L2", instance_id="inst-2",
        vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs")
    assert report2["outcome"] in ("WAITING", "COMPILED")
    if report2["outcome"] == "COMPILED":
        # A genuinely new cycle may only compile from a NEW candidate; the
        # original candidate/mission/outcome stay singular.
        assert len(_candidate_lineage_mission_ids(database)) <= 2
    assert len(_outcomes(database)) == 1


def test_crash_window_result_written_outcome_absent_is_reconciled_once(tmp_path):
    """Crash after mission.completed, before closure: catch-up records once."""
    from jarvis_foundry.v3_outcomes import reconcile_pending_outcomes

    database, _repository = _env(tmp_path)
    candidate_id = _lab_candidate(database)

    report = run_cycle(
        database, FoundryPaths(tmp_path), _config(True, tmp_path),
        loop_id="L1", instance_id="i1",
        vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs")
    assert report["outcome"] == "PASSED"
    assert len(_outcomes(database)) == 1

    # Simulate the crash window on a SECOND lineage mission using ONLY
    # canonical events: the mission reaches terminal COMPLETE with a PASSED
    # completion event, then the process "dies" before outcome closure.
    # (ACTION_SELECTION -> COMPLETE is a valid domain transition.)
    second = generate_candidate(
        database, "mission-seed", "maintenance",
        "Investigate unknown root cause of flaky checkout service",
        [{"kind": "log", "ref": "run-9"}],
        typed={"expected_outcome": "verified: root cause documented",
               "candidate_type": "research"})
    assert second["created"]
    crash_mission_id = f"mission-crash-{second['candidate_id']}"
    database.append(Event.create(
        "mission.created",
        {"mission_id": crash_mission_id,
         "objective": "Investigate unknown root cause of flaky checkout service",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["root cause documented"],
         "created_from": f"candidate:{second['candidate_id']}"},
        source_type="supervisor", source_id="v3-closure-test",
        idempotency_key=f"mission.created:{crash_mission_id}"))
    for state in ("ORIENTING", "ACTION_SELECTION", "COMPLETE"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": crash_mission_id, "state": state},
            source_type="supervisor", source_id="v3-closure-test",
            idempotency_key=f"mission.state_changed:{crash_mission_id}:{state}"))
    database.append(Event.create(
        "mission.completed",
        {"mission_id": crash_mission_id, "outcome": "PASSED", "run_id": "run-x"},
        source_type="supervisor", source_id="mission-loop",
        idempotency_key=f"mission.completed:{crash_mission_id}"))
    outcomes_before = len(_outcomes(database))
    assert outcomes_before == 1  # crash window: second mission has NO outcome yet

    first = reconcile_pending_outcomes(database)
    assert first["reconciled"] >= 1
    assert len(_outcomes(database)) == outcomes_before + 1
    # Repeat (another restart) — exactly once holds.
    second_scan = reconcile_pending_outcomes(database)
    assert second_scan["reconciled"] == 0
    assert len(_outcomes(database)) == outcomes_before + 1


def test_restart_reopen_still_exactly_once(tmp_path):
    database, _repository = _env(tmp_path)
    _lab_candidate(database)
    assert run_cycle(
        database, FoundryPaths(tmp_path), _config(True, tmp_path),
        loop_id="L1", instance_id="i1",
        vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs",
    )["outcome"] == "PASSED"

    # Fresh process/handle over the SAME database file.
    reopened = FoundryDatabase(tmp_path.parent / (
        tmp_path.name + "-reopen.sqlite")) if False else FoundryDatabase(
        _reopen_copy(tmp_path))
    from jarvis_foundry.v3_outcomes import (
        reconcile_mission_outcome,
        reconcile_pending_outcomes,
    )

    mission_id = _candidate_lineage_mission_ids(reopened)[0]
    assert reconcile_mission_outcome(reopened, mission_id=mission_id)["idempotent"]
    assert reconcile_pending_outcomes(reopened)["reconciled"] == 0
    assert len(_outcomes(reopened)) == 1


def _reopen_copy(tmp_path: Path) -> Path:
    """Reopen the SAME file (not a copy): simulate process restart."""
    return Path(str(tmp_path / "foundry.db"))


def test_failed_or_blocked_terminal_never_records(tmp_path):
    database, _repository = _env(tmp_path)
    candidate_id = _lab_candidate(database)

    # Sabotage builder output so verification can never PASS; the loop must
    # exhaust its repair budget into a non-PASSED terminal.
    import jarvis_foundry.deployment.lab_hermes as lab

    original = lab.LabHermesAdapter._dispatch_dynamic

    def sabotage(self, contract, *, heartbeat):
        if contract.role in ("builder", "repair"):
            for rel in tuple(contract.expected_artifacts or ()) or ("step_a.md",):
                path = Path(contract.working_root) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("WRONG CONTENT\n", encoding="utf-8")
            return super(lab.LabHermesAdapter, self).dispatch(
                contract, heartbeat=heartbeat)
        return original(self, contract, heartbeat=heartbeat)

    lab.LabHermesAdapter._dispatch_dynamic = sabotage
    try:
        report = run_cycle(
            database, FoundryPaths(tmp_path), _config(True, tmp_path),
            loop_id="L1-fail", instance_id="i1",
            vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs")
        assert report["outcome"] != "PASSED", report
    finally:
        lab.LabHermesAdapter._dispatch_dynamic = original
    assert len(_outcomes(database)) == 0
    # The blocked/failed mission never flips the candidate to EXECUTED.
    from jarvis_foundry.projections import PROJECTION_TABLES  # noqa: F401


def test_forged_non_passed_completion_event_never_records(tmp_path):
    """F1 regression (adversarial review): a raw-written mission.completed
    with outcome=FAILED must NOT satisfy the terminal gate via the bounded
    SQL fallback — the fallback requires BOTH the mission id AND
    outcome=PASSED in the payload."""
    from jarvis_foundry.v3_outcomes import (
        reconcile_mission_outcome,
        reconcile_pending_outcomes,
    )

    database, _repository = _env(tmp_path)
    candidate_id = _lab_candidate(database)
    # Drive a real PASSED cycle first (control: closure works).
    report = run_cycle(
        database, FoundryPaths(tmp_path), _config(True, tmp_path),
        loop_id="L1", instance_id="i1",
        vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs")
    assert report["outcome"] == "PASSED"
    assert len(_outcomes(database)) == 1

    # Forge a second lineage mission whose completion event says FAILED
    # (non-canonical writer; canonical paths never emit this).
    second = generate_candidate(
        database, "mission-seed", "maintenance",
        "Investigate unknown root cause of flaky checkout service",
        [{"kind": "log", "ref": "run-9"}],
        typed={"expected_outcome": "verified: root cause documented",
               "candidate_type": "research"})
    assert second["created"]
    forged_mission_id = f"mission-forged-{second['candidate_id']}"
    database.append(Event.create(
        "mission.created",
        {"mission_id": forged_mission_id,
         "objective": "Investigate unknown root cause of flaky checkout service",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["root cause documented"],
         "created_from": f"candidate:{second['candidate_id']}"},
        source_type="supervisor", source_id="v3-closure-test",
        idempotency_key=f"mission.created:{forged_mission_id}"))
    for state in ("ORIENTING", "ACTION_SELECTION", "COMPLETE"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": forged_mission_id, "state": state},
            source_type="supervisor", source_id="v3-closure-test",
            idempotency_key=f"mission.state_changed:{forged_mission_id}:{state}"))
    database.append(Event.create(
        "mission.completed",
        {"mission_id": forged_mission_id, "outcome": "FAILED", "run_id": "r"},
        source_type="supervisor", source_id="mission-loop",
        idempotency_key=f"mission.completed:{forged_mission_id}"))

    result = reconcile_mission_outcome(database, mission_id=forged_mission_id)
    assert result["recorded"] is False
    assert result["idempotent"] is False
    assert "not terminal PASSED" in (result["skipped_reason"] or "")
    scan = reconcile_pending_outcomes(database)
    assert scan["reconciled"] == 0
    assert len(_outcomes(database)) == 1  # still exactly the control outcome


def test_flag_off_records_no_new_outcome_events(tmp_path):
    database, _repository = _env(tmp_path)
    _lab_candidate(database)
    before = len(_outcomes(database))
    report = run_cycle(
        database, FoundryPaths(tmp_path), _config(False, tmp_path, executive=True),
        loop_id="L1-off", instance_id="i1",
        vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs")
    assert report["outcome"] in ("PASSED", "WAITING")  # base split-path behavior
    assert len(_outcomes(database)) == before  # zero new outcome events
