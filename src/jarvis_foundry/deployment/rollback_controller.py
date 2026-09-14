"""Freeze-guarded, crash-resumable cross-schema release rollback.

The controller keeps its transaction state outside ``foundry.db`` because the
database is itself replaced during rollback. It never imports a target release
to validate it; each immutable release validates itself through its own Python
runtime and schema contract.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import sqlite3
import struct
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from xml.etree import ElementTree

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase
from ..installer import OWNED_TASK_PREFIXES, V1_TASK_NAME
from ..platform.windows_subprocess import (
    headless_popen_kwargs,
    headless_run_kwargs,
    sanitized_env,
)
from ..supervisor.estop import EmergencyStop
from ..supervisor.worker import (
    pid_alive,
    process_creation_identity,
    terminate_owned_tree,
)
from .layout import (
    current_pointer,
    headless_launcher_cmd,
    previous_pointer,
    release_dir,
    rollback_runtime_marker,
    rollback_runtime_shim_dir,
)
from .release import (
    read_current,
    read_previous,
    read_release_json,
    release_authority_report,
    write_pointer,
)
from .runtime_identity import (
    ProcessQueryError,
    process_matches_home,
    query_processes_strict,
    supervisor_processes,
)

ROLLBACK_CONTRACT = "jarvis.cross-schema-rollback.v1"
ROLLBACK_RUNTIME_CONTRACT = "jarvis.cross-schema-rollback-runtime.v1"
STATE_DIRECTORY_NAME = "rollback-transactions"
ACTIVE_TRANSACTION_NAME = "ACTIVE.json"
STATE_FILE_NAME = "STATE.json"
LOCK_FILE_NAME = "LOCK"

PREPARED = "PREPARED"
PRECONDITIONS_VERIFIED = "PRECONDITIONS_VERIFIED"
SNAPSHOT_VALIDATED = "SNAPSHOT_VALIDATED"
FORENSIC_CAPTURED = "FORENSIC_CAPTURED"
DATABASE_RESTORE_PENDING = "DATABASE_RESTORE_PENDING"
DATABASE_RESTORED = "DATABASE_RESTORED"
DB_AUTHORITY_RESTORED = "DB_AUTHORITY_RESTORED"
POINTER_RESTORE_PENDING = "POINTER_RESTORE_PENDING"
POINTERS_RESTORED = "POINTERS_RESTORED"
PARITY_VERIFIED = "PARITY_VERIFIED"
PREDECESSOR_VALIDATED = "PREDECESSOR_VALIDATED"
SUPERVISOR_START_PENDING = "SUPERVISOR_START_PENDING"
SUPERVISOR_STARTED = "SUPERVISOR_STARTED"
HEALTH_ACCEPTED = "HEALTH_ACCEPTED"
POST_START_VERIFIED = "POST_START_VERIFIED"
SCHEDULER_ENABLE_PENDING = "SCHEDULER_ENABLE_PENDING"
SCHEDULER_ENABLED = "SCHEDULER_ENABLED"
COMPLETE = "COMPLETE"

PHASES = (
    PREPARED,
    PRECONDITIONS_VERIFIED,
    SNAPSHOT_VALIDATED,
    FORENSIC_CAPTURED,
    DATABASE_RESTORE_PENDING,
    DATABASE_RESTORED,
    DB_AUTHORITY_RESTORED,
    POINTER_RESTORE_PENDING,
    POINTERS_RESTORED,
    PARITY_VERIFIED,
    PREDECESSOR_VALIDATED,
    SUPERVISOR_START_PENDING,
    SUPERVISOR_STARTED,
    HEALTH_ACCEPTED,
    POST_START_VERIFIED,
    SCHEDULER_ENABLE_PENDING,
    SCHEDULER_ENABLED,
    COMPLETE,
)

FAULT_BEFORE_SNAPSHOT_VALIDATION = "before_snapshot_validation"
FAULT_AFTER_FORENSIC_CAPTURE = "after_forensic_capture"
FAULT_AFTER_V38_RESTORE = "after_v38_restore"
FAULT_AFTER_DB_AUTHORITY_RESTORE = "after_db_authority_restore"
FAULT_AFTER_POINTER_RESTORE = "after_pointer_restore"
FAULT_BEFORE_PREDECESSOR_START = "before_predecessor_start"
FAULT_AFTER_PREDECESSOR_START = "after_predecessor_start_before_health"
FAULT_BEFORE_SCHEDULER_REENABLE = "before_scheduler_reenable"
FAULT_DURING_SCHEDULER_REENABLE = "during_scheduler_reenable"

FAULT_POINTS = (
    FAULT_BEFORE_SNAPSHOT_VALIDATION,
    FAULT_AFTER_FORENSIC_CAPTURE,
    FAULT_AFTER_V38_RESTORE,
    FAULT_AFTER_DB_AUTHORITY_RESTORE,
    FAULT_AFTER_POINTER_RESTORE,
    FAULT_BEFORE_PREDECESSOR_START,
    FAULT_AFTER_PREDECESSOR_START,
    FAULT_BEFORE_SCHEDULER_REENABLE,
    FAULT_DURING_SCHEDULER_REENABLE,
)

_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_TRANSACTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_ACTIVE_MISSION_STATES = {
    "ACTION_SELECTION",
    "COMMITTING",
    "DISPATCHING",
    "EXECUTING",
    "RUNNING",
    "VERIFYING",
}
_EFFECT_EVENT_TYPES = (
    "effect.compensated",
    "effect.frozen",
    "effect.reconcile_mismatch",
    "effect.reconcile_pending",
    "effect.reconciled",
    "effect.terminal_truth",
    "system.action_completed",
    "system.action_failed",
    "system.action_started",
)
_WORK_CONTINUITY_TABLES = (
    "action_decisions",
    "agent_runs",
    "candidate_outcomes",
    "evidence_bundles",
    "final_outcomes",
    "mission_step_contracts",
    "missions",
    "verification_verdicts",
    "work_packages",
    "worker_manifests",
)
_CONTINUITY_EXCLUDED_TABLES = frozenset({
    "events",
    "leases",
    "release_manifests",
    "release_slots",
    "schema_migrations",
    "supervisor_state",
})
_EVENT_COLUMNS = (
    "sequence",
    "event_id",
    "event_type",
    "occurred_at",
    "recorded_at",
    "source_type",
    "source_id",
    "correlation_id",
    "causation_id",
    "idempotency_key",
    "schema_version",
    "payload_json",
    "payload_hash",
)
_TERMINAL_CONTROL_RESOLUTIONS = frozenset({
    ("recovered", "RUNNING"),
    ("started", "RUNNING"),
    ("stopped", "STOPPED"),
    ("stopped_for_restart", "STOPPED"),
})
_MAX_ACTIVATION_SUFFIX_EVENTS = 128
_LEGACY_LEASE_RECLAIM_REASON = (
    "legacy worker-loop lease expired without process identity"
)
# The independent Supervisor records exactly one of these reasons when it
# reclaims a dead predecessor's singleton lease (supervisor/service.py).
_SUPERVISOR_LEASE_RECLAIM_REASONS = frozenset({
    "supervisor process is no longer alive",
    "supervisor PID creation identity was reused",
})


class RollbackControllerError(RuntimeError):
    """Base class for an explicit rollback failure."""


class RollbackRefusal(RollbackControllerError):
    """A fail-closed precondition or invariant refusal."""


class RollbackInterruption(BaseException):
    """Test/rehearsal hard interruption that bypasses normal containment."""


@dataclass(frozen=True, slots=True)
class RollbackRequest:
    transaction_id: str
    install_root: Path
    foundry_home: Path
    snapshot_path: Path
    snapshot_sha256: str
    failed_source_sha: str
    failed_manifest_sha256: str
    failed_package_sha256: str
    predecessor_source_sha: str
    predecessor_manifest_sha256: str
    predecessor_package_sha256: str
    task_name: str = V1_TASK_NAME
    health_timeout_seconds: float = 90.0

    def __post_init__(self) -> None:
        transaction_id = str(self.transaction_id).strip()
        if not _TRANSACTION_RE.fullmatch(transaction_id):
            raise ValueError("transaction_id must be 1-96 safe filename characters")
        object.__setattr__(self, "transaction_id", transaction_id)
        for field_name in ("install_root", "foundry_home", "snapshot_path"):
            value = Path(getattr(self, field_name)).expanduser().resolve()
            object.__setattr__(self, field_name, value)
        for field_name in (
            "snapshot_sha256",
            "failed_manifest_sha256",
            "failed_package_sha256",
            "predecessor_manifest_sha256",
            "predecessor_package_sha256",
        ):
            value = str(getattr(self, field_name)).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(
                    f"{field_name} must be a 64-character SHA-256")
            object.__setattr__(self, field_name, value)
        for field_name in ("failed_source_sha", "predecessor_source_sha"):
            value = str(getattr(self, field_name)).strip().lower()
            if not _SHA_RE.fullmatch(value):
                raise ValueError(f"{field_name} must be a full Git object id")
            object.__setattr__(self, field_name, value)
        if self.failed_source_sha == self.predecessor_source_sha:
            raise ValueError("cross-schema rollback requires two distinct releases")
        task_name = str(self.task_name).strip().lstrip("\\")
        if not any(task_name.startswith(prefix) for prefix in OWNED_TASK_PREFIXES):
            raise ValueError("task_name is not JARVIS-owned")
        object.__setattr__(self, "task_name", task_name)
        timeout = float(self.health_timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("health_timeout_seconds must be positive and finite")
        object.__setattr__(self, "health_timeout_seconds", timeout)
        if self.snapshot_path == self.foundry_home / "foundry.db":
            raise ValueError("snapshot_path must not be the live database")

    def to_record(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "install_root": str(self.install_root),
            "foundry_home": str(self.foundry_home),
            "snapshot_path": str(self.snapshot_path),
            "snapshot_sha256": self.snapshot_sha256,
            "failed_source_sha": self.failed_source_sha,
            "failed_manifest_sha256": self.failed_manifest_sha256,
            "failed_package_sha256": self.failed_package_sha256,
            "predecessor_source_sha": self.predecessor_source_sha,
            "predecessor_manifest_sha256": (
                self.predecessor_manifest_sha256
            ),
            "predecessor_package_sha256": (
                self.predecessor_package_sha256
            ),
            "task_name": self.task_name,
            "health_timeout_seconds": self.health_timeout_seconds,
        }


class RollbackHost(Protocol):
    def scheduler_contract(self, request: RollbackRequest) -> dict[str, Any]:
        ...

    def set_scheduler_enabled(
        self, request: RollbackRequest, enabled: bool,
    ) -> dict[str, Any]:
        ...

    def owned_processes(self, request: RollbackRequest) -> list[dict[str, Any]]:
        ...

    def validate_release_runtime(
        self,
        request: RollbackRequest,
        source_sha: str,
        *,
        foundry_home: Path | None,
    ) -> dict[str, Any]:
        ...

    def validate_release_launcher(
        self, request: RollbackRequest, source_sha: str,
    ) -> dict[str, Any]:
        ...

    def build_continuity_baseline(
        self,
        request: RollbackRequest,
        *,
        source_sha: str,
        snapshot_path: Path,
        destination_path: Path,
        expected_schema: int,
    ) -> dict[str, Any]:
        ...

    def start_predecessor(
        self,
        request: RollbackRequest,
        *,
        runtime_identity: Mapping[str, Any],
        scheduler: Mapping[str, Any],
        shim_dir: Path,
        marker_path: Path,
        log_path: Path,
    ) -> dict[str, Any]:
        ...

    def terminate_started_process(
        self, request: RollbackRequest, process_record: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...

    def terminate_owned_processes(
        self,
        request: RollbackRequest,
        processes: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        ...

    def probe_predecessor(
        self,
        request: RollbackRequest,
        *,
        scheduler: Mapping[str, Any],
        expected_identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_package_digest(path: Path) -> dict[str, Any]:
    """Hash every byte and normalized relative path in an immutable release."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise RollbackRefusal(f"release package directory missing: {root}")
    digest = hashlib.sha256(b"jarvis.release-package.v1\0")
    file_count = 0
    total_bytes = 0
    try:
        entries = sorted(
            root.rglob("*"),
            key=lambda item: item.relative_to(root).as_posix(),
        )
    except OSError as error:
        raise RollbackRefusal(
            f"release package inventory failed: {root}") from error
    for entry in entries:
        if entry.is_symlink():
            raise RollbackRefusal(
                f"release package contains a symbolic link: {entry}")
        if entry.is_dir():
            continue
        if not entry.is_file():
            raise RollbackRefusal(
                f"release package contains an unsupported entry: {entry}")
        relative = entry.relative_to(root).as_posix().encode("utf-8")
        try:
            size = entry.stat().st_size
            content_hash = _sha256_file(entry)
        except OSError as error:
            raise RollbackRefusal(
                f"release package file could not be hashed: {entry}") from error
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(int(size).to_bytes(8, "big"))
        digest.update(bytes.fromhex(content_hash))
        file_count += 1
        total_bytes += int(size)
    if file_count == 0:
        raise RollbackRefusal(f"release package is empty: {root}")
    return {
        "contract": "jarvis.release-package.v1",
        "path": str(root),
        "sha256": digest.hexdigest(),
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _atomic_copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
    )


def _exclusive_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RollbackRefusal(f"unreadable JSON record: {path}") from error
    if not isinstance(payload, dict):
        raise RollbackRefusal(f"JSON record is not an object: {path}")
    return payload


def _load_adapter_pids_strict(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise RollbackRefusal(
            f"adapter PID registry is not a file: {path}")
    payload = _read_json(path)
    records: dict[str, dict[str, Any]] = {}
    for raw_run_id, raw_record in payload.items():
        run_id = str(raw_run_id).strip()
        if not run_id or not isinstance(raw_record, Mapping):
            raise RollbackRefusal(
                "adapter PID registry contains an invalid record")
        try:
            pid = int(raw_record["pid"])
        except (KeyError, TypeError, ValueError) as error:
            raise RollbackRefusal(
                f"adapter PID registry record {run_id!r} has no valid PID"
            ) from error
        if pid <= 0:
            raise RollbackRefusal(
                f"adapter PID registry record {run_id!r} has no valid PID")
        records[run_id] = dict(raw_record)
    return records


class _RollbackFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "_RollbackFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise RollbackRefusal(
                "another rollback controller holds the transaction lock") from error
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _manifest_identity(
    root: Path,
    source_sha: str,
    *,
    expected_manifest_sha256: str | None = None,
    expected_package_sha256: str | None = None,
) -> dict[str, Any]:
    directory = release_dir(source_sha, root)
    manifest_path = directory / "RELEASE.json"
    if not manifest_path.is_file():
        raise RollbackRefusal(f"release manifest missing: {manifest_path}")
    manifest_sha256 = _sha256_file(manifest_path)
    if (
        expected_manifest_sha256 is not None
        and manifest_sha256 != expected_manifest_sha256
    ):
        raise RollbackRefusal(
            f"release {source_sha} manifest SHA-256 does not match "
            "the frozen rollback input")
    try:
        manifest = read_release_json(manifest_path)
    except (OSError, ValueError) as error:
        raise RollbackRefusal(
            f"release manifest is unreadable: {manifest_path}") from error
    if manifest.get("source_sha") != source_sha:
        raise RollbackRefusal(
            f"manifest source_sha does not match {source_sha}")
    if manifest.get("git_commit") != source_sha:
        raise RollbackRefusal(
            f"manifest git_commit does not match {source_sha}")
    git_tree = str(manifest.get("git_tree") or "").strip().lower()
    release_id = str(manifest.get("release_id") or "").strip()
    schema_contract = manifest.get("schema_compatibility")
    if not _SHA_RE.fullmatch(git_tree):
        raise RollbackRefusal(f"release {source_sha} has invalid git_tree")
    if not release_id:
        raise RollbackRefusal(f"release {source_sha} has no release_id")
    if not isinstance(schema_contract, Mapping):
        raise RollbackRefusal(
            f"release {source_sha} has no schema_compatibility")
    try:
        package_schema = int(schema_contract["package_schema"])
        opens_min = int(schema_contract["opens_min"])
        opens_max = int(schema_contract["opens_max"])
    except (KeyError, TypeError, ValueError) as error:
        raise RollbackRefusal(
            f"release {source_sha} has invalid schema compatibility") from error
    if package_schema <= 0 or not opens_min <= package_schema <= opens_max:
        raise RollbackRefusal(
            f"release {source_sha} has an invalid schema window")
    package = None
    if expected_package_sha256 is not None:
        package = release_package_digest(directory)
        if package["sha256"] != expected_package_sha256:
            raise RollbackRefusal(
                f"release {source_sha} package SHA-256 does not match "
                "the frozen rollback input")
    python_exe = directory / ".venv" / "Scripts" / "python.exe"
    if not python_exe.is_file():
        raise RollbackRefusal(f"release Python missing: {python_exe}")
    return {
        "source_sha": source_sha,
        "release_id": release_id,
        "git_commit": source_sha,
        "git_tree": git_tree,
        "schema": package_schema,
        "opens_min": opens_min,
        "opens_max": opens_max,
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha256,
        "package": package,
        "python_exe": str(python_exe.resolve()),
        "previous_runtime": manifest.get("previous_runtime"),
    }


