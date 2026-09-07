from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.release import (
    read_current,
    read_previous,
    release_authority_report,
    write_pointer,
)
from jarvis_foundry.deployment.rollback_controller import (
    COMPLETE,
    FAULT_POINTS,
    FROZEN_SITE_CUSTOMIZE,
    CrossSchemaRollbackController,
    RollbackControllerError,
    RollbackInterruption,
    RollbackRefusal,
    RollbackRequest,
    inspect_database,
)
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.estop import EmergencyStop


PREDECESSOR_SHA = "a" * 40
FAILED_SHA = "b" * 40
PREDECESSOR_TREE = "1" * 40
FAILED_TREE = "2" * 40


def _identity(
    source_sha: str,
    *,
    release_id: str,
    git_tree: str,
    schema: int,
) -> dict[str, Any]:
    return {
        "source_sha": source_sha,
        "release_id": release_id,
        "git_commit": source_sha,
        "git_tree": git_tree,
        "schema": schema,
    }


PREDECESSOR_IDENTITY = _identity(
    PREDECESSOR_SHA,
    release_id="rel-predecessor-v38",
    git_tree=PREDECESSOR_TREE,
    schema=38,
)
FAILED_IDENTITY = _identity(
    FAILED_SHA,
    release_id="rel-failed-v40",
    git_tree=FAILED_TREE,
    schema=40,
)


