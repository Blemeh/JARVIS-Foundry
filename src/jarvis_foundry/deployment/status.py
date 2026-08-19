"""Read-only operator truth for canonical source vs live release."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..installer import V1_TASK_NAME, Installer
from ..migrations import SCHEMA_VERSION
from ..paths import FoundryPaths
from .layout import install_root
from .release import read_current, read_previous, read_release_json, release_dir, schema_version


def _supervisor_processes() -> list[dict[str, Any]]:
    import json
    import subprocess
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match 'jarvis_foundry.(supervisor.run|deployment.launcher)' } | "
        "Select-Object ProcessId, ExecutablePath, CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=30, check=False,
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
    if current:
        manifest_path = release_dir(current) / "RELEASE.json"
        if manifest_path.is_file():
            current_manifest = read_release_json(manifest_path)
    source_sha = None
    if source_repo and (Path(source_repo) / ".git").exists():
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        source_sha = result.stdout.strip() or None
    supervisor = {}
    if paths.database.is_file():
        try:
            with database.connect(readonly=True) as connection:
                row = connection.execute(
                    "SELECT * FROM supervisor_state WHERE singleton = 1").fetchone()
                supervisor = dict(row) if row else {}
        except Exception as error:  # noqa: BLE001
            supervisor = {"error": str(error)}
    return {
        "canonical_source": source_sha,
        "current_release": current,
        "previous_release": previous,
        "current_manifest": current_manifest,
        "install_root": str(install_root()),
        "live_db": str(paths.database),
        "live_schema": live_schema,
        "package_schema": SCHEMA_VERSION,
        "stop": paths.emergency_stop.exists(),
        "stop_path": str(paths.emergency_stop),
        "supervisor_state": supervisor,
        "task": installer.status(),
        "supervisor_processes": _supervisor_processes(),
        "pid": os.getpid(),
    }
