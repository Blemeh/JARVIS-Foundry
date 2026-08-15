"""Versioned, read-only readers for legacy V1-V7 event rows."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.compat.errors import CompatReadError, CompatSchemaError
from jarvis_foundry.kernel.events import LedgerEvent

LEGACY_EVENTS_COLUMNS: tuple[str, ...] = (
    "sequence",
    "event_id",
    "event_type",
    "occurred_at",
    "recorded_at",
    "source_type",
    "source_id",
    "correlation_id",
    "causation_id",
    "idempotency_key",
    "schema_version",
    "payload_json",
    "payload_hash",
)

SUPPORTED_MIGRATION_VERSIONS: frozenset[int] = frozenset(range(1, 33))
SUPPORTED_ROW_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
ALLOWED_TABLES: frozenset[str] = frozenset({"events"})


@dataclass(frozen=True)
class LegacyRow:
    """A raw legacy event row with the stored payload bytes preserved."""

    sequence: int
    event_id: str
    event_type: str
    occurred_at: str
    recorded_at: str
    source_type: str
    source_id: str
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    schema_version: int
    payload: Mapping[str, Any]
    payload_json: str
    payload_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "idempotency_key": self.idempotency_key,
            "schema_version": self.schema_version,
            "payload_json": self.payload_json,
            "payload_hash": self.payload_hash,
        }


class LegacyEventsReader:
    """Read legacy event rows through a read-only connection, never mutating.

    The reader supports the V1-V7 ``events`` table shape introduced by migration
    version 1 and unchanged through migration version 32.  Unsupported tables,
    unsupported row schema versions, malformed rows, and payload-hash
    mismatches fail closed with an explicit reason.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        table: str = "events",
        migration_version: int = 32,
    ) -> None:
        self.path = Path(path)
        if table not in ALLOWED_TABLES:
            raise CompatSchemaError(f"unsupported legacy event table: {table!r}")
        if migration_version not in SUPPORTED_MIGRATION_VERSIONS:
            raise CompatSchemaError(
                f"unsupported schema migration version {migration_version}; "
                f"supported: {sorted(SUPPORTED_MIGRATION_VERSIONS)}"
            )
        self.table = table
        self.migration_version = migration_version
        self._check_schema()

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        uri = self.path.as_posix()
        connection = sqlite3.connect(
            f"file:{uri}?mode=ro", uri=True, timeout=30, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()

    def _check_schema(self) -> None:
        if not self.path.is_file():
            raise CompatSchemaError(f"legacy database missing: {self.path}")
        try:
            with self._read_connection() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (self.table,),
                ).fetchone()
                if exists is None:
                    raise CompatSchemaError(f"legacy table {self.table!r} missing from {self.path}")
                columns = tuple(
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM pragma_table_info(?)", (self.table,)
                    )
                )
        except CompatSchemaError:
            raise
        except sqlite3.Error as error:
            raise CompatSchemaError(f"cannot open legacy database: {error}") from error
        if columns != LEGACY_EVENTS_COLUMNS:
            raise CompatSchemaError(
                f"unsupported legacy events columns for migration {self.migration_version}: "
                f"{columns}"
            )

    def read_rows(self) -> list[LegacyRow]:
        """Return every legacy row ordered by the durable sequence."""
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT sequence, event_id, event_type, occurred_at, recorded_at, "
                "source_type, source_id, correlation_id, causation_id, idempotency_key, "
                "schema_version, payload_json, payload_hash "
                "FROM events ORDER BY sequence ASC"
            ).fetchall()
        return [self._row_from_sqlite(row) for row in rows]

    @staticmethod
    def _row_from_sqlite(row: sqlite3.Row) -> LegacyRow:
        raw_payload = str(row["payload_json"])
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as error:
            raise CompatReadError(
                f"payload_json is not valid JSON: {error}",
                sequence=int(row["sequence"]),
            ) from error
        if not isinstance(payload, dict):
            raise CompatReadError(
                "payload_json must decode to a JSON object",
                sequence=int(row["sequence"]),
            )
        return LegacyRow(
            sequence=int(row["sequence"]),
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            occurred_at=str(row["occurred_at"]),
            recorded_at=str(row["recorded_at"]),
            source_type=str(row["source_type"]),
            source_id=str(row["source_id"]),
            correlation_id=str(row["correlation_id"]),
            causation_id=(str(row["causation_id"]) if row["causation_id"] is not None else None),
            idempotency_key=str(row["idempotency_key"]),
            schema_version=int(row["schema_version"]),
            payload=payload,
            payload_json=raw_payload,
            payload_hash=str(row["payload_hash"]),
        )

    @staticmethod
    def _parse_payload(row: LegacyRow) -> Mapping[str, Any]:
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError as error:
            raise CompatReadError(
                f"payload_json is not valid JSON: {error}",
                sequence=row.sequence,
            ) from error
        if not isinstance(payload, dict):
            raise CompatReadError(
                "payload_json must decode to a JSON object",
                sequence=row.sequence,
            )
        return payload

    def to_ledger_event(self, row: LegacyRow) -> LedgerEvent:
        """Map one legacy row to the R4 LedgerEvent shape, fail closed on error."""
        if row.sequence < 1:
            raise CompatReadError("sequence must be positive", sequence=row.sequence)
        if row.schema_version not in SUPPORTED_ROW_SCHEMA_VERSIONS:
            raise CompatReadError(
                f"unsupported row schema version {row.schema_version}; "
                f"supported: {sorted(SUPPORTED_ROW_SCHEMA_VERSIONS)}",
                sequence=row.sequence,
            )
        payload = row.payload
        for field_name in (
            "event_id",
            "event_type",
            "occurred_at",
            "recorded_at",
            "source_type",
            "source_id",
            "correlation_id",
            "idempotency_key",
        ):
            value = getattr(row, field_name)
            if not isinstance(value, str) or not value.strip():
                raise CompatReadError(f"field {field_name!r} is empty", sequence=row.sequence)
        if "." not in row.event_type:
            raise CompatReadError(
                f"event_type {row.event_type!r} is malformed (missing '.')",
                sequence=row.sequence,
            )
        expected_hash = canonical_hash(payload)
        if row.payload_hash != expected_hash:
            raise CompatReadError(
                f"payload hash mismatch: stored {row.payload_hash}, recomputed {expected_hash}",
                sequence=row.sequence,
            )
        return LedgerEvent(
            event_id=row.event_id,
            event_type=row.event_type,
            occurred_at=row.occurred_at,
            recorded_at=row.recorded_at,
            source_type=row.source_type,
            source_id=row.source_id,
            correlation_id=row.correlation_id,
            causation_id=row.causation_id,
            idempotency_key=row.idempotency_key,
            schema_version=row.schema_version,
            kind_version=1,
            payload=dict(payload),
            payload_hash=row.payload_hash,
        )

    def collect_refusals(self, rows: list[LegacyRow]) -> list[CompatReadError]:
        """Validate every row without raising; used for deterministic reports."""
        refusals: list[CompatReadError] = []
        for row in rows:
            try:
                self.to_ledger_event(row)
            except CompatReadError as error:
                refusals.append(error)
        return refusals

    def read_events(self) -> list[LedgerEvent]:
        return [self.to_ledger_event(row) for row in self.read_rows()]
