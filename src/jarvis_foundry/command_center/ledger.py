"""Append-only, hash-bound JSONL ledger for canonical owner directives."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash


class DirectiveLedgerError(RuntimeError):
    pass


class DirectiveLedgerIntegrityError(DirectiveLedgerError):
    pass


class DirectiveLedgerConflictError(DirectiveLedgerError):
    pass


@dataclass(frozen=True)
class LedgerEvent:
    sequence: int
    event_id: str
    event_type: str
    payload: dict[str, Any]
    recorded_at: str
    source_type: str
    source_id: str
    payload_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "recorded_at": self.recorded_at,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "payload_hash": self.payload_hash,
        }


class DirectiveLedger:
    """Canonical durable directive events independent of any UI process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._events: list[LedgerEvent] = []
        self._by_id: dict[str, LedgerEvent] = {}
        self._rebuild()

    def _rebuild(self) -> None:
        self._events = []
        self._by_id = {}
        if not self.path.is_file():
            return
        for line_no, raw in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            marker = raw.rfind("|")
            if marker < 0:
                raise DirectiveLedgerIntegrityError(
                    f"directive ledger line {line_no} has no hash marker"
                )
            body, digest = raw[:marker], raw[marker + 1 :]
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise DirectiveLedgerIntegrityError(
                    f"directive ledger line {line_no} is not JSON"
                ) from exc
            if canonical_hash(payload) != digest:
                raise DirectiveLedgerIntegrityError(
                    f"directive ledger line {line_no} failed hash verification"
                )
            event = self._event_from_payload(payload, len(self._events) + 1)
            if event.event_id in self._by_id:
                raise DirectiveLedgerIntegrityError(
                    f"directive ledger repeats event id {event.event_id}"
                )
            self._events.append(event)
            self._by_id[event.event_id] = event

    @staticmethod
    def _event_from_payload(payload: dict[str, Any], sequence: int) -> LedgerEvent:
        try:
            return LedgerEvent(
                sequence=sequence,
                event_id=str(payload["event_id"]),
                event_type=str(payload["event_type"]),
                payload=dict(payload["payload"]),
                recorded_at=str(payload["recorded_at"]),
                source_type=str(payload.get("source_type", "")),
                source_id=str(payload.get("source_id", "")),
                payload_hash=str(payload["payload_hash"]),
            )
        except KeyError as exc:
            raise DirectiveLedgerIntegrityError(
                f"directive ledger event {sequence} misses key {exc.args[0]}"
            ) from exc

    def append(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        recorded_at: str,
        source_type: str = "owner",
        source_id: str = "r10-owner",
    ) -> LedgerEvent:
        if not event_id or not event_type:
            raise DirectiveLedgerError("event_id and event_type are required")
        if event_id in self._by_id:
            raise DirectiveLedgerConflictError(f"directive event already exists: {event_id}")
        payload_hash = canonical_hash(payload)
        body = {
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
            "recorded_at": recorded_at,
            "source_type": source_type,
            "source_id": source_id,
            "payload_hash": payload_hash,
        }
        digest = canonical_hash(body)
        line = json.dumps(body, sort_keys=True, separators=(",", ":")) + f"|{digest}\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        event = LedgerEvent(
            sequence=len(self._events) + 1,
            event_id=event_id,
            event_type=event_type,
            payload=dict(payload),
            recorded_at=recorded_at,
            source_type=source_type,
            source_id=source_id,
            payload_hash=payload_hash,
        )
        self._events.append(event)
        self._by_id[event_id] = event
        return event

    def events(self) -> list[LedgerEvent]:
        return list(self._events)

    def verify(self) -> None:
        self._rebuild()

    def count(self) -> int:
        return len(self._events)

    def sha256(self) -> str:
        import hashlib

        digest = hashlib.sha256()
        if self.path.is_file():
            digest.update(self.path.read_bytes())
        return digest.hexdigest()
