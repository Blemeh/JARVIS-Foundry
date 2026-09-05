"""V13 package boundaries on the canonical hybrid worker bridge."""
from __future__ import annotations

import json
import sys
import types

from jarvis_foundry.roles.manifest import ContextManifest
from jarvis_foundry.roles.results import BLOCKED, COMPLETED, RoleResult
from jarvis_foundry.workers.adapters.fakes import ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.router import READY, VETOED, WorkerRouter

from worker_test_helpers import mission_database


def test_fabric_fast_path_enforces_scope_and_rejects_unknown_package(
    tmp_path,
    monkeypatch,
):
    database, repository = mission_database(tmp_path)
    dispatches = []
    fabric_package = types.ModuleType("jarvis_foundry.fabric")
    fabric_package.__path__ = []
    fabric_adapter = types.ModuleType("jarvis_foundry.fabric.adapter")

    def resolve_fabric_action(capability, selected_database=None):
        assert selected_database is database
        assert capability == "write_registered_worktree_files"
        return "file.write"

    def payload_from_manifest(manifest, action, owned_root, **_kwargs):
        assert action == "file.write"
        assert owned_root == repository.resolve()
        return json.loads(manifest.instructions)

    def try_fabric_dispatch(
        _database,
        manifest,
        _heartbeat,
        *,
        owned_root,
        **_kwargs,
    ):
        payload = json.loads(manifest.instructions)
        target = (owned_root / payload["target"]).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload["content"], encoding="utf-8")
        dispatches.append(target)
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=COMPLETED,
            provider="fabric",
            model="local",
        )

    fabric_adapter.resolve_fabric_action = resolve_fabric_action
    fabric_adapter.payload_from_manifest = payload_from_manifest
    fabric_adapter.try_fabric_dispatch = try_fabric_dispatch
    fabric_package.adapter = fabric_adapter
    monkeypatch.setitem(sys.modules, "jarvis_foundry.fabric", fabric_package)
    monkeypatch.setitem(
        sys.modules,
        "jarvis_foundry.fabric.adapter",
        fabric_adapter,
    )
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=WorkerRouter(),
        registry=WorkerRegistry(),
        use_fabric=True,
    )
    bridge.router.mark_ready(
        WORKER_HERMES,
        state=VETOED,
        note="prove narrow scope falls through without executing Fabric",
        evidence=("scope-regression",),
    )
    lineage = {
        "package_id": "package-scope",
        "package_hash": "a" * 64,
        "attempt_id": "attempt-scope",
    }

    forged = bridge.dispatch(
        ContextManifest(
            run_id="run-fabric-forged-package",
            role="builder",
            mission_id="mission-1",
            step_id="step-forged-package",
            resource_id="resource-1",
            capability="write_registered_worktree_files",
            provider="opencode_go",
            model="local",
            isolation_group="scope",
            instructions=json.dumps(
                {"target": "forged.txt", "content": "forged\n"}
            ),
            expected_artifacts=("forged.txt",),
            **lineage,
        ),
        heartbeat=lambda: None,
    )
    root_wide = bridge.dispatch(
        ContextManifest(
            run_id="run-fabric-root-wide",
            role="builder",
            mission_id="mission-1",
            step_id="step-root-wide",
            resource_id="resource-1",
            capability="write_registered_worktree_files",
            provider="opencode_go",
            model="local",
            isolation_group="scope",
            instructions=json.dumps(
                {"target": "root-wide.txt", "content": "root wide\n"}
            ),
            expected_artifacts=("root-wide.txt",),
        ),
        heartbeat=lambda: None,
    )
    narrow = bridge.dispatch(
        ContextManifest(
            run_id="run-fabric-narrow",
            role="builder",
            mission_id="mission-1",
            step_id="step-narrow",
            resource_id="resource-1",
            capability="write_registered_worktree_files",
            provider="opencode_go",
            model="local",
            isolation_group="scope",
            instructions=json.dumps(
                {"target": "src/inside.txt", "content": "inside\n"}
            ),
            expected_artifacts=("src/inside.txt",),
            allowed_paths=("src/inside.txt",),
            forbidden_paths=("live",),
        ),
        heartbeat=lambda: None,
    )
    blocked = bridge.dispatch(
        ContextManifest(
            run_id="run-fabric-blocked",
            role="builder",
            mission_id="mission-1",
            step_id="step-blocked",
            resource_id="resource-1",
            capability="write_registered_worktree_files",
            provider="opencode_go",
            model="local",
            isolation_group="scope",
            instructions=json.dumps(
                {"target": "outside.txt", "content": "outside\n"}
            ),
            expected_artifacts=("outside.txt",),
            allowed_paths=("src/inside.txt",),
            forbidden_paths=("live",),
        ),
        heartbeat=lambda: None,
    )

    assert forged.status == BLOCKED
    assert forged.error_class == "unregistered_resource"
    assert forged.error_detail == "unknown work package: package-scope"
    assert not (repository / "forged.txt").exists()
    assert root_wide.status == COMPLETED
    assert root_wide.provider == "fabric"
    assert (repository / "root-wide.txt").read_text(
        encoding="utf-8"
    ) == "root wide\n"
    assert narrow.status == BLOCKED
    assert narrow.error_class == "no_eligible_worker"
    assert not (repository / "src" / "inside.txt").exists()
    assert blocked.status == BLOCKED
    assert blocked.error_class == "invalid_workforce_scope"
    assert not (repository / "outside.txt").exists()
    assert dispatches == [(repository / "root-wide.txt").resolve()]


