"""V5-S6: external-effect execution, idempotency, and reconciliation.

The effect driver executes an ALLOWed envelope through the selected
connector EXACTLY ONCE under a deterministic effect key bound to
(system, canonical target, payload identity). Journal semantics:
STARTED -> COMPLETED/FAILED/FROZEN, monotonic, never regresses; a
crash between start and completion is reaped deterministically (the
effect freezes for review — a second execution would duplicate the
effect); ambiguous outcomes get an append-only compensation path;
provider reconciliation is honest (NOT_TESTED without a real
provider — R-EXT-01).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from .control.broker import (BrokerDecision, PolicyBroker,
                             resolve_envelope_targets)
from .control.redaction import redact_text
from .database import FoundryDatabase
from .domain import ActionEnvelope
from .events import Event, canonical_json, payload_hash
from .systems import external_action_key

STARTED = "STARTED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
FROZEN = "FROZEN"
_TERMINAL = {COMPLETED, FAILED, FROZEN}


class EffectStateError(Exception):
    """A deterministic effect-state refusal (terminal effects are never
    re-executed; freezes only apply to STARTED effects; reconcile needs
    a terminal state)."""


def effect_identity(database: FoundryDatabase,
                    envelope: ActionEnvelope) -> tuple[str, str, str, str]:
    """Deterministic effect identity: (action_key, system_id, target_ref,
    payload_hash).

    The key is bound to the system, the canonical target, and the
    payload identity — a retry of the SAME effect dedupes while a
    different effect always gets a different key.
    """
    targets = resolve_envelope_targets(database, envelope)
    resolved = targets[0]
    if resolved.startswith("system:"):
        system_id = resolved.split(":", 1)[1]
    else:
        system_id = "file"
    target_ref = envelope.target_resources[0] \
        if envelope.target_resources else resolved
    payload = {
        "action_kind": envelope.action_kind,
        "target_resources": list(envelope.target_resources),
        "expected_effect": envelope.expected_effect,
        "expected_effects": list(envelope.expected_effects),
        "before_state_capture": envelope.before_state_capture,
    }
    effect_hash = hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()
    return (external_action_key(system_id, target_ref, effect_hash),
            system_id, target_ref, effect_hash)


def _journal_row(database: FoundryDatabase,
                 action_key: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM system_actions WHERE action_key = ?",
            (action_key,)).fetchone()
    return None if row is None else {"status": row[0]}


def _event_payloads(database: FoundryDatabase, event_type: str,
                    action_key: str) -> list[dict[str, Any]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT payload_json, sequence FROM events WHERE event_type = ? "
            "AND json_extract(payload_json, '$.action_key') = ? "
            "ORDER BY sequence", (event_type, action_key)).fetchall()
    return [dict(json.loads(row[0]), _sequence=row[1]) for row in rows]


def _terminal_events(payloads: dict[str, list[dict[str, Any]]]) \
        -> tuple[str, dict[str, Any]] | None:
    """The FIRST terminal event in sequence order — mirrors the
    projection's transition guards (each terminal transition applies
    only from STARTED, so the first terminal event wins)."""
    candidates = [(event["_sequence"], kind)
                  for kind in ("system.action_completed",
                               "system.action_failed", "effect.frozen")
                  for event in payloads[kind]]
    if not candidates:
        return None
    _, kind = min(candidates)
    return kind, payloads[kind][0]


def effect_state(database: FoundryDatabase,
                 action_key: str) -> dict[str, Any]:
    """Derived effect state: journal status + before/after state,
    failure reason, freeze/compensation events — rebuilt from the
    append-only stream, identical after replay.

    The events are the durable truth: when the journal ROW is missing
    but the events exist (rebuild-only window, §10.4 — S6 verifier
    deleg_2d03fb7f M2), the state derives from the events so a crash
    can never be mistaken for a fresh effect (which would re-perform).
    """
    state: dict[str, Any] = {"action_key": action_key}
    journal = _journal_row(database, action_key)
    started = _event_payloads(database, "system.action_started", action_key)
    completed = _event_payloads(database, "system.action_completed",
                                action_key)
    failed = _event_payloads(database, "system.action_failed", action_key)
    frozen = _event_payloads(database, "effect.frozen", action_key)
    compensated = _event_payloads(database, "effect.compensated", action_key)
    if journal is None and not started:
        state["status"] = "UNKNOWN"
        return state
    # journal-row truth when present; otherwise derive from the events
    state["status"] = journal["status"] if journal is not None else STARTED
    if journal is None:
        mismatch = _event_payloads(database, "effect.reconcile_mismatch",
                                   action_key)
        if mismatch:
            state["status"] = FROZEN  # strongest: applies to any terminal
        else:
            terminal = _terminal_events({
                "system.action_completed": completed,
                "system.action_failed": failed,
                "effect.frozen": frozen,
            })
            if terminal is not None:
                state["status"] = {
                    "system.action_completed": COMPLETED,
                    "system.action_failed": FAILED,
                    "effect.frozen": FROZEN,
                }[terminal[0]]
        if compensated:
            state["status"] = FROZEN
    if started:
        state["before_state"] = started[-1].get("before_state_capture")
        state["expected_effects"] = started[-1].get("expected_effects")
    if completed:
        state["after_state"] = completed[-1].get("after_state")
        state["cost"] = completed[-1].get("cost")
    if failed:
        state["failure_reason"] = failed[-1].get("reason")
        if "after_state" in failed[-1]:
            state["after_state"] = failed[-1]["after_state"]
    if frozen:
        state["frozen_reason"] = frozen[-1].get("reason")
    if compensated:
        state["compensation_ref"] = compensated[-1].get("compensation_ref")
    return state


def _append(database: FoundryDatabase, event_type: str, payload: dict[str, Any],
            key_suffix: str, *, full_payload_key: bool = False) -> None:
    """Append an effect event. ``full_payload_key`` events (frozen/
    compensated/reconciled/mismatch) record a CHANGED observation as
    NEW history instead of an EventConflict (S6 verifier deleg_2d03fb7f
    L1) while identical re-delivery still dedupes."""
    if full_payload_key:
        key = f"{payload['action_key']}.{key_suffix}." \
              f"{payload_hash(payload)}"
    else:
        key = f"{payload['action_key']}.{key_suffix}"
    database.append(Event.create(
        event_type, payload,
        source_type="supervisor", source_id="v5-effect-driver",
        idempotency_key=key))


def execute_effect(
    database: FoundryDatabase,
    *,
    envelope: ActionEnvelope,
    connector_id: str,
    perform: Callable[[str, str], str],
    stop_path: str | None = None,
) -> dict[str, Any]:
    """Execute an ALLOWed envelope through a connector EXACTLY ONCE.

    The ALLOW ticket must ALREADY exist (the mission loop's decide
    created it — this driver NEVER records a decision of its own; a
    budget-exceeded or non-ALLOW decision means no ticket and a
    PermissionError here). Deterministic order: ticket -> fresh
    STOP/veto re-check -> effect identity -> crash-window reap (STARTED
    without terminal = FROZEN, never re-executed) -> terminal-state
    refusal -> start journal -> perform -> completion or failed journal.
    Failure reasons are redacted; financial consequences are recorded.
    """
    from .control.broker import (emergency_stop_reason,
                                 matching_veto_reason,
                                 normalize_action_kind,
                                 require_allowance_for_envelope,
                                 veto_identities_for)
    require_allowance_for_envelope(database, envelope)
    stop = emergency_stop_reason(database, stop_path)
    if stop:
        raise PermissionError(f"effect execution stopped: {stop}")
    targets = resolve_envelope_targets(database, envelope)
    veto = matching_veto_reason(
        database, veto_identities_for(
            envelope, normalize_action_kind(envelope.action_kind), targets))
    if veto:
        raise PermissionError(f"effect execution vetoed: {veto}")
    key, system_id, target_ref, effect_hash = effect_identity(
        database, envelope)
    state = effect_state(database, key)
    if state["status"] in _TERMINAL:
        # Idempotent retry semantics: a retried delivery of a terminal
        # effect returns the RECORDED outcome without re-performing
        # (duplicate-effect prevention — the exit evidence gate).
        return {"outcome": state["status"], "action_key": key,
                "reaped": False, "performed": False}
    if state["status"] == STARTED:
        # crash between start and completion: the previous attempt never
        # reached a terminal event. Reap deterministically — the effect
        # freezes for review; NO second execution.
        _append(database, "effect.crash_reaped", {
            "action_key": key, "connector_id": connector_id,
            "system_id": system_id, "target_ref": target_ref,
            "payload_hash": effect_hash,
            "reaped_at": envelope.created_at}, "crash")
        _append(database, "effect.frozen", {
            "action_key": key,
            "reason": ("crash between start and completion — reaped; "
                       "effect may or may not have landed provider-side; "
                       "compensation recorded append-only"),
            "connector_id": connector_id}, "frozen")
        return {"outcome": FROZEN, "action_key": key, "reaped": True,
                "connector_id": connector_id}

    # first execution: START journal with before-state + effects
    _append(database, "system.action_started", {
        "action_key": key, "system_id": system_id, "target_ref": target_ref,
        "payload_hash": effect_hash, "kind": envelope.action_kind,
        "connector_id": connector_id,
        "before_state_capture": envelope.before_state_capture,
        "expected_effects": list(envelope.expected_effects),
        "action_id": envelope.action_id}, "started")
    after_state: str | None = None
    try:
        after_state = perform(target_ref, envelope.expected_effect or "")
    except TimeoutError as exc:
        _append(database, "system.action_failed", {
            "action_key": key, "system_id": system_id,
            "target_ref": target_ref, "payload_hash": effect_hash,
            "kind": envelope.action_kind,
            "reason": f"timeout: {redact_text(str(exc))}",
            "after_state": after_state}, "failed.timeout")
        return {"outcome": FAILED, "action_key": key, "reaped": False}
    except BaseException as exc:  # noqa: BLE001 — the driver records ALL failures
        _append(database, "system.action_failed", {
            "action_key": key, "system_id": system_id,
            "target_ref": target_ref, "payload_hash": effect_hash,
            "kind": envelope.action_kind,
            "reason": redact_text(str(exc)),
            "after_state": after_state}, "failed")
        return {"outcome": FAILED, "action_key": key, "reaped": False}
    _append(database, "system.action_completed", {
        "action_key": key, "system_id": system_id, "target_ref": target_ref,
        "payload_hash": effect_hash, "kind": envelope.action_kind,
        "connector_id": connector_id, "after_state": after_state,
        "cost": envelope.budget_estimate,
        "consequence": {"expected_effects": list(envelope.expected_effects),
                        "verification_required": envelope.verification_required},
        "evidence_refs": [f"connector:{connector_id}",
                          f"action:{envelope.action_id}"]}, "completed")
    return {"outcome": COMPLETED, "action_key": key, "reaped": False,
            "after_state": after_state}


def freeze_effect(database: FoundryDatabase, action_key: str, *,
                  reason: str) -> None:
    """Freeze a STARTED effect for review (ambiguous outcome). A
    completed/failed/frozen effect is never frozen by request — the
    refusal is deterministic."""
    state = effect_state(database, action_key)
    if state["status"] != STARTED:
        raise EffectStateError(
            f"effect {action_key} is {state['status']} — freeze only "
            "applies to STARTED effects")
    _append(database, "effect.frozen", {
        "action_key": action_key, "reason": reason}, "frozen",
        full_payload_key=True)


def compensate_effect(database: FoundryDatabase, action_key: str, *,
                      compensation_ref: str) -> None:
    """Record the append-only compensation of a FROZEN effect. The
    journal stays FROZEN — the correction is an event, never a
    rewrite of the original effect record."""
    state = effect_state(database, action_key)
    if state["status"] != FROZEN:
        raise EffectStateError(
            f"effect {action_key} is {state['status']} — compensation "
            "only applies to FROZEN effects")
    _append(database, "effect.compensated", {
        "action_key": action_key, "compensation_ref": compensation_ref},
        "compensated", full_payload_key=True)


def reconcile_effect(database: FoundryDatabase, action_key: str, *,
                     provider_state: dict[str, Any]) -> dict[str, Any]:
    """Reconcile a terminal effect against PROVIDER-SIDE truth.

    Only callable with a REAL provider evidence object (none exists on
    this host — recorded NOT_TESTED at the slice close). Matching
    provider state confirms the effect; a mismatch freezes it (the
    provider disagrees with the local claim — stronger than a request).
    """
    state = effect_state(database, action_key)
    if state["status"] not in _TERMINAL:
        raise EffectStateError(
            f"effect {action_key} is {state['status']} — reconcile only "
            "applies to terminal effects")
    claimed = state.get("after_state")
    provider_exists = bool(provider_state.get("exists"))
    matches = provider_exists and (
        claimed is None or
        provider_state.get("content_hash") == claimed)
    if matches:
        _append(database, "effect.reconciled", {
            "action_key": action_key,
            "provider_claim": provider_state}, "reconciled",
            full_payload_key=True)
        return {"reconciled": True, "action_key": action_key}
    _append(database, "effect.reconcile_mismatch", {
        "action_key": action_key,
        "local_claim": claimed, "provider_claim": provider_state},
        "reconcile-mismatch", full_payload_key=True)
    _append(database, "effect.frozen", {
        "action_key": action_key,
        "reason": "provider reconciliation mismatch — effect frozen "
                  "for review"}, "frozen.reconcile", full_payload_key=True)
    return {"reconciled": False, "action_key": action_key}
