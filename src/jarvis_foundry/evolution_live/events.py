"""Durable kernel ledger kinds and helpers for the R11 lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.clock import Clock, FixedClock, SystemClock
from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent
from jarvis_foundry.kernel.ledger import Ledger

LIFECYCLE_KINDS: tuple[str, ...] = (
    "evolution_live.observed",
    "evolution_live.hypothesized",
    "evolution_live.candidate_isolated",
    "evolution_live.evaluated",
    "evolution_live.independently_verified",
    "evolution_live.shadowed",
    "evolution_live.canary_ready",
    "evolution_live.promoted",
    "evolution_live.rejected",
    "evolution_live.outcome_measured",
    "evolution_live.learned",
    "evolution_live.rolled_back",
    "evolution_live.restored",
    "evolution_live.waiting",
    "evolution_live.refused",
)

_REQUIRED: dict[str, tuple[str, ...]] = {
    "evolution_live.observed": (
        "cycle_id",
        "observation",
        "baseline_ref",
        "baseline_hash",
    ),
    "evolution_live.hypothesized": (
        "cycle_id",
        "candidate_id",
        "hypothesis",
        "acceptance_criteria",
        "risk_class",
        "surfaces",
        "budget",
        "authority_scope",
        "frozen",
    ),
    "evolution_live.candidate_isolated": (
        "cycle_id",
        "candidate_id",
        "patch_hash",
        "artifact_hashes",
        "isolation_clone_hash",
    ),
    "evolution_live.evaluated": (
        "cycle_id",
        "candidate_id",
        "baseline_metrics",
        "candidate_metrics",
        "improvement",
        "guardrails_ok",
        "delta",
    ),
    "evolution_live.independently_verified": (
        "cycle_id",
        "candidate_id",
        "verifier_id",
        "verdict",
        "evidence_refs",
        "verification_request_hash",
    ),
    "evolution_live.shadowed": (
        "cycle_id",
        "candidate_id",
        "shadow_hash",
        "shadow_checks",
    ),
    "evolution_live.canary_ready": (
        "cycle_id",
        "candidate_id",
        "canary_hash",
        "canary_checks",
        "ready",
    ),
    "evolution_live.promoted": (
        "cycle_id",
        "candidate_id",
        "release_id",
        "live_before_hash",
        "live_after_hash",
        "artifact_hashes",
        "outcome_before",
        "outcome_after",
    ),
    "evolution_live.rejected": (
        "cycle_id",
        "candidate_id",
        "reason",
        "failed_history_ref",
        "failure_hash",
    ),
    "evolution_live.outcome_measured": (
        "cycle_id",
        "candidate_id",
        "outcome_before",
        "outcome_after",
        "delta",
        "outcome",
    ),
    "evolution_live.learned": (
        "cycle_id",
        "candidate_id",
        "insight",
        "next_action",
    ),
    "evolution_live.rolled_back": (
        "cycle_id",
        "candidate_id",
        "release_id",
        "prior_release_id",
        "before_hash",
        "after_hash",
    ),
    "evolution_live.restored": (
        "cycle_id",
        "candidate_id",
        "release_id",
        "before_hash",
        "after_hash",
    ),
    "evolution_live.waiting": (
        "cadence_key",
        "reason",
        "next_action",
    ),
    "evolution_live.refused": (
        "probe_id",
        "candidate_id",
        "operation",
        "reason",
        "controller_identity",
    ),
}


def r11_event_registry() -> EventKindRegistry:
    """Versioned, non-financial event kinds for the R11 lifecycle."""
    registry = EventKindRegistry()
    for kind, required in _REQUIRED.items():
        registry.register(kind, required_keys=required, financial=False)
    return registry


def r11_ledger(path: Path, *, fixed_utc: str | None = None) -> Ledger:
    clock: Clock = FixedClock(fixed_utc) if fixed_utc else SystemClock()
    return Ledger(path, registry=r11_event_registry(), clock=clock)


def record_event(
    ledger: Ledger,
    *,
    event_type: str,
    payload: Mapping[str, Any],
    source_type: str,
    source_id: str,
    idempotency_key: str,
    event_id: str,
    occurred_at: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> LedgerEvent:
    event = LedgerEvent.create(
        event_type=event_type,
        payload=payload,
        source_type=source_type,
        source_id=source_id,
        idempotency_key=idempotency_key,
        event_id=event_id,
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
    return ledger.append(event).event
