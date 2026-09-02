"""ExecutiveDecisionService — one canonical production decision authority."""
from __future__ import annotations

import uuid
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event
from .calibration import CalibrationState, rebuild_calibration
from .eligibility import (
    WAIT_COMPARATIVE,
    WAIT_NO_ELIGIBLE,
    assess_eligibility,
    compute_resource_contention,
    current_activation_epoch,
    eligible_candidates,
)
from ..owner_directives import active_directives_for, constraint_context
from .goals import StructuredGoal, active_goals
from .predictions import build_prediction, record_prediction
from .scoring import (
    DECISION_VERSION,
    A2GovernorBounds,
    CandidateScore,
    rank_candidates,
)

DecisionKind = Literal["SELECT", "REJECT", "DEFER", "WAIT"]

A2_OUTCOMES = frozenset({"REJECTED", "DEFERRED", "ELIGIBLE", "WAIT", "SUPERSEDED"})
A2_REASON_CODES = frozenset({
    "DUPLICATE", "ALREADY_DONE", "LOW_OWNER_RELEVANCE", "LOW_VALUE",
    "LOW_CONFIDENCE", "STALE", "EXCESSIVE_EFFORT",
    "UNSATISFIED_DEPENDENCY", "CAPABILITY_UNAVAILABLE",
    "REVERSIBILITY_POLICY_BOUNDARY", "BUDGET_EXCEEDED", "WIP_LIMIT",
    "OPPORTUNITY_COST", "PREVIOUSLY_REJECTED", "INSUFFICIENT_EVIDENCE", "SUPERSEDED",
    "READY_FOR_ADMISSION", "WAIT_FOR_EVIDENCE",
})


