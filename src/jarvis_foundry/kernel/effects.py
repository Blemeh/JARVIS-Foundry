"""Effect intents, receipts, reconciliation, and compensation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import EffectError
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ids import DeterministicIdGenerator, IdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.ports import SystemAdapterPort
from jarvis_foundry.kernel.records import (
    EffectIntent,
    EffectReceipt,
    ReconciliationResult,
)

EFFECT_SCHEMA = """
CREATE TABLE IF NOT EXISTS effect_intents (
  intent_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  action_envelope_id TEXT NOT NULL,
  system TEXT NOT NULL,
  operation TEXT NOT NULL,
  params TEXT NOT NULL CHECK (json_valid(params)),
  phase TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS effect_receipts (
  receipt_id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL REFERENCES effect_intents(intent_id),
  status TEXT NOT NULL,
  outcome TEXT NOT NULL,
  performed_at TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  compensation_of TEXT
);
CREATE TABLE IF NOT EXISTS reconciliations (
  result_id TEXT PRIMARY KEY,
  intent_ids TEXT NOT NULL CHECK (json_valid(intent_ids)),
  reconciliation_status TEXT NOT NULL,
  matched INTEGER NOT NULL CHECK (matched IN (0, 1)),
  detail TEXT NOT NULL,
  evidence TEXT NOT NULL CHECK (json_valid(evidence)),
  compensated_intent_ids TEXT NOT NULL CHECK (json_valid(compensated_intent_ids))
);
"""


@dataclass(frozen=True)
class PreparedEffect:
    effect_id: str
    intent_id: str
    system: str
    operation: str
    params: Mapping[str, Any]
    prepared_at: str


@dataclass(frozen=True)
class RecoveryReport:
    recovered_intents: tuple[str, ...]
    at: str


class RecordingSystemAdapter:
    """Deterministic in-process adapter used by tests and lab evidence."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def prepare(self, intent: EffectIntent) -> PreparedEffect:
        self.calls.append(f"prepare:{intent.intent_id}")
        return PreparedEffect(
            effect_id=f"prepared-{intent.intent_id}",
            intent_id=intent.intent_id,
            system=intent.system,
            operation=intent.operation,
            params=dict(intent.params),
            prepared_at="2026-08-15T00:00:00Z",
        )

    def execute(self, prepared: PreparedEffect) -> Mapping[str, Any]:
        self.calls.append(f"execute:{prepared.intent_id}")
        return {"status": "ok", "effect_id": prepared.effect_id}

    def inspect(self, intent_id: str) -> list[EffectReceipt]:
        self.calls.append(f"inspect:{intent_id}")
        return []

    def reconcile(self, intent_id: str) -> Mapping[str, Any]:
        self.calls.append(f"reconcile:{intent_id}")
        return {"status": "reconciled"}

    def compensate(self, intent_id: str) -> Mapping[str, Any]:
        self.calls.append(f"compensate:{intent_id}")
        return {"status": "compensated"}


class EffectEngine:
    """Durable effect execution with idempotency, recovery, and compensation."""

    def __init__(
        self,
        *,
        store_path: str | Path,
        adapter: SystemAdapterPort,
        ledger: Ledger,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
    ) -> None:
        self._store_path = Path(store_path)
        self._adapter = adapter
        self.ledger = ledger
        self._clock = clock if clock is not None else SystemClock()
        self._ids = ids if ids is not None else DeterministicIdGenerator(seed=1)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._store_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(EFFECT_SCHEMA)

    def submit(self, intent: EffectIntent) -> EffectIntent:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT intent_id FROM effect_intents WHERE intent_id = ?", (intent.intent_id,)
            ).fetchone()
            if existing is not None:
                return intent
            connection.execute(
                "INSERT INTO effect_intents ("
                "intent_id, idempotency_key, action_envelope_id, system, operation, "
                "params, phase, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                (
                    intent.intent_id,
                    intent.idempotency_key,
                    intent.action_envelope_id,
                    intent.system,
                    intent.operation,
                    json.dumps(dict(intent.params), sort_keys=True),
                    self._clock.now_utc(),
                ),
            )
        self.ledger.append(
            LedgerEvent.create(
                event_type="effect.intent",
                payload={
                    "intent_id": intent.intent_id,
                    "idempotency_key": intent.idempotency_key,
                    "action_envelope_id": intent.action_envelope_id,
                    "system": intent.system,
                    "operation": intent.operation,
                },
                source_type="effect-engine",
                source_id=intent.action_envelope_id,
                idempotency_key=intent.idempotency_key,
                event_id=intent.intent_id,
            )
        )
        return intent

    def execute(self, intent_id: str) -> EffectReceipt:
        intent = self._load_intent(intent_id)
        existing = self._latest_receipt(intent_id)
        if existing is not None and existing.status in {"SUCCEEDED", "COMPENSATED"}:
            return existing
        phase = str(intent["phase"])
        prepared: PreparedEffect | None = None
        if phase != "PREPARED":
            prepared = self._adapter.prepare(self._to_record(intent))
            self._set_phase(intent_id, "PREPARED")
        if prepared is None:
            prepared = PreparedEffect(
                effect_id=f"prepared-{intent_id}",
                intent_id=intent_id,
                system=str(intent["system"]),
                operation=str(intent["operation"]),
                params=json.loads(str(intent["params"])),
                prepared_at=self._clock.now_utc(),
            )
        self._set_phase(intent_id, "EXECUTING")
        try:
            outcome = self._adapter.execute(prepared)
        except EffectError as error:
            return self._record_receipt(intent_id, "FAILED", str(error), "effect-engine")
        self._set_phase(intent_id, "SUCCEEDED")
        return self._record_receipt(
            intent_id,
            "SUCCEEDED",
            json.dumps(dict(outcome), sort_keys=True),
            "effect-engine",
        )

    def inspect(self, intent_id: str) -> list[EffectReceipt]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM effect_receipts WHERE intent_id = ? ORDER BY performed_at",
                (intent_id,),
            ).fetchall()
        return [self._receipt_from_row(row) for row in rows]

    def compensate(self, intent_id: str) -> EffectReceipt:
        self._load_intent(intent_id)
        outcome = self._adapter.compensate(intent_id)
        self._set_phase(intent_id, "COMPENSATED")
        return self._record_receipt(
            intent_id,
            "COMPENSATED",
            json.dumps(dict(outcome), sort_keys=True),
            "effect-engine",
        )

    def reconcile(self, intent_ids: Sequence[str]) -> ReconciliationResult:
        latest: dict[str, EffectReceipt | None] = {
            intent_id: self._latest_receipt(intent_id) for intent_id in intent_ids
        }
        statuses = [receipt.status for receipt in latest.values() if receipt is not None]
        succeeded = [
            intent_id
            for intent_id, receipt in latest.items()
            if receipt is not None and receipt.status == "SUCCEEDED"
        ]
        failed = [
            intent_id
            for intent_id, receipt in latest.items()
            if receipt is not None and receipt.status == "FAILED"
        ]
        compensated: list[str] = []
        if succeeded and failed:
            for intent_id in succeeded:
                self.compensate(intent_id)
                compensated.append(intent_id)
            reconciliation_status = "PARTIAL_SUCCESS"
            matched = True
            detail = f"compensated {compensated} after failures {failed}"
        elif statuses and all(status == "SUCCEEDED" for status in statuses):
            reconciliation_status = "SUCCESS"
            matched = True
            detail = "all effects succeeded"
        else:
            reconciliation_status = "FAILED"
            matched = False
            detail = f"unrecoverable failures: {failed}"
        result = ReconciliationResult(
            result_id=self._ids.new_id("reconciliation"),
            intent_ids=tuple(intent_ids),
            reconciliation_status=reconciliation_status,
            matched=matched,
            detail=detail,
            evidence={"statuses": statuses},
            compensated_intent_ids=tuple(compensated),
        )
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO reconciliations ("
                "result_id, intent_ids, reconciliation_status, matched, detail, "
                "evidence, compensated_intent_ids"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    result.result_id,
                    json.dumps(list(result.intent_ids)),
                    result.reconciliation_status,
                    1 if result.matched else 0,
                    result.detail,
                    json.dumps(result.evidence, sort_keys=True),
                    json.dumps(list(result.compensated_intent_ids)),
                ),
            )
        return result

    def recover(self, *, at: str | None = None) -> RecoveryReport:
        timestamp = at if at is not None else self._clock.now_utc()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT intent_id FROM effect_intents "
                "WHERE phase IN ('PREPARED', 'EXECUTING', 'FAILED') ORDER BY created_at"
            ).fetchall()
        recovered: list[str] = []
        for row in rows:
            intent_id = str(row["intent_id"])
            self.execute(intent_id)
            recovered.append(intent_id)
        return RecoveryReport(recovered_intents=tuple(recovered), at=timestamp)

    def _load_intent(self, intent_id: str) -> sqlite3.Row:
        with self.connect() as connection:
            row: sqlite3.Row | None = connection.execute(
                "SELECT * FROM effect_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        if row is None:
            raise EffectError(f"unknown effect intent: {intent_id}")
        return row

    def _to_record(self, row: sqlite3.Row) -> EffectIntent:
        return EffectIntent(
            intent_id=str(row["intent_id"]),
            idempotency_key=str(row["idempotency_key"]),
            action_envelope_id=str(row["action_envelope_id"]),
            system=str(row["system"]),
            operation=str(row["operation"]),
            params=json.loads(str(row["params"])),
        )

    def _set_phase(self, intent_id: str, phase: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE effect_intents SET phase = ? WHERE intent_id = ?",
                (phase, intent_id),
            )

    def _latest_receipt(self, intent_id: str) -> EffectReceipt | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM effect_receipts WHERE intent_id = ? "
                "ORDER BY performed_at DESC LIMIT 1",
                (intent_id,),
            ).fetchone()
        return self._receipt_from_row(row) if row is not None else None

    def _record_receipt(
        self, intent_id: str, status: str, outcome: str, adapter_id: str
    ) -> EffectReceipt:
        receipt = EffectReceipt(
            receipt_id=self._ids.new_id("receipt"),
            intent_id=intent_id,
            status=status,
            outcome=outcome,
            performed_at=self._clock.now_utc(),
            adapter_id=adapter_id,
        )
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO effect_receipts ("
                "receipt_id, intent_id, status, outcome, performed_at, adapter_id, "
                "compensation_of"
                ") VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    receipt.receipt_id,
                    intent_id,
                    status,
                    outcome,
                    receipt.performed_at,
                    adapter_id,
                ),
            )
        self.ledger.append(
            LedgerEvent.create(
                event_type="effect.receipt",
                payload={
                    "receipt_id": receipt.receipt_id,
                    "intent_id": intent_id,
                    "status": status,
                },
                source_type="effect-engine",
                source_id=adapter_id,
                idempotency_key=f"effect.receipt:{receipt.receipt_id}",
                event_id=receipt.receipt_id,
            )
        )
        return receipt

    def _receipt_from_row(self, row: sqlite3.Row) -> EffectReceipt:
        return EffectReceipt(
            receipt_id=str(row["receipt_id"]),
            intent_id=str(row["intent_id"]),
            status=str(row["status"]),
            outcome=str(row["outcome"]),
            performed_at=str(row["performed_at"]),
            adapter_id=str(row["adapter_id"]),
        )
