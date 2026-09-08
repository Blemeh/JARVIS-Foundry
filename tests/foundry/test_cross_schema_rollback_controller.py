from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, replace
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
    WindowsRollbackHost,
    inspect_database,
    release_package_digest,
)
from jarvis_foundry.deployment.runtime_identity import ProcessQueryError
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
) -> Path:
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
    return directory


def _record_database_authority(
    database_path: Path,
    identity: Mapping[str, Any],
    *,
    previous_identity: Mapping[str, Any] | None = None,
    manifest_sha256: str | None = None,
) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO release_manifests("
            "release_id, source_commit, artifact_hashes_json, dependencies_json,"
            "runtime_identity_json, parent_release_id, improvement_id, risk_class,"
            "migrations_json, verdicts_json, rollback_target, created_at,"
            "last_event_id, updated_at"
            ") VALUES (?, ?, ?, '[]', ?, NULL, ?, 'R0', ?, '[]', NULL, ?, ?, ?)",
            (
                identity["release_id"],
                identity["source_sha"],
                json.dumps(
                    {"RELEASE.json": manifest_sha256}
                    if manifest_sha256 is not None else {},
                    sort_keys=True,
                ),
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
        if previous_identity is not None:
            connection.execute(
                "INSERT OR REPLACE INTO release_slots("
                "slot, release_id, updated_at, last_event_id"
                ") VALUES ('previous', ?, ?, ?)",
                (
                    previous_identity["release_id"],
                    "2026-09-07T00:00:00Z",
                    f"previous:{previous_identity['release_id']}",
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
        self.owned_termination_count = 0
        self.fail_enable_once = False
        self.spawn_wrapper_on_enable = False

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
            if self.spawn_wrapper_on_enable:
                self.processes.append({
                    "kind": "scheduler_wrapper",
                    "pid": 9900 + self.enable_count,
                    "creation_identity": {
                        "pid": 9900 + self.enable_count,
                        "create_time": float(self.enable_count),
                    },
                    "live": True,
                })
                self.spawn_wrapper_on_enable = False
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

    def build_continuity_baseline(
        self,
        request: RollbackRequest,
        *,
        source_sha: str,
        snapshot_path: Path,
        destination_path: Path,
        expected_schema: int,
    ) -> dict[str, Any]:
        assert source_sha == FAILED_SHA
        if destination_path.exists():
            destination_path.unlink()
        FoundryDatabase(snapshot_path).backup_to(destination_path)
        observed = FoundryDatabase(destination_path).initialize(
            target=expected_schema)
        report = inspect_database(
            destination_path, include_logical_digest=False)
        assert observed == expected_schema
        return {
            "schema": report["schema"],
            "quick_check": report["quick_check"],
            "integrity_check": report["integrity_check"],
            "path": str(destination_path.resolve()),
            "file_sha256": hashlib.sha256(
                destination_path.read_bytes()).hexdigest(),
            "source_sha": source_sha,
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
        targeted = [
            item["pid"] for item in self.processes
            if item.get("kind") == "supervisor"
        ]
        self.processes = [
            item for item in self.processes
            if item.get("kind") != "supervisor"
        ]
        self.termination_count += 1
        return {"targeted_pids": targeted, "remaining": []}

    def terminate_owned_processes(
        self,
        request: RollbackRequest,
        processes: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        keys = {
            (str(item.get("kind")), int(item.get("pid") or 0))
            for item in processes
        }
        targeted = [
            int(item["pid"]) for item in self.processes
            if (str(item.get("kind")), int(item.get("pid") or 0)) in keys
        ]
        self.processes = [
            item for item in self.processes
            if (str(item.get("kind")), int(item.get("pid") or 0)) not in keys
        ]
        self.owned_termination_count += 1
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
    predecessor_dir = _write_release(
        install_root,
        PREDECESSOR_IDENTITY,
        previous_runtime=None,
    )
    failed_dir = _write_release(
        install_root,
        FAILED_IDENTITY,
        previous_runtime=PREDECESSOR_IDENTITY,
    )

    live_database = FoundryDatabase(home / "foundry.db")
    live_database.initialize(target=38)
    _record_database_authority(
        live_database.path,
        PREDECESSOR_IDENTITY,
        manifest_sha256=hashlib.sha256(
            (predecessor_dir / "RELEASE.json").read_bytes()
        ).hexdigest(),
    )
    snapshot_path = tmp_path / "pre-migration-v38.db"
    live_database.backup_to(snapshot_path)
    snapshot_report = inspect_database(snapshot_path)

    live_database.initialize(target=40)
    _record_database_authority(
        live_database.path,
        FAILED_IDENTITY,
        previous_identity=PREDECESSOR_IDENTITY,
        manifest_sha256=hashlib.sha256(
            (failed_dir / "RELEASE.json").read_bytes()
        ).hexdigest(),
    )
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
        failed_manifest_sha256=hashlib.sha256(
            (failed_dir / "RELEASE.json").read_bytes()
        ).hexdigest(),
        failed_package_sha256=release_package_digest(
            failed_dir)["sha256"],
        predecessor_source_sha=PREDECESSOR_SHA,
        predecessor_manifest_sha256=hashlib.sha256(
            (predecessor_dir / "RELEASE.json").read_bytes()
        ).hexdigest(),
        predecessor_package_sha256=release_package_digest(
            predecessor_dir)["sha256"],
        task_name="JARVIS-Foundry-Supervisor-Rehearsal",
        health_timeout_seconds=1.0,
    )
    return RollbackSandbox(
        request=request,
        host=FakeRollbackHost(),
        initial_failed_report=initial_failed_report,
        snapshot_report=snapshot_report,
    )


def _refresh_sandbox_reports(sandbox: RollbackSandbox) -> None:
    sandbox.request = replace(
        sandbox.request,
        snapshot_sha256=hashlib.sha256(
            sandbox.request.snapshot_path.read_bytes()
        ).hexdigest(),
    )
    sandbox.snapshot_report = inspect_database(
        sandbox.request.snapshot_path)
    sandbox.initial_failed_report = inspect_database(
        sandbox.request.foundry_home / "foundry.db")


def _seed_runtime_control_history(
    sandbox: RollbackSandbox,
    *,
    terminal: bool,
) -> tuple[str, str]:
    request_id = "already-resolved-owner-restart"
    instance_id = "supervisor-failed-activation"
    shared_events = [
        Event.create(
            "runtime.control.requested",
            {
                "action": "RESTART",
                "expected_release": PREDECESSOR_SHA,
                "expires_at": "2026-09-08T00:00:01Z",
                "request_id": request_id,
                "requested_at": "2026-09-08T00:00:00Z",
                "requested_by": "owner",
            },
            source_type="owner",
            source_id="owner",
            idempotency_key=f"runtime.control.requested:{request_id}",
            occurred_at="2026-09-08T00:00:00Z",
        ),
    ]
    if terminal:
        shared_events.append(Event.create(
            "runtime.control.resolved",
            {
                "action": "RESTART",
                "attempt": 0,
                "instance_id": "supervisor-predecessor",
                "phase": "started",
                "release_id": PREDECESSOR_SHA,
                "request_id": request_id,
                "request_status": None,
                "status": "RUNNING",
            },
            source_type="supervisor",
            source_id="supervisor-predecessor",
            idempotency_key=(
                f"runtime.control.resolved:{request_id}:started:RUNNING"
            ),
            occurred_at="2026-09-08T00:00:00.500000Z",
        ))
    for path in (
        sandbox.request.snapshot_path,
        sandbox.request.foundry_home / "foundry.db",
    ):
        FoundryDatabase(path).append_events(shared_events)
    failed_database = FoundryDatabase(
        sandbox.request.foundry_home / "foundry.db")
    failed_database.append(Event.create(
        "runtime.control.claiming",
        {
            "creation_identity": {
                "pid": 9001,
                "create_time": 1,
                "exe": r"C:\Python\python.exe",
            },
            "generation": 1,
            "instance_id": instance_id,
            "pid": 9001,
            "release_id": FAILED_SHA,
            "request_id": None,
        },
        source_type="supervisor",
        source_id=instance_id,
        idempotency_key=f"runtime.control.claiming:{instance_id}:1",
        occurred_at="2026-09-08T00:01:00Z",
    ))
    failed_database.append(Event.create(
        "runtime.control.resolved",
        {
            "action": "RESTART",
            "attempt": 0,
            "creation_identity": {
                "pid": 9001,
                "create_time": 1,
                "exe": r"C:\Python\python.exe",
            },
            "instance_id": instance_id,
            "phase": "stale_request",
            "pid": 9001,
            "reason": "owner request expired before application",
            "release_id": FAILED_SHA,
            "request_id": request_id,
            "request_status": "STALE",
            "status": "RUNNING",
        },
        source_type="supervisor",
        source_id=instance_id,
        idempotency_key=(
            f"runtime.control.resolved:{request_id}:stale_request:RUNNING"
        ),
        occurred_at="2026-09-08T00:02:00Z",
    ))
    _refresh_sandbox_reports(sandbox)
    return request_id, instance_id


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
    assert state["resume_authorized"] is True
    runtime_marker = json.loads(
        (request.install_root / "ROLLBACK_RUNTIME.json").read_text(
            encoding="utf-8"))
    assert runtime_marker["status"] == "COMPLETE_FROZEN_RUNTIME"
    assert state["evidence"]["runtime_bridge_completion"] == runtime_marker
    assert (
        state["evidence"]["completion_report"]["status"]
        == "ROLLBACK_COMPLETE"
    )
    assert Path(runtime_marker["transaction_state_path"]).resolve() == (
        request.install_root
        / "rollback-transactions"
        / request.transaction_id
        / "STATE.json"
    ).resolve()
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


def test_runtime_bridge_completion_follows_durable_complete(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    controller = CrossSchemaRollbackController(
        sandbox.request,
        host=sandbox.host,
    )
    original = controller._mark_runtime_bridge_complete
    observed_phases: list[str] = []

    def guarded_completion():
        state = json.loads(controller.state_path.read_text(encoding="utf-8"))
        observed_phases.append(str(state["phase"]))
        assert state["phase"] == COMPLETE
        assert state["resume_authorized"] is True
        assert not controller.active_path.exists()
        return original()

    monkeypatch.setattr(
        controller, "_mark_runtime_bridge_complete", guarded_completion)

    report = controller.run()

    _assert_completed(sandbox, report)
    assert observed_phases == [COMPLETE]


def test_runtime_bridge_publish_failure_blocks_resume_and_reenters(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    controller = CrossSchemaRollbackController(
        sandbox.request,
        host=sandbox.host,
    )

    def fail_publication():
        raise OSError("injected final marker publication failure")

    monkeypatch.setattr(
        controller, "_mark_runtime_bridge_complete", fail_publication)

    with pytest.raises(
        RollbackControllerError,
        match="final marker publication failure",
    ):
        controller.run()

    marker_path = (
        sandbox.request.install_root / "ROLLBACK_RUNTIME.json"
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["status"] == "ACTIVE"
    state = json.loads(controller.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "PREDECESSOR_VALIDATED"
    assert state["resume_authorized"] is False
    assert "completion_report" not in state["evidence"]
    assert "runtime_bridge_completion" not in state["evidence"]
    assert sandbox.host.scheduler_enabled is False
    assert sandbox.host.processes == []

    EmergencyStop(
        sandbox.request.foundry_home / "EMERGENCY_STOP").clear()
    shim_dir = Path(marker["sitecustomize_path"]).parent
    environment = os.environ.copy()
    environment.update({
        "JARVIS_HOME": str(sandbox.request.foundry_home),
        "JARVIS_INSTALL_ROOT": str(sandbox.request.install_root),
        "JARVIS_RELEASE_SHA": PREDECESSOR_SHA,
        "JARVIS_ROLLBACK_RUNTIME_MARKER": str(marker_path),
        "PYTHONPATH": str(shim_dir),
    })
    environment.pop("PYTHONHOME", None)
    result = subprocess.run(
        [sys.executable, "-c", "print('unsafe owner resume started')"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=environment,
    )
    assert result.returncode == 2
    assert "durable STOP is not active" in result.stderr
    assert marker_path.is_file()

    EmergencyStop(
        sandbox.request.foundry_home / "EMERGENCY_STOP").activate(
            "resume finalization test containment")
    report = CrossSchemaRollbackController(
        sandbox.request,
        host=sandbox.host,
    ).run()
    _assert_completed(sandbox, report)


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


def test_uncertain_scheduler_enable_contains_exact_wrapper_process(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    sandbox.host.spawn_wrapper_on_enable = True
    injected = False

    def interrupt(point: str, state: Mapping[str, Any]) -> None:
        nonlocal injected
        if point == "during_scheduler_reenable" and not injected:
            injected = True
            raise RollbackInterruption(point)

    with pytest.raises(
        RollbackInterruption,
        match="during_scheduler_reenable",
    ):
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
    assert sandbox.host.owned_termination_count >= 1


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


def test_windows_owned_process_census_refuses_unknown_provider(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)

    def fail_query():
        raise ProcessQueryError("CIM and WMIC unavailable")

    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.query_processes_strict",
        fail_query,
    )

    with pytest.raises(
        RollbackRefusal,
        match="authoritative owned-process census failed",
    ):
        WindowsRollbackHost().owned_processes(sandbox.request)


def test_windows_owned_process_census_includes_scheduler_wrapper(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    wrapper = sandbox.request.install_root / "bin" / (
        "jarvis-supervisor-headless.vbs")
    executable = r"C:\Windows\System32\wscript.exe"
    raw = {
        "ProcessId": 3210,
        "ParentProcessId": 100,
        "ExecutablePath": executable,
        "CommandLine": (
            f'"{executable}" //B //Nologo "{wrapper}" '
            f'--home "{sandbox.request.foundry_home}" '
            "--interval 5 --command-center-port 8767"
        ),
    }
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.query_processes_strict",
        lambda: [raw],
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.pid_alive",
        lambda pid: pid == 3210,
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.process_creation_identity",
        lambda pid: {
            "pid": pid,
            "create_time": 123456,
            "exe": executable,
        },
    )

    assert WindowsRollbackHost().owned_processes(sandbox.request) == [{
        "kind": "scheduler_wrapper",
        "pid": 3210,
        "executable": executable,
        "command_line": raw["CommandLine"],
        "creation_identity": {
            "pid": 3210,
            "create_time": 123456,
            "exe": executable,
        },
        "live": True,
    }]


def test_windows_owned_process_census_uses_wrapper_command_when_exe_missing(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    wrapper = sandbox.request.install_root / "bin" / (
        "jarvis-supervisor-headless.vbs")
    executable = r"C:\Windows\System32\wscript.exe"
    raw = {
        "ProcessId": 3212,
        "ParentProcessId": 100,
        "ExecutablePath": None,
        "CommandLine": (
            f'"{executable}" //B //Nologo "{wrapper}" '
            f'--home "{sandbox.request.foundry_home}"'
        ),
    }
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.query_processes_strict",
        lambda: [raw],
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.pid_alive",
        lambda pid: pid == 3212,
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.process_creation_identity",
        lambda pid: {
            "pid": pid,
            "create_time": 123458,
            "exe": executable,
        },
    )

    observed = WindowsRollbackHost().owned_processes(sandbox.request)

    assert len(observed) == 1
    assert observed[0]["kind"] == "scheduler_wrapper"
    assert observed[0]["pid"] == 3212


def test_windows_owned_process_census_refuses_wrapper_creation_exe_mismatch(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    wrapper = sandbox.request.install_root / "bin" / (
        "jarvis-supervisor-headless.vbs")
    executable = r"C:\Windows\System32\wscript.exe"
    raw = {
        "ProcessId": 3213,
        "ParentProcessId": 100,
        "ExecutablePath": None,
        "CommandLine": (
            f'"{executable}" //B //Nologo "{wrapper}" '
            f'--home "{sandbox.request.foundry_home}"'
        ),
    }
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.query_processes_strict",
        lambda: [raw],
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.pid_alive",
        lambda pid: pid == 3213,
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.process_creation_identity",
        lambda pid: {
            "pid": pid,
            "create_time": 123459,
            "exe": r"C:\Windows\System32\cscript.exe",
        },
    )

    with pytest.raises(
        RollbackRefusal,
        match="wrapper executable identity is uncertain",
    ):
        WindowsRollbackHost().owned_processes(sandbox.request)


def test_windows_owned_process_census_refuses_live_wrapper_without_identity(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    wrapper = sandbox.request.install_root / "bin" / (
        "jarvis-supervisor-headless.vbs")
    executable = r"C:\Windows\System32\wscript.exe"
    raw = {
        "ProcessId": 3211,
        "ParentProcessId": 100,
        "ExecutablePath": executable,
        "CommandLine": (
            f'"{executable}" //B //Nologo "{wrapper}" '
            f'--home "{sandbox.request.foundry_home}"'
        ),
    }
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.query_processes_strict",
        lambda: [raw],
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.pid_alive",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.process_creation_identity",
        lambda pid: {"pid": pid},
    )

    with pytest.raises(
        RollbackRefusal,
        match="creation identity is unavailable",
    ):
        WindowsRollbackHost().owned_processes(sandbox.request)


def test_windows_owned_process_census_refuses_corrupt_adapter_registry(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    (
        sandbox.request.foundry_home / "adapter_worker_pids.json"
    ).write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.query_processes_strict",
        lambda: [],
    )

    with pytest.raises(RollbackRefusal, match="unreadable JSON record"):
        WindowsRollbackHost().owned_processes(sandbox.request)


def test_windows_owned_process_census_refuses_live_adapter_without_identity(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    (
        sandbox.request.foundry_home / "adapter_worker_pids.json"
    ).write_text(
        json.dumps({"adapter-run": {"pid": 4321}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.query_processes_strict",
        lambda: [],
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.pid_alive",
        lambda pid: pid == 4321,
    )

    with pytest.raises(
        RollbackRefusal,
        match="stored creation identity is unavailable",
    ):
        WindowsRollbackHost().owned_processes(sandbox.request)


def test_windows_owned_process_census_refuses_live_adapter_identity_change(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    (
        sandbox.request.foundry_home / "adapter_worker_pids.json"
    ).write_text(
        json.dumps({
            "adapter-run": {
                "pid": 4322,
                "creation_identity": {
                    "pid": 4322,
                    "create_time": 10,
                    "exe": r"C:\Python\python.exe",
                },
                "owned_descendants": [4322],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.query_processes_strict",
        lambda: [],
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.pid_alive",
        lambda pid: pid == 4322,
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.process_creation_identity",
        lambda pid: {
            "pid": pid,
            "create_time": 10,
            "exe": r"C:\OtherPython\python.exe",
        },
    )

    with pytest.raises(
        RollbackRefusal,
        match="creation identity changed during census",
    ):
        WindowsRollbackHost().owned_processes(sandbox.request)


def test_windows_owned_process_census_refuses_dead_adapter_live_descendant(
    tmp_path,
    monkeypatch,
):
    sandbox = _rollback_sandbox(tmp_path)
    (
        sandbox.request.foundry_home / "adapter_worker_pids.json"
    ).write_text(
        json.dumps({
            "adapter-run": {
                "pid": 5000,
                "creation_identity": {
                    "pid": 5000,
                    "create_time": 10,
                    "exe": r"C:\Python\python.exe",
                },
                "owned_descendants": [5000, 5001],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.query_processes_strict",
        lambda: [{
            "ProcessId": 5001,
            "ParentProcessId": 5000,
            "ExecutablePath": r"C:\Tools\child.exe",
            "CommandLine": r"C:\Tools\child.exe",
        }],
    )
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller.pid_alive",
        lambda pid: pid == 5001,
    )

    with pytest.raises(
        RollbackRefusal,
        match="dead adapter root retains live or uncertain descendants",
    ):
        WindowsRollbackHost().owned_processes(sandbox.request)


def test_scheduler_contract_refuses_additional_action(tmp_path, monkeypatch):
    sandbox = _rollback_sandbox(tmp_path)
    wrapper = sandbox.request.install_root / "bin" / (
        "jarvis-supervisor-headless.vbs")
    home = sandbox.request.foundry_home
    namespace = WindowsRollbackHost.TASK_NAMESPACE.strip("{}")
    task_xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task xmlns="{namespace}">
  <RegistrationInfo><Description>JARVIS Foundry production</Description></RegistrationInfo>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <Enabled>false</Enabled>
  </Settings>
  <Actions>
    <Exec>
      <Command>C:\\Windows\\System32\\wscript.exe</Command>
      <Arguments>//B //Nologo "{wrapper}" --home "{home}" --command-center-port 8767</Arguments>
      <WorkingDirectory>{home}</WorkingDirectory>
    </Exec>
    <Exec>
      <Command>C:\\Windows\\System32\\cmd.exe</Command>
      <Arguments>/c exit 0</Arguments>
      <WorkingDirectory>{home}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""
    monkeypatch.setattr(
        "jarvis_foundry.deployment.rollback_controller._run_schtasks",
        lambda *args: subprocess.CompletedProcess(
            args, 0, stdout=task_xml, stderr=""),
    )

    with pytest.raises(
        RollbackRefusal,
        match="exactly one Exec action",
    ):
        WindowsRollbackHost().scheduler_contract(sandbox.request)


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


def test_snapshot_refuses_completed_effect_identity_loss(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    connection = sqlite3.connect(
        sandbox.request.foundry_home / "foundry.db")
    try:
        connection.execute(
            "INSERT INTO system_actions("
            "action_key, system_id, target_ref, payload_hash, kind, status,"
            "last_event_id, updated_at"
            ") VALUES ('post-snapshot-effect', 'github', 'repo', ?, "
            "'write', 'COMPLETED', 'post-snapshot-event', "
            "'2026-09-08T00:00:00Z')",
            ("3" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RollbackRefusal,
        match="discard post-snapshot effect/work idempotency evidence",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert read_current(sandbox.request.install_root) == FAILED_SHA
    assert inspect_database(
        sandbox.request.foundry_home / "foundry.db"
    )["schema"] == 40


def test_snapshot_refuses_completed_work_identity_loss(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    connection = sqlite3.connect(
        sandbox.request.foundry_home / "foundry.db")
    try:
        connection.execute(
            "INSERT INTO missions("
            "mission_id, objective, resource_id, state, acceptance_json, "
            "next_action, last_event_id, updated_at, created_from"
            ") VALUES ('post-snapshot-mission', 'completed after snapshot', "
            "NULL, 'COMPLETE', '[]', NULL, 'post-snapshot-mission-event', "
            "'2026-09-08T00:00:00Z', 'test')"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RollbackRefusal,
        match="discard post-snapshot effect/work idempotency evidence",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert read_current(sandbox.request.install_root) == FAILED_SHA
    assert inspect_database(
        sandbox.request.foundry_home / "foundry.db"
    )["schema"] == 40


def test_snapshot_refuses_v40_initiative_authority_loss(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    connection = sqlite3.connect(
        sandbox.request.foundry_home / "foundry.db")
    try:
        connection.execute(
            "INSERT INTO initiative_cycles("
            "cycle_id, stage, status, started_at, completed_at, "
            "last_event_id, updated_at"
            ") VALUES ('post-snapshot-cycle', 'CLOSED', 'COMPLETED', "
            "'2026-09-08T00:00:00Z', '2026-09-08T00:01:00Z', "
            "'post-snapshot-cycle-event', '2026-09-08T00:01:00Z')"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RollbackRefusal,
        match="discard post-snapshot effect/work idempotency evidence",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert inspect_database(
        sandbox.request.foundry_home / "foundry.db"
    )["schema"] == 40


def test_snapshot_refuses_research_queue_authority_loss(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    connection = sqlite3.connect(
        sandbox.request.foundry_home / "foundry.db")
    try:
        connection.execute(
            "INSERT INTO research_queue("
            "item_id, reason, evidence_json, status, candidate_id, "
            "completed_at, last_event_id, updated_at"
            ") VALUES ('post-snapshot-research', 'test', '[]', 'COMPLETED', "
            "'candidate-after-snapshot', '2026-09-08T00:01:00Z', "
            "'post-snapshot-research-event', '2026-09-08T00:01:00Z')"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RollbackRefusal,
        match="discard post-snapshot effect/work idempotency evidence",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()


def test_snapshot_refuses_provider_dispatch_attempt_evidence_loss(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    database = FoundryDatabase(
        sandbox.request.foundry_home / "foundry.db")
    database.append(Event.create(
        "provider.dispatch_attempted",
        {
            "run_id": "post-snapshot-run",
            "attempt_generation": 1,
            "attempt_ordinal": 1,
            "provider": "provider-a",
            "model": "model-a",
            "package_id": "package-a",
            "package_hash": "4" * 64,
            "attempt_id": "attempt-a",
        },
        source_type="worker",
        source_id="test",
        idempotency_key=(
            "provider.dispatch_attempted:post-snapshot-run:"
            "1:1:provider-a:model-a"
        ),
    ))

    with pytest.raises(
        RollbackRefusal,
        match="discard post-snapshot effect/work idempotency evidence",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()


def test_snapshot_accepts_exact_failed_activation_release_suffix(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    database = FoundryDatabase(
        sandbox.request.foundry_home / "foundry.db")
    database.append(Event.create(
        "release.manifest_recorded",
        {
            "artifact_hashes": {
                "RELEASE.json": sandbox.request.failed_manifest_sha256,
            },
            "authority_kind": "immutable_installed_release",
            "dependencies": [],
            "improvement_id": f"deployment:{FAILED_SHA}",
            "migrations": [40],
            "parent_release_id": None,
            "release_id": FAILED_IDENTITY["release_id"],
            "risk_class": "R0",
            "rollback_target": None,
            "runtime_identity": FAILED_IDENTITY,
            "source_commit": FAILED_SHA,
            "verdicts": [],
        },
        source_type="supervisor",
        source_id="deployment-authority",
        idempotency_key=(
            f"release.manifest_recorded:{FAILED_IDENTITY['release_id']}"
        ),
    ))
    database.append(Event.create(
        "release.slot_changed",
        {
            "controller": "supervisor",
            "previous_release_id": None,
            "reason": "test activation",
            "release_id": PREDECESSOR_IDENTITY["release_id"],
            "slot": "previous",
        },
        source_type="supervisor",
        source_id="deployment-authority",
        idempotency_key="release.slot_changed:test-previous",
    ))
    database.append(Event.create(
        "release.slot_changed",
        {
            "controller": "supervisor",
            "previous_release_id": PREDECESSOR_IDENTITY["release_id"],
            "reason": "test activation",
            "release_id": FAILED_IDENTITY["release_id"],
            "slot": "live",
        },
        source_type="supervisor",
        source_id="deployment-authority",
        idempotency_key="release.slot_changed:test-live",
    ))
    _refresh_sandbox_reports(sandbox)

    report = CrossSchemaRollbackController(
        sandbox.request,
        host=sandbox.host,
    ).run()

    _assert_completed(sandbox, report)
    continuity = report["snapshot"]["rollback_continuity"]
    assert (
        continuity["event_continuity"]["suffix_event_types"]
        == {
            "release.manifest_recorded": 1,
            "release.slot_changed": 2,
        }
    )


def test_snapshot_refuses_protected_release_slot_loss(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    connection = sqlite3.connect(
        sandbox.request.foundry_home / "foundry.db")
    try:
        connection.execute(
            "INSERT INTO release_slots("
            "slot, release_id, updated_at, last_event_id"
            ") VALUES ('candidate', ?, ?, ?)",
            (
                FAILED_IDENTITY["release_id"],
                "2026-09-08T00:00:00Z",
                "candidate-after-snapshot",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RollbackRefusal,
        match="protected release slots",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()


def test_snapshot_refuses_unbound_runtime_control_resolution(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)
    FoundryDatabase(
        sandbox.request.foundry_home / "foundry.db"
    ).append(Event.create(
        "runtime.control.resolved",
        {
            "action": "RESTART",
            "attempt": 0,
            "instance_id": "unbound-supervisor",
            "phase": "stale_request",
            "release_id": FAILED_SHA,
            "request_id": "new-owner-request",
            "request_status": "STALE",
            "status": "RUNNING",
        },
        source_type="supervisor",
        source_id="unbound-supervisor",
        idempotency_key="runtime.control.resolved:new-owner-request:stale",
    ))

    with pytest.raises(
        RollbackRefusal,
        match="no exact Supervisor claim",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()


def test_snapshot_accepts_redundant_stale_resolution_after_terminal_request(
    tmp_path,
):
    sandbox = _rollback_sandbox(tmp_path)
    request_id, _ = _seed_runtime_control_history(
        sandbox, terminal=True)

    report = CrossSchemaRollbackController(
        sandbox.request,
        host=sandbox.host,
    ).run()

    _assert_completed(sandbox, report)
    assert report["snapshot"]["rollback_continuity"][
        "event_continuity"
    ]["stale_terminal_request_ids"] == [request_id]


def test_snapshot_refuses_stale_resolution_without_prior_terminal_request(
    tmp_path,
):
    sandbox = _rollback_sandbox(tmp_path)
    _seed_runtime_control_history(sandbox, terminal=False)

    with pytest.raises(
        RollbackRefusal,
        match="unproven runtime-control resolution",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()


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


def test_snapshot_is_pinned_before_external_source_changes(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)

    def interrupt(point: str, state: Mapping[str, Any]) -> None:
        if point == "after_forensic_capture":
            raise RollbackInterruption(point)

    with pytest.raises(RollbackInterruption, match="after_forensic_capture"):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
            fault_injector=interrupt,
        ).run()

    sandbox.request.snapshot_path.write_bytes(b"external snapshot replaced")
    report = CrossSchemaRollbackController(
        sandbox.request,
        host=sandbox.host,
    ).run()

    _assert_completed(sandbox, report)
    assert report["snapshot"]["owned_copy"] is True
    assert Path(report["snapshot"]["path"]).is_relative_to(
        sandbox.request.install_root / "rollback-transactions")


def test_owned_snapshot_tamper_refuses_before_database_restore(tmp_path):
    sandbox = _rollback_sandbox(tmp_path)

    def interrupt(point: str, state: Mapping[str, Any]) -> None:
        if point == "after_forensic_capture":
            raise RollbackInterruption(point)

    with pytest.raises(RollbackInterruption, match="after_forensic_capture"):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
            fault_injector=interrupt,
        ).run()
    state_path = (
        sandbox.request.install_root
        / "rollback-transactions"
        / sandbox.request.transaction_id
        / "STATE.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    Path(state["evidence"]["snapshot"]["path"]).write_bytes(
        b"tampered transaction snapshot")

    with pytest.raises(
        RollbackRefusal,
        match="transaction-owned rollback snapshot SHA-256 changed",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert read_current(sandbox.request.install_root) == FAILED_SHA
    assert inspect_database(
        sandbox.request.foundry_home / "foundry.db"
    )["schema"] == 40


@pytest.mark.parametrize("target", ["manifest", "package"])
def test_predecessor_external_anchor_refuses_tampering(tmp_path, target):
    sandbox = _rollback_sandbox(tmp_path)
    predecessor = (
        sandbox.request.install_root / "releases" / PREDECESSOR_SHA
    )
    if target == "manifest":
        manifest = predecessor / "RELEASE.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["package_version"] = "tampered"
        manifest.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        message = "manifest SHA-256"
    else:
        (
            predecessor / ".venv" / "Scripts" / "python.exe"
        ).write_text("tampered release python", encoding="utf-8")
        message = "package SHA-256"

    with pytest.raises(RollbackRefusal, match=message):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert not (
        sandbox.request.install_root / "rollback-transactions"
    ).exists()


@pytest.mark.parametrize("target", ["manifest", "package"])
def test_failed_release_external_anchor_refuses_tampering(tmp_path, target):
    sandbox = _rollback_sandbox(tmp_path)
    failed = sandbox.request.install_root / "releases" / FAILED_SHA
    if target == "manifest":
        manifest = failed / "RELEASE.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["package_version"] = "tampered"
        manifest.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        message = "manifest SHA-256"
    else:
        (
            failed / ".venv" / "Scripts" / "python.exe"
        ).write_text("tampered failed release python", encoding="utf-8")
        message = "package SHA-256"

    with pytest.raises(RollbackRefusal, match=message):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
        ).run()

    assert not (
        sandbox.request.install_root / "rollback-transactions"
    ).exists()


def test_failed_release_package_is_revalidated_before_baseline_migration(
    tmp_path,
):
    sandbox = _rollback_sandbox(tmp_path)
    mutated = False

    def mutate_before_snapshot(
        point: str,
        state: Mapping[str, Any],
    ) -> None:
        nonlocal mutated
        if point == "before_snapshot_validation" and not mutated:
            mutated = True
            (
                sandbox.request.install_root
                / "releases"
                / FAILED_SHA
                / ".venv"
                / "Scripts"
                / "python.exe"
            ).write_text(
                "tampered after preconditions",
                encoding="utf-8",
            )

    with pytest.raises(
        RollbackRefusal,
        match="package SHA-256",
    ):
        CrossSchemaRollbackController(
            sandbox.request,
            host=sandbox.host,
            fault_injector=mutate_before_snapshot,
        ).run()

    assert mutated is True
    state = json.loads(
        (
            sandbox.request.install_root
            / "rollback-transactions"
            / sandbox.request.transaction_id
            / "STATE.json"
        ).read_text(encoding="utf-8")
    )
    assert state["phase"] == "PRECONDITIONS_VERIFIED"
    assert not (
        sandbox.request.install_root
        / "rollback-transactions"
        / sandbox.request.transaction_id
        / "evidence"
        / f"snapshot-migrated-{FAILED_SHA[:12]}.db"
    ).exists()


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
    predecessor_dir = (
        sandbox.request.install_root / "releases" / PREDECESSOR_SHA
    )
    sandbox.request = replace(
        sandbox.request,
        failed_manifest_sha256=hashlib.sha256(
            failed_manifest.read_bytes()
        ).hexdigest(),
        failed_package_sha256=release_package_digest(
            sandbox.request.install_root
            / "releases"
            / FAILED_SHA
        )["sha256"],
        predecessor_manifest_sha256=hashlib.sha256(
            (predecessor_dir / "RELEASE.json").read_bytes()
        ).hexdigest(),
        predecessor_package_sha256=release_package_digest(
            predecessor_dir)["sha256"],
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
            "status": "ACTIVE",
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


def test_completed_runtime_bridge_retires_on_owner_resume(tmp_path):
    install_root = tmp_path / "JARVIS"
    foundry_home = tmp_path / "foundry"
    shim_dir = install_root / "rollback-runtime" / "active"
    shim_dir.mkdir(parents=True)
    foundry_home.mkdir()
    write_pointer(install_root / "CURRENT", PREDECESSOR_SHA)
    shim_path = shim_dir / "sitecustomize.py"
    shim_path.write_text(FROZEN_SITE_CUSTOMIZE, encoding="utf-8")
    transaction_id = "completed-resume"
    state_path = (
        install_root
        / "rollback-transactions"
        / transaction_id
        / "STATE.json"
    )
    state_path.parent.mkdir(parents=True)
    marker_path = install_root / "ROLLBACK_RUNTIME.json"
    completed_marker = {
        "contract": "jarvis.cross-schema-rollback-runtime.v1",
        "status": "COMPLETE_FROZEN_RUNTIME",
        "transaction_id": transaction_id,
        "source_sha": PREDECESSOR_SHA,
        "install_root": str(install_root.resolve()),
        "foundry_home": str(foundry_home.resolve()),
        "transaction_state_path": str(state_path.resolve()),
        "sitecustomize_sha256": hashlib.sha256(
            shim_path.read_bytes()
        ).hexdigest(),
    }
    state_path.write_text(
        json.dumps({
            "contract": "jarvis.cross-schema-rollback.v1",
            "transaction_id": transaction_id,
            "phase": "COMPLETE",
            "resume_authorized": True,
            "request": {
                "install_root": str(install_root.resolve()),
                "foundry_home": str(foundry_home.resolve()),
                "predecessor_source_sha": PREDECESSOR_SHA,
            },
            "evidence": {
                "runtime_bridge_completion": completed_marker,
                "completion_report": {
                    "status": "ROLLBACK_COMPLETE",
                    "phase": "COMPLETE",
                },
            },
        }),
        encoding="utf-8",
    )
    marker_path.write_text(
        json.dumps(completed_marker),
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
        [sys.executable, "-c", "print('owner resume started')"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=environment,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "owner resume started"
    assert not marker_path.exists()


def test_completed_runtime_bridge_refuses_resume_before_durable_complete(
    tmp_path,
):
    install_root = tmp_path / "JARVIS"
    foundry_home = tmp_path / "foundry"
    shim_dir = install_root / "rollback-runtime" / "active"
    shim_dir.mkdir(parents=True)
    foundry_home.mkdir()
    write_pointer(install_root / "CURRENT", PREDECESSOR_SHA)
    shim_path = shim_dir / "sitecustomize.py"
    shim_path.write_text(FROZEN_SITE_CUSTOMIZE, encoding="utf-8")
    transaction_id = "incomplete-resume"
    state_path = (
        install_root
        / "rollback-transactions"
        / transaction_id
        / "STATE.json"
    )
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({
            "contract": "jarvis.cross-schema-rollback.v1",
            "transaction_id": transaction_id,
            "phase": "SCHEDULER_ENABLED",
            "request": {
                "install_root": str(install_root.resolve()),
                "foundry_home": str(foundry_home.resolve()),
                "predecessor_source_sha": PREDECESSOR_SHA,
            },
        }),
        encoding="utf-8",
    )
    marker_path = install_root / "ROLLBACK_RUNTIME.json"
    marker_path.write_text(
        json.dumps({
            "contract": "jarvis.cross-schema-rollback-runtime.v1",
            "status": "COMPLETE_FROZEN_RUNTIME",
            "transaction_id": transaction_id,
            "source_sha": PREDECESSOR_SHA,
            "install_root": str(install_root.resolve()),
            "foundry_home": str(foundry_home.resolve()),
            "transaction_state_path": str(state_path.resolve()),
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
        [sys.executable, "-c", "print('unsafe owner resume started')"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=environment,
    )

    assert result.returncode == 2
    assert "not durably finalized" in result.stderr
    assert "unsafe owner resume started" not in result.stdout
    assert marker_path.is_file()
