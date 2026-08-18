"""Governed procedure execution through the existing JARVIS pipeline."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..control.broker import BrokerDecision
from ..database import FoundryDatabase
from ..domain import ActionEnvelope
from ..effects import FROZEN, effect_identity, effect_state, reconcile_effect
from ..events import Event
from ..execution.pipeline import authorize, execute as execute_pipeline, learn
from ..connectors.registry import select_connector
from .adapter import OpenAdaptAdapter, check_openadapt
from .contracts import (
    ActionAuthorityError,
    AmbiguousEffect,
    ProcedureBundle,
    ReplayOutcome,
    canonical_json,
    payload_hash,
    validate_parameters,
)
from .registry import OPENADAPT_BROWSER_CONNECTOR, OPENADAPT_BROWSER_SYSTEM
from .tier_c import verify_tier_c_reference


@dataclass(frozen=True, slots=True)
class LocalStateOracle:
    """Independent oracle for the Phase-1 harmless synthetic app."""

    state_url: str

    def read(self) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(self.state_url, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return {"exists": False, "oracle_error": type(exc).__name__}
        return payload if isinstance(payload, dict) else {"exists": False}

    @staticmethod
    def normalized(state: dict[str, Any]) -> dict[str, Any]:
        previous = state.get("prev") if isinstance(state.get("prev"), dict) else None
        return {
            "count": int(state.get("count", 0) or 0),
            "last": {
                "task": str(previous.get("task", "")) if previous else "",
                "option": str(previous.get("option", "")) if previous else "",
            },
        }

    @classmethod
    def content_hash(cls, state: dict[str, Any]) -> str:
        return hashlib.sha256(canonical_json(cls.normalized(state)).encode("utf-8")).hexdigest()

    def before_capture(self) -> dict[str, Any]:
        state = self.read()
        return {"ref": self.state_url, "hash": self.content_hash(state),
                "normalized": self.normalized(state)}

    def expected_provider(self, before: dict[str, Any], *, task: str, option: str) -> dict[str, Any]:
        normalized_before = dict(before.get("normalized", {}))
        expected = {
            "count": int(normalized_before.get("count", 0)) + 1,
            "last": {"task": str(task), "option": str(option)},
        }
        return {"exists": True, "content_hash": hashlib.sha256(
            canonical_json(expected).encode("utf-8")).hexdigest(),
                "normalized": expected}

    def provider_state(self) -> dict[str, Any]:
        state = self.read()
        if "oracle_error" in state:
            return state
        return {"exists": bool(state.get("count", 0)),
                "content_hash": self.content_hash(state),
                "normalized": self.normalized(state)}


def _existing_request(database: FoundryDatabase, request_id: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'procedure.execution.requested' AND json_extract(payload_json, "
            "'$.request_id') = ? ORDER BY sequence LIMIT 1", (request_id,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def _action_envelope(
    bundle: ProcedureBundle,
    *,
    mission_id: str,
    run_id: str,
    request_id: str,
    before_capture: dict[str, Any],
    params: dict[str, str],
) -> ActionEnvelope:
    params_hash = payload_hash({"request_id": request_id, "params": dict(sorted(params.items()))})
    action_id = f"procedure:{bundle.procedure_id}:{request_id}"
    return ActionEnvelope(
        action_id=action_id,
        mission_id=mission_id,
        resource_id=OPENADAPT_BROWSER_SYSTEM,
        action_kind="openadapt.replay",
        capability="openadapt.browser",
        expected_effects=bundle.expected_effects or ("local synthetic record created",),
        expected_effect="local synthetic record created",
        risk_class=bundle.risk_class,
        verification_required=True,
        rollback={"method": "freeze"},
        step_id=f"{mission_id}:{bundle.procedure_id}",
        requested_by_agent_run_id=run_id,
        target_resources=(OPENADAPT_BROWSER_SYSTEM,),
        capabilities_required=("openadapt.browser",),
        goal_alignment=f"execute governed procedure {bundle.procedure_id}",
        active_owner_vetoes_checked=(),
        reversibility="reversible",
        before_state_capture=before_capture,
        verification_plan={
            "kind": "independent_system_of_record",
            "bundle_hash": bundle.bundle_hash,
            "params_hash": params_hash,
            "oracle": bundle.verification_plan,
        },
        rollback_plan={"method": "freeze", "steps": ("reconcile", "owner review")},
        idempotency_key=f"procedure-effect:{bundle.procedure_id}:{request_id}",
        budget_estimate={},
        credential_requirements=(),
    )


def _record_requested(database: FoundryDatabase, *, request_id: str,
                      envelope: ActionEnvelope, params: dict[str, str]) -> None:
    payload = {
        "request_id": request_id,
        "action_id": envelope.action_id,
        "mission_id": envelope.mission_id,
        "bundle_hash": envelope.verification_plan["bundle_hash"],
        "params": dict(sorted(params.items())),
        "before_state_capture": envelope.before_state_capture,
        "idempotency_key": envelope.idempotency_key,
    }
    database.append(Event.create(
        "procedure.execution.requested", payload,
        source_type="supervisor", source_id="phase4-procedure-executor",
        idempotency_key=f"procedure.execution.requested:{request_id}",
    ))


def governed_replay(
    database: FoundryDatabase,
    *,
    tier_c_procedure_id: str,
    mission_id: str,
    run_id: str,
    request_id: str,
    params: dict[str, str],
    adapter: OpenAdaptAdapter,
    run_dir: str | Path,
    oracle: LocalStateOracle | Callable[[], dict[str, Any]],
    stop_path: str | Path | None = None,
    surface: str = "browser",
) -> ReplayOutcome:
    """One bounded replay through ActionEnvelope and effect reconciliation."""
    if surface != "browser":
        return ReplayOutcome("NOT_READY", f"surface-{request_id}", None, "",
                             None, None, reason="OpenAdapt desktop remains PROBING")
    if stop_path is not None and Path(stop_path).exists():
        return ReplayOutcome("NOT_READY", f"stop-{request_id}", None, "",
                             None, None, reason="STOP active before replay")
    bundle = verify_tier_c_reference(database, tier_c_procedure_id)
    try:
        check_openadapt(adapter.config)
    except Exception as exc:
        return ReplayOutcome("NOT_READY", f"replay-{request_id}", None,
                             bundle.bundle_hash, None, None,
                             reason=f"OpenAdapt unavailable: {type(exc).__name__}")
    params = validate_parameters(bundle.input_schema, params)
    connector = select_connector(
        database, "openadapt.replay",
        capabilities_required=("openadapt.browser",),
        preferred=OPENADAPT_BROWSER_CONNECTOR,
        stop_path=stop_path,
    )
    if connector.connector is None:
        return ReplayOutcome("NOT_READY", f"replay-{request_id}", None,
                             bundle.bundle_hash, None, None, reason=connector.reason)
    prior = _existing_request(database, request_id)
    if prior is not None:
        if prior.get("bundle_hash") != bundle.bundle_hash or prior.get("params") != dict(sorted(params.items())):
            raise ActionAuthorityError("duplicate request id carries different bundle or parameters")
        before_capture = dict(prior.get("before_state_capture", {}))
    else:
        before_capture = oracle.before_capture() if isinstance(oracle, LocalStateOracle) else {
            "ref": "custom-oracle", "hash": payload_hash(oracle()),
        }
    envelope = _action_envelope(
        bundle, mission_id=mission_id, run_id=run_id,
        request_id=request_id, before_capture=before_capture, params=params,
    )
    _record_requested(database, request_id=request_id, envelope=envelope, params=params)
    action_key_state = effect_state(database, effect_identity(database, envelope)[0])
    if action_key_state.get("status") == FROZEN:
        return ReplayOutcome("FROZEN", f"replay-{request_id}", envelope.action_id,
                             bundle.bundle_hash, None, None,
                             effect_state=action_key_state,
                             reason="effect is already frozen")

    claim: dict[str, Any] = {}

    def perform(_target: str, _expected: str) -> str:
        result = adapter.replay(bundle, params=params, run_dir=run_dir,
                                stop_path=stop_path)
        claim.update({"run_id": result.run_id, "model_calls": result.model_calls,
                      "report_path": result.report_path, "status": result.status,
                      "reason": result.reason})
        database.append(Event.create(
            "procedure.execution.claimed",
            {"request_id": request_id, "action_id": envelope.action_id,
             "bundle_hash": bundle.bundle_hash, "adapter_status": result.status,
             "run_id": result.run_id, "model_calls": result.model_calls},
            source_type="supervisor", source_id="phase4-procedure-executor",
            idempotency_key=f"procedure.execution.claimed:{request_id}:{result.run_id}",
        ))
        if result.status != "COMPLETED_UNVERIFIED":
            raise AmbiguousEffect(result.reason or result.status)
        if isinstance(oracle, LocalStateOracle):
            expected = oracle.expected_provider(before_capture,
                                                task=params.get("task", ""),
                                                option=params.get("option", ""))
            return str(expected["content_hash"])
        return payload_hash({"request_id": request_id, "bundle_hash": bundle.bundle_hash})

    try:
        capability_id = authorize(database, envelope, stop_path=stop_path)
    except PermissionError as exc:
        return ReplayOutcome("NOT_READY", f"replay-{request_id}", envelope.action_id,
                             bundle.bundle_hash, None, None, reason=str(exc))
    executed = execute_pipeline(
        database, envelope, capability_id,
        connector_id=connector.connector.connector_id,
        perform=perform, stop_path=stop_path,
    )
    key = str(executed["action_key"])
    provider = oracle.provider_state() if isinstance(oracle, LocalStateOracle) else oracle()
    adapter_status = claim.get("status")
    if adapter_status and adapter_status != "COMPLETED_UNVERIFIED":
        # An adapter refusal, STOP, timeout, or process fault is ambiguous
        # even when the provider happens to report a row. Preserve the
        # provider observation and freeze the terminal journal so a later
        # request cannot blindly replay it.
        database.append(Event.create(
            "effect.reconcile_mismatch",
            {"action_key": key, "local_claim": {
                "adapter_status": adapter_status,
                "reason": claim.get("reason", "adapter did not complete")},
             "provider_claim": provider},
            source_type="verifier", source_id="phase4-independent-oracle",
            idempotency_key=(f"procedure.effect.mismatch:{key}:"
                             f"{payload_hash(provider)}:{adapter_status}"),
        ))
        reconciliation = {"reconciled": False, "action_key": key}
    else:
        reconciliation = reconcile_effect(database, key, provider_state=provider)
    state = effect_state(database, key)
    status = str(state.get("status", executed.get("outcome", "FAILED")))
    before_hash = str(before_capture.get("hash", ""))
    provider_hash = str(provider.get("content_hash", ""))
    if provider.get("oracle_error") or not provider_hash:
        recovery_classification = "AMBIGUOUS"
    elif status == "COMPLETED":
        recovery_classification = (
            "COMPLETED_VERIFIED" if reconciliation.get("reconciled")
            else "COMPLETED_UNVERIFIED"
        )
    elif before_hash and provider_hash != before_hash:
        recovery_classification = "PARTIAL"
    else:
        recovery_classification = "NO_EFFECT"
    verification_payload = {
        "request_id": request_id, "action_key": key, "status": status,
        "provider": provider, "reconciled": reconciliation.get("reconciled"),
        "independent": True,
    }
    database.append(Event.create(
        "procedure.effect.verified", verification_payload,
        source_type="verifier", source_id="phase4-independent-oracle",
        idempotency_key=f"procedure.effect.verified:{key}:{payload_hash(provider)}",
    ))
    result = dict(executed)
    result["outcome"] = status
    result["provider"] = provider
    result["reconciled"] = reconciliation.get("reconciled")
    learn(database, mission_id, envelope.action_id, result)
    return ReplayOutcome(
        status, f"replay-{request_id}", envelope.action_id, bundle.bundle_hash,
        claim.get("model_calls"), claim.get("report_path"), effect_state=state,
        reason="independent effect oracle matched" if status == "COMPLETED" else "effect frozen or failed",
        recovery_classification=recovery_classification,
    )
