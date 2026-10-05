"""Typed contracts for minimal-instruction owner delegation.

The delegation engine persists conclusions and evidence labels, never hidden
reasoning.  These values are deliberately JSON-shaped so the append-only event
ledger remains the only durability authority.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

from ..clock import parse
from ..events import canonical_json


DELEGATION_INTENT_CONTRACT = "jarvis.delegation-intent.v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")


class DelegationMode(StrEnum):
    TELL_ME = "TELL_ME"
    PREPARE = "PREPARE"
    HANDLE = "HANDLE"
    WATCH = "WATCH"
    TAKE_OVER = "TAKE_OVER"
    STANDING = "STANDING"


class DelegationDecision(StrEnum):
    ACT = "ACT"
    ASK = "ASK"
    PREPARE = "PREPARE"
    WATCH = "WATCH"


class ConsequenceClass(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    IRREVERSIBLE = "IRREVERSIBLE"


class Reversibility(StrEnum):
    REVERSIBLE = "REVERSIBLE"
    PARTIAL = "PARTIAL"
    IRREVERSIBLE = "IRREVERSIBLE"
    UNKNOWN = "UNKNOWN"


class DelegationIntentStatus(StrEnum):
    INTERPRETED = "INTERPRETED"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    READY = "READY"
    ADMITTED = "ADMITTED"
    BLOCKED = "BLOCKED"
    SUPERSEDED = "SUPERSEDED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def _required(value: Any, field: str, *, max_length: int = 4096) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > max_length:
        raise ValueError(f"{field} exceeds {max_length} characters")
    return text


def _optional(value: Any, *, max_length: int = 4096) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValueError(f"value exceeds {max_length} characters")
    return text


def _string_tuple(
    values: Sequence[Any] | None,
    field: str,
    *,
    limit: int,
    item_length: int = 1024,
    clip: bool = False,
) -> tuple[str, ...]:
    def _one(value: Any) -> str:
        if clip:
            text = str(value or "").strip()
            if len(text) > item_length:
                return (text[:item_length - 1].rstrip() + "…")[:item_length]
            if not text:
                raise ValueError(f"{field} is required")
            return text
        return _required(value, field, max_length=item_length)
    rows = tuple(_one(value) for value in (values or ()))
    if len(rows) > limit:
        raise ValueError(f"{field} exceeds {limit} items")
    return tuple(dict.fromkeys(rows))


def _confidence(value: Any, field: str = "confidence") -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{field} must be within [0, 1]")
    return round(score, 4)


@dataclass(frozen=True, slots=True)
class ReferentCandidate:
    context_id: str
    kind: str
    text: str
    confidence: float
    evidence: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    state: str = "ACTIVE"
    effective_at: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    resolution_contract: str | None = None

    def __post_init__(self) -> None:
        _required(self.context_id, "context_id", max_length=256)
        _required(self.kind, "kind", max_length=96)
        _required(self.text, "text", max_length=2048)
        _confidence(self.confidence)
        if self.effective_at is not None:
            try:
                parse(self.effective_at)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError("effective_at must be ISO-8601") from error

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReferentCandidate":
        return cls(
            context_id=_required(value.get("context_id"), "context_id", max_length=256),
            kind=_required(value.get("kind"), "kind", max_length=96).upper(),
            text=_required(value.get("text"), "text", max_length=2048),
            confidence=_confidence(value.get("confidence")),
            evidence=_string_tuple(
                value.get("evidence"), "evidence", limit=24, item_length=512,
            ),
            source_refs=_string_tuple(
                value.get("source_refs"), "source_refs", limit=16, item_length=512,
            ),
            state=_required(
                value.get("state") or "ACTIVE", "state", max_length=64,
            ).upper(),
            effective_at=_optional(value.get("effective_at"), max_length=64),
            scope=dict(value.get("scope") or {}),
            provenance=dict(value.get("provenance") or {}),
            resolution_contract=_optional(
                value.get("resolution_contract"), max_length=128,
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "kind": self.kind,
            "text": self.text,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "source_refs": list(self.source_refs),
            "state": self.state,
            "effective_at": self.effective_at,
            "scope": dict(self.scope),
            "provenance": dict(self.provenance),
            "resolution_contract": self.resolution_contract,
        }


@dataclass(frozen=True, slots=True)
class MaterialAmbiguity:
    ambiguity_id: str
    code: str
    question: str
    options: tuple[str, ...] = ()
    material: bool = True
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MaterialAmbiguity":
        return cls(
            ambiguity_id=_required(
                value.get("ambiguity_id"), "ambiguity_id", max_length=256,
            ),
            code=_required(value.get("code"), "code", max_length=96).upper(),
            question=_required(value.get("question"), "question", max_length=1024),
            # Clip (don't reject) overlong options on read: legacy ledger rows
            # embedded full constraint text, and the projection must stay total
            # over recorded history. New writes are clipped at construction.
            options=_string_tuple(
                value.get("options"), "options", limit=8, item_length=512,
                clip=True,
            ),
            material=bool(value.get("material", True)),
            evidence=_string_tuple(
                value.get("evidence"), "evidence", limit=16, item_length=512,
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "ambiguity_id": self.ambiguity_id,
            "code": self.code,
            "question": self.question,
            "options": list(self.options),
            "material": self.material,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class MethodAssessment:
    requested_method: str
    owner_goal: str
    recommended_method: str
    reason: str
    disposition: str
    evidence: tuple[str, ...] = ()
    preserves_explicit_constraints: bool = True

    def __post_init__(self) -> None:
        _required(
            self.requested_method,
            "requested_method",
            max_length=1024,
        )
        _required(self.owner_goal, "owner_goal", max_length=1024)
        _required(
            self.recommended_method,
            "recommended_method",
            max_length=1024,
        )
        _required(self.reason, "reason", max_length=1024)
        if self.disposition not in {
            "PRESERVE_REQUEST",
            "RECOMMEND_ALTERNATIVE",
            "ASK_OWNER",
        }:
            raise ValueError("unsupported method assessment disposition")
        _string_tuple(
            self.evidence,
            "method_assessment.evidence",
            limit=16,
            item_length=512,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MethodAssessment":
        return cls(
            requested_method=_required(
                value.get("requested_method"),
                "requested_method",
                max_length=1024,
            ),
            owner_goal=_required(
                value.get("owner_goal"),
                "owner_goal",
                max_length=1024,
            ),
            recommended_method=_required(
                value.get("recommended_method"),
                "recommended_method",
                max_length=1024,
            ),
            reason=_required(
                value.get("reason"),
                "reason",
                max_length=1024,
            ),
            disposition=_required(
                value.get("disposition"),
                "disposition",
                max_length=64,
            ).upper(),
            evidence=_string_tuple(
                value.get("evidence"),
                "method_assessment.evidence",
                limit=16,
                item_length=512,
            ),
            preserves_explicit_constraints=bool(
                value.get("preserves_explicit_constraints", True),
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "requested_method": self.requested_method,
            "owner_goal": self.owner_goal,
            "recommended_method": self.recommended_method,
            "reason": self.reason,
            "disposition": self.disposition,
            "evidence": list(self.evidence),
            "preserves_explicit_constraints": (
                self.preserves_explicit_constraints
            ),
        }


@dataclass(frozen=True, slots=True)
class DelegationIntent:
    intent_id: str
    owner_directive_id: str
    owner_source_identity: str
    exact_owner_text: str
    desired_outcome: str
    mode: DelegationMode
    decision: DelegationDecision
    success_criteria: tuple[str, ...]
    consequence_class: ConsequenceClass
    reversibility: Reversibility
    confidence: float
    created_at: str
    updated_at: str
    status: DelegationIntentStatus = DelegationIntentStatus.INTERPRETED
    resolved_referents: tuple[ReferentCandidate, ...] = ()
    explicit_constraints: tuple[str, ...] = ()
    standing_order_ids: tuple[str, ...] = ()
    context_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    ambiguities: tuple[MaterialAmbiguity, ...] = ()
    method_assessment: MethodAssessment | None = None
    rationale_labels: tuple[str, ...] = ()
    program_id: str | None = None
    project_id: str | None = None
    context_objective_id: str | None = None
    resulting_objective_id: str | None = None
    resulting_plan_id: str | None = None
    resulting_step_ids: tuple[str, ...] = ()
    resulting_mission_ids: tuple[str, ...] = ()
    context_contract: str | None = None
    context_package_hash: str | None = None
    reference_resolution_contract: str | None = None
    clarification_question: str | None = None
    clarification_revision: int = 0
    superseded_by_intent_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, DelegationMode):
            raise ValueError("mode must be a DelegationMode")
        if not isinstance(self.decision, DelegationDecision):
            raise ValueError("decision must be a DelegationDecision")
        if not isinstance(self.consequence_class, ConsequenceClass):
            raise ValueError("consequence_class must be a ConsequenceClass")
        if not isinstance(self.reversibility, Reversibility):
            raise ValueError("reversibility must be a Reversibility")
        if not isinstance(self.status, DelegationIntentStatus):
            raise ValueError("status must be a DelegationIntentStatus")
        _required(self.intent_id, "intent_id", max_length=256)
        _required(self.owner_directive_id, "owner_directive_id", max_length=256)
        _required(
            self.owner_source_identity, "owner_source_identity", max_length=256,
        )
        _required(self.exact_owner_text, "exact_owner_text", max_length=4096)
        _required(self.desired_outcome, "desired_outcome", max_length=2048)
        _required(self.created_at, "created_at", max_length=64)
        _required(self.updated_at, "updated_at", max_length=64)
        for name, value in (
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            try:
                parse(value)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError(f"{name} must be ISO-8601") from error
        if not self.success_criteria:
            raise ValueError("success_criteria requires at least one item")
        _string_tuple(
            self.success_criteria, "success_criteria", limit=16, item_length=1024,
        )
        _string_tuple(
            self.explicit_constraints,
            "explicit_constraints",
            limit=24,
            item_length=1024,
        )
        _string_tuple(
            self.standing_order_ids,
            "standing_order_ids",
            limit=16,
            item_length=256,
        )
        _string_tuple(self.context_refs, "context_refs", limit=48, item_length=512)
        _string_tuple(self.evidence_refs, "evidence_refs", limit=48, item_length=512)
        _string_tuple(
            self.rationale_labels, "rationale_labels", limit=32, item_length=256,
        )
        _confidence(self.confidence)
        if len(self.resolved_referents) > 8:
            raise ValueError("resolved_referents exceeds 8 items")
        if len(self.ambiguities) > 8:
            raise ValueError("ambiguities exceeds 8 items")
        if (
            self.method_assessment is not None
            and not isinstance(self.method_assessment, MethodAssessment)
        ):
            raise ValueError("method_assessment must be a MethodAssessment")
        if self.clarification_revision < 0:
            raise ValueError("clarification_revision must be non-negative")
        if (
            self.reversibility == Reversibility.IRREVERSIBLE
            and self.consequence_class != ConsequenceClass.IRREVERSIBLE
        ):
            raise ValueError(
                "IRREVERSIBLE reversibility requires IRREVERSIBLE consequence",
            )
        material_ambiguities = tuple(
            ambiguity for ambiguity in self.ambiguities if ambiguity.material
        )
        if (
            self.decision == DelegationDecision.ASK
            and self.status not in {
                DelegationIntentStatus.AWAITING_CLARIFICATION,
                DelegationIntentStatus.BLOCKED,
                DelegationIntentStatus.SUPERSEDED,
                DelegationIntentStatus.FAILED,
                DelegationIntentStatus.CANCELLED,
            }
        ):
            raise ValueError(
                "ASK requires AWAITING_CLARIFICATION or a terminal authority state",
            )
        if (
            self.status == DelegationIntentStatus.AWAITING_CLARIFICATION
            and self.decision != DelegationDecision.ASK
        ):
            raise ValueError("AWAITING_CLARIFICATION requires ASK decision")
        if (
            self.status == DelegationIntentStatus.READY
            and material_ambiguities
        ):
            raise ValueError("READY cannot retain material ambiguities")
        if (
            self.decision == DelegationDecision.PREPARE
            and self.mode != DelegationMode.PREPARE
        ):
            raise ValueError("PREPARE decision requires PREPARE mode")
        if (
            self.decision == DelegationDecision.WATCH
            and self.mode != DelegationMode.WATCH
        ):
            raise ValueError("WATCH decision requires WATCH mode")
        if (
            self.status in {
                DelegationIntentStatus.ADMITTED,
                DelegationIntentStatus.COMPLETED,
                DelegationIntentStatus.FAILED,
                DelegationIntentStatus.CANCELLED,
            }
            and (
                not self.resulting_objective_id
                or not self.resulting_plan_id
                or not self.resulting_step_ids
            )
        ):
            raise ValueError(
                f"{self.status.value} requires Executive objective, plan, and step",
            )
        if (
            self.context_package_hash is not None
            and not _HASH.fullmatch(self.context_package_hash)
        ):
            raise ValueError("context_package_hash must be sha256 hex")
        if (
            self.status == DelegationIntentStatus.AWAITING_CLARIFICATION
            and not _optional(self.clarification_question, max_length=1024)
        ):
            raise ValueError(
                "AWAITING_CLARIFICATION requires clarification_question",
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DelegationIntent":
        contract = value.get("contract")
        if contract not in {None, DELEGATION_INTENT_CONTRACT}:
            raise ValueError(f"unsupported delegation intent contract: {contract}")
        return cls(
            intent_id=_required(value.get("intent_id"), "intent_id", max_length=256),
            owner_directive_id=_required(
                value.get("owner_directive_id"),
                "owner_directive_id",
                max_length=256,
            ),
            owner_source_identity=_required(
                value.get("owner_source_identity"),
                "owner_source_identity",
                max_length=256,
            ),
            exact_owner_text=_required(
                value.get("exact_owner_text"), "exact_owner_text", max_length=4096,
            ),
            desired_outcome=_required(
                value.get("desired_outcome"), "desired_outcome", max_length=2048,
            ),
            mode=DelegationMode(str(value.get("mode") or "")),
            decision=DelegationDecision(str(value.get("decision") or "")),
            success_criteria=_string_tuple(
                value.get("success_criteria"),
                "success_criteria",
                limit=16,
                item_length=1024,
            ),
            consequence_class=ConsequenceClass(
                str(value.get("consequence_class") or ""),
            ),
            reversibility=Reversibility(str(value.get("reversibility") or "")),
            confidence=_confidence(value.get("confidence")),
            created_at=_required(
                value.get("created_at"), "created_at", max_length=64,
            ),
            updated_at=_required(
                value.get("updated_at"), "updated_at", max_length=64,
            ),
            status=DelegationIntentStatus(
                str(value.get("status") or DelegationIntentStatus.INTERPRETED),
            ),
            resolved_referents=tuple(
                ReferentCandidate.from_mapping(row)
                for row in (value.get("resolved_referents") or ())
                if isinstance(row, Mapping)
            ),
            explicit_constraints=_string_tuple(
                value.get("explicit_constraints"),
                "explicit_constraints",
                limit=24,
                item_length=1024,
            ),
            standing_order_ids=_string_tuple(
                value.get("standing_order_ids"),
                "standing_order_ids",
                limit=16,
                item_length=256,
            ),
            context_refs=_string_tuple(
                value.get("context_refs"), "context_refs", limit=48, item_length=512,
            ),
            evidence_refs=_string_tuple(
                value.get("evidence_refs"), "evidence_refs", limit=48, item_length=512,
            ),
            ambiguities=tuple(
                MaterialAmbiguity.from_mapping(row)
                for row in (value.get("ambiguities") or ())
                if isinstance(row, Mapping)
            ),
            method_assessment=(
                MethodAssessment.from_mapping(value["method_assessment"])
                if isinstance(value.get("method_assessment"), Mapping)
                else None
            ),
            rationale_labels=_string_tuple(
                value.get("rationale_labels"),
                "rationale_labels",
                limit=32,
                item_length=256,
            ),
            program_id=_optional(value.get("program_id"), max_length=256),
            project_id=_optional(value.get("project_id"), max_length=256),
            context_objective_id=_optional(
                value.get("context_objective_id"), max_length=256,
            ),
            resulting_objective_id=_optional(
                value.get("resulting_objective_id")
                or value.get("objective_id"),
                max_length=256,
            ),
            resulting_plan_id=_optional(
                value.get("resulting_plan_id") or value.get("plan_id"),
                max_length=256,
            ),
            resulting_step_ids=_string_tuple(
                value.get("resulting_step_ids") or value.get("step_ids"),
                "resulting_step_ids",
                limit=16,
                item_length=256,
            ),
            resulting_mission_ids=_string_tuple(
                value.get("resulting_mission_ids"),
                "resulting_mission_ids",
                limit=16,
                item_length=256,
            ),
            context_contract=_optional(
                value.get("context_contract"), max_length=128,
            ),
            context_package_hash=_optional(
                value.get("context_package_hash"), max_length=64,
            ),
            reference_resolution_contract=_optional(
                value.get("reference_resolution_contract"), max_length=128,
            ),
            clarification_question=_optional(
                value.get("clarification_question"), max_length=1024,
            ),
            clarification_revision=int(value.get("clarification_revision") or 0),
            superseded_by_intent_id=_optional(
                value.get("superseded_by_intent_id"), max_length=256,
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "contract": DELEGATION_INTENT_CONTRACT,
            "intent_id": self.intent_id,
            "owner_directive_id": self.owner_directive_id,
            "owner_source_identity": self.owner_source_identity,
            "exact_owner_text": self.exact_owner_text,
            "desired_outcome": self.desired_outcome,
            "mode": self.mode.value,
            "decision": self.decision.value,
            "success_criteria": list(self.success_criteria),
            "consequence_class": self.consequence_class.value,
            "reversibility": self.reversibility.value,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "resolved_referents": [
                value.to_payload() for value in self.resolved_referents
            ],
            "explicit_constraints": list(self.explicit_constraints),
            "standing_order_ids": list(self.standing_order_ids),
            "context_refs": list(self.context_refs),
            "evidence_refs": list(self.evidence_refs),
            "ambiguities": [value.to_payload() for value in self.ambiguities],
            "method_assessment": (
                self.method_assessment.to_payload()
                if self.method_assessment is not None
                else None
            ),
            "rationale_labels": list(self.rationale_labels),
            "program_id": self.program_id,
            "project_id": self.project_id,
            "context_objective_id": self.context_objective_id,
            "resulting_objective_id": self.resulting_objective_id,
            "resulting_plan_id": self.resulting_plan_id,
            "resulting_step_ids": list(self.resulting_step_ids),
            "resulting_mission_ids": list(self.resulting_mission_ids),
            "context_contract": self.context_contract,
            "context_package_hash": self.context_package_hash,
            "reference_resolution_contract": (
                self.reference_resolution_contract
            ),
            "clarification_question": self.clarification_question,
            "clarification_revision": self.clarification_revision,
            "superseded_by_intent_id": self.superseded_by_intent_id,
        }


def stable_intent_id(
    owner_directive_id: str,
    exact_owner_text: str,
    *,
    request_identity: str | None = None,
) -> str:
    """Return the stable identity for one authenticated delegation request."""

    directive = _required(
        owner_directive_id, "owner_directive_id", max_length=256,
    )
    text = _required(exact_owner_text, "exact_owner_text", max_length=4096)
    digest = hashlib.sha256(canonical_json({
        "owner_directive_id": directive,
        "exact_owner_text": text,
        "request_identity": _optional(request_identity, max_length=256),
    }).encode("utf-8")).hexdigest()
    return f"delegation-intent:{digest[:32]}"


__all__ = [
    "ConsequenceClass",
    "DELEGATION_INTENT_CONTRACT",
    "DelegationDecision",
    "DelegationIntent",
    "DelegationIntentStatus",
    "DelegationMode",
    "MaterialAmbiguity",
    "MethodAssessment",
    "ReferentCandidate",
    "Reversibility",
    "stable_intent_id",
]
