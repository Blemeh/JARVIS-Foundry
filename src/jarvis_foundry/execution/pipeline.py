"""V5-S7: mission-loop integration — the connector-execution pipeline.

The full deterministic control path any mission loop calls for actions
against systems: observe (correlated context manifest + connector
state) -> select (frozen scores, eligible = registered system + healthy
connector + READY capability) -> envelope -> authorize (broker ALLOW
ticket + record_decision + S3 capability issuance) -> execute (the S6
driver: one execution under the effect key, fresh STOP/veto, crash
reap) -> reconcile (journal vs provider evidence; honest NOT_TESTED
without a provider) -> verify -> learn (outcome events with the
transparent JARVIS representational identity).

Properties: one connector's outage never stops unrelated candidates
(isolated per candidate); STOP blocks queued AND in-flight work; the
effect journal is the resume point (interruption reaps, never
duplicates); running the same mission twice never re-performs an
effect; selection is deterministic (max frozen score, tie -> lowest
action id — scores are never re-scored, D-035).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ..control.broker import (BrokerDecision, PolicyBroker,
                              emergency_stop_reason, record_decision,
                              resolve_envelope_targets)
from ..control.capability_broker import (CapabilityError,
                                         issue_capability_for_envelope,
                                         use_capability)
from ..control.redaction import redact_text
from ..correlation.events import build_cross_system_manifest
from ..database import FoundryDatabase
from ..domain import ActionEnvelope
from ..effects import (COMPLETED, FAILED, FROZEN, STARTED, execute_effect,
                       reconcile_effect)
from ..events import Event, payload_hash
from ..systems import systems_allowed_by_policy

#: Transparent representational identity (roadmap §8: JARVIS acting for
#: the owner — recorded on every outcome, never hidden).
IDENTITY = {"name": "jarvis", "role": "representational",
            "acting_for": "owner"}

_REFUSED = "REFUSED"
_SKIPPED = "NOT_READY"


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    action_id: str
    action_kind: str
    target_resources: tuple[str, ...]
    capability: str
    expected_effect: str
    connector_id: str
    expected_effects: tuple[str, ...] = ()
    before_state_capture: dict[str, Any] = field(default_factory=dict)
    risk_class: str = "C1"
    reversibility: str = "reversible"
    budget_estimate: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    goal_alignment: str = ""


def build_envelope(candidate: ActionCandidate, *, mission_id: str,
                   run_id: str) -> ActionEnvelope:
    """The deterministic envelope for a candidate (broker-contract
    fields fully populated; step 0 of the pipeline).

    Ingestion boundary guard (S7 verifier deleg_0b2cbd47 M2): free-text
    candidate fields are written into the envelope and the ledger
    verbatim — secret-like content is REFUSED here, exactly like the
    S1 registry boundary (_reject_secrets), so secrets can never enter
    the append-only stream through the mission loop.
    """
    from ..control.redaction import contains_secret_like
    free_text = {
        "goal_alignment": candidate.goal_alignment,
        "expected_effect": candidate.expected_effect,
        "expected_effects": " ".join(candidate.expected_effects),
        "before_state_capture": str(candidate.before_state_capture),
        "action_kind": candidate.action_kind,
        "capability": candidate.capability,
        "budget_estimate": " ".join(candidate.budget_estimate),
        **{f"target:{i}": t for i, t in enumerate(candidate.target_resources)},
    }
    for field, value in free_text.items():
        if contains_secret_like(value):
            raise ValueError(
                f"candidate {candidate.action_id} field {field} carries "
                "secret-like content — refused at the mission-loop "
                "boundary (secrets never enter the ledger)")
    return ActionEnvelope(
        action_id=candidate.action_id,
        mission_id=mission_id,
        resource_id=candidate.target_resources[0]
        if candidate.target_resources else "",
        action_kind=candidate.action_kind,
        target=None,
        capability=candidate.capability,
        expected_effects=candidate.expected_effects
        or (candidate.expected_effect,),
        risk_class=candidate.risk_class,
        verification_required=True,
        rollback={"method": "freeze"},
        step_id=f"{mission_id}:{candidate.action_id}",
        requested_by_agent_run_id=run_id,
        target_resources=candidate.target_resources,
        capabilities_required=(candidate.capability,),
        goal_alignment=candidate.goal_alignment or mission_id,
        active_owner_vetoes_checked=(),
        expected_effect=candidate.expected_effect,
        reversibility=candidate.reversibility,
        before_state_capture=candidate.before_state_capture,
        verification_plan={"kind": "deterministic"},
        rollback_plan={"strategy": "freeze",
                       "steps": ("record", "freeze for review")},
        idempotency_key=f"{mission_id}:{candidate.action_id}",
        budget_estimate=candidate.budget_estimate,
        credential_requirements=(),
        created_at=None,
    )


# ---------------------------------------------------------------------------
# observe -> select
# ---------------------------------------------------------------------------

def observe(database: FoundryDatabase, mission_id: str) -> dict[str, Any]:
    """Bounded observation: the correlated context manifest (S5) + the
    registered connectors with derived health/quota (S4) + policy-
    allowed systems (S1)."""
    manifest = build_cross_system_manifest(
        database, mission_id, max_entities=20, max_events=100)
    connectors: dict[str, dict[str, Any]] = {}
    from ..connectors.registry import connector_status, _registered_ids
    for connector_id in _registered_ids(database):
        try:
            status = connector_status(database, connector_id)
        except Exception:  # noqa: BLE001 — observation never crashes
            status = {"connector_id": connector_id, "health": "UNKNOWN"}
        connectors[connector_id] = {
            "health": status.get("health"), "quota": status.get("quota"),
            "disabled": status.get("disabled"),
            "actions": status.get("actions"),
            "capabilities": status.get("capabilities"),
        }
    return {"mission_id": mission_id, "manifest": manifest,
            "connectors": connectors,
            "systems_allowed": systems_allowed_by_policy(
                database, "file.write")}


def _eligible(database: FoundryDatabase, candidate: ActionCandidate) -> str | None:
    """Deterministic eligibility: the connector must be registered,
    HEALTHY, not disabled, declare the action with the capability READY,
    and the target system must be registered."""
    from ..connectors.registry import connector_status
    try:
        status = connector_status(database, candidate.connector_id)
    except Exception as exc:  # noqa: BLE001
        return f"connector {candidate.connector_id} unavailable: {exc}"
    if status["health"] != "HEALTHY":
        return (f"connector {candidate.connector_id} health is "
                f"{status['health']}")
    if status["disabled"]:
        return f"connector {candidate.connector_id} is disabled"
    declared = status["actions"].get(candidate.action_kind)
    if declared is None:
        return (f"connector {candidate.connector_id} does not declare "
                f"{candidate.action_kind}")
    if status["capabilities"].get(candidate.capability) not in \
            ("READY", "ACTIVE"):
        return (f"connector {candidate.connector_id} capability "
                f"{candidate.capability} not READY")
    for target in candidate.target_resources:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT capabilities_json FROM systems WHERE system_id = ?",
                (target,)).fetchone()
        if row is None:
            return f"system {target} is not registered"
        system_caps = json.loads(row[0] or "{}")
        if system_caps.get(candidate.capability) not in ("READY", "ACTIVE"):
            return (f"system {target} capability {candidate.capability} "
                    f"is {system_caps.get(candidate.capability)}")
    return None


def select_candidate(database: FoundryDatabase,
                     candidates: list[ActionCandidate]) -> ActionCandidate | None:
    """Deterministic selection: FROZEN scores (never re-scored), the
    highest eligible score wins, ties break on the lowest action id."""
    eligible = [c for c in candidates if _eligible(database, c) is None]
    if not eligible:
        return None
    return sorted(eligible, key=lambda c: (-c.score, c.action_id))[0]


# ---------------------------------------------------------------------------
# authorize -> execute -> reconcile -> verify -> learn
# ---------------------------------------------------------------------------

def authorize(database: FoundryDatabase, envelope: ActionEnvelope, *,
              budget: dict[str, float] | None = None,
              stop_path: str | None = None) -> str:
    """The full authorization path: deterministic decide -> the ALLOW
    ticket (record_decision) -> the S3 short-lived capability bound to
    the envelope. Returns the capability id. Never records on
    non-ALLOW; STOP/veto refuse at execution time too (the driver's
    fresh re-check)."""
    outcome = PolicyBroker(database, stop_path).decide(envelope, budget=budget)
    if outcome.decision != BrokerDecision.ALLOW:
        raise PermissionError(
            f"action {envelope.action_id} not allowed: "
            f"{outcome.decision.value} — {redact_text(outcome.reason)}")
    record_decision(database, envelope, outcome)
    # Idempotent issuance: the capability id derives from the envelope
    # identity (capability_broker's derivation), so a re-delivery of the
    # same envelope reuses the existing capability — never a second
    # issued event with a fresh timestamp under the fixed key.
    import hashlib
    from ..control.capability_broker import capability_state
    raw = f"{envelope.action_id}:{envelope.idempotency_key or ''}"
    capability_id = "cap-" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:16]
    if capability_state(database, capability_id) == "UNKNOWN":
        capability = issue_capability_for_envelope(database, envelope)
        capability_id = capability.capability_id
    return capability_id


def execute(database: FoundryDatabase, envelope: ActionEnvelope,
            capability_id: str, *, connector_id: str,
            perform: Callable[[str, str], str],
            stop_path: str | None = None) -> dict[str, Any]:
    """Execute the authorized envelope exactly once through the
    connector. Idempotent re-delivery: a terminal effect returns the
    recorded outcome without touching the capability; a crash-window
    reap performs NOTHING (no capability consumption); only a FRESH
    execution consumes the S3 capability (proving the scoped authority
    is alive and in scope at execution time)."""
    from ..effects import effect_identity, effect_state
    key, _, _, _ = effect_identity(database, envelope)
    state = effect_state(database, key)
    if state["status"] in (COMPLETED, FAILED, FROZEN):
        return {"outcome": state["status"], "action_key": key,
                "performed": False, "reaped": False,
                "after_state": state.get("after_state"),
                "cost": state.get("cost"),
                "failure_reason": state.get("failure_reason"),
                "frozen_reason": state.get("frozen_reason")}
    if state["status"] == STARTED:
        # crash between start and completion: the driver reaps to FROZEN
        # and performs nothing — no authority consumption for a no-op.
        # Normalized here (early return): same shape as every other path.
        reaped = execute_effect(database, envelope=envelope,
                                connector_id=connector_id, perform=perform,
                                stop_path=stop_path)
        reaped["performed"] = False
        reaped.setdefault("cost", None)
        return reaped
    # Fresh execution: the S6 ticket/STOP/veto gates run BEFORE the
    # capability is consumed (S7 verifier deleg_0b2cbd47 I1 — a foreign
    # envelope must never burn a legitimate capability), then the
    # capability proves the scoped authority at execution time.
    from ..control.broker import (emergency_stop_reason, matching_veto_reason,
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
    target = targets[0]
    try:
        use_capability(
            database, capability_id,
            mission_id=envelope.mission_id,
            agent_run_id=envelope.requested_by_agent_run_id,
            action_kind=envelope.action_kind,
            target=target, stop_path=stop_path)
    except CapabilityError as exc:
        raise PermissionError(
            f"capability {capability_id} refused at execution: {exc}")
    executed = execute_effect(database, envelope=envelope,
                              connector_id=connector_id, perform=perform,
                              stop_path=stop_path)
    # Uniform result shape from ONE source of truth (S7 round-2
    # deleg_8b1eef76 M1′): cost/after_state are derived from the journal
    # exactly like the re-delivery path — a fresh FAILED and its
    # re-delivery record IDENTICAL learn payloads (no drift).
    state = effect_state(database, executed["action_key"])
    executed["performed"] = not bool(executed.get("reaped"))
    executed["cost"] = state.get("cost")
    if "after_state" not in executed or executed["after_state"] is None:
        executed["after_state"] = state.get("after_state")
    return executed


def reconcile(database: FoundryDatabase, action_key: str, *,
              provider_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Journal vs provider-side evidence. Without a real provider the
    honest record is NOT_TESTED (R-EXT-01) — the effect's own journal
    is the verified claim."""
    if provider_state is None:
        database.append(Event.create(
            "effect.reconcile_not_tested",
            {"action_key": action_key,
             "reason": "no real provider exists on this host — provider-"
                       "side reconciliation NOT_TESTED (R-EXT-01)"},
            source_type="supervisor", source_id="v5-pipeline",
            idempotency_key=f"effect.reconcile_not_tested:{action_key}"))
        return {"reconciled": None, "action_key": action_key,
                "status": "NOT_TESTED"}
    return reconcile_effect(database, action_key,
                            provider_state=provider_state)


