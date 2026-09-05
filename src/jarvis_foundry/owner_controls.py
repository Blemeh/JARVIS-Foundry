"""Authenticated owner controls for the roadmap-V4 surface.

The policy broker and sentinel remain the enforcement points.  This module
only authenticates a local owner command and records it through the same
``owner_directive.*`` event path already used by the Supervisor.
"""
from __future__ import annotations

import hashlib
import hmac
import os
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
    "PAUSE", "RESUME", "REVOKE", "CANCEL_MISSION", "GATE_REEVALUATE",
    "REPRIORITIZE", "REDIRECT", "RESTART_RECOVERY", "DELEGATE",
    "STANDING_ORDER",
})


def load_or_create_owner_secret(path: str | Path) -> bytes:
    """Return a stable machine-local owner secret without exposing it to UI.

    The localhost Command Center endpoint signs owner commands inside the
    Supervisor process.  Persisting a random key makes restart recovery
    possible while keeping replay protection and durable HMAC provenance.
    """
    secret_path = Path(path).expanduser().resolve()
    secret_path.parent.mkdir(parents=True, exist_ok=True)

    def _read() -> bytes:
        raw = secret_path.read_text(encoding="ascii").strip()
        try:
            value = bytes.fromhex(raw)
        except ValueError as error:
            raise OwnerAuthenticationError(
                f"owner secret file is malformed: {secret_path}") from error
        if len(value) < 32:
            raise OwnerAuthenticationError(
                f"owner secret file is too short: {secret_path}")
        return value

    if secret_path.is_file():
        return _read()
    encoded = secrets.token_bytes(32).hex().encode("ascii") + b"\n"
    try:
        descriptor = os.open(
            secret_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return _read()
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)
    try:
        os.chmod(secret_path, 0o600)
    except OSError:
        pass
    return _read()


