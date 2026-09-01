"""Durable Supervisor-owned proving-window contract.

The proving window records registration, checkpoints, and close as
append-only ledger events on the existing ``FoundryDatabase`` / ``Event``
APIs. No elapsed time is fabricated: healthy-day counts are derived from real
``supervisor.heartbeat``, ``supervisor.degraded``, and owner STOP/PAUSE
evidence in the ledger, and verified-mission counts are derived from durable
mission / evidence / accepted-verdict state. Provider, voice, UI, and external
connector effects are never claimed here; they remain ``NOT_TESTED``
boundaries in every status snapshot.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .clock import isoformat, parse, utc_now
from .database import FoundryDatabase
from .events import Event

SUPERVISOR = "supervisor"
DEFAULT_REQUIRED_DAYS = 7
DEFAULT_REQUIRED_MISSIONS = 10
_STOP_TYPES = frozenset({"STOP_EVERYTHING", "PAUSE"})
_SCOPED_STOP_TYPES = frozenset({"VETO_ACTION", "STOP_MISSION"})


class ProvingError(ValueError):
    pass


def _as_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return utc_now()
    return value if isinstance(value, datetime) else parse(value)


def _require_supervisor(controller: str) -> None:
    if controller != SUPERVISOR:
        raise ProvingError(
            f"proving windows are Supervisor-owned; controller {controller!r} refused")


def _window_events(database: FoundryDatabase, window_id: str) -> list[Event]:
    return [
        event for event in database.events()
        if event.payload.get("window_id") == window_id
    ]


def _registered(window_id: str, events: list[Event]) -> Event:
    matches = [
        event for event in events
        if event.event_type == "proving.window_registered"
    ]
    if not matches:
        raise ProvingError(f"unknown proving window: {window_id}")
    return matches[-1]


def _closed(events: list[Event]) -> Event | None:
    matches = [
        event for event in events
        if event.event_type == "proving.window_closed"
    ]
    return matches[-1] if matches else None


def _active_stop_dates(
    events: list[Event],
    horizon: datetime.date,
    *,
    window_id: str | None = None,
    window_mission_id: str | None = None,
) -> dict[datetime.date, str]:
    """Dates covered by a still-active owner stop/veto directive.

    ``STOP_EVERYTHING`` / ``PAUSE`` are global. ``VETO_ACTION`` /
    ``STOP_MISSION`` must be authenticated and target the window's global /
    mission / window identity; unknown target forms are not treated as
    matching (fail closed by refusing to count the window day as healthy
    only when the target is ambiguous is not possible, so ambiguous targets
    are deliberately ignored and the gate stays conservative: scoped
    directives must name the window to exclude it). A directive covers a
    date when it was recorded on or before that date and was not revoked
    before the end of that date (any overlap fails the day closed).
    """
    recorded: dict[str, tuple[Event, str]] = {}
    revoked_at: dict[str, datetime] = {}
    for event in events:
        payload = event.payload
        if event.event_type == "owner_directive.recorded":
            command_type = str(payload.get("command_type") or "")
            if command_type in _STOP_TYPES or command_type in _SCOPED_STOP_TYPES:
                recorded[payload["directive_id"]] = (event, command_type)
        elif event.event_type == "owner_directive.revoked":
            revoked_at[payload["directive_id"]] = parse(event.occurred_at)
    identities = {"GLOBAL", "proving", "proving-window", "proving_window"}
    if window_id:
        identities.update({
            window_id, f"window:{window_id}", f"proving:{window_id}"})
    if window_mission_id:
        identities.update({
            window_mission_id, f"mission:{window_mission_id}"})
    covered: dict[datetime.date, str] = {}
    for directive_id, (event, command_type) in recorded.items():
        payload = event.payload
        if command_type in _SCOPED_STOP_TYPES:
            if not payload.get("authenticated"):
                continue
            target = str(payload.get("target") or "").strip() or "GLOBAL"
            if target not in identities:
                continue
            reason = "veto/stop-mission directive"
        else:
            reason = "stop/pause directive"
        recorded_day = parse(event.occurred_at).date()
        revoked = revoked_at.get(directive_id)
        end_day = (revoked - timedelta(seconds=1)).date() if revoked is not None else None
        day = recorded_day
        while day <= (end_day or horizon):
            covered[day] = reason
            day += timedelta(days=1)
    return covered


def _day_health(
    events: list[Event],
    day: datetime.date,
    stop_dates: dict[datetime.date, str],
) -> tuple[bool, str]:
    day_events = [
        event for event in events
        if parse(event.occurred_at).date() == day
    ]
    if not any(event.event_type == "supervisor.heartbeat" for event in day_events):
        return False, "no heartbeat"
    if any(event.event_type == "supervisor.degraded" for event in day_events):
        return False, "degraded"
    if any(event.event_type == "emergency_stop.frozen" for event in day_events):
        return False, "stop frozen"
    if any(event.event_type == "supervisor.stopped" for event in day_events):
        return False, "supervisor stopped"
    if day in stop_dates:
        return False, stop_dates[day]
    return True, "healthy"


def healthy_days(
    database: FoundryDatabase,
    window_id: str,
    *,
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Fully elapsed healthy UTC calendar days derived from ledger evidence.

    A day counts only when the calendar day has actually completed
    (``day < observed day``), at least one real ``supervisor.heartbeat``
    exists on it, and no degraded / STOP / PAUSE / authenticated VETO_ACTION
    or STOP_MISSION (targeting the window) evidence overlaps it.
    """
    all_events = database.events()
    window_events = [
        event for event in all_events
        if event.payload.get("window_id") == window_id
    ]
    registered = _registered(window_id, window_events)
    observed = _as_datetime(observed_at)
    start = parse(registered.payload["started_at"]).date()
    end = observed.date()
    stop_dates = _active_stop_dates(
        all_events, end,
        window_id=window_id,
        window_mission_id=registered.payload.get("mission_id"),
    )
    dates: list[str] = []
    excluded: list[dict[str, str]] = []
    day = start
    while day < end:
        healthy, reason = _day_health(all_events, day, stop_dates)
        if healthy:
            dates.append(day.isoformat())
        else:
            excluded.append({"date": day.isoformat(), "reason": reason})
        day += timedelta(days=1)
    return {
        "count": len(dates),
        "dates": dates,
        "excluded": excluded,
    }


