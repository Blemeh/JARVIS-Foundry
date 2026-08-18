"""CANONICAL RUNTIME PATH — live worker loop driver tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.mission.loop import FAILED, MissionLoop
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.runtime._stack import build_worker_stack
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES, WORKER_OPENHANDS
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


def test_run_cycle_disabled(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    report = run_cycle(
        database, FoundryPaths(tmp_path), WorkerRuntimeConfig(),
        loop_id="l1", instance_id="i1")
    assert report["outcome"] == "DISABLED"


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
    assert any(p == Path("tests/private")
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
