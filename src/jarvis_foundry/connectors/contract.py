"""V5-S4: versioned connector contract (roadmap §8).

A connector DECLARES its actions with explicit semantics
(read/write/execute/communicate/external_effect), its capability
states, and its retry policy. The declaration is the deterministic
basis for routing AND for the action-semantics check that closes the
S2 verifier's F3 finding (declaration spoofing): a connector that
declares a MUTATING action (write/execute/communicate/external_effect)
requires the envelope to declare expected effects and a before-state
capture — the semantics are no longer inferred from the kind string.
"""
from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Any

from ..database import FoundryDatabase
from ..domain import CapabilityState

ACTION_SEMANTICS = frozenset(
    {"read", "write", "execute", "communicate", "external_effect"})
_MUTATING_SEMANTICS = frozenset(
    {"write", "execute", "communicate", "external_effect"})
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_PAYLOAD_HASH = re.compile(r"^[0-9a-f]{64}$")

ATTEMPT_OUTCOMES = frozenset(
    {"COMPLETED", "FAILED", "UNKNOWN", "CANCELLED"})
_IDEMPOTENCY_STRATEGIES = frozenset({"effect-key", "provider-key"})
_RECONCILIATION_STRATEGIES = frozenset(
    {"system-readback", "provider-query", "journal-only", "none"})
_READY_STATES = frozenset({"READY", "ACTIVE"})


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    connector_id: str
    kind: str
    version: str
    actions: dict[str, str]        # canonical action kind -> semantic
    capabilities: dict[str, str]   # capability -> CapabilityState value
    retry_policy: dict[str, Any]   # {"max_attempts": int, "backoff_s": [..]}
    target_kinds: tuple[str, ...] = ("opaque",)
    idempotency_strategy: str = "effect-key"
    reconciliation_strategy: str = "system-readback"
    supports_cancellation: bool = False
    cost_metadata: dict[str, Any] = field(default_factory=dict)
    evidence_fields: tuple[str, ...] = ("attempt_id", "outcome")
    stop_policy: str = "broker-and-driver"


@dataclass(frozen=True, slots=True)
class ConnectorRequest:
    """The in-memory request crossing the connector boundary.

    ``payload`` is available to the adapter but is never part of durable
    attempt evidence. ``payload_hash`` is the identity used for retries.
    """

    connector_id: str
    action_kind: str
    target_ref: str
    idempotency_key: str
    payload_hash: str
    payload: Any = None
    before_state: dict[str, Any] = field(default_factory=dict)
    capability: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectorAttempt:
    """Durable-safe result shape returned by a connector adapter."""

    attempt_id: str
    connector_id: str
    action_kind: str
    target_ref: str
    idempotency_key: str
    payload_hash: str
    outcome: str
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] | None = None
    provider_ref: str | None = None
    error_code: str | None = None
    evidence_refs: tuple[str, ...] = ()
    cost: dict[str, int | float] = field(default_factory=dict)
    reconciled: bool = False
    replayed: bool = False

    def to_evidence(self) -> dict[str, Any]:
        """Return the persisted-safe result without raw request payload."""
        return {
            "attempt_id": self.attempt_id,
            "connector_id": self.connector_id,
            "action_kind": self.action_kind,
            "target_ref": self.target_ref,
            "idempotency_key": self.idempotency_key,
            "payload_hash": self.payload_hash,
            "outcome": self.outcome,
            "before_state": dict(self.before_state),
            "after_state": (dict(self.after_state)
                             if self.after_state is not None else None),
            "provider_ref": self.provider_ref,
            "error_code": self.error_code,
            "evidence_refs": list(self.evidence_refs),
            "cost": dict(self.cost),
            "reconciled": self.reconciled,
            "replayed": self.replayed,
        }


