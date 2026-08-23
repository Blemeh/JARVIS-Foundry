"""V5-5 contract adapter for the existing bounded filesystem connector."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .activation import FsConnector
from .contract import ConnectorAttempt, ConnectorSpec


class FilesystemConnectorError(ValueError):
    """A bounded filesystem request was refused or needs reconciliation."""


@dataclass(frozen=True, slots=True)
class FilesystemSnapshot:
    target_ref: str
    exists: bool
    content_hash: str | None
    size_bytes: int
    content: bytes | None = None

    def as_state(self) -> dict[str, Any]:
        return {
            "target_ref": self.target_ref,
            "exists": self.exists,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
        }


def _content_hash(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


class FilesystemConnector(FsConnector):
    """A restart-aware adapter that reuses :class:`FsConnector` I/O.

    The small hash-only ledger is stored inside the lane-owned root. It never
    stores file content or credentials. A crash after the file replacement but
    before the ledger write is reconciled by reading the resulting file hash.
    """

    LEDGER_NAME = ".jarvis-fs-idempotency.json"

    spec = ConnectorSpec(
        connector_id="conn-fs-v5",
        kind="filesystem",
        version="1.0.0",
        actions={"file.read": "read", "file.write": "write"},
        capabilities={"read": "READY", "write": "READY"},
        retry_policy={"max_attempts": 3, "backoff_s": [0, 0.05]},
        target_kinds=("filesystem_path",),
        idempotency_strategy="provider-key",
        reconciliation_strategy="system-readback",
        supports_cancellation=False,
        cost_metadata={"unit": "local_io", "cost_known": True},
        evidence_fields=("attempt_id", "outcome", "after_state", "reconciled"),
    )

    def __init__(self, root: str | Path) -> None:
        try:
            super().__init__(root)
        except ValueError as error:
            raise FilesystemConnectorError(str(error)) from error
        self._ledger_path = self.root / self.LEDGER_NAME

    def _resolve_contract_ref(self, target_ref: str) -> Path:
        if target_ref == self.LEDGER_NAME:
            raise FilesystemConnectorError("the connector ledger is reserved")
        try:
            return self._resolve(target_ref)
        except ValueError as error:
            raise FilesystemConnectorError(str(error)) from error

    def snapshot(self, target_ref: str) -> FilesystemSnapshot:
        target = self._resolve_contract_ref(target_ref)
        if not target.exists():
            return FilesystemSnapshot(target_ref, False, None, 0, None)
        if not target.is_file():
            raise FilesystemConnectorError("target is not a regular file")
        content = target.read_bytes()
        return FilesystemSnapshot(
            target_ref, True, _content_hash(content), len(content), content
        )

    def _load_ledger(self) -> dict[str, dict[str, Any]]:
        if not self._ledger_path.exists():
            return {}
        try:
            value = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FilesystemConnectorError("connector ledger is unreadable") from error
        if not isinstance(value, dict) or any(not isinstance(item, dict) for item in value.values()):
            raise FilesystemConnectorError("connector ledger is malformed")
        return value

    def _write_ledger(self, ledger: dict[str, dict[str, Any]]) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.root, prefix=".jarvis-fs-", suffix=".tmp", delete=False
            ) as handle:
                temporary = handle.name
                json.dump(ledger, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._ledger_path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    @staticmethod
    def _payload_hash(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _attempt_id() -> str:
        return f"fs-attempt-{uuid.uuid4().hex}"

    def _failed(
        self,
        *,
        action_kind: str,
        target_ref: str,
        idempotency_key: str,
        payload_hash: str,
        before: FilesystemSnapshot,
        error_code: str,
        reconciled: bool = False,
    ) -> ConnectorAttempt:
        return ConnectorAttempt(
            attempt_id=self._attempt_id(), connector_id=self.spec.connector_id,
            action_kind=action_kind, target_ref=target_ref,
            idempotency_key=idempotency_key, payload_hash=payload_hash,
            outcome="UNKNOWN" if error_code in {"state_drift", "ledger_write_failed"} else "FAILED",
            before_state=before.as_state(), after_state=None,
            error_code=error_code, evidence_refs=(f"filesystem:{target_ref}",),
            reconciled=reconciled,
        )

    def request(
        self,
        action_kind: str,
        target_ref: str,
        payload: str | None,
        *,
        idempotency_key: str,
    ) -> ConnectorAttempt:
        if not idempotency_key or not idempotency_key.strip():
            raise FilesystemConnectorError("idempotency_key is required")
        if action_kind not in self.spec.actions:
            raise FilesystemConnectorError(f"action {action_kind!r} is not declared")
        if action_kind == "file.write" and not isinstance(payload, str):
            raise FilesystemConnectorError("file.write payload must be text")
        text = payload or ""
        digest = self._payload_hash(text)
        with self._lock:
            before = self.snapshot(target_ref)
            if action_kind == "file.read":
                if not before.exists or before.content is None:
                    return self._failed(
                        action_kind=action_kind, target_ref=target_ref,
                        idempotency_key=idempotency_key, payload_hash=digest,
                        before=before, error_code="not_found")
                return ConnectorAttempt(
                    attempt_id=self._attempt_id(), connector_id=self.spec.connector_id,
                    action_kind=action_kind, target_ref=target_ref,
                    idempotency_key=idempotency_key, payload_hash=digest,
                    outcome="COMPLETED", before_state=before.as_state(),
                    after_state=before.as_state(),
                    provider_ref=target_ref, evidence_refs=(f"filesystem:{target_ref}",),
                )

            ledger = self._load_ledger()
            previous = ledger.get(idempotency_key)
            if previous is not None:
                if (previous.get("payload_hash") != digest or
                        previous.get("target_ref") != target_ref):
                    return self._failed(
                        action_kind=action_kind, target_ref=target_ref,
                        idempotency_key=idempotency_key, payload_hash=digest,
                        before=before, error_code="idempotency_conflict")
                expected = previous.get("after_hash")
                if before.content_hash == expected:
                    return ConnectorAttempt(
                        attempt_id=self._attempt_id(), connector_id=self.spec.connector_id,
                        action_kind=action_kind, target_ref=target_ref,
                        idempotency_key=idempotency_key, payload_hash=digest,
                        outcome="COMPLETED", before_state=before.as_state(),
                        after_state=before.as_state(), provider_ref=target_ref,
                        evidence_refs=(f"filesystem:{target_ref}",), replayed=True,
                        reconciled=True,
                    )
                return self._failed(
                    action_kind=action_kind, target_ref=target_ref,
                    idempotency_key=idempotency_key, payload_hash=digest,
                    before=before, error_code="state_drift")

            desired = text.encode("utf-8")
            desired_hash = _content_hash(desired)
            # A crash after replacement but before ledger persistence is safe to
            # retry: readback proves the desired state and no second write runs.
            if before.content_hash == desired_hash:
                ledger[idempotency_key] = {
                    "target_ref": target_ref, "payload_hash": digest,
                    "after_hash": desired_hash,
                }
                try:
                    self._write_ledger(ledger)
                except OSError as error:
                    return self._failed(
                        action_kind=action_kind, target_ref=target_ref,
                        idempotency_key=idempotency_key, payload_hash=digest,
                        before=before, error_code="ledger_write_failed")
                return ConnectorAttempt(
                    attempt_id=self._attempt_id(), connector_id=self.spec.connector_id,
                    action_kind=action_kind, target_ref=target_ref,
                    idempotency_key=idempotency_key, payload_hash=digest,
                    outcome="COMPLETED", before_state=before.as_state(),
                    after_state=before.as_state(), provider_ref=target_ref,
                    evidence_refs=(f"filesystem:{target_ref}",), replayed=True,
                    reconciled=True,
                )

            target = self._resolve_contract_ref(target_ref)
            if target.exists() and not (target.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)):
                return self._failed(
                    action_kind=action_kind, target_ref=target_ref,
                    idempotency_key=idempotency_key, payload_hash=digest,
                    before=before, error_code="read_only")
            try:
                self._perform_unlocked(target_ref, text, action="file.write")
                after = self.snapshot(target_ref)
                if after.content_hash != desired_hash:
                    return self._failed(
                        action_kind=action_kind, target_ref=target_ref,
                        idempotency_key=idempotency_key, payload_hash=digest,
                        before=before, error_code="readback_mismatch")
                ledger[idempotency_key] = {
                    "target_ref": target_ref, "payload_hash": digest,
                    "after_hash": desired_hash,
                }
                try:
                    self._write_ledger(ledger)
                except OSError:
                    return self._failed(
                        action_kind=action_kind, target_ref=target_ref,
                        idempotency_key=idempotency_key, payload_hash=digest,
                        before=before, error_code="ledger_write_failed")
            except (OSError, ValueError) as error:
                return self._failed(
                    action_kind=action_kind, target_ref=target_ref,
                    idempotency_key=idempotency_key, payload_hash=digest,
                    before=before, error_code="write_failed")
            return ConnectorAttempt(
                attempt_id=self._attempt_id(), connector_id=self.spec.connector_id,
                action_kind=action_kind, target_ref=target_ref,
                idempotency_key=idempotency_key, payload_hash=digest,
                outcome="COMPLETED", before_state=before.as_state(),
                after_state=after.as_state(), provider_ref=target_ref,
                evidence_refs=(f"filesystem:{target_ref}",),
            )

    def reconcile(self, target_ref: str, *, expected_hash: str) -> ConnectorAttempt:
        before = self.snapshot(target_ref)
        digest = self._payload_hash(expected_hash)
        matches = before.content_hash == expected_hash
        return ConnectorAttempt(
            attempt_id=self._attempt_id(), connector_id=self.spec.connector_id,
            action_kind="file.read", target_ref=target_ref,
            idempotency_key=f"reconcile:{target_ref}:{expected_hash}", payload_hash=digest,
            outcome="COMPLETED" if matches else "UNKNOWN",
            before_state=before.as_state(), after_state=before.as_state(),
            provider_ref=target_ref, error_code=None if matches else "state_drift",
            evidence_refs=(f"filesystem:{target_ref}",), reconciled=matches,
        )

    def rollback(self, target_ref: str, snapshot: FilesystemSnapshot) -> ConnectorAttempt:
        if snapshot.target_ref != target_ref:
            raise FilesystemConnectorError("rollback snapshot target mismatch")
        with self._lock:
            before = self.snapshot(target_ref)
            target = self._resolve_contract_ref(target_ref)
            try:
                if snapshot.exists:
                    if snapshot.content is None:
                        raise FilesystemConnectorError("rollback snapshot has no content")
                    if target.exists() and not (target.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)):
                        return self._failed(
                            action_kind="file.write", target_ref=target_ref,
                            idempotency_key=f"rollback:{target_ref}", payload_hash="0" * 64,
                            before=before, error_code="read_only")
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".jarvis-restore-", delete=False) as handle:
                        temporary = handle.name
                        handle.write(snapshot.content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, target)
                elif target.exists():
                    if not (target.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)):
                        return self._failed(
                            action_kind="file.write", target_ref=target_ref,
                            idempotency_key=f"rollback:{target_ref}", payload_hash="0" * 64,
                            before=before, error_code="read_only")
                    target.unlink()
                after = self.snapshot(target_ref)
            except (OSError, ValueError) as error:
                raise FilesystemConnectorError(f"rollback failed: {error}") from error
        return ConnectorAttempt(
            attempt_id=self._attempt_id(), connector_id=self.spec.connector_id,
            action_kind="file.write", target_ref=target_ref,
            idempotency_key=f"rollback:{target_ref}", payload_hash="0" * 64,
            outcome="COMPLETED", before_state=before.as_state(),
            after_state=after.as_state(), provider_ref=target_ref,
            evidence_refs=(f"filesystem:{target_ref}",), reconciled=True,
        )


__all__ = ["FilesystemConnector", "FilesystemConnectorError", "FilesystemSnapshot"]
