"""ExecutiveDecisionService — one canonical production decision authority."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event
from .calibration import CalibrationState, rebuild_calibration
from .eligibility import (
    assess_eligibility,
    current_activation_epoch,
    eligible_candidates,
)
from .goals import StructuredGoal, active_goals
from .predictions import build_prediction, record_prediction
from .scoring import DECISION_VERSION, CandidateScore, rank_candidates

DecisionKind = Literal["SELECT", "REJECT", "DEFER", "WAIT"]


@dataclass(frozen=True, slots=True)
class ExecutiveWait:
    reason: str
    reconsider_condition: str
    best_candidate_id: str | None = None
    best_score: float | None = None
    comparative_rationale: str = ""


@dataclass(frozen=True, slots=True)
class ExecutiveDecision:
    kind: DecisionKind
    decision_id: str
    reason: str
    candidate_id: str | None = None
    mission_id: str | None = None
    score: CandidateScore | None = None
    rejected: tuple[dict[str, Any], ...] = ()
    deferred: tuple[dict[str, Any], ...] = ()
    wait: ExecutiveWait | None = None
    factor_breakdown: dict[str, Any] = field(default_factory=dict)
    active_goals: tuple[str, ...] = ()
    eligibility: dict[str, Any] = field(default_factory=dict)
    calibration_version: str = ""


class ExecutiveDecisionService:
    """Bounded deterministic executive evaluation."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        activation_epoch: str | None = None,
        min_action_score: float = 0.40,
        wait_margin: float = 0.05,
        source_id: str = "executive",
    ) -> None:
        self.database = database
        self.activation_epoch = activation_epoch or current_activation_epoch(database)
        self.min_action_score = min_action_score
        self.wait_margin = wait_margin
        self.source_id = source_id

    def ensure_activation(self, epoch: str | None = None) -> str:
        """Record activation epoch if not yet set (idempotent per epoch)."""
        epoch = epoch or f"epoch-{uuid.uuid4().hex[:12]}"
        if self.activation_epoch:
            return self.activation_epoch
        self.database.append(Event.create(
            "executive.activation_recorded",
            {"epoch": epoch, "decision_version": DECISION_VERSION},
            source_type="supervisor",
            source_id=self.source_id,
            idempotency_key=f"executive.activation_recorded:{epoch}",
        ))
        self.activation_epoch = epoch
        return epoch

    def _safety_block(self) -> str | None:
        stop = emergency_stop_reason(self.database, None)
        if stop:
            return f"STOP active: {stop}"
        veto = matching_veto_reason(self.database, {"GLOBAL"})
        if veto:
            return f"veto active: {veto}"
        return None

    def evaluate(
        self,
        *,
        resource_id: str,
        capability: str = "write_registered_worktree_files",
        loop_id: str,
        candidate_limit: int = 20,
        resource_contention: float = 0.0,
    ) -> ExecutiveDecision:
        """Evaluate eligible candidates and return exactly one decision."""
        decision_id = f"exec-{loop_id}-{uuid.uuid4().hex[:8]}"
        blocked = self._safety_block()
        if blocked:
            return self._wait_decision(
                decision_id, reason=blocked,
                reconsider_condition="STOP/veto cleared",
                goals=(),
            )

        goals = active_goals(self.database)
        goal_ids = tuple(g.goal_id for g in goals)
        calibration = rebuild_calibration(self.database)

        if not self.activation_epoch:
            self.ensure_activation()

        rows = eligible_candidates(
            self.database,
            activation_epoch=self.activation_epoch,
            resource_id=resource_id,
            capability=capability,
            limit=candidate_limit,
        )

        eligibility_audit: dict[str, Any] = {"eligible_count": len(rows)}
        rejected: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []

        if not rows:
            return self._record_and_return(self._wait_decision(
                decision_id,
                reason="no eligible candidates after historical firewall",
                reconsider_condition="fresh evidence-backed candidates with executive_epoch marker",
                goals=goal_ids,
                eligibility=eligibility_audit,
                calibration_version=calibration.version,
            ))

        ranked = rank_candidates(
            self.database, rows, goals=goals, calibration=calibration,
            resource_contention=resource_contention,
        )

        best = ranked[0]
        for score in ranked[1:]:
            if score.composite < self.min_action_score:
                rejected.append({
                    "candidate_id": score.candidate_id,
                    "composite": score.composite,
                    "reason": f"below min_action_score {self.min_action_score}",
                })
            elif score.goal_fit < 0.05 and best.goal_fit > 0.2:
                rejected.append({
                    "candidate_id": score.candidate_id,
                    "composite": score.composite,
                    "reason": "low goal alignment vs selected candidate",
                })

        # Comparative WAIT: candidates exist but acting is worse than waiting.
        wait_threshold = self.min_action_score + self.wait_margin
        if best.composite < self.min_action_score:
            for score in ranked:
                row = next(r for r in rows if r["candidate_id"] == score.candidate_id)
                result = assess_eligibility(
                    self.database, row,
                    activation_epoch=self.activation_epoch,
                    resource_id=resource_id,
                    capability=capability,
                )
                if not result.eligible and result.reason == "stale_historical":
                    deferred.append({
                        "candidate_id": score.candidate_id,
                        "reason": result.reason,
                    })
                else:
                    rejected.append({
                        "candidate_id": score.candidate_id,
                        "composite": score.composite,
                        "reason": f"below action threshold ({best.composite:.3f} < {self.min_action_score})",
                    })
            return self._record_and_return(self._wait_decision(
                decision_id,
                reason=(
                    f"best candidate {best.candidate_id} scored {best.composite:.3f} "
                    f"below min_action_score {self.min_action_score} — waiting beats acting"
                ),
                reconsider_condition=f"eligible candidate scores >= {self.min_action_score}",
                goals=goal_ids,
                best=best,
                eligibility={**eligibility_audit, "best_score": best.composite},
                calibration_version=calibration.version,
                rejected=tuple(rejected),
                deferred=tuple(deferred),
            ))

        if best.composite < wait_threshold and best.goal_fit < 0.15:
            return self._record_and_return(self._wait_decision(
                decision_id,
                reason="evidence too weak for goal-aligned action",
                reconsider_condition="stronger goal-aligned evidence",
                goals=goal_ids,
                best=best,
                eligibility=eligibility_audit,
                calibration_version=calibration.version,
            ))

        # Keyword bait rejection: superficially attractive but no goal fit.
        if best.goal_fit < 0.05 and goals and len(ranked) > 1:
            better = next((s for s in ranked if s.goal_fit >= 0.15), None)
            if better is not None:
                rejected.append({
                    "candidate_id": best.candidate_id,
                    "composite": best.composite,
                    "reason": "keyword bait without goal alignment",
                })
                best = better

        prediction = build_prediction(best)
        record_prediction(
            self.database,
            decision_id=decision_id,
            candidate_id=best.candidate_id,
            prediction=prediction,
            source_id=self.source_id,
        )

        decision = ExecutiveDecision(
            kind="SELECT",
            decision_id=decision_id,
            reason=(
                f"selected {best.candidate_id} (composite={best.composite:.3f}) "
                f"over {len(ranked)-1} alternatives; goals={list(best.compared_goals)}"
            ),
            candidate_id=best.candidate_id,
            score=best,
            rejected=tuple(rejected),
            deferred=tuple(deferred),
            factor_breakdown=_factor_dict(best),
            active_goals=goal_ids,
            eligibility=eligibility_audit,
            calibration_version=calibration.version,
        )
        return self._record_and_return(decision)

    def _wait_decision(
        self,
        decision_id: str,
        *,
        reason: str,
        reconsider_condition: str,
        goals: tuple[str, ...],
        best: CandidateScore | None = None,
        eligibility: dict[str, Any] | None = None,
        calibration_version: str = "",
        rejected: tuple[dict[str, Any], ...] = (),
        deferred: tuple[dict[str, Any], ...] = (),
    ) -> ExecutiveDecision:
        wait = ExecutiveWait(
            reason=reason,
            reconsider_condition=reconsider_condition,
            best_candidate_id=best.candidate_id if best else None,
            best_score=best.composite if best else None,
            comparative_rationale=reason,
        )
        return ExecutiveDecision(
            kind="WAIT",
            decision_id=decision_id,
            reason=reason,
            wait=wait,
            score=best,
            rejected=rejected,
            deferred=deferred,
            active_goals=goals,
            eligibility=eligibility or {},
            calibration_version=calibration_version,
        )

    def _record_and_return(self, decision: ExecutiveDecision) -> ExecutiveDecision:
        payload: dict[str, Any] = {
            "decision_id": decision.decision_id,
            "decision": decision.kind,
            "reason": decision.reason,
            "decision_version": DECISION_VERSION,
            "active_goals": list(decision.active_goals),
            "candidate_id": decision.candidate_id,
            "mission_id": decision.mission_id,
            "factor_breakdown": decision.factor_breakdown,
            "eligibility": decision.eligibility,
            "rejected": list(decision.rejected),
            "deferred": list(decision.deferred),
            "calibration_version": decision.calibration_version,
        }
        if decision.score is not None:
            payload["selected_score"] = {
                "candidate_id": decision.score.candidate_id,
                "composite": decision.score.composite,
                "goal_fit": decision.score.goal_fit,
                "calibration_adjustment": decision.score.calibration_adjustment,
            }
        if decision.wait is not None:
            payload["wait"] = {
                "reason": decision.wait.reason,
                "reconsider_condition": decision.wait.reconsider_condition,
                "best_candidate_id": decision.wait.best_candidate_id,
                "best_score": decision.wait.best_score,
            }
        self.database.append(Event.create(
            "executive.decision_recorded",
            payload,
            source_type="supervisor",
            source_id=self.source_id,
            idempotency_key=f"executive.decision_recorded:{decision.decision_id}",
        ))
        return decision


def _factor_dict(score: CandidateScore) -> dict[str, Any]:
    return {
        f.name: {"value": f.value, "kind": f.kind, "contribution": f.contribution}
        for f in score.factors
    }
