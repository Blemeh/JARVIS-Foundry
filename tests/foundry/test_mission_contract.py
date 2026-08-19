"""Focused proof for durable existing-mission executable contracts."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    MissionStepContractError,
    latest_contract,
    record_step_contract,
)
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.reconcile import reconcile
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import LOOP_LEASE_KEY, run_cycle
from jarvis_foundry.supervisor.leases import LeaseManager

from worker_test_helpers import mission_database


def _contract(*, mission_id: str = "mission-1", resource_id: str = "resource-1",
              version: int = 1, **changes: object) -> MissionStepContract:
    values: dict[str, object] = {
        "mission_id": mission_id,
        "step_id": "step-health",
        "version": version,
        "resource_id": resource_id,
        "capability": "git_local_commit",
        "target_relative_path": "artifacts/health.json",
        "instructions": "write the health artifact and run the acceptance checks",
        "expected_effects": ("artifact is written",),
        "rollback": {"method": "git_reset_soft", "reversible": True},
        "risk_class": "C1",
        "verification_criteria": ("artifact hash is recorded",),
        "expected_artifacts": ("artifacts/health.json",),
        "forbidden_paths": ("owner-private",),
        "recorded_by": "supervisor",
        "source": "portfolio.decision",
        "provenance": {"candidate_id": "candidate-health-1"},
    }
    values.update(changes)
    return MissionStepContract(**values)  # type: ignore[arg-type]


def test_contract_is_canonical_hash_bound_and_rebuildable(tmp_path: Path) -> None:
    database, _ = mission_database(tmp_path)
    contract = _contract()
    sequence, created = record_step_contract(database, contract)
    assert sequence > 0 and created is True
    assert latest_contract(database, "mission-1") == contract
    assert database.all("mission_step_contracts", mission_id="mission-1")

    duplicate_sequence, duplicate_created = record_step_contract(database, contract)
    assert duplicate_sequence == sequence
    assert duplicate_created is False
    assert len(database.all("mission_step_contracts", mission_id="mission-1")) == 1

    assert database.rebuild_projections() > 0
    assert latest_contract(database, "mission-1") == contract
    assert reconcile(database).consistent is True


def test_contract_versions_are_explicitly_superseded(tmp_path: Path) -> None:
    database, _ = mission_database(tmp_path)
    first = _contract()
    record_step_contract(database, first)
    second = replace(
        first, version=2, instructions="write the corrected health artifact",
        supersedes_hash=first.content_hash())
    record_step_contract(database, second)
    assert latest_contract(database, "mission-1") == second
    assert len(database.all("mission_step_contracts", mission_id="mission-1")) == 2
    with pytest.raises(MissionStepContractError, match="immutable"):
        record_step_contract(database, replace(first, instructions="conflict"))


def test_contract_rejects_forged_controller_provider_and_escape() -> None:
    with pytest.raises(MissionStepContractError, match="relative"):
        _contract(target_relative_path="../outside.txt")
    with pytest.raises(MissionStepContractError, match="provider/model"):
        MissionStepContract.from_payload({**_contract().to_payload(), "provider": "glm"})


def test_recording_requires_exact_supervisor_and_known_binding(tmp_path: Path) -> None:
    database, _ = mission_database(tmp_path)
    with pytest.raises(MissionStepContractError, match="exact Supervisor"):
        record_step_contract(database, _contract(), controller="worker")
    with pytest.raises(MissionStepContractError, match="resource_id"):
        record_step_contract(database, _contract(resource_id="resource-missing"))


def test_worker_loop_waits_without_contract_and_does_not_build_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, _ = mission_database(tmp_path)
    import jarvis_foundry.runtime.worker_loop as worker_loop

    def fail_build(*args: object, **kwargs: object) -> None:
        raise AssertionError("worker stack must not be built while waiting")

    monkeypatch.setattr(worker_loop, "build_worker_stack", fail_build)
    report = run_cycle(
        database, FoundryPaths(tmp_path),
        WorkerRuntimeConfig(enabled=True, runtime_state_root=str(tmp_path / "state")),
        loop_id="wait-1", instance_id="worker-1")
    assert report["outcome"] == "WAITING"
    assert "no dispatchable mission" in str(report["reason"])
    assert not any(event.event_type == "mission.created" and
                   event.payload.get("mission_id") != "mission-1"
                   for event in database.events())


def test_worker_loop_blocks_tampered_contract_before_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, _ = mission_database(tmp_path)
    record_step_contract(database, _contract())
    with database.transaction() as connection:
        connection.execute(
            "UPDATE mission_step_contracts SET contract_hash = ? WHERE contract_id = ?",
            ("0" * 64, _contract().contract_id),
        )
    import jarvis_foundry.runtime.worker_loop as worker_loop
    monkeypatch.setattr(
        worker_loop, "build_worker_stack",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("tampered contract must not build a worker stack")),
    )
    report = run_cycle(
        database, FoundryPaths(tmp_path),
        WorkerRuntimeConfig(enabled=True, runtime_state_root=str(tmp_path / "state")),
        loop_id="blocked-1", instance_id="worker-1")
    assert report["outcome"] == "BLOCKED"
    assert "contract" in str(report["reason"]).lower()


def test_existing_contract_reaches_mission_loop_without_legacy_invention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, repository = mission_database(tmp_path)
    contract = _contract()
    record_step_contract(database, contract)
    import jarvis_foundry.runtime.worker_loop as worker_loop
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        worker_loop, "build_worker_stack",
        lambda *args, **kwargs: SimpleNamespace(bridge=object()),
    )

    class FakeMissionLoop:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["init"] = kwargs

        def run_until_settled(self, **kwargs: object) -> SimpleNamespace:
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                outcome="PASSED", step_id="step-health", run_id="run-1",
                verdict_id="verdict-1", reason=None)

    monkeypatch.setattr(worker_loop, "MissionLoop", FakeMissionLoop)
    report = run_cycle(
        database, FoundryPaths(tmp_path),
        WorkerRuntimeConfig(enabled=True, runtime_state_root=str(tmp_path / "state")),
        loop_id="dispatch-1", instance_id="worker-1")
    assert report["outcome"] == "PASSED"
    kwargs = captured["kwargs"]
    assert kwargs["mission_id"] == "mission-1"
    assert kwargs["step_id"] == contract.step_id
    assert kwargs["target"] == (repository / "artifacts" / "health.json").resolve()
    assert kwargs["expected_artifacts"] == contract.expected_artifacts
    assert kwargs["forbidden_paths"] == contract.forbidden_paths


def test_worker_loop_lease_remains_before_contract_dispatch(tmp_path: Path) -> None:
    database, _ = mission_database(tmp_path)
    record_step_contract(database, _contract())
    leases = LeaseManager(database)
    leases.acquire(LOOP_LEASE_KEY, "other-worker", 120)
    report = run_cycle(
        database, FoundryPaths(tmp_path),
        WorkerRuntimeConfig(enabled=True, runtime_state_root=str(tmp_path / "state")),
        loop_id="lease-1", instance_id="worker-1")
    assert report["outcome"] == "LEASE_UNAVAILABLE"
