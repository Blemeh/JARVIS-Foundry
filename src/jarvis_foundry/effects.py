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
import math
import time
import uuid
from enum import StrEnum
from typing import Any, Callable, Mapping

from .control.broker import (BrokerDecision, PolicyBroker,
                             resolve_envelope_targets)
from .control.redaction import contains_secret_like, redact_text
from .clock import isoformat, utc_now
from .database import FoundryDatabase
from .domain import ActionEnvelope
from .events import Event, canonical_json, payload_hash
from .systems import external_action_key

STARTED = "STARTED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
FROZEN = "FROZEN"
_TERMINAL = {COMPLETED, FAILED, FROZEN}
_DEFAULT_CLAIM_TTL_S = 300.0


class EffectClass(StrEnum):
    """The shared V10 effect vocabulary.

    ``Semantic`` describes what an adapter does at a coarse level.  This
    class describes the recovery and retry consequences of doing it.  It is
    intentionally owned by the existing effect module so Fabric metadata and
    MissionLoop receipts cannot grow a second effect authority.
    """

    READ_ONLY = "READ_ONLY"
    REVERSIBLE_LOCAL = "REVERSIBLE_LOCAL"
    REVERSIBLE_EXTERNAL = "REVERSIBLE_EXTERNAL"
    IRREVERSIBLE_OR_HIGH_COST = "IRREVERSIBLE_OR_HIGH_COST"


def infer_effect_class(
    action_kind: str,
    semantic: str | None = None,
    target_ref: str | None = None,
) -> EffectClass:
    """Map an existing action/semantic to the V10 recovery vocabulary.

    This is deliberately conservative: network/provider mutations are never
    treated as local, and communication sends are high-consequence by
    default.  Callers may supply an explicit descriptor class when a domain
    adapter has a narrower, verified policy.
    """

    action = str(action_kind or "").strip().lower()
    semantic_value = str(semantic or "").strip().lower()
    if action in {
        "gmail.send", "mail.send", "email.send", "git.push",
        "github.pull_request.create", "github.pull_request.publish",
    }:
        return EffectClass.IRREVERSIBLE_OR_HIGH_COST
    if semantic_value == "read" or action.endswith(".read") or action in {
        "dir.list", "git.inspect", "git.diff", "git.log", "git.read_ref",
        "system.diagnose", "system.runtime", "hash.file.sha256",
    }:
        return EffectClass.READ_ONLY
    if (
        semantic_value in {"external_effect", "communicate"}
        or action.startswith(("github.", "gmail.", "http."))
        and not action.endswith(".read")
        or (target_ref or "").lower().startswith(("http://", "https://"))
        and not action.endswith(".read")
    ):
        return EffectClass.REVERSIBLE_EXTERNAL
    return EffectClass.REVERSIBLE_LOCAL


def fabric_effect_id(
    *,
    system_id: str,
    target_ref: str,
    capability_id: str,
    capability_version: str,
    action_kind: str,
    input_payload: Any,
    before_state_ref: str | None = None,
) -> str:
    """Return a stable, secret-safe effect identity for a Fabric request.

    Only the canonical hash crosses this boundary.  The identity includes
    exact capability/version and input material so two adapters cannot
    accidentally share a replay key while a retry/restart of one request is
    stable.  Mission/workflow scope belongs in the separate idempotency key.
    """

    material = {
        "system_id": str(system_id),
        "target_ref": str(target_ref),
        "capability_id": str(capability_id),
        "capability_version": str(capability_version),
        "action_kind": str(action_kind),
        "input": input_payload,
        "before_state_ref": before_state_ref,
    }
    digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    return f"effect:{digest}"


