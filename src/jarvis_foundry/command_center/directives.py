"""Canonical durable owner directives: the UI is never the authority.

Every directive is appended to the hash-bound directive ledger and recorded as
a kernel ``ControlDirective`` in the isolated ``PortfolioStore``. The rendered
state is always derived from those durable stores, so killing the UI cannot
remove Supervisor (owner) control.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.portfolio.errors import ConflictError
from jarvis_foundry.kernel.portfolio.records import ControlDirective
from jarvis_foundry.kernel.portfolio.store import PortfolioStore

from .ledger import (
    DirectiveLedger,
    DirectiveLedgerError,
    LedgerEvent,
)

DIRECTIVE_COMMANDS: frozenset[str] = frozenset(
    {"STOP", "VETO", "PAUSE", "RESUME", "REPRIORITIZE", "REDIRECT"}
)

_GLOBAL_COMMANDS: frozenset[str] = frozenset({"STOP"})


class DirectiveError(ValueError):
    pass


class DirectiveConflictError(DirectiveError):
    pass


@dataclass(frozen=True)
class DirectiveReceipt:
    directive_id: str
    event_id: str
    command_type: str
    nonce: str
    created: bool
    sequence: int


@dataclass(frozen=True)
class DirectiveState:
    stop_active: bool
    paused_projects: tuple[str, ...]
    vetoed_candidates: tuple[str, ...]
    directive_count: int
    control_hash: str
    redirects: tuple[dict[str, str], ...]


class DirectiveService:
    """Owner-only control surface operating on the isolated proving stores."""

    def __init__(
        self,
        portfolio_store: PortfolioStore,
        directive_ledger: DirectiveLedger,
        *,
        clock: Clock | None = None,
        source_type: str = "owner",
        source_id: str = "r10-owner",
    ) -> None:
        self.store = portfolio_store
        self.ledger = directive_ledger
        self._clock = clock if clock is not None else SystemClock()
        self._source_type = source_type
        self._source_id = source_id

    def _validate(
        self,
        command_type: str,
        target: str | None,
        reason: str,
        nonce: str,
        value: str,
    ) -> tuple[str, str | None, str]:
        command = command_type.upper()
        if command not in DIRECTIVE_COMMANDS:
            raise DirectiveError(f"unsupported directive command: {command_type}")
        if not nonce.strip():
            raise DirectiveError("nonce is required")
        if not reason.strip():
            raise DirectiveError("reason text is required")
        clean_target = target.strip() if target else ""
        if command in _GLOBAL_COMMANDS:
            clean_target = "GLOBAL"
        elif not clean_target:
            raise DirectiveError(f"{command} requires a target")
        clean_value = value.strip()
        if command == "REPRIORITIZE":
            try:
                int(clean_value)
            except ValueError as exc:
                raise DirectiveError("REPRIORITIZE requires an integer value") from exc
        return command, clean_target, clean_value

    def apply(
        self,
        *,
        command_type: str,
        target: str | None,
        reason: str,
        nonce: str,
        value: str = "",
    ) -> DirectiveReceipt:
        command, clean_target, clean_value = self._validate(
            command_type, target, reason, nonce, value
        )
        recorded_at = self._clock.now_utc()
        directive_id = f"directive-{command.lower()}-{nonce}"
        event_id = f"r10-directive-{nonce}"
        payload: dict[str, Any] = {
            "directive_id": directive_id,
            "command_type": command,
            "target": clean_target,
            "reason": reason,
            "nonce": nonce,
            "value": clean_value,
            "recorded_at": recorded_at,
            "authority": "canonical-directive-system",
        }

        existing = self._existing_event(event_id)
        if existing is not None:
            if existing.payload != payload:
                raise DirectiveConflictError(
                    f"directive nonce reused with different content: {nonce}"
                )
            return DirectiveReceipt(
                directive_id=directive_id,
                event_id=event_id,
                command_type=command,
                nonce=nonce,
                created=False,
                sequence=existing.sequence,
            )

        event = self.ledger.append(
            event_id=event_id,
            event_type="owner.directive.recorded",
            payload=payload,
            recorded_at=recorded_at,
            source_type=self._source_type,
            source_id=self._source_id,
        )
        directive = ControlDirective(
            directive_id=directive_id,
            command_type=command,
            target=clean_target,
            nonce=nonce,
            reason=reason,
            recorded_at=recorded_at,
            status="ACTIVE",
            value=clean_value,
        )
        try:
            result = self.store.append_directive(
                directive, idempotency_key=f"control_directive:{nonce}"
            )
        except ConflictError as exc:
            raise DirectiveConflictError(
                f"control directive conflict for nonce {nonce}: {exc}"
            ) from exc
        return DirectiveReceipt(
            directive_id=directive_id,
            event_id=event.event_id,
            command_type=command,
            nonce=nonce,
            created=result.created,
            sequence=event.sequence,
        )

    def _existing_event(self, event_id: str) -> LedgerEvent | None:
        try:
            return next((item for item in self.ledger.events() if item.event_id == event_id), None)
        except DirectiveLedgerError:
            return None

    def state(self) -> DirectiveState:
        stop_active = False
        paused: dict[str, bool] = {}
        vetoed: dict[str, bool] = {}
        for directive in self._directives_in_order():
            target = directive.target or "GLOBAL"
            if directive.command_type == "STOP":
                stop_active = True
            elif directive.command_type == "PAUSE":
                paused[target] = True
            elif directive.command_type == "RESUME":
                if target == "GLOBAL":
                    stop_active = False
                    paused = {key: False for key in paused}
                else:
                    paused[target] = False
            elif directive.command_type == "VETO":
                vetoed[target] = True
        redirects = tuple(
            {
                "directive_id": str(directive.directive_id),
                "project_id": str(directive.target or ""),
                "new_target": directive.value,
                "reason": directive.reason,
                "recorded_at": directive.recorded_at,
            }
            for directive in self.store.control_directives()
            if directive.command_type == "REDIRECT"
        )
        paused_projects = tuple(sorted(key for key, value in paused.items() if value))
        vetoed_candidates = tuple(sorted(key for key, value in vetoed.items() if value))
        state_payload = {
            "stop_active": stop_active,
            "paused_projects": paused_projects,
            "vetoed_candidates": vetoed_candidates,
            "directive_count": len(self.store.control_directives()),
        }
        return DirectiveState(
            stop_active=stop_active,
            paused_projects=paused_projects,
            vetoed_candidates=vetoed_candidates,
            directive_count=len(self.store.control_directives()),
            control_hash=canonical_hash(state_payload),
            redirects=redirects,
        )

    def _directives_in_order(self) -> list[ControlDirective]:
        """Chronological directives from the append-only portfolio event file.

        Kernel ``PortfolioStore.control_directives()`` sorts records by logical id,
        which is not chronological for control replay; the raw event file preserves
        append order and stays hash-verified by the store on reopen.
        """
        events_path = self.store.root / "events.jsonl"
        if not events_path.is_file():
            return []
        directives: list[ControlDirective] = []
        for raw in events_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            body = raw.rsplit("|", 1)[0]
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                continue
            if payload.get("kind") != "control_directive":
                continue
            record = payload.get("record")
            if isinstance(record, dict):
                directives.append(ControlDirective.from_dict(record))
        return directives

    def state_dict(self) -> dict[str, Any]:
        state = self.state()
        return {
            "stop_active": state.stop_active,
            "paused_projects": list(state.paused_projects),
            "vetoed_candidates": list(state.vetoed_candidates),
            "directive_count": state.directive_count,
            "control_hash": state.control_hash,
            "redirects": [dict(item) for item in state.redirects],
            "authority": "PortfolioControl/ControlState + hash-bound directive ledger",
            "ui_authority": False,
            "voice_authority": False,
        }


def open_directive_service(
    portfolio_root: Path,
    directive_ledger_path: Path,
    *,
    clock: Clock | None = None,
) -> DirectiveService:
    return DirectiveService(
        PortfolioStore(portfolio_root),
        DirectiveLedger(directive_ledger_path),
        clock=clock,
    )
