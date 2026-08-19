"""Goal 3: P2/P3 structural closure regressions."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest
import tomllib

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.installer import (
    WORKER_LOOP_TASK_NAME,
    Installer,
    InstallerError,
    WorkerLoopInstaller,
)
from jarvis_foundry.mission.lineage import origin_mission_id
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.authority import (
    CONFIG_PRECEDENCE,
    EXECUTION_CLASSIFICATION,
    FAST_TEST_COMMAND,
    FAST_TEST_PATHS,
    FOUNDRY_YAML_ROLE,
    FULL_TEST_COMMAND,
    FULL_TEST_PATHS,
    PRODUCTION_EXECUTOR,
    PRODUCTION_EXECUTOR_COUNT,
    foundry_yaml_is_live,
    require_existing_executable,
    resolve_runtime_authority,
)
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime._stack import build_worker_stack
from jarvis_foundry.roles.reaper import recover_stale_mission_state
from jarvis_foundry.supervisor.service import Supervisor
from jarvis_foundry.workers.adapters.base import AdapterConfigurationError
from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
from jarvis_foundry.workers.orchestrator import HybridStepController
from jarvis_foundry.mission.loop import MissionLoop
from jarvis_foundry.runtime import worker_loop as worker_loop_mod
from jarvis_foundry.roles.adapter import HermesOneshotAdapter


REPO = Path(__file__).resolve().parents[2]


def test_foundry_yaml_is_explicit_template():
    text = (REPO / "config" / "foundry.yaml").read_text(encoding="utf-8")
    assert "TEMPLATE" in text
    assert FOUNDRY_YAML_ROLE == "TEMPLATE"
    assert foundry_yaml_is_live() is False
    src = REPO / "src" / "jarvis_foundry"
    loaders = []
    for path in src.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        if "foundry.yaml" in body and path.name != "authority.py":
            loaders.append(path)
    assert loaders == []


def test_config_precedence_ignores_foundry_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("JARVIS_SPAWN_WORKER_LOOP", raising=False)
    monkeypatch.delenv("JARVIS_HEARTBEAT_SECONDS", raising=False)
    authority = resolve_runtime_authority(home=tmp_path / "foundry-home")
    assert authority.foundry_yaml_role == "TEMPLATE"
    assert authority.precedence == CONFIG_PRECEDENCE
    assert authority.heartbeat_seconds == 5.0  # not yaml's 60
    assert authority.spawn_worker_loop is False
    assert authority.stop_path == authority.home / "EMERGENCY_STOP"
    yaml_text = (REPO / "config" / "foundry.yaml").read_text(encoding="utf-8")
    assert "heartbeat_seconds: 60" in yaml_text


def test_fast_is_not_full_and_full_includes_convergence():
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_paths = tuple(data["tool"]["pytest"]["ini_options"]["testpaths"])
    jarvis = data["tool"]["jarvis"]["tests"]
    assert pytest_paths == FAST_TEST_PATHS == ("tests/foundry",)
    assert tuple(jarvis["fast"]) == FAST_TEST_PATHS
    assert tuple(jarvis["full"]) == FULL_TEST_PATHS
    assert "tests/convergence" in FULL_TEST_PATHS
    assert "tests/convergence" not in FAST_TEST_PATHS
    assert FAST_TEST_COMMAND != FULL_TEST_COMMAND
    assert "convergence" in FULL_TEST_COMMAND
    assert "full" not in FAST_TEST_COMMAND.lower()


def test_doctor_counts_without_loading_ledger(tmp_path, monkeypatch):
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(database, tmp_path / "STOP", instance_id="count")
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"
    loaded = []
    original = database.events

    def boom(*, readonly=False):
        loaded.append(True)
        return original(readonly=readonly)

    monkeypatch.setattr(database, "events", boom)
    report = supervisor.doctor()
    assert loaded == []
    assert report["event_count"] == database.event_count()
    assert report["heartbeat_count"] == database.event_count(
        event_type="supervisor.heartbeat")
    assert report["heartbeat_count"] == 1


def test_nonmaterial_heartbeats_coalesce(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(
        database, tmp_path / "STOP", instance_id="coalesce",
        ledger_heartbeat_seconds=60,
    )
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"
    assert database.event_count(event_type="supervisor.heartbeat") == 1


def test_heartbeat_preserves_trust_and_never_promotes(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(database, tmp_path / "STOP", instance_id="trust")
    assert supervisor.start() == "RUNNING"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE supervisor_state SET trust_state='DEGRADED' WHERE singleton=1")
    assert supervisor.heartbeat() == "RUNNING"
    with database.connect() as connection:
        row = dict(connection.execute(
            "SELECT trust_state FROM supervisor_state WHERE singleton=1"
        ).fetchone())
    assert row["trust_state"] == "DEGRADED"
    events = [e for e in database.events() if e.event_type == "supervisor.heartbeat"]
    assert events[-1].payload["trust_state"] == "DEGRADED"
    assert events[-1].payload["trust_state"] != "TRUSTED_AUTONOMOUS"


def test_production_executor_count_is_one():
    assert PRODUCTION_EXECUTOR_COUNT == 1
    assert "MissionLoop" in PRODUCTION_EXECUTOR
    assert EXECUTION_CLASSIFICATION["MissionLoop"] == "PRODUCTION"
    assert EXECUTION_CLASSIFICATION["runtime.worker_loop"] == "PRODUCTION"
    assert EXECUTION_CLASSIFICATION["HybridStepController"] == "TEST_ONLY"
    assert EXECUTION_CLASSIFICATION["HermesOneshotAdapter"] == "LEGACY_DISABLED"
    assert EXECUTION_CLASSIFICATION["JARVIS-Foundry-Worker-Loop"] == "LEGACY_DISABLED"
    assert "run_until_settled" in inspect.getsource(worker_loop_mod.run_cycle)
    assert "HybridStepController" not in inspect.getsource(worker_loop_mod)
    assert HybridStepController.__module__.endswith("orchestrator")
    assert MissionLoop.__module__.endswith("loop")
    assert HermesOneshotAdapter.__name__ == "HermesOneshotAdapter"


def test_legacy_worker_loop_task_cannot_be_enabled(tmp_path, monkeypatch):
    import jarvis_foundry.installer as installer_module

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    config = tmp_path / "worker.yaml"
    config.write_text(
        "enabled: true\nprovider: opencode_go\nmodel: deepseek-v4-flash\n",
        encoding="utf-8")
    worker = WorkerLoopInstaller(
        database,
        foundry_home=tmp_path / "home",
        hermes_home=tmp_path / "hermes",
        python_exe=sys.executable,
        worker_config_path=config,
    )
    assert worker.task_name == WORKER_LOOP_TASK_NAME
    monkeypatch.setattr(
        installer_module, "_run_schtasks",
        lambda *args: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    with pytest.raises(InstallerError, match="no production authority"):
        worker.set_enabled(True)


def test_mission_lineage_survives_restart(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": "mission-origin",
            "objective": "keep identity",
            "resource_id": "resource-1",
            "state": "EXECUTING",
            "acceptance_criteria": ["done"],
            "next_action": "step-1",
        },
        source_type="supervisor", source_id="test",
        idempotency_key="mission.created:mission-origin",
    ))
    child_id = "mission-child"
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": child_id,
            "objective": "derived",
            "resource_id": "resource-1",
            "state": "READY",
            "acceptance_criteria": ["done"],
            "created_from": "mission-origin",
        },
        source_type="supervisor", source_id="test",
        idempotency_key=f"mission.created:{child_id}",
    ))
    recovered = recover_stale_mission_state(database)
    assert "mission-origin" in recovered
    row = database.row("missions", "mission_id", "mission-origin")
    assert row is not None
    assert row["state"] == "ACTION_SELECTION"
    assert origin_mission_id(database, child_id) == "mission-origin"
    assert origin_mission_id(database, "mission-origin") == "mission-origin"
    assert database.row("missions", "mission_id", "mission-duplicate") is None
    recovered_again = recover_stale_mission_state(database)
    assert recovered_again == []


def test_missing_hermes_python_fails_closed(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    cfg = WorkerRuntimeConfig(
        hermes={"enabled": True, "root": str(tmp_path),
                "python_exe": str(tmp_path / "missing-python.exe")},
        runtime_state_root=str(tmp_path / "state"),
    )
    with pytest.raises(AdapterConfigurationError, match="missing"):
        build_worker_stack(
            database, FoundryPaths(tmp_path), cfg, run_id="x")


def test_require_existing_executable_uses_alternate_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "alt-hermes"))
    live = HermesWorkerAdapter.LIVE_HERMES_HOME
    assert live == (tmp_path / "alt-hermes").resolve()
    with pytest.raises(AdapterConfigurationError, match="not configured"):
        require_existing_executable("", kind="hermes python")
    with pytest.raises(AdapterConfigurationError, match="missing"):
        require_existing_executable(tmp_path / "nope.exe", kind="hermes python")
    assert require_existing_executable(sys.executable, kind="python") == Path(
        sys.executable).resolve()


def test_installer_records_source_identity_and_working_directory(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    installer = Installer(
        database,
        task_name=f"JF-Goal3-{tmp_path.name[-8:]}",
        foundry_home=tmp_path / "home",
        hermes_home=tmp_path / "hermes",
        python_exe=sys.executable,
        gate_verdict_path=tmp_path / "GATE.json",
    )
    xml = installer._task_xml()
    identity = installer.source_identity()
    assert "<WorkingDirectory>" in xml
    assert str(tmp_path / "home") in xml
    assert identity["source_sha"]
    assert identity["code_origin"]
    (tmp_path / "home").mkdir()
    installer._write_profile()
    profile = (tmp_path / "home" / "foundry-profile.yaml").read_text(encoding="utf-8")
    assert "source_sha:" in profile
    assert "code_origin:" in profile