def fabric_idempotency_key(
    effect_id: str,
    *,
    mission_id: str,
    workflow_id: str,
    step_id: str,
) -> str:
    """Derive a stable connector replay key without exposing request data."""

    material = {
        "effect_id": str(effect_id),
        "mission_id": str(mission_id),
        "workflow_id": str(workflow_id),
        "step_id": str(step_id),
    }
    digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    return f"idempotency:{digest}"


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
    abandoned_events = _event_payloads(database, "effect.abandoned", action_key)
    terminal_truth = _event_payloads(
        database, "effect.terminal_truth", action_key,
    )
    if journal is None and not started:
        state["status"] = "UNKNOWN"
        return state
    # journal-row truth when present; otherwise derive from the events
    state["status"] = journal["status"] if journal is not None else STARTED
    if state["status"] == STARTED and abandoned_events:
        # The claim was closed as never-performed. A crash between the
        # abandonment event and the journal update must not resurrect it as
        # a live STARTED claim that a later pass could try to perform.
        state["status"] = FAILED
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
        state["claimed_at"] = started[-1].get("claimed_at")
        state["claim_expires_at"] = started[-1].get("claim_expires_at")
        state["request_identity"] = started[-1].get("request_identity")
        try:
            state["claim_active"] = (
                state["status"] == STARTED
                and state["claim_expires_at"] is not None
                and float(state["claim_expires_at"]) > time.time()
            )
        except (TypeError, ValueError):
            state["claim_active"] = False
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
    if terminal_truth:
        truth = terminal_truth[-1]
        state["terminal_outcome"] = truth.get("outcome")
        state["accepted"] = bool(truth.get("accepted"))
        state["reconciled"] = bool(truth.get("reconciled"))
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


def _claim_effect(database: FoundryDatabase, *, action_key: str,
                  start_payload: dict[str, Any], claim_token: str,
                  claim_ttl_s: float) -> tuple[str, dict[str, Any]]:
    """Atomically claim a never-started effect or observe its current claim.

    The transaction closes the read-then-start race between workers. An
    unexpired claim is returned as UNKNOWN to a competing worker. A claim
    without an expiry, or with an expired expiry, is stale and may be reaped;
    it is never executed a second time.
    """
    if (not isinstance(claim_ttl_s, (int, float)) or
            isinstance(claim_ttl_s, bool) or
            not math.isfinite(float(claim_ttl_s)) or claim_ttl_s <= 0):
        raise ValueError("claim_ttl_s must be a finite positive number")
    now = time.time()
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT status FROM system_actions WHERE action_key = ?",
            (action_key,)).fetchone()
        terminal_rows = connection.execute(
            "SELECT event_type FROM events WHERE event_type IN "
            "('system.action_completed', 'system.action_failed', "
            "'effect.frozen') AND json_extract(payload_json, "
            "'$.action_key') = ? ORDER BY sequence",
            (action_key,)).fetchall()
        if row is not None and row[0] in _TERMINAL:
            return "TERMINAL", {"status": row[0]}
        if terminal_rows:
            status = {
                "system.action_completed": COMPLETED,
                "system.action_failed": FAILED,
                "effect.frozen": FROZEN,
            }[terminal_rows[0][0]]
            return "TERMINAL", {"status": status}

        started = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'system.action_started' AND json_extract(payload_json, "
            "'$.action_key') = ? ORDER BY sequence DESC LIMIT 1",
            (action_key,)).fetchone()
        if started is not None:
            observed = dict(json.loads(started[0]))
            try:
                active = (observed.get("claim_expires_at") is not None and
                          float(observed["claim_expires_at"]) > now)
            except (TypeError, ValueError):
                active = False
            return ("ACTIVE" if active else "STALE"), observed

        claimed = dict(start_payload)
        claimed.update({
            "claim_token": claim_token,
            "claimed_at": now,
            "claim_expires_at": now + float(claim_ttl_s),
        })
        event = Event.create(
            "system.action_started", claimed,
            source_type="supervisor", source_id="v5-effect-driver",
            idempotency_key=f"{action_key}.started")
        database.append_events_on_connection(connection, [event])
        return "CLAIMED", claimed


