"""CANONICAL RUNTIME PATH — hybrid worker live driver.

One production path for worker-driven mission steps:

    Supervisor/STOP sentinel
      -> driver cycle (bounded, durable singleton lease)
      -> existing durable mission selector + executable step contract
      -> MissionLoop (canonical mission state machine)
      -> RoleDispatcher (durable leases, replay, exactly-once)
      -> HybridWorkerRoleAdapter (the single worker bridge)
      -> WorkerRouter (deterministic routing)
      -> worker adapters (Hermes / OpenHands, disposable state)
      -> EvidenceReconciler (JARVIS-owned truth)
      -> RoleResult -> durable ledger -> fresh verifier -> accept/repair
      -> next action selection

The driver is a long-lived companion process (like the Supervisor daemon)
so the Supervisor keeps its pure heartbeat/reap/STOP responsibility. It
refuses to start cycles while STOP is active or while the subsystem is
configured disabled, holds its own durable singleton lease, and relies on
the canonical replay/dedup semantics for exactly-once recovery.

No credential is ever read or stored here: adapters consume the resolved
machine-local provider configuration (``%LOCALAPPDATA%/JARVIS/config/provider.yaml``
with legacy env compatibility) at process start.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Iterable

from ..database import FoundryDatabase
from ..clock import parse, utc_now
from ..events import Event, EventConflict, canonical_json
from ..paths import FoundryPaths
from ..roles.router import RoleRouter
from ..supervisor.estop import EmergencyStop
from ..supervisor.leases import LeaseManager
from ..mission.contracts import (
    MissionStepContract,
    MissionStepContractError,
    dispatchable_contract_missions,
    next_executable_contract,
)
from ..mission.directives import select_dispatchable_mission
from ..mission.loop import MissionLoop, ProposalError
from ..control.policy import ActionPolicy
from ..domain import ActionEnvelope
from ..workers.contracts import WORKER_OPENHANDS
from ..workers.router import NOT_READY, READY
from ..v3_fault import invoke_fault_hook
from ._stack import build_worker_stack
from .config import WorkerRuntimeConfig
from .provider_config import ProviderConfigError

LOOP_LEASE_KEY = "worker-loop:singleton"
IDLE_HEARTBEAT_SECONDS = 300
DEFAULT_MISSION_ID = "mission-wfm-optimizer"
DEFAULT_RESOURCE_ID = "resource-wfm-optimizer"


def _default_stop_path(paths: FoundryPaths, config: WorkerRuntimeConfig) -> Path:
    return config.resolve_stop_path(paths.home)


def _append_coalesced_idle_event(
    database: FoundryDatabase,
    event_type: str,
    payload: dict[str, Any],
    *,
    stable_fields: dict[str, Any],
    source_id: str,
) -> bool:
    """Record an idle state transition once, not once per polling tick.

    The worker loop remains a polling process, but an unchanged WAIT state is
    not a new fact.  Compare the latest ledger record inside a short read and
    key each real transition by its predecessor sequence plus a canonical
    state hash.  A concurrent duplicate loses harmlessly to the unique
    idempotency key; no second authority or mutable idle table is introduced.
    """

    state_key = hashlib.sha256(
        canonical_json(stable_fields).encode("utf-8")
    ).hexdigest()[:24]
    connection = database.connect(readonly=True)
    try:
        row = connection.execute(
            "SELECT sequence, payload_json FROM events "
            "WHERE event_type = ? ORDER BY sequence DESC LIMIT 1",
            (event_type,),
        ).fetchone()
    finally:
        connection.close()
    if row is not None:
        try:
            prior = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            prior = {}
        if isinstance(prior, dict) and prior.get("_coalesced_state") == state_key:
            return False
        predecessor = int(row["sequence"])
    else:
        predecessor = 0
    enriched = dict(payload)
    enriched["_coalesced_state"] = state_key
    try:
        database.append(Event.create(
            event_type,
            enriched,
            source_type="supervisor",
            source_id=source_id,
            idempotency_key=(
                f"{event_type}:transition:{predecessor}:{state_key}"),
        ))
    except EventConflict:
        # Another valid loop owner recorded the same transition between the
        # read and append.  Treat that as the desired coalesced outcome.
        return False
    return True


@contextmanager
def _adapter_runtime_environment(
    paths: FoundryPaths, config: WorkerRuntimeConfig,
) -> Iterator[None]:
    """Expose run-scoped STOP/PID paths to adapters, then scrub the process env.

    The normal stack passes these paths explicitly. The environment fallback
    is retained for adapter call paths created by older integrations, while
    restoration on every exit prevents retained state from leaking between
    cycles or into unrelated processes.
    """
    previous = {
        "JARVIS_ADAPTER_PID_TABLE": os.environ.get("JARVIS_ADAPTER_PID_TABLE"),
        "JARVIS_STOP_PATH": os.environ.get("JARVIS_STOP_PATH"),
    }
    os.environ["JARVIS_ADAPTER_PID_TABLE"] = str(
        paths.home / "adapter_worker_pids.json")
    os.environ["JARVIS_STOP_PATH"] = str(_default_stop_path(paths, config))
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _executive_waiting_report(
    database: FoundryDatabase, *, loop_id: str, instance_id: str,
    decision: Any, reason: str,
) -> dict[str, Any]:
    import hashlib

    payload = {
        "loop_id": loop_id, "instance_id": instance_id,
        "decision_id": decision.decision_id,
        "reason": reason,
        "episode_key": hashlib.sha256(str(reason).strip().encode("utf-8")).hexdigest()[:20],
        "wait": {
            "reconsider_condition": (
                decision.wait.reconsider_condition if decision.wait else ""),
            "best_candidate_id": (
                decision.wait.best_candidate_id if decision.wait else None),
            "best_score": (
                decision.wait.best_score if decision.wait else None),
            "wait_kind": (
                decision.wait.wait_kind if decision.wait else None),
        },
    }
    _append_coalesced_idle_event(
        database,
        "worker_loop.executive_waiting",
        payload,
        stable_fields={"reason": reason, "wait": payload["wait"]},
        source_id=instance_id,
    )
    return {
        "loop_id": loop_id,
        "outcome": "WAITING",
        "reason": reason,
        "executive_decision": decision.kind,
        "decision_id": decision.decision_id,
    }


def _maybe_executive_select(
    database: FoundryDatabase,
    config: WorkerRuntimeConfig,
    *,
    loop_id: str,
    instance_id: str,
) -> str | None:
    """Run bounded executive evaluation when enabled; return new mission_id or None."""
    if not config.executive_selection_enabled:
        return None
    from ..executive.decisions import ExecutiveDecisionService
    from ..executive.eligibility import enable_admission_authority
    from ..executive.missions import materialize_selection

    executive = ExecutiveDecisionService(
        database,
        activation_epoch=config.executive_activation_epoch,
        min_action_score=config.executive_min_action_score,
        wait_margin=config.executive_wait_margin,
        source_id=f"worker-loop:{instance_id}",
    )
    epoch = executive.ensure_activation(
        config.executive_activation_epoch or None)
    enable_admission_authority(
        database, epoch, source_id=f"worker-loop:{instance_id}",
    )
    decision = executive.evaluate(
        resource_id=config.resource_id,
        capability=config.capability,
        loop_id=loop_id,
        candidate_limit=config.executive_candidate_limit,
    )
    if decision.kind == "WAIT":
        _executive_waiting_report(
            database, loop_id=loop_id, instance_id=instance_id,
            decision=decision, reason=decision.reason,
        )
        return None
    if decision.kind != "SELECT" or decision.candidate_id is None:
        return None
    materialized = materialize_selection(
        database,
        candidate_id=decision.candidate_id,
        objective=decision.score.objective if decision.score else "",
        resource_id=config.resource_id,
        capability=config.capability,
        decision_id=decision.decision_id,
        activation_epoch=epoch,
        source_id=f"worker-loop:{instance_id}",
    )
    if not materialized.get("materialized"):
        database.append(Event.create(
            "worker_loop.executive_materialization_blocked",
            {
                "loop_id": loop_id,
                "decision_id": decision.decision_id,
                "candidate_id": decision.candidate_id,
                "reason": materialized.get("blocked_reason"),
            },
            source_type="supervisor",
            source_id=f"worker-loop:{instance_id}",
            idempotency_key=f"worker_loop.executive_materialization_blocked:{loop_id}",
        ))
        return None
    decision_id = decision.decision_id
    database.append(Event.create(
        "executive.decision_materialized",
        {
            "decision_id": decision_id,
            "decision": "SELECT",
            "reason": decision.reason,
            "mission_id": materialized["mission_id"],
            "candidate_id": decision.candidate_id,
            "materialized": True,
        },
        source_type="supervisor",
        source_id=f"worker-loop:{instance_id}",
        idempotency_key=f"executive.decision_materialized:{decision_id}",
    ))
    return str(materialized["mission_id"])


def _record_executive_outcome(
    database: FoundryDatabase,
    *,
    mission_id: str,
    outcome: Any,
    instance_id: str,
) -> None:
    """Capture actuals and calibration after a verified mission step."""
    from ..executive.calibration import outcome_from_mission, record_calibration

    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        return
    created_from = str(mission.get("created_from") or "")
    if not created_from.startswith("candidate:"):
        return
    candidate_id = created_from.split(":", 1)[1]
    actual = outcome_from_mission(database, mission_id)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT source, objective FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    source = str(row[0]) if row else ""
    objective = str(row[1]) if row else str(mission.get("objective") or "")
    decision_events = [
        e for e in database.events()
        if e.event_type in ("executive.decision_recorded", "executive.decision_materialized")
        and e.payload.get("candidate_id") == candidate_id
        and (
            e.payload.get("mission_id") in (mission_id, None)
            or e.event_type == "executive.decision_recorded"
        )
    ]
    decision_id = (
        str(decision_events[-1].payload.get("decision_id"))
        if decision_events else f"post-{mission_id}"
    )
    record_calibration(
        database,
        decision_id=decision_id,
        candidate_id=candidate_id,
        mission_id=mission_id,
        source=source,
        objective=objective,
        outcome=str(actual.get("outcome") or getattr(outcome, "outcome", "UNKNOWN")),
        repair_count=int(actual.get("repair_count") or 0),
        actual=actual,
        source_id=f"worker-loop:{instance_id}",
    )


def _maybe_portfolio_select(
    database: FoundryDatabase,
    config: WorkerRuntimeConfig,
    *,
    loop_id: str,
    instance_id: str,
) -> str | None:
    """V7 portfolio tier: global next-useful-work above candidate selection.

    Consumes the canonical V7A selector; materializes through the bridge so
    STOP, vetoes, capacity/budget, WAIT/wake, and fairness stay authoritative.
    """
    from ..evolution.executive_portfolio import (
        claim_selection,
        release_selection_claim,
        select_next_work,
    )
    from ..executive.portfolio_bridge import materialize_portfolio_selection

    decision = select_next_work(
        database,
        loop_id=f"portfolio:{loop_id}:{uuid.uuid4().hex[:8]}",
        resource_id=config.resource_id,
        capability=config.capability,
        source_id=f"worker-loop:{instance_id}",
    )
    if decision.kind != "SELECT" or decision.entity_id is None:
        reason = str(decision.reason or "portfolio waiting")
        episode_key = hashlib.sha256(reason.strip().encode("utf-8")).hexdigest()[:20]
        stable = {
            "selected_class": decision.selected_class,
            "reason": decision.reason,
            "why_now": decision.why_now,
            "wake_on": list(decision.wake_on),
        }
        # ``revisit_at`` is a polling deadline, not a change in the idle
        # state.  The selector creates a new deadline for each fresh loop
        # id; hashing it here would turn every unchanged WAIT poll back into
        # an event and recreate the legacy ledger-growth incident.  Keep the
        # current deadline in the payload for operators, but key coalescing
        # on the durable reason/wake conditions only.
        _append_coalesced_idle_event(
            database,
            "worker_loop.portfolio_waiting",
            {
                "loop_id": loop_id,
                "instance_id": instance_id,
                "decision_id": decision.decision_id,
                **stable,
                "revisit_at": decision.revisit_at,
                "episode_key": episode_key,
            },
            stable_fields=stable,
            source_id=f"worker-loop:{instance_id}",
        )
        return None
    payload = decision.to_payload()
    result = materialize_portfolio_selection(
        database, decision=payload,
        resource_id=config.resource_id, capability=config.capability,
        source_id=f"worker-loop:{instance_id}",
    )
    if not result.get("materialized"):
        database.append(Event.create(
            "worker_loop.portfolio_materialization_blocked",
            {
                "loop_id": loop_id,
                "decision_id": decision.decision_id,
                "item_id": decision.entity_id,
                "reason": result.get("blocked_reason"),
            },
            source_type="supervisor",
            source_id=f"worker-loop:{instance_id}",
            idempotency_key=(
                f"worker_loop.portfolio_materialization_blocked:{loop_id}"),
        ))
        return None
    try:
        claim = claim_selection(
            database, decision, source_id=f"worker-loop:{instance_id}")
    except Exception:  # noqa: BLE001 - claim loss must not kill the cycle
        claim = {}
    mission_id = str(result.get("mission_id") or "")
    database.append(Event.create(
        "worker_loop.portfolio_selected",
        {
            "loop_id": loop_id,
            "instance_id": instance_id,
            "decision_id": decision.decision_id,
            "selected_class": decision.selected_class,
            "item_id": decision.entity_id,
            "mission_id": mission_id,
            "claim": dict(claim or {}),
        },
        source_type="supervisor",
        source_id=f"worker-loop:{instance_id}",
        idempotency_key=f"worker_loop.portfolio_selected:{loop_id}",
    ))
    # Release the selection lease immediately: the mission itself is now the
    # durable unit of work; holding the portfolio lease would only block
    # unrelated selections after a crash.
    try:
        release_selection_claim(
            database, decision.entity_id,
            source_id=f"worker-loop:{instance_id}")
    except Exception:  # noqa: BLE001 - lease TTL bounds any leak
        pass
    return mission_id



def _maybe_continuous_cycle(database, config, *, paths, loop_id, instance_id):
    """V9 Continuous Executive: durable wake-aware cycle (opt-in)."""
    if not getattr(config, "continuous_enabled", False):
        return None
    try:
        from ..executive.continuous import run_continuous_cycle, auto_wake_for_blocked
    except Exception as e:
        database.append(Event.create("worker_loop.continuous_blocked", {"loop_id": loop_id, "reason": f"import failed: {e}"}, source_type="supervisor", source_id=f"worker-loop:{instance_id}", idempotency_key=f"worker_loop.continuous_blocked:{loop_id}"))
        return None
    try:
        auto_wake_for_blocked(database, controller="supervisor")
    except Exception:
        pass
    try:
        result = run_continuous_cycle(database, cycle_id=f"continuous:{loop_id}", controller="supervisor")
        # run_continuous_cycle already does wake tick + autonomy cycle; return its mission if any
        ac = result.get("autonomy_cycle") if isinstance(result, dict) else None
        mid = None
        if isinstance(ac, dict):
            mat = ac.get("materialization") or {}
            if isinstance(mat, dict):
                mid = mat.get("mission_id")
        if mid:
            return str(mid)
        # Also check direct mission_id
        if isinstance(result, dict) and result.get("mission_id"):
            return str(result.get("mission_id"))
        return None
    except Exception as e:
        try:
            database.append(Event.create("worker_loop.continuous_blocked", {"loop_id": loop_id, "reason": f"{type(e).__name__}: {e}"[:500]}, source_type="supervisor", source_id=f"worker-loop:{instance_id}", idempotency_key=f"worker_loop.continuous_blocked:{loop_id}"))
        except Exception:
            pass
        return None

def _maybe_executive_autonomy(
    database: FoundryDatabase,
    config: WorkerRuntimeConfig,
    *,
    paths: FoundryPaths,
    loop_id: str,
    instance_id: str,
) -> str | None:
    """Run the V8 executive pass through the existing V7/mission seams."""
    if not config.executive_autonomy_enabled:
        return None
    from ..executive.autonomy import run_autonomy_cycle

    try:
        cycle = run_autonomy_cycle(
            database,
            cycle_id=f"worker:{loop_id}",
            resource_id=config.resource_id,
            capability=config.capability,
            max_new_missions=1,
            stop_path=_default_stop_path(paths, config),
        )
    except Exception as error:  # noqa: BLE001 - fail closed and keep loop alive
        database.append(Event.create(
            "worker_loop.executive_autonomy_blocked",
            {"loop_id": loop_id, "instance_id": instance_id,
             "reason": f"{type(error).__name__}: {error}"[:500]},
            source_type="supervisor", source_id=f"worker-loop:{instance_id}",
            idempotency_key=f"worker_loop.executive_autonomy_blocked:{loop_id}",
        ))
        return None
    mission_id = str(cycle.materialization.get("mission_id") or "")
    database.append(Event.create(
        "worker_loop.executive_autonomy_observed",
        {"loop_id": loop_id, "instance_id": instance_id,
         "cycle_id": cycle.cycle_id, "objective_id": cycle.objective_id,
         "status": cycle.status, "reason": cycle.reason,
         "mission_id": mission_id or None},
        source_type="supervisor", source_id=f"worker-loop:{instance_id}",
        idempotency_key=f"worker_loop.executive_autonomy_observed:{loop_id}",
    ))
    return mission_id or None


def _record_mission_portfolio_outcome(
    database: FoundryDatabase,
    *,
    mission_id: str,
    outcome: Any,
    status: str,
    instance_id: str,
    repair_count: int = 0,
) -> None:
    """Reconcile a portfolio-materialized mission's terminal truth."""
    from ..executive.portfolio_bridge import record_mission_portfolio_outcome

    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        return
    created_from = str(mission.get("created_from") or "")
    if not created_from.startswith("portfolio:"):
        return
    item_id = created_from.split(":", 1)[1]
    materialization = None
    recorded = None
    for event in reversed(database.events()):
        if materialization is None and \
                event.event_type == "portfolio.selection_materialized" \
                and str(event.payload.get("mission_id") or "") == mission_id:
            materialization = event.payload
        if recorded is None and \
                event.event_type == "portfolio.selection_recorded":
            payload_item = dict(event.payload.get("selected_item") or {})
            if str(payload_item.get("item_id") or "") == item_id:
                recorded = event.payload
        if materialization is not None and recorded is not None:
            break
    if materialization is None or recorded is None:
        return
    actual = {
        "outcome": status,
        "mission_id": mission_id,
        "step_id": getattr(outcome, "step_id", None),
        "verdict_id": getattr(outcome, "verdict_id", None),
        "reason": getattr(outcome, "reason", ""),
    }
    try:
        record_mission_portfolio_outcome(
            database, decision=recorded, mission_id=mission_id,
            status=status, actual=actual, repair_count=repair_count,
            outcome_id=f"outcome-{mission_id[:20]}",
            source_id=f"worker-loop:{instance_id}")
    except (ValueError, KeyError, TypeError) as error:
        database.append(Event.create(
            "worker_loop.portfolio_outcome_blocked",
            {"mission_id": mission_id, "reason": str(error)},
            source_type="supervisor",
            source_id=f"worker-loop:{instance_id}",
            idempotency_key=(
                f"worker_loop.portfolio_outcome_blocked:{mission_id}"),
        ))


