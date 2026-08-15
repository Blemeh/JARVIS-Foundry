"""Worker gateway: bounded context in, proposal/result/evidence out."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_bytes, canonical_hash
from jarvis_foundry.kernel.capabilities import CapabilityBroker
from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import AuthorityError
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ids import DeterministicIdGenerator, IdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.policy import PolicyEngine
from jarvis_foundry.kernel.records import ActionEnvelope


@dataclass(frozen=True)
class WorkerProposal:
    proposal_id: str
    mission_id: str
    context_hash: str
    submitted_at: str


class WorkerGatewayService:
    def __init__(
        self,
        *,
        ledger: Ledger,
        capabilities: CapabilityBroker,
        policy: PolicyEngine,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
        max_context_bytes: int = 65536,
        owner_grant_id: str,
        worker_id: str,
    ) -> None:
        self.ledger = ledger
        self.capabilities = capabilities
        self.policy = policy
        self._ids = ids if ids is not None else DeterministicIdGenerator(seed=1)
        self._clock = clock if clock is not None else SystemClock()
        self._max_context_bytes = max_context_bytes
        self.owner_grant_id = owner_grant_id
        self._worker_id = worker_id

    def bounded_context_in(self, *, mission_id: str, context: Mapping[str, Any]) -> WorkerProposal:
        encoded = canonical_bytes(context)
        if len(encoded) > self._max_context_bytes:
            raise AuthorityError(f"bounded worker context exceeds {self._max_context_bytes} bytes")
        at = self._clock.now_utc()
        self.capabilities.verify(self.owner_grant_id, requested_target=mission_id, at=at)
        envelope = ActionEnvelope(
            action_id=self._ids.new_id("action"),
            mission_id=mission_id,
            action_class="worker.context",
            target=mission_id,
            scope="worker-gateway",
            policy_version=self.policy.policy_version,
            decision="PENDING",
            reason="",
            authorized_by="",
        )
        self.policy.authorize(envelope)
        proposal = WorkerProposal(
            proposal_id=self._ids.new_id("proposal"),
            mission_id=mission_id,
            context_hash=canonical_hash(context),
            submitted_at=at,
        )
        self.ledger.append(
            LedgerEvent.create(
                event_type="worker.proposal",
                payload={
                    "proposal_id": proposal.proposal_id,
                    "mission_id": mission_id,
                    "context_hash": proposal.context_hash,
                    "worker_id": self._worker_id,
                },
                source_type="worker-gateway",
                source_id=self._worker_id,
                idempotency_key=f"worker.proposal:{proposal.proposal_id}",
                event_id=proposal.proposal_id,
            )
        )
        return proposal

    def proposal_out(self, proposal_id: str, *, decision: str) -> bool:
        self._require_proposal(proposal_id)
        if decision not in {"APPROVED", "REJECTED"}:
            raise AuthorityError(f"invalid proposal decision: {decision}")
        return True

    def result_out(self, proposal_id: str, *, result: Mapping[str, Any]) -> bool:
        self._require_proposal(proposal_id)
        self.ledger.append(
            LedgerEvent.create(
                event_type="worker.result",
                payload={
                    "proposal_id": proposal_id,
                    "result_hash": canonical_hash(result),
                    "worker_id": self._worker_id,
                },
                source_type="worker-gateway",
                source_id=self._worker_id,
                idempotency_key=f"worker.result:{proposal_id}",
                event_id=f"worker-result-{proposal_id}",
            )
        )
        return True

    def evidence_out(self, proposal_id: str, *, evidence: Mapping[str, Any]) -> bool:
        self._require_proposal(proposal_id)
        self.ledger.append(
            LedgerEvent.create(
                event_type="worker.evidence",
                payload={
                    "proposal_id": proposal_id,
                    "evidence_id": f"evidence-{proposal_id}",
                    "evidence_hash": canonical_hash(evidence),
                    "worker_id": self._worker_id,
                },
                source_type="worker-gateway",
                source_id=self._worker_id,
                idempotency_key=f"worker.evidence:{proposal_id}",
                event_id=f"worker-evidence-{proposal_id}",
            )
        )
        return True

    def _require_proposal(self, proposal_id: str) -> None:
        for event in self.ledger.replay():
            if (
                event.event_type == "worker.proposal"
                and event.payload.get("proposal_id") == proposal_id
            ):
                return
        raise AuthorityError(f"unknown worker proposal: {proposal_id}")
