"""Event-derived owner-directive truth for the V4 product surface.

The existing ``directives`` table remains the deterministic enforcement
projection used by the broker.  This module is a richer, read-only projection
for owner-facing management and briefing code.  It deliberately reconstructs
state from the append-only ledger so a stale or manually edited projection
cannot become a second control authority.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .clock import parse, utc_now
from .database import FoundryDatabase
from .events import Event


OWNER_COMMANDS = frozenset({
    "GOAL",
    "CONSTRAINT",
    "ADD_CONSTRAINT",
    "VETO_ACTION",
    "STOP_MISSION",
    "STOP_EVERYTHING",
    "PAUSE",
    "RESUME",
    "REPRIORITIZE",
    "REDIRECT",
    "RESTART_RECOVERY",
})

DIRECTIVE_STATES = frozenset({"ACTIVE", "REVOKED", "SUPERSEDED", "STALE", "UNKNOWN"})


@dataclass(frozen=True, slots=True)
class OwnerDirectiveRecord:
    """A reconstructible owner directive and its honest lifecycle state."""

    directive_id: str
    command_type: str | None
    target: str | None
    exact_text: str | None
    state: str
    authenticated: bool | None
    source_type: str
    source_id: str
    owner_id: str | None
    nonce: str | None
    command_hash: str | None
    event_id: str
    correlation_id: str
    causation_id: str | None
    occurred_at: str
    recorded_at: str
    supersedes: str | None
    superseded_by: str | None
    revoked_at: str | None
    expires_at: str | None
    target_status: str
    unknown_fields: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.state == "ACTIVE"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe owner-facing record."""
        return {
            "directive_id": self.directive_id,
            "command_type": self.command_type,
            "target": self.target,
            "exact_text": self.exact_text,
            "state": self.state,
            "status": self.state,
            "active": self.active,
            "authenticated": self.authenticated,
            "source": {
                "type": self.source_type,
                "id": self.source_id,
                "owner_id": self.owner_id,
            },
            "nonce": self.nonce,
            "command_hash": self.command_hash,
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "revoked_at": self.revoked_at,
            "expires_at": self.expires_at,
            "target_status": self.target_status,
            "unknown_fields": list(self.unknown_fields),
        }


def _record_values(record: OwnerDirectiveRecord) -> dict[str, Any]:
    return {
        field: getattr(record, field)
        for field in record.__dataclass_fields__
    }


def _replace_record(record: OwnerDirectiveRecord, **changes: Any) -> OwnerDirectiveRecord:
    values = _record_values(record)
    values.update(changes)
    return OwnerDirectiveRecord(**values)


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return _text(value)


def _command_hash(
    command_type: str,
    target: str | None,
    exact_text: str,
    nonce: str,
) -> str | None:
    """Calculate the admission hash without importing the owner service at module load."""
    try:
        # Import lazily: owner_controls owns the public signing helper and
        # imports this module's command set in some candidate integrations.
        from .owner_controls import canonical_command

        return hashlib.sha256(
            canonical_command(command_type, target, exact_text, nonce)
        ).hexdigest()
    except (TypeError, ValueError):
        return None


def _target_status(database: FoundryDatabase, target: str | None) -> str:
    """Resolve target identity without treating an unknown target as global."""
    if target is None or not target.strip():
        return "UNKNOWN"
    if target == "GLOBAL":
        return "KNOWN"

    candidates = (
        ("missions", "mission_id", target),
        ("resources", "resource_id", target),
        ("systems", "system_id", target),
    )
    try:
        with database.connect(readonly=True) as connection:
            for table, key, value in candidates:
                try:
                    row = connection.execute(
                        f"SELECT 1 FROM {table} WHERE {key} = ? LIMIT 1",
                        (value,),
                    ).fetchone()
                except Exception:  # additive tables may not exist on old state
                    row = None
                if row is not None:
                    return "KNOWN"
    except (OSError, KeyError):
        return "UNKNOWN"
    return "UNKNOWN"


