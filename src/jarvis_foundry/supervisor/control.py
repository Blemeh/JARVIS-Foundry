"""Durable owner requests and conservative runtime-control state.

Owner requests live in the append-only event ledger. A request survives a
process crash, duplicate delivery is idempotent, and the derived state never
claims ``RUNNING`` without a live lease and a matching process identity.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase
from ..events import Event, EventConflict
from .estop import EmergencyStop
from .worker import pid_alive, process_creation_identity

CONTROL_REQUESTED = "runtime.control.requested"
CONTROL_RESOLVED = "runtime.control.resolved"
CONTROL_CLAIMING = "runtime.control.claiming"
CONTROL_LEASE_RECLAIMED = "runtime.control.lease_reclaimed"

ACTIONS = frozenset({"START", "STOP", "RESTART"})
RUNTIME_STATES = frozenset({"STOPPED", "RUNNING", "BLOCKED", "DEGRADED", "UNKNOWN"})
REQUEST_STATUSES = frozenset({"PENDING", "APPLIED", "BLOCKED", "STALE"})
REQUEST_STALE_AFTER_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class RuntimeControlRequest:
    request_id: str
    action: str
    requested_by: str
    requested_at: str
    expected_release: str | None = None
    expires_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RuntimeControlState:
    status: str
    desired_action: str | None
    request_id: str | None
    request_status: str | None
    instance_id: str | None
    pid: int | None
    creation_identity: dict[str, Any] | None
    lease_owner: str | None
    release_id: str | None
    reason: str | None
    observed_at: str | None
    attempt: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _request_from_payload(payload: dict[str, Any]) -> RuntimeControlRequest:
    return RuntimeControlRequest(
        request_id=str(payload["request_id"]),
        action=str(payload["action"]).upper(),
        requested_by=str(payload.get("requested_by") or "owner"),
        requested_at=str(payload.get("requested_at") or ""),
        expected_release=(
            str(payload["expected_release"])
            if payload.get("expected_release") not in (None, "") else None
        ),
        expires_at=(
            str(payload["expires_at"])
            if payload.get("expires_at") not in (None, "") else None
        ),
    )


def request_is_stale(
    request: RuntimeControlRequest,
    *,
    now=None,
) -> bool:
    """Return whether an owner request has exceeded its bounded lifetime.

    Historical requests may not have an ``expires_at`` field. For those
    records, use the original request timestamp and the same default TTL. A
    malformed timestamp is treated as not stale so an unreadable record is
    surfaced for reconciliation instead of silently discarded.
    """

    try:
        expires_at = parse(request.expires_at) if request.expires_at else (
            parse(request.requested_at) + timedelta(seconds=REQUEST_STALE_AFTER_SECONDS)
        )
    except (TypeError, ValueError, OverflowError):
        return False
    return expires_at <= (now or utc_now())


def _request_events(database: FoundryDatabase) -> list[tuple[RuntimeControlRequest, Event]]:
    requests: list[tuple[RuntimeControlRequest, Event]] = []
    for event in _query_events(
        database, "event_type = ?", (CONTROL_REQUESTED,), order="ASC"):
        if event.event_type != CONTROL_REQUESTED:
            continue
        try:
            request = _request_from_payload(event.payload)
        except (KeyError, TypeError, ValueError):
            continue
        requests.append((request, event))
    return requests


def _query_events(
    database: FoundryDatabase,
    predicate: str,
    parameters: tuple[Any, ...],
    *,
    order: str = "DESC",
    limit: int | None = None,
) -> list[Event]:
    """Read only the small control/runtime slice of a potentially huge ledger."""

    sql = f"SELECT * FROM events WHERE {predicate} ORDER BY sequence {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    connection = database.connect(readonly=True)
    try:
        rows = connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()
    return [database._row_to_event(row) for row in rows]


def latest_request(database: FoundryDatabase) -> RuntimeControlRequest | None:
    """Return the newest well-formed owner request, if one exists."""

    requests = _request_events(database)
    return requests[-1][0] if requests else None


def _find_request_event(
    database: FoundryDatabase, request_id: str,
) -> Event | None:
    for event in _query_events(
        database, "event_type = ?", (CONTROL_REQUESTED,)):
        if event.event_type == CONTROL_REQUESTED and event.payload.get("request_id") == request_id:
            return event
    return None


def submit_request(
    database: FoundryDatabase,
    action: str,
    *,
    request_id: str | None = None,
    requested_by: str = "owner",
    expected_release: str | None = None,
) -> RuntimeControlRequest:
    """Persist an owner request exactly once.

    Repeating the same request ID with the same payload returns the original
    request. Reusing it for a different action or owner fails closed.
    """

    normalized_action = str(action).upper().strip()
    if normalized_action not in ACTIONS:
        raise ValueError(f"unknown runtime control action: {action!r}")
    normalized_by = str(requested_by).strip() or "owner"
    normalized_id = str(request_id or uuid.uuid4()).strip()
    if not normalized_id or len(normalized_id) > 128:
        raise ValueError("request_id must be non-empty and at most 128 characters")
    if any(char in normalized_id for char in "\r\n"):
        raise ValueError("request_id must not contain newlines")

    existing = _find_request_event(database, normalized_id)
    if existing is not None:
        prior = _request_from_payload(existing.payload)
        if (
            prior.action != normalized_action
            or prior.requested_by != normalized_by
            or prior.expected_release != expected_release
        ):
            raise EventConflict(
                f"runtime control request conflict for {normalized_id}")
        return prior

    request = RuntimeControlRequest(
        request_id=normalized_id,
        action=normalized_action,
        requested_by=normalized_by,
        requested_at=isoformat(),
        expected_release=(str(expected_release) if expected_release else None),
        expires_at=isoformat(utc_now() + timedelta(seconds=REQUEST_STALE_AFTER_SECONDS)),
    )
    event = Event.create(
        CONTROL_REQUESTED,
        request.to_json(),
        source_type="owner",
        source_id=normalized_by,
        idempotency_key=f"{CONTROL_REQUESTED}:{normalized_id}",
    )
    try:
        database.append(event)
    except EventConflict:
        concurrent = _find_request_event(database, normalized_id)
        if concurrent is None:
            raise
        prior = _request_from_payload(concurrent.payload)
        if (
            prior.action != normalized_action
            or prior.requested_by != normalized_by
            or prior.expected_release != request.expected_release
        ):
            raise
        return prior
    return request


def record_claim(
    database: FoundryDatabase,
    *,
    instance_id: str,
    pid: int,
    creation_identity: dict[str, Any],
    request_id: str | None = None,
    release_id: str | None = None,
    generation: int = 1,
) -> None:
    """Record intent before acquiring the singleton lease.

    The claim lets a later Supervisor prove that a lease owner died between
    lease acquisition and ``supervisor.started``. It contains no credentials.
    """

    payload = {
        "instance_id": instance_id,
        "pid": int(pid),
        "creation_identity": dict(creation_identity),
        "request_id": request_id,
        "release_id": release_id,
        "generation": max(1, int(generation)),
    }
    database.append(Event.create(
        CONTROL_CLAIMING,
        payload,
        source_type="supervisor",
        source_id=instance_id,
        idempotency_key=f"{CONTROL_CLAIMING}:{instance_id}:{payload['generation']}",
    ))


def record_resolution(
    database: FoundryDatabase,
    request: RuntimeControlRequest,
    *,
    status: str,
    phase: str,
    reason: str | None = None,
    instance_id: str | None = None,
    pid: int | None = None,
    creation_identity: dict[str, Any] | None = None,
    release_id: str | None = None,
    attempt: int = 0,
    request_status: str | None = None,
) -> None:
    """Record an idempotent request outcome or restart phase."""

    normalized_status = str(status).upper()
    if normalized_status not in RUNTIME_STATES:
        raise ValueError(f"unknown runtime state: {status!r}")
    if not phase or any(char in phase for char in "\r\n"):
        raise ValueError("resolution phase must be a non-empty single line")
    normalized_request_status = (
        str(request_status).upper() if request_status is not None else None
    )
    if normalized_request_status is not None and normalized_request_status not in REQUEST_STATUSES:
        raise ValueError(f"unknown owner request status: {request_status!r}")
    payload: dict[str, Any] = {
        "request_id": request.request_id,
        "action": request.action,
        "status": normalized_status,
        "phase": phase,
        "reason": reason,
        "instance_id": instance_id,
        "pid": int(pid) if pid is not None else None,
        "creation_identity": dict(creation_identity) if creation_identity else None,
        "release_id": release_id,
        "attempt": max(0, int(attempt)),
        "request_status": normalized_request_status,
    }
    key = (
        f"{CONTROL_RESOLVED}:{request.request_id}:{phase}:{normalized_status}:"
        f"{instance_id or '-'}:{payload['attempt']}"
    )
    existing = next(
        (event for event in _query_events(
            database, "event_type = ?", (CONTROL_RESOLVED,))
         if event.idempotency_key == key),
        None,
    )
    if existing is not None:
        if existing.event_type != CONTROL_RESOLVED:
            raise EventConflict(f"runtime control resolution key collision: {key}")
        if existing.payload != payload:
            raise EventConflict(f"runtime control resolution conflict: {key}")
        return
    database.append(Event.create(
        CONTROL_RESOLVED,
        payload,
        source_type="supervisor",
        source_id=instance_id or "runtime-control",
        idempotency_key=key,
    ))


def record_lease_reclaimed(
    database: FoundryDatabase,
    *,
    owner_id: str,
    pid: int,
    reason: str,
) -> None:
    key = f"{CONTROL_LEASE_RECLAIMED}:{owner_id}:{pid}:{reason}"
    existing = next(
        (event for event in _query_events(
            database, "event_type = ?", (CONTROL_LEASE_RECLAIMED,))
         if event.idempotency_key == key),
        None,
    )
    if existing is not None:
        return
    database.append(Event.create(
        CONTROL_LEASE_RECLAIMED,
        {"owner_id": owner_id, "pid": int(pid), "reason": reason},
        source_type="supervisor",
        source_id="lease-reconciler",
        idempotency_key=key,
    ))


def _latest_resolution(
    database: FoundryDatabase, request_id: str,
) -> Event | None:
    for event in _query_events(
        database, "event_type = ?", (CONTROL_RESOLVED,)):
        if event.event_type == CONTROL_RESOLVED and event.payload.get("request_id") == request_id:
            return event
    return None


def latest_resolution(
    database: FoundryDatabase, request_id: str,
) -> Event | None:
    """Public read: the newest durable resolution for one request id."""
    return _latest_resolution(database, request_id)


def _latest_runtime_event(database: FoundryDatabase) -> Event | None:
    event_types = (
        "supervisor.started", "supervisor.heartbeat", "supervisor.degraded",
        "supervisor.stopped", "emergency_stop.requested",
        "emergency_stop.frozen", "emergency_stop.reconciled",
    )
    placeholders = ",".join("?" for _ in event_types)
    events = _query_events(
        database, f"event_type IN ({placeholders})", event_types, limit=1)
    return events[0] if events else None


def _resolution_is_current(
    resolution: Event | None,
    runtime_event: Event | None,
) -> bool:
    """Use a resolution for runtime status only until a newer runtime event."""

    if resolution is None or runtime_event is None:
        return resolution is not None
    try:
        return parse(resolution.recorded_at) >= parse(runtime_event.recorded_at)
    except (TypeError, ValueError, OverflowError):
        # A malformed ledger timestamp must not erase a valid resolution. The
        # integrity/reconciliation view will still surface the malformed row.
        return True


def _latest_identity(database: FoundryDatabase, instance_id: str | None) -> dict[str, Any]:
    if not instance_id:
        return {}
    event_types = ("supervisor.started", "supervisor.heartbeat", CONTROL_CLAIMING)
    placeholders = ",".join("?" for _ in event_types)
    pattern = f'%"instance_id":"{instance_id}"%'
    for event in _query_events(
        database,
        f"event_type IN ({placeholders}) AND payload_json LIKE ?",
        (*event_types, pattern),
        limit=1,
    ):
        payload = event.payload
        if payload.get("pid") is not None:
            return payload
    return {}


def latest_process_identity(
    database: FoundryDatabase, instance_id: str,
) -> dict[str, Any] | None:
    """Return the newest durable identity for one Supervisor instance."""

    payload = _latest_identity(database, instance_id)
    return payload or None


def latest_generation(database: FoundryDatabase, instance_id: str) -> int:
    """Return the highest durable Supervisor generation for an instance.

    ``Supervisor`` objects are normally short-lived process instances, but a
    caller may intentionally reuse an instance ID during a controlled
    restart. Generation allocation must therefore survive object loss so an
    old idempotency key cannot collide with the new lifecycle.
    """

    event_types = (
        "supervisor.started", "supervisor.heartbeat", "supervisor.stopped",
        CONTROL_CLAIMING,
    )
    placeholders = ",".join("?" for _ in event_types)
    pattern = f'%%"instance_id":"{instance_id}"%%'
    highest = 0
    for event in _query_events(
        database,
        f"event_type IN ({placeholders}) AND payload_json LIKE ?",
        (*event_types, pattern),
    ):
        if event.payload.get("instance_id") != instance_id:
            continue
        try:
            highest = max(highest, int(event.payload.get("generation") or 0))
        except (TypeError, ValueError):
            continue
    return highest


def _pid_matches(pid: int | None, creation_identity: dict[str, Any] | None) -> bool | None:
    if pid is None or pid <= 0:
        return None
    if not pid_alive(pid):
        return False
    expected_time = (creation_identity or {}).get("create_time")
    if expected_time is None:
        return None
    observed = process_creation_identity(pid)
    observed_time = observed.get("create_time")
    if observed_time is None:
        return None
    return observed_time == expected_time


def _lease(database: FoundryDatabase) -> tuple[str | None, bool]:
    try:
        connection = database.connect(readonly=True)
        try:
            row = connection.execute(
                "SELECT * FROM leases WHERE lease_key = ?",
                ("supervisor:singleton",),
            ).fetchone()
        finally:
            connection.close()
    except (OSError, KeyError):
        return None, False
    if row is None:
        return None, False
    try:
        live = parse(str(row["expires_at"])) > utc_now()
    except (TypeError, ValueError, OverflowError):
        return str(row["owner_id"]), False
    return str(row["owner_id"]), live


def inspect_control(
    database: FoundryDatabase,
    *,
    stop_path: str | None = None,
    stale_after_seconds: float = 120.0,
) -> RuntimeControlState:
    """Derive operator truth from durable events, lease, and process identity."""

    request = latest_request(database)
    resolution = _latest_resolution(database, request.request_id) if request else None
    runtime_event = _latest_runtime_event(database)
    lease_owner, lease_live = _lease(database)
    payload = runtime_event.payload if runtime_event else {}
    instance_id = payload.get("instance_id")
    identity = _latest_identity(database, str(instance_id) if instance_id else None)
    pid = int(identity["pid"]) if identity.get("pid") is not None else None
    creation = identity.get("creation_identity")
    if not isinstance(creation, dict):
        creation = None
    release_id = identity.get("release_id")

    event_status = {
        "supervisor.started": "RUNNING",
        "supervisor.heartbeat": "RUNNING",
        "supervisor.degraded": "DEGRADED",
        "supervisor.stopped": "STOPPED",
        "emergency_stop.requested": "BLOCKED",
        "emergency_stop.frozen": "BLOCKED",
        "emergency_stop.reconciled": "BLOCKED",
    }.get(runtime_event.event_type if runtime_event else "", "STOPPED")
    status = event_status
    reason = payload.get("reason")
    attempt = 0
    request_status: str | None = None
    if resolution is not None:
        resolved = resolution.payload
        resolution_status = str(resolved.get("status") or "UNKNOWN").upper()
        if _resolution_is_current(resolution, runtime_event):
            status = resolution_status
            reason = resolved.get("reason") or reason
        attempt = int(resolved.get("attempt") or 0)
        phase = str(resolved.get("phase") or "")
        request_status = str(resolved.get("request_status") or "").upper() or None
        if request_status is None:
            request_status = "PENDING" if phase == "stopped_for_restart" else (
                "BLOCKED" if status == "BLOCKED" else "APPLIED"
            )
        if request_status == "STALE":
            reason = resolved.get("reason") or reason

    if status == "RUNNING":
        identity_match = _pid_matches(pid, creation)
        if identity_match is False:
            status = "UNKNOWN"
            reason = "supervisor process is missing or PID identity was reused"
        elif identity_match is None:
            status = "UNKNOWN"
            reason = "supervisor process identity is unavailable"
        elif not lease_live or (lease_owner is not None and lease_owner != instance_id):
            status = "UNKNOWN"
            reason = "supervisor singleton lease is missing or owned by another instance"
        elif runtime_event is not None:
            try:
                age = (utc_now() - parse(runtime_event.recorded_at)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                age = stale_after_seconds + 1
            if age > stale_after_seconds:
                status = "DEGRADED"
                reason = "supervisor heartbeat is stale"

    if request is not None and request_status is None:
        if request_is_stale(request):
            request_status = "STALE"
            reason = reason or "owner request expired before application"
        else:
            request_status = "PENDING"
            if request.action == "STOP" and status == "STOPPED":
                reason = reason or "stop request is waiting for reconciliation"
            elif request.action == "STOP" and status == "RUNNING":
                reason = reason or "stop request is waiting for the owner heartbeat"
            elif status == "BLOCKED":
                request_status = "BLOCKED"
            elif status == "RUNNING" and request.action in {"START", "RESTART"}:
                request_status = "APPLIED"
            else:
                status = "UNKNOWN"
                reason = reason or "owner request has no durable process outcome"

    if stop_path and EmergencyStop(stop_path).active() and status == "RUNNING":
        status = "BLOCKED"
        reason = "emergency STOP sentinel is active"

    if status not in RUNTIME_STATES:
        status = "UNKNOWN"
    return RuntimeControlState(
        status=status,
        desired_action=request.action if request else None,
        request_id=request.request_id if request else None,
        request_status=request_status,
        instance_id=str(instance_id) if instance_id else None,
        pid=pid,
        creation_identity=creation,
        lease_owner=lease_owner if lease_live else None,
        release_id=str(release_id) if release_id else None,
        reason=str(reason) if reason else None,
        observed_at=runtime_event.recorded_at if runtime_event else None,
        attempt=attempt,
    )


def reconcile_control(
    database: FoundryDatabase,
    *,
    stop_path: str | None = None,
) -> RuntimeControlState:
    """Close only safe request gaps; never invent a successful process run."""

    request = latest_request(database)
    if request is None:
        return inspect_control(database, stop_path=stop_path)
    resolution = _latest_resolution(database, request.request_id)
    state = inspect_control(database, stop_path=stop_path)
    if resolution is None and request_is_stale(request):
        record_resolution(
            database, request, status=state.status, phase="stale_request",
            request_status="STALE",
            reason="owner request expired before application")
    elif resolution is None and state.status == "STOPPED" and request.action == "STOP":
        record_resolution(
            database, request, status="STOPPED", phase="already_stopped",
            reason="supervisor was already stopped")
    elif resolution is None and state.status == "RUNNING" and request.action in {"START", "RESTART"}:
        record_resolution(
            database, request, status="RUNNING", phase="recovered",
            reason="durable supervisor event proves the request outcome",
            instance_id=state.instance_id, pid=state.pid,
            creation_identity=state.creation_identity, release_id=state.release_id)
    return inspect_control(database, stop_path=stop_path)


def current_process_identity() -> tuple[int, dict[str, Any]]:
    """Return the current process identity used in supervisor events."""

    import os

    pid = os.getpid()
    return pid, process_creation_identity(pid)