def validate_connector_spec(spec: ConnectorSpec) -> str | None:
    """Deterministic contract validation — returns a reason or None.

    A connector that does not satisfy the versioned contract can never
    be registered or selected.
    """
    if not spec.connector_id or not spec.kind:
        return "connector id and kind are required"
    if not _VERSION.match(spec.version or ""):
        return f"version must be semver-like (N.N.N), got {spec.version!r}"
    if not spec.actions:
        return "connector must declare at least one action (actions is empty)"
    for action, semantic in spec.actions.items():
        if semantic not in ACTION_SEMANTICS:
            return (f"action {action!r} declares unknown semantic "
                    f"{semantic!r} — must be one of "
                    f"{sorted(ACTION_SEMANTICS)}")
    valid_states = frozenset(state.value for state in CapabilityState)
    for capability, state in spec.capabilities.items():
        if state not in valid_states:
            return (f"capability {capability!r} has invalid state "
                    f"{state!r}")
    max_attempts = spec.retry_policy.get("max_attempts")
    if not isinstance(max_attempts, int) or max_attempts < 1:
        return "retry_policy.max_attempts must be a positive integer"
    backoff = spec.retry_policy.get("backoff_s", [])
    if not isinstance(backoff, list) or any(
            not isinstance(step, (int, float)) or step < 0 for step in backoff):
        return "retry_policy.backoff_s must be a list of non-negative numbers"
    if (not isinstance(spec.target_kinds, (tuple, list)) or
            not spec.target_kinds or
            any(not isinstance(kind, str) or not kind.strip()
                for kind in spec.target_kinds)):
        return "target_kinds must contain at least one non-empty string"
    if spec.idempotency_strategy not in _IDEMPOTENCY_STRATEGIES:
        return ("idempotency_strategy must be one of "
                f"{sorted(_IDEMPOTENCY_STRATEGIES)}")
    if spec.reconciliation_strategy not in _RECONCILIATION_STRATEGIES:
        return ("reconciliation_strategy must be one of "
                f"{sorted(_RECONCILIATION_STRATEGIES)}")
    if not isinstance(spec.supports_cancellation, bool):
        return "supports_cancellation must be a boolean"
    if not isinstance(spec.cost_metadata, dict):
        return "cost_metadata must be an object"
    if (not isinstance(spec.evidence_fields, (tuple, list)) or
            not spec.evidence_fields or
            any(not isinstance(field_name, str) or not field_name.strip()
                for field_name in spec.evidence_fields)):
        return "evidence_fields must contain at least one non-empty string"
    if spec.stop_policy != "broker-and-driver":
        return "stop_policy must be 'broker-and-driver'"
    return None


def validate_connector_request(spec: ConnectorSpec,
                               request: ConnectorRequest) -> str | None:
    """Validate the non-durable request boundary before adapter execution."""
    if request.connector_id != spec.connector_id:
        return "request connector id does not match the registered connector"
    if request.action_kind not in spec.actions:
        return f"action {request.action_kind!r} is not declared"
    if not isinstance(request.target_ref, str) or not request.target_ref.strip():
        return "target_ref is required"
    if not isinstance(request.idempotency_key, str) or not request.idempotency_key.strip():
        return "idempotency_key is required"
    if not isinstance(request.payload_hash, str) or not _PAYLOAD_HASH.fullmatch(
            request.payload_hash):
        return "payload_hash must be a lowercase sha256 digest"
    if request.capability is not None:
        state = spec.capabilities.get(request.capability)
        if state not in _READY_STATES:
            return (f"capability {request.capability!r} is not READY or ACTIVE")
    return None


