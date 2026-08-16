"""Fail-closed contract fakes: refusals always leave no partial effect."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.capabilities_live.adapters.base import (
    BeforeState,
    CanaryAdapter,
    ExecutionResult,
    Outcome,
)
from jarvis_foundry.capabilities_live.broker import ResolvedCredential
from jarvis_foundry.capabilities_live.policy import FinancialPolicy


class FailClosedFakeAdapter(CanaryAdapter):
    def __init__(self, proving_root: Path, mode: str) -> None:
        super().__init__(
            proving_root,
            capability_id=f"fake.{mode}",
            domain="fakes",
        )
        self.mode = mode
        self.root = self._ensure_within(self.proving_root / "fake-state")
        self.root.mkdir(parents=True, exist_ok=True)

    def execute(
        self,
        operation: str,
        request: Mapping[str, Any],
        credential: ResolvedCredential | None,
    ) -> ExecutionResult:
        before = BeforeState(
            state_hash=self._dir_hash(self.root),
            detail={"root": str(self.root), "mode": self.mode},
        )
        idempotency_key = str(request.get("idempotency_key", "n/a"))
        mode = str(request.get("mode", self.mode))
        if credential is None:
            reason = (
                "missing credential refused"
                if mode == "missing_credential"
                else f"credential unavailable; {mode} refused"
            )
            return self._refusal(
                outcome=Outcome.REFUSED,
                operation=operation,
                idempotency_key=idempotency_key,
                before=before,
                reason=reason,
            )
        if mode in {"expired", "revoked"}:
            return self._refusal(
                outcome=Outcome.REFUSED,
                operation=operation,
                idempotency_key=idempotency_key,
                before=before,
                reason=f"credential {mode} refused",
            )
        if mode in {"quota", "rate"}:
            retry = 60 if mode == "quota" else 5
            return self._refusal(
                outcome=Outcome.DEFERRED,
                operation=operation,
                idempotency_key=idempotency_key,
                before=before,
                reason=f"{mode} exceeded; deferred",
                retry_after_seconds=retry,
            )
        if mode == "malformed_receipt":
            return self._refusal(
                outcome=Outcome.NOT_ACCEPTED,
                operation=operation,
                idempotency_key=idempotency_key,
                before=before,
                reason="provider receipt missing required fields; not accepted",
            )
        if mode == "unknown_target":
            return self._refusal(
                outcome=Outcome.REFUSED,
                operation=operation,
                idempotency_key=idempotency_key,
                before=before,
                reason="unknown account or target refused",
            )
        if mode == "financial":
            try:
                FinancialPolicy.assert_denied(str(request.get("request_type", "payment")))
            except Exception as error:  # noqa: BLE001 - policy refusal becomes a result
                return self._refusal(
                    outcome=Outcome.DENIED,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    before=before,
                    reason=str(error),
                )
        return self._refusal(
            outcome=Outcome.REFUSED,
            operation=operation,
            idempotency_key=idempotency_key,
            before=before,
            reason=f"unknown fail-closed mode: {mode}",
        )
