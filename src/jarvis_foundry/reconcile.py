"""Event/projection reconciliation diagnostics.

``reconcile`` rebuilds every projection from the verified event ledger in an
isolated in-memory database and compares it row-by-row, column-by-column with
the live projection tables. The report carries exact mismatch output: table,
key, column, expected value (from the deterministic rebuild), and actual value
(in the live database).

The comparison never mutates the live database. It is the deterministic
integrity control called by ``jarvis doctor`` and available to the Supervisor
before any dispatch or recovery decision.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .database import FoundryDatabase
from .events import EventIntegrityError
from .migrations import SCHEMA_VERSION, migrate
from .projections import PROJECTION_TABLES, apply_event, reset

# Runtime state derived from events; reconciled alongside the projections.
DERIVED_TABLES = ("system_state", "supervisor_state")


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    consistent: bool
    event_count: int
    integrity_failures: list[dict[str, Any]] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    unexpected_rows: list[dict[str, Any]] = field(default_factory=list)
    missing_rows: list[dict[str, Any]] = field(default_factory=list)
    version_mismatch: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "consistent": self.consistent,
            "event_count": self.event_count,
            "integrity_failures": self.integrity_failures,
            "mismatches": self.mismatches,
            "unexpected_rows": self.unexpected_rows,
            "missing_rows": self.missing_rows,
            "version_mismatch": self.version_mismatch,
        }


def _live_schema_version(database: FoundryDatabase, *, readonly: bool = False) -> int:
    with database.connect(readonly=readonly) as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
    return int(row[0])


def _table_key_column(table: str) -> str:
    key_columns = {
        "missions": "mission_id",
        "mission_step_contracts": "contract_id",
        "resources": "resource_id",
        "directives": "directive_id",
        "action_decisions": "action_id",
        "agent_runs": "run_id",
        "evidence_bundles": "bundle_id",
        "verification_verdicts": "verdict_id",
        "budgets": "provider",
        "obsidian_writes": "write_id",
        "knowledge_claims": "claim_id",
        "knowledge_contradictions": "contradiction_id",
        "knowledge_manifests": "manifest_id",
        "knowledge_entities": "entity_id",
        "knowledge_claim_entities": "claim_id",
        "knowledge_claim_evidence": "claim_id",
        "knowledge_ingestions": "ingestion_id",
        "knowledge_snapshots": "snapshot_id",
        "candidates": "candidate_id",
        "candidate_scores": "candidate_id",
        "monitors": "monitor_id",
        "systems": "system_id",
        "system_actions": "action_key",
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
        "restore_points": "point_id",
        "discovery_runs": "run_id",
        "cycles": "cycle_id",
        "acceptance_runs": "run_id",
        "system_state": "singleton",
        "supervisor_state": "singleton",
        # V8 hybrid memory: Tier C projections reconcile like everything else
        "tier_c_memories": "memory_id",
        "tier_c_memory_revisions": "revision_id",
        "tier_c_validations": "memory_id",
        "tier_c_blocks": "label",
        "tier_c_block_revisions": "revision_id",
        "tier_c_procedures": "procedure_id",
    }
    return key_columns[table]


def _rows_by_key(connection: sqlite3.Connection, table: str) -> dict[str, dict[str, Any]]:
    key_column = _table_key_column(table)
    rows = connection.execute(
        f"SELECT * FROM {table} ORDER BY {key_column}"
    ).fetchall()
    return {str(row[key_column]): dict(row) for row in rows}


def inspect(database: FoundryDatabase) -> ReconciliationReport:
    """Read-only ledger inspection. Never mutates the live database."""
    return reconcile(database, readonly=True)


def reconcile(database: FoundryDatabase, *, readonly: bool = False) -> ReconciliationReport:
    live_version = _live_schema_version(database, readonly=readonly)
    if live_version != SCHEMA_VERSION:
        try:
            count = database.event_count(readonly=readonly)
        except Exception:  # noqa: BLE001 — unmigrated files may lack events
            count = 0
        return ReconciliationReport(
            consistent=False,
            event_count=count,
            version_mismatch={
                "live_schema_version": live_version,
                "expected_schema_version": SCHEMA_VERSION,
                "detail": "reconciliation compares against a deterministic rebuild at the current schema version; migrate the database first (initialize) or operate on a migrated copy",
            },
        )

    # ONE connection + ONE deferred read transaction for the events read
    # AND every live-row read: WAL gives a reader a stable snapshot once
    # the transaction begins, so the rebuild and the compared rows come
    # from the SAME instant — a live daemon appending heartbeats
    # mid-reconcile can no longer advance supervisor_state between
    # separate per-table connections and fabricate mismatches
    # (V2-operational gate 2026-08-12: supervisor_state raced 5/5 runs
    # while the daemon heartbeated every 5s)
    with database.connect(readonly=readonly) as snapshot:
        snapshot.execute("BEGIN")  # deferred: stable WAL snapshot, no write lock
        rows = snapshot.execute(
            "SELECT * FROM events ORDER BY sequence").fetchall()
        events = [database._row_to_event(row) for row in rows]

        integrity_failures: list[dict[str, Any]] = []
        for event in events:
            try:
                event.validate()
            except EventIntegrityError as error:
                integrity_failures.append({
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "error": str(error),
                })

        if integrity_failures:
            # The ledger is untrusted: any projection comparison against
            # a rebuild from unverified events would be meaningless.
            return ReconciliationReport(
                consistent=False,
                event_count=len(events),
                integrity_failures=integrity_failures,
            )

        rebuilt = sqlite3.connect(":memory:")

        try:
            rebuilt.row_factory = sqlite3.Row
            migrate(rebuilt, "1970-01-01T00:00:00Z")
            reset(rebuilt)
            for event in events:
                apply_event(rebuilt, event)

            mismatches: list[dict[str, Any]] = []
            unexpected_rows: list[dict[str, Any]] = []
            missing_rows: list[dict[str, Any]] = []

            for table in (*PROJECTION_TABLES, *DERIVED_TABLES):
                key_column = _table_key_column(table)
                live_rows = _rows_by_key(snapshot, table)
                rebuilt_rows = _rows_by_key(rebuilt, table)

                for key, rebuilt_row in rebuilt_rows.items():
                    live_row = live_rows.get(key)
                    if live_row is None:
                        missing_rows.append({"table": table, "key_column": key_column, "key": key})
                        continue
                    for column, expected in rebuilt_row.items():
                        actual = live_row.get(column)
                        if _values_differ(column, expected, actual):
                            mismatches.append({
                                "table": table,
                                "key_column": key_column,
                                "key": key,
                                "column": column,
                                "expected": _json_safe(expected),
                                "actual": _json_safe(actual),
                            })
                for key in live_rows.keys() - rebuilt_rows.keys():
                    unexpected_rows.append({"table": table, "key_column": key_column, "key": key})

            consistent = (
                not integrity_failures
                and not mismatches
                and not unexpected_rows
                and not missing_rows
            )
            return ReconciliationReport(
                consistent=consistent,
                event_count=len(events),
                integrity_failures=integrity_failures,
                mismatches=mismatches,
                unexpected_rows=unexpected_rows,
                missing_rows=missing_rows,
            )
        finally:
            rebuilt.close()


def _values_differ(column: str, expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is not actual
    if isinstance(expected, float) or isinstance(actual, float):
        return float(expected) != float(actual)
    return expected != actual


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)
