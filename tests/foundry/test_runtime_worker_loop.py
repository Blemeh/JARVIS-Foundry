"""CANONICAL RUNTIME PATH — live worker loop driver tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from jarvis_foundry import __version__
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.mission.loop import FAILED, MissionLoop
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.runtime._stack import (
    _prepare_hermes_home,
    _resolve_root,
    build_worker_stack,
)
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import (
    _adapter_runtime_environment,
    _end_waiting_episode,
    _waiting_report,
    run_cycle,
)
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import (
    WORKER_HERMES,
    WORKER_OPENHANDS,
    WorkerStepContract,
)
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.router import NOT_READY, READY, WorkerRouter

from test_worker_orchestrator import MutatingScriptedAdapter
from worker_test_helpers import mission_database


def test_config_fail_closed_when_missing():
    cfg = WorkerRuntimeConfig.from_yaml(None)
    assert cfg.enabled is False
    assert cfg.status == "INACTIVE"


def test_config_rejects_non_directive_model():
    cfg = WorkerRuntimeConfig(provider="glm-5.1", model="kimi-k2.7-code")
    with pytest.raises(ValueError, match="must route to"):
        cfg.validate()


def test_config_roundtrip(tmp_path):
    path = tmp_path / "workers.yaml"
    path.write_text(
        "enabled: true\nstatus: ACTIVE\nprovider: opencode_go\n"
        "model: deepseek-v4-flash\n"
        "verification:\n  max_repairs: 3\n"
        "role_signals:\n  builder:\n    bounded: true\n",
        encoding="utf-8")
    cfg = WorkerRuntimeConfig.from_yaml(path)
    assert cfg.enabled is True
    assert cfg.max_repairs == 3
    assert cfg.role_signals["builder"]["bounded"] is True


def test_campaign_runtime_config_is_explicit_and_observation_incompatible(tmp_path):
    path = tmp_path / "campaign-worker.yaml"
    path.write_text(
        "enabled: true\nstatus: ACTIVE\nprovider: opencode_go\n"
        "model: deepseek-v4-flash\n"
        "campaign_runtime:\n  enabled: true\n"
        "  resource_capacity:\n    shared-repository: 1\n",
        encoding="utf-8",
    )
    cfg = WorkerRuntimeConfig.from_yaml(path)
    assert cfg.campaign_enabled is True
    assert cfg.campaign_runtime["resource_capacity"] == {"shared-repository": 1}
    observation = tmp_path / "campaign-observation.yaml"
    observation.write_text(
        "enabled: true\nstatus: ACTIVE\nprovider: opencode_go\n"
        "model: deepseek-v4-flash\n"
        "campaign_runtime:\n  enabled: true\n"
        "continuous_observation:\n  enabled: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="campaign_runtime"):
        WorkerRuntimeConfig.from_yaml(observation)


def test_relative_runtime_root_moves_outside_live_hermes_profile(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("HERMES_HOME", str(local / "hermes"))
    home = local / "hermes" / "foundry"
    root = _resolve_root("var/worker-runtime", "containment", kind="run", home=home)
    assert root.is_relative_to(local / "JARVIS" / "runtime" / "worker-runtime")
    assert not root.is_relative_to(local / "hermes")


def test_runtime_root_safely_materializes_workforce_run_identity(tmp_path):
    first = _resolve_root(
        str(tmp_path / "runtime"),
        "workforce:package-a:a1",
        kind="run",
        home=tmp_path,
    )
    second = _resolve_root(
        str(tmp_path / "runtime"),
        "workforce/package-a/a1",
        kind="run",
        home=tmp_path,
    )

    assert first.is_dir()
    assert ":" not in first.name
    assert "/" not in first.name
    assert first != second


def test_contributor_acknowledgement_is_materialized_in_isolated_home(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    cfg = WorkerRuntimeConfig(
        enabled=True,
        hermes={
            "enabled": True,
            "root": str(tmp_path),
            "python_exe": sys.executable,
            "allow_data_training_tiers_noninteractive": True,
        },
        runtime_state_root=str(tmp_path / "state"),
    )
    stack = build_worker_stack(
        database, FoundryPaths(tmp_path / "foundry"), cfg, run_id="consent")
    config = stack.disposable_root / "hermes-home" / "config.yaml"
    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert document["security"] == {
        "allow_data_training_tiers_noninteractive": True,
    }
    entry = document["providers"]["opencode-go"]
    assert entry["api"] == cfg.provider_settings.base_url
    headers = entry["extra_headers"]
    assert UUID(str(headers["x-opencode-session"]))
    assert headers["User-Agent"] == f"jarvis-foundry/{__version__}"


def test_provider_session_header_is_stable_within_one_run_home(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    cfg = WorkerRuntimeConfig(
        enabled=True,
        hermes={
            "enabled": True,
            "root": str(tmp_path),
            "python_exe": sys.executable,
            "allow_data_training_tiers_noninteractive": True,
        },
        runtime_state_root=str(tmp_path / "state"),
    )
    first = build_worker_stack(
        database, FoundryPaths(tmp_path / "foundry"), cfg, run_id="stable")
    first_path = first.disposable_root / "hermes-home" / "config.yaml"
    first_text = first_path.read_text(encoding="utf-8")
    second = build_worker_stack(
        database, FoundryPaths(tmp_path / "foundry"), cfg, run_id="stable")
    second_path = second.disposable_root / "hermes-home" / "config.yaml"
    assert second_path.read_text(encoding="utf-8") == first_text
    session_id = yaml.safe_load(first_text)["providers"]["opencode-go"][
        "extra_headers"]["x-opencode-session"]
    assert session_id in second_path.read_text(encoding="utf-8")


def test_consent_only_home_keeps_the_exact_legacy_document(tmp_path):
    _prepare_hermes_home(
        tmp_path, {"allow_data_training_tiers_noninteractive": True})
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == (
        "security:\n"
        "  allow_data_training_tiers_noninteractive: true\n"
    )


def test_run_cycle_disabled(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    report = run_cycle(
        database, FoundryPaths(tmp_path), WorkerRuntimeConfig(),
        loop_id="l1", instance_id="i1")
    assert report["outcome"] == "DISABLED"


def test_idle_waiting_is_one_episode_not_one_event_per_poll(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    for index in range(500):
        _waiting_report(
            database, loop_id=f"loop-{index}", instance_id=f"instance-{index}",
            reason="no dispatchable mission",
        )
    waiting = [
        event for event in database.events()
        if event.event_type.startswith("worker_loop.waiting")
    ]
    assert [event.event_type for event in waiting] == [
        "worker_loop.waiting_started"
    ]


def test_idle_reason_transition_and_restart_are_auditable(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    _waiting_report(database, loop_id="a", instance_id="i", reason="reason-a")
    _waiting_report(database, loop_id="b", instance_id="i", reason="reason-b")
    _waiting_report(database, loop_id="c", instance_id="i", reason="reason-a")
    _end_waiting_episode(database, loop_id="d", instance_id="i")
    events = [event.event_type for event in database.events()
              if event.event_type.startswith("worker_loop.waiting")]
    assert events == [
        "worker_loop.waiting_started",
        "worker_loop.waiting_ended",
        "worker_loop.waiting_started",
        "worker_loop.waiting_ended",
        "worker_loop.waiting_started",
        "worker_loop.waiting_ended",
    ]
    starts = [event for event in database.events()
              if event.event_type == "worker_loop.waiting_started"]
    assert starts[0].payload["episode_id"] != starts[-1].payload["episode_id"]


def test_adapter_runtime_environment_is_scoped_and_restored(tmp_path):
    paths = FoundryPaths(tmp_path)
    cfg = WorkerRuntimeConfig(enabled=True)
    old_pid_table = os.environ.get("JARVIS_ADAPTER_PID_TABLE")
    old_stop_path = os.environ.get("JARVIS_STOP_PATH")
    os.environ["JARVIS_ADAPTER_PID_TABLE"] = "outer-pids.json"
    os.environ["JARVIS_STOP_PATH"] = "outer-stop"
    try:
        with _adapter_runtime_environment(paths, cfg):
            assert os.environ["JARVIS_ADAPTER_PID_TABLE"] == str(
                paths.home / "adapter_worker_pids.json")
            assert os.environ["JARVIS_STOP_PATH"] == str(paths.emergency_stop)
        assert os.environ["JARVIS_ADAPTER_PID_TABLE"] == "outer-pids.json"
        assert os.environ["JARVIS_STOP_PATH"] == "outer-stop"
    finally:
        if old_pid_table is None:
            os.environ.pop("JARVIS_ADAPTER_PID_TABLE", None)
        else:
            os.environ["JARVIS_ADAPTER_PID_TABLE"] = old_pid_table
        if old_stop_path is None:
            os.environ.pop("JARVIS_STOP_PATH", None)
        else:
            os.environ["JARVIS_STOP_PATH"] = old_stop_path


def test_run_cycle_frozen_when_stop_sentinel(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    stop = tmp_path / "EMERGENCY_STOP"
    stop.write_text("{}", encoding="utf-8")
    cfg = WorkerRuntimeConfig(enabled=True, stop_sentinel_path=str(stop))
    report = run_cycle(
        database, FoundryPaths(tmp_path), cfg,
        loop_id="l1", instance_id="i1")
    assert report["outcome"] == "FROZEN"
    assert any(event.event_type == "worker_loop.frozen"
               for event in database.events())


def test_durable_campaign_keeps_worker_guard_after_config_opt_in_is_removed(tmp_path):
    """A restart/config flip cannot route campaign work through V11 admission."""

    from jarvis_foundry.executive.autonomy import create_objective
    from jarvis_foundry.executive.campaigns import create_campaign, declare_child_mission

    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    create_objective(
        database,
        "campaign guard objective",
        objective_id="objective-campaign-guard",
        success_criteria=("child acceptance",),
    )
    create_campaign(
        database,
        "objective-campaign-guard",
        campaign_id="campaign-guard",
    )
    declare_child_mission(database, "campaign-guard", child_id="pending-child")

    # campaign_runtime_enabled intentionally remains False: the durable
    # campaign.created receipt makes recovery/dispatch policy sticky.
    report = run_cycle(
        database,
        FoundryPaths(tmp_path),
        WorkerRuntimeConfig(enabled=True),
        loop_id="campaign-guard-cycle",
        instance_id="campaign-guard-instance",
    )
    assert report["outcome"] == "WAITING"
    assert report["campaign_resume"]["status"] == "READY"


def test_production_yaml_stop_path_matches_cli_sentinel(tmp_path):
    """P0: jarvis stop writes FoundryPaths.emergency_stop. worker_loop and
    the adapter heartbeat runner must watch that same file. Production yaml
    historically nested foundry/EMERGENCY_STOP under a home already named
    foundry; tests that inject an absolute sentinel hide the split."""
    from jarvis_foundry.runtime.worker_loop import _default_stop_path

    repo = Path(__file__).resolve().parents[2]
    cfg = WorkerRuntimeConfig.from_yaml(repo / "config" / "worker_routing.yaml")
    paths = FoundryPaths(tmp_path / "foundry")
    watched = _default_stop_path(paths, cfg).resolve()
    assert watched == paths.emergency_stop.resolve(), (
        f"worker_loop watches {watched} but jarvis stop writes "
        f"{paths.emergency_stop.resolve()}"
    )
    assert watched == (tmp_path / "foundry" / "EMERGENCY_STOP").resolve()
    nested = tmp_path / "foundry" / "foundry" / "EMERGENCY_STOP"
    assert watched != nested.resolve()


def test_legacy_nested_foundry_stop_path_matches_cli_sentinel(tmp_path):
    """yaml used to say foundry/EMERGENCY_STOP while home is already foundry."""
    home = tmp_path / "foundry"
    home.mkdir()
    cfg = WorkerRuntimeConfig(
        enabled=True, stop_sentinel_path="foundry/EMERGENCY_STOP")
    assert cfg.resolve_stop_path(home) == (home / "EMERGENCY_STOP").resolve()
    database = FoundryDatabase(home / "db.sqlite")
    database.initialize()
    (home / "EMERGENCY_STOP").write_text("{}", encoding="utf-8")
    report = run_cycle(
        database, FoundryPaths(home), cfg,
        loop_id="l1", instance_id="i1")
    assert report["outcome"] == "FROZEN"


def test_stack_never_selects_disabled_framework(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    cfg = WorkerRuntimeConfig(
        runtime_state_root=str(tmp_path / "state"))  # both adapters disabled
    stack = build_worker_stack(
        database, FoundryPaths(tmp_path), cfg, run_id="r1")
    assert stack.enabled_frameworks == ()
    assert stack.router.is_eligible(WORKER_HERMES) is False
    assert stack.router.is_eligible(WORKER_OPENHANDS) is False
    assert stack.router.readiness[WORKER_OPENHANDS].state == NOT_READY


def test_mission_contract_carries_durable_truth(tmp_path):
    """MissionLoop -> bridge: the WorkerStepContract receives durable
    mission truth (acceptance criteria, expected artifacts, forbidden
    scope, attempt) and repair increments the attempt durably."""
    database, repository = mission_database(tmp_path)
    worker_router = WorkerRouter()
    worker_router.mark_ready(WORKER_OPENHANDS, state=READY,
                             note="proven in test",
                             evidence=("test-proof",))
    builder = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))

    class HermesFailAdapter(ScriptedWorkerAdapter):
        def dispatch(self, contract, *, heartbeat):
            if contract.role == "repair":
                self.outcome = ScriptedOutcome(
                    verdict="FAIL", files_changed=("feature.py",))
            else:
                self.outcome = ScriptedOutcome(verdict="FAIL")
            return super().dispatch(contract, heartbeat=heartbeat)

    verifier = HermesFailAdapter(WORKER_HERMES)
    registry = WorkerRegistry(
        {WORKER_OPENHANDS: builder, WORKER_HERMES: verifier})
    bridge = HybridWorkerRoleAdapter(
        database=database, router=worker_router, registry=registry,
        role_signals={
            "builder": {"bounded": True, "explicit_acceptance": True,
                        "tests_available": True, "narrow_scope": True,
                        "reversible": True},
            "verifier": {"ambiguous": True, "root_cause_required": True},
            "repair": {"ambiguous": True, "root_cause_required": True},
        },
    )
    loop = MissionLoop(database, router=RoleRouter(), adapter=bridge,
                       max_repairs=2)
    kwargs = dict(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=repository,
        instructions="implement feature.py and run the tests",
        expected_effects=("feature.py created",),
        rollback={"kind": "git_reset_soft"},
        verification_criteria=["feature.py exists and is correct"],
        expected_artifacts=("feature.py",),
        forbidden_paths=("tests/private",),
    )
    first = loop.run_step(**kwargs)
    assert first.outcome == FAILED  # verifier FAIL -> REPAIRING

    builder_contract = builder.received[0]
    assert builder_contract.attempt == 1
    assert builder_contract.expected_artifacts == ("feature.py",)
    assert builder_contract.verification_criteria == (
        "feature.py exists and is correct",)
    assert any(p == (repository / "tests/private").resolve()
               for p in builder_contract.forbidden_paths)

    second = loop.run_step(**kwargs)  # state REPAIRING -> role repair
    assert second.outcome == FAILED
    repairs = [c for c in verifier.received if c.role == "repair"]
    assert len(repairs) == 1
    assert repairs[0].attempt == 2

    third = loop.run_step(**kwargs)  # repair budget exhausted -> BLOCKED
    assert third.outcome == "BLOCKED"
    repairs = [c for c in verifier.received if c.role == "repair"]
    assert len(repairs) == 2
    assert repairs[-1].attempt == 3


def test_lab_scripted_stack_keeps_openhands_not_ready(tmp_path):
    from jarvis_foundry.runtime.worker_loop import _pin_routing_to_hermes

    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    cfg = WorkerRuntimeConfig(
        enabled=True,
        lab_scripted_hermes=True,
        openhands={"enabled": True},
        readiness={"openhands": {"state": READY, "evidence": ["claimed"]}},
        runtime_state_root=str(tmp_path / "state"),
    )
    stack = build_worker_stack(
        database, FoundryPaths(tmp_path), cfg, run_id="pin-lab")
    _pin_routing_to_hermes(stack, cfg)
    assert stack.router.is_eligible(WORKER_HERMES) is True
    assert stack.router.is_eligible(WORKER_OPENHANDS) is False


def test_openhands_stays_eligible_when_evidence_ready(tmp_path):
    from jarvis_foundry.runtime.worker_loop import _pin_routing_to_hermes

    exe = tmp_path / "openhands.exe"
    exe.write_bytes(b"")
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    cfg = WorkerRuntimeConfig(
        enabled=True,
        hermes={"enabled": False},
        openhands={"enabled": True, "exe": str(exe), "timeout_seconds": 30},
        readiness={"openhands": {
            "state": READY,
            "note": "containment proven in test",
            "evidence": ["containment-test"],
        }},
        runtime_state_root=str(tmp_path / "state"),
    )
    stack = build_worker_stack(
        database, FoundryPaths(tmp_path), cfg, run_id="pin-oh")
    _pin_routing_to_hermes(stack, cfg)
    assert WORKER_OPENHANDS in stack.enabled_frameworks
    assert stack.router.is_eligible(WORKER_OPENHANDS) is True
    step = WorkerStepContract(
        run_id="r", mission_id="m", step_id="s", attempt=1, role="builder",
        objective="o", instructions="i", working_root=tmp_path,
        task_signals={"bounded": True, "explicit_acceptance": True,
                      "tests_available": True, "narrow_scope": True,
                      "reversible": True},
    )
    decision = stack.router.route(step)
    assert decision.selected_worker == WORKER_OPENHANDS
    assert decision.fallback == (WORKER_HERMES,)


def test_manifest_hash_stable_without_criteria(tmp_path):
    """Default steps (no explicit criteria/artifacts) keep byte-identical
    manifest hashes so historical replay semantics are preserved."""
    from jarvis_foundry.roles.dispatcher import RoleDispatcher
    from jarvis_foundry.roles.router import RoleRouter
    from jarvis_foundry.workers.adapters.fakes import ScriptedWorkerAdapter

    database, repository = mission_database(tmp_path)
    router = RoleRouter()
    adapter = ScriptedWorkerAdapter(WORKER_HERMES)
    dispatcher = RoleDispatcher(database, router, adapter)
    manifest = dispatcher._manifest(
        "run-1", "builder", "mission-1", "step-1", "resource-1",
        "write_registered_worktree_files", "do the thing",
        300, 0.0, "opencode_go", router.config("builder"))
    payload = manifest.to_payload()
    assert "verification_criteria" not in payload
    assert "expected_artifacts" not in payload
    assert "forbidden_paths" not in payload
    assert "attempt" not in payload
    expected = {
        "run_id", "role", "mission_id", "step_id", "resource_id",
        "capability", "provider", "model", "isolation_group",
        "tool_policy", "budget_usd", "duration_seconds", "instructions",
    }
    assert set(payload) == expected
