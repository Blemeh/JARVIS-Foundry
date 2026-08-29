"""Post-V7 account-to-procedure-to-resilience composition.

This module is an integration adapter, not a new authority.  Account
activation remains the source of account truth, the capability plane remains
the source of execution readiness, the Supervisor portfolio remains the
selector, V7B operations remain the procedure lifecycle, V5 remains the
effect/broker authority, and V7C remains the incident lifecycle.
"""
from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from ..connectors.account_activation import (
    ActivationReadiness,
    AuthState,
    activation_state,
)
from ..control.capabilities import set_capability_state
from ..control.registration import registration_event
from ..database import FoundryDatabase
from ..events import Event, EventConflict, canonical_json
from ..evolution.executive_portfolio import (
    PortfolioDecision,
    record_portfolio_outcome,
    register_work_item,
    select_next_work,
)
from ..executive.portfolio_bridge import (
    capability_readiness,
    gate_procedure_step_capability,
    plan_broad_operation,
)
from ..operations.procedure import (
    OperationError,
    OperationStep,
    ProcedurePlan,
    ProcedureState,
    StepStatus,
    complete_step,
    fail_step,
    freeze_step,
    load_procedure,
    start_step,
)
from ..procedure_library.contracts import ProcedureDefinition, ProofStatus
from ..procedure_library.validator import ProcedureCatalog
from ..resilience.core import (
    EffectAmbiguity,
    HealthSignal,
    IncidentStore,
)
from ..supervisor.authority import SUPERVISOR_CONTROLLER


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()[:20]


def _append_once(
    database: FoundryDatabase,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    source_id: str = "postv7-integration",
) -> None:
    try:
        database.append(Event.create(
            event_type,
            dict(payload),
            source_type="supervisor",
            source_id=source_id,
            idempotency_key=f"{event_type}:{key}",
        ))
    except EventConflict:
        # Re-delivery of an identical integration receipt is expected after a
        # restart; the ledger remains the authority and no second projection
        # is created.
        return


def _required_capabilities(definition: ProcedureDefinition) -> tuple[str, ...]:
    values = set(str(item) for item in definition.capabilities)
    for step in definition.steps:
        values.update(str(item) for item in step.capabilities)
    return tuple(sorted(values))


def _account_resource(record: Any) -> str:
    return f"account:{record.provider}:{record.account_identity}"


def _safe_scopes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(sorted({str(item) for item in value if str(item).strip()}))


@dataclass(frozen=True, slots=True)
class ProcedureEligibility:
    """A durable-readiness receipt for one catalog definition."""

    eligible: bool
    procedure_id: str
    version: str
    proof_status: str
    resource_id: str
    activation_id: str | None
    capabilities: tuple[str, ...]
    blocked_reasons: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "ELIGIBLE" if self.eligible else "BLOCKED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "status": self.status,
            "procedure_id": self.procedure_id,
            "version": self.version,
            "proof_status": self.proof_status,
            "resource_id": self.resource_id,
            "activation_id": self.activation_id,
            "capabilities": list(self.capabilities),
            "blocked_reasons": list(self.blocked_reasons),
        }


