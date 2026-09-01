"""Small typed contracts at the Fabric-to-effect boundary.

V9 already owns the capability catalog, effect journal, MissionLoop, and
independent verifier.  V10 only makes the data crossing those boundaries
explicit.  This module contains no dispatch, persistence, scheduling, or
owner-control logic; it is a typed view over those existing authorities.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Mapping, Sequence

from ..control.redaction import redact_text
from ..effects import (
    EffectClass,
    fabric_effect_id,
    fabric_idempotency_key,
    infer_effect_class,
)
from ..events import canonical_json


CONTRACT_VERSION = "jarvis.v10.adapter.v1"


class ContractError(ValueError):
    """Raised when a V10 descriptor, request, or receipt is malformed."""


class PostconditionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise ContractError(f"{field_name} contains a control character")
    return result


def _identity(value: object, field_name: str) -> str:
    result = _text(value, field_name)
    if len(result) > 256 or any(char.isspace() for char in result):
        raise ContractError(f"{field_name} must be a bounded identity")
    return result


def _tuple_text(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)):
        raise ContractError(f"{field_name} must be a list of strings")
    return tuple(_text(item, f"{field_name} item") for item in value)


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractError(f"{field_name} must be an object")
    try:
        result = json.loads(canonical_json(dict(value)))
    except (TypeError, ValueError) as error:
        raise ContractError(f"{field_name} must contain JSON-safe values") from error
    if not isinstance(result, dict):
        raise ContractError(f"{field_name} must be an object")
    return result


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CapabilityMetadata:
    """Typed V10 sidecar metadata for one existing Fabric capability."""

    capability_id: str
    version: str
    input_schema: str
    output_schema: str
    effect_class: EffectClass
    requirements: tuple[str, ...] = ()
    credential_refs: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    idempotency: Mapping[str, Any] = field(default_factory=dict)
    execute: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    timeout_and_cancel: Mapping[str, Any] = field(default_factory=dict)
    recovery: Mapping[str, Any] = field(default_factory=dict)
    world_model: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _identity(self.capability_id, "capability_id"))
        object.__setattr__(self, "version", _text(self.version, "version"))
        object.__setattr__(self, "input_schema", _text(self.input_schema, "input_schema"))
        object.__setattr__(self, "output_schema", _text(self.output_schema, "output_schema"))
        if not isinstance(self.effect_class, EffectClass):
            object.__setattr__(self, "effect_class", EffectClass(self.effect_class))
        object.__setattr__(self, "requirements", _tuple_text(self.requirements, "requirements"))
        object.__setattr__(self, "credential_refs", _tuple_text(self.credential_refs, "credential_refs"))
        object.__setattr__(self, "preconditions", _tuple_text(self.preconditions, "preconditions"))
        object.__setattr__(self, "postconditions", _tuple_text(self.postconditions, "postconditions"))
        for name in (
            "idempotency", "execute", "evidence", "timeout_and_cancel",
            "recovery", "world_model", "provenance",
        ):
            object.__setattr__(self, name, _mapping(getattr(self, name), name))
        if self.idempotency.get("required") is True and not self.idempotency.get("strategy"):
            raise ContractError("idempotency.strategy is required when idempotency is required")
        max_attempts = self.recovery.get("max_attempts", 1)
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ContractError("recovery.max_attempts must be a positive integer")
        if self.world_model.get("verified_terminal_only") is False:
            raise ContractError("world_model.verified_terminal_only cannot be disabled")

    @classmethod
    def from_spec(cls, spec: Any) -> "CapabilityMetadata":
        """Derive conservative metadata from an existing CapabilitySpec."""

        action = str(spec.action_kind)
        effect_class = infer_effect_class(action, str(spec.semantic.value))
        external = effect_class in {
            EffectClass.REVERSIBLE_EXTERNAL,
            EffectClass.IRREVERSIBLE_OR_HIGH_COST,
        }
        return cls(
            capability_id=str(spec.capability_id),
            version=str(spec.version),
            input_schema=f"jarvis.action.{action}.input.v1",
            output_schema=f"jarvis.action.{action}.receipt.v1",
            effect_class=effect_class,
            requirements=tuple(spec.requirements),
            idempotency={
                "required": effect_class is not EffectClass.READ_ONLY,
                "strategy": "effect-key" if not external else "provider-key",
                "replay": "no-op" if not external else "reconcile-before-retry",
            },
            execute={
                "executor": str(spec.executor),
                "action_kind": action,
                "unattended": str(spec.unattended.value),
            },
            evidence={
                "receipt_schema": "jarvis.v10.adapter.receipt.v1",
                "before_state_required": effect_class is not EffectClass.READ_ONLY,
                "independent_verifier": True,
            },
            timeout_and_cancel={
                "timeout_ms": 120_000,
                "cancel_supported": effect_class is EffectClass.READ_ONLY,
                "timeout_outcome": "UNKNOWN",
            },
            recovery={
                "max_attempts": 1 if external else 3,
                "retryable_outcomes": ["FAILED"] if not external else [],
                "partial_effect": "FROZEN",
                "ambiguous_outcome": "FROZEN",
                "compensation": "declared-only",
            },
            world_model={
                "verified_terminal_only": True,
                "source_kind": "mission:outcome",
            },
            provenance={
                "authority": "Capability Fabric",
                "source": "fabric.catalog",
                "provider_deps": list(spec.provider_deps),
                "limitations": list(spec.limitations),
            },
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CapabilityMetadata":
        if not isinstance(value, Mapping):
            raise ContractError("capability metadata must be an object")
        action = str((value.get("execute") or {}).get("action_kind") or "action")
        semantic = str(value.get("semantic") or "")
        effect = value.get("effect_class")
        return cls(
            capability_id=_identity(value.get("capability_id"), "capability_id"),
            version=_text(value.get("version"), "version"),
            input_schema=_text(value.get("input_schema"), "input_schema"),
            output_schema=_text(value.get("output_schema"), "output_schema"),
            effect_class=EffectClass(effect or infer_effect_class(action, semantic)),
            requirements=_tuple_text(value.get("requirements"), "requirements"),
            credential_refs=_tuple_text(value.get("credential_refs"), "credential_refs"),
            preconditions=_tuple_text(value.get("preconditions"), "preconditions"),
            postconditions=_tuple_text(value.get("postconditions"), "postconditions"),
            idempotency=value.get("idempotency"),
            execute=value.get("execute"),
            evidence=value.get("evidence"),
            timeout_and_cancel=value.get("timeout_and_cancel"),
            recovery=value.get("recovery"),
            world_model=value.get("world_model") or {"verified_terminal_only": True},
            provenance=value.get("provenance"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "capability_id": self.capability_id,
            "version": self.version,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "effect_class": self.effect_class.value,
            "requirements": list(self.requirements),
            "credential_refs": list(self.credential_refs),
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "idempotency": dict(self.idempotency),
            "execute": dict(self.execute),
            "evidence": dict(self.evidence),
            "timeout_and_cancel": dict(self.timeout_and_cancel),
            "recovery": dict(self.recovery),
            "world_model": dict(self.world_model),
            "provenance": dict(self.provenance),
        }


def metadata_for_spec(spec: Any) -> CapabilityMetadata:
    """Return a descriptor sidecar without mutating or registering anything."""

    return CapabilityMetadata.from_spec(spec)


@dataclass(frozen=True, slots=True)
class PostconditionResult:
    name: str
    status: PostconditionStatus
    evidence_refs: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "postcondition.name"))
        if not isinstance(self.status, PostconditionStatus):
            object.__setattr__(self, "status", PostconditionStatus(self.status))
        object.__setattr__(self, "evidence_refs", _tuple_text(self.evidence_refs, "postcondition.evidence_refs"))
        object.__setattr__(self, "detail", redact_text(str(self.detail or ""))[:400])

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "evidence_refs": list(self.evidence_refs),
            "detail": self.detail,
        }


def _lookup(observed: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = observed
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def evaluate_postconditions(
    observed: Mapping[str, Any],
    conditions: Sequence[object] | None,
) -> tuple[PostconditionResult, ...]:
    """Evaluate only named, observable conditions supplied by the contract.

    String conditions mean a truthy observed field.  Mapping conditions may
    use ``field`` with ``equals``, ``not_equals``, ``exists``, ``truthy``, or
    ``in``.  Missing observations are UNKNOWN, never PASS.
    """

    results: list[PostconditionResult] = []
    for index, raw in enumerate(conditions or ()):
        if isinstance(raw, str):
            name = _text(raw, f"postcondition[{index}]")
            field_name = name
            condition: Mapping[str, Any] = {"field": field_name, "truthy": True}
        elif isinstance(raw, Mapping):
            name = _text(raw.get("name") or raw.get("field") or f"postcondition-{index}", f"postcondition[{index}].name")
            field_name = _text(raw.get("field") or name, f"postcondition[{index}].field")
            condition = raw
        else:
            raise ContractError(f"postcondition[{index}] must be a string or object")
        exists, actual = _lookup(observed, field_name)
        status = PostconditionStatus.UNKNOWN
        detail = f"observation {field_name!r} was not available"
        if "exists" in condition:
            expected_exists = bool(condition["exists"])
            status = PostconditionStatus.PASS if exists is expected_exists else PostconditionStatus.FAIL
            detail = f"exists={exists!s} expected={expected_exists!s}"
        elif not exists:
            status = PostconditionStatus.UNKNOWN
        elif "equals" in condition:
            status = PostconditionStatus.PASS if actual == condition["equals"] else PostconditionStatus.FAIL
            detail = f"field={field_name!r} equality={'matched' if status is PostconditionStatus.PASS else 'mismatched'}"
        elif "not_equals" in condition:
            status = PostconditionStatus.PASS if actual != condition["not_equals"] else PostconditionStatus.FAIL
            detail = f"field={field_name!r} inequality={'matched' if status is PostconditionStatus.PASS else 'mismatched'}"
        elif "in" in condition:
            values = condition["in"]
            if not isinstance(values, (list, tuple, set, frozenset)):
                raise ContractError(f"postcondition[{index}].in must be a list")
            status = PostconditionStatus.PASS if actual in values else PostconditionStatus.FAIL
            detail = f"field={field_name!r} membership={'matched' if status is PostconditionStatus.PASS else 'mismatched'}"
        elif condition.get("truthy") is True:
            status = PostconditionStatus.PASS if bool(actual) else PostconditionStatus.FAIL
            detail = f"field={field_name!r} truthiness={'matched' if status is PostconditionStatus.PASS else 'mismatched'}"
        else:
            raise ContractError(f"postcondition[{index}] has no supported assertion")
        results.append(PostconditionResult(
            name=name,
            status=status,
            evidence_refs=(f"observed:{field_name}",),
            detail=detail,
        ))
    return tuple(results)


def _target_ref(action_kind: str, payload: Mapping[str, Any]) -> str:
    for key in ("target_ref", "target", "endpoint", "url", "worktree", "source", "path"):
        value = payload.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return action_kind


def system_id_for_action(action_kind: str, target_ref: str) -> str:
    """Return the canonical V5 effect-journal system for a Fabric action."""

    action = action_kind.lower()
    if action.startswith("github."):
        return "github"
    if action.startswith("gmail."):
        return "gmail"
    if action.startswith("http.") or target_ref.startswith(("http://", "https://")):
        return "http"
    if action.startswith("git."):
        return "git"
    return "filesystem" if action.startswith(("file.", "artifact.")) else "local"


@dataclass(frozen=True, slots=True)
class AdapterRequest:
    """The non-durable request crossing the selected capability boundary."""

    request_id: str
    mission_id: str
    workflow_id: str
    step_id: str
    step_version: int
    attempt: int
    capability_id: str
    capability_version: str
    action_kind: str
    input_hash: str
    target_ref: str
    effect_class: EffectClass
    effect_id: str
    idempotency_key: str
    before_state_ref: str | None = None
    stop_scope: tuple[str, ...] = ()
    budget: Mapping[str, Any] = field(default_factory=dict)
    credential_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "request_id", "mission_id", "workflow_id", "step_id", "capability_id",
            "capability_version", "action_kind", "input_hash",
            "effect_id", "idempotency_key",
        ):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        # Targets and state references are bounded resource identifiers, not
        # token-like IDs. Windows paths and human-readable endpoint labels may
        # legitimately contain spaces.
        object.__setattr__(self, "target_ref", _text(self.target_ref, "target_ref")[:2048])
        if not isinstance(self.step_version, int) or isinstance(self.step_version, bool) or self.step_version < 1:
            raise ContractError("step_version must be a positive integer")
        if not isinstance(self.attempt, int) or isinstance(self.attempt, bool) or self.attempt < 1:
            raise ContractError("attempt must be a positive integer")
        if not isinstance(self.effect_class, EffectClass):
            object.__setattr__(self, "effect_class", EffectClass(self.effect_class))
        object.__setattr__(self, "stop_scope", _tuple_text(self.stop_scope, "stop_scope"))
        object.__setattr__(self, "credential_refs", _tuple_text(self.credential_refs, "credential_refs"))
        object.__setattr__(self, "budget", _mapping(self.budget, "budget"))
        if self.before_state_ref is not None:
            object.__setattr__(self, "before_state_ref", _text(self.before_state_ref, "before_state_ref")[:2048])

    @classmethod
    def build(
        cls,
        *,
        mission_id: str,
        workflow_id: str | None,
        step_id: str,
        step_version: int,
        attempt: int,
        action_kind: str,
        payload: Mapping[str, Any],
        spec: Any,
    ) -> "AdapterRequest":
        metadata = metadata_for_spec(spec)
        target = _target_ref(action_kind, payload)
        before_value = payload.get("before_state_ref")
        if before_value is None and payload.get("before_state") is not None:
            before_value = f"before:{_digest(payload.get('before_state'))}"
        before_ref = str(before_value).strip() if before_value is not None and str(before_value).strip() else None
        workflow = str(workflow_id or f"workflow:{mission_id}")
        input_hash = _digest(dict(payload))
        effect_id = fabric_effect_id(
            system_id=system_id_for_action(action_kind, target),
            target_ref=target,
            capability_id=str(spec.capability_id),
            capability_version=str(spec.version),
            action_kind=action_kind,
            input_payload=dict(payload),
            before_state_ref=before_ref,
        )
        explicit_key = payload.get("idempotency_key")
        key = str(explicit_key).strip() if isinstance(explicit_key, str) and explicit_key.strip() else fabric_idempotency_key(
            effect_id,
            mission_id=str(mission_id),
            workflow_id=workflow,
            step_id=str(step_id),
        )
        supplied_scope = payload.get("stop_scope") or ()
        if isinstance(supplied_scope, str):
            supplied_scope = (supplied_scope,)
        scope = tuple(dict.fromkeys(("GLOBAL", str(mission_id), *tuple(supplied_scope))))
        return cls(
            request_id=f"request:{effect_id}:attempt:{attempt}",
            mission_id=str(mission_id),
            workflow_id=workflow,
            step_id=str(step_id),
            step_version=step_version,
            attempt=attempt,
            capability_id=str(spec.capability_id),
            capability_version=str(spec.version),
            action_kind=action_kind,
            input_hash=input_hash,
            target_ref=target,
            effect_class=metadata.effect_class,
            effect_id=effect_id,
            idempotency_key=key,
            before_state_ref=before_ref,
            stop_scope=scope,
            budget=payload.get("budget") or payload.get("budget_estimate") or {},
            credential_refs=metadata.credential_refs,
        )

    def to_evidence(self) -> dict[str, Any]:
        """Return identity and policy fields without raw input or secrets."""

        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "workflow_id": self.workflow_id,
            "step_id": self.step_id,
            "step_version": self.step_version,
            "attempt": self.attempt,
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "action_kind": self.action_kind,
            "input_hash": self.input_hash,
            "target_ref": self.target_ref,
            "effect_class": self.effect_class.value,
            "effect_id": self.effect_id,
            "idempotency_key": self.idempotency_key,
            "before_state_ref": self.before_state_ref,
            "stop_scope": list(self.stop_scope),
            "budget": dict(self.budget),
            "credential_refs": list(self.credential_refs),
        }


def _safe_observed(claimed: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in claimed.items()
        if str(key) not in {
            "document", "text", "stdout", "stderr", "patch", "content",
            # Execution/replay markers belong to the effect journal, not the
            # stable adapter receipt.  Keeping them out makes the spine's
            # idempotent receipt identical on a durable replay.
            "performed", "replayed", "journal_state", "action_key",
        }
    }


@dataclass(frozen=True, slots=True)
class AdapterReceipt:
    """The normalized, durable-safe handoff to MissionLoop verification."""

    receipt_id: str
    mission_id: str
    workflow_id: str
    step_id: str
    attempt: int
    capability_id: str
    capability_version: str
    action_kind: str
    effect_class: EffectClass
    effect_id: str
    idempotency_key: str
    claimed_outcome: str
    observed_outcome: str
    state: str
    observed: Mapping[str, Any] = field(default_factory=dict)
    postcondition_results: tuple[PostconditionResult, ...] = ()
    before_state_ref: str | None = None
    after_state_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    error: Mapping[str, Any] | None = None
    provider_ref: str | None = None
    cost: Mapping[str, Any] = field(default_factory=dict)
    world_model_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "receipt_id", "mission_id", "workflow_id", "step_id", "capability_id",
            "capability_version", "action_kind", "effect_id", "idempotency_key",
            "claimed_outcome", "observed_outcome", "state",
        ):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        if not isinstance(self.attempt, int) or isinstance(self.attempt, bool) or self.attempt < 1:
            raise ContractError("receipt.attempt must be a positive integer")
        if not isinstance(self.effect_class, EffectClass):
            object.__setattr__(self, "effect_class", EffectClass(self.effect_class))
        object.__setattr__(self, "observed", _mapping(self.observed, "receipt.observed"))
        object.__setattr__(self, "postcondition_results", tuple(self.postcondition_results))
        object.__setattr__(self, "evidence_refs", _tuple_text(self.evidence_refs, "receipt.evidence_refs"))
        object.__setattr__(self, "world_model_refs", _tuple_text(self.world_model_refs, "receipt.world_model_refs"))
        object.__setattr__(self, "cost", _mapping(self.cost, "receipt.cost"))
        if self.error is not None:
            object.__setattr__(self, "error", _mapping(self.error, "receipt.error"))
        if self.provider_ref is not None:
            object.__setattr__(self, "provider_ref", redact_text(str(self.provider_ref))[:500])

    @classmethod
    def from_execution(
        cls,
        request: AdapterRequest,
        *,
        claimed: Mapping[str, Any],
        claimed_outcome: str,
        verified: str,
        note: str = "",
        postcondition_results: Sequence[PostconditionResult] = (),
        world_model_refs: Sequence[str] = (),
    ) -> "AdapterReceipt":
        claimed_value = str(claimed_outcome or "UNKNOWN")
        verified_value = str(verified or "UNKNOWN")
        if claimed_value == "COMPLETED" and verified_value == "MATCH":
            state = "VERIFIED"
            observed_outcome = "VERIFIED"
        elif claimed_value == "BLOCKED":
            state = "BLOCKED"
            observed_outcome = "BLOCKED"
        elif claimed_value == "UNKNOWN" or verified_value == "UNKNOWN":
            state = "FROZEN" if request.effect_class in {
                EffectClass.REVERSIBLE_EXTERNAL,
                EffectClass.IRREVERSIBLE_OR_HIGH_COST,
            } else "UNKNOWN"
            observed_outcome = "UNKNOWN"
        elif verified_value == "MISMATCH":
            state = "FAILED"
            observed_outcome = "MISMATCH"
        else:
            state = "FAILED" if claimed_value == "FAILED" else "COMPLETED_UNVERIFIED"
            observed_outcome = claimed_value
        error_code = claimed.get("error_code")
        if isinstance(error_code, str) and not error_code.strip():
            error_code = None
        if error_code is None and state in {"FAILED", "FROZEN", "UNKNOWN", "BLOCKED"}:
            error_code = state.lower()
        error = None
        if error_code is not None or note:
            retryable = (
                state == "FAILED"
                and request.effect_class in {EffectClass.READ_ONLY, EffectClass.REVERSIBLE_LOCAL}
            )
            error = {
                "class": str(error_code or "adapter_error"),
                "retryable": retryable,
                "detail": redact_text(str(note or ""))[:400],
            }
        evidence = list(dict.fromkeys(
            [str(item) for item in (claimed.get("evidence_refs") or ()) if str(item).strip()]
            + [f"capability:{request.capability_id}", f"effect:{request.effect_id}"]
        ))
        after = claimed.get("after_state_ref")
        if after is None and claimed.get("content_hash"):
            after = f"content:{claimed.get('content_hash')}"
        return cls(
            receipt_id=f"receipt:{request.effect_id}:attempt:{request.attempt}",
            mission_id=request.mission_id,
            workflow_id=request.workflow_id,
            step_id=request.step_id,
            attempt=request.attempt,
            capability_id=request.capability_id,
            capability_version=request.capability_version,
            action_kind=request.action_kind,
            effect_class=request.effect_class,
            effect_id=request.effect_id,
            idempotency_key=request.idempotency_key,
            claimed_outcome=claimed_value,
            observed_outcome=observed_outcome,
            state=state,
            observed=_safe_observed(claimed),
            postcondition_results=tuple(postcondition_results),
            before_state_ref=request.before_state_ref,
            after_state_ref=str(after) if after is not None else None,
            evidence_refs=tuple(evidence),
            error=error,
            provider_ref=(str(claimed.get("provider_ref")) if claimed.get("provider_ref") else None),
            cost=claimed.get("cost") or {},
            world_model_refs=tuple(str(item) for item in world_model_refs),
        )

    @property
    def verified(self) -> bool:
        return self.state == "VERIFIED"

    def with_world_model_refs(self, refs: Sequence[str]) -> "AdapterReceipt":
        return replace(self, world_model_refs=tuple(str(item) for item in refs))

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "receipt_id": self.receipt_id,
            "mission_id": self.mission_id,
            "workflow_id": self.workflow_id,
            "step_id": self.step_id,
            "attempt": self.attempt,
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "action_kind": self.action_kind,
            "effect_class": self.effect_class.value,
            "effect_id": self.effect_id,
            "idempotency_key": self.idempotency_key,
            "claimed_outcome": self.claimed_outcome,
            "observed_outcome": self.observed_outcome,
            "state": self.state,
            "observed": dict(self.observed),
            "postcondition_results": [item.as_dict() for item in self.postcondition_results],
            "before_state_ref": self.before_state_ref,
            "after_state_ref": self.after_state_ref,
            "evidence_refs": list(self.evidence_refs),
            "error": dict(self.error) if self.error is not None else None,
            "provider_ref": self.provider_ref,
            "cost": dict(self.cost),
            "world_model_refs": list(self.world_model_refs),
        }


def build_adapter_request(**kwargs: Any) -> AdapterRequest:
    return AdapterRequest.build(**kwargs)


def normalize_receipt(
    request: AdapterRequest,
    *,
    claimed: Mapping[str, Any],
    claimed_outcome: str,
    verified: str,
    note: str = "",
    postcondition_results: Sequence[PostconditionResult] = (),
    world_model_refs: Sequence[str] = (),
) -> AdapterReceipt:
    return AdapterReceipt.from_execution(
        request,
        claimed=claimed,
        claimed_outcome=claimed_outcome,
        verified=verified,
        note=note,
        postcondition_results=postcondition_results,
        world_model_refs=world_model_refs,
    )


__all__ = [
    "AdapterReceipt",
    "AdapterRequest",
    "CapabilityMetadata",
    "ContractError",
    "CONTRACT_VERSION",
    "EffectClass",
    "PostconditionResult",
    "PostconditionStatus",
    "build_adapter_request",
    "evaluate_postconditions",
    "metadata_for_spec",
    "normalize_receipt",
]
