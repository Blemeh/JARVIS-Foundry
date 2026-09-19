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
  ``capability_readiness`` reads the durable capability state plane.
  Missing records are UNKNOWN and not ready. Incident suspensions
  (STOPPED/VETOED/DEGRADED) block procedure steps. The procedure gate
  itself only refuses those suspensions; callers that dispatch work must
  treat UNKNOWN as not ready.
- Mission terminal outcomes flow back into portfolio calibration through
  ``record_mission_portfolio_outcome``.

STOP and owner vetoes are re-checked at materialization time; a selection is
permission to attempt materialization, never irrevocable authority.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from typing import Any, Iterable, Mapping

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..control.capabilities import capability_state_recorded, set_capability_state
from ..database import FoundryDatabase
from ..events import Event, EventConflict
from ..fabric.catalog import get_spec, observed_readiness
from ..mission.contracts import MissionStepContract, record_step_contract
from ..mission.intent import (
    ACTIONS as INTENT_ACTIONS,
    BLOCKED as INTENT_BLOCKED,
    GAP as INTENT_GAP,
    WORKFLOW as INTENT_WORKFLOW,
    MissionIntent,
    MissionIntentError,
    MissionResolution,
    resolve_mission_intent,
)
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
    with database.connect(readonly=True) as connection:
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
_READY_CAPABILITY_STATES = frozenset({"READY", "ACTIVE"})


def capability_readiness(
    database: FoundryDatabase,
    *,
    resource_id: str,
    capability: str,
) -> dict[str, Any]:
    """Report whether a capability may execute right now.

    An incident suspension recorded on the capability plane blocks execution.
    A missing record is UNKNOWN, never ready. Callers that only care about
    incident STOPPED/VETOED/DEGRADED must inspect ``state``, not ``ready``.
    """
    latest = latest_capability_state(database, resource_id, capability)
    if latest is None:
        return {
            "ready": False,
            "state": "UNKNOWN",
            "reason": "no capability state recorded",
            "blocked_by": None,
        }
    state = latest["state"]
    if state in _BLOCKING_CAPABILITY_STATES:
        return {
            "ready": False, "state": state,
            "reason": f"capability {capability} is {state}",
            "blocked_by": latest["source_id"],
        }
    if state in _READY_CAPABILITY_STATES:
        return {"ready": True, "state": state, "reason": "", "blocked_by": None}
    return {
        "ready": False,
        "state": state,
        "reason": f"capability {capability} is {state}",
        "blocked_by": None,
    }