def project_account_capabilities(
    database: FoundryDatabase,
    activation_id: str,
    capability_map: Mapping[str, Iterable[str]] | None = None,
    *,
    resource_id: str | None = None,
    source_id: str = "postv7-account-capability-projector",
) -> dict[str, Any]:
    """Project account activation readiness onto the canonical capability plane.

    The account event stream remains authoritative for identity and auth.  The
    projection deliberately uses ``DEGRADED`` until both the credential and
    provider probe are READY, and uses ``VETOED`` after revocation.  No token or
    credential material is copied into the capability evidence.
    """
    record = activation_state(database, activation_id)
    if record is None:
        raise ValueError(f"activation {activation_id!r} is not registered")
    resolved_resource = resource_id or _account_resource(record)
    mapping = dict(capability_map or {})
    if not mapping:
        mapping = {str(key): () for key in record.capabilities}
    if not mapping:
        mapping = {str(key): () for key in record.requested_scopes}
    # The capability projection is allowed to create/update the canonical
    # resource registration, but it still does so through the existing
    # ``project.registered`` authority.  Without this registration the
    # capability projection correctly fails closed as an unknown resource.
    registration_payload = {
        "resource_id": resolved_resource,
        "kind": "account_connector",
        "root_path": f"account://{record.provider}/{record.account_identity}",
        "resolved_root": f"account://{record.provider}/{record.account_identity}",
        "baseline": {"activation_id": record.activation_id, "provider": record.provider},
        "capabilities": {str(key): "PROBING" for key in mapping},
        "readiness_state": "PROBING",
    }
    try:
        database.append(registration_event(registration_payload, source_id=source_id))
    except EventConflict:
        pass
    revoked = str(record.revocation_state).upper() == "REVOKED" or str(record.auth_state) == AuthState.REVOKED
    if revoked:
        projected_state = "VETOED"
    elif record.activation_eligible and record.enabled:
        projected_state = "ACTIVE"
    elif record.activation_eligible:
        projected_state = "READY"
    else:
        projected_state = "DEGRADED"

    projected: list[dict[str, Any]] = []
    for capability, required in sorted(mapping.items()):
        required_scopes = _safe_scopes(required)
        observed = set(record.observed_scopes)
        provider_state = str(record.capabilities.get(capability) or "")
        state = projected_state
        if required_scopes and not set(required_scopes).issubset(observed):
            state = "VETOED" if revoked else "DEGRADED"
        if provider_state and provider_state not in {"READY", "ACTIVE"}:
            state = "VETOED" if revoked else "DEGRADED"
        evidence = {
            "activation_id": record.activation_id,
            "provider": record.provider,
            "account_identity": record.account_identity,
            "activation_readiness": str(record.readiness),
            "auth_state": str(record.auth_state),
            "enabled": bool(record.enabled),
            "revocation_state": str(record.revocation_state),
            "required_scopes": list(required_scopes),
            "observed_scopes": list(record.observed_scopes),
            "provider_capability_state": provider_state or None,
        }
        latest = capability_readiness(
            database, resource_id=resolved_resource, capability=str(capability)
        )
        if latest.get("state") != state:
            set_capability_state(
                database,
                resolved_resource,
                str(capability),
                state,
                source_id=source_id,
                evidence=evidence,
            )
        _append_once(
            database,
            "account.activation_capability_projected",
            {
                "activation_id": record.activation_id,
                "resource_id": resolved_resource,
                "capability": str(capability),
                "state": state,
                "evidence": evidence,
            },
            key=f"{record.activation_id}:{resolved_resource}:{capability}:{state}",
            source_id=source_id,
        )
        projected.append({"capability": str(capability), "state": state, "required_scopes": list(required_scopes)})
    return {
        "activation_id": record.activation_id,
        "resource_id": resolved_resource,
        "provider": record.provider,
        "account_identity": record.account_identity,
        "activation_readiness": str(record.readiness),
        "auth_state": str(record.auth_state),
        "enabled": bool(record.enabled),
        "live_status": "READY" if record.activation_eligible and record.enabled else "UNCONFIGURED" if str(record.readiness) == ActivationReadiness.UNCONFIGURED else "BLOCKED",
        "projected": projected,
    }


