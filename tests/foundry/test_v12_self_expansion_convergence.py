"""Disposable A5-style proof of the complete V12 self-expansion loop."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis_foundry.adapter_factory import (
    AdapterFactory,
    AdapterShape,
    CapabilityAdapterContract,
    ContractCase,
    DependencyDeclaration,
    DependencyKind,
    InvocationMapping,
    ProbeKind,
    ReadinessProbe,
    SelectedSolutionDescriptor,
    SolutionClass,
)
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import EffectClass
from jarvis_foundry.executive.autonomy import (
    create_objective,
    rebuild_executive_state,
    run_autonomy_cycle,
    set_objective_status,
)
from jarvis_foundry.fabric.artifact_trial import (
    CanonicalArtifactTrialPipeline,
    TrialMission,
)
from jarvis_foundry.fabric.catalog import (
    capability_registration,
    catalog_truth_snapshot,
    observed_readiness,
)
from jarvis_foundry.fabric.executors import ExecutorContext
from jarvis_foundry.fabric.gap_bridge import (
    ATTEMPT_DISPATCHED,
    ATTEMPT_STARTED,
    GAP_OPENED,
    RECURSION_BLOCKED,
    RESUMED,
    WAKE_FIRED,
    GapBridgeStopped,
    rebuild_gap_state,
    start_expansion_attempt,
)
from jarvis_foundry.fabric.loop import execute_action, seed_builtins
from jarvis_foundry.fabric.model import (
    Readiness,
    Semantic,
    TaskIntent,
    UnattendedMode,
)
from jarvis_foundry.mcp_backend import (
    CANONICAL_MCP_RUNTIME,
    FabricMcpBackend,
    stable_tool_identity,
)
from jarvis_foundry.mission.verification import accepted_verifications
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.roles.manifest import ContextManifest
from jarvis_foundry.roles.results import COMPLETED, FAILED
from jarvis_foundry.runtime._stack import build_worker_stack
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.self_expansion import (
    MCP_ADOPTION_VERIFIED,
    RUN_COMPLETED,
    RUN_FAILED,
    ExpansionBlueprint,
    SelfExpansionEngine,
    SelfExpansionPolicy,
    run_isolated_expansion_then_resume,
    self_expansion_record,
)


ROOT = Path(__file__).resolve().parents[2]
MISSING = "cap.hash.file.sha256"
ACTION = "hash.file.sha256"
TRIAL_SELECTED = "fabric.capability.artifact_trial.selected"
TRIAL_REJECTED = "fabric.capability.artifact_trial.rejected"
REGISTERED = "fabric.capability.registered"
MCP_SERVER_SCRIPT = ROOT / "scripts" / "v12_disposable_mcp_server.py"
MCP_SERVER_ID = "v12-selected-expansion"
MCP_MISSING, MCP_ACTION = stable_tool_identity(MCP_SERVER_ID, "echo")


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "mission-repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "v12@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "V12 Proof"],
        check=True,
    )
    (repository / "README.md").write_text("# V12 proof\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )
    return repository


def _events(database: FoundryDatabase, event_type: str):
    return [
        event for event in database.events(readonly=True)
        if event.event_type == event_type
    ]


def _blueprint(candidate, request, *, bad_contract: bool) -> ExpansionBlueprint:
    contract_text = "V12 contract fixture\n"
    mission_text = "V12 original objective mission fixture\n"
    contract_hash = hashlib.sha256(contract_text.encode()).hexdigest()
    mission_hash = hashlib.sha256(mission_text.encode()).hexdigest()
    expected_contract_hash = "0" * 64 if bad_contract else contract_hash
    suffix = candidate.candidate_id[-10:]
    descriptor = SelectedSolutionDescriptor(
        adapter_id=f"adapter.v12.sha256.{suffix}.{'bad' if bad_contract else 'good'}",
        solution_id=candidate.candidate_id,
        solution_class=SolutionClass.JARVIS_COMPONENT,
        solution_name=f"Adapted current JARVIS SHA256 {suffix}",
        solution_version="1.0.0",
        locator=candidate.locator,
        adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(
            ProbeKind.JARVIS_IMPORT,
            "jarvis_foundry.artifacts",
            (),
            {"module": "jarvis_foundry.artifacts"},
        ),
        capabilities=(
            CapabilityAdapterContract(
                capability_id=MISSING,
                version="1.0.0",
                action_kind=ACTION,
                semantic=Semantic.READ,
                effect_class=EffectClass.READ_ONLY,
                input_schema={
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string", "format": "path"}},
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "required": [
                        "claimed", "sha256", "adapter_id", "capability_id", "action_kind",
                    ],
                    "properties": {
                        "claimed": {"type": "string", "enum": ["COMPLETED"]},
                        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "adapter_id": {"type": "string"},
                        "capability_id": {"type": "string"},
                        "action_kind": {"type": "string"},
                    },
                },
                invocation=InvocationMapping(
                    target="jarvis_foundry.artifacts:sha256_file",
                    arguments=("$input.path",),
                    output={"sha256": "$raw.value"},
                ),
                contract_cases=(
                    ContractCase(
                        "known-file",
                        {"path": "$trial_root/contract.txt"},
                        {"claimed": "COMPLETED", "sha256": expected_contract_hash},
                    ),
                ),
                limitations=("owned-root files only", "read-only"),
            ),
        ),
        dependencies=(
            DependencyDeclaration(
                "jarvis-foundry",
                DependencyKind.JARVIS,
                "0.1.0",
                "jarvis-wheel",
                import_name="jarvis_foundry",
            ),
        ),
        provenance={
            "source": "self-expansion-proof-resolver",
            "authority": "JARVIS_SYSTEM_METADATA",
        },
        selection={
            "candidate_id": candidate.candidate_id,
            "gap_id": request.gap_id,
            "tier": candidate.tier.label,
            "source_authority": candidate.authority,
            "external_content_policy": "DATA_ONLY",
        },
    )
    return ExpansionBlueprint(
        descriptor=descriptor,
        mission=TrialMission(
            case_id="realistic-hash-mission",
            capability_id=MISSING,
            payload={"path": "$trial_root/mission.txt"},
            expected={"claimed": "COMPLETED", "sha256": mission_hash},
            fixtures={
                "contract.txt": contract_text,
                "mission.txt": mission_text,
            },
        ),
    )


def _mcp_blueprint(
    candidate,
    request,
    *,
    disposable_root: Path,
) -> ExpansionBlueprint:
    suffix = candidate.candidate_id[-10:]
    descriptor = SelectedSolutionDescriptor(
        adapter_id=f"adapter.v12.mcp.echo.{suffix}",
        solution_id=candidate.candidate_id,
        solution_class=SolutionClass.MCP_STDIO,
        solution_name=f"Selected disposable MCP echo {suffix}",
        solution_version="1.0.0",
        locator=str(MCP_SERVER_SCRIPT),
        adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(
            ProbeKind.MCP_TOOLS_LIST,
            sys.executable,
            ("-u", str(MCP_SERVER_SCRIPT)),
            {},
            timeout_ms=10_000,
        ),
        capabilities=(
            CapabilityAdapterContract(
                capability_id=MCP_MISSING,
                version="1.0.0",
                action_kind=MCP_ACTION,
                semantic=Semantic.READ,
                effect_class=EffectClass.READ_ONLY,
                input_schema={
                    "type": "object",
                    "required": ["text"],
                    "properties": {
                        "text": {"type": "string"},
                        "repeat": {"type": "integer", "minimum": 1, "maximum": 4},
                    },
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
                invocation=InvocationMapping(target="echo"),
                timeout_ms=4_000,
                unattended=UnattendedMode.ALLOWED,
                limitations=("disposable read-only proof server",),
            ),
        ),
        dependencies=(
            DependencyDeclaration(
                "python",
                DependencyKind.EXECUTABLE,
                sys.version.split()[0],
                sys.executable,
            ),
        ),
        provenance={
            "source": "v12-selected-mcp-proof-resolver",
            "authority": "JARVIS_SYSTEM_METADATA",
        },
        selection={
            "candidate_id": candidate.candidate_id,
            "gap_id": request.gap_id,
            "tier": candidate.tier.label,
            "source_authority": candidate.authority,
            "external_content_policy": "DATA_ONLY",
        },
        mcp_backend={
            "server_id": MCP_SERVER_ID,
            "env": {
                "JARVIS_MCP_DISPOSABLE_ROOT": str(disposable_root),
            },
        },
    )
    return ExpansionBlueprint(
        descriptor=descriptor,
        mission=TrialMission(
            case_id="selected-mcp-echo",
            capability_id=MCP_MISSING,
            payload={"arguments": {"text": "jarvis", "repeat": 2}},
            expected={"claimed": "COMPLETED", "verified": "MATCH"},
        ),
    )


def test_selected_mcp_solution_uses_canonical_backend_and_worker_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    resource_id = "resource-v12-selected-mcp"
    repository = _git_repository(tmp_path)
    database.append(registration_event(
        inspect_git_resource(repository, resource_id=resource_id)
    ))
    objective_id = "objective-v12-selected-mcp"
    create_objective(
        database,
        "Resume the original objective through one selected MCP capability",
        objective_id=objective_id,
        resource_id=resource_id,
        required_capabilities=(MCP_MISSING,),
        success_criteria=("selected MCP echo is verified through Fabric",),
        budget={"max_missions": 1, "max_retries": 1},
    )
    first_cycle = run_autonomy_cycle(
        database,
        cycle_id="v12-selected-mcp-gap",
    )
    assert first_cycle.materialization["materialized"] is False
    gap = next(
        item
        for item in rebuild_gap_state(database).values()
        if item.origin_objective_id == objective_id
    )
    assert gap.required_capability_id == MCP_MISSING

    resolver_calls: list[str] = []

    def resolver(candidate, request):
        resolver_calls.append(candidate.candidate_id)
        return _mcp_blueprint(
            candidate,
            request,
            disposable_root=tmp_path / "selected-mcp-disposable",
        )

    engine = SelfExpansionEngine(
        database,
        repo_root=ROOT,
        artifact_root=tmp_path / "unused-adapter-artifacts",
        trial_root=tmp_path / "selected-mcp-trials",
        resolver=resolver,
    )
    delivered = []

    class CapturingPort:
        def dispatch(self, request):
            delivered.append(request)
            return engine.dispatch(request)

    resumed = start_expansion_attempt(
        database,
        gap.gap_id,
        strategy_id="selected-mcp-canonical-backend",
        port=CapturingPort(),
    )
    assert resumed.status.value == "RESUMED"
    assert resolver_calls
    assert len(delivered) == 1
    receipt = engine.dispatch(delivered[0])
    assert receipt["status"] == "REGISTERED"
    assert receipt["runtime"] == CANONICAL_MCP_RUNTIME
    assert receipt["second_mcp_runtime"] is False
    assert receipt["trial_mode"] == "FABRIC_MCP_INVOCATION"
    assert engine.mcp_backend_for(MCP_MISSING) is not None
    assert len(_events(database, MCP_ADOPTION_VERIFIED)) == 1
    assert len(_events(database, WAKE_FIRED)) == 1
    assert len(_events(database, RESUMED)) == 1
    registrations = [
        event
        for event in _events(database, REGISTERED)
        if event.payload.get("capability_id") == MCP_MISSING
    ]
    assert len(registrations) == 1

    # Process-local MCP state is reconstructable from the durable selected
    # descriptor. The same registration is reconnected, never re-created.
    engine.close()

    def should_not_resolve(*_args):
        raise AssertionError("durable MCP adoption must replay without discovery")

    restarted_database = FoundryDatabase(database.path)
    restarted_database.initialize()
    restarted = SelfExpansionEngine(
        restarted_database,
        repo_root=ROOT,
        artifact_root=tmp_path / "unused-adapter-artifacts",
        trial_root=tmp_path / "selected-mcp-trials",
        resolver=should_not_resolve,
    )
    try:
        replay = restarted.dispatch(delivered[0])
        assert replay == receipt
        assert restarted.mcp_backend_for(MCP_MISSING) is None

        worker_home = tmp_path / "selected-mcp-worker-home"
        worker_home.mkdir()
        stack = build_worker_stack(
            restarted_database,
            FoundryPaths(worker_home),
            WorkerRuntimeConfig(
                enabled=True,
                status="ACTIVE",
                resource_id=resource_id,
                runtime_state_root=str(tmp_path / "selected-mcp-worker-state"),
            ),
            run_id="selected-mcp-worker",
        )
        try:
            assert stack.mcp_backend is not None
            result = stack.bridge.dispatch(
                ContextManifest(
                    run_id="run-v12-selected-mcp",
                    role="builder",
                    mission_id="mission-v12-selected-mcp",
                    step_id="echo",
                    resource_id=resource_id,
                    capability=MCP_MISSING,
                    provider="fabric",
                    model="local",
                    isolation_group="v12-selected-mcp",
                    instructions=json.dumps(
                        {"arguments": {"text": "worker-path", "repeat": 1}}
                    ),
                ),
                heartbeat=lambda: None,
            )
        finally:
            stack.close()
        assert result.status == COMPLETED
        assert result.provider == "fabric"
        assert f"capability:{MCP_MISSING}" in result.evidence
        assert len([
            event
            for event in _events(restarted_database, REGISTERED)
            if event.payload.get("capability_id") == MCP_MISSING
        ]) == 1
        assert len(_events(restarted_database, MCP_ADOPTION_VERIFIED)) == 1

        def fail_restoration(
            _backend: FabricMcpBackend,
            _server_id: str,
            *,
            include_disabled: bool = False,
        ):
            del include_disabled
            raise RuntimeError("simulated selected MCP restoration failure")

        monkeypatch.setattr(
            FabricMcpBackend,
            "connect_server",
            fail_restoration,
        )
        degraded_stack = build_worker_stack(
            restarted_database,
            FoundryPaths(worker_home),
            WorkerRuntimeConfig(
                enabled=True,
                status="ACTIVE",
                resource_id=resource_id,
                runtime_state_root=str(
                    tmp_path / "selected-mcp-worker-state-degraded"
                ),
            ),
            run_id="selected-mcp-worker-degraded",
        )
        generic_dispatches: list[object] = []

        def forbid_generic_dispatch(contract):
            generic_dispatches.append(contract)
            raise AssertionError(
                "degraded selected MCP capability must not reach a generic worker"
            )

        monkeypatch.setattr(
            degraded_stack.bridge.router,
            "route",
            forbid_generic_dispatch,
        )
        spine_before = len(_events(restarted_database, "fabric.spine.result"))
        route_before = len(_events(restarted_database, "worker.route_decided"))
        try:
            assert degraded_stack.mcp_backend is None
            degraded_result = degraded_stack.bridge.dispatch(
                ContextManifest(
                    run_id="run-v12-selected-mcp-degraded",
                    role="builder",
                    mission_id="mission-v12-selected-mcp-degraded",
                    step_id="echo",
                    resource_id=resource_id,
                    capability=MCP_MISSING,
                    provider="fabric",
                    model="local",
                    isolation_group="v12-selected-mcp-degraded",
                    instructions=json.dumps(
                        {"arguments": {"text": "must-not-fallback", "repeat": 1}}
                    ),
                ),
                heartbeat=lambda: None,
            )
        finally:
            degraded_stack.close()
        restoration_spine = _events(
            restarted_database,
            "fabric.spine.result",
        )[spine_before:]
        route_delta = (
            len(_events(restarted_database, "worker.route_decided"))
            - route_before
        )
        assert observed_readiness(
            restarted_database,
            MCP_MISSING,
        ).state is Readiness.DEGRADED
        assert degraded_result.status == FAILED
        assert degraded_result.provider == "fabric"
        assert degraded_result.error_class == "fabric_effect_failed"
        assert generic_dispatches == []
        assert route_delta == 0
        assert len(restoration_spine) == 1
        assert restoration_spine[0].payload["claimed"] == "BLOCKED"
        assert restoration_spine[0].payload["verified"] == "UNKNOWN"
        assert restoration_spine[0].payload["success"] is False

        proof_output = os.environ.get("JARVIS_V12_MCP_ADOPTION_PROOF_OUTPUT")
        if proof_output:
            registrations = [
                event
                for event in _events(restarted_database, REGISTERED)
                if event.payload.get("capability_id") == MCP_MISSING
            ]
            wakes = _events(restarted_database, WAKE_FIRED)
            resumes = _events(restarted_database, RESUMED)
            proof = {
                "contract_version": "jarvis.v12.selected-mcp-adoption-proof.v1",
                "passed": True,
                "disposable": True,
                "runtime": receipt["runtime"],
                "second_mcp_runtime": receipt["second_mcp_runtime"],
                "capability_id": MCP_MISSING,
                "action_kind": MCP_ACTION,
                "trial_mode": receipt["trial_mode"],
                "probation": True,
                "registration_count": len(registrations),
                "duplicate_registrations": max(0, len(registrations) - 1),
                "wake_count": len(wakes),
                "duplicate_wakes": max(0, len(wakes) - 1),
                "resume_count": len(resumes),
                "duplicate_resumes": max(0, len(resumes) - 1),
                "restart_replayed_same_receipt": replay == receipt,
                "worker_dispatch": {
                    "status": result.status,
                    "provider": result.provider,
                    "fallback_dispatches": 0,
                    "evidence": list(result.evidence),
                },
                "restoration_failure": {
                    "readiness": Readiness.DEGRADED.value,
                    "result_status": degraded_result.status,
                    "result_provider": degraded_result.provider,
                    "result_error_class": degraded_result.error_class,
                    "generic_dispatches": len(generic_dispatches),
                    "worker_route_event_delta": route_delta,
                    "spine_result_count": len(restoration_spine),
                    "spine_claimed": restoration_spine[0].payload["claimed"],
                    "spine_verified": restoration_spine[0].payload["verified"],
                    "spine_success": restoration_spine[0].payload["success"],
                },
                "live_mutated": False,
                "main_or_origin_mutated": False,
            }
            proof_path = Path(proof_output).expanduser().resolve()
            proof_path.parent.mkdir(parents=True, exist_ok=True)
            proof_path.write_text(
                json.dumps(proof, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    finally:
        restarted.close()


def test_selected_mcp_receipt_failure_rolls_back_runtime_and_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    objective_id = "objective-v12-selected-mcp-receipt-failure"
    create_objective(
        database,
        "Fail closed when selected MCP completion cannot be persisted",
        objective_id=objective_id,
        required_capabilities=(MCP_MISSING,),
        success_criteria=("no unreceipted MCP capability remains active",),
        budget={"max_missions": 1, "max_retries": 1},
    )
    first_cycle = run_autonomy_cycle(
        database,
        cycle_id="v12-selected-mcp-receipt-failure-gap",
    )
    assert first_cycle.materialization["materialized"] is False
    gap = next(
        item
        for item in rebuild_gap_state(database).values()
        if item.origin_objective_id == objective_id
    )

    def resolver(candidate, request):
        return _mcp_blueprint(
            candidate,
            request,
            disposable_root=tmp_path / "selected-mcp-receipt-failure",
        )

    engine = SelfExpansionEngine(
        database,
        repo_root=ROOT,
        artifact_root=tmp_path / "unused-adapter-artifacts",
        trial_root=tmp_path / "selected-mcp-receipt-failure-trials",
        resolver=resolver,
        policy=SelfExpansionPolicy(max_candidate_trials=1),
    )
    original_append_events = database.append_events

    def fail_completion_batch(events):
        if any(event.event_type == RUN_COMPLETED for event in events):
            raise RuntimeError("simulated durable completion receipt failure")
        return original_append_events(events)

    monkeypatch.setattr(database, "append_events", fail_completion_batch)
    try:
        failed = start_expansion_attempt(
            database,
            gap.gap_id,
            strategy_id="selected-mcp-receipt-failure",
            port=engine,
        )
        assert failed.status.value == "RETRYABLE_FAILURE"
        assert engine.mcp_backend_for(MCP_MISSING) is None
        assert capability_registration(database, MCP_MISSING) is None
        assert observed_readiness(database, MCP_MISSING).state is Readiness.MISSING
        assert len(_events(database, MCP_ADOPTION_VERIFIED)) == 0
        assert len(_events(database, RUN_COMPLETED)) == 0
        failures = _events(database, RUN_FAILED)
        assert len(failures) == 1
        assert failures[0].payload["receipt"]["status"] == "FAILED"
        assert len([
            event
            for event in _events(database, "fabric.capability.disabled")
            if event.payload.get("capability_id") == MCP_MISSING
        ]) == 1
    finally:
        engine.close()


def test_callable_expansion_entrypoint_resumes_and_replays_without_duplicate_trial(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    objective_id = "objective-v16-callable-expansion"
    create_objective(
        database,
        "Resume through the public isolated expansion entrypoint",
        objective_id=objective_id,
        required_capabilities=(MISSING,),
        success_criteria=("the exact objective resumes",),
        budget={"max_missions": 1, "max_retries": 1},
    )
    first_cycle = run_autonomy_cycle(
        database,
        cycle_id="v16-callable-expansion-gap",
    )
    assert first_cycle.materialization["materialized"] is False

    def resolver(candidate, request):
        return _blueprint(candidate, request, bad_contract=False)

    engine = SelfExpansionEngine(
        database,
        repo_root=ROOT,
        artifact_root=tmp_path / "callable-adapter-artifacts",
        trial_root=tmp_path / "callable-artifact-trials",
        resolver=resolver,
    )
    try:
        result = run_isolated_expansion_then_resume(
            database,
            objective_id=objective_id,
            port=engine,
            strategy_id="v16-callable-expansion",
        )
        assert len(result.gaps) == 1
        assert result.resumed_gap_ids == (result.gaps[0].gap_id,)
        assert result.waiting_gap_ids == ()
        assert rebuild_executive_state(database).objectives[objective_id].status == "ACTIVE"
        trial_count = len(_events(database, TRIAL_SELECTED))
        registration_count = len(_events(database, REGISTERED))

        replay = run_isolated_expansion_then_resume(
            database,
            objective_id=objective_id,
            port=engine,
            strategy_id="v16-callable-expansion",
        )
        assert replay.as_dict() == result.as_dict()
        assert len(_events(database, TRIAL_SELECTED)) == trial_count
        assert len(_events(database, REGISTERED)) == registration_count
        assert len(_events(database, WAKE_FIRED)) == 1
        assert len(_events(database, RESUMED)) == 1
    finally:
        engine.close()


def test_supervisor_default_expansion(tmp_path: Path) -> None:
    """The ordinary Executive dispatch must own the resolver and Catalog path."""
    # Keep the database parent short enough for Windows to create the
    # DB-adjacent immutable adapter/trial artifacts under production_port.
    scratch_root = Path(__file__).resolve().parents[2] / f"tmp-exp-{tmp_path.name[-8:]}"
    scratch_root.mkdir()
    database = FoundryDatabase(scratch_root / "f.db")
    database.initialize()
    objective_id = "objective-supervisor-default-source-adapter"
    try:
        create_objective(
            database,
            "Resume the original objective through the declared SHA256 adapter",
            objective_id=objective_id,
            required_capabilities=(MISSING,),
            success_criteria=("the original objective is active after gap resolution",),
            budget={"max_missions": 1, "max_retries": 1},
        )

        waiting_cycle = run_autonomy_cycle(
            database,
            cycle_id="supervisor-default-source-adapter-wait",
        )
        assert waiting_cycle.materialization["materialized"] is False
        opened_gap = next(iter(rebuild_gap_state(database).values()))
        assert opened_gap.origin_objective_id == objective_id
        assert opened_gap.required_capability_id == MISSING
        assert rebuild_executive_state(database).objectives[objective_id].status == "WAITING"

        # This is the production entry point: run_autonomy_cycle creates and closes
        # production_port(database), which selects canonical_blueprint_resolver.
        # No resolver, engine, discovery result, or Catalog registration is injected.
        run_autonomy_cycle(
            database,
            cycle_id="supervisor-default-source-adapter-expand",
            self_expansion=True,
        )

        dispatches = _events(database, "executive.self_expansion_dispatch_recorded")
        assert len(dispatches) == 1
        dispatch = dispatches[0].payload
        assert dispatch["objective_id"] == objective_id
        assert dispatch["gap_ids"] == [opened_gap.gap_id]
        assert dispatch["resumed_gap_ids"] == [opened_gap.gap_id]
        assert dispatch["waiting_gap_ids"] == []

        selected = _events(database, "fabric.self_expansion.solution_selected")
        assert len(selected) == 1
        assert selected[0].payload["adapter_id"].startswith(
            "adapter.jarvis.source.hash-file-sha256."
        )
        completed = _events(database, RUN_COMPLETED)
        assert len(completed) == 1
        receipt = completed[0].payload["receipt"]
        assert receipt["status"] == "REGISTERED"
        assert receipt["gap_id"] == opened_gap.gap_id
        assert receipt["trust"] == "PROBATION"
        assert receipt["independent_verification"] == "PASS"

        # Re-read active readiness through the Fabric Catalog after adoption.
        registration = capability_registration(database, MISSING)
        readiness = observed_readiness(database, MISSING)
        assert registration is not None
        assert registration["registration_id"] == receipt["registration_id"]
        assert readiness.state is Readiness.READY
        assert readiness.trust_state.value == "PROBATION"

        resumed_gap = rebuild_gap_state(database)[opened_gap.gap_id]
        assert resumed_gap.status.value == "RESUMED"
        assert resumed_gap.effective_capability_state == "PROBATION"
        assert rebuild_executive_state(database).objectives[objective_id].status == "ACTIVE"
        assert len(_events(database, WAKE_FIRED)) == 1
        assert len(_events(database, RESUMED)) == 1
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_real_gap_discover_adapt_trial_probation_resume_and_complete(tmp_path: Path) -> None:
    database = _database(tmp_path)
    repository = _git_repository(tmp_path)
    resource_id = "resource-v12-self-expansion"
    database.append(registration_event(
        inspect_git_resource(repository, resource_id=resource_id)
    ))
    set_capability_state(
        database,
        resource_id,
        "write_registered_worktree_files",
        "READY",
        evidence={"source": "v12-self-expansion-proof"},
    )
    objective_id = "objective-v12-self-expansion"
    create_objective(
        database,
        "Create the verified resumed self-expansion proof report",
        objective_id=objective_id,
        resource_id=resource_id,
        required_capabilities=(MISSING, "write_registered_worktree_files"),
        success_criteria=("resumed report exists",),
        budget={"max_missions": 2, "max_retries": 1},
    )

    first_cycle = run_autonomy_cycle(
        database,
        cycle_id="v12-self-expansion-gap",
        resource_id=resource_id,
        capability="write_registered_worktree_files",
    )
    assert first_cycle.materialization["materialized"] is False
    gap = next(iter(rebuild_gap_state(database).values()))
    executive = rebuild_executive_state(database)
    assert executive.objectives[objective_id].status == "WAITING"
    assert len(_events(database, GAP_OPENED)) == 1
    # Match normal worker-loop boot before pinning the Catalog rollback base.
    # The worker initializes these canonical rows before any mission dispatch.
    seed_builtins(database)
    before_catalog = catalog_truth_snapshot(database)

    resolver_calls: list[str] = []
    blueprints: dict[str, ExpansionBlueprint] = {}

    def resolver(candidate, request):
        resolver_calls.append(candidate.candidate_id)
        blueprint = _blueprint(
            candidate,
            request,
            bad_contract=len(resolver_calls) == 1,
        )
        blueprints[candidate.candidate_id] = blueprint
        return blueprint

    engine = SelfExpansionEngine(
        database,
        repo_root=ROOT,
        artifact_root=tmp_path / "adapter-artifacts",
        trial_root=tmp_path / "artifact-trials",
        resolver=resolver,
    )
    stop = tmp_path / "EMERGENCY_STOP"
    delivered = []

    class StopAfterVerifiedAdoption:
        def dispatch(self, request):
            delivered.append(request)
            receipt = engine.dispatch(request)
            stop.write_text("owner STOP at bridge handoff\n", encoding="utf-8")
            return receipt

    with pytest.raises(GapBridgeStopped):
        start_expansion_attempt(
            database,
            gap.gap_id,
            strategy_id="reuse-first-adapter-artifact-trial",
            port=StopAfterVerifiedAdoption(),
        )
    stopped = rebuild_gap_state(database)[gap.gap_id]
    attempt_id = stopped.active_attempt.attempt_id
    assert stopped.active_attempt.dispatched_at is None
    assert len(_events(database, ATTEMPT_STARTED)) == 1
    assert len(_events(database, ATTEMPT_DISPATCHED)) == 0
    assert len(resolver_calls) >= 2
    assert len(set(resolver_calls)) == len(resolver_calls)
    assert len(_events(database, TRIAL_REJECTED)) == 1
    rejected = _events(database, TRIAL_REJECTED)[0].payload["rejection"]
    assert rejected["catalog_unchanged"] is True

    # A fresh engine object after STOP replays the exact attempt/run/trial and
    # registration identities.  It does not rerun discovery or either trial.
    stop.unlink()

    def should_not_resolve(*_args):
        raise AssertionError("completed expansion must replay without resolving again")

    restarted = SelfExpansionEngine(
        FoundryDatabase(database.path),
        repo_root=ROOT,
        artifact_root=tmp_path / "adapter-artifacts",
        trial_root=tmp_path / "artifact-trials",
        resolver=should_not_resolve,
    )
    resumed_gap = start_expansion_attempt(
        database,
        gap.gap_id,
        strategy_id="same-stable-attempt",
        port=restarted,
    )
    assert resumed_gap.attempts[0].attempt_id == attempt_id
    assert resumed_gap.attempts[0].dispatched_at is not None
    assert resumed_gap.status.value == "RESUMED"
    assert rebuild_executive_state(database).objectives[objective_id].status == "ACTIVE"
    assert len(_events(database, ATTEMPT_STARTED)) == 1
    assert len(_events(database, ATTEMPT_DISPATCHED)) == 1
    assert len(_events(database, WAKE_FIRED)) == 1
    assert len(_events(database, RESUMED)) == 1

    selected_events = _events(database, TRIAL_SELECTED)
    trial_ids = [str(event.payload["trial_id"]) for event in selected_events]
    assert len(trial_ids) == 2
    assert len(trial_ids) == len(set(trial_ids))
    registration_events = [
        event for event in _events(database, REGISTERED)
        if event.payload.get("capability_id") == MISSING
    ]
    assert len(registration_events) == 1
    registration_id = str(registration_events[0].payload["registration_id"])
    winning_candidate = str(registration_events[0].payload["adoption"]["artifact_id"])
    run_id = str(engine.dispatch(delivered[0])["run_id"])
    expansion = self_expansion_record(database, run_id)
    assert expansion is not None
    assert expansion["status"] == "REGISTERED"
    assert expansion["discovery"]["reuse_order"] == [
        "FABRIC_CATALOG", "JARVIS_SOURCE", "JARVIS_GIT", "EXTERNAL", "CUSTOM_BUILD",
    ]
    assert expansion["discovery"]["external_results_authority"] == "DATA_ONLY"
    assert len(expansion["rejections"]) == 1

    mission_input = tmp_path / "original-mission-input.txt"
    mission_input.write_text("objective resumed through the adopted adapter\n", encoding="utf-8")
    action = execute_action(
        database,
        ExecutorContext(database=database, owned_root=tmp_path),
        TaskIntent(task_id=objective_id, required_actions=(ACTION,)),
        ACTION,
        {"path": str(mission_input)},
    )
    assert action.success is True
    assert action.claimed_effect["sha256"] == hashlib.sha256(mission_input.read_bytes()).hexdigest()
    assert action.observed["adapter_verification"]["readback_matches"] is True

    second_cycle = run_autonomy_cycle(
        database,
        cycle_id="v12-self-expansion-resumed",
        resource_id=resource_id,
        capability="write_registered_worktree_files",
    )
    assert second_cycle.materialization["materialized"] is True
    mission_id = str(second_cycle.materialization["mission_id"])
    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        resource_id=resource_id,
        lab_scripted_hermes=True,
    )
    mission = None
    worker_outcomes = []
    for ordinal in range(1, 4):
        worker_outcomes.append(run_cycle(
            database,
            FoundryPaths(tmp_path),
            config,
            loop_id=f"v12-self-expansion-mission-{ordinal}",
            instance_id=f"v12-self-expansion-worker-{ordinal}",
        ))
        mission = database.row("missions", "mission_id", mission_id)
        if mission is not None and mission["state"] == "COMPLETE":
            break
    assert mission is not None and mission["state"] == "COMPLETE", worker_outcomes
    with database.connect(readonly=True) as connection:
        pass_verdicts = int(connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts "
            "WHERE mission_id = ? AND status = 'PASS'",
            (mission_id,),
        ).fetchone()[0])
    assert pass_verdicts >= 1
    set_objective_status(
        database,
        objective_id,
        "COMPLETED",
        reason="resumed mission and adopted adapter both independently verified",
        evidence_refs=(mission_id, str(action.receipt["receipt_id"]), registration_id),
    )
    assert rebuild_executive_state(database).objectives[objective_id].status == "COMPLETED"

    # The winning artifact can be disabled without touching prior Catalog
    # truth.  A cached expansion receipt then fails closed instead of claiming
    # the disabled registration is still active.
    winning_blueprint = next(
        blueprint for candidate_id, blueprint in blueprints.items()
        if blueprint.descriptor.artifact_id == winning_candidate
    )
    winning_artifact = AdapterFactory(tmp_path / "adapter-artifacts").build(
        winning_blueprint.descriptor
    )
    rollback = CanonicalArtifactTrialPipeline(
        database,
        tmp_path / "artifact-trials",
        project_root=ROOT,
    ).disable(winning_artifact, capability_id=MISSING)
    after_rollback_catalog = catalog_truth_snapshot(database)
    assert rollback.status == "RESTORED"
    assert after_rollback_catalog["sha256"] == before_catalog["sha256"]
    assert catalog_truth_snapshot(database)["sha256"] == before_catalog["sha256"]
    stale = engine.dispatch(delivered[0])
    assert stale["status"] == "FAILED"
    assert stale["reason_code"] == "ADOPTION_NO_LONGER_ACTIVE"
    assert len([
        event for event in _events(database, REGISTERED)
        if event.payload.get("capability_id") == MISSING
    ]) == 1
    assert len(_events(database, RECURSION_BLOCKED)) == 0
    assert database.quick_check() == "ok"

    proof_output = os.environ.get("JARVIS_V12_PROOF_OUTPUT")
    if proof_output:
        proof_path = Path(proof_output).expanduser().resolve()
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        created_mission_ids = [
            str(event.payload.get("mission_id") or "")
            for event in _events(database, "mission.created")
            if event.payload.get("mission_id")
        ]
        completed_mission_ids = {
            str(event.payload.get("mission_id") or "")
            for event in _events(database, "mission.completed")
            if event.payload.get("mission_id")
        }
        duplicate_missions = (
            len(created_mission_ids) - len(set(created_mission_ids))
        )
        false_successes = sum(
            not accepted_verifications(database, mission_id=completed_mission_id)
            for completed_mission_id in completed_mission_ids
        )
        accepted_effect_keys = {
            str(event.payload.get("action_key") or "")
            for event in _events(database, "effect.acceptance_recorded")
            if event.payload.get("accepted") is True
            and event.payload.get("action_key")
        }
        terminal_effect_keys = {
            str(event.payload.get("action_key") or "")
            for event in _events(database, "effect.terminal_truth")
            if event.payload.get("accepted") is True
            and event.payload.get("reconciled") is True
            and event.payload.get("outcome") == "COMPLETED"
            and event.payload.get("action_key")
        }
        unreconciled_accepted_effects = len(
            accepted_effect_keys - terminal_effect_keys
        )
        assert duplicate_missions == 0
        assert false_successes == 0
        assert unreconciled_accepted_effects == 0
        proof = {
            "contract_version": "jarvis.v12.self-expansion-proof.v1",
            "passed": True,
            "disposable": True,
            "objective_id": objective_id,
            "objective_final_status": "COMPLETED",
            "mission_id": mission_id,
            "mission_final_state": mission["state"],
            "mission_pass_verdicts": pass_verdicts,
            "gap_id": gap.gap_id,
            "gap_count": len(_events(database, GAP_OPENED)),
            "duplicate_gaps": len(_events(database, GAP_OPENED)) - len({
                str(event.payload.get("gap_id")) for event in _events(database, GAP_OPENED)
            }),
            "attempt_id_before_stop": attempt_id,
            "attempt_id_after_restart": resumed_gap.attempts[0].attempt_id,
            "attempt_started_count": len(_events(database, ATTEMPT_STARTED)),
            "attempt_dispatched_count": len(_events(database, ATTEMPT_DISPATCHED)),
            "stop_paused_before_bridge_receipt": True,
            "discovery": {
                "reuse_order": expansion["discovery"]["reuse_order"],
                "search_stages": expansion["discovery"]["search_stages"],
                "candidate_count": expansion["discovery"]["candidate_count"],
                "first_failed_candidate_id": resolver_calls[0],
                "selected_candidate_id": expansion["receipt"]["candidate_id"],
                "external_results_authority": "DATA_ONLY",
            },
            "adapter_protocol": expansion["receipt"]["adapter_protocol"],
            "trial_ids": trial_ids,
            "trial_count": len(trial_ids),
            "duplicate_trials": len(trial_ids) - len(set(trial_ids)),
            "failed_trial_catalog_unchanged": rejected["catalog_unchanged"],
            "independent_trial_verification": expansion["receipt"]["independent_verification"],
            "registration_id": registration_id,
            "registration_count": len(registration_events),
            "duplicate_registrations": len(registration_events) - len({
                str(event.payload.get("registration_id")) for event in registration_events
            }),
            "probation": True,
            "wake_count": len(_events(database, WAKE_FIRED)),
            "duplicate_wakes": max(0, len(_events(database, WAKE_FIRED)) - 1),
            "resume_count": len(_events(database, RESUMED)),
            "duplicate_resumes": max(0, len(_events(database, RESUMED)) - 1),
            "duplicate_missions": duplicate_missions,
            "false_successes": false_successes,
            "unreconciled_accepted_effects": unreconciled_accepted_effects,
            "adapter_action": {
                "claimed": action.claimed.value,
                "verified": action.verified.value,
                "readback_matches": action.observed["adapter_verification"]["readback_matches"],
                "receipt_id": action.receipt["receipt_id"],
            },
            "rollback": {
                "status": rollback.status,
                "catalog_before_sha256": before_catalog["sha256"],
                "catalog_after_sha256": catalog_truth_snapshot(database)["sha256"],
                "restored": rollback.status == "RESTORED",
                "stale_receipt_failed_closed": stale["reason_code"],
            },
            "nested_expansion_count": len(_events(database, RECURSION_BLOCKED)),
            "sqlite_quick_check": database.quick_check(),
            "live_mutated": False,
            "main_or_origin_mutated": False,
        }
        proof_path.write_text(
            json.dumps(proof, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