def verified_mission_count(
    database: FoundryDatabase,
    *,
    started_at: datetime | str | None = None,
) -> int:
    """Missions with durable completion, evidence, and an accepted PASS.

    A mission counts only when it exists in the ledger, has a durable
    completion event (``mission.completed`` or ``mission.state_changed`` to
    COMPLETE), has at least one evidence bundle, and has at least one PASS
    verdict with an AcceptanceGate receipt whose verifier run is distinct
    from the builder run (a worker never verifies itself). When
    ``started_at`` is provided, the mission's creation, completion, evidence,
    and accepted verdict must all be recorded on or after that timestamp, so
    a proving window never reuses work that predates it.
    """
    started = _as_datetime(started_at) if started_at is not None else None
    created_at: dict[str, datetime] = {}
    completed_at: dict[str, datetime] = {}
    evidence_at: dict[str, datetime] = {}
    verdict_builder: dict[str, str | None] = {}
    verdict_verifier: dict[str, str | None] = {}
    verdict_status: dict[str, str] = {}
    accepted_at: dict[str, datetime] = {}
    for event in database.events():
        payload = event.payload
        occurred = parse(event.occurred_at)
        if event.event_type == "mission.created":
            mission_id = str(payload["mission_id"])
            created_at[mission_id] = occurred
        elif event.event_type == "mission.completed":
            mission_id = str(payload["mission_id"])
            completed_at[mission_id] = occurred
        elif (event.event_type == "mission.state_changed"
              and payload.get("state") == "COMPLETE"):
            mission_id = str(payload["mission_id"])
            completed_at[mission_id] = occurred
        elif event.event_type == "evidence.created":
            mission_id = str(payload["mission_id"])
            evidence_at[mission_id] = occurred
        elif event.event_type == "verification.started":
            verdict_id = str(payload["verdict_id"])
            verdict_builder[verdict_id] = payload.get("builder_run_id")
            verdict_verifier[verdict_id] = payload.get("verifier_run_id")
            verdict_status[verdict_id] = "STARTED"
        elif event.event_type == "verification.passed":
            verdict_id = str(payload["verdict_id"])
            verdict_status[verdict_id] = "PASS"
        elif event.event_type == "verification.accepted":
            verdict_id = str(payload.get("verdict_id") or "")
            if payload.get("decision") == "ACCEPT" and verdict_id:
                accepted_at[verdict_id] = occurred
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT v.verdict_id, v.mission_id FROM verification_verdicts v
               JOIN evidence_bundles e ON e.mission_id = v.mission_id
               WHERE v.status = 'PASS' AND v.verdict = 'PASS'"""
        ).fetchall()
    counted: set[str] = set()
    count = 0
    for row in rows:
        verdict_id = str(row["verdict_id"])
        mission_id = str(row["mission_id"])
        if verdict_id not in verdict_status or verdict_status[verdict_id] != "PASS":
            continue
        if verdict_id not in accepted_at:
            continue
        if not verdict_verifier.get(verdict_id):
            continue
        builder = verdict_builder.get(verdict_id)
        if builder and builder == verdict_verifier[verdict_id]:
            continue
        if mission_id in counted:
            continue
        if mission_id not in completed_at:
            continue
        if started is not None:
            created = created_at.get(mission_id)
            if created is None or created < started:
                continue
            if completed_at[mission_id] < started:
                continue
            evidence = evidence_at.get(mission_id)
            if evidence is None or evidence < started:
                continue
            verdict = accepted_at.get(verdict_id)
            if verdict is None or verdict < started:
                continue
        counted.add(mission_id)
        count += 1
    return count


def window_status(
    database: FoundryDatabase,
    window_id: str,
    *,
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Replay-derived proving-window state (restart-safe, never chat truth)."""
    events = _window_events(database, window_id)
    registered = _registered(window_id, events)
    payload = registered.payload
    required_days = int(payload["required_days"])
    required_missions = int(payload["required_missions"])
    observed = _as_datetime(observed_at)
    days = healthy_days(database, window_id, observed_at=observed)
    missions = verified_mission_count(
        database, started_at=payload["started_at"])
    met = days["count"] >= required_days and missions >= required_missions
    close = _closed(events)
    state = close.payload["outcome"] if close else "OPEN"
    if state == "OPEN":
        verdict = "READY" if met else "WAITING"
    else:
        verdict = state
    return {
        "window_id": window_id,
        "started_at": payload["started_at"],
        "registered_at": payload["registered_at"],
        "controller": payload["controller"],
        "mission_id": payload.get("mission_id"),
        "required_days": required_days,
        "required_missions": required_missions,
        "elapsed_days": len(days["dates"]) + len(days["excluded"]),
        "healthy_days": days["count"],
        "healthy_day_dates": days["dates"],
        "excluded_days": days["excluded"],
        "verified_mission_count": missions,
        "met": met,
        "state": state,
        "verdict": verdict,
        "close": {
            "outcome": close.payload["outcome"],
            "reason": close.payload.get("reason"),
            "closed_at": close.payload["closed_at"],
        } if close else None,
        "checkpoints": sum(
            1 for event in events
            if event.event_type == "proving.window_checkpoint"),
        "observed_at": isoformat(observed),
        "external_effect_boundaries": {
            "provider": "NOT_TESTED",
            "voice": "NOT_TESTED",
            "ui": "NOT_TESTED",
            "connectors": "NOT_TESTED",
        },
    }


