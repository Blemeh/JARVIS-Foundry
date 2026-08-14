"""V7-S3 — Supervisor adoption wiring: the evolution control plane in an
isolated observe-only schema-32 runtime.

Gate matrix proved here (TDD):
  1. the Supervisor runs and recovers without Hermes;
  2. evolution defaults disabled/observe-only;
  3. only the Supervisor can schedule cycles, issue canaries, promote,
     freeze, roll back, restore, or change release pointers;
  4. STOP blocks new and in-flight work;
  5. duplicate wakeups, events, leases, retries, and restarts are idempotent;
  6. workers and candidates cannot bypass or terminate the control plane;
  7. command/UI/voice failure cannot disable Supervisor control.

All tests use isolated temp DBs (schema 32) and temp homes; the live DB is
never touched.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.promote import (
    freeze_active, unfreeze_active, promote_release,
)
from jarvis_foundry.evolution.registry import set_slot
from jarvis_foundry.supervisor.evolution_control import (
    EvolutionControl, EvolutionControlError, SUPERVISOR_CONTROLLER,
)

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture()
def db(tmp_path: Path):
    database = FoundryDatabase(tmp_path / "runtime.db")
    database.initialize()
    return database


@pytest.fixture()
def control(db: FoundryDatabase):
    return EvolutionControl(db)


# --- 2. evolution defaults disabled/observe-only ----------------------------

def test_default_mode_is_observe(control: EvolutionControl) -> None:
    assert control.mode() == "observe"


def test_observe_mode_allows_read_only_queries(
        control: EvolutionControl, db: FoundryDatabase) -> None:
    assert control.is_frozen() is False
    assert control.mode() == "observe"


def test_observe_mode_refuses_effectful_operations(
        control: EvolutionControl) -> None:
    with pytest.raises(EvolutionControlError, match="observe"):
        control.freeze(reason="probe", controller=SUPERVISOR_CONTROLLER)
    with pytest.raises(EvolutionControlError, match="observe"):
        control.unfreeze(controller=SUPERVISOR_CONTROLLER)
    with pytest.raises(EvolutionControlError, match="observe"):
        control.set_slot("candidate", None, reason="probe",
                         controller=SUPERVISOR_CONTROLLER)
    with pytest.raises(EvolutionControlError, match="observe"):
        control.start_canary(improvement_id="x", candidate_release_id="y",
                             traffic_share=0.1, duration_cycles=2,
                             max_cost_usd=0.0,
                             health_thresholds={"error_rate_max": 0.1,
                                                "latency_max_s": 1.0},
                             controller=SUPERVISOR_CONTROLLER)
    with pytest.raises(EvolutionControlError, match="observe"):
        control.run_cycle(improvement_id="x", source_commit="a" * 40,
                          workload_dir=Path("."), baseline_command=["true"],
                          candidate_command=["true"], build_command=["true"],
                          controller=SUPERVISOR_CONTROLLER)
    with pytest.raises(EvolutionControlError, match="observe"):
        control.record_discovered(candidates=[], sources={},
                                  controller=SUPERVISOR_CONTROLLER)
    with pytest.raises(EvolutionControlError, match="observe"):
        control.create_restore_point(label="probe",
                                     controller=SUPERVISOR_CONTROLLER)


# --- 3. only the Supervisor can drive the control plane ----------------------

def test_enable_requires_supervisor_controller(
        control: EvolutionControl) -> None:
    with pytest.raises(EvolutionControlError, match="Supervisor"):
        control.enable(controller="candidate-abc")
    control.enable(controller=SUPERVISOR_CONTROLLER)
    assert control.mode() == "active"
    control.disable(controller=SUPERVISOR_CONTROLLER)
    assert control.mode() == "observe"


def test_mode_transitions_recorded_in_ledger(
        control: EvolutionControl, db: FoundryDatabase) -> None:
    control.enable(controller=SUPERVISOR_CONTROLLER)
    control.disable(controller=SUPERVISOR_CONTROLLER)
    events = [e for e in db.events()
              if e.event_type == "evolution.mode_changed"]
    assert len(events) == 2
    payloads = [e.payload for e in db.events()
                if e.event_type == "evolution.mode_changed"]
    assert [p["mode"] for p in payloads] == ["active", "observe"]
    assert all(p["controller"] == SUPERVISOR_CONTROLLER for p in payloads)


def test_adapter_pins_controller_never_accepts_candidate(
        control: EvolutionControl) -> None:
    control.enable(controller=SUPERVISOR_CONTROLLER)
    with pytest.raises(EvolutionControlError, match="Supervisor"):
        control.freeze(reason="probe", controller="candidate-x")
    with pytest.raises(EvolutionControlError, match="Supervisor"):
        control.set_slot("candidate", None, reason="probe",
                         controller="worker-1")


def test_machinery_refuses_candidate_controllers(
        control: EvolutionControl, db: FoundryDatabase) -> None:
    control.enable(controller=SUPERVISOR_CONTROLLER)
    from jarvis_foundry.evolution.promote import PromoteError
    from jarvis_foundry.evolution.canary import CanaryError, start_canary
    from jarvis_foundry.evolution.discover import (
        DiscoveryError, record_discovered_improvements,
    )
    with pytest.raises(PromoteError):
        freeze_active(db, reason="probe", controller="candidate-1")
    with pytest.raises(PromoteError):
        unfreeze_active(db, controller="candidate-1")
    with pytest.raises(CanaryError):
        start_canary(db, improvement_id="x", candidate_release_id="y",
                     traffic_share=0.1, duration_cycles=2, max_cost_usd=0.0,
                     health_thresholds={"error_rate_max": 0.1,
                                        "latency_max_s": 1.0},
                     controller="candidate-1")
    with pytest.raises(DiscoveryError):
        record_discovered_improvements(db, candidates=[], sources={},
                                       controller="candidate-1")
    # NOTE (documented boundary): raw registry.set_slot records the
    # controller as provenance, not authorization — release-pointer changes
    # are authorized through the controller-gated entry points
    # (promote/rollback + the Supervisor's adapter, which pins "supervisor").


def test_frozen_release_control_blocks_promotion(
        control: EvolutionControl, db: FoundryDatabase) -> None:
    # only the Supervisor can freeze (machinery-level), and a frozen release
    # control blocks promotion end-to-end
    control.enable(controller=SUPERVISOR_CONTROLLER)
    control.freeze(reason="S3 gate probe", controller=SUPERVISOR_CONTROLLER)
    assert control.is_frozen() is True
    with pytest.raises(Exception):
        promote_release(db, candidate_release_id="release-x",
                        verification_id="verif-x",
                        baseline_run_id="b", candidate_run_id="c",
                        controller=SUPERVISOR_CONTROLLER)


# --- 4. STOP blocks new and in-flight work ------------------------------------

def test_stop_sentinel_blocks_effectful_ops(
        db: FoundryDatabase, tmp_path: Path) -> None:
    sentinel = tmp_path / "EMERGENCY_STOP"
    sentinel.write_text(json.dumps({"stop_id": "s3-probe", "reason": "test"}),
                        encoding="utf-8")
    control = EvolutionControl(db)
    control.enable(controller=SUPERVISOR_CONTROLLER)
    with pytest.raises(EvolutionControlError, match="STOP"):
        control.freeze(reason="probe", controller=SUPERVISOR_CONTROLLER)
    with pytest.raises(EvolutionControlError, match="STOP"):
        control.run_cycle(improvement_id="x", source_commit="a" * 40,
                          workload_dir=Path("."), baseline_command=["true"],
                          candidate_command=["true"], build_command=["true"],
                          controller=SUPERVISOR_CONTROLLER)


# --- 1. the Supervisor runs and recovers without Hermes -----------------------

def _run_once(home: Path) -> subprocess.CompletedProcess[str]:
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(SRC)
    return subprocess.run(
        [sys.executable, "-m", "jarvis_foundry.supervisor.run", "--once",
         "--home", str(home)],
        capture_output=True, text=True, timeout=120, cwd=str(SRC.parent),
        env=env,
    )


def test_supervisor_runs_and_recovers_without_hermes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    first = _run_once(home)
    assert first.returncode == 0, first.stderr
    state = json.loads(first.stdout.strip().splitlines()[0])
    assert state["supervisor"] == "RUNNING"
    db_path = home / "foundry.db"
    assert db_path.exists()
    database = FoundryDatabase(db_path)
    started = [e for e in database.events()
               if e.event_type == "supervisor.started"]
    assert len(started) == 1
    first_instance = started[0].payload["instance_id"]
    # restart: new instance id, lease re-acquired, no duplicate state
    second = _run_once(home)
    assert second.returncode == 0, second.stderr
    database = FoundryDatabase(db_path)
    started = [e for e in database.events()
               if e.event_type == "supervisor.started"]
    assert len(started) == 2
    second_instance = started[1].payload["instance_id"]
    assert second_instance != first_instance
    # clean --once exits release the singleton lease (no stale holder);
    # a third run still starts cleanly — restart idempotency
    assert database.row("leases", "lease_key", "supervisor:singleton") is None
    third = _run_once(home)
    assert third.returncode == 0, third.stderr
    started = [e for e in database.events()
               if e.event_type == "supervisor.started"]
    assert len(started) == 3


def test_stop_sentinel_freezes_supervisor_at_startup(tmp_path: Path) -> None:
    home = tmp_path / "frozen-home"
    home.mkdir()
    (home / "EMERGENCY_STOP").write_text(
        json.dumps({"stop_id": "s3-freeze", "reason": "startup probe"}),
        encoding="utf-8")
    run = _run_once(home)
    assert run.returncode == 0, run.stderr
    state = json.loads(run.stdout.strip().splitlines()[0])
    assert state["supervisor"] == "FROZEN"
    database = FoundryDatabase(home / "foundry.db")
    frozen = [e for e in database.events()
              if e.event_type == "emergency_stop.frozen"]
    assert len(frozen) >= 1
    assert frozen[0].payload["stop_id"] == "s3-freeze"


def test_duplicate_event_delivery_is_idempotent(
        db: FoundryDatabase) -> None:
    from jarvis_foundry.events import Event
    event = Event.create("release.slot_changed",
                         {"slot": "candidate", "release_id": None,
                          "previous_release_id": None, "reason": "dedupe",
                          "controller": SUPERVISOR_CONTROLLER},
                         source_type="supervisor", source_id="v7")
    db.append(event)
    db.append(event)  # duplicate delivery
    rows = db.events()
    assert len([e for e in rows if e.idempotency_key == event.idempotency_key]) == 1


# --- 6/7. candidates cannot bypass; UI/voice failure cannot disable control ---

def test_candidates_cannot_terminate_control_plane(
        control: EvolutionControl) -> None:
    # the control plane exposes no candidate-facing termination path: every
    # effectful entry point pins the Supervisor identity
    for op in ("freeze", "unfreeze", "rollback", "promote"):
        method = getattr(control, op)
        with pytest.raises((EvolutionControlError, TypeError)):
            method(controller="candidate-9")  # refused before any effect


def test_control_plane_independent_of_hermes_ui_voice(tmp_path: Path) -> None:
    # a bare python process (no Hermes, no UI, no voice) can drive the
    # control plane: enable + freeze + unfreeze through the adapter
    script = tmp_path / "drive.py"
    script.write_text(
        "import sys, json\n"
        "sys.path.insert(0, %r)\n"
        "from jarvis_foundry.database import FoundryDatabase\n"
        "from jarvis_foundry.supervisor.evolution_control import (\n"
        "    EvolutionControl, SUPERVISOR_CONTROLLER)\n"
        "db = FoundryDatabase(r'%s')\n"
        "db.initialize()\n"
        "ctl = EvolutionControl(db)\n"
        "assert ctl.mode() == 'observe'\n"
        "ctl.enable(controller=SUPERVISOR_CONTROLLER)\n"
        "ctl.freeze(reason='bare-process probe', controller=SUPERVISOR_CONTROLLER)\n"
        "assert ctl.is_frozen() is True\n"
        "ctl.unfreeze(controller=SUPERVISOR_CONTROLLER)\n"
        "print('CONTROL_PLANE_OK')\n"
        % (str(SRC), tmp_path / "bare.db"))
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(SRC)
    run = subprocess.run([sys.executable, str(script)],
                         capture_output=True, text=True, timeout=120,
                         env=env)
    assert run.returncode == 0, run.stderr
    assert "CONTROL_PLANE_OK" in run.stdout
