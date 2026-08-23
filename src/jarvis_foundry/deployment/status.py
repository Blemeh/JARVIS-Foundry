"""Read-only operator truth for canonical source vs live release."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..diagnostics.runtime import diagnostic_snapshot, derive_health
from ..installer import V1_TASK_NAME, Installer
from ..migrations import SCHEMA_VERSION
from ..paths import FoundryPaths
from ..reconcile import inspect as inspect_ledger
from .layout import install_root
from .release import (
    ReleaseError,
    read_current,
    read_previous,
    read_release_json,
    release_dir,
    schema_version,
    validate_release,
)


def _supervisor_processes() -> list[dict[str, Any]]:
    import json
    import subprocess

    from ..platform.windows_subprocess import headless_run_kwargs

    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match 'jarvis_foundry.(supervisor.run|deployment.launcher)' } | "
        "Select-Object ProcessId, ExecutablePath, CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=30, check=False,
        **headless_run_kwargs(),
    )
    raw = (result.stdout or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        return [payload]
    return list(payload)


def production_status(*, home: Path | None = None, source_repo: Path | None = None) -> dict[str, Any]:
    paths = FoundryPaths.discover(home)
    database = FoundryDatabase(paths.database)
    installer = Installer(
        database, task_name=V1_TASK_NAME, foundry_home=paths.home,
        hermes_home=FoundryPaths.hermes_home(),
    )
    live_schema = schema_version(paths.database) if paths.database.is_file() else 0
    current = read_current()
    previous = read_previous()
    current_manifest = None
    release_identity = {
        "status": "UNKNOWN",
        "reason": "CURRENT pointer missing",
    }
    if current:
        manifest_path = release_dir(current) / "RELEASE.json"
        if manifest_path.is_file():
            try:
                current_manifest = read_release_json(manifest_path)
                validated = validate_release(
                    current, foundry_home=paths.home, require_schema_match=True)
                release_identity = {
                    "status": "VALID",
                    "runtime_identity": validated["runtime_identity"],
                }
            except (OSError, ReleaseError, ValueError) as error:
                release_identity = {
                    "status": "DEGRADED",
                    "reason": str(error),
                }
        else:
            release_identity = {
                "status": "DEGRADED",
                "reason": "CURRENT release manifest missing",
            }
    source_sha = None
    if source_repo and (Path(source_repo) / ".git").exists():
        import subprocess

        from ..platform.windows_subprocess import headless_run_kwargs

        result = subprocess.run(
            ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15, check=False,
            **headless_run_kwargs(),
        )
        source_sha = result.stdout.strip() or None
    supervisor = {}
    runtime_diagnostic = {
        "runtime_control": {
            "status": "UNKNOWN",
            "reason": "database missing",
        },
        "correlation": {
            "release_ids": [], "process_instances": [], "pids": [],
            "lease_owners": [], "request_ids": [], "mission_ids": [],
        },
        "events": [],
    }
    reconciliation = {"consistent": False, "reason": "database missing"}
    if paths.database.is_file():
        try:
            with database.connect(readonly=True) as connection:
                row = connection.execute(
                    "SELECT * FROM supervisor_state WHERE singleton = 1").fetchone()
                supervisor = dict(row) if row else {}
            runtime_diagnostic = diagnostic_snapshot(
                database, stop_path=str(paths.emergency_stop))
            reconciliation = inspect_ledger(database).to_json()
        except Exception as error:  # noqa: BLE001
            supervisor = {"error": str(error)}
            runtime_diagnostic["runtime_control"] = {
                "status": "UNKNOWN",
                "reason": f"diagnostic read failed: {type(error).__name__}",
            }
    task = installer.status()
    health = derive_health(
        runtime_status=str(
            runtime_diagnostic.get("runtime_control", {}).get("status") or "UNKNOWN"),
        reconciliation_consistent=bool(reconciliation.get("consistent")),
        release_status=str(release_identity.get("status") or "UNKNOWN"),
        stop_active=paths.emergency_stop.exists(),
    )
    if health["status"] == "RUNNING" and not task.get("installed"):
        health = {
            "status": "DEGRADED",
            "reason": "Supervisor Task Scheduler ownership is not proven",
        }
    return {
        "canonical_source": source_sha,
        "current_release": current,
        "previous_release": previous,
        "current_manifest": current_manifest,
        "release_identity": release_identity,
        "install_root": str(install_root()),
        "live_db": str(paths.database),
        "live_schema": live_schema,
        "package_schema": SCHEMA_VERSION,
        "stop": paths.emergency_stop.exists(),
        "stop_path": str(paths.emergency_stop),
        "supervisor_state": supervisor,
        "task": task,
        "health": health,
        "reconciliation": reconciliation,
        "runtime_diagnostic": runtime_diagnostic,
        "supervisor_processes": _supervisor_processes(),
        "pid": os.getpid(),
    }
