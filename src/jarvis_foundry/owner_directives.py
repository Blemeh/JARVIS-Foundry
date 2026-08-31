"""Event-derived owner-directive truth for the V4 product surface.

The existing ``directives`` table remains the deterministic enforcement
projection used by the broker.  This module is a richer, read-only projection
for owner-facing management and briefing code.  It deliberately reconstructs
state from the append-only ledger so a stale or manually edited projection
cannot become a second control authority.
"""
from __future__ import annotations

import hashlib
import sqlite3
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
    "REVOKE",
    "CANCEL_MISSION",
    "GATE_REEVALUATE",
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
    metadata: dict[str, Any]
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
            "metadata": self.metadata,
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
    *,
    supersedes: str | None = None,
    expires_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Calculate the admission hash without importing the owner service at module load."""
    try:
        # Import lazily: owner_controls owns the public signing helper and
        # imports this module's command set in some candidate integrations.
        from .owner_controls import canonical_command

        return hashlib.sha256(
            canonical_command(
                command_type,
                target,
                exact_text,
                nonce,
                supersedes=supersedes,
                expires_at=expires_at,
                metadata=metadata,
            )
        ).hexdigest()
    except (TypeError, ValueError):
        return None


def _target_status(database: FoundryDatabase, target: str | None) -> str:
    """Resolve target identity without treating an unknown target as global."""
    if target is None or not target.strip():
        return "UNKNOWN"
    if target == "GLOBAL":
        return "KNOWN"

    try:
        with database.connect(readonly=True) as connection:
            # Resolve all core target namespaces with one indexed lookup. The
            # previous three-query loop ran once for every directive and was
            # visible as a systems-table N+1 in the Command Center trace.
            row = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM missions WHERE mission_id = ?) "
                "OR EXISTS(SELECT 1 FROM resources WHERE resource_id = ?) "
                "OR EXISTS(SELECT 1 FROM systems WHERE system_id = ?)",
                (target, target, target),
            ).fetchone()
            if row is not None and bool(row[0]):
                return "KNOWN"
    except (OSError, KeyError, sqlite3.Error):
        return "UNKNOWN"
    return "UNKNOWN"


def _target_statuses(
    database: FoundryDatabase, targets: set[str],
) -> dict[str, str]:
    """Resolve all directive targets with one indexed batch lookup."""
    normalized = {
        target.strip() for target in targets
        if isinstance(target, str) and target.strip() and target.strip() != "GLOBAL"
    }
    statuses = {"GLOBAL": "KNOWN"}
    statuses.update({target: "UNKNOWN" for target in normalized})
    if not normalized:
        return statuses
    values = tuple(sorted(normalized))
    placeholders = ", ".join("?" for _ in values)
    query = (
        "SELECT mission_id AS target FROM missions WHERE mission_id IN (" + placeholders + ") "
        "UNION SELECT resource_id AS target FROM resources WHERE resource_id IN (" + placeholders + ") "
        "UNION SELECT system_id AS target FROM systems WHERE system_id IN (" + placeholders + ")"
    )
    try:
        with database.connect(readonly=True) as connection:
            rows = connection.execute(query, values + values + values).fetchall()
    except (OSError, sqlite3.Error):
        return statuses
    for row in rows:
        target = str(row[0] or "")
        if target in statuses:
            statuses[target] = "KNOWN"
    return statuses


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
        supersedes = payload.get("supersedes")
        supersedes_value = supersedes if isinstance(supersedes, str) else None
        expires_at = payload.get("expires_at")
        expires_value = expires_at if isinstance(expires_at, str) else None
        metadata = payload.get("metadata")
        metadata_value = metadata if isinstance(metadata, dict) else None
        expected = _command_hash(
            command_type,
            target,
            exact_text,
            nonce,
            supersedes=supersedes_value,
            expires_at=expires_value,
            metadata=metadata_value,
        )
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
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        unknown.append("metadata")
    return tuple(dict.fromkeys(unknown))


def _event_record(
    database: FoundryDatabase,
    event: Event,
    sequence: int,
    nonce_events: dict[str, Event],
    target_status: str | None = None,
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
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
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
        target_status=(
            target_status if target_status is not None
            else _target_status(database, target)
        ),
        metadata=dict(metadata),
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

    directive_events = database.events(
        readonly=True,
        event_types=(
            "owner_control.nonce_used",
            "owner_directive.recorded",
            "owner_directive.revoked",
            "owner_directive.superseded",
        ),
    )
    target_status = _target_statuses(
        database,
        {
            str(event.payload.get("target"))
            for event in directive_events
            if event.event_type == "owner_directive.recorded"
            and isinstance(event.payload, dict)
            and isinstance(event.payload.get("target"), str)
        },
    )

    for sequence, event in enumerate(directive_events, start=1):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.event_type == "owner_control.nonce_used":
            nonce = payload.get("nonce")
            if event.source_type == "owner" and isinstance(nonce, str) and nonce.strip():
                nonce_events[nonce] = event
            continue
        if event.event_type == "owner_directive.recorded":
            target = event.payload.get("target") if isinstance(event.payload, dict) else None
            record = _event_record(
                database, event, sequence, nonce_events,
                target_status=target_status.get(str(target).strip(), "UNKNOWN")
                if isinstance(target, str) else "UNKNOWN",
            )
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


def active_directives_for(
    database: FoundryDatabase,
    *,
    command_types: set[str] | frozenset[str] | None = None,
    targets: set[str] | frozenset[str] | None = None,
    now: datetime | str | None = None,
) -> list[OwnerDirectiveRecord]:
    """Return active directives matching a command family and target scope."""
    records = active_owner_directives(database, now=now)
    wanted_commands = set(command_types) if command_types is not None else None
    wanted_targets = set(targets) if targets is not None else None
    return [
        record
        for record in records
        if (wanted_commands is None or record.command_type in wanted_commands)
        and (
            wanted_targets is None
            or record.target == "GLOBAL"
            or record.target in wanted_targets
        )
    ]


def constraint_context(
    database: FoundryDatabase,
    *,
    targets: set[str] | frozenset[str] | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Project applicable constraints without guessing at vague owner text.

    A constraint becomes machine-classifiable only when its optional metadata
    supplies both ``constraint_key`` and ``mode`` (``ALLOW`` or ``DENY``).
    Free-text constraints remain visible in ``unknown`` and are carried to
    workers verbatim; they never silently become a guessed block or allow.
    """
    records = active_directives_for(
        database,
        command_types=frozenset({"CONSTRAINT", "ADD_CONSTRAINT"}),
        targets=targets,
        now=now,
    )
    classified: dict[str, dict[str, OwnerDirectiveRecord]] = {}
    unknown: list[OwnerDirectiveRecord] = []
    for record in records:
        key = record.metadata.get("constraint_key")
        mode = str(record.metadata.get("mode") or "").upper()
        if not isinstance(key, str) or not key.strip() or mode not in {"ALLOW", "DENY"}:
            unknown.append(record)
            continue
        classified.setdefault(key.strip(), {})[mode] = record

    conflicts: list[dict[str, Any]] = []
    for key, modes in classified.items():
        if "ALLOW" in modes and "DENY" in modes:
            conflicts.append({
                "constraint_key": key,
                "directive_ids": [modes["ALLOW"].directive_id, modes["DENY"].directive_id],
                "status": "CONFLICTING",
            })

    return {
        "records": [record.to_dict() for record in records],
        "unknown": [record.to_dict() for record in unknown],
        "conflicts": conflicts,
        "status": "CONFLICTING" if conflicts else "UNKNOWN" if unknown else "CLEAR",
    }


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


