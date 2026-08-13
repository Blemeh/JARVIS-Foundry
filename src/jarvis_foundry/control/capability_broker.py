"""V5-S3: scoped capability and credential broker (architecture §8).

Workers receive a short-lived CAPABILITY, never a reusable secret.
A capability is bound to mission, agent run, canonical action, target,
and expiry; it is one-shot (replay refused); its use is refused
immediately when STOP/veto is active or when it is used outside its
scope (escalation/forwarding). All events carry identifiers and
outcomes — never secret VALUES. Provider authentication is resolved at
execution time by the executor (S8); until a credentials system is
registered READY, resolution is an honest CapabilityUnavailable
(NOT_READY) — never a fabricated credential.

Events (append-only, full-payload idempotency keys):
- capability.issued   (binding + expiry; fixed key per capability id)
- capability.used     (one-shot use record; full-payload key)
- capability.revoked  (explicit revocation; full-payload key)
Expiry is DERIVED (expires_at vs now) — no event, rebuild-safe.

NOTE: the V4 module src/jarvis_foundry/control/capabilities.py holds the
resource capability-state transitions (capability.state_changed); this
module is the V5 worker-capability broker.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..clock import add_seconds, isoformat
from ..database import FoundryDatabase
from ..domain import ActionEnvelope
from ..events import Event, payload_hash
from .broker import (emergency_stop_reason, matching_veto_reason,
                     normalize_action_kind, require_allowance,
                     resolve_envelope_targets)

_READY_STATES = frozenset({"READY", "ACTIVE"})


class CapabilityError(Exception):
    """Deterministic refusal: expiry, replay, escalation, forwarding,
    revocation, or STOP/veto — the reason is always attached."""


class CapabilityUnavailable(CapabilityError):
    """Honest NOT_READY: no registered READY credentials system exists
    for the requested provider. Never fabricates a credential."""


@dataclass(frozen=True, slots=True)
class Capability:
    capability_id: str
    mission_id: str
    agent_run_id: str
    action_kind: str          # canonical form
    target: str               # resolved identity ("system:x" | "file:p")
    scopes: tuple[str, ...]
    expires_at: str           # ISO; derived expiry — now >= expires_at = EXPIRED
    issued_at: str
    from_action_id: str | None


# ---------------------------------------------------------------------------
# issuance
# ---------------------------------------------------------------------------

def issue_capability(
    database: FoundryDatabase,
    *,
    capability_id: str,
    mission_id: str,
    agent_run_id: str,
    action_kind: str,
    target: str,
    scopes: tuple[str, ...] = (),
    ttl_seconds: int = 300,
    now: str | None = None,
    from_action_id: str | None = None,
) -> Capability:
    """Issue a short-lived capability bound to mission/run/action/target.

    Identical re-delivery dedupes on the fixed per-id event key; a
    different binding under the same id is an EventConflict (the id is
    the identity — re-issue with a NEW id).
    """
    canonical = normalize_action_kind(action_kind)
    if not capability_id or not mission_id or not agent_run_id:
        raise CapabilityError(
            "capability id, mission id, and agent run id are required")
    if not target:
        raise CapabilityError("a resolved target is required")
    if ttl_seconds <= 0:
        raise CapabilityError("ttl must be positive")
    issued_at = now or isoformat()
    expires_at = add_seconds(issued_at, ttl_seconds)
    database.append(Event.create(
        "capability.issued",
        {"capability_id": capability_id, "mission_id": mission_id,
         "agent_run_id": agent_run_id, "action_kind": canonical,
         "target": target, "scopes": list(scopes),
         "expires_at": expires_at, "issued_at": issued_at,
         "from_action_id": from_action_id},
        source_type="supervisor", source_id="v5-capability-broker",
        idempotency_key=f"capability.issued:{capability_id}"))
    return Capability(capability_id=capability_id, mission_id=mission_id,
                      agent_run_id=agent_run_id, action_kind=canonical,
                      target=target, scopes=scopes, expires_at=expires_at,
                      issued_at=issued_at, from_action_id=from_action_id)


def issue_capability_for_envelope(
    database: FoundryDatabase,
    envelope: ActionEnvelope,
    *,
    ttl_seconds: int = 300,
    now: str | None = None,
    stop_path: str | None = None,
) -> Capability:
    """Issue the broker step-9 capability for an ALLOWed envelope.

    Requires an ALLOW decision ticket (require_allowance) — issuance is
    as impossible to bypass as execution. The capability id is derived
    from the envelope identity + idempotency key, so an identical
    re-delivery re-issues the SAME capability (deduped event). The
    binding uses the broker's RESOLVED target identities.
    """
    require_allowance(database, envelope.action_id)
    canonical = normalize_action_kind(envelope.action_kind)
    resolved_targets = resolve_envelope_targets(database, envelope)
    raw = f"{envelope.action_id}:{envelope.idempotency_key or ''}"
    capability_id = ("cap-" + hashlib.sha256(raw.encode("utf-8"))
                     .hexdigest()[:16])
    return issue_capability(
        database,
        capability_id=capability_id,
        mission_id=envelope.mission_id,
        agent_run_id=envelope.requested_by_agent_run_id or "run-unknown",
        action_kind=canonical,
        target=",".join(resolved_targets),
        scopes=tuple(envelope.capabilities_required),
        ttl_seconds=ttl_seconds, now=now,
        from_action_id=envelope.action_id)


# ---------------------------------------------------------------------------
# state derivation (pure; rebuild-safe)
# ---------------------------------------------------------------------------

def capability_state(database: FoundryDatabase, capability_id: str, *,
                     now: str | None = None) -> str:
    """Derived capability state: ISSUED / USED / REVOKED / EXPIRED /
    UNKNOWN. Expiry is computed from expires_at — no expiry event."""
    with database.connect() as connection:
        issued = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'capability.issued' AND json_extract(payload_json, "
            "'$.capability_id') = ?", (capability_id,)).fetchone()
        if issued is None:
            return "UNKNOWN"
        payload = json.loads(issued["payload_json"])
        used = connection.execute(
            "SELECT 1 FROM events WHERE event_type = 'capability.used' "
            "AND json_extract(payload_json, '$.capability_id') = ? "
            "LIMIT 1", (capability_id,)).fetchone()
        revoked = connection.execute(
            "SELECT 1 FROM events WHERE event_type = "
            "'capability.revoked' AND json_extract(payload_json, "
            "'$.capability_id') = ? LIMIT 1", (capability_id,)).fetchone()
    if revoked is not None:
        return "REVOKED"
    if used is not None:
        return "USED"
    if payload["expires_at"] <= (now or isoformat()):
        return "EXPIRED"
    return "ISSUED"


# ---------------------------------------------------------------------------
# use (one-shot, scoped, STOP/veto-immediate)
# ---------------------------------------------------------------------------

def use_capability(
    database: FoundryDatabase,
    capability_id: str,
    *,
    now: str | None = None,
    mission_id: str | None = None,
    agent_run_id: str | None = None,
    action_kind: str | None = None,
    target: str | None = None,
    stop_path: str | None = None,
) -> str:
    """Use a capability exactly once, within scope, under live control.

    Refuses (CapabilityError with the reason) when the capability is
    unknown/used/revoked/expired, when STOP or an applicable veto is
    active (immediate revocation — no revoke event needed), or when the
    presented context does not match the issued binding (scope
    escalation / worker forwarding).
    """
    timestamp = now or isoformat()
    state = capability_state(database, capability_id, now=timestamp)
    if state != "ISSUED":
        raise CapabilityError(
            f"capability {capability_id} is {state} — not usable")
    stop = emergency_stop_reason(database, stop_path)
    if stop:
        raise CapabilityError(
            f"capability {capability_id} revoked by STOP: {stop}")
    with database.connect() as connection:
        issued = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'capability.issued' AND json_extract(payload_json, "
            "'$.capability_id') = ?", (capability_id,)).fetchone()
    binding = json.loads(issued["payload_json"])
    bound_targets = binding["target"].split(",")
    veto = matching_veto_reason(database, {
        "GLOBAL", binding["action_kind"], binding["mission_id"],
        binding["target"], *bound_targets,
    })
    if veto:
        raise CapabilityError(
            f"capability {capability_id} revoked by veto: {veto}")
    if mission_id is not None and mission_id != binding["mission_id"]:
        raise CapabilityError(
            f"capability {capability_id} is bound to mission "
            f"{binding['mission_id']} — scope escalation refused")
    if agent_run_id is not None and agent_run_id != binding["agent_run_id"]:
        raise CapabilityError(
            f"capability {capability_id} is bound to run "
            f"{binding['agent_run_id']} — forwarding refused")
    if action_kind is not None and \
            normalize_action_kind(action_kind) != binding["action_kind"]:
        raise CapabilityError(
            f"capability {capability_id} is bound to action "
            f"{binding['action_kind']} — scope escalation refused")
    if target is not None and target not in bound_targets:
        raise CapabilityError(
            f"capability {capability_id} is bound to target "
            f"{binding['target']} — scope escalation refused")
    database.append(Event.create(
        "capability.used",
        {"capability_id": capability_id, "used_at": timestamp,
         "by_agent_run_id": agent_run_id},
        source_type="supervisor", source_id="v5-capability-broker",
        idempotency_key=(f"capability.used:{capability_id}:"
                         f"{payload_hash({'used_at': timestamp})}")))
    return "USED"


def revoke_capability(database: FoundryDatabase, capability_id: str, *,
                      reason: str, now: str | None = None) -> None:
    """Explicit revocation (append-only; full-payload key so repeat
    revocations with different reasons record their history)."""
    database.append(Event.create(
        "capability.revoked",
        {"capability_id": capability_id, "reason": reason,
         "revoked_at": now or isoformat()},
        source_type="supervisor", source_id="v5-capability-broker",
        idempotency_key=(f"capability.revoked:{capability_id}:"
                         f"{payload_hash({'reason': reason})}")))


# ---------------------------------------------------------------------------
# provider authentication — resolved at execution time, never stored
# ---------------------------------------------------------------------------

def resolve_provider_auth(
    database: FoundryDatabase,
    provider: str,
    *,
    mission_id: str,
    agent_run_id: str,
    ttl_seconds: int = 120,
    now: str | None = None,
) -> Capability:
    """Resolve provider authentication AT EXECUTION TIME.

    Returns a short-lived capability bound to ``provider.auth:<provider>``
    when a registered credentials system declares that provider READY —
    the capability IDENTIFIES the resolution; the credential VALUE never
    enters the ledger, prompts, logs, or evidence. Without a READY
    credentials system this raises CapabilityUnavailable (honest
    NOT_READY — no fabricated credentials).
    """
    with database.connect() as connection:
        systems = connection.execute(
            "SELECT capabilities_json FROM systems WHERE kind = "
            "'credentials'").fetchall()
    ready = False
    for row in systems:
        state = json.loads(str(row["capabilities_json"])).get(provider)
        if state in _READY_STATES:
            ready = True
            break
    if not ready:
        raise CapabilityUnavailable(
            f"provider auth for {provider!r} is NOT_READY — no registered "
            "READY credentials system")
    auth_id = ("auth-" + hashlib.sha256(
        f"{provider}:{mission_id}:{agent_run_id}".encode("utf-8"))
        .hexdigest()[:16])
    return issue_capability(
        database,
        capability_id=auth_id,
        mission_id=mission_id,
        agent_run_id=agent_run_id,
        action_kind=f"provider.auth.{provider}",
        target=f"provider:{provider}",
        scopes=("resolve-auth",),
        ttl_seconds=ttl_seconds, now=now)
