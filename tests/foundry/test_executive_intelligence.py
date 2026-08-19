"""Mission 1 — Executive Intelligence focused tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate, score_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.executive.calibration import rebuild_calibration, record_calibration
from jarvis_foundry.executive.decisions import ExecutiveDecisionService
from jarvis_foundry.executive.eligibility import assess_eligibility, current_activation_epoch
from jarvis_foundry.executive.goals import StructuredGoal, active_goals, goal_alignment_score
from jarvis_foundry.executive.scoring import rank_candidates
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.router import READY, WorkerRouter

from worker_test_helpers import mission_database

EPOCH = "proof-epoch-m1"


def _register_resource(database: FoundryDatabase, repository: Path, resource_id: str = "resource-1") -> None:
    from jarvis_foundry.control.capabilities import set_capability_state
    from jarvis_foundry.control.registration import inspect_git_resource, registration_event
    database.append(registration_event(
        inspect_git_resource(repository, resource_id=resource_id)))
    set_capability_state(
        database, resource_id, "write_registered_worktree_files", "READY",
        evidence={"source": "executive-test"})


def _goal_directive(database: FoundryDatabase, secret: bytes, text: str) -> None:
    svc = OwnerControlService(database, secret=secret, owner_id="owner")
    nonce = "goal-nonce-1"
    sig = sign_command(secret, "GOAL", "GLOBAL", text, nonce)
    svc.apply("GOAL", target="GLOBAL", exact_text=text, nonce=nonce, signature=sig)


def _eligible_candidate(
    database: FoundryDatabase, objective: str, *, source: str = "executive:proof",
    extra_evidence: dict | None = None,
) -> str:
    evidence = [{"executive_epoch": EPOCH, "executive_eligible": True}]
    if extra_evidence:
        evidence.append(extra_evidence)
    result = generate_candidate(
        database, "mission-exec", source, objective, evidence=evidence)
    assert result["created"] is True
    cid = result["candidate_id"]
    score_candidate(database, cid)
    return cid


@pytest.fixture()
def exec_env(tmp_path: Path):
    database, repository = mission_database(tmp_path, resource_id="resource-1")
    secret = b"test-secret-32-bytes-minimum!!"
    _goal_directive(
        database, secret,
        "Improve gateway reliability and reduce customer outage risk today. "
        "priority: 3 success: latency under 200ms; zero customer incidents",
    )
    database.append(Event.create(
        "executive.activation_recorded",
        {"epoch": EPOCH, "decision_version": "1.0.0"},
        source_type="supervisor", source_id="test",
        idempotency_key=f"executive.activation_recorded:{EPOCH}",
    ))
    return database, repository, secret


def test_historical_candidate_firewall_blocks_unmarked(exec_env):
    database, _, _ = exec_env
    result = generate_candidate(
        database, "mission-exec", "maintenance",
        "Fix urgent crash risk outage tonight", evidence=[{"counts": 99}])
    row = database.row("candidates", "candidate_id", result["candidate_id"])
    eligibility = assess_eligibility(
        database, row, activation_epoch=EPOCH, resource_id="resource-1")
    assert eligibility.eligible is False
    assert eligibility.reason in {"blocked_source", "stale_historical"}


def test_goal_alignment_changes_ranking(exec_env):
    database, _, _ = exec_env
    bait = _eligible_candidate(
        database,
        "URGENT crash outage revenue fix tonight blocked deadline now",
        source="executive:proof-bait",
    )
    aligned = _eligible_candidate(
        database,
        "Reduce gateway customer outage risk and improve reliability latency",
        source="executive:proof-aligned",
    )
    goals = active_goals(database)
    assert goals
    ranked = rank_candidates(
        database,
        [database.row("candidates", "candidate_id", bait),
         database.row("candidates", "candidate_id", aligned)],
        goals=goals,
    )
    assert ranked[0].candidate_id == aligned
    assert ranked[0].goal_fit > ranked[1].goal_fit


def test_executive_rejects_low_value_waits(exec_env):
    database, _, _ = exec_env
    _eligible_candidate(
        database, "URGENT cosmetic typo label rename document",
        source="executive:proof-low",
    )
    svc = ExecutiveDecisionService(
        database, activation_epoch=EPOCH, min_action_score=0.55, source_id="test")
    decision = svc.evaluate(resource_id="resource-1", loop_id="t1")
    assert decision.kind == "WAIT"
    assert decision.wait is not None
    assert "below" in decision.reason.lower() or "waiting" in decision.reason.lower()


def test_executive_selects_goal_aligned_candidate(exec_env):
    database, _, _ = exec_env
    _eligible_candidate(database, "URGENT revenue crash fix tonight now", source="executive:bait")
    good = _eligible_candidate(
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        source="executive:good",
    )
    svc = ExecutiveDecisionService(
        database, activation_epoch=EPOCH, min_action_score=0.35, source_id="test")
    decision = svc.evaluate(resource_id="resource-1", loop_id="t2")
    assert decision.kind == "SELECT"
    assert decision.candidate_id == good


def test_calibration_penalizes_failed_pattern(exec_env):
    database, _, _ = exec_env
    objective = "Improve gateway reliability reduce outage risk"
    cid = _eligible_candidate(database, objective, source="executive:cal")
    record_calibration(
        database, decision_id="d1", candidate_id=cid, mission_id="m-fail",
        source="executive:cal", objective=objective, outcome="FAILED",
        repair_count=2,
    )
    state = rebuild_calibration(database)
    assert state.adjustment_for(source="executive:cal", objective=objective) < 0


def test_worker_loop_executive_disabled_by_default(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    cfg = WorkerRuntimeConfig(enabled=True)
    assert cfg.executive_selection_enabled is False
    report = run_cycle(
        database, FoundryPaths(tmp_path), cfg, loop_id="l1", instance_id="i1")
    assert report["outcome"] in {"WAITING", "DISABLED"}


class ExecutiveScriptedHermes(ScriptedWorkerAdapter):
    def __init__(self) -> None:
        super().__init__(WORKER_HERMES)

    def dispatch(self, contract, *, heartbeat):
        root = contract.working_root
        if contract.role in ("builder", "repair"):
            for rel in contract.expected_artifacts or ("executive-work/report.md",):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "Objective report\nVERIFIED_BY executive-worker\n",
                    encoding="utf-8")
            self.outcome = ScriptedOutcome(
                files_changed=tuple(contract.expected_artifacts or ()),
                observed_touched_paths=tuple(contract.expected_artifacts or ()),
                summary="wrote executive artifacts",
            )
        else:
            missing = [
                rel for rel in (contract.expected_artifacts or ())
                if not (root / rel).is_file()
            ]
            passed = not missing
            self.outcome = ScriptedOutcome(
                verdict="PASS" if passed else "FAIL",
                summary="verified" if passed else f"missing {missing}",
            )
        return super().dispatch(contract, heartbeat=heartbeat)


def test_worker_loop_executive_select_and_execute(exec_env, tmp_path, monkeypatch):
    database, repository, _ = exec_env
    _eligible_candidate(
        database,
        "Improve gateway reliability reduce customer outage risk latency",
        source="executive:run",
    )
    cfg = WorkerRuntimeConfig(
        enabled=True,
        resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        executive_min_action_score=0.30,
        runtime_state_root=str(tmp_path / "state"),
        stop_sentinel_path="EMERGENCY_STOP",
        lab_scripted_hermes=True,
    )
    adapter = ExecutiveScriptedHermes()
    worker_router = WorkerRouter()
    worker_router.mark_ready(WORKER_HERMES, state=READY, evidence=("exec-test",))
    bridge = HybridWorkerRoleAdapter(
        database=database, router=worker_router,
        registry=WorkerRegistry({WORKER_HERMES: adapter}),
        stop_path=tmp_path / "EMERGENCY_STOP",
    )
    stack = type("Stack", (), {"bridge": bridge, "router": worker_router})()

    def fake_stack(database, paths, config, *, run_id):
        return stack

    monkeypatch.setattr("jarvis_foundry.runtime.worker_loop.build_worker_stack", fake_stack)
    report = run_cycle(
        database, FoundryPaths(tmp_path), cfg,
        loop_id="proof-exec", instance_id="proof-inst",
    )
    assert report["outcome"] == "PASSED", report
    assert report.get("mission_id")
    events = [e.event_type for e in database.events()]
    assert "executive.prediction_recorded" in events
    assert "executive.calibration_recorded" in events


def test_stop_blocks_executive_selection(exec_env, tmp_path):
    database, _, _ = exec_env
    _eligible_candidate(database, "Improve gateway outage risk reliability")
    stop = tmp_path / "EMERGENCY_STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("{}", encoding="utf-8")
    cfg = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        stop_sentinel_path=str(stop),
    )
    report = run_cycle(
        database, FoundryPaths(tmp_path), cfg,
        loop_id="stop", instance_id="inst",
    )
    assert report["outcome"] == "FROZEN"
