from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .clock import isoformat
from .events import Event, EventConflict, canonical_json
from .migrations import migrate
from .projections import apply_event, reset


class FoundryDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> int:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = migrate(connection, isoformat())
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

    def events(self) -> list[Event]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        return [self._row_to_event(row) for row in rows]

    def rebuild_projections(self) -> int:
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
            reset(connection)
            for row in rows:
                event = self._row_to_event(row)
                event.validate()
                apply_event(connection, event)
            return len(rows)

    def quick_check(self) -> str:
        with self.connect() as connection:
            return str(connection.execute("PRAGMA quick_check").fetchone()[0])

    def verify_event_integrity(self) -> int:
        events = self.events()
        for event in events:
            event.validate()
        return len(events)

    def backup_to(self, target: str | Path) -> Path:
        destination_path = Path(target).expanduser().resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination_path) as destination:
            source.backup(destination)
            result = destination.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise sqlite3.DatabaseError(f"backup integrity check failed: {result}")
        return destination_path

    def restore_from(self, source_path: str | Path) -> int:
        source_path = Path(source_path).expanduser().resolve()
        source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("restore source failed integrity check")
            with self.connect() as destination:
                source.backup(destination)
        finally:
            source.close()
        return self.verify_event_integrity()

    def row(self, table: str, key_column: str, key: str) -> dict[str, object] | None:
        allowed = {
            "missions": "mission_id",
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
            "candidates": "candidate_id",
            "candidate_scores": "candidate_id",
            "monitors": "monitor_id",
            "vault_files": "rel_path",
            "vault_sync_state": "sync_id",
            "ingestion_cursors": "source_kind",
            "research_queue": "item_id",
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
        }
        if allowed.get(table) != key_column:
            raise ValueError("unsupported projection lookup")
        with self.connect() as connection:
            row = connection.execute(f"SELECT * FROM {table} WHERE {key_column} = ?", (key,)).fetchone()
        return dict(row) if row else None

    def all(self, table: str, **filters: object) -> list[dict[str, object]]:
        """Bounded read helper for the projection tables (exact-match filters)."""
        allowed = {"knowledge_claims", "knowledge_contradictions", "obsidian_writes",
                   "knowledge_manifests", "knowledge_entities", "knowledge_claim_entities",
                   "knowledge_ingestions", "knowledge_snapshots", "candidates",
                   "candidate_scores", "monitors", "vault_files",
                   "vault_sync_state", "ingestion_cursors", "research_queue",
                   "worker_manifests", "consolidation_ops"}
        allowed.update({"cost_measurements", "briefing_outbox", "voice_interactions", "control_nonces", "improvement_records", "release_manifests", "release_slots", "candidate_builds", "evaluation_runs", "verifications", "shadow_runs", "canaries", "promotions", "release_control"})
        if table not in allowed:
            raise ValueError("unsupported projection listing")
        clauses, args = [], []
        for column, value in filters.items():
            clauses.append(f"{column} = ?")
            args.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(f"SELECT * FROM {table}{where} ORDER BY rowid", args).fetchall()
        return [dict(r) for r in rows]

    def count(self, table: str) -> int:
        allowed = {"knowledge_claims", "knowledge_contradictions", "obsidian_writes",
                   "knowledge_manifests", "knowledge_entities", "knowledge_claim_entities",
                   "knowledge_ingestions", "knowledge_snapshots", "candidates",
                   "candidate_scores", "monitors", "vault_files",
                   "vault_sync_state", "ingestion_cursors", "research_queue",
                   "worker_manifests", "consolidation_ops"}
        allowed.update({"cost_measurements", "briefing_outbox", "voice_interactions", "control_nonces", "improvement_records", "release_manifests", "release_slots", "candidate_builds", "evaluation_runs", "verifications", "shadow_runs", "canaries", "promotions", "release_control"})
        if table not in allowed:
            raise ValueError("unsupported projection count")
        with self.connect() as connection:
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
