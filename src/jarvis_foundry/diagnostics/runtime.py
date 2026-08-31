"""Read-only runtime health and correlation diagnostics."""

from __future__ import annotations

import json
from typing import Any

from ..database import FoundryDatabase
from ..supervisor.control import inspect_control

_SAFE_FIELDS = {
    "event_type", "sequence", "recorded_at", "event_id", "source_type",
    "source_id", "release_id", "instance_id", "pid", "generation",
    "creation_identity", "lease_owner", "control_request_id", "request_id",
    "mission_id", "action_id", "channel", "provider_requested",
    "provider_actual", "provider_request_id", "status", "cost_status",
    "attempt", "reason", "error_class", "retryable", "outcome",
}


def derive_health(
    *,
    runtime_status: str,
    reconciliation_consistent: bool,
    release_status: str | None,
    stop_active: bool,
    supervisor_count: int | None = None,
    parity: str | None = None,
    trust_state: str | None = None,
) -> dict[str, Any]:
    """Derive an honest operational status without a fabricated healthy default."""

    runtime_status = str(runtime_status).upper()
    if supervisor_count == 0:
        return {"status": "DEGRADED", "reason": "canonical Supervisor process is missing"}
    if supervisor_count is not None and supervisor_count > 1:
        return {"status": "DEGRADED", "reason": "duplicate canonical Supervisor processes detected"}
    if stop_active or runtime_status == "BLOCKED":
        return {"status": "BLOCKED", "reason": "emergency STOP or runtime block is active"}
    if runtime_status in {"UNKNOWN", "DEGRADED"}:
        return {"status": runtime_status, "reason": "runtime control did not prove a healthy owner"}
    if runtime_status == "STOPPED":
        return {"status": "STOPPED", "reason": "supervisor is durably stopped"}
    if not reconciliation_consistent:
        return {"status": "DEGRADED", "reason": "event/projection reconciliation is inconsistent"}
    if release_status not in (None, "VALID"):
        return {"status": "DEGRADED", "reason": f"release identity status is {release_status}"}
    if parity not in (None, "MATCH"):
        return {"status": "DEGRADED", "reason": f"source/installed/runtime parity is {parity}"}
    if runtime_status == "RUNNING":
        return {"status": "RUNNING", "reason": "live lease and process identity are proven"}
    return {"status": "UNKNOWN", "reason": f"unrecognized runtime status: {runtime_status}"}


def _redacted_event(row: Any) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    safe_payload = {
        key: value for key, value in payload.items() if key in _SAFE_FIELDS
    }
    return {
        "event_type": row["event_type"],
        "sequence": int(row["sequence"]),
        "recorded_at": row["recorded_at"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "payload": safe_payload,
    }


def diagnostic_snapshot(
    database: FoundryDatabase,
    *,
    stop_path: str | None = None,
    event_limit: int = 40,
) -> dict[str, Any]:
    """Return a bounded, secret-free diagnostic view from durable state."""

    state = inspect_control(database, stop_path=stop_path)
    connection = database.connect(readonly=True)
    try:
        rows = connection.execute(
            "SELECT event_type, sequence, recorded_at, source_type, source_id, payload_json "
            "FROM events ORDER BY sequence DESC LIMIT ?", (max(1, int(event_limit)),)
        ).fetchall()
    finally:
        connection.close()
    events = [_redacted_event(row) for row in reversed(rows)]
    request_ids: set[str] = set()
    mission_ids: set[str] = set()
    release_ids: set[str] = set()
    process_ids: set[str] = set()
    lease_owners: set[str] = set()
    for event in events:
        payload = event["payload"]
        for value, target in (
            (payload.get("request_id") or payload.get("control_request_id"), request_ids),
            (payload.get("mission_id"), mission_ids),
            (payload.get("release_id"), release_ids),
            (payload.get("instance_id"), process_ids),
            (payload.get("lease_owner"), lease_owners),
        ):
            if value:
                target.add(str(value))
    if state.request_id:
        request_ids.add(state.request_id)
    if state.release_id:
        release_ids.add(state.release_id)
    if state.instance_id:
        process_ids.add(state.instance_id)
    if state.lease_owner:
        lease_owners.add(state.lease_owner)
    return {
        "runtime_control": state.to_json(),
        "correlation": {
            "release_ids": sorted(release_ids),
            "process_instances": sorted(process_ids),
            "pids": [state.pid] if state.pid is not None else [],
            "lease_owners": sorted(lease_owners),
            "request_ids": sorted(request_ids),
            "mission_ids": sorted(mission_ids),
        },
        "events": events,
    }