def test_relative_scope_paths_bind_to_registered_worktree(tmp_path):
    database, repository = mission_database(tmp_path)
    router = WorkerRouter()
    router.mark_ready(
        WORKER_HERMES,
        state=READY,
        note="scope capture",
        evidence=("scope-regression",),
    )
    worker = ScriptedWorkerAdapter(WORKER_HERMES)
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

    bridge.dispatch(
        ContextManifest(
            run_id="run-relative-scope",
            role="builder",
            mission_id="mission-1",
            step_id="step-relative-scope",
            resource_id="resource-1",
            capability="git_local_commit",
            provider="opencode_go",
            model="local",
            isolation_group="scope",
            instructions="capture normalized scope",
            allowed_paths=("src",),
            forbidden_paths=("live",),
        ),
        heartbeat=lambda: None,
    )

    assert worker.received
    contract = worker.received[0]
    assert contract.allowed_paths == ((repository / "src").resolve(),)
    assert contract.forbidden_paths == ((repository / "live").resolve(),)


def test_route_event_replays_without_event_conflict(tmp_path):
    database, _repository = mission_database(tmp_path)
    router = WorkerRouter()
    router.mark_ready(
        WORKER_HERMES,
        state=VETOED,
        note="force a route-only blocked result",
        evidence=("replay-regression",),
    )
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=router,
        registry=WorkerRegistry(),
    )
    manifest = ContextManifest(
        run_id="run-route-replay",
        role="builder",
        mission_id="mission-1",
        step_id="step-route-replay",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="local",
        isolation_group="replay",
        instructions="route only",
    )

    first = bridge.dispatch(manifest, heartbeat=lambda: None)
    event_count = len(database.events())
    second = bridge.dispatch(manifest, heartbeat=lambda: None)

    assert first.status == BLOCKED
    assert second.status == first.status
    assert second.error_class == first.error_class
    assert len(database.events()) == event_count


def test_route_event_replays_with_advisory_context(tmp_path):
    database, _repository = mission_database(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    router = WorkerRouter()
    router.mark_ready(
        WORKER_HERMES,
        state=VETOED,
        note="force a route-only blocked result",
        evidence=("advisory-replay-regression",),
    )
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=router,
        registry=WorkerRegistry(),
        vault_root=vault,
    )
    manifest = ContextManifest(
        run_id="run-advisory-route-replay",
        role="builder",
        mission_id="mission-1",
        step_id="step-advisory-route-replay",
        resource_id="resource-1",
        capability="git_local_commit",
        provider="opencode_go",
        model="local",
        isolation_group="replay",
        instructions="route with advisory context",
    )

    first = bridge.dispatch(manifest, heartbeat=lambda: None)
    second = bridge.dispatch(manifest, heartbeat=lambda: None)
    route_events = [
        event
        for event in database.events()
        if event.event_type == "worker.route_decided"
    ]

    assert first.status == BLOCKED
    assert second.status == first.status
    assert len(route_events) == 1
