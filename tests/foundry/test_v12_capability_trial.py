"""V12 capability trial/adoption/disable proof on disposable state."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import (
    CapabilitySpec,
    CapabilityTrialPipeline,
    CapabilityTrust,
    DependencyPin,
    ExecutorContext,
    Kind,
    SelectedCapabilitySolution,
    TaskIntent,
    TrialCase,
    TrialStatus,
    UnattendedMode,
    VerifyVerdict,
    capability_registration,
    catalog_truth_snapshot,
    catalog_snapshot,
    execute_action,
    get_spec,
    observed_readiness,
    record_outcome,
    register_capability,
    trial_record,
)
from jarvis_foundry.fabric.installed_adapter import run_installed_adapter
from jarvis_foundry.fabric.model import Semantic


ROOT = Path(__file__).resolve().parents[2]
SOLUTION = ROOT / "examples" / "capability_solutions" / "sha256" / "solution.py"


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _descriptor(tmp_path: Path, *, source: Path = SOLUTION) -> SelectedCapabilitySolution:
    first = tmp_path / "first.bin"
    second = tmp_path / "mission.bin"
    first.write_bytes(b"contract input\n")
    second.write_bytes(b"a realistic bounded mission input\n")
    return SelectedCapabilitySolution(
        solution_id="solution.python.stdlib.sha256",
        spec=CapabilitySpec(
            capability_id="cap.hash.file.sha256.trial",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="hash.file.sha256",
            semantic=Semantic.READ,
            executor="selected-by-pipeline",
            unattended=UnattendedMode.ALLOWED,
            limitations=("owned-root files only", "read-only"),
        ),
        source_path=source,
        dependencies=(
            DependencyPin("python-stdlib", sys.version.split()[0], "runtime"),
            DependencyPin("hashlib", "stdlib", "python-runtime"),
        ),
        contract_cases=(
            TrialCase(
                "known-file",
                {"path": str(first)},
                {"claimed": "COMPLETED", "sha256": hashlib.sha256(first.read_bytes()).hexdigest()},
            ),
        ),
        mission=TrialCase(
            "realistic-mission",
            {"path": str(second)},
            {"claimed": "COMPLETED", "sha256": hashlib.sha256(second.read_bytes()).hexdigest(), "bytes": second.stat().st_size},
        ),
        config={"mode": "sha256", "network": "disabled", "effects": "read-only"},
        selection_reason="bounded local hashing gap selected for V12 proof",
    )


def test_trial_reaches_probation_with_real_adapter_and_stable_replay(tmp_path: Path) -> None:
    database = _db(tmp_path)
    descriptor = _descriptor(tmp_path)
    before = catalog_truth_snapshot(database)
    pipeline = CapabilityTrialPipeline(database, tmp_path / "trial-workspace")

    result = pipeline.run(descriptor)
    assert result.status == TrialStatus.PROBATION.value
    assert Path(result.workspace).is_dir()
    registration = capability_registration(database, descriptor.spec.capability_id)
    assert registration is not None
    assert registration["registration_id"] == descriptor.registration_id
    assert registration["trust"] == CapabilityTrust.PROBATION.value
    assert observed_readiness(database, descriptor.spec.capability_id).state.value == "READY"
    assert result.verification and result.verification["verdict"] == "PASS"
    assert (Path(result.workspace) / "dependency-lock.json").is_file()
    assert (Path(result.workspace) / "adapter-manifest.json").is_file()
    assert (Path(result.workspace) / "evidence" / "trial-bundle.json").is_file()

    # The same selected descriptor is a replay, not a second registration or
    # second adoption.  The durable state survives a new database object.
    replay = pipeline.run(descriptor)
    assert replay.status == TrialStatus.PROBATION.value
    registrations = [
        event for event in database.events()
        if event.event_type == "fabric.capability.registered"
        and event.payload.get("registration_id") == descriptor.registration_id
    ]
    assert len(registrations) == 1
    reopened = FoundryDatabase(database.path)
    assert trial_record(reopened, result.trial_id)["status"] == TrialStatus.PROBATION.value
    assert catalog_truth_snapshot(reopened)["count"] == before["count"] + 1

    # Exercise the adopted adapter through the canonical Fabric action path.
    owned = tmp_path / "owned"
    owned.mkdir()
    mission_input = owned / "post-adoption.bin"
    mission_input.write_bytes(b"post adoption read")
    context = ExecutorContext(database=database, owned_root=owned)
    action = execute_action(
        database,
        context,
        TaskIntent(task_id="trial-mission", required_actions=("hash.file.sha256",)),
        "hash.file.sha256",
        {"path": str(mission_input)},
    )
    assert action.success is True
    assert action.claimed_effect["sha256"] == hashlib.sha256(mission_input.read_bytes()).hexdigest()


def test_disable_removes_trial_capability_and_restores_catalog_truth(tmp_path: Path) -> None:
    database = _db(tmp_path)
    descriptor = _descriptor(tmp_path)
    before = catalog_truth_snapshot(database)
    pipeline = CapabilityTrialPipeline(database, tmp_path / "trial-workspace")
    adopted = pipeline.run(descriptor)
    assert adopted.status == TrialStatus.PROBATION.value

    disabled = pipeline.disable(descriptor)
    assert disabled.status == TrialStatus.RESTORED.value
    assert disabled.disable and disabled.disable["restored"] is True
    assert get_spec(database, descriptor.spec.capability_id) is None
    assert catalog_truth_snapshot(database)["sha256"] == before["sha256"]
    assert trial_record(database, adopted.trial_id)["status"] == TrialStatus.RESTORED.value
    # A disabled capability is unavailable; no adapter process or effect is
    # created by the post-disable attempt.
    owned = tmp_path / "owned"
    owned.mkdir()
    result = execute_action(
        database,
        ExecutorContext(database=database, owned_root=owned),
        TaskIntent(task_id="after-disable", required_actions=("hash.file.sha256",)),
        "hash.file.sha256",
        {"path": str(owned / "never-created")},
    )
    assert result.claimed.value == "BLOCKED"
    assert result.capability_id == "missing"


def test_failed_trial_is_durable_rejection_with_no_active_capability(tmp_path: Path) -> None:
    poor = tmp_path / "poor_solution.py"
    poor.write_text(
        "def readiness_probe():\n    return {'ready': False, 'protocol': 'jarvis.capability.adapter.v1'}\n"
        "def run(payload):\n    return {'claimed': 'COMPLETED', 'sha256': 'wrong'}\n",
        encoding="utf-8",
    )
    database = _db(tmp_path)
    descriptor = _descriptor(tmp_path, source=poor)
    result = CapabilityTrialPipeline(database, tmp_path / "trial-workspace").run(descriptor)
    assert result.status == TrialStatus.REJECTED.value
    assert result.rejection
    assert get_spec(database, descriptor.spec.capability_id) is None
    assert not [
        event for event in database.events()
        if event.event_type == "fabric.capability.registered"
        and event.payload.get("capability_id") == descriptor.spec.capability_id
    ]
    assert trial_record(database, result.trial_id)["status"] == TrialStatus.REJECTED.value


def test_probation_blocks_irreversible_effect_before_journal_claim(tmp_path: Path) -> None:
    database = _db(tmp_path)
    spec = CapabilitySpec(
        capability_id="cap.trial.mail.send",
        version="1.0.0",
        kind=Kind.ADAPTER,
        action_kind="gmail.send",
        semantic=Semantic.COMMUNICATE,
        executor="not-invoked",
        unattended=UnattendedMode.ALLOWED,
    )
    register_capability(
        database,
        spec,
        trust=CapabilityTrust.PROBATION,
        registration_id="fabric-registration:trial:mail-block",
        adoption={"trial_id": "trial:mail-block", "verification_id": "verification:mail-block"},
    )
    record_outcome(database, spec.capability_id, success=True, verified=VerifyVerdict.MATCH, note="disposable proof")
    result = execute_action(
        database,
        ExecutorContext(database=database, owned_root=tmp_path / "owned"),
        TaskIntent(task_id="probation-mail", required_actions=("gmail.send",)),
        "gmail.send",
        {"to": "owner@example.invalid", "body": "never sent"},
    )
    assert result.claimed.value == "BLOCKED"
    assert "PROBATION" in result.note
    assert not [event for event in database.events() if event.event_type == "system.action_started"]
    assert any(event.event_type == "fabric.capability.probation_blocked" for event in database.events())


def test_adapter_path_containment_and_manifest_hash_are_enforced(tmp_path: Path) -> None:
    database = _db(tmp_path)
    descriptor = _descriptor(tmp_path)
    result = CapabilityTrialPipeline(database, tmp_path / "trial-workspace").run(descriptor)
    assert result.status == TrialStatus.PROBATION.value
    manifest = Path(result.workspace) / "adapter-manifest.json"
    with pytest.raises(Exception):
        run_installed_adapter(
            manifest,
            {"path": "../outside.bin"},
            allowed_root=Path(result.workspace) / "inputs",
        )
    tampered = Path(result.workspace) / "solution" / "solution.py"
    tampered.write_text(tampered.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
    with pytest.raises(Exception):
        run_installed_adapter(manifest, {"path": "inputs/known-file/first.bin"}, allowed_root=result.workspace)
