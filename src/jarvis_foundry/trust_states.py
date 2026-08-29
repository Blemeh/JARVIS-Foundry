"""Canonical trust-state vocabulary shared by lifecycle and projections.

Keeping the vocabulary in one dependency-free module prevents an event
replay, Supervisor restart, and trust assessment from silently disagreeing
about which states are valid.  ``PROVEN`` remains readable only as a legacy
ledger token; new lifecycle events use ``PROVING``.
"""
from __future__ import annotations

TRUST_STATES = (
    "LAB",
    "PROVING",
    "LIMITED_AUTONOMY",
    "TRUSTED_AUTONOMOUS",
    "DEGRADED",
    "SUSPENDED",
)

LEGACY_TRUST_STATES = ("PROVEN",)

ALLOWED_TRUST_STATES = TRUST_STATES + LEGACY_TRUST_STATES
