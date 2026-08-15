from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.kernel.capabilities import CapabilityBroker
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.errors import AuthorityError, CapabilityError
from jarvis_foundry.kernel.events import EventKindRegistry
from jarvis_foundry.kernel.ids import DeterministicIdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.policy import PolicyEngine, PolicyRule
from jarvis_foundry.kernel.worker_gateway import WorkerGatewayService


def gateway_registry() -> EventKindRegistry:
    return (
        EventKindRegistry()
        .register("worker.proposal", required_keys=("proposal_id", "mission_id"))
        .register("worker.result", required_keys=("proposal_id",))
        .register("worker.evidence", required_keys=("evidence_id", "proposal_id"))
    )


def fresh_gateway(tmp_path: Path, max_context_bytes: int = 1024) -> WorkerGatewayService:
    ledger = Ledger(tmp_path / "kernel.db", registry=gateway_registry())
    capabilities = CapabilityBroker(
        tmp_path / "capabilities.db",
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
    )
    grant = capabilities.issue(
        grantee="worker-1",
        capability="worker.context",
        target="mission-1",
        scope={"write": True},
        ttl_seconds=60,
    )
    policy = PolicyEngine(rules=[PolicyRule(action_class="worker.context", effect="ALLOW")])
    return WorkerGatewayService(
        ledger=ledger,
        capabilities=capabilities,
        policy=policy,
        ids=DeterministicIdGenerator(seed=1),
        clock=FixedClock("2026-08-15T00:00:00Z"),
        max_context_bytes=max_context_bytes,
        owner_grant_id=grant.grant_id,
        worker_id="worker-1",
    )


def test_bounded_context_in_and_proposal_out(tmp_path: Path) -> None:
    gateway = fresh_gateway(tmp_path)
    proposal = gateway.bounded_context_in(
        mission_id="mission-1",
        context={"task": "build evidence", "budget": 100},
    )
    assert proposal.proposal_id.startswith("proposal-")
    emitted = gateway.proposal_out(proposal.proposal_id, decision="APPROVED")
    assert emitted is True
    assert len(gateway.ledger.replay()) == 1


def test_context_size_limit_is_refused(tmp_path: Path) -> None:
    gateway = fresh_gateway(tmp_path, max_context_bytes=8)
    with pytest.raises(AuthorityError):
        gateway.bounded_context_in(
            mission_id="mission-1",
            context={"task": "this context is definitely too large"},
        )


def test_result_and_evidence_out_require_proposal(tmp_path: Path) -> None:
    gateway = fresh_gateway(tmp_path)
    proposal = gateway.bounded_context_in(mission_id="mission-1", context={"task": "x"})
    assert gateway.result_out(proposal.proposal_id, result={"ok": True}) is True
    assert gateway.evidence_out(proposal.proposal_id, evidence={"hash": "0" * 64}) is True
    with pytest.raises(AuthorityError):
        gateway.result_out("proposal-missing", result={})


def test_missing_grant_is_refused(tmp_path: Path) -> None:
    gateway = fresh_gateway(tmp_path)
    gateway.capabilities.revoke(gateway.owner_grant_id, reason="expired")
    with pytest.raises(CapabilityError):
        gateway.bounded_context_in(mission_id="mission-1", context={"task": "x"})