def register_window(
    database: FoundryDatabase,
    window_id: str,
    *,
    required_days: int = DEFAULT_REQUIRED_DAYS,
    required_missions: int = DEFAULT_REQUIRED_MISSIONS,
    started_at: str | None = None,
    mission_id: str | None = None,
    controller: str = SUPERVISOR,
) -> Event:
    """Register a Supervisor-owned proving window (append-only, idempotent)."""
    _require_supervisor(controller)
    if not window_id:
        raise ProvingError("window_id is required")
    if required_days < 1 or required_missions < 1:
        raise ProvingError("required_days and required_missions must be positive")
    started_at = started_at or isoformat()
    event = Event.create(
        "proving.window_registered",
        {
            "window_id": window_id,
            "controller": controller,
            "started_at": started_at,
            "registered_at": started_at,
            "required_days": required_days,
            "required_missions": required_missions,
            "mission_id": mission_id,
        },
        source_type=SUPERVISOR,
        source_id=controller,
        idempotency_key=f"proving.window_registered:{window_id}",
    )
    database.append(event)
    return event


def checkpoint_window(
    database: FoundryDatabase,
    window_id: str,
    *,
    observed_at: datetime | str | None = None,
    checkpoint_id: str | None = None,
    note: str | None = None,
    controller: str = SUPERVISOR,
) -> Event:
    """Record a durable state snapshot for a still-open window."""
    _require_supervisor(controller)
    events = _window_events(database, window_id)
    _registered(window_id, events)
    if _closed(events) is not None:
        raise ProvingError(f"window {window_id} is already closed")
    observed = _as_datetime(observed_at)
    snapshot = window_status(database, window_id, observed_at=observed)
    event = Event.create(
        "proving.window_checkpoint",
        {
            "window_id": window_id,
            "controller": controller,
            "observed_at": isoformat(observed),
            "checkpoint_id": checkpoint_id or isoformat(observed),
            "snapshot": snapshot,
            "note": note,
        },
        source_type=SUPERVISOR,
        source_id=controller,
        idempotency_key=(
            f"proving.window_checkpoint:{window_id}:"
            f"{checkpoint_id or isoformat(observed)}"),
    )
    database.append(event)
    return event


