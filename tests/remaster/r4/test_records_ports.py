from __future__ import annotations

from dataclasses import fields
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.ports import (
    CapabilityBrokerPort,
    ClockPort,
    IdPort,
    LedgerPort,
    OwnerControlPort,
    ReleaseControllerPort,
    SupervisorPort,
    SystemAdapterPort,
    WorkerGatewayPort,
)
from jarvis_foundry.kernel.records import (
    RECORD_TYPES,
    ActionEnvelope,
    CapabilityGrant,
    EffectIntent,
    EffectReceipt,
    EvidenceRecord,
    Goal,
    IncidentRecord,
    Mission,
    MissionStep,
    OwnerDirective,
    ReconciliationResult,
    ReleaseRecord,
    VerifierVerdict,
)


def test_all_thirteen_mandatory_records_exist_and_are_typed() -> None:
    names = {
        "OwnerDirective",
        "Goal",
        "Mission",
        "MissionStep",
        "ActionEnvelope",
        "CapabilityGrant",
        "EffectIntent",
        "EffectReceipt",
        "ReconciliationResult",
        "EvidenceRecord",
        "VerifierVerdict",
        "ReleaseRecord",
        "IncidentRecord",
    }
    assert names == {record.__name__ for record in RECORD_TYPES}
    for record in RECORD_TYPES:
        assert len(fields(record)) > 0


def test_record_roundtrip_and_deterministic_hash() -> None:
    directive = OwnerDirective(
        directive_id="d-1",
        command_type="PAUSE",
        target="GLOBAL",
        nonce="n-1",
        exact_text="pause jarvis",
        recorded_at="2026-08-15T00:00:00Z",
        status="ACTIVE",
        channel_id="owner",
    )
    as_dict: dict[str, Any] = directive.to_dict()
    restored = OwnerDirective.from_dict(as_dict)
    assert restored == directive
    assert canonical_hash(as_dict) == canonical_hash(directive.to_dict())
    second = OwnerDirective.from_dict(directive.to_dict())
    assert second == directive


def test_all_records_roundtrip() -> None:
    samples: dict[type[Any], Any] = {
        Goal: Goal(
            goal_id="g-1",
            title="t",
            objective="o",
            status="ACTIVE",
            created_at="2026-08-15T00:00:00Z",
            acceptance_criteria=("c",),
        ),
        Mission: Mission(
            mission_id="m-1",
            goal_id="g-1",
            objective="o",
            status="RUNNING",
            priority=1,
            created_at="2026-08-15T00:00:00Z",
        ),
        MissionStep: MissionStep(
            step_id="s-1",
            mission_id="m-1",
            title="t",
            status="RUNNING",
            order_index=0,
            created_at="2026-08-15T00:00:00Z",
        ),
        ActionEnvelope: ActionEnvelope(
            action_id="a-1",
            mission_id="m-1",
            action_class="file.read",
            target="C:/x",
            scope="worktree",
            policy_version=1,
            decision="ALLOWED",
            reason="ok",
            authorized_by="policy",
        ),
        CapabilityGrant: CapabilityGrant(
            grant_id="c-1",
            capability="file.read",
            target_canonical="c:/x",
            grantee="worker-1",
            scope={"write": True},
            issued_at="2026-08-15T00:00:00Z",
            expires_at="2026-08-16T00:00:00Z",
            revoked_at=None,
            status="ACTIVE",
        ),
        EffectIntent: EffectIntent(
            intent_id="i-1",
            idempotency_key="effect:1",
            action_envelope_id="a-1",
            system="fs",
            operation="write",
            params={"path": "C:/x"},
        ),
        EffectReceipt: EffectReceipt(
            receipt_id="r-1",
            intent_id="i-1",
            status="SUCCEEDED",
            outcome="ok",
            performed_at="2026-08-15T00:00:00Z",
            adapter_id="fs",
        ),
        ReconciliationResult: ReconciliationResult(
            result_id="rc-1",
            intent_ids=("i-1",),
            reconciliation_status="SUCCESS",
            matched=True,
            detail="all ok",
            evidence={},
        ),
        EvidenceRecord: EvidenceRecord(
            evidence_id="e-1",
            producer="builder",
            artifact_hashes={"a.txt": "0" * 64},
            recorded_at="2026-08-15T00:00:00Z",
        ),
        VerifierVerdict: VerifierVerdict(
            verdict_id="v-1",
            evidence_id="e-1",
            verifier_id="verifier",
            status="PASS",
            reason="ok",
            verified_at="2026-08-15T00:00:00Z",
        ),
        ReleaseRecord: ReleaseRecord(
            release_id="rel-1",
            artifact_id="art-1",
            version="1.0.0",
            stage="BUILD",
            created_at="2026-08-15T00:00:00Z",
            updated_at="2026-08-15T00:00:00Z",
        ),
        IncidentRecord: IncidentRecord(
            incident_id="inc-1",
            severity="HIGH",
            status="OPEN",
            title="t",
            description="d",
            created_at="2026-08-15T00:00:00Z",
            resolved_at=None,
        ),
    }
    for record_type, sample in samples.items():
        restored = record_type.from_dict(sample.to_dict())
        assert restored == sample
        assert canonical_hash(sample.to_dict()) == canonical_hash(restored.to_dict())


def test_eight_mandatory_ports_exist() -> None:
    assert [
        LedgerPort,
        OwnerControlPort,
        SupervisorPort,
        WorkerGatewayPort,
        CapabilityBrokerPort,
        SystemAdapterPort,
        ReleaseControllerPort,
        ClockPort,
        IdPort,
    ]
    assert ClockPort is not None
    assert IdPort is not None