def procedure_eligibility(
    database: FoundryDatabase,
    definition: ProcedureDefinition,
    resource_id: str,
    *,
    activation_id: str | None = None,
    require_projected_account: bool = True,
    source_id: str = "postv7-procedure-eligibility",
) -> ProcedureEligibility:
    """Evaluate a catalog definition against canonical auth/capability truth."""
    capabilities = _required_capabilities(definition)
    blocked: list[str] = []
    allowed_proof = {ProofStatus.PROVEN_ONCE.value, ProofStatus.TRUSTED.value}
    if str(definition.proof_status) not in allowed_proof:
        blocked.append(f"proof_status:{definition.proof_status}")
    resolved_resource = str(resource_id or "")
    record = None
    if activation_id:
        record = activation_state(database, activation_id)
        if record is None:
            blocked.append(f"activation_missing:{activation_id}")
        else:
            if not record.activation_eligible:
                blocked.append(f"activation_readiness:{record.readiness}/{record.auth_state}")
            if not record.enabled:
                blocked.append("activation_handoff_required")
            if not resolved_resource:
                resolved_resource = _account_resource(record)
    if not resolved_resource:
        blocked.append("resource_id_required")
    for capability in capabilities:
        readiness = capability_readiness(
            database, resource_id=resolved_resource or "unresolved", capability=capability
        )
        state = str(readiness.get("state") or "")
        if activation_id and require_projected_account and state in {"UNREGISTERED", "UNKNOWN"}:
            blocked.append(f"capability:{capability}:account_projection_missing")
        elif not readiness.get("ready"):
            blocked.append(f"capability:{capability}:{readiness.get('reason') or state}")
    receipt = ProcedureEligibility(
        eligible=not blocked,
        procedure_id=definition.procedure_id,
        version=definition.version,
        proof_status=str(definition.proof_status),
        resource_id=resolved_resource,
        activation_id=activation_id,
        capabilities=capabilities,
        blocked_reasons=tuple(sorted(set(blocked))),
    )
    _append_once(
        database,
        "procedure.library.eligibility_checked",
        receipt.to_dict(),
        key=f"{definition.procedure_id}:{definition.version}:{resolved_resource}:{activation_id or 'local'}:{_hash(receipt.to_dict())}",
        source_id=source_id,
    )
    return receipt


def register_eligible_procedure_work(
    database: FoundryDatabase,
    catalog: ProcedureCatalog,
    *,
    resource_id: str,
    activation_id: str | None = None,
    domain: str | None = None,
    statuses: Iterable[str] = (ProofStatus.PROVEN_ONCE.value, ProofStatus.TRUSTED.value),
    source_id: str = "postv7-procedure-portfolio",
) -> dict[str, Any]:
    """Expose only eligible catalog entries to the V7A work queue."""
    selected = catalog.select(domain=domain, statuses=statuses)
    registered: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for definition in selected:
        receipt = procedure_eligibility(
            database,
            definition,
            resource_id,
            activation_id=activation_id,
            source_id=source_id,
        )
        if not receipt.eligible:
            blocked.append(receipt.to_dict())
            continue
        item_id = f"procedure:{definition.procedure_id}:{definition.version}:{receipt.resource_id}"
        kind = "RESEARCH" if definition.domain == "research" else "MAINTENANCE"
        payload = {
            "procedure_id": definition.procedure_id,
            "version": definition.version,
            "proof_status": str(definition.proof_status),
            "domain": definition.domain,
            "activation_id": activation_id,
            "resource_id": receipt.resource_id,
            "capabilities": list(receipt.capabilities),
        }
        try:
            register_work_item(
                database,
                item_id=item_id,
                kind=kind,
                objective=f"Run verified procedure {definition.name} ({definition.version})",
                resource_id=receipt.resource_id,
                expected_value=0.65,
                urgency=0.45,
                information_gain=0.55,
                confidence=0.75 if str(definition.proof_status) == ProofStatus.TRUSTED.value else 0.55,
                effort=0.35,
                risk=0.2,
                reversibility=1.0 if definition.reversibility == "reversible" else 0.5,
                requires_verifier=True,
                evidence_refs=tuple(str(item) for item in definition.evidence_outputs),
                provenance={"source": "postv7-procedure-library", "catalog": "immutable-by-replacement"},
                metadata=payload,
                source_id=source_id,
            )
        except EventConflict:
            pass
        registered.append({**payload, "item_id": item_id, "kind": kind})
    return {"registered": registered, "blocked": blocked, "selected_count": len(selected)}