def _identity_fields(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return {
            "source_sha": str(value["source_sha"]),
            "release_id": str(value["release_id"]),
            "git_commit": str(value["git_commit"]),
            "git_tree": str(value["git_tree"]),
            "schema": int(value["schema"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _feed_digest_value(digest: Any, value: Any) -> None:
    if value is None:
        encoded = b""
        kind = b"N"
    elif isinstance(value, bytes):
        encoded = value
        kind = b"B"
    elif isinstance(value, int):
        encoded = str(value).encode("ascii")
        kind = b"I"
    elif isinstance(value, float):
        encoded = struct.pack(">d", value)
        kind = b"F"
    else:
        encoded = str(value).encode("utf-8", errors="surrogatepass")
        kind = b"T"
    digest.update(kind)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def database_logical_digest(path: Path) -> dict[str, Any]:
    """Hash SQLite schema, row order, values, and autoincrement state."""
    database_path = Path(path).resolve()
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro",
        uri=True,
        timeout=10,
    )
    digest = hashlib.sha256()
    table_counts: dict[str, int] = {}
    try:
        entries = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_autoindex_%' "
            "ORDER BY type, name"
        ).fetchall()
        for entry in entries:
            for value in entry:
                _feed_digest_value(digest, value)
        table_names = sorted({
            str(entry[1])
            for entry in entries
            if entry[0] == "table"
            and (
                not str(entry[1]).startswith("sqlite_")
                or str(entry[1]) == "sqlite_sequence"
            )
        })
        for table_name in table_names:
            table_digest = hashlib.sha256()
            _feed_digest_value(table_digest, table_name)
            info = connection.execute(
                f"PRAGMA table_info({_quoted_identifier(table_name)})"
            ).fetchall()
            primary = [
                (int(column[5]), str(column[1]))
                for column in info if int(column[5] or 0) > 0
            ]
            order_clause = "rowid"
            schema_sql = next(
                (str(entry[3] or "") for entry in entries
                 if entry[0] == "table" and entry[1] == table_name),
                "",
            )
            if "WITHOUT ROWID" in schema_sql.upper():
                if not primary:
                    raise RollbackRefusal(
                        f"WITHOUT ROWID table has no primary key: {table_name}")
                order_clause = ", ".join(
                    _quoted_identifier(name)
                    for _, name in sorted(primary)
                )
            query = (
                f"SELECT * FROM {_quoted_identifier(table_name)} "
                f"ORDER BY {order_clause}"
            )
            cursor = connection.execute(query)
            count = 0
            while True:
                rows = cursor.fetchmany(512)
                if not rows:
                    break
                for row in rows:
                    table_digest.update(b"R")
                    for value in row:
                        _feed_digest_value(table_digest, value)
                    count += 1
            table_counts[table_name] = count
            _feed_digest_value(digest, table_name)
            digest.update(table_digest.digest())
        return {
            "sha256": digest.hexdigest(),
            "table_counts": table_counts,
        }
    finally:
        connection.close()


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_continuity_digest(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, Any]:
    if not _table_exists(connection, table_name):
        return {
            "present": False,
            "row_count": 0,
            "sha256": None,
        }
    info = connection.execute(
        f"PRAGMA table_info({_quoted_identifier(table_name)})"
    ).fetchall()
    columns = [str(column[1]) for column in info]
    primary = [
        (int(column[5]), str(column[1]))
        for column in info if int(column[5] or 0) > 0
    ]
    schema_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    schema_sql = str(schema_row[0] or "") if schema_row else ""
    order_clause = "rowid"
    if "WITHOUT ROWID" in schema_sql.upper():
        if not primary:
            raise RollbackRefusal(
                f"continuity table has no stable row order: {table_name}")
        order_clause = ", ".join(
            _quoted_identifier(name) for _, name in sorted(primary)
        )
    digest = hashlib.sha256()
    _feed_digest_value(digest, table_name)
    for column in columns:
        _feed_digest_value(digest, column)
    row_count = 0
    cursor = connection.execute(
        f"SELECT * FROM {_quoted_identifier(table_name)} "
        f"ORDER BY {order_clause}"
    )
    while True:
        rows = cursor.fetchmany(512)
        if not rows:
            break
        for row in rows:
            digest.update(b"R")
            for value in row:
                _feed_digest_value(digest, value)
            row_count += 1
    return {
        "present": True,
        "row_count": row_count,
        "sha256": digest.hexdigest(),
    }


def _event_ledger_digest(
    connection: sqlite3.Connection,
    *,
    maximum_sequence: int | None = None,
) -> dict[str, Any]:
    if not _table_exists(connection, "events"):
        return {
            "present": False,
            "row_count": 0,
            "sha256": None,
            "minimum_sequence": None,
            "maximum_sequence": None,
            "event_types": {},
        }
    observed_columns = [
        str(column[1])
        for column in connection.execute("PRAGMA table_info(events)").fetchall()
    ]
    if not set(_EVENT_COLUMNS) <= set(observed_columns):
        raise RollbackRefusal(
            "events table does not expose the durable ledger contract")
    columns = list(_EVENT_COLUMNS)
    predicate = "WHERE sequence <= ?" if maximum_sequence is not None else ""
    parameters: tuple[Any, ...] = (
        (int(maximum_sequence),)
        if maximum_sequence is not None else ()
    )
    digest = hashlib.sha256()
    _feed_digest_value(digest, "events")
    for column in columns:
        _feed_digest_value(digest, column)
    row_count = 0
    minimum_observed: int | None = None
    maximum_observed: int | None = None
    event_types: dict[str, int] = {}
    cursor = connection.execute(
        "SELECT "
        + ", ".join(_quoted_identifier(column) for column in columns)
        + f" FROM events {predicate} ORDER BY sequence",
        parameters,
    )
    while True:
        rows = cursor.fetchmany(512)
        if not rows:
            break
        for row in rows:
            digest.update(b"R")
            for value in row:
                _feed_digest_value(digest, value)
            sequence = int(row[0])
            if minimum_observed is None:
                minimum_observed = sequence
            maximum_observed = sequence
            event_type = str(row[columns.index("event_type")])
            event_types[event_type] = event_types.get(event_type, 0) + 1
            row_count += 1
    return {
        "present": True,
        "row_count": row_count,
        "sha256": digest.hexdigest(),
        "minimum_sequence": minimum_observed,
        "maximum_sequence": maximum_observed,
        "event_types": event_types,
    }


def _row_digest(columns: Sequence[str], row: Sequence[Any]) -> str:
    digest = hashlib.sha256()
    for column in columns:
        _feed_digest_value(digest, column)
    for value in row:
        _feed_digest_value(digest, value)
    return digest.hexdigest()


def _release_projection(connection: sqlite3.Connection) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for table_name, key_name in (
        ("release_manifests", "release_id"),
        ("release_slots", "slot"),
    ):
        if not _table_exists(connection, table_name):
            projection[table_name] = {
                "present": False,
                "columns": [],
                "rows": {},
            }
            continue
        columns = [
            str(column[1])
            for column in connection.execute(
                f"PRAGMA table_info({_quoted_identifier(table_name)})"
            ).fetchall()
        ]
        if key_name not in columns:
            raise RollbackRefusal(
                f"{table_name} has no {key_name} authority key")
        key_index = columns.index(key_name)
        rows: dict[str, Any] = {}
        cursor = connection.execute(
            "SELECT "
            + ", ".join(_quoted_identifier(column) for column in columns)
            + f" FROM {_quoted_identifier(table_name)} "
            + f"ORDER BY {_quoted_identifier(key_name)}"
        )
        for row in cursor.fetchall():
            key = str(row[key_index] or "")
            if not key or key in rows:
                raise RollbackRefusal(
                    f"{table_name} contains an invalid authority key")
            rows[key] = {
                "sha256": _row_digest(columns, row),
                "values": dict(zip(columns, row, strict=True)),
            }
        projection[table_name] = {
            "present": True,
            "columns": columns,
            "rows": rows,
        }
    return projection


def _event_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(row.get("payload_json") or ""))
    except (TypeError, ValueError) as error:
        raise RollbackRefusal(
            "activation suffix contains invalid event payload JSON") from error
    if not isinstance(payload, dict):
        raise RollbackRefusal(
            "activation suffix event payload is not an object")
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    observed_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if observed_hash != row.get("payload_hash"):
        raise RollbackRefusal(
            "activation suffix event payload hash is invalid")
    return payload


def _baseline_control_history(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    history: dict[str, dict[str, Any]] = {}
    if not _table_exists(connection, "events"):
        return history
    rows = connection.execute(
        "SELECT sequence, event_type, occurred_at, payload_json "
        "FROM events WHERE event_type IN "
        "('runtime.control.requested', 'runtime.control.resolved') "
        "ORDER BY sequence"
    ).fetchall()
    for sequence, event_type, occurred_at, payload_json in rows:
        try:
            payload = json.loads(str(payload_json or ""))
        except (TypeError, ValueError) as error:
            raise RollbackRefusal(
                "baseline runtime-control event payload is invalid") from error
        if not isinstance(payload, Mapping):
            raise RollbackRefusal(
                "baseline runtime-control event payload is not an object")
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            continue
        record = history.setdefault(request_id, {
            "requested": None,
            "terminal_resolutions": [],
        })
        if event_type == "runtime.control.requested":
            record["requested"] = {
                "sequence": int(sequence),
                "occurred_at": str(occurred_at),
                "payload": dict(payload),
            }
            continue
        phase = str(payload.get("phase") or "")
        status = str(payload.get("status") or "").upper()
        if (phase, status) in _TERMINAL_CONTROL_RESOLUTIONS:
            record["terminal_resolutions"].append({
                "sequence": int(sequence),
                "occurred_at": str(occurred_at),
                "payload": dict(payload),
            })
    return history


def _validate_stale_resolution(
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    control_history: Mapping[str, Mapping[str, Any]],
    failed_identity: Mapping[str, Any],
    supervisor_instance: str,
) -> None:
    request_id = str(payload.get("request_id") or "")
    record = control_history.get(request_id)
    requested = record.get("requested") if isinstance(record, Mapping) else None
    terminal = (
        record.get("terminal_resolutions")
        if isinstance(record, Mapping) else None
    )
    if (
        not request_id
        or not isinstance(requested, Mapping)
        or not isinstance(terminal, list)
        or not terminal
        or payload.get("phase") != "stale_request"
        or payload.get("request_status") != "STALE"
        or str(payload.get("status") or "").upper() != "RUNNING"
        or payload.get("release_id") != failed_identity.get("source_sha")
        or payload.get("instance_id") != supervisor_instance
        or row.get("source_id") != supervisor_instance
    ):
        raise RollbackRefusal(
            "activation suffix contains an unproven runtime-control resolution")
    requested_payload = requested.get("payload")
    if (
        not isinstance(requested_payload, Mapping)
        or requested_payload.get("action") != payload.get("action")
    ):
        raise RollbackRefusal(
            "stale runtime-control resolution does not match its request")
    expires_at = str(requested_payload.get("expires_at") or "")
    try:
        if parse(expires_at) > parse(str(row.get("occurred_at") or "")):
            raise RollbackRefusal(
                "stale runtime-control resolution predates request expiry")
    except (TypeError, ValueError) as error:
        raise RollbackRefusal(
            "runtime-control request expiry evidence is invalid") from error


def _validate_activation_event_suffix(
    baseline_path: Path,
    failed_path: Path,
    *,
    baseline_events: Mapping[str, Any],
    failed_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any],
    failed_manifest_sha256: str,
    stop_record: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_connection = sqlite3.connect(
        f"file:{Path(baseline_path).resolve().as_posix()}?mode=ro",
        uri=True,
        timeout=10,
    )
    failed_connection = sqlite3.connect(
        f"file:{Path(failed_path).resolve().as_posix()}?mode=ro",
        uri=True,
        timeout=10,
    )
    try:
        maximum_sequence = baseline_events.get("maximum_sequence")
        prefix = _event_ledger_digest(
            failed_connection,
            maximum_sequence=(
                int(maximum_sequence)
                if maximum_sequence is not None else -1
            ),
        )
        for field in (
            "present",
            "row_count",
            "sha256",
            "minimum_sequence",
            "maximum_sequence",
            "event_types",
        ):
            if prefix.get(field) != baseline_events.get(field):
                raise RollbackRefusal(
                    "failed database no longer preserves the migrated "
                    "snapshot event prefix")
        control_history = _baseline_control_history(baseline_connection)
        start_sequence = int(maximum_sequence or 0)
        cursor = failed_connection.execute(
            "SELECT "
            + ", ".join(
                _quoted_identifier(column) for column in _EVENT_COLUMNS
            )
            + " FROM events WHERE sequence > ? ORDER BY sequence",
            (start_sequence,),
        )
        raw_rows = cursor.fetchmany(_MAX_ACTIVATION_SUFFIX_EVENTS + 1)
        if len(raw_rows) > _MAX_ACTIVATION_SUFFIX_EVENTS:
            raise RollbackRefusal(
                "activation event suffix exceeds the bounded rollback contract")
        rows = [
            dict(zip(_EVENT_COLUMNS, row, strict=True))
            for row in raw_rows
        ]
        payloads = [_event_payload(row) for row in rows]
        event_types: dict[str, int] = {}
        for row in rows:
            event_type = str(row["event_type"])
            event_types[event_type] = event_types.get(event_type, 0) + 1
            if (
                row.get("source_type") != "supervisor"
                or int(row.get("schema_version") or 0) != 1
            ):
                raise RollbackRefusal(
                    "activation suffix contains an event outside Supervisor "
                    "release authority")

        claims = [
            (row, payload)
            for row, payload in zip(rows, payloads, strict=True)
            if row["event_type"] == "runtime.control.claiming"
        ]
        if len(claims) > 1:
            raise RollbackRefusal(
                "activation suffix contains multiple Supervisor claims")
        supervisor_instance = ""
        if claims:
            claim_row, claim = claims[0]
            supervisor_instance = str(claim.get("instance_id") or "")
            if (
                not supervisor_instance
                or claim.get("request_id") is not None
                or claim.get("release_id") != failed_identity.get("source_sha")
                or claim_row.get("source_id") != supervisor_instance
            ):
                raise RollbackRefusal(
                    "activation suffix Supervisor claim is not release-bound")

        manifest_count = 0
        slot_names: set[str] = set()
        stopped_count = 0
        stale_request_ids: set[str] = set()
        allowed_types = {
            "emergency_stop.frozen",
            "release.manifest_recorded",
            "release.slot_changed",
            "runtime.control.claiming",
            "runtime.control.lease_reclaimed",
            "runtime.control.resolved",
            "supervisor.stopped",
            "worker_loop.lease_reclaimed",
        }
        for row, payload in zip(rows, payloads, strict=True):
            event_type = str(row["event_type"])
            if event_type not in allowed_types:
                raise RollbackRefusal(
                    "activation suffix contains post-snapshot authority: "
                    f"{event_type}")
            if event_type == "release.manifest_recorded":
                manifest_count += 1
                runtime_identity = payload.get("runtime_identity")
                artifact_hashes = payload.get("artifact_hashes")
                if (
                    manifest_count > 1
                    or row.get("source_id") != "deployment-authority"
                    or payload.get("release_id")
                    != failed_identity.get("release_id")
                    or payload.get("source_commit")
                    != failed_identity.get("source_sha")
                    or _identity_fields(runtime_identity)
                    != _identity_fields(failed_identity)
                    or not isinstance(artifact_hashes, Mapping)
                    or artifact_hashes.get("RELEASE.json")
                    != failed_manifest_sha256
                ):
                    raise RollbackRefusal(
                        "activation suffix release manifest is not the failed "
                        "immutable release")
                continue
            if event_type == "release.slot_changed":
                slot = str(payload.get("slot") or "")
                if (
                    slot not in {"live", "previous"}
                    or slot in slot_names
                    or row.get("source_id") != "deployment-authority"
                ):
                    raise RollbackRefusal(
                        "activation suffix changed an unapproved release slot")
                slot_names.add(slot)
                expected_release_id = (
                    failed_identity.get("release_id")
                    if slot == "live"
                    else predecessor_identity.get("release_id")
                )
                if payload.get("release_id") != expected_release_id:
                    raise RollbackRefusal(
                        "activation suffix release slot has the wrong identity")
                if (
                    slot == "live"
                    and payload.get("previous_release_id")
                    != predecessor_identity.get("release_id")
                ):
                    raise RollbackRefusal(
                        "activation live-slot event lost predecessor authority")
                continue
            if event_type == "worker_loop.lease_reclaimed":
                expires_at = str(payload.get("lease_expires_at") or "")
                if (
                    row.get("source_id") != "worker-loop-recovery"
                    or payload.get("pid") is not None
                    or payload.get("reason") != _LEGACY_LEASE_RECLAIM_REASON
                    or not str(payload.get("instance_id") or "")
                ):
                    raise RollbackRefusal(
                        "activation suffix contains an unproven lease reclaim")
                try:
                    if parse(expires_at) > parse(str(row["occurred_at"])):
                        raise RollbackRefusal(
                            "activation suffix reclaimed a live worker lease")
                except (TypeError, ValueError) as error:
                    raise RollbackRefusal(
                        "activation suffix lease expiry is invalid") from error
                continue
            if event_type == "runtime.control.claiming":
                continue
            if event_type == "runtime.control.lease_reclaimed":
                # The independent Supervisor reclaims a dead predecessor's
                # singleton lease while taking over the failed release
                # (supervisor/control.py: record_lease_reclaimed).  The event
                # is authority-bearing, so it is accepted only when it proves
                # the exact dead owner it reclaimed: the reconciler source,
                # a positive pid, and the two fail-closed reason strings the
                # service can produce.  The reclaimed owner must not be the
                # activation Supervisor itself.
                if (
                    row.get("source_id") != "lease-reconciler"
                    or payload.get("reason") not in _SUPERVISOR_LEASE_RECLAIM_REASONS
                    or not str(payload.get("owner_id") or "")
                    or not isinstance(payload.get("pid"), int)
                    or int(payload["pid"]) <= 0
                ):
                    raise RollbackRefusal(
                        "activation suffix contains an unproven Supervisor "
                        "lease reclaim")
                continue
            if not supervisor_instance:
                raise RollbackRefusal(
                    "activation lifecycle event has no exact Supervisor claim")
            if (
                row.get("source_id") != supervisor_instance
                or payload.get("instance_id") != supervisor_instance
            ):
                raise RollbackRefusal(
                    "activation lifecycle event changed Supervisor identity")
            if event_type == "runtime.control.resolved":
                request_id = str(payload.get("request_id") or "")
                if request_id in stale_request_ids:
                    raise RollbackRefusal(
                        "activation suffix repeats a stale request resolution")
                _validate_stale_resolution(
                    row,
                    payload,
                    control_history=control_history,
                    failed_identity=failed_identity,
                    supervisor_instance=supervisor_instance,
                )
                stale_request_ids.add(request_id)
                continue
            if event_type == "emergency_stop.frozen":
                if payload.get("stop_id") != stop_record.get("stop_id"):
                    raise RollbackRefusal(
                        "activation freeze event does not match durable STOP")
                if (
                    payload.get("release_id") is not None
                    and payload.get("release_id")
                    != failed_identity.get("source_sha")
                ):
                    raise RollbackRefusal(
                        "activation freeze event has the wrong release")
                if payload.get("control_request_id") is not None:
                    raise RollbackRefusal(
                        "activation freeze event consumed an owner request")
                continue
            if event_type == "supervisor.stopped":
                stopped_count += 1
                if (
                    stopped_count > 1
                    or payload.get("release_id")
                    != failed_identity.get("source_sha")
                    or payload.get("control_request_id") is not None
                ):
                    raise RollbackRefusal(
                        "activation stop event is not failed-release cleanup")
                continue
        return {
            "status": "PRESERVED_WITH_BOUND_ACTIVATION_SUFFIX",
            "prefix_row_count": prefix["row_count"],
            "prefix_sha256": prefix["sha256"],
            "suffix_row_count": len(rows),
            "suffix_event_types": event_types,
            "stale_terminal_request_ids": sorted(stale_request_ids),
        }
    finally:
        failed_connection.close()
        baseline_connection.close()


def _validate_release_projection_continuity(
    baseline: Mapping[str, Any],
    failed: Mapping[str, Any],
    *,
    failed_identity: Mapping[str, Any],
    predecessor_identity: Mapping[str, Any],
    failed_manifest_sha256: str,
) -> dict[str, Any]:
    baseline_manifests = (
        (baseline.get("release_manifests") or {}).get("rows") or {}
    )
    failed_manifests = (
        (failed.get("release_manifests") or {}).get("rows") or {}
    )
    if not isinstance(baseline_manifests, Mapping) or not isinstance(
        failed_manifests, Mapping
    ):
        raise RollbackRefusal(
            "release manifest continuity projection is invalid")
    changed_manifests = [
        release_id
        for release_id, row in baseline_manifests.items()
        if failed_manifests.get(release_id) != row
    ]
    new_manifests = set(failed_manifests) - set(baseline_manifests)
    failed_release_id = str(failed_identity.get("release_id") or "")
    if (
        changed_manifests
        or new_manifests - {failed_release_id}
        or set(baseline_manifests) - set(failed_manifests)
    ):
        raise RollbackRefusal(
            "failed activation changed unrelated release manifest authority")
    failed_manifest = failed_manifests.get(failed_release_id)
    failed_values = (
        failed_manifest.get("values")
        if isinstance(failed_manifest, Mapping) else None
    )
    if not isinstance(failed_values, Mapping):
        raise RollbackRefusal(
            "failed release manifest projection is missing")
    try:
        runtime_identity = json.loads(
            str(failed_values.get("runtime_identity_json") or ""))
        artifact_hashes = json.loads(
            str(failed_values.get("artifact_hashes_json") or ""))
    except (TypeError, ValueError) as error:
        raise RollbackRefusal(
            "failed release manifest projection is invalid") from error
    if (
        failed_values.get("source_commit") != failed_identity.get("source_sha")
        or _identity_fields(runtime_identity) != _identity_fields(failed_identity)
        or not isinstance(artifact_hashes, Mapping)
        or artifact_hashes.get("RELEASE.json")
        not in {None, failed_manifest_sha256}
    ):
        raise RollbackRefusal(
            "failed release manifest projection does not match its package")

    baseline_slots = (
        (baseline.get("release_slots") or {}).get("rows") or {}
    )
    failed_slots = (
        (failed.get("release_slots") or {}).get("rows") or {}
    )
    if not isinstance(baseline_slots, Mapping) or not isinstance(
        failed_slots, Mapping
    ):
        raise RollbackRefusal(
            "release slot continuity projection is invalid")
    protected_slots = (
        set(baseline_slots) | set(failed_slots)
    ) - {"live", "previous"}
    changed_slots = [
        slot
        for slot in sorted(protected_slots)
        if baseline_slots.get(slot) != failed_slots.get(slot)
    ]
    if changed_slots:
        raise RollbackRefusal(
            "failed activation changed protected release slots: "
            + ", ".join(changed_slots)
        )
    baseline_live = baseline_slots.get("live")
    failed_live = failed_slots.get("live")
    failed_previous = failed_slots.get("previous")
    baseline_live_values = (
        baseline_live.get("values")
        if isinstance(baseline_live, Mapping) else None
    )
    failed_live_values = (
        failed_live.get("values")
        if isinstance(failed_live, Mapping) else None
    )
    failed_previous_values = (
        failed_previous.get("values")
        if isinstance(failed_previous, Mapping) else None
    )
    if (
        not isinstance(baseline_live_values, Mapping)
        or baseline_live_values.get("release_id")
        != predecessor_identity.get("release_id")
        or not isinstance(failed_live_values, Mapping)
        or failed_live_values.get("release_id")
        != failed_identity.get("release_id")
        or not isinstance(failed_previous_values, Mapping)
        or failed_previous_values.get("release_id")
        != predecessor_identity.get("release_id")
    ):
        raise RollbackRefusal(
            "failed activation release slot pair is not exact")
    return {
        "status": "PRESERVED_WITH_EXACT_RUNTIME_SLOT_TRANSITION",
        "unchanged_manifest_count": len(baseline_manifests),
        "new_manifest_ids": sorted(new_manifests),
        "protected_slots": sorted(protected_slots),
        "baseline_live_release_id": baseline_live_values.get("release_id"),
        "failed_previous_release_id": failed_previous_values.get("release_id"),
        "failed_live_release_id": failed_live_values.get("release_id"),
    }


def database_rollback_continuity(path: Path) -> dict[str, Any]:
    """Fingerprint every durable authority not owned by rollback bookkeeping."""
    database_path = Path(path).expanduser().resolve()
    if not database_path.is_file():
        raise RollbackRefusal(f"database missing: {database_path}")
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro",
        uri=True,
        timeout=10,
    )
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        integrity_check = str(
            connection.execute("PRAGMA integrity_check").fetchone()[0])
        table_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
            if str(row[0]) not in _CONTINUITY_EXCLUDED_TABLES
        ]
        tables = {
            table_name: _table_continuity_digest(connection, table_name)
            for table_name in table_names
        }
        return {
            "path": str(database_path),
            "file_sha256": _sha256_file(database_path),
            "schema": _schema_version(connection),
            "quick_check": quick_check,
            "integrity_check": integrity_check,
            "tables": tables,
            "events": _event_ledger_digest(connection),
            "release_projection": _release_projection(connection),
            "excluded_tables": sorted(_CONTINUITY_EXCLUDED_TABLES),
        }
    finally:
        connection.close()


