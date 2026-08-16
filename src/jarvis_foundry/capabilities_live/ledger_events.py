"""Durable R9 capability lifecycle ledger kinds, events, and helpers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.clock import Clock, FixedClock, SystemClock
from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent
from jarvis_foundry.kernel.ledger import Ledger

CAPABILITY_KINDS: tuple[str, ...] = (
    "capability.discovered",
    "capability.probing",
    "capability.ready",
    "capability.active",
    "capability.blocked",
    "capability.not_ready",
    "capability.not_tested",
    "capability.degraded",
    "capability.suspended",
    "capability.revoked",
)

ADAPTER_KINDS: tuple[str, ...] = (
    "adapter.executed",
    "adapter.refused",
    "adapter.receipt_rejected",
    "adapter.failed",
)

CREDENTIAL_KINDS: tuple[str, ...] = (
    "credential.registered",
    "credential.revoked",
    "authorization.denied",
    "financial.request_denied",
)


def r9_event_registry() -> EventKindRegistry:
    """Versioned, non-financial event kinds for the R9 lifecycle and adapters."""
    registry = EventKindRegistry()
    required: dict[str, tuple[str, ...]] = {
        "capability.discovered": ("capability_id", "domain", "name", "system_id"),
        "capability.probing": ("capability_id", "probe_note"),
        "capability.ready": ("capability_id", "reason"),
        "capability.active": (
            "capability_id",
            "proof_hash",
            "before_state_hash",
            "after_state_hash",
            "receipt_hash",
            "reconciliation_hash",
        ),
        "capability.blocked": ("capability_id", "reason"),
        "capability.not_ready": ("capability_id", "reason"),
        "capability.not_tested": ("capability_id", "reason"),
        "capability.degraded": ("capability_id", "reason"),
        "capability.suspended": ("capability_id", "reason"),
        "capability.revoked": ("capability_id", "reason"),
        "adapter.executed": (
            "capability_id",
            "operation",
            "idempotency_key",
            "outcome",
            "before_state_hash",
            "after_state_hash",
            "receipt_hash",
            "reconciled",
        ),
        "adapter.refused": (
            "capability_id",
            "operation",
            "idempotency_key",
            "refusal",
            "reason",
            "no_partial_effect",
        ),
        "adapter.receipt_rejected": (
            "capability_id",
            "operation",
            "idempotency_key",
            "reason",
            "no_partial_effect",
        ),
        "adapter.failed": (
            "capability_id",
            "operation",
            "idempotency_key",
            "reason",
            "no_partial_effect",
        ),
        "credential.registered": (
            "credential_id",
            "identity_canonical",
            "provider",
            "capabilities",
            "expires_at",
            "fingerprint",
            "scope_hash",
        ),
        "credential.revoked": ("credential_id", "reason"),
        "authorization.denied": (
            "credential_id",
            "capability",
            "requested_target",
            "reason",
        ),
        "financial.request_denied": ("request_type", "reason"),
    }
    for kind, keys in required.items():
        registry.register(kind, required_keys=keys, financial=False)
    return registry


def r9_ledger(path: Path, *, fixed_utc: str | None = None) -> Ledger:
    clock: Clock = FixedClock(fixed_utc) if fixed_utc else SystemClock()
    return Ledger(path, registry=r9_event_registry(), clock=clock)


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