@dataclass(frozen=True, slots=True)
class ExecutiveWait:
    reason: str
    reconsider_condition: str
    wait_kind: str = WAIT_NO_ELIGIBLE
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
    owner_controls: dict[str, Any] = field(default_factory=dict)


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

    def _safety_block(self, resource_id: str | None = None) -> str | None:
        stop = emergency_stop_reason(self.database, None)
        if stop:
            return f"STOP active: {stop}"
        identities = {"GLOBAL"}
        if resource_id:
            identities.add(str(resource_id))
        veto = matching_veto_reason(self.database, identities)
        if veto:
            return f"veto active: {veto}"
        return None

    def _owner_control_context(self, resource_id: str) -> dict[str, Any]:
        """Return the durable owner controls relevant to this evaluation."""
        targets = {"GLOBAL", str(resource_id)}
        vetoes = active_directives_for(
            self.database,
            command_types=frozenset({"VETO_ACTION", "STOP_MISSION"}),
            targets=targets,
        )
        return {
            "constraints": constraint_context(self.database, targets=targets),
            "vetoes": [record.to_dict() for record in vetoes],
            "veto_reason": matching_veto_reason(self.database, targets),
        }

    def _decision_id_for_loop(self, loop_id: str) -> str:
        return f"exec-{loop_id}"

    def _existing_decision(self, decision_id: str) -> ExecutiveDecision | None:
        with self.database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE idempotency_key = ? LIMIT 1",
                (f"executive.decision_recorded:{decision_id}",),
            ).fetchone()
        return self._decision_from_event(_event_row(row)) if row is not None else None

    def _decision_from_event(self, event: Any) -> ExecutiveDecision:
        payload = event.payload
        kind = str(payload.get("decision") or "WAIT")
        wait_payload = payload.get("wait") or {}
        wait = ExecutiveWait(
            reason=str(wait_payload.get("reason") or payload.get("reason") or ""),
            reconsider_condition=str(wait_payload.get("reconsider_condition") or ""),
            wait_kind=str(wait_payload.get("wait_kind") or WAIT_NO_ELIGIBLE),
            best_candidate_id=wait_payload.get("best_candidate_id"),
            best_score=wait_payload.get("best_score"),
            comparative_rationale=str(wait_payload.get("comparative_rationale") or ""),
        ) if kind == "WAIT" else None
        return ExecutiveDecision(
            kind=kind,  # type: ignore[arg-type]
            decision_id=str(payload.get("decision_id") or ""),
            reason=str(payload.get("reason") or ""),
            candidate_id=payload.get("candidate_id"),
            mission_id=payload.get("mission_id"),
            wait=wait,
            active_goals=tuple(payload.get("active_goals") or ()),
            eligibility=dict(payload.get("eligibility") or {}),
            calibration_version=str(payload.get("calibration_version") or ""),
            rejected=tuple(payload.get("rejected") or ()),
            deferred=tuple(payload.get("deferred") or ()),
            factor_breakdown=dict(payload.get("factor_breakdown") or {}),
            owner_controls=dict(payload.get("owner_controls") or {}),
        )

    def evaluate(
        self,
        *,
        resource_id: str,
        capability: str = "write_registered_worktree_files",
        loop_id: str,
        candidate_limit: int = 20,
        resource_contention: float | None = None,
        a2_mode: bool = False,
        governor: A2GovernorBounds | None = None,
    ) -> ExecutiveDecision:
        """Evaluate eligible candidates and return exactly one decision."""
        decision_id = self._decision_id_for_loop(loop_id)
        existing = self._existing_decision(decision_id)
        if existing is not None:
            return existing

        if resource_contention is None:
            resource_contention = compute_resource_contention(self.database, resource_id)
        owner_controls = self._owner_control_context(resource_id)
        blocked = self._safety_block(resource_id)
        if blocked:
            return self._wait_decision(
                decision_id, reason=blocked,
                reconsider_condition="STOP/veto cleared",
                goals=(),
                owner_controls=owner_controls,
            )

        goals = active_goals(self.database)
        goal_ids = tuple(g.goal_id for g in goals)
        calibration = rebuild_calibration(self.database)

        if not self.activation_epoch and not a2_mode:
            self.ensure_activation()

        # A2 is deliberately pre-admission. Observation candidates are
        # compared without manufacturing the trusted admission event that the
        # execution path requires. The normal Executive path is unchanged.
        rows = (
            _a2_observation_candidates(self.database, limit=candidate_limit)
            if a2_mode else eligible_candidates(
                self.database,
                activation_epoch=self.activation_epoch,
                resource_id=resource_id,
                capability=capability,
                limit=candidate_limit,
            )
        )

        eligibility_audit: dict[str, Any] = {
            "eligible_count": len(rows),
            "owner_controls": owner_controls,
        }
        rejected: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []

        if not rows:
            return self._record_and_return(self._wait_decision(
                decision_id,
                reason=WAIT_NO_ELIGIBLE,
                reconsider_condition=(
                    "fresh bounded initiative observations" if a2_mode else
                    "fresh evidence-backed candidates with governed executive_epoch marker"
                ),
                goals=goal_ids,
                eligibility=eligibility_audit,
                calibration_version=calibration.version,
                wait_kind=WAIT_NO_ELIGIBLE,
                owner_controls=owner_controls,
            ))

        governor = governor or A2GovernorBounds()
        ranked = rank_candidates(
            self.database, rows, goals=goals, calibration=calibration,
            resource_contention=resource_contention,
            a2_mode=a2_mode,
            governor=governor,
        )

        if a2_mode:
            viable: list[CandidateScore] = []
            for score in ranked:
                disposition = a2_precondition(score)
                if disposition is None:
                    viable.append(score)
                    continue
                target = deferred if disposition[0] == "DEFERRED" else rejected
                target.append(_a2_disposition_record(score, *disposition))
            ranked = viable
            if not ranked:
                return self._record_and_return(self._wait_decision(
                    decision_id,
                    reason="all candidates failed rejection-first A2 preconditions",
                    reconsider_condition="materially new evidence or a resolved dependency/capability",
                    goals=goal_ids,
                    eligibility=eligibility_audit,
                    calibration_version=calibration.version,
                    rejected=tuple(rejected),
                    deferred=tuple(deferred),
                    wait_kind=WAIT_COMPARATIVE,
                    owner_controls=owner_controls,
                ))

        best = ranked[0]
        comparative_rationale = ""
        if len(ranked) >= 2:
            first, second = ranked[0], ranked[1]
            first_row = next(r for r in rows if r["candidate_id"] == first.candidate_id)
            second_row = next(r for r in rows if r["candidate_id"] == second.candidate_id)
            pair = {str(first_row.get("candidate_type") or ""),
                    str(second_row.get("candidate_type") or "")}
            if pair == {"research", "build"}:
                research = first if first_row.get("candidate_type") == "research" else second
                build = first if first_row.get("candidate_type") == "build" else second
                research_row = first_row if first_row.get("candidate_type") == "research" else second_row
                build_row = first_row if first_row.get("candidate_type") == "build" else second_row
                from ..v3_scoring import derive_dimensions, load_active_policy
                def _evidence_count(row: dict[str, Any]) -> int:
                    try:
                        value = json.loads(str(row.get("evidence_json") or "[]"))
                    except (TypeError, ValueError):
                        value = []
                    return len(value) if isinstance(value, list) else 0
                research_dims = derive_dimensions(
                    str(research_row.get("objective") or ""),
                    evidence_count=_evidence_count(research_row))
                build_dims = derive_dimensions(
                    str(build_row.get("objective") or ""),
                    evidence_count=_evidence_count(build_row))
                try:
                    policy = load_active_policy(self.database)
                    info_threshold = float(policy["thresholds"][
                        "min_information_gain_for_research_override"])
                except (KeyError, TypeError, ValueError):
                    info_threshold = 0.55
                if (build_dims["confidence"] < 0.40
                        and research_dims["information_gain"] >= info_threshold
                        and abs(research.composite - build.composite) <= 0.08):
                    best = research
                    comparative_rationale = (
                        "research override: build confidence "
                        f"{build_dims['confidence']:.3f} < 0.40 and research "
                        f"information gain {research_dims['information_gain']:.3f} "
                        f">= {info_threshold:.3f}; within 0.08 of build")
        # Every runner-up gets an explicit, recorded disposition so the
        # decision is auditable: why each alternative lost.
        for score in ranked[1:]:
            if score.composite < self.min_action_score:
                rejected.append(_a2_disposition_record(
                    score, "REJECTED", "LOW_VALUE",
                    f"below min_action_score {self.min_action_score}",
                ) if a2_mode else {
                    "candidate_id": score.candidate_id,
                    "composite": score.composite,
                    "reason": f"below min_action_score {self.min_action_score}",
                })
            elif score.goal_fit < 0.05 and best.goal_fit > 0.2:
                rejected.append(_a2_disposition_record(
                    score, "REJECTED", "LOW_OWNER_RELEVANCE",
                    "low goal alignment vs selected candidate",
                ) if a2_mode else {
                    "candidate_id": score.candidate_id,
                    "composite": score.composite,
                    "reason": "low goal alignment vs selected candidate",
                })
            else:
                detail = (
                    f"ranked below selected candidate "
                    f"({score.composite:.3f} < {best.composite:.3f})"
                )
                rejected.append(_a2_disposition_record(
                    score, "REJECTED", "OPPORTUNITY_COST", detail,
                ) if a2_mode else {
                    "candidate_id": score.candidate_id,
                    "composite": score.composite,
                    "reason": detail,
                })

        # Comparative WAIT: candidates exist but acting is worse than waiting.
        action_threshold = max(
            self.min_action_score,
            governor.do_nothing_score if a2_mode else self.min_action_score,
        )
        wait_threshold = action_threshold + self.wait_margin
        if best.composite < action_threshold:
            for score in ranked:
                if a2_mode and any(
                    item.get("candidate_id") == score.candidate_id
                    for item in (*rejected, *deferred)
                ):
                    continue
                row = next(r for r in rows if r["candidate_id"] == score.candidate_id)
                if a2_mode:
                    detail = (
                        f"below action threshold "
                        f"({best.composite:.3f} < {action_threshold})"
                    )
                    rejected.append(_a2_disposition_record(
                        score, "REJECTED", "OPPORTUNITY_COST", detail,
                    ))
                    continue
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
                    detail = f"below action threshold ({best.composite:.3f} < {action_threshold})"
                    rejected.append(_a2_disposition_record(
                        score, "REJECTED", "OPPORTUNITY_COST", detail,
                    ) if a2_mode else {
                        "candidate_id": score.candidate_id,
                        "composite": score.composite,
                        "reason": detail,
                    })
            return self._record_and_return(self._wait_decision(
                decision_id,
                reason=(
                    f"best candidate {best.candidate_id} scored {best.composite:.3f} "
                    f"below action threshold {action_threshold} — waiting beats acting"
                ),
                reconsider_condition=f"eligible candidate scores >= {action_threshold}",
                goals=goal_ids,
                best=best,
                eligibility={**eligibility_audit, "best_score": best.composite},
                calibration_version=calibration.version,
                rejected=tuple(rejected),
                deferred=tuple(deferred),
                wait_kind=WAIT_COMPARATIVE,
                owner_controls=owner_controls,
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
                wait_kind=WAIT_COMPARATIVE,
                owner_controls=owner_controls,
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
        if not self._prediction_exists(decision_id):
            record_prediction(
                self.database,
                decision_id=decision_id,
                candidate_id=best.candidate_id,
                prediction=prediction,
                source_id=self.source_id,
            )

        factor_breakdown = _factor_dict(best)
        if comparative_rationale:
            factor_breakdown["comparative_rationale"] = comparative_rationale
        factor_breakdown["owner_controls"] = owner_controls
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
            factor_breakdown=factor_breakdown,
            active_goals=goal_ids,
            eligibility=eligibility_audit,
            calibration_version=calibration.version,
            owner_controls=owner_controls,
        )
        return self._record_and_return(decision)

    def evaluate_a2(
        self,
        *,
        resource_id: str,
        loop_id: str,
        capability: str = "write_registered_worktree_files",
        governor: A2GovernorBounds | None = None,
        candidate_limit: int | None = None,
        resource_contention: float | None = None,
    ) -> ExecutiveDecision:
        """Run rejection-first judgment and stop before objective/mission creation."""

        bounds = governor or A2GovernorBounds()
        decision = self.evaluate(
            resource_id=resource_id,
            capability=capability,
            loop_id=loop_id,
            candidate_limit=candidate_limit or bounds.candidate_limit,
            resource_contention=resource_contention,
            a2_mode=True,
            governor=bounds,
        )
        self._record_a2_judgments(decision, cycle_id=loop_id)
        return decision

    def preview_a2(
        self,
        *,
        resource_id: str,
        governor: A2GovernorBounds | None = None,
        candidate_limit: int | None = None,
        resource_contention: float | None = None,
    ) -> tuple[CandidateScore, ...]:
        """Rank pre-admission candidates without recording a final decision.

        The initiative cycle may use this bounded preview to select research
        finalists. A later :meth:`evaluate_a2` recomputes scores from current
        research truth and remains the only final chooser.
        """

        if self._safety_block(resource_id):
            return ()
        bounds = governor or A2GovernorBounds()
        rows = _a2_observation_candidates(
            self.database,
            limit=candidate_limit or bounds.candidate_limit,
        )
        if resource_contention is None:
            resource_contention = compute_resource_contention(
                self.database, resource_id,
            )
        return tuple(rank_candidates(
            self.database,
            rows,
            goals=active_goals(self.database),
            calibration=rebuild_calibration(self.database),
            resource_contention=resource_contention,
            a2_mode=True,
            governor=bounds,
        ))

    def _prediction_exists(self, decision_id: str) -> bool:
        with self.database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT 1 FROM events WHERE event_type = 'executive.prediction_recorded' "
                "AND json_extract(payload_json, '$.decision_id') = ? LIMIT 1",
                (decision_id,),
            ).fetchone()
        return row is not None

    def _record_a2_judgments(
        self,
        decision: ExecutiveDecision,
        *,
        cycle_id: str,
    ) -> None:
        if _a2_judgments_exist(self.database, decision.decision_id):
            return
        decided_at = _decision_recorded_at(self.database, decision.decision_id)
        dispositions = [*decision.rejected, *decision.deferred]
        if decision.candidate_id and decision.kind == "SELECT":
            dispositions.append(_a2_disposition_record(
                decision.score,
                "ELIGIBLE",
                "READY_FOR_ADMISSION",
                "highest-ranked survivor stopped at the A2 admission boundary",
            ) if decision.score is not None else {
                "candidate_id": decision.candidate_id,
                "outcome": "ELIGIBLE",
                "reason_code": "READY_FOR_ADMISSION",
                "reason": "highest-ranked survivor stopped at the A2 admission boundary",
                "composite": None,
                "factors": {},
            })
        elif decision.wait is not None and decision.wait.best_candidate_id and not any(
            row.get("candidate_id") == decision.wait.best_candidate_id for row in dispositions
        ):
            dispositions.append({
                "candidate_id": decision.wait.best_candidate_id,
                "outcome": "WAIT",
                "reason_code": "WAIT_FOR_EVIDENCE",
                "reason": decision.reason,
                "composite": decision.wait.best_score,
                "factors": decision.factor_breakdown,
            })
        ranked_dispositions = sorted(
            dispositions,
            key=lambda row: (
                -(float(row["composite"]) if row.get("composite") is not None else -1.0),
                str(row.get("candidate_id") or ""),
            ),
        )
        rank_by_candidate = {
            str(row.get("candidate_id") or ""): rank
            for rank, row in enumerate(ranked_dispositions, start=1)
        }
        do_nothing_won = decision.kind == "WAIT" and decision.wait is not None
        events: list[Event] = []
        for row in dispositions:
            candidate_id = str(row.get("candidate_id") or "")
            if not candidate_id:
                continue
            candidate = self.database.row("candidates", "candidate_id", candidate_id) or {}
            outcome = str(row.get("outcome") or (
                "DEFERRED" if row in decision.deferred else "REJECTED"
            )).upper()
            reason_code = str(row.get("reason_code") or "OPPORTUNITY_COST").upper()
            if outcome not in A2_OUTCOMES or reason_code not in A2_REASON_CODES:
                raise ValueError(f"invalid A2 disposition: {outcome}/{reason_code}")
            judgment_id = _a2_judgment_id(decision.decision_id, candidate_id, outcome, reason_code)
            try:
                evidence = json.loads(str(candidate.get("evidence_json") or "[]"))
            except (TypeError, ValueError):
                evidence = []
            payload = {
                "judgment_id": judgment_id,
                "candidate_id": candidate_id,
                "fingerprint": candidate.get("fingerprint"),
                "decision_id": decision.decision_id,
                "cycle_id": cycle_id,
                "stage": "FINAL",
                "outcome": outcome,
                "reason_code": reason_code,
                "reason_detail": str(row.get("reason") or decision.reason),
                "score": row.get("composite"),
                "factors": dict(row.get("factors") or {}),
                "evidence": evidence if isinstance(evidence, list) else [],
                "rank": rank_by_candidate.get(candidate_id),
                "do_nothing_won": do_nothing_won,
                "decided_at": decided_at,
            }
            events.append(Event.create(
                "discovery.candidate_judged", payload,
                source_type="supervisor", source_id="v11-a2-judgment",
                idempotency_key=f"discovery.candidate_judged:{judgment_id}",
            ))
        if events:
            self.database.append_events(events)

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
        wait_kind: str = WAIT_NO_ELIGIBLE,
        owner_controls: dict[str, Any] | None = None,
    ) -> ExecutiveDecision:
        comparative = (
            wait_kind == WAIT_COMPARATIVE
            and best is not None
            and reason != WAIT_NO_ELIGIBLE
        )
        wait = ExecutiveWait(
            reason=reason,
            reconsider_condition=reconsider_condition,
            wait_kind=wait_kind,
            best_candidate_id=best.candidate_id if best else None,
            best_score=best.composite if best else None,
            comparative_rationale=reason if comparative else "",
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
            owner_controls=owner_controls or {},
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
            "owner_controls": decision.owner_controls,
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
                "wait_kind": decision.wait.wait_kind,
                "best_candidate_id": decision.wait.best_candidate_id,
                "best_score": decision.wait.best_score,
                "comparative_rationale": decision.wait.comparative_rationale,
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


def _event_row(row: Any) -> Any:
    """Adapt a raw events row to the small event interface used above."""
    from types import SimpleNamespace
    payload = json.loads(str(row["payload_json"]))
    return SimpleNamespace(payload=payload)


def _score_factor(score: CandidateScore, name: str) -> float | None:
    factor = next((item for item in score.factors if item.name == name), None)
    return factor.value if factor is not None else None


def _a2_disposition_record(
    score: CandidateScore | None,
    outcome: str,
    reason_code: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "candidate_id": score.candidate_id if score is not None else None,
        "outcome": outcome,
        "reason_code": reason_code,
        "reason": reason,
        "composite": score.composite if score is not None else None,
        "factors": _factor_dict(score) if score is not None else {},
    }


def a2_precondition(score: CandidateScore) -> tuple[str, str, str] | None:
    """Return terminal/deferred disposition before comparative ranking."""
    meta = score.a2_metadata
    if meta.get("duplicate_of"):
        return "REJECTED", "DUPLICATE", f"duplicate of {meta['duplicate_of']}"
    history = meta.get("history") or {}
    if history.get("completed"):
        return "REJECTED", "ALREADY_DONE", f"equivalent completed work: {history['completed'][0]}"
    if history.get("active"):
        return "DEFERRED", "WIP_LIMIT", f"equivalent work is already active: {history['active'][0]}"
    if history.get("rejected"):
        return "REJECTED", "PREVIOUSLY_REJECTED", f"equivalent work was previously rejected: {history['rejected'][0]}"
    if history.get("superseded"):
        return "REJECTED", "SUPERSEDED", f"equivalent work was superseded: {history['superseded'][0]}"
    if meta.get("expired"):
        return "REJECTED", "STALE", f"candidate expired at {meta.get('expires_at')}"
    owner = meta.get("owner_context") or {}
    rejection_overlap = owner.get("standing_rejection_overlap")
    if rejection_overlap is not None and float(rejection_overlap) >= 0.45:
        return (
            "REJECTED", "REVERSIBILITY_POLICY_BOUNDARY",
            f"matches a provenanced standing rejection ({float(rejection_overlap):.3f})",
        )
    capability_states = meta.get("capability_states") or {}
    unattended_states = meta.get("unattended_states") or {}
    unavailable = [
        capability_id for capability_id, state in capability_states.items()
        if state != "READY" or unattended_states.get(capability_id) != "ALLOWED"
    ]
    if unavailable:
        return (
            "DEFERRED", "CAPABILITY_UNAVAILABLE",
            "canonical Fabric readiness unavailable: " + ", ".join(
                f"{capability_id}={capability_states.get(capability_id)}/"
                f"{unattended_states.get(capability_id)}" for capability_id in unavailable
            ),
        )
    dependency = meta.get("dependencies") or {}
    states = dependency.get("states") or {}
    blocked = [key for key, value in states.items() if value not in {"EXECUTED", "COMPLETED", "ACCEPTED", "PASSED"}]
    if blocked:
        return "DEFERRED", "UNSATISFIED_DEPENDENCY", f"unsatisfied dependencies: {', '.join(blocked)}"
    if meta.get("autonomous_wip") is None:
        return "DEFERRED", "INSUFFICIENT_EVIDENCE", "autonomous WIP state is unavailable"
    if int(meta["autonomous_wip"]) >= int(meta.get("autonomous_wip_cap") or 1):
        return "DEFERRED", "WIP_LIMIT", "autonomous WIP cap 1 is already occupied"
    requested = meta.get("research_budget_requested")
    if requested is not None:
        try:
            if float(requested) > float(meta.get("research_budget_per_candidate") or 0.0):
                return "DEFERRED", "BUDGET_EXCEEDED", "candidate research estimate exceeds its A2 budget"
        except (TypeError, ValueError, OverflowError):
            return "DEFERRED", "INSUFFICIENT_EVIDENCE", "research budget estimate is malformed"
    goal_relevance = owner.get("goal_relevance")
    project_relevance = owner.get("project_relevance")
    if (
        goal_relevance is not None and project_relevance is not None
        and max(float(goal_relevance), float(project_relevance), score.goal_fit) < 0.08
    ):
        return "REJECTED", "LOW_OWNER_RELEVANCE", "no evidence-backed owner goal or active-project relevance"
    confidence = _score_factor(score, "confidence")
    if confidence is None:
        return "REJECTED", "INSUFFICIENT_EVIDENCE", "candidate confidence is unknown"
    if confidence < 0.20:
        return "REJECTED", "LOW_CONFIDENCE", f"candidate confidence {confidence:.3f} is below floor"
    effort = _score_factor(score, "effort")
    if effort is not None and effort < 0.10:
        return "REJECTED", "EXCESSIVE_EFFORT", "estimated effort exceeds the bounded A2 class"
    reversibility = _score_factor(score, "reversibility")
    if reversibility is not None and reversibility < 0.30:
        return "REJECTED", "REVERSIBILITY_POLICY_BOUNDARY", "candidate is not sufficiently reversible"
    return None


def _a2_judgment_id(
    decision_id: str,
    candidate_id: str,
    outcome: str,
    reason_code: str,
) -> str:
    import hashlib
    material = "\x1f".join((decision_id, candidate_id, outcome, reason_code))
    return f"judgment-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


def _a2_observation_candidates(
    database: FoundryDatabase,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Return bounded discovery candidates before Executive admission."""

    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT * FROM candidates "
            "WHERE source = 'initiative_observation' "
            "AND status IN ('NEW', 'SCORED', 'ELIGIBLE') "
            "ORDER BY rowid DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(row) for row in rows]


def _a2_judgments_exist(database: FoundryDatabase, decision_id: str) -> bool:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT 1 FROM candidate_judgments WHERE decision_id = ? LIMIT 1",
            (decision_id,),
        ).fetchone()
    return row is not None


def _decision_recorded_at(database: FoundryDatabase, decision_id: str) -> str:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT recorded_at FROM events WHERE idempotency_key = ? LIMIT 1",
            (f"executive.decision_recorded:{decision_id}",),
        ).fetchone()
    if row is not None:
        return str(row[0])
    from ..clock import isoformat
    return isoformat()
