"""RoleAdapter that executes fabric actions on the mission/worker path.

Hermes stays the adapter for roles and capabilities the fabric cannot
run. This wrapper intercepts builder/repair sessions whose capability
maps to a real fabric action, executes through ``execute_for_spine``,
and returns a structured RoleResult. It is not a second mission loop.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..roles.adapter import RoleAdapter
from ..roles.manifest import ContextManifest
from ..roles.results import COMPLETED, FAILED, RoleResult, VERDICT_FAIL, VERDICT_PASS
from .executors import HANDLERS, ExecutorContext
from .spine import execute_for_spine

V5_TO_FABRIC = {
    "write_registered_worktree_files": "file.write",
    "read_registered_files": "file.read",
    "run_registered_local_commands": "script.run",
    "git_local_commit": "git.commit",
    "git_push": "git.push",
}


def resolve_fabric_action(capability: str) -> str | None:
    if capability in HANDLERS:
        return capability
    return V5_TO_FABRIC.get(capability)


def _json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def payload_from_manifest(
    manifest: ContextManifest,
    action_kind: str,
    owned_root: Path,
) -> dict[str, Any] | None:
    parsed = _json_object(manifest.instructions)
    if parsed is not None:
        payload = dict(parsed)
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
    elif action_kind == "system.diagnose":
        payload = {}
    elif action_kind == "system.runtime":
        payload = {}
    elif action_kind in {"git.inspect", "git.diff", "git.log"}:
        payload = {"worktree": str(owned_root)}
    else:
        return None
    if action_kind.startswith("git.") and "worktree" not in payload:
        payload["worktree"] = str(owned_root)
    return payload


class FabricRoleAdapter:
    """Prefer fabric execution; delegate unknown work to ``inner``."""

    def __init__(
        self,
        database,
        *,
        owned_root: str | Path,
        inner: RoleAdapter,
    ) -> None:
        self.database = database
        self.owned_root = Path(owned_root).expanduser().resolve()
        self.inner = inner

    def dispatch(self, manifest: ContextManifest, *, heartbeat) -> RoleResult:
        action = resolve_fabric_action(manifest.capability)
        if manifest.role not in {"builder", "repair"} or action is None:
            return self.inner.dispatch(manifest, heartbeat=heartbeat)
        payload = payload_from_manifest(manifest, action, self.owned_root)
        if payload is None:
            return self.inner.dispatch(manifest, heartbeat=heartbeat)
        heartbeat()
        ctx = ExecutorContext(database=self.database, owned_root=self.owned_root)
        receipt = execute_for_spine(
            self.database,
            ctx,
            mission_id=manifest.mission_id,
            step_id=str(manifest.step_id or "step"),
            action_kind=action,
            payload=payload,
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
            ),
            error_class=None if success else "fabric_effect_failed",
            error_detail=str(receipt.get("note") or "") or None,
        )