def execute_effect(
    database: FoundryDatabase,
    *,
    envelope: ActionEnvelope,
    connector_id: str,
    perform: Callable[[str, str], str],
    stop_path: str | None = None,
    claim_ttl_s: float = _DEFAULT_CLAIM_TTL_S,
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
    claim_mode, observed = _claim_effect(
        database,
        action_key=key,
        start_payload={
            "action_key": key, "system_id": system_id,
            "target_ref": target_ref, "payload_hash": effect_hash,
            "kind": envelope.action_kind, "connector_id": connector_id,
            "before_state_capture": envelope.before_state_capture,
            "expected_effects": list(envelope.expected_effects),
            "action_id": envelope.action_id,
        },
        claim_token=uuid.uuid4().hex,
        claim_ttl_s=claim_ttl_s)
    if claim_mode == "TERMINAL":
        # Idempotent retry semantics: a retried delivery of a terminal
        # effect returns the RECORDED outcome without re-performing
        # (duplicate-effect prevention — the exit evidence gate).
        return {"outcome": observed["status"], "action_key": key,
                "reaped": False, "performed": False}
    if claim_mode == "ACTIVE":
        return {"outcome": "UNKNOWN", "action_key": key,
                "reaped": False, "performed": False,
                "claim_expires_at": observed.get("claim_expires_at")}
    if claim_mode == "STALE":
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

    # The atomic claim above is the only path that reaches the external call.
    after_state: str | None = None
    try:
        after_state = perform(target_ref, envelope.expected_effect or "")
    except TimeoutError as exc:
        reason = f"timeout: {redact_text(str(exc))}"
        _append(database, "effect.reconcile_pending", {
            "action_key": key, "system_id": system_id,
            "target_ref": target_ref, "payload_hash": effect_hash,
            "kind": envelope.action_kind,
            "reason": reason, "provider_state": "UNKNOWN"},
            "pending.timeout", full_payload_key=True)
        _append(database, "effect.frozen", {
            "action_key": key, "reason": (
                f"{reason}; external state is unknown and requires "
                "provider or system reconciliation"),
            "connector_id": connector_id}, "frozen.timeout",
            full_payload_key=True)
        return {"outcome": FROZEN, "action_key": key, "reaped": False,
                "ambiguous": True}
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


def _fabric_effect_result(
    database: FoundryDatabase,
    action_key: str,
    *,
    status: str,
) -> dict[str, Any] | None:
    """Return the safe result recorded by a Fabric-owned journal transition."""

    truths = _event_payloads(database, "effect.terminal_truth", action_key)
    if truths:
        raw_truth = truths[-1].get("result")
        if isinstance(raw_truth, Mapping):
            result = dict(raw_truth)
            result.update({
                "claimed": str(truths[-1].get("outcome") or COMPLETED),
                "action_key": action_key,
                "journal_state": status,
                "performed": False,
                "replayed": True,
                "reconciled": bool(truths[-1].get("reconciled")),
                "accepted": bool(truths[-1].get("accepted")),
                "terminal_truth": True,
            })
            return result

    event_types = (
        "system.action_completed",
        "system.action_failed",
        "effect.frozen",
    )
    candidates: list[dict[str, Any]] = []
    for event_type in event_types:
        candidates.extend(_event_payloads(database, event_type, action_key))
    if not candidates:
        return None
    candidate = min(candidates, key=lambda item: int(item.get("_sequence", 0)))
    raw = candidate.get("result")
    if not isinstance(raw, Mapping):
        return None
    result = dict(raw)
    result.setdefault("claimed", "UNKNOWN" if status == FROZEN else status)
    result.update({
        "action_key": action_key,
        "journal_state": status,
        "performed": False,
        "replayed": True,
    })
    return result


def _safe_effect_evidence(
    value: Mapping[str, Any], *, field_name: str,
) -> dict[str, Any]:
    """Return a bounded secret-safe evidence object for the effect ledger."""

    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    try:
        encoded = canonical_json(dict(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be JSON-safe") from error
    if len(encoded.encode("utf-8")) > 128 * 1024:
        raise ValueError(f"{field_name} exceeds the effect evidence limit")
    if contains_secret_like(encoded):
        raise ValueError(f"{field_name} contains secret-like material")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be an object")
    return decoded


def finalize_verified_effect(
    database: FoundryDatabase,
    action_key: str,
    *,
    provider_receipt: Mapping[str, Any],
    readback: Mapping[str, Any],
    result: Mapping[str, Any],
    acceptance_evidence: Mapping[str, Any] | None = None,
    reconciled: bool = False,
) -> dict[str, Any]:
    """Append provider proof and accepted terminal truth to one journaled effect.

    This is the reconciliation completion seam for irreversible effects.  It
    never rewrites the original ``COMPLETED``/``FROZEN`` projection.  A
    provider receipt, independent matching readback, acceptance record, and
    terminal truth are appended in that order.  A later replay can therefore
    return the accepted result without dispatching again, including when the
    original call timed out after the provider accepted it.
    """

    state = effect_state(database, action_key)
    if state.get("status") not in _TERMINAL:
        raise EffectStateError(
            f"effect {action_key} is {state.get('status')} — terminal "
            "provider proof requires a terminal journal state"
        )
    if state.get("accepted") is True and state.get("terminal_outcome"):
        recorded = _fabric_effect_result(
            database, action_key, status=str(state["status"]),
        )
        if recorded is not None:
            return recorded
    receipt = _safe_effect_evidence(
        provider_receipt, field_name="provider_receipt",
    )
    observed = _safe_effect_evidence(readback, field_name="readback")
    safe_result = _safe_effect_evidence(result, field_name="result")
    acceptance = _safe_effect_evidence(
        acceptance_evidence or {}, field_name="acceptance_evidence",
    )
    if observed.get("status") != "MATCH" or observed.get("matches") is not True:
        raise EffectStateError(
            "independent provider readback must be MATCH before acceptance"
        )
    if not receipt.get("provider_id") or not receipt.get("provider_ref"):
        raise EffectStateError(
            "provider receipt requires provider_id and provider_ref"
        )

    _append(database, "effect.provider_receipt", {
        "action_key": action_key,
        "provider_receipt": receipt,
    }, "provider-receipt", full_payload_key=True)
    _append(database, "effect.readback_observed", {
        "action_key": action_key,
        "readback": observed,
    }, "readback", full_payload_key=True)
    recovered_from_ambiguity = bool(
        state.get("status") == FROZEN or reconciled
    )
    _append(database, "effect.reconciled", {
        "action_key": action_key,
        "provider_claim": observed,
        "provider_receipt": receipt,
        "recovered_from_ambiguity": recovered_from_ambiguity,
    }, "reconciled.accepted", full_payload_key=True)
    _append(database, "effect.acceptance_recorded", {
        "action_key": action_key,
        "accepted": True,
        "provider_id": receipt["provider_id"],
        "provider_ref": receipt["provider_ref"],
        "evidence": acceptance,
    }, "acceptance", full_payload_key=True)
    _append(database, "effect.terminal_truth", {
        "action_key": action_key,
        "outcome": COMPLETED,
        "accepted": True,
        "reconciled": True,
        "recovered_from_ambiguity": recovered_from_ambiguity,
        "provider_id": receipt["provider_id"],
        "provider_ref": receipt["provider_ref"],
        "result": safe_result,
    }, "terminal-truth", full_payload_key=True)
    return {
        **safe_result,
        "claimed": COMPLETED,
        "action_key": action_key,
        "journal_state": state["status"],
        "accepted": True,
        "reconciled": True,
        "recovered_from_ambiguity": recovered_from_ambiguity,
        "terminal_truth": True,
    }


def execute_fabric_effect(
    database: FoundryDatabase,
    *,
    action_key: str,
    system_id: str,
    target_ref: str,
    payload_hash: str,
    kind: str,
    connector_id: str,
    before_state_capture: Mapping[str, Any] | None = None,
    expected_effects: tuple[str, ...] = (),
    action_id: str | None = None,
    request_identity: Mapping[str, Any] | None = None,
    perform: Callable[[], Mapping[str, Any]],
    stop_check: Callable[[], bool] | None = None,
    claim_ttl_s: float = _DEFAULT_CLAIM_TTL_S,
) -> dict[str, Any]:
    """Run a Fabric adapter through the canonical V5 effect journal.

    Fabric already owns capability selection and V10 owns request identity.
    This helper supplies the missing durable claim at that seam by reusing
    system_actions and the existing effect events.  It is not a second
    journal: the same STARTED/COMPLETED/FAILED/FROZEN projection and
    crash-reap rules are used for every Fabric effect.
    """

    key = str(action_key)
    identity = dict(request_identity or {})
    if identity:
        try:
            encoded_identity = canonical_json(identity)
        except (TypeError, ValueError) as error:
            raise ValueError("Fabric effect request identity must be JSON-safe") from error
        if (
            len(encoded_identity.encode("utf-8")) > 16 * 1024
            or contains_secret_like(encoded_identity)
        ):
            raise ValueError("Fabric effect request identity is unsafe to persist")
    state = effect_state(database, key)
    if state.get("status") in _TERMINAL:
        recorded = _fabric_effect_result(
            database, key, status=str(state["status"]),
        )
        if recorded is not None:
            return recorded
        return {
            "claimed": "UNKNOWN" if state["status"] == FROZEN else state["status"],
            "action_key": key,
            "journal_state": state["status"],
            "performed": False,
            "replayed": True,
            "error_code": "terminal_effect_recorded",
        }
    stopped = bool(stop_check is not None and stop_check())
    if stopped and state.get("status") != STARTED:
        return {
            "claimed": "BLOCKED",
            "action_key": key,
            "journal_state": "NOT_STARTED",
            "performed": False,
            "replayed": False,
            "error_code": "stop_requested",
        }
    claim_mode, observed = _claim_effect(
        database,
        action_key=key,
        start_payload={
            "action_key": key,
            "system_id": str(system_id),
            "target_ref": str(target_ref),
            "payload_hash": str(payload_hash),
            "kind": str(kind),
            "connector_id": str(connector_id),
                "before_state_capture": dict(before_state_capture or {}),
                "expected_effects": list(expected_effects),
                "action_id": action_id,
                "request_identity": identity,
            },
        claim_token=uuid.uuid4().hex,
        claim_ttl_s=claim_ttl_s,
    )
    if claim_mode == "TERMINAL":
        recorded = _fabric_effect_result(
            database, key, status=str(observed.get("status") or FROZEN),
        )
        return recorded or {
            "claimed": "UNKNOWN",
            "action_key": key,
            "journal_state": str(observed.get("status") or FROZEN),
            "performed": False,
            "replayed": True,
            "error_code": "terminal_effect_recorded",
        }
    if claim_mode == "ACTIVE":
        return {
            "claimed": "UNKNOWN",
            "action_key": key,
            "journal_state": STARTED,
            "performed": False,
            "replayed": False,
            "error_code": "effect_claim_active",
        }
    if claim_mode == "STALE":
        _append(database, "effect.crash_reaped", {
            "action_key": key,
            "connector_id": str(connector_id),
            "system_id": str(system_id),
            "target_ref": str(target_ref),
            "payload_hash": str(payload_hash),
            "reaped_at": isoformat(),
        }, "crash")
        _append(database, "effect.frozen", {
            "action_key": key,
            "reason": (
                "crash between effect start and completion — "
                "effect may or may not have landed; no second execution"
            ),
            "connector_id": str(connector_id),
        }, "frozen")
        return {
            "claimed": "UNKNOWN",
            "action_key": key,
            "journal_state": FROZEN,
            "performed": False,
            "replayed": False,
            "reaped": True,
            "error_code": "crash_reaped",
        }

    # STOP may arrive in the narrow window after the atomic claim. Close the
    # claim as a deterministic non-effect; never leave it active and never
    # enter the provider after STOP.
    if stop_check is not None and stop_check():
        result = {
            "claimed": "BLOCKED",
            "action_key": key,
            "performed": False,
            "replayed": False,
            "error_code": "stop_requested",
        }
        _append(database, "system.action_failed", {
            "action_key": key,
            "system_id": str(system_id),
            "target_ref": str(target_ref),
            "payload_hash": str(payload_hash),
            "kind": str(kind),
            "reason": "stop_requested_before_dispatch",
            "result": result,
        }, "blocked.stop")
        result["journal_state"] = FAILED
        return result

    try:
        raw = perform()
        if not isinstance(raw, Mapping):
            raise TypeError("Fabric effect performer must return an object")
        result = dict(raw)
        encoded = canonical_json(result)
        if len(encoded.encode("utf-8")) > 128 * 1024 or contains_secret_like(encoded):
            raise ValueError("Fabric effect result is unsafe to persist")
        claimed = str(result.get("claimed") or result.get("outcome") or "UNKNOWN").upper()
        result["claimed"] = claimed
        result["action_key"] = key
        result["performed"] = bool(
            result.get("performed")
            if "performed" in result
            else claimed == "COMPLETED"
        )
        result["replayed"] = False
    except TimeoutError as error:
        result = {
            "claimed": "UNKNOWN",
            "action_key": key,
            "performed": False,
            "replayed": False,
            "error_code": "timeout_after_action",
            "detail": redact_text(str(error))[:300],
        }
        _append(database, "effect.reconcile_pending", {
            "action_key": key,
            "system_id": str(system_id),
            "target_ref": str(target_ref),
            "payload_hash": str(payload_hash),
            "kind": str(kind),
            "reason": "Fabric effect performer timed out",
            "provider_state": "UNKNOWN",
            "result": result,
        }, "pending.timeout", full_payload_key=True)
        _append(database, "effect.frozen", {
            "action_key": key,
            "reason": "effect timed out after claim; reconcile before retry",
            "connector_id": str(connector_id),
            "result": result,
        }, "frozen.timeout", full_payload_key=True)
        result["journal_state"] = FROZEN
        return result
    except BaseException as error:  # noqa: BLE001 - journal every failure
        result = {
            "claimed": "FAILED",
            "action_key": key,
            "performed": False,
            "replayed": False,
            "error_code": "effect_performer_failed",
            "detail": redact_text(str(error))[:300],
        }
        _append(database, "system.action_failed", {
            "action_key": key,
            "system_id": str(system_id),
            "target_ref": str(target_ref),
            "payload_hash": str(payload_hash),
            "kind": str(kind),
            "reason": redact_text(str(error))[:300],
            "result": result,
        }, "failed")
        result["journal_state"] = FAILED
        return result

    if claimed == "COMPLETED":
        _append(database, "system.action_completed", {
            "action_key": key,
            "system_id": str(system_id),
            "target_ref": str(target_ref),
            "payload_hash": str(payload_hash),
            "kind": str(kind),
            "connector_id": str(connector_id),
            "after_state": encoded,
            "result": result,
            "evidence_refs": [f"connector:{connector_id}"],
        }, "completed")
        result["journal_state"] = COMPLETED
        return result
    if claimed == "BLOCKED":
        _append(database, "system.action_failed", {
            "action_key": key,
            "system_id": str(system_id),
            "target_ref": str(target_ref),
            "payload_hash": str(payload_hash),
            "kind": str(kind),
            "reason": str(result.get("error_code") or "blocked"),
            "result": result,
        }, "blocked")
        result["performed"] = False
        result["journal_state"] = FAILED
        return result
    if claimed == "UNKNOWN" or result.get("ambiguous"):
        result["claimed"] = "UNKNOWN"
        _append(database, "effect.reconcile_pending", {
            "action_key": key,
            "system_id": str(system_id),
            "target_ref": str(target_ref),
            "payload_hash": str(payload_hash),
            "kind": str(kind),
            "reason": str(result.get("error_code") or "ambiguous_effect"),
            "provider_state": "UNKNOWN",
            "result": result,
        }, "pending.ambiguous", full_payload_key=True)
        _append(database, "effect.frozen", {
            "action_key": key,
            "reason": "effect outcome is ambiguous; reconcile before retry",
            "connector_id": str(connector_id),
            "result": result,
        }, "frozen.ambiguous", full_payload_key=True)
        result["journal_state"] = FROZEN
        return result
    result["claimed"] = "FAILED"
    _append(database, "system.action_failed", {
        "action_key": key,
        "system_id": str(system_id),
        "target_ref": str(target_ref),
        "payload_hash": str(payload_hash),
        "kind": str(kind),
        "reason": str(result.get("error_code") or "effect_failed"),
        "result": result,
    }, "failed.result")
    result["journal_state"] = FAILED
    return result


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


def abandon_effect(database: FoundryDatabase, action_key: str, *,
                   reason: str, superseded_by: str | None = None) -> None:
    """Close a STARTED effect whose claim must never be performed.

    Used when a run-scoped effect identity is permanently superseded before
    its connector was ever invoked: the durable claim is closed as
    ``FAILED`` so no journal row is left non-terminal, and the reason is
    recorded as history (the payload-keyed event keeps changed reasons as
    new records). Nothing is re-performed and no completion is fabricated.

    Refuses when the effect is already terminal: a completed or failed
    effect is never rewritten by this call.
    """
    state = effect_state(database, action_key)
    if state["status"] != STARTED:
        raise EffectStateError(
            f"effect {action_key} is {state['status']} — abandon only "
            "applies to STARTED effects")
    _append(database, "effect.abandoned", {
        "action_key": action_key,
        "reason": reason,
        "superseded_by": superseded_by,
        "performed": False,
    }, "abandoned", full_payload_key=True)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE system_actions SET status = ?, updated_at = ? "
            "WHERE action_key = ? AND status = ?",
            (FAILED, isoformat(), action_key, STARTED),
        )


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