def _unknown_fields(
    event: Event,
    payload: dict[str, Any],
    nonce_event: Event | None,
) -> tuple[str, ...]:
    unknown: list[str] = []
    command_type = payload.get("command_type")
    exact_text = payload.get("exact_text")
    target = payload.get("target")
    nonce = payload.get("nonce")
    supplied_hash = payload.get("command_hash")

    if event.source_type != "owner":
        unknown.append("source_identity")
    if payload.get("authenticated") is not True:
        unknown.append("authentication")
    if not isinstance(command_type, str) or command_type not in OWNER_COMMANDS:
        unknown.append("command_type")
    if not isinstance(exact_text, str) or not exact_text.strip():
        unknown.append("exact_text")
    if target is not None and not isinstance(target, str):
        unknown.append("target")
    if not isinstance(nonce, str) or not nonce.strip():
        unknown.append("nonce")
    if nonce_event is None:
        unknown.append("nonce_event")
    if not isinstance(supplied_hash, str) or not supplied_hash:
        unknown.append("command_hash")
    elif isinstance(command_type, str) and command_type in OWNER_COMMANDS \
            and isinstance(exact_text, str) and exact_text.strip() \
            and (target is None or isinstance(target, str)) \
            and isinstance(nonce, str) and nonce.strip():
        expected = _command_hash(command_type, target, exact_text, nonce)
        if expected is None or supplied_hash != expected:
            unknown.append("command_hash")
        if nonce_event is not None and nonce_event.payload.get("command_hash") != expected:
            unknown.append("nonce_command_hash")
    if nonce_event is not None:
        if event.causation_id != nonce_event.event_id:
            unknown.append("causation")
        if event.correlation_id != nonce_event.correlation_id:
            unknown.append("correlation")
    supersedes = payload.get("supersedes")
    if supersedes is not None and (not isinstance(supersedes, str) or not supersedes.strip()):
        unknown.append("supersedes")
    expires_at = payload.get("expires_at")
    if expires_at is not None:
        if not isinstance(expires_at, str) or not expires_at.strip():
            unknown.append("expires_at")
        else:
            try:
                parse(expires_at)
            except (TypeError, ValueError, OverflowError):
                unknown.append("expires_at")
    return tuple(dict.fromkeys(unknown))


def _event_record(
    database: FoundryDatabase,
    event: Event,
    sequence: int,
    nonce_events: dict[str, Event],
) -> OwnerDirectiveRecord:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw_id = payload.get("directive_id")
    directive_id = _text(raw_id) or f"unknown:{event.event_id}"
    command_type = payload.get("command_type") if isinstance(payload.get("command_type"), str) else None
    target = payload.get("target") if isinstance(payload.get("target"), str) else None
    exact_text = payload.get("exact_text") if isinstance(payload.get("exact_text"), str) else None
    nonce = payload.get("nonce") if isinstance(payload.get("nonce"), str) else None
    command_hash = payload.get("command_hash") if isinstance(payload.get("command_hash"), str) else None
    nonce_event = nonce_events.get(nonce) if nonce else None
    unknown = list(_unknown_fields(event, payload, nonce_event))
    if not raw_id:
        unknown.append("directive_id")
    if command_type == "RESUME":
        # RESUME is an authenticated lifecycle command, not an active hold.
        state = "UNKNOWN" if unknown else "REVOKED"
    else:
        state = "UNKNOWN" if unknown else "ACTIVE"
    expires_at = payload.get("expires_at") if isinstance(payload.get("expires_at"), str) else None
    return OwnerDirectiveRecord(
        directive_id=directive_id,
        command_type=command_type,
        target=target,
        exact_text=exact_text,
        state=state,
        authenticated=payload.get("authenticated") if isinstance(payload.get("authenticated"), bool) else None,
        source_type=event.source_type,
        source_id=event.source_id,
        owner_id=_optional_text(payload.get("owner_id")) or (
            event.source_id if event.source_type == "owner" else None
        ),
        nonce=nonce,
        command_hash=command_hash,
        event_id=event.event_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        supersedes=_optional_text(payload.get("supersedes")),
        superseded_by=None,
        revoked_at=None,
        expires_at=expires_at,
        target_status=_target_status(database, target),
        unknown_fields=tuple(dict.fromkeys(unknown)),
    )


