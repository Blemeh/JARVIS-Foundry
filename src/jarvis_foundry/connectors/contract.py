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
from dataclasses import dataclass
from typing import Any

from ..database import FoundryDatabase
from ..domain import CapabilityState

ACTION_SEMANTICS = frozenset(
    {"read", "write", "execute", "communicate", "external_effect"})
_MUTATING_SEMANTICS = frozenset(
    {"write", "execute", "communicate", "external_effect"})
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    connector_id: str
    kind: str
    version: str
    actions: dict[str, str]        # canonical action kind -> semantic
    capabilities: dict[str, str]   # capability -> CapabilityState value
    retry_policy: dict[str, Any]   # {"max_attempts": int, "backoff_s": [..]}


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
        retry_policy=dict(payload["retry_policy"]))
