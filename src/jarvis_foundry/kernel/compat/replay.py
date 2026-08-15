"""Deterministic zero-to-current replay of the legacy ledger."""

from __future__ import annotations

import hashlib
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_bytes, canonical_hash
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.compat.corrections import CORRECTION_EVENT_TYPE
from jarvis_foundry.kernel.compat.invariants import (
    InvariantReport,
    check_projection_snapshot,
    validate_rows,
)
from jarvis_foundry.kernel.compat.projections import build_projection_registry
from jarvis_foundry.kernel.compat.readers import LegacyEventsReader
from jarvis_foundry.kernel.compat.registry import (
    HISTORICAL_EVENT_TYPE_CATALOG,
    compat_kind_registry,
)
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ledger import ChainReport, Ledger
from jarvis_foundry.kernel.projections import ProjectionDispatcher, snapshot_bytes


@dataclass(frozen=True)
class Refusal:
    sequence: int | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"sequence": self.sequence, "reason": self.reason}


@dataclass(frozen=True)
class ReplayResult:
    source_path: str
    table: str
    migration_version: int
    row_count: int
    converted_count: int
    refused_count: int
    refusals: tuple[Refusal, ...]
    chain_valid: bool
    chain_sequence_count: int
    chain_problem_count: int
    head_hash: str | None
    projection_snapshot_sha256: str | None
    ledger_content_sha256: str | None
    projection_versions: Mapping[str, int]
    event_type_counts: Mapping[str, int]
    invariant_report: InvariantReport
    projection_mismatches: tuple[str, ...]
    projection_invariant_ok: bool
    wall_seconds: float

    @property
    def passed(self) -> bool:
        return (
            self.refused_count == 0
            and self.invariant_report.ok
            and self.chain_valid
            and self.chain_sequence_count == self.row_count
            and self.projection_invariant_ok
            and not self.projection_mismatches
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "table": self.table,
            "migration_version": self.migration_version,
            "row_count": self.row_count,
            "converted_count": self.converted_count,
            "refused_count": self.refused_count,
            "refusals": [refusal.to_dict() for refusal in self.refusals],
            "chain_valid": self.chain_valid,
            "chain_sequence_count": self.chain_sequence_count,
            "chain_problem_count": self.chain_problem_count,
            "head_hash": self.head_hash,
            "projection_snapshot_sha256": self.projection_snapshot_sha256,
            "ledger_content_sha256": self.ledger_content_sha256,
            "projection_versions": dict(sorted(self.projection_versions.items())),
            "event_type_counts": dict(sorted(self.event_type_counts.items())),
            "invariant_report": self.invariant_report.to_dict(),
            "projection_mismatches": list(self.projection_mismatches),
            "projection_invariant_ok": self.projection_invariant_ok,
            "passed": self.passed,
            "wall_seconds": round(self.wall_seconds, 3),
        }


def replay_signature(result: ReplayResult) -> str:
    """Stable signature excluding timing; byte-identical across runs."""
    payload = result.to_dict()
    payload.pop("wall_seconds", None)
    return canonical_hash(payload)


def _ledger_content_sha256(events: list[LedgerEvent]) -> str:
    digest = hashlib.sha256()
    for event in events:
        payload = {
            "sequence": event.sequence,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "recorded_at": event.recorded_at,
            "source_type": event.source_type,
            "source_id": event.source_id,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "idempotency_key": event.idempotency_key,
            "schema_version": event.schema_version,
            "kind_version": event.kind_version,
            "payload": event.payload,
            "payload_hash": event.payload_hash,
            "previous_hash": event.previous_hash,
            "header_hash": event.header_hash,
        }
        digest.update(canonical_bytes(payload))
    return digest.hexdigest()


def _chain_report_dict(report: ChainReport) -> dict[str, Any]:
    return {
        "valid": report.valid,
        "sequence_count": report.sequence_count,
        "head_hash": report.head_hash,
        "problem_count": len(report.problems),
    }


