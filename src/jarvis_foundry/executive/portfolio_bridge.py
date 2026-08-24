"""Cross-lane V7 wiring: portfolio -> operations -> resilience -> outcome.

This module is the single reconciliation seam between the four V7 surfaces:

- V7A executive portfolio decisions become executable missions without
  creating a second mission/scheduler authority: candidate selections reuse
  ``executive.missions.materialize_selection``; every other class is
  materialized through the canonical ``mission.created`` event plus a
  bounded C1 step contract.
- V7B broad operations receive portfolio work as durable procedures bound to
  a real mission id (``plan_broad_operation``), keeping effect tickets and
  lineage intact.
- V5 governance stays authoritative for execution readiness:
  ``capability_readiness`` reads the durable capability state plane so
  incident suspensions (STOPPED/VETOED/DEGRADED) block procedure steps.
- Mission terminal outcomes flow back into portfolio calibration through
  ``record_mission_portfolio_outcome``.

STOP and owner vetoes are re-checked at materialization time; a selection is
permission to attempt materialization, never irrevocable authority.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterable, Mapping

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event, EventConflict
from ..mission.contracts import MissionStepContract, record_step_contract
from ..operations.procedure import (
    OperationError,
    OperationStep,
    ProcedurePlan,
    create_procedure,
)
from ..supervisor.evolution_control import EvolutionControlError
from .missions import materialize_selection


def latest_capability_state(
    database: FoundryDatabase,
    resource_id: str,
    capability: str,
) -> dict[str, Any] | None:
    """Return the newest durable capability.state_changed for the pair."""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json, source_id FROM events "
            "WHERE event_type = 'capability.state_changed' "
            "AND json_extract(payload_json, '$.resource_id') = ? "
            "AND json_extract(payload_json, '$.capability') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (resource_id, capability),
        ).fetchone()
    if row is None:
        return None
    raw = row["payload_json"]
    payload = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    return {
        "state": str(payload.get("state") or ""),
        "source_id": str(row["source_id"] or ""),
    }


_BLOCKING_CAPABILITY_STATES = frozenset({"STOPPED", "VETOED", "DEGRADED"})


def capability_readiness(
    database: FoundryDatabase,
    *,
    resource_id: str,
    capability: str,
) -> dict[str, Any]:
    """Report whether a capability may execute right now.

    An incident suspension recorded on the capability plane blocks execution;
    an unregistered capability is UNKNOWN and left to the caller's policy.
    """
    latest = latest_capability_state(database, resource_id, capability)
    if latest is None:
        return {"ready": True, "state": "UNREGISTERED",
                "reason": "no capability state recorded", "blocked_by": None}
    state = latest["state"]
    if state in _BLOCKING_CAPABILITY_STATES:
        return {
            "ready": False, "state": state,
            "reason": f"capability {capability} is {state}",
            "blocked_by": latest["source_id"],
        }
    return {"ready": True, "state": state, "reason": "", "blocked_by": None}


def _stop_or_veto(database: FoundryDatabase) -> str | None:
    stop = emergency_stop_reason(
        database, database.path.parent / "EMERGENCY_STOP")
    if stop:
        return f"STOP active: {stop}"
    veto = matching_veto_reason(database, {"GLOBAL"})
    if veto:
        return f"owner veto active: {veto}"
    return None


def _materialization_for_decision(
    database: FoundryDatabase,
    decision_id: str,
) -> str | None:
    for event in reversed(database.events()):
        if event.event_type == "portfolio.selection_materialized" \
                and str(event.payload.get("decision_id") or "") == decision_id:
            mission_id = str(event.payload.get("mission_id") or "")
            return mission_id or None
    return None


def materialize_portfolio_selection(
    database: FoundryDatabase,
    *,
    decision: Mapping[str, Any],
    resource_id: str,
    capability: str = "write_registered_worktree_files",
    source_id: str = "v7-portfolio-bridge",
) -> dict[str, Any]:
    """Turn a SELECT portfolio decision into a dispatchable mission.

    Returns {"materialized": bool, "mission_id": ..., "blocked_reason": ...}.
    Idempotent per decision id via the created_from marker.
    """
    kind = str(decision.get("selected_class") or "")
    item = dict(decision.get("selected_item") or {})
    item_id = str(item.get("item_id") or "")
    decision_id = str(decision.get("decision_id") or "")
    objective = str(item.get("objective") or decision.get("reason") or kind)

    blocked = _stop_or_veto(database)
    if blocked:
        return {"materialized": False, "mission_id": None,
                "blocked_reason": blocked}

    if kind == "CANDIDATE":
        candidate_id = str(item.get("candidate_id") or "")
        if not candidate_id:
            return {"materialized": False, "mission_id": None,
                    "blocked_reason": "candidate selection lacks candidate_id"}
        result = materialize_selection(
            database,
            candidate_id=candidate_id,
            objective=objective,
            resource_id=resource_id,
            capability=capability,
            decision_id=decision_id,
            source_id=source_id,
        )
        return {
            "materialized": bool(result.get("materialized")),
            "mission_id": result.get("mission_id"),
            "blocked_reason": result.get("blocked_reason"),
        }

    if kind in ("ACTIVE_MISSION", "REPAIR"):
        mission_id = str(item.get("mission_id") or "")
        if not mission_id:
            return {"materialized": False, "mission_id": None,
                    "blocked_reason": f"{kind} item lacks mission_id"}
        return {"materialized": True, "mission_id": mission_id,
                "blocked_reason": None}

    if kind not in ("MAINTENANCE", "RESEARCH", "UNKNOWN", "IMPROVEMENT",
                    "INCIDENT", "NEW_MISSION"):
        return {"materialized": False, "mission_id": None,
                "blocked_reason": f"unsupported portfolio class {kind!r}"}

    prior = _materialization_for_decision(database, decision_id)
    if prior is not None:
        return {"materialized": True, "mission_id": prior,
                "blocked_reason": None}

    mission_id = f"mission-{uuid.uuid4().hex[:12]}"
    acceptance = [
        f"{objective} completed within declared capacity/budget",
        "independent verification PASS or honest BLOCKED with evidence",
    ]
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": objective,
            "resource_id": resource_id,
            "state": "PLANNED",
            "acceptance_criteria": acceptance,
            "next_action": f"execute: {objective}",
            "created_from": f"portfolio:{item_id}",
        },
        source_type="supervisor",
        source_id=source_id,
        correlation_id=resource_id,
        idempotency_key=f"mission.created:portfolio:{item_id}:{decision_id}",
    ))
    for state, next_action in (
        ("ORIENTING", f"orient: {objective}"),
        ("ACTION_SELECTION", f"execute: {objective}"),
        ("READY", f"execute: {objective}"),
    ):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state,
             "next_action": next_action},
            source_type="supervisor",
            source_id=source_id,
            idempotency_key=(
                f"mission.state_changed:{mission_id}:{state}:portfolio"),
        ))

    artifact_name = f"report-{decision_id[:8] or 'portfolio'}.md"
    contract = MissionStepContract(
        mission_id=mission_id,
        step_id="portfolio-step",
        version=1,
        resource_id=resource_id,
        capability=capability,
        target_relative_path=artifact_name,
        instructions=(
            f"Portfolio class {kind}. Objective: {objective}\n"
            f"Create {artifact_name} stating: (1) the objective, "
            f"(2) what was verified with evidence, (3) VERIFIED_BY "
            f"portfolio-worker. Under 1200 bytes."
        ),
        expected_effects=(f"{artifact_name} exists",),
        rollback={"method": "delete_file", "reversible": True},
        risk_class="C1",
        verification_criteria=(
            f"{artifact_name} exists",
            "report states the objective",
        ),
        expected_artifacts=(artifact_name,),
        forbidden_paths=(),
        recorded_by="supervisor",
        source="portfolio",
        provenance={
            "decision_id": decision_id,
            "portfolio_item_id": item_id,
            "portfolio_class": kind,
            "incident_id": item.get("metadata", {}).get("incident_id"),
        },
    )
    record_step_contract(database, contract)
    database.append(Event.create(
        "portfolio.selection_materialized",
        {
            "decision_id": decision_id,
            "item_id": item_id,
            "kind": kind,
            "mission_id": mission_id,
            "contract_id": contract.contract_id,
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"portfolio.selection_materialized:{decision_id}",
    ))
    return {"materialized": True, "mission_id": mission_id,
            "blocked_reason": None}


def _operation_step(data: Mapping[str, Any]) -> OperationStep:
    """Build a safe OperationStep from a plain mapping.

    Defaults are conservative: local, reversible, lowest risk class.
    """
    rollback = dict(data.get("rollback") or {})
    compensation = dict(data.get("compensation") or {})
    if not rollback and not compensation:
        rollback = {"method": "none", "reversible": True,
                    "note": "default bridge rollback stub"}
    return OperationStep(
        step_id=str(data.get("step_id") or ""),
        action_kind=str(data.get("action_kind") or data.get("action") or ""),
        target=str(data.get("target") or "local:disposable"),
        preconditions=tuple(str(item) for item in (
            data.get("preconditions") or ())),
        capabilities=tuple(str(item) for item in (
            data.get("capabilities") or ())),
        risk_class=str(data.get("risk_class") or "R1"),
        reversibility="reversible",
        postconditions=tuple(str(item) for item in (
            data.get("postconditions") or ())),
        rollback=rollback,
        compensation=compensation,
        effect_ticket=str(data.get("effect_ticket") or ""),
        evidence_refs=tuple(str(item) for item in (
            data.get("evidence_refs") or ())),
    )


def plan_broad_operation(
    database: FoundryDatabase,
    *,
    procedure_id: str,
    mission_id: str,
    target: str,
    steps: Iterable[Mapping[str, Any]],
    capabilities: Iterable[str],
    preconditions: Iterable[str] = (),
    evidence_refs: Iterable[str] = (),
    metadata: Mapping[str, Any] | None = None,
    source_id: str = "v7-portfolio-bridge",
) -> ProcedurePlan:
    """Bind a V7B durable procedure to a portfolio-owned mission."""
    plan = ProcedurePlan(
        procedure_id=procedure_id,
        mission_id=mission_id,
        target=target,
        steps=tuple(
            step if isinstance(step, OperationStep)
            else _operation_step(dict(step))
            for step in steps),
        preconditions=tuple(preconditions),
        capabilities=tuple(capabilities),
        evidence_refs=tuple(evidence_refs),
        metadata=dict(metadata or {}),
    )
    try:
        create_procedure(database, plan)
    except EventConflict:
        pass
    database.append(Event.create(
        "portfolio.broad_operation_planned",
        {
            "procedure_id": procedure_id,
            "mission_id": mission_id,
            "target": target,
                "step_ids": [step.step_id for step in plan.steps],
            "capabilities": list(plan.capabilities),
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"portfolio.broad_operation_planned:{procedure_id}",
    ))
    return plan


def gate_procedure_step_capability(
    database: FoundryDatabase,
    *,
    resource_id: str,
    capability: str,
    procedure_id: str,
    step_id: str,
    effect_ticket: str,
    source_id: str = "v7-portfolio-bridge",
) -> dict[str, Any]:
    """V7B->V5 seam: refuse to start a step whose capability is suspended.

    Raises OperationError when blocked; returns the readiness receipt when
    allowed. The receipt is journaled so restarts see the same gate result.
    """
    readiness = capability_readiness(
        database, resource_id=resource_id, capability=capability)
    receipt = {
        "procedure_id": procedure_id,
        "step_id": step_id,
        "effect_ticket": effect_ticket,
        **readiness,
    }
    database.append(Event.create(
        "operations.capability_gate_checked",
        receipt,
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=(
            f"operations.capability_gate_checked:{procedure_id}:"
            f"{step_id}:{effect_ticket}"),
    ))
    if not readiness["ready"]:
        raise OperationError(
            f"capability gate blocked step {step_id} of {procedure_id}: "
            f"{readiness['reason']} (by {readiness['blocked_by']})")
    return receipt


_OUTCOME_STATUS = frozenset({"PASSED", "FAILED", "BLOCKED", "NOT_TESTED"})


def record_mission_portfolio_outcome(
    database: FoundryDatabase,
    *,
    decision: Mapping[str, Any],
    mission_id: str,
    status: str,
    actual: Mapping[str, Any] | None = None,
    outcome_id: str | None = None,
    repair_count: int = 0,
    actual_cost_usd: float = 0.0,
    source_id: str = "v7-portfolio-bridge",
) -> str:
    """Reconcile a materialized selection's terminal truth into calibration."""
    from ..evolution.executive_portfolio import (
        record_portfolio_outcome,
        set_work_status,
    )

    if status not in _OUTCOME_STATUS:
        raise ValueError(f"unsupported outcome status: {status!r}")
    item = dict(decision.get("selected_item") or {})
    predicted = {
        "expected_value": item.get("expected_value"),
        "uncertainty": decision.get("uncertainty"),
        "selected_score": decision.get("selected_score"),
    }
    resolved_id = outcome_id or f"outcome-{uuid.uuid4().hex[:12]}"
    record_portfolio_outcome(
        database,
        outcome_id=resolved_id,
        selection_id=str(decision.get("decision_id") or ""),
        entity_id=str(item.get("item_id") or ""),
        kind=str(decision.get("selected_class") or ""),
        predicted=predicted,
        actual=dict(actual or {}),
        status=status,
        repair_count=max(0, int(repair_count)),
        actual_cost_usd=float(actual_cost_usd or 0.0),
        source_id=source_id,
    )
    # Close the loop: a terminal outcome retires the portfolio item so the
    # next selection cycle cannot re-admit completed work as busywork.
    closing_status = {
        "PASSED": "COMPLETED",
        "FAILED": "FAILED",
        "BLOCKED": "BLOCKED",
        "NOT_TESTED": "BLOCKED",
    }[status]
    entity = str(item.get("item_id") or "")
    if entity:
        try:
            set_work_status(
                database, entity, closing_status,
                reason=f"mission {mission_id} terminal: {status}",
                source_type="supervisor", source_id=source_id)
        except (ValueError, KeyError, EvolutionControlError) as error:
            database.append(Event.create(
                "portfolio.work_status_close_blocked",
                {"item_id": entity, "reason": str(error)},
                source_type="supervisor", source_id=source_id,
                idempotency_key=(
                    f"portfolio.work_status_close_blocked:{entity}:{resolved_id}"),
            ))
    return resolved_id