def verify(database: FoundryDatabase, action_key: str,
           envelope: ActionEnvelope) -> dict[str, Any]:
    """Deterministic verification: the effect journal must be terminal
    and the recorded after-state present for a COMPLETED claim."""
    from ..effects import effect_state
    state = effect_state(database, action_key)
    verified = state["status"] == COMPLETED and \
        state.get("after_state") is not None
    database.append(Event.create(
        "pipeline.verification.recorded",
        {"action_key": action_key,
         "action_id": envelope.action_id,
         "verified": int(verified),
         "journal_status": state["status"],
         "verification_required": int(envelope.verification_required)},
        source_type="supervisor", source_id="v5-pipeline",
        idempotency_key=f"pipeline.verification.recorded:{action_key}"))
    return {"action_key": action_key, "verified": verified,
            "journal_status": state["status"]}


def learn(database: FoundryDatabase, mission_id: str, action_id: str,
          result: dict[str, Any]) -> dict[str, Any]:
    """Record the outcome with the transparent identity, cost, and
    consequence fields."""
    payload = {
        "mission_id": mission_id,
        "action_id": action_id,
        "outcome": result.get("outcome"),
        "action_key": result.get("action_key"),
        "cost": result.get("cost"),
        "identity": IDENTITY,
        "consequence": result.get("after_state"),
    }
    database.append(Event.create(
        "mission.outcome.recorded", payload,
        source_type="supervisor", source_id="v5-pipeline",
        idempotency_key=(f"mission.outcome.recorded:{mission_id}:"
                         f"{action_id}:{payload_hash(payload)}")))
    return payload