def _write_release(
    install_root: Path,
    identity: Mapping[str, Any],
    *,
    previous_runtime: Mapping[str, Any] | None,
) -> None:
    directory = install_root / "releases" / str(identity["source_sha"])
    scripts = directory / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("test release python", encoding="utf-8")
    (scripts / "pythonw.exe").write_text("test release pythonw", encoding="utf-8")
    manifest = {
        "release_id": identity["release_id"],
        "source_sha": identity["source_sha"],
        "git_commit": identity["git_commit"],
        "git_tree": identity["git_tree"],
        "python_version": "3.13.0",
        "package_version": "0.1.0",
        "schema_compatibility": {
            "package_schema": identity["schema"],
            "opens_min": identity["schema"],
            "opens_max": identity["schema"],
        },
        "provenance": {
            "builder": "test",
            "source_repo": "disposable",
            "git_commit": identity["git_commit"],
            "git_tree": identity["git_tree"],
        },
        "previous_runtime": (
            dict(previous_runtime) if previous_runtime is not None else None
        ),
        "created_at": "2026-09-07T00:00:00Z",
    }
    (directory / "RELEASE.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _record_database_authority(
    database_path: Path,
    identity: Mapping[str, Any],
) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO release_manifests("
            "release_id, source_commit, artifact_hashes_json, dependencies_json,"
            "runtime_identity_json, parent_release_id, improvement_id, risk_class,"
            "migrations_json, verdicts_json, rollback_target, created_at,"
            "last_event_id, updated_at"
            ") VALUES (?, ?, '{}', '[]', ?, NULL, ?, 'R0', ?, '[]', NULL, ?, ?, ?)",
            (
                identity["release_id"],
                identity["source_sha"],
                json.dumps(dict(identity), sort_keys=True),
                f"deployment:{identity['source_sha']}",
                json.dumps([identity["schema"]]),
                "2026-09-07T00:00:00Z",
                f"authority:{identity['release_id']}",
                "2026-09-07T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT OR REPLACE INTO release_slots("
            "slot, release_id, updated_at, last_event_id"
            ") VALUES ('live', ?, ?, ?)",
            (
                identity["release_id"],
                "2026-09-07T00:00:00Z",
                f"live:{identity['release_id']}",
            ),
        )
        connection.commit()
    finally:
        connection.close()


class FakeRollbackHost:
    def __init__(self) -> None:
        self.scheduler_enabled = False
        self.processes: list[dict[str, Any]] = []
        self.start_count = 0
        self.enable_count = 0
        self.disable_count = 0
        self.termination_count = 0
        self.fail_enable_once = False

    def scheduler_contract(self, request: RollbackRequest) -> dict[str, Any]:
        return {
            "task_name": request.task_name,
            "owned": True,
            "enabled": self.scheduler_enabled,
            "launcher_arguments": [
                "--home",
                str(request.foundry_home),
                "--interval",
                "0.05",
                "--command-center-port",
                "18767",
            ],
            "heartbeat_interval_seconds": 0.05,
            "command_center_port": 18767,
        }

    def set_scheduler_enabled(
        self,
        request: RollbackRequest,
        enabled: bool,
    ) -> dict[str, Any]:
        self.scheduler_enabled = enabled
        if enabled:
            self.enable_count += 1
            if self.fail_enable_once:
                self.fail_enable_once = False
                raise RuntimeError("injected Scheduler enable failure")
        else:
            self.disable_count += 1
        return self.scheduler_contract(request)

    def owned_processes(
        self,
        request: RollbackRequest,
    ) -> list[dict[str, Any]]:
        return [dict(item) for item in self.processes]

    def validate_release_runtime(
        self,
        request: RollbackRequest,
        source_sha: str,
        *,
        foundry_home: Path | None,
    ) -> dict[str, Any]:
        identity = (
            PREDECESSOR_IDENTITY
            if source_sha == PREDECESSOR_SHA
            else FAILED_IDENTITY
        )
        if foundry_home is not None:
            assert inspect_database(
                foundry_home / "foundry.db",
                include_logical_digest=False,
            )["schema"] == identity["schema"]
        return {
            "ok": True,
            "source_sha": source_sha,
            "release_dir": str(request.install_root / "releases" / source_sha),
            "python_exe": str(
                request.install_root
                / "releases"
                / source_sha
                / ".venv"
                / "Scripts"
                / "python.exe"
            ),
            "runtime_identity": dict(identity),
            "legacy_identity": False,
        }

    def validate_release_launcher(
        self,
        request: RollbackRequest,
        source_sha: str,
    ) -> dict[str, Any]:
        assert source_sha == PREDECESSOR_SHA
        assert read_current(request.install_root) == PREDECESSOR_SHA
        assert inspect_database(
            request.foundry_home / "foundry.db",
            include_logical_digest=False,
        )["schema"] == 38
        return {
            "launcher": "OK",
            "identity": {
                "source_sha": source_sha,
                "schema": 38,
                "home": str(request.foundry_home),
            },
        }

    def start_predecessor(
        self,
        request: RollbackRequest,
        *,
        runtime_identity: Mapping[str, Any],
        scheduler: Mapping[str, Any],
        shim_dir: Path,
        marker_path: Path,
        log_path: Path,
    ) -> dict[str, Any]:
        assert scheduler["enabled"] is False
        assert runtime_identity["runtime_identity"] == PREDECESSOR_IDENTITY
        assert marker_path.is_file()
        assert (shim_dir / "sitecustomize.py").is_file()
        assert read_current(request.install_root) == PREDECESSOR_SHA
        assert inspect_database(
            request.foundry_home / "foundry.db",
            include_logical_digest=False,
        )["schema"] == 38
        self.start_count += 1
        process = {
            "kind": "supervisor",
            "pid": 4200 + self.start_count,
            "release_sha": PREDECESSOR_SHA,
            "creation_identity": {
                "pid": 4200 + self.start_count,
                "create_time": float(self.start_count),
            },
            "live": True,
        }
        self.processes = [process]
        return {
            "root_pid": process["pid"],
            "root_creation_identity": process["creation_identity"],
            "supervisor_processes": [process],
            "log_path": str(log_path),
            "started_at": "2026-09-07T00:00:00Z",
        }

    def terminate_started_process(
        self,
        request: RollbackRequest,
        process_record: Mapping[str, Any],
    ) -> dict[str, Any]:
        targeted = [item["pid"] for item in self.processes]
        self.processes = []
        self.termination_count += 1
        return {"targeted_pids": targeted, "remaining": []}

    def probe_predecessor(
        self,
        request: RollbackRequest,
        *,
        scheduler: Mapping[str, Any],
        expected_identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        assert {
            key: expected_identity[key]
            for key in PREDECESSOR_IDENTITY
        } == PREDECESSOR_IDENTITY
        assert len(self.processes) == 1
        assert EmergencyStop(
            request.foundry_home / "EMERGENCY_STOP").active()
        authority = release_authority_report(
            root=request.install_root,
            foundry_home=request.foundry_home,
        )
        assert authority["status"] == "CONSISTENT"
        return {
            "accepted": True,
            "health": {
                "status": "BLOCKED",
                "stop_active": True,
                "trust_state": "SUSPENDED",
                "installed_release_sha": PREDECESSOR_SHA,
                "runtime_release_sha": PREDECESSOR_SHA,
                "schema": {"live": 38, "package": 38},
                "release_authority": authority,
                "supervisor_count": 1,
            },
            "status": {
                "status": "BLOCKED",
                "trust_state": "SUSPENDED",
            },
            "command_center": {
                "stop_active": True,
                "schema": {"live": 38, "package": 38},
                "release_identity": {"current": PREDECESSOR_SHA},
            },
            "scheduler_enabled": scheduler["enabled"],
        }


@dataclass
class RollbackSandbox:
    request: RollbackRequest
    host: FakeRollbackHost
    initial_failed_report: dict[str, Any]
    snapshot_report: dict[str, Any]


def _rollback_sandbox(tmp_path: Path) -> RollbackSandbox:
    install_root = tmp_path / "JARVIS"
    home = tmp_path / "foundry"
    home.mkdir(parents=True)
    _write_release(
        install_root,
        PREDECESSOR_IDENTITY,
        previous_runtime=None,
    )
    _write_release(
        install_root,
        FAILED_IDENTITY,
        previous_runtime=PREDECESSOR_IDENTITY,
    )

    live_database = FoundryDatabase(home / "foundry.db")
    live_database.initialize(target=38)
    _record_database_authority(live_database.path, PREDECESSOR_IDENTITY)
    snapshot_path = tmp_path / "pre-migration-v38.db"
    live_database.backup_to(snapshot_path)
    snapshot_report = inspect_database(snapshot_path)

    live_database.initialize(target=40)
    _record_database_authority(live_database.path, FAILED_IDENTITY)
    write_pointer(install_root / "CURRENT", FAILED_SHA)
    write_pointer(install_root / "PREVIOUS", PREDECESSOR_SHA)
    EmergencyStop(home / "EMERGENCY_STOP").activate(
        "disposable cross-schema rollback rehearsal")
    initial_failed_report = inspect_database(live_database.path)
    snapshot_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    request = RollbackRequest(
        transaction_id="rollback-v40-to-v38",
        install_root=install_root,
        foundry_home=home,
        snapshot_path=snapshot_path,
        snapshot_sha256=snapshot_hash,
        failed_source_sha=FAILED_SHA,
        predecessor_source_sha=PREDECESSOR_SHA,
        task_name="JARVIS-Foundry-Supervisor-Rehearsal",
        health_timeout_seconds=1.0,
    )
    return RollbackSandbox(
        request=request,
        host=FakeRollbackHost(),
        initial_failed_report=initial_failed_report,
        snapshot_report=snapshot_report,
    )


def _assert_completed(sandbox: RollbackSandbox, report: Mapping[str, Any]) -> None:
    request = sandbox.request
    assert report["status"] == "ROLLBACK_COMPLETE"
    assert report["phase"] == COMPLETE
    assert read_current(request.install_root) == PREDECESSOR_SHA
    assert read_previous(request.install_root) == FAILED_SHA
    database = inspect_database(request.foundry_home / "foundry.db")
    assert database["schema"] == 38
    assert (
        database["logical"]["sha256"]
        == sandbox.snapshot_report["logical"]["sha256"]
    )
    assert database["authority"]["release_id"] == PREDECESSOR_IDENTITY["release_id"]
    assert database["effects"]["status"] == "CLEAR"
    assert database["effects"]["duplicate_starts"] == []
    assert database["work"]["status"] == "CLEAR"
    assert sandbox.host.scheduler_enabled is True
    assert len(sandbox.host.processes) == 1
    assert EmergencyStop(
        request.foundry_home / "EMERGENCY_STOP").active()
    assert not (
        request.install_root
        / "rollback-transactions"
        / "ACTIVE.json"
    ).exists()
    state = json.loads(
        (
            request.install_root
            / "rollback-transactions"
            / request.transaction_id
            / "STATE.json"
        ).read_text(encoding="utf-8")
    )
    assert state["phase"] == COMPLETE
    forensic = Path(state["evidence"]["forensic_database"]["path"])
    assert forensic.is_file()
    assert (
        inspect_database(forensic)["logical"]["sha256"]
        == sandbox.initial_failed_report["logical"]["sha256"]
    )


def test_cross_schema_rollback_completes_as_one_guarded_transaction(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)

    report = CrossSchemaRollbackController(
        sandbox.request,
        host=sandbox.host,
    ).run()

    _assert_completed(sandbox, report)
    assert sandbox.host.start_count == 1
    assert sandbox.host.enable_count == 1
    assert sandbox.host.termination_count == 0


@pytest.mark.parametrize("fault_point", FAULT_POINTS)
def test_every_required_interruption_resumes_without_duplicate_effects(
    tmp_path,
    fault_point,
):
    sandbox = _rollback_sandbox(tmp_path)
    injected = False

    def interrupt(point: str, state: Mapping[str, Any]) -> None:
        nonlocal injected
        if point == fault_point and not injected:
            injected = True
            raise RollbackInterruption(point)

    with pytest.raises(RollbackInterruption, match=fault_point):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
            fault_injector=interrupt,
        ).run()

    report = CrossSchemaRollbackController(
        sandbox.request,
        host=sandbox.host,
    ).run()

    _assert_completed(sandbox, report)
    assert injected is True
    if fault_point == "during_scheduler_reenable":
        assert sandbox.host.start_count == 2
        assert sandbox.host.termination_count == 1
        assert sandbox.host.disable_count >= 1
    else:
        assert sandbox.host.start_count == 1


def test_scheduler_enable_failure_is_contained_before_retry(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    sandbox.host.fail_enable_once = True

    with pytest.raises(
        RollbackControllerError,
        match="Scheduler enable failure",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert EmergencyStop(
        sandbox.request.foundry_home / "EMERGENCY_STOP").active()
    assert sandbox.host.scheduler_enabled is False
    assert sandbox.host.processes == []

    report = CrossSchemaRollbackController(
        sandbox.request,
        host=sandbox.host,
    ).run()

    _assert_completed(sandbox, report)
    assert sandbox.host.start_count == 2
    assert sandbox.host.termination_count >= 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda sandbox: EmergencyStop(
                sandbox.request.foundry_home / "EMERGENCY_STOP").clear(),
            "STOP/freeze",
        ),
        (
            lambda sandbox: setattr(
                sandbox.host, "scheduler_enabled", True),
            "Scheduler is enabled",
        ),
        (
            lambda sandbox: sandbox.host.processes.append({
                "kind": "supervisor",
                "pid": 9999,
                "release_sha": FAILED_SHA,
            }),
            "process lineage",
        ),
    ],
)
def test_entry_refusals_leave_no_transaction_files(
    tmp_path,
    mutate,
    message,
):
    sandbox = _rollback_sandbox(tmp_path)
    before_database = inspect_database(
        sandbox.request.foundry_home / "foundry.db")
    mutate(sandbox)

    with pytest.raises(RollbackRefusal, match=message):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert not (
        sandbox.request.install_root / "rollback-transactions"
    ).exists()
    assert read_current(sandbox.request.install_root) == FAILED_SHA
    assert read_previous(sandbox.request.install_root) == PREDECESSOR_SHA
    after_database = inspect_database(
        sandbox.request.foundry_home / "foundry.db")
    assert after_database["logical"] == before_database["logical"]


