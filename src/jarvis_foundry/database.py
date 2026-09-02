from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .clock import isoformat
from .events import Event, EventConflict, canonical_json
from .migrations import migrate
from .projections import apply_event, reset


class _ClosingConnection(sqlite3.Connection):
    """Close SQLite handles when their transaction context exits."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


_ACTIVE_READ_SNAPSHOT: ContextVar["ReadSnapshotContext | None"] = ContextVar(
    "jarvis_foundry_active_read_snapshot", default=None
)


class ReadSnapshotContext:
    """One request-scoped, mechanically read-only view of a ledger.

    Command Center projections are composed from several older read models.
    Those models intentionally keep their public ``FoundryDatabase`` API, so
    this context is the narrow shared seam: repeated event/table reads during
    one projection request reuse the same read handle and decoded result.  It
    is never used by ``transaction`` or append paths.
    """

    def __init__(self, database: "FoundryDatabase") -> None:
        self.database = database
        self.connection: sqlite3.Connection | None = None
        self._token = None
        self._event_cache: dict[tuple[object, ...], list[Event]] = {}
        self._event_count_cache: dict[str | None, int] = {}
        self._row_cache: dict[tuple[str, str, str], dict[str, object] | None] = {}
        self._count_cache: dict[str, int] = {}

    def __enter__(self) -> "ReadSnapshotContext":
        if self.connection is not None:
            raise RuntimeError("read snapshot context cannot be re-entered")
        self.connection = self.database.connect(readonly=True)
        # Pin one SQLite read transaction for the complete projection. This
        # keeps related fields mutually consistent while remaining explicitly
        # read-only; write paths continue to use transaction()/BEGIN IMMEDIATE.
        self.connection.execute("BEGIN")
        self._token = _ACTIVE_READ_SNAPSHOT.set(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._token is not None:
            _ACTIVE_READ_SNAPSHOT.reset(self._token)
            self._token = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self._event_cache.clear()
        self._event_count_cache.clear()
        self._row_cache.clear()
        self._count_cache.clear()

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("read snapshot context is not active")
        return self.connection

    def events(
        self,
        *,
        limit: int | None = None,
        newest_first: bool = False,
        event_types: tuple[str, ...] | None = None,
        event_type_prefixes: tuple[str, ...] | None = None,
        exclude_event_types: tuple[str, ...] | None = None,
    ) -> list[Event]:
        key = (
            limit,
            newest_first,
            tuple(event_types or ()),
            tuple(event_type_prefixes or ()),
            tuple(exclude_event_types or ()),
        )
        cached = self._event_cache.get(key)
        if cached is not None:
            return cached

        clauses: list[str] = []
        args: list[object] = []
        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            args.extend(event_types)
        if event_type_prefixes:
            clauses.append(
                "(" + " OR ".join("event_type LIKE ?" for _ in event_type_prefixes) + ")"
            )
            args.extend(f"{prefix}%" for prefix in event_type_prefixes)
        if exclude_event_types:
            placeholders = ", ".join("?" for _ in exclude_event_types)
            clauses.append(f"event_type NOT IN ({placeholders})")
            args.extend(exclude_event_types)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "DESC" if newest_first else "ASC"
        limit_sql = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            args.append(max(1, int(limit)))
        rows = self._connection().execute(
            f"SELECT * FROM events{where} ORDER BY sequence {order}{limit_sql}",
            tuple(args),
        ).fetchall()
        events = [self.database._row_to_event(row) for row in rows]
        self._event_cache[key] = events
        return events

    def event_count(self, event_type: str | None = None) -> int:
        if event_type in self._event_count_cache:
            return self._event_count_cache[event_type]
        if event_type is None:
            row = self._connection().execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()
        else:
            row = self._connection().execute(
                "SELECT COUNT(*) FROM events WHERE event_type = ?",
                (event_type,),
            ).fetchone()
        value = int(row[0])
        self._event_count_cache[event_type] = value
        return value

    def projection_head(self, exclude_event_types: tuple[str, ...]) -> int:
        placeholders = ", ".join("?" for _ in exclude_event_types)
        row = self._connection().execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM events "
            f"WHERE event_type NOT IN ({placeholders})",
            tuple(exclude_event_types),
        ).fetchone()
        return int(row[0] or 0)

    def row(self, table: str, key_column: str, key: str) -> dict[str, object] | None:
        cache_key = (table, key_column, key)
        if cache_key not in self._row_cache:
            row = self._connection().execute(
                f"SELECT * FROM {table} WHERE {key_column} = ?", (key,)
            ).fetchone()
            self._row_cache[cache_key] = dict(row) if row else None
        return self._row_cache[cache_key]

    def all(self, table: str, filters: dict[str, object]) -> list[dict[str, object]]:
        clauses = [f"{column} = ?" for column in filters]
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection().execute(
            f"SELECT * FROM {table}{where} ORDER BY rowid",
            tuple(filters.values()),
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self, table: str) -> int:
        if table not in self._count_cache:
            self._count_cache[table] = int(
                self._connection().execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
        return self._count_cache[table]


class FoundryDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        """Open the ledger. ``readonly=True`` uses SQLite ``mode=ro`` and
        never creates or migrates the file (status/doctor/events must use
        this against a live Supervisor home).
        """
        if readonly:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
            connection = sqlite3.connect(
                uri, uri=True, timeout=10, isolation_level=None,
                # Read-only callers commonly use ``with`` and Windows keeps
                # an open SQLite handle from being removed during disposable
                # recovery cleanup.  The closing subclass has no writable
                # journal work, but guarantees the scope releases its handle.
                factory=_ClosingConnection)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path, timeout=10, isolation_level=None,
                factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        if not readonly:
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def read_snapshot(self) -> ReadSnapshotContext:
        """Return a bounded request-scoped read context.

        The context owns one ``mode=ro`` connection and is deliberately
        separate from the write transaction API.  Nested database readers
        called while it is active automatically reuse its cached reads.
        """
        return ReadSnapshotContext(self)

    def initialize(self, target: int | None = None) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = migrate(connection, isoformat(), target=target)
                connection.commit()
                return version
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def append(self, event: Event) -> tuple[int, bool]:
        return self.append_events([event])[0]

    def append_events(self, events: list[Event]) -> list[tuple[int, bool]]:
        """Append multiple events atomically in ONE transaction.

        Same idempotency semantics as ``append`` per event (identical
        re-delivery dedupes; a changed payload under the same key raises
        ``EventConflict`` and rolls back the WHOLE batch), but the batch is
        all-or-nothing — used by deterministic recovery paths that must not
        leave a partial transition (e.g. mission BLOCKED -> ACTION_SELECTION
        resume) behind on a crash.
        """
        results: list[tuple[int, bool]] = []
        with self.transaction() as connection:
            results.extend(self._append_events_on_connection(connection, events))
        return results

    def append_events_on_connection(
        self, connection: sqlite3.Connection, events: list[Event]
    ) -> list[tuple[int, bool]]:
        """Append events while an existing caller-owned transaction is open.

        This is intentionally a narrow escape hatch for state transitions that
        must combine their projection reads and event writes atomically.
        """
        return self._append_events_on_connection(connection, events)

    @staticmethod
    def _append_events_on_connection(
        connection: sqlite3.Connection, events: list[Event]
    ) -> list[tuple[int, bool]]:
        results: list[tuple[int, bool]] = []
        for event in events:
            event.validate()
            existing = connection.execute(
                "SELECT sequence, event_id, event_type, payload_hash FROM events WHERE idempotency_key = ?",
                (event.idempotency_key,),
            ).fetchone()
            if existing:
                identical = (
                    existing["event_type"] == event.event_type
                    and existing["payload_hash"] == event.payload_hash
                )
                if not identical:
                    raise EventConflict(f"idempotency conflict for {event.idempotency_key}")
                results.append((int(existing["sequence"]), False))
                continue
            cursor = connection.execute(
                """INSERT INTO events(
                    event_id, event_type, occurred_at, recorded_at, source_type, source_id,
                    correlation_id, causation_id, idempotency_key, schema_version,
                    payload_json, payload_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id, event.event_type, event.occurred_at, event.recorded_at,
                    event.source_type, event.source_id, event.correlation_id, event.causation_id,
                    event.idempotency_key, event.schema_version, canonical_json(event.payload), event.payload_hash,
                ),
            )
            apply_event(connection, event)
            results.append((int(cursor.lastrowid), True))
        return results

    def events(
        self,
        *,
        readonly: bool = True,
        limit: int | None = None,
        newest_first: bool = False,
        event_types: tuple[str, ...] | None = None,
        event_type_prefixes: tuple[str, ...] | None = None,
        exclude_event_types: tuple[str, ...] | None = None,
    ) -> list[Event]:
        """Read ledger events with optional bounded/filtering controls.

        The default remains the complete ledger in sequence order.  Read-only
        owner projections can request a newest bounded window and exclude
        heartbeat/noise records without materializing hundreds of thousands of
        historical ``Event`` objects on every HTTP refresh.
        """
        active_context = _ACTIVE_READ_SNAPSHOT.get()
        if active_context is not None and active_context.database is self and readonly:
            return active_context.events(
                limit=limit,
                newest_first=newest_first,
                event_types=event_types,
                event_type_prefixes=event_type_prefixes,
                exclude_event_types=exclude_event_types,
            )

        clauses: list[str] = []
        args: list[object] = []
        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            args.extend(event_types)
        if event_type_prefixes:
            clauses.append("(" + " OR ".join("event_type LIKE ?" for _ in event_type_prefixes) + ")")
            args.extend(f"{prefix}%" for prefix in event_type_prefixes)
        if exclude_event_types:
            placeholders = ", ".join("?" for _ in exclude_event_types)
            clauses.append(f"event_type NOT IN ({placeholders})")
            args.extend(exclude_event_types)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "DESC" if newest_first else "ASC"
        limit_sql = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            args.append(max(1, int(limit)))
        with self.connect(readonly=readonly) as connection:
            rows = connection.execute(
                f"SELECT * FROM events{where} ORDER BY sequence {order}{limit_sql}",
                tuple(args),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def event_count(self, *, readonly: bool = True, event_type: str | None = None) -> int:
        """Row count without materializing the ledger."""
        active_context = _ACTIVE_READ_SNAPSHOT.get()
        if active_context is not None and active_context.database is self and readonly:
            return active_context.event_count(event_type)
        with self.connect(readonly=readonly) as connection:
            if event_type is None:
                row = connection.execute("SELECT COUNT(*) FROM events").fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type = ?",
                    (event_type,),
                ).fetchone()
        return int(row[0])

    def rebuild_projections(self) -> int:
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
            reset(connection)
            for row in rows:
                event = self._row_to_event(row)
                event.validate()
                apply_event(connection, event)
            return len(rows)

    def quick_check(self, *, readonly: bool = True) -> str:
        with self.connect(readonly=readonly) as connection:
            return str(connection.execute("PRAGMA quick_check").fetchone()[0])

    def verify_event_integrity(self) -> int:
        events = self.events()
        for event in events:
            event.validate()
        return len(events)

    def backup_to(self, target: str | Path) -> Path:
        destination_path = Path(target).expanduser().resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination_path.with_name(
            f".{destination_path.name}.{uuid.uuid4().hex}.tmp")
        temporary_path.unlink(missing_ok=True)
        destination = sqlite3.connect(temporary_path)
        source: sqlite3.Connection | None = None
        try:
            source = self.connect(readonly=True)
            source.backup(destination)
            result = destination.execute(
                "PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise sqlite3.DatabaseError(
                    f"backup integrity check failed: {result}")
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        finally:
            if source is not None:
                source.close()
            destination.close()
        os.replace(temporary_path, destination_path)
        return destination_path

    def restore_from(self, source_path: str | Path) -> int:
        source_path = Path(source_path).expanduser().resolve()
        source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True, timeout=10)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.restore-", suffix=".tmp",
            dir=self.path.parent)
        os.close(fd)
        temporary_path = Path(temporary_name)
        try:
            if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("restore source failed integrity check")
            destination = sqlite3.connect(temporary_path)
            try:
                source.backup(destination)
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("restore destination failed integrity check")
            finally:
                destination.close()
        except BaseException:
            if temporary_path.exists():
                temporary_path.unlink()
            raise
        finally:
            source.close()
        # The target may be open elsewhere on Windows, so replacing its file
        # path is not portable. SQLite's backup API performs the final copy
        # through the destination journal, retaining the old file on a killed
        # or failed restore while still avoiding an unvalidated source.
        try:
            validated_source = sqlite3.connect(
                f"file:{temporary_path.as_posix()}?mode=ro", uri=True,
                timeout=10)
            destination = self.connect()
            try:
                validated_source.backup(destination)
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError(
                        "restore target failed integrity check")
            finally:
                destination.close()
                validated_source.close()
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return self.verify_event_integrity()

    def row(self, table: str, key_column: str, key: str) -> dict[str, object] | None:
        allowed = {
            "missions": "mission_id",
            "mission_step_contracts": "contract_id",
            "resources": "resource_id",
            "directives": "directive_id",
            "action_decisions": "action_id",
            "leases": "lease_key",
            "agent_runs": "run_id",
            "evidence_bundles": "bundle_id",
            "verification_verdicts": "verdict_id",
            "budgets": "provider",
            "obsidian_writes": "write_id",
            "knowledge_claims": "claim_id",
            "knowledge_manifests": "manifest_id",
            "knowledge_entities": "entity_id",
            "knowledge_ingestions": "ingestion_id",
            "knowledge_snapshots": "snapshot_id",
            "candidate_sources": "link_id",
            "candidate_judgments": "judgment_id",
            "candidates": "candidate_id",
            "candidate_scores": "candidate_id",
            "candidate_outcomes": "outcome_id",
            "scoring_policy_registry": "policy_version",
            "discovery_portfolio": "loop_id",
            "monitors": "monitor_id",
            "vault_files": "rel_path",
            "vault_sync_state": "sync_id",
            "ingestion_cursors": "source_kind",
            "research_queue": "item_id",
            "initiative_source_runs": "source_run_id",
            "initiative_cycles": "cycle_id",
            "worker_manifests": "manifest_id",
            "consolidation_ops": "op_id",
            "cost_measurements": "measurement_id",
            "briefing_outbox": "briefing_id",
            "voice_interactions": "interaction_id",
            "control_nonces": "nonce",
            "improvement_records": "improvement_id",
            "release_manifests": "release_id",
            "release_slots": "slot",
            "candidate_builds": "build_id",
            "evaluation_runs": "run_id",
            "verifications": "verification_id",
            "shadow_runs": "run_id",
            "canaries": "canary_id",
            "promotions": "promotion_id",
            "release_control": "id",
            "restore_points": "point_id",
            "discovery_runs": "run_id",
            "cycles": "cycle_id",
            "acceptance_runs": "run_id",
            # Software-intelligence (Lane 4): derived alias projection
            "tier_c_project_aliases": "alias",
            # V9 World Intelligence: event-derived projections
            "world_entities": "entity_id",
            "world_relationships": "relationship_id",
            "world_observations": "observation_id",
            "world_signals": "signal_id",
            "world_predictions": "prediction_id",
            "world_knowledge_gaps": "gap_id",
            "world_patterns": "pattern_id",
            "world_calibration": "calibration_id",
        }
        if allowed.get(table) != key_column:
            raise ValueError("unsupported projection lookup")
        active_context = _ACTIVE_READ_SNAPSHOT.get()
        if active_context is not None and active_context.database is self:
            return active_context.row(table, key_column, key)
        with self.connect(readonly=True) as connection:
            row = connection.execute(f"SELECT * FROM {table} WHERE {key_column} = ?", (key,)).fetchone()
        return dict(row) if row else None

    def all(self, table: str, **filters: object) -> list[dict[str, object]]:
        """Bounded read helper for the projection tables (exact-match filters)."""
        allowed = {"knowledge_claims", "knowledge_contradictions", "obsidian_writes",
                   "knowledge_manifests", "knowledge_entities", "knowledge_claim_entities",
                   "knowledge_claim_evidence",
                   "knowledge_ingestions", "knowledge_snapshots", "candidates",
                   "candidate_sources", "candidate_judgments",
                   "candidate_scores", "candidate_outcomes",
                   "scoring_policy_registry", "discovery_portfolio",
                   "monitors", "vault_files",
                   "vault_sync_state", "ingestion_cursors", "research_queue",
                   "initiative_source_runs", "initiative_cycles",
                   "worker_manifests", "consolidation_ops", "mission_step_contracts",
                   "world_entities", "world_relationships", "world_observations",
                   "world_signals", "world_predictions", "world_knowledge_gaps",
                   "world_patterns", "world_calibration"}
        allowed.update({"tier_c_project_aliases"})
        allowed.update({"cost_measurements", "briefing_outbox", "voice_interactions", "control_nonces", "improvement_records", "release_manifests", "release_slots", "candidate_builds", "evaluation_runs", "verifications", "shadow_runs", "canaries", "promotions", "release_control", "restore_points", "discovery_runs", "cycles", "acceptance_runs"})
        if table not in allowed:
            raise ValueError("unsupported projection listing")
        clauses, args = [], []
        for column, value in filters.items():
            clauses.append(f"{column} = ?")
            args.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        active_context = _ACTIVE_READ_SNAPSHOT.get()
        if active_context is not None and active_context.database is self:
            return active_context.all(table, filters)
        with self.connect(readonly=True) as connection:
            rows = connection.execute(f"SELECT * FROM {table}{where} ORDER BY rowid", args).fetchall()
        return [dict(r) for r in rows]

    def count(self, table: str) -> int:
        allowed = {"knowledge_claims", "knowledge_contradictions", "obsidian_writes",
                   "knowledge_manifests", "knowledge_entities", "knowledge_claim_entities",
                   "knowledge_claim_evidence",
                   "knowledge_ingestions", "knowledge_snapshots", "candidates",
                   "candidate_sources", "candidate_judgments",
                   "candidate_scores", "candidate_outcomes",
                   "scoring_policy_registry", "discovery_portfolio",
                   "monitors", "vault_files",
                   "vault_sync_state", "ingestion_cursors", "research_queue",
                   "initiative_source_runs", "initiative_cycles",
                   "worker_manifests", "consolidation_ops", "mission_step_contracts",
                   "tier_c_project_aliases", "world_entities", "world_relationships",
                   "world_observations", "world_signals", "world_predictions",
                   "world_knowledge_gaps", "world_patterns", "world_calibration"}
        allowed.update({"cost_measurements", "briefing_outbox", "voice_interactions", "control_nonces", "improvement_records", "release_manifests", "release_slots", "candidate_builds", "evaluation_runs", "verifications", "shadow_runs", "canaries", "promotions", "release_control", "restore_points", "discovery_runs", "cycles", "acceptance_runs"})
        if table not in allowed:
            raise ValueError("unsupported projection count")
        active_context = _ACTIVE_READ_SNAPSHOT.get()
        if active_context is not None and active_context.database is self:
            return active_context.count(table)
        with self.connect(readonly=True) as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            event_id=row["event_id"], event_type=row["event_type"],
            occurred_at=row["occurred_at"], recorded_at=row["recorded_at"],
            source_type=row["source_type"], source_id=row["source_id"],
            correlation_id=row["correlation_id"], causation_id=row["causation_id"],
            idempotency_key=row["idempotency_key"], schema_version=row["schema_version"],
            payload=json.loads(row["payload_json"]), payload_hash=row["payload_hash"],
        )
