"""Deterministic policy and ActionEnvelope authorization."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from jarvis_foundry.kernel.errors import AuthorityError, FinancialActionDeniedError
from jarvis_foundry.kernel.records import ActionEnvelope

FINANCIAL_ACTION_CLASSES: tuple[str, ...] = (
    "payment",
    "transfer",
    "purchase",
    "banking",
    "trading",
    "crypto_transfer",
    "financial_account_change",
    "money_movement",
)


@dataclass(frozen=True)
class PolicyRule:
    action_class: str
    effect: str
    scope: str | None = None
    reason: str = ""


class PolicyEngine:
    def __init__(
        self,
        *,
        rules: Sequence[PolicyRule] = (),
        financial_classes: Sequence[str] = FINANCIAL_ACTION_CLASSES,
        policy_version: int = 1,
    ) -> None:
        self._rules = tuple(rules)
        self._financial = frozenset(financial_classes)
        self._version = policy_version

    @property
    def policy_version(self) -> int:
        return self._version

    def decide(self, envelope: ActionEnvelope) -> ActionEnvelope:
        if envelope.action_class in self._financial:
            return replace(
                envelope,
                decision="DENIED",
                reason="financial action class is deterministically denied",
                policy_version=self._version,
                authorized_by="policy-engine",
            )
        for rule in self._rules:
            if rule.action_class != envelope.action_class:
                continue
            if rule.scope is not None and rule.scope != envelope.scope:
                continue
            allowed = rule.effect == "ALLOW"
            return replace(
                envelope,
                decision="ALLOWED" if allowed else "DENIED",
                reason=rule.reason or ("allowed by policy" if allowed else "denied by policy"),
                policy_version=self._version,
                authorized_by="policy-engine",
            )
        return replace(
            envelope,
            decision="DENIED",
            reason="default deny: no policy rule matches",
            policy_version=self._version,
            authorized_by="policy-engine",
        )

    def authorize(self, envelope: ActionEnvelope) -> ActionEnvelope:
        if envelope.action_class in self._financial:
            raise FinancialActionDeniedError(
                f"financial action class denied: {envelope.action_class}"
            )
        decided = self.decide(envelope)
        if decided.decision != "ALLOWED":
            raise AuthorityError(decided.reason)
        return decided
