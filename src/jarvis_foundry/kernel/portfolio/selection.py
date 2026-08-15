"""Deterministic multi-project next-action selection with honest WAITING."""

from __future__ import annotations

from dataclasses import replace

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.portfolio.budget import Budget
from jarvis_foundry.kernel.portfolio.control import ControlState, PortfolioControl
from jarvis_foundry.kernel.portfolio.errors import TargetMismatchError
from jarvis_foundry.kernel.portfolio.provenance import json_hash
from jarvis_foundry.kernel.portfolio.records import (
    AlternativeScore,
    SelectionDecision,
)
from jarvis_foundry.kernel.portfolio.scoring import ScoreWeights, score_alternative
from jarvis_foundry.kernel.portfolio.store import PortfolioStore

MIN_VALUABLE_SCORE = 0.20


class SelectionService:
    def __init__(
        self,
        store: PortfolioStore,
        *,
        budget: Budget | None = None,
        clock: Clock | None = None,
        weights: ScoreWeights | None = None,
    ) -> None:
        self.store = store
        self.budget = budget
        self._clock = clock if clock is not None else SystemClock()
        self._weights = weights

    def run(self) -> SelectionDecision:
        now = self._clock.now_utc()
        control = PortfolioControl(self.store)
        control_state = control.state()
        portfolio_hash = json_hash(
            {
                "projects": [
                    project.to_dict()
                    for project in sorted(self.store.projects(), key=lambda p: p.project_id)
                ],
                "candidates": [
                    candidate.to_dict()
                    for candidate in sorted(self.store.candidates(), key=lambda c: c.candidate_id)
                ],
            }
        )
        budget_key = self.budget.budget_key if self.budget is not None else "unbounded"
        decision_key = f"decision:{portfolio_hash}:{control_state.control_hash}:{budget_key}"
        decision_id = f"decision-{canonical_hash(decision_key)[:16]}"
        existing = self.store.latest("selection_decision", decision_id)
        if existing is not None and isinstance(existing, SelectionDecision):
            return existing

        if self.budget is not None and self.budget.max_calls <= 0:
            return self._waiting(
                decision_id=decision_id,
                decision_key=decision_key,
                portfolio_hash=portfolio_hash,
                control_state=control_state,
                budget_key=budget_key,
                now=now,
                reason="budget exhausted; no action can be safely selected",
                alternatives=(),
            )
        if control_state.stop_active:
            return self._waiting(
                decision_id=decision_id,
                decision_key=decision_key,
                portfolio_hash=portfolio_hash,
                control_state=control_state,
                budget_key=budget_key,
                now=now,
                reason="owner STOP active; selection blocked",
                alternatives=(),
            )

        alternatives: list[AlternativeScore] = []
        for candidate in sorted(self.store.candidates(), key=lambda c: c.candidate_id):
            project = self.store.project(candidate.project_id)
            if project is None:
                continue
            if candidate.status != "READY" or project.status != "ACTIVE":
                continue
            if project.project_id in control_state.paused_projects:
                continue
            if candidate.candidate_id in control_state.vetoed_candidates:
                continue
            if self.budget is not None and candidate.budget_effects > self.budget.effects_limit:
                continue
            scored = score_alternative(project, candidate, self._weights)
            boost = control.priority_boost(candidate.candidate_id)
            if boost:
                dimensions = dict(scored.dimensions)
                dimensions["priority_boost"] = float(boost)
                scored = replace(
                    scored,
                    total_score=round(min(1.0, scored.total_score + 0.05 * boost), 4),
                    dimensions=dimensions,
                    reason=scored.reason + f"; owner priority boost {boost}",
                )
            # Every eligible alternative is recorded so decisions expose the
            # full tradeoff set (J04-S01/J04-U02). Zero-value and
            # below-threshold candidates are included in the decision record
            # with their honest scores; only the winner must be valuable.
            alternatives.append(scored)
        valuable = [
            alt
            for alt in alternatives
            if (alt_candidate := self.store.candidate(alt.candidate_id)) is not None
            and alt_candidate.expected_value > 0.0
            and alt.total_score >= MIN_VALUABLE_SCORE
        ]
        alternatives.sort(key=lambda item: (-item.total_score, item.candidate_id))
        if not valuable:
            return self._waiting(
                decision_id=decision_id,
                decision_key=decision_key,
                portfolio_hash=portfolio_hash,
                control_state=control_state,
                budget_key=budget_key,
                now=now,
                reason="no evidence-backed valuable action is eligible; honest WAITING",
                alternatives=tuple(alternatives),
            )

        valuable.sort(key=lambda item: (-item.total_score, item.candidate_id))
        winner = valuable[0]
        winner_candidate = self.store.candidate(winner.candidate_id)
        project = self.store.project(winner_candidate.project_id) if winner_candidate else None
        uncertainty = {alt.candidate_id: alt.uncertainty for alt in alternatives}
        decision = SelectionDecision(
            decision_id=decision_id,
            portfolio_hash=portfolio_hash,
            control_hash=control_state.control_hash,
            budget_key=budget_key,
            selected_candidate_id=winner.candidate_id,
            project_id=project.project_id if project else None,
            canonical_target=winner_candidate.canonical_target if winner_candidate else None,
            status="SELECTED",
            reason=(
                f"selected {winner.candidate_id} at score {winner.total_score:.4f} "
                f"from {len(alternatives)} scored alternatives; target bound to "
                f"{winner_candidate.canonical_target if winner_candidate else 'none'}"
            ),
            alternatives=tuple(alternatives),
            uncertainty=uncertainty,
            supersedes=None,
            created_at=now,
            idempotency_key=decision_key,
        )
        self.store.append_decision(decision, idempotency_key=decision_key)
        return decision

    def _waiting(
        self,
        *,
        decision_id: str,
        decision_key: str,
        portfolio_hash: str,
        control_state: ControlState,
        budget_key: str,
        now: str,
        reason: str,
        alternatives: tuple[AlternativeScore, ...],
    ) -> SelectionDecision:
        decision = SelectionDecision(
            decision_id=decision_id,
            portfolio_hash=portfolio_hash,
            control_hash=control_state.control_hash,
            budget_key=budget_key,
            selected_candidate_id=None,
            project_id=None,
            canonical_target=None,
            status="WAITING",
            reason=reason,
            alternatives=alternatives,
            uncertainty={alt.candidate_id: alt.uncertainty for alt in alternatives},
            supersedes=None,
            created_at=now,
            idempotency_key=decision_key,
        )
        self.store.append_decision(decision, idempotency_key=decision_key)
        return decision

    def validate_dispatch(self, decision: SelectionDecision, expected_target: str) -> None:
        if decision.canonical_target != expected_target:
            raise TargetMismatchError(
                f"selected target {decision.canonical_target!r} differs from dispatch target {expected_target!r}"
            )
