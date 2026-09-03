"""V12 capability trial/adoption/disable proof on disposable state."""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
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
from jarvis_foundry.fabric.model import FabricError, Semantic
from jarvis_foundry.fabric.trial_verifier import verify_bundle


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
    baseline = CapabilitySpec(
        capability_id="cap.trial.baseline",
        version="1.0.0",
        kind=Kind.TOOL,
        action_kind="dir.list.baseline",
        semantic=Semantic.READ,
        executor="local",
        unattended=UnattendedMode.ALLOWED,
    )
    register_capability(database, baseline, note="pre-existing disposable baseline")
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
    replay_disable = pipeline.disable(descriptor)
    assert replay_disable.status == TrialStatus.RESTORED.value
    assert len([event for event in database.events() if event.event_type == "fabric.capability.trial.disabled"]) == 1


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


def test_selected_solution_source_hash_is_frozen_at_selection(tmp_path: Path) -> None:
    source = tmp_path / "selected.py"
    source.write_text(SOLUTION.read_text(encoding="utf-8"), encoding="utf-8")
    descriptor = _descriptor(tmp_path, source=source)
    original_hash = descriptor.source_sha256
    source.write_text(source.read_text(encoding="utf-8") + "\n# changed after selection\n", encoding="utf-8")
    assert descriptor.source_sha256 == original_hash
    database = _db(tmp_path)
    result = CapabilityTrialPipeline(database, tmp_path / "trial-workspace").run(descriptor)
    assert result.status == TrialStatus.REJECTED.value
    assert get_spec(database, descriptor.spec.capability_id) is None