def replay_legacy(
    path: str | Path,
    *,
    table: str = "events",
    migration_version: int = 32,
    workdir: str | Path | None = None,
    catalog: frozenset[str] = HISTORICAL_EVENT_TYPE_CATALOG,
) -> ReplayResult:
    """Replay every legacy row into an R4 ledger and projection snapshot.

    The legacy database is opened read-only.  The R4 ledger is built in an
    isolated work directory and is the only object written by this function.
    """
    started = time.monotonic()
    reader = LegacyEventsReader(path, table=table, migration_version=migration_version)
    rows = reader.read_rows()
    invariant_report = validate_rows(rows)
    refusals = [
        Refusal(sequence=error.sequence, reason=error.reason)
        for error in reader.collect_refusals(rows)
    ]
    observed_types = sorted({row.event_type for row in rows})
    for event_type in observed_types:
        if event_type not in catalog:
            refusals.append(
                Refusal(
                    sequence=None,
                    reason=(
                        f"event type {event_type!r} is not in the historical compatibility catalog"
                    ),
                )
            )

    if refusals:
        return ReplayResult(
            source_path=str(path),
            table=table,
            migration_version=migration_version,
            row_count=len(rows),
            converted_count=0,
            refused_count=len(refusals),
            refusals=tuple(refusals),
            chain_valid=False,
            chain_sequence_count=0,
            chain_problem_count=0,
            head_hash=None,
            projection_snapshot_sha256=None,
            ledger_content_sha256=None,
            projection_versions={},
            event_type_counts=dict(invariant_report.event_type_counts),
            invariant_report=invariant_report,
            projection_mismatches=("replay refused",),
            projection_invariant_ok=False,
            wall_seconds=time.monotonic() - started,
        )

    converted: list[LedgerEvent] = []
    for row in rows:
        converted.append(reader.to_ledger_event(row))

    cleanup = workdir is None
    directory = (
        Path(tempfile.mkdtemp(prefix="jarvis-r5-replay-")) if workdir is None else Path(workdir)
    )
    directory.mkdir(parents=True, exist_ok=True)
    ledger_path = directory / "r4-ledger.db"
    registry = compat_kind_registry(observed_types, extra_types=[CORRECTION_EVENT_TYPE])
    ledger = Ledger(ledger_path, registry=registry, clock=FixedClock("1970-01-01T00:00:00Z"))
    with ledger.transaction() as connection:
        for event in converted:
            ledger.append_with_connection(connection, event)
    chain_report = ledger.verify_chain()
    ledger_events = ledger.replay()
    ledger_content_sha256 = _ledger_content_sha256(ledger_events)
    dispatcher = ProjectionDispatcher(build_projection_registry(observed_types))
    snapshot = dispatcher.replay(ledger_events)
    snapshot_sha256 = hashlib.sha256(snapshot_bytes(snapshot)).hexdigest()
    projection_check = check_projection_snapshot(snapshot, invariant_report)

    result = ReplayResult(
        source_path=str(path),
        table=table,
        migration_version=migration_version,
        row_count=len(rows),
        converted_count=len(converted),
        refused_count=0,
        refusals=(),
        chain_valid=chain_report.valid,
        chain_sequence_count=chain_report.sequence_count,
        chain_problem_count=len(chain_report.problems),
        head_hash=chain_report.head_hash,
        projection_snapshot_sha256=snapshot_sha256,
        ledger_content_sha256=ledger_content_sha256,
        projection_versions=snapshot.versions,
        event_type_counts=dict(invariant_report.event_type_counts),
        invariant_report=invariant_report,
        projection_mismatches=tuple(projection_check.mismatches),
        projection_invariant_ok=projection_check.ok,
        wall_seconds=time.monotonic() - started,
    )
    if cleanup:
        import shutil

        shutil.rmtree(directory, ignore_errors=True)
    return result
