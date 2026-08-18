"""Durable perception cursor - reuse of the existing ingestion cursor
projection (``ingestion_cursors`` + ``ingestion.cursor_updated``). A restart
never re-scans the whole source and a crash before advancing never loses
records (idempotent replay)."""
from __future__ import annotations

from ..database import FoundryDatabase
from ..events import Event

CURSOR_SOURCE_KIND = "screenpipe-local"


def read_cursor(database: FoundryDatabase,
                source_kind: str = CURSOR_SOURCE_KIND) -> int:
    """Latest durable source position (monotonic external source id)."""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT cursor FROM ingestion_cursors WHERE source_kind = ?",
            (source_kind,)).fetchone()
    if row is None or row["cursor"] is None:
        return 0
    try:
        return int(row["cursor"])
    except (TypeError, ValueError):
        return 0


def advance_cursor(database: FoundryDatabase, source_kind: str, last_id: int,
                   *, source_id: str = "perception") -> Event:
    """Advance the durable cursor to ``last_id`` (idempotent per position).

    Replays of a previously advanced position dedupe; a higher position
    records a new event (history preserved, no silent rewrites).
    """
    event = Event.create(
        "ingestion.cursor_updated",
        {"source_kind": source_kind, "cursor": str(int(last_id))},
        source_type="supervisor", source_id=source_id,
        correlation_id=source_kind,
        idempotency_key=f"ingestion.cursor_updated:{source_kind}:{int(last_id)}",
    )
    database.append(event)
    return event
