"""Append-only event ledger with header hash chaining and DB constraints."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import (
    EventConflictError,
    EventIntegrityError,
    FinancialActionDeniedError,
)
from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent, payload_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_kinds (
  kind TEXT NOT NULL,
  version INTEGER NOT NULL,
  financial INTEGER NOT NULL CHECK (financial IN (0, 1)),
  PRIMARY KEY (kind, version)
);
CREATE TABLE IF NOT EXISTS ledger_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  correlation_id TEXT,
  causation_id TEXT,
  idempotency_key TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  kind_version INTEGER NOT NULL,
  payload TEXT NOT NULL CHECK (json_valid(payload)),
  payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
  previous_hash TEXT CHECK (previous_hash IS NULL OR length(previous_hash) = 64),
  header_hash TEXT NOT NULL CHECK (length(header_hash) = 64),
  CHECK (length(event_id) > 0),
  CHECK (length(event_type) > 0),
  CHECK (schema_version = kind_version),
  UNIQUE (event_id),
  UNIQUE (event_type, idempotency_key),
  FOREIGN KEY (event_type, kind_version) REFERENCES event_kinds(kind, version)
);
CREATE INDEX IF NOT EXISTS idx_ledger_events_sequence ON ledger_events(sequence);
CREATE TRIGGER IF NOT EXISTS ledger_events_no_update
BEFORE UPDATE ON ledger_events
BEGIN
  SELECT RAISE(ABORT, 'ledger is append-only: update refused');
END;
CREATE TRIGGER IF NOT EXISTS ledger_events_no_delete
BEFORE DELETE ON ledger_events
BEGIN
  SELECT RAISE(ABORT, 'ledger is append-only: delete refused');
END;
CREATE TRIGGER IF NOT EXISTS event_kinds_no_update
BEFORE UPDATE ON event_kinds
BEGIN
  SELECT RAISE(ABORT, 'event kinds are append-only');
END;
CREATE TRIGGER IF NOT EXISTS event_kinds_no_delete
BEFORE DELETE ON event_kinds
BEGIN
  SELECT RAISE(ABORT, 'event kinds are append-only');
END;
"""


@dataclass(frozen=True)
class AppendResult:
    sequence: int
    created: bool
    event: LedgerEvent


@dataclass(frozen=True)
class ChainProblem:
    sequence: int | None
    detail: str


@dataclass(frozen=True)
class ChainReport:
    valid: bool
    sequence_count: int
    head_hash: str | None
    problems: tuple[ChainProblem, ...]


