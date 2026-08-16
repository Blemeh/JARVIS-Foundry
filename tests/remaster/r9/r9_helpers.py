"""Shared helpers for the R9 regression suite (no top-level conftest)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.capabilities_live.broker import CredentialBroker, ResolvedCredential
from jarvis_foundry.capabilities_live.ledger_events import r9_ledger
from jarvis_foundry.capabilities_live.registry import CapabilityRegistry
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.ledger import Ledger

FIXED_UTC = "2026-08-16T06:00:00Z"
EXPIRED_UTC = "2026-08-16T05:00:00Z"
LATER_UTC = "2026-08-17T06:00:00Z"


def make_ledger(root: Path) -> Ledger:
    return r9_ledger(root / "r9-ledger.db", fixed_utc=FIXED_UTC)


def make_registry(ledger: Ledger) -> CapabilityRegistry:
    return CapabilityRegistry(ledger, clock=FixedClock(FIXED_UTC))


def make_broker(
    root: Path,
    ledger: Ledger | None = None,
    *,
    fixed_utc: str = FIXED_UTC,
) -> CredentialBroker:
    return CredentialBroker(
        root / "credentials.db",
        clock=FixedClock(fixed_utc),
        ledger=ledger,
    )


def register_canary_credential(
    broker: CredentialBroker,
    *,
    credential_id: str = "cred-0001",
    identity: str = "canary-owner",
    provider: str = "local-canary",
    secret_value: str = "r9-local-canary-credential-a",
    capabilities: tuple[str, ...] = ("capability.files",),
    scope: Mapping[str, Any] | None = None,
    expires_at: str = LATER_UTC,
) -> Any:
    return broker.register(
        identity=identity,
        provider=provider,
        secret_value=secret_value,
        capabilities=capabilities,
        scope=scope or {"targets": ["canary/*"]},
        expires_at=expires_at,
        credential_id=credential_id,
    )


def resolve_ok(
    broker: CredentialBroker,
    *,
    capability: str = "capability.files",
    target: str = "canary/files/one.txt",
    at: str = FIXED_UTC,
) -> ResolvedCredential:
    return broker.resolve(
        "cred-0001",
        capability=capability,
        requested_target=target,
        at=at,
    )
