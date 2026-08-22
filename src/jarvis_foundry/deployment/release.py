"""Build and point immutable releases. Durable Foundry state stays elsewhere."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__ as PACKAGE_VERSION
from ..clock import isoformat
from ..database import FoundryDatabase
from ..migrations import SCHEMA_VERSION
from .layout import (
    cli_cmd,
    current_pointer,
    headless_launcher_cmd,
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

JARVIS_CLI_CMD_TEXT = """@echo off
setlocal EnableExtensions
set "PYTHONPATH="
set "PYTHONHOME="
if exist "C:\\Python313\\python.exe" (
  "C:\\Python313\\python.exe" -m jarvis_foundry.cli.main %*
  exit /b %ERRORLEVEL%
)
if exist "%APPDATA%\\Python\\Python313\\Scripts\\jarvis.exe" (
  "%APPDATA%\\Python\\Python313\\Scripts\\jarvis.exe" %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>nul && (
  py -3.13 -m jarvis_foundry.cli.main %*
  exit /b %ERRORLEVEL%
)
if not defined JARVIS_INSTALL_ROOT set "JARVIS_INSTALL_ROOT=%LOCALAPPDATA%\\JARVIS"
set "CURRENT_FILE=%JARVIS_INSTALL_ROOT%\\CURRENT"
if exist "%CURRENT_FILE%" (
  set /p SHA=<"%CURRENT_FILE%"
  for /f "tokens=* delims= " %%A in ("%SHA%") do set "SHA=%%A"
  set "PY=%JARVIS_INSTALL_ROOT%\\releases\\%SHA%\\.venv\\Scripts\\python.exe"
  if exist "%PY%" (
    "%PY%" -m jarvis_foundry.cli.main %*
    exit /b %ERRORLEVEL%
  )
)
echo FAIL_CLOSED: jarvis CLI not installed. Run: pip install -e . from JARVIS-Foundry>&2
exit /b 2
"""


def _packaging_template(name: str) -> Path:
    repo = Path(__file__).resolve().parents[3]
    path = repo / "packaging" / name
    if not path.is_file():
        raise ReleaseError(f"missing packaging template: {path}")
    return path


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


_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_MISSING = object()


def _runtime_identity(manifest: dict[str, Any], source_sha: str) -> dict[str, Any]:
    """Extract the immutable identity needed for promotion/rollback proof."""

    schema_compatibility = manifest.get("schema_compatibility")
    if not isinstance(schema_compatibility, dict):
        raise ReleaseError("RELEASE.json schema_compatibility is missing")
    try:
        schema = int(schema_compatibility["package_schema"])
    except (KeyError, TypeError, ValueError):
        raise ReleaseError("RELEASE.json package_schema is missing") from None
    return {
        "source_sha": source_sha,
        "release_id": manifest.get("release_id"),
        "git_commit": manifest.get("git_commit"),
        "git_tree": manifest.get("git_tree"),
        "schema": schema,
    }


def _validate_runtime_identity(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseError(f"RELEASE.json {field} must be an object")
    required = ("source_sha", "release_id", "git_commit", "git_tree", "schema")
    missing = [name for name in required if value.get(name) in (None, "")]
    if missing:
        raise ReleaseError(
            f"RELEASE.json {field} missing identity: {', '.join(missing)}")
    if not isinstance(value["source_sha"], str) or not _GIT_OBJECT_RE.fullmatch(value["source_sha"]):
        raise ReleaseError(f"RELEASE.json {field}.source_sha is invalid")
    if not isinstance(value["release_id"], str):
        raise ReleaseError(f"RELEASE.json {field}.release_id is invalid")
    if not isinstance(value["git_commit"], str) or not _GIT_OBJECT_RE.fullmatch(value["git_commit"]):
        raise ReleaseError(f"RELEASE.json {field}.git_commit is invalid")
    if not isinstance(value["git_tree"], str) or not _GIT_OBJECT_RE.fullmatch(value["git_tree"]):
        raise ReleaseError(f"RELEASE.json {field}.git_tree is invalid")
    try:
        value["schema"] = int(value["schema"])
    except (TypeError, ValueError):
        raise ReleaseError(f"RELEASE.json {field}.schema is invalid") from None
    return value


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
    if not isinstance(manifest.get("release_id"), str) or not manifest.get("release_id"):
        raise ReleaseError("RELEASE.json missing release_id")
    git_commit = manifest.get("git_commit")
    git_tree = manifest.get("git_tree")
    if not git_commit:
        raise ReleaseError("RELEASE.json missing git_commit")
    if not isinstance(git_commit, str) or not _GIT_OBJECT_RE.fullmatch(git_commit):
        raise ReleaseError("RELEASE.json has invalid git_commit")
    if git_commit != sha:
        raise ReleaseError("RELEASE.json git_commit does not match source_sha")
    if not git_tree:
        raise ReleaseError("RELEASE.json missing git_tree")
    if not isinstance(git_tree, str) or not _GIT_OBJECT_RE.fullmatch(git_tree):
        raise ReleaseError("RELEASE.json has invalid git_tree")
    schema_compatibility = manifest.get("schema_compatibility")
    if not isinstance(schema_compatibility, dict):
        raise ReleaseError("RELEASE.json missing schema_compatibility")
    try:
        package_schema = int(schema_compatibility["package_schema"])
    except (KeyError, TypeError, ValueError):
        raise ReleaseError("RELEASE.json missing package_schema") from None
    if package_schema != SCHEMA_VERSION:
        raise ReleaseError(
            f"RELEASE.json schema {package_schema} != package {SCHEMA_VERSION}")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise ReleaseError("RELEASE.json missing provenance")
    if not isinstance(provenance.get("source_repo"), str) or not provenance["source_repo"]:
        raise ReleaseError("RELEASE.json provenance source_repo is missing")
    if provenance.get("git_commit") != git_commit or provenance.get("git_tree") != git_tree:
        raise ReleaseError("RELEASE.json provenance does not match git identity")
    previous_runtime = manifest.get("previous_runtime", _MISSING)
    if previous_runtime is _MISSING:
        raise ReleaseError("RELEASE.json missing previous_runtime identity")
    if previous_runtime is not None:
        _validate_runtime_identity(previous_runtime, field="previous_runtime")
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
        "runtime_identity": _runtime_identity(manifest, sha),
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
    tree_observed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{sha}^{{tree}}"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    tree = tree_observed.stdout.strip()
    if tree_observed.returncode != 0 or not _GIT_OBJECT_RE.fullmatch(tree):
        raise ReleaseError(
            f"could not resolve Git tree for {sha}: {tree_observed.stderr.strip()}")
    directory = release_dir(sha, root)
    if directory.exists():
        raise ReleaseError(f"release already exists: {directory}")
    previous_runtime = None
    previous_sha = read_current(root)
    if previous_sha and previous_sha != sha:
        previous_manifest_path = release_dir(previous_sha, root) / "RELEASE.json"
        if not previous_manifest_path.is_file():
            raise ReleaseError(
                f"previous CURRENT release manifest missing: {previous_manifest_path}")
        previous_manifest = read_release_json(previous_manifest_path)
        previous_runtime = _runtime_identity(previous_manifest, previous_sha)
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
        "git_commit": sha,
        "git_tree": tree,
        "python_version": py_ver.stdout.strip(),
        "package_version": PACKAGE_VERSION,
        "schema_compatibility": {
            "package_schema": SCHEMA_VERSION,
            "opens_min": SCHEMA_VERSION,
            "opens_max": SCHEMA_VERSION,
        },
        "provenance": {
            "builder": "jarvis_foundry.deployment.release",
            "source_repo": str(repo),
            "git_commit": sha,
            "git_tree": tree,
        },
        "previous_runtime": previous_runtime,
        "created_at": isoformat(),
    }
    (directory / "RELEASE.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _install_stable_launcher(root)
    return {"release_dir": str(directory), "manifest": manifest, "python_exe": str(release_python)}


def install_cli_launcher(root: Path | None = None) -> Path:
    from .path_setup import install_cli_launchers

    return install_cli_launchers(root)


def _install_stable_launcher(root: Path | None = None) -> Path:
    bin_dir = install_root(root) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    destination = launcher_cmd(root)
    destination.write_text(LAUNCHER_CMD_TEXT, encoding="utf-8")
    headless_launcher_cmd(root).write_text(
        _packaging_template("jarvis-supervisor-headless.vbs").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    install_cli_launcher(root)
    return destination


def set_current(source_sha: str, *, root: Path | None = None) -> dict[str, str | None]:
    candidate = validate_release(source_sha, root=root, require_schema_match=False)
    previous = read_current(root)
    if previous == source_sha:
        return {"current": source_sha, "previous": read_previous(root)}
    candidate_previous = candidate["manifest"].get("previous_runtime")
    if previous is None:
        if candidate_previous is not None:
            raise ReleaseError(
                "candidate previous_runtime does not match empty CURRENT pointer")
    else:
        previous_manifest_path = release_dir(previous, root) / "RELEASE.json"
        if not previous_manifest_path.is_file():
            raise ReleaseError(
                f"CURRENT release manifest missing: {previous_manifest_path}")
        previous_identity = _runtime_identity(
            read_release_json(previous_manifest_path), previous)
        if candidate_previous != previous_identity:
            raise ReleaseError(
                "candidate previous_runtime does not match the CURRENT release")
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
    identity = validate_release(sha, root=root, require_schema_match=False)
    schema = 0
    if foundry_home is not None:
        schema = schema_version(Path(foundry_home) / "foundry.db")
    record = {
        "source_sha": sha,
        "release_id": identity["manifest"]["release_id"],
        "git_commit": identity["manifest"]["git_commit"],
        "git_tree": identity["manifest"]["git_tree"],
        "schema": schema,
        "previous_runtime": identity["manifest"]["previous_runtime"],
        "provenance": identity["manifest"]["provenance"],
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


def release_authority_report(
    *,
    root: Path | None = None,
    foundry_home: Path | None = None,
) -> dict[str, Any]:
    """Report whether durable DB authority agrees with filesystem pointers."""
    current = read_current(root)
    previous = read_previous(root)
    if foundry_home is None:
        return {
            "current": current,
            "previous": previous,
            "database_release_records": 0,
            "database_current_recorded": False,
            "consistent": False,
            "status": "FOUNDRY_HOME_MISSING",
        }

    database_path = Path(foundry_home) / "foundry.db"
    if not database_path.is_file():
        return {
            "current": current,
            "previous": previous,
            "database_release_records": 0,
            "database_current_recorded": False,
            "consistent": False,
            "status": "DATABASE_MISSING",
        }

    database = FoundryDatabase(database_path)
    try:
        with database.connect(readonly=True) as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM release_manifests").fetchone()[0])
            recorded = int(connection.execute(
                "SELECT COUNT(*) FROM release_manifests WHERE source_commit = ?",
                (current,),
            ).fetchone()[0]) if current else 0
    except sqlite3.OperationalError as error:
        return {
            "current": current,
            "previous": previous,
            "database_release_records": 0,
            "database_current_recorded": False,
            "consistent": False,
            "status": "RELEASE_SCHEMA_UNAVAILABLE",
            "error": str(error),
        }

    if not current:
        status = "NO_FILESYSTEM_AUTHORITY"
    elif not recorded:
        status = "FILESYSTEM_ONLY_AUTHORITY"
    else:
        status = "CONSISTENT"
    return {
        "current": current,
        "previous": previous,
        "database_release_records": total,
        "database_current_recorded": bool(recorded),
        "consistent": status == "CONSISTENT",
        "status": status,
    }


def rollback_current(
    *,
    root: Path | None = None,
    foundry_home: Path | None = None,
) -> dict[str, Any]:
    previous = read_previous(root)
    if not previous:
        raise ReleaseError("no PREVIOUS release to roll back to")
    current = read_current(root)
    previous_identity = validate_release(
        previous, root=root, foundry_home=foundry_home,
        require_schema_match=foundry_home is not None,
    )
    current_identity = None
    if current:
        current_identity = validate_release(current, root=root, require_schema_match=False)
    if current == previous:
        return {
            "current": current,
            "previous": read_previous(root),
            "generation": None,
            "switched": False,
        }
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