def validate_connector_attempt(
        spec: ConnectorSpec, request: ConnectorRequest,
        attempt: ConnectorAttempt) -> str | None:
    """Validate that an attempt is bound to its request and safe to persist."""
    request_error = validate_connector_request(spec, request)
    if request_error:
        return request_error
    for field_name in ("attempt_id", "connector_id", "action_kind",
                       "target_ref", "idempotency_key", "payload_hash"):
        if not getattr(attempt, field_name):
            return f"attempt {field_name} is required"
    if attempt.connector_id != request.connector_id:
        return "attempt connector id does not match request"
    if attempt.action_kind != request.action_kind:
        return "attempt action kind does not match request"
    if attempt.target_ref != request.target_ref:
        return "attempt target_ref does not match request"
    if attempt.idempotency_key != request.idempotency_key:
        return "attempt idempotency_key does not match request"
    if attempt.payload_hash != request.payload_hash:
        return "attempt payload_hash does not match request"
    if attempt.outcome not in ATTEMPT_OUTCOMES:
        return f"attempt outcome must be one of {sorted(ATTEMPT_OUTCOMES)}"
    if attempt.outcome == "CANCELLED" and not spec.supports_cancellation:
        return "connector does not support cancellation"
    if attempt.outcome in {"FAILED", "UNKNOWN"} and not attempt.error_code:
        return f"{attempt.outcome.lower()} attempts require error_code"
    if attempt.after_state is not None and not isinstance(attempt.after_state, dict):
        return "after_state must be an object when present"
    if (not isinstance(attempt.evidence_refs, (tuple, list)) or
            any(not isinstance(ref, str) or not ref.strip()
                for ref in attempt.evidence_refs)):
        return "evidence_refs must contain non-empty strings"
    for name, value in attempt.cost.items():
        if (not isinstance(name, str) or not name.strip() or
                not isinstance(value, (int, float)) or isinstance(value, bool) or
                not math.isfinite(float(value)) or value < 0):
            return "cost values must be finite non-negative numbers"
    return None


def is_mutating(semantic: str) -> bool:
    return semantic in _MUTATING_SEMANTICS


def check_action_semantics(
    database: FoundryDatabase,
    connector_id: str,
    action_kind: str,
    *,
    expected_effects: tuple[str, ...] = (),
    before_state_capture: dict[str, Any] | None = None,
) -> str | None:
    """Deterministic action-semantics check (S2 verifier F3, structural
    fix): when the connector DECLARES the action as mutating, the
    envelope must declare expected effects AND a before-state capture.

    Unknown connector / undeclared action -> None (no declaration, no
    claim — the envelope is not judged against undeclared semantics).
    """
    from ..control.broker import normalize_action_kind
    canonical = normalize_action_kind(action_kind)
    spec = latest_spec(database, connector_id)
    if spec is None:
        return None
    semantic = spec.actions.get(canonical)
    if semantic is None:
        return None
    if not is_mutating(semantic):
        return None
    if not expected_effects:
        return (f"connector {connector_id} declares {canonical!r} as "
                f"'{semantic}' — expected effects must be declared")
    if not before_state_capture or not (
            before_state_capture.get("ref") or before_state_capture.get("hash")):
        return (f"connector {connector_id} declares {canonical!r} as "
                f"'{semantic}' — before-state capture must be declared")
    return None


def latest_spec(database: FoundryDatabase,
                connector_id: str) -> ConnectorSpec | None:
    """The latest registered spec for a connector (derived from the
    append-only connector.registered stream)."""
    import json
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'connector.registered' AND json_extract(payload_json, "
            "'$.connector_id') = ? ORDER BY sequence DESC LIMIT 1",
            (connector_id,)).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    return ConnectorSpec(
        connector_id=payload["connector_id"],
        kind=payload["kind"],
        version=payload["version"],
        actions=dict(payload["actions"]),
        capabilities=dict(payload["capabilities"]),
        retry_policy=dict(payload["retry_policy"]),
        target_kinds=tuple(payload.get("target_kinds", ("opaque",))),
        idempotency_strategy=payload.get("idempotency_strategy", "effect-key"),
        reconciliation_strategy=payload.get(
            "reconciliation_strategy", "system-readback"),
        supports_cancellation=bool(payload.get("supports_cancellation", False)),
        cost_metadata=dict(payload.get("cost_metadata", {})),
        evidence_fields=tuple(payload.get("evidence_fields",
                                         ("attempt_id", "outcome"))),
        stop_policy=payload.get("stop_policy", "broker-and-driver"))