def _retire_unexecutable(
    database: FoundryDatabase,
    *,
    mission_id: str,
    item_id: str,
    kind: str,
    decision_id: str,
    source_id: str,
) -> None:
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": mission_id,
            "state": "CANCELLED",
            "next_action": (
                f"retired by portfolio: no executable step contract "
                f"remains ({kind} selection)"),
            "reason": (
                "bounded portfolio policy: repair without an "
                "executable contract cannot make progress"),
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=(
            f"mission.state_changed:{mission_id}:CANCELLED:"
            f"portfolio-unexecutable"),
    ))
    database.append(Event.create(
        "portfolio.repair_retired",
        {
            "decision_id": decision_id,
            "item_id": item_id,
            "kind": kind,
            "mission_id": mission_id,
            "reason": "no remaining executable step contract",
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=(
            f"portfolio.repair_retired:{decision_id}:{mission_id}"),
    ))


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


def _intent_token(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_." else "-" for char in str(value))
    return cleaned.strip("-.")[:80] or "intent"


def _owner_goal_intent(
    *,
    mission_id: str,
    resource_id: str,
    objective: str,
    metadata: Mapping[str, Any],
) -> tuple[MissionIntent | None, str]:
    block = metadata.get("intent")
    if not isinstance(block, Mapping):
        return None, "owner goal carries no normalized intent block"
    try:
        intent = MissionIntent.from_mapping(
            block,
            intent_id=f"owner-goal:{mission_id}",
            resource_id=resource_id,
            objective=objective,
            source="owner-goal-admission",
        )
    except MissionIntentError as error:
        return None, f"owner goal intent is invalid: {error}"
    return intent, ""


def _declared_fabric_capabilities(
    database: FoundryDatabase,
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    """Fabric capabilities the selected work item DECLARED as required.

    Goal section 3.  Two declared sources, both available at materialization
    time and both written by the runtime itself:

    * the work item metadata (``generate_missions_for_objective`` copies the
      plan step's ``required_capabilities`` onto the item before the mission
      exists), and
    * the ACTIVE plan step the item declares - ``executive_objective_id`` and
      ``executive_step_id`` name it explicitly, so this is a declared
      correspondence, never a guessed mapping between the plan namespace and
      the contract namespace.

    The second source matters because the runtime only discovers some
    requirements while materializing: ``_record_unrealizable_intent`` attaches
    the missing ``cap.*`` to the plan step, and the work item metadata written
    before that attempt is then stale.  Reading the plan step as well is what
    keeps the requirement the gap/resume machinery actually acquired identical
    to the requirement the mission records.

    Only canonical ``cap.*`` ids the Fabric Catalog can prove are returned: a
    legacy resource capability keeps its existing compatibility path, and an
    unknown ``cap.*`` stays the honest gap owned by the capability bridge.
    """

    declared: list[Any] = []
    raw = metadata.get("required_capabilities") or ()
    if isinstance(raw, (list, tuple)):
        declared.extend(raw)
    objective_id = str(metadata.get("executive_objective_id") or "")
    step_id = str(metadata.get("executive_step_id") or "")
    if objective_id and step_id:
        try:
            from .autonomy import active_plan

            plan = active_plan(database, objective_id)
        except Exception:  # noqa: BLE001 - an unreadable plan is not a refusal
            plan = None
        if plan is not None:
            for step in plan.steps:
                if step.step_id == step_id:
                    declared.extend(step.required_capabilities)
    ordered: list[str] = []
    for item in declared:
        capability_id = str(item or "").strip()
        if not capability_id.startswith("cap."):
            continue
        if get_spec(database, capability_id) is None:
            continue
        if capability_id not in ordered:
            ordered.append(capability_id)
    return tuple(ordered)


def _bind_resolution_capabilities(
    database: FoundryDatabase,
    resource_id: str,
    capability_ids: Iterable[str],
    *,
    source_id: str,
    resolution: MissionResolution | None = None,
    objective_id: str = "",
) -> None:
    """Bind Catalog-proven runtime capabilities onto the mission resource.

    The mission step gate requires the capability to be declared for the
    resource.  Recording the canonical ``capability.state_changed`` binding here
    is what removes the developer-supplied driver step from the normal path: for
    a resolved intent the resolver already proved the Catalog registration, and
    for a DECLARED plan-step requirement (goal section 3) the gap/resume
    machinery already proved it by acquiring the capability.
    """
    for capability_id in capability_ids:
        spec = get_spec(database, capability_id)
        if spec is None:
            continue
        if capability_state_recorded(database, resource_id, capability_id):
            # The binding is recorded once per (resource, capability); the
            # per-mission resolution lineage lives in the contract provenance.
            continue
        readiness = observed_readiness(database, capability_id)
        trust = str(getattr(readiness, "trust_state", "") or "")
        state = "DISCOVERED" if trust == "DISABLED" else "READY"
        evidence: dict[str, Any] = {
            "source": (
                "mission-intent-resolution" if resolution is not None
                else "declared-plan-step-requirement"
            ),
            "catalog_state": str(getattr(readiness, "state", "") or ""),
            "catalog_trust": trust,
            "catalog_version": spec.version,
        }
        if resolution is not None:
            evidence["intent_id"] = resolution.intent_id
            evidence["resolution_kind"] = resolution.kind
        else:
            evidence["objective_id"] = str(objective_id or "")
        set_capability_state(
            database,
            resource_id,
            capability_id,
            state,
            source_id=source_id,
            evidence=evidence,
        )


def _action_target(
    payload: Mapping[str, Any], *, intent_id: str, index: int,
) -> str:
    candidate = str(payload.get("target") or "")
    if candidate and not candidate.startswith("/") and ":" not in candidate.split("/")[0]:
        parts = [part for part in candidate.replace("\\", "/").split("/") if part not in ("", ".")]
        if parts and ".." not in parts:
            return "/".join(parts)
    return f".jarvis/intents/{_intent_token(intent_id)}/step-{index:02d}"


def _materialize_intent_action_plan(
    database: FoundryDatabase,
    *,
    mission_id: str,
    resource_id: str,
    objective: str,
    decision_id: str,
    item_id: str,
    kind: str,
    resolution: MissionResolution,
    provenance: Mapping[str, Any],
    source_id: str,
    required_capabilities: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Materialize a resolved action plan as ordered immutable contracts."""
    contracts: list[MissionStepContract] = []
    for index, action in enumerate(resolution.actions, start=1):
        action_kind = str(action.get("action_kind") or "").strip()
        capability_id = str(action.get("capability_id") or f"cap.{action_kind}")
        payload = dict(action.get("payload") or {})
        target = _action_target(payload, intent_id=resolution.intent_id, index=index)
        criteria = tuple(resolution.provenance.get("success_criteria") or ()) if isinstance(
            resolution.provenance.get("success_criteria"), (list, tuple)) else ()
        contract = MissionStepContract(
            mission_id=mission_id,
            step_id=f"{index:02d}-action",
            version=1,
            resource_id=resource_id,
            capability=capability_id,
            target_relative_path=target,
            instructions=json.dumps(payload, ensure_ascii=True, sort_keys=True),
            expected_effects=(
                f"{action_kind} executes once inside the registered resource",
                f"{action_kind} result is independently verified",
            ),
            rollback={
                "method": "revert registered-resource effects of this action step",
                "reversible": True,
            },
            risk_class="C1",
            verification_criteria=tuple(criteria) or (
                f"{action_kind} completes and is independently verified",
            ),
            expected_artifacts=(),
            forbidden_paths=(),
            required_capabilities=required_capabilities,
            recorded_by="supervisor",
            source="owner-goal-intent-resolution",
            provenance={
                **dict(provenance),
                "need": action.get("need"),
                "action_kind": action_kind,
            },
        )
        record_step_contract(database, contract)
        contracts.append(contract)
    _bind_resolution_capabilities(
        database, resource_id,
        (contract.capability for contract in contracts),
        source_id=source_id, resolution=resolution,
    )
    database.append(Event.create(
        "portfolio.selection_materialized",
        {
            "decision_id": decision_id,
            "item_id": item_id,
            "kind": kind,
            "mission_id": mission_id,
            "contract_ids": [contract.contract_id for contract in contracts],
            "intent_id": resolution.intent_id,
            "resolution": resolution.as_dict(),
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"portfolio.selection_materialized:{decision_id}",
    ))
    return {"materialized": True, "mission_id": mission_id, "blocked_reason": None}


def _record_unrealizable_intent(
    database: FoundryDatabase,
    *,
    objective_id: str,
    mission_id: str,
    missing_capability_ids: tuple[str, ...],
    next_action: str,
    source_id: str,
) -> None:
    """Fail the attempt honestly and release the mission for the retry path.

    A mission whose intent cannot be realized must not stay stranded in READY:
    its attempt failed, the required capabilities are recorded on the plan step
    (so the canonical gap/WAIT/expansion bridge owns them), and the Executive's
    bounded retry is what resumes the same objective once the capability
    exists.
    """
    from ..events import Event as _Event

    from .autonomy import PlanRecord as _PlanRecord, active_plan

    plan = active_plan(database, objective_id)
    if plan is not None:
        updated_steps = []
        for step in plan.steps:
            required = tuple(dict.fromkeys(
                (*step.required_capabilities, *missing_capability_ids)))
            if step.status == "RUNNING" or step.mission_id == mission_id:
                updated_steps.append(replace(
                    step, status="FAILED", mission_id=None,
                    required_capabilities=required,
                ))
            elif required != step.required_capabilities:
                updated_steps.append(replace(step, required_capabilities=required))
            else:
                updated_steps.append(step)
        if tuple(updated_steps) != plan.steps:
            updated = _PlanRecord(
                plan_id=plan.plan_id, objective_id=plan.objective_id,
                steps=tuple(updated_steps), critical_path=plan.critical_path,
                status=plan.status, version=plan.version + 1,
            )
            database.append(_Event.create(
                "executive.plan_updated",
                updated.to_payload(),
                source_type="supervisor",
                source_id=source_id,
                idempotency_key=f"executive.plan_updated:{plan.plan_id}:{updated.version}",
                correlation_id=objective_id,
            ))
    database.append(_Event.create(
        "mission.state_changed",
        {
            "mission_id": mission_id,
            "state": "BLOCKED",
            "next_action": next_action,
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"mission.state_changed:{mission_id}:BLOCKED:intent-unrealizable",
    ))


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
    # Goal section 3: the Fabric capabilities the selected work item DECLARED
    # as required.  ``generate_missions_for_objective`` wrote them from the plan
    # step BEFORE this mission existed, so they are the earliest authoritative
    # source on the normal path.  Only Catalog-proven ``cap.*`` ids qualify.
    item_metadata = dict(item.get("metadata") or {})
    declared_capabilities = _declared_fabric_capabilities(database, item_metadata)
    declared_objective_id = str(item_metadata.get("executive_objective_id") or "")

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
        from ..mission.contracts import (
            MissionStepContractError,
            next_executable_contract,
        )
        mission = database.row("missions", "mission_id", mission_id)
        if mission is None:
            return {"materialized": False, "mission_id": None,
                    "blocked_reason": f"{kind} target mission is unknown"}
        try:
            executable = next_executable_contract(database, mission_id)
        except MissionStepContractError:
            # A BLOCKED/terminal mission refuses contracts outright; that is
            # exactly the unrepairable case this branch retires.
            executable = None
        blocked_state = str(mission["state"] or "") == "BLOCKED"
        if executable is None or blocked_state:
            # A BLOCKED mission can never be dispatched again under current
            # machinery (the loop refuses non-dispatchable states), so a
            # REPAIR selection would only produce endless blocked cycles
            # (live incident 2026-08-24). Retire it durably so the selector
            # stops ranking it; the ledger keeps the full history.
            _retire_unexecutable(
                database, mission_id=mission_id, item_id=item_id,
                kind=kind, decision_id=decision_id, source_id=source_id)
            return {"materialized": False, "mission_id": None,
                    "blocked_reason": (
                        f"{kind} target {mission_id} cannot be dispatched "
                        f"(state {mission['state'] or 'unknown'}); retired")}
            # A repairable mission without any remaining executable contract
            # can only produce an endless blocked-cycle (live incident
            # 2026-08-24). Retire it durably so the selector stops ranking
            # it; the ledger keeps the full history.
            database.append(Event.create(
                "mission.state_changed",
                {
                    "mission_id": mission_id,
                    "state": "CANCELLED",
                    "next_action": (
                        f"retired by portfolio: no executable step contract "
                        f"remains ({kind} selection)"),
                    "reason": (
                        "bounded portfolio policy: repair without an "
                        "executable contract cannot make progress"),
                },
                source_type="supervisor",
                source_id=source_id,
                idempotency_key=(
                    f"mission.state_changed:{mission_id}:CANCELLED:"
                    f"portfolio-unexecutable"),
            ))
            database.append(Event.create(
                "portfolio.repair_retired",
                {
                    "decision_id": decision_id,
                    "item_id": item_id,
                    "kind": kind,
                    "mission_id": mission_id,
                    "reason": "no remaining executable step contract",
                },
                source_type="supervisor",
                source_id=source_id,
                idempotency_key=(
                    f"portfolio.repair_retired:{decision_id}:{mission_id}"),
            ))
            return {"materialized": False, "mission_id": None,
                    "blocked_reason": (
                        f"{kind} target {mission_id} has no executable "
                        f"contract; retired")}
        return {"materialized": True, "mission_id": mission_id,
                "blocked_reason": None}

    # NEW_PROJECT greenfield seam (WP4): no pre-existing resource required.
    # This is the ONLY path that may create a project resource; every other
    # class reuses an existing resource_id supplied by the caller.
    if kind == "NEW_PROJECT":
        from ..executive.new_project_factory import (
            derive_slug_from_item,
            ensure_new_project_resource,
            new_project_mission_contract,
        )

        # Mirror the work item first so outcome lifecycle can retire it.
        from ..evolution.executive_portfolio import register_work_item

        try:
            register_work_item(
                database,
                item_id=item_id,
                kind=kind,
                project_id=str(item.get("project_id") or derive_slug_from_item(item)) or None,
                objective=objective,
                expected_value=item.get("expected_value"),
                urgency=item.get("urgency"),
                confidence=item.get("confidence"),
                reversibility=item.get("reversibility"),
                requires_verifier=bool(item.get("requires_verifier", True)),
                metadata=dict(item.get("metadata") or {}),
                source_type="supervisor",
                source_id=source_id,
            )
        except EventConflict:
            pass
        except Exception as error:  # noqa: BLE001
            database.append(Event.create(
                "portfolio.work_item_mirror_blocked",
                {"item_id": item_id, "reason": str(error)[:200]},
                source_type="supervisor",
                source_id=source_id,
                idempotency_key=f"portfolio.work_item_mirror_blocked:{item_id}:{decision_id}",
            ))

        # Idempotency: a prior materialization for this decision reuses the same
        # mission so retries do not create duplicate greenfield repos.
        prior = _materialization_for_decision(database, decision_id)
        if prior is not None:
            return {"materialized": True, "mission_id": prior, "blocked_reason": None}

        # Ensure the greenfield resource exists (bounded creation). For tests
        # the database lives under a disposable tmp_path, so the greenfield root
        # is automatically isolated. No second orchestrator is introduced.
        try:
            greenfield_override = item.get("metadata", {}).get("greenfield_root_override")
            ensured = ensure_new_project_resource(
                database, item, decision_id, source_id=source_id,
                greenfield_root_override=greenfield_override,
            )
        except Exception as error:  # noqa: BLE001 — fail closed, honest state
            return {"materialized": False, "mission_id": None, "blocked_reason": str(error)}
        resource_id = ensured
        slug = derive_slug_from_item(item)

        mission_id = f"mission-{uuid.uuid4().hex[:12]}"
        acceptance = [
            f"{objective} completed within declared capacity/budget",
            "independent verification PASS or honest BLOCKED with evidence",
            f"greenfield project {slug} exists at resource {resource_id}",
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
                {"mission_id": mission_id, "state": state, "next_action": next_action},
                source_type="supervisor",
                source_id=source_id,
                idempotency_key=f"mission.state_changed:{mission_id}:{state}:portfolio",
            ))

        contract = new_project_mission_contract(
            mission_id=mission_id,
            resource_id=resource_id,
            objective=objective,
            slug=slug,
            item=item,
            decision_id=decision_id,
            capability=capability,
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
                "resource_id": resource_id,
                "slug": slug,
            },
            source_type="supervisor",
            source_id=source_id,
            idempotency_key=f"portfolio.selection_materialized:{decision_id}",
        ))
        return {"materialized": True, "mission_id": mission_id, "blocked_reason": None}

    if kind not in ("MAINTENANCE", "RESEARCH", "UNKNOWN", "IMPROVEMENT",
                    "INCIDENT", "NEW_MISSION", "OWNER_GOAL"):
        return {"materialized": False, "mission_id": None,
                "blocked_reason": f"unsupported portfolio class {kind!r}"}

    # Derived sources (improvements, research, incidents) rebuild their
    # work items from foreign tables on every selection. Without a durable
    # mirror, a terminal outcome cannot retire them and the same item is
    # re-admitted forever (live busywork incident 2026-08-24).
    from ..evolution.executive_portfolio import register_work_item
    try:
        register_work_item(
            database,
            item_id=item_id,
            kind=kind,
            project_id=str(item.get("project_id") or "") or None,
            objective=objective,
            expected_value=item.get("expected_value"),
            urgency=item.get("urgency"),
            confidence=item.get("confidence"),
            reversibility=item.get("reversibility"),
            requires_verifier=bool(item.get("requires_verifier", True)),
            metadata=dict(item.get("metadata") or {}),
            source_type="supervisor",
            source_id=source_id,
        )
    except EventConflict:
        pass
    except Exception as error:  # noqa: BLE001 - mirror failure must not block selection
        database.append(Event.create(
            "portfolio.work_item_mirror_blocked",
            {"item_id": item_id, "reason": str(error)[:200]},
            source_type="supervisor",
            source_id=source_id,
            idempotency_key=f"portfolio.work_item_mirror_blocked:{item_id}:{decision_id}",
        ))

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

    # Authenticated owner goals need an actual plan followed by execution.
    # Both steps stay under the canonical MissionLoop and ordered immutable
    # contracts; no parallel mission engine or manual continuation is added.
    if kind == "OWNER_GOAL":
        metadata = item_metadata
        recipe_id = str(metadata.get("recipe_id") or "").strip()
        provenance = {
            "decision_id": decision_id,
            "portfolio_item_id": item_id,
            "portfolio_class": kind,
            "goal_id": metadata.get("goal_id"),
        }
        if not recipe_id and isinstance(metadata.get("intent"), Mapping):
            # Normal path: the owner goal carries a normalized intent (needs +
            # supplied inputs).  The runtime resolves the workflow/action plan
            # itself; no recipe id and no recipe-shaped parameter block is
            # required from the caller.
            intent, intent_error = _owner_goal_intent(
                mission_id=mission_id,
                resource_id=resource_id,
                objective=objective,
                metadata=metadata,
            )
            if intent is None:
                database.append(Event.create(
                    "portfolio.intent_unresolved",
                    {
                        "decision_id": decision_id,
                        "item_id": item_id,
                        "mission_id": mission_id,
                        "reason": intent_error,
                    },
                    source_type="supervisor",
                    source_id=source_id,
                    idempotency_key=f"portfolio.intent_unresolved:{decision_id}",
                ))
                return {"materialized": False, "mission_id": None,
                        "blocked_reason": intent_error}
            resolution = resolve_mission_intent(database, intent)
            provenance = {
                **provenance,
                "intent_id": intent.intent_id,
                "intent_needs": list(intent.needs),
                "resolution": resolution.as_dict(),
            }
            if resolution.kind == INTENT_GAP:
                _record_unrealizable_intent(
                    database,
                    objective_id=str(metadata.get("executive_objective_id") or ""),
                    mission_id=mission_id,
                    missing_capability_ids=tuple(
                        f"cap.{action}" if not str(action).startswith("cap.") else str(action)
                        for action in (resolution.missing or ())
                    ),
                    next_action=(
                        "resume: " + resolution.reason
                        + " -- retry once the capability is registered"
                    ),
                    source_id=source_id,
                )
                database.append(Event.create(
                    "portfolio.intent_unresolved",
                    {
                        "decision_id": decision_id,
                        "item_id": item_id,
                        "mission_id": mission_id,
                        "intent_id": intent.intent_id,
                        "intent_needs": list(intent.needs),
                        "gap": dict(resolution.gap or {}),
                        "reason": resolution.reason,
                    },
                    source_type="supervisor",
                    source_id=source_id,
                    idempotency_key=f"portfolio.intent_unresolved:{decision_id}",
                ))
                return {"materialized": False, "mission_id": None,
                        "blocked_reason": resolution.reason}
            if resolution.kind == INTENT_BLOCKED:
                _record_unrealizable_intent(
                    database,
                    objective_id=str(metadata.get("executive_objective_id") or ""),
                    mission_id=mission_id,
                    missing_capability_ids=(),
                    next_action=(
                        "resume: " + resolution.reason
                        + " -- retry once the intent supplies those inputs"
                    ),
                    source_id=source_id,
                )
                database.append(Event.create(
                    "portfolio.intent_unresolved",
                    {
                        "decision_id": decision_id,
                        "item_id": item_id,
                        "mission_id": mission_id,
                        "intent_id": intent.intent_id,
                        "missing_inputs": list(resolution.missing),
                        "reason": resolution.reason,
                    },
                    source_type="supervisor",
                    source_id=source_id,
                    idempotency_key=f"portfolio.intent_unresolved:{decision_id}",
                ))
                return {"materialized": False, "mission_id": None,
                        "blocked_reason": resolution.reason}
            if resolution.kind == INTENT_ACTIONS:
                return _materialize_intent_action_plan(
                    database,
                    mission_id=mission_id,
                    resource_id=resource_id,
                    objective=objective,
                    decision_id=decision_id,
                    item_id=item_id,
                    kind=kind,
                    resolution=resolution,
                    provenance=provenance,
                    source_id=source_id,
                    required_capabilities=declared_capabilities,
                )
            recipe_id = str(resolution.recipe_id or "")
            metadata = {**metadata, "recipe_params": dict(resolution.params)}
            _bind_resolution_capabilities(
                database, resource_id, ("cap.workflow.run",),
                source_id=source_id, resolution=resolution,
            )
        if recipe_id:
            params = dict(metadata.get("recipe_params") or {})
            artifact = str(
                params.get("artifact_target")
                or params.get("target")
                or "research/owner-goal-artifact.md"
            ).strip() or "research/owner-goal-artifact.md"
            envelope = json.dumps(
                {
                    "recipe_id": recipe_id,
                    "params": params,
                    "mission_id": mission_id,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
            execute = MissionStepContract(
                mission_id=mission_id,
                step_id="01-workflow",
                version=1,
                resource_id=resource_id,
                capability="cap.workflow.run",
                target_relative_path=artifact,
                instructions=envelope,
                expected_effects=(
                    f"named recipe {recipe_id} completes through workflow.run",
                    f"{artifact} exists after independent verification",
                ),
                rollback={
                    "method": "revert registered-resource recipe effects",
                    "reversible": True,
                },
                risk_class="C1",
                verification_criteria=tuple(
                    str(value)
                    for value in metadata.get("success_criteria") or ()
                    if str(value).strip()
                ) or (
                    f"{artifact} exists",
                    "workflow.run nested receipts are independently verified",
                ),
                expected_artifacts=(artifact,),
                forbidden_paths=(),
                required_capabilities=declared_capabilities,
                recorded_by="supervisor",
                source="owner-goal-admission",
                provenance={**provenance, "recipe_id": recipe_id},
            )
            record_step_contract(database, execute)
            database.append(Event.create(
                "portfolio.selection_materialized",
                {
                    "decision_id": decision_id,
                    "item_id": item_id,
                    "kind": kind,
                    "mission_id": mission_id,
                    "contract_ids": [execute.contract_id],
                    "goal_id": metadata.get("goal_id"),
                    "recipe_id": recipe_id,
                },
                source_type="supervisor",
                source_id=source_id,
                idempotency_key=f"portfolio.selection_materialized:{decision_id}",
            ))
            return {"materialized": True, "mission_id": mission_id,
                    "blocked_reason": None}
        goal_token = hashlib.sha256(
            f"{decision_id}:{item_id}".encode("utf-8")).hexdigest()[:10]
        plan_name = f".jarvis/owner-goals/{goal_token}/plan.md"
        outcome_name = f".jarvis/owner-goals/{goal_token}/outcome.md"
        success = tuple(
            str(value) for value in metadata.get("success_criteria") or ()
            if str(value).strip())
        objective_criteria = success or (
            f"the registered resource satisfies the owner objective: {objective}",)
        plan = MissionStepContract(
            mission_id=mission_id,
            step_id="01-plan",
            version=1,
            resource_id=resource_id,
            capability=capability,
            target_relative_path=plan_name,
            instructions=(
                f"Orient to the registered resource and the authenticated owner goal below.\n"
                f"OWNER GOAL: {objective}\n"
                f"Create {plan_name} with a concise executable plan, the current-state "
                "observations that justify it, explicit verification criteria, risks, "
                "rollback, and the next useful action. Do not claim the goal is complete."
            ),
            expected_effects=(f"{plan_name} contains an evidence-aware execution plan",),
            rollback={"method": "delete_file", "reversible": True},
            risk_class="C1",
            verification_criteria=(
                f"{plan_name} exists",
                "the plan is specific to the owner goal and registered resource",
                "the plan includes verification and rollback",
            ),
            expected_artifacts=(plan_name,),
            forbidden_paths=(),
            required_capabilities=declared_capabilities,
            recorded_by="supervisor",
            source="owner-goal-admission",
            provenance=provenance,
        )
        execute = MissionStepContract(
            mission_id=mission_id,
            step_id="02-execute",
            version=1,
            resource_id=resource_id,
            capability=capability,
            target_relative_path=outcome_name,
            instructions=(
                f"Read {plan_name}, then perform the authenticated owner goal in the "
                f"registered resource: {objective}\n"
                "Do the real bounded work; do not merely describe it. Run relevant "
                "checks. Finally create " + outcome_name + " containing the changes, "
                "commands/checks and observed results, remaining uncertainty, rollback, "
                "and whether each owner success criterion is satisfied."
            ),
            expected_effects=(
                f"the registered resource is changed to satisfy: {objective}",
                f"{outcome_name} records actual checks and observed results",
            ),
            rollback={
                "method": "revert registered-resource changes and delete owner-goal artifacts",
                "reversible": True,
            },
            risk_class="C1",
            verification_criteria=tuple(objective_criteria) + (
                f"{outcome_name} exists and its claims agree with direct inspection",
            ),
            expected_artifacts=(outcome_name,),
            forbidden_paths=(),
            required_capabilities=declared_capabilities,
            recorded_by="supervisor",
            source="owner-goal-admission",
            provenance=provenance,
        )
        # The declared plan-step requirements are bound onto the mission's
        # resource through the existing capability-state seam, so the
        # requirement the gap/resume machinery owns is selectable by the
        # mission that executes this step - not merely recorded beside it.
        _bind_resolution_capabilities(
            database, resource_id, declared_capabilities,
            source_id=source_id, objective_id=declared_objective_id,
        )
        record_step_contract(database, plan)
        record_step_contract(database, execute)
        database.append(Event.create(
            "portfolio.selection_materialized",
            {
                "decision_id": decision_id,
                "item_id": item_id,
                "kind": kind,
                "mission_id": mission_id,
                "contract_ids": [plan.contract_id, execute.contract_id],
                "goal_id": metadata.get("goal_id"),
            },
            source_type="supervisor",
            source_id=source_id,
            idempotency_key=f"portfolio.selection_materialized:{decision_id}",
        ))
        return {"materialized": True, "mission_id": mission_id,
                "blocked_reason": None}

    # Unique artifact per decision: a shared name plus deterministic
    # content would collapse every mission's evidence delta to empty
    # (live incident 2026-08-24: every selection targeted the same
    # report-portfoli.md and exhausted its repair budget on a false
    # evidence_mismatch).
    artifact_suffix = hashlib.sha256(
        f"{decision_id}:{item_id}".encode("utf-8")).hexdigest()[:10]
    artifact_name = f"report-portfolio-{artifact_suffix}.md"
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
        required_capabilities=declared_capabilities,
        recorded_by="supervisor",
        source="portfolio",
        provenance={
            "decision_id": decision_id,
            "portfolio_item_id": item_id,
            "portfolio_class": kind,
            "incident_id": item_metadata.get("incident_id"),
        },
    )
    _bind_resolution_capabilities(
        database, resource_id, declared_capabilities,
        source_id=source_id, objective_id=declared_objective_id,
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
    if not readiness["ready"] and readiness["state"] in _BLOCKING_CAPABILITY_STATES:
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
