"""Canonical MissionLoop integration through the hybrid worker bridge."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.mission.loop import PASSED, MissionLoop
from jarvis_foundry.roles.manifest import ContextManifest
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES, WORKER_OPENHANDS
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.result_contract import RESULT_FILENAME
from jarvis_foundry.workers.router import READY, WorkerRouter

from worker_test_helpers import make_controller, mission_database


class MutatingScriptedAdapter(ScriptedWorkerAdapter):
    def __init__(self, framework, *, write_files=(), content=None,
                 outcome=None):
        super().__init__(framework, outcome=outcome)
        self.write_files = tuple(write_files)
        self.content = content

    def dispatch(self, contract, *, heartbeat):
        for rel in self.write_files:
            path = contract.working_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.content or f"content of {rel}\n",
                            encoding="utf-8")
        return super().dispatch(contract, heartbeat=heartbeat)


def test_canonical_mission_loop_dispatches_through_hybrid_workers(tmp_path):
    """The existing MissionLoop executes a step through the worker bridge
    (Hermes primary path) and returns to next-action selection."""
    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    router.mark_ready(WORKER_OPENHANDS, state=READY,
                      note="containment proven in test",
                      evidence=("test-proof",))
    hermes = MutatingScriptedAdapter(
        WORKER_HERMES, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    verifier = ScriptedWorkerAdapter(
        WORKER_OPENHANDS, outcome=ScriptedOutcome(verdict="PASS"))
    registry = WorkerRegistry(
        {WORKER_HERMES: hermes, WORKER_OPENHANDS: verifier})
    adapter = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        role_signals={"builder": {"ambiguous": True,
                                  "root_cause_required": True},
                       "verifier": {"bounded": True,
                                    "explicit_acceptance": True,
                                    "tests_available": True,
                                    "narrow_scope": True}},
    )
    loop = MissionLoop(database, router=RoleRouter(), adapter=adapter)

    from jarvis_foundry.domain import ActionEnvelope
    envelope = ActionEnvelope(
        action_id="commit-step-1", mission_id="mission-1",
        resource_id="resource-1", action_kind="git_commit",
        target=repository, capability="git_local_commit",
        expected_effects=("create local commit",),
        risk_class="C1", verification_required=True,
        rollback={"kind": "git_reset_soft"},
    )
    outcome = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=repository,
        instructions="implement feature.py and run the tests",
        expected_effects=("feature.py created",),
        rollback={"kind": "git_reset_soft"},
        commit_envelope=envelope,
    )
    assert outcome.outcome == PASSED
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert hermes.received[0].run_id == outcome.run_id


def test_canonical_mission_loop_rejects_false_success_from_bridge(tmp_path):
    """A worker claiming success with no real change must NOT PASS through
    the canonical loop: the bridge returns NOT_TESTED/evidence_mismatch."""
    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=(),
        outcome=ScriptedOutcome(claimed_outcome="done",
                                files_changed=("feature.py",)))
    registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    router.mark_ready(WORKER_OPENHANDS, state=READY, note="containment proven",
                      evidence=("test-proof",))
    adapter = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        role_signals={"builder": {"bounded": True, "explicit_acceptance": True,
                                  "tests_available": True, "narrow_scope": True}},
    )
    loop = MissionLoop(database, router=RoleRouter(), adapter=adapter)
    outcome = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=repository,
        instructions="implement feature.py",
        expected_effects=("feature.py created",),
        rollback={"kind": "git_reset_soft"},
    )
    # The loop must NOT accept: either FAIL (repair) or BLOCKED, never PASS.
    assert outcome.outcome != PASSED
    assert outcome.outcome in ("FAILED", "BLOCKED")


def test_evidence_mismatch_is_not_accepted_even_if_verifier_would_pass(tmp_path):
    """Builder mutates the tree but claims files_changed=[] while an eligible
    verifier is scripted to PASS. The loop must BLOCK before verification;
    a PASS wash-out is the live accept hole."""
    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    router.mark_ready(WORKER_OPENHANDS, state=READY,
                      note="containment proven in test",
                      evidence=("test-proof",))
    router.mark_ready(WORKER_HERMES, state=READY,
                      note="containment proven in test",
                      evidence=("test-proof",))
    builder = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=()))
    verifier = ScriptedWorkerAdapter(
        WORKER_HERMES, outcome=ScriptedOutcome(verdict="PASS"))
    registry = WorkerRegistry(
        {WORKER_OPENHANDS: builder, WORKER_HERMES: verifier})
    adapter = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        role_signals={
            "builder": {"bounded": True, "explicit_acceptance": True,
                        "tests_available": True, "narrow_scope": True,
                        "reversible": True},
            "verifier": {"bounded": True, "explicit_acceptance": True,
                         "tests_available": True, "narrow_scope": True},
        },
    )
    loop = MissionLoop(database, router=RoleRouter(), adapter=adapter)
    outcome = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=repository,
        instructions="implement feature.py",
        expected_effects=("feature.py created",),
        rollback={"kind": "git_reset_soft"},
    )
    assert outcome.outcome != PASSED
    assert outcome.outcome == "BLOCKED"
    assert verifier.received == []


def test_bridge_mismatch_status_is_failed_not_completed(tmp_path):
    """Direct bridge contract: evidence mismatch must not stay COMPLETED."""
    from jarvis_foundry.roles.manifest import ContextManifest
    from jarvis_foundry.roles.results import COMPLETED as ROLE_COMPLETED
    from jarvis_foundry.roles.results import FAILED as ROLE_FAILED

    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    router.mark_ready(WORKER_OPENHANDS, state=READY,
                      note="containment proven in test",
                      evidence=("test-proof",))
    builder = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=()))
    registry = WorkerRegistry({WORKER_OPENHANDS: builder})
    bridge = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        role_signals={"builder": {"bounded": True, "explicit_acceptance": True,
                                  "tests_available": True, "narrow_scope": True,
                                  "reversible": True}},
    )
    manifest = ContextManifest(
        run_id="run-mismatch", role="builder",
        mission_id="mission-1", step_id="step-1",
        resource_id="resource-1", capability="git_local_commit",
        provider="opencode_go", model="deepseek-v4-flash",
        isolation_group="ind", instructions="implement feature.py",
    )
    result = bridge.dispatch(manifest, heartbeat=lambda: None)
    assert result.status != ROLE_COMPLETED
    assert result.status == ROLE_FAILED
    assert result.error_class == "evidence_mismatch"
    assert result.verdict == "NOT_TESTED"


def test_mission_bridge_enriches_instructions_with_result_contract(tmp_path):
    """The live MissionLoop -> RoleDispatcher -> HybridWorkerRoleAdapter
    path must append the machine-ingestable result requirement so real
    provider builders can return a durable files_changed claim."""
    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    router.mark_ready(WORKER_OPENHANDS, state=READY,
                      note="containment proven in test",
                      evidence=("test-proof",))
    builder = ScriptedWorkerAdapter(WORKER_OPENHANDS)
    registry = WorkerRegistry({WORKER_OPENHANDS: builder})
    bridge = HybridWorkerRoleAdapter(
        database=database, router=router, registry=registry,
        role_signals={"builder": {"bounded": True,
                                  "explicit_acceptance": True,
                                  "tests_available": True,
                                  "narrow_scope": True,
                                  "reversible": True}},
    )
    manifest = ContextManifest(
        run_id="run-bridge-result", role="builder",
        mission_id="mission-1", step_id="step-1",
        resource_id="resource-1", capability="git_local_commit",
        provider="opencode_go", model="deepseek-v4-flash",
        isolation_group="ind", instructions="implement feature.py",
    )
    outcome = bridge.dispatch(manifest, heartbeat=lambda: None)
    assert outcome is not None
    assert builder.received, "bridge did not dispatch the builder"
    prompt = builder.received[0].instructions
    assert RESULT_FILENAME in prompt
    marker = str((repository / RESULT_FILENAME).resolve())
    assert marker in prompt
    assert prompt.index("JARVIS RESULT CONTRACT") < prompt.index("implement feature.py")
