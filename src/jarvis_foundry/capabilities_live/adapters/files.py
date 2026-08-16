"""Real local files adapter with hashed before/after state and receipts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.capabilities_live.adapters.base import (
    AfterState,
    BeforeState,
    CanaryAdapter,
    ExecutionResult,
    Outcome,
    Receipt,
)
from jarvis_foundry.capabilities_live.broker import ResolvedCredential


class FilesAdapter(CanaryAdapter):
    def __init__(self, proving_root: Path) -> None:
        super().__init__(
            proving_root,
            capability_id="capability.files",
            domain="files",
        )
        self.root = self._ensure_within(self.proving_root / "files-canary")
        self.root.mkdir(parents=True, exist_ok=True)

    def execute(
        self,
        operation: str,
        request: Mapping[str, Any],
        credential: ResolvedCredential,
    ) -> ExecutionResult:
        before = BeforeState(
            state_hash=self._dir_hash(self.root),
            detail={"root": str(self.root)},
        )
        if self._stop_requested:
            return self._refusal(
                outcome=Outcome.STOPPED,
                operation=operation,
                idempotency_key=str(request.get("idempotency_key", "n/a")),
                before=before,
                reason="adapter stop requested; new operations refused",
            )
        if operation == "rollback":
            return self._rollback(request, before)
        if operation != "write":
            return self._refusal(
                outcome=Outcome.REFUSED,
                operation=operation,
                idempotency_key=str(request.get("idempotency_key", "n/a")),
                before=before,
                reason=f"unknown operation: {operation}",
            )
        idempotency_key = str(request["idempotency_key"])
        relative = str(request["relative_path"])
        content = str(request["content"])
        target = self._ensure_within(self.root / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        prior_file_hash = self._file_hash(target)
        previous_content = target.read_text(encoding="utf-8") if target.is_file() else ""
        previous_existed = target.is_file()

        entry = self._journal_entry("write", idempotency_key)
        if entry is not None:
            current_dir_hash = self._dir_hash(self.root)
            current_file_hash = self._file_hash(target)
            if (
                current_dir_hash == entry["after_dir_hash"]
                and current_file_hash == entry["after_file_hash"]
            ):
                receipt = Receipt(
                    operation="write",
                    idempotency_key=idempotency_key,
                    provider_reference=str(entry["receipt"]["provider_reference"]),
                    effect_hash=str(entry["receipt"]["effect_hash"]),
                    detail=dict(entry["receipt"]["detail"]),
                )
                return ExecutionResult(
                    outcome=Outcome.REPLAYED,
                    before=before,
                    after=AfterState(
                        state_hash=current_dir_hash,
                        detail={"root": str(self.root), "relative_path": relative},
                    ),
                    receipt=receipt,
                    reason="idempotent replay; state already matches receipt",
                    no_partial_effect=True,
                    reconciliation={"reconciled": True, "detail": "state unchanged by replay"},
                )
            return ExecutionResult(
                outcome=Outcome.FAILED,
                before=before,
                after=None,
                receipt=None,
                reason="state diverged from idempotent receipt",
                no_partial_effect=False,
                reconciliation={"reconciled": False, "detail": "expected receipt state missing"},
            )

        if self._fault:
            return ExecutionResult(
                outcome=Outcome.FAILED,
                before=before,
                after=AfterState(
                    state_hash=before.state_hash,
                    detail=dict(before.detail),
                ),
                receipt=None,
                reason="injected provider failure; no write performed",
                no_partial_effect=True,
                reconciliation={"reconciled": True, "detail": "before and after state identical"},
            )

        if target.is_file() and target.read_text(encoding="utf-8") == content:
            after_hash = self._dir_hash(self.root)
            receipt = Receipt(
                operation="write",
                idempotency_key=idempotency_key,
                provider_reference=f"file://{relative}",
                effect_hash=self._file_hash(target) or "",
                detail={
                    "relative_path": relative,
                    "before_dir_hash": before.state_hash,
                    "after_dir_hash": after_hash,
                    "already_matched": True,
                },
            )
            return ExecutionResult(
                outcome=Outcome.REPLAYED,
                before=before,
                after=AfterState(state_hash=after_hash, detail={"relative_path": relative}),
                receipt=receipt,
                reason="content already present; no write performed",
                no_partial_effect=True,
                reconciliation={"reconciled": True, "detail": "read-back matched request"},
            )

        temporary = target.with_name(target.name + ".r9tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
        after_dir_hash = self._dir_hash(self.root)
        after_file_hash = self._file_hash(target) or ""
        receipt = Receipt(
            operation="write",
            idempotency_key=idempotency_key,
            provider_reference=f"file://{relative}",
            effect_hash=after_file_hash,
            detail={
                "relative_path": relative,
                "before_dir_hash": before.state_hash,
                "after_dir_hash": after_dir_hash,
                "before_file_hash": prior_file_hash,
                "after_file_hash": after_file_hash,
                "size_bytes": len(content.encode("utf-8")),
            },
        )
        read_back = target.read_text(encoding="utf-8")
        reconciled = read_back == content and self._dir_hash(self.root) == after_dir_hash
        self._append_journal(
            {
                "operation": "write",
                "idempotency_key": idempotency_key,
                "relative_path": relative,
                "before_dir_hash": before.state_hash,
                "after_dir_hash": after_dir_hash,
                "after_file_hash": after_file_hash,
                "previous_content": previous_content,
                "previous_existed": previous_existed,
                "receipt": receipt.to_dict(),
                "reconciliation": {
                    "reconciled": reconciled,
                    "detail": "read-back hash matched after state",
                },
            }
        )
        return ExecutionResult(
            outcome=Outcome.EXECUTED,
            before=before,
            after=AfterState(
                state_hash=after_dir_hash,
                detail={"root": str(self.root), "relative_path": relative},
            ),
            receipt=receipt,
            reason="real bounded file effect produced",
            no_partial_effect=not reconciled,
            reconciliation={
                "reconciled": reconciled,
                "detail": "read-back hash matched after state",
            },
        )

    def _rollback(self, request: Mapping[str, Any], before: BeforeState) -> ExecutionResult:
        relative = str(request.get("relative_path", ""))
        idempotency_key = str(request.get("idempotency_key", "n/a"))
        entry = self._journal_entry("write", idempotency_key)
        if entry is None:
            return self._refusal(
                outcome=Outcome.REFUSED,
                operation="rollback",
                idempotency_key=idempotency_key,
                before=before,
                reason="no journal entry for idempotency key",
            )
        target = self._ensure_within(self.root / relative)
        if bool(entry.get("previous_existed", False)):
            target.write_text(str(entry.get("previous_content", "")), encoding="utf-8")
        else:
            target.unlink(missing_ok=True)
        after_hash = self._dir_hash(self.root)
        restored = after_hash == entry.get("before_dir_hash", "")
        return ExecutionResult(
            outcome=Outcome.EXECUTED,
            before=before,
            after=AfterState(state_hash=after_hash, detail={"relative_path": relative}),
            receipt=Receipt(
                operation="rollback",
                idempotency_key=idempotency_key,
                provider_reference=f"file://{relative}",
                effect_hash=self._file_hash(target) or "",
                detail={"restored_to": entry.get("before_dir_hash", "")},
            ),
            reason="compensation restored prior canary state",
            no_partial_effect=not restored,
            reconciliation={"reconciled": restored, "detail": "rollback restored before-state"},
        )
