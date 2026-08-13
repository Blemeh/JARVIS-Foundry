"""V6-S3: isolated candidate builder.

Candidates build ONLY inside registered canonical candidate roots with
their own runtime homes. The build environment is sanitized (no
HERMES_HOME/JARVIS_HOME/credential variables; JARVIS_HOME is scoped
inside the root), build commands are secret-scanned at the boundary,
roots are locked per-build with stale-marker reaping (a crashed
builder's dead marker is reaped and recorded), outside-root writes are
detected and fail the build, and the release-pointer/registry surfaces
are unreachable from the candidate process (its whole world lives under
the root; no DB path is ever passed).

Build lifecycle: candidate.build_started -> candidate.build_completed |
candidate.build_failed | (crashed executor) candidate.build_reaped.
Each build is a fresh attempt (new build_id); identical re-delivery of
a build event dedupes by its key.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ..clock import isoformat
from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash
from .records import RISK_ORDER  # noqa: F401 — risk vocabulary shared

MARKER_NAME = ".candidate-build.json"
RUNTIME_HOME_NAME = "runtime-home"

#: Environment variables that must NEVER reach a candidate process.
_STRIPPED_ENV_KEYS = {
    "HERMES_HOME", "JARVIS_HOME", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
}
_STRIPPED_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


class BuilderError(ValueError):
    """A candidate build is invalid, refused, or cannot start."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BuilderError(message)


def _runtime_home(database: FoundryDatabase) -> Path:
    return Path(database.path).resolve().parent


