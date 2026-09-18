from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any

from ..events import Event, payload_hash


class RegistrationError(RuntimeError):
    pass


def sync_registered_resource_capabilities(
    database: Any,
    *,
    source_id: str = "capability-plane-sync",
) -> int:
    """Project each registered resource's DECLARED capability readiness once.

    Registration declares initial readiness (a clean worktree is READY for
    ``write_registered_worktree_files``).  The durable capability plane is the
    execution authority and treats a missing record as UNKNOWN/not-ready, so
    without this pass a freshly registered resource could never become
    eligible.  Only the first record per (resource, capability) is written:
    later control-plane transitions own the state and are never overwritten by
    re-projection.
    """
    import json

    from .capabilities import (
        DECLARED_STATES,
        capability_state_recorded,
        set_capability_state,
    )

    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT resource_id, capabilities_json FROM resources ORDER BY resource_id"
        ).fetchall()
    written = 0
    for row in rows:
        resource_id = str(row["resource_id"])
        try:
            capabilities = json.loads(str(row["capabilities_json"]))
        except (TypeError, ValueError):
            continue
        if not isinstance(capabilities, dict):
            continue
        for capability, state in sorted(capabilities.items()):
            declared = str(state or "")
            if declared not in DECLARED_STATES:
                continue
            if capability_state_recorded(database, resource_id, str(capability)):
                continue
            set_capability_state(
                database, resource_id, str(capability), declared,
                source_id=source_id,
                evidence={
                    "source": "resource-registration",
                    "declared_state": declared,
                    "projected_by": "control.registration.sync_registered_resource_capabilities",
                },
            )
            written += 1
    return written


def _git(root: Path, *args: str) -> str:
    from ..platform.windows_subprocess import headless_run_kwargs

    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15, check=False,
        **headless_run_kwargs(),
    )
    if result.returncode != 0:
        raise RegistrationError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def inspect_git_resource(
    path: str | Path,
    *,
    resource_id: str | None = None,
    writable: bool = True,
) -> dict[str, Any]:
    requested = Path(path).expanduser().resolve()
    root = Path(_git(requested, "rev-parse", "--show-toplevel")).resolve()
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current") or "DETACHED"
    status = _git(root, "status", "--porcelain=v1")
    remotes = _git(root, "remote", "-v").splitlines()
    capabilities = {
        "read_registered_files": "READY",
        "write_registered_worktree_files": "READY" if writable and not status else "PROBING",
        "run_registered_local_commands": "READY",
        "git_local_commit": "PROBING",
        "git_push": "DISCOVERED",
    }
    return {
        "resource_id": resource_id or f"resource-{uuid.uuid4()}",
        "kind": "git_worktree",
        "root_path": str(path),
        "resolved_root": str(root),
        "baseline": {
            "head": head,
            "branch": branch,
            "status_porcelain": status.splitlines(),
            "remotes": remotes,
        },
        "capabilities": capabilities,
        "readiness_state": "READY" if not status else "PROBING",
    }


def registration_event(resource: dict[str, Any], *, source_id: str = "wp0") -> Event:
    # Idempotency covers the FULL registration payload: a re-registration at an
    # unchanged HEAD must still update dirty state (status_porcelain), remotes,
    # capabilities or readiness, while an identical delivery dedupes. A key that
    # excluded status_porcelain would raise EventConflict on the same-HEAD dirty
    # change (verifier finding B5).
    return Event.create(
        "project.registered",
        resource,
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"project.registered:{resource['resource_id']}:{payload_hash(resource)}",
    )

