"""Deterministic, auditable worker routing (Hybrid Worker Integration §8-9).

The router is NOT an LLM popularity contest: it selects a worker from the
step contract's deterministic task signals plus recorded framework evidence
and current readiness. A route decision is a first-class durable record with
an id, eligible workers, the selected worker, reasons, the evidence
considered, the task classification, latency/cost expectations, confidence
and the fallback chain.

Policy (bootstrap version 1, conservative):

- OpenHands when the step is bounded, well specified, local, reversible,
  narrow-scoped, has explicit acceptance criteria and deterministic tests,
  and OpenHands readiness is verified (containment proven).
- Hermes when the step needs ambiguous diagnosis, root-cause reasoning,
  misleading-symptom interpretation, recovery, broad orientation, or when
  OpenHands produced an evidence mismatch / failed / is unhealthy / has
  uncontained persistence.
- Mandatory fallback triggers from the External Fusion verdict.

Repeated routing with identical recorded inputs yields the identical
decision (pure function over the contract + registry snapshot).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ..events import canonical_json
from .contracts import WORKER_HERMES, WORKER_OPENHANDS, WorkerStepContract

#: Routing policy version — every decision records which policy produced it.
POLICY_VERSION = "hybrid-worker-routing-v1"

#: Worker readiness vocabulary (capability-style, honest about containment).
READY = "READY"
DEGRADED = "DEGRADED"
NOT_READY = "NOT_READY"
VETOED = "VETOED"

#: Mandatory OpenHands -> Hermes fallback triggers (External Fusion verdict,
#: JARVIS Hybrid Worker Integration §8).
FALLBACK_TRIGGERS = (
    "claimed_success_contradicted_by_evidence",
    "held_out_failure",
    "scope_violation",
    "ambiguous_failure",
    "repeated_equivalent_failure",
    "inability_to_establish_root_cause",
    "worker_runtime_defect",
    "uncontained_persistence",
)


class RoutingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TaskClassification:
    """Deterministic classification of a step's worker requirements."""

    classification: str  # bounded_implementation | ambiguous_diagnosis | ...
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkerReadiness:
    """Observed readiness snapshot for one worker framework."""

    framework: str
    state: str = READY
    note: str = ""
    evidence_considered: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Durable auditable routing record (§9)."""

    route_decision_id: str
    mission_id: str
    step_id: str
    eligible_workers: tuple[str, ...]
    selected_worker: str
    reason: str
    evidence_considered: tuple[str, ...]
    task_classification: str
    expected_latency: str
    expected_cost: str
    confidence: str
    fallback: tuple[str, ...]
    policy_version: str = POLICY_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "route_decision_id": self.route_decision_id,
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "eligible_workers": list(self.eligible_workers),
            "selected_worker": self.selected_worker,
            "reason": self.reason,
            "evidence_considered": list(self.evidence_considered),
            "task_classification": self.task_classification,
            "expected_latency": self.expected_latency,
            "expected_cost": self.expected_cost,
            "confidence": self.confidence,
            "fallback": list(self.fallback),
            "policy_version": self.policy_version,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RouteDecision":
        return cls(
            route_decision_id=payload["route_decision_id"],
            mission_id=payload["mission_id"],
            step_id=payload["step_id"],
            eligible_workers=tuple(payload.get("eligible_workers", [])),
            selected_worker=payload["selected_worker"],
            reason=payload["reason"],
            evidence_considered=tuple(payload.get("evidence_considered", [])),
            task_classification=payload["task_classification"],
            expected_latency=payload["expected_latency"],
            expected_cost=payload["expected_cost"],
            confidence=payload["confidence"],
            fallback=tuple(payload.get("fallback", [])),
            policy_version=payload.get("policy_version", POLICY_VERSION),
        )


def classify_task(contract: WorkerStepContract) -> TaskClassification:
    """Deterministic classification from explicit task signals.

    ``contract.task_signals`` is the ONLY input (plus the contract role), so
    classification is reproducible and cannot be swayed by prose.
    """
    signals = dict(contract.task_signals or {})
    bounded = bool(signals.get("bounded", False))
    explicit_acceptance = bool(signals.get("explicit_acceptance", False))
    tests_available = bool(signals.get("tests_available", False))
    narrow_scope = bool(signals.get("narrow_scope", False))
    ambiguous = bool(signals.get("ambiguous", False))
    root_cause_required = bool(signals.get("root_cause_required", False))
    misleading_symptoms = bool(signals.get("misleading_symptoms", False))
    recovery = bool(signals.get("recovery", False))
    reversible = bool(signals.get("reversible", True))

    if ambiguous or root_cause_required or misleading_symptoms or recovery:
        return TaskClassification(
            classification="ambiguous_diagnosis",
            reason="task signals require diagnosis/recovery reasoning",
            signals=signals,
        )
    if bounded and explicit_acceptance and tests_available and narrow_scope and reversible:
        return TaskClassification(
            classification="bounded_implementation",
            reason="bounded, well-specified, tested, narrow-scope, reversible",
            signals=signals,
        )
    if bounded and explicit_acceptance:
        return TaskClassification(
            classification="bounded_implementation",
            reason="bounded with explicit acceptance criteria",
            signals=signals,
        )
    return TaskClassification(
        classification="ambiguous_diagnosis",
        reason="task does not satisfy bounded/well-specified criteria",
        signals=signals,
    )


def _latency_expectation(classification: str, readiness: dict[str, WorkerReadiness]) -> str:
    if classification == "bounded_implementation":
        return "fast" if readiness.get(WORKER_OPENHANDS, WorkerReadiness(WORKER_OPENHANDS)).state == READY else "medium"
    return "slow"


class WorkerRouter:
    """Pure routing logic; never launches anything."""

    def __init__(
        self,
        *,
        readiness: dict[str, WorkerReadiness] | None = None,
        fallback_triggers: tuple[str, ...] = FALLBACK_TRIGGERS,
        policy_version: str = POLICY_VERSION,
    ) -> None:
        self.readiness = dict(readiness or {})
        self.fallback_triggers = fallback_triggers
        self.policy_version = policy_version
        # Baseline readiness: Hermes primary; OpenHands NOT_READY until a
        # containment proof marks it READY (mirrors the experiment verdict).
        self.readiness.setdefault(
            WORKER_HERMES,
            WorkerReadiness(WORKER_HERMES, READY,
                            note="Hermes primary builder; isolated home required",
                            evidence_considered=("external-fusion-5e90a5d",)),
        )
        self.readiness.setdefault(
            WORKER_OPENHANDS,
            WorkerReadiness(WORKER_OPENHANDS, NOT_READY,
                            note="containment not yet proven for this run",
                            evidence_considered=("openhands-persistence-defect",)),
        )

    def mark_ready(
        self,
        framework: str,
        *,
        state: str = READY,
        note: str = "",
        evidence: tuple[str, ...] = (),
    ) -> None:
        self.readiness[framework] = WorkerReadiness(
            framework, state, note, evidence)

    def is_eligible(self, framework: str) -> bool:
        return self.readiness.get(framework, WorkerReadiness(framework, NOT_READY)).state == READY

    def eligible_workers(self) -> tuple[str, ...]:
        return tuple(fw for fw in (WORKER_HERMES, WORKER_OPENHANDS) if self.is_eligible(fw))

    def fallback_chain(self, selected: str) -> tuple[str, ...]:
        if selected == WORKER_OPENHANDS:
            return (WORKER_HERMES,)
        return ()

    def route(self, contract: WorkerStepContract) -> RouteDecision:
        """Deterministic route decision for one step contract.

        Same contract + same readiness snapshot -> same decision. Vetoed or
        unhealthy workers are removed from eligibility; when nothing is
        eligible the selected worker is '' and the reason states the
        blocker (the caller records an honest BLOCKED/wait state).
        """
        classification = classify_task(contract)
        decision_id = self._decision_id(contract, classification)
        eligible = self.eligible_workers()
        preferred = str(
            classification.signals.get("preferred_worker") or ""
        ).strip()
        evidence: list[str] = [
            f"task_signals={sorted((contract.task_signals or {}).keys())}",
            f"classification={classification.classification}",
        ]
        for framework in (WORKER_HERMES, WORKER_OPENHANDS):
            state = self.readiness[framework].state
            evidence.append(f"{framework}_readiness={state}")
        if preferred:
            preference_evidence = classification.signals.get(
                "worker_preference_evidence"
            ) or ()
            evidence.append(f"preferred_worker={preferred}")
            evidence.extend(
                f"worker_preference_evidence={item}"
                for item in preference_evidence
            )

        if not eligible:
            return RouteDecision(
                route_decision_id=decision_id,
                mission_id=contract.mission_id,
                step_id=contract.step_id,
                eligible_workers=(),
                selected_worker="",
                reason="no eligible worker (all unavailable or vetoed)",
                evidence_considered=tuple(evidence),
                task_classification=classification.classification,
                expected_latency="NOT_AVAILABLE",
                expected_cost="NOT_AVAILABLE",
                confidence="low",
                fallback=(),
                policy_version=self.policy_version,
            )

        selected: str
        reason: str
        preference_allowed = (
            preferred in eligible
            and (
                classification.classification == "bounded_implementation"
                or preferred == WORKER_HERMES
            )
        )
        if preference_allowed:
            selected = preferred
            reason = (
                "eligible evidence-backed worker preference accepted by "
                "canonical routing policy"
            )
        elif classification.classification == "bounded_implementation":
            if WORKER_OPENHANDS in eligible:
                selected = WORKER_OPENHANDS
                reason = "bounded implementation with explicit acceptance; OpenHands fast-path eligible"
            else:
                selected = WORKER_HERMES
                reason = "bounded implementation but OpenHands not ready; Hermes primary fallback"
        else:
            if WORKER_HERMES in eligible:
                selected = WORKER_HERMES
                reason = "ambiguous/root-cause task; Hermes primary diagnostic path"
            else:
                selected = WORKER_OPENHANDS if WORKER_OPENHANDS in eligible else ""
                reason = "ambiguous task routed to the only eligible worker" if selected else "no eligible worker"

        fallback = self.fallback_chain(selected)
        if selected == WORKER_OPENHANDS:
            confidence = "medium" if classification.signals.get("tests_available") else "low"
        else:
            confidence = "high"
        return RouteDecision(
            route_decision_id=decision_id,
            mission_id=contract.mission_id,
            step_id=contract.step_id,
            eligible_workers=tuple(eligible),
            selected_worker=selected,
            reason=reason,
            evidence_considered=tuple(evidence),
            task_classification=classification.classification,
            expected_latency=_latency_expectation(classification.classification, self.readiness),
            expected_cost="NOT_AVAILABLE",
            confidence=confidence,
            fallback=fallback,
            policy_version=self.policy_version,
        )

    def _decision_id(
        self,
        contract: WorkerStepContract,
        classification: TaskClassification,
    ) -> str:
        readiness = []
        for framework in (WORKER_HERMES, WORKER_OPENHANDS):
            snapshot = self.readiness[framework]
            readiness.append({
                "framework": framework,
                "state": snapshot.state,
                "note": snapshot.note,
                "evidence_considered": list(snapshot.evidence_considered),
            })
        identity = {
            "contract": contract.to_payload(),
            "classification": {
                "classification": classification.classification,
                "reason": classification.reason,
                "signals": classification.signals,
            },
            "readiness": readiness,
            "fallback_triggers": list(self.fallback_triggers),
            "policy_version": self.policy_version,
        }
        digest = hashlib.sha256(
            canonical_json(identity).encode("utf-8")
        ).hexdigest()
        return f"route-{digest}"

    def requires_fallback(self, trigger: str) -> bool:
        """Mandatory OpenHands -> Hermes escalation check."""
        return trigger in self.fallback_triggers