def owner_control_state(
    snapshot: dict[str, Any],
    *,
    legacy_active: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    stop_record: dict[str, Any] | None = None,
    supervisor_status: str | None = None,
    degraded: bool = False,
    active_work_count: int = 0,
    blocked_work_count: int = 0,
) -> dict[str, Any]:
    """Derive one truthful management state from durable control evidence.

    This is a read model only.  The broker and Supervisor still enforce STOP,
    PAUSE, and vetoes; this function gives product surfaces one precedence
    ordered explanation without inventing RUNNING when runtime evidence is
    absent.
    """
    active = [
        value for value in snapshot.get("active", ())
        if isinstance(value, dict)
    ]
    known_ids = {
        str(value.get("directive_id"))
        for value in active
        if value.get("directive_id")
    }
    # Historical projections can predate the authenticated event envelope.
    # They remain visible to the enforcement/status read model, but are
    # explicitly marked legacy rather than silently promoted to owner truth.
    for value in legacy_active:
        if not isinstance(value, dict):
            continue
        directive_id = str(value.get("directive_id") or "")
        if directive_id and directive_id not in known_ids:
            legacy_value = dict(value)
            legacy_value["legacy_projection_only"] = True
            active.append(legacy_value)
            known_ids.add(directive_id)
    stops = [
        value for value in active
        if value.get("command_type") == "STOP_EVERYTHING"
    ]
    pauses = [
        value for value in active
        if value.get("command_type") == "PAUSE"
    ]
    vetoes = [
        value for value in active
        if value.get("command_type") in {"VETO_ACTION", "STOP_MISSION"}
    ]
    supervisor = str(supervisor_status or "UNKNOWN").upper()
    stop = stop_record is not None or bool(stops)
    paused = bool(pauses)
    if stop:
        status = "STOPPED"
        reason = (
            str(stop_record.get("reason") or "emergency stop sentinel is active")
            if stop_record else
            f"STOP_EVERYTHING directive {stops[0].get('directive_id')} is active"
        )
    elif paused:
        status = "PAUSED"
        reason = f"PAUSE directive {pauses[0].get('directive_id')} is active"
    elif supervisor in {"STOPPED", "FROZEN", "SUSPENDED"}:
        status = "STOPPED"
        reason = f"supervisor is {supervisor}"
    elif vetoes or blocked_work_count > 0:
        status = "BLOCKED"
        reason = (
            f"{len(vetoes)} active owner veto directive(s)"
            if vetoes else
            f"{blocked_work_count} blocked mission(s)"
        )
    elif degraded or supervisor == "DEGRADED":
        status = "DEGRADED"
        reason = "runtime health evidence is degraded"
    elif supervisor == "RUNNING":
        status = "RUNNING" if active_work_count > 0 else "WAITING"
        reason = (
            f"{active_work_count} active work item(s)"
            if active_work_count > 0 else "no active work is currently evidenced"
        )
    else:
        status = "UNKNOWN"
        reason = "no trustworthy runtime status evidence is available"
    return {
        "status": status,
        "reason": reason,
        "source": "durable_owner_controls_and_runtime_projection",
        "legacy_projection_only_ids": [
            str(value.get("directive_id"))
            for value in active
            if value.get("legacy_projection_only") and value.get("directive_id")
        ],
        "paused": paused,
        "stopped": stop,
        "blocked": status == "BLOCKED",
        "degraded": status == "DEGRADED",
        "waiting": status == "WAITING",
        "resume_available": bool(stop or paused),
        "active_directive_ids": [
            str(value.get("directive_id"))
            for value in active
            if value.get("directive_id")
        ],
        "pause_directive_ids": [
            str(value.get("directive_id"))
            for value in pauses
            if value.get("directive_id")
        ],
        "stop_directive_ids": [
            str(value.get("directive_id"))
            for value in stops
            if value.get("directive_id")
        ],
        "veto_directive_ids": [
            str(value.get("directive_id"))
            for value in vetoes
            if value.get("directive_id")
        ],
        "supervisor_status": supervisor,
        "active_work_count": int(active_work_count),
        "blocked_work_count": int(blocked_work_count),
    }
