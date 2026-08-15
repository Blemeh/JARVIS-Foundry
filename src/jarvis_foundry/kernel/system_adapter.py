"""Fail-closed default system adapter for the kernel."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jarvis_foundry.kernel.errors import EffectError
from jarvis_foundry.kernel.records import EffectIntent, EffectReceipt, ReconciliationResult


class RejectingSystemAdapter:
    """Kernel-safe adapter that refuses every external effect."""

    def prepare(self, intent: EffectIntent) -> Any:
        raise EffectError(f"no system adapter is configured for {intent.system}")

    def execute(self, prepared: Any) -> Mapping[str, Any]:
        raise EffectError("no system adapter is configured")

    def inspect(self, intent_id: str) -> list[EffectReceipt]:
        raise EffectError("no system adapter is configured")

    def reconcile(self, intent_id: str) -> ReconciliationResult:
        raise EffectError("no system adapter is configured")

    def compensate(self, intent_id: str) -> Mapping[str, Any]:
        raise EffectError("no system adapter is configured")
