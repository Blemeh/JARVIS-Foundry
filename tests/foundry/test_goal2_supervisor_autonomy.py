"""Goal 2: Supervisor-owned worker-loop lifecycle and P1 correctness."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from jarvis_foundry.cli.main import main
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.loop import (
    BLOCKED_OUTCOME,
    FAILED,
    MissionLoop,
    PASSED,
    WAITING_OUTCOME,
)
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.results import COMPLETED, RoleResult
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.supervisor.service import WORKER_LOOP_RUN_ID, Supervisor
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.router import READY, WorkerRouter

from test_mission_loop import (
    ScriptedAdapter,
    commit_envelope,
    mission_fixture,
    step_kwargs,
)
from test_worker_orchestrator import CrashAfterWriteAdapter, MutatingScriptedAdapter


def test_heartbeat_stays_nonblocking_when_spawning_worker_loop(tmp_path):
    config = tmp_path / "workers.yaml"
    config.write_text(
        "enabled: true\nstatus: ACTIVE\nprovider: opencode_go\n"
        "model: deepseek-v4-flash\n",
        encoding="utf-8")
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(
        database, tmp_path / "STOP", instance_id="hb-spawn",
        spawn_worker_loop=True, worker_loop_config=config,
    )
    original = supervisor.workers.spawn

    def sleep_child(run_id, command, **kwargs):
        return original(
            run_id,
            [sys.executable, "-c", "import time; time.sleep(30)"],
            **kwargs,
        )

    supervisor.workers.spawn = sleep_child  # type: ignore[method-assign]
    assert supervisor.start() == "RUNNING"
    started = time.monotonic()
    assert supervisor.heartbeat() == "RUNNING"
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert supervisor.workers.status(WORKER_LOOP_RUN_ID).alive
    supervisor.stop()
    assert not supervisor.workers.status(WORKER_LOOP_RUN_ID).alive


def test_mission_loop_refuses_new_work_when_stop_active(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    stop = tmp_path / "STOP"
    EmergencyStop(stop).activate("goal2 stop")
    loop = MissionLoop(
        database, router, FakeHermesAdapter(working_root=repository),
        policy=policy, stop_path=stop,
    )
    outcome = loop.run_step(**step_kwargs())
    assert outcome.outcome == BLOCKED_OUTCOME
    settled = loop.run_until_settled(**step_kwargs())
    assert settled.outcome == BLOCKED_OUTCOME


def test_stop_race_while_accepting_returns_blocked_without_child_crash(
        tmp_path, monkeypatch):
    database, repository, policy, router = mission_fixture(tmp_path)
    for state in ("READY", "EXECUTING", "VERIFYING", "ACCEPTING"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": "mission-1", "state": state,
             "next_action": "deliver feature X"},
            source_type="test", source_id="stop-accepting-race",
            idempotency_key=f"stop-accepting-race:{state}",
        ))
    loop = MissionLoop(
        database, router, FakeHermesAdapter(working_root=repository),
        policy=policy, stop_path=tmp_path / "STOP",
    )
    checks = iter((None, None, "race stop", "race stop"))
    monkeypatch.setattr(loop, "_stop_reason", lambda: next(checks, "race stop"))
    outcome = loop.run_until_settled(**step_kwargs())
    assert outcome.outcome == BLOCKED_OUTCOME
    assert "race stop" in str(outcome.reason)
    assert database.row("missions", "mission_id", "mission-1")["state"] == "ACTION_SELECTION"


def test_status_doctor_events_are_sqlite_readonly(tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    assert main(["init", "--home", str(home)]) == 0
    capsys.readouterr()
    writable: list[str] = []
    original = FoundryDatabase.connect

    def wrapped(self, *, readonly: bool = False):
        if not readonly:
            writable.append("write")
        return original(self, readonly=readonly)

    monkeypatch.setattr(FoundryDatabase, "connect", wrapped)
    assert main(["status", "--home", str(home)]) == 0
    assert main(["doctor", "--home", str(home)]) == 0
    assert main(["events", "--home", str(home)]) == 0
    assert writable == []


def test_foundry_database_init_does_not_create_directories(tmp_path):
    nested = tmp_path / "missing" / "foundry.db"
    FoundryDatabase(nested)
    assert not (tmp_path / "missing").exists()


def test_not_tested_does_not_consume_repair_budget(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)

    class NotTestedVerifier(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            if manifest.role == "verifier":
                heartbeat()
                return RoleResult(
                    run_id=manifest.run_id, role=manifest.role,
                    status=COMPLETED, provider=manifest.provider,
                    model=manifest.model, verdict="NOT_TESTED",
                    evidence=("cannot determine",),
                )
            return super().dispatch(manifest, heartbeat=heartbeat)

    loop = MissionLoop(
        database, router, NotTestedVerifier(working_root=repository),
        policy=policy, max_repairs=2,
    )
    outcome = loop.run_until_settled(**step_kwargs())
    assert outcome.outcome == BLOCKED_OUTCOME
    verdict = database.row("verification_verdicts", "verdict_id", outcome.verdict_id)
    assert verdict["status"] == "NOT_TESTED"
    assert verdict["verdict"] == "NOT_TESTED"
    with database.connect() as connection:
        fails = connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts WHERE status='FAIL'"
        ).fetchone()[0]
    assert fails == 0
    assert database.row("missions", "mission_id", "mission-1")["state"] == "BLOCKED"


def test_empty_verdict_fails_closed_never_pass(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)

    class EmptyVerdict(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            if manifest.role == "verifier":
                heartbeat()
                return RoleResult(
                    run_id=manifest.run_id, role=manifest.role,
                    status=COMPLETED, provider=manifest.provider,
                    model=manifest.model, verdict=None,
                )
            return super().dispatch(manifest, heartbeat=heartbeat)

    outcome = MissionLoop(
        database, router, EmptyVerdict(working_root=repository),
        policy=policy,
    ).run_step(**step_kwargs())
    assert outcome.outcome == BLOCKED_OUTCOME
    verdict = database.row("verification_verdicts", "verdict_id", outcome.verdict_id)
    assert verdict["status"] != "PASS"


def test_model_pass_without_artifact_is_fail_then_repair(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository)
    loop = MissionLoop(database, router, adapter, policy=policy, max_repairs=2)
    outcome = loop.run_until_settled(
        **step_kwargs(expected_artifacts=("result.txt",)),
        commit_envelope=commit_envelope(repository),
    )
    assert outcome.outcome == PASSED
    assert (repository / "result.txt").exists()
    assert adapter.attempts["repair"] == 1
    waiting = loop.run_until_settled(**step_kwargs())
    assert waiting.outcome == WAITING_OUTCOME


def test_false_success_claim_is_rejected_then_repaired(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    worker_router = WorkerRouter()
    worker_router.mark_ready(
        WORKER_HERMES, state=READY, note="goal2", evidence=("test",))
    liar = ScriptedWorkerAdapter(
        WORKER_HERMES,
        outcome=ScriptedOutcome(
            files_changed=("result.txt",),
            claimed_outcome="PASS",
            summary="claimed result.txt without writing it",
        ),
    )
    fixer = MutatingScriptedAdapter(
        WORKER_HERMES,
        write_files=("result.txt",),
        outcome=ScriptedOutcome(files_changed=("result.txt",)),
    )

    class RepairThenHonest(ScriptedWorkerAdapter):
        def dispatch(self, contract, *, heartbeat):
            if contract.role == "repair":
                return fixer.dispatch(contract, heartbeat=heartbeat)
            if contract.role == "verifier":
                present = (contract.working_root / "result.txt").is_file()
                self.outcome = ScriptedOutcome(
                    verdict="PASS" if present else "FAIL",
                    files_changed=(),
                    claimed_outcome="inspected",
                )
                return super().dispatch(contract, heartbeat=heartbeat)
            return liar.dispatch(contract, heartbeat=heartbeat)

    adapter = RepairThenHonest(WORKER_HERMES)
    registry = WorkerRegistry({WORKER_HERMES: adapter})
    bridge = HybridWorkerRoleAdapter(
        database=database, router=worker_router, registry=registry)
    loop = MissionLoop(database, router=router, adapter=bridge, max_repairs=2)
    outcome = loop.run_until_settled(
        **step_kwargs(expected_artifacts=("result.txt",), target=repository),
        commit_envelope=commit_envelope(repository),
    )
    assert outcome.outcome == PASSED
    assert (repository / "result.txt").exists()
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"


def test_worker_crash_after_partial_write_repairs(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    worker_router = WorkerRouter()
    worker_router.mark_ready(
        WORKER_HERMES, state=READY, note="goal2", evidence=("test",))
    crashed = CrashAfterWriteAdapter(WORKER_HERMES, write_files=("result.txt",))
    repaired = MutatingScriptedAdapter(
        WORKER_HERMES,
        write_files=("result.txt",),
        content="recovered\n",
        outcome=ScriptedOutcome(files_changed=("result.txt",)),
    )

    class CrashThenRepair(ScriptedWorkerAdapter):
        def dispatch(self, contract, *, heartbeat):
            if contract.role == "builder" and contract.attempt == 1:
                return crashed.dispatch(contract, heartbeat=heartbeat)
            if contract.role == "verifier":
                present = (contract.working_root / "result.txt").is_file()
                self.outcome = ScriptedOutcome(
                    verdict="PASS" if present else "FAIL", files_changed=())
                return super().dispatch(contract, heartbeat=heartbeat)
            return repaired.dispatch(contract, heartbeat=heartbeat)

    registry = WorkerRegistry({WORKER_HERMES: CrashThenRepair(WORKER_HERMES)})
    bridge = HybridWorkerRoleAdapter(
        database=database, router=worker_router, registry=registry)
    loop = MissionLoop(database, router=router, adapter=bridge, max_repairs=2)
    first = loop.run_step(**step_kwargs(expected_artifacts=("result.txt",),
                                        target=repository))
    assert first.outcome == FAILED
    assert (repository / "result.txt").exists()
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "REPAIRING"

    restarted = FoundryDatabase(database.path)
    resume = MissionLoop(
        restarted, router=router, adapter=bridge, max_repairs=2)
    outcome = resume.run_until_settled(
        **step_kwargs(expected_artifacts=("result.txt",), target=repository),
        commit_envelope=commit_envelope(repository),
    )
    assert outcome.outcome == PASSED
    assert (repository / "result.txt").read_text(encoding="utf-8") == "recovered\n"


def test_bounded_autonomous_sequence_pass_fail_repair_waiting(tmp_path):
    """Orient → step → auto worker → evidence → verify PASS → next action
    → deliberate FAIL → automatic repair → fresh PASS → WAITING_FOR_EVENT."""
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository)
    loop = MissionLoop(database, router, adapter, policy=policy, max_repairs=2)

    first = loop.run_until_settled(
        **step_kwargs(expected_artifacts=("result.txt",)),
        commit_envelope=commit_envelope(repository))
    assert first.outcome == PASSED
    assert adapter.attempts["repair"] == 1
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"

    with database.transaction() as connection:
        connection.execute(
            "UPDATE missions SET next_action = ? WHERE mission_id = 'mission-1'",
            ("deliver the second feature",))
    adapter.attempts["builder"] = 0
    adapter.attempts["repair"] = 0
    adapter.attempts["verifier"] = 0
    adapter.artifact_name = "second.txt"
    (repository / "second.txt").unlink(missing_ok=True)
    second = loop.run_until_settled(
        **step_kwargs(step_id="step-2", expected_artifacts=("second.txt",),
                      target=Path("second.txt")))
    assert second.outcome == PASSED
    assert second.step_id == "step-2"
    assert (repository / "second.txt").exists()

    waiting = loop.run_until_settled(**step_kwargs(step_id="step-3"))
    assert waiting.outcome == WAITING_OUTCOME
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["next_action"] is None
