"""Bounded local file transformations for the General Work engine.

The connector owns no planning, scheduling, concurrency, or effect state.
Those authorities remain in the Foundry database and Fabric effect journal.
Each mutation is hash-guarded, root-confined, read back, and fail-closed.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RECEIPT_SCHEMA = "jarvis.general_work.file_receipt.v1"
OPERATION_INTENT_SCHEMA = "jarvis.general_work.file_operation_intent.v1"
STATE_DIRECTORY = ".jarvis-general-work"
_SHA256 = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
_TRANSFER_ACTIONS = frozenset({"file.copy", "file.move", "file.rename"})


class GeneralWorkFileError(ValueError):
    """A bounded file request was refused with a stable classification."""

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


class InjectedGeneralWorkFileFailure(RuntimeError):
    """Test-only fault raised by a caller-provided fault injector."""


@dataclass(frozen=True, slots=True)
class GeneralWorkFileLimits:
    max_file_bytes: int = 16 * 1024 * 1024
    max_search_files: int = 1_000
    max_search_results: int = 50
    max_search_file_bytes: int = 1 * 1024 * 1024
    max_preview_bytes: int = 65_536

    def __post_init__(self) -> None:
        if any(
            value <= 0
            for value in (
                self.max_file_bytes,
                self.max_search_files,
                self.max_search_results,
                self.max_search_file_bytes,
                self.max_preview_bytes,
            )
        ):
            raise ValueError("all General Work file limits must be positive")


def _sha256_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _normalize_hash(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise GeneralWorkFileError(
            "invalid_input", f"{field_name} must be a SHA-256 identity"
        )
    match = _SHA256.fullmatch(value.strip())
    if match is None:
        raise GeneralWorkFileError(
            "invalid_input", f"{field_name} must be a SHA-256 identity"
        )
    return f"sha256:{match.group(1).lower()}"


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True))


class GeneralWorkFileConnector:
    """Root-confined file inspection and hash-guarded transformation."""

    def __init__(
        self,
        root: str | Path,
        *,
        limits: GeneralWorkFileLimits | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        owned = Path(root).expanduser()
        owned.mkdir(parents=True, exist_ok=True)
        if not owned.is_dir():
            raise ValueError("General Work file root must be a directory")
        self.root = owned.resolve()
        self.limits = limits or GeneralWorkFileLimits()
        self._fault_injector = fault_injector
        self._lock = threading.RLock()
        self._backup_root = self.root / STATE_DIRECTORY / "backups"
        self._operation_root = self.root / STATE_DIRECTORY / "operations"

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)

    @staticmethod
    def _validate_idempotency_key(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise GeneralWorkFileError(
                "invalid_input", "idempotency_key must be non-empty text"
            )
        if len(value) > 512:
            raise GeneralWorkFileError(
                "invalid_input", "idempotency_key exceeds 512 characters"
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
            raise GeneralWorkFileError("invalid_input", "path must be text")
        raw = str(reference)
        if not raw.strip() or "\x00" in raw:
            raise GeneralWorkFileError(
                "invalid_input", "path is empty or contains NUL"
            )
        relative = Path(raw)
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise GeneralWorkFileError(
                "path_outside_root", "absolute paths and traversal are refused"
            )
        if any(":" in part for part in relative.parts):
            raise GeneralWorkFileError(
                "invalid_input", "path components cannot contain a drive or stream"
            )
        if relative == Path(".") and not allow_root:
            raise GeneralWorkFileError(
                "invalid_input", "the owned root is not a file target"
            )
        if (
            relative.parts
            and relative.parts[0] == STATE_DIRECTORY
            and not allow_internal
        ):
            raise GeneralWorkFileError(
                "reserved_path", "connector backup paths are not caller-accessible"
            )
        candidate = self.root.joinpath(relative)
        try:
            resolved = candidate.resolve(strict=False)
            if os.path.commonpath((str(self.root), str(resolved))) != str(self.root):
                raise GeneralWorkFileError(
                    "path_outside_root", "resolved path leaves the owned root"
                )
        except OSError as error:
            raise GeneralWorkFileError("path_unavailable", str(error)) from error
        current = self.root
        for part in relative.parts:
            if part in {"", "."}:
                continue
            current = current / part
            if current.exists() and current.is_symlink():
                raise GeneralWorkFileError(
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
            raise GeneralWorkFileError(
                "symlink_refused", "symbolic links are refused"
            )
        stat_result = path.stat()
        if path.is_file():
            if stat_result.st_size > self.limits.max_file_bytes:
                raise GeneralWorkFileError(
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
        raise GeneralWorkFileError(
            "unsupported_file_type", "path is not a regular file or directory"
        )

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
        return _json_copy(
            {
                "schema": RECEIPT_SCHEMA,
                "operation": operation,
                "request_id": request_id or f"general-work-{uuid.uuid4().hex}",
                "status": status,
                "before": before,
                "target_identity": target_identity,
                "source_identity": source_identity,
                "result": result,
                "readback": readback,
                "error": (
                    {
                        "classification": classification,
                        "message": message or classification,
                    }
                    if classification
                    else None
                ),
                "replayed": bool(replayed),
                "reconciled": bool(reconciled),
            }
        )

    def _error_receipt(
        self,
        operation: str,
        error: GeneralWorkFileError,
        *,
        request_id: str | None = None,
        before: Any = None,
        target_identity: Any = None,
        source_identity: Any = None,
        readback: Any = None,
    ) -> dict[str, Any]:
        return self._receipt(
            operation,
            request_id=request_id,
            status=error.status,
            before=before,
            target_identity=target_identity,
            source_identity=source_identity,
            readback=readback,
            classification=error.classification,
            message=str(error),
        )

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
                os.link(temporary, path)
                os.unlink(temporary)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

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

    def _operation_intent_path(self, idempotency_key: str) -> Path:
        token = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return self._operation_root / f"{token}.json"

    def _read_operation_intent(
        self,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        path = self._operation_intent_path(idempotency_key)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise GeneralWorkFileError(
                "idempotency_state_invalid",
                "file-operation idempotency state is not a regular file",
                status="UNKNOWN",
            )
        try:
            if path.stat().st_size > 64 * 1024:
                raise GeneralWorkFileError(
                    "idempotency_state_invalid",
                    "file-operation idempotency state exceeds its bound",
                    status="UNKNOWN",
                )
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise GeneralWorkFileError(
                "idempotency_state_invalid",
                "file-operation idempotency state is unreadable",
                status="UNKNOWN",
            ) from error
        if not isinstance(value, dict):
            raise GeneralWorkFileError(
                "idempotency_state_invalid",
                "file-operation idempotency state is malformed",
                status="UNKNOWN",
            )
        return value

    @staticmethod
    def _operation_signature(
        operation: str,
        source_ref: str,
        target_ref: str,
        expected_source_hash: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        return {
            "schema": OPERATION_INTENT_SCHEMA,
            "operation": operation,
            "source_ref": source_ref,
            "target_ref": target_ref,
            "expected_source_hash": expected_source_hash,
            "idempotency_key_sha256": hashlib.sha256(
                idempotency_key.encode("utf-8")
            ).hexdigest(),
        }

    @staticmethod
    def _validate_operation_intent(
        intent: Mapping[str, Any],
        signature: Mapping[str, str],
    ) -> None:
        if any(intent.get(name) != value for name, value in signature.items()):
            raise GeneralWorkFileError(
                "idempotency_conflict",
                "idempotency key already names a different file operation",
                status="UNKNOWN",
            )
        if (
            not isinstance(intent.get("source_identity"), Mapping)
            or not isinstance(intent.get("target_before"), Mapping)
        ):
            raise GeneralWorkFileError(
                "idempotency_state_invalid",
                "file-operation idempotency state lacks captured identities",
                status="UNKNOWN",
            )

    def _record_operation_intent(
        self,
        *,
        signature: Mapping[str, str],
        idempotency_key: str,
        source_identity: Mapping[str, Any],
        target_before: Mapping[str, Any],
    ) -> dict[str, Any]:
        existing = self._read_operation_intent(idempotency_key)
        if existing is not None:
            self._validate_operation_intent(existing, signature)
            return existing
        intent = {
            **dict(signature),
            "source_identity": dict(source_identity),
            "target_before": dict(target_before),
        }
        encoded = json.dumps(
            intent,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        path = self._operation_intent_path(idempotency_key)
        try:
            self._atomic_create(path, encoded)
        except FileExistsError:
            existing = self._read_operation_intent(idempotency_key)
            if existing is None:  # pragma: no cover - impossible without deletion race
                raise GeneralWorkFileError(
                    "idempotency_state_invalid",
                    "file-operation idempotency state disappeared",
                    status="UNKNOWN",
                )
            self._validate_operation_intent(existing, signature)
            return existing
        return intent

    def _reconcile_existing_transfer(
        self,
        *,
        operation: str,
        source: Path,
        target: Path,
        expected_source_hash: str,
        idempotency_key: str,
        source_current: Mapping[str, Any],
        target_current: Mapping[str, Any],
        missing_intent_classification: str,
    ) -> dict[str, Any]:
        signature = self._operation_signature(
            operation,
            self._relative_ref(source),
            self._relative_ref(target),
            expected_source_hash,
            idempotency_key,
        )
        intent = self._read_operation_intent(idempotency_key)
        if intent is None:
            raise GeneralWorkFileError(
                missing_intent_classification,
                "operation refuses an existing target without matching identity",
                status=(
                    "FAILED"
                    if missing_intent_classification == "target_exists"
                    else "UNKNOWN"
                ),
            )
        self._validate_operation_intent(intent, signature)
        target_matches = (
            target_current.get("kind") == "file"
            and target_current.get("sha256") == expected_source_hash
        )
        source_matches = (
            source_current.get("kind") == "file"
            and source_current.get("sha256") == expected_source_hash
            if operation == "file.copy"
            else source_current.get("exists") is False
        )
        if not target_matches or not source_matches:
            raise GeneralWorkFileError(
                "reconciliation_mismatch",
                "current source/target state does not prove the landed operation",
                status="UNKNOWN",
            )
        return self._receipt(
            operation,
            request_id=idempotency_key,
            before=intent["target_before"],
            source_identity=intent["source_identity"],
            target_identity=target_current,
            result={
                "copied": operation == "file.copy",
                "moved": operation in {"file.move", "file.rename"},
            },
            readback={
                "source": source_current,
                "target": target_current,
            },
            replayed=True,
            reconciled=True,
        )

    def inspect_transfer_reconciliation(
        self,
        operation: str,
        source_ref: str | Path,
        target_ref: str | Path,
        *,
        expected_source_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Read whether a previously-started transfer already landed."""

        source_current = None
        target_current = None
        try:
            if operation not in _TRANSFER_ACTIONS:
                raise GeneralWorkFileError(
                    "invalid_input", "operation is not a reconcilable file transfer"
                )
            key = self._validate_idempotency_key(idempotency_key)
            expected_hash = _normalize_hash(
                expected_source_hash, "expected_source_hash"
            )
            source = self._resolve(source_ref)
            target = self._resolve(target_ref)
            if source == target:
                raise GeneralWorkFileError(
                    "invalid_input", "source and target must be different"
                )
            with self._lock:
                source_current = self._identity(source)
                target_current = self._identity(target)
                if not target_current["exists"]:
                    raise GeneralWorkFileError(
                        "reconciliation_pending",
                        "target has not landed",
                        status="UNKNOWN",
                    )
                return self._reconcile_existing_transfer(
                    operation=operation,
                    source=source,
                    target=target,
                    expected_source_hash=expected_hash,
                    idempotency_key=key,
                    source_current=source_current,
                    target_current=target_current,
                    missing_intent_classification="reconciliation_identity_missing",
                )
        except GeneralWorkFileError as error:
            return self._error_receipt(
                operation,
                error,
                request_id=(
                    idempotency_key if isinstance(idempotency_key, str) else None
                ),
                source_identity=source_current,
                target_identity=target_current,
                readback={
                    "source": source_current,
                    "target": target_current,
                },
            )
        except OSError as error:
            return self._error_receipt(
                operation,
                GeneralWorkFileError(
                    "reconciliation_failed", str(error), status="UNKNOWN"
                ),
                request_id=(
                    idempotency_key if isinstance(idempotency_key, str) else None
                ),
                source_identity=source_current,
                target_identity=target_current,
            )

    def inspect_file(
        self,
        reference: str | Path,
        *,
        preview_bytes: int = 0,
    ) -> dict[str, Any]:
        operation = "file.inspect"
        try:
            if (
                not isinstance(preview_bytes, int)
                or preview_bytes < 0
                or preview_bytes > self.limits.max_preview_bytes
            ):
                raise GeneralWorkFileError(
                    "invalid_input",
                    f"preview_bytes must be between 0 and {self.limits.max_preview_bytes}",
                )
            target = self._resolve(reference, allow_root=True)
            identity = self._identity(target)
            result: dict[str, Any] = {"identity": identity}
            if preview_bytes and identity["kind"] == "file":
                content = target.read_bytes()[:preview_bytes]
                result["preview"] = content.decode("utf-8", errors="replace")
                result["preview_bytes"] = len(content)
            elif identity["kind"] == "directory":
                entries = sorted(
                    item.name
                    for item in target.iterdir()
                    if item.name != STATE_DIRECTORY and not item.is_symlink()
                )
                result["entries"] = entries[: self.limits.max_search_results]
                result["entries_truncated"] = (
                    len(entries) > self.limits.max_search_results
                )
            return self._receipt(
                operation,
                before=identity,
                target_identity=identity,
                result=result,
                readback=identity,
                reconciled=True,
            )
        except GeneralWorkFileError as error:
            return self._error_receipt(operation, error)
        except OSError as error:
            return self._error_receipt(
                operation,
                GeneralWorkFileError("inspect_failed", str(error)),
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
                raise GeneralWorkFileError(
                    "invalid_input", "name_pattern or content_query is required"
                )
            if name_pattern is not None and not isinstance(name_pattern, str):
                raise GeneralWorkFileError(
                    "invalid_input", "name_pattern must be text"
                )
            if (
                content_query is not None
                and (not isinstance(content_query, str) or not content_query)
            ):
                raise GeneralWorkFileError(
                    "invalid_input", "content_query must be non-empty text"
                )
            result_limit = (
                self.limits.max_search_results
                if max_results is None
                else min(int(max_results), self.limits.max_search_results)
            )
            file_limit = (
                self.limits.max_search_files
                if max_files is None
                else min(int(max_files), self.limits.max_search_files)
            )
            if result_limit <= 0 or file_limit <= 0:
                raise GeneralWorkFileError(
                    "invalid_input", "search limits must be positive"
                )
            start_path = self._resolve(start, allow_root=True)
            start_identity = self._identity(start_path)
            if start_identity["kind"] != "directory":
                raise GeneralWorkFileError(
                    "not_a_directory", "search start must be a directory"
                )
            matches: list[dict[str, Any]] = []
            scanned = 0
            limit_reached = False
            for current, directories, files in os.walk(
                start_path, followlinks=False
            ):
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
                        if path.stat().st_size > self.limits.max_search_file_bytes:
                            continue
                        text = path.read_bytes().decode("utf-8", errors="replace")
                        index = text.find(content_query)
                        if index < 0:
                            continue
                        left = max(0, index - 80)
                        right = min(len(text), index + len(content_query) + 80)
                        snippet = text[left:right]
                    matches.append(
                        {"identity": self._identity(path), "snippet": snippet}
                    )
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
        except (TypeError, ValueError) as error:
            return self._error_receipt(
                operation,
                GeneralWorkFileError("invalid_input", str(error)),
            )
        except GeneralWorkFileError as error:
            return self._error_receipt(operation, error)
        except OSError as error:
            return self._error_receipt(
                operation,
                GeneralWorkFileError("search_failed", str(error)),
            )

    def copy_file(
        self,
        source_ref: str | Path,
        target_ref: str | Path,
        *,
        expected_source_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._copy_or_move(
            "file.copy",
            source_ref,
            target_ref,
            expected_source_hash=expected_source_hash,
            idempotency_key=idempotency_key,
            move=False,
        )

    def move_file(
        self,
        source_ref: str | Path,
        target_ref: str | Path,
        *,
        expected_source_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._copy_or_move(
            "file.move",
            source_ref,
            target_ref,
            expected_source_hash=expected_source_hash,
            idempotency_key=idempotency_key,
            move=True,
        )

    def rename_file(
        self,
        source_ref: str | Path,
        target_ref: str | Path,
        *,
        expected_source_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._copy_or_move(
            "file.rename",
            source_ref,
            target_ref,
            expected_source_hash=expected_source_hash,
            idempotency_key=idempotency_key,
            move=True,
        )

    def _copy_or_move(
        self,
        operation: str,
        source_ref: str | Path,
        target_ref: str | Path,
        *,
        expected_source_hash: str,
        idempotency_key: str,
        move: bool,
    ) -> dict[str, Any]:
        before = None
        source_before = None
        target_after = None
        try:
            key = self._validate_idempotency_key(idempotency_key)
            expected_hash = _normalize_hash(
                expected_source_hash, "expected_source_hash"
            )
            source = self._resolve(source_ref)
            target = self._resolve(target_ref)
            if source == target:
                raise GeneralWorkFileError(
                    "invalid_input", "source and target must be different"
                )
            with self._lock:
                source_before = self._identity(source)
                before = self._identity(target)
                if before["exists"]:
                    target_after = before
                    return self._reconcile_existing_transfer(
                        operation=operation,
                        source=source,
                        target=target,
                        expected_source_hash=expected_hash,
                        idempotency_key=key,
                        source_current=source_before,
                        target_current=before,
                        missing_intent_classification="target_exists",
                    )
                if source_before["kind"] != "file":
                    classification = (
                        "not_found"
                        if not source_before["exists"]
                        else "not_a_file"
                    )
                    raise GeneralWorkFileError(
                        classification, "source must be an existing regular file"
                    )
                if source_before["sha256"] != expected_hash:
                    raise GeneralWorkFileError(
                        "source_hash_mismatch",
                        "source changed since the plan captured it",
                    )
                signature = self._operation_signature(
                    operation,
                    self._relative_ref(source),
                    self._relative_ref(target),
                    expected_hash,
                    key,
                )
                self._record_operation_intent(
                    signature=signature,
                    idempotency_key=key,
                    source_identity=source_before,
                    target_before=before,
                )
                content = source.read_bytes()
                self._fault(f"{operation}.before_publish")
                if move:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(source, target)
                else:
                    self._atomic_create(target, content)
                self._fault(f"{operation}.after_publish")
                source_after = self._identity(source)
                target_after = self._identity(target)
                if target_after["sha256"] != expected_hash:
                    raise GeneralWorkFileError(
                        "readback_mismatch",
                        "target hash differs from the captured source hash",
                        status="UNKNOWN",
                    )
                if move and source_after["exists"]:
                    raise GeneralWorkFileError(
                        "readback_mismatch",
                        "move target exists but source was not removed",
                        status="UNKNOWN",
                    )
                return self._receipt(
                    operation,
                    request_id=key,
                    before=before,
                    source_identity=source_before,
                    target_identity=target_after,
                    result={"copied": not move, "moved": move},
                    readback={"source": source_after, "target": target_after},
                )
        except GeneralWorkFileError as error:
            return self._error_receipt(
                operation,
                error,
                request_id=(
                    idempotency_key if isinstance(idempotency_key, str) else None
                ),
                before=before,
                target_identity=target_after,
                source_identity=source_before,
            )
        except (OSError, InjectedGeneralWorkFileFailure) as error:
            readback = None
            try:
                readback = {
                    "source": self._identity(source),
                    "target": self._identity(target),
                }
            except (OSError, GeneralWorkFileError):
                pass
            return self._error_receipt(
                operation,
                GeneralWorkFileError(
                    "partial_failure", str(error), status="UNKNOWN"
                ),
                request_id=(
                    idempotency_key if isinstance(idempotency_key, str) else None
                ),
                before=before,
                target_identity=target_after,
                source_identity=source_before,
                readback=readback,
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
        operation = "file.safe_edit"
        before = None
        backup_identity = None
        try:
            key = self._validate_idempotency_key(idempotency_key)
            expected_hash = _normalize_hash(
                expected_source_hash, "expected_source_hash"
            )
            if not isinstance(new_text, str):
                raise GeneralWorkFileError(
                    "invalid_input", "new_text must be text"
                )
            if not preserve_original:
                raise GeneralWorkFileError(
                    "source_preservation_required",
                    "safe text edit requires original preservation",
                )
            try:
                payload = new_text.encode(encoding)
            except (LookupError, UnicodeEncodeError) as error:
                raise GeneralWorkFileError("invalid_input", str(error)) from error
            if len(payload) > self.limits.max_file_bytes:
                raise GeneralWorkFileError(
                    "size_limit_exceeded",
                    "edited content exceeds the file byte limit",
                )
            desired_hash = _sha256_bytes(payload)
            target = self._resolve(target_ref)
            backup_token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
            backup = (
                self._backup_root
                / backup_token
                / Path(self._relative_ref(target) + ".original")
            )
            backup_ref = self._relative_ref(backup)
            with self._lock:
                before = self._identity(target)
                backup_identity = self._identity(backup)
                if (
                    before["kind"] == "file"
                    and before["sha256"] == desired_hash
                    and backup_identity["kind"] == "file"
                    and backup_identity["sha256"] == expected_hash
                ):
                    result = {
                        "edited": True,
                        "backup_ref": backup_ref,
                        "backup_sha256": expected_hash,
                    }
                    return self._receipt(
                        operation,
                        request_id=key,
                        before=before,
                        target_identity=before,
                        source_identity=backup_identity,
                        result=result,
                        readback={
                            "target": before,
                            "backup": backup_identity,
                        },
                        replayed=True,
                        reconciled=True,
                    )
                if before["kind"] != "file":
                    classification = (
                        "not_found" if not before["exists"] else "not_a_file"
                    )
                    raise GeneralWorkFileError(
                        classification,
                        "safe edit target must be an existing regular file",
                    )
                if before["sha256"] != expected_hash:
                    raise GeneralWorkFileError(
                        "source_hash_mismatch",
                        "target changed since the plan captured it",
                    )
                original = target.read_bytes()
                if backup_identity["exists"]:
                    if backup_identity["sha256"] != expected_hash:
                        raise GeneralWorkFileError(
                            "source_preservation_drift",
                            "preserved original does not match the captured source",
                            status="UNKNOWN",
                        )
                else:
                    self._atomic_create(backup, original)
                    backup_identity = self._identity(backup)
                self._fault("file.safe_edit.after_backup")
                self._atomic_replace(target, payload)
                self._fault("file.safe_edit.after_publish")
                after = self._identity(target)
                backup_after = self._identity(backup)
                if (
                    after["sha256"] != desired_hash
                    or backup_after["sha256"] != expected_hash
                ):
                    raise GeneralWorkFileError(
                        "readback_mismatch",
                        "edited target or preserved original failed verification",
                        status="UNKNOWN",
                    )
                result = {
                    "edited": True,
                    "backup_ref": backup_ref,
                    "backup_sha256": expected_hash,
                }
                return self._receipt(
                    operation,
                    request_id=key,
                    before=before,
                    target_identity=after,
                    source_identity=backup_after,
                    result=result,
                    readback={"target": after, "backup": backup_after},
                )
        except GeneralWorkFileError as error:
            return self._error_receipt(
                operation,
                error,
                request_id=(
                    idempotency_key if isinstance(idempotency_key, str) else None
                ),
                before=before,
                source_identity=backup_identity,
            )
        except (OSError, InjectedGeneralWorkFileFailure) as error:
            readback = None
            try:
                readback = {
                    "target": self._identity(target),
                    "backup": self._identity(backup),
                }
            except (OSError, GeneralWorkFileError):
                pass
            return self._error_receipt(
                operation,
                GeneralWorkFileError(
                    "partial_failure", str(error), status="UNKNOWN"
                ),
                request_id=(
                    idempotency_key if isinstance(idempotency_key, str) else None
                ),
                before=before,
                source_identity=backup_identity,
                readback=readback,
            )


__all__ = [
    "GeneralWorkFileConnector",
    "GeneralWorkFileError",
    "GeneralWorkFileLimits",
    "InjectedGeneralWorkFileFailure",
]