def _schema_version(connection: sqlite3.Connection) -> int:
    if not _table_exists(connection, "schema_migrations"):
        return 0
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0] if row else 0)


def _database_authority(connection: sqlite3.Connection) -> dict[str, Any]:
    if not (
        _table_exists(connection, "release_slots")
        and _table_exists(connection, "release_manifests")
    ):
        return {
            "release_id": None,
            "source_sha": None,
            "runtime_identity": None,
            "status": "UNAVAILABLE",
        }
    slot = connection.execute(
        "SELECT release_id, updated_at, last_event_id "
        "FROM release_slots WHERE slot = 'live'"
    ).fetchone()
    if slot is None or not slot[0]:
        return {
            "release_id": None,
            "source_sha": None,
            "runtime_identity": None,
            "status": "LIVE_SLOT_EMPTY",
        }
    manifest = connection.execute(
        "SELECT source_commit, runtime_identity_json "
        "FROM release_manifests WHERE release_id = ?",
        (slot[0],),
    ).fetchone()
    if manifest is None:
        return {
            "release_id": str(slot[0]),
            "source_sha": None,
            "runtime_identity": None,
            "status": "MANIFEST_MISSING",
        }
    try:
        runtime_identity = json.loads(str(manifest[1] or "{}"))
    except (TypeError, ValueError):
        runtime_identity = None
    if not isinstance(runtime_identity, Mapping):
        runtime_identity = None
    return {
        "release_id": str(slot[0]),
        "source_sha": str(manifest[0]),
        "runtime_identity": dict(runtime_identity) if runtime_identity else None,
        "updated_at": slot[1],
        "last_event_id": slot[2],
        "status": "RECORDED",
    }


def _effect_barrier(connection: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(connection, "system_actions"):
        return {
            "status": "UNAVAILABLE",
            "unresolved": ["system_actions table missing"],
            "duplicate_starts": [],
            "event_count": 0,
            "event_sha256": None,
            "journal_counts": {},
        }
    journal_rows = connection.execute(
        "SELECT action_key, status FROM system_actions ORDER BY action_key"
    ).fetchall()
    journal_continuity = _table_continuity_digest(
        connection, "system_actions")
    journal_counts: dict[str, int] = {}
    unresolved: list[str] = []
    for action_key, status_value in journal_rows:
        status = str(status_value or "UNKNOWN").upper()
        journal_counts[status] = journal_counts.get(status, 0) + 1
        if status == "STARTED":
            unresolved.append(str(action_key))
            continue
        if status != "FROZEN" or not _table_exists(connection, "events"):
            continue
        rows = connection.execute(
            "SELECT sequence, event_type, payload_json "
            "FROM events WHERE event_type IN ("
            + ", ".join("?" for _ in _EFFECT_EVENT_TYPES)
            + ") AND json_extract(payload_json, '$.action_key') = ? "
            "ORDER BY sequence",
            (*_EFFECT_EVENT_TYPES, action_key),
        ).fetchall()
        last_uncertain = 0
        last_reconciled = 0
        last_terminal_truth = 0
        for sequence, event_type, raw_payload in rows:
            sequence_value = int(sequence)
            if event_type in {
                "effect.frozen",
                "effect.reconcile_mismatch",
                "effect.reconcile_pending",
            }:
                last_uncertain = max(last_uncertain, sequence_value)
            elif event_type == "effect.reconciled":
                last_reconciled = max(last_reconciled, sequence_value)
            elif event_type == "effect.terminal_truth":
                try:
                    payload = json.loads(str(raw_payload))
                except (TypeError, ValueError):
                    payload = {}
                if (
                    payload.get("accepted") is True
                    and payload.get("reconciled") is True
                ):
                    last_terminal_truth = max(
                        last_terminal_truth, sequence_value)
        if max(last_reconciled, last_terminal_truth) <= last_uncertain:
            unresolved.append(str(action_key))

    duplicate_starts: list[str] = []
    event_digest = hashlib.sha256()
    effect_event_count = 0
    if _table_exists(connection, "events"):
        duplicate_starts = [
            str(row[0])
            for row in connection.execute(
                "SELECT json_extract(payload_json, '$.action_key'), COUNT(*) "
                "FROM events WHERE event_type = 'system.action_started' "
                "GROUP BY json_extract(payload_json, '$.action_key') "
                "HAVING COUNT(*) > 1"
            ).fetchall()
            if row[0] is not None
        ]
        rows = connection.execute(
            "SELECT sequence, event_type, payload_hash, payload_json "
            "FROM events WHERE event_type IN ("
            + ", ".join("?" for _ in _EFFECT_EVENT_TYPES)
            + ") ORDER BY sequence",
            _EFFECT_EVENT_TYPES,
        )
        for row in rows:
            for value in row:
                _feed_digest_value(event_digest, value)
            effect_event_count += 1
    return {
        "status": (
            "CLEAR" if not unresolved and not duplicate_starts else "BLOCKED"
        ),
        "unresolved": unresolved,
        "duplicate_starts": duplicate_starts,
        "event_count": effect_event_count,
        "event_sha256": event_digest.hexdigest(),
        "journal_counts": journal_counts,
        "journal_continuity": journal_continuity,
    }


def _work_barrier(connection: sqlite3.Connection) -> dict[str, Any]:
    running_agents: list[str] = []
    active_missions: list[str] = []
    active_leases: list[str] = []
    if _table_exists(connection, "agent_runs"):
        running_agents = [
            str(row[0])
            for row in connection.execute(
                "SELECT run_id FROM agent_runs WHERE status = 'RUNNING' "
                "ORDER BY run_id"
            ).fetchall()
        ]
    if _table_exists(connection, "missions"):
        placeholders = ", ".join("?" for _ in _ACTIVE_MISSION_STATES)
        active_missions = [
            str(row[0])
            for row in connection.execute(
                "SELECT mission_id FROM missions WHERE state IN ("
                + placeholders + ") ORDER BY mission_id",
                tuple(sorted(_ACTIVE_MISSION_STATES)),
            ).fetchall()
        ]
    if _table_exists(connection, "leases"):
        now = utc_now()
        rows = connection.execute(
            "SELECT lease_key, expires_at FROM leases "
            "WHERE lease_key LIKE 'worker:%' OR lease_key LIKE 'role:%' "
            "ORDER BY lease_key"
        ).fetchall()
        for lease_key, expires_at in rows:
            try:
                is_active = expires_at is None or parse(str(expires_at)) > now
            except (TypeError, ValueError, OverflowError):
                is_active = True
            if is_active:
                active_leases.append(str(lease_key))
    continuity_tables = {
        table_name: _table_continuity_digest(connection, table_name)
        for table_name in _WORK_CONTINUITY_TABLES
    }
    return {
        "status": (
            "CLEAR"
            if not running_agents and not active_missions and not active_leases
            else "BLOCKED"
        ),
        "running_agent_runs": running_agents,
        "active_missions": active_missions,
        "active_worker_leases": active_leases,
        "continuity_tables": continuity_tables,
    }


def inspect_database(path: Path, *, include_logical_digest: bool = True) -> dict[str, Any]:
    database_path = Path(path).expanduser().resolve()
    if not database_path.is_file():
        raise RollbackRefusal(f"database missing: {database_path}")
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro",
        uri=True,
        timeout=10,
    )
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        integrity_check = str(
            connection.execute("PRAGMA integrity_check").fetchone()[0])
        event_count = (
            int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            if _table_exists(connection, "events") else 0
        )
        maximum_sequence = (
            int(connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0])
            if _table_exists(connection, "events") else 0
        )
        result = {
            "path": str(database_path),
            "file_sha256": _sha256_file(database_path),
            "quick_check": quick_check,
            "integrity_check": integrity_check,
            "schema": _schema_version(connection),
            "event_count": event_count,
            "maximum_event_sequence": maximum_sequence,
            "authority": _database_authority(connection),
            "effects": _effect_barrier(connection),
            "work": _work_barrier(connection),
        }
    finally:
        connection.close()
    if include_logical_digest:
        result["logical"] = database_logical_digest(database_path)
    return result