def select_library_work(
    database: FoundryDatabase,
    catalog: ProcedureCatalog,
    *,
    loop_id: str,
    resource_id: str,
    activation_id: str | None = None,
    domain: str | None = None,
    source_id: str = "postv7-procedure-portfolio",
) -> dict[str, Any]:
    registration = register_eligible_procedure_work(
        database,
        catalog,
        resource_id=resource_id,
        activation_id=activation_id,
        domain=domain,
        source_id=source_id,
    )
    decision = select_next_work(
        database,
        loop_id=loop_id,
        resource_id=resource_id,
        capability="procedure.select",
        source_id=source_id,
    )
    selected_registration = next(
        (item for item in registration["registered"]
         if item.get("item_id") == decision.entity_id),
        None,
    )
    if decision.kind == "SELECT" and selected_registration:
        _append_once(
            database,
            "procedure.library.portfolio_selected",
            {
                "loop_id": loop_id,
                "decision_id": decision.decision_id,
                "entity_id": decision.entity_id,
                "procedure_id": selected_registration["procedure_id"],
                "version": selected_registration.get("version"),
            },
            key=f"{decision.decision_id}",
            source_id=source_id,
        )
    return {"registration": registration, "decision": decision}


def plan_library_procedure(
    database: FoundryDatabase,
    definition: ProcedureDefinition,
    *,
    mission_id: str,
    target: str,
    resource_id: str,
    activation_id: str | None = None,
    evidence_refs: Iterable[str] = (),
    source_id: str = "postv7-procedure-binding",
) -> ProcedurePlan:
    receipt = procedure_eligibility(
        database,
        definition,
        resource_id,
        activation_id=activation_id,
        source_id=source_id,
    )
    if not receipt.eligible:
        raise OperationError(
            f"procedure {definition.procedure_id} is not eligible: "
            + "; ".join(receipt.blocked_reasons)
        )
    plan_id = f"procedure:{definition.procedure_id}:{definition.version}:{mission_id}"
    shared_refs = tuple(str(item) for item in evidence_refs)
    steps: list[OperationStep] = []
    for step in definition.steps:
        refs = tuple(dict.fromkeys((*shared_refs, *step.evidence_outputs)))
        reversible = "reversible" if step.reversibility == "mixed" else step.reversibility
        steps.append(OperationStep(
            step_id=step.step_id,
            action_kind=step.action,
            target=target,
            preconditions=step.preconditions,
            capabilities=step.capabilities,
            risk_class=step.risk_class,
            reversibility=reversible,
            postconditions=step.postconditions,
            rollback={"method": step.recovery},
            effect_ticket=f"postv7:{definition.procedure_id}:{definition.version}:{mission_id}:{step.step_id}",
            evidence_refs=refs,
        ))
    plan = plan_broad_operation(
        database,
        procedure_id=plan_id,
        mission_id=mission_id,
        target=target,
        steps=steps,
        capabilities=receipt.capabilities,
        preconditions=definition.preconditions,
        evidence_refs=shared_refs,
        metadata={
            "library_procedure_id": definition.procedure_id,
            "library_version": definition.version,
            "proof_status": str(definition.proof_status),
            "resource_id": receipt.resource_id,
            "activation_id": activation_id,
            "oracle_id": definition.oracle.oracle_id if definition.oracle else None,
        },
        source_id=source_id,
    )
    _append_once(
        database,
        "procedure.library.bound",
        {
            "procedure_id": plan.procedure_id,
            "mission_id": mission_id,
            "library_procedure_id": definition.procedure_id,
            "library_version": definition.version,
            "resource_id": receipt.resource_id,
            "activation_id": activation_id,
            "plan_hash": plan.plan_hash,
        },
        key=plan.procedure_id,
        source_id=source_id,
    )
    return plan


