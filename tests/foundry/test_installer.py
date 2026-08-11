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
    # NOTE: only call this for tasks the TEST created. A task that already
    # existed before the test (e.g. the live V1 task after WP7 activation)
    # must never be removed by a test's teardown.
    try:
        installer.uninstall()
    except Exception:
        pass


def _guard_real_task(installer):
    """Host-state guard for tests that use the REAL V1 task name.

    Once WP7 activated the live task, the real name may exist on the host.
    These tests verify the gate-verdict guard; they must never create,
    replace, or delete the live task. Returns whether the task pre-existed,
    so the caller can skip host-mutating teardown for it.
    """
    return installer.task_exists()


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
    existed_before = _guard_real_task(installer)
    try:
        with pytest.raises(InstallerError, match="gate verdict is not recorded"):
            installer.install()
        # The guard must raise BEFORE any host mutation: the real task (when
        # installed) is untouched, and no new task/profile appears.
        assert installer.task_exists() == existed_before
        assert not (home / PROFILE_FILE).exists()
    finally:
        if not existed_before:
            teardown(installer)


def test_real_v1_task_allowed_after_gate_verdict(tmp_path):
    home, database, gate_verdict, installer = installer_fixture(tmp_path, task_name=V1_TASK_NAME)
    existed_before = _guard_real_task(installer)
    try:
        gate_verdict.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
        assert installer.v1_allowed() is True
        if existed_before:
            # The live V1 task is already installed: the guard has passed,
            # so the only remaining blocker is the existing task.
            with pytest.raises(InstallerError, match="already exists"):
                installer.install()
        else:
            status = installer.install()
            assert status["installed"] is True
    finally:
        if not existed_before:
            teardown(installer)


def test_real_v1_task_refused_on_fail_verdict_or_missing_file(tmp_path):
    home, database, gate_verdict, installer = installer_fixture(tmp_path, task_name=V1_TASK_NAME)
    existed_before = _guard_real_task(installer)
    try:
        assert installer.v1_allowed() is False
        gate_verdict.write_text(json.dumps({"verdict": "FAIL"}), encoding="utf-8")
        assert installer.v1_allowed() is False
        gate_verdict.write_text("not json", encoding="utf-8")
        assert installer.v1_allowed() is False
        with pytest.raises(InstallerError):
            installer.install()
        assert installer.task_exists() == existed_before
    finally:
        if not existed_before:
            teardown(installer)


def test_teardown_never_deletes_a_preexisting_task(tmp_path):
    """Regression: WP7 activation incident (2026-08-12).

    The original real-name tests called teardown() unconditionally, so the
    first full-suite run after activation deleted the LIVE V1 task
    (JARVIS-Foundry-Supervisor). Teardown must only remove tasks the test
    itself created; a pre-existing task always survives.
    """
    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    try:
        installer.install()
        # A second installer bound to the SAME existing task name: its
        # guarded refusal flow must leave the pre-existing task in place.
        second = Installer(
            database,
            task_name=installer.task_name,
            foundry_home=tmp_path / "other-home",
            hermes_home=tmp_path / "other-hermes",
            python_exe=sys.executable,
            gate_verdict_path=tmp_path / "GATE_VERDICT.json",
        )
        existed_before = _guard_real_task(second)
        assert existed_before is True
        with pytest.raises(InstallerError, match="already exists"):
            second.install()
        if not existed_before:
            teardown(second)
        assert second.task_exists() is True  # the pre-existing task survived
    finally:
        teardown(installer)  # this test created the staging task; safe to remove


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


def test_task_xml_has_repeating_time_trigger_for_bounded_restart(tmp_path):
    # Task Scheduler's RestartOnFailure does not fire for this task shape on
    # this host (empirical probes 2026-08-12). Bounded auto-restart is
    # delivered by a repeating time trigger (PT1M, 30-day window) combined
    # with IgnoreNew: while the daemon lives every tick is a no-op; after a
    # death the next tick (<= 1 minute) starts a fresh recovering instance.
    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    try:
        xml = installer._task_xml()
        assert "<TimeTrigger>" in xml
        assert "<Repetition>" in xml
        assert "<Interval>PT1M</Interval>" in xml
        assert "<Duration>P30D</Duration>" in xml
        assert "<StopAtDurationEnd>false</StopAtDurationEnd>" in xml
        # StartBoundary is a valid local timestamp one minute in the past.
        import re
        match = re.search(r"<StartBoundary>(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})</StartBoundary>", xml)
        assert match is not None
        from datetime import datetime, timedelta
        boundary = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
        assert datetime.now() - timedelta(minutes=3) <= boundary <= datetime.now()

        # The exact XML shape must register as a real task (probe-verified).
        status = installer.install()
        assert status["installed"] is True
    finally:
        teardown(installer)


