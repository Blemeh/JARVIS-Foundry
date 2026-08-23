"""WP7 — V1 installer/uninstaller: Windows scheduled task + Foundry profile.

Scoped by design: the installer touches ONLY the named scheduled task and
the JARVIS-owned profile file in the Foundry home. ``host_task_inventory``
captures the full task list before/after so scope is provable.

The REAL V1 task name is guarded: ``install`` refuses it unless the WP7 gate
verdict file exists (var/evidence/wp7-gate/GATE_VERDICT.json with a PASS
verdict). Gate tests use staging task names and never need the guard.
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from .database import FoundryDatabase
from .events import Event
from .platform.windows_subprocess import background_python_executable
from .runtime.config import WorkerRuntimeConfig

V1_TASK_NAME = "JARVIS-Foundry-Supervisor"
WORKER_LOOP_TASK_NAME = "JARVIS-Foundry-Worker-Loop"
PROFILE_FILE = "foundry-profile.yaml"
WORKER_PROFILE_FILE = "worker-loop-profile.yaml"
WORKER_CONFIG_FILE = "worker-routing.yaml"
GATE_VERDICT_RELATIVE = Path("var/evidence/wp7-gate/GATE_VERDICT.json")
OWNED_TASK_PREFIXES = ("JARVIS-", "JF-")

TASK_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>JARVIS Foundry V1 Supervisor (independent deterministic controller)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <UserId>{user_id}</UserId>
      <Enabled>true</Enabled>
    </LogonTrigger>
    <TimeTrigger>
      <StartBoundary>{start_boundary}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1M</Interval>
        <Duration>P30D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
    <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{working_directory}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

WORKER_LOOP_TASK_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>JARVIS Foundry canonical worker loop (separate from Supervisor)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <UserId>{user_id}</UserId>
      <Enabled>false</Enabled>
    </LogonTrigger>
    <TimeTrigger>
      <StartBoundary>{start_boundary}</StartBoundary>
      <Enabled>false</Enabled>
      <Repetition>
        <Interval>PT5M</Interval>
        <Duration>P30D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>-m jarvis_foundry.runtime.worker_loop --home "{foundry_home}" --config "{worker_config}"</Arguments>
      <WorkingDirectory>{working_directory}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _current_user_id() -> str:
    """The Task Scheduler logon-trigger user id (DOMAIN\\user or user).

    An explicit UserId in the LogonTrigger is REQUIRED for task creation
    from non-interactive contexts (cron/scheduled agent sessions): with a
    user-less <LogonTrigger/> schtasks rejects the create with "Access is
    denied" (probed 2026-08-12, FIND-2026-08-12-02). The same XML with the
    current user id registers cleanly from both interactive and automated
    contexts.
    """
    user = os.environ.get("USERNAME") or getpass.getuser()
    computer = os.environ.get("COMPUTERNAME")
    return f"{computer}\\{user}" if computer else user


class InstallerError(RuntimeError):
    pass


def _run_schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    from .platform.windows_subprocess import headless_run_kwargs

    return subprocess.run(
        ["schtasks"] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
        **headless_run_kwargs(),
    )


class Installer:
    def __init__(
        self,
        database: FoundryDatabase,
        *,
        task_name: str,
        foundry_home: str | Path,
        hermes_home: str | Path,
        python_exe: str | Path | None = None,
        profile: dict[str, Any] | None = None,
        gate_verdict_path: str | Path | None = None,
        exec_command: str | Path | None = None,
        exec_arguments: str | None = None,
    ) -> None:
        self.database = database
        self.task_name = task_name
        if not isinstance(task_name, str) or not any(
                task_name.startswith(prefix) for prefix in OWNED_TASK_PREFIXES):
            raise InstallerError(
                "refusing an unowned scheduled task name; expected a JARVIS-/JF- prefix")
        self.foundry_home = Path(foundry_home).resolve()
        self.hermes_home = Path(hermes_home).resolve()
        self.python_exe = Path(python_exe or sys.executable).resolve()
        self.profile = profile or {}
        self.gate_verdict_path = Path(gate_verdict_path) if gate_verdict_path else None
        self.profile_path = self.foundry_home / PROFILE_FILE
        self.exec_command = Path(exec_command).resolve() if exec_command else None
        self.exec_arguments = exec_arguments

    # -- helpers ------------------------------------------------------------

    def _gate_verdict_path(self) -> Path:
        if self.gate_verdict_path is not None:
            return self.gate_verdict_path
        # Repository-relative default: <repo>/var/evidence/wp7-gate/GATE_VERDICT.json
        here = Path(__file__).resolve()
        repo = here.parents[2]
        return repo / GATE_VERDICT_RELATIVE

    def v1_allowed(self) -> bool:
        """The real V1 task may only be installed after the WP7 gate passes."""
        verdict_path = self._gate_verdict_path()
        if not verdict_path.exists():
            return False
        try:
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return verdict.get("verdict") == "PASS"

    def task_exists(self) -> bool:
        result = _run_schtasks("/query", "/tn", self.task_name, "/fo", "LIST")
        return result.returncode == 0

    def _append(self, event_type: str, payload: dict[str, Any], *, key: str) -> None:
        # Self-heal: every event-emitting operation works even when the
        # Foundry home was never initialized (F5: uninstall/activate on a
        # fresh home crashed with "no such table: events").
        self.database.initialize()
        self.database.append(Event.create(
            event_type, payload,
            source_type="supervisor", source_id="installer",
            idempotency_key=key,
        ))

    # -- lifecycle ----------------------------------------------------------

    def _task_xml(self) -> str:
        """The scheduled-task XML (logon + repeating time trigger, hidden,
        restart-on-failure, absolute executable path and explicit absolute
        --home argument).

        The Task Scheduler XML schema (2004/02/mit/task) has no
        EnvironmentVariables node, so the deterministic environment is
        delivered as an explicit absolute --home argument; HERMES_HOME is
        recorded in the Foundry profile (the daemon never touches the live
        Hermes home — adapter-level isolation).

        Restart mechanism (empirical, 2026-08-12): Task Scheduler's
        RestartOnFailure does NOT fire on this host for this task shape
        (verified for on-demand interactive, time-triggered interactive and
        on-demand S4U runs). Bounded auto-restart is delivered by a
        repeating time trigger (PT1M, 30-day window) combined with
        MultipleInstancesPolicy=IgnoreNew: while the daemon lives, every
        tick is a no-op; after any death, the next tick (≤ 1 minute) starts
        a fresh instance whose startup recovery reconciles durable state
        (staging probe: hard-killed task restarted at the following ticks).
        The repetition window is 30 days; reinstall refreshes it.
        """
        now = datetime.now()
        start_boundary = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
        command = self.exec_command or Path(
            background_python_executable(self.python_exe))
        arguments = self.exec_arguments or (
            f'-m jarvis_foundry.supervisor.run --home "{self.foundry_home}"'
        )
        return TASK_XML_TEMPLATE.format(
            hermes_home=xml_escape(str(self.hermes_home)),
            command=xml_escape(str(command)),
            arguments=xml_escape(arguments),
            foundry_home=xml_escape(str(self.foundry_home)),
            working_directory=xml_escape(str(self.foundry_home)),
            user_id=xml_escape(_current_user_id()),
            start_boundary=start_boundary,
        )

    def source_identity(self) -> dict[str, Any]:
        from .runtime.authority import runtime_source_identity
        identity = dict(runtime_source_identity(self.python_exe))
        identity["working_directory"] = str(self.foundry_home)
        return identity

    def install(self, *, force: bool = False) -> dict[str, Any]:
        if self.task_name == V1_TASK_NAME and not self.v1_allowed():
            raise InstallerError(
                "refusing to install the real V1 task: the WP7 gate verdict "
                "is not recorded (PASS required)")
        existing = self.task_exists()
        if existing and not force:
            raise InstallerError(
                f"task {self.task_name} already exists (use force=True to replace)")
        if existing and force and not self.profile_path.is_file():
            raise InstallerError(
                "refusing to replace an existing task without the JARVIS ownership profile")

        xml = self._task_xml()
        xml_path = self.foundry_home / f".task-{uuid.uuid4().hex[:8]}.xml"
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path.write_text(xml, encoding="utf-16")
        try:
            result = _run_schtasks(
                "/create", "/tn", self.task_name, "/xml", str(xml_path), "/f")
            if result.returncode != 0:
                raise InstallerError(
                    f"schtasks create failed: {result.stderr.strip() or result.stdout.strip()}")
        finally:
            xml_path.unlink(missing_ok=True)

        self.foundry_home.mkdir(parents=True, exist_ok=True)
        self._write_profile()
        self._append(
            "installation.installed",
            {"task_name": self.task_name, "foundry_home": str(self.foundry_home),
             "python_exe": str(self.python_exe)},
            key=f"installation.installed:{self.task_name}",
        )
        return self.status()

    def retarget_existing(self) -> dict[str, Any]:
        """Rewrite an already-installed task's action without WP7 first-install.

        Used to point production at the stable launcher. Refuses to create a
        missing V1 task (that still requires the gate).
        """
        if not self.task_exists():
            raise InstallerError(
                f"task {self.task_name} is not installed; cannot retarget")
        xml = self._task_xml()
        xml_path = self.foundry_home / f".task-{uuid.uuid4().hex[:8]}.xml"
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        xml_path.write_text(xml, encoding="utf-16")
        try:
            result = _run_schtasks(
                "/create", "/tn", self.task_name, "/xml", str(xml_path), "/f")
            if result.returncode != 0:
                raise InstallerError(
                    f"schtasks retarget failed: {result.stderr.strip() or result.stdout.strip()}")
        finally:
            xml_path.unlink(missing_ok=True)
        self._write_profile()
        self._append(
            "installation.retargeted",
            {"task_name": self.task_name, "command": str(self.exec_command or self.python_exe)},
            key=f"installation.retargeted:{self.task_name}",
        )
        return self.status()

    def _write_profile(self) -> None:
        self.foundry_home.mkdir(parents=True, exist_ok=True)
        identity = self.source_identity()
        lines = [
            "# JARVIS Foundry V1 profile (JARVIS-owned; removed by uninstall)",
            f"task_name: {self.task_name}",
            f"foundry_home: {self.foundry_home}",
            f"hermes_home: {self.hermes_home}",
            f"python_exe: {self.python_exe}",
            f"working_directory: {identity['working_directory']}",
            f"code_origin: {identity['code_origin']}",
            f"source_sha: {identity['source_sha'] or 'unknown'}",
            f"source_kind: {identity['source_kind']}",
            f"package_version: {identity['package_version']}",
        ]
        for key, value in self.profile.items():
            lines.append(f"{key}: {value}")
        self.profile_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def uninstall(self) -> dict[str, Any]:
        # Stop any running instance first, then delete the task (idempotent:
        # an absent task is fine — schtasks reports
        # "cannot find the file specified" in that case).
        _run_schtasks("/end", "/tn", self.task_name)
        if self.task_exists():
            result = _run_schtasks("/delete", "/tn", self.task_name, "/f")
            if result.returncode != 0:
                raise InstallerError(
                    f"schtasks delete failed: {result.stderr.strip() or result.stdout.strip()}")
        removed = self.profile_path.unlink(missing_ok=True)
        self._append(
            "installation.uninstalled",
            {"task_name": self.task_name, "profile_removed": removed},
            key=f"installation.uninstalled:{self.task_name}",
        )
        return self.status()

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if not self.task_exists():
            raise InstallerError(f"task {self.task_name} is not installed")
        flag = "ENABLE" if enabled else "DISABLE"
        result = _run_schtasks("/change", "/tn", self.task_name, f"/{flag}")
        if result.returncode != 0:
            raise InstallerError(
                f"schtasks change failed: {result.stderr.strip() or result.stdout.strip()}")
        self._append(
            "installation.enabled" if enabled else "installation.disabled",
            {"task_name": self.task_name},
            key=f"installation.{'enabled' if enabled else 'disabled'}:{self.task_name}",
        )
        return self.status()

    # -- inspection ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        present = self.task_exists()
        state = None
        if present:
            result = _run_schtasks(
                "/query", "/tn", self.task_name, "/fo", "LIST", "/v")
            output = result.stdout
            for line in output.splitlines():
                if "Status:" in line:
                    state = line.split(":", 1)[1].strip()
        return {
            "task_name": self.task_name,
            "installed": present,
            "task_state": state,
            "profile_present": self.profile_path.exists(),
            "profile_path": str(self.profile_path),
        }

    def host_task_inventory(self) -> list[str]:
        """All task names on the host (scope proof: before/after diff)."""
        result = _run_schtasks("/query", "/fo", "CSV", "/nh")
        if result.returncode != 0:
            return []
        names: list[str] = []
        for line in result.stdout.splitlines():
            parts = line.split('","')
            if parts:
                names.append(parts[0].strip('"').lstrip("\\"))
        return sorted(names)


class WorkerLoopInstaller(Installer):
    """Install the canonical worker-loop task as a separate, disabled task.

    This deliberately reuses the existing installer lifecycle and event
    recording while changing only the task XML/profile surface. The worker
    loop remains its own process and durable ``worker-loop:singleton`` lease;
    the Supervisor task is never replaced or managed by this class.
    """

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        task_name: str = WORKER_LOOP_TASK_NAME,
        foundry_home: str | Path,
        hermes_home: str | Path,
        python_exe: str | Path | None = None,
        worker_config_path: str | Path | None = None,
        config_path: str | Path | None = None,
        profile: dict[str, Any] | None = None,
    ) -> None:
        if task_name == V1_TASK_NAME:
            raise InstallerError(
                "worker-loop installer cannot manage the Supervisor task")
        if worker_config_path is not None and config_path is not None:
            raise InstallerError(
                "provide only one of worker_config_path or config_path")
        super().__init__(
            database,
            task_name=task_name,
            foundry_home=foundry_home,
            hermes_home=hermes_home,
            python_exe=python_exe,
            profile=profile,
        )
        self.worker_config_path = Path(
            config_path or worker_config_path or
            (self.foundry_home / WORKER_CONFIG_FILE)
        ).resolve()
        self.profile_path = self.foundry_home / WORKER_PROFILE_FILE

    def _task_xml(self) -> str:
        now = datetime.now()
        start_boundary = (now - timedelta(minutes=1)).strftime(
            "%Y-%m-%dT%H:%M:%S")
        identity = self.source_identity()
        return WORKER_LOOP_TASK_XML_TEMPLATE.format(
            python_exe=xml_escape(background_python_executable(self.python_exe)),
            foundry_home=xml_escape(str(self.foundry_home)),
            worker_config=xml_escape(str(self.worker_config_path)),
            working_directory=xml_escape(str(identity["working_directory"])),
            user_id=xml_escape(_current_user_id()),
            start_boundary=start_boundary,
        )

    def _write_profile(self) -> None:
        lines = [
            "# JARVIS Foundry worker-loop profile (JARVIS-owned; removed by uninstall)",
            f"task_name: {self.task_name}",
            f"foundry_home: {self.foundry_home}",
            f"worker_config_path: {self.worker_config_path}",
        ]
        for key, value in self.profile.items():
            lines.append(f"{key}: {value}")
        self.profile_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def install(self, *, force: bool = False) -> dict[str, Any]:
        # The worker triggers are disabled in the XML before registration;
        # /change then makes the task's registered state unambiguously Disabled.
        super().install(force=force)
        try:
            return self.set_enabled(False)
        except Exception:
            # Do not leave an unexpectedly enabled worker task behind after a
            # partial activation failure.
            try:
                super().uninstall()
            except Exception:
                pass
            raise

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if enabled and self.task_name == WORKER_LOOP_TASK_NAME:
            raise InstallerError(
                "JARVIS-Foundry-Worker-Loop has no production authority; "
                "Supervisor owns runtime lifecycle and spawn_worker_loop "
                "defaults OFF"
            )
        if enabled:
            if not self.worker_config_path.is_file():
                raise InstallerError(
                    f"worker config does not exist: {self.worker_config_path}")
            try:
                config = WorkerRuntimeConfig.from_yaml(self.worker_config_path)
            except Exception as error:  # noqa: BLE001 - fail closed at boundary
                raise InstallerError(
                    f"worker config is invalid: {self.worker_config_path}") from error
            if not config.enabled:
                raise InstallerError(
                    "worker config is disabled; set enabled: true before activation")
        return super().set_enabled(enabled)
