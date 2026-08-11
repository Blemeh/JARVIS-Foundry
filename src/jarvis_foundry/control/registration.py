from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any

from ..events import Event, payload_hash


class RegistrationError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15, check=False,
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

