"""Build and point immutable releases. Durable Foundry state stays elsewhere."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__ as PACKAGE_VERSION
from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event
from ..migrations import SCHEMA_VERSION
from .layout import (
    cli_cmd,
    current_pointer,
    headless_launcher_cmd,
    install_root,
    launcher_cmd,
    previous_pointer,
    release_dir,
    rollback_runtime_marker,
)

RELEASE_BROWSER_ASSET_DIR = ".pw"
RELEASE_WEB_EXTRA = "web"
COMMAND_CENTER_NPM_INSTALL_TIMEOUT_SECONDS = 600

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
set "JARVIS_RELEASE_ID=%SHA%"
set "JARVIS_RELEASE_SHA=%SHA%"
set "PLAYWRIGHT_BROWSERS_PATH=%RELEASE%\\.pw"
"%PY%" -I -m jarvis_foundry.deployment.launcher %*
exit /b %ERRORLEVEL%
"""

# Keep the owner-facing CLI on the same immutable CURRENT-release route as
# the Supervisor launcher. Execute its bootstrap from the selected release
# itself: `-I -m` would still allow a venv's editable-install metadata to pick
# a different source tree, while an ordinary `-m` can be shadowed by cwd.
JARVIS_CLI_CMD_TEXT = LAUNCHER_CMD_TEXT.replace(
    "-I -m jarvis_foundry.deployment.launcher",
    '-I "%RELEASE%\\src\\jarvis_foundry\\deployment\\cli_launcher.py"',
).replace(
    'set "JARVIS_RELEASE_ID=%SHA%"',
    'set "CLI_ENTRY=%RELEASE%\\src\\jarvis_foundry\\deployment\\cli_launcher.py"\n'
    'if not exist "%CLI_ENTRY%" (\n'
    '  echo FAIL_CLOSED: CURRENT release CLI bootstrap missing>&2\n'
    '  exit /b 2\n'
    ')\n'
    'set "JARVIS_RELEASE_ID=%SHA%"',
)


def _packaging_template(name: str) -> Path:
    module_root = Path(__file__).resolve().parents[1]
    packaged_path = module_root / "assets" / "packaging" / name
    if packaged_path.is_file():
        return packaged_path
    # Source checkouts keep release templates in the repository-level
    # packaging directory. _copy_source_snapshot also copies them into the
    # package assets so installed immutable releases can build later releases.
    source_path = Path(__file__).resolve().parents[3] / "packaging" / name
    if source_path.is_file():
        return source_path
    raise ReleaseError(
        f"missing packaging template: {packaged_path} (source: {source_path})")


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


def _rollback_runtime_retirement_plan(
    root: Path | None, *, target_source_sha: str,
) -> dict[str, str] | None:
    """Validate a bridge before authority or pointer state can be changed."""
    marker = rollback_runtime_marker(root)
    if not marker.is_file():
        return None
    try:
        marker_bytes = marker.read_bytes()
        payload = json.loads(marker_bytes)
    except (OSError, ValueError) as error:
        raise ReleaseError(
            f"rollback runtime marker is unreadable: {marker}") from error
    if not isinstance(payload, dict):
        raise ReleaseError(
            f"rollback runtime marker is not an object: {marker}")
    if payload.get("contract") != "jarvis.cross-schema-rollback-runtime.v1":
        raise ReleaseError(
            f"rollback runtime marker contract is invalid: {marker}")
    marker_sha = str(payload.get("source_sha") or "").strip()
    if not _GIT_OBJECT_RE.fullmatch(marker_sha):
        raise ReleaseError(
            f"rollback runtime marker source_sha is invalid: {marker}")
    if payload.get("status") != "COMPLETE_FROZEN_RUNTIME":
        raise ReleaseError(
            f"rollback runtime marker is not complete: {marker}")
    return {
        "path": str(marker.resolve()),
        "sha256": hashlib.sha256(marker_bytes).hexdigest(),
        "source_sha": marker_sha,
    }


def _retire_obsolete_rollback_runtime(
    plan: dict[str, str] | None,
) -> None:
    """Remove the exact preflighted bridge after CURRENT moved away from it."""
    if plan is None:
        return
    marker = Path(plan["path"])
    try:
        observed_hash = hashlib.sha256(marker.read_bytes()).hexdigest()
    except OSError as error:
        raise ReleaseError(
            f"rollback runtime marker changed before retirement: {marker}") from error
    if observed_hash != plan["sha256"]:
        raise ReleaseError(
            f"rollback runtime marker changed before retirement: {marker}")
    marker.unlink()


def _transition_marker(root: Path | None) -> Path:
    """Return the recoverable filesystem/ledger transition marker."""

    return install_root(root) / "RELEASE_TRANSITION.json"


