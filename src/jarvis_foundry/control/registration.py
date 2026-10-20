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


def _status_involved_paths(line: str) -> list[str]:
    """All repository paths a porcelain line touches.

    Renames/copies (``R``/``C``) name origin and destination separated by
    ``' -> '``; every other entry names one path after the XY status field.
    """
    body = line[3:] if len(line) > 3 else line
    if " -> " in body:
        return [part.strip().strip('"') for part in body.split(" -> ")]
    return [body.strip().strip('"')]


def _is_exhaust_path(path: str) -> bool:
    """Whether one path belongs to the runtime's prescribed exhaust namespace.

    Exactly the top-level ``.jarvis`` directory and top-level ``.jarvis*``
    result files.  Sibling directories such as ``.jarvis-evil/`` and nested
    ``sub/.jarvis/`` paths are NOT exhaust: an actor able to write outside
    the prescribed namespace must not inherit its tolerance.
    """
    if "/" in path:
        return path.split("/", 1)[0] == ".jarvis"
    return path == ".jarvis" or path.startswith(".jarvis")


def _meaningful_status_lines(status_lines: list[str]) -> list[str]:
    """Porcelain lines that count as worktree dirt for readiness.

    The runtime's own prescribed exhaust namespace (``.jarvis/`` receipts,
    outcomes and diagnoses, plus top-level ``.jarvis*`` result files) is
    tolerated by name: missions routinely leave evidence there (the B6
    receipt pattern), and treating that exhaust as dirt would demote every
    resource a mission just used, blocking its own continuation.  Every
    path a line touches must be exhaust (a staged rename out of ``.jarvis/``
    into a real path still counts as dirt).  Real user/session changes
    anywhere else still count.  The full status stays in the stored
    baseline; only the readiness derivation ignores exhaust.
    """
    meaningful: list[str] = []
    for line in status_lines:
        paths = _status_involved_paths(line)
        if paths and all(_is_exhaust_path(path) for path in paths):
            continue
        meaningful.append(line)
    return meaningful


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
    meaningful = _meaningful_status_lines(status.splitlines())
    capabilities = {
        "read_registered_files": "READY",
        "write_registered_worktree_files": "READY" if writable and not meaningful else "PROBING",
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
        "readiness_state": "READY" if not meaningful else "PROBING",
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


def refresh_git_resource_readiness(
    database: Any,
    resource_id: str,
    *,
    source_id: str = "readiness-refresh",
) -> dict[str, Any]:
    """Re-inspect one git_worktree resource and supersede stale declarations.

    Registration declares initial readiness (a clean worktree is READY for
    ``write_registered_worktree_files``), but that declaration is projected
    once and the stored baseline can drift (new HEAD, cleaned/dirtied tree)
    while the old evidence keeps gating portfolio eligibility.  This refresh
    re-runs the same inspection on the stored checkout and, only when the
    current observation differs, records new evidence:

    - dirty/stale/uncertain -> clean/resolved/current: the capability plane
      moves to the fresh declaration (e.g. PROBING -> READY), so a parked
      objective becomes eligible on the next cycle;
    - clean -> dirty/unresolvable: the plane moves back to PROBING, so
      mutation is blocked again.

    History is preserved (append-only events; the old PROBING evidence stays
    auditable).  Incident-owned BLOCKING states (STOPPED/VETOED/DEGRADED) are
    never overwritten by an inspection: veto/STOP/suspension supremacy holds.
    Mission-pinned baselines live in mission contracts and are untouched; only
    the resource's current-observed baseline is updated.  A failed inspection
    records nothing (fail closed).  An unchanged observation records nothing,
    so replay and restart converge with no duplicate transitions.
    """
    import json

    from .capabilities import (
        BLOCKING_STATES,
        DECLARED_STATES,
        latest_capability_state,
        set_capability_state,
    )

    row = database.row("resources", "resource_id", resource_id)
    if row is None:
        return {"resource_id": resource_id, "refreshed": False, "reason": "unknown resource"}
    if str(row["kind"]) != "git_worktree":
        return {"resource_id": resource_id, "refreshed": False, "reason": f"unsupported kind {row['kind']}"}
    root = Path(str(row["resolved_root"] or row["root_path"] or ""))
    try:
        inspected = inspect_git_resource(root, resource_id=resource_id)
    except Exception as error:  # noqa: BLE001 - failed inspection records nothing
        return {"resource_id": resource_id, "refreshed": False, "reason": f"{type(error).__name__}: {error}"[:200]}
    try:
        stored_baseline = json.loads(str(row["baseline_json"] or "{}"))
    except (TypeError, ValueError):
        stored_baseline = {}
    fresh_baseline = dict(inspected["baseline"])
    # Exhaust-only churn (runtime .jarvis/ evidence coming and going) must
    # neither flip readiness nor spam re-registration events; real changes
    # still do.  The stored baseline keeps the full status truth.
    stored_meaningful = _meaningful_status_lines(
        [str(line) for line in (stored_baseline.get("status_porcelain") or [])]
    )
    fresh_meaningful = _meaningful_status_lines(
        [str(line) for line in (fresh_baseline.get("status_porcelain") or [])]
    )
    baseline_changed = (
        stored_baseline.get("head") != fresh_baseline.get("head")
        or stored_meaningful != fresh_meaningful
    )
    declared = {str(key): str(value) for key, value in dict(inspected["capabilities"]).items()}
    # Resolve each declaration against the current plane: the plane owns the
    # state; the inspection only supersedes non-incident states that changed.
    effective: dict[str, str] = {}
    transitions: list[dict[str, Any]] = []
    for capability in sorted(declared):
        state = declared[capability]
        if state not in DECLARED_STATES:
            continue
        latest = latest_capability_state(database, resource_id, capability)
        current = str((latest or {}).get("state") or "")
        if current in BLOCKING_STATES:
            effective[capability] = current
            transitions.append({"capability": capability, "from": current, "to": current, "skipped": "incident-owned"})
            continue
        effective[capability] = state
        if current == state:
            continue
        set_capability_state(
            database,
            resource_id,
            capability,
            state,
            source_id=source_id,
            evidence={
                "source": "readiness-refresh",
                "inspected_head": str(fresh_baseline.get("head") or ""),
                "inspected_branch": str(fresh_baseline.get("branch") or ""),
                "status_entries": len(fresh_baseline.get("status_porcelain") or []),
                "prior_state": current or "UNRECORDED",
                "projected_by": "control.registration.refresh_git_resource_readiness",
            },
        )
        transitions.append({"capability": capability, "from": current or "UNRECORDED", "to": state})
    if not baseline_changed and not any("skipped" not in item for item in transitions):
        return {"resource_id": resource_id, "refreshed": False, "reason": "unchanged", "head": str(fresh_baseline.get("head") or "")}
    if any(value in BLOCKING_STATES for value in effective.values()):
        readiness_state = "DEGRADED"
    elif any(value == "PROBING" for value in effective.values()):
        readiness_state = "PROBING"
    else:
        readiness_state = "READY"
    payload = {
        "resource_id": resource_id,
        "kind": "git_worktree",
        "root_path": str(row["root_path"]),
        "resolved_root": str(inspected["resolved_root"]),
        "baseline": fresh_baseline,
        "capabilities": effective,
        "readiness_state": readiness_state,
    }
    database.append(registration_event(payload, source_id=source_id))
    return {
        "resource_id": resource_id,
        "refreshed": True,
        "head": str(fresh_baseline.get("head") or ""),
        "readiness_state": readiness_state,
        "transitions": transitions,
    }

