"""Canonical Adapter Factory artifact trial/adoption/rollback proofs."""
from __future__ import annotations

import sys
from dataclasses import replace
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
    ReconciliationMapping,
    SelectedSolutionDescriptor,
    SolutionClass,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import EffectClass
from jarvis_foundry.fabric.artifact_trial import (
    ArtifactTrialError,
    ArtifactTrialStatus,
    CanonicalArtifactTrialPipeline,
    TrialMission,
    artifact_trial_record,
)
from jarvis_foundry.fabric.catalog import (
    capability_registration,
    catalog_truth_snapshot,
    observed_readiness,
)
from jarvis_foundry.fabric.executors import ExecutorContext
from jarvis_foundry.fabric.loop import execute_action
from jarvis_foundry.fabric.model import CapabilityTrust, Semantic, TaskIntent


ROOT = Path(__file__).resolve().parents[2]
INPUT_SCHEMA = {
    "type": "object",
    "required": ["text"],
    "properties": {"text": {"type": "string", "minLength": 1}},
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["claimed", "echo", "adapter_id", "capability_id", "action_kind"],
    "properties": {
        "claimed": {"type": "string", "enum": ["COMPLETED"]},
        "echo": {"type": "string"},
        "length": {"type": "integer"},
        "adapter_id": {"type": "string"},
        "capability_id": {"type": "string"},
        "action_kind": {"type": "string"},
    },
}


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _descriptor(tmp_path: Path, *, solution: str = "good", bad_contract: bool = False) -> SelectedSolutionDescriptor:
    script = tmp_path / f"echo-{solution}.py"
    script.write_text(
        "import json,sys\n"
        "print(json.dumps({'echo': sys.argv[1], 'length': len(sys.argv[1])}, sort_keys=True))\n",
        encoding="utf-8",
    )
    expected = "wrong" if bad_contract else "jarvis"
    return SelectedSolutionDescriptor(
        adapter_id=f"adapter.v12.echo.{solution}",
        solution_id=f"solution.v12.echo.{solution}",
        solution_class=SolutionClass.CLI,
        solution_name=f"Disposable echo {solution}",
        solution_version="1.0.0",
        locator=str(script),
        adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(
            ProbeKind.COMMAND,
            sys.executable,
            ("--version",),
            {"returncode": 0},
        ),
        capabilities=(
            CapabilityAdapterContract(
                capability_id="cap.v12.echo.read",
                version="1.0.0",
                action_kind="v12.echo.read",
                semantic=Semantic.READ,
                effect_class=EffectClass.READ_ONLY,
                input_schema=INPUT_SCHEMA,
                output_schema=OUTPUT_SCHEMA,
                invocation=InvocationMapping(
                    target=sys.executable,
                    arguments=(str(script), "$input.text"),
                    output={"echo": "$raw.json.echo", "length": "$raw.json.length"},
                ),
                contract_cases=(
                    ContractCase(
                        "known-echo",
                        {"text": "jarvis"},
                        {"claimed": "COMPLETED", "echo": expected, "length": 6},
                    ),
                ),
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
        provenance={"source": "disposable-proof", "authority": "JARVIS_SYSTEM_METADATA"},
    )


def _mission() -> TrialMission:
    return TrialMission(
        case_id="realistic-objective",
        capability_id="cap.v12.echo.read",
        payload={"text": "original mission resumes"},
        expected={"claimed": "COMPLETED", "echo": "original mission resumes", "length": 24},
    )


def test_canonical_artifact_reaches_probation_replays_executes_and_rolls_back(tmp_path: Path) -> None:
    database = _db(tmp_path)
    before = catalog_truth_snapshot(database)
    artifact = AdapterFactory(tmp_path / "artifacts").build(_descriptor(tmp_path))
    pipeline = CanonicalArtifactTrialPipeline(database, tmp_path / "trials", project_root=ROOT)

    adopted = pipeline.run(artifact, _mission())
    assert adopted.status == ArtifactTrialStatus.PROBATION.value
    assert adopted.verification and adopted.verification["verdict"] == "PASS"
    registration = capability_registration(database, "cap.v12.echo.read")
    assert registration is not None
    assert registration["registration_id"] == adopted.registration_id
    assert registration["trust"] == CapabilityTrust.PROBATION.value
    assert observed_readiness(database, "cap.v12.echo.read").state.value == "READY"

    replay = CanonicalArtifactTrialPipeline(
        FoundryDatabase(database.path), tmp_path / "trials", project_root=ROOT
    ).run(artifact, _mission())
    assert replay.trial_id == adopted.trial_id
    assert len([
        event for event in database.events(readonly=True)
        if event.event_type == "fabric.capability.registered"
        and event.payload.get("registration_id") == adopted.registration_id
    ]) == 1

    action = execute_action(
        database,
        ExecutorContext(database=database, owned_root=tmp_path),
        TaskIntent(task_id="original-objective", required_actions=("v12.echo.read",)),
        "v12.echo.read",
        {"text": "original mission resumes"},
    )
    assert action.success is True
    assert action.claimed_effect["echo"] == "original mission resumes"

    disabled = pipeline.disable(artifact, capability_id="cap.v12.echo.read")
    assert disabled.status == ArtifactTrialStatus.RESTORED.value
    assert disabled.disable and disabled.disable["restored"] is True
    assert catalog_truth_snapshot(database)["sha256"] == before["sha256"]
    assert artifact_trial_record(database, adopted.trial_id)["status"] == "RESTORED"


def test_failed_artifact_trial_cannot_corrupt_catalog(tmp_path: Path) -> None:
    database = _db(tmp_path)
    before = catalog_truth_snapshot(database)
    artifact = AdapterFactory(tmp_path / "artifacts").build(
        _descriptor(tmp_path, solution="bad", bad_contract=True)
    )
    pipeline = CanonicalArtifactTrialPipeline(database, tmp_path / "trials", project_root=ROOT)

    rejected = pipeline.run(artifact, _mission())
    assert rejected.status == ArtifactTrialStatus.REJECTED.value
    assert rejected.rejection and rejected.rejection["stage"] == "contracts"
    assert rejected.rejection["catalog_unchanged"] is True
    assert capability_registration(database, "cap.v12.echo.read") is None
    assert catalog_truth_snapshot(database)["sha256"] == before["sha256"]
    assert (artifact.root / "ADAPTER_DISABLED.json").is_file()


def test_probation_trial_refuses_mutating_capability_before_registration(tmp_path: Path) -> None:
    database = _db(tmp_path)
    before = catalog_truth_snapshot(database)
    descriptor = _descriptor(tmp_path, solution="mutating")
    contract = descriptor.capabilities[0]
    mutating = replace(
        contract,
        semantic=Semantic.EXTERNAL_EFFECT,
        effect_class=EffectClass.IRREVERSIBLE_OR_HIGH_COST,
        reconciliation=ReconciliationMapping(
            "provider_query",
            ("echo",),
            contract.invocation,
        ),
    )
    artifact = AdapterFactory(tmp_path / "artifacts").build(
        replace(descriptor, capabilities=(mutating,))
    )
    pipeline = CanonicalArtifactTrialPipeline(
        database, tmp_path / "trials", project_root=ROOT
    )

    with pytest.raises(ArtifactTrialError, match="must be read-only"):
        pipeline.run(artifact, _mission())
    assert capability_registration(database, "cap.v12.echo.read") is None
    assert catalog_truth_snapshot(database)["sha256"] == before["sha256"]