def register_candidate_root(
    database: FoundryDatabase, root: str | Path, *,
    controller: str,
    source_type: str = "executive", source_id: str = "v6",
) -> Path:
    """Register a canonical candidate root. Idempotent per resolved
    path. Roots inside the runtime home (the live-like surface) are
    refused."""
    _require(isinstance(controller, str) and controller.strip(),
             "controller identity must be a non-empty string")
    resolved = Path(root).expanduser().resolve()
    runtime_home = _runtime_home(database)
    if resolved == runtime_home or runtime_home in resolved.parents:
        raise BuilderError(
            f"candidate root {resolved} is inside the runtime home "
            f"{runtime_home} — refused (live-like surface)")
    resolved.mkdir(parents=True, exist_ok=True)
    event = Event.create(
        "candidate_root.registered",
        {"root": str(resolved), "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"candidate_root.registered:{resolved}")
    database.append(event)
    return resolved


def registered_roots(database: FoundryDatabase) -> set[Path]:
    """All registered candidate roots (from the event stream)."""
    roots: set[Path] = set()
    for event in database.events():
        if event.event_type == "candidate_root.registered":
            roots.add(Path(event.payload["root"]))
    return roots


def _process_alive(pid: int) -> bool:
    """Windows-safe process-liveness probe (os.kill(pid, 0) on Windows
    TERMINATES the process — never use it there)."""
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _artifact_hashes(root: Path) -> dict[str, str]:
    """Hash every regular file under the root except the build marker
    and the candidate runtime home (deliverables only)."""
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == MARKER_NAME or rel.startswith(RUNTIME_HOME_NAME + "/"):
            continue
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def build_candidate(
    database: FoundryDatabase, *,
    improvement_id: str,
    build_command: list[str],
    candidate_root: str | Path,
    controller: str,
    timeout_s: int = 120,
    source_type: str = "executive", source_id: str = "v6",
) -> str:
    """Run one isolated candidate build. Returns the build id.

    Refused when: the improvement is unknown, the root is unregistered,
    the command carries secret-like content, or another build is
    running on the same root (live marker). A stale marker (dead pid)
    is reaped first and recorded as candidate.build_reaped.
    """
    _require(database.row("improvement_records", "improvement_id",
                          improvement_id) is not None,
             f"improvement {improvement_id} does not exist — refused")
    _require(isinstance(build_command, list) and build_command and all(
        isinstance(part, str) and part for part in build_command),
        "build_command must be a non-empty list of strings")
    root = Path(candidate_root).expanduser().resolve()
    _require(root in registered_roots(database),
             f"candidate root {root} is not registered — refused")
    _require(isinstance(controller, str) and controller.strip(),
             "controller identity must be a non-empty string")
    _require(isinstance(timeout_s, int) and timeout_s > 0,
             "timeout_s must be a positive integer")
    if contains_secret_like(canonical_json(build_command)):
        raise BuilderError(
            "build command contains secret-like content — refused at "
            "the boundary (V5-S7 guard family)")

    marker_path = root / MARKER_NAME
    if marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if _process_alive(int(marker.get("pid", -1))):
            raise BuilderError(
                f"another build ({marker.get('build_id')}) is running "
                f"on root {root} — refused (concurrent build)")
        reap_id = str(marker.get("build_id", "build-unknown"))
        database.append(Event.create(
            "candidate.build_reaped",
            {"build_id": reap_id, "reason": "stale marker, builder "
             "process no longer alive"},
            source_type=source_type, source_id=source_id,
            idempotency_key=f"candidate.build_reaped:{reap_id}"))

    build_id = "build-" + uuid.uuid4().hex[:12]
    command_hash = payload_hash({"command": list(build_command)})
    database.append(Event.create(
        "candidate.build_started",
        {"build_id": build_id, "improvement_id": improvement_id,
         "candidate_root": str(root), "command_hash": command_hash,
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"candidate.build_started:{build_id}"))
    marker_path.write_text(json.dumps(
        {"build_id": build_id, "pid": os.getpid()}), encoding="utf-8")

    env = {key: value for key, value in os.environ.items()
           if key not in _STRIPPED_ENV_KEYS
           and not key.endswith(_STRIPPED_ENV_SUFFIXES)}
    runtime_home = root / RUNTIME_HOME_NAME
    runtime_home.mkdir(parents=True, exist_ok=True)
    env["JARVIS_HOME"] = str(runtime_home)

    parent_before = {
        entry.name for entry in Path(root.parent).iterdir()
    } if root.parent.is_dir() else set()
    db_name = Path(database.path).name

    started = isoformat()
    t0 = time.monotonic()
    try:
        completed = subprocess.run(
            build_command, cwd=str(root), env=env,
            capture_output=True, timeout=timeout_s, shell=False)
        exit_code = completed.returncode
        reason = None if exit_code == 0 else f"exit code {exit_code}"
    except subprocess.TimeoutExpired:
        exit_code = None
        reason = f"timeout after {timeout_s}s"
    except OSError as error:
        exit_code = None
        reason = f"spawn error: {error}"
    duration_s = round(time.monotonic() - t0, 3)

    parent_after = {
        entry.name for entry in Path(root.parent).iterdir()
    } if root.parent.is_dir() else set()
    outside = sorted(
        name for name in (parent_after - parent_before)
        if not name.startswith(db_name))  # WAL/-shm of the runtime DB
    if outside:
        reason = f"outside write detected: {', '.join(outside)}"
        exit_code = exit_code if exit_code is not None else 0

    finished = isoformat()
    if reason is None:
        database.append(Event.create(
            "candidate.build_completed",
            {"build_id": build_id, "exit_code": exit_code,
             "artifact_hashes": _artifact_hashes(root),
             "duration_s": duration_s,
             "started_at": started, "finished_at": finished,
             "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=f"candidate.build_completed:{build_id}"))
    else:
        database.append(Event.create(
            "candidate.build_failed",
            {"build_id": build_id, "exit_code": exit_code,
             "reason": reason, "started_at": started,
             "finished_at": finished, "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=f"candidate.build_failed:{build_id}"))
    marker_path.unlink(missing_ok=True)
    return build_id


def candidate_builds(database: FoundryDatabase,
                     improvement_id: str | None = None) -> list[dict[str, Any]]:
    """All candidate builds (optionally filtered by improvement)."""
    builds = database.all("candidate_builds")
    if improvement_id is not None:
        builds = [b for b in builds
                  if b["improvement_id"] == improvement_id]
    return builds
