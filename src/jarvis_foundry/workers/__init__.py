"""Hybrid worker integration (JARVIS owns its workforce).

Framework-neutral worker abstraction on top of the canonical role/mission
control plane:

- ``WorkerStepContract`` -- what JARVIS asks a worker to do (mission/step
  binding, scope, context manifest reference, action-envelope snapshot,
  provider/model identity, bounds).
- ``ResultEnvelope`` -- the canonical normalized result contract every
  framework (Hermes, OpenHands, future workers) must satisfy.
- ``WorkerRouter`` -- deterministic, auditable task classification and
  worker selection (bounded -> OpenHands; ambiguous/root-cause -> Hermes;
  evidence mismatch -> Hermes fallback).
- ``EvidenceReconciler`` -- JARVIS independently reconciles worker claims
  against observed worktree/artifact/containment state. A worker claim is
  never canonical truth.
- ``DisposableWorkerState`` / ``HeldOutBoundary`` -- run-specific worker
  homes and genuinely private held-out evidence.
- ``HybridStepController`` -- route -> dispatch -> evidence -> independent
  verification -> accept/repair -> next action, with STOP and veto honored.
- ``lab`` -- B3-equivalent clean diagnosis harness with truly private
  held-out criteria.

The control plane owns routing, scope, evidence, verification, recovery and
acceptance; workers are replaceable implementations.
"""
from .contracts import (
    WORKER_HERMES,
    WORKER_OPENHANDS,
    VALID_WORKERS,
    WorkerStepContract,
)
from .envelope import ResultEnvelope
from .router import RouteDecision, TaskClassification, WorkerRouter
from .evidence import EvidenceReport, EvidenceReconciler
from .isolation import DisposableWorkerState, HeldOutBoundary

__all__ = [
    "WORKER_HERMES",
    "WORKER_OPENHANDS",
    "VALID_WORKERS",
    "WorkerStepContract",
    "ResultEnvelope",
    "RouteDecision",
    "TaskClassification",
    "WorkerRouter",
    "EvidenceReport",
    "EvidenceReconciler",
    "DisposableWorkerState",
    "HeldOutBoundary",
]
