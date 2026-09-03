"""RoleAdapter that executes fabric actions on the mission/worker path.

Hermes stays the adapter for roles and capabilities the fabric cannot
run. This wrapper intercepts builder/repair sessions whose capability
maps to a real fabric action, executes through ``execute_for_spine``,
and returns a structured RoleResult. It is not a second mission loop.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

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
    action = resolve_fabric_action(manifest.capability, database)
    if manifest.role not in {"builder", "repair"}:
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
        manifest, action, owned_root, structured_only=structured_only,
    )
    if payload is None:
        if mcp_fabric_only:
            return _mcp_fabric_blocked(
                manifest,
                reason="selected MCP capability requires a structured Fabric payload",
            )
        return None
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
