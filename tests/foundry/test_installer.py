"""WP7 installer tests: staging-task lifecycle (install -> disabled ->
activated -> uninstall), scope proof, real-name guard, profile handling.

These tests create and delete a REAL Windows scheduled task with a STAGING
name (never the real V1 task name) — that is the gate's purpose: clean
install, disabled state, activation, and uninstall must be reproducible.
Every test cleans up its task in a finally block; the staging name is
unique per test so tests never collide.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.installer import PROFILE_FILE, V1_TASK_NAME, Installer, InstallerError


def installer_fixture(tmp_path, *, task_name=None):
    home = tmp_path / "foundry-home"
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    gate_verdict = tmp_path / "GATE_VERDICT.json"
    name = task_name or f"JF-WP7Gate-{tmp_path.name[:8]}-{abs(hash(str(tmp_path))) % 100000}"
    installer = Installer(
        database,
        task_name=name,
        foundry_home=home,
        hermes_home=tmp_path / "hermes-home",
        python_exe=sys.executable,
        profile={"mission_roles": "default"},
        gate_verdict_path=gate_verdict,
    )
    return home, database, gate_verdict, installer


def teardown(installer):
    # Best-effort cleanup: never leave a task behind, whatever the test did.
    try:
        installer.uninstall()
    except Exception:
        pass


def test_staging_install_disable_enable_uninstall_reproducible(tmp_path):
    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    try:
        # Clean install.
        status = installer.install()
        assert status["installed"] is True
        assert status["profile_present"] is True
        assert (home / PROFILE_FILE).exists()
        assert installer.task_exists()

        # Disabled state.
        disabled = installer.set_enabled(False)
        assert disabled["installed"] is True
        assert disabled["task_state"] == "Disabled"

        # Activation.
        enabled = installer.set_enabled(True)
        assert enabled["installed"] is True
        assert enabled["task_state"] in ("Ready", "Running")

        # Uninstall.
        gone = installer.uninstall()
        assert gone["installed"] is False
        assert gone["profile_present"] is False
        assert not installer.task_exists()
        assert not (home / PROFILE_FILE).exists()

        # Events recorded.
        with database.connect() as connection:
            types = [r["event_type"] for r in connection.execute(
                "SELECT event_type FROM events WHERE event_type LIKE 'installation.%' ORDER BY sequence")]
        assert types == [
            "installation.installed", "installation.disabled",
            "installation.enabled", "installation.uninstalled",
        ]
    finally:
        teardown(installer)


def test_uninstall_is_idempotent(tmp_path):
    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    teardown(installer)  # not installed: must not raise
    status = installer.uninstall()
    assert status["installed"] is False
    assert status["profile_present"] is False


def test_install_refuses_existing_task_without_force(tmp_path):
    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    try:
        installer.install()
        with pytest.raises(InstallerError, match="already exists"):
            installer.install()
        installer.install(force=True)  # force replaces cleanly
        assert installer.task_exists()
    finally:
        teardown(installer)


def test_real_v1_task_refused_without_gate_verdict(tmp_path):
    home, database, gate_verdict, installer = installer_fixture(tmp_path, task_name=V1_TASK_NAME)
    try:
        with pytest.raises(InstallerError, match="gate verdict is not recorded"):
            installer.install()
        assert not installer.task_exists()
        assert not (home / PROFILE_FILE).exists()
    finally:
        teardown(installer)


def test_real_v1_task_allowed_after_gate_verdict(tmp_path):
    home, database, gate_verdict, installer = installer_fixture(tmp_path, task_name=V1_TASK_NAME)
    try:
        gate_verdict.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
        assert installer.v1_allowed() is True
        status = installer.install()
        assert status["installed"] is True
    finally:
        teardown(installer)


def test_real_v1_task_refused_on_fail_verdict_or_missing_file(tmp_path):
    home, database, gate_verdict, installer = installer_fixture(tmp_path, task_name=V1_TASK_NAME)
    try:
        assert installer.v1_allowed() is False
        gate_verdict.write_text(json.dumps({"verdict": "FAIL"}), encoding="utf-8")
        assert installer.v1_allowed() is False
        gate_verdict.write_text("not json", encoding="utf-8")
        assert installer.v1_allowed() is False
        with pytest.raises(InstallerError):
            installer.install()
    finally:
        teardown(installer)


def test_install_scope_proof_host_inventory(tmp_path):
    # Scope: the installer may add/remove ONLY its own task name.
    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    try:
        before = installer.host_task_inventory()
        installer.install()
        after_install = installer.host_task_inventory()
        installer.uninstall()
        after_uninstall = installer.host_task_inventory()

        added = sorted(set(after_install) - set(before))
        removed = sorted(set(after_install) - set(after_uninstall))
        assert added == [installer.task_name]
        assert removed == [installer.task_name]
        assert after_uninstall == before  # host fully restored
    finally:
        teardown(installer)


def test_profile_written_and_removed_with_install(tmp_path):
    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    try:
        installer.install()
        profile_text = (home / PROFILE_FILE).read_text(encoding="utf-8")
        assert "task_name:" in profile_text
        assert str(home) in profile_text
        assert str(tmp_path / "hermes-home") in profile_text
        assert "mission_roles: default" in profile_text
        installer.uninstall()
        assert not (home / PROFILE_FILE).exists()
    finally:
        teardown(installer)


def test_task_xml_contains_absolute_paths_and_home_argument(tmp_path):
    # The task XML must carry the absolute python path, the foundry home, and
    # the explicit --home argument (spec §3.3: absolute executable path,
    # explicit deterministic environment, hidden window, max one instance).
    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    try:
        installer.install()
        xml = installer._task_xml()
        assert str(Path(sys.executable).resolve()) in xml
        assert str(home) in xml
        assert '--home "' in xml
        assert "<Hidden>true</Hidden>" in xml
        assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml
        assert "<LogonTrigger>" in xml
        assert "<RestartOnFailure>" in xml
    finally:
        teardown(installer)


def test_headless_supervisor_once_cycle(tmp_path):
    # The scheduled task runs `python -m jarvis_foundry.supervisor.run --once`;
    # a single cycle must start, heartbeat, and stop cleanly on a temp home.
    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    import subprocess

    def once() -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "jarvis_foundry.supervisor.run",
             "--home", str(tmp_path), "--once"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, cwd=home.parent,
        )
        assert result.returncode == 0, result.stderr
        lines = result.stdout.strip().splitlines()
        return {"supervisor": json.loads(lines[-2])["supervisor"],
                "heartbeat": json.loads(lines[-1])["heartbeat"]}

    first = once()
    assert first["supervisor"] == "RUNNING"
    assert first["heartbeat"] == "RUNNING"

    # Restart: a second daemon cycle reconciles and runs again (lease released
    # on exit; no duplicate-worker conflict).
    second = once()
    assert second["supervisor"] == "RUNNING"

    # STOP: an existing sentinel freezes the daemon deterministically.
    sentinel = tmp_path / "EMERGENCY_STOP"
    sentinel.write_text(json.dumps({"stop_id": "gate-stop", "reason": "gate test"}), encoding="utf-8")
    frozen = once()
    assert frozen["supervisor"] == "FROZEN"
    assert frozen["heartbeat"] == "FROZEN"

    # Resume: clearing the sentinel lets the next cycle run again.
    sentinel.unlink()
    resumed = once()
    assert resumed["supervisor"] == "RUNNING"

    with database.connect() as connection:
        started = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'supervisor.started'").fetchone()[0]
        frozen_events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'emergency_stop.frozen'").fetchone()[0]
        assert started == 3  # first, restart, resumed (frozen run emits emergency_stop.frozen)
        assert frozen_events == 2  # one from start (sentinel present), one from heartbeat