FROZEN_SITE_CUSTOMIZE = r'''"""Cross-schema rollback STOP-runtime compatibility hook."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _abort(message):
    try:
        os.write(2, ("ROLLBACK_RUNTIME_FAIL_CLOSED: " + str(message) + "\n").encode("utf-8"))
    finally:
        os._exit(2)


def _same_path(left, right):
    return os.path.normcase(str(Path(left).expanduser().resolve())) == os.path.normcase(
        str(Path(right).expanduser().resolve())
    )


def _retire_completed_marker(marker_path, *, root, home, source_sha, shim_hash):
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as error:
        _abort("completed marker is unreadable: " + type(error).__name__)
    if (
        payload.get("contract") != "jarvis.cross-schema-rollback-runtime.v1"
        or payload.get("status") != "COMPLETE_FROZEN_RUNTIME"
        or payload.get("source_sha") != source_sha
        or not _same_path(payload.get("install_root", ""), root)
        or not _same_path(payload.get("foundry_home", ""), home)
        or payload.get("sitecustomize_sha256") != shim_hash
    ):
        _abort("completed marker changed before owner RESUME")
    transaction_id = str(payload.get("transaction_id") or "")
    state_path = Path(
        str(payload.get("transaction_state_path") or "")
    ).expanduser().resolve()
    expected_state = (
        root / "rollback-transactions" / transaction_id / "STATE.json"
    ).resolve()
    if not transaction_id or not _same_path(state_path, expected_state):
        _abort("completed rollback transaction state path is invalid")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as error:
        _abort("completed rollback transaction state is unreadable: " + type(error).__name__)
    if not isinstance(state, dict):
        _abort("completed rollback transaction state is not an object")
    request = state.get("request")
    evidence = state.get("evidence")
    prepared_marker = (
        evidence.get("runtime_bridge_completion")
        if isinstance(evidence, dict) else None
    )
    completion_report = (
        evidence.get("completion_report")
        if isinstance(evidence, dict) else None
    )
    active_path = root / "rollback-transactions" / "ACTIVE.json"
    if (
        state.get("contract") != "jarvis.cross-schema-rollback.v1"
        or state.get("transaction_id") != transaction_id
        or state.get("phase") != "COMPLETE"
        or state.get("resume_authorized") is not True
        or not isinstance(request, dict)
        or request.get("predecessor_source_sha") != source_sha
        or not _same_path(request.get("install_root", ""), root)
        or not _same_path(request.get("foundry_home", ""), home)
        or active_path.exists()
        or not isinstance(prepared_marker, dict)
        or prepared_marker != payload
        or not isinstance(completion_report, dict)
        or completion_report.get("status") != "ROLLBACK_COMPLETE"
        or completion_report.get("phase") != "COMPLETE"
    ):
        _abort("completed rollback transaction is not durably finalized")
    try:
        marker_path.unlink()
    except Exception as error:
        _abort("completed marker retirement failed: " + type(error).__name__)


def _activate():
    marker_raw = os.environ.get("JARVIS_ROLLBACK_RUNTIME_MARKER", "").strip()
    if not marker_raw:
        return
    marker_path = Path(marker_raw).expanduser().resolve()
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as error:
        _abort("unreadable marker: " + type(error).__name__)
    if marker.get("contract") != "jarvis.cross-schema-rollback-runtime.v1":
        _abort("unexpected marker contract")
    if marker.get("status") not in {"ACTIVE", "COMPLETE_FROZEN_RUNTIME"}:
        _abort("rollback runtime marker is not active")
    root = Path(str(marker.get("install_root") or "")).expanduser().resolve()
    home = Path(str(marker.get("foundry_home") or "")).expanduser().resolve()
    if not _same_path(root, os.environ.get("JARVIS_INSTALL_ROOT", "")):
        _abort("install root mismatch")
    if not _same_path(home, os.environ.get("JARVIS_HOME", "")):
        _abort("Foundry home mismatch")
    current_path = root / "CURRENT"
    try:
        current = current_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except Exception as error:
        _abort("CURRENT is unreadable: " + type(error).__name__)
    if current != marker.get("source_sha"):
        _abort("CURRENT does not match rollback predecessor")
    supplied = os.environ.get("JARVIS_RELEASE_SHA", "").strip()
    if supplied and supplied != current:
        _abort("launcher release identity mismatch")
    source_path = Path(__file__).resolve()
    try:
        observed_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    except Exception as error:
        _abort("shim hash unavailable: " + type(error).__name__)
    if observed_hash != marker.get("sitecustomize_sha256"):
        _abort("shim hash mismatch")
    stop_path = home / "EMERGENCY_STOP"
    if not stop_path.is_file():
        if marker.get("status") == "COMPLETE_FROZEN_RUNTIME":
            _retire_completed_marker(
                marker_path,
                root=root,
                home=home,
                source_sha=current,
                shim_hash=observed_hash,
            )
            return
        _abort("durable STOP is not active")

    try:
        from jarvis_foundry.supervisor import run as runtime
    except Exception as error:
        _abort("predecessor Supervisor import failed: " + type(error).__name__)

    def fixed_frozen_supervisor(
        paths,
        *,
        spawn_worker_loop,
        worker_loop_config,
        once,
        interval=5.0,
        command_center_port=None,
        owner_secret_path=None,
    ):
        database = runtime.FoundryDatabase(paths.database)
        supervisor = runtime.Supervisor(
            database,
            paths.emergency_stop,
            spawn_worker_loop=spawn_worker_loop,
            worker_loop_config=worker_loop_config,
            command_center_port=command_center_port,
            owner_secret_path=owner_secret_path,
        )
        console_handler = runtime.make_console_handler(supervisor)
        console_handler.install()
        exit_code = 0
        try:
            state = supervisor.start()
            print(json.dumps({"supervisor": state, "home": str(paths.home)}), flush=True)
            if state != "FROZEN":
                try:
                    supervisor.stop()
                finally:
                    _abort("predecessor did not enter FROZEN state")
            if once:
                print(json.dumps({"heartbeat": supervisor.heartbeat()}), flush=True)
                return 0
            configured = os.environ.get("JARVIS_ROLLBACK_HEARTBEAT_SECONDS")
            heartbeat_seconds = float(configured) if configured else float(interval)
            original_heartbeat = supervisor.heartbeat

            def guarded_frozen_heartbeat(*args, **kwargs):
                if not stop_path.is_file():
                    _retire_completed_marker(
                        marker_path,
                        root=root,
                        home=home,
                        source_sha=current,
                        shim_hash=observed_hash,
                    )
                    try:
                        supervisor.stop()
                    finally:
                        os._exit(0)
                state = original_heartbeat(*args, **kwargs)
                if state != "FROZEN":
                    try:
                        supervisor.stop()
                    finally:
                        _abort("predecessor left FROZEN state")
                return state

            supervisor.heartbeat = guarded_frozen_heartbeat
            exit_code = runtime._heartbeat_loop(supervisor, heartbeat_seconds)
        except runtime.LeaseUnavailable as error:
            print(json.dumps({
                "supervisor": "LEASE_UNAVAILABLE",
                "error": str(error),
            }), flush=True)
            return 2
        except KeyboardInterrupt:
            pass
        finally:
            supervisor.stop()
            console_handler.uninstall()
        return exit_code

    runtime._run_frozen_supervisor = fixed_frozen_supervisor


_activate()
'''


def _run_schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        env=sanitized_env(),
        **headless_run_kwargs(),
    )


def _split_windows_arguments(arguments: str) -> list[str]:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            count = ctypes.c_int()
            command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
            command_line_to_argv.argtypes = [
                wintypes.LPCWSTR,
                ctypes.POINTER(ctypes.c_int),
            ]
            command_line_to_argv.restype = ctypes.POINTER(wintypes.LPWSTR)
            pointer = command_line_to_argv(arguments, ctypes.byref(count))
            if not pointer:
                raise OSError("CommandLineToArgvW failed")
            try:
                return [pointer[index] for index in range(count.value)]
            finally:
                ctypes.windll.kernel32.LocalFree(pointer)
        except (AttributeError, OSError, ValueError):
            pass
    return [
        token[1:-1] if len(token) >= 2 and token[0] == token[-1] == '"' else token
        for token in shlex.split(arguments, posix=False)
    ]


def _option_value(
    arguments: Sequence[str],
    name: str,
    *,
    required: bool = False,
) -> str | None:
    indexes = [index for index, value in enumerate(arguments) if value == name]
    if len(indexes) > 1:
        raise RollbackRefusal(f"scheduled task repeats {name}")
    if not indexes:
        if required:
            raise RollbackRefusal(f"scheduled task is missing {name}")
        return None
    index = indexes[0]
    if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
        raise RollbackRefusal(f"scheduled task has no value for {name}")
    return arguments[index + 1]


def _validated_launcher_arguments(
    arguments: Sequence[str],
) -> dict[str, Any]:
    value_options = {
        "--command-center-port",
        "--home",
        "--interval",
        "--owner-secret-path",
        "--worker-loop-config",
    }
    flag_options = {"--spawn-worker-loop"}
    parsed: dict[str, Any] = {}
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in flag_options:
            if token in parsed:
                raise RollbackRefusal(f"scheduled task repeats {token}")
            parsed[token] = True
            index += 1
            continue
        if token not in value_options:
            raise RollbackRefusal(
                f"scheduled task has unsupported launcher argument: {token}")
        if token in parsed or index + 1 >= len(arguments):
            raise RollbackRefusal(
                f"scheduled task has invalid launcher argument: {token}")
        parsed[token] = arguments[index + 1]
        index += 2
    return parsed


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(str(Path(left).expanduser().resolve())) == os.path.normcase(
        str(Path(right).expanduser().resolve())
    )


