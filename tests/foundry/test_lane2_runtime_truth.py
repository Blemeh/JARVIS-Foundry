"""Lane 2 runtime/deployment truth probes (disposable state only)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.release import (
    ReleaseError,
    release_authority_report,
    recover_release_transition,
    rollback_current,
    set_current,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.runtime.worker_loop import (
    _append_coalesced_idle_event,
    _waiting_report,
)


def _fake_release(root: Path, sha: str, *, previous: str | None = None) -> None:
    directory = root / "releases" / sha
    scripts = directory / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("fake", encoding="utf-8")
    prior_identity = None
    if previous:
        prior = json.loads(
            (root / "releases" / previous / "RELEASE.json").read_text(
                encoding="utf-8"))
        prior_identity = {
            "source_sha": previous,
            "release_id": prior["release_id"],
            "git_commit": prior["git_commit"],
            "git_tree": prior["git_tree"],
            "schema": SCHEMA_VERSION,
        }
    manifest = {
        "release_id": f"rel-{sha[:12]}",
        "source_sha": sha,
        "git_commit": sha,
        "git_tree": "1" * 40,
        "python_version": "3.13.0",
        "package_version": "0.1.0",
        "schema_compatibility": {
            "package_schema": SCHEMA_VERSION,
            "opens_min": SCHEMA_VERSION,
            "opens_max": SCHEMA_VERSION,
        },
        "provenance": {
            "builder": "lane2-test",
            "source_repo": str(root / "source"),
            "git_commit": sha,
            "git_tree": "1" * 40,
        },
        "previous_runtime": prior_identity,
    }
    (directory / "RELEASE.json").write_text(
        json.dumps(manifest), encoding="utf-8")


def test_headless_launcher_is_synchronous_and_identity_bound(tmp_path: Path) -> None:
    from jarvis_foundry.deployment.release import _install_stable_launcher

    root = tmp_path / "install"
    _install_stable_launcher(root)
    text = (root / "bin" / "jarvis-supervisor-headless.vbs").read_text(
        encoding="utf-8")
    assert 'environment("JARVIS_RELEASE_ID") = sha' in text
    assert "shell.Run(cmd, 0, True)" in text
    assert "WScript.Quit exitCode" in text
    assert "shell.Run cmd, 0, False" not in text
    assert "WScript.ScriptFullName" in text
    assert "JARVIS_INSTALL_ROOT does not match task launcher" in text


def test_failed_release_build_does_not_publish_partial_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis_foundry.deployment.release as release

    repo = Path(__file__).resolve().parents[2]
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    source = tmp_path / "source"
    subprocess.run(
        ["git", "clone", "--local", "--no-hardlinks", str(repo), str(source)],
        capture_output=True, text=True, check=True,
    )

    def fail_bundle(*args: object, **kwargs: object) -> None:
        raise ReleaseError("disposable bundle failure")

    monkeypatch.setattr(release, "_build_command_center_bundle", fail_bundle)
    with pytest.raises(ReleaseError, match="disposable bundle failure"):
        release.build_release(
            source_repo=source, source_sha=sha, root=tmp_path / "install",
        )
    releases = tmp_path / "install" / "releases"
    assert not (releases / sha).exists()
    assert not list(releases.glob(".staging-*"))


def test_scheduler_contract_keeps_wscript_switches(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    import jarvis_foundry.deployment.status as deployment_status
    from jarvis_foundry.deployment.layout import headless_launcher_cmd
    from jarvis_foundry.deployment.release import _install_stable_launcher

    install = tmp_path / "install"
    home = tmp_path / "home"
    _install_stable_launcher(install)
    launcher = headless_launcher_cmd(install)

    class Result:
        returncode = 0
        stderr = ""
        stdout = (
            "<Command>C:\\Windows\\System32\\wscript.exe</Command>"
            f"<Arguments>//B //Nologo \"{launcher}\" --home \"{home}\"</Arguments>"
            "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
            "<Interval>PT1M</Interval><Duration>P30D</Duration>"
        )

    monkeypatch.setattr(deployment_status.subprocess, "run", lambda *args, **kwargs: Result())
    report = deployment_status._scheduler_contract(
        install, home,
        {
            "installed": True,
            "owned_for_home": True,
            "task_name": "JARVIS-Foundry-Supervisor",
        },
    )
    assert report["action_contract"] is True
    assert report["status"] == "PASS"


def test_set_current_records_database_live_authority_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = tmp_path / "install"
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(install))
    first, second = "a" * 40, "b" * 40
    _fake_release(install, first)
    _fake_release(install, second, previous=first)
    home = tmp_path / "home"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()

    set_current(first, root=install)
    set_current(second, root=install, database=database)
    report = release_authority_report(root=install, foundry_home=home)
    assert report["status"] == "CONSISTENT"
    assert report["database_authority_mode"] == "DATABASE_LIVE_SLOT"
    assert report["database_active_source_sha"] == second
    assert report["database_current_recorded"] is True
    assert not (install / "RELEASE_TRANSITION.json").exists()

    rollback = rollback_current(root=install, foundry_home=home)
    assert rollback["current"] == first
    rolled = release_authority_report(root=install, foundry_home=home)
    assert rolled["consistent"] is True
    assert rolled["database_active_source_sha"] == first
    assert database.rebuild_projections() > 0


def test_database_authority_is_not_claimed_when_pointer_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = tmp_path / "install"
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(install))
    first, second = "c" * 40, "d" * 40
    _fake_release(install, first)
    _fake_release(install, second, previous=first)
    home = tmp_path / "home"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    set_current(first, root=install)

    import jarvis_foundry.deployment.release as release

    original = release.write_pointer

    def fail_current(path: Path, sha: str) -> None:
        if path.name == "CURRENT":
            raise OSError("disposable pointer fault")
        original(path, sha)

    monkeypatch.setattr(release, "write_pointer", fail_current)
    with pytest.raises(OSError, match="pointer fault"):
        set_current(second, root=install, database=database)
    marker = install / "RELEASE_TRANSITION.json"
    assert marker.is_file()
    assert json.loads(marker.read_text(encoding="utf-8"))["phase"] == (
        "POINTER_WRITE_PENDING")
    report = release_authority_report(root=install, foundry_home=home)
    assert report["status"] == "DATABASE_FILESYSTEM_MISMATCH"
    assert report["consistent"] is False

    monkeypatch.undo()
    recovered = recover_release_transition(root=install, foundry_home=home)
    assert recovered["recovered"] is True
    assert recovered["current"] == second
    assert not marker.exists()
    assert release_authority_report(root=install, foundry_home=home)["consistent"]


def test_unchanged_idle_state_is_coalesced_but_transitions_are_recorded(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    _waiting_report(database, loop_id="one", instance_id="i-one", reason="no work")
    _waiting_report(database, loop_id="two", instance_id="i-two", reason="no work")
    _waiting_report(database, loop_id="three", instance_id="i-three", reason="STOP active")
    waiting = [
        event for event in database.events()
        if event.event_type == "worker_loop.waiting"
    ]
    assert len(waiting) == 2
    assert [event.payload["reason"] for event in waiting] == [
        "no work", "STOP active"]


def test_portfolio_poll_deadline_does_not_break_idle_coalescing(
    tmp_path: Path,
) -> None:
    """A fresh polling deadline is metadata, not a new idle state."""

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    for index in range(3):
        _append_coalesced_idle_event(
            database,
            "worker_loop.portfolio_waiting",
            {
                "loop_id": f"poll-{index}",
                "instance_id": f"instance-{index}",
                "decision_id": f"decision-{index}",
                "reason": "no positive-value work is currently executable",
                "wake_on": ["time", "new_evidence"],
                "revisit_at": f"2026-08-28T12:0{index}:00Z",
            },
            stable_fields={
                "selected_class": "WAIT",
                "reason": "no positive-value work is currently executable",
                "why_now": "no candidate has positive executable value",
                "wake_on": ["time", "new_evidence"],
            },
            source_id=f"worker-loop:instance-{index}",
        )
    waiting = [
        event for event in database.events()
        if event.event_type == "worker_loop.portfolio_waiting"
    ]
    assert len(waiting) == 1


def test_process_census_classifies_duplicate_and_stale_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis_foundry.deployment import status as deployment_status

    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps([
            {
                "ProcessId": 10,
                "ParentProcessId": 1,
                "ExecutablePath": "C:\\install\\releases\\" + "a" * 40 + "\\.venv\\Scripts\\pythonw.exe",
                "CommandLine": r"python -m jarvis_foundry.deployment.headless_launcher",
                "CreationDate": "now",
            },
            {
                "ProcessId": 11,
                "ParentProcessId": 1,
                "ExecutablePath": "C:\\install\\releases\\" + "b" * 40 + "\\.venv\\Scripts\\pythonw.exe",
                "CommandLine": r"python -m jarvis_foundry.deployment.headless_launcher",
                "CreationDate": "old",
            },
        ])

    monkeypatch.setattr(deployment_status.subprocess, "run", lambda *args, **kwargs: Result())
    observed = deployment_status._supervisor_processes(
        expected_sha="a" * 40, active_pid=10)
    assert observed[0]["classification"] == "VALID_OWNER"
    assert observed[1]["classification"] == "STALE_RELEASE"

    monkeypatch.setattr(
        "jarvis_foundry.supervisor.worker.process_creation_identity",
        lambda pid: {"pid": pid, "create_time": 2, "exe": "C:\\install\\pythonw.exe"},
    )
    reused = deployment_status._supervisor_processes(
        expected_sha="a" * 40,
        active_pid=10,
        active_creation_identity={
            "pid": 10, "create_time": 1, "exe": "C:\\install\\pythonw.exe",
        },
    )
    assert reused[0]["classification"] == "STALE_PROCESS_IDENTITY"


def test_command_center_status_endpoint_exposes_runtime_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis_foundry.command_center import CommandCenterServer

    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "install"))
    database = FoundryDatabase(tmp_path / "home" / "foundry.db")
    database.initialize()
    server = CommandCenterServer(database, stop_path=tmp_path / "home" / "STOP")
    server.start()
    try:
        with urlopen(
            f"http://{server.address[0]}:{server.address[1]}/api/status",
            timeout=5,
        ) as response:
            payload = json.loads(response.read())
        assert payload["status"] == payload["health"]["status"]
        assert "release_authority" in payload
        assert "parity" in payload
        assert "process_truth" in payload
        assert "scheduler" in payload
    finally:
        server.close()


def test_owner_controls_survive_restart_and_hard_kill(tmp_path: Path) -> None:
    from jarvis_foundry.owner_controls import OwnerControlService, sign_command
    from jarvis_foundry.owner_directives import owner_directive_snapshot
    from jarvis_foundry.supervisor.service import Supervisor

    home = tmp_path / "home"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    stop_path = home / "EMERGENCY_STOP"
    secret = b"lane2-owner-secret-012345678901234567890123"
    service = OwnerControlService(database, secret=secret, stop_path=stop_path)
    pause_nonce = "pause-restart"
    paused = service.apply(
        "PAUSE", target="GLOBAL", exact_text="pause for hard-kill probe",
        nonce=pause_nonce,
        signature=sign_command(secret, "PAUSE", "GLOBAL", "pause for hard-kill probe", pause_nonce),
    )
    assert owner_directive_snapshot(database)["active_count"] == 1
    # A fresh service instance sees the same durable hold and can revoke it.
    fresh = OwnerControlService(database, secret=secret, stop_path=stop_path)
    resume_nonce = "resume-restart"
    fresh.resume(
        nonce=resume_nonce,
        signature=sign_command(secret, "RESUME", "GLOBAL", "resume after restart", resume_nonce),
        exact_text="resume after restart",
    )
    assert owner_directive_snapshot(database)["active_count"] == 0
    stop_nonce = "stop-restart"
    fresh.apply(
        "STOP_EVERYTHING", target="GLOBAL", exact_text="stop for durability probe",
        nonce=stop_nonce,
        signature=sign_command(secret, "STOP_EVERYTHING", "GLOBAL", "stop for durability probe", stop_nonce),
    )
    assert stop_path.is_file()
    clear_nonce = "clear-stop-restart"
    fresh.resume(
        nonce=clear_nonce,
        signature=sign_command(secret, "RESUME", "GLOBAL", "clear stop after restart", clear_nonce),
        exact_text="clear stop after restart",
    )
    assert not stop_path.exists()
    veto_nonce = "veto-restart"
    veto = fresh.apply(
        "VETO_ACTION", target="GLOBAL", exact_text="veto durability probe",
        nonce=veto_nonce,
        signature=sign_command(secret, "VETO_ACTION", "GLOBAL", "veto durability probe", veto_nonce),
    )
    revoke_nonce = "revoke-restart"
    fresh.revoke(
        veto.directive_id,
        nonce=revoke_nonce,
        signature=sign_command(secret, "RESUME", veto.directive_id, "revoke durability probe", revoke_nonce),
        exact_text="revoke durability probe",
    )
    assert owner_directive_snapshot(database)["active_count"] == 0

    script = (
        "import time; "
        "from jarvis_foundry.database import FoundryDatabase; "
        "from jarvis_foundry.supervisor.service import Supervisor; "
        f"db=FoundryDatabase(r'{database.path}'); "
        f"s=Supervisor(db, r'{stop_path}', instance_id='hard-kill-owner'); "
        "print(s.start(), flush=True); time.sleep(60)"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "RUNNING"
        child.kill()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    recovered = Supervisor(database, stop_path, instance_id="after-reboot")
    assert recovered.start() == "RUNNING"
    recovered.stop()
    assert any(
        event.event_type == "runtime.control.lease_reclaimed"
        for event in database.events()
    )
