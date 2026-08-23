"""Authenticated owner controls for the roadmap-V4 surface.

The policy broker and sentinel remain the enforcement points.  This module
only authenticates a local owner command and records it through the same
``owner_directive.*`` event path already used by the Supervisor.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .clock import isoformat
from .database import FoundryDatabase
from .events import Event, canonical_json
from .supervisor.estop import EmergencyStop


class OwnerAuthenticationError(PermissionError):
    pass


class OwnerReplayError(OwnerAuthenticationError):
    pass


class OwnerCommandError(ValueError):
    pass


_COMMANDS = frozenset({
    "GOAL", "CONSTRAINT", "ADD_CONSTRAINT", "VETO_ACTION", "STOP_MISSION", "STOP_EVERYTHING",
    "PAUSE", "RESUME", "REPRIORITIZE", "REDIRECT", "RESTART_RECOVERY",
})


def canonical_command(command_type: str, target: str | None, exact_text: str, nonce: str) -> bytes:
    if command_type not in _COMMANDS:
        raise OwnerCommandError(f"unsupported owner command: {command_type}")
    if not nonce or not exact_text.strip():
        raise OwnerCommandError("nonce and exact_text are required")
    return canonical_json({
        "command_type": command_type,
        "target": target,
        "exact_text": exact_text,
        "nonce": nonce,
    }).encode("utf-8")


def sign_command(secret: bytes, command_type: str, target: str | None, exact_text: str, nonce: str) -> str:
    return hmac.new(secret, canonical_command(command_type, target, exact_text, nonce), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class OwnerCommandResult:
    directive_id: str
    event_id: str
    command_type: str
    nonce: str
    superseded_directive_id: str | None = None


class OwnerControlService:
    def __init__(self, database: FoundryDatabase, *, secret: bytes, stop_path: str | Path | None = None, owner_id: str = "local-owner") -> None:
        if not secret:
            raise OwnerAuthenticationError("an owner secret is required")
        self.database = database
        self.secret = bytes(secret)
        self.stop = EmergencyStop(stop_path) if stop_path is not None else None
        self.owner_id = owner_id

    def _verify(self, command_type: str, target: str | None, exact_text: str, nonce: str, signature: str) -> None:
        expected = sign_command(self.secret, command_type, target, exact_text, nonce)
        if not hmac.compare_digest(expected, signature or ""):
            raise OwnerAuthenticationError("owner command authentication failed")
        with self.database.connect() as connection:
            used = connection.execute("SELECT 1 FROM control_nonces WHERE nonce = ?", (nonce,)).fetchone()
        if used:
            raise OwnerReplayError(f"owner command nonce already used: {nonce}")

    def _existing_directive(self, directive_id: str) -> Event | None:
        for event in self.database.events(readonly=True):
            if event.event_type != "owner_directive.recorded":
                continue
            if event.payload.get("directive_id") == directive_id:
                return event
        return None

    def _existing_directive_id(self, directive_id: str) -> bool:
        return self._existing_directive(directive_id) is not None

    def apply(
        self,
        command_type: str,
        *,
        target: str | None,
        exact_text: str,
        nonce: str,
        signature: str,
        directive_id: str | None = None,
        supersedes_directive_id: str | None = None,
        expires_at: str | None = None,
    ) -> OwnerCommandResult:
        self._verify(command_type, target, exact_text, nonce, signature)
        directive_id = directive_id or f"owner-{secrets.token_hex(12)}"
        if self._existing_directive_id(directive_id):
            raise OwnerCommandError(
                f"directive_id already admitted: {directive_id}; use a new directive_id"
            )
        if supersedes_directive_id is not None:
            if not supersedes_directive_id.strip() or supersedes_directive_id == directive_id:
                raise OwnerCommandError("supersedes_directive_id must identify another directive")
            if not self._existing_directive_id(supersedes_directive_id):
                raise OwnerCommandError(
                    f"cannot supersede unknown directive: {supersedes_directive_id}"
                )
        now = isoformat()
        command_hash = hashlib.sha256(canonical_command(command_type, target, exact_text, nonce)).hexdigest()
        nonce_event = Event.create(
            "owner_control.nonce_used",
            {"nonce": nonce, "command_hash": command_hash, "issued_at": now, "used_at": now},
            source_type="owner", source_id=self.owner_id,
            idempotency_key=f"owner_control.nonce_used:{nonce}",
        )
        directive_event = Event.create(
            "owner_directive.recorded",
            {"directive_id": directive_id, "command_type": command_type, "target": target,
             "exact_text": exact_text, "authenticated": True, "nonce": nonce,
             "command_hash": command_hash, "reason": exact_text,
             "owner_id": self.owner_id, "source_identity": f"owner:{self.owner_id}",
             "admission_version": "v4.1", "supersedes": supersedes_directive_id,
             "expires_at": expires_at},
            source_type="owner", source_id=self.owner_id,
            correlation_id=nonce_event.correlation_id,
            causation_id=nonce_event.event_id,
            idempotency_key=f"owner_directive.recorded:{directive_id}:{command_hash}",
        )
        events = [nonce_event, directive_event]
        if supersedes_directive_id is not None:
            events.append(Event.create(
                "owner_directive.superseded",
                {
                    "directive_id": supersedes_directive_id,
                    "superseded_by": directive_id,
                    "reason": exact_text,
                    "owner_id": self.owner_id,
                },
                source_type="owner", source_id=self.owner_id,
                correlation_id=nonce_event.correlation_id,
                causation_id=directive_event.event_id,
                idempotency_key=(
                    f"owner_directive.superseded:{supersedes_directive_id}:{directive_id}"
                ),
            ))
        # The nonce and directive are one atomic ledger batch.  A retry cannot
        # create a second directive, and a partial authentication cannot leave
        # an owner command without its replay marker.
        self.database.append_events(events)
        if command_type == "STOP_EVERYTHING" and self.stop is not None:
            # The filesystem sentinel is intentionally written after the
            # durable directive; if the process dies before this line, the
            # durable STOP still blocks through emergency_stop_reason().
            self.stop.activate(exact_text, source="authenticated-owner")
        return OwnerCommandResult(
            directive_id, directive_event.event_id, command_type, nonce,
            supersedes_directive_id,
        )

    def revoke(self, directive_id: str, *, nonce: str, signature: str, exact_text: str = "revoke owner directive") -> str:
        self._verify("RESUME", directive_id, exact_text, nonce, signature)
        now = isoformat()
        command_hash = hashlib.sha256(canonical_command("RESUME", directive_id, exact_text, nonce)).hexdigest()
        nonce_event = Event.create(
            "owner_control.nonce_used",
            {"nonce": nonce, "command_hash": command_hash, "issued_at": now, "used_at": now},
            source_type="owner", source_id=self.owner_id,
            idempotency_key=f"owner_control.nonce_used:{nonce}",
        )
        event = Event.create(
            "owner_directive.revoked",
            {
                "directive_id": directive_id, "nonce": nonce,
                "authenticated": True, "reason": exact_text,
                "owner_id": self.owner_id,
            },
            source_type="owner", source_id=self.owner_id,
            correlation_id=nonce_event.correlation_id,
            causation_id=nonce_event.event_id,
            idempotency_key=f"owner_directive.revoked:{directive_id}:{nonce}",
        )
        self.database.append_events([nonce_event, event])
        return event.event_id

    def stop_now(self, *, reason: str, nonce: str, signature: str) -> OwnerCommandResult:
        return self.apply("STOP_EVERYTHING", target="GLOBAL", exact_text=reason, nonce=nonce, signature=signature)

    def reprioritize(self, *, target: str, exact_text: str, nonce: str,
                     signature: str,
                     directive_id: str | None = None) -> OwnerCommandResult:
        """Record an authenticated priority change on the canonical ledger."""
        return self.apply(
            "REPRIORITIZE", target=target, exact_text=exact_text,
            nonce=nonce, signature=signature, directive_id=directive_id,
        )

    def redirect(self, *, target: str, exact_text: str, nonce: str,
                 signature: str,
                 directive_id: str | None = None) -> OwnerCommandResult:
        """Record an authenticated mission/project redirect on the ledger."""
        return self.apply(
            "REDIRECT", target=target, exact_text=exact_text,
            nonce=nonce, signature=signature, directive_id=directive_id,
        )

    def resume(self, *, nonce: str, signature: str, exact_text: str = "resume jarvis") -> list[str]:
        self._verify("RESUME", "GLOBAL", exact_text, nonce, signature)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT directive_id FROM directives WHERE active = 1 AND command_type IN ('STOP_EVERYTHING', 'PAUSE')"
            ).fetchall()
        now = isoformat()
        command_hash = hashlib.sha256(canonical_command("RESUME", "GLOBAL", exact_text, nonce)).hexdigest()
        events = [Event.create(
            "owner_control.nonce_used",
            {"nonce": nonce, "command_hash": command_hash, "issued_at": now, "used_at": now},
            source_type="owner", source_id=self.owner_id,
            idempotency_key=f"owner_control.nonce_used:{nonce}",
        )]
        event_ids = []
        for row in rows:
            event = Event.create(
                "owner_directive.revoked", {
                    "directive_id": str(row[0]), "nonce": nonce,
                    "authenticated": True, "reason": exact_text,
                    "owner_id": self.owner_id,
                },
                source_type="owner", source_id=self.owner_id,
                correlation_id=events[0].correlation_id,
                causation_id=events[0].event_id,
                idempotency_key=f"owner_directive.revoked:{row[0]}:{nonce}",
            )
            events.append(event)
            event_ids.append(event.event_id)
        self.database.append_events(events)
        if self.stop is not None:
            self.stop.clear()
        return event_ids