def canonical_command(
    command_type: str,
    target: str | None,
    exact_text: str,
    nonce: str,
    *,
    supersedes: str | None = None,
    expires_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bytes:
    if command_type not in _COMMANDS:
        raise OwnerCommandError(f"unsupported owner command: {command_type}")
    if not nonce or not exact_text.strip():
        raise OwnerCommandError("nonce and exact_text are required")
    return canonical_json({
        "command_type": command_type,
        "target": target,
        "exact_text": exact_text,
        "nonce": nonce,
        "supersedes": supersedes,
        "expires_at": expires_at,
        "metadata": dict(metadata or {}),
    }).encode("utf-8")


def sign_command(
    secret: bytes,
    command_type: str,
    target: str | None,
    exact_text: str,
    nonce: str,
    *,
    supersedes: str | None = None,
    expires_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    return hmac.new(
        secret,
        canonical_command(
            command_type,
            target,
            exact_text,
            nonce,
            supersedes=supersedes,
            expires_at=expires_at,
            metadata=metadata,
        ),
        hashlib.sha256,
    ).hexdigest()


def stable_owner_request_directive_id(owner_id: str, request_id: str) -> str:
    """Return one durable directive identity for an owner delivery request."""

    normalized_owner = str(owner_id or "").strip()
    normalized_request = str(request_id or "").strip()
    if not normalized_owner:
        raise OwnerCommandError("owner_id is required for stable request identity")
    if (
        not normalized_request
        or len(normalized_request) > 256
        or "\n" in normalized_request
        or "\r" in normalized_request
    ):
        raise OwnerCommandError(
            "metadata.request_id must be non-empty, at most 256 characters, "
            "and contain no newlines"
        )
    digest = hashlib.sha256(canonical_json({
        "command_type": "DELEGATE",
        "owner_id": normalized_owner,
        "request_id": normalized_request,
    }).encode("utf-8")).hexdigest()
    return f"owner-delegation-{digest[:32]}"


def _directive_semantics(
    command_type: str,
    target: str | None,
    exact_text: str,
    *,
    supersedes: str | None,
    expires_at: str | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "command_type": command_type,
        "target": target,
        "exact_text": exact_text,
        "supersedes": supersedes,
        "expires_at": expires_at,
        "metadata": dict(metadata or {}),
    }


@dataclass(frozen=True, slots=True)
class OwnerCommandResult:
    directive_id: str
    event_id: str
    command_type: str
    nonce: str
    superseded_directive_id: str | None = None
    outcome: str | None = None
    details: dict[str, Any] | None = None


class OwnerControlService:
    def __init__(self, database: FoundryDatabase, *, secret: bytes, stop_path: str | Path | None = None, owner_id: str = "local-owner") -> None:
        if not secret:
            raise OwnerAuthenticationError("an owner secret is required")
        self.database = database
        self.secret = bytes(secret)
        self.stop = EmergencyStop(stop_path) if stop_path is not None else None
        self.owner_id = owner_id

    def _verify(
        self,
        command_type: str,
        target: str | None,
        exact_text: str,
        nonce: str,
        signature: str,
        *,
        supersedes: str | None = None,
        expires_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        expected = sign_command(
            self.secret,
            command_type,
            target,
            exact_text,
            nonce,
            supersedes=supersedes,
            expires_at=expires_at,
            metadata=metadata,
        )
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

    def _active_owner_records(self) -> list[dict[str, Any]]:
        """Read authenticated directive truth from the append-only ledger."""
        from .owner_directives import active_owner_directives

        return [record.to_dict() for record in active_owner_directives(self.database)]

    def _clear_stop_sentinel_if_unblocked(self) -> None:
        """Clear the STOP mirror only after durable global STOP revocation."""
        if self.stop is None:
            return
        if not any(
            value.get("command_type") == "STOP_EVERYTHING"
            for value in self._active_owner_records()
        ):
            self.stop.clear()

    def _gate_decision(
        self,
        *,
        mission_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> tuple[str, str, dict[str, Any]]:
        """Evaluate a requested acceptance gate without inventing missing context."""
        from .mission.verifier_binding import AcceptanceGate

        values = dict(metadata or {})
        if not mission_id or not mission_id.strip():
            return "REFUSE", "GATE_REEVALUATE requires a mission target", {
                "input_valid": "no",
            }
        mission = self.database.row("missions", "mission_id", mission_id)
        if mission is None:
            return "REFUSE", f"unknown mission: {mission_id}", {"input_valid": "no"}
        step_id = str(values.get("step_id") or "").strip()
        builder_run_id = str(values.get("builder_run_id") or "").strip()
        if not step_id or not builder_run_id:
            return "REFUSE", "GATE_REEVALUATE requires metadata.step_id and metadata.builder_run_id", {
                "input_valid": "no",
            }
        resource_id = str(mission.get("resource_id") or "").strip()
        resource = self.database.row("resources", "resource_id", resource_id) if resource_id else None
        root = Path(str(resource.get("resolved_root"))) if resource and resource.get("resolved_root") else None
        target_raw = values.get("target_path") or values.get("path")
        target = Path(str(target_raw)).expanduser() if target_raw else root
        if target is None:
            return "REFUSE", "GATE_REEVALUATE cannot resolve a registered resource root", {
                "input_valid": "no",
            }
        target = target.resolve(strict=False)
        if root is not None:
            root = root.resolve(strict=False)
            try:
                target.relative_to(root)
            except ValueError:
                return "REFUSE", "gate target escapes the mission resource root", {
                    "input_valid": "no", "target": str(target), "root": str(root),
                }
        decision = AcceptanceGate(
            self.database,
            stop_path=self.stop.path if self.stop is not None else None,
        ).evaluate(
            mission_id=mission_id, step_id=step_id, target=target,
            builder_run_id=builder_run_id,
        )
        return decision.decision, decision.reason, dict(decision.checks)

    def _revoke_authenticated(
        self,
        directive_id: str,
        *,
        command_type: str,
        nonce: str,
        signature: str,
        exact_text: str,
    ) -> OwnerCommandResult:
        """Revoke one directive under a signed REVOKE (or legacy RESUME) command."""
        existing = self._existing_directive(directive_id)
        if existing is None:
            raise OwnerCommandError(f"cannot revoke unknown directive: {directive_id}")
        self._verify(command_type, directive_id, exact_text, nonce, signature)
        now = isoformat()
        command_hash = hashlib.sha256(
            canonical_command(command_type, directive_id, exact_text, nonce)
        ).hexdigest()
        nonce_event = Event.create(
            "owner_control.nonce_used",
            {"nonce": nonce, "command_hash": command_hash, "issued_at": now, "used_at": now},
            source_type="owner", source_id=self.owner_id,
            idempotency_key=f"owner_control.nonce_used:{nonce}",
        )
        event = Event.create(
            "owner_directive.revoked",
            {
                "directive_id": directive_id, "command_type": command_type,
                "nonce": nonce, "command_hash": command_hash,
                "authenticated": True, "reason": exact_text,
                "owner_id": self.owner_id,
            },
            source_type="owner", source_id=self.owner_id,
            correlation_id=nonce_event.correlation_id,
            causation_id=nonce_event.event_id,
            idempotency_key=f"owner_directive.revoked:{directive_id}:{nonce}",
        )
        self.database.append_events([nonce_event, event])
        self._clear_stop_sentinel_if_unblocked()
        return OwnerCommandResult(
            directive_id, event.event_id, command_type, nonce,
            outcome="REVOKED", details={"revoked_directive_id": directive_id},
        )

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
        metadata: dict[str, Any] | None = None,
    ) -> OwnerCommandResult:
        self._verify(
            command_type,
            target,
            exact_text,
            nonce,
            signature,
            supersedes=supersedes_directive_id,
            expires_at=expires_at,
            metadata=metadata,
        )
        if command_type == "STANDING_ORDER":
            from .owner_context import validate_standing_order_metadata

            try:
                validate_standing_order_metadata(
                    exact_text, metadata, target=target,
                )
            except ValueError as error:
                raise OwnerCommandError(str(error)) from error
        if command_type == "REVOKE":
            if not isinstance(target, str) or not target.strip():
                raise OwnerCommandError("REVOKE requires target directive_id")
            # The command was already authenticated above; use the dedicated
            # path only for validation/recording and avoid a second nonce use.
            existing = self._existing_directive(target)
            if existing is None:
                raise OwnerCommandError(f"cannot revoke unknown directive: {target}")
            now = isoformat()
            command_hash = hashlib.sha256(canonical_command(
                command_type, target, exact_text, nonce,
                supersedes=supersedes_directive_id,
                expires_at=expires_at, metadata=metadata,
            )).hexdigest()
            nonce_event = Event.create(
                "owner_control.nonce_used",
                {"nonce": nonce, "command_hash": command_hash, "issued_at": now, "used_at": now},
                source_type="owner", source_id=self.owner_id,
                idempotency_key=f"owner_control.nonce_used:{nonce}",
            )
            revoked = Event.create(
                "owner_directive.revoked",
                {"directive_id": target, "command_type": command_type,
                 "nonce": nonce, "command_hash": command_hash,
                 "authenticated": True, "reason": exact_text,
                 "owner_id": self.owner_id},
                source_type="owner", source_id=self.owner_id,
                correlation_id=nonce_event.correlation_id,
                causation_id=nonce_event.event_id,
                idempotency_key=f"owner_directive.revoked:{target}:{nonce}",
            )
            self.database.append_events([nonce_event, revoked])
            self._clear_stop_sentinel_if_unblocked()
            return OwnerCommandResult(
                target, revoked.event_id, command_type, nonce,
                outcome="REVOKED", details={"revoked_directive_id": target},
            )

        gate_outcome: tuple[str, str, dict[str, Any]] | None = None
        if command_type == "GATE_REEVALUATE":
            gate_outcome = self._gate_decision(mission_id=target, metadata=metadata)
        if command_type == "CANCEL_MISSION":
            if not isinstance(target, str) or not target.strip():
                raise OwnerCommandError("CANCEL_MISSION requires target mission_id")
            mission = self.database.row("missions", "mission_id", target)
            if mission is None:
                raise OwnerCommandError(f"unknown mission: {target}")
            from .domain import require_transition
            try:
                require_transition(str(mission["state"]), "CANCELLED")
            except ValueError as error:
                raise OwnerCommandError(str(error)) from error
        request_id: str | None = None
        stable_request_identity = False
        if command_type == "DELEGATE" and directive_id is None:
            request_value = (metadata or {}).get("request_id")
            if request_value is not None:
                if not isinstance(request_value, str):
                    raise OwnerCommandError(
                        "metadata.request_id must be a string"
                    )
                request_id = request_value.strip()
                directive_id = stable_owner_request_directive_id(
                    self.owner_id,
                    request_id,
                )
                stable_request_identity = True
        directive_id = directive_id or f"owner-{secrets.token_hex(12)}"
        existing_directive = self._existing_directive(directive_id)
        if existing_directive is not None and stable_request_identity:
            existing_semantics = _directive_semantics(
                str(existing_directive.payload.get("command_type") or ""),
                existing_directive.payload.get("target"),
                str(existing_directive.payload.get("exact_text") or ""),
                supersedes=existing_directive.payload.get("supersedes"),
                expires_at=existing_directive.payload.get("expires_at"),
                metadata=(
                    existing_directive.payload.get("metadata")
                    if isinstance(existing_directive.payload.get("metadata"), dict)
                    else {}
                ),
            )
            requested_semantics = _directive_semantics(
                command_type,
                target,
                exact_text,
                supersedes=supersedes_directive_id,
                expires_at=expires_at,
                metadata=metadata,
            )
            if canonical_json(existing_semantics) != canonical_json(
                requested_semantics
            ):
                raise OwnerCommandError(
                    "metadata.request_id already identifies a different "
                    "delegation"
                )
            now = isoformat()
            command_hash = hashlib.sha256(canonical_command(
                command_type,
                target,
                exact_text,
                nonce,
                supersedes=supersedes_directive_id,
                expires_at=expires_at,
                metadata=metadata,
            )).hexdigest()
            nonce_event = Event.create(
                "owner_control.nonce_used",
                {
                    "nonce": nonce,
                    "command_hash": command_hash,
                    "issued_at": now,
                    "used_at": now,
                },
                source_type="owner",
                source_id=self.owner_id,
                correlation_id=existing_directive.correlation_id,
                idempotency_key=f"owner_control.nonce_used:{nonce}",
            )
            duplicate_event = Event.create(
                "owner_control.duplicate_received",
                {
                    "directive_id": directive_id,
                    "request_id": request_id,
                    "nonce": nonce,
                    "command_hash": command_hash,
                    "authenticated": True,
                    "owner_id": self.owner_id,
                    "original_event_id": existing_directive.event_id,
                    "received_at": now,
                },
                source_type="owner",
                source_id=self.owner_id,
                correlation_id=existing_directive.correlation_id,
                causation_id=nonce_event.event_id,
                idempotency_key=(
                    f"owner_control.duplicate_received:{directive_id}:{nonce}"
                ),
            )
            self.database.append_events([nonce_event, duplicate_event])
            return OwnerCommandResult(
                directive_id,
                duplicate_event.event_id,
                command_type,
                nonce,
                supersedes_directive_id,
                outcome="REPLAYED",
                details={
                    "request_id": request_id,
                    "original_event_id": existing_directive.event_id,
                },
            )
        if existing_directive is not None:
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
        command_hash = hashlib.sha256(
            canonical_command(
                command_type,
                target,
                exact_text,
                nonce,
                supersedes=supersedes_directive_id,
                expires_at=expires_at,
                metadata=metadata,
            )
        ).hexdigest()
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
             "expires_at": expires_at, "metadata": dict(metadata or {})},
            source_type="owner", source_id=self.owner_id,
            correlation_id=nonce_event.correlation_id,
            causation_id=nonce_event.event_id,
            idempotency_key=f"owner_directive.recorded:{directive_id}:{command_hash}",
        )
        events = [nonce_event, directive_event]
        if command_type == "CANCEL_MISSION":
            events.append(Event.create(
                "mission.state_changed",
                {"mission_id": target, "state": "CANCELLED",
                 "next_action": "cancelled by authenticated owner directive",
                 "owner_directive_id": directive_id},
                source_type="supervisor", source_id="owner-control",
                correlation_id=nonce_event.correlation_id,
                causation_id=directive_event.event_id,
                idempotency_key=f"mission.state_changed:{target}:CANCELLED:owner:{directive_id}",
            ))
        if gate_outcome is not None:
            decision, reason, checks = gate_outcome
            events.append(Event.create(
                "acceptance.gate_reevaluated",
                {"directive_id": directive_id, "mission_id": target,
                 "decision": decision, "reason": reason, "checks": checks,
                 "metadata": dict(metadata or {})},
                source_type="owner", source_id=self.owner_id,
                correlation_id=nonce_event.correlation_id,
                causation_id=directive_event.event_id,
                idempotency_key=f"acceptance.gate_reevaluated:{directive_id}",
            ))
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
            outcome=gate_outcome[0] if gate_outcome is not None else (
                "CANCELLED" if command_type == "CANCEL_MISSION" else None),
            details=(
                {"reason": gate_outcome[1], "checks": gate_outcome[2]}
                if gate_outcome is not None else None
            ),
        )

    def revoke(self, directive_id: str, *, nonce: str, signature: str, exact_text: str = "revoke owner directive") -> str:
        # RESUME was the historical spelling for this operation.  Accept it
        # only for existing callers while preferring the explicit REVOKE verb.
        accepted = None
        for candidate in ("REVOKE", "RESUME"):
            expected = sign_command(self.secret, candidate, directive_id, exact_text, nonce)
            if hmac.compare_digest(expected, signature or ""):
                accepted = candidate
                break
        if accepted is None:
            raise OwnerAuthenticationError("owner command authentication failed")
        result = self._revoke_authenticated(
            directive_id, command_type=accepted, nonce=nonce,
            signature=signature, exact_text=exact_text,
        )
        return result.event_id

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
                 directive_id: str | None = None,
                 supersedes_directive_id: str | None = None,
                 metadata: dict[str, Any] | None = None) -> OwnerCommandResult:
        """Record an authenticated mission/project redirect on the ledger."""
        return self.apply(
            "REDIRECT", target=target, exact_text=exact_text,
            nonce=nonce, signature=signature, directive_id=directive_id,
            supersedes_directive_id=supersedes_directive_id,
            metadata=metadata,
        )

    def resume(self, *, nonce: str, signature: str, target: str = "GLOBAL", exact_text: str = "resume jarvis") -> list[str]:
        """Revoke only the requested scope; GLOBAL retains legacy all-stop behavior."""
        self._verify("RESUME", target, exact_text, nonce, signature)
        active = [
            value for value in self._active_owner_records()
            if value.get("command_type") in {"STOP_EVERYTHING", "PAUSE"}
        ]
        if target == "GLOBAL":
            selected = active
        elif any(value.get("directive_id") == target for value in active):
            selected = [value for value in active if value.get("directive_id") == target]
        else:
            selected = [value for value in active if value.get("target") == target]
        now = isoformat()
        command_hash = hashlib.sha256(canonical_command("RESUME", target, exact_text, nonce)).hexdigest()
        events = [Event.create(
            "owner_control.nonce_used",
            {"nonce": nonce, "command_hash": command_hash, "issued_at": now, "used_at": now},
            source_type="owner", source_id=self.owner_id,
            idempotency_key=f"owner_control.nonce_used:{nonce}",
        )]
        event_ids = []
        for value in selected:
            event = Event.create(
                "owner_directive.revoked", {
                    "directive_id": str(value["directive_id"]), "command_type": "RESUME",
                    "nonce": nonce, "command_hash": command_hash,
                    "authenticated": True, "reason": exact_text,
                    "owner_id": self.owner_id,
                },
                source_type="owner", source_id=self.owner_id,
                correlation_id=events[0].correlation_id,
                causation_id=events[0].event_id,
                    idempotency_key=f"owner_directive.revoked:{value['directive_id']}:{nonce}",
                )
            events.append(event)
            event_ids.append(event.event_id)
        self.database.append_events(events)
        self._clear_stop_sentinel_if_unblocked()
        return event_ids