def _call_callback(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a small test/effect/verifier adapter without hiding failures."""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback(*args)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return callback(*args, **kwargs)
    positional = [param for param in signature.parameters.values()
                  if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}]
    return callback(*args[:len(positional)])


def _verifier_result(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        result = dict(value)
    else:
        result = {"verdict": str(value)}
    verdict = str(result.get("verdict") or result.get("status") or "NOT_TESTED").upper()
    if verdict in {"PASS", "PASSED", "COMPLETED", "SUCCESS"}:
        verdict = "PASS"
    elif verdict in {"FAIL", "FAILED", "REJECTED"}:
        verdict = "FAIL"
    elif verdict in {"BLOCKED", "UNKNOWN", "FROZEN"}:
        verdict = "BLOCKED"
    else:
        verdict = "NOT_TESTED"
    result["verdict"] = verdict
    result.setdefault("verifier_id", "independent-verifier-missing")
    return result


def execute_library_step(
    database: FoundryDatabase,
    *,
    plan: ProcedurePlan | str,
    step_id: str,
    resource_id: str,
    verifier: Callable[..., Any] | None,
    before_state: Mapping[str, Any],
    evidence_refs: Iterable[str],
    effect_executor: Callable[..., Any] | None = None,
    procedure_capability: str | None = None,
    source_id: str = "postv7-procedure-execution",
) -> dict[str, Any]:
    """Run one bounded lifecycle transition through V7B/V5/V7C seams.

    ``effect_executor`` is intentionally injected for a disposable local
    proof.  A production caller must make it call V5 ``execute_effect`` with
    an ActionEnvelope and broker ALLOW receipt; this adapter never issues a
    capability or writes provider state directly.
    """
    procedure_id = plan.procedure_id if isinstance(plan, ProcedurePlan) else str(plan)
    state = load_procedure(database, procedure_id)
    spec = next((item for item in state.plan.steps if item.step_id == step_id), None)
    if spec is None:
        raise KeyError(step_id)
    refs = tuple(str(item) for item in evidence_refs)
    capabilities = spec.capabilities or ((procedure_capability,) if procedure_capability else ())
    gate_receipts = []
    for index, capability in enumerate(capabilities):
        gate_receipts.append(gate_procedure_step_capability(
            database,
            resource_id=resource_id,
            capability=str(capability),
            procedure_id=procedure_id,
            step_id=step_id,
            effect_ticket=f"{spec.effect_ticket}:{index}",
            source_id=source_id,
        ))
    state = start_step(
        database,
        procedure_id,
        step_id,
        before_state=before_state,
        evidence_refs=refs,
        effect_ticket=spec.effect_ticket,
        source_id=source_id,
    )
    if effect_executor is None:
        effect_result: dict[str, Any] = {"state": "NOT_EXECUTED", "effect_ticket": spec.effect_ticket}
    else:
        value = _call_callback(effect_executor, spec, state, effect_ticket=spec.effect_ticket)
        effect_result = dict(value) if isinstance(value, Mapping) else {"state": str(value)}
    effect_state = str(effect_result.get("state") or effect_result.get("status") or "NOT_EXECUTED").upper()
    if verifier is None:
        verification = {"verdict": "NOT_TESTED", "verifier_id": "independent-verifier-missing"}
    else:
        verification = _verifier_result(_call_callback(verifier, effect_result, state, step=spec))
    _append_once(
        database,
        "procedure.library.verification_recorded",
        {
            "procedure_id": procedure_id,
            "step_id": step_id,
            "effect_ticket": spec.effect_ticket,
            "effect_state": effect_state,
            "verification": verification,
        },
        key=f"{procedure_id}:{step_id}:{spec.effect_ticket}:{verification['verdict']}",
        source_id=source_id,
    )
    incidents: list[dict[str, Any]] = []
    if verification["verdict"] == "PASS" and effect_state in {"COMPLETED", "SUCCESS", "PASS"}:
        after = effect_result.get("after_state") or {"effect_ticket": spec.effect_ticket, "state": effect_state}
        final_state = complete_step(
            database,
            procedure_id,
            step_id,
            after_state=after,
            evidence_refs=refs or (f"verification:{procedure_id}:{step_id}",),
            postcondition_results=effect_result.get("postcondition_results") or {item: True for item in spec.postconditions},
            effect_ticket=spec.effect_ticket,
            source_id=source_id,
        )
        outcome = "COMPLETED"
    elif effect_state in {"UNKNOWN", "FROZEN", "TIMEOUT", "AMBIGUOUS"}:
        final_state = freeze_step(
            database,
            procedure_id,
            step_id,
            reason=str(effect_result.get("reason") or "external effect truth is ambiguous"),
            evidence_refs=refs or (f"effect:{spec.effect_ticket}",),
            effect_ticket=spec.effect_ticket,
            source_id=source_id,
        )
        incident = IncidentStore(database, source_id=source_id).detect(HealthSignal(
            signal_id=f"procedure-effect:{procedure_id}:{step_id}:{spec.effect_ticket}",
            kind="EFFECT_UNKNOWN",
            correlation_key=f"procedure:{procedure_id}:{step_id}",
            resource_id=resource_id,
            capability=capabilities[0] if capabilities else None,
            mission_id=state.plan.mission_id,
            effect_action_key=spec.effect_ticket,
            effect_ambiguity=EffectAmbiguity.UNKNOWN.value,
            evidence={"procedure_id": procedure_id, "step_id": step_id, "verification": verification},
            recovery_criteria=("provider reconciliation", "independent verification"),
        ))
        incidents.append(incident.to_payload())
        outcome = "FROZEN"
    elif verification["verdict"] == "FAIL" or effect_state in {"FAILED", "ERROR"}:
        final_state = fail_step(
            database,
            procedure_id,
            step_id,
            reason=str(effect_result.get("reason") or "independent verifier rejected effect"),
            evidence_refs=refs or (f"verification:{procedure_id}:{step_id}",),
            after_state=effect_result.get("after_state") or {},
            terminal=True,
            effect_ticket=spec.effect_ticket,
            source_id=source_id,
        )
        outcome = "FAILED"
    else:
        final_state = freeze_step(
            database,
            procedure_id,
            step_id,
            reason="effect or independent verification was not proven",
            evidence_refs=refs or (f"verification:{procedure_id}:{step_id}",),
            effect_ticket=spec.effect_ticket,
            source_id=source_id,
        )
        outcome = "NOT_TESTED"
    maturity = {
        "procedure_id": procedure_id,
        "step_id": step_id,
        "library_proof_status": state.plan.metadata.get("proof_status", ProofStatus.PROVEN_ONCE.value),
        "maturity_status": "PROVEN_ONCE",
        "verifier_passed": verification["verdict"] == "PASS",
        "evidence_refs": list(refs),
        "independent_verifier_id": verification.get("verifier_id"),
    }
    _append_once(database, "procedure.library.outcome_recorded", {
        "procedure_id": procedure_id,
        "step_id": step_id,
        "outcome": outcome,
        "effect_state": effect_state,
        "verification": verification,
        "incident_ids": [item["incident_id"] for item in incidents],
    }, key=f"{procedure_id}:{step_id}:{outcome}", source_id=source_id)
    _append_once(database, "procedure.library.maturity_evidence_recorded", maturity,
                 key=f"{procedure_id}:{step_id}:{outcome}:{verification.get('verifier_id')}", source_id=source_id)
    return {
        "procedure_id": procedure_id,
        "step_id": step_id,
        "outcome": outcome,
        "effect": effect_result,
        "verification": verification,
        "gate_receipts": gate_receipts,
        "incidents": incidents,
        "procedure_state": final_state,
    }


def record_library_outcome(
    database: FoundryDatabase,
    *,
    decision: PortfolioDecision | Mapping[str, Any],
    mission_id: str,
    status: str,
    actual: Mapping[str, Any] | None = None,
    outcome_id: str | None = None,
    source_id: str = "postv7-procedure-portfolio",
) -> str:
    """Feed the terminal procedure truth back into V7A calibration."""
    payload = decision.to_payload() if isinstance(decision, PortfolioDecision) else dict(decision)
    item = dict(payload.get("selected_item") or {})
    item_id = str(item.get("item_id") or payload.get("entity_id") or "")
    return record_portfolio_outcome(
        database,
        outcome_id=outcome_id or f"procedure-outcome:{mission_id}",
        selection_id=str(payload.get("decision_id") or ""),
        entity_id=item_id,
        kind=str(payload.get("selected_class") or "MAINTENANCE"),
        predicted={
            "expected_value": item.get("expected_value"),
            "selected_score": payload.get("selected_score"),
            "uncertainty": payload.get("uncertainty"),
        },
        actual={"mission_id": mission_id, **dict(actual or {})},
        status=status,
        source_id=source_id,
    )


__all__ = [
    "ProcedureEligibility",
    "project_account_capabilities",
    "procedure_eligibility",
    "register_eligible_procedure_work",
    "select_library_work",
    "plan_library_procedure",
    "execute_library_step",
    "record_library_outcome",
]