def _creation_identity_matches(
    expected: Mapping[str, Any] | None,
    observed: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(expected, Mapping) or not isinstance(observed, Mapping):
        return False
    if expected.get("create_time") is None or observed.get("create_time") is None:
        return False
    if int(expected.get("pid", -1)) != int(observed.get("pid", -2)):
        return False
    if expected.get("create_time") != observed.get("create_time"):
        return False
    expected_exe = str(expected.get("exe") or "")
    observed_exe = str(observed.get("exe") or "")
    return (
        not expected_exe
        or not observed_exe
        or os.path.normcase(expected_exe) == os.path.normcase(observed_exe)
        or {
            Path(expected_exe).name.casefold(),
            Path(observed_exe).name.casefold(),
        } <= {"python.exe", "pythonw.exe"}
    )


def _exact_creation_identity_matches(
    expected: Mapping[str, Any] | None,
    observed: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(expected, Mapping) or not isinstance(observed, Mapping):
        return False
    try:
        expected_pid = int(expected["pid"])
        observed_pid = int(observed["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if (
        expected_pid != observed_pid
        or expected.get("create_time") is None
        or observed.get("create_time") is None
        or expected.get("create_time") != observed.get("create_time")
    ):
        return False
    expected_exe = str(expected.get("exe") or "")
    observed_exe = str(observed.get("exe") or "")
    if expected_exe and (
        not observed_exe
        or os.path.normcase(str(Path(expected_exe).resolve()))
        != os.path.normcase(str(Path(observed_exe).resolve()))
    ):
        return False
    return True


def _process_parent_map(
    raw_processes: Sequence[Mapping[str, Any]],
) -> dict[int, int]:
    parents: dict[int, int] = {}
    for raw in raw_processes:
        try:
            pid = int(raw.get("ProcessId", raw.get("pid")))
            parent = int(raw.get("ParentProcessId", raw.get("parent_pid", 0)))
        except (TypeError, ValueError):
            continue
        if pid > 0 and parent >= 0:
            parents[pid] = parent
    return parents


def _descendant_pids(root_pid: int, parents: Mapping[int, int]) -> set[int]:
    descendants: set[int] = set()
    frontier = {int(root_pid)}
    while frontier:
        next_frontier = {
            pid
            for pid, parent in parents.items()
            if parent in frontier and pid not in descendants
        }
        descendants.update(next_frontier)
        frontier = next_frontier
    descendants.discard(int(root_pid))
    return descendants


class WindowsRollbackHost:
    """Concrete Windows adapter for the one owned production task and process tree."""

    TASK_NAMESPACE = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"

    def scheduler_contract(self, request: RollbackRequest) -> dict[str, Any]:
        if os.name != "nt":
            raise RollbackRefusal("Windows Task Scheduler is unavailable")
        result = _run_schtasks("/query", "/tn", request.task_name, "/xml")
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RollbackRefusal(
                f"owned Scheduler task is unavailable: {detail}")
        try:
            task = ElementTree.fromstring(result.stdout.lstrip("\ufeff"))
        except ElementTree.ParseError as error:
            raise RollbackRefusal("owned Scheduler task XML is invalid") from error
        namespace = self.TASK_NAMESPACE
        description = task.findtext(f"{namespace}RegistrationInfo/{namespace}Description")
        policy = task.findtext(f"{namespace}Settings/{namespace}MultipleInstancesPolicy")
        enabled_text = task.findtext(f"{namespace}Settings/{namespace}Enabled")
        actions = task.find(f"{namespace}Actions")
        action_nodes = list(actions) if actions is not None else []
        if (
            len(action_nodes) != 1
            or action_nodes[0].tag != f"{namespace}Exec"
        ):
            raise RollbackRefusal(
                "owned Scheduler task must contain exactly one Exec action")
        action = action_nodes[0]
        command = str(action.findtext(f"{namespace}Command") or "").strip()
        raw_arguments = str(
            action.findtext(f"{namespace}Arguments") or "").strip()
        working_directory = str(
            action.findtext(f"{namespace}WorkingDirectory") or "").strip()
        if "JARVIS Foundry" not in str(description or ""):
            raise RollbackRefusal("Scheduler task ownership description is missing")
        if str(policy or "").strip() != "IgnoreNew":
            raise RollbackRefusal(
                "Scheduler task MultipleInstancesPolicy is not IgnoreNew")
        if Path(command).name.casefold() not in {"wscript.exe", "cscript.exe"}:
            raise RollbackRefusal(
                "Scheduler task does not use the stable VBS launcher")
        tokens = _split_windows_arguments(raw_arguments)
        expected_vbs = headless_launcher_cmd(request.install_root)
        vbs_indexes = [
            index for index, token in enumerate(tokens)
            if Path(token).name.casefold()
            == expected_vbs.name.casefold()
        ]
        if len(vbs_indexes) != 1:
            raise RollbackRefusal(
                "Scheduler task does not identify exactly one stable launcher")
        vbs_index = vbs_indexes[0]
        if not _same_path(tokens[vbs_index], expected_vbs):
            raise RollbackRefusal(
                "Scheduler task stable launcher is outside the install root")
        launcher_arguments = tokens[vbs_index + 1:]
        parsed = _validated_launcher_arguments(launcher_arguments)
        task_home = _option_value(
            launcher_arguments, "--home", required=True)
        if task_home is None or not _same_path(task_home, request.foundry_home):
            raise RollbackRefusal(
                "Scheduler task --home does not match the rollback home")
        if not _same_path(working_directory, request.foundry_home):
            raise RollbackRefusal(
                "Scheduler task working directory does not match the rollback home")
        port_raw = parsed.get("--command-center-port")
        if port_raw is None:
            raise RollbackRefusal(
                "Scheduler task has no Command Center port")
        try:
            port = int(port_raw)
        except (TypeError, ValueError) as error:
            raise RollbackRefusal(
                "Scheduler task Command Center port is invalid") from error
        if not 1 <= port <= 65535:
            raise RollbackRefusal(
                "Scheduler task Command Center port is out of range")
        interval_raw = parsed.get("--interval")
        try:
            interval = float(interval_raw) if interval_raw is not None else 5.0
        except (TypeError, ValueError) as error:
            raise RollbackRefusal(
                "Scheduler task heartbeat interval is invalid") from error
        if not math.isfinite(interval) or interval <= 0:
            raise RollbackRefusal(
                "Scheduler task heartbeat interval is invalid")
        return {
            "task_name": request.task_name,
            "installed": True,
            "owned": True,
            "enabled": (
                True if enabled_text is None
                else str(enabled_text).strip().lower() != "false"
            ),
            "description": description,
            "multiple_instances_policy": policy,
            "command": command,
            "arguments": raw_arguments,
            "launcher_arguments": list(launcher_arguments),
            "working_directory": str(Path(working_directory).resolve()),
            "command_center_port": port,
            "heartbeat_interval_seconds": interval,
            "spawn_worker_loop": bool(parsed.get("--spawn-worker-loop")),
            "worker_loop_config": parsed.get("--worker-loop-config"),
            "owner_secret_path": parsed.get("--owner-secret-path"),
        }

    def set_scheduler_enabled(
        self, request: RollbackRequest, enabled: bool,
    ) -> dict[str, Any]:
        self.scheduler_contract(request)
        flag = "/enable" if enabled else "/disable"
        result = _run_schtasks(
            "/change", "/tn", request.task_name, flag)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RollbackControllerError(
                f"Scheduler {flag[1:]} failed: {detail}")
        contract = self.scheduler_contract(request)
        if bool(contract["enabled"]) is not bool(enabled):
            raise RollbackControllerError(
                "Scheduler did not reach the requested enabled state")
        return contract

    def owned_processes(self, request: RollbackRequest) -> list[dict[str, Any]]:
        try:
            raw_processes = query_processes_strict()
        except ProcessQueryError as error:
            raise RollbackRefusal(
                f"authoritative owned-process census failed: {error}") from error
        process_parents = _process_parent_map(raw_processes)

        supervisor_pattern = re.compile(
            r"(?:^|\s)-m\s+[\"']?jarvis_foundry\."
            r"(?:supervisor\.run|deployment\.(?:launcher|headless_launcher))"
            r"(?:[\"']?)(?=\s|$)",
            re.IGNORECASE,
        )
        target_supervisor_rows = [
            raw for raw in raw_processes
            if supervisor_pattern.search(str(
                raw.get("CommandLine") or raw.get("command_line") or ""
            ))
            and process_matches_home(raw, request.foundry_home)
        ]
        observed: list[dict[str, Any]] = []
        try:
            supervisors = supervisor_processes(
                query=lambda: target_supervisor_rows)
        except Exception as error:
            raise RollbackRefusal(
                "Supervisor creation identity could not be established"
            ) from error
        covered_supervisor_pids: set[int] = set()
        for process in supervisors:
            if not process_matches_home(process, request.foundry_home):
                continue
            try:
                pid = int(process["pid"])
            except (KeyError, TypeError, ValueError) as error:
                raise RollbackRefusal(
                    "Supervisor census returned an invalid PID") from error
            covered_supervisor_pids.add(pid)
            wrapper_pid = process.get("interpreter_wrapper_pid")
            if wrapper_pid is not None:
                covered_supervisor_pids.add(int(wrapper_pid))
            identity_status = process.get("identity_status")
            if identity_status == "UNAVAILABLE":
                if pid_alive(pid):
                    raise RollbackRefusal(
                        "live Supervisor creation identity is unavailable")
                continue
            if identity_status == "PID_REUSED" or not process.get("live"):
                continue
            observed.append({"kind": "supervisor", **process})

        for raw in target_supervisor_rows:
            command_line = str(
                raw.get("CommandLine") or raw.get("command_line") or "")
            try:
                pid = int(raw.get("ProcessId", raw.get("pid")))
            except (TypeError, ValueError) as error:
                raise RollbackRefusal(
                    "matching Supervisor census row has no valid PID") from error
            if pid > 0 and pid_alive(pid) and pid not in covered_supervisor_pids:
                raise RollbackRefusal(
                    "matching live Supervisor could not be normalized "
                    "into an exact creation identity")

        worker_pattern = re.compile(
            r"(?:^|\s)-m\s+[\"']?jarvis_foundry\.runtime\.worker_loop"
            r"(?:[\"']?)(?=\s|$)",
            re.IGNORECASE,
        )
        expected_vbs = headless_launcher_cmd(request.install_root)
        for raw in raw_processes:
            command_line = str(
                raw.get("CommandLine") or raw.get("command_line") or "")
            executable = str(
                raw.get("ExecutablePath")
                or raw.get("executable_path")
                or ""
            )
            is_worker = bool(
                worker_pattern.search(command_line)
                and process_matches_home(raw, request.foundry_home)
            )
            is_scheduler_wrapper = False
            command_interpreter: str | None = None
            if (
                expected_vbs.name.casefold() in command_line.casefold()
                and process_matches_home(raw, request.foundry_home)
            ):
                try:
                    tokens = _split_windows_arguments(command_line)
                except (OSError, ValueError) as error:
                    raise RollbackRefusal(
                        "matching Scheduler wrapper command line is invalid"
                    ) from error
                wrapper_tokens = [
                    token for token in tokens
                    if Path(token).name.casefold()
                    == expected_vbs.name.casefold()
                ]
                executable_names = {
                    Path(value).name.casefold()
                    for value in (executable, tokens[0] if tokens else "")
                    if value
                }
                command_executable = (
                    Path(tokens[0]).name.casefold() if tokens else "")
                command_interpreter = tokens[0] if tokens else None
                census_executable = (
                    Path(executable).name.casefold() if executable else "")
                if (
                    len(wrapper_tokens) != 1
                    or not _same_path(wrapper_tokens[0], expected_vbs)
                    or not executable_names
                    or not executable_names <= {"wscript.exe", "cscript.exe"}
                    or (
                        command_executable
                        and census_executable
                        and command_executable != census_executable
                    )
                ):
                    raise RollbackRefusal(
                        "matching Scheduler wrapper identity is uncertain")
                is_scheduler_wrapper = any(
                        _same_path(token, expected_vbs)
                        for token in wrapper_tokens
                    )
            if not is_worker and not is_scheduler_wrapper:
                continue
            try:
                pid = int(raw.get("ProcessId", raw.get("pid")))
            except (TypeError, ValueError) as error:
                raise RollbackRefusal(
                    "matching owned process census row has no valid PID"
                ) from error
            if pid <= 0:
                raise RollbackRefusal(
                    "matching owned process census row has no valid PID")
            if not pid_alive(pid):
                continue
            try:
                creation_identity = process_creation_identity(pid)
            except (OSError, TypeError, ValueError) as error:
                raise RollbackRefusal(
                    "matching owned process creation identity is unavailable"
                ) from error
            if creation_identity.get("create_time") is None:
                raise RollbackRefusal(
                    "matching owned process creation identity is unavailable")
            if is_scheduler_wrapper:
                observed_executable = str(creation_identity.get("exe") or "")
                if (
                    not command_interpreter
                    or not observed_executable
                    or not _same_path(command_interpreter, observed_executable)
                ):
                    raise RollbackRefusal(
                        "matching Scheduler wrapper executable identity "
                        "is uncertain")
            if executable and not _creation_identity_matches(
                {
                    "pid": pid,
                    "create_time": creation_identity.get("create_time"),
                    "exe": executable,
                },
                    creation_identity,
            ):
                raise RollbackRefusal(
                    "matching owned process executable identity changed "
                    "during census")
            observed.append({
                "kind": (
                    "scheduler_wrapper"
                    if is_scheduler_wrapper else "worker_loop"
                ),
                "pid": pid,
                "executable": executable or None,
                "command_line": command_line,
                "creation_identity": creation_identity,
                "live": True,
            })

        adapter_table = request.foundry_home / "adapter_worker_pids.json"
        for run_id, record in _load_adapter_pids_strict(adapter_table).items():
            pid = int(record["pid"])
            if not pid_alive(pid):
                raw_descendants = _descendant_pids(pid, process_parents)
                recorded_raw = record.get("owned_descendants", ())
                if not isinstance(recorded_raw, (list, tuple)):
                    raise RollbackRefusal(
                        "adapter PID registry has invalid descendant evidence")
                try:
                    recorded_descendants = {
                        int(value) for value in recorded_raw
                        if int(value) > 0 and int(value) != pid
                    }
                except (TypeError, ValueError) as error:
                    raise RollbackRefusal(
                        "adapter PID registry has invalid descendant evidence"
                    ) from error
                live_descendants = sorted({
                    descendant
                    for descendant in (
                        raw_descendants | recorded_descendants
                    )
                    if pid_alive(descendant)
                })
                if live_descendants:
                    raise RollbackRefusal(
                        "dead adapter root retains live or uncertain "
                        f"descendants: run_id={run_id!r}, "
                        f"pids={live_descendants}"
                    )
                continue
            recorded_identity = record.get("creation_identity")
            if (
                not isinstance(recorded_identity, Mapping)
                or recorded_identity.get("create_time") is None
                or not str(recorded_identity.get("exe") or "")
            ):
                raise RollbackRefusal(
                    "live adapter stored creation identity is unavailable")
            try:
                recorded_pid = int(recorded_identity.get("pid"))
            except (TypeError, ValueError) as error:
                raise RollbackRefusal(
                    "live adapter stored creation identity is unavailable"
                ) from error
            if recorded_pid != pid:
                raise RollbackRefusal(
                    "live adapter stored creation identity does not match PID")
            try:
                observed_identity = process_creation_identity(pid)
            except (OSError, TypeError, ValueError) as error:
                raise RollbackRefusal(
                    "live adapter creation identity is unavailable") from error
            if observed_identity.get("create_time") is None:
                raise RollbackRefusal(
                    "live adapter creation identity is unavailable")
            if not _exact_creation_identity_matches(
                recorded_identity, observed_identity
            ):
                raise RollbackRefusal(
                    "live adapter creation identity changed during census")
            observed.append({
                "kind": "adapter",
                "pid": pid,
                "run_id": run_id,
                "creation_identity": observed_identity,
                "live": True,
            })
        return sorted(
            {
                (str(item.get("kind")), int(item.get("pid") or 0)): item
                for item in observed
            }.values(),
            key=lambda item: (
                str(item.get("kind")),
                int(item.get("pid") or 0),
            ),
        )

    def validate_release_runtime(
        self,
        request: RollbackRequest,
        source_sha: str,
        *,
        foundry_home: Path | None,
    ) -> dict[str, Any]:
        python_exe = release_dir(
            source_sha, request.install_root,
        ) / ".venv" / "Scripts" / "python.exe"
        code = (
            "import json,sys;"
            "from pathlib import Path;"
            "from jarvis_foundry.deployment.release import validate_release;"
            "home=Path(sys.argv[3]) if sys.argv[3] else None;"
            "result=validate_release(sys.argv[2],root=Path(sys.argv[1]),"
            "foundry_home=home,require_schema_match=home is not None);"
            "print(json.dumps(result,sort_keys=True))"
        )
        command = [
            str(python_exe),
            "-I",
            "-B",
            "-c",
            code,
            str(request.install_root),
            source_sha,
            str(foundry_home) if foundry_home is not None else "",
        ]
        try:
            environment = sanitized_env()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                cwd=request.foundry_home,
                env=environment,
                **headless_run_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RollbackRefusal(
                f"release {source_sha} self-validation could not run") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RollbackRefusal(
                f"release {source_sha} self-validation failed: {detail[:500]}")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise RollbackRefusal(
                f"release {source_sha} returned invalid validation JSON") from error
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise RollbackRefusal(
                f"release {source_sha} did not validate itself")
        return dict(payload)

    def build_continuity_baseline(
        self,
        request: RollbackRequest,
        *,
        source_sha: str,
        snapshot_path: Path,
        destination_path: Path,
        expected_schema: int,
    ) -> dict[str, Any]:
        python_exe = release_dir(
            source_sha, request.install_root,
        ) / ".venv" / "Scripts" / "python.exe"
        destination = Path(destination_path).resolve()
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp")
        code = """
import json
import sqlite3
import sys

from jarvis_foundry.migrations import SCHEMA_VERSION, migrate

path = sys.argv[1]
expected = int(sys.argv[2])
if SCHEMA_VERSION != expected:
    raise SystemExit(
        f"release schema {SCHEMA_VERSION} does not match expected {expected}"
    )
connection = sqlite3.connect(path)
try:
    connection.execute("BEGIN IMMEDIATE")
    observed = migrate(
        connection,
        "rollback-continuity-baseline",
        target=expected,
    )
    quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    integrity_check = str(
        connection.execute("PRAGMA integrity_check").fetchone()[0]
    )
    if observed != expected or quick_check != "ok" or integrity_check != "ok":
        raise RuntimeError("migrated continuity baseline failed validation")
    connection.commit()
finally:
    connection.close()
print(json.dumps({
    "schema": observed,
    "quick_check": quick_check,
    "integrity_check": integrity_check,
}, sort_keys=True))
"""
        try:
            _atomic_copy_file(Path(snapshot_path).resolve(), temporary)
            environment = sanitized_env()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [
                    str(python_exe),
                    "-I",
                    "-B",
                    "-c",
                    code,
                    str(temporary),
                    str(expected_schema),
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                cwd=request.foundry_home,
                env=environment,
                **headless_run_kwargs(),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise RollbackRefusal(
                    "failed release could not migrate the rollback continuity "
                    f"baseline: {detail[:500]}")
            try:
                payload = json.loads(result.stdout)
            except (TypeError, ValueError) as error:
                raise RollbackRefusal(
                    "failed release returned invalid continuity baseline JSON"
                ) from error
            if (
                not isinstance(payload, Mapping)
                or payload.get("schema") != expected_schema
                or payload.get("quick_check") != "ok"
                or payload.get("integrity_check") != "ok"
            ):
                raise RollbackRefusal(
                    "failed release did not validate the continuity baseline")
            with temporary.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, destination)
            return {
                **dict(payload),
                "path": str(destination),
                "file_sha256": _sha256_file(destination),
                "source_sha": source_sha,
            }
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RollbackRefusal(
                "failed release continuity baseline migration could not run"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

    def validate_release_launcher(
        self, request: RollbackRequest, source_sha: str,
    ) -> dict[str, Any]:
        python_exe = release_dir(
            source_sha, request.install_root,
        ) / ".venv" / "Scripts" / "python.exe"
        environment = sanitized_env()
        environment.update({
            "JARVIS_INSTALL_ROOT": str(request.install_root),
            "JARVIS_HOME": str(request.foundry_home),
            "JARVIS_RELEASE_ID": source_sha,
            "JARVIS_RELEASE_SHA": source_sha,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        command = [
            str(python_exe),
            "-I",
            "-B",
            "-m",
            "jarvis_foundry.deployment.launcher",
            "--home",
            str(request.foundry_home),
            "--validate-only",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                cwd=request.foundry_home,
                env=environment,
                **headless_run_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RollbackRefusal(
                "predecessor launcher validation could not run") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RollbackRefusal(
                f"predecessor launcher validation failed: {detail[:500]}")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise RollbackRefusal(
                "predecessor launcher returned invalid JSON") from error
        identity = payload.get("identity") if isinstance(payload, Mapping) else None
        if (
            payload.get("launcher") != "OK"
            or not isinstance(identity, Mapping)
            or identity.get("source_sha") != source_sha
        ):
            raise RollbackRefusal(
                "predecessor launcher identity did not match the target")
        return dict(payload)

    def start_predecessor(
        self,
        request: RollbackRequest,
        *,
        runtime_identity: Mapping[str, Any],
        scheduler: Mapping[str, Any],
        shim_dir: Path,
        marker_path: Path,
        log_path: Path,
    ) -> dict[str, Any]:
        python_exe = Path(str(runtime_identity["python_exe"])).resolve()
        launcher_arguments = [
            str(value) for value in scheduler["launcher_arguments"]
        ]
        environment = sanitized_env()
        environment.update({
            "JARVIS_HOME": str(request.foundry_home),
            "JARVIS_INSTALL_ROOT": str(request.install_root),
            "JARVIS_RELEASE_ID": request.predecessor_source_sha,
            "JARVIS_RELEASE_SHA": request.predecessor_source_sha,
            "JARVIS_ROLLBACK_HEARTBEAT_SECONDS": str(
                scheduler["heartbeat_interval_seconds"]),
            "JARVIS_ROLLBACK_RUNTIME_MARKER": str(marker_path),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHOME": "",
            "PYTHONPATH": str(shim_dir),
        })
        command = [
            str(python_exe),
            "-m",
            "jarvis_foundry.deployment.launcher",
            *launcher_arguments,
        ]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", buffering=1) as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=request.foundry_home,
                env=environment,
                **headless_popen_kwargs(),
            )
        root_identity = process_creation_identity(process.pid)
        deadline = time.monotonic() + 15.0
        exact: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            if process.poll() is not None:
                tail = ""
                try:
                    tail = log_path.read_text(
                        encoding="utf-8", errors="replace")[-2000:]
                except OSError:
                    pass
                raise RollbackControllerError(
                    "predecessor Supervisor exited during startup: "
                    + tail.strip())
            exact = [
                item for item in self.owned_processes(request)
                if item.get("kind") == "supervisor"
                and item.get("release_sha")
                == request.predecessor_source_sha
            ]
            if len(exact) == 1:
                break
            if len(exact) > 1:
                terminate_owned_tree(process.pid, process=process)
                raise RollbackControllerError(
                    "predecessor startup produced duplicate Supervisors")
            time.sleep(0.25)
        if len(exact) != 1:
            terminate_owned_tree(process.pid, process=process)
            raise RollbackControllerError(
                "predecessor Supervisor identity was not observed")
        return {
            "root_pid": process.pid,
            "root_creation_identity": root_identity,
            "supervisor_processes": exact,
            "command_sha256": hashlib.sha256(
                "\0".join(command).encode("utf-8")).hexdigest(),
            "log_path": str(log_path.resolve()),
            "started_at": isoformat(),
        }

    def terminate_started_process(
        self, request: RollbackRequest, process_record: Mapping[str, Any],
    ) -> dict[str, Any]:
        targeted: list[int] = []
        root_pid = process_record.get("root_pid")
        root_identity = process_record.get("root_creation_identity")
        try:
            root_pid_value = int(root_pid)
        except (TypeError, ValueError):
            root_pid_value = 0
        if root_pid_value > 0 and _creation_identity_matches(
            root_identity, process_creation_identity(root_pid_value),
        ):
            targeted.extend(terminate_owned_tree(root_pid_value))
        stored = {
            int(item["pid"]): item.get("creation_identity")
            for item in process_record.get("supervisor_processes", ())
            if isinstance(item, Mapping) and item.get("pid") is not None
        }
        for process in self.owned_processes(request):
            if process.get("kind") != "supervisor":
                continue
            try:
                pid = int(process["pid"])
            except (KeyError, TypeError, ValueError):
                continue
            if pid in targeted:
                continue
            if pid in stored and _creation_identity_matches(
                stored[pid], process_creation_identity(pid),
            ):
                targeted.extend(terminate_owned_tree(pid))
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            remaining = [
                item for item in self.owned_processes(request)
                if item.get("kind") == "supervisor"
                and item.get("release_sha")
                == request.predecessor_source_sha
            ]
            if not remaining:
                return {
                    "targeted_pids": sorted(set(targeted)),
                    "remaining": [],
                }
            time.sleep(0.1)
        return {
            "targeted_pids": sorted(set(targeted)),
            "remaining": [
                item for item in self.owned_processes(request)
                if item.get("kind") == "supervisor"
                and item.get("release_sha")
                == request.predecessor_source_sha
            ],
        }

    def terminate_owned_processes(
        self,
        request: RollbackRequest,
        processes: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Terminate only exact creation identities from a strict owned census."""
        requested = {
            (str(item.get("kind")), int(item.get("pid") or 0)): item
            for item in processes
            if str(item.get("kind")) in {
                "adapter",
                "scheduler_wrapper",
                "supervisor",
                "worker_loop",
            }
            and int(item.get("pid") or 0) > 0
        }
        current = {
            (str(item.get("kind")), int(item.get("pid") or 0)): item
            for item in self.owned_processes(request)
        }
        targeted: list[int] = []
        for key, expected in requested.items():
            observed_record = current.get(key)
            if observed_record is None:
                continue
            pid = key[1]
            try:
                observed_identity = process_creation_identity(pid)
            except (OSError, TypeError, ValueError):
                continue
            if not (
                _creation_identity_matches(
                    expected.get("creation_identity"), observed_identity)
                and _creation_identity_matches(
                    observed_record.get("creation_identity"),
                    observed_identity,
                )
            ):
                continue
            targeted.extend(terminate_owned_tree(pid))
        deadline = time.monotonic() + 12.0
        remaining = self.owned_processes(request)
        while remaining and time.monotonic() < deadline:
            time.sleep(0.1)
            remaining = self.owned_processes(request)
        return {
            "targeted_pids": sorted(set(targeted)),
            "remaining": remaining,
        }

    @staticmethod
    def _http_json(
        port: int,
        path: str,
        *,
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        with urlopen(
            f"http://127.0.0.1:{port}{path}",
            timeout=timeout_seconds,
        ) as response:
            payload = json.loads(response.read())
        if not isinstance(payload, dict):
            raise ValueError(f"{path} did not return an object")
        return payload

    def probe_predecessor(
        self,
        request: RollbackRequest,
        *,
        scheduler: Mapping[str, Any],
        expected_identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        port = int(scheduler["command_center_port"])
        deadline = time.monotonic() + request.health_timeout_seconds
        last_error = "Command Center did not answer"
        while time.monotonic() < deadline:
            try:
                request_timeout = max(
                    1.0,
                    min(20.0, request.health_timeout_seconds),
                )
                health = self._http_json(
                    port,
                    "/api/health",
                    timeout_seconds=request_timeout,
                )
                status = self._http_json(
                    port,
                    "/api/status",
                    timeout_seconds=request_timeout,
                )
                command_center = self._http_json(
                    port,
                    "/api/command-center",
                    timeout_seconds=request_timeout,
                )
                processes = [
                    item for item in self.owned_processes(request)
                    if item.get("kind") == "supervisor"
                ]
                authority = health.get("release_authority") or {}
                release = health.get("release") or {}
                schema = health.get("schema") or {}
                snapshot_health = command_center.get("health") or {}
                snapshot_schema = snapshot_health.get("schema") or {}
                release_identity = command_center.get("release_identity") or {}
                accepted = (
                    health.get("stop_active") is True
                    and health.get("status") == "BLOCKED"
                    and health.get("trust_state") == "SUSPENDED"
                    and health.get("installed_release_sha")
                    == request.predecessor_source_sha
                    and health.get("runtime_release_sha")
                    == request.predecessor_source_sha
                    and health.get("parity_state") == "MATCH"
                    and schema.get("live") == expected_identity["schema"]
                    and schema.get("package") == expected_identity["schema"]
                    and authority.get("status") == "CONSISTENT"
                    and authority.get("database_authority_mode")
                    == "DATABASE_LIVE_SLOT"
                    and release.get("supervisor_count") == 1
                    and len(processes) == 1
                    and status.get("status") in {"BLOCKED", "DEGRADED", "OK"}
                    and command_center.get("stop", {}).get("active") is True
                    and snapshot_schema.get("live") == expected_identity["schema"]
                    and snapshot_schema.get("package") == expected_identity["schema"]
                    and release_identity.get("current")
                    == request.predecessor_source_sha
                )
                if accepted:
                    return {
                        "accepted": True,
                        "observed_at": isoformat(),
                        "health": {
                            "status": health.get("status"),
                            "reason": health.get("reason"),
                            "stop_active": health.get("stop_active"),
                            "trust_state": health.get("trust_state"),
                            "installed_release_sha": health.get(
                                "installed_release_sha"),
                            "runtime_release_sha": health.get(
                                "runtime_release_sha"),
                            "parity_state": health.get("parity_state"),
                            "schema": schema,
                            "release_authority": authority,
                            "supervisor_count": release.get(
                                "supervisor_count"),
                        },
                        "status": {
                            "status": status.get("status"),
                            "supervisor_status": status.get(
                                "supervisor_status"),
                            "trust_state": status.get("trust_state"),
                        },
                        "command_center": {
                            "stop_active": command_center.get(
                                "stop", {}).get("active"),
                            "schema": snapshot_schema,
                            "release_identity": release_identity,
                        },
                    }
                last_error = "predecessor health evidence is not yet acceptable"
            except (
                HTTPError,
                OSError,
                TimeoutError,
                URLError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                last_error = f"{type(error).__name__}: {error}"
            time.sleep(0.5)
        raise RollbackControllerError(
            f"predecessor health acceptance timed out: {last_error}")


class CrossSchemaRollbackController:
    """One durable controller transaction for a failed cross-schema activation."""

    def __init__(
        self,
        request: RollbackRequest,
        *,
        host: RollbackHost | None = None,
        fault_injector: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.request = request
        self.host = host or WindowsRollbackHost()
        self.fault_injector = fault_injector
        self.transactions_root = (
            request.install_root / STATE_DIRECTORY_NAME
        )
        self.transaction_dir = (
            self.transactions_root / request.transaction_id
        )
        self.state_path = self.transaction_dir / STATE_FILE_NAME
        self.active_path = self.transactions_root / ACTIVE_TRANSACTION_NAME
        self.lock_path = (
            request.install_root / f".{STATE_DIRECTORY_NAME}.{LOCK_FILE_NAME}"
        )
        self.evidence_dir = self.transaction_dir / "evidence"
        self.owned_snapshot_path = self.evidence_dir / (
            f"snapshot-{request.snapshot_sha256[:16]}.db"
        )
        self.continuity_baseline_path = self.evidence_dir / (
            f"snapshot-migrated-{request.failed_source_sha[:12]}.db"
        )
        self.log_path = self.transaction_dir / "predecessor-supervisor.log"
        self.state: dict[str, Any] = {}

    def run(self) -> dict[str, Any]:
        with _RollbackFileLock(self.lock_path):
            initial_evidence = None
            if (
                not self.state_path.is_file()
                and not self.active_path.is_file()
            ):
                initial_evidence = self._initial_preconditions()
            self.state = self._load_or_create_state(
                initial_evidence=initial_evidence)
            try:
                return self._execute()
            except RollbackInterruption:
                raise
            except Exception as error:
                containment: dict[str, Any] | None = None
                if self.state.get("phase") != PREPARED:
                    try:
                        containment = self._contain(
                            reason=f"{type(error).__name__}: {error}")
                    except Exception as containment_error:
                        containment = {
                            "status": "INCOMPLETE",
                            "error": (
                                f"{type(containment_error).__name__}: "
                                f"{containment_error}"
                            ),
                        }
                self.state.setdefault("errors", []).append({
                    "recorded_at": isoformat(),
                    "phase": self.state.get("phase"),
                    "error_class": type(error).__name__,
                    "error": str(error)[:1000],
                    "containment": containment,
                })
                self._persist()
                if isinstance(error, RollbackControllerError):
                    raise
                raise RollbackControllerError(
                    f"rollback failed in {self.state.get('phase')}: {error}"
                ) from error

    def _load_or_create_state(
        self,
        *,
        initial_evidence: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        self.transactions_root.mkdir(parents=True, exist_ok=True)
        active = _read_json(self.active_path) if self.active_path.is_file() else None
        if active is not None:
            active_transaction = str(active.get("transaction_id") or "")
            active_state_path = Path(str(active.get("state_path") or ""))
            if active_transaction != self.request.transaction_id:
                completed = False
                if active_state_path.is_file():
                    try:
                        completed = (
                            _read_json(active_state_path).get("phase") == COMPLETE
                        )
                    except RollbackRefusal:
                        completed = False
                if completed:
                    self.active_path.unlink(missing_ok=True)
                    active = None
                else:
                    raise RollbackRefusal(
                        "a conflicting rollback transaction is active: "
                        f"{active_transaction or 'UNKNOWN'}"
                    )

        if self.state_path.is_file():
            state = _read_json(self.state_path)
            if state.get("contract") != ROLLBACK_CONTRACT:
                raise RollbackRefusal(
                    f"rollback state contract is invalid: {self.state_path}")
            if state.get("request") != self.request.to_record():
                raise RollbackRefusal(
                    "existing rollback transaction request does not match")
            if state.get("phase") not in PHASES:
                raise RollbackRefusal(
                    f"rollback state phase is invalid: {state.get('phase')}")
            if state["phase"] != COMPLETE and active is None:
                _exclusive_write_json(
                    self.active_path,
                    self._active_marker_payload(),
                )
            return state

        if active is not None:
            raise RollbackRefusal(
                "active rollback marker has no matching transaction state")
        if initial_evidence is None:
            raise RollbackRefusal(
                "new rollback transaction has no verified preconditions")
        self.transaction_dir.mkdir(parents=True, exist_ok=False)
        prepared_at = isoformat()
        verified_at = isoformat()
        state = {
            "contract": ROLLBACK_CONTRACT,
            "transaction_id": self.request.transaction_id,
            "request": self.request.to_record(),
            "phase": PRECONDITIONS_VERIFIED,
            "created_at": prepared_at,
            "updated_at": verified_at,
            "history": [
                {
                    "phase": PREPARED,
                    "recorded_at": prepared_at,
                    "event": "transaction_created",
                },
                {
                    "phase": PRECONDITIONS_VERIFIED,
                    "recorded_at": verified_at,
                    "event": "preconditions_verified",
                },
            ],
            "evidence": dict(initial_evidence),
            "process": None,
            "resume_authorized": False,
            "errors": [],
            "containment": [],
        }
        _atomic_write_json(self.state_path, state)
        try:
            _exclusive_write_json(
                self.active_path,
                self._active_marker_payload(),
            )
        except BaseException:
            if self.state_path.is_file():
                state["history"].append({
                    "phase": PREPARED,
                    "recorded_at": isoformat(),
                    "event": "active_marker_creation_failed",
                })
                _atomic_write_json(self.state_path, state)
            raise
        return state

    def _active_marker_payload(self) -> dict[str, Any]:
        return {
            "contract": ROLLBACK_CONTRACT,
            "transaction_id": self.request.transaction_id,
            "state_path": str(self.state_path),
            "recorded_at": isoformat(),
        }

    def _persist(self) -> None:
        self.state["updated_at"] = isoformat()
        _atomic_write_json(self.state_path, self.state)

    def _record(
        self,
        *,
        phase: str | None = None,
        event: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        if phase is not None:
            if phase not in PHASES:
                raise ValueError(f"unknown rollback phase: {phase}")
            self.state["phase"] = phase
        if evidence:
            self.state.setdefault("evidence", {}).update(dict(evidence))
        self.state.setdefault("history", []).append({
            "phase": self.state["phase"],
            "recorded_at": isoformat(),
            "event": event,
        })
        self._persist()

    def _inject(self, point: str) -> None:
        if point not in FAULT_POINTS:
            raise ValueError(f"unknown rollback fault point: {point}")
        if self.fault_injector is not None:
            self.fault_injector(point, dict(self.state))

    def _execute(self) -> dict[str, Any]:
        while True:
            phase = self.state["phase"]
            if phase == PREPARED:
                evidence = self._initial_preconditions()
                self._record(
                    phase=PRECONDITIONS_VERIFIED,
                    event="preconditions_verified",
                    evidence=evidence,
                )
                continue

            if phase == PRECONDITIONS_VERIFIED:
                self._inject(FAULT_BEFORE_SNAPSHOT_VALIDATION)
                snapshot = self._validate_snapshot()
                self._record(
                    phase=SNAPSHOT_VALIDATED,
                    event="rollback_snapshot_validated",
                    evidence={"snapshot": snapshot},
                )
                continue

            if phase == SNAPSHOT_VALIDATED:
                self._assert_original_guard()
                forensic = self._capture_forensic_database()
                self._record(
                    phase=FORENSIC_CAPTURED,
                    event="failed_database_forensic_capture_completed",
                    evidence={"forensic_database": forensic},
                )
                self._inject(FAULT_AFTER_FORENSIC_CAPTURE)
                continue

            if phase == FORENSIC_CAPTURED:
                self._assert_original_guard()
                self._record(
                    phase=DATABASE_RESTORE_PENDING,
                    event="database_restore_intent_persisted",
                )
                continue

            if phase == DATABASE_RESTORE_PENDING:
                restored = self._restore_database()
                self._record(
                    phase=DATABASE_RESTORED,
                    event="database_restored",
                    evidence={"restored_database": restored},
                )
                self._inject(FAULT_AFTER_V38_RESTORE)
                continue

            if phase == DATABASE_RESTORED:
                authority = self._verify_target_database()
                self._record(
                    phase=DB_AUTHORITY_RESTORED,
                    event="database_live_release_authority_restored",
                    evidence={"restored_database_authority": authority},
                )
                self._inject(FAULT_AFTER_DB_AUTHORITY_RESTORE)
                continue

            if phase == DB_AUTHORITY_RESTORED:
                self._record(
                    phase=POINTER_RESTORE_PENDING,
                    event="pointer_restore_intent_persisted",
                )
                continue

            if phase == POINTER_RESTORE_PENDING:
                pointers = self._restore_pointers()
                self._record(
                    phase=POINTERS_RESTORED,
                    event="current_previous_pointers_restored",
                    evidence={"restored_pointers": pointers},
                )
                self._inject(FAULT_AFTER_POINTER_RESTORE)
                continue

            if phase == POINTERS_RESTORED:
                parity = self._verify_target_parity()
                self._record(
                    phase=PARITY_VERIFIED,
                    event="database_pointer_parity_verified",
                    evidence={"target_parity": parity},
                )
                continue

            if phase == PARITY_VERIFIED:
                runtime = self._validate_predecessor_runtime()
                bridge = self._install_runtime_bridge(
                    runtime_identity=runtime["self_validation"])
                database = inspect_database(
                    self.request.foundry_home / "foundry.db",
                    include_logical_digest=False,
                )
                self._record(
                    phase=PREDECESSOR_VALIDATED,
                    event="predecessor_runtime_validated",
                    evidence={
                        "predecessor_runtime": runtime,
                        "runtime_bridge": bridge,
                        "post_restore_effect_baseline": database["effects"],
                    },
                )
                continue

            if phase == PREDECESSOR_VALIDATED:
                self._assert_frozen_runtime_absent()
                self._inject(FAULT_BEFORE_PREDECESSOR_START)
                self._record(
                    phase=SUPERVISOR_START_PENDING,
                    event="predecessor_supervisor_start_intent_persisted",
                )
                continue

            if phase == SUPERVISOR_START_PENDING:
                process_record = self._start_or_adopt_predecessor()
                self.state["process"] = process_record
                self._record(
                    phase=SUPERVISOR_STARTED,
                    event="predecessor_supervisor_started",
                )
                self._inject(FAULT_AFTER_PREDECESSOR_START)
                continue

            if phase == SUPERVISOR_STARTED:
                if not self._exact_predecessor_processes():
                    self._rewind_after_runtime_loss(
                        "predecessor exited before health acceptance")
                    continue
                scheduler = self._require_scheduler(enabled=False)
                expected = self.state["evidence"]["predecessor_identity"]
                health = self.host.probe_predecessor(
                    self.request,
                    scheduler=scheduler,
                    expected_identity=expected,
                )
                self._record(
                    phase=HEALTH_ACCEPTED,
                    event="predecessor_health_accepted",
                    evidence={"predecessor_health": health},
                )
                continue

            if phase == HEALTH_ACCEPTED:
                verification = self._verify_post_start_invariants(
                    scheduler_enabled=False)
                self._record(
                    phase=POST_START_VERIFIED,
                    event="post_start_duplicate_and_effect_checks_passed",
                    evidence={"post_start_verification": verification},
                )
                continue

            if phase == POST_START_VERIFIED:
                self._inject(FAULT_BEFORE_SCHEDULER_REENABLE)
                self.state.setdefault("evidence", {})[
                    "scheduler_enable_command_started"
                ] = False
                self._record(
                    phase=SCHEDULER_ENABLE_PENDING,
                    event="scheduler_enable_intent_persisted",
                )
                continue

            if phase == SCHEDULER_ENABLE_PENDING:
                if self.state["evidence"].get(
                    "scheduler_enable_command_started"
                ):
                    self._recover_uncertain_scheduler_enable()
                    continue
                self.state["evidence"][
                    "scheduler_enable_command_started"
                ] = True
                self._record(
                    event="scheduler_enable_command_started",
                )
                scheduler = self.host.set_scheduler_enabled(
                    self.request, True)
                self._inject(FAULT_DURING_SCHEDULER_REENABLE)
                if scheduler.get("enabled") is not True:
                    raise RollbackControllerError(
                        "Scheduler enable returned an unproven state")
                self.state["evidence"][
                    "scheduler_enable_command_started"
                ] = False
                self._record(
                    phase=SCHEDULER_ENABLED,
                    event="scheduler_enabled",
                    evidence={"enabled_scheduler": scheduler},
                )
                continue

            if phase == SCHEDULER_ENABLED:
                final = self._verify_post_start_invariants(
                    scheduler_enabled=True)
                scheduler = self._require_scheduler(enabled=True)
                health = self.host.probe_predecessor(
                    self.request,
                    scheduler=scheduler,
                    expected_identity=self.state[
                        "evidence"]["predecessor_identity"],
                )
                self._record(
                    phase=COMPLETE,
                    event="rollback_transaction_durably_completed",
                    evidence={
                        "final_verification": final,
                        "final_health": health,
                    },
                )
                continue

            if phase == COMPLETE:
                evidence = self.state.setdefault("evidence", {})
                report = evidence.get("completion_report")
                prepared_bridge = evidence.get(
                    "runtime_bridge_completion")
                if (
                    not isinstance(report, Mapping)
                    or not isinstance(prepared_bridge, Mapping)
                ):
                    report = self._completion_report()
                    prepared_bridge = (
                        self._prepare_runtime_bridge_completion())
                    self.state["resume_authorized"] = False
                    self._record(
                        event="rollback_completion_prepared",
                        evidence={
                            "completion_report": report,
                            "runtime_bridge_completion": prepared_bridge,
                        },
                    )
                    continue
                if self.active_path.is_file():
                    self._clear_active_marker()
                    continue
                if self.state.get("resume_authorized") is not True:
                    self.state["resume_authorized"] = True
                    self._record(
                        event="rollback_owner_resume_authority_finalized")
                    continue
                final_report = report
                self._mark_runtime_bridge_complete()
                return final_report

            raise RollbackRefusal(f"unsupported rollback phase: {phase}")

    def _stop_record(self) -> dict[str, Any]:
        stop = EmergencyStop(
            self.request.foundry_home / "EMERGENCY_STOP").read()
        if not isinstance(stop, Mapping):
            raise RollbackRefusal(
                "durable emergency STOP/freeze is not active")
        return dict(stop)

    def _require_scheduler(self, *, enabled: bool) -> dict[str, Any]:
        scheduler = self.host.scheduler_contract(self.request)
        if scheduler.get("owned") is not True:
            raise RollbackRefusal("Scheduler task ownership is not proven")
        if bool(scheduler.get("enabled")) is not enabled:
            state = "enabled" if scheduler.get("enabled") else "disabled"
            wanted = "enabled" if enabled else "disabled"
            raise RollbackRefusal(
                f"Scheduler is {state}; rollback requires it {wanted}")
        return scheduler

    def _require_no_owned_processes(self) -> list[dict[str, Any]]:
        processes = self.host.owned_processes(self.request)
        if processes:
            summary = [
                {
                    "kind": item.get("kind"),
                    "pid": item.get("pid"),
                    "release_sha": item.get("release_sha"),
                }
                for item in processes
            ]
            raise RollbackRefusal(
                f"owned production process lineage is still alive: {summary}")
        return processes

    @staticmethod
    def _require_database_healthy(report: Mapping[str, Any]) -> None:
        if (
            report.get("quick_check") != "ok"
            or report.get("integrity_check") != "ok"
        ):
            raise RollbackRefusal(
                "database failed SQLite quick_check/integrity_check")

    @staticmethod
    def _require_barriers_clear(report: Mapping[str, Any]) -> None:
        effects = report.get("effects") or {}
        work = report.get("work") or {}
        if effects.get("status") != "CLEAR":
            raise RollbackRefusal(
                "unresolved, ambiguous, or duplicate effects block rollback: "
                f"{effects.get('unresolved') or effects.get('duplicate_starts')}"
            )
        if work.get("status") != "CLEAR":
            raise RollbackRefusal(
                "active mission/worker state blocks rollback: "
                f"{work}"
            )

    @staticmethod
    def _require_authority(
        report: Mapping[str, Any],
        expected_identity: Mapping[str, Any],
    ) -> None:
        authority = report.get("authority") or {}
        if authority.get("status") != "RECORDED":
            raise RollbackRefusal(
                f"database live authority is {authority.get('status')}")
        if (
            authority.get("release_id") != expected_identity.get("release_id")
            or authority.get("source_sha") != expected_identity.get("source_sha")
            or _identity_fields(authority.get("runtime_identity"))
            != _identity_fields(expected_identity)
        ):
            raise RollbackRefusal(
                "database live authority does not match the expected release")

    def _initial_preconditions(self) -> dict[str, Any]:
        stop = self._stop_record()
        scheduler = self._require_scheduler(enabled=False)
        self._require_no_owned_processes()
        current = read_current(self.request.install_root)
        previous = read_previous(self.request.install_root)
        if current != self.request.failed_source_sha:
            raise RollbackRefusal(
                f"CURRENT {current!r} is not failed release "
                f"{self.request.failed_source_sha}")
        if previous != self.request.predecessor_source_sha:
            raise RollbackRefusal(
                f"PREVIOUS {previous!r} is not predecessor "
                f"{self.request.predecessor_source_sha}")
        transition = self.request.install_root / "RELEASE_TRANSITION.json"
        if transition.exists():
            raise RollbackRefusal(
                f"an immutable release transition is already pending: {transition}")
        runtime_marker = rollback_runtime_marker(self.request.install_root)
        if runtime_marker.exists():
            raise RollbackRefusal(
                f"a rollback runtime marker already exists: {runtime_marker}")

        failed_identity = _manifest_identity(
            self.request.install_root,
            self.request.failed_source_sha,
            expected_manifest_sha256=self.request.failed_manifest_sha256,
            expected_package_sha256=self.request.failed_package_sha256,
        )
        predecessor_identity = _manifest_identity(
            self.request.install_root,
            self.request.predecessor_source_sha,
            expected_manifest_sha256=(
                self.request.predecessor_manifest_sha256
            ),
            expected_package_sha256=(
                self.request.predecessor_package_sha256
            ),
        )
        if failed_identity["schema"] == predecessor_identity["schema"]:
            raise RollbackRefusal(
                "cross-schema controller refuses a same-schema rollback")
        if _identity_fields(failed_identity["previous_runtime"]) != _identity_fields(
            predecessor_identity
        ):
            raise RollbackRefusal(
                "failed release previous_runtime does not identify PREVIOUS")

        failed_validation = self.host.validate_release_runtime(
            self.request,
            self.request.failed_source_sha,
            foundry_home=self.request.foundry_home,
        )
        predecessor_validation = self.host.validate_release_runtime(
            self.request,
            self.request.predecessor_source_sha,
            foundry_home=None,
        )
        if _identity_fields(failed_validation.get("runtime_identity")) != (
            _identity_fields(failed_identity)
        ):
            raise RollbackRefusal(
                "failed release self-validation identity does not match manifest")
        if _identity_fields(predecessor_validation.get("runtime_identity")) != (
            _identity_fields(predecessor_identity)
        ):
            raise RollbackRefusal(
                "predecessor self-validation identity does not match manifest")

        database = inspect_database(
            self.request.foundry_home / "foundry.db")
        self._require_database_healthy(database)
        self._require_barriers_clear(database)
        if database["schema"] != failed_identity["schema"]:
            raise RollbackRefusal(
                "live database schema does not match the failed release")
        self._require_authority(database, failed_identity)
        authority = release_authority_report(
            root=self.request.install_root,
            foundry_home=self.request.foundry_home,
        )
        if (
            authority.get("status") != "CONSISTENT"
            or authority.get("database_authority_mode")
            != "DATABASE_LIVE_SLOT"
        ):
            raise RollbackRefusal(
                "failed release database/pointer pair is inconsistent")
        return {
            "stop": stop,
            "disabled_scheduler": scheduler,
            "failed_identity": failed_identity,
            "predecessor_identity": predecessor_identity,
            "initial_database": database,
            "initial_release_authority": authority,
            "failed_self_validation": self._validation_summary(
                failed_validation),
            "predecessor_self_validation": self._validation_summary(
                predecessor_validation),
        }

    def _validate_failed_release_package(self) -> dict[str, Any]:
        anchored_identity = _manifest_identity(
            self.request.install_root,
            self.request.failed_source_sha,
            expected_manifest_sha256=self.request.failed_manifest_sha256,
            expected_package_sha256=self.request.failed_package_sha256,
        )
        expected = self.state["evidence"]["failed_identity"]
        if (
            _identity_fields(anchored_identity) != _identity_fields(expected)
            or anchored_identity.get("manifest_sha256")
            != expected.get("manifest_sha256")
            or (
                anchored_identity.get("package") or {}
            ).get("sha256") != (
                expected.get("package") or {}
            ).get("sha256")
        ):
            raise RollbackRefusal(
                "failed release package identity changed after preconditions")
        return anchored_identity

    @staticmethod
    def _validation_summary(validation: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "ok": validation.get("ok"),
            "source_sha": validation.get("source_sha"),
            "release_dir": validation.get("release_dir"),
            "python_exe": validation.get("python_exe"),
            "runtime_identity": validation.get("runtime_identity"),
            "legacy_identity": validation.get("legacy_identity"),
        }

    def _require_rollback_continuity(
        self,
        baseline: Mapping[str, Any],
        failed_database: Mapping[str, Any],
    ) -> dict[str, Any]:
        if baseline.get("schema") != failed_database.get("schema"):
            raise RollbackRefusal(
                "rollback continuity baseline schema does not match failed database")
        baseline_tables = baseline.get("tables") or {}
        failed_tables = failed_database.get("tables") or {}
        table_mismatches = [
            table_name
            for table_name in sorted(set(baseline_tables) | set(failed_tables))
            if baseline_tables.get(table_name) != failed_tables.get(table_name)
        ]
        if table_mismatches:
            raise RollbackRefusal(
                "rollback snapshot would discard post-snapshot effect/work "
                "idempotency evidence: "
                f"tables={table_mismatches}"
            )
        failed_identity = self.state["evidence"]["failed_identity"]
        predecessor_identity = self.state["evidence"]["predecessor_identity"]
        try:
            release_projection = _validate_release_projection_continuity(
                baseline.get("release_projection") or {},
                failed_database.get("release_projection") or {},
                failed_identity=failed_identity,
                predecessor_identity=predecessor_identity,
                failed_manifest_sha256=self.request.failed_manifest_sha256,
            )
            event_continuity = _validate_activation_event_suffix(
                Path(str(baseline.get("path") or "")).resolve(),
                Path(str(failed_database.get("path") or "")).resolve(),
                baseline_events=baseline.get("events") or {},
                failed_identity=failed_identity,
                predecessor_identity=predecessor_identity,
                failed_manifest_sha256=self.request.failed_manifest_sha256,
                stop_record=self.state["evidence"]["stop"],
            )
        except RollbackRefusal as error:
            raise RollbackRefusal(
                "rollback snapshot would discard post-snapshot effect/work "
                f"idempotency evidence: {error}"
            ) from error
        return {
            "status": "PRESERVED",
            "schema": baseline.get("schema"),
            "event_continuity": event_continuity,
            "release_projection": release_projection,
            "protected_table_count": len(baseline_tables),
            "protected_tables": baseline_tables,
            "excluded_tables": baseline.get("excluded_tables"),
        }

    def _validate_snapshot(self) -> dict[str, Any]:
        if self.owned_snapshot_path.exists():
            if not self.owned_snapshot_path.is_file():
                raise RollbackRefusal(
                    "transaction-owned rollback snapshot is not a file")
            observed_hash = _sha256_file(self.owned_snapshot_path)
            if observed_hash != self.request.snapshot_sha256:
                raise RollbackRefusal(
                    "transaction-owned rollback snapshot SHA-256 changed")
        else:
            if not self.request.snapshot_path.is_file():
                raise RollbackRefusal(
                    f"rollback snapshot missing: {self.request.snapshot_path}")
            observed_hash = _sha256_file(self.request.snapshot_path)
            if observed_hash != self.request.snapshot_sha256:
                raise RollbackRefusal(
                    "rollback snapshot SHA-256 does not match the frozen input")
            _atomic_copy_file(
                self.request.snapshot_path,
                self.owned_snapshot_path,
            )
            owned_hash = _sha256_file(self.owned_snapshot_path)
            if owned_hash != self.request.snapshot_sha256:
                raise RollbackRefusal(
                    "rollback snapshot changed while being pinned")

        snapshot = inspect_database(self.owned_snapshot_path)
        expected = self.state["evidence"]["predecessor_identity"]
        self._require_database_healthy(snapshot)
        self._require_barriers_clear(snapshot)
        if snapshot["schema"] != expected["schema"]:
            raise RollbackRefusal(
                "rollback snapshot schema does not match the predecessor")
        self._require_authority(snapshot, expected)
        snapshot["source_path"] = str(self.request.snapshot_path)
        snapshot["owned_copy"] = True
        failed_identity = self.state["evidence"]["failed_identity"]
        self._validate_failed_release_package()
        baseline_migration = self.host.build_continuity_baseline(
            self.request,
            source_sha=self.request.failed_source_sha,
            snapshot_path=self.owned_snapshot_path,
            destination_path=self.continuity_baseline_path,
            expected_schema=int(failed_identity["schema"]),
        )
        baseline_continuity = database_rollback_continuity(
            self.continuity_baseline_path)
        if (
            baseline_continuity["file_sha256"]
            != baseline_migration.get("file_sha256")
            or baseline_continuity["schema"] != failed_identity["schema"]
        ):
            raise RollbackRefusal(
                "transaction-owned rollback continuity baseline changed")
        failed_continuity = database_rollback_continuity(
            self.request.foundry_home / "foundry.db")
        if failed_continuity["schema"] != failed_identity["schema"]:
            raise RollbackRefusal(
                "failed database schema changed during snapshot validation")
        snapshot["continuity_baseline"] = {
            **baseline_migration,
            "continuity": baseline_continuity,
        }
        snapshot["failed_continuity"] = failed_continuity
        snapshot["rollback_continuity"] = (
            self._require_rollback_continuity(
                baseline_continuity,
                failed_continuity,
            )
        )
        return snapshot

    def _validated_owned_snapshot(self) -> dict[str, Any]:
        recorded = self.state.get("evidence", {}).get("snapshot")
        if not isinstance(recorded, Mapping):
            raise RollbackRefusal(
                "validated rollback snapshot evidence is missing")
        recorded_path = Path(str(recorded.get("path") or "")).resolve()
        if not _same_path(recorded_path, self.owned_snapshot_path):
            raise RollbackRefusal(
                "validated rollback snapshot is outside the transaction")
        if not self.owned_snapshot_path.is_file():
            raise RollbackRefusal(
                "transaction-owned rollback snapshot is missing")
        observed_hash = _sha256_file(self.owned_snapshot_path)
        if observed_hash != self.request.snapshot_sha256:
            raise RollbackRefusal(
                "transaction-owned rollback snapshot SHA-256 changed")
        baseline = recorded.get("continuity_baseline")
        if not isinstance(baseline, Mapping):
            raise RollbackRefusal(
                "validated rollback continuity baseline evidence is missing")
        recorded_baseline_path = Path(str(baseline.get("path") or "")).resolve()
        if not _same_path(
            recorded_baseline_path, self.continuity_baseline_path
        ):
            raise RollbackRefusal(
                "rollback continuity baseline is outside the transaction")
        if (
            not self.continuity_baseline_path.is_file()
            or _sha256_file(self.continuity_baseline_path)
            != baseline.get("file_sha256")
        ):
            raise RollbackRefusal(
                "transaction-owned rollback continuity baseline changed")
        observed = inspect_database(self.owned_snapshot_path)
        if (
            observed.get("logical", {}).get("sha256")
            != recorded.get("logical", {}).get("sha256")
        ):
            raise RollbackRefusal(
                "transaction-owned rollback snapshot logical identity changed")
        return observed

    def _assert_original_guard(self) -> dict[str, Any]:
        self._stop_record()
        self._require_scheduler(enabled=False)
        self._require_no_owned_processes()
        if read_current(self.request.install_root) != self.request.failed_source_sha:
            raise RollbackRefusal(
                "CURRENT changed before database restoration")
        if read_previous(
            self.request.install_root
        ) != self.request.predecessor_source_sha:
            raise RollbackRefusal(
                "PREVIOUS changed before database restoration")
        observed = inspect_database(
            self.request.foundry_home / "foundry.db")
        expected = self.state["evidence"]["initial_database"]
        if observed["logical"]["sha256"] != expected["logical"]["sha256"]:
            raise RollbackRefusal(
                "live database changed after rollback preconditions were frozen")
        self._require_authority(
            observed, self.state["evidence"]["failed_identity"])
        self._require_barriers_clear(observed)
        return observed

    def _capture_forensic_database(self) -> dict[str, Any]:
        failed = self.state["evidence"]["failed_identity"]
        path = self.evidence_dir / (
            f"failed-{self.request.failed_source_sha[:12]}"
            f"-schema-{failed['schema']}.db"
        )
        if not path.exists():
            FoundryDatabase(
                self.request.foundry_home / "foundry.db").backup_to(path)
        forensic = inspect_database(path)
        initial = self.state["evidence"]["initial_database"]
        if forensic["logical"]["sha256"] != initial["logical"]["sha256"]:
            raise RollbackRefusal(
                "failed-release forensic database does not match frozen live state")
        self._require_authority(forensic, failed)
        return forensic

    def _restore_database(self) -> dict[str, Any]:
        self._stop_record()
        self._require_scheduler(enabled=False)
        self._require_no_owned_processes()
        pinned_snapshot = self._validated_owned_snapshot()
        live_path = self.request.foundry_home / "foundry.db"
        observed = inspect_database(live_path)
        initial_digest = self.state[
            "evidence"]["initial_database"]["logical"]["sha256"]
        snapshot_digest = self.state[
            "evidence"]["snapshot"]["logical"]["sha256"]
        observed_digest = observed["logical"]["sha256"]
        if observed_digest == snapshot_digest:
            restored = observed
        elif observed_digest == initial_digest:
            FoundryDatabase(live_path).restore_from(
                Path(pinned_snapshot["path"]))
            restored = inspect_database(live_path)
        else:
            raise RollbackRefusal(
                "database restore re-entry found neither the failed nor target state")
        if restored["logical"]["sha256"] != snapshot_digest:
            raise RollbackRefusal(
                "restored database logical fingerprint does not match snapshot")
        self._require_database_healthy(restored)
        self._require_barriers_clear(restored)
        expected = self.state["evidence"]["predecessor_identity"]
        if restored["schema"] != expected["schema"]:
            raise RollbackRefusal(
                "restored database schema does not match predecessor")
        self._require_authority(restored, expected)
        return restored

    def _verify_target_database(self) -> dict[str, Any]:
        self._stop_record()
        self._require_scheduler(enabled=False)
        self._require_no_owned_processes()
        report = inspect_database(
            self.request.foundry_home / "foundry.db")
        expected = self.state["evidence"]["predecessor_identity"]
        self._require_database_healthy(report)
        self._require_barriers_clear(report)
        if report["schema"] != expected["schema"]:
            raise RollbackRefusal(
                "target database schema changed after restore")
        self._require_authority(report, expected)
        return report["authority"]

    def _restore_pointers(self) -> dict[str, Any]:
        self._stop_record()
        self._require_scheduler(enabled=False)
        self._require_no_owned_processes()
        self._verify_target_database()
        current = read_current(self.request.install_root)
        previous = read_previous(self.request.install_root)
        if current not in {
            self.request.failed_source_sha,
            self.request.predecessor_source_sha,
        }:
            raise RollbackRefusal(
                f"unexpected CURRENT during pointer recovery: {current}")
        if previous not in {
            self.request.failed_source_sha,
            self.request.predecessor_source_sha,
        }:
            raise RollbackRefusal(
                f"unexpected PREVIOUS during pointer recovery: {previous}")
        if previous != self.request.failed_source_sha:
            write_pointer(
                previous_pointer(self.request.install_root),
                self.request.failed_source_sha,
            )
        if current != self.request.predecessor_source_sha:
            write_pointer(
                current_pointer(self.request.install_root),
                self.request.predecessor_source_sha,
            )
        current = read_current(self.request.install_root)
        previous = read_previous(self.request.install_root)
        if (
            current != self.request.predecessor_source_sha
            or previous != self.request.failed_source_sha
        ):
            raise RollbackRefusal(
                "CURRENT/PREVIOUS restoration did not reach the target pair")
        return {"current": current, "previous": previous}

    def _verify_target_parity(self) -> dict[str, Any]:
        self._stop_record()
        self._require_scheduler(enabled=False)
        self._require_no_owned_processes()
        self._verify_target_database()
        if (
            read_current(self.request.install_root)
            != self.request.predecessor_source_sha
            or read_previous(self.request.install_root)
            != self.request.failed_source_sha
        ):
            raise RollbackRefusal(
                "restored pointer pair changed before parity verification")
        authority = release_authority_report(
            root=self.request.install_root,
            foundry_home=self.request.foundry_home,
        )
        if (
            authority.get("status") != "CONSISTENT"
            or authority.get("database_authority_mode")
            != "DATABASE_LIVE_SLOT"
            or authority.get("database_active_source_sha")
            != self.request.predecessor_source_sha
        ):
            raise RollbackRefusal(
                "predecessor database/pointer parity is not consistent")
        return authority

    def _validate_predecessor_runtime(self) -> dict[str, Any]:
        anchored_identity = _manifest_identity(
            self.request.install_root,
            self.request.predecessor_source_sha,
            expected_manifest_sha256=(
                self.request.predecessor_manifest_sha256
            ),
            expected_package_sha256=(
                self.request.predecessor_package_sha256
            ),
        )
        expected = self.state["evidence"]["predecessor_identity"]
        if (
            _identity_fields(anchored_identity) != _identity_fields(expected)
            or anchored_identity.get("manifest_sha256")
            != expected.get("manifest_sha256")
            or (
                anchored_identity.get("package") or {}
            ).get("sha256") != (
                expected.get("package") or {}
            ).get("sha256")
        ):
            raise RollbackRefusal(
                "predecessor package identity changed after preconditions")
        self_validation = self.host.validate_release_runtime(
            self.request,
            self.request.predecessor_source_sha,
            foundry_home=self.request.foundry_home,
        )
        if _identity_fields(
            self_validation.get("runtime_identity")
        ) != _identity_fields(expected):
            raise RollbackRefusal(
                "predecessor runtime self-validation identity changed")
        launcher = self.host.validate_release_launcher(
            self.request, self.request.predecessor_source_sha)
        return {
            "self_validation": self._validation_summary(self_validation),
            "launcher": launcher,
        }

    def _install_runtime_bridge(
        self, *, runtime_identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        shim_dir = rollback_runtime_shim_dir(self.request.install_root)
        shim_path = shim_dir / "sitecustomize.py"
        _atomic_write_text(shim_path, FROZEN_SITE_CUSTOMIZE)
        shim_hash = _sha256_file(shim_path)
        marker_path = rollback_runtime_marker(self.request.install_root)
        marker = {
            "contract": ROLLBACK_RUNTIME_CONTRACT,
            "status": "ACTIVE",
            "transaction_id": self.request.transaction_id,
            "source_sha": self.request.predecessor_source_sha,
            "release_id": runtime_identity.get("runtime_identity", {}).get(
                "release_id"),
            "schema": self.state["evidence"]["predecessor_identity"]["schema"],
            "install_root": str(self.request.install_root),
            "foundry_home": str(self.request.foundry_home),
            "sitecustomize_path": str(shim_path.resolve()),
            "sitecustomize_sha256": shim_hash,
            "transaction_state_path": str(self.state_path.resolve()),
            "recorded_at": isoformat(),
        }
        if marker_path.exists():
            existing = _read_json(marker_path)
            comparable = {
                key: existing.get(key)
                for key in (
                    "contract",
                    "transaction_id",
                    "source_sha",
                    "install_root",
                    "foundry_home",
                    "sitecustomize_sha256",
                    "transaction_state_path",
                )
            }
            expected = {
                key: marker.get(key)
                for key in comparable
            }
            if comparable != expected:
                raise RollbackRefusal(
                    "conflicting rollback runtime marker exists")
        _atomic_write_json(marker_path, marker)
        return marker

    def _prepare_runtime_bridge_completion(self) -> dict[str, Any]:
        if self.state.get("phase") != COMPLETE:
            raise RollbackRefusal(
                "rollback runtime bridge cannot prepare before transaction")
        marker_path = rollback_runtime_marker(self.request.install_root)
        marker = _read_json(marker_path)
        if (
            marker.get("transaction_id") != self.request.transaction_id
            or marker.get("source_sha")
            != self.request.predecessor_source_sha
            or not _same_path(
                marker.get("transaction_state_path", ""), self.state_path)
        ):
            raise RollbackRefusal(
                "rollback runtime marker changed before completion")
        if marker.get("status") != "ACTIVE":
            raise RollbackRefusal(
                "rollback runtime marker is not active during preparation")
        marker["status"] = "COMPLETE_FROZEN_RUNTIME"
        marker["completed_at"] = isoformat()
        return marker

    def _mark_runtime_bridge_complete(self) -> dict[str, Any]:
        if (
            self.state.get("phase") != COMPLETE
            or self.state.get("resume_authorized") is not True
            or self.active_path.exists()
        ):
            raise RollbackRefusal(
                "rollback runtime bridge cannot complete before finalization")
        prepared = self.state.get("evidence", {}).get(
            "runtime_bridge_completion")
        if not isinstance(prepared, Mapping):
            raise RollbackRefusal(
                "prepared runtime bridge completion evidence is missing")
        expected = dict(prepared)
        marker_path = rollback_runtime_marker(self.request.install_root)
        observed = _read_json(marker_path)
        if observed == expected:
            return observed
        immutable_fields = (
            "contract",
            "transaction_id",
            "source_sha",
            "release_id",
            "schema",
            "install_root",
            "foundry_home",
            "sitecustomize_path",
            "sitecustomize_sha256",
            "transaction_state_path",
            "recorded_at",
        )
        if (
            observed.get("status") != "ACTIVE"
            or expected.get("status") != "COMPLETE_FROZEN_RUNTIME"
            or any(
                observed.get(field) != expected.get(field)
                for field in immutable_fields
            )
        ):
            raise RollbackRefusal(
                "rollback runtime marker changed before final publication")
        try:
            _atomic_write_json(marker_path, expected)
        except Exception:
            try:
                if _read_json(marker_path) == expected:
                    return expected
            except RollbackRefusal:
                pass
            raise
        return expected

    def _assert_frozen_runtime_absent(self) -> None:
        self._stop_record()
        self._require_scheduler(enabled=False)
        self._require_no_owned_processes()
        self._verify_target_parity()

    def _exact_predecessor_processes(
        self,
        *,
        scheduler_wrapper_grace_seconds: float = 0.0,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + max(
            0.0, scheduler_wrapper_grace_seconds)
        while True:
            processes = self.host.owned_processes(self.request)
            supervisors = [
                item for item in processes
                if item.get("kind") == "supervisor"
                and item.get("release_sha")
                == self.request.predecessor_source_sha
            ]
            non_supervisors = [
                item for item in processes
                if item.get("kind") != "supervisor"
            ]
            unexpected_supervisors = [
                item for item in processes
                if item.get("kind") == "supervisor"
                and item.get("release_sha")
                != self.request.predecessor_source_sha
            ]
            transient_wrapper = bool(
                non_supervisors
                and all(
                    item.get("kind") == "scheduler_wrapper"
                    for item in non_supervisors
                )
                and not unexpected_supervisors
                and len(supervisors) == 1
                and time.monotonic() < deadline
            )
            if transient_wrapper:
                time.sleep(0.1)
                continue
            if (
                non_supervisors
                or unexpected_supervisors
                or len(supervisors) > 1
            ):
                raise RollbackRefusal(
                    "runtime process census found duplicate or unexpected "
                    "owned lineage")
            return supervisors

    def _start_or_adopt_predecessor(self) -> dict[str, Any]:
        self._stop_record()
        scheduler = self._require_scheduler(enabled=False)
        existing = self._exact_predecessor_processes()
        if existing:
            process = existing[0]
            return {
                "root_pid": process.get("pid"),
                "root_creation_identity": process.get("creation_identity"),
                "supervisor_processes": existing,
                "adopted_after_interruption": True,
                "started_at": isoformat(),
            }
        runtime = self.host.validate_release_runtime(
            self.request,
            self.request.predecessor_source_sha,
            foundry_home=self.request.foundry_home,
        )
        return self.host.start_predecessor(
            self.request,
            runtime_identity=runtime,
            scheduler=scheduler,
            shim_dir=rollback_runtime_shim_dir(
                self.request.install_root),
            marker_path=rollback_runtime_marker(
                self.request.install_root),
            log_path=self.log_path,
        )

    def _rewind_after_runtime_loss(self, reason: str) -> None:
        scheduler = self.host.scheduler_contract(self.request)
        if scheduler.get("enabled"):
            self.host.set_scheduler_enabled(self.request, False)
        self.state["process"] = None
        self.state.get("evidence", {}).pop("predecessor_health", None)
        self.state.get("evidence", {}).pop("post_start_verification", None)
        self._record(
            phase=PREDECESSOR_VALIDATED,
            event=f"runtime_reentry_rewind:{reason}",
        )

    def _verify_post_start_invariants(
        self, *, scheduler_enabled: bool,
    ) -> dict[str, Any]:
        stop = self._stop_record()
        scheduler = self._require_scheduler(enabled=scheduler_enabled)
        processes = self._exact_predecessor_processes(
            scheduler_wrapper_grace_seconds=(
                5.0 if scheduler_enabled else 0.0
            )
        )
        if len(processes) != 1:
            raise RollbackRefusal(
                "exactly one predecessor Supervisor is required")
        parity = self._verify_target_parity_with_runtime()
        database = inspect_database(
            self.request.foundry_home / "foundry.db",
            include_logical_digest=False,
        )
        self._require_database_healthy(database)
        self._require_barriers_clear(database)
        baseline = self.state[
            "evidence"]["post_restore_effect_baseline"]
        if (
            database["effects"]["event_count"] != baseline["event_count"]
            or database["effects"]["event_sha256"] != baseline["event_sha256"]
            or database["effects"]["journal_counts"]
            != baseline["journal_counts"]
        ):
            raise RollbackRefusal(
                "effect journal changed while predecessor was frozen")
        return {
            "stop": stop,
            "scheduler_enabled": scheduler_enabled,
            "scheduler": scheduler,
            "processes": processes,
            "parity": parity,
            "effects": database["effects"],
            "work": database["work"],
            "schema": database["schema"],
        }

    def _verify_target_parity_with_runtime(self) -> dict[str, Any]:
        if (
            read_current(self.request.install_root)
            != self.request.predecessor_source_sha
            or read_previous(self.request.install_root)
            != self.request.failed_source_sha
        ):
            raise RollbackRefusal(
                "runtime pointer pair does not match rollback target")
        database = inspect_database(
            self.request.foundry_home / "foundry.db",
            include_logical_digest=False,
        )
        expected = self.state["evidence"]["predecessor_identity"]
        if database["schema"] != expected["schema"]:
            raise RollbackRefusal(
                "runtime database schema does not match predecessor")
        self._require_authority(database, expected)
        authority = release_authority_report(
            root=self.request.install_root,
            foundry_home=self.request.foundry_home,
        )
        if (
            authority.get("status") != "CONSISTENT"
            or authority.get("database_authority_mode")
            != "DATABASE_LIVE_SLOT"
        ):
            raise RollbackRefusal(
                "runtime database/pointer authority is inconsistent")
        return authority

    def _recover_uncertain_scheduler_enable(self) -> None:
        self._stop_record()
        scheduler = self.host.scheduler_contract(self.request)
        if scheduler.get("enabled"):
            self.host.set_scheduler_enabled(self.request, False)
        process_record = self.state.get("process")
        termination = None
        if isinstance(process_record, Mapping):
            termination = self.host.terminate_started_process(
                self.request, process_record)
        remaining = self.host.owned_processes(self.request)
        owned_termination = None
        if remaining:
            owned_termination = self.host.terminate_owned_processes(
                self.request, remaining)
            remaining = self.host.owned_processes(self.request)
        if remaining:
            raise RollbackRefusal(
                "uncertain Scheduler enable containment left owned processes alive")
        self.state["process"] = None
        self.state["evidence"][
            "scheduler_enable_command_started"
        ] = False
        self.state["containment"].append({
            "recorded_at": isoformat(),
            "reason": "uncertain Scheduler enable was contained before re-entry",
            "scheduler_disabled": True,
            "termination": termination,
            "owned_termination": owned_termination,
        })
        self._record(
            phase=PREDECESSOR_VALIDATED,
            event="uncertain_scheduler_enable_contained_and_rewound",
        )

    def _contain(self, *, reason: str) -> dict[str, Any]:
        stop_path = self.request.foundry_home / "EMERGENCY_STOP"
        stop = EmergencyStop(stop_path)
        if not stop.active():
            stop.activate(
                f"rollback {self.request.transaction_id} containment: {reason}")
        scheduler_disabled = False
        scheduler_error = None
        try:
            scheduler = self.host.scheduler_contract(self.request)
            if scheduler.get("enabled"):
                self.host.set_scheduler_enabled(self.request, False)
            scheduler_disabled = not self.host.scheduler_contract(
                self.request).get("enabled")
        except Exception as error:
            scheduler_error = f"{type(error).__name__}: {error}"

        process_record = self.state.get("process")
        termination: dict[str, Any] | None = None
        if isinstance(process_record, Mapping):
            try:
                termination = self.host.terminate_started_process(
                    self.request, process_record)
            except Exception as error:
                termination = {
                    "error": f"{type(error).__name__}: {error}",
                }
        elif self.state.get("phase") == SUPERVISOR_START_PENDING:
            exact = self._exact_predecessor_processes()
            if len(exact) == 1:
                adopted = {
                    "root_pid": exact[0].get("pid"),
                    "root_creation_identity": exact[0].get(
                        "creation_identity"),
                    "supervisor_processes": exact,
                }
                termination = self.host.terminate_started_process(
                    self.request, adopted)
        remaining = self.host.owned_processes(self.request)
        owned_termination: dict[str, Any] | None = None
        if remaining:
            try:
                owned_termination = self.host.terminate_owned_processes(
                    self.request, remaining)
            except Exception as error:
                owned_termination = {
                    "error": f"{type(error).__name__}: {error}",
                }
            remaining = self.host.owned_processes(self.request)
        complete = scheduler_disabled and not remaining and stop.active()
        record = {
            "recorded_at": isoformat(),
            "reason": reason,
            "status": "COMPLETE" if complete else "INCOMPLETE",
            "stop_active": stop.active(),
            "scheduler_disabled": scheduler_disabled,
            "scheduler_error": scheduler_error,
            "termination": termination,
            "owned_termination": owned_termination,
            "remaining_processes": remaining,
        }
        self.state.setdefault("containment", []).append(record)
        if (
            complete
            and self.state.get("phase") in {
                SUPERVISOR_START_PENDING,
                SUPERVISOR_STARTED,
                HEALTH_ACCEPTED,
                POST_START_VERIFIED,
                SCHEDULER_ENABLE_PENDING,
                SCHEDULER_ENABLED,
                COMPLETE,
            }
            and read_current(self.request.install_root)
            == self.request.predecessor_source_sha
        ):
            self.state["phase"] = PREDECESSOR_VALIDATED
            self.state["process"] = None
            self.state["evidence"][
                "scheduler_enable_command_started"
            ] = False
            self.state["evidence"].pop("completion_report", None)
            self.state["evidence"].pop(
                "runtime_bridge_completion", None)
            self.state["resume_authorized"] = False
        self._persist()
        return record

    def _clear_active_marker(self) -> None:
        if not self.active_path.is_file():
            return
        active = _read_json(self.active_path)
        if active.get("transaction_id") != self.request.transaction_id:
            raise RollbackRefusal(
                "active rollback marker changed before completion")
        self.active_path.unlink()

    def _completion_report(self) -> dict[str, Any]:
        database = inspect_database(
            self.request.foundry_home / "foundry.db",
            include_logical_digest=False,
        )
        scheduler = self.host.scheduler_contract(self.request)
        processes = self.host.owned_processes(self.request)
        return {
            "status": "ROLLBACK_COMPLETE",
            "contract": ROLLBACK_CONTRACT,
            "transaction_id": self.request.transaction_id,
            "phase": self.state.get("phase"),
            "state_path": str(self.state_path),
            "current": read_current(self.request.install_root),
            "previous": read_previous(self.request.install_root),
            "database": {
                "path": database["path"],
                "schema": database["schema"],
                "quick_check": database["quick_check"],
                "integrity_check": database["integrity_check"],
                "authority": database["authority"],
            },
            "scheduler": scheduler,
            "processes": processes,
            "stop": EmergencyStop(
                self.request.foundry_home / "EMERGENCY_STOP").read(),
            "effects": database["effects"],
            "work": database["work"],
            "snapshot": self.state.get("evidence", {}).get("snapshot"),
            "forensic_database": self.state.get(
                "evidence", {}).get("forensic_database"),
            "health": self.state.get(
                "evidence", {}).get("final_health"),
            "trust_state": "SUSPENDED",
        }


def run_cross_schema_rollback(
    request: RollbackRequest,
    *,
    host: RollbackHost | None = None,
    fault_injector: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    return CrossSchemaRollbackController(
        request,
        host=host,
        fault_injector=fault_injector,
    ).run()


__all__ = [
    "COMPLETE",
    "CrossSchemaRollbackController",
    "FAULT_POINTS",
    "FROZEN_SITE_CUSTOMIZE",
    "RollbackControllerError",
    "RollbackHost",
    "RollbackInterruption",
    "RollbackRefusal",
    "RollbackRequest",
    "WindowsRollbackHost",
    "database_logical_digest",
    "inspect_database",
    "release_package_digest",
    "run_cross_schema_rollback",
]
