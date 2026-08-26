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
from .runtime_identity import (
    parity_state, process_matches_home, release_sha_for_id, supervisor_processes,
)
from .release import (
    ReleaseError,
    read_current,
    read_previous,
    read_release_json,
    release_dir,
    release_authority_report,
    schema_version,
    validate_release,
)


def _supervisor_processes() -> list[dict[str, Any]]:
    """Compatibility wrapper around the single runtime identity contract."""
    return supervisor_processes()


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
    processes = _supervisor_processes()
    scoped_processes = [item for item in processes if process_matches_home(item, paths.home)]
    active_processes = [
        item for item in scoped_processes
        if item.get("identity_status", "VALID") == "VALID" and item.get("live", True)
    ]
    stale_processes = [item for item in scoped_processes if item not in active_processes]
    task = installer.status()
    authority = release_authority_report(foundry_home=paths.home)
    installed_sha = current
    runtime_shas = [item.get("release_sha") for item in active_processes]
    control_pid = (runtime_diagnostic.get("runtime_control", {}) or {}).get("pid")
    recorded_release_id = str(
        supervisor.get("release_id")
        or (runtime_diagnostic.get("runtime_control", {}) or {}).get("release_id")
        or ""
    ).strip()
    if (len(active_processes) == 1
            and not any(runtime_shas)
            and control_pid == active_processes[0].get("pid")):
        recorded_sha = release_sha_for_id(recorded_release_id)
        if recorded_sha:
            runtime_shas = [recorded_sha]
    parity = parity_state(
        canonical_sha=source_sha,
        installed_sha=installed_sha,
        runtime_shas=runtime_shas,
        supervisor_count=len(active_processes),
    )
    observed_runtime_shas = {str(item).lower() for item in runtime_shas if item}
    runtime_release_sha = (
        next(iter(observed_runtime_shas)) if len(observed_runtime_shas) == 1 else None
    )
    health = derive_health(
        runtime_status=str(
            runtime_diagnostic.get("runtime_control", {}).get("status") or "UNKNOWN"),
        reconciliation_consistent=bool(reconciliation.get("consistent")),
        release_status=str(release_identity.get("status") or "UNKNOWN"),
        stop_active=paths.emergency_stop.exists(),
        supervisor_count=len(active_processes),
        parity=parity,
        trust_state=str(supervisor.get("trust_state") or "UNKNOWN"),
    )
    if health["status"] == "RUNNING" and not task.get("installed"):
        health = {
            "status": "DEGRADED",
            "reason": "Supervisor Task Scheduler ownership is not proven",
        }
    return {
        "canonical_source": source_sha,
        "canonical_source_sha": source_sha,
        "current_release": current,
        "installed_release_sha": installed_sha,
        "runtime_release_sha": runtime_release_sha,
        "parity_state": parity,
        "release_authority": authority,
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
        "supervisor_processes": processes,
        "active_supervisor_processes": active_processes,
        "stale_supervisor_processes": stale_processes,
        "pid": os.getpid(),
    }
