"""Dual-run and cutover rehearsal with loss/duplication accounting."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.compat.readers import LegacyEventsReader
from jarvis_foundry.kernel.compat.registry import compat_kind_registry
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ledger import Ledger

CUTOVER_SYNTHETIC_TYPE = "compat.cutover.synthetic"


@dataclass(frozen=True)
class CutoverReport:
    legacy_count: int
    r4_count: int
    matched_ids: int
    legacy_only_ids: tuple[str, ...]
    r4_only_ids: tuple[str, ...]
    duplicated_ids: tuple[str, ...]
    chain_valid: bool
    lossless: bool
    duplicate_free: bool
    reconciliation_clean: bool
    idempotency_signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "legacy_count": self.legacy_count,
            "r4_count": self.r4_count,
            "matched_ids": self.matched_ids,
            "legacy_only_ids": list(self.legacy_only_ids),
            "r4_only_ids": list(self.r4_only_ids),
            "duplicated_ids": list(self.duplicated_ids),
            "chain_valid": self.chain_valid,
            "lossless": self.lossless,
            "duplicate_free": self.duplicate_free,
            "reconciliation_clean": self.reconciliation_clean,
            "idempotency_signature": self.idempotency_signature,
        }


def _legacy_payload_hash(payload: Mapping[str, Any]) -> str:
    return canonical_hash(payload)


def _append_legacy_row(
    connection: sqlite3.Connection,
    *,
    index: int,
    recorded_at: str,
) -> str:
    event_id = f"cutover-synthetic-{index:04d}"
    payload = {"index": index, "synthetic": True}
    payload_hash = _legacy_payload_hash(payload)
    connection.execute(
        "INSERT INTO events ("
        "sequence, event_id, event_type, occurred_at, recorded_at, source_type, "
        "source_id, correlation_id, causation_id, idempotency_key, schema_version, "
        "payload_json, payload_hash"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            None,
            event_id,
            CUTOVER_SYNTHETIC_TYPE,
            recorded_at,
            recorded_at,
            "r5-cutover-gate",
            "cutover-rehearsal",
            event_id,
            None,
            f"cutover-synthetic:{index}",
            1,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            payload_hash,
        ),
    )
    return event_id


def rehearse_dual_run(
    legacy_path: str | Path,
    ledger_path: str | Path,
    *,
    synthetic_count: int = 3,
    ledger_workdir: str | Path | None = None,
    recorded_at: str = "2026-08-15T00:00:00Z",
) -> CutoverReport:
    """Rehearse dual-run writes and cutover on a legacy copy and R4 ledger."""
    legacy_path = Path(legacy_path)
    reader = LegacyEventsReader(legacy_path)
    legacy_before = reader.read_rows()

    registry = compat_kind_registry(
        {row.event_type for row in legacy_before},
        extra_types=[CUTOVER_SYNTHETIC_TYPE],
    )
    ledger = Ledger(ledger_path, registry=registry, clock=FixedClock(recorded_at))
    with ledger.transaction() as connection:
        for row in legacy_before:
            event = reader.to_ledger_event(row)
            ledger.append_with_connection(connection, event)

    # Dual-run: both legacy and R4 receive the same synthetic events.
    synthetic_ids: list[str] = []
    legacy_connection = sqlite3.connect(legacy_path)
    try:
        with legacy_connection:
            for index in range(1, synthetic_count + 1):
                synthetic_ids.append(
                    _append_legacy_row(legacy_connection, index=index, recorded_at=recorded_at)
                )
        for index in range(1, synthetic_count + 1):
            ledger.append(
                LedgerEvent.create(
                    event_type=CUTOVER_SYNTHETIC_TYPE,
                    payload={"index": index, "synthetic": True},
                    source_type="r5-cutover-gate",
                    source_id="cutover-rehearsal",
                    idempotency_key=f"cutover-synthetic:{index}",
                    event_id=f"cutover-synthetic-{index:04d}",
                    occurred_at=recorded_at,
                    recorded_at=recorded_at,
                )
            )
    finally:
        legacy_connection.close()

    legacy_final = reader.read_rows()
    legacy_ids_final = [row.event_id for row in legacy_final]
    r4_ids_final = [event.event_id for event in ledger.replay()]
    chain_report = ledger.verify_chain()
    legacy_set = set(legacy_ids_final)
    r4_set = set(r4_ids_final)
    legacy_only = tuple(sorted(legacy_set - r4_set))
    r4_only = tuple(sorted(r4_set - legacy_set))
    duplicated = tuple(
        sorted(event_id for event_id in set(r4_ids_final) if r4_ids_final.count(event_id) > 1)
    )
    lossless = not legacy_only
    duplicate_free = not duplicated
    reconciliation_clean = lossless and duplicate_free and not r4_only
    matched = len(legacy_set & r4_set)
    signature_payload = {
        "legacy_count": len(legacy_ids_final),
        "r4_count": len(r4_ids_final),
        "legacy_only": list(legacy_only),
        "r4_only": list(r4_only),
        "duplicated": list(duplicated),
        "chain_valid": chain_report.valid,
        "synthetic_ids": synthetic_ids,
    }
    return CutoverReport(
        legacy_count=len(legacy_ids_final),
        r4_count=len(r4_ids_final),
        matched_ids=matched,
        legacy_only_ids=legacy_only,
        r4_only_ids=r4_only,
        duplicated_ids=duplicated,
        chain_valid=chain_report.valid,
        lossless=lossless,
        duplicate_free=duplicate_free,
        reconciliation_clean=reconciliation_clean,
        idempotency_signature=canonical_hash(signature_payload),
    )
