"""WP7 — V1 installer/uninstaller: Windows scheduled task + Foundry profile.

Scoped by design: the installer touches ONLY the named scheduled task and
the JARVIS-owned profile file in the Foundry home. ``host_task_inventory``
captures the full task list before/after so scope is provable.

The REAL V1 task name is guarded: ``install`` refuses it unless the WP7 gate
verdict file exists (var/evidence/wp7-gate/GATE_VERDICT.json with a PASS
verdict). Gate tests use staging task names and never need the guard.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from .database import FoundryDatabase
from .events import Event

V1_TASK_NAME = "JARVIS-Foundry-Supervisor"
PROFILE_FILE = "foundry-profile.yaml"
GATE_VERDICT_RELATIVE = Path("var/evidence/wp7-gate/GATE_VERDICT.json")

TASK_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>JARVIS Foundry V1 Supervisor (independent deterministic controller)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
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
      <Command>{python_exe}</Command>
      <Arguments>-m jarvis_foundry.supervisor.run --home "{foundry_home}"</Arguments>
    </Exec>
  </Actions>
</Task>
"""


class InstallerError(RuntimeError):
    pass


def _run_schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks"] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
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
    ) -> None:
        self.database = database
        self.task_name = task_name
        self.foundry_home = Path(foundry_home).resolve()
        self.hermes_home = Path(hermes_home).resolve()
        self.python_exe = Path(python_exe or sys.executable).resolve()
        self.profile = profile or {}
        self.gate_verdict_path = Path(gate_verdict_path) if gate_verdict_path else None
        self.profile_path = self.foundry_home / PROFILE_FILE

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
        return TASK_XML_TEMPLATE.format(
            hermes_home=xml_escape(str(self.hermes_home)),
            python_exe=xml_escape(str(self.python_exe)),
            foundry_home=xml_escape(str(self.foundry_home)),
            start_boundary=start_boundary,
        )

    def install(self, *, force: bool = False) -> dict[str, Any]:
        if self.task_name == V1_TASK_NAME and not self.v1_allowed():
            raise InstallerError(
                "refusing to install the real V1 task: the WP7 gate verdict "
                "is not recorded (PASS required)")
        if self.task_exists() and not force:
            raise InstallerError(
                f"task {self.task_name} already exists (use force=True to replace)")

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

    def _write_profile(self) -> None:
        lines = [
            "# JARVIS Foundry V1 profile (JARVIS-owned; removed by uninstall)",
            f"task_name: {self.task_name}",
            f"foundry_home: {self.foundry_home}",
            f"hermes_home: {self.hermes_home}",
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
