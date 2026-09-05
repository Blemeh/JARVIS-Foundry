"""RoleAdapter that executes fabric actions on the mission/worker path.

Hermes stays the adapter for roles and capabilities the fabric cannot
run. This wrapper intercepts builder/repair sessions whose capability
maps to a real fabric action, executes through ``execute_for_spine``,
and returns a structured RoleResult. It is not a second mission loop.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from ..effects import FAILED as EFFECT_FAILED
from ..effects import FROZEN, effect_state, finalize_verified_effect
from ..events import Event
from ..roles.adapter import RoleAdapter
from ..roles.manifest import ContextManifest
from ..roles.results import (
    BLOCKED,
    COMPLETED,
    FAILED,
    RoleResult,
    VERDICT_FAIL,
    VERDICT_NOT_TESTED,
    VERDICT_PASS,
)
from .catalog import get_spec
from .executors import HANDLERS, ExecutorContext
from .contracts import build_adapter_request, metadata_for_spec
from .general_work import (
    ARTIFACT_ACTIONS,
    FILE_TRANSFER_ACTIONS,
    GENERAL_WORK_ACTIONS,
    RECONCILABLE_ACTIONS,
    inspect_general_work_reconciliation,
)
from .loop import seed_builtins, select_capability
from .model import FabricError, TaskIntent
from .spine import execute_for_spine

V5_TO_FABRIC = {
    "write_registered_worktree_files": "file.write",
    "read_registered_files": "file.read",
    "run_registered_local_commands": "script.run",
    "git_local_commit": "git.commit",
    "git_push": "git.push",
}

# Local reads that do not need a JSON envelope on the worker path.
_STRUCTURED_READS = {
    "file.read",
    "dir.list",
    "git.inspect",
    "git.diff",
    "git.log",
    "system.diagnose",
    "system.runtime",
    "browser.web.wait",
}


def _is_mcp_fabric_only(capability: str, action_kind: str | None = None) -> bool:
    return (
        capability.startswith(("mcp.", "cap.mcp."))
        or bool(action_kind and action_kind.startswith("mcp."))
    )


def _mcp_fabric_blocked(
    manifest: ContextManifest,
    *,
    reason: str,
) -> RoleResult:
    return RoleResult(
        run_id=manifest.run_id,
        role=manifest.role,
        status=BLOCKED,
        provider="fabric",
        model="local",
        verdict=VERDICT_NOT_TESTED,
        evidence=(
            f"fabric:{manifest.capability}",
            "verified:UNKNOWN",
        ),
        error_class="fabric_capability_unavailable",
        error_detail=reason,
        identity_transition_authorized=True,
    )


def _fabric_reconciliation_blocked(
    manifest: ContextManifest,
    *,
    action: str,
    reason: str,
) -> RoleResult:
    return RoleResult(
        run_id=manifest.run_id,
        role=manifest.role,
        status=BLOCKED,
        provider="fabric",
        model="local",
        verdict=VERDICT_NOT_TESTED,
        evidence=(f"fabric:{action}", "verified:UNKNOWN"),
        error_class="fabric_effect_reconciliation_required",
        error_detail=reason,
        identity_transition_authorized=True,
    )


def resolve_fabric_action(capability: str, database=None) -> str | None:
    if capability in HANDLERS:
        return capability
    if capability.startswith("mcp."):
        return capability
    mapped = V5_TO_FABRIC.get(capability)
    if mapped is not None:
        return mapped
    if database is not None and capability.startswith("cap."):
        spec = get_spec(database, capability)
        if spec is not None:
            return spec.action_kind
    return None


def resolve_general_work_dispatch(
    database,
    *,
    role: str,
    mission_id: str,
    step_id: str | None,
    capability: str,
) -> tuple[str, str, str | None] | None:
    """Resolve exact local General Work authority, including verifier replay."""

    from ..mission.contracts import latest_contract

    contract = latest_contract(
        database,
        mission_id,
        step_id=step_id,
        allow_invalid=False,
    )
    contract_owned = (
        contract is not None and contract.source == "v13-general-work"
    )
    compatibility_owned = mission_id.startswith("mission-general-work-")
    if not contract_owned and not compatibility_owned:
        return None
    if contract_owned and role != "verifier" and capability != contract.capability:
        return None
    resolved_capability = (
        contract.capability if contract_owned else capability
    )
    action = resolve_fabric_action(resolved_capability, database)
    instructions = None
    if (
        contract_owned
        and role == "verifier"
        and capability == "verification"
    ):
        instructions = contract.instructions
    if action not in GENERAL_WORK_ACTIONS:
        return None
    return action, resolved_capability, instructions


def _json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def owned_root_for_manifest(
    database,
    manifest: ContextManifest,
    fallback: Path | None = None,
) -> Path | None:
    """Prefer the registered resource root over a constructor default."""
    resource = database.row("resources", "resource_id", manifest.resource_id)
    raw = None
    if resource is not None:
        raw = resource.get("resolved_root") or resource.get("root_path")
    if raw:
        root = Path(str(raw)).expanduser().resolve()
        if root.is_file():
            root = root.parent
        if root.is_dir():
            return root
    if fallback is not None:
        return Path(fallback).expanduser().resolve()
    return None


def payload_from_manifest(
    manifest: ContextManifest,
    action_kind: str,
    owned_root: Path,
    *,
    structured_only: bool = False,
) -> dict[str, Any] | None:
    parsed = _json_object(manifest.instructions)
    if parsed is not None:
        payload = dict(parsed)
    elif structured_only and action_kind not in _STRUCTURED_READS:
        return None
    elif action_kind == "file.write":
        name = manifest.expected_artifacts[0] if manifest.expected_artifacts else "result.txt"
        payload = {
            "target": str(name),
            "content": manifest.instructions.strip() or "fabric-delivered\n",
        }
    elif action_kind == "file.read":
        name = manifest.expected_artifacts[0] if manifest.expected_artifacts else "result.txt"
        payload = {"target": str(name)}
    elif action_kind == "dir.list":
        payload = {"target": "."}
    elif action_kind in {"system.diagnose", "system.runtime"}:
        payload = {}
    elif action_kind in {"git.inspect", "git.diff", "git.log"}:
        payload = {"worktree": str(owned_root)}
    else:
        return None
    if action_kind.startswith("git.") and "worktree" not in payload:
        payload["worktree"] = str(owned_root)
    return payload


def try_fabric_dispatch(
    database,
    manifest: ContextManifest,
    heartbeat: Callable[[], Any],
    *,
    owned_root: Path,
    structured_only: bool = False,
    mcp_backend=None,
    mcp_mutation_verifier=None,
) -> RoleResult | None:
    """Run a mapped fabric action or return None so the inner adapter can."""
    dispatch_manifest = manifest
    action = resolve_fabric_action(manifest.capability, database)
    resolved_capability = manifest.capability
    general_work = resolve_general_work_dispatch(
        database,
        role=manifest.role,
        mission_id=manifest.mission_id,
        step_id=manifest.step_id,
        capability=manifest.capability,
    )
    if general_work is not None:
        action, resolved_capability, instructions = general_work
        dispatch_manifest = replace(
            manifest,
            capability=resolved_capability,
            instructions=(
                instructions
                if instructions is not None
                else manifest.instructions
            ),
        )
    role_allowed = manifest.role in {"builder", "repair"} or (
        manifest.role == "verifier" and action in GENERAL_WORK_ACTIONS
    )
    if not role_allowed:
        return None
    mcp_fabric_only = _is_mcp_fabric_only(manifest.capability, action)
    if action is None:
        if mcp_fabric_only:
            return _mcp_fabric_blocked(
                manifest,
                reason="selected MCP capability is missing from the Fabric catalog",
            )
        return None
    payload = payload_from_manifest(
        dispatch_manifest,
        action,
        owned_root,
        structured_only=structured_only,
    )
    if payload is None:
        if mcp_fabric_only:
            return _mcp_fabric_blocked(
                manifest,
                reason="selected MCP capability requires a structured Fabric payload",
            )
        return None
    if manifest.role == "verifier" and action in ARTIFACT_ACTIONS:
        probe_ctx = ExecutorContext(
            database=database,
            owned_root=owned_root,
            mcp_backend=mcp_backend,
            mcp_mutation_verifier=mcp_mutation_verifier,
        )
        observation = inspect_general_work_reconciliation(
            probe_ctx,
            action,
            payload,
        )
        matches = observation.get("matches") is True
        artifact_sha256 = str(
            observation.get("artifact_sha256") or "unavailable"
        )
        database.append(
            Event.create(
                "general_work.artifact_verification_observed",
                {
                    "mission_id": manifest.mission_id,
                    "step_id": manifest.step_id,
                    "run_id": manifest.run_id,
                    "action_kind": action,
                    "status": observation.get("status"),
                    "matches": matches,
                    "target_ref": observation.get("target_ref"),
                    "artifact_sha256": observation.get("artifact_sha256"),
                    "reason": observation.get("reason"),
                },
                source_type="verifier",
                source_id="v13-general-work",
                correlation_id=manifest.mission_id,
                idempotency_key=(
                    "general_work.artifact_verification_observed:"
                    f"{manifest.run_id}:{artifact_sha256}"
                ),
            )
        )
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=COMPLETED,
            provider="fabric",
            model="local",
            verdict=VERDICT_PASS if matches else VERDICT_FAIL,
            evidence=(
                f"fabric:{action}",
                "verification:independent-readback",
                f"verified:{observation.get('status')}",
                f"artifact-sha256:{artifact_sha256}",
            ),
            error_class=(
                None if matches else "artifact_verification_mismatch"
            ),
            error_detail=(
                None
                if matches
                else str(
                    observation.get("reason")
                    or "format-aware artifact readback did not match"
                )
            ),
            identity_transition_authorized=True,
        )
    original_effect_id: str | None = None
    repair_reconciliation: dict[str, Any] | None = None
    if manifest.role == "repair" and action in RECONCILABLE_ACTIONS:
        spec = get_spec(database, resolved_capability)
        if spec is None:
            return None
        original_request = build_adapter_request(
            mission_id=manifest.mission_id,
            workflow_id=f"workflow:{manifest.mission_id}",
            step_id=str(manifest.step_id or "step"),
            step_version=1,
            attempt=manifest.attempt,
            action_kind=action,
            payload=payload,
            spec=spec,
            metadata=metadata_for_spec(spec),
        )
        original_effect_id = original_request.effect_id
        prior_effect = effect_state(database, original_effect_id)
        if prior_effect.get("status") == FROZEN:
            reconciliation_payload = dict(payload)
            if action in FILE_TRANSFER_ACTIONS:
                recorded_identity = prior_effect.get("request_identity")
                expected_identity = original_request.to_evidence()
                identity_fields = (
                    "effect_id",
                    "idempotency_key",
                    "action_kind",
                    "input_hash",
                    "target_ref",
                )
                if (
                    not isinstance(recorded_identity, Mapping)
                    or any(
                        recorded_identity.get(field)
                        != expected_identity.get(field)
                        for field in identity_fields
                    )
                ):
                    repair_reconciliation = {
                        "status": "IDENTITY_MISMATCH",
                        "matches": False,
                        "target_ref": payload.get("target"),
                        "reason": (
                            "frozen file effect request identity does not match "
                            "the repair request"
                        ),
                    }
                else:
                    reconciliation_payload.setdefault(
                        "idempotency_key",
                        original_request.idempotency_key,
                    )
            if repair_reconciliation is None:
                probe_ctx = ExecutorContext(
                    database=database,
                    owned_root=owned_root,
                    mcp_backend=mcp_backend,
                    mcp_mutation_verifier=mcp_mutation_verifier,
                )
                repair_reconciliation = inspect_general_work_reconciliation(
                    probe_ctx,
                    action,
                    reconciliation_payload,
                )
            if repair_reconciliation.get("matches") is True:
                claim = repair_reconciliation.get("claim")
                if not isinstance(claim, dict):
                    return _mcp_fabric_blocked(
                        manifest,
                        reason="General Work reconciliation produced no claim",
                    )
                finalize_verified_effect(
                    database,
                    original_effect_id,
                    provider_receipt={
                        "provider_id": "filesystem",
                        "provider_ref": str(
                            repair_reconciliation.get("target_ref")
                            or payload.get("target")
                            or ""
                        ),
                    },
                    readback={
                        "status": "MATCH",
                        "matches": True,
                        "artifact_sha256": repair_reconciliation.get(
                            "artifact_sha256"
                        ),
                        "details": repair_reconciliation.get("readback"),
                    },
                    result=claim,
                    acceptance_evidence={
                        "authority": "general_work.independent_readback",
                        "mission_id": manifest.mission_id,
                        "step_id": manifest.step_id,
                    },
                    reconciled=True,
                )
            else:
                database.append(
                    Event.create(
                        "general_work.effect_reconciliation_observed",
                        {
                            "mission_id": manifest.mission_id,
                            "step_id": manifest.step_id,
                            "action_kind": action,
                            "effect_id": original_effect_id,
                            "status": repair_reconciliation.get("status"),
                            "target_ref": repair_reconciliation.get(
                                "target_ref"
                            ),
                            "artifact_sha256": repair_reconciliation.get(
                                "artifact_sha256"
                            ),
                        },
                        source_type="supervisor",
                        source_id="v13-general-work",
                        correlation_id=manifest.mission_id,
                        idempotency_key=(
                            "general_work.effect_reconciliation_observed:"
                            f"{original_effect_id}:{manifest.attempt}"
                        ),
                    )
                )
                if action in FILE_TRANSFER_ACTIONS:
                    return _fabric_reconciliation_blocked(
                        manifest,
                        action=action,
                        reason=str(
                            repair_reconciliation.get("reason")
                            or "frozen file effect did not match current readback"
                        ),
                    )
        if (
            action in ARTIFACT_ACTIONS
            and prior_effect.get("status") in {EFFECT_FAILED, FROZEN}
            and not (
            repair_reconciliation
            and repair_reconciliation.get("matches") is True
            )
        ):
            payload = {
                **payload,
                "repair_existing": True,
                "repair_attempt": manifest.attempt,
                "repair_of_effect_id": original_effect_id,
            }
    seed_builtins(database)
    selection = select_capability(
        database,
        TaskIntent(
            task_id=f"mission.{manifest.mission_id}",
            required_actions=(action,),
            unattended=True,
        ),
        action,
    )
    if not selection.available and not action.startswith("mcp."):
        # DEGRADED / VETOED / missing: do not execute, and do not claim
        # the step. The inner Hermes path may still satisfy it.
        return None
    heartbeat()
    ctx = ExecutorContext(
        database=database,
        owned_root=owned_root,
        mcp_backend=mcp_backend,
        mcp_mutation_verifier=mcp_mutation_verifier,
    )
    try:
        receipt = execute_for_spine(
            database,
            ctx,
            mission_id=manifest.mission_id,
            step_id=str(manifest.step_id or "step"),
            action_kind=action,
            payload=payload,
            workflow_id=f"workflow:{manifest.mission_id}",
            attempt=manifest.attempt,
            result_scope=f"{manifest.role}:{manifest.run_id}",
            route_owner="general_work" if general_work is not None else None,
        )
    except FabricError as error:
        # An attempted fabric effect must not also run Hermes.
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=FAILED,
            provider="fabric",
            model="local",
            verdict=VERDICT_FAIL,
            evidence=(f"fabric:{action}", "verified:UNKNOWN"),
            error_class="fabric_effect_failed",
            error_detail=str(error),
            identity_transition_authorized=True,
        )
    success = bool(receipt.get("success"))
    if (
        success
        and original_effect_id is not None
        and payload.get("repair_existing") is True
    ):
        database.append(
            Event.create(
                "general_work.output_repaired",
                {
                    "mission_id": manifest.mission_id,
                    "step_id": manifest.step_id,
                    "action_kind": action,
                    "repair_attempt": manifest.attempt,
                    "repaired_effect_id": original_effect_id,
                    "repair_effect_id": receipt.get("effect_id"),
                    "target_ref": payload.get("target"),
                },
                source_type="supervisor",
                source_id="v13-general-work",
                correlation_id=manifest.mission_id,
                idempotency_key=(
                    "general_work.output_repaired:"
                    f"{original_effect_id}:{manifest.attempt}"
                ),
            )
        )
    return RoleResult(
        run_id=manifest.run_id,
        role=manifest.role,
        status=COMPLETED if success else FAILED,
        provider="fabric",
        model="local",
        verdict=VERDICT_PASS if success else VERDICT_FAIL,
        evidence=(
            f"fabric:{action}",
            f"verified:{receipt.get('verified')}",
            f"capability:{receipt.get('capability_id')}",
            *(
                (f"receipt:{receipt['receipt_id']}", f"effect:{receipt['effect_id']}")
                if receipt.get("receipt_id") and receipt.get("effect_id")
                else ()
            ),
        ),
        error_class=None if success else "fabric_effect_failed",
        error_detail=str(receipt.get("note") or "") or None,
        identity_transition_authorized=True,
    )


class FabricRoleAdapter:
    """Prefer fabric execution; delegate unknown work to ``inner``."""

    def __init__(
        self,
        database,
        *,
        owned_root: str | Path,
        inner: RoleAdapter,
        structured_only: bool = False,
        mcp_backend=None,
        mcp_mutation_verifier=None,
    ) -> None:
        self.database = database
        self.owned_root = Path(owned_root).expanduser().resolve()
        self.inner = inner
        self.structured_only = structured_only
        self.mcp_backend = mcp_backend
        self.mcp_mutation_verifier = mcp_mutation_verifier

    def dispatch(self, manifest: ContextManifest, *, heartbeat) -> RoleResult:
        root = owned_root_for_manifest(
            self.database, manifest, fallback=self.owned_root,
        )
        if root is None:
            if _is_mcp_fabric_only(manifest.capability):
                return _mcp_fabric_blocked(
                    manifest,
                    reason="selected MCP capability has no registered resource root",
                )
            return self.inner.dispatch(manifest, heartbeat=heartbeat)
        result = try_fabric_dispatch(
            self.database, manifest, heartbeat,
            owned_root=root,
            structured_only=self.structured_only,
            mcp_backend=self.mcp_backend,
            mcp_mutation_verifier=self.mcp_mutation_verifier,
        )
        if result is None:
            return self.inner.dispatch(manifest, heartbeat=heartbeat)
        return result
