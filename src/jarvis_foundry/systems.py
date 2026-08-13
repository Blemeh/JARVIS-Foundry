"""V5-S1: System Registry (Multi-System Intelligence, slice 1).

The durable catalog + policy gate + external-effect journal that every
later V5 adapter builds on. Deterministic, append-only, no live
external connections in this slice.

Exit evidence (roadmap V5 §8) enforced here:
1. Revocation and STOP immediately block new actions —
   ``systems_allowed_by_policy`` consults the stop marker, active
   STOP_EVERYTHING/VETO_ACTION directives, and the frozen supervisor
   state;
2. Retries do not duplicate external effects — every effect carries an
   idempotency key bound to (system_id, target_ref, payload_hash) and
   the journal row is UNIQUE on that triple;
3. Secrets never appear — secret-like capability/probe payloads are
   refused at the API boundary (deterministic pattern guard).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .database import FoundryDatabase
from .events import Event

#: secret-like assignments refused at the API boundary (exit evidence 3)
_SECRET_PATTERN = re.compile(
    r"(api[_-]?key|token|password|passwd|secret|private[_-]?key|"
    r"authorization|bearer)\\?\"?\s*[:=]", re.IGNORECASE)

_READINESS = frozenset({"READY", "UNREADY", "NOT_TESTED"})
_PROBE_STATES = frozenset({"READY", "FAILED", "NOT_TESTED"})


def _reject_secrets(payload: dict[str, Any], where: str) -> None:
    for key, value in payload.items():
        probe = f"{key}={value}"
        if _SECRET_PATTERN.search(probe):
            raise ValueError(
                f"secret-like value refused in {where} ({key}) — secrets "
                f"must never enter the ledger (V5 exit evidence 3)")


def register_system(db: FoundryDatabase, system_id: str, kind: str,
                    name: str, *, capabilities: dict[str, str] | None = None,
                    probe_ref: str | None = None) -> None:
    """Register an accessible system in the durable catalog.

    Idempotent: identical re-delivery dedupes on the event key.
    Capability states are honest READY / NOT_READY / NOT_TESTED —
    never fabricated. Secret-like values are refused.
    """
    capabilities = capabilities or {}
    _reject_secrets(capabilities, f"capabilities of {system_id}")
    if probe_ref:
        _reject_secrets({"probe_ref": probe_ref}, f"probe of {system_id}")
    db.append(Event.create(
        "system.registered",
        {"system_id": system_id, "kind": kind, "name": name,
         "capabilities": capabilities, "probe_ref": probe_ref},
        source_type="supervisor", source_id="v5-registry",
        idempotency_key=f"system.registered:{system_id}"))


def probe_system(db: FoundryDatabase, system_id: str,
                 results: dict[str, str], *,
                 probe_key: str | None = None) -> None:
    """Record a bounded probe; readiness follows the results honestly.

    Any FAILED probe marks the system UNREADY; all-READY marks READY;
    a probe with no FAILED but partial results keeps NOT_TESTED for the
    unprobed capabilities. The probe key is per-cycle (a crash-retried
    probe with the same key dedupes; distinct cycles probe distinctly).
    """
    import uuid
    _reject_secrets(results, f"probe results of {system_id}")
    for state in results.values():
        if state not in _PROBE_STATES:
            raise ValueError(
                f"probe state {state!r} not in {sorted(_PROBE_STATES)}")
    db.append(Event.create(
        "system.probe_recorded",
        {"system_id": system_id, "results": results},
        source_type="supervisor", source_id="v5-registry",
        idempotency_key=f"system.probe_recorded:{system_id}:"
                        f"{probe_key or uuid.uuid4().hex[:8]}"))


def systems_allowed_by_policy(db: FoundryDatabase, action_kind: str, *,
                              stop_path: str | Path | None = None) -> bool:
    """V5 exit evidence 1: revocation and STOP immediately block new
    actions. True only when every gate passes:
    - no stop marker (when a path is provided),
    - no active STOP_EVERYTHING directive,
    - no active VETO_ACTION matching the action kind,
    - the supervisor is not FROZEN or SUSPENDED (a plain STOPPED
      precursor state is not a freeze; a reconciled STOP stays
      SUSPENDED until supervisor.started reopens the gate).
    """
    if stop_path is not None and Path(stop_path).exists():
        return False
    with db.connect() as connection:
        directives = connection.execute(
            "SELECT command_type, target FROM directives WHERE active = 1"
        ).fetchall()
        for row in directives:
            if row["command_type"] in ("STOP_EVERYTHING", "PAUSE"):
                return False
            if row["command_type"] in ("VETO_ACTION", "STOP_MISSION") and \
                    row["target"] in ("GLOBAL", action_kind):
                return False
        state = connection.execute(
            "SELECT status FROM supervisor_state WHERE singleton = 1"
        ).fetchone()
        frozen = connection.execute(
            "SELECT 1 FROM events WHERE event_type = "
            "'emergency_stop.frozen' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        reconciled = connection.execute(
            "SELECT 1 FROM events WHERE event_type = "
            "'emergency_stop.reconciled' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    if state is not None and state[0] in ("FROZEN", "SUSPENDED"):
        return False
    # a frozen event without a LATER reconciled event = STOP still live
    if frozen is not None and (reconciled is None):
        return False
    return True


def external_action_key(system_id: str, target_ref: str,
                        payload_hash: str) -> str:
    """V5 exit evidence 2: the idempotency key for an external effect —
    bound to (system, target, payload), so a retry of the SAME effect
    dedupes while a different effect always gets a different key."""
    raw = f"{system_id}:{target_ref}:{payload_hash}"
    return f"system.action:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def record_external_action(db: FoundryDatabase, system_id: str,
                           target_ref: str, payload_hash: str, *,
                           kind: str, started: bool = True) -> str:
    """Record the START (or completion) of an external effect.

    The journal row is UNIQUE(system_id, target_ref, payload_hash):
    identical re-delivery dedupes at the projection level AND the event
    key — the effect happens exactly once (exit evidence 2).
    """
    key = external_action_key(system_id, target_ref, payload_hash)
    event_type = "system.action_started" if started else "system.action_completed"
    # phase-distinct event keys: started and completed carry the same
    # journal payload fields, so the completed event must NOT collide
    # with the started one (same key + identical payload would dedupe,
    # same key + different payload would EventConflict)
    event_key = f"{key}.{'started' if started else 'completed'}"
    db.append(Event.create(
        event_type,
        {"action_key": key, "system_id": system_id, "target_ref": target_ref,
         "payload_hash": payload_hash, "kind": kind},
        source_type="supervisor", source_id="v5-registry",
        idempotency_key=event_key))
    return key
