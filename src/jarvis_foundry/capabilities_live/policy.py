"""Financial denial policy: no capability, no code path, deterministic refusal."""

from __future__ import annotations

from jarvis_foundry.kernel.errors import FinancialActionDeniedError
from jarvis_foundry.kernel.events import EventKindRegistry


class FinancialPolicy:
    """The only financial behavior implemented is refusal."""

    @staticmethod
    def has_capability() -> bool:
        return False

    @staticmethod
    def capability_ids() -> tuple[str, ...]:
        return ()

    @staticmethod
    def assert_denied(request_type: str) -> None:
        raise FinancialActionDeniedError(
            f"financial action always denied; no capability or code path exists: {request_type}"
        )

    @staticmethod
    def financial_kinds(registry: EventKindRegistry) -> list[str]:
        return [kind for kind, spec in registry.entries().items() if spec.financial]