def run_candidate(
    database: FoundryDatabase, mission_id: str, run_id: str,
    candidate: ActionCandidate, *, connector_id: str,
    perform: Callable[[str, str], str],
    budget: dict[str, float] | None = None,
    stop_path: str | None = None,
) -> dict[str, Any]:
    """One candidate through the full pipeline. Outage isolation: any
    failure in this candidate returns a REFUSED/FAILED result — it can
    never raise into the mission loop or block other candidates."""
    try:
        envelope = build_envelope(candidate, mission_id=mission_id,
                                  run_id=run_id)
    except ValueError as exc:
        result = {"action_id": candidate.action_id,
                  "outcome": _REFUSED, "reason": redact_text(str(exc))}
        learn(database, mission_id, candidate.action_id, result)
        return result
    try:
        capability_id = authorize(database, envelope, budget=budget,
                                  stop_path=stop_path)
    except PermissionError as exc:
        result = {"action_id": candidate.action_id,
                  "outcome": _REFUSED, "reason": str(exc)}
        learn(database, mission_id, candidate.action_id, result)
        return result
    try:
        executed = execute(database, envelope, capability_id,
                           connector_id=connector_id, perform=perform,
                           stop_path=stop_path)
    except PermissionError as exc:
        result = {"action_id": candidate.action_id,
                  "outcome": _REFUSED, "reason": str(exc)}
        learn(database, mission_id, candidate.action_id, result)
        return result
    except Exception as exc:  # noqa: BLE001 — outage isolation
        result = {"action_id": candidate.action_id,
                  "outcome": FAILED,
                  "reason": redact_text(str(exc))}
        learn(database, mission_id, candidate.action_id, result)
        return result
    if executed["outcome"] in (COMPLETED, FAILED, FROZEN):
        reconcile(database, executed["action_key"])
        if executed["outcome"] == COMPLETED:
            verify(database, executed["action_key"], envelope)
    result = dict(executed)
    result["action_id"] = candidate.action_id
    learn(database, mission_id, candidate.action_id, result)
    return result


