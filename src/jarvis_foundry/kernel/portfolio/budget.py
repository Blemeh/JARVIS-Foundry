"""Deterministic rate, token, effect, and cost budgets."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis_foundry.kernel.portfolio.errors import BudgetExceeded
from jarvis_foundry.kernel.portfolio.records import BudgetState


@dataclass(frozen=True)
class Budget:
    budget_key: str
    max_calls: int
    max_tokens: int
    max_cost_usd: float
    max_effects: int | None = None
    used_calls: int = 0
    used_tokens: int = 0
    used_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.max_calls < 0 or self.max_tokens < 0 or self.max_cost_usd < 0:
            raise ValueError("budget limits cannot be negative")

    @property
    def effects_limit(self) -> int:
        return self.max_effects if self.max_effects is not None else self.max_calls

    def consume(self, *, calls: int = 1, tokens: int = 0, cost_usd: float = 0.0) -> None:
        if self.used_calls + calls > self.max_calls:
            raise BudgetExceeded(
                f"budget {self.budget_key}: call limit {self.used_calls}/{self.max_calls}"
            )
        if self.used_tokens + tokens > self.max_tokens:
            raise BudgetExceeded(
                f"budget {self.budget_key}: token limit {self.used_tokens}/{self.max_tokens}"
            )
        if round(self.used_cost_usd + cost_usd, 6) > self.max_cost_usd:
            raise BudgetExceeded(
                f"budget {self.budget_key}: cost limit {self.used_cost_usd + cost_usd}/{self.max_cost_usd}"
            )
        object.__setattr__(self, "used_calls", self.used_calls + calls)
        object.__setattr__(self, "used_tokens", self.used_tokens + tokens)
        object.__setattr__(self, "used_cost_usd", round(self.used_cost_usd + cost_usd, 6))

    def state(self) -> BudgetState:
        return BudgetState(
            budget_key=self.budget_key,
            max_calls=self.max_calls,
            max_tokens=self.max_tokens,
            max_cost_usd=self.max_cost_usd,
            max_effects=self.effects_limit,
            used_calls=self.used_calls,
            used_tokens=self.used_tokens,
            used_cost_usd=self.used_cost_usd,
        )


__all__ = ["Budget", "BudgetExceeded", "BudgetState"]