def _write_transition_marker(
    root: Path | None, *, target: str, previous: str | None, phase: str,
    transition_id: str | None = None,
) -> Path:
    path = _transition_marker(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    transition_id = transition_id or uuid.uuid4().hex
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps({
            "target_source_sha": target,
            "previous_source_sha": previous,
            "phase": phase,
            "transition_id": transition_id,
            "recorded_at": isoformat(),
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _database_active_release(database: FoundryDatabase) -> dict[str, Any]:
    """Read the canonical database live slot and its immutable manifest."""

    with database.connect(readonly=True) as connection:
        slot = connection.execute(
            "SELECT slot, release_id, updated_at, last_event_id "
            "FROM release_slots WHERE slot = 'live'"
        ).fetchone()
        if slot is None or slot["release_id"] in (None, ""):
            return {
                "slot": "live", "release_id": None, "source_sha": None,
                "manifest": None, "updated_at": None,
                "last_event_id": None,
            }
        manifest = connection.execute(
            "SELECT * FROM release_manifests WHERE release_id = ?",
            (slot["release_id"],),
        ).fetchone()
    return {
        "slot": "live",
        "release_id": str(slot["release_id"]),
        "source_sha": str(manifest["source_commit"]) if manifest else None,
        "manifest": dict(manifest) if manifest else None,
        "updated_at": slot["updated_at"],
        "last_event_id": slot["last_event_id"],
    }


def _installed_manifest_event(
    identity: dict[str, Any], *, source_sha: str,
) -> Event:
    """Create the ledger record for an immutable installed release.

    The V6 release registry already owns ``release_manifests``.  This small
    deployment bridge records the exact ``RELEASE.json`` identity in that
    registry so the runtime can prove source/install/DB parity without
    introducing a second authority or mutating the event ledger directly.
    """

    manifest = dict(identity["manifest"])
    release_id = str(manifest["release_id"])
    release_root = Path(str(identity["release_dir"]))
    release_json = release_root / "RELEASE.json"
    artifact_hashes = {
        "RELEASE.json": hashlib.sha256(release_json.read_bytes()).hexdigest(),
    }
    runtime_identity = dict(identity["runtime_identity"])
    payload = {
        "release_id": release_id,
        "source_commit": source_sha,
        "artifact_hashes": artifact_hashes,
        "dependencies": [],
        "runtime_identity": runtime_identity,
        "parent_release_id": None,
        "improvement_id": f"deployment:{source_sha}",
        "risk_class": "R0",
        "migrations": [int(runtime_identity["schema"])],
        "verdicts": [],
        "rollback_target": None,
        "authority_kind": "immutable_installed_release",
    }
    return Event.create(
        "release.manifest_recorded",
        payload,
        source_type="supervisor",
        source_id="deployment-authority",
        idempotency_key=f"release.manifest_recorded:{release_id}",
    )


def record_active_release(
    source_sha: str,
    *,
    root: Path | None = None,
    database: FoundryDatabase,
    reason: str = "explicit immutable release activation",
    controller: str = "supervisor",
    transition_id: str | None = None,
) -> dict[str, Any]:
    """Record an installed release as the database-backed live authority.

    This is deliberately separate from ``build_release``: building and
    installing an immutable directory never changes live state.  The caller
    invokes it only in the explicit promotion/rollback window, where the
    manifest and the live slot are appended atomically.  Filesystem pointers
    are reconciled by ``set_current`` after this ledger transaction.
    """

    if controller != "supervisor":
        raise ReleaseError("only the Supervisor may activate a release")
    if not reason or not reason.strip():
        raise ReleaseError("release activation reason must be non-empty")
    sha = source_sha.strip()
    transition_id = transition_id or uuid.uuid4().hex
    if not database.path.is_file():
        raise ReleaseError(f"database missing: {database.path}")
    # Refuse to make a DB authority claim until the immutable release's
    # declared schema window actually opens the target database.  This keeps
    # a failed promotion from leaving a live slot that the launcher can never
    # start.
    identity = validate_release(
        sha,
        root=root,
        foundry_home=database.path.parent,
        require_schema_match=True,
    )

    active = _database_active_release(database)
    release_id = str(identity["manifest"]["release_id"])
    events: list[Event] = []
    with database.connect(readonly=True) as connection:
        existing = connection.execute(
            "SELECT source_commit, artifact_hashes_json, runtime_identity_json "
            "FROM release_manifests "
            "WHERE release_id = ?", (release_id,)
        ).fetchone()
    if existing is None:
        events.append(_installed_manifest_event(identity, source_sha=sha))
    else:
        if str(existing["source_commit"]) != sha:
            raise ReleaseError(
                f"database manifest {release_id} source {existing['source_commit']} "
                f"!= installed source {sha}")
        try:
            existing_runtime = json.loads(
                str(existing["runtime_identity_json"] or "{}"))
            existing_artifacts = json.loads(
                str(existing["artifact_hashes_json"] or "{}"))
        except (TypeError, ValueError) as error:
            raise ReleaseError(
                f"database manifest {release_id} identity is unreadable") from error
        expected_artifacts = {
            "RELEASE.json": hashlib.sha256(
                (Path(str(identity["release_dir"])) / "RELEASE.json").read_bytes()
            ).hexdigest()
        }
        if (
            existing_runtime != identity["runtime_identity"]
            or existing_artifacts != expected_artifacts
        ):
            raise ReleaseError(
                f"database manifest {release_id} does not match immutable install")

    if active["release_id"] != release_id:
        prior = active["release_id"]
        if prior:
            events.append(Event.create(
                "release.slot_changed",
                {"slot": "previous", "release_id": prior,
                 "previous_release_id": None, "reason": reason,
                 "controller": controller},
                source_type="supervisor", source_id="deployment-authority",
                idempotency_key=(
                    f"release.slot_changed:runtime-previous:{prior}:{release_id}:{transition_id}"),
            ))
        events.append(Event.create(
            "release.slot_changed",
            {"slot": "live", "release_id": release_id,
             "previous_release_id": prior, "reason": reason,
             "controller": controller},
            source_type="supervisor", source_id="deployment-authority",
            idempotency_key=(
                f"release.slot_changed:runtime-live:{release_id}:{prior or 'none'}:{transition_id}"),
        ))
    if events:
        database.append_events(events)
    report = _database_active_release(database)
    if report["source_sha"] != sha:
        raise ReleaseError(
            f"database live authority {report['source_sha']!r} != {sha}")
    return report


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


def _directory_digest(directory: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest(), len(files)


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


def _validate_legacy_runtime_identity(value: Any, *, field: str) -> dict[str, Any]:
    """Validate the reduced identity retained for a pre-provenance release.

    A legacy release may be used only as an explicitly identified predecessor
    or rollback target.  It must still carry a real source object, release id,
    and schema; Git and provenance fields are absent by definition rather than
    silently accepted when malformed.
    """

    if not isinstance(value, dict):
        raise ReleaseError(f"RELEASE.json {field} must be an object")
    required = ("source_sha", "release_id", "schema")
    missing = [name for name in required if value.get(name) in (None, "")]
    if missing:
        raise ReleaseError(
            f"RELEASE.json {field} missing identity: {', '.join(missing)}")
    if not isinstance(value["source_sha"], str) or not _GIT_OBJECT_RE.fullmatch(value["source_sha"]):
        raise ReleaseError(f"RELEASE.json {field}.source_sha is invalid")
    if not isinstance(value["release_id"], str) or not value["release_id"]:
        raise ReleaseError(f"RELEASE.json {field}.release_id is invalid")
    for name in ("git_commit", "git_tree"):
        if value.get(name) not in (None, ""):
            raise ReleaseError(
                f"RELEASE.json {field}.{name} is not a legacy identity")
    try:
        value["schema"] = int(value["schema"])
    except (TypeError, ValueError):
        raise ReleaseError(f"RELEASE.json {field}.schema is invalid") from None
    if value["schema"] <= 0:
        raise ReleaseError(f"RELEASE.json {field}.schema is invalid")
    return value


def _is_legacy_manifest(manifest: dict[str, Any]) -> bool:
    """Return true only for the known pre-Git/provenance manifest shape."""

    return (
        manifest.get("git_commit") in (None, "")
        and manifest.get("git_tree") in (None, "")
        and "provenance" not in manifest
    )


def validate_release(
    source_sha: str,
    *,
    root: Path | None = None,
    foundry_home: Path | None = None,
    require_schema_match: bool = True,
    allow_legacy_identity: bool = False,
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

    # The current release must always have complete immutable identity.  The
    # compatibility branch is callable only by explicit predecessor/rollback
    # paths and accepts the historical schema/provenance shape without
    # manufacturing Git values.
    if allow_legacy_identity and _is_legacy_manifest(manifest):
        schema_compatibility = manifest.get("schema_compatibility")
        if not isinstance(schema_compatibility, dict):
            raise ReleaseError("RELEASE.json missing schema_compatibility")
        try:
            package_schema = int(schema_compatibility["package_schema"])
        except (KeyError, TypeError, ValueError):
            raise ReleaseError("RELEASE.json missing package_schema") from None
        if package_schema <= 0:
            raise ReleaseError("RELEASE.json package_schema is invalid")
        previous_runtime = manifest.get("previous_runtime", _MISSING)
        if previous_runtime is not _MISSING and previous_runtime is not None:
            _validate_legacy_runtime_identity(previous_runtime, field="previous_runtime")
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
            "legacy_identity": True,
        }

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
    bundle = manifest.get("command_center_bundle", _MISSING)
    if bundle is not _MISSING and bundle is not None:
        if not isinstance(bundle, dict) or not isinstance(bundle.get("sha256"), str):
            raise ReleaseError("RELEASE.json Command Center bundle identity is invalid")
        bundle_root = directory / "command-center"
        if not (bundle_root / "index.html").is_file():
            raise ReleaseError("release Command Center bundle is missing")
        observed_digest, observed_count = _directory_digest(bundle_root)
        if (observed_digest != bundle["sha256"]
                or observed_count != int(bundle.get("file_count", -1))):
            raise ReleaseError("release Command Center bundle hash mismatch")
    previous_runtime = manifest.get("previous_runtime", _MISSING)
    if previous_runtime is _MISSING:
        raise ReleaseError("RELEASE.json missing previous_runtime identity")
    if previous_runtime is not None:
        if (
            isinstance(previous_runtime, dict)
            and previous_runtime.get("git_commit") in (None, "")
            and previous_runtime.get("git_tree") in (None, "")
        ):
            _validate_legacy_runtime_identity(previous_runtime, field="previous_runtime")
        else:
            _validate_runtime_identity(previous_runtime, field="previous_runtime")
    web_runtime = manifest.get("web_runtime")
    if web_runtime is not None:
        _validate_web_runtime_manifest(directory, web_runtime)
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
        "legacy_identity": False,
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
    package_assets = staging / "src" / "jarvis_foundry" / "assets" / "packaging"
    package_assets.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        Path(source_repo) / "packaging",
        package_assets,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _build_command_center_bundle(
    source_repo: Path, source_sha: str, directory: Path,
) -> dict[str, Any] | None:
    """Build the React client from tracked files at the release commit."""
    listed = subprocess.run(
        ["git", "-C", str(source_repo), "ls-tree", "-r", "--name-only",
         source_sha, "ui/command-center"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    tracked = [line.strip() for line in listed.stdout.splitlines()
               if line.strip()]
    if listed.returncode != 0:
        raise ReleaseError(listed.stderr.strip() or "could not list Command Center source")
    if not tracked:
        return None
    with tempfile.TemporaryDirectory(prefix="jarvis-command-center-") as raw:
        build_root = Path(raw)
        for relative in tracked:
            blob = subprocess.run(
                ["git", "-C", str(source_repo), "show",
                 f"{source_sha}:{relative}"],
                capture_output=True, timeout=30, check=False,
            )
            if blob.returncode != 0:
                raise ReleaseError(
                    blob.stderr.decode(errors="replace").strip()
                    or f"could not read tracked UI file {relative}")
            destination = build_root / Path(relative).relative_to("ui/command-center")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(blob.stdout)
        _node_dir = Path("C:/Program Files/nodejs")
        _npm_cli = _node_dir / "node_modules" / "npm" / "bin" / "npm-cli.js"
        _node_exe = _node_dir / "node.exe"
        if _npm_cli.is_file() and _node_exe.is_file():
            _npm_cmd = [str(_node_exe), str(_npm_cli)]
        else:
            _npm_cmd = ["npm.cmd" if os.name == "nt" else "npm"]
        _npm_env = dict(os.environ)
        _npm_env["PATH"] = str(_node_dir) + os.pathsep + _npm_env.get(
            "PATH", _npm_env.get("Path", ""))
        if os.name == "nt" and len(_npm_env["PATH"]) > 7000:
            # npm adds node_modules/.bin entries before cmd.exe runs scripts.
            _npm_env["PATH"] = _npm_env["PATH"][:7000]
        if "Path" in _npm_env:
            _npm_env["Path"] = _npm_env["PATH"]
        installed = subprocess.run(
            [
                *_npm_cmd,
                "ci",
                "--prefer-offline",
                "--no-audit",
                "--no-fund",
            ],
            cwd=build_root,
            capture_output=True,
            text=True,
            timeout=COMMAND_CENTER_NPM_INSTALL_TIMEOUT_SECONDS,
            check=False,
            env=_npm_env)
        if installed.returncode != 0:
            raise ReleaseError(
                installed.stderr.strip() or installed.stdout.strip()
                or "Command Center npm ci failed")
        built = subprocess.run(
            [*_npm_cmd, "run", "build"], cwd=build_root,
            capture_output=True, text=True, timeout=300, check=False,
            env=_npm_env)
        if built.returncode != 0:
            raise ReleaseError(
                built.stderr.strip() or built.stdout.strip()
                or "Command Center build failed")
        dist = build_root / "dist"
        if not (dist / "index.html").is_file():
            raise ReleaseError("Command Center build did not produce dist/index.html")
        destination = directory / "command-center"
        shutil.copytree(dist, destination)
    digest, file_count = _directory_digest(destination)
    return {
        "status": "BUILT_FROM_TRACKED_SOURCE",
        "file_count": file_count,
        "sha256": digest,
    }


def _release_subprocess_env(*, browser_path: Path | None = None) -> dict[str, str]:
    """Return an install/probe environment isolated from developer Python."""
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PIP_NO_INPUT"] = "1"
    environment["PIP_NO_CACHE_DIR"] = "1"
    # A caller must opt into browser downloads through this build path.  A
    # developer setting that disables downloads must never produce a partial
    # immutable release.
    environment.pop("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", None)
    if browser_path is not None:
        environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_path.resolve())
    return environment


def _process_error(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "process failed").strip()
    return detail[-2000:]


def _provision_release_web_runtime(
    *, release_python: Path, staging_directory: Path,
) -> dict[str, Any]:
    """Install V10 web extras and Chromium entirely inside one release."""
    browser_path = (staging_directory / RELEASE_BROWSER_ASSET_DIR).resolve()
    browser_path.mkdir(parents=True, exist_ok=False)
    environment = _release_subprocess_env(browser_path=browser_path)
    source = staging_directory / "source"

    installed = subprocess.run(
        [
            str(release_python), "-m", "pip", "install",
            "--disable-pip-version-check", "--no-cache-dir",
            f"{source}[{RELEASE_WEB_EXTRA}]",
        ],
        cwd=staging_directory,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        env=environment,
    )
    if installed.returncode != 0:
        raise ReleaseError(
            "release web dependency install failed: " + _process_error(installed))

    browser_install = subprocess.run(
        [str(release_python), "-m", "playwright", "install", "chromium"],
        cwd=staging_directory,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
        env=environment,
    )
    if browser_install.returncode != 0:
        raise ReleaseError(
            "release Chromium provisioning failed: "
            + _process_error(browser_install))

    probe_code = """
import json
from jarvis_foundry.connectors.web_research import (
    PlaywrightBrowserConnector,
    TrafilaturaExtractor,
)

browser = PlaywrightBrowserConnector(headless=True, timeout_ms=30000)
extractor = TrafilaturaExtractor()
browser_health = browser.health()
extractor_health = extractor.health()
if not browser_health.get("available") or browser_health.get("status") != "READY":
    raise SystemExit(json.dumps({"browser_health": browser_health}, sort_keys=True))
if not extractor_health.get("available"):
    raise SystemExit(json.dumps({"extractor_health": extractor_health}, sort_keys=True))
print(json.dumps({
    "browser": browser_health,
    "extractor": extractor_health,
}, sort_keys=True))
"""
    probed = subprocess.run(
        [str(release_python), "-c", probe_code],
        cwd=staging_directory,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=environment,
    )
    if probed.returncode != 0:
        raise ReleaseError(
            "release web runtime health probe failed: " + _process_error(probed))
    try:
        payload = json.loads(probed.stdout.strip().splitlines()[-1])
        browser_health = payload["browser"]
        extractor_health = payload["extractor"]
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise ReleaseError(
            "release web runtime health probe returned invalid JSON") from error

    executable_raw = browser_health.get("browser_executable")
    if not isinstance(executable_raw, str) or not executable_raw:
        raise ReleaseError("release browser health omitted browser_executable")
    executable = Path(executable_raw).resolve()
    try:
        executable.relative_to(browser_path)
    except ValueError as error:
        raise ReleaseError(
            "release browser executable escaped the release-local browser path") from error
    if not executable.is_file():
        raise ReleaseError(f"release browser executable missing: {executable}")
    if browser_health.get("browsers_path") != str(browser_path):
        raise ReleaseError("release browser health path does not match release-local path")
    browser_version = browser_health.get("browser_version")
    playwright_version = browser_health.get("playwright_version")
    trafilatura_version = extractor_health.get("trafilatura_version")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (browser_version, playwright_version, trafilatura_version)
    ):
        raise ReleaseError("release web runtime health omitted dependency versions")

    relative_executable = executable.relative_to(staging_directory).as_posix()
    return {
        "contract": "release-local",
        "browser_name": "chromium",
        "browsers_path": RELEASE_BROWSER_ASSET_DIR,
        "browser_executable": relative_executable,
        "browser_version": browser_version,
        "playwright_version": playwright_version,
        "trafilatura_version": trafilatura_version,
    }


def _validate_web_runtime_manifest(
    directory: Path, runtime: Any,
) -> None:
    """Validate the release-local web asset contract without launching it."""
    if not isinstance(runtime, dict):
        raise ReleaseError("RELEASE.json web_runtime must be an object")
    if runtime.get("contract") != "release-local":
        raise ReleaseError("RELEASE.json web_runtime contract is invalid")
    if runtime.get("browser_name") != "chromium":
        raise ReleaseError("RELEASE.json web_runtime browser is invalid")
    if runtime.get("browsers_path") != RELEASE_BROWSER_ASSET_DIR:
        raise ReleaseError("RELEASE.json web_runtime browsers_path is not release-local")
    for field in ("browser_version", "playwright_version", "trafilatura_version"):
        if not isinstance(runtime.get(field), str) or not runtime[field].strip():
            raise ReleaseError(f"RELEASE.json web_runtime missing {field}")
    browsers_path = (directory / runtime["browsers_path"]).resolve()
    try:
        browsers_path.relative_to(directory.resolve())
    except ValueError as error:
        raise ReleaseError("RELEASE.json web_runtime path escaped release") from error
    if not browsers_path.is_dir():
        raise ReleaseError(f"release-local browser path missing: {browsers_path}")
    executable_raw = runtime.get("browser_executable")
    if not isinstance(executable_raw, str) or not executable_raw:
        raise ReleaseError("RELEASE.json web_runtime missing browser_executable")
    executable = (directory / executable_raw).resolve()
    try:
        executable.relative_to(browsers_path)
    except ValueError as error:
        raise ReleaseError("RELEASE.json browser executable escaped browser path") from error
    if not executable.is_file():
        raise ReleaseError(f"release-local browser executable missing: {executable}")


def configure_release_browser_runtime(identity: dict[str, Any]) -> Path | None:
    """Bind Playwright to the validated browser assets of the current release."""
    manifest = identity.get("manifest")
    if not isinstance(manifest, dict):
        raise ReleaseError("validated release identity has no manifest")
    runtime = manifest.get("web_runtime")
    if runtime is None:
        return None
    directory = Path(str(identity["release_dir"])).resolve()
    _validate_web_runtime_manifest(directory, runtime)
    browser_path = (directory / RELEASE_BROWSER_ASSET_DIR).resolve()
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured and Path(configured).expanduser().resolve() != browser_path:
        raise ReleaseError(
            "PLAYWRIGHT_BROWSERS_PATH does not match the current release")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_path)
    return browser_path


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
    # Build under a unique hidden sibling and publish with one rename.  A
    # Scheduler/status reader can therefore observe either no release or a
    # complete immutable release, never a half-populated venv/bundle.
    staging_directory = directory.parent / (
        f".staging-{sha[:12]}-{uuid.uuid4().hex[:12]}"
    )
    staging_directory.mkdir(parents=True, exist_ok=False)
    try:
        staging = staging_directory / "source"
        _copy_source_snapshot(repo, staging)
        command_center_bundle = _build_command_center_bundle(
            repo, sha, staging_directory)
        venv = staging_directory / ".venv"
        builder = str(Path(python_exe or sys.executable).resolve())
        created = subprocess.run(
            [builder, "-m", "venv", str(venv)],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if created.returncode != 0:
            raise ReleaseError(created.stderr.strip() or "venv create failed")
        release_python = venv / "Scripts" / "python.exe"
        web_runtime = _provision_release_web_runtime(
            release_python=release_python,
            staging_directory=staging_directory,
        )
        py_ver = subprocess.run(
            [str(release_python), "-c",
             "import sys; print(sys.version.split()[0])"],
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
            "command_center_bundle": command_center_bundle,
            "web_runtime": web_runtime,
        }
        (staging_directory / "RELEASE.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        os.replace(staging_directory, directory)
    except BaseException:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise
    release_python = directory / ".venv" / "Scripts" / "python.exe"
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


def set_current(
    source_sha: str, *, root: Path | None = None,
    database: FoundryDatabase | None = None,
    reason: str = "explicit immutable release activation",
) -> dict[str, str | None]:
    candidate = validate_release(source_sha, root=root, require_schema_match=False)
    previous = read_current(root)
    rollback_runtime_retirement = _rollback_runtime_retirement_plan(
        root, target_source_sha=source_sha)
    if previous == source_sha and database is None:
        _retire_obsolete_rollback_runtime(rollback_runtime_retirement)
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
    marker = None
    if database is not None:
        transition_id = uuid.uuid4().hex
        marker = _write_transition_marker(
            root, target=source_sha, previous=previous, phase="DB_AUTHORITY_PENDING",
            transition_id=transition_id)
        # The ledger is authoritative during a transition.  If the process
        # dies before the pointer write, startup/status sees the mismatch and
        # the marker gives recovery an unambiguous target.
        record_active_release(
            source_sha, root=root, database=database, reason=reason,
            transition_id=transition_id)
    try:
        if previous and previous != source_sha:
            write_pointer(previous_pointer(root), previous)
        write_pointer(current_pointer(root), source_sha)
        _retire_obsolete_rollback_runtime(rollback_runtime_retirement)
        if marker is not None:
            marker.unlink(missing_ok=True)
    except BaseException:
        if marker is not None:
            _write_transition_marker(
                root,
                target=source_sha,
                previous=previous,
                phase="POINTER_WRITE_PENDING",
                transition_id=transition_id,
            )
        raise
    return {"current": source_sha, "previous": read_previous(root)}


def recover_release_transition(
    *,
    root: Path | None = None,
    foundry_home: Path | None = None,
    database: FoundryDatabase | None = None,
) -> dict[str, Any]:
    """Complete an interrupted DB-authority/pointer transition safely.

    Recovery is explicit and bounded: it reads the marker, validates the
    immutable target, confirms (or records) the database live authority, then
    rewrites only the two JARVIS-owned pointers.  It never migrates, deletes,
    or rewrites the live database and is suitable for reboot/hard-kill
    startup probes.
    """

    marker = _transition_marker(root)
    if not marker.is_file():
        raise ReleaseError(f"release transition marker missing: {marker}")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReleaseError(f"release transition marker is unreadable: {marker}") from error
    target = str(payload.get("target_source_sha") or "").strip()
    previous = payload.get("previous_source_sha")
    previous_sha = str(previous).strip() if previous not in (None, "") else None
    if not target:
        raise ReleaseError("release transition marker has no target source SHA")
    transition_id = str(payload.get("transition_id") or uuid.uuid4().hex)
    validate_release(target, root=root, require_schema_match=False)
    rollback_runtime_retirement = _rollback_runtime_retirement_plan(
        root, target_source_sha=target)
    authority_database = database
    if authority_database is None and foundry_home is not None:
        authority_database = FoundryDatabase(Path(foundry_home) / "foundry.db")
    if authority_database is not None:
        record_active_release(
            target, root=root, database=authority_database,
            reason="recover interrupted immutable release transition",
            transition_id=transition_id)
    if previous_sha and previous_sha != target:
        write_pointer(previous_pointer(root), previous_sha)
    write_pointer(current_pointer(root), target)
    _retire_obsolete_rollback_runtime(rollback_runtime_retirement)
    marker.unlink(missing_ok=True)
    return {
        "current": target,
        "previous": read_previous(root),
        "recovered": True,
        "authority": (
            release_authority_report(root=root, foundry_home=foundry_home)
            if foundry_home is not None else None
        ),
    }


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
    identity = validate_release(
        sha, root=root, require_schema_match=False, allow_legacy_identity=True)
    schema = 0
    if foundry_home is not None:
        schema = schema_version(Path(foundry_home) / "foundry.db")
    record = {
        "source_sha": sha,
        "release_id": identity["manifest"]["release_id"],
        "git_commit": identity["manifest"].get("git_commit"),
        "git_tree": identity["manifest"].get("git_tree"),
        "schema": schema,
        "previous_runtime": identity["manifest"].get("previous_runtime"),
        "provenance": identity["manifest"].get("provenance"),
        "legacy_identity": bool(identity.get("legacy_identity")),
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
            "database_active_release_id": None,
            "database_active_source_sha": None,
            "database_authority_mode": "UNAVAILABLE",
            "database_runtime_identity": None,
            "filesystem_runtime_identity": None,
            "database_runtime_identity_match": None,
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
            "database_active_release_id": None,
            "database_active_source_sha": None,
            "database_authority_mode": "FILESYSTEM_ONLY",
            "database_runtime_identity": None,
            "filesystem_runtime_identity": None,
            "database_runtime_identity_match": None,
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
            live_slot = connection.execute(
                "SELECT release_id, updated_at, last_event_id FROM release_slots "
                "WHERE slot = 'live'"
            ).fetchone()
            active_manifest = None
            if live_slot is not None and live_slot["release_id"]:
                active_manifest = connection.execute(
                    "SELECT release_id, source_commit, runtime_identity_json "
                    "FROM release_manifests WHERE release_id = ?",
                    (live_slot["release_id"],),
                ).fetchone()
    except sqlite3.OperationalError as error:
        return {
            "current": current,
            "previous": previous,
            "database_release_records": 0,
            "database_current_recorded": False,
            "database_active_release_id": None,
            "database_active_source_sha": None,
            "database_authority_mode": "UNAVAILABLE",
            "database_runtime_identity": None,
            "filesystem_runtime_identity": None,
            "database_runtime_identity_match": None,
            "consistent": False,
            "status": "RELEASE_SCHEMA_UNAVAILABLE",
            "error": str(error),
        }

    active_release_id = (
        str(live_slot["release_id"])
        if live_slot is not None and live_slot["release_id"] else None
    )
    active_source_sha = (
        str(active_manifest["source_commit"])
        if active_manifest is not None else None
    )
    database_runtime_identity = None
    if active_manifest is not None:
        try:
            parsed_runtime = json.loads(
                str(active_manifest["runtime_identity_json"] or "{}"))
            database_runtime_identity = (
                parsed_runtime if isinstance(parsed_runtime, dict) else None)
        except (TypeError, ValueError):
            database_runtime_identity = None
    current_manifest = None
    if current:
        manifest_path = release_dir(current, root) / "RELEASE.json"
        try:
            current_manifest = read_release_json(manifest_path)
        except (OSError, ReleaseError, ValueError):
            current_manifest = None
    filesystem_runtime_identity = None
    if current and current_manifest:
        try:
            filesystem_runtime_identity = _runtime_identity(
                {**current_manifest, "release_id": current_manifest.get("release_id")},
                current,
            )
        except (ReleaseError, TypeError, ValueError):
            filesystem_runtime_identity = None
    # The release id is part of the immutable runtime identity too.  Matching
    # only the source/tree would let two separately built manifests for the
    # same commit appear interchangeable even when their artifact hashes or
    # registry records differ.
    identity_keys = ("source_sha", "release_id", "git_commit", "git_tree", "schema")
    runtime_identity_match = None
    if active_source_sha and database_runtime_identity is not None:
        runtime_identity_match = bool(
            filesystem_runtime_identity is not None
            and all(
                database_runtime_identity.get(key)
                == filesystem_runtime_identity.get(key)
                for key in identity_keys
            )
        )
    if active_release_id and active_manifest is None:
        status = "DATABASE_AUTHORITY_MANIFEST_MISSING"
        authority_mode = "LIVE_SLOT_WITHOUT_MANIFEST"
    elif active_source_sha and current and active_source_sha != current:
        status = "DATABASE_FILESYSTEM_MISMATCH"
        authority_mode = "DATABASE_LIVE_SLOT"
    elif (active_source_sha and current and active_source_sha == current
          and runtime_identity_match is False):
        status = "DATABASE_RUNTIME_IDENTITY_MISMATCH"
        authority_mode = "DATABASE_LIVE_SLOT"
    elif active_source_sha and current and active_source_sha == current:
        status = "CONSISTENT"
        authority_mode = "DATABASE_LIVE_SLOT"
    elif not current:
        status = "NO_FILESYSTEM_AUTHORITY"
        authority_mode = "NONE"
    elif not recorded:
        status = "FILESYSTEM_ONLY_AUTHORITY"
        authority_mode = "FILESYSTEM_ONLY"
    else:
        # Compatibility for pre-Lane-2 ledgers that recorded the immutable
        # manifest before the live slot projection existed.  The report is
        # explicit about this weaker mode so callers can require a live slot.
        status = "CONSISTENT"
        authority_mode = "MANIFEST_SOURCE_COMPATIBILITY"
    return {
        "current": current,
        "previous": previous,
        "database_release_records": total,
        "database_current_recorded": bool(recorded),
        "database_active_release_id": active_release_id,
        "database_active_source_sha": active_source_sha,
        "database_runtime_identity": database_runtime_identity,
        "filesystem_runtime_identity": filesystem_runtime_identity,
        "database_runtime_identity_match": runtime_identity_match,
        "database_authority_mode": authority_mode,
        "database_live_slot_updated_at": (
            live_slot["updated_at"] if live_slot is not None else None),
        "database_live_slot_event_id": (
            live_slot["last_event_id"] if live_slot is not None else None),
        "consistent": status == "CONSISTENT",
        "status": status,
    }


def rollback_current(
    *,
    root: Path | None = None,
    foundry_home: Path | None = None,
    database: FoundryDatabase | None = None,
    reason: str = "explicit immutable release rollback",
) -> dict[str, Any]:
    previous = read_previous(root)
    if not previous:
        raise ReleaseError("no PREVIOUS release to roll back to")
    current = read_current(root)
    validate_release(
        previous, root=root, foundry_home=foundry_home,
        require_schema_match=foundry_home is not None,
        allow_legacy_identity=True,
    )
    if current:
        validate_release(current, root=root, require_schema_match=False)
    rollback_runtime_retirement = _rollback_runtime_retirement_plan(
        root, target_source_sha=previous)
    authority_database = database
    if authority_database is None and foundry_home is not None:
        authority_database = FoundryDatabase(Path(foundry_home) / "foundry.db")
    if current == previous:
        if authority_database is not None:
            record_active_release(
                previous, root=root, database=authority_database, reason=reason)
        return {
            "current": current,
            "previous": read_previous(root),
            "generation": None,
            "switched": False,
        }
    marker = None
    if authority_database is not None:
        transition_id = uuid.uuid4().hex
        marker = _write_transition_marker(
            root, target=previous, previous=current, phase="DB_AUTHORITY_PENDING",
            transition_id=transition_id)
        record_active_release(
            previous, root=root, database=authority_database, reason=reason,
            transition_id=transition_id)
    try:
        write_pointer(current_pointer(root), previous)
        if current:
            write_pointer(previous_pointer(root), current)
        _retire_obsolete_rollback_runtime(rollback_runtime_retirement)
        if marker is not None:
            marker.unlink(missing_ok=True)
    except BaseException:
        if marker is not None:
            _write_transition_marker(
                root,
                target=previous,
                previous=current,
                phase="POINTER_WRITE_PENDING",
                transition_id=transition_id,
            )
        raise
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
    # Migrations update schema only; derived projections can legitimately be
    # stale in a byte-for-byte backup.  Rebuild them on the disposable clone
    # before reporting reconciliation so this command rehearses the same
    # canonical recovery path used by production restart/rollback checks.
    event_count = cloned.rebuild_projections()
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
        "event_count": event_count,
    }


def restore_database(live: Path, backup: Path) -> int:
    return FoundryDatabase(live).restore_from(backup)
