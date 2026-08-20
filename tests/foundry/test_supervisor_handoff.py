"""Focused regressions for immutable Supervisor release handoff."""
from __future__ import annotations

import subprocess
import json
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment import handoff
from jarvis_foundry.deployment.handoff import (
    SupervisorHandoffError,
    SupervisorProcess,
    handoff_running_supervisor,
    resolve_handoff_release_python,
    select_authoritative_root,
    supervisor_roots,
)
from jarvis_foundry.installer import Installer


def _process(pid: int, parent_pid: int, *, exe: str = "old-python.exe") -> SupervisorProcess:
    return SupervisorProcess(
        pid=pid,
        parent_pid=parent_pid,
        executable=exe,
        command_line=f"{exe} -m jarvis_foundry.deployment.launcher --home C:/foundry",
        creation_identity={"pid": pid, "create_time": pid, "exe": exe},
    )


def test_supervisor_roots_excludes_same_tree_children() -> None:
    root = _process(101, 7)
    child = _process(102, 101)
    unrelated = _process(202, 8)
    assert [item.pid for item in supervisor_roots([root, child, unrelated])] == [101, 202]


def test_authoritative_root_matches_durable_start_identity() -> None:
    from datetime import UTC, datetime, timedelta

    started_at = datetime(2026, 8, 20, 10, 32, 41, tzinfo=UTC)
    old = _process(101, 7)
    transient = _process(202, 8)
    old = SupervisorProcess(
        pid=old.pid, parent_pid=old.parent_pid, executable=old.executable,
        command_line=old.command_line,
        creation_identity={"pid": old.pid, "create_time": int(
            ((started_at - timedelta(seconds=18)) - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 10_000_000), "exe": old.executable},
    )
    transient = SupervisorProcess(
        pid=transient.pid, parent_pid=transient.parent_pid, executable=transient.executable,
        command_line=transient.command_line,
        creation_identity={"pid": transient.pid, "create_time": int(
            ((started_at + timedelta(seconds=2)) - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 10_000_000), "exe": transient.executable},
    )
    assert select_authoritative_root([old, transient], started_at=started_at).pid == 101


def test_handoff_release_follows_exact_running_root_when_pointer_is_stale(
    monkeypatch, tmp_path: Path,
) -> None:
    running = _process(101, 7, exe="C:/JARVIS/releases/old/.venv/Scripts/python.exe")
    monkeypatch.setattr(
        handoff, "discover_supervisor_processes", lambda home, release_python=None: [running]
    )
    resolved = resolve_handoff_release_python(
        tmp_path, "C:/JARVIS/releases/stale/.venv/Scripts/python.exe")
    assert resolved.casefold().endswith("old\\.venv\\scripts\\python.exe")


def test_process_inventory_includes_headless_release_launcher(monkeypatch) -> None:
    payload = [{
        "ProcessId": 101,
        "ParentProcessId": 7,
        "ExecutablePath": "C:/JARVIS/releases/live/.venv/Scripts/pythonw.exe",
        "CommandLine": (
            "pythonw.exe -m jarvis_foundry.deployment.headless_launcher "
            "--home C:/foundry"
        ),
    }]

    def fake_run(command, **_kwargs):
        assert "deployment\\.(launcher|headless_launcher)" in command[-1]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(handoff.subprocess, "run", fake_run)
    monkeypatch.setattr(
        handoff, "process_creation_identity",
        lambda pid: {"pid": pid, "create_time": pid, "exe": payload[0]["ExecutablePath"]},
    )
    observed = handoff.discover_supervisor_processes(
        Path("C:/foundry"),
        release_python=payload[0]["ExecutablePath"],
    )
    assert [item.pid for item in observed] == [101]


def test_handoff_release_refuses_ambiguous_running_roots(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        handoff, "discover_supervisor_processes",
        lambda home, release_python=None: [_process(101, 7), _process(202, 8)],
    )
    try:
        resolve_handoff_release_python(tmp_path, "stale-python.exe")
    except SupervisorHandoffError:
        pass
    else:
        raise AssertionError("ambiguous Supervisor roots must fail closed")


def test_handoff_terminates_only_exact_orphan_tree(monkeypatch, tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    old = _process(101, 7)
    child = _process(102, 101)
    unrelated = _process(202, 8, exe="other-python.exe")
    calls: list[str] = []
    alive = {101: True}

    monkeypatch.setattr(
        handoff, "discover_supervisor_processes",
        lambda home, release_python=None: (
            [item for item in [old, child, unrelated]
             if release_python is None or item.executable == release_python]
            if any(alive.get(item.pid, False) for item in [old, child, unrelated])
            else []
        ),
    )
    monkeypatch.setattr(handoff, "_wait_for_process_exit", lambda *args: False)
    monkeypatch.setattr(handoff, "pid_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(
        handoff, "process_creation_identity",
        lambda pid: {"pid": pid, "create_time": pid, "exe": "old-python.exe"},
    )

    def terminate(pid: int, *, grace_seconds: float) -> list[int]:
        calls.append(f"terminate:{pid}")
        assert pid == 101
        alive[101] = False
        return [101, 102]

    monkeypatch.setattr(handoff, "terminate_owned_tree", terminate)
    monkeypatch.setattr(handoff, "list_process_tree", lambda pid: [101, 102])

    result = handoff_running_supervisor(
        home=tmp_path,
        release_python="old-python.exe",
        request_shutdown=lambda: calls.append("request") or {"task_state": "Ready"},
        wait_seconds=0.1,
        lease_wait_seconds=0.1,
    )

    assert calls == ["request", "terminate:101"]
    assert result["forced_exact_tree"] is True
    assert result["terminated_pids"] == [101, 102]
    assert result["singleton_lease"] == "released"


def test_handoff_refuses_multiple_supervisor_roots(monkeypatch, tmp_path: Path) -> None:
    first = _process(101, 7)
    second = _process(202, 8)
    monkeypatch.setattr(
        handoff, "discover_supervisor_processes",
        lambda home, release_python=None: [first, second],
    )
    raised = False
    try:
        handoff_running_supervisor(
            home=tmp_path,
            release_python="old-python.exe",
            request_shutdown=lambda: None,
            wait_seconds=0.1,
            lease_wait_seconds=0.1,
        )
    except SupervisorHandoffError:
        raised = True
    assert raised is True


def test_handoff_terminates_child_when_task_wrapper_root_exits(monkeypatch, tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    old = _process(101, 7)
    child = _process(102, 101)
    alive = {101: False, 102: True}
    terminated: list[int] = []
    monkeypatch.setattr(
        handoff, "discover_supervisor_processes",
        lambda home, release_python=None: [old, child] if any(
            alive.get(item.pid, False) for item in [old, child]
        ) else [],
    )
    # The task wrapper/root has exited, but its Python child remains alive.
    monkeypatch.setattr(handoff, "_wait_for_process_exit", lambda *args: True)
    monkeypatch.setattr(handoff, "pid_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(
        handoff, "process_creation_identity",
        lambda pid: {"pid": pid, "create_time": pid, "exe": "old-python.exe"},
    )

    def terminate(pid: int, *, grace_seconds: float) -> list[int]:
        terminated.append(pid)
        assert pid == 102
        alive[102] = False
        return [102]

    monkeypatch.setattr(handoff, "terminate_owned_tree", terminate)
    monkeypatch.setattr(handoff, "list_process_tree", lambda pid: [101, 102])
    result = handoff_running_supervisor(
        home=tmp_path,
        release_python="old-python.exe",
        request_shutdown=lambda: None,
        wait_seconds=0.1,
        lease_wait_seconds=0.1,
    )
    assert terminated == [102]
    assert result["forced_exact_tree"] is True
    assert result["old_pid"] == 101


def test_installer_handoff_controls_use_task_scheduler(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_schtasks(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(["schtasks"], 0, "", "")

    monkeypatch.setattr("jarvis_foundry.installer._run_schtasks", fake_schtasks)
    installer = Installer(
        FoundryDatabase(tmp_path / "foundry.db"),
        task_name="JARVIS-Test-Supervisor",
        foundry_home=tmp_path / "home",
        hermes_home=tmp_path / "hermes",
    )
    monkeypatch.setattr(installer, "task_exists", lambda: True)
    monkeypatch.setattr(installer, "status", lambda: {"task_state": "Ready"})

    assert installer.end_running()["task_state"] == "Ready"
    assert installer.run_existing()["task_state"] == "Ready"
    assert calls == [
        ("/end", "/tn", "JARVIS-Test-Supervisor"),
        ("/run", "/tn", "JARVIS-Test-Supervisor"),
    ]
