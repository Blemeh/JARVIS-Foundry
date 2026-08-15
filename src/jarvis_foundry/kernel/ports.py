"""The eight mandatory ports/interfaces of the trusted kernel."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

if TYPE_CHECKING:
    from jarvis_foundry.kernel.effects import PreparedEffect
    from jarvis_foundry.kernel.events import LedgerEvent
    from jarvis_foundry.kernel.ledger import AppendResult, ChainReport, Subscription
    from jarvis_foundry.kernel.records import (
        CapabilityGrant,
        EffectIntent,
        EffectReceipt,
        ReconciliationResult,
        ReleaseRecord,
    )
    from jarvis_foundry.kernel.supervisor import RecoveryReport, SupervisorLease, SupervisorState
    from jarvis_foundry.kernel.worker_gateway import WorkerProposal


class LedgerPort(Protocol):
    """Append-only ledger: append/read/replay/subscribe, no arbitrary mutation."""

    def append(self, event: LedgerEvent) -> AppendResult: ...

    def read(self, *, after: int = 0, limit: int | None = None) -> list[LedgerEvent]: ...

    def replay(self) -> list[LedgerEvent]: ...

    def subscribe(self, subscriber: Callable[[LedgerEvent], None]) -> Subscription: ...

    def verify_chain(self) -> ChainReport: ...


class StopGate(Protocol):
    def activate(self, reason: str, source: str) -> Mapping[str, str]: ...

    def clear(self) -> bool: ...

    def active(self) -> bool: ...


State = TypeVar("State")


class ProjectionHandler(Protocol[State]):
    def initial_state(self) -> State: ...

    def version(self) -> int: ...

    def event_types(self) -> frozenset[str]: ...

    def apply(self, state: State, event: LedgerEvent) -> State: ...


class OwnerControlPort(Protocol):
    """Authenticated owner commands outside retrieved content."""

    def apply_command(
        self,
        *,
        command_type: str,
        target: str | None,
        exact_text: str,
        nonce: str,
        signature: str,
    ) -> Any: ...

    def active_directives(self) -> list[Any]: ...


class SupervisorPort(Protocol):
    """Durable supervisor selection and state advancement."""

    def start(self, instance_id: str) -> SupervisorState: ...

    def heartbeat(self, instance_id: str) -> SupervisorState: ...

    def acquire_lease(
        self, worker_id: str, instance_id: str, *, allow_wait: bool = False
    ) -> SupervisorLease: ...

    def recover(self, *, at: str) -> RecoveryReport: ...


class WorkerGatewayPort(Protocol):
    """Bounded context in; proposal/result/evidence out."""

    def bounded_context_in(
        self, *, mission_id: str, context: Mapping[str, Any]
    ) -> WorkerProposal: ...

    def proposal_out(self, proposal_id: str, *, decision: str) -> bool: ...

    def result_out(self, proposal_id: str, *, result: Mapping[str, Any]) -> bool: ...

    def evidence_out(self, proposal_id: str, *, evidence: Mapping[str, Any]) -> bool: ...


class CapabilityBrokerPort(Protocol):
    """Deterministic scoped expiring grants with canonical targets."""

    def issue(
        self,
        *,
        grantee: str,
        capability: str,
        target: str,
        scope: Mapping[str, Any],
        ttl_seconds: int,
    ) -> CapabilityGrant: ...

    def verify(self, grant_id: str, *, requested_target: str, at: str) -> CapabilityGrant: ...

    def revoke(self, grant_id: str, reason: str) -> CapabilityGrant: ...


class SystemAdapterPort(Protocol):
    """Prepare/execute/inspect/reconcile/compensate for external systems."""

    def prepare(self, intent: EffectIntent) -> PreparedEffect: ...

    def execute(self, prepared: PreparedEffect) -> Mapping[str, Any]: ...

    def inspect(self, intent_id: str) -> list[EffectReceipt]: ...

    def reconcile(self, intent_id: str) -> ReconciliationResult: ...

    def compensate(self, intent_id: str) -> Mapping[str, Any]: ...


class ReleaseControllerPort(Protocol):
    """Freeze/canary/promote/rollback/restore release registry transitions."""

    def freeze(self, release_id: str) -> ReleaseRecord: ...

    def canary(self, release_id: str) -> ReleaseRecord: ...

    def promote(self, release_id: str) -> ReleaseRecord: ...

    def rollback(self, release_id: str) -> ReleaseRecord: ...

    def restore(self, release_id: str) -> ReleaseRecord: ...


class ClockPort(Protocol):
    def now_utc(self) -> str: ...


class IdPort(Protocol):
    def new_id(self, prefix: str) -> str: ...


__all__ = [
    "CapabilityBrokerPort",
    "ClockPort",
    "IdPort",
    "LedgerPort",
    "OwnerControlPort",
    "ReleaseControllerPort",
    "SupervisorPort",
    "SystemAdapterPort",
    "WorkerGatewayPort",
]
