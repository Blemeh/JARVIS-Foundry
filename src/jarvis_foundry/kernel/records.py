"""The thirteen mandatory typed records for the trusted kernel."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Record:
    """Base record with explicit dict serialization support."""

    def to_dict(self) -> dict[str, Any]:
        return {field_name: getattr(self, field_name) for field_name in self.dict_fields()}

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Record:
        raise NotImplementedError


@dataclass(frozen=True)
class OwnerDirective(Record):
    directive_id: str
    command_type: str
    target: str | None
    nonce: str
    exact_text: str
    recorded_at: str
    status: str
    channel_id: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "directive_id",
            "command_type",
            "target",
            "nonce",
            "exact_text",
            "recorded_at",
            "status",
            "channel_id",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> OwnerDirective:
        return OwnerDirective(
            directive_id=str(payload["directive_id"]),
            command_type=str(payload["command_type"]),
            target=str(payload["target"]) if payload.get("target") is not None else None,
            nonce=str(payload["nonce"]),
            exact_text=str(payload["exact_text"]),
            recorded_at=str(payload["recorded_at"]),
            status=str(payload["status"]),
            channel_id=str(payload["channel_id"]),
        )


@dataclass(frozen=True)
class Goal(Record):
    goal_id: str
    title: str
    objective: str
    status: str
    created_at: str
    acceptance_criteria: tuple[str, ...] = ()

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "goal_id",
            "title",
            "objective",
            "status",
            "created_at",
            "acceptance_criteria",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Goal:
        return Goal(
            goal_id=str(payload["goal_id"]),
            title=str(payload["title"]),
            objective=str(payload["objective"]),
            status=str(payload["status"]),
            created_at=str(payload["created_at"]),
            acceptance_criteria=tuple(payload.get("acceptance_criteria", ())),
        )


@dataclass(frozen=True)
class Mission(Record):
    mission_id: str
    goal_id: str
    objective: str
    status: str
    priority: int
    created_at: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "mission_id",
            "goal_id",
            "objective",
            "status",
            "priority",
            "created_at",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Mission:
        return Mission(
            mission_id=str(payload["mission_id"]),
            goal_id=str(payload["goal_id"]),
            objective=str(payload["objective"]),
            status=str(payload["status"]),
            priority=int(payload["priority"]),
            created_at=str(payload["created_at"]),
        )


@dataclass(frozen=True)
class MissionStep(Record):
    step_id: str
    mission_id: str
    title: str
    status: str
    order_index: int
    created_at: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return ("step_id", "mission_id", "title", "status", "order_index", "created_at")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MissionStep:
        return MissionStep(
            step_id=str(payload["step_id"]),
            mission_id=str(payload["mission_id"]),
            title=str(payload["title"]),
            status=str(payload["status"]),
            order_index=int(payload["order_index"]),
            created_at=str(payload["created_at"]),
        )


@dataclass(frozen=True)
class ActionEnvelope(Record):
    action_id: str
    mission_id: str
    action_class: str
    target: str
    scope: str
    policy_version: int
    decision: str
    reason: str
    authorized_by: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "action_id",
            "mission_id",
            "action_class",
            "target",
            "scope",
            "policy_version",
            "decision",
            "reason",
            "authorized_by",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ActionEnvelope:
        return ActionEnvelope(
            action_id=str(payload["action_id"]),
            mission_id=str(payload["mission_id"]),
            action_class=str(payload["action_class"]),
            target=str(payload["target"]),
            scope=str(payload["scope"]),
            policy_version=int(payload["policy_version"]),
            decision=str(payload["decision"]),
            reason=str(payload["reason"]),
            authorized_by=str(payload["authorized_by"]),
        )


@dataclass(frozen=True)
class CapabilityGrant(Record):
    grant_id: str
    capability: str
    target_canonical: str
    grantee: str
    scope: Mapping[str, Any]
    issued_at: str
    expires_at: str
    revoked_at: str | None
    status: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "grant_id",
            "capability",
            "target_canonical",
            "grantee",
            "scope",
            "issued_at",
            "expires_at",
            "revoked_at",
            "status",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CapabilityGrant:
        return CapabilityGrant(
            grant_id=str(payload["grant_id"]),
            capability=str(payload["capability"]),
            target_canonical=str(payload["target_canonical"]),
            grantee=str(payload["grantee"]),
            scope=dict(payload.get("scope", {})),
            issued_at=str(payload["issued_at"]),
            expires_at=str(payload["expires_at"]),
            revoked_at=str(payload["revoked_at"]) if payload.get("revoked_at") else None,
            status=str(payload["status"]),
        )


@dataclass(frozen=True)
class EffectIntent(Record):
    intent_id: str
    idempotency_key: str
    action_envelope_id: str
    system: str
    operation: str
    params: Mapping[str, Any]

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "intent_id",
            "idempotency_key",
            "action_envelope_id",
            "system",
            "operation",
            "params",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EffectIntent:
        return EffectIntent(
            intent_id=str(payload["intent_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            action_envelope_id=str(payload["action_envelope_id"]),
            system=str(payload["system"]),
            operation=str(payload["operation"]),
            params=dict(payload.get("params", {})),
        )


@dataclass(frozen=True)
class EffectReceipt(Record):
    receipt_id: str
    intent_id: str
    status: str
    outcome: str
    performed_at: str
    adapter_id: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "receipt_id",
            "intent_id",
            "status",
            "outcome",
            "performed_at",
            "adapter_id",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EffectReceipt:
        return EffectReceipt(
            receipt_id=str(payload["receipt_id"]),
            intent_id=str(payload["intent_id"]),
            status=str(payload["status"]),
            outcome=str(payload["outcome"]),
            performed_at=str(payload["performed_at"]),
            adapter_id=str(payload["adapter_id"]),
        )


@dataclass(frozen=True)
class ReconciliationResult(Record):
    result_id: str
    intent_ids: tuple[str, ...]
    reconciliation_status: str
    matched: bool
    detail: str
    evidence: Mapping[str, Any]
    compensated_intent_ids: tuple[str, ...] = ()

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "result_id",
            "intent_ids",
            "reconciliation_status",
            "matched",
            "detail",
            "evidence",
            "compensated_intent_ids",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ReconciliationResult:
        return ReconciliationResult(
            result_id=str(payload["result_id"]),
            intent_ids=tuple(payload.get("intent_ids", ())),
            reconciliation_status=str(payload["reconciliation_status"]),
            matched=bool(payload.get("matched", False)),
            detail=str(payload.get("detail", "")),
            evidence=dict(payload.get("evidence", {})),
            compensated_intent_ids=tuple(payload.get("compensated_intent_ids", ())),
        )


@dataclass(frozen=True)
class EvidenceRecord(Record):
    evidence_id: str
    producer: str
    artifact_hashes: Mapping[str, str]
    recorded_at: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return ("evidence_id", "producer", "artifact_hashes", "recorded_at")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=str(payload["evidence_id"]),
            producer=str(payload["producer"]),
            artifact_hashes=dict(payload.get("artifact_hashes", {})),
            recorded_at=str(payload["recorded_at"]),
        )


@dataclass(frozen=True)
class VerifierVerdict(Record):
    verdict_id: str
    evidence_id: str
    verifier_id: str
    status: str
    reason: str
    verified_at: str
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "verdict_id",
            "evidence_id",
            "verifier_id",
            "status",
            "reason",
            "verified_at",
            "artifact_hashes",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> VerifierVerdict:
        return VerifierVerdict(
            verdict_id=str(payload["verdict_id"]),
            evidence_id=str(payload["evidence_id"]),
            verifier_id=str(payload["verifier_id"]),
            status=str(payload["status"]),
            reason=str(payload["reason"]),
            verified_at=str(payload["verified_at"]),
            artifact_hashes=dict(payload.get("artifact_hashes", {})),
        )


@dataclass(frozen=True)
class ReleaseRecord(Record):
    release_id: str
    artifact_id: str
    version: str
    stage: str
    created_at: str
    updated_at: str

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "release_id",
            "artifact_id",
            "version",
            "stage",
            "created_at",
            "updated_at",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ReleaseRecord:
        return ReleaseRecord(
            release_id=str(payload["release_id"]),
            artifact_id=str(payload["artifact_id"]),
            version=str(payload["version"]),
            stage=str(payload["stage"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )


@dataclass(frozen=True)
class IncidentRecord(Record):
    incident_id: str
    severity: str
    status: str
    title: str
    description: str
    created_at: str
    resolved_at: str | None = None

    @classmethod
    def dict_fields(cls) -> tuple[str, ...]:
        return (
            "incident_id",
            "severity",
            "status",
            "title",
            "description",
            "created_at",
            "resolved_at",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> IncidentRecord:
        return IncidentRecord(
            incident_id=str(payload["incident_id"]),
            severity=str(payload["severity"]),
            status=str(payload["status"]),
            title=str(payload["title"]),
            description=str(payload["description"]),
            created_at=str(payload["created_at"]),
            resolved_at=str(payload["resolved_at"]) if payload.get("resolved_at") else None,
        )


RECORD_TYPES: tuple[type[Record], ...] = (
    OwnerDirective,
    Goal,
    Mission,
    MissionStep,
    ActionEnvelope,
    CapabilityGrant,
    EffectIntent,
    EffectReceipt,
    ReconciliationResult,
    EvidenceRecord,
    VerifierVerdict,
    ReleaseRecord,
    IncidentRecord,
)
