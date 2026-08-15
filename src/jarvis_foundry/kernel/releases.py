"""Release registry with build/lab/shadow/canary/promote lifecycle."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import ReleaseError
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ids import DeterministicIdGenerator, IdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.records import ReleaseRecord
from jarvis_foundry.kernel.state_machines import (
    RELEASE_TRANSITIONS,
    ReleaseMachine,
    ReleaseStage,
)

RELEASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
  release_id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL,
  version TEXT NOT NULL,
  stage TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_transitions (
  transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
  release_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ReleaseFailureAdapter:
    fail_on: frozenset[str] = field(default_factory=frozenset)

    def __init__(self, fail_on: tuple[str, ...] = ()) -> None:
        object.__setattr__(self, "fail_on", frozenset(fail_on))

    def promote(self, release: ReleaseRecord) -> None:
        if "promote" in self.fail_on:
            raise ReleaseError("injected promotion failure")

    def canary(self, release: ReleaseRecord) -> None:
        if "canary" in self.fail_on:
            raise ReleaseError("injected canary failure")


class ReleaseController:
    def __init__(
        self,
        *,
        store_path: str | Path,
        ledger: Ledger,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        adapter: ReleaseFailureAdapter | None = None,
    ) -> None:
        self._store_path = Path(store_path)
        self.ledger = ledger
        self._clock = clock if clock is not None else SystemClock()
        self._ids = ids if ids is not None else DeterministicIdGenerator(seed=1)
        self._adapter = adapter if adapter is not None else ReleaseFailureAdapter()
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
            connection.executescript(RELEASE_SCHEMA)

    def create(self, *, artifact_id: str, version: str) -> ReleaseRecord:
        now = self._clock.now_utc()
        record = ReleaseRecord(
            release_id=self._ids.new_id("release"),
            artifact_id=artifact_id,
            version=version,
            stage=ReleaseStage.BUILD.value,
            created_at=now,
            updated_at=now,
        )
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO releases ("
                "release_id, artifact_id, version, stage, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.release_id,
                    artifact_id,
                    version,
                    record.stage,
                    now,
                    now,
                ),
            )
        self.ledger.append(
            LedgerEvent.create(
                event_type="release.created",
                payload={
                    "release_id": record.release_id,
                    "artifact_id": artifact_id,
                    "version": version,
                    "stage": record.stage,
                },
                source_type="release-controller",
                source_id="release-controller",
                idempotency_key=f"release.created:{record.release_id}",
                event_id=f"release-created-{record.release_id}",
            )
        )
        return record

    def get(self, release_id: str) -> ReleaseRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM releases WHERE release_id = ?", (release_id,)
            ).fetchone()
        if row is None:
            raise ReleaseError(f"unknown release: {release_id}")
        return self._record_from_row(row)

    def enter_lab(self, release_id: str) -> ReleaseRecord:
        return self._transition(release_id, "enter_lab")

    def shadow(self, release_id: str) -> ReleaseRecord:
        return self._transition(release_id, "shadow")

    def canary(self, release_id: str) -> ReleaseRecord:
        return self._transition(release_id, "canary", hook=self._adapter.canary)

    def promote(self, release_id: str) -> ReleaseRecord:
        record = self.get(release_id)
        if record.stage == ReleaseStage.PROMOTED.value:
            raise ReleaseError("release is already promoted")
        stage_order = {
            ReleaseStage.BUILD.value: "enter_lab",
            ReleaseStage.LAB.value: "shadow",
            ReleaseStage.SHADOW.value: "canary",
            ReleaseStage.CANARY.value: "promote",
        }
        current = record
        while current.stage != ReleaseStage.PROMOTED.value:
            action = stage_order.get(current.stage)
            if action is None:
                raise ReleaseError(f"cannot promote release in stage {current.stage}")
            current = self._transition(
                current.release_id,
                action,
                hook=self._adapter.canary if action == "canary" else self._adapter.promote,
            )
        return current

    def freeze(self, release_id: str) -> ReleaseRecord:
        return self._transition(release_id, "freeze")

    def rollback(self, release_id: str) -> ReleaseRecord:
        return self._transition(release_id, "rollback")

    def restore(self, release_id: str) -> ReleaseRecord:
        return self._transition(release_id, "restore")

    def quarantine(self, release_id: str) -> ReleaseRecord:
        return self._transition(release_id, "quarantine")

    def _transition(
        self,
        release_id: str,
        action: str,
        *,
        hook: Any = None,
    ) -> ReleaseRecord:
        record = self.get(release_id)
        machine = ReleaseMachine(record.stage)
        allowed = RELEASE_TRANSITIONS.get(machine.stage, {})
        if action not in allowed:
            raise ReleaseError(f"transition {action!r} not allowed from {record.stage}")
        try:
            if hook is not None:
                hook(record)
        except ReleaseError:
            self._persist(release_id, ReleaseStage.QUARANTINED.value)
            raise
        target = allowed[action].value
        return self._persist(release_id, target)

    def _persist(self, release_id: str, stage: str) -> ReleaseRecord:
        now = self._clock.now_utc()
        with self.connect() as connection:
            connection.execute(
                "UPDATE releases SET stage = ?, updated_at = ? WHERE release_id = ?",
                (stage, now, release_id),
            )
            connection.execute(
                "INSERT INTO release_transitions (release_id, stage, occurred_at) VALUES (?, ?, ?)",
                (release_id, stage, now),
            )
        self.ledger.append(
            LedgerEvent.create(
                event_type="release.transitioned",
                payload={"release_id": release_id, "stage": stage, "updated_at": now},
                source_type="release-controller",
                source_id="release-controller",
                idempotency_key=f"release.transitioned:{release_id}:{stage}:{now}",
                event_id=f"release-transitioned-{release_id}-{stage}-{now}",
            )
        )
        return self.get(release_id)

    def _record_from_row(self, row: sqlite3.Row) -> ReleaseRecord:
        return ReleaseRecord(
            release_id=str(row["release_id"]),
            artifact_id=str(row["artifact_id"]),
            version=str(row["version"]),
            stage=str(row["stage"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
