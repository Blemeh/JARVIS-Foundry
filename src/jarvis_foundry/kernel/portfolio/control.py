"""Durable owner control state: STOP, pause, resume, veto, reprioritize."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.portfolio.records import ControlDirective
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


@dataclass(frozen=True)
class ControlState:
    stop_active: bool
    paused_projects: tuple[str, ...]
    vetoed_candidates: tuple[str, ...]
    directive_count: int
    control_hash: str


class PortfolioControl:
    """Owner-only control surface; retrieved content can never reach it."""

    def __init__(self, store: PortfolioStore, clock: Clock | None = None) -> None:
        self.store = store
        self._clock = clock if clock is not None else SystemClock()

    def _record(
        self,
        *,
        command_type: str,
        target: str | None,
        nonce: str,
        reason: str,
        value: str = "",
    ) -> ControlDirective:
        directive = ControlDirective(
            directive_id=f"directive-{command_type.lower()}-{nonce}",
            command_type=command_type,
            target=target,
            nonce=nonce,
            reason=reason,
            recorded_at=self._clock.now_utc(),
            status="ACTIVE",
            value=value,
        )
        result = self.store.append_directive(
            directive, idempotency_key=f"control_directive:{nonce}"
        )
        return result.record if isinstance(result.record, ControlDirective) else directive

    def stop(self, *, nonce: str, reason: str = "owner STOP") -> ControlDirective:
        return self._record(command_type="STOP", target="GLOBAL", nonce=nonce, reason=reason)

    def pause(self, target: str, *, nonce: str, reason: str = "") -> ControlDirective:
        return self._record(command_type="PAUSE", target=target, nonce=nonce, reason=reason)

    def resume(self, target: str, *, nonce: str, reason: str = "") -> ControlDirective:
        return self._record(command_type="RESUME", target=target, nonce=nonce, reason=reason)

    def veto(self, target: str, *, nonce: str, reason: str = "") -> ControlDirective:
        return self._record(command_type="VETO", target=target, nonce=nonce, reason=reason)

    def reprioritize(
        self, target: str, *, priority_boost: int, nonce: str, reason: str = ""
    ) -> ControlDirective:
        return self._record(
            command_type="REPRIORITIZE",
            target=target,
            nonce=nonce,
            reason=reason,
            value=str(int(priority_boost)),
        )

    def priority_boost(self, candidate_id: str) -> int:
        latest: ControlDirective | None = None
        for directive in self.store.control_directives():
            if directive.command_type == "REPRIORITIZE" and directive.target == candidate_id:
                latest = directive
        if latest is None or not latest.value.isdigit():
            return 0
        return int(latest.value)

    def state(self) -> ControlState:
        stop_active = False
        paused: dict[str, bool] = {}
        vetoed: dict[str, bool] = {}
        for directive in self.store.control_directives():
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
        paused_projects = tuple(sorted(key for key, value in paused.items() if value))
        vetoed_candidates = tuple(sorted(key for key, value in vetoed.items() if value))
        state = {
            "stop_active": stop_active,
            "paused_projects": paused_projects,
            "vetoed_candidates": vetoed_candidates,
            "directive_count": len(self.store.control_directives()),
        }
        return ControlState(
            stop_active=stop_active,
            paused_projects=paused_projects,
            vetoed_candidates=vetoed_candidates,
            directive_count=len(self.store.control_directives()),
            control_hash=canonical_hash(state),
        )
