"""CASE A kernel: next-action persistence, per-step contracts, evidence isolation."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    latest_contract,
    next_executable_contract,
    record_step_contract,
)
from jarvis_foundry.mission.loop import PASSED, WAITING_OUTCOME
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.router import READY, WorkerRouter

from worker_test_helpers import mission_database


EXPECTED = {
    "step-a": ("step_a.md", "STEP-A\n"),
    "step-b": ("step_b.md", "STEP-B\n"),
    "step-c": ("step_c.md", "STEP-C\n"),
}


class InspectingHermes(ScriptedWorkerAdapter):
    """Hermes-pinned adapter: builder claims are not verifier truth.

    Step-B's first builder attempt plants a reversible defect. The verifier
    inspects the worktree. Repair writes the correct bytes.
    """

    def dispatch(self, contract, *, heartbeat):
        artifact, expected = EXPECTED[contract.step_id]
        path = contract.working_root / artifact
        if contract.role in ("builder", "repair"):
            defect = (
                contract.role == "builder"
                and contract.step_id == "step-b"
                and contract.attempt == 1
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("DEFECT\n" if defect else expected, encoding="utf-8")
            self.outcome = ScriptedOutcome(
                files_changed=(artifact,),
                observed_touched_paths=(artifact,),
                summary=f"{contract.role} wrote {artifact}",
            )
        else:
            actual = path.read_text(encoding="utf-8") if path.is_file() else ""
            passed = actual == expected
            self.outcome = ScriptedOutcome(
                files_changed=(),
                observed_touched_paths=(),
                verdict="PASS" if passed else "FAIL",
                summary="artifact matches" if passed else "artifact mismatch",
            )
        return super().dispatch(contract, heartbeat=heartbeat)


def _contract(step_id: str, artifact: str, token: str) -> MissionStepContract:
    return MissionStepContract(
        mission_id="mission-1",
        step_id=step_id,
        version=1,
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        target_relative_path=artifact,
        instructions=f"create {artifact} containing {token.strip()}",
        expected_effects=(f"{artifact} exists",),
        rollback={"method": "git_reset_soft", "reversible": True},
        risk_class="C1",
        verification_criteria=(f"{artifact} contains {token.strip()}",),
        expected_artifacts=(artifact,),
        forbidden_paths=("owner-private",),
        recorded_by="supervisor",
        source="prompt1-m2",
        provenance={"kernel": "case-a"},
    )


def _stack(database, adapter, stop_path: Path):
    worker_router = WorkerRouter()
    worker_router.mark_ready(
        WORKER_HERMES, state=READY, note="isolated inspecting hermes",
        evidence=("prompt1-m2",),
    )
    bridge = HybridWorkerRoleAdapter(
        database=database, router=worker_router,
        registry=WorkerRegistry({WORKER_HERMES: adapter}),
        role_signals={
            "builder": {"ambiguous": True, "root_cause_required": True},
            "verifier": {"ambiguous": True, "root_cause_required": True},
            "repair": {"ambiguous": True, "root_cause_required": True, "recovery": True},
        },
        stop_path=stop_path,
    )
    return type("Stack", (), {"bridge": bridge, "router": worker_router})()


def _cfg(tmp_path: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        enabled=True, runtime_state_root=str(tmp_path / "state"),
        stop_sentinel_path="EMERGENCY_STOP", max_repairs=2,
    )


def test_next_executable_contract_skips_passed_steps(tmp_path):
    database, _ = mission_database(tmp_path)
    first = _contract("step-a", "step_a.md", "STEP-A")
    second = _contract("step-b", "step_b.md", "STEP-B")
    record_step_contract(database, first)
    record_step_contract(database, second)
    assert next_executable_contract(database, "mission-1").step_id == "step-a"
    database.append(Event.create(
        "verification.started",
        {"verdict_id": "v-a", "mission_id": "mission-1", "subject_type": "mission_step",
         "subject_id": "step-a", "verifier_run_id": "vr-a", "builder_run_id": "br-a",
         "independence": {}, "criteria": ["step_a.md contains STEP-A"]},
        source_type="supervisor", source_id="mission-loop",
        idempotency_key="verification.started:v-a",
    ))
    database.append(Event.create(
        "verification.passed",
        {"verdict_id": "v-a", "findings": [], "evidence": {}},
        source_type="supervisor", source_id="mission-loop",
        idempotency_key="verification.passed:v-a",
    ))
    assert next_executable_contract(database, "mission-1").step_id == "step-b"
    assert latest_contract(database, "mission-1", step_id="step-a") == first


def test_worker_loop_persists_next_action_and_isolates_step_b(tmp_path, monkeypatch):
    database, repository = mission_database(
        tmp_path, next_action="create step_a.md containing STEP-A")
    set_capability_state(
        database, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "prompt1-m2"})
    record_step_contract(database, _contract("step-a", "step_a.md", "STEP-A"))
    record_step_contract(database, _contract("step-b", "step_b.md", "STEP-B"))
    adapter = InspectingHermes(WORKER_HERMES)
    import jarvis_foundry.runtime.worker_loop as worker_loop
    monkeypatch.setattr(
        worker_loop, "build_worker_stack",
        lambda *args, **kwargs: _stack(database, adapter, tmp_path / "EMERGENCY_STOP"),
    )
    paths = FoundryPaths(tmp_path)
    cfg = _cfg(tmp_path)

    first = run_cycle(database, paths, cfg, loop_id="a", instance_id="i-a")
    assert first["outcome"] == PASSED
    assert first["step_id"] == "step-a"
    assert (repository / "step_a.md").read_text(encoding="utf-8") == "STEP-A\n"
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert "step_b.md" in str(mission["next_action"])

    second = run_cycle(database, paths, cfg, loop_id="b", instance_id="i-b")
    # First builder attempt plants DEFECT → FAIL → repair → PASS → COMPLETE
    # because step-a and step-b are the whole recorded plan.
    assert second["outcome"] == PASSED
    assert second["step_id"] == "step-b"
    assert (repository / "step_b.md").read_text(encoding="utf-8") == "STEP-B\n"
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "COMPLETE"


def test_contract_plan_completes_without_owner_transition(tmp_path, monkeypatch):
    database, repository = mission_database(
        tmp_path, next_action="create step_a.md containing STEP-A")
    set_capability_state(
        database, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "prompt1-m2"})
    record_step_contract(database, _contract("step-a", "step_a.md", "STEP-A"))
    adapter = InspectingHermes(WORKER_HERMES)
    import jarvis_foundry.runtime.worker_loop as worker_loop
    monkeypatch.setattr(
        worker_loop, "build_worker_stack",
        lambda *args, **kwargs: _stack(database, adapter, tmp_path / "EMERGENCY_STOP"),
    )
    report = run_cycle(
        database, FoundryPaths(tmp_path), _cfg(tmp_path),
        loop_id="only", instance_id="i-only")
    assert report["outcome"] == PASSED
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "COMPLETE"
    waiting = run_cycle(
        database, FoundryPaths(tmp_path), _cfg(tmp_path),
        loop_id="done", instance_id="i-done")
    assert waiting["outcome"] == WAITING_OUTCOME
    assert (repository / "step_a.md").is_file()


def test_fifteen_point_isolated_kernel_and_fresh_process(tmp_path, monkeypatch):
    database, repository = mission_database(
        tmp_path, next_action="create step_a.md containing STEP-A")
    set_capability_state(
        database, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "prompt1-m2"})
    for step_id, (artifact, token) in EXPECTED.items():
        record_step_contract(database, _contract(step_id, artifact, token.strip()))
    adapter = InspectingHermes(WORKER_HERMES)
    import jarvis_foundry.runtime.worker_loop as worker_loop
    monkeypatch.setattr(
        worker_loop, "build_worker_stack",
        lambda *args, **kwargs: _stack(database, adapter, tmp_path / "EMERGENCY_STOP"),
    )
    paths = FoundryPaths(tmp_path)
    cfg = _cfg(tmp_path)
    reports = []
    for index in range(6):
        reports.append(run_cycle(
            database, paths, cfg, loop_id=f"c{index}", instance_id=f"i{index}"))
        mission = database.row("missions", "mission_id", "mission-1")
        if mission["state"] == "COMPLETE":
            break
    outcomes = [item["outcome"] for item in reports]
    assert PASSED in outcomes
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "COMPLETE"
    for artifact, token in EXPECTED.values():
        assert (repository / artifact).read_text(encoding="utf-8") == token
    types = [event.event_type for event in database.events()]
    assert "verification.passed" in types
    assert "verification.failed" in types
    assert "evidence.created" in types
    assert "mission.completed" in types
    failed = [
        event for event in database.events()
        if event.event_type == "verification.failed"
    ]
    assert failed
    assert "rebuild the step artifact and re-verify" in (
        failed[0].payload.get("repair_requirements") or [])
    # Fresh process reconstructs durable truth with no chat context.
    script = (
        "import json,sys; from jarvis_foundry.database import FoundryDatabase; "
        f"db=FoundryDatabase(r'{database.path}'); "
        "row=db.row('missions','mission_id','mission-1'); "
        "print(json.dumps({'state': row['state'], "
        "'events': [e.event_type for e in db.events()]}))"
    )
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    fresh = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, check=True, env=env,
    )
    reconstructed = json.loads(fresh.stdout)
    assert reconstructed["state"] == "COMPLETE"
    assert "mission.completed" in reconstructed["events"]