def project_owner_directives(
    database: FoundryDatabase,
    *,
    now: datetime | str | None = None,
) -> list[OwnerDirectiveRecord]:
    """Reconstruct every owner directive and lifecycle transition from events.

    Invalid records remain visible as ``UNKNOWN`` with the missing fields
    listed; they are never silently promoted into owner authority.  The
    returned order is newest-first for product surfaces, while transition
    application follows ledger order for deterministic replay.
    """
    if now is None:
        now_value = utc_now()
    elif isinstance(now, str):
        now_value = parse(now)
    else:
        now_value = now

    nonce_events: dict[str, Event] = {}
    records: dict[str, OwnerDirectiveRecord] = {}
    order: list[str] = []

    for sequence, event in enumerate(database.events(readonly=True), start=1):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.event_type == "owner_control.nonce_used":
            nonce = payload.get("nonce")
            if event.source_type == "owner" and isinstance(nonce, str) and nonce.strip():
                nonce_events[nonce] = event
            continue
        if event.event_type == "owner_directive.recorded":
            record = _event_record(database, event, sequence, nonce_events)
            if record.directive_id in records:
                # A duplicate directive id is a visible integrity problem, not
                # a replacement of the first admitted owner intent.
                record = _replace_record(
                    record,
                    directive_id=f"duplicate:{record.directive_id}:{record.event_id}",
                    state="UNKNOWN",
                    unknown_fields=tuple(dict.fromkeys((*record.unknown_fields, "duplicate_directive_id"))),
                )
            records[record.directive_id] = record
            order.append(record.directive_id)
            continue
        if event.event_type == "owner_directive.revoked":
            directive_id = _text(payload.get("directive_id"))
            if directive_id and directive_id in records:
                prior = records[directive_id]
                records[directive_id] = _replace_record(
                    prior,
                    state="REVOKED",
                    revoked_at=event.occurred_at,
                )
            continue
        if event.event_type == "owner_directive.superseded":
            old_id = _text(payload.get("directive_id"))
            new_id = _text(payload.get("superseded_by"))
            if old_id and old_id in records:
                prior = records[old_id]
                records[old_id] = _replace_record(
                    prior,
                    state="SUPERSEDED",
                    superseded_by=new_id,
                )

    # A recorded directive may carry a supersedes pointer. Apply it after the
    # scan so replay remains deterministic even when old fixtures omit the
    # optional field or contain a forward reference.
    for directive_id in order:
        record = records[directive_id]
        if not record.supersedes:
            continue
        previous = records.get(record.supersedes)
        if previous is None:
            records[directive_id] = _replace_record(
                record,
                state="UNKNOWN",
                unknown_fields=tuple(dict.fromkeys((*record.unknown_fields, "supersedes_unknown_directive"))),
            )
            continue
        if record.state == "ACTIVE":
            records[record.supersedes] = _replace_record(
                previous,
                state="SUPERSEDED",
                superseded_by=record.directive_id,
            )

    # Expiry is product truth only when the timestamp is valid. Invalid expiry
    # remains UNKNOWN rather than becoming a healthy active directive.
    for directive_id in order:
        record = records[directive_id]
        if record.state != "ACTIVE" or not record.expires_at:
            continue
        try:
            expired = parse(record.expires_at) <= now_value
        except (TypeError, ValueError, OverflowError):
            expired = False
        if expired:
            records[directive_id] = _replace_record(record, state="STALE")

    return [records[directive_id] for directive_id in reversed(order)]


def active_owner_directives(
    database: FoundryDatabase,
    *,
    now: datetime | str | None = None,
) -> list[OwnerDirectiveRecord]:
    """Return only authenticated, non-expired, non-superseded directives."""
    return [record for record in project_owner_directives(database, now=now) if record.active]


def owner_directive_snapshot(
    database: FoundryDatabase,
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Bounded management projection used by Command Center and briefings."""
    records = project_owner_directives(database, now=now)
    return {
        "records": [record.to_dict() for record in records],
        "active": [record.to_dict() for record in records if record.active],
        "unknown": [record.to_dict() for record in records if record.state == "UNKNOWN"],
        "history_count": len(records),
        "active_count": sum(record.active for record in records),
    }