def run_mission(
    database: FoundryDatabase, mission_id: str, run_id: str,
    candidates: list[ActionCandidate], *, perform_map: dict[str, Callable],
    budget: dict[str, float] | None = None,
    stop_path: str | None = None,
) -> dict[str, Any]:
    """One mission through the pipeline. Deterministic order: each
    candidate is selected (highest frozen score first), then executed;
    a fresh STOP blocks every queued candidate (REFUSED) without
    touching completed work; the effect journal is the resume point."""
    results: list[dict[str, Any]] = []
    from ..effects import effect_identity, effect_state
    for candidate in sorted(candidates,
                            key=lambda c: (-c.score, c.action_id)):
        try:
            envelope = build_envelope(candidate, mission_id=mission_id,
                                      run_id=run_id)
        except ValueError as exc:
            # boundary refusal (M2): a secret-bearing candidate is
            # REFUSED in isolation — it can never crash the mission
            result = {"action_id": candidate.action_id,
                      "outcome": _REFUSED,
                      "reason": redact_text(str(exc))}
            results.append(result)
            learn(database, mission_id, candidate.action_id, result)
            continue
        # Idempotent re-delivery FIRST: a terminal effect's recorded
        # outcome returns even under STOP (STOP blocks NEW work — it can
        # never un-do or re-run completed effects). The result carries
        # the RECORDED details so learn()'s payload-hash key matches the
        # original outcome event (no event drift — S7 verifier
        # deleg_0b2cbd47 M1).
        # Eligibility BEFORE identity resolution (S7 round-2
        # deleg_8b1eef76 M2′): effect_identity raises on unresolvable
        # targets — an unregistered-system candidate must be SKIPPED in
        # isolation, never crash the mission.
        gap = _eligible(database, candidate)
        if gap is not None:
            result = {"action_id": candidate.action_id,
                      "outcome": _SKIPPED, "reason": gap}
            results.append(result)
            learn(database, mission_id, candidate.action_id, result)
            continue
        try:
            key, _, _, _ = effect_identity(database, envelope)
        except ValueError as exc:
            result = {"action_id": candidate.action_id,
                      "outcome": _REFUSED,
                      "reason": redact_text(str(exc))}
            results.append(result)
            learn(database, mission_id, candidate.action_id, result)
            continue
        state = effect_state(database, key)
        if state["status"] in (COMPLETED, FAILED, FROZEN):
            result = {"action_id": candidate.action_id,
                      "outcome": state["status"], "action_key": key,
                      "performed": False, "reaped": False,
                      "after_state": state.get("after_state"),
                      "cost": state.get("cost"),
                      "failure_reason": state.get("failure_reason"),
                      "frozen_reason": state.get("frozen_reason")}
            results.append(result)
            learn(database, mission_id, candidate.action_id, result)
            continue
        stop = emergency_stop_reason(database, stop_path)
        if stop:
            result = {"action_id": candidate.action_id,
                      "outcome": _REFUSED, "reason": f"STOP: {stop}"}
            results.append(result)
            learn(database, mission_id, candidate.action_id, result)
            continue
        perform = perform_map.get(candidate.connector_id)
        if perform is None:
            result = {"action_id": candidate.action_id,
                      "outcome": _SKIPPED,
                      "reason": f"no performer for {candidate.connector_id}"}
            results.append(result)
            learn(database, mission_id, candidate.action_id, result)
            continue
        results.append(run_candidate(
            database, mission_id, run_id, candidate,
            connector_id=candidate.connector_id, perform=perform,
            budget=budget, stop_path=stop_path))
    return {"mission_id": mission_id, "run_id": run_id,
            "identity": IDENTITY, "results": results}
