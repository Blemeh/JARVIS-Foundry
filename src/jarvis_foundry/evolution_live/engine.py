"""Supervisor-owned evolution engine with durable lifecycle transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jarvis_foundry.evolution_live.lifecycle import (
    LIFECYCLE_EVENTS,
    RISK_ORDER,
    LifecycleError,
    LifecycleMachine,
    require_supervisor,
    risk_index,
    validate_criteria,
    validate_metrics,
    validate_surfaces,
)
from jarvis_foundry.evolution_live.store import EvolutionStore, json_hash
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ledger import AppendResult


@dataclass(frozen=True)
class TransitionResult:
    event: LedgerEvent
    created: bool
    cycle_state: dict[str, Any] | None


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise LifecycleError(f"{label} must be a non-empty string")
    if not value.strip():
        raise LifecycleError(f"{label} must be a non-empty string")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LifecycleError(f"{label} must be a mapping")
    return dict(value)


def _hashes(value: Any, label: str) -> dict[str, Any]:
    payload = _mapping(value, label)
    for key, item in payload.items():
        if not isinstance(key, str) or not isinstance(item, str) or len(item) != 64:
            raise LifecycleError(f"{label}[{key}] must be a 64-char sha256 hex string")
    return payload


def _hash64(value: Any, label: str) -> str:
    text: str = _text(value, label)
    if len(text) != 64:
        raise LifecycleError(f"{label} must be a 64-char sha256 hex string")
    return text


class EvolutionEngine:
    """Drives one or more lifecycle milestones against the durable ledger."""

    def __init__(
        self,
        store: EvolutionStore,
        *,
        source_type: str = "supervisor",
        source_id: str = "r11",
    ) -> None:
        self.store = store
        self.source_type = source_type
        self.source_id = source_id

    # -- low level ------------------------------------------------------------

    def transition(
        self,
        *,
        cycle_id: str,
        event_type: str,
        payload: dict[str, Any],
        controller: str,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> TransitionResult:
        if event_type not in LIFECYCLE_EVENTS:
            raise LifecycleError(f"unknown lifecycle event {event_type!r}")
        require_supervisor(controller, f"record {event_type}")
        cycle_state = self.store.cycle_state(cycle_id)
        if event_type != "evolution_live.observed" and cycle_state is None:
            raise LifecycleError(f"cycle {cycle_id} does not exist")
        machine = LifecycleMachine(str(cycle_state["last_event"]) if cycle_state else None)
        machine.assert_transition(event_type)
        self._validate(event_type, payload, cycle_state)
        at = self.store.now_utc()
        digest = json_hash(payload)[:16]
        event_id = f"r11-{event_type}-{cycle_id}-{digest}"
        idempotency_key = f"{event_type}:{cycle_id}:{digest}"
        resolved_source_type = source_type if source_type is not None else self.source_type
        resolved_source_id = source_id if source_id is not None else self.source_id
        if event_type == "evolution_live.independently_verified":
            verifier_id = _text(payload.get("verifier_id"), "verifier_id")
            candidate_id = _text(payload.get("candidate_id"), "candidate_id")
            if resolved_source_type != "independent_verifier":
                raise LifecycleError(
                    "independent verification must be recorded by an "
                    "independent_verifier source, never by the builder or candidate"
                )
            if verifier_id == candidate_id or "candidate" in verifier_id.lower():
                raise LifecycleError("a candidate cannot certify itself; verifier identity refused")
        event = LedgerEvent.create(
            event_type=event_type,
            payload=payload,
            source_type=resolved_source_type,
            source_id=resolved_source_id,
            idempotency_key=idempotency_key,
            event_id=event_id,
            occurred_at=at,
        )
        result: AppendResult = self.store.ledger.append(event)
        return TransitionResult(
            event=result.event,
            created=result.created,
            cycle_state=self.store.cycle_state(cycle_id),
        )

    def waiting(
        self,
        *,
        controller: str,
        cadence_key: str,
        reason: str,
        next_action: str,
    ) -> TransitionResult:
        require_supervisor(controller, "emit WAITING")
        payload = {
            "cadence_key": _text(cadence_key, "cadence_key"),
            "reason": _text(reason, "reason"),
            "next_action": _text(next_action, "next_action"),
        }
        digest = json_hash(payload)[:16]
        event = LedgerEvent.create(
            event_type="evolution_live.waiting",
            payload=payload,
            source_type=self.source_type,
            source_id=self.source_id,
            idempotency_key=f"evolution_live.waiting:{cadence_key}:{digest}",
            event_id=f"r11-evolution_live.waiting-{cadence_key}-{digest}",
            occurred_at=self.store.now_utc(),
        )
        result: AppendResult = self.store.ledger.append(event)
        return TransitionResult(
            event=result.event,
            created=result.created,
            cycle_state=None,
        )

    def refuse(
        self,
        *,
        probe_id: str,
        candidate_id: str,
        operation: str,
        reason: str,
        controller_identity: str,
    ) -> TransitionResult:
        payload = {
            "probe_id": _text(probe_id, "probe_id"),
            "candidate_id": _text(candidate_id, "candidate_id"),
            "operation": _text(operation, "operation"),
            "reason": _text(reason, "reason"),
            "controller_identity": _text(controller_identity, "controller_identity"),
        }
        digest = json_hash(payload)[:16]
        event = LedgerEvent.create(
            event_type="evolution_live.refused",
            payload=payload,
            source_type=self.source_type,
            source_id=self.source_id,
            idempotency_key=f"evolution_live.refused:{probe_id}:{digest}",
            event_id=f"r11-evolution_live.refused-{probe_id}-{digest}",
            occurred_at=self.store.now_utc(),
        )
        result: AppendResult = self.store.ledger.append(event)
        return TransitionResult(
            event=result.event,
            created=result.created,
            cycle_state=None,
        )

    # -- milestone helpers -----------------------------------------------------

    def observe(
        self,
        *,
        controller: str,
        cycle_id: str,
        observation: str,
        baseline_ref: str,
        baseline_hash: str,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.observed",
            payload={
                "cycle_id": cycle_id,
                "observation": _text(observation, "observation"),
                "baseline_ref": _text(baseline_ref, "baseline_ref"),
                "baseline_hash": _hash64(baseline_hash, "baseline_hash"),
            },
            controller=controller,
        )

    def hypothesize(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        hypothesis: str,
        acceptance_criteria: list[str],
        risk_class: str,
        surfaces: list[str],
        budget: dict[str, Any],
        authority_scope: dict[str, Any],
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.hypothesized",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "hypothesis": _text(hypothesis, "hypothesis"),
                "acceptance_criteria": validate_criteria(acceptance_criteria),
                "risk_class": risk_class,
                "surfaces": validate_surfaces(surfaces),
                "budget": _mapping(budget, "budget"),
                "authority_scope": _mapping(authority_scope, "authority_scope"),
                "frozen": True,
            },
            controller=controller,
        )

    def isolate_candidate(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        patch_hash: str,
        artifact_hashes: dict[str, Any],
        isolation_clone_hash: str,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.candidate_isolated",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "patch_hash": _hash64(patch_hash, "patch_hash"),
                "artifact_hashes": _hashes(artifact_hashes, "artifact_hashes"),
                "isolation_clone_hash": _hash64(isolation_clone_hash, "isolation_clone_hash"),
            },
            controller=controller,
        )

    def evaluate(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        baseline_metrics: dict[str, Any],
        candidate_metrics: dict[str, Any],
        improvement: bool,
        guardrails_ok: bool,
        delta: int,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.evaluated",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "baseline_metrics": validate_metrics(baseline_metrics, "baseline_metrics"),
                "candidate_metrics": validate_metrics(candidate_metrics, "candidate_metrics"),
                "improvement": bool(improvement),
                "guardrails_ok": bool(guardrails_ok),
                "delta": int(delta),
            },
            controller=controller,
        )

    def record_verification(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        verifier_id: str,
        verdict: str,
        evidence_refs: list[str],
        verification_request_hash: str,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.independently_verified",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "verifier_id": _text(verifier_id, "verifier_id"),
                "verdict": _text(verdict, "verdict"),
                "evidence_refs": [
                    _text(ref, "evidence_ref") for ref in evidence_refs if isinstance(ref, str)
                ],
                "verification_request_hash": _hash64(
                    verification_request_hash, "verification_request_hash"
                ),
            },
            controller=controller,
            source_type="independent_verifier",
            source_id=verifier_id,
        )

    def shadow(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        shadow_hash: str,
        shadow_checks: dict[str, Any],
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.shadowed",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "shadow_hash": _hash64(shadow_hash, "shadow_hash"),
                "shadow_checks": _mapping(shadow_checks, "shadow_checks"),
            },
            controller=controller,
        )

    def canary_ready(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        canary_hash: str,
        canary_checks: dict[str, Any],
        ready: bool,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.canary_ready",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "canary_hash": _hash64(canary_hash, "canary_hash"),
                "canary_checks": _mapping(canary_checks, "canary_checks"),
                "ready": bool(ready),
            },
            controller=controller,
        )

    def promote(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        release_id: str,
        live_before_hash: str,
        live_after_hash: str,
        artifact_hashes: dict[str, Any],
        outcome_before: dict[str, Any],
        outcome_after: dict[str, Any],
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.promoted",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "release_id": _text(release_id, "release_id"),
                "live_before_hash": _hash64(live_before_hash, "live_before_hash"),
                "live_after_hash": _hash64(live_after_hash, "live_after_hash"),
                "artifact_hashes": _hashes(artifact_hashes, "artifact_hashes"),
                "outcome_before": _mapping(outcome_before, "outcome_before"),
                "outcome_after": _mapping(outcome_after, "outcome_after"),
            },
            controller=controller,
        )

    def reject(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        reason: str,
        failed_history_ref: str,
        failure_hash: str,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.rejected",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "reason": _text(reason, "reason"),
                "failed_history_ref": _text(failed_history_ref, "failed_history_ref"),
                "failure_hash": _hash64(failure_hash, "failure_hash"),
            },
            controller=controller,
        )

    def measure_outcome(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        outcome_before: dict[str, Any],
        outcome_after: dict[str, Any],
        delta: int,
        outcome: str,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.outcome_measured",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "outcome_before": _mapping(outcome_before, "outcome_before"),
                "outcome_after": _mapping(outcome_after, "outcome_after"),
                "delta": int(delta),
                "outcome": _text(outcome, "outcome"),
            },
            controller=controller,
        )

    def learn(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        insight: str,
        next_action: str,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.learned",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "insight": _text(insight, "insight"),
                "next_action": _text(next_action, "next_action"),
            },
            controller=controller,
        )

    def rollback(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        release_id: str,
        prior_release_id: str,
        before_hash: str,
        after_hash: str,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.rolled_back",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "release_id": _text(release_id, "release_id"),
                "prior_release_id": _text(prior_release_id, "prior_release_id"),
                "before_hash": _hash64(before_hash, "before_hash"),
                "after_hash": _hash64(after_hash, "after_hash"),
            },
            controller=controller,
        )

    def restore(
        self,
        *,
        controller: str,
        cycle_id: str,
        candidate_id: str,
        release_id: str,
        before_hash: str,
        after_hash: str,
    ) -> TransitionResult:
        return self.transition(
            cycle_id=cycle_id,
            event_type="evolution_live.restored",
            payload={
                "cycle_id": cycle_id,
                "candidate_id": _text(candidate_id, "candidate_id"),
                "release_id": _text(release_id, "release_id"),
                "before_hash": _hash64(before_hash, "before_hash"),
                "after_hash": _hash64(after_hash, "after_hash"),
            },
            controller=controller,
        )

    # -- guard probes ----------------------------------------------------------

    def change_risk(self, *, controller: str, cycle_id: str, new_risk: str) -> None:
        require_supervisor(controller, "reclassify risk")
        state = self.store.cycle_state(cycle_id)
        if state is None:
            raise LifecycleError(f"cycle {cycle_id} does not exist")
        current = str(state["risk_class"])
        if new_risk not in RISK_ORDER:
            raise LifecycleError(f"unknown risk class {new_risk!r}")
        if risk_index(new_risk) <= risk_index(current):
            raise LifecycleError(
                f"risk reclassification {current} -> {new_risk} is not "
                "strictly ascending; a candidate can never lower its risk class"
            )

    def attempt_criteria_rewrite(
        self, *, controller: str, cycle_id: str, criteria: list[str]
    ) -> None:
        require_supervisor(controller, "rewrite acceptance criteria")
        state = self.store.cycle_state(cycle_id)
        if state is None:
            raise LifecycleError(f"cycle {cycle_id} does not exist")
        original = list(state.get("acceptance_criteria", []))
        if list(criteria) != original:
            raise LifecycleError("acceptance criteria are frozen at hypothesis; rewrite refused")

    def attempt_authority_widen(
        self, *, controller: str, cycle_id: str, authority_scope: dict[str, Any]
    ) -> None:
        require_supervisor(controller, "widen authority")
        state = self.store.cycle_state(cycle_id)
        if state is None:
            raise LifecycleError(f"cycle {cycle_id} does not exist")
        original = state.get("authority_scope", {})
        if not isinstance(original, dict):
            raise LifecycleError("authority scope is frozen")
        original_targets = original.get("targets", [])
        new_targets = authority_scope.get("targets", [])
        if (
            isinstance(original_targets, list)
            and isinstance(new_targets, list)
            and "*" not in original_targets
            and any(target not in original_targets for target in new_targets)
        ):
            raise LifecycleError("authority scope is frozen at hypothesis; widening refused")

    def attempt_budget_increase(
        self, *, controller: str, cycle_id: str, budget: dict[str, Any]
    ) -> None:
        require_supervisor(controller, "increase budget")
        state = self.store.cycle_state(cycle_id)
        if state is None:
            raise LifecycleError(f"cycle {cycle_id} does not exist")
        original_budget = state.get("budget", {})
        if not isinstance(original_budget, dict):
            raise LifecycleError("budget is frozen")
        original_max = original_budget.get("max_cost_usd", 0.0)
        new_max = budget.get("max_cost_usd", 0.0)
        if (
            isinstance(original_max, (int, float))
            and isinstance(new_max, (int, float))
            and float(new_max) > float(original_max)
        ):
            raise LifecycleError("budget is frozen at hypothesis; increase refused")

    def attempt_disable_stop(self, *, controller: str) -> None:
        require_supervisor(controller, "disable STOP")

    def attempt_delete_failed_history(self, *, controller: str, ref: str) -> None:
        require_supervisor(controller, f"delete failed history {ref}")

    def attempt_erase_evidence(self, *, controller: str, ref: str) -> None:
        require_supervisor(controller, f"erase evidence {ref}")

    def attempt_termination(self, *, controller: str, cycle_id: str) -> None:
        require_supervisor(controller, f"terminate cycle {cycle_id}")

    # -- payload validation -----------------------------------------------------

    def _validate(
        self,
        event_type: str,
        payload: dict[str, Any],
        cycle_state: dict[str, Any] | None,
    ) -> None:
        if event_type == "evolution_live.hypothesized":
            risk = payload.get("risk_class")
            if risk not in RISK_ORDER:
                raise LifecycleError(f"risk_class must be one of {RISK_ORDER}")
            if cycle_state and risk_index(str(risk)) < risk_index(
                str(cycle_state.get("risk_class", risk))
            ):
                raise LifecycleError(
                    "risk reclassification may only ascend; a candidate can "
                    "never lower its risk class"
                )
            budget = payload.get("budget")
            if isinstance(budget, dict):
                max_cost = budget.get("max_cost_usd")
                if not isinstance(max_cost, (int, float)) or max_cost < 0:
                    raise LifecycleError("budget.max_cost_usd must be >= 0")
        if event_type == "evolution_live.independently_verified":
            verdict = payload.get("verdict")
            if verdict not in ("PASS", "FAIL"):
                raise LifecycleError("verdict must be PASS or FAIL")
            evidence_refs = payload.get("evidence_refs")
            if not isinstance(evidence_refs, list) or not evidence_refs:
                raise LifecycleError("evidence_refs must be a non-empty list")
        if event_type == "evolution_live.evaluated":
            candidate_metrics = payload.get("candidate_metrics", {})
            baseline_metrics = payload.get("baseline_metrics", {})
            if isinstance(candidate_metrics, dict) and isinstance(baseline_metrics, dict):
                passed_before = baseline_metrics.get("pytest_passed")
                passed_after = candidate_metrics.get("pytest_passed")
                if (
                    isinstance(passed_before, int)
                    and isinstance(passed_after, int)
                    and payload.get("improvement") is True
                    and passed_after < passed_before
                ):
                    raise LifecycleError(
                        "improvement cannot be recorded when pytest passed counts regress"
                    )
        if event_type == "evolution_live.canary_ready" and payload.get("ready") is not True:
            raise LifecycleError("canary_ready requires ready=True")