@dataclass
class Subscription:
    _callback: Callable[[LedgerEvent], None]
    _closed: bool = False

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class Ledger:
    """Durable append-only ledger bound to a versioned event-kind registry."""

    def __init__(
        self,
        path: str | Path,
        *,
        registry: EventKindRegistry,
        clock: Clock | None = None,
    ) -> None:
        self._path = Path(path)
        self._registry = registry
        self._clock = clock if clock is not None else SystemClock()
        self._subscriptions: list[Subscription] = []
        self._initialize()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def registry(self) -> EventKindRegistry:
        return self._registry

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # Internal writable handle used for initialization and appends only.
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        """Public read-only handle: callers cannot drop triggers or mutate state."""
        uri = self._path.as_posix()
        connection = sqlite3.connect(
            f"file:{uri}?mode=ro", uri=True, timeout=30, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            for spec in self._registry.entries().values():
                connection.execute(
                    "INSERT OR IGNORE INTO event_kinds (kind, version, financial) VALUES (?, ?, ?)",
                    (spec.kind, spec.version, 1 if spec.financial else 0),
                )

    def append(self, event: LedgerEvent) -> AppendResult:
        self._registry.validate(event)
        spec = self._registry.spec(event.event_type)
        if spec.financial:
            raise FinancialActionDeniedError(f"financial event kind refused: {event.event_type}")
        with self.transaction() as connection:
            result = self._append_with_connection(connection, event)
        self._notify(result.event)
        return result

    def append_with_connection(
        self, connection: sqlite3.Connection, event: LedgerEvent
    ) -> AppendResult:
        self._registry.validate(event)
        spec = self._registry.spec(event.event_type)
        if spec.financial:
            raise FinancialActionDeniedError(f"financial event kind refused: {event.event_type}")
        return self._append_with_connection(connection, event)

    def _append_with_connection(
        self, connection: sqlite3.Connection, event: LedgerEvent
    ) -> AppendResult:
        existing = connection.execute(
            "SELECT sequence FROM ledger_events WHERE event_id = ?", (event.event_id,)
        ).fetchone()
        if existing is not None:
            stored = self._row_to_event(connection, int(existing["sequence"]))
            return AppendResult(sequence=int(existing["sequence"]), created=False, event=stored)
        conflict = connection.execute(
            "SELECT sequence FROM ledger_events WHERE event_type = ? AND idempotency_key = ?",
            (event.event_type, event.idempotency_key),
        ).fetchone()
        if conflict is not None:
            raise EventConflictError(
                f"idempotency key {event.idempotency_key} conflicts with sequence "
                f"{int(conflict['sequence'])}"
            )
        spec = self._registry.spec(event.event_type)
        connection.execute(
            "INSERT OR IGNORE INTO event_kinds (kind, version, financial) VALUES (?, ?, ?)",
            (spec.kind, spec.version, 1 if spec.financial else 0),
        )
        previous = connection.execute(
            "SELECT header_hash FROM ledger_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous["header_hash"]) if previous is not None else None
        next_row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_seq FROM ledger_events"
        ).fetchone()
        sequence = int(next_row["next_seq"])
        final = event.with_kind_version(spec.version)
        header_hash = final.header_hash_for(sequence, previous_hash)
        final = final.with_chain(
            sequence=sequence, previous_hash=previous_hash, header_hash=header_hash
        )
        connection.execute(
            "INSERT INTO ledger_events ("
            "sequence, event_id, event_type, occurred_at, recorded_at, source_type, "
            "source_id, correlation_id, causation_id, idempotency_key, schema_version, "
            "kind_version, payload, payload_hash, previous_hash, header_hash"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                final.event_id,
                final.event_type,
                final.occurred_at,
                final.recorded_at,
                final.source_type,
                final.source_id,
                final.correlation_id,
                final.causation_id,
                final.idempotency_key,
                final.schema_version,
                final.kind_version,
                json.dumps(dict(final.payload), sort_keys=True, separators=(",", ":")),
                final.payload_hash,
                final.previous_hash,
                final.header_hash,
            ),
        )
        return AppendResult(sequence=sequence, created=True, event=final)

    def _row_to_event(self, connection: sqlite3.Connection, sequence: int) -> LedgerEvent:
        row = connection.execute(
            "SELECT * FROM ledger_events WHERE sequence = ?", (sequence,)
        ).fetchone()
        if row is None:
            raise EventIntegrityError(f"missing ledger row for sequence {sequence}")
        return LedgerEvent(
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            occurred_at=str(row["occurred_at"]),
            recorded_at=str(row["recorded_at"]),
            source_type=str(row["source_type"]),
            source_id=str(row["source_id"]),
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
            idempotency_key=str(row["idempotency_key"]),
            schema_version=int(row["schema_version"]),
            kind_version=int(row["kind_version"]),
            payload=json.loads(str(row["payload"])),
            payload_hash=str(row["payload_hash"]),
            previous_hash=row["previous_hash"],
            header_hash=str(row["header_hash"]),
            sequence=int(row["sequence"]),
        )

    def read(self, *, after: int = 0, limit: int | None = None) -> list[LedgerEvent]:
        if limit is not None:
            sql = "SELECT * FROM ledger_events WHERE sequence > ? ORDER BY sequence ASC LIMIT ?"
            params: tuple[Any, ...] = (after, limit)
        else:
            sql = "SELECT * FROM ledger_events WHERE sequence > ? ORDER BY sequence ASC"
            params = (after,)
        with self.read_connection() as connection:
            rows = connection.execute(sql, params).fetchall()
            return [self._row_to_event(connection, int(row["sequence"])) for row in rows]

    def replay(self) -> list[LedgerEvent]:
        return self.read()

    def subscribe(self, subscriber: Callable[[LedgerEvent], None]) -> Subscription:
        subscription = Subscription(subscriber)
        self._subscriptions.append(subscription)
        return subscription

    def _notify(self, event: LedgerEvent) -> None:
        for subscription in self._subscriptions:
            if not subscription.closed:
                subscription._callback(event)

    def verify_chain(self) -> ChainReport:
        events = self.read()
        problems: list[ChainProblem] = []
        expected_sequence = 1
        previous_hash: str | None = None
        for event in events:
            sequence = event.sequence if event.sequence is not None else 0
            if sequence != expected_sequence:
                problems.append(
                    ChainProblem(sequence, f"sequence gap: expected {expected_sequence}")
                )
            if event.previous_hash != previous_hash:
                problems.append(ChainProblem(sequence, "previous_hash mismatch"))
            if event.payload_hash != payload_hash(event.payload):
                problems.append(ChainProblem(sequence, "payload hash mismatch"))
            recomputed = event.header_hash_for(sequence, previous_hash)
            if event.header_hash != recomputed:
                problems.append(ChainProblem(sequence, "header hash mismatch"))
            previous_hash = event.header_hash
            expected_sequence += 1
        head_hash = events[-1].header_hash if events else None
        return ChainReport(
            valid=not problems,
            sequence_count=len(events),
            head_hash=head_hash,
            problems=tuple(problems),
        )

    @property
    def head_hash(self) -> str | None:
        events = self.read()
        return events[-1].header_hash if events else None

    def next_sequence(self) -> int:
        with self.read_connection() as connection:
            return int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_seq FROM ledger_events"
                ).fetchone()["next_seq"]
            )