def test_task_restarts_after_hard_kill_via_repeating_trigger(tmp_path):
    """Live staging regression for the bounded-restart mechanism (F1 shape):
    a hard-killed task instance is restarted by the next repeating-trigger
    tick, and the DB recovers deterministically (no duplicate, one canonical
    state). Takes up to ~2.5 minutes by design (tick interval is 1 minute)."""
    import subprocess
    import time

    home, database, gate_verdict, installer = installer_fixture(tmp_path)
    try:
        installer.install()
        # The daemon's --home is installer.foundry_home, so its durable
        # state (lease, events) lives in foundry-home/foundry.db.
        runtime_db = FoundryDatabase(home / "foundry.db")
        # The repeating trigger fires within ~1 minute; start the daemon via
        # the scheduler so IgnoreNew semantics apply.
        result = subprocess.run(
            ["schtasks", "/run", "/tn", installer.task_name],
            capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr

        def daemon_lease_owner() -> str | None:
            if not runtime_db.path.exists():
                return None
            with runtime_db.connect() as connection:
                row = connection.execute(
                    "SELECT owner_id FROM leases WHERE lease_key = 'supervisor:singleton'"
                ).fetchone()
            return row["owner_id"] if row else None

        # Wait for the daemon to start and hold the singleton lease.
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline and daemon_lease_owner() is None:
            time.sleep(2)
        first_owner = daemon_lease_owner()
        assert first_owner is not None, "daemon did not take the singleton lease"

        # Hard-kill the daemon process (TerminateProcess semantics).
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{installer.task_name}*' -or "
             f"($_.CommandLine -like '*jarvis_foundry.supervisor.run*' -and "
             f"$_.CommandLine -like '*{installer.foundry_home}*') }} | "
             f"Select-Object -ExpandProperty ProcessId | Select-Object -First 1"],
            capture_output=True, text=True, errors="replace", timeout=60)
        pid = ps.stdout.strip()
        assert pid, "daemon process not found"
        kill = subprocess.run(["taskkill", "/f", "/pid", pid],
                              capture_output=True, text=True, errors="replace", timeout=60)
        assert kill.returncode == 0, kill.stderr

        # The next repeating tick (<= 1 minute) must start a NEW instance
        # that reclaims the expired lease and heartbeats again.
        deadline = time.monotonic() + 150
        new_owner = None
        while time.monotonic() < deadline:
            time.sleep(5)
            owner = daemon_lease_owner()
            if owner is not None and owner != first_owner:
                new_owner = owner
                break
        assert new_owner is not None, "daemon was not restarted by the repeating trigger"

        with runtime_db.connect() as connection:
            started = connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'supervisor.started'"
            ).fetchone()[0]
            assert started >= 2  # first instance + restarted instance
        from jarvis_foundry.reconcile import reconcile
        assert reconcile(runtime_db).consistent is True
    finally:
        teardown(installer)


def test_uninstall_works_on_uninitialized_database(tmp_path):
    # Verifier finding F5: `jarvis uninstall` on a fresh Foundry home crashed
    # with "no such table: events". Every installer event path must self-heal
    # by initializing the database.
    home = tmp_path / "foundry-home"
    database = FoundryDatabase(tmp_path / "foundry.db")  # never initialized
    installer = Installer(
        database,
        task_name=f"JF-WP7Gate-{tmp_path.name[:8]}-uninit",
        foundry_home=home,
        hermes_home=tmp_path / "hermes-home",
        python_exe=sys.executable,
        gate_verdict_path=tmp_path / "GATE_VERDICT.json",
    )
    teardown(installer)
    status = installer.uninstall()  # must not raise
    assert status["installed"] is False
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'installation.uninstalled'"
        ).fetchone()[0] == 1

    # The shipped CLI path must exit 0 on a fresh home.
    from jarvis_foundry.cli.main import main
    assert main(["uninstall", "--home", str(tmp_path)]) == 0


def test_task_xml_escapes_special_characters(tmp_path):
    # Verifier observation: paths with XML metacharacters must not corrupt or
    # inject the task XML (defense-in-depth; operator-controlled values only).
    home = tmp_path / "foundry & home"  # Windows forbids < > in dir names
    home.mkdir(parents=True)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    installer = Installer(
        database,
        task_name=f"JF-WP7Gate-{tmp_path.name[:8]}-xml",
        foundry_home=home,
        hermes_home=tmp_path / "hermes-home",
        python_exe=sys.executable,
        gate_verdict_path=tmp_path / "GATE_VERDICT.json",
    )
    try:
        xml = installer._task_xml()
        assert "foundry &amp; home" in xml
        # The escaped XML must still register a real task.
        status = installer.install()
        assert status["installed"] is True
    finally:
        teardown(installer)


def test_daemon_exits_nonzero_on_degraded(tmp_path):
    # Verifier observation: a supervisor that cannot reconcile must fail
    # loud (exit != 0) instead of heartbeating while DEGRADED.
    import subprocess

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    from jarvis_foundry.events import Event
    database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-1", "objective": "corrupt me",
         "resource_id": None, "acceptance_criteria": [], "next_action": None},
        source_type="supervisor", source_id="degraded-test",
        idempotency_key="mission.created:mission-1",
    ))
    with database.transaction() as connection:
        connection.execute("DELETE FROM missions")  # projection now mismatches

    result = subprocess.run(
        [sys.executable, "-m", "jarvis_foundry.supervisor.run",
         "--home", str(tmp_path), "--once"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    assert result.returncode == 1
    assert "DEGRADED" in result.stdout


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
