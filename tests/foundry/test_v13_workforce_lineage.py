"""Optional workforce lineage on the canonical worker execution path."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.roles.manifest import ContextManifest
from jarvis_foundry.workers.adapters.fakes import ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES, WorkerStepContract
from jarvis_foundry.workers.envelope import ResultEnvelope
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.router import READY, WorkerRouter

from test_worker_mission_bridge import durable_bridge_package
from worker_test_helpers import mission_database


LINEAGE_KEYS = frozenset({"package_id", "package_hash", "attempt_id"})
LEGACY_MANIFEST_HASH = (
    "e53cdf87f780be92b6afd7a48eae263ae88d1b215932d6e4c42b30367f3d8cfa"
)
LEGACY_CONTRACT_HASH = (
    "69a7ef9e3bf8b585c69818ce08cc1a66180cd75914b9f56f2ba61f82bb7a7851"
)


def manifest(**overrides) -> ContextManifest:
    fields = {
        "run_id": "run-lineage",
        "role": "builder",
        "mission_id": "mission-lineage",
        "step_id": "step-lineage",
        "resource_id": "resource-lineage",
        "capability": "code.write",
        "provider": "opencode_go",
        "model": "muse-spark-1.2-contributor",
        "isolation_group": "group-lineage",
        "tool_policy": ("git",),
        "budget_usd": 1.25,
        "duration_seconds": 600,
        "instructions": "Implement lineage.",
    }
    fields.update(overrides)
    return ContextManifest(**fields)


def contract(**overrides) -> WorkerStepContract:
    fields = {
        "run_id": "run-lineage",
        "mission_id": "mission-lineage",
        "step_id": "step-lineage",
        "attempt": 1,
        "role": "builder",
        "objective": "Implement lineage.",
        "instructions": "Bounded work.",
        "working_root": Path("C:/tmp/worktree"),
        "allowed_paths": (Path("C:/tmp/worktree/src"),),
        "forbidden_paths": (Path("C:/tmp/worktree/live"),),
        "context_manifest_id": "manifest-lineage",
        "context_manifest_hash": "a" * 64,
        "action_envelope": {"kind": "code"},
        "provider": "opencode_go",
        "model": "muse-spark-1.2-contributor",
        "duration_seconds": 600,
        "budget_usd": 1.25,
        "task_signals": {"code": True},
        "expected_artifacts": ("result.txt",),
        "verification_criteria": ("tests pass",),
    }
    fields.update(overrides)
    return WorkerStepContract(**fields)


class CapturingAdapter(ScriptedWorkerAdapter):
    def __init__(self) -> None:
        super().__init__(WORKER_HERMES)
        self.envelope: ResultEnvelope | None = None

    def dispatch(self, contract, *, heartbeat):
        self.envelope = super().dispatch(contract, heartbeat=heartbeat)
        return self.envelope


def test_empty_lineage_preserves_historical_payload_hashes():
    context = manifest()
    step = contract()
    envelope = ResultEnvelope.from_contract(step, framework=WORKER_HERMES)

    assert LINEAGE_KEYS.isdisjoint(context.to_payload())
    assert LINEAGE_KEYS.isdisjoint(step.to_payload())
    assert LINEAGE_KEYS.isdisjoint(envelope.to_payload())
    assert context.content_hash() == LEGACY_MANIFEST_HASH
    assert step.content_hash() == LEGACY_CONTRACT_HASH


def test_populated_lineage_round_trips_through_all_contracts():
    lineage = {
        "package_id": "package-1",
        "package_hash": "b" * 64,
        "attempt_id": "attempt-1",
    }
    context = manifest(**lineage)
    step = contract(**lineage)
    envelope = ResultEnvelope.from_contract(step, framework=WORKER_HERMES)

    assert ContextManifest.from_payload(context.to_payload()) == context
    assert WorkerStepContract.from_payload(step.to_payload()) == step
    assert ResultEnvelope.from_payload(envelope.to_payload()) == envelope
    assert {key: context.to_payload()[key] for key in LINEAGE_KEYS} == lineage
    assert {key: step.to_payload()[key] for key in LINEAGE_KEYS} == lineage
    assert {key: envelope.to_payload()[key] for key in LINEAGE_KEYS} == lineage


def test_hybrid_adapter_propagates_lineage_to_contract_and_envelope(tmp_path):
    database, repository = mission_database(tmp_path)
    package = durable_bridge_package(
        database,
        repository,
        package_id="package-bridge",
        cost_budget_usd=None,
    )
    router = WorkerRouter()
    router.mark_ready(
        WORKER_HERMES,
        state=READY,
        note="lineage propagation test",
        evidence=("focused-test",),
    )
    worker = CapturingAdapter()
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=router,
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={
            "builder": {
                "ambiguous": True,
                "root_cause_required": True,
            }
        },
    )
    attempt_id = f"workforce:{package.package_id}:a1"
    lineage = {
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "attempt_id": attempt_id,
    }
    preference = {
        "worker_preference": WORKER_HERMES,
        "worker_preference_evidence": ("suitability:builder:python",),
    }

    bridge.dispatch(
        manifest(
            run_id=attempt_id,
            mission_id=str(package.mission_id),
            step_id=str(package.step_id),
            resource_id=package.repository_id,
            capability=package.capability,
            **lineage,
            **preference,
        ),
        heartbeat=lambda: None,
    )

    assert worker.received
    assert worker.envelope is not None
    step = worker.received[0]
    assert {key: getattr(step, key) for key in LINEAGE_KEYS} == lineage
    assert {
        key: getattr(worker.envelope, key) for key in LINEAGE_KEYS
    } == lineage
    assert step.task_signals["preferred_worker"] == WORKER_HERMES
    assert step.task_signals["worker_preference_evidence"] == [
        "suitability:builder:python"
    ]


def test_worker_preference_is_hash_bound_and_round_trips():
    context = manifest(
        worker_preference=WORKER_HERMES,
        worker_preference_evidence=("suitability:sample-1",),
    )

    assert ContextManifest.from_payload(context.to_payload()) == context
    assert context.to_payload()["worker_preference"] == WORKER_HERMES
    assert context.content_hash() != manifest().content_hash()