def _record_v3_outcome(
    database: FoundryDatabase,
    *,
    mission_id: str,
    outcome: Any,
    loop_id: str,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Write the V3 candidate outcome and close the cycle lineage.

    The outcome service verifies the durable candidate/mission/completion
    chain.  A runtime PASS is not returned as complete when that proof is
    absent or inconsistent.
    """
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        return None
    created_from = str(mission.get("created_from") or "")
    if not created_from.startswith("candidate:"):
        return None
    candidate_id = created_from.split(":", 1)[1]
    candidate = database.row("candidates", "candidate_id", candidate_id) or {}
    from ..executive.calibration import outcome_from_mission
    from ..v3_outcomes import record_outcome

    actual = outcome_from_mission(database, mission_id)
    payload = record_outcome(
        database,
        candidate_id=candidate_id,
        mission_id=mission_id,
        mission_outcome="PASSED",
        predicted_value=(
            {"expected_outcome": candidate["expected_outcome"]}
            if candidate.get("expected_outcome") else None),
        actual_value=actual,
        confidence=0.8,
        source_id=f"worker-loop:{loop_id}",
        continuation_loop_id=loop_id,
    )
    invoke_fault_hook(fault_hook, "post outcome_recorded")
    _record_v3_lineage(database, loop_id=loop_id, payload=payload)
    return payload


def _record_v3_lineage(database: FoundryDatabase, *, loop_id: str,
                       payload: dict[str, Any]) -> None:
    """Append the terminal lineage once, including after a caller crash."""
    lineage = dict(payload.get("lineage") or {})
    actual = dict(payload.get("actual_value") or {})
    terminal_outcome = actual.get("outcome")
    if terminal_outcome is None:
        outcome_payload = payload.get("outcome") or {}
        if isinstance(outcome_payload, dict):
            terminal_outcome = outcome_payload.get("actual")
    lineage.update({
        "loop_id": loop_id,
        "outcome_id": payload.get("outcome_id"),
        "next_selection_id": (payload.get("continuation") or {})
        .get("next_selection_id"),
        "terminal_outcome": terminal_outcome,
    })
    database.append(Event.create(
        "discovery.cycle_lineage_completed", lineage,
        source_type="supervisor", source_id=f"worker-loop:{loop_id}",
        idempotency_key=f"discovery.cycle_lineage_completed:{loop_id}"))


def _recover_v3_terminal(database: FoundryDatabase, *, loop_id: str,
                         fault_hook: Callable[[str], None] | None = None) -> None:
    """Finish a durable COMPILED mission after a post-completion crash."""
    cycle = next((event for event in database.events()
                  if event.event_type == "discovery.cycle_completed"
                  and event.payload.get("loop_id") == loop_id
                  and event.payload.get("mission_id")), None)
    if cycle is None:
        return
    mission_id = str(cycle.payload["mission_id"])
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None or str(mission.get("state")) != "COMPLETE":
        return
    lineage_event = next((event for event in database.events()
                          if event.event_type == "discovery.cycle_lineage_completed"
                          and event.payload.get("loop_id") == loop_id), None)
    if lineage_event is not None:
        return
    outcome_event = next((event for event in database.events()
                          if event.event_type == "discovery.candidate_outcome_recorded"
                          and event.source_id == f"worker-loop:{loop_id}"), None)
    if outcome_event is not None:
        _record_v3_lineage(
            database, loop_id=loop_id, payload=dict(outcome_event.payload))
        return
    created_from = str(mission.get("created_from") or "")
    if not created_from.startswith("candidate:"):
        return
    _record_v3_outcome(
        database,
        mission_id=mission_id,
        outcome=SimpleNamespace(outcome="PASSED", step_id="recovered"),
        loop_id=loop_id,
        fault_hook=fault_hook,
    )


def _waiting_report(database: FoundryDatabase, *, loop_id: str,
                    instance_id: str, reason: str) -> dict[str, Any]:
    """Record an idle episode once, then emit sparse audit heartbeats.

    ``loop_id`` is intentionally excluded from the episode identity: the
    Supervisor launches short-lived one-shot workers and historically each
    generated a fresh UUID, producing tens of thousands of identical idle
    events.  A reason transition starts a new episode; an unchanged reason
    gets at most one heartbeat every five minutes.
    """
    import hashlib

    episode_key = hashlib.sha256(str(reason).strip().encode("utf-8")).hexdigest()[:20]
    active: dict[str, Any] | None = None
    # Count every prior start for this reason, not only the currently open
    # episode.  If an idle reason recurs after a closed episode, the ordinal
    # must advance or the new durable event would collide with the old one.
    # Count historical episodes in SQLite rather than a bounded Python window;
    # otherwise a busy-but-legitimate reason that recurs after >200 episodes
    # could reuse an old idempotency key.  JSON1 is part of the SQLite builds
    # supported by Foundry; a malformed legacy payload simply contributes zero.
    with database.connect(readonly=True) as connection:
        starts = int(connection.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE event_type IN ('worker_loop.waiting_started', 'worker_loop.waiting') "
            "AND json_extract(payload_json, '$.episode_key') = ?",
            (episode_key,),
        ).fetchone()[0])
        latest_row = connection.execute(
            "SELECT event_type, payload_json, recorded_at FROM events "
            "WHERE event_type IN ('worker_loop.waiting_started', "
            "'worker_loop.waiting_heartbeat', 'worker_loop.waiting_ended', "
            "'worker_loop.waiting') "
            "AND json_extract(payload_json, '$.episode_key') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (episode_key,),
        ).fetchone()
        latest_any_row = connection.execute(
            "SELECT event_type, payload_json FROM events "
            "WHERE event_type IN ('worker_loop.waiting_started', "
            "'worker_loop.waiting_heartbeat', 'worker_loop.waiting_ended', "
            "'worker_loop.waiting') ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    active_types = {
        "worker_loop.waiting_started", "worker_loop.waiting_heartbeat",
        "worker_loop.waiting",
    }
    # A reason change closes the prior episode before opening the new one.
    # Without this transition, alternating reasons could leave several active
    # episodes and make a later return to reason A look like a no-op.
    if latest_any_row is not None and latest_any_row[0] in active_types:
        try:
            prior_payload = json.loads(str(latest_any_row[1]))
        except (TypeError, ValueError):
            prior_payload = {}
        if prior_payload.get("episode_key") != episode_key:
            prior_episode = str(prior_payload.get("episode_id") or "")
            if prior_episode:
                try:
                    database.append(Event.create(
                        "worker_loop.waiting_ended",
                        {"loop_id": loop_id, "instance_id": instance_id,
                         "episode_key": prior_payload.get("episode_key"),
                         "episode_id": prior_episode,
                         "reason": "idle reason changed"},
                        source_type="supervisor", source_id=instance_id,
                        idempotency_key=f"worker_loop.waiting_ended:{prior_episode}",
                    ))
                except EventConflict:
                    pass
            latest_row = None
    if latest_row is not None and latest_row[0] in active_types:
        try:
            latest_payload = json.loads(str(latest_row[1]))
        except (TypeError, ValueError):
            latest_payload = {}
        active = {
            "episode_id": str(latest_payload.get("episode_id") or f"{episode_key}:1"),
            "recorded_at": latest_row[2],
        }
    now = utc_now()
    if active is None:
        episode_id = f"{episode_key}:{starts + 1}"
        try:
            database.append(Event.create(
                "worker_loop.waiting_started",
                {"loop_id": loop_id, "instance_id": instance_id, "reason": reason,
                 "episode_key": episode_key, "episode_id": episode_id},
                source_type="supervisor", source_id=instance_id,
                idempotency_key=f"worker_loop.waiting_started:{episode_id}",
            ))
        except EventConflict:
            # Another short-lived loop won the same reason episode race.  The
            # durable row is already the truthful outcome; do not turn a
            # harmless duplicate poll into a worker failure.
            pass
    else:
        try:
            age = (now - parse(str(active.get("recorded_at")))).total_seconds()
        except (TypeError, ValueError, OverflowError):
            age = IDLE_HEARTBEAT_SECONDS
        if age >= IDLE_HEARTBEAT_SECONDS:
            bucket = int(now.timestamp() // IDLE_HEARTBEAT_SECONDS)
            try:
                database.append(Event.create(
                    "worker_loop.waiting_heartbeat",
                    {"loop_id": loop_id, "instance_id": instance_id, "reason": reason,
                     "episode_key": episode_key,
                     "episode_id": active["episode_id"], "age_seconds": max(0, int(age))},
                    source_type="supervisor", source_id=instance_id,
                    idempotency_key=(
                        f"worker_loop.waiting_heartbeat:{active['episode_id']}:{bucket}"),
                ))
            except EventConflict:
                pass
    return {"loop_id": loop_id, "outcome": "WAITING", "reason": reason}


def _end_waiting_episode(database: FoundryDatabase, *, loop_id: str,
                         instance_id: str) -> None:
    """Close the newest idle episode when actionable work appears."""
    with database.connect(readonly=True) as connection:
        latest_row = connection.execute(
            "SELECT event_type, payload_json FROM events "
            "WHERE event_type IN ('worker_loop.waiting_started', "
            "'worker_loop.waiting_heartbeat', 'worker_loop.waiting_ended', "
            "'worker_loop.waiting') ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    active = None
    if latest_row is not None and latest_row[0] in {
        "worker_loop.waiting_started", "worker_loop.waiting_heartbeat",
        "worker_loop.waiting"
    }:
        try:
            active = json.loads(str(latest_row[1]))
        except (TypeError, ValueError):
            active = None
    if not active:
        return
    episode_id = str(active.get("episode_id") or "")
    if not episode_id:
        return
    try:
        database.append(Event.create(
            "worker_loop.waiting_ended",
            {"loop_id": loop_id, "instance_id": instance_id,
             "episode_key": active.get("episode_key"), "episode_id": episode_id,
             "reason": "actionable mission selected"},
            source_type="supervisor", source_id=instance_id,
            idempotency_key=f"worker_loop.waiting_ended:{episode_id}",
        ))
    except EventConflict:
        pass


def _blocked_contract_report(database: FoundryDatabase, *, loop_id: str,
                             instance_id: str, mission_id: str | None,
                             reason: str) -> dict[str, Any]:
    database.append(Event.create(
        "worker_loop.contract_blocked",
        {"loop_id": loop_id, "instance_id": instance_id,
         "mission_id": mission_id, "reason": reason},
        source_type="supervisor", source_id=instance_id,
        idempotency_key=f"worker_loop.contract_blocked:{loop_id}",
    ))
    return {"loop_id": loop_id, "outcome": "BLOCKED",
            "mission_id": mission_id, "reason": reason}


def _select_contract(database: FoundryDatabase) -> str | None:
    """Select an existing mission that has a durable contract.

    Missions without executable authority are excluded from selection rather
    than converted into invented work.  A selected but malformed contract is
    returned for the caller to report as BLOCKED, preserving the distinction
    between honest WAITING and a corrupted authoritative record.
    """
    mission_ids = dispatchable_contract_missions(database)
    if not mission_ids:
        return None
    return select_dispatchable_mission(database, mission_ids)


def _pin_routing_to_hermes(stack: Any, config: WorkerRuntimeConfig | None = None) -> None:
    """Keep Hermes as the fail-closed production worker.

    OpenHands stays registered when enabled and is eligible only when the
    runtime config carries positive READY evidence. Missing evidence or a
    lab-scripted Hermes stack leaves OpenHands honestly NOT_READY.
    Stacks without a router (test fakes) no-op.
    """
    router = getattr(stack, "router", None)
    if router is None or not hasattr(router, "mark_ready"):
        return
    enabled = tuple(getattr(stack, "enabled_frameworks", ()) or ())
    spec = dict((config.readiness if config is not None else {}).get(WORKER_OPENHANDS) or {})
    openhands_enabled = bool((config.openhands if config is not None else {}).get("enabled"))
    lab = bool(getattr(config, "lab_scripted_hermes", False)) if config is not None else False
    evidence = tuple(spec.get("evidence") or ())
    claimed_ready = str(spec.get("state") or "") == READY
    genuinely_ready = (
        openhands_enabled
        and not lab
        and WORKER_OPENHANDS in enabled
        and claimed_ready
        and bool(evidence)
    )
    if genuinely_ready:
        return
    router.mark_ready(
        WORKER_OPENHANDS, state=NOT_READY,
        note="OpenHands not evidence-READY for this run; Hermes remains production dispatch",
        evidence=("hybrid-worker-openhands-not-ready",),
    )


def _advisory_accepted_outcome_writeback(
    database: FoundryDatabase, *, mission_id: str, outcome: Any,
) -> None:
    """Governed observation writeback after a verified PASS.

    Memory remains advisory. Failure here never changes mission state.
    """
    try:
        from ..hybrid_memory.lifecycle import TierCMemoryStore
        run_id = getattr(outcome, "run_id", None) or "unknown"
        step_id = getattr(outcome, "step_id", None) or "unknown"
        store = TierCMemoryStore(database)
        store.record_observation(
            f"accepted verified outcome mission={mission_id} step={step_id}",
            mission_id=mission_id,
            created_by="worker-loop",
            source_ids=[f"mission:{mission_id}", f"run:{run_id}"],
            evidence_ids=[str(getattr(outcome, "verdict_id", "") or "")],
            confidence=0.8,
            idempotency_key=f"memory.observed:accepted:{mission_id}:{run_id}",
        )
    except Exception:
        return


def _advisory_mission_outcome_writeback(
    database: FoundryDatabase, *, mission_id: str, outcome: Any,
) -> None:
    """Record every terminal result as advisory, provenance-backed memory.

    Verified PASS retains the historical accepted-outcome record.  FAILED and
    BLOCKED results are equally valuable for future executive planning, but
    remain advisory and can never override the mission/verifier projection.
    """
    status = str(getattr(outcome, "outcome", "UNKNOWN") or "UNKNOWN").upper()
    if status == "PASSED":
        _advisory_accepted_outcome_writeback(
            database, mission_id=mission_id, outcome=outcome)
        return
    if status not in {"FAILED", "BLOCKED", "CANCELLED"}:
        return
    try:
        from ..hybrid_memory.lifecycle import TierCMemoryStore
        run_id = getattr(outcome, "run_id", None) or "unknown"
        step_id = getattr(outcome, "step_id", None) or "unknown"
        reason = str(getattr(outcome, "reason", "") or "").strip()
        store = TierCMemoryStore(database)
        store.record_observation(
            f"mission outcome {status.lower()} mission={mission_id} step={step_id}"
            + (f" reason={reason[:240]}" if reason else ""),
            mission_id=mission_id,
            created_by="worker-loop",
            source_ids=[f"mission:{mission_id}", f"run:{run_id}"],
            evidence_ids=[str(getattr(outcome, "verdict_id", "") or "")],
            confidence=0.65,
            idempotency_key=f"memory.observed:outcome:{mission_id}:{run_id}:{status}",
        )
    except Exception:
        return


def _commit_envelope(
    database: FoundryDatabase, contract: MissionStepContract,
) -> ActionEnvelope | None:
    """Local commit of an accepted step when git_local_commit is READY.

    Isolation still works without a commit (builder delta vs before-state).
    A missing commit capability must not block the kernel.
    """
    resource = database.row("resources", "resource_id", contract.resource_id)
    if resource is None:
        return None
    capabilities = json.loads(str(resource["capabilities_json"]))
    if capabilities.get("git_local_commit") not in ("READY", "ACTIVE"):
        return None
    root = Path(str(resource["resolved_root"])).expanduser().resolve()
    return ActionEnvelope(
        action_id=f"commit:{contract.mission_id}:{contract.step_id}:v{contract.version}",
        mission_id=contract.mission_id,
        resource_id=contract.resource_id,
        action_kind="git_commit",
        target=root,
        capability="git_local_commit",
        expected_effects=("create local commit of accepted step",),
        rollback={"kind": "git_reset_soft", "reversible": True},
        step_id=contract.step_id,
    )


def run_cycle(
    database: FoundryDatabase,
    paths: FoundryPaths,
    config: WorkerRuntimeConfig,
    *,
    loop_id: str,
    instance_id: str,
    vault_root: str | Path | None = None,
    handoff_dir: str | Path | None = None,
    research_dirs: Iterable[str | Path] = (),
    repo_paths: Iterable[str | Path] | None = None,
    target_root: str | Path | None = None,
    min_score: float = 0.35,
    score_budget: int = 8,
    max_items: int = 40,
    max_bytes: int = 80_000,
    instructions_builder: Callable[[str, Path], str] | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one bounded autonomous loop pass through the canonical path.

    Returns a report dict; outcome is one of: DISABLED, FROZEN,
    LEASE_UNAVAILABLE, or the ``run_one_loop`` report (WAITING when no
    useful work exists — honest waiting, no invented work).
    """
    if not config.enabled:
        return {"loop_id": loop_id, "outcome": "DISABLED",
                "reason": "worker runtime subsystem is not enabled"}
    estop = EmergencyStop(_default_stop_path(paths, config))
    if estop.active():
        stop = estop.read()
        database.append(Event.create(
            "worker_loop.frozen",
            {"instance_id": instance_id, "loop_id": loop_id,
             "stop_id": stop.get("stop_id") if stop else None,
             "reason": stop.get("reason") if stop else "stop sentinel present"},
            source_type="supervisor", source_id=instance_id,
            idempotency_key=f"worker_loop.frozen:{loop_id}:{instance_id}",
        ))
        return {"loop_id": loop_id, "outcome": "FROZEN",
                "reason": "emergency stop is active"}

    leases = LeaseManager(database)
    try:
        leases.acquire(LOOP_LEASE_KEY, instance_id, 120)
    except Exception as error:  # noqa: BLE001 - lease conflict is honest
        return {"loop_id": loop_id, "outcome": "LEASE_UNAVAILABLE",
                "reason": f"{type(error).__name__}: {error}"}
    try:
        database.append(Event.create(
            "worker_loop.cycle_started",
            {"instance_id": instance_id, "loop_id": loop_id,
             "resource_id": config.resource_id,
             "capability": config.capability},
            source_type="supervisor", source_id=instance_id,
            idempotency_key=f"worker_loop.cycle_started:{loop_id}",
        ))
        _recover_v3_terminal(database, loop_id=loop_id,
                             fault_hook=fault_hook)
        # V3 WP3 single choke point: when discovery.cycle_enabled the
        # unified experimental cycle replaces the split selection path.
        # Flag OFF (default) keeps v3_loop WAIT + executive selection
        # exactly as before — no other code changes.
        if config.discovery_cycle_enabled:
            from functools import partial
            from ..v3_cycle import run_discovery_cycle
            from ..v3_loop import observe_for_cycle

            report = run_discovery_cycle(
                database, loop_id=loop_id,
                resource_id=config.resource_id,
                capability=config.capability,
                source_id=f"worker-loop:{instance_id}",
                observe=partial(
                    observe_for_cycle, loop_id=loop_id,
                    mission_id=config.mission_id,
                    resource_id=config.resource_id,
                     vault_root=vault_root, handoff_dir=handoff_dir,
                     research_dirs=research_dirs, repo_paths=repo_paths),
                fault_hook=fault_hook,
            )
            if report.outcome == "COMPILED" and report.mission_id:
                mission_id: str | None = report.mission_id
            else:
                return _waiting_report(
                    database, loop_id=loop_id, instance_id=instance_id,
                    reason=f"v3 cycle {report.outcome}: "
                           f"{report.reason or 'no mission compiled'}",
                )
        else:
            if config.executive_autonomy_enabled:
                try:
                    from ..executive.autonomy import sync_owner_goals
                    sync_owner_goals(database)
                except Exception as error:  # noqa: BLE001 - authority reconciliation fails closed
                    database.append(Event.create(
                        "worker_loop.executive_authority_sync_blocked",
                        {"loop_id": loop_id, "instance_id": instance_id,
                         "reason": f"{type(error).__name__}: {error}"[:500]},
                        source_type="supervisor", source_id=instance_id,
                        idempotency_key=f"worker_loop.executive_authority_sync_blocked:{loop_id}",
                    ))
            mission_id = _select_contract(database)
            if mission_id is None and getattr(config, "continuous_enabled", False):
                mission_id = _maybe_continuous_cycle(database, config, paths=paths, loop_id=loop_id, instance_id=instance_id)
            if mission_id is None and config.executive_autonomy_enabled:
                mission_id = _maybe_executive_autonomy(
                    database, config, paths=paths,
                    loop_id=loop_id, instance_id=instance_id)
            if mission_id is None and config.executive_selection_enabled:
                mission_id = _maybe_executive_select(
                    database, config, loop_id=loop_id, instance_id=instance_id)
            if mission_id is None and config.portfolio_selection_enabled:
                mission_id = _maybe_portfolio_select(
                    database, config, loop_id=loop_id, instance_id=instance_id)
        if mission_id is None:
            return _waiting_report(
                database, loop_id=loop_id, instance_id=instance_id,
                reason="no dispatchable mission has an authoritative executable contract",
            )
        _end_waiting_episode(
            database, loop_id=loop_id, instance_id=instance_id)
        try:
            contract = next_executable_contract(database, mission_id)
            if contract is None:
                # A dispatchable-state mission without any remaining
                # executable contract is residue: re-selecting it forever
                # would starve the portfolio tier (live incident
                # 2026-08-24). Retire it durably unless work is actively
                # in flight, then let the cycle continue.
                mission_row = database.row("missions", "mission_id", mission_id)
                state = str((mission_row or {}).get("state") or "")
                if state in {"PLANNED", "ORIENTING", "ACTION_SELECTION",
                             "READY"}:
                    database.append(Event.create(
                        "mission.state_changed",
                        {
                            "mission_id": mission_id,
                            "state": "CANCELLED",
                            "next_action": (
                                "retired by worker loop: no executable "
                                "step contract remains"),
                            "reason": (
                                "bounded policy: contractless residue "
                                "must not idle the loop"),
                        },
                        source_type="supervisor",
                        source_id=f"worker-loop:{instance_id}",
                        idempotency_key=(
                            f"mission.state_changed:{mission_id}:"
                            f"CANCELLED:loop-contractless"),
                    ))
                return _waiting_report(
                    database, loop_id=loop_id, instance_id=instance_id,
                    reason=f"mission {mission_id} has no remaining executable contract",
                )
            runtime_kwargs = contract.runtime_kwargs(database)
        except (MissionStepContractError, KeyError, TypeError, ValueError) as error:
            return _blocked_contract_report(
                database, loop_id=loop_id, instance_id=instance_id,
                mission_id=mission_id, reason=f"invalid executable contract: {error}",
            )

        role_router = RoleRouter(provider_settings=config.provider_settings)
        role_router.enforce_directive_routing()
        stack = build_worker_stack(
            database, paths, config, run_id=f"loop-{loop_id}")
        _pin_routing_to_hermes(stack, config)
        stop_path = _default_stop_path(paths, config)
        loop = MissionLoop(
            database, router=role_router, adapter=stack.bridge,
            leases=leases, max_repairs=config.max_repairs,
            source_id=f"worker-loop:{instance_id}",
            stop_path=stop_path,
            policy=ActionPolicy(database, stop_path),
            fault_hook=fault_hook,
        )
        try:
            with _adapter_runtime_environment(paths, config):
                outcome = loop.run_until_settled(
                    **runtime_kwargs,
                    commit_envelope=_commit_envelope(database, contract),
                )
        except (MissionStepContractError, ProposalError, KeyError, TypeError, ValueError) as error:
            return _blocked_contract_report(
                database, loop_id=loop_id, instance_id=instance_id,
                mission_id=mission_id, reason=f"mission contract dispatch blocked: {error}",
            )
        terminal = getattr(outcome, "outcome", None)
        if terminal == "PASSED":
            try:
                _record_v3_outcome(
                    database, mission_id=mission_id, outcome=outcome,
                    loop_id=loop_id, fault_hook=fault_hook)
            except (ValueError, TypeError, KeyError) as error:
                database.append(Event.create(
                    "worker_loop.outcome_blocked",
                    {"loop_id": loop_id, "mission_id": mission_id,
                     "reason": str(error)},
                    source_type="supervisor", source_id=instance_id,
                    idempotency_key=f"worker_loop.outcome_blocked:{loop_id}"))
                return _blocked_contract_report(
                    database, loop_id=loop_id, instance_id=instance_id,
                    mission_id=mission_id,
                    reason=f"V3 outcome proof blocked: {error}",
                )
            _advisory_mission_outcome_writeback(
                database, mission_id=mission_id, outcome=outcome)
        mission_after = database.row("missions", "mission_id", mission_id) or {}
        portfolio_terminal = (
            terminal in ("FAILED", "BLOCKED")
            or (terminal == "PASSED"
                and str(mission_after.get("state") or "") == "COMPLETE")
        )
        if portfolio_terminal:
            # Keep the memory/knowledge authority in the same terminal
            # boundary for PASS, FAIL, and BLOCKED outcomes.  The writeback is
            # advisory; mission and verifier projections remain authoritative.
            if terminal != "PASSED":
                _advisory_mission_outcome_writeback(
                    database, mission_id=mission_id, outcome=outcome)
            # World Intelligence receives the same terminal fact before the
            # Executive continuation decision.  This is an advisory,
            # event-derived projection and cannot alter mission/verifier
            # truth; the stable observation identity makes recovery replay
            # exactly-once for world learning.
            try:
                from ..world_model.connector import writeback_mission_outcome
                writeback_mission_outcome(
                    database,
                    mission_id,
                    terminal or "UNKNOWN",
                    {
                        "state": mission_after.get("state"),
                        "objective": mission_after.get("objective"),
                    },
                    evidence_refs=tuple(
                        ref for ref in (getattr(outcome, "verdict_id", None),)
                        if ref
                    ),
                    source_id=f"worker-loop:{instance_id}",
                )
            except Exception as error:  # noqa: BLE001 - advisory writeback cannot block closure
                try:
                    database.append(Event.create(
                        "worker_loop.world_intelligence_writeback_blocked",
                        {
                            "loop_id": loop_id,
                            "mission_id": mission_id,
                            "reason": f"{type(error).__name__}: {error}"[:500],
                        },
                        source_type="supervisor",
                        source_id=instance_id,
                        idempotency_key=(
                            f"worker_loop.world_intelligence_writeback_blocked:"
                            f"{mission_id}:{terminal}"),
                    ))
                except Exception:
                    pass
            _record_executive_outcome(
                database, mission_id=mission_id, outcome=outcome,
                instance_id=instance_id)
            _record_mission_portfolio_outcome(
                database, mission_id=mission_id, outcome=outcome,
                status=terminal, instance_id=instance_id)
            if config.executive_autonomy_enabled:
                try:
                    from ..executive.autonomy import observe_mission_outcome
                    observe_mission_outcome(database, mission_id)
                except Exception as error:  # noqa: BLE001 - outcome remains authoritative
                    database.append(Event.create(
                        "worker_loop.executive_autonomy_observation_blocked",
                        {"loop_id": loop_id, "mission_id": mission_id,
                         "reason": f"{type(error).__name__}: {error}"[:500]},
                        source_type="supervisor", source_id=instance_id,
                        idempotency_key=f"worker_loop.executive_autonomy_observation_blocked:{loop_id}",
                    ))
            elif getattr(config, "continuous_enabled", False):
                try:
                    from ..executive.continuous import on_mission_outcome
                    on_mission_outcome(database, mission_id)
                except Exception as error:  # noqa: BLE001 - outcome remains authoritative
                    database.append(Event.create(
                        "worker_loop.continuous_outcome_observation_blocked",
                        {"loop_id": loop_id, "mission_id": mission_id,
                         "reason": f"{type(error).__name__}: {error}"[:500]},
                        source_type="supervisor", source_id=instance_id,
                        idempotency_key=f"worker_loop.continuous_outcome_observation_blocked:{loop_id}",
                    ))
        # V3 WP-B single authoritative closure seam: when the discovery
        # cycle is enabled, a terminal harmless result is handed to the
        # OutcomeService automatically (idempotent; PASSED-only inside).
        # Flag OFF keeps base behavior byte-for-byte: zero new events.
        if config.discovery_cycle_enabled and terminal == "PASSED":
            from ..v3_outcomes import reconcile_mission_outcome

            reconcile_mission_outcome(
                database, mission_id=mission_id,
                source_id=f"worker-loop:{instance_id}")
        return {
            "loop_id": loop_id,
            "outcome": outcome.outcome,
            "mission_id": mission_id,
            "step_id": outcome.step_id,
            "run_id": outcome.run_id,
            "verdict_id": outcome.verdict_id,
            "reason": outcome.reason,
        }
    finally:
        leases.release(LOOP_LEASE_KEY, instance_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis-worker-loop",
        description="Canonical hybrid worker live loop driver")
    parser.add_argument("--home", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, default=None,
                        help="worker runtime YAML (default: none -> disabled)")
    parser.add_argument("--once", action="store_true",
                        help="run a single bounded cycle and exit")
    parser.add_argument("--interval", type=float, default=300.0,
                        help="seconds between cycles (default 300)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = FoundryPaths.discover(getattr(args, "home", None))
    if EmergencyStop(paths.emergency_stop).active():
        print(json.dumps({
            "worker_loop": "FROZEN",
            "reason": "emergency stop is active",
        }, indent=2))
        return 0
    try:
        config = WorkerRuntimeConfig.from_yaml(args.config)
    except (ValueError, ProviderConfigError) as error:
        print(json.dumps({
            "worker_loop": "BLOCKED",
            "reason": str(error),
        }, indent=2))
        return 1
    if not config.enabled:
        print(json.dumps({
            "worker_loop": "DISABLED",
            "config": str(args.config),
            "reason": "worker runtime subsystem is not enabled",
        }, indent=2))
        return 0
    database = FoundryDatabase(paths.database)
    database.initialize()
    instance_id = f"worker-loop-{uuid.uuid4().hex[:12]}"
    iterations = 0
    while True:
        loop_id = f"{iterations}-{uuid.uuid4().hex[:8]}"
        report = run_cycle(
            database, paths, config,
            loop_id=loop_id, instance_id=instance_id,
        )
        print(json.dumps({"worker_loop": report}, indent=2), flush=True)
        iterations += 1
        if args.once or report.get("outcome") == "FROZEN":
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
