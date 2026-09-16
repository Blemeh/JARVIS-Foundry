"""Bounded local computer and filesystem hands for a single owned root.

This connector deliberately owns no planning, scheduling, policy, or effect
authority. Callers provide explicit operations and exact executable paths; the
connector enforces root containment, durable replay, readback, and exact
process identity before any local mutation.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..platform.windows_subprocess import (
    headless_popen_kwargs,
    pid_alive,
    sanitized_env,
)
from ..supervisor.worker import (
    list_process_tree,
    process_creation_identity,
)
from .windows_operator import (
    VisibleWindow,
    is_helper_window_class,
    prefer_application_windows,
    resolve_launch_window_owner,
)


RECEIPT_SCHEMA = "jarvis.personal_operator.receipt.v1"
STATE_DIRECTORY = ".jarvis-personal-operator"
LEDGER_FILE = "operations.json"
PROCESS_REGISTRY_FILE = "processes.json"
_PROCESS_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _visible_application_windows() -> tuple[VisibleWindow, ...]:
    """Snapshot visible non-helper top-level windows on this desktop."""

    if os.name != "nt":
        return ()
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetClassNameW.restype = ctypes.c_int
    found: list[VisibleWindow] = []

    @callback_type
    def collect(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, 256)
        if is_helper_window_class(class_name.value):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        found.append(
            VisibleWindow(
                hwnd=int(hwnd),
                pid=int(pid.value),
                title=title.value,
                class_name=class_name.value,
            )
        )
        return True

    if not user32.EnumWindows(collect, 0):
        return ()
    return tuple(found)


def _unique_owned_window(pid: int) -> VisibleWindow:
    """Return the unique application frame for a live owned PID."""

    ranked = prefer_application_windows(
        [
            window
            for window in _visible_application_windows()
            if window.pid == int(pid)
        ]
    )
    if not ranked:
        raise PersonalOperatorError(
            "window_not_found",
            "owned process has no visible top-level window",
        )
    if len(ranked) != 1:
        raise PersonalOperatorError(
            "window_ambiguous",
            "owned process has multiple visible top-level windows",
        )
    return ranked[0]


class PersonalOperatorError(ValueError):
    """A bounded request was refused with a stable classification."""

    def __init__(
        self,
        classification: str,
        message: str,
        *,
        status: str = "FAILED",
    ) -> None:
        super().__init__(message)
        self.classification = classification
        self.status = status


class InjectedOperatorFailure(RuntimeError):
    """Test-only fault raised by a caller-provided fault injector."""


@dataclass(frozen=True, slots=True)
class PersonalOperatorLimits:
    max_file_bytes: int = 16 * 1024 * 1024
    max_search_files: int = 1_000
    max_search_results: int = 50
    max_search_file_bytes: int = 1 * 1024 * 1024
    max_command_seconds: float = 30.0
    max_command_output_bytes: int = 1 * 1024 * 1024
    max_command_args: int = 64
    max_inspect_items: int = 128

    def __post_init__(self) -> None:
        numeric = (
            self.max_file_bytes,
            self.max_search_files,
            self.max_search_results,
            self.max_search_file_bytes,
            self.max_command_output_bytes,
            self.max_command_args,
            self.max_inspect_items,
        )
        if any(value <= 0 for value in numeric) or self.max_command_seconds <= 0:
            raise ValueError("all personal operator limits must be positive")


def _sha256_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    """Return a detached JSON-safe value and fail on accidental rich objects."""
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True))


def _same_executable_path(expected: object, observed: object) -> bool:
    if not expected or not observed:
        return False
    try:
        left = Path(str(expected)).resolve(strict=False)
        right = Path(str(observed)).resolve(strict=False)
    except OSError:
        return os.path.normcase(str(expected)) == os.path.normcase(str(observed))
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _terminate_popen_exact(
    process: subprocess.Popen[bytes],
    *,
    grace_seconds: float,
) -> tuple[bool, dict[str, Any]]:
    """Terminate through the retained process handle, never a fresh PID lookup."""
    if process.poll() is not None:
        return True, {"pid": process.pid, "live": False, "via": "popen_handle"}
    try:
        process.terminate()
        process.wait(timeout=max(0.1, grace_seconds))
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
    return process.poll() is not None, {
        "pid": process.pid,
        "live": process.poll() is None,
        "exit_code": process.poll(),
        "via": "popen_handle",
    }


def _terminate_windows_exact(
    record: Mapping[str, Any],
    *,
    grace_seconds: float,
) -> tuple[bool, dict[str, Any], str | None]:
    """Open, verify, and terminate the exact process through one Windows handle."""
    if os.name != "nt":
        return False, {}, "exact process-handle termination requires Windows"
    expected = record.get("creation_identity")
    if not isinstance(expected, Mapping) or expected.get("create_time") is None:
        return False, {}, "record lacks a complete process creation identity"
    pid = int(record.get("pid") or 0)
    if pid <= 0:
        return False, {}, "record PID is invalid"
    try:
        import ctypes
        from ctypes import wintypes

        process_terminate = 0x0001
        synchronize = 0x00100000
        query = 0x1000
        still_active = 259
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(
            process_terminate | synchronize | query,
            False,
            pid,
        )
        if not handle:
            if pid_alive(pid):
                return (
                    False,
                    {"pid": pid, "live": True, "via": "windows_handle"},
                    "could not open a live PID with an exact termination handle",
                )
            return True, {"pid": pid, "live": False, "via": "windows_handle"}, None
        try:
            actual: dict[str, Any] = {
                "pid": pid,
                "live": False,
                "via": "windows_handle",
            }
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                actual["live"] = int(exit_code.value) == still_active
                actual["exit_code"] = int(exit_code.value)
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                actual["create_time"] = (
                    int(creation.dwHighDateTime) << 32
                ) | int(creation.dwLowDateTime)
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                actual["exe"] = buffer.value
            if not actual.get("live"):
                return True, actual, None
            if (
                actual.get("create_time") != expected.get("create_time")
                or not _same_executable_path(
                    expected.get("exe"),
                    actual.get("exe"),
                )
            ):
                return False, actual, "process identity changed before termination"
            if not kernel32.TerminateProcess(handle, 0):
                return False, actual, "TerminateProcess failed"
            waited = kernel32.WaitForSingleObject(
                handle,
                max(100, int(grace_seconds * 1000)),
            )
            exit_after = wintypes.DWORD()
            live_after = True
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_after)):
                live_after = int(exit_after.value) == still_active
            actual["live_after"] = live_after
            actual["exit_code_after"] = int(exit_after.value)
            if waited == wait_timeout or live_after:
                return False, actual, "exact process remained live after termination"
            return True, actual, None
        finally:
            kernel32.CloseHandle(handle)
    except Exception as error:  # noqa: BLE001 - exact close fails closed
        return False, {"pid": pid, "live": True}, f"{type(error).__name__}: {error}"


class PersonalOperatorConnector:
    """Reusable, restart-aware local operator rooted in one owned directory."""

    def __init__(
        self,
        root: str | Path,
        *,
        executable_allowlist: Iterable[str | Path] = (),
        environment_allowlist: Iterable[str] = (),
        limits: PersonalOperatorLimits | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        owned = Path(root).expanduser()
        owned.mkdir(parents=True, exist_ok=True)
        if not owned.is_dir():
            raise ValueError("personal operator root must be a directory")
        self.root = owned.resolve()
        self.limits = limits or PersonalOperatorLimits()
        self._fault_injector = fault_injector
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._state_root = self.root / STATE_DIRECTORY
        self._ledger_path = self._state_root / LEDGER_FILE
        self._process_registry_path = self._state_root / PROCESS_REGISTRY_FILE
        self._state_root.mkdir(parents=True, exist_ok=True)
        self._environment_allowlist = frozenset(str(name) for name in environment_allowlist)
        self._executables: dict[str, Path] = {}
        for executable in executable_allowlist:
            resolved = self._resolve_executable(executable, require_allowed=False)
            self._executables[self._path_key(resolved)] = resolved

    # ------------------------------------------------------------------
    # Common receipts, validation, and durable state
    # ------------------------------------------------------------------

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(str(path.resolve()))

    def _receipt(
        self,
        operation: str,
        *,
        request_id: str | None = None,
        status: str = "COMPLETED",
        before: Any = None,
        target_identity: Any = None,
        source_identity: Any = None,
        result: Any = None,
        readback: Any = None,
        classification: str | None = None,
        message: str | None = None,
        replayed: bool = False,
        reconciled: bool = False,
    ) -> dict[str, Any]:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "operation": operation,
            "request_id": request_id or f"operator-{uuid.uuid4().hex}",
            "status": status,
            "before": before,
            "target_identity": target_identity,
            "source_identity": source_identity,
            "result": result,
            "readback": readback,
            "error": (
                {"classification": classification, "message": message or classification}
                if classification
                else None
            ),
            "replayed": bool(replayed),
            "reconciled": bool(reconciled),
        }
        return _json_copy(receipt)

    def _error_receipt(
        self,
        operation: str,
        error: PersonalOperatorError,
        *,
        request_id: str | None = None,
        before: Any = None,
        target_identity: Any = None,
        source_identity: Any = None,
        result: Any = None,
        readback: Any = None,
    ) -> dict[str, Any]:
        return self._receipt(
            operation,
            request_id=request_id,
            status=error.status,
            before=before,
            target_identity=target_identity,
            source_identity=source_identity,
            result=result,
            readback=readback,
            classification=error.classification,
            message=str(error),
        )

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)

    @staticmethod
    def _validate_idempotency_key(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PersonalOperatorError(
                "invalid_input", "idempotency_key must be a non-empty string"
            )
        if len(value) > 512:
            raise PersonalOperatorError(
                "invalid_input", "idempotency_key exceeds 512 characters"
            )
        return value

    @staticmethod
    def _validate_process_id(value: str) -> str:
        if not isinstance(value, str) or not _PROCESS_ID.fullmatch(value):
            raise PersonalOperatorError(
                "invalid_input",
                "process_id must contain only letters, numbers, '.', '_', ':', or '-'",
            )
        return value

    def _resolve(
        self,
        reference: str | Path,
        *,
        allow_root: bool = False,
        allow_internal: bool = False,
    ) -> Path:
        if not isinstance(reference, (str, Path)):
            raise PersonalOperatorError("invalid_input", "path must be a string or Path")
        raw = str(reference)
        if "\x00" in raw or not raw.strip():
            raise PersonalOperatorError("invalid_input", "path is empty or contains NUL")
        relative = Path(raw)
        if relative.is_absolute():
            raise PersonalOperatorError("path_outside_root", "absolute paths are refused")
        if any(part == ".." for part in relative.parts):
            raise PersonalOperatorError("path_outside_root", "path traversal is refused")
        if relative == Path(".") and not allow_root:
            raise PersonalOperatorError("invalid_input", "the owned root is not a file target")
        if relative.parts and relative.parts[0] == STATE_DIRECTORY and not allow_internal:
            raise PersonalOperatorError(
                "reserved_path", "connector state paths are not caller-accessible"
            )
        candidate = self.root.joinpath(relative)
        try:
            resolved = candidate.resolve(strict=False)
            if os.path.commonpath((str(self.root), str(resolved))) != str(self.root):
                raise PersonalOperatorError(
                    "path_outside_root", "resolved path leaves the owned root"
                )
        except OSError as error:
            raise PersonalOperatorError("path_unavailable", str(error)) from error

        current = self.root
        for part in relative.parts:
            if part in {"", "."}:
                continue
            current = current / part
            if current.exists() and current.is_symlink():
                raise PersonalOperatorError(
                    "symlink_refused", "symbolic-link paths are refused"
                )
        return candidate

    def _relative_ref(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() if path != self.root else "."

    def _identity(self, path: Path) -> dict[str, Any]:
        reference = self._relative_ref(path)
        absolute = str(path.resolve(strict=False))
        if not path.exists():
            return {
                "ref": reference,
                "path": absolute,
                "exists": False,
                "kind": "missing",
                "size_bytes": 0,
                "sha256": None,
                "mtime_ns": None,
            }
        if path.is_symlink():
            raise PersonalOperatorError("symlink_refused", "symbolic links are refused")
        stat_result = path.stat()
        if path.is_file():
            if stat_result.st_size > self.limits.max_file_bytes:
                raise PersonalOperatorError(
                    "size_limit_exceeded",
                    f"file exceeds {self.limits.max_file_bytes} bytes",
                )
            content = path.read_bytes()
            return {
                "ref": reference,
                "path": absolute,
                "exists": True,
                "kind": "file",
                "size_bytes": len(content),
                "sha256": _sha256_bytes(content),
                "mtime_ns": stat_result.st_mtime_ns,
            }
        if path.is_dir():
            return {
                "ref": reference,
                "path": absolute,
                "exists": True,
                "kind": "directory",
                "size_bytes": 0,
                "sha256": None,
                "mtime_ns": stat_result.st_mtime_ns,
            }
        raise PersonalOperatorError("unsupported_file_type", "path is not a file or directory")

    def _read_file(self, path: Path) -> bytes:
        identity = self._identity(path)
        if not identity["exists"]:
            raise PersonalOperatorError("not_found", "source file does not exist")
        if identity["kind"] != "file":
            raise PersonalOperatorError("not_a_file", "operation requires a regular file")
        return path.read_bytes()

    def _load_json_table(self, path: Path, *, table_key: str) -> dict[str, dict[str, Any]]:
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PersonalOperatorError(
                "state_unreadable",
                f"{path.name} is unreadable",
                status="UNKNOWN",
            ) from error
        if not isinstance(raw, dict) or raw.get("schema") != 1:
            raise PersonalOperatorError(
                "state_malformed",
                f"{path.name} has an unsupported schema",
                status="UNKNOWN",
            )
        table = raw.get(table_key)
        if not isinstance(table, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in table.items()
        ):
            raise PersonalOperatorError(
                "state_malformed",
                f"{path.name} has a malformed {table_key} table",
                status="UNKNOWN",
            )
        return table

    def _atomic_write_json(
        self,
        path: Path,
        *,
        table_key: str,
        table: Mapping[str, Mapping[str, Any]],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema": 1, table_key: table},
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        self._atomic_replace(path, payload)

    def _load_ledger(self) -> dict[str, dict[str, Any]]:
        return self._load_json_table(self._ledger_path, table_key="operations")

    def _write_ledger(self, ledger: Mapping[str, Mapping[str, Any]]) -> None:
        self._atomic_write_json(
            self._ledger_path, table_key="operations", table=ledger
        )

    def _load_process_registry(self) -> dict[str, dict[str, Any]]:
        return self._load_json_table(
            self._process_registry_path, table_key="processes"
        )

    def _write_process_registry(
        self, registry: Mapping[str, Mapping[str, Any]]
    ) -> None:
        self._atomic_write_json(
            self._process_registry_path, table_key="processes", table=registry
        )

    @staticmethod
    def _atomic_replace(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    @staticmethod
    def _atomic_create(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                raise FileExistsError(str(path))
            if os.name == "nt":
                os.rename(temporary, path)
            else:
                try:
                    os.link(temporary, path)
                    os.unlink(temporary)
                except OSError:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    try:
                        with os.fdopen(fd, "wb") as handle:
                            handle.write(content)
                            handle.flush()
                            os.fsync(handle.fileno())
                    except BaseException:
                        try:
                            path.unlink()
                        except OSError:
                            pass
                        raise
                    os.unlink(temporary)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _operation_record(
        self,
        ledger: dict[str, dict[str, Any]],
        idempotency_key: str,
        *,
        operation: str,
        request: Mapping[str, Any],
        before: Mapping[str, Any],
        source: Mapping[str, Any] | None = None,
        desired_hash: str | None = None,
        backup_ref: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        fingerprint = _canonical_hash(dict(request))
        prior = ledger.get(idempotency_key)
        if prior is not None:
            if (
                prior.get("operation") != operation
                or prior.get("request_fingerprint") != fingerprint
            ):
                raise PersonalOperatorError(
                    "idempotency_conflict",
                    "idempotency key was already used for a different request",
                )
            return prior, True
        record = {
            "operation": operation,
            "request_fingerprint": fingerprint,
            "request": dict(request),
            "status": "PENDING",
            "before": dict(before),
            "source": dict(source) if source is not None else None,
            "desired_hash": desired_hash,
            "backup_ref": backup_ref,
            "started_at": _utc_now(),
        }
        ledger[idempotency_key] = record
        self._write_ledger(ledger)
        return record, False

    def _complete_operation(
        self,
        ledger: dict[str, dict[str, Any]],
        idempotency_key: str,
        *,
        target: Mapping[str, Any],
        source_after: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        record = ledger[idempotency_key]
        record.update(
            {
                "status": "COMPLETED",
                "target_after": dict(target),
                "source_after": dict(source_after) if source_after is not None else None,
                "result": dict(result) if result is not None else None,
                "completed_at": _utc_now(),
            }
        )
        self._write_ledger(ledger)

    # ------------------------------------------------------------------
    # Bounded filesystem hands
    # ------------------------------------------------------------------

    def inspect_file(
        self,
        reference: str | Path,
        *,
        preview_bytes: int = 0,
    ) -> dict[str, Any]:
        operation = "file.inspect"
        try:
            if preview_bytes < 0 or preview_bytes > 65_536:
                raise PersonalOperatorError(
                    "invalid_input", "preview_bytes must be between 0 and 65536"
                )
            target = self._resolve(reference, allow_root=True)
            identity = self._identity(target)
            result: dict[str, Any] = {"identity": identity}
            if preview_bytes and identity["kind"] == "file":
                content = target.read_bytes()[:preview_bytes]
                result["preview"] = content.decode("utf-8", errors="replace")
                result["preview_bytes"] = len(content)
            elif identity["kind"] == "directory":
                entries = sorted(item.name for item in target.iterdir())
                result["entries"] = entries[: self.limits.max_search_results]
                result["entries_truncated"] = len(entries) > self.limits.max_search_results
            return self._receipt(
                operation,
                before=identity,
                target_identity=identity,
                result=result,
                readback=identity,
                reconciled=True,
            )
        except PersonalOperatorError as error:
            return self._error_receipt(operation, error)
        except OSError as error:
            return self._error_receipt(
                operation, PersonalOperatorError("inspect_failed", str(error))
            )

    def search(
        self,
        *,
        name_pattern: str | None = None,
        content_query: str | None = None,
        start: str | Path = ".",
        max_results: int | None = None,
        max_files: int | None = None,
    ) -> dict[str, Any]:
        operation = "file.search"
        try:
            if not name_pattern and not content_query:
                raise PersonalOperatorError(
                    "invalid_input", "name_pattern or content_query is required"
                )
            if name_pattern is not None and not isinstance(name_pattern, str):
                raise PersonalOperatorError("invalid_input", "name_pattern must be text")
            if content_query is not None and (
                not isinstance(content_query, str) or not content_query
            ):
                raise PersonalOperatorError(
                    "invalid_input", "content_query must be non-empty text"
                )
            result_limit = (
                self.limits.max_search_results
                if max_results is None
                else min(max_results, self.limits.max_search_results)
            )
            file_limit = (
                self.limits.max_search_files
                if max_files is None
                else min(max_files, self.limits.max_search_files)
            )
            if result_limit <= 0 or file_limit <= 0:
                raise PersonalOperatorError(
                    "invalid_input", "search limits must be positive"
                )
            start_path = self._resolve(start, allow_root=True)
            start_identity = self._identity(start_path)
            if start_identity["kind"] != "directory":
                raise PersonalOperatorError(
                    "not_a_directory", "search start must be a directory"
                )
            matches: list[dict[str, Any]] = []
            scanned = 0
            limit_reached = False
            for current, directories, files in os.walk(start_path, followlinks=False):
                current_path = Path(current)
                directories[:] = sorted(
                    name
                    for name in directories
                    if name != STATE_DIRECTORY
                    and not (current_path / name).is_symlink()
                )
                for name in sorted(files):
                    if scanned >= file_limit:
                        limit_reached = True
                        break
                    path = current_path / name
                    if path.is_symlink():
                        continue
                    scanned += 1
                    relative = self._relative_ref(path)
                    if name_pattern and not (
                        fnmatch.fnmatch(name, name_pattern)
                        or fnmatch.fnmatch(relative, name_pattern)
                    ):
                        continue
                    snippet = None
                    if content_query is not None:
                        size = path.stat().st_size
                        if size > self.limits.max_search_file_bytes:
                            continue
                        text = path.read_bytes().decode("utf-8", errors="replace")
                        index = text.find(content_query)
                        if index < 0:
                            continue
                        start_index = max(0, index - 80)
                        end_index = min(len(text), index + len(content_query) + 80)
                        snippet = text[start_index:end_index]
                    identity = self._identity(path)
                    matches.append({"identity": identity, "snippet": snippet})
                    if len(matches) >= result_limit:
                        limit_reached = True
                        break
                if limit_reached:
                    break
            readback = {
                "scanned_files": scanned,
                "match_count": len(matches),
                "limit_reached": limit_reached,
            }
            return self._receipt(
                operation,
                before=start_identity,
                target_identity=start_identity,
                result={"matches": matches},
                readback=readback,
                reconciled=True,
            )
        except PersonalOperatorError as error:
            return self._error_receipt(operation, error)
        except OSError as error:
            return self._error_receipt(
                operation, PersonalOperatorError("search_failed", str(error))
            )

    def create_file(
        self,
        target_ref: str | Path,
        content: str | bytes,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        operation = "file.create"
        before = None
        try:
            key = self._validate_idempotency_key(idempotency_key)
            if isinstance(content, str):
                payload = content.encode("utf-8")
                encoding = "utf-8"
            elif isinstance(content, bytes):
                payload = content
                encoding = None
            else:
                raise PersonalOperatorError(
                    "invalid_input", "content must be text or bytes"
                )
            if len(payload) > self.limits.max_file_bytes:
                raise PersonalOperatorError(
                    "size_limit_exceeded", "content exceeds the file byte limit"
                )
            target = self._resolve(target_ref)
            with self._lock:
                before = self._identity(target)
                desired_hash = _sha256_bytes(payload)
                ledger = self._load_ledger()
                request = {
                    "target_ref": self._relative_ref(target),
                    "desired_hash": desired_hash,
                    "encoding": encoding,
                }
                record, replay = self._operation_record(
                    ledger,
                    key,
                    operation=operation,
                    request=request,
                    before=before,
                    desired_hash=desired_hash,
                )
                current = self._identity(target)
                if replay:
                    if (
                        current["kind"] == "file"
                        and current["sha256"] == desired_hash
                    ):
                        if record.get("status") != "COMPLETED":
                            self._complete_operation(
                                ledger, key, target=current, result={"created": True}
                            )
                        return self._receipt(
                            operation,
                            request_id=key,
                            before=record["before"],
                            target_identity=current,
                            result={"created": True},
                            readback=current,
                            replayed=True,
                            reconciled=True,
                        )
                    raise PersonalOperatorError(
                        "state_drift",
                        "replayed create no longer matches its recorded output",
                        status="UNKNOWN",
                    )
                if before["exists"]:
                    ledger.pop(key, None)
                    self._write_ledger(ledger)
                    raise PersonalOperatorError(
                        "target_exists", "create refuses to overwrite an existing target"
                    )
                self._fault("file.create.before_publish")
                self._atomic_create(target, payload)
                self._fault("file.create.after_publish")
                after = self._identity(target)
                if after["sha256"] != desired_hash:
                    raise PersonalOperatorError(
                        "readback_mismatch",
                        "created file hash does not match requested content",
                        status="UNKNOWN",
                    )
                self._complete_operation(
                    ledger, key, target=after, result={"created": True}
                )
                return self._receipt(
                    operation,
                    request_id=key,
                    before=before,
                    target_identity=after,
                    result={"created": True},
                    readback=after,
                )
        except PersonalOperatorError as error:
            return self._error_receipt(
                operation, error, request_id=idempotency_key, before=before
            )
        except (OSError, InjectedOperatorFailure) as error:
            return self._error_receipt(
                operation,
                PersonalOperatorError("partial_failure", str(error), status="UNKNOWN"),
                request_id=idempotency_key,
                before=before,
            )

    def copy_file(
        self,
        source_ref: str | Path,
        target_ref: str | Path,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._copy_or_move(
            "file.copy",
            source_ref,
            target_ref,
            idempotency_key=idempotency_key,
            move=False,
        )

    def move_file(
        self,
        source_ref: str | Path,
        target_ref: str | Path,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._copy_or_move(
            "file.move",
            source_ref,
            target_ref,
            idempotency_key=idempotency_key,
            move=True,
        )

    def rename_file(
        self,
        source_ref: str | Path,
        target_ref: str | Path,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._copy_or_move(
            "file.rename",
            source_ref,
            target_ref,
            idempotency_key=idempotency_key,
            move=True,
        )

    def _copy_or_move(
        self,
        operation: str,
        source_ref: str | Path,
        target_ref: str | Path,
        *,
        idempotency_key: str,
        move: bool,
    ) -> dict[str, Any]:
        before = None
        source_before = None
        try:
            key = self._validate_idempotency_key(idempotency_key)
            source = self._resolve(source_ref)
            target = self._resolve(target_ref)
            if source == target:
                raise PersonalOperatorError(
                    "invalid_input", "source and target must be different"
                )
            with self._lock:
                source_before = self._identity(source)
                if source_before["kind"] != "file":
                    if not source_before["exists"]:
                        raise PersonalOperatorError(
                            "not_found", "source file does not exist"
                        )
                    raise PersonalOperatorError(
                        "not_a_file", "source must be a regular file"
                    )
                before = self._identity(target)
                desired_hash = str(source_before["sha256"])
                ledger = self._load_ledger()
                request = {
                    "source_ref": self._relative_ref(source),
                    "target_ref": self._relative_ref(target),
                    "source_hash": desired_hash,
                }
                record, replay = self._operation_record(
                    ledger,
                    key,
                    operation=operation,
                    request=request,
                    before=before,
                    source=source_before,
                    desired_hash=desired_hash,
                )
                current_source = self._identity(source)
                current_target = self._identity(target)
                if replay:
                    source_ok = (
                        not move
                        and current_source["kind"] == "file"
                        and current_source["sha256"] == desired_hash
                    ) or (move and not current_source["exists"])
                    target_ok = (
                        current_target["kind"] == "file"
                        and current_target["sha256"] == desired_hash
                    )
                    if source_ok and target_ok:
                        if record.get("status") != "COMPLETED":
                            self._complete_operation(
                                ledger,
                                key,
                                target=current_target,
                                source_after=current_source,
                                result={"moved": move, "copied": not move},
                            )
                        return self._receipt(
                            operation,
                            request_id=key,
                            before=record["before"],
                            source_identity=record["source"],
                            target_identity=current_target,
                            result={"moved": move, "copied": not move},
                            readback={
                                "source": current_source,
                                "target": current_target,
                            },
                            replayed=True,
                            reconciled=True,
                        )
                    raise PersonalOperatorError(
                        "state_drift",
                        "replayed file operation does not match recorded state",
                        status="UNKNOWN",
                    )
                if before["exists"]:
                    ledger.pop(key, None)
                    self._write_ledger(ledger)
                    raise PersonalOperatorError(
                        "target_exists", "operation refuses to overwrite its target"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                self._fault(f"{operation}.before_publish")
                if move:
                    os.rename(source, target)
                else:
                    self._atomic_create(target, source.read_bytes())
                self._fault(f"{operation}.after_publish")
                source_after = self._identity(source)
                target_after = self._identity(target)
                if target_after["sha256"] != desired_hash:
                    raise PersonalOperatorError(
                        "readback_mismatch",
                        "target hash differs from source hash",
                        status="UNKNOWN",
                    )
                if move and source_after["exists"]:
                    raise PersonalOperatorError(
                        "readback_mismatch",
                        "move target exists but source was not removed",
                        status="UNKNOWN",
                    )
                self._complete_operation(
                    ledger,
                    key,
                    target=target_after,
                    source_after=source_after,
                    result={"moved": move, "copied": not move},
                )
                return self._receipt(
                    operation,
                    request_id=key,
                    before=before,
                    source_identity=source_before,
                    target_identity=target_after,
                    result={"moved": move, "copied": not move},
                    readback={"source": source_after, "target": target_after},
                )
        except PersonalOperatorError as error:
            return self._error_receipt(
                operation,
                error,
                request_id=idempotency_key,
                before=before,
                source_identity=source_before,
            )
        except (OSError, InjectedOperatorFailure) as error:
            return self._error_receipt(
                operation,
                PersonalOperatorError("partial_failure", str(error), status="UNKNOWN"),
                request_id=idempotency_key,
                before=before,
                source_identity=source_before,
            )

    def safe_text_edit(
        self,
        target_ref: str | Path,
        new_text: str,
        *,
        expected_source_hash: str,
        idempotency_key: str,
        preserve_original: bool = True,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        operation = "file.safe_text_edit"
        before = None
        backup_identity = None
        try:
            key = self._validate_idempotency_key(idempotency_key)
            if not isinstance(new_text, str):
                raise PersonalOperatorError("invalid_input", "new_text must be text")
            if not isinstance(expected_source_hash, str) or not expected_source_hash.startswith(
                "sha256:"
            ):
                raise PersonalOperatorError(
                    "invalid_input", "expected_source_hash must be a sha256 identity"
                )
            if not preserve_original:
                raise PersonalOperatorError(
                    "source_preservation_required",
                    "safe text edit requires original preservation",
                )
            try:
                payload = new_text.encode(encoding)
            except (LookupError, UnicodeEncodeError) as error:
                raise PersonalOperatorError("invalid_input", str(error)) from error
            if len(payload) > self.limits.max_file_bytes:
                raise PersonalOperatorError(
                    "size_limit_exceeded", "edited content exceeds the file byte limit"
                )
            target = self._resolve(target_ref)
            desired_hash = _sha256_bytes(payload)
            backup_token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
            backup = (
                self._state_root
                / "backups"
                / backup_token
                / Path(self._relative_ref(target) + ".original")
            )
            backup_ref = self._relative_ref(backup)
            with self._lock:
                before = self._identity(target)
                if before["kind"] != "file":
                    if not before["exists"]:
                        raise PersonalOperatorError(
                            "not_found", "safe edit target does not exist"
                        )
                    raise PersonalOperatorError(
                        "not_a_file", "safe edit target must be a regular file"
                    )
                ledger = self._load_ledger()
                request = {
                    "target_ref": self._relative_ref(target),
                    "expected_source_hash": expected_source_hash,
                    "desired_hash": desired_hash,
                    "encoding": encoding,
                    "preserve_original": True,
                }
                record, replay = self._operation_record(
                    ledger,
                    key,
                    operation=operation,
                    request=request,
                    before=before,
                    desired_hash=desired_hash,
                    backup_ref=backup_ref,
                )
                current = self._identity(target)
                backup_identity = self._identity(backup)
                if replay and current["sha256"] == desired_hash:
                    if (
                        backup_identity["kind"] != "file"
                        or backup_identity["sha256"] != expected_source_hash
                    ):
                        raise PersonalOperatorError(
                            "source_preservation_drift",
                            "edited target exists but preserved original is missing or changed",
                            status="UNKNOWN",
                        )
                    if record.get("status") != "COMPLETED":
                        self._complete_operation(
                            ledger,
                            key,
                            target=current,
                            result={
                                "edited": True,
                                "backup_ref": backup_ref,
                                "backup_sha256": expected_source_hash,
                            },
                        )
                    return self._receipt(
                        operation,
                        request_id=key,
                        before=record["before"],
                        target_identity=current,
                        source_identity=backup_identity,
                        result={
                            "edited": True,
                            "backup_ref": backup_ref,
                            "backup_sha256": expected_source_hash,
                        },
                        readback={"target": current, "backup": backup_identity},
                        replayed=True,
                        reconciled=True,
                    )
                if replay and record.get("status") == "COMPLETED":
                    raise PersonalOperatorError(
                        "state_drift",
                        "completed edit no longer matches recorded target state",
                        status="UNKNOWN",
                    )
                if current["sha256"] != expected_source_hash:
                    raise PersonalOperatorError(
                        "source_hash_mismatch",
                        "target changed since the caller captured its source hash",
                    )
                original = target.read_bytes()
                if backup_identity["exists"]:
                    if backup_identity["sha256"] != expected_source_hash:
                        raise PersonalOperatorError(
                            "source_preservation_drift",
                            "existing backup does not match the expected original",
                            status="UNKNOWN",
                        )
                else:
                    self._atomic_create(backup, original)
                    backup_identity = self._identity(backup)
                self._fault("safe_edit.after_backup")
                self._atomic_replace(target, payload)
                self._fault("safe_edit.after_replace")
                after = self._identity(target)
                backup_after = self._identity(backup)
                if after["sha256"] != desired_hash:
                    raise PersonalOperatorError(
                        "readback_mismatch",
                        "edited target hash does not match requested text",
                        status="UNKNOWN",
                    )
                if backup_after["sha256"] != expected_source_hash:
                    raise PersonalOperatorError(
                        "source_preservation_drift",
                        "preserved original failed hash verification",
                        status="UNKNOWN",
                    )
                result = {
                    "edited": True,
                    "backup_ref": backup_ref,
                    "backup_sha256": expected_source_hash,
                }
                self._complete_operation(ledger, key, target=after, result=result)
                return self._receipt(
                    operation,
                    request_id=key,
                    before=before,
                    target_identity=after,
                    source_identity=backup_after,
                    result=result,
                    readback={"target": after, "backup": backup_after},
                )
        except PersonalOperatorError as error:
            return self._error_receipt(
                operation,
                error,
                request_id=idempotency_key,
                before=before,
                source_identity=backup_identity,
            )
        except (OSError, InjectedOperatorFailure) as error:
            return self._error_receipt(
                operation,
                PersonalOperatorError("partial_failure", str(error), status="UNKNOWN"),
                request_id=idempotency_key,
                before=before,
                source_identity=backup_identity,
            )

    # ------------------------------------------------------------------
    # System information and bounded commands
    # ------------------------------------------------------------------

    def system_info(self) -> dict[str, Any]:
        identity = self._identity(self.root)
        result = {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "python_executable": str(Path(sys.executable).resolve()),
            "process_id": os.getpid(),
            "owned_root": str(self.root),
        }
        return self._receipt(
            "system.info",
            before=identity,
            target_identity=identity,
            result=result,
            readback=result,
            reconciled=True,
        )

    def environment_info(self, names: Sequence[str]) -> dict[str, Any]:
        operation = "system.environment"
        try:
            if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
                raise PersonalOperatorError(
                    "invalid_input", "names must be a sequence of environment names"
                )
            requested = [str(name) for name in names]
            refused = sorted(set(requested) - self._environment_allowlist)
            if refused:
                raise PersonalOperatorError(
                    "environment_not_allowed",
                    f"environment names are not allowlisted: {', '.join(refused)}",
                )
            values = {name: os.environ.get(name) for name in requested}
            return self._receipt(
                operation,
                target_identity={"allowlisted_names": sorted(self._environment_allowlist)},
                result={"values": values},
                readback={"names_read": requested},
                reconciled=True,
            )
        except PersonalOperatorError as error:
            return self._error_receipt(operation, error)

    def inspect_processes(self, process_ids: Sequence[int]) -> dict[str, Any]:
        """Inspect an explicit bounded PID set without reading command lines."""
        operation = "system.processes"
        try:
            if (
                isinstance(process_ids, (str, bytes))
                or not isinstance(process_ids, Sequence)
                or len(process_ids) > self.limits.max_inspect_items
            ):
                raise PersonalOperatorError(
                    "invalid_input",
                    "process_ids must be a bounded sequence of positive integers",
                )
            normalized: list[int] = []
            for raw_pid in process_ids:
                if (
                    not isinstance(raw_pid, int)
                    or isinstance(raw_pid, bool)
                    or raw_pid <= 0
                ):
                    raise PersonalOperatorError(
                        "invalid_input", "process_ids must contain positive integers"
                    )
                if raw_pid not in normalized:
                    normalized.append(raw_pid)
            observed = []
            for pid in normalized:
                identity = process_creation_identity(pid)
                observed.append({
                    "pid": pid,
                    "alive": pid_alive(pid),
                    "create_time": identity.get("create_time"),
                    "exe": identity.get("exe"),
                })
            return self._receipt(
                operation,
                target_identity={"process_ids": normalized},
                result={"processes": observed},
                readback={"processes": observed},
                reconciled=True,
            )
        except PersonalOperatorError as error:
            return self._error_receipt(operation, error)

    def inspect_applications(
        self,
        applications: Sequence[str | Path],
    ) -> dict[str, Any]:
        """Inspect exact allow-listed application files without PATH lookup."""
        operation = "system.applications"
        try:
            if (
                isinstance(applications, (str, bytes, Path))
                or not isinstance(applications, Sequence)
                or len(applications) > self.limits.max_inspect_items
            ):
                raise PersonalOperatorError(
                    "invalid_input",
                    "applications must be a bounded sequence of exact paths",
                )
            observed: list[dict[str, Any]] = []
            seen: set[str] = set()
            for application in applications:
                resolved = self._resolve_executable(application)
                key = self._path_key(resolved)
                if key in seen:
                    continue
                seen.add(key)
                stat = resolved.stat()
                observed.append({
                    "path": str(resolved),
                    "size_bytes": stat.st_size,
                    "modified_ns": stat.st_mtime_ns,
                })
            return self._receipt(
                operation,
                target_identity={"applications": observed},
                result={"applications": observed},
                readback={"applications": observed},
                reconciled=True,
            )
        except (OSError, PersonalOperatorError) as error:
            if isinstance(error, PersonalOperatorError):
                classified = error
            else:
                classified = PersonalOperatorError(
                    "application_inspect_failed", str(error)
                )
            return self._error_receipt(operation, classified)

    def _resolve_executable(
        self,
        executable: str | Path,
        *,
        require_allowed: bool = True,
    ) -> Path:
        if not isinstance(executable, (str, Path)):
            raise PersonalOperatorError(
                "invalid_input", "executable must be an exact absolute path"
            )
        path = Path(executable).expanduser()
        if not path.is_absolute():
            raise PersonalOperatorError(
                "executable_not_allowed", "PATH lookup and relative executables are refused"
            )
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise PersonalOperatorError("executable_not_found", str(error)) from error
        if not resolved.is_file():
            raise PersonalOperatorError(
                "executable_not_found", "executable path is not a file"
            )
        if require_allowed and self._path_key(resolved) not in self._executables:
            raise PersonalOperatorError(
                "executable_not_allowed", "executable is not in the exact allowlist"
            )
        return resolved

    def _validate_command(
        self,
        executable: str | Path,
        args: Sequence[str],
        cwd: str | Path,
    ) -> tuple[Path, list[str], Path]:
        resolved_executable = self._resolve_executable(executable)
        if isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
            raise PersonalOperatorError(
                "invalid_input", "args must be a sequence of strings"
            )
        if len(args) > self.limits.max_command_args:
            raise PersonalOperatorError(
                "argument_limit_exceeded", "command has too many arguments"
            )
        normalized_args: list[str] = []
        for value in args:
            if not isinstance(value, str) or "\x00" in value:
                raise PersonalOperatorError(
                    "invalid_input", "every command argument must be NUL-free text"
                )
            normalized_args.append(value)
        resolved_cwd = self._resolve(cwd, allow_root=True)
        cwd_identity = self._identity(resolved_cwd)
        if cwd_identity["kind"] != "directory":
            raise PersonalOperatorError(
                "not_a_directory", "command cwd must be an owned directory"
            )
        return resolved_executable, normalized_args, resolved_cwd

    def run_command(
        self,
        executable: str | Path,
        args: Sequence[str] = (),
        *,
        cwd: str | Path = ".",
        timeout_seconds: float | None = None,
        output_limit_bytes: int | None = None,
    ) -> dict[str, Any]:
        operation = "system.command"
        process: subprocess.Popen[bytes] | None = None
        stdout_path: Path | None = None
        stderr_path: Path | None = None
        before = None
        started = time.monotonic()
        try:
            resolved, normalized_args, resolved_cwd = self._validate_command(
                executable, args, cwd
            )
            timeout = (
                self.limits.max_command_seconds
                if timeout_seconds is None
                else float(timeout_seconds)
            )
            output_limit = (
                self.limits.max_command_output_bytes
                if output_limit_bytes is None
                else int(output_limit_bytes)
            )
            if timeout <= 0 or timeout > self.limits.max_command_seconds:
                raise PersonalOperatorError(
                    "invalid_input",
                    f"timeout must be in (0, {self.limits.max_command_seconds}]",
                )
            if output_limit <= 0 or output_limit > self.limits.max_command_output_bytes:
                raise PersonalOperatorError(
                    "invalid_input",
                    f"output limit must be in (0, {self.limits.max_command_output_bytes}]",
                )
            before = self._identity(resolved_cwd)
            command = [str(resolved), *normalized_args]
            command_identity = _canonical_hash(
                {
                    "executable": str(resolved),
                    "args": normalized_args,
                    "cwd": str(resolved_cwd),
                }
            )
            tmp_root = self._state_root / "command-output"
            tmp_root.mkdir(parents=True, exist_ok=True)
            stdout_fd, stdout_name = tempfile.mkstemp(prefix="stdout-", dir=tmp_root)
            stderr_fd, stderr_name = tempfile.mkstemp(prefix="stderr-", dir=tmp_root)
            stdout_path = Path(stdout_name)
            stderr_path = Path(stderr_name)
            limit_exceeded = False
            timed_out = False
            with os.fdopen(stdout_fd, "wb") as stdout_handle, os.fdopen(
                stderr_fd, "wb"
            ) as stderr_handle:
                process = subprocess.Popen(
                    command,
                    cwd=resolved_cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=sanitized_env(),
                    shell=False,
                    **headless_popen_kwargs(),
                )
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        _terminate_popen_exact(
                            process,
                            grace_seconds=0.5,
                        )
                        break
                    stdout_handle.flush()
                    stderr_handle.flush()
                    size = stdout_path.stat().st_size + stderr_path.stat().st_size
                    if size > output_limit:
                        limit_exceeded = True
                        _terminate_popen_exact(
                            process,
                            grace_seconds=0.5,
                        )
                        break
                    time.sleep(0.02)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _terminate_popen_exact(
                        process,
                        grace_seconds=0.1,
                    )
                stdout_handle.flush()
                stderr_handle.flush()
            stdout_raw = stdout_path.read_bytes()
            stderr_raw = stderr_path.read_bytes()
            combined_size = len(stdout_raw) + len(stderr_raw)
            available = output_limit
            stdout_limited = stdout_raw[:available]
            available -= len(stdout_limited)
            stderr_limited = stderr_raw[:available]
            duration = round(time.monotonic() - started, 6)
            result = {
                "command_identity": command_identity,
                "executable": str(resolved),
                "args": normalized_args,
                "cwd": str(resolved_cwd),
                "pid": process.pid,
                "exit_code": process.returncode,
                "stdout": stdout_limited.decode("utf-8", errors="replace"),
                "stderr": stderr_limited.decode("utf-8", errors="replace"),
                "captured_output_bytes": len(stdout_limited) + len(stderr_limited),
                "observed_output_bytes": combined_size,
                "output_truncated": combined_size > output_limit,
                "duration_seconds": duration,
            }
            readback = {
                "alive": pid_alive(process.pid),
                "exit_code": process.returncode,
                "process_tree": list_process_tree(process.pid),
            }
            if timed_out:
                raise PersonalOperatorError(
                    "command_timeout",
                    f"command exceeded {timeout} seconds",
                )
            if limit_exceeded or combined_size > output_limit:
                raise PersonalOperatorError(
                    "output_limit_exceeded",
                    f"command output exceeded {output_limit} bytes",
                )
            if process.returncode != 0:
                return self._receipt(
                    operation,
                    status="FAILED",
                    before=before,
                    target_identity={"executable": str(resolved), "cwd": str(resolved_cwd)},
                    result=result,
                    readback=readback,
                    classification="command_failed",
                    message=f"command exited with code {process.returncode}",
                )
            return self._receipt(
                operation,
                before=before,
                target_identity={"executable": str(resolved), "cwd": str(resolved_cwd)},
                result=result,
                readback=readback,
            )
        except PersonalOperatorError as error:
            result = None
            readback = None
            if process is not None:
                result = {
                    "pid": process.pid,
                    "exit_code": process.poll(),
                    "duration_seconds": round(time.monotonic() - started, 6),
                }
                readback = {"alive": pid_alive(process.pid)}
            return self._error_receipt(
                operation,
                error,
                before=before,
                result=result,
                readback=readback,
            )
        except OSError as error:
            if process is not None and pid_alive(process.pid):
                _terminate_popen_exact(
                    process,
                    grace_seconds=0.1,
                )
            return self._error_receipt(
                operation,
                PersonalOperatorError("command_launch_failed", str(error)),
                before=before,
            )
        finally:
            for path in (stdout_path, stderr_path):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    # Exact owned process and small desktop verbs
    # ------------------------------------------------------------------

    @staticmethod
    def _same_executable(expected: str | None, observed: str | None) -> bool:
        return _same_executable_path(expected, observed)

    def _record_matches_process(
        self, record: Mapping[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        pid = int(record.get("pid", 0))
        observed = process_creation_identity(pid)
        alive = pid_alive(pid)
        expected = record.get("creation_identity")
        if not isinstance(expected, Mapping):
            return False, {"alive": alive, "observed_identity": observed}
        matches = bool(
            alive
            and expected.get("create_time") is not None
            and observed.get("create_time") == expected.get("create_time")
            and self._same_executable(
                str(expected.get("exe") or record.get("executable")),
                str(observed.get("exe") or ""),
            )
        )
        return matches, {"alive": alive, "observed_identity": observed}

    def launch_process(
        self,
        process_id: str,
        executable: str | Path,
        args: Sequence[str] = (),
        *,
        cwd: str | Path = ".",
        idempotency_key: str,
        show_window: bool = False,
    ) -> dict[str, Any]:
        return self._launch_process(
            "process.launch",
            process_id,
            executable,
            args,
            cwd=cwd,
            idempotency_key=idempotency_key,
            show_window=show_window,
            opened_identity=None,
        )

    def bind_new_window(
        self,
        process_id: str,
        *,
        title_contains: str,
        before: Sequence[VisibleWindow],
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        """Rebind a launched GUI process onto a newly appeared window owner."""

        logical_id = self._validate_process_id(process_id)
        with self._lock:
            record = self._load_process_registry().get(logical_id)
        if record is None:
            return self._error_receipt(
                "desktop.focus",
                PersonalOperatorError(
                    "process_not_registered",
                    "process_id is not registry-owned",
                ),
            )
        receipt = {
            "target_identity": record,
            "request_id": record.get("idempotency_key") or "",
            "status": "COMPLETED",
        }
        return self._bind_window_owning_process(
            logical_id,
            title_contains=title_contains,
            before=before,
            launch_receipt=receipt,
            timeout_s=timeout_s,
        )

    def desktop_open(
        self,
        process_id: str,
        application: str | Path,
        target_ref: str | Path,
        *,
        args: Sequence[str] = (),
        cwd: str | Path = ".",
        idempotency_key: str,
        show_window: bool = True,
    ) -> dict[str, Any]:
        operation = "desktop.open"
        try:
            target = self._resolve(target_ref, allow_root=True)
            target_identity = self._identity(target)
            if not target_identity["exists"]:
                raise PersonalOperatorError(
                    "not_found", "desktop open target does not exist"
                )
            normalized_args = list(args)
            normalized_args.append(str(target.resolve()))
            snapshot = _visible_application_windows()
            receipt = self._launch_process(
                operation,
                process_id,
                application,
                normalized_args,
                cwd=cwd,
                idempotency_key=idempotency_key,
                show_window=show_window,
                opened_identity=target_identity,
            )
            if (
                receipt.get("status") == "COMPLETED"
                and not receipt.get("replayed")
                and target_identity.get("kind") == "file"
                and show_window
            ):
                return self._bind_window_owning_process(
                    process_id,
                    title_contains=Path(str(target.resolve())).stem,
                    before=snapshot,
                    launch_receipt=receipt,
                    timeout_s=30.0,
                )
            return receipt
        except PersonalOperatorError as error:
            return self._error_receipt(
                operation, error, request_id=idempotency_key
            )

    def _bind_window_owning_process(
        self,
        process_id: str,
        *,
        title_contains: str,
        before: Sequence[VisibleWindow],
        launch_receipt: Mapping[str, Any],
        timeout_s: float,
    ) -> dict[str, Any]:
        """Rebind a launch record onto the new window-owning process.

        Windows App Execution Alias and single-instance apps often leave the
        CreateProcess PID windowless. A pre-existing instance that merely
        changes title is refused rather than adopted.
        """

        logical_id = self._validate_process_id(process_id)
        deadline = time.monotonic() + float(timeout_s)
        last_status = "missing"
        last_window: VisibleWindow | None = None
        before_keys = {window.key() for window in before}
        launched_record = launch_receipt.get("target_identity") or {}
        launched_exe = str(
            ((launched_record.get("creation_identity") or {}) if isinstance(launched_record, Mapping) else {}).get("exe")
            or (launched_record.get("executable") if isinstance(launched_record, Mapping) else "")
            or ""
        )
        while time.monotonic() < deadline:
            after = _visible_application_windows()
            last_status, last_window = resolve_launch_window_owner(
                before,
                after,
                title_contains=title_contains,
            )
            if last_status == "missing":
                fresh = [
                    window
                    for window in after
                    if window.key() not in before_keys
                    and not is_helper_window_class(window.class_name)
                ]
                matched: list[VisibleWindow] = []
                for window in fresh:
                    identity = process_creation_identity(window.pid)
                    exe = str(identity.get("exe") or "")
                    if exe and launched_exe and self._same_executable(launched_exe, exe):
                        matched.append(window)
                if len(matched) == 1:
                    last_status, last_window = "bound", matched[0]
            if last_status != "missing":
                break
            time.sleep(0.05)
        if last_status == "missing" or last_window is None:
            self._release_unbound_launch(
                logical_id, reason="window_not_found"
            )
            return self._error_receipt(
                "desktop.open",
                PersonalOperatorError(
                    "window_not_found",
                    "launched process did not present a new window for the artifact",
                    status="UNKNOWN",
                ),
                request_id=str(launch_receipt.get("request_id") or ""),
            )
        if last_status == "ambiguous":
            self._release_unbound_launch(
                logical_id, reason="window_ambiguous"
            )
            return self._error_receipt(
                "desktop.open",
                PersonalOperatorError(
                    "window_ambiguous",
                    "multiple new windows matched the opened artifact title",
                    status="UNKNOWN",
                ),
                request_id=str(launch_receipt.get("request_id") or ""),
            )
        if last_status == "reused":
            self._release_unbound_launch(
                logical_id, reason="existing_instance_reused"
            )
            return self._error_receipt(
                "desktop.open",
                PersonalOperatorError(
                    "existing_instance_reused",
                    "opened artifact reused a pre-existing application instance",
                ),
                request_id=str(launch_receipt.get("request_id") or ""),
            )
        identity = process_creation_identity(last_window.pid)
        if identity.get("create_time") is None or not identity.get("exe"):
            return self._error_receipt(
                "desktop.open",
                PersonalOperatorError(
                    "process_identity_unavailable",
                    "window-owning process could not be bound to a complete identity",
                    status="UNKNOWN",
                ),
                request_id=str(launch_receipt.get("request_id") or ""),
            )
        with self._lock:
            registry = self._load_process_registry()
            record = registry.get(logical_id)
            if record is None:
                raise PersonalOperatorError(
                    "process_not_registered",
                    "process_id is not registry-owned",
                )
            stub_pid = int(record["pid"])
            stub_identity = dict(record.get("creation_identity") or {})
            if last_window.pid != stub_pid:
                record["stub_pid"] = stub_pid
                record["pid"] = int(last_window.pid)
                record["creation_identity"] = identity
                descendants = list(record.get("owned_descendants") or [])
                if stub_pid not in descendants:
                    descendants.append(stub_pid)
                record["owned_descendants"] = descendants
                record["window_hwnd"] = last_window.hwnd
                record["window_title"] = last_window.title
                registry[logical_id] = record
                self._write_process_registry(registry)
                self._processes.pop(logical_id, None)
                if stub_identity.get("create_time") is not None:
                    _terminate_windows_exact(
                        {
                            "pid": stub_pid,
                            "creation_identity": stub_identity,
                        },
                        grace_seconds=0.2,
                    )
            else:
                record["window_hwnd"] = last_window.hwnd
                record["window_title"] = last_window.title
                registry[logical_id] = record
                self._write_process_registry(registry)
            matches, readback = self._record_matches_process(record)
            if not matches:
                raise PersonalOperatorError(
                    "process_identity_mismatch",
                    "window-owning process identity changed before readback",
                    status="UNKNOWN",
                )
            merged = dict(launch_receipt)
            merged["target_identity"] = _json_copy(record)
            result = dict(merged.get("result") or {})
            result["pid"] = record["pid"]
            result["window_hwnd"] = last_window.hwnd
            merged["result"] = result
            merged["readback"] = readback
            merged["reconciled"] = True
            return merged

    def _release_unbound_launch(
        self, process_id: str, *, reason: str
    ) -> None:
        """Terminate only the launch stub. Never touch a reused instance."""

        process = self._processes.pop(process_id, None)
        if process is not None:
            _terminate_popen_exact(process, grace_seconds=0.5)
        with self._lock:
            registry = self._load_process_registry()
            record = registry.get(process_id)
            if record is None:
                return
            _terminate_windows_exact(record, grace_seconds=0.5)
            record["state"] = "exited"
            record["close_reason"] = reason
            record["reconciled_at"] = _utc_now()
            registry[process_id] = record
            self._write_process_registry(registry)

    def _launch_process(
        self,
        operation: str,
        process_id: str,
        executable: str | Path,
        args: Sequence[str],
        *,
        cwd: str | Path,
        idempotency_key: str,
        show_window: bool,
        opened_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        process: subprocess.Popen[bytes] | None = None
        try:
            key = self._validate_idempotency_key(idempotency_key)
            logical_id = self._validate_process_id(process_id)
            resolved, normalized_args, resolved_cwd = self._validate_command(
                executable, args, cwd
            )
            command_identity = _canonical_hash(
                {
                    "executable": str(resolved),
                    "args": normalized_args,
                    "cwd": str(resolved_cwd),
                    "show_window": bool(show_window),
                    "opened_ref": opened_identity.get("ref") if opened_identity else None,
                }
            )
            request_fingerprint = _canonical_hash(
                {
                    "operation": operation,
                    "process_id": logical_id,
                    "idempotency_key": key,
                    "command_identity": command_identity,
                }
            )
            with self._lock:
                registry = self._load_process_registry()
                for existing_id, existing in registry.items():
                    if (
                        existing_id != logical_id
                        and existing.get("idempotency_key") == key
                    ):
                        raise PersonalOperatorError(
                            "idempotency_conflict",
                            "launch idempotency key belongs to another process",
                        )
                existing = registry.get(logical_id)
                if existing is not None:
                    if existing.get("request_fingerprint") != request_fingerprint:
                        raise PersonalOperatorError(
                            "process_id_conflict",
                            "process_id is already bound to another command identity",
                        )
                    matches, readback = self._record_matches_process(existing)
                    if matches:
                        return self._receipt(
                            operation,
                            request_id=key,
                            before=existing.get("before"),
                            target_identity=existing,
                            source_identity=opened_identity,
                            result={
                                "process_id": logical_id,
                                "pid": existing["pid"],
                                "state": "running",
                                "command_identity": command_identity,
                            },
                            readback=readback,
                            replayed=True,
                            reconciled=True,
                        )
                    if not readback["alive"]:
                        existing["state"] = "exited"
                        existing["reconciled_at"] = _utc_now()
                        registry[logical_id] = existing
                        self._write_process_registry(registry)
                        return self._receipt(
                            operation,
                            request_id=key,
                            before=existing.get("before"),
                            target_identity=existing,
                            source_identity=opened_identity,
                            result={
                                "process_id": logical_id,
                                "pid": existing["pid"],
                                "state": "exited",
                                "command_identity": command_identity,
                            },
                            readback=readback,
                            replayed=True,
                            reconciled=True,
                        )
                    raise PersonalOperatorError(
                        "process_identity_mismatch",
                        "PID is alive but no longer matches the recorded process",
                        status="UNKNOWN",
                    )

                command = [str(resolved), *normalized_args]
                kwargs: dict[str, Any] = {
                    "cwd": resolved_cwd,
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                    "env": sanitized_env(),
                    "shell": False,
                }
                if not show_window:
                    kwargs.update(headless_popen_kwargs())
                process = subprocess.Popen(command, **kwargs)
                identity: dict[str, Any] = {}
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    identity = process_creation_identity(process.pid)
                    if identity.get("create_time") is not None and identity.get("exe"):
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.02)
                if identity.get("create_time") is None or not identity.get("exe"):
                    _terminate_popen_exact(
                        process,
                        grace_seconds=0.1,
                    )
                    raise PersonalOperatorError(
                        "process_identity_unavailable",
                        "launched process could not be bound to a complete identity",
                        status="UNKNOWN",
                    )
                record = {
                    "process_id": logical_id,
                    "pid": process.pid,
                    "creation_identity": identity,
                    "executable": str(resolved),
                    "args": normalized_args,
                    "cwd": str(resolved_cwd),
                    "command_identity": command_identity,
                    "request_fingerprint": request_fingerprint,
                    "idempotency_key": key,
                    "operation": operation,
                    "show_window": bool(show_window),
                    "opened_identity": dict(opened_identity) if opened_identity else None,
                    "owned_descendants": list_process_tree(process.pid),
                    "state": "running",
                    "before": {"registered": False, "process_id": logical_id},
                    "launched_at": _utc_now(),
                }
                registry[logical_id] = record
                try:
                    self._write_process_registry(registry)
                except BaseException:
                    _terminate_popen_exact(
                        process,
                        grace_seconds=0.1,
                    )
                    raise
                self._processes[logical_id] = process
                matches, readback = self._record_matches_process(record)
                if not matches:
                    _terminate_popen_exact(
                        process,
                        grace_seconds=0.1,
                    )
                    raise PersonalOperatorError(
                        "process_identity_mismatch",
                        "process identity changed before launch readback",
                        status="UNKNOWN",
                    )
                return self._receipt(
                    operation,
                    request_id=key,
                    before=record["before"],
                    target_identity=record,
                    source_identity=opened_identity,
                    result={
                        "process_id": logical_id,
                        "pid": process.pid,
                        "state": "running",
                        "command_identity": command_identity,
                    },
                    readback=readback,
                )
        except PersonalOperatorError as error:
            return self._error_receipt(
                operation, error, request_id=idempotency_key
            )
        except OSError as error:
            if process is not None and pid_alive(process.pid):
                _terminate_popen_exact(
                    process,
                    grace_seconds=0.1,
                )
            return self._error_receipt(
                operation,
                PersonalOperatorError("process_launch_failed", str(error)),
                request_id=idempotency_key,
            )

    def inspect_process(self, process_id: str) -> dict[str, Any]:
        operation = "process.inspect"
        try:
            logical_id = self._validate_process_id(process_id)
            with self._lock:
                registry = self._load_process_registry()
                record = registry.get(logical_id)
                if record is None:
                    raise PersonalOperatorError(
                        "process_not_registered", "process_id is not registry-owned"
                    )
                matches, readback = self._record_matches_process(record)
                if matches:
                    state = "running"
                    status = "COMPLETED"
                    classification = None
                elif not readback["alive"]:
                    state = "exited"
                    status = "COMPLETED"
                    classification = None
                else:
                    state = "identity_mismatch"
                    status = "UNKNOWN"
                    classification = "process_identity_mismatch"
                return self._receipt(
                    operation,
                    status=status,
                    before=record,
                    target_identity=record,
                    result={"process_id": logical_id, "state": state, "matches": matches},
                    readback=readback,
                    classification=classification,
                    message=(
                        "PID is alive but does not match the durable registry identity"
                        if classification
                        else None
                    ),
                    reconciled=True,
                )
        except PersonalOperatorError as error:
            return self._error_receipt(operation, error)

    def get_validated_process_record(
        self,
        process_id: str,
    ) -> dict[str, Any]:
        """Return one live registry-owned process after exact identity readback."""

        logical_id = self._validate_process_id(process_id)
        with self._lock:
            registry = self._load_process_registry()
            record = registry.get(logical_id)
            if record is None:
                raise PersonalOperatorError(
                    "process_not_registered",
                    "process_id is not registry-owned",
                )
            matches, readback = self._record_matches_process(record)
            if not matches:
                classification = (
                    "process_exited"
                    if not readback.get("alive")
                    else "process_identity_mismatch"
                )
                raise PersonalOperatorError(
                    classification,
                    "process record no longer matches the live exact identity",
                    status="UNKNOWN" if readback.get("alive") else "FAILED",
                )
            return _json_copy(record)

    def reconcile_processes(self) -> dict[str, Any]:
        operation = "process.reconcile"
        with self._lock:
            try:
                registry = self._load_process_registry()
                results = [
                    self.inspect_process(process_id) for process_id in sorted(registry)
                ]
                return self._receipt(
                    operation,
                    target_identity={"registry": str(self._process_registry_path)},
                    result={"processes": results},
                    readback={"count": len(results)},
                    reconciled=True,
                )
            except PersonalOperatorError as error:
                return self._error_receipt(operation, error)

    def close_process(
        self,
        process_id: str,
        *,
        idempotency_key: str,
        grace_seconds: float = 2.0,
    ) -> dict[str, Any]:
        operation = "process.close"
        try:
            key = self._validate_idempotency_key(idempotency_key)
            logical_id = self._validate_process_id(process_id)
            if grace_seconds <= 0 or grace_seconds > 10:
                raise PersonalOperatorError(
                    "invalid_input", "grace_seconds must be in (0, 10]"
                )
            with self._lock:
                registry = self._load_process_registry()
                record = registry.get(logical_id)
                if record is None:
                    raise PersonalOperatorError(
                        "process_not_registered", "process_id is not registry-owned"
                    )
                prior_close_key = record.get("close_idempotency_key")
                if prior_close_key is not None and prior_close_key != key:
                    raise PersonalOperatorError(
                        "idempotency_conflict",
                        "process close was already bound to another idempotency key",
                    )
                before_record = _json_copy(record)
                matches, before_readback = self._record_matches_process(record)
                if not matches:
                    if before_readback["alive"]:
                        raise PersonalOperatorError(
                            "process_identity_mismatch",
                            "refusing to terminate a PID with changed identity",
                            status="UNKNOWN",
                        )
                    record.update(
                        {
                            "state": "exited",
                            "close_idempotency_key": key,
                            "closed_at": record.get("closed_at") or _utc_now(),
                        }
                    )
                    registry[logical_id] = record
                    self._write_process_registry(registry)
                    return self._receipt(
                        operation,
                        request_id=key,
                        before=record,
                        target_identity=record,
                        result={"process_id": logical_id, "state": "exited"},
                        readback=before_readback,
                        replayed=prior_close_key == key,
                        reconciled=True,
                    )

                record["close_idempotency_key"] = key
                record["state"] = "closing"
                record["close_started_at"] = _utc_now()
                registry[logical_id] = record
                self._write_process_registry(registry)
                process = self._processes.get(logical_id)
                if process is not None:
                    terminated, termination_readback = _terminate_popen_exact(
                        process,
                        grace_seconds=grace_seconds,
                    )
                    termination_error = (
                        None
                        if terminated
                        else "retained process handle remained live"
                    )
                else:
                    (
                        terminated,
                        termination_readback,
                        termination_error,
                    ) = _terminate_windows_exact(
                        record,
                        grace_seconds=grace_seconds,
                    )
                record.update(
                    {
                        "state": "exited" if terminated else "termination_failed",
                        "owned_descendants": [int(record["pid"])],
                        "closed_at": _utc_now(),
                    }
                )
                registry[logical_id] = record
                self._write_process_registry(registry)
                if terminated:
                    self._processes.pop(logical_id, None)
                readback = {
                    "alive": not terminated,
                    "observed_identity": termination_readback,
                    "targeted_pids": [int(record["pid"])],
                    "termination_via": termination_readback.get("via"),
                }
                if not terminated:
                    classification = (
                        "process_identity_mismatch"
                        if termination_error
                        and "identity changed" in termination_error
                        else "termination_failed"
                    )
                    raise PersonalOperatorError(
                        classification,
                        termination_error
                        or "owned process remained live after bounded termination",
                        status="UNKNOWN",
                    )
                return self._receipt(
                    operation,
                    request_id=key,
                    before=before_record,
                    target_identity=record,
                    result={"process_id": logical_id, "state": "exited"},
                    readback=readback,
                )
        except PersonalOperatorError as error:
            return self._error_receipt(
                operation, error, request_id=idempotency_key
            )
        except OSError as error:
            return self._error_receipt(
                operation,
                PersonalOperatorError("termination_failed", str(error), status="UNKNOWN"),
                request_id=idempotency_key,
            )

    def focus_process(self, process_id: str) -> dict[str, Any]:
        operation = "desktop.focus"
        try:
            logical_id = self._validate_process_id(process_id)
            if os.name != "nt":
                raise PersonalOperatorError(
                    "unsupported_platform", "window focus is available only on Windows"
                )
            with self._lock:
                registry = self._load_process_registry()
                record = registry.get(logical_id)
                if record is None:
                    raise PersonalOperatorError(
                        "process_not_registered", "process_id is not registry-owned"
                    )
                matches, process_readback = self._record_matches_process(record)
                if not matches:
                    classification = (
                        "process_identity_mismatch"
                        if process_readback["alive"]
                        else "process_exited"
                    )
                    raise PersonalOperatorError(
                        classification,
                        "focus requires a live exact registry-owned process",
                    )
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.GetWindowThreadProcessId.argtypes = [
                    wintypes.HWND,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                user32.GetWindowThreadProcessId.restype = wintypes.DWORD
                user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                user32.ShowWindow.restype = wintypes.BOOL
                user32.SetForegroundWindow.argtypes = [wintypes.HWND]
                user32.SetForegroundWindow.restype = wintypes.BOOL
                user32.BringWindowToTop.argtypes = [wintypes.HWND]
                user32.BringWindowToTop.restype = wintypes.BOOL
                user32.GetForegroundWindow.argtypes = []
                user32.GetForegroundWindow.restype = wintypes.HWND
                user32.AttachThreadInput.argtypes = [
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.BOOL,
                ]
                user32.AttachThreadInput.restype = wintypes.BOOL
                user32.SetActiveWindow.argtypes = [wintypes.HWND]
                user32.SetActiveWindow.restype = wintypes.HWND
                user32.SetFocus.argtypes = [wintypes.HWND]
                user32.SetFocus.restype = wintypes.HWND
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.GetCurrentThreadId.argtypes = []
                kernel32.GetCurrentThreadId.restype = wintypes.DWORD

                hwnd = _unique_owned_window(int(record["pid"])).hwnd
                foreground_before = int(user32.GetForegroundWindow() or 0)
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                api_focused = bool(user32.SetForegroundWindow(hwnd))
                foreground_after = int(user32.GetForegroundWindow() or 0)
                brought_to_top = False
                attached_threads: list[int] = []
                current_thread = int(kernel32.GetCurrentThreadId())
                foreground_thread = int(
                    user32.GetWindowThreadProcessId(
                        wintypes.HWND(foreground_before), None
                    )
                    if foreground_before
                    else 0
                )
                target_thread = int(
                    user32.GetWindowThreadProcessId(
                        wintypes.HWND(hwnd), None
                    )
                )
                if foreground_after != hwnd:
                    for thread_id in (foreground_thread, target_thread):
                        if (
                            thread_id
                            and thread_id != current_thread
                            and thread_id not in attached_threads
                            and user32.AttachThreadInput(
                                current_thread, thread_id, True
                            )
                        ):
                            attached_threads.append(thread_id)
                    try:
                        brought_to_top = bool(user32.BringWindowToTop(hwnd))
                        user32.SetActiveWindow(hwnd)
                        user32.SetFocus(hwnd)
                        api_focused = bool(
                            user32.SetForegroundWindow(hwnd)
                        ) or api_focused
                        time.sleep(0.05)
                        foreground_after = int(
                            user32.GetForegroundWindow() or 0
                        )
                    finally:
                        for thread_id in reversed(attached_threads):
                            user32.AttachThreadInput(
                                current_thread, thread_id, False
                            )
                focused = foreground_after == hwnd
                readback = {
                    **process_readback,
                    "window_handle": hwnd,
                    "visible_window_count": 1,
                    "foreground_before": foreground_before,
                    "foreground_window": foreground_after,
                    "current_thread": current_thread,
                    "foreground_thread": foreground_thread,
                    "target_thread": target_thread,
                    "attached_threads": attached_threads,
                    "set_foreground_returned": api_focused,
                    "brought_to_top": brought_to_top,
                }
                if not focused:
                    return self._receipt(
                        operation,
                        status="FAILED",
                        before=record,
                        target_identity=record,
                        result={
                            "process_id": logical_id,
                            "focused": False,
                        },
                        readback=readback,
                        classification="focus_refused",
                        message="Windows refused the foreground-window request",
                    )
                return self._receipt(
                    operation,
                    before=record,
                    target_identity=record,
                    result={"process_id": logical_id, "focused": True},
                    readback=readback,
                )
        except PersonalOperatorError as error:
            return self._error_receipt(operation, error)

    def observe_process_window(self, process_id: str) -> dict[str, Any]:
        """Read one owned process window and foreground state without mutation."""

        operation = "desktop.window.observe"
        try:
            logical_id = self._validate_process_id(process_id)
            if os.name != "nt":
                raise PersonalOperatorError(
                    "unsupported_platform",
                    "window observation is available only on Windows",
                )
            with self._lock:
                registry = self._load_process_registry()
                record = registry.get(logical_id)
                if record is None:
                    raise PersonalOperatorError(
                        "process_not_registered",
                        "process_id is not registry-owned",
                    )
                matches, process_readback = self._record_matches_process(record)
                if not matches:
                    classification = (
                        "process_identity_mismatch"
                        if process_readback["alive"]
                        else "process_exited"
                    )
                    raise PersonalOperatorError(
                        classification,
                        "window observation requires a live exact registry-owned process",
                    )

                import ctypes
                from ctypes import wintypes

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.GetForegroundWindow.argtypes = []
                user32.GetForegroundWindow.restype = wintypes.HWND
                hwnd = _unique_owned_window(int(record["pid"])).hwnd
                foreground = int(user32.GetForegroundWindow() or 0)
                readback = {
                    **process_readback,
                    "window_handle": hwnd,
                    "visible_window_count": 1,
                    "foreground_window": foreground,
                }
                return self._receipt(
                    operation,
                    before=record,
                    target_identity=record,
                    result={
                        "process_id": logical_id,
                        "window_handle": hwnd,
                        "focused": foreground == hwnd,
                    },
                    readback=readback,
                    reconciled=True,
                )
        except PersonalOperatorError as error:
            return self._error_receipt(operation, error)


__all__ = [
    "InjectedOperatorFailure",
    "PersonalOperatorConnector",
    "PersonalOperatorError",
    "PersonalOperatorLimits",
    "RECEIPT_SCHEMA",
]