def test_unresolved_effect_refuses_before_transaction_creation(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    connection = sqlite3.connect(
        sandbox.request.foundry_home / "foundry.db")
    try:
        connection.execute(
            "INSERT INTO system_actions("
            "action_key, system_id, target_ref, payload_hash, kind, status,"
            "last_event_id, updated_at"
            ") VALUES ('effect-1', 'github', 'repo', ?, 'write', 'STARTED',"
            "'effect-event', '2026-09-07T00:00:00Z')",
            ("0" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RollbackRefusal, match="effects block rollback"):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert not (
        sandbox.request.install_root / "rollback-transactions"
    ).exists()
    assert sandbox.host.scheduler_enabled is False
    assert sandbox.host.processes == []


def test_effect_barrier_uses_latest_reconciliation_evidence(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    database = FoundryDatabase(
        sandbox.request.foundry_home / "foundry.db")
    action_key = "effect-ordering"
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO system_actions("
            "action_key, system_id, target_ref, payload_hash, kind, status,"
            "last_event_id, updated_at"
            ") VALUES (?, 'github', 'repo', ?, 'write', 'FROZEN',"
            "'effect-event', '2026-09-07T00:00:00Z')",
            (action_key, "0" * 64),
        )
    database.append(Event.create(
        "effect.reconciled",
        {"action_key": action_key, "provider_claim": {"status": "MATCH"}},
        source_type="supervisor",
        source_id="test",
        idempotency_key="effect-ordering.reconciled.old",
    ))
    database.append(Event.create(
        "effect.reconcile_mismatch",
        {"action_key": action_key, "provider_claim": {"status": "MISMATCH"}},
        source_type="supervisor",
        source_id="test",
        idempotency_key="effect-ordering.mismatch.new",
    ))

    effects = inspect_database(
        database.path, include_logical_digest=False)["effects"]
    assert effects["status"] == "BLOCKED"
    assert effects["unresolved"] == [action_key]

    database.append(Event.create(
        "effect.reconciled",
        {"action_key": action_key, "provider_claim": {"status": "MATCH"}},
        source_type="supervisor",
        source_id="test",
        idempotency_key="effect-ordering.reconciled.new",
    ))
    effects = inspect_database(
        database.path, include_logical_digest=False)["effects"]
    assert effects["status"] == "CLEAR"

    database.append(Event.create(
        "effect.reconcile_pending",
        {"action_key": action_key, "provider_state": "UNKNOWN"},
        source_type="supervisor",
        source_id="test",
        idempotency_key="effect-ordering.pending.latest",
    ))
    effects = inspect_database(
        database.path, include_logical_digest=False)["effects"]
    assert effects["status"] == "BLOCKED"

    database.append(Event.create(
        "effect.terminal_truth",
        {
            "action_key": action_key,
            "accepted": True,
            "reconciled": True,
            "outcome": "COMPLETED",
        },
        source_type="supervisor",
        source_id="test",
        idempotency_key="effect-ordering.terminal.latest",
    ))
    effects = inspect_database(
        database.path, include_logical_digest=False)["effects"]
    assert effects["status"] == "CLEAR"


def test_bad_snapshot_refuses_and_retains_frozen_containment(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    sandbox.request.snapshot_path.write_bytes(b"corrupt")

    with pytest.raises(RollbackRefusal, match="SHA-256"):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert EmergencyStop(
        sandbox.request.foundry_home / "EMERGENCY_STOP").active()
    assert sandbox.host.scheduler_enabled is False
    assert sandbox.host.processes == []
    state = json.loads(
        (
            sandbox.request.install_root
            / "rollback-transactions"
            / sandbox.request.transaction_id
            / "STATE.json"
        ).read_text(encoding="utf-8")
    )
    assert state["phase"] == "PRECONDITIONS_VERIFIED"


def test_conflicting_transaction_is_refused(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)

    def interrupt(point: str, state: Mapping[str, Any]) -> None:
        if point == "before_snapshot_validation":
            raise RollbackInterruption(point)

    with pytest.raises(RollbackInterruption):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
            fault_injector=interrupt,
        ).run()
    conflicting = RollbackRequest(
        **{
            **sandbox.request.to_record(),
            "transaction_id": "conflicting-rollback",
        }
    )

    with pytest.raises(RollbackRefusal, match="conflicting rollback"):
        CrossSchemaRollbackController(
            conflicting,
            host=sandbox.host,
        ).run()


def test_controller_refuses_same_schema_pair_without_creating_state(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    failed_manifest = (
        sandbox.request.install_root
        / "releases"
        / FAILED_SHA
        / "RELEASE.json"
    )
    payload = json.loads(failed_manifest.read_text(encoding="utf-8"))
    payload["schema_compatibility"] = {
        "package_schema": 38,
        "opens_min": 38,
        "opens_max": 38,
    }
    failed_manifest.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    live = FoundryDatabase(sandbox.request.foundry_home / "foundry.db")
    live.restore_from(sandbox.request.snapshot_path)
    _record_database_authority(live.path, {
        **FAILED_IDENTITY,
        "schema": 38,
    })

    with pytest.raises(RollbackRefusal, match="same-schema"):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert not (
        sandbox.request.install_root / "rollback-transactions"
    ).exists()


def test_runtime_bridge_refuses_to_launch_without_durable_stop(tmp_path):
    install_root = tmp_path / "JARVIS"
    foundry_home = tmp_path / "foundry"
    shim_dir = install_root / "rollback-runtime" / "active"
    shim_dir.mkdir(parents=True)
    foundry_home.mkdir()
    write_pointer(install_root / "CURRENT", PREDECESSOR_SHA)
    shim_path = shim_dir / "sitecustomize.py"
    shim_path.write_text(FROZEN_SITE_CUSTOMIZE, encoding="utf-8")
    marker_path = install_root / "ROLLBACK_RUNTIME.json"
    marker_path.write_text(
        json.dumps({
            "contract": "jarvis.cross-schema-rollback-runtime.v1",
            "status": "COMPLETE_FROZEN_RUNTIME",
            "source_sha": PREDECESSOR_SHA,
            "install_root": str(install_root.resolve()),
            "foundry_home": str(foundry_home.resolve()),
            "sitecustomize_sha256": hashlib.sha256(
                shim_path.read_bytes()
            ).hexdigest(),
        }),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({
        "JARVIS_HOME": str(foundry_home.resolve()),
        "JARVIS_INSTALL_ROOT": str(install_root.resolve()),
        "JARVIS_RELEASE_SHA": PREDECESSOR_SHA,
        "JARVIS_ROLLBACK_RUNTIME_MARKER": str(marker_path.resolve()),
        "PYTHONPATH": str(shim_dir.resolve()),
    })
    environment.pop("PYTHONHOME", None)

    result = subprocess.run(
        [sys.executable, "-c", "print('unsafe runtime started')"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=environment,
    )

    assert result.returncode == 2
    assert "durable STOP is not active" in result.stderr
    assert "unsafe runtime started" not in result.stdout