def test_adoption_finalization_failure_disables_partial_catalog_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = _db(tmp_path)
    descriptor = _descriptor(tmp_path)

    def fail_outcome(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated finalization failure")

    import jarvis_foundry.fabric.trial as trial_module
    monkeypatch.setattr(trial_module, "record_outcome", fail_outcome)
    result = CapabilityTrialPipeline(database, tmp_path / "trial-workspace").run(descriptor)
    assert result.status == TrialStatus.REJECTED.value
    assert get_spec(database, descriptor.spec.capability_id) is None
    assert any(event.event_type == "fabric.capability.disabled" for event in database.events())
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
    lock = Path(result.workspace) / "dependency-lock.json"
    original_lock = lock.read_bytes()
    lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(Exception):
        run_installed_adapter(manifest, {"path": "inputs/known-file/first.bin"}, allowed_root=result.workspace)
    tampered = Path(result.workspace) / "solution" / "solution.py"
    # Restore the lock before isolating the solution-hash check.
    lock.write_bytes(original_lock)
    tampered.write_text(tampered.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
    with pytest.raises(Exception):
        run_installed_adapter(manifest, {"path": "inputs/known-file/first.bin"}, allowed_root=result.workspace)


def test_independent_verifier_rejects_tampered_contract_bundle(tmp_path: Path) -> None:
    database = _db(tmp_path)
    descriptor = _descriptor(tmp_path)
    result = CapabilityTrialPipeline(database, tmp_path / "trial-workspace").run(descriptor)
    assert result.status == TrialStatus.PROBATION.value
    bundle_path = Path(result.workspace) / "evidence" / "trial-bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["contract_cases"][0]["expected"]["sha256"] = "0" * 64
    tampered = tmp_path / "tampered-bundle.json"
    tampered.write_text(json.dumps(bundle), encoding="utf-8")
    verification = verify_bundle(tampered)
    assert verification["verdict"] == "FAIL"
    failed = {item["criterion"] for item in verification["criteria"] if item["status"] == "FAIL"}
    assert {"contract:known-file", "contract-cases-bound"}.issubset(failed)


def test_registration_identity_is_stable_across_disposable_input_roots(tmp_path: Path) -> None:
    left_root = tmp_path / "left"
    right_root = tmp_path / "right"
    left_root.mkdir()
    right_root.mkdir()
    left = _descriptor(left_root)
    right = _descriptor(right_root)
    assert left.descriptor_hash == right.descriptor_hash
    assert left.registration_id == right.registration_id


def test_selected_descriptor_round_trips_from_mapping_and_file(tmp_path: Path) -> None:
    descriptor = _descriptor(tmp_path)
    serialized = descriptor.as_dict()
    assert SelectedCapabilitySolution.from_mapping(serialized).registration_id == descriptor.registration_id
    descriptor_file = tmp_path / "selected-solution.json"
    descriptor_file.write_text(json.dumps(serialized), encoding="utf-8")
    loaded = SelectedCapabilitySolution.from_file(descriptor_file)
    assert loaded.descriptor_hash == descriptor.descriptor_hash
    assert loaded.source_path == descriptor.source_path


def test_pipeline_accepts_descriptor_json_file(tmp_path: Path) -> None:
    descriptor = _descriptor(tmp_path)
    descriptor_file = tmp_path / "selected-solution.json"
    descriptor_file.write_text(json.dumps(descriptor.as_dict()), encoding="utf-8")
    database = _db(tmp_path)
    result = CapabilityTrialPipeline(database, tmp_path / "trial-workspace").run_descriptor(descriptor_file)
    assert result.status == TrialStatus.PROBATION.value
    assert result.registration_id == descriptor.registration_id


def test_installed_adapter_receives_hash_bound_local_config(tmp_path: Path) -> None:
    source = tmp_path / "config-solution.py"
    source.write_text(
        "import hashlib\n"
        "from pathlib import Path\n"
        "def readiness_probe(config):\n"
        "    return {'ready': config.get('mode') == 'configured', 'protocol': 'jarvis.capability.adapter.v1'}\n"
        "def run(payload):\n"
        "    if payload.get('_config', {}).get('mode') != 'configured':\n"
        "        return {'claimed': 'FAILED', 'error_code': 'config_missing'}\n"
        "    path = Path(payload['path'])\n"
            "    return {'claimed': 'COMPLETED', 'mode': payload['_config']['mode'], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': len(path.read_bytes())}\n",
        encoding="utf-8",
    )
    base = _descriptor(tmp_path, source=source)
    cases = (replace(base.contract_cases[0], expected={**base.contract_cases[0].expected, "mode": "configured"}),)
    mission = replace(base.mission, expected={**base.mission.expected, "mode": "configured"})
    descriptor = replace(base, contract_cases=cases, mission=mission, config={"mode": "configured"})
    result = CapabilityTrialPipeline(_db(tmp_path), tmp_path / "trial-workspace").run(descriptor)
    assert result.status == TrialStatus.PROBATION.value


def test_explicit_registration_identity_rejects_conflicting_duplicate(tmp_path: Path) -> None:
    database = _db(tmp_path)
    spec = CapabilitySpec(
        capability_id="cap.trial.identity",
        version="1.0.0",
        kind=Kind.ADAPTER,
        action_kind="hash.file.sha256.identity",
        semantic=Semantic.READ,
        executor="adapter-manifest.json",
        unattended=UnattendedMode.ALLOWED,
    )
    registration_id = "fabric-registration:trial:identity"
    register_capability(database, spec, registration_id=registration_id, trust=CapabilityTrust.PROBATION)
    with pytest.raises(FabricError):
        register_capability(
            database,
            replace(spec, executor="other-manifest.json"),
            registration_id=registration_id,
            trust=CapabilityTrust.PROBATION,
        )


def test_probation_cannot_supersede_existing_catalog_truth(tmp_path: Path) -> None:
    database = _db(tmp_path)
    previous = CapabilitySpec(
        capability_id="cap.trial.previous",
        version="1.0.0",
        kind=Kind.TOOL,
        action_kind="hash.file.sha256.previous",
        semantic=Semantic.READ,
        executor="local",
        unattended=UnattendedMode.ALLOWED,
    )
    replacement = CapabilitySpec(
        capability_id="cap.trial.replacement",
        version="1.0.0",
        kind=Kind.ADAPTER,
        action_kind="hash.file.sha256.replacement",
        semantic=Semantic.READ,
        executor="manifest.json",
        supersedes=previous.capability_id,
        unattended=UnattendedMode.ALLOWED,
    )
    register_capability(database, previous)
    with pytest.raises(FabricError):
        register_capability(
            database,
            replacement,
            trust=CapabilityTrust.PROBATION,
            registration_id="fabric-registration:trial:supersede",
        )
    assert get_spec(database, previous.capability_id) == previous
    assert get_spec(database, replacement.capability_id) is None
