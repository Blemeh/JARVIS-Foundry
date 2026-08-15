"""Authenticated owner controls with nonce anti-replay and durable directives."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import AuthorityError, NonceReplayError
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ids import DeterministicIdGenerator, IdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.ports import StopGate
from jarvis_foundry.kernel.records import OwnerDirective

DIRECTIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_directives (
  directive_id TEXT PRIMARY KEY,
  command_type TEXT NOT NULL,
  target TEXT,
  nonce TEXT NOT NULL UNIQUE,
  exact_text TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  status TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  active INTEGER NOT NULL CHECK (active IN (0, 1))
);
"""


def sign_command(
    secret: bytes,
    command_type: str,
    target: str | None,
    nonce: str,
    exact_text: str,
    channel_id: str = "owner",
) -> str:
    payload = "|".join([channel_id, command_type, target or "", nonce, exact_text])
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_command(
    secret: bytes,
    command_type: str,
    target: str | None,
    nonce: str,
    exact_text: str,
    channel_id: str,
    signature: str,
) -> bool:
    expected = sign_command(secret, command_type, target, nonce, exact_text, channel_id)
    return hmac.compare_digest(expected, signature)


class FileStopGate:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def activate(self, reason: str, source: str) -> Mapping[str, str]:
        record = {
            "stop_id": "stop-sentinel",
            "reason": reason,
            "source": source,
            "activated_at": SystemClock().now_utc(),
        }
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        temporary.replace(self._path)
        return record

    def clear(self) -> bool:
        if not self._path.exists():
            return False
        self._path.unlink()
        return True

    def active(self) -> bool:
        return self._path.exists()


class OwnerControlService:
    """Owner-only commands persisted atomically with the canonical ledger."""

    def __init__(
        self,
        ledger: Ledger,
        *,
        stop_gate: StopGate,
        secret: bytes,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        owner_channel: str = "owner",
    ) -> None:
        self.ledger = ledger
        self._stop_gate = stop_gate
        self._secret = secret
        self._clock = clock if clock is not None else SystemClock()
        self._ids = ids if ids is not None else DeterministicIdGenerator(seed=1)
        self._owner_channel = owner_channel
        self._fail_append = False
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.ledger.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(DIRECTIVE_SCHEMA)

    def apply_command(
        self,
        *,
        command_type: str,
        target: str | None,
        exact_text: str,
        nonce: str,
        signature: str,
        channel_id: str = "owner",
    ) -> OwnerDirective:
        if channel_id != self._owner_channel:
            raise AuthorityError("command channel is not the authenticated owner channel")
        if not verify_command(
            self._secret, command_type, target, nonce, exact_text, channel_id, signature
        ):
            raise AuthorityError("owner command signature is invalid")
        recorded_at = self._clock.now_utc()
        directive = OwnerDirective(
            directive_id=self._ids.new_id("directive"),
            command_type=command_type,
            target=target,
            nonce=nonce,
            exact_text=exact_text,
            recorded_at=recorded_at,
            status="ACTIVE",
            channel_id=channel_id,
        )
        event = LedgerEvent.create(
            event_type="owner.directive.recorded",
            payload={
                "directive_id": directive.directive_id,
                "command_type": command_type,
                "target": target,
                "nonce": nonce,
                "recorded_at": recorded_at,
                "channel_id": channel_id,
            },
            source_type="owner-control",
            source_id=channel_id,
            idempotency_key=f"owner.directive:{nonce}",
            event_id=directive.directive_id,
            occurred_at=recorded_at,
        )
        with self.ledger.transaction() as connection:
            used = connection.execute(
                "SELECT nonce FROM owner_directives WHERE nonce = ?", (nonce,)
            ).fetchone()
            if used is not None:
                raise NonceReplayError("owner command nonce already used", nonce=nonce)
            if self._fail_append:
                raise RuntimeError("injected crash before ledger append")
            active_value = 1
            directive_status = "ACTIVE"
            if command_type == "RESUME":
                connection.execute(
                    "UPDATE owner_directives SET active = 0, status = 'CLEARED' "
                    "WHERE command_type IN ('STOP', 'PAUSE') AND active = 1"
                )
                if self._stop_gate.active():
                    self._stop_gate.clear()
                active_value = 0
                directive_status = "INACTIVE"
            connection.execute(
                "INSERT INTO owner_directives ("
                "directive_id, command_type, target, nonce, exact_text, recorded_at, "
                "status, channel_id, active"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    directive.directive_id,
                    command_type,
                    target,
                    nonce,
                    exact_text,
                    recorded_at,
                    directive_status,
                    channel_id,
                    active_value,
                ),
            )
            self.ledger.append_with_connection(connection, event)
            if command_type == "STOP":
                self._stop_gate.activate(f"owner STOP: {exact_text}", source=channel_id)
        self.ledger._notify(event)
        return directive

    def stop(
        self, *, nonce: str, signature: str, exact_text: str, target: str = "GLOBAL"
    ) -> OwnerDirective:
        return self.apply_command(
            command_type="STOP",
            target=target,
            exact_text=exact_text,
            nonce=nonce,
            signature=signature,
        )

    def veto(self, *, nonce: str, signature: str, exact_text: str, target: str) -> OwnerDirective:
        return self.apply_command(
            command_type="VETO",
            target=target,
            exact_text=exact_text,
            nonce=nonce,
            signature=signature,
        )

    def pause(
        self, *, nonce: str, signature: str, exact_text: str, target: str = "GLOBAL"
    ) -> OwnerDirective:
        return self.apply_command(
            command_type="PAUSE",
            target=target,
            exact_text=exact_text,
            nonce=nonce,
            signature=signature,
        )

    def resume(
        self, *, nonce: str, signature: str, exact_text: str, target: str = "GLOBAL"
    ) -> OwnerDirective:
        return self.apply_command(
            command_type="RESUME",
            target=target,
            exact_text=exact_text,
            nonce=nonce,
            signature=signature,
        )

    def redirect(
        self, *, nonce: str, signature: str, exact_text: str, target: str
    ) -> OwnerDirective:
        return self.apply_command(
            command_type="REDIRECT",
            target=target,
            exact_text=exact_text,
            nonce=nonce,
            signature=signature,
        )

    def reprioritize(
        self, *, nonce: str, signature: str, exact_text: str, target: str
    ) -> OwnerDirective:
        return self.apply_command(
            command_type="REPRIORITIZE",
            target=target,
            exact_text=exact_text,
            nonce=nonce,
            signature=signature,
        )

    def active_directives(self) -> list[OwnerDirective]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM owner_directives WHERE active = 1 ORDER BY recorded_at"
            ).fetchall()
        return [
            OwnerDirective(
                directive_id=str(row["directive_id"]),
                command_type=str(row["command_type"]),
                target=row["target"],
                nonce=str(row["nonce"]),
                exact_text=str(row["exact_text"]),
                recorded_at=str(row["recorded_at"]),
                status=str(row["status"]),
                channel_id=str(row["channel_id"]),
            )
            for row in rows
        ]
