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
from ..supervisor.estop import EmergencyStop
from ..supervisor.leases import LeaseManager
from ..supervisor.worker import pid_alive, process_creation_identity
from ..mission.contracts import (
    contract_rows,
    latest_contracts_by_step,
    MissionStepContract,
    MissionStepContractError,
    dispatchable_contract_missions,
    next_executable_contract,
)
from ..mission.directives import select_dispatchable_mission
from ..mission.loop import MissionLoop, ProposalError
from ..domain import ActionEnvelope
from ..workers.contracts import WORKER_OPENHANDS
from ..workers.router import NOT_READY, READY
from ..v3_fault import invoke_fault_hook
from ._stack import build_worker_stack
from .config import WorkerRuntimeConfig
from .mission_session import build_canonical_mission_loop
from .provider_config import ProviderConfigError

LOOP_LEASE_KEY = "worker-loop:singleton"
IDLE_HEARTBEAT_SECONDS = 300
DEFAULT_MISSION_ID = "mission-wfm-optimizer"
DEFAULT_RESOURCE_ID = "resource-wfm-optimizer"


def _loop_lease_process(
    database: FoundryDatabase,
) -> tuple[str, str, int | None, dict[str, Any] | None]:
    """Return durable lease/process identity without mutating the ledger."""
    with database.connect(readonly=True) as connection:
        lease = connection.execute(
            "SELECT owner_id, expires_at FROM leases WHERE lease_key = ?",
            (LOOP_LEASE_KEY,),
        ).fetchone()
        if lease is None:
            return "", "", None, None
        owner_id = str(lease["owner_id"])
        row = connection.execute(
            "SELECT payload_json FROM events "
            "WHERE event_type = 'worker_loop.cycle_started' "
            "AND json_extract(payload_json, '$.instance_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (owner_id,),
        ).fetchone()
    if row is None:
        return owner_id, str(lease["expires_at"]), None, None
    try:
        payload = json.loads(str(row["payload_json"]))
        return (
            owner_id,
            str(lease["expires_at"]),
            int(payload["pid"]),
            dict(payload["creation_identity"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return owner_id, str(lease["expires_at"]), None, None


def worker_loop_lease_is_live(database: FoundryDatabase) -> bool:
    """Recognize a live external loop or an unexpired legacy lease."""
    owner_id, expires_at, pid, expected = _loop_lease_process(database)
    if not owner_id:
        return False
    if pid is None or expected is None:
        try:
            return parse(expires_at) > utc_now()
        except (TypeError, ValueError):
            return True
    if pid > 0 and pid_alive(pid):
        observed = process_creation_identity(pid)
        if observed.get("create_time") == expected.get("create_time"):
            return True
    return False


def reclaim_orphaned_loop_lease(database: FoundryDatabase) -> bool:
    """Release the worker-loop lease only when its recorded process is dead."""
    owner_id, expires_at, pid, expected = _loop_lease_process(database)
    if not owner_id or worker_loop_lease_is_live(database):
        return False
    if pid is None or expected is None:
        reason = "legacy worker-loop lease expired without process identity"
    elif pid > 0 and pid_alive(pid):
        reason = "worker-loop PID creation identity was reused"
    else:
        reason = "worker-loop process is no longer alive"
    if not LeaseManager(database).release(LOOP_LEASE_KEY, owner_id):
        return False
    database.append(Event.create(
        "worker_loop.lease_reclaimed",
        {
            "instance_id": owner_id,
            "pid": pid,
            "lease_expires_at": expires_at,
            "reason": reason,
        },
        source_type="supervisor", source_id="worker-loop-recovery",
        idempotency_key=(
            f"worker_loop.lease_reclaimed:{owner_id}:{expires_at}"
        ),
    ))
    return True


def _default_stop_path(paths: FoundryPaths, config: WorkerRuntimeConfig) -> Path:
    return config.resolve_stop_path(paths.home)


def _accepted_contract_pending_evidence(
    database: FoundryDatabase, mission_id: str,
) -> MissionStepContract | None:
    """Find accepted work whose canonical evidence/continuation was cut off."""
    from ..mission.verification import (
        accepted_verifications,
        latest_verdict_for_step,
    )

    accepted = accepted_verifications(database, mission_id=mission_id)
    for step_id, contract in sorted(
        latest_contracts_by_step(database, mission_id).items()
    ):
        verdict = latest_verdict_for_step(database, mission_id, step_id)
        if (
            verdict is None
            or str(verdict.get("status") or "") != "PASS"
            or str(verdict.get("verdict_id") or "") not in accepted
        ):
            continue
        verdict_id = str(verdict["verdict_id"])
        with database.connect(readonly=True) as connection:
            evidence = connection.execute(
                "SELECT 1 FROM events WHERE event_type = 'evidence.created' "
                "AND json_extract(payload_json, '$.mission_id') = ? "
                "AND json_extract(payload_json, '$.step_id') = ? "
                "AND json_extract(payload_json, '$.verifier_verdict_id') = ? "
                "LIMIT 1",
                (mission_id, step_id, verdict_id),
            ).fetchone()
        if evidence is None:
            return contract
    return None


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


def _maybe_portfolio_select(
    database: FoundryDatabase,
    config: WorkerRuntimeConfig,
    *,
    loop_id: str,
    instance_id: str,
    excluded_mission_ids: Iterable[str] = (),
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
    excluded = {str(value) for value in excluded_mission_ids}
    selected_item = dict(decision.selected_item or {})
    selected_mission_id = str(selected_item.get("mission_id") or "")
    if selected_mission_id and selected_mission_id in excluded:
        # A campaign child that is waiting/failed/stopped is still visible to
        # the portfolio's repair ranking.  Do not let that advisory ranking
        # retire or otherwise mutate the canonical mission before the
        # campaign owner has supplied a retry/replan decision.
        database.append(Event.create(
            "worker_loop.campaign_dispatch_blocked",
            {
                "loop_id": loop_id,
                "instance_id": instance_id,
                "mission_id": selected_mission_id,
                "reason": "portfolio selection was a campaign child awaiting durable release",
            },
            source_type="supervisor",
            source_id=instance_id,
            idempotency_key=(
                f"worker_loop.campaign_dispatch_blocked:{loop_id}"),
        ))
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



def _absolute_worktree_parent(value: Any, paths: FoundryPaths) -> Path:
    """Resolve the admission worktree parent to an explicit absolute path.

    ``InitiativeAdmissionAuthority`` refuses a relative parent on purpose: an
    isolated worktree must never be created relative to an unknown cwd.  A
    candidate config may still express it relative to the candidate home, so
    resolve that here (and default to ``<home>/worktrees``) rather than letting
    admission raise and the discovery phase report a false BLOCKED.
    """
    raw = str(value or "").strip()
    if not raw:
        return (paths.home / "worktrees").resolve()
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (paths.home / candidate).resolve()


def _discovery_settings(
    database: FoundryDatabase,
    config: WorkerRuntimeConfig,
    paths: FoundryPaths,
) -> dict[str, Any] | None:
    """Discovery/admission settings handed to the Continuous Executive cycle.

    Observing registered projects and admitting the Executive's selected work
    is part of the canonical next-work authority, so the CE cycle receives the
    admission policy, the registered project roots and the isolated worktree
    parent instead of a second selector branch running beside it.
    """
    if not getattr(config, "continuous_discovery_enabled", True):
        return None
    from ..discovery_sources import SourceSpec
    from ..executive.intelligence import project_roots_from_sources
    from ..initiative_a3 import A3AdmissionPolicy

    raw = dict(getattr(config, "initiative_a3", None) or {})
    sources = tuple(
        SourceSpec(
            source_id=str(item["source_id"]),
            source_kind=str(item["source_kind"]),
            source_identity=str(item["source_identity"]),
            poll_interval_s=int(item.get("poll_interval_s", 300)),
            config=dict(item.get("config") or {}),
            enabled=bool(item.get("enabled", True)),
        )
        for item in (raw.get("sources") or ())
        if isinstance(item, dict)
    )
    policy_raw = dict(raw.get("policy") or {})
    if "allowed_action_classes" in policy_raw:
        policy_raw["allowed_action_classes"] = frozenset(
            str(value) for value in policy_raw["allowed_action_classes"])
    allowed_policy = {
        name: value for name, value in policy_raw.items()
        if name in A3AdmissionPolicy.__dataclass_fields__
    }
    project_roots = dict(raw.get("project_roots") or {})
    if not project_roots and sources:
        project_roots = project_roots_from_sources(sources)
    return {
        "resource_id": config.resource_id,
        "capability": config.capability,
        "sources": sources,
        "stop_path": _default_stop_path(paths, config),
        "policy": A3AdmissionPolicy(**allowed_policy),
        "project_roots": project_roots,
        "worktree_parent": str(
            _absolute_worktree_parent(raw.get("worktree_parent"), paths)
        ),
        "bounds": dict(raw.get("bounds") or {}),
    }


def _maybe_continuous_cycle(
    database,
    config,
    *,
    paths,
    loop_id,
    instance_id,
    excluded_objective_ids: Iterable[str] = (),
):
    """V9 Continuous Executive: durable wake-aware cycle (opt-in)."""
    if not getattr(config, "continuous_enabled", False):
        return None
    try:
        from ..executive.continuous import run_continuous_cycle, auto_wake_for_blocked
    except Exception as e:
        database.append(Event.create("worker_loop.continuous_blocked", {"loop_id": loop_id, "reason": f"import failed: {e}"}, source_type="supervisor", source_id=f"worker-loop:{instance_id}", idempotency_key=f"worker_loop.continuous_blocked:{loop_id}"))
        return None
    try:
        auto_wake_for_blocked(
            database,
            controller="supervisor",
            excluded_objective_ids=tuple(excluded_objective_ids),
        )
    except Exception:
        pass
    try:
        result = run_continuous_cycle(
            database,
            cycle_id=f"continuous:{loop_id}",
            controller="supervisor",
            excluded_objective_ids=tuple(excluded_objective_ids),
            discovery=_discovery_settings(database, config, paths),
            stop_path=_default_stop_path(paths, config),
        )
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
    excluded_objective_ids: Iterable[str] = (),
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
            excluded_objective_ids=tuple(sorted({str(value) for value in excluded_objective_ids})),
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
    if actual.get("outcome") != "PASSED":
        raise ValueError(
            "V3 outcome proof requires final AcceptanceGate acceptance for "
            "the latest verifier PASS")
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


def _select_contract(
    database: FoundryDatabase,
    *,
    excluded_mission_ids: Iterable[str] = (),
    preferred_mission_ids: Iterable[str] = (),
) -> str | None:
    """Select an existing mission that has a durable contract.

    Missions without executable authority are excluded from selection rather
    than converted into invented work.  A selected but malformed contract is
    returned for the caller to report as BLOCKED, preserving the distinction
    between honest WAITING and a corrupted authoritative record.
    """
    excluded = {str(value) for value in excluded_mission_ids}
    available = tuple(
        mission_id for mission_id in dispatchable_contract_missions(database)
        if str(mission_id) not in excluded
    )
    preferred = tuple(
        str(mission_id) for mission_id in preferred_mission_ids
        if str(mission_id) in available
    )
    # Campaign continuation is a bounded priority hint, not a second
    # scheduler: the canonical selector still applies owner reprioritization
    # and deterministic tie-breaking to this ordered candidate set.
    mission_ids = preferred + tuple(
        mission_id for mission_id in available if mission_id not in preferred
    )
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
    # ONE STOP authority.  The filesystem sentinel is
    # ``{foundry_home}/EMERGENCY_STOP``, but the Executive, autonomy, campaign
    # and MissionLoop defaults read ``{db.parent}/EMERGENCY_STOP``.  Those must
    # be the same path or a STOP would freeze only half the spine, so a cycle
    # whose DB does not live in the foundry home refuses to run at all instead
    # of silently watching a different sentinel.
    if Path(database.path).parent.resolve() != Path(paths.home).resolve():
        mismatch = (
            f"STOP authority split: database parent {Path(database.path).parent} "
            f"is not the foundry home {paths.home}"
        )
        try:
            database.append(Event.create(
                "worker_loop.stop_authority_mismatch",
                {"loop_id": loop_id, "instance_id": instance_id,
                 "database_path": str(database.path), "foundry_home": str(paths.home),
                 "reason": mismatch},
                source_type="supervisor", source_id=f"worker-loop:{instance_id}",
                idempotency_key=f"worker_loop.stop_authority_mismatch:{loop_id}",
            ))
        except Exception:
            pass
        return {"loop_id": loop_id, "outcome": "FROZEN", "reason": mismatch}
    estop = EmergencyStop(_default_stop_path(paths, config))
    if estop.active():
        stop = estop.read()
        campaign_stop: dict[str, Any] | None = None
        try:
            if database.event_count(event_type="campaign.created"):
                from ..executive.campaigns import propagate_stop

                campaign_stop = {
                    "stopped_campaigns": propagate_stop(
                        database,
                        stop_path=_default_stop_path(paths, config),
                        controller="supervisor",
                    )
                }
        except Exception as error:  # noqa: BLE001 - STOP remains fail-closed
            campaign_stop = {
                "error": f"{type(error).__name__}: {error}"[:300],
            }
        database.append(Event.create(
            "worker_loop.frozen",
            {"instance_id": instance_id, "loop_id": loop_id,
             "stop_id": stop.get("stop_id") if stop else None,
             "reason": stop.get("reason") if stop else "stop sentinel present"},
            source_type="supervisor", source_id=instance_id,
            idempotency_key=f"worker_loop.frozen:{loop_id}:{instance_id}",
        ))
        result = {"loop_id": loop_id, "outcome": "FROZEN",
                  "reason": "emergency stop is active"}
        if campaign_stop is not None:
            result["campaign_stop"] = campaign_stop
        return result

    leases = LeaseManager(database)
    try:
        leases.acquire(LOOP_LEASE_KEY, instance_id, 120)
    except Exception as error:  # noqa: BLE001 - lease conflict is honest
        return {"loop_id": loop_id, "outcome": "LEASE_UNAVAILABLE",
                "reason": f"{type(error).__name__}: {error}"}
    stack = None
    try:
        database.append(Event.create(
            "worker_loop.cycle_started",
            {"instance_id": instance_id, "loop_id": loop_id,
             "resource_id": config.resource_id,
             "capability": config.capability,
             "pid": os.getpid(),
             "creation_identity": process_creation_identity(os.getpid())},
            source_type="supervisor", source_id=instance_id,
            idempotency_key=f"worker_loop.cycle_started:{loop_id}",
        ))
        invoke_fault_hook(fault_hook, "post worker_loop.cycle_started")
        from ..effects import discover_reconcilable_developer_effects

        developer_effect_recovery = discover_reconcilable_developer_effects(database)
        if developer_effect_recovery:
            recovery_identity = hashlib.sha256(
                canonical_json(developer_effect_recovery).encode("utf-8")
            ).hexdigest()[:24]
            database.append(Event.create(
                "worker_loop.developer_effect_recovery_discovered",
                {
                    "loop_id": loop_id,
                    "instance_id": instance_id,
                    "candidate_count": len(developer_effect_recovery),
                    "candidates": developer_effect_recovery,
                },
                source_type="supervisor",
                source_id="worker-loop-recovery",
                idempotency_key=(
                    "worker_loop.developer_effect_recovery_discovered:"
                    f"{recovery_identity}"
                ),
            ))
        initiative_mission_id: str | None = None
        # Stranded-mission recovery (P3-D2).  The Supervisor performs this in
        # _reap_and_recover(), but it deliberately skips the call while a
        # worker-loop is alive AND its singleton lease is live.  A one-shot
        # loop refreshes that lease every cycle, so the Supervisor gate stays
        # closed exactly while a loop is running - the only time a stranded
        # mission could be picked up again.  A mission left in EXECUTING by a
        # killed session is returned by neither canonical selector
        # (dispatchable_contract_missions and select_dispatchable_mission both
        # accept only ACTION_SELECTION/REPAIRING/READY/VERIFYING) and
        # propose_next_action raises ProposalError for it, so it would stall
        # forever (live 2026-09-14: mission-65b4968b76e6).
        #
        # This loop already holds the worker-loop singleton lease, so the
        # in-process handoff race the Supervisor guard exists to protect
        # against cannot apply here: recovery still refuses any mission whose
        # RUNNING role run holds a live lease, is idempotent, and appends only
        # the two canonical recovery transitions.
        try:
            from ..roles.reaper import (
                reap_stale_role_runs,
                recover_stale_mission_state,
            )
            reap_stale_role_runs(database)
            recover_stale_mission_state(database)
        except Exception as error:  # noqa: BLE001 - recovery must not wedge the cycle
            database.append(Event.create(
                "worker_loop.recovery_failed",
                {"instance_id": instance_id, "loop_id": loop_id,
                 "error_class": type(error).__name__,
                 "error": str(error)[:300]},
                source_type="supervisor", source_id=instance_id,
                idempotency_key=f"worker_loop.recovery_failed:{loop_id}",
            ))
        campaign_resume_payload: dict[str, Any] | None = None
        campaign_blocked_mission_ids: tuple[str, ...] = ()
        campaign_dispatchable_mission_ids: tuple[str, ...] = ()
        campaign_objective_ids: tuple[str, ...] = ()
        # Once a campaign has been durably created, its recovery/dispatch
        # guard remains active even if a later worker config omits the opt-in
        # flag.  Otherwise a config flip or stale process would let campaign
        # children bypass checkpoint, STOP, wait, and join policy.  Untouched
        # V11 ledgers have no campaign.created receipt and keep the exact old
        # path.
        campaign_runtime_active = bool(
            config.campaign_enabled
            or database.event_count(event_type="campaign.created") > 0
        )
        if campaign_runtime_active:
            # Campaign recovery is a Supervisor-owned startup seam.  It only
            # reconciles durable mission/effect/provider truth and re-derives
            # wakes; dispatch still enters through the existing selector and
            # MissionLoop below.
            from ..executive.campaigns import startup_resume_pass

            try:
                campaign_resume = startup_resume_pass(
                    database,
                    stop_path=_default_stop_path(paths, config),
                    controller="supervisor",
                    fault_hook=fault_hook,
                    resource_capacity=dict(
                        config.campaign_runtime.get("resource_capacity") or {}
                    ),
                )
                campaign_resume_payload = campaign_resume.to_payload()
                campaign_blocked_mission_ids = tuple(
                    campaign_resume.blocked_mission_ids)
                campaign_dispatchable_mission_ids = tuple(
                    campaign_resume.dispatchable_mission_ids)
                from ..executive.campaigns import rebuild_campaign_state

                campaign_objective_ids = tuple(sorted({
                    campaign.objective_id
                    for campaign in rebuild_campaign_state(database).campaigns.values()
                    if campaign.status not in {"COMPLETED", "CANCELLED"}
                }))
                database.append(Event.create(
                    "worker_loop.campaign_resume_completed",
                    {
                        "loop_id": loop_id,
                        "instance_id": instance_id,
                        **campaign_resume_payload,
                    },
                    source_type="supervisor", source_id=instance_id,
                    idempotency_key=(
                        f"worker_loop.campaign_resume_completed:{loop_id}"),
                ))
                if campaign_resume.errors:
                    # Do not dispatch while the enabled campaign aggregate is
                    # only partially reconstructed.  The next bounded cycle
                    # can retry after the underlying durable fault is fixed.
                    return {
                        "loop_id": loop_id,
                        "outcome": "BLOCKED",
                        "reason": campaign_resume.reason,
                        "campaign_resume": campaign_resume_payload,
                    }
            except Exception as error:  # noqa: BLE001 - fail closed
                campaign_resume_payload = {
                    "schema": "v12.campaign-runtime.v1",
                    "status": "BLOCKED",
                    "reason": f"campaign startup recovery failed: {type(error).__name__}: {error}"[:500],
                }
                database.append(Event.create(
                    "worker_loop.campaign_resume_blocked",
                    {
                        "loop_id": loop_id,
                        "instance_id": instance_id,
                        **campaign_resume_payload,
                    },
                    source_type="supervisor", source_id=instance_id,
                    idempotency_key=(
                        f"worker_loop.campaign_resume_blocked:{loop_id}"),
                ))
                return {
                    "loop_id": loop_id,
                    "outcome": "BLOCKED",
                    "reason": campaign_resume_payload["reason"],
                    "campaign_resume": campaign_resume_payload,
                }
        # Recover accepted V3 lineage only after the campaign pass has
        # reconciled in-flight effects and exact child truth.  MissionLoop
        # remains the sole execution authority in both cases.
        _recover_v3_terminal(database, loop_id=loop_id,
                             fault_hook=fault_hook)
        if config.provider_resilience_enabled:
            # Effect reconciliation or provider recovery may occur while no
            # worker process is alive. Re-evaluate the existing durable waits
            # before any observation, admission, or mission selection so the
            # exact blocked mission can become dispatchable once.
            from ..executive.continuous import resume_mission_provider_waits

            resume_mission_provider_waits(
                database,
                stop_path=_default_stop_path(paths, config),
            )
            if campaign_runtime_active:
                # Provider recovery can release the exact campaign child in
                # this same cycle.  Refresh the selector boundary from
                # durable state instead of waiting for a second poll or
                # using the pre-recovery startup snapshot.
                from ..executive.campaigns import (
                    campaign_blocked_mission_ids as _campaign_blocked,
                    campaign_dispatchable_mission_ids as _campaign_ready,
                )

                campaign_blocked_mission_ids = _campaign_blocked(database)
                campaign_dispatchable_mission_ids = _campaign_ready(database)
        # V11 continuous observation is the durable, due-driven successor to
        # the A2 probe. It uses the existing monitor/ResearchQueue/Executive
        # wake path and still returns before objective or mission admission.
        if config.continuous_observation_enabled:
            from ..discovery_sources import SourceSpec
            from ..initiative_a2 import (
                InitiativeA2Bounds,
                run_continuous_observation_cycle,
            )

            raw = dict(config.continuous_observation or {})
            sources = tuple(SourceSpec(
                source_id=str(item["source_id"]),
                source_kind=str(item["source_kind"]),
                source_identity=str(item["source_identity"]),
                poll_interval_s=int(item.get("poll_interval_s", 300)),
                config=dict(item.get("config") or {}),
                enabled=bool(item.get("enabled", True)),
            ) for item in (raw.get("sources") or ()) if isinstance(item, dict))
            bounds_raw = dict(raw.get("bounds") or {})
            allowed_bounds = {
                name: value for name, value in bounds_raw.items()
                if name in InitiativeA2Bounds.__dataclass_fields__
            }
            report = run_continuous_observation_cycle(
                database,
                sources=sources,
                resource_id=config.resource_id,
                capability=config.capability,
                bounds=InitiativeA2Bounds(**allowed_bounds),
                stop_path=_default_stop_path(paths, config),
                fault_hook=fault_hook,
            )
            return _waiting_report(
                database, loop_id=loop_id, instance_id=instance_id,
                reason=(
                    f"V11 continuous observation {report['outcome']}: "
                    f"{report.get('reason') or 'bounded due cycle complete'}; "
                    f"next_due_at={report.get('next_due_at')}"
                ),
            )
        # V11 A2 is an inert branch at the existing worker scheduling choke
        # point.  It returns WAIT/IDLE before mission selection, objective
        # admission, effect, or provider dispatch.
        if config.initiative_a2_enabled:
            from ..discovery_sources import SourceSpec
            from ..initiative_a2 import InitiativeA2Bounds, run_initiative_a2_cycle

            raw = dict(config.initiative_a2 or {})
            sources = tuple(SourceSpec(
                source_id=str(item["source_id"]),
                source_kind=str(item["source_kind"]),
                source_identity=str(item["source_identity"]),
                poll_interval_s=int(item.get("poll_interval_s", 300)),
                config=dict(item.get("config") or {}),
            ) for item in (raw.get("sources") or ()) if isinstance(item, dict))
            if not sources:
                from ..executive.intelligence import observation_sources_from_registered_projects
                sources = observation_sources_from_registered_projects(database)
            if not sources:
                return _waiting_report(
                    database, loop_id=loop_id, instance_id=instance_id,
                    reason="V11 A2 has no registered project sources",
                )
            bounds_raw = dict(raw.get("bounds") or {})
            allowed_bounds = {
                name: value for name, value in bounds_raw.items()
                if name in InitiativeA2Bounds.__dataclass_fields__
            }
            if config.initiative_a2_enabled:
                report = run_initiative_a2_cycle(
                    database,
                    cycle_id=f"initiative-{loop_id}",
                    sources=sources,
                    resource_id=config.resource_id,
                    bounds=InitiativeA2Bounds(**allowed_bounds),
                    stop_path=_default_stop_path(paths, config),
                    fault_hook=fault_hook,
                )
                return _waiting_report(
                    database, loop_id=loop_id, instance_id=instance_id,
                    reason=(
                        f"V11 A2 {report['outcome']}: "
                        f"{report.get('reason') or 'admission boundary reached'}"
                    ),
                )

        # A3 owns only the explicit candidate admission boundary. Observation
        # uses the existing due-driven A2 driver; execution resumes through
        # the existing Executive -> portfolio -> MissionLoop path below.
        if config.initiative_a3_enabled:
            from ..discovery_sources import SourceSpec
            from ..initiative_a2 import (
                InitiativeA2Bounds,
                run_continuous_observation_cycle,
            )
            from ..initiative_a3 import (
                A3AdmissionPolicy,
                InitiativeAdmissionAuthority,
                active_initiative_objective,
                continue_initiative_objective,
                pending_initiative_decision,
            )
            raw = dict(config.initiative_a3 or {})
            from ..executive.intelligence import (
                judge_new_project_opportunity,
                prepare_executive_observation,
                record_product_wait_diagnosis,
            )
            prepare_executive_observation(database, cycle_id=loop_id)
            sources = tuple(SourceSpec(
                source_id=str(item["source_id"]),
                source_kind=str(item["source_kind"]),
                source_identity=str(item["source_identity"]),
                poll_interval_s=int(item.get("poll_interval_s", 300)),
                config=dict(item.get("config") or {}),
                enabled=bool(item.get("enabled", True)),
            ) for item in (raw.get("sources") or ()) if isinstance(item, dict))
            if not sources:
                from ..executive.intelligence import observation_sources_from_registered_projects
                sources = observation_sources_from_registered_projects(
                    database,
                    required_capabilities=tuple(
                        A3AdmissionPolicy().required_fabric_capabilities
                    ),
                )
            if not sources:
                record_product_wait_diagnosis(
                    database,
                    cycle_id=loop_id,
                    reason="V11 A3 has no registered project sources",
                )
                judge_new_project_opportunity(database, None)
                return _waiting_report(
                    database, loop_id=loop_id, instance_id=instance_id,
                    reason="V11 A3 has no registered project sources",
                )
            bounds_raw = dict(raw.get("bounds") or {})
            allowed_bounds = {
                name: value for name, value in bounds_raw.items()
                if name in InitiativeA2Bounds.__dataclass_fields__
            }
            policy_raw = dict(raw.get("policy") or {})
            if "allowed_action_classes" in policy_raw:
                policy_raw["allowed_action_classes"] = frozenset(
                    str(value) for value in policy_raw["allowed_action_classes"])
            allowed_policy = {
                name: value for name, value in policy_raw.items()
                if name in A3AdmissionPolicy.__dataclass_fields__
            }
            project_roots = dict(raw.get("project_roots") or {})
            if not project_roots:
                from ..executive.intelligence import project_roots_from_sources
                project_roots = project_roots_from_sources(sources)
            authority = InitiativeAdmissionAuthority(
                database,
                worktree_parent=str(raw.get("worktree_parent") or ""),
                project_roots=project_roots,
                policy=A3AdmissionPolicy(**allowed_policy),
                stop_path=_default_stop_path(paths, config),
            )

            # Existing mission contracts always run before another observation
            # or admission pass. This is the normal MissionLoop continuation,
            # not a new scheduler.
            initiative_mission_id = _select_contract(database)
            decision = None
            admission = None
            if initiative_mission_id is None:
                active_objective = active_initiative_objective(database)
                if active_objective is not None:
                    admission = continue_initiative_objective(
                        database,
                        active_objective,
                        cycle_id=f"initiative-executive-{loop_id}",
                    )
                else:
                    decision = pending_initiative_decision(database)
                    if decision is None:
                        report = run_continuous_observation_cycle(
                            database,
                            sources=sources,
                            resource_id=config.resource_id,
                            capability=config.capability,
                            bounds=InitiativeA2Bounds(**allowed_bounds),
                            stop_path=_default_stop_path(paths, config),
                            fault_hook=fault_hook,
                        )
                        decision = report.get("decision")
                        if getattr(decision, "kind", None) != "SELECT":
                            wait_reason = (
                                f"V11 A3 WAIT: {report.get('reason') or 'no eligible candidate'}"
                            )
                            record_product_wait_diagnosis(
                                database,
                                cycle_id=loop_id,
                                decision=decision if hasattr(decision, "kind") else None,
                                reason=wait_reason,
                            )
                            judge_new_project_opportunity(
                                database,
                                decision if hasattr(decision, "kind") else None,
                            )
                            return _waiting_report(
                                database, loop_id=loop_id, instance_id=instance_id,
                                reason=wait_reason,
                            )
                        invoke_fault_hook(fault_hook, "post initiative.selection")
                    admission = authority.admit(decision)
                    if admission.admitted and admission.objective_id:
                        invoke_fault_hook(fault_hook, "post initiative.objective_created")
                        admission = continue_initiative_objective(
                            database,
                            admission.objective_id,
                            cycle_id=f"initiative-executive-{loop_id}",
                        )
                if admission.mission_id:
                    invoke_fault_hook(fault_hook, "post initiative.mission_created")
                initiative_mission_id = admission.mission_id
                if initiative_mission_id is None:
                    wait_reason = f"V11 A3 {admission.status}: {admission.reason}"
                    record_product_wait_diagnosis(
                        database,
                        cycle_id=loop_id,
                        decision=decision if hasattr(decision, "kind") else None,
                        admission=admission,
                        reason=wait_reason,
                    )
                    judge_new_project_opportunity(
                        database,
                        decision if hasattr(decision, "kind") else None,
                    )
                    return _waiting_report(
                        database, loop_id=loop_id, instance_id=instance_id,
                        reason=wait_reason,
                    )
        # V3 WP3 single choke point: when discovery.cycle_enabled the
        # unified experimental cycle replaces the split selection path.
        # Flag OFF (default) keeps v3_loop WAIT + executive selection
        # exactly as before — no other code changes.
        if config.discovery_cycle_enabled and not campaign_dispatchable_mission_ids:
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
            preferred_mission_ids = campaign_dispatchable_mission_ids
            if initiative_mission_id is not None:
                preferred_mission_ids += (initiative_mission_id,)
            mission_id = _select_contract(
                database,
                excluded_mission_ids=campaign_blocked_mission_ids,
                preferred_mission_ids=preferred_mission_ids,
            )
            if mission_id is None and getattr(config, "continuous_enabled", False):
                mission_id = _maybe_continuous_cycle(
                    database,
                    config,
                    paths=paths,
                    loop_id=loop_id,
                    instance_id=instance_id,
                    excluded_objective_ids=campaign_objective_ids,
                )
                # The Continuous Executive is the canonical next-work authority.
                # Once it has run, no later factory may create the mission in
                # this loop_id: CE returning None is an honest WAIT/STALL, and a
                # fall-through here is how two selectors used to win one cycle.
            elif mission_id is None and config.executive_autonomy_enabled:
                mission_id = _maybe_executive_autonomy(
                    database, config, paths=paths,
                    loop_id=loop_id, instance_id=instance_id,
                    excluded_objective_ids=campaign_objective_ids)
            if mission_id is None and not getattr(config, "continuous_enabled", False) \
                    and config.executive_selection_enabled:
                mission_id = _maybe_executive_select(
                    database, config, loop_id=loop_id, instance_id=instance_id)
            if mission_id is None and not getattr(config, "continuous_enabled", False) \
                    and config.portfolio_selection_enabled:
                mission_id = _maybe_portfolio_select(
                    database,
                    config,
                    loop_id=loop_id,
                    instance_id=instance_id,
                    excluded_mission_ids=campaign_blocked_mission_ids,
                )
        # Discovery-cycle and other selector branches can also return a
        # mission.  Apply the campaign guard once at the common choke point so
        # a blocked child can never bypass STOP/wait/dependency/replan policy.
        if mission_id is not None and campaign_runtime_active:
            from ..executive.campaigns import campaign_blocked_mission_ids
            if str(mission_id) in set(campaign_blocked_mission_ids(database)):
                database.append(Event.create(
                    "worker_loop.campaign_dispatch_blocked",
                    {
                        "loop_id": loop_id,
                        "instance_id": instance_id,
                        "mission_id": str(mission_id),
                        "reason": "campaign child is waiting, stopped, incomplete, or awaiting replan",
                    },
                    source_type="supervisor", source_id=instance_id,
                    idempotency_key=(
                        f"worker_loop.campaign_dispatch_blocked:{loop_id}"),
                ))
                mission_id = None
        if mission_id is None:
            waiting_report = _waiting_report(
                database, loop_id=loop_id, instance_id=instance_id,
                reason="no dispatchable mission has an authoritative executable contract",
            )
            if campaign_resume_payload is not None:
                waiting_report["campaign_resume"] = campaign_resume_payload
            return waiting_report
        _end_waiting_episode(
            database, loop_id=loop_id, instance_id=instance_id)
        try:
            mission_row = database.row("missions", "mission_id", mission_id)
            state = str((mission_row or {}).get("state") or "")
            contract = None
            if state in {"ACTION_SELECTION", "VERIFYING"}:
                contract = _accepted_contract_pending_evidence(
                    database, mission_id,
                )
            contract = contract or next_executable_contract(database, mission_id)
            if contract is None:
                contracts = latest_contracts_by_step(database, mission_id)
                # Recovery may reopen an already accepted terminal contract
                # at ACTION_SELECTION. Supply one immutable contract only to
                # enter MissionLoop's accepted-result replay path; it restores
                # missing evidence and continuation without redispatch.
                if state == "ACTION_SELECTION" and contract_rows(
                    database, mission_id
                ) and contracts:
                    contract = contracts[sorted(contracts)[-1]]
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

        stack = build_worker_stack(
            database, paths, config, run_id=f"loop-{loop_id}")
        _pin_routing_to_hermes(stack, config)
        loop = build_canonical_mission_loop(
            database,
            paths,
            config,
            stack=stack,
            leases=leases,
            source_id=f"worker-loop:{instance_id}",
            fault_hook=fault_hook,
            mission_loop_cls=MissionLoop,
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
        mission_after = database.row("missions", "mission_id", mission_id) or {}
        portfolio_terminal = (
            terminal in ("FAILED", "BLOCKED")
            or (terminal == "PASSED"
                and str(mission_after.get("state") or "") == "COMPLETE")
        )
        if portfolio_terminal:
            # One accepted, event-derived outcome contract is the only input
            # to calibration, World Intelligence, and Tier-C memory. Runtime
            # StepOutcome/provider claims are intentionally not forwarded.
            learning: dict[str, Any] = {"finalized": False}
            try:
                from ..world_model.connector import reconcile_outcome_learning
                learning = reconcile_outcome_learning(
                    database, mission_id=mission_id,
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
            if learning.get("finalized"):
                portfolio_status = {
                    "SUCCEEDED": "PASSED",
                    "FAILED": "FAILED",
                    "BLOCKED": "BLOCKED",
                    "REJECTED": "CANCELLED",
                }.get(str(learning.get("status") or ""), "UNKNOWN")
                _record_mission_portfolio_outcome(
                    database, mission_id=mission_id, outcome=outcome,
                    status=portfolio_status, instance_id=instance_id)
            if learning.get("finalized") and (
                config.executive_autonomy_enabled or config.initiative_a3_enabled
            ):
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
            elif learning.get("finalized") and getattr(config, "continuous_enabled", False):
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
            if learning.get("finalized") and config.initiative_a3_enabled:
                try:
                    from ..initiative_a3 import record_terminal_initiative_outcome
                    record_terminal_initiative_outcome(
                        database,
                        mission_id=mission_id,
                        runtime_outcome=str(terminal or "UNKNOWN"),
                        verdict_id=getattr(outcome, "verdict_id", None),
                    )
                except Exception as error:  # noqa: BLE001 - fail closed; mission truth stays authoritative
                    database.append(Event.create(
                        "worker_loop.initiative_outcome_blocked",
                        {
                            "loop_id": loop_id,
                            "mission_id": mission_id,
                            "reason": f"{type(error).__name__}: {error}"[:500],
                        },
                        source_type="supervisor",
                        source_id=instance_id,
                        idempotency_key=f"worker_loop.initiative_outcome_blocked:{loop_id}",
                    ))
                try:
                    from ..executive.intelligence import schedule_next_action_after_outcome
                    schedule_next_action_after_outcome(
                        database,
                        mission_id=mission_id,
                        outcome=str(terminal or getattr(outcome, "outcome", "UNKNOWN")),
                    )
                except Exception as error:  # noqa: BLE001 - outcome remains authoritative
                    database.append(Event.create(
                        "worker_loop.next_action_schedule_blocked",
                        {
                            "loop_id": loop_id,
                            "mission_id": mission_id,
                            "reason": f"{type(error).__name__}: {error}"[:500],
                        },
                        source_type="supervisor",
                        source_id=instance_id,
                        idempotency_key=f"worker_loop.next_action_schedule_blocked:{loop_id}",
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
        result = {
            "loop_id": loop_id,
            "outcome": outcome.outcome,
            "mission_id": mission_id,
            "step_id": outcome.step_id,
            "run_id": outcome.run_id,
            "verdict_id": outcome.verdict_id,
            "reason": outcome.reason,
        }
        if campaign_resume_payload is not None:
            result["campaign_resume"] = campaign_resume_payload
        return result
    finally:
        if stack is not None:
            close_stack = getattr(stack, "close", None)
            if callable(close_stack):
                close_stack()
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
