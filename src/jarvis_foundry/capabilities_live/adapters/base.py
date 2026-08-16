"""Shared adapter contracts, receipts, and bounded canary helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.errors import CapabilityError


class Outcome(StrEnum):
    EXECUTED = "EXECUTED"
    REPLAYED = "REPLAYED"
    REFUSED = "REFUSED"
    DEFERRED = "DEFERRED"
    DENIED = "DENIED"
    NOT_ACCEPTED = "NOT_ACCEPTED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class BeforeState:
    state_hash: str
    detail: Mapping[str, Any]


@dataclass(frozen=True)
class AfterState:
    state_hash: str
    detail: Mapping[str, Any]


@dataclass(frozen=True)
class Receipt:
    operation: str
    idempotency_key: str
    provider_reference: str
    effect_hash: str
    detail: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "idempotency_key": self.idempotency_key,
            "provider_reference": self.provider_reference,
            "effect_hash": self.effect_hash,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class ExecutionResult:
    outcome: Outcome
    before: BeforeState
    after: AfterState | None
    receipt: Receipt | None
    reason: str = ""
    no_partial_effect: bool = True
    reconciliation: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "before_state_hash": self.before.state_hash,
            "before_detail": dict(self.before.detail),
            "after_state_hash": self.after.state_hash if self.after is not None else None,
            "after_detail": dict(self.after.detail) if self.after is not None else None,
            "receipt": self.receipt.to_dict() if self.receipt is not None else None,
            "reason": self.reason,
            "no_partial_effect": self.no_partial_effect,
            "reconciliation": dict(self.reconciliation),
        }


class CanaryAdapter:
    """Base for bounded local adapters with durable idempotency journals."""

    capability_id: str
    domain: str

    def __init__(self, proving_root: Path, *, capability_id: str, domain: str) -> None:
        self._proving_root = Path(proving_root).resolve()
        self.capability_id = capability_id
        self.domain = domain
        self._stop_requested = False
        self._fault = False
        self._journal_path = self._proving_root / "receipts" / f"{capability_id}.jsonl"
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def proving_root(self) -> Path:
        return self._proving_root

    def request_stop(self) -> None:
        self._stop_requested = True

    def stop(self) -> dict[str, Any]:
        self._stop_requested = True
        return {
            "capability_id": self.capability_id,
            "stopped": True,
            "refuses_new_work": True,
        }

    def inject_fault(self) -> None:
        self._fault = True

    def _ensure_within(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self._proving_root):
            raise CapabilityError(f"canary path escapes proving root: {resolved}")
        return resolved

    def _load_journal(self) -> list[dict[str, Any]]:
        if not self._journal_path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._journal_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
        return entries

    def _journal_entry(self, operation: str, idempotency_key: str) -> dict[str, Any] | None:
        for entry in self._load_journal():
            if (
                entry.get("operation") == operation
                and entry.get("idempotency_key") == idempotency_key
            ):
                return entry
        return None

    def _append_journal(self, entry: Mapping[str, Any]) -> None:
        with self._journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(entry), sort_keys=True) + "\n")

    def _refusal(
        self,
        *,
        outcome: Outcome,
        operation: str,
        idempotency_key: str,
        before: BeforeState,
        reason: str,
        retry_after_seconds: int | None = None,
    ) -> ExecutionResult:
        reconciliation: dict[str, Any] = {
            "reconciled": True,
            "detail": "no effect produced",
        }
        if retry_after_seconds is not None:
            reconciliation["retry_after_seconds"] = retry_after_seconds
        return ExecutionResult(
            outcome=outcome,
            before=before,
            after=AfterState(
                state_hash=before.state_hash,
                detail=dict(before.detail),
            ),
            receipt=None,
            reason=reason,
            no_partial_effect=True,
            reconciliation=reconciliation,
        )

    @staticmethod
    def _file_hash(path: Path) -> str | None:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _dir_hash(self, root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith("receipts/"):
                continue
            file_hash = self._file_hash(path)
            digest.update(relative.encode("utf-8"))
            digest.update(b":")
            digest.update(file_hash.encode("utf-8") if file_hash is not None else b"")
            digest.update(b";")
        return digest.hexdigest()
