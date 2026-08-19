"""Build and point immutable releases. Durable Foundry state stays elsewhere."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__ as PACKAGE_VERSION
from ..clock import isoformat
from ..database import FoundryDatabase
from ..migrations import SCHEMA_VERSION
from .layout import (
    current_pointer,
    install_root,
    launcher_cmd,
    previous_pointer,
    release_dir,
)

LAUNCHER_CMD_TEXT = """@echo off
setlocal EnableExtensions
set "PYTHONPATH="
set "PYTHONHOME="
if not defined JARVIS_INSTALL_ROOT set "JARVIS_INSTALL_ROOT=%LOCALAPPDATA%\\JARVIS"
set "CURRENT_FILE=%JARVIS_INSTALL_ROOT%\\CURRENT"
if not exist "%CURRENT_FILE%" (
  echo FAIL_CLOSED: CURRENT pointer missing>&2
  exit /b 2
)
set /p SHA=<"%CURRENT_FILE%"
for /f "tokens=* delims= " %%A in ("%SHA%") do set "SHA=%%A"
set "RELEASE=%JARVIS_INSTALL_ROOT%\\releases\\%SHA%"
set "PY=%RELEASE%\\.venv\\Scripts\\python.exe"
if not exist "%PY%" (
  echo FAIL_CLOSED: release python missing>&2
  exit /b 2
)
"%PY%" -m jarvis_foundry.deployment.launcher %*
exit /b %ERRORLEVEL%
"""


class ReleaseError(RuntimeError):
    pass


def read_pointer(path: Path) -> str | None:
    if not path.is_file():
        return None
    sha = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return sha or None


def read_current(root: Path | None = None) -> str | None:
    return read_pointer(current_pointer(root))


def read_previous(root: Path | None = None) -> str | None:
    return read_pointer(previous_pointer(root))


def write_pointer(path: Path, sha: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(sha.strip() + "\n", encoding="utf-8")
    os.replace(temporary, path)


def schema_version(database_path: Path) -> int:
    if not database_path.is_file():
        return 0
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return int(row[0] if row else 0)
    finally:
        connection.close()


def read_release_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ReleaseError("RELEASE.json is not an object")
    return data


def validate_release(
    source_sha: str,
    *,
    root: Path | None = None,
    foundry_home: Path | None = None,
    require_schema_match: bool = True,
) -> dict[str, Any]:
    sha = source_sha.strip()
    directory = release_dir(sha, root)
    manifest_path = directory / "RELEASE.json"
    python_exe = directory / ".venv" / "Scripts" / "python.exe"
    if not manifest_path.is_file():
        raise ReleaseError(f"RELEASE.json missing: {manifest_path}")
    if not python_exe.is_file():
        raise ReleaseError(f"release python missing: {python_exe}")
    manifest = read_release_json(manifest_path)
    if manifest.get("source_sha") != sha:
        raise ReleaseError("RELEASE.json source_sha does not match directory")
    if manifest.get("release_id") in (None, ""):
        raise ReleaseError("RELEASE.json missing release_id")
    package_schema = int(manifest.get("schema_compatibility", {}).get("package_schema", 0))
    if package_schema != SCHEMA_VERSION:
        raise ReleaseError(
            f"RELEASE.json schema {package_schema} != package {SCHEMA_VERSION}")
    if foundry_home is not None and require_schema_match:
        live_schema = schema_version(Path(foundry_home) / "foundry.db")
        if live_schema != package_schema:
            raise ReleaseError(
                f"schema mismatch: live={live_schema} package={package_schema}")
    return {
        "ok": True,
        "source_sha": sha,
        "release_dir": str(directory),
        "python_exe": str(python_exe.resolve()),
        "manifest": manifest,
    }


def _copy_source_snapshot(source_repo: Path, staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for name in ("src", "pyproject.toml", "packaging"):
        origin = source_repo / name
        if not origin.exists():
            raise ReleaseError(f"missing source path: {origin}")
        destination = staging / name
        if origin.is_dir():
            shutil.copytree(origin, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(origin, destination)


def build_release(
    *,
    source_repo: Path,
    source_sha: str,
    python_exe: str | Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    repo = Path(source_repo).resolve()
    sha = source_sha.strip()
    if len(sha) < 7:
        raise ReleaseError("source_sha is too short")
    observed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if observed.returncode != 0 or observed.stdout.strip() != sha:
        raise ReleaseError(
            f"observed HEAD {observed.stdout.strip()!r} != requested {sha}")
    dirty = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    # Untracked evidence docs are allowed; tracked source must be clean.
    tracked_dirty = [
        line for line in dirty.stdout.splitlines()
        if line and not line.startswith("??")
    ]
    if tracked_dirty:
        raise ReleaseError("refusing to build from a dirty tracked worktree")
    directory = release_dir(sha, root)
    if directory.exists():
        raise ReleaseError(f"release already exists: {directory}")
    directory.mkdir(parents=True)
    staging = directory / "source"
    _copy_source_snapshot(repo, staging)
    venv = directory / ".venv"
    builder = str(Path(python_exe or sys.executable).resolve())
    created = subprocess.run(
        [builder, "-m", "venv", str(venv)],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if created.returncode != 0:
        raise ReleaseError(created.stderr.strip() or "venv create failed")
    release_python = venv / "Scripts" / "python.exe"
    installed = subprocess.run(
        [str(release_python), "-m", "pip", "install", "--disable-pip-version-check", str(staging)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if installed.returncode != 0:
        raise ReleaseError(installed.stderr.strip() or installed.stdout.strip() or "pip install failed")
    py_ver = subprocess.run(
        [str(release_python), "-c", "import sys; print(sys.version.split()[0])"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    manifest = {
        "release_id": f"rel-{sha[:12]}-{uuid.uuid4().hex[:8]}",
        "source_sha": sha,
        "python_version": py_ver.stdout.strip(),
        "package_version": PACKAGE_VERSION,
        "schema_compatibility": {
            "package_schema": SCHEMA_VERSION,
            "opens_min": SCHEMA_VERSION,
            "opens_max": SCHEMA_VERSION,
        },
        "created_at": isoformat(),
    }
    (directory / "RELEASE.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _install_stable_launcher(root)
    return {"release_dir": str(directory), "manifest": manifest, "python_exe": str(release_python)}


def _install_stable_launcher(root: Path | None = None) -> Path:
    destination = launcher_cmd(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(LAUNCHER_CMD_TEXT, encoding="utf-8")
    return destination


def set_current(source_sha: str, *, root: Path | None = None) -> dict[str, str | None]:
    validate_release(source_sha, root=root, require_schema_match=False)
    previous = read_current(root)
    if previous and previous != source_sha:
        write_pointer(previous_pointer(root), previous)
    write_pointer(current_pointer(root), source_sha)
    return {"current": source_sha, "previous": read_previous(root)}


def record_generation(
    *,
    root: Path | None = None,
    foundry_home: Path | None = None,
    backup: Path | None = None,
) -> dict[str, Any]:
    """Record which immutable release currently owns which DB generation."""
    sha = read_current(root)
    if not sha:
        raise ReleaseError("CURRENT pointer missing")
    schema = 0
    if foundry_home is not None:
        schema = schema_version(Path(foundry_home) / "foundry.db")
    record = {
        "source_sha": sha,
        "schema": schema,
        "backup": str(backup) if backup else None,
        "recorded_at": isoformat(),
    }
    directory = install_root(root) / "generations"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sha[:12]}-{uuid.uuid4().hex[:8]}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (install_root(root) / "GENERATION.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def rollback_current(
    *,
    root: Path | None = None,
    foundry_home: Path | None = None,
) -> dict[str, Any]:
    previous = read_previous(root)
    if not previous:
        raise ReleaseError("no PREVIOUS release to roll back to")
    current = read_current(root)
    validate_release(
        previous, root=root, foundry_home=foundry_home,
        require_schema_match=foundry_home is not None,
    )
    if current:
        validate_release(current, root=root, require_schema_match=False)
    write_pointer(current_pointer(root), previous)
    if current:
        write_pointer(previous_pointer(root), current)
    generation = None
    if foundry_home is not None:
        generation = record_generation(root=root, foundry_home=foundry_home)
    return {
        "current": previous,
        "previous": current,
        "generation": generation,
        "switched": True,
    }


def backup_database(source: Path, destination: Path) -> Path:
    database = FoundryDatabase(source)
    return database.backup_to(destination)


def migrate_clone(source: Path, destination: Path) -> dict[str, Any]:
    backup_database(source, destination)
    cloned = FoundryDatabase(destination)
    version = cloned.initialize()
    integrity = cloned.quick_check()
    if integrity != "ok":
        raise ReleaseError(f"migrated clone failed integrity: {integrity}")
    from ..reconcile import inspect as inspect_ledger
    report = inspect_ledger(cloned)
    return {
        "path": str(destination),
        "schema": version,
        "integrity": integrity,
        "consistent": report.consistent,
        "mismatch_count": len(report.mismatches),
        "event_count": cloned.event_count(),
    }


def restore_database(live: Path, backup: Path) -> int:
    return FoundryDatabase(live).restore_from(backup)