def close_window(
    database: FoundryDatabase,
    window_id: str,
    *,
    outcome: str | None = None,
    reason: str | None = None,
    observed_at: datetime | str | None = None,
    controller: str = SUPERVISOR,
) -> Event:
    """Close the window terminally: PASSED (criteria met) or FAILED/SUSPENDED.

    PASSED refuses to close when the real criteria are not yet met. FAILED
    and SUSPENDED are explicit Supervisor decisions and require a reason.
    """
    _require_supervisor(controller)
    events = _window_events(database, window_id)
    _registered(window_id, events)
    if _closed(events) is not None:
        raise ProvingError(f"window {window_id} is already closed")
    observed = _as_datetime(observed_at)
    status = window_status(database, window_id, observed_at=observed)
    outcome = outcome or ("PASSED" if status["met"] else None)
    if outcome is None:
        raise ProvingError(
            f"window {window_id} does not meet the proving criteria yet "
            f"(healthy_days={status['healthy_days']}/"
            f"{status['required_days']}, missions="
            f"{status['verified_mission_count']}/"
            f"{status['required_missions']}); window stays open (WAITING)")
    if outcome == "PASSED" and not status["met"]:
        raise ProvingError(
            f"refusing PASSED close: window {window_id} criteria not met "
            "(healthy_days and verified missions are real ledger evidence)")
    if outcome not in {"PASSED", "FAILED", "SUSPENDED"}:
        raise ProvingError(f"invalid proving-window outcome: {outcome!r}")
    if outcome in {"FAILED", "SUSPENDED"} and not reason:
        raise ProvingError(f"{outcome} close requires a reason")
    event = Event.create(
        "proving.window_closed",
        {
            "window_id": window_id,
            "controller": controller,
            "outcome": outcome,
            "reason": reason,
            "closed_at": isoformat(observed),
            "healthy_days": status["healthy_days"],
            "verified_mission_count": status["verified_mission_count"],
        },
        source_type=SUPERVISOR,
        source_id=controller,
        idempotency_key=f"proving.window_closed:{window_id}:{outcome}",
    )
    database.append(event)
    return event
