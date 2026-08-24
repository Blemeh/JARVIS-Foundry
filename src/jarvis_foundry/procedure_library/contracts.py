"""Typed contracts for reusable, evidence-gated JARVIS procedures."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping

from ..events import canonical_json


class CatalogError(ValueError):
    """Raised when a procedure contract cannot be represented safely."""


class ProofStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    PROVEN_ONCE = "PROVEN_ONCE"
    TRUSTED = "TRUSTED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"


class RiskClass(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


VALUE_TYPES = frozenset({"string", "path", "identifier", "enum", "integer", "boolean"})
REVERSIBILITIES = frozenset({"reversible", "irreversible", "mixed"})
RISK_CLASSES = frozenset(item.value for item in RiskClass)
PROOF_STATUSES = frozenset(item.value for item in ProofStatus)
FAILURE_CLASSES = frozenset({
    "PRODUCT_DEFECT", "TEST_DEFECT", "ENVIRONMENT_HOST", "UNKNOWN",
    "MISSING_PRECONDITION", "WRONG_TARGET", "UNAVAILABLE_CAPABILITY",
    "TEST_FAILURE", "TIMEOUT", "AMBIGUOUS_EFFECT", "VERIFIER_REJECTION",
    "RESTART",
})

SUPPORTED_CAPABILITIES = frozenset({
    "filesystem.read", "filesystem.write", "git.read", "git.write",
    "git.push.disposable", "tests.run", "diagnostics.read", "service.read",
    "service.repair.disposable", "release.build", "release.switch.disposable",
    "research.read", "knowledge.read", "knowledge.write.disposable",
    "communication.draft", "calendar.read", "calendar.draft",
    "inbox.read", "evidence.verify", "procedure.select", "procedure.version",
    "incident.read", "project.read", "dependency.read", "dependency.write.disposable",
})

_IDENTITY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]*$")
_PARAMETER = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION = re.compile(r"^v?\d+\.\d+\.\d+$")
_SECRET_VALUE = re.compile(
    r"(?i)(?:api[_ -]?key|password|secret|access[_ -]?token|private key|credential)\s*[:=]"
)
_PATH_VALUE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\|%[A-Z_][A-Z0-9_]*%|\$HOME|\$\{|\$\()")


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise CatalogError(f"{field_name} contains a control character")
    return result


def _identity(value: object, field_name: str) -> str:
    result = _text(value, field_name)
    if not _IDENTITY.fullmatch(result) or any(token in result for token in ("*", "..", "${", "%", "$(")):
        raise CatalogError(f"{field_name} must be an exact identity")
    return result


def _items(value: Iterable[object], field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise CatalogError(f"{field_name} must be a sequence of strings")
    result = tuple(_text(item, field_name) for item in value)
    if len(set(result)) != len(result):
        raise CatalogError(f"{field_name} must not contain duplicates")
    return result


def _json_mapping(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogError(f"{field_name} must be an object")
    try:
        return dict(__import__("json").loads(canonical_json(dict(value))))
    except (TypeError, ValueError) as exc:
        raise CatalogError(f"{field_name} must contain JSON-safe values") from exc


def _scan_values(value: object, path: str = "") -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        if _SECRET_VALUE.search(value):
            found.append((path, "possible hardcoded secret"))
        if _PATH_VALUE.search(value):
            found.append((path, "user-specific or environment path"))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            found.extend(_scan_values(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            found.extend(_scan_values(item, f"{path}[{index}]"))
    return tuple(found)


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    value_type: str
    required: bool = True
    description: str = ""
    allowed_values: tuple[str, ...] = ()
    default: str | None = None

    def __post_init__(self) -> None:
        name = _text(self.name, "parameter name")
        if not _PARAMETER.fullmatch(name):
            raise CatalogError("parameter name must be snake_case")
        value_type = _text(self.value_type, "parameter value_type")
        if value_type not in VALUE_TYPES:
            raise CatalogError(f"unsupported parameter type {value_type!r}")
        if not isinstance(self.required, bool):
            raise CatalogError("parameter required must be boolean")
        description = self.description if not self.description else _text(self.description, "parameter description")
        allowed = _items(self.allowed_values, "parameter allowed_values")
        if value_type == "enum" and not allowed:
            raise CatalogError("enum parameters require allowed_values")
        if self.required and self.default is not None:
            raise CatalogError("required parameters cannot define a default")
        if self.default is not None:
            _text(self.default, "parameter default")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "value_type", value_type)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "allowed_values", allowed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value_type": self.value_type,
            "required": self.required,
            "description": self.description,
            "allowed_values": list(self.allowed_values),
            "default": self.default,
        }


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    retryable_failures: tuple[str, ...] = ()
    requires_reconciliation: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise CatalogError("retry max_attempts must be a positive integer")
        failures = _items(self.retryable_failures, "retryable_failures")
        unknown = set(failures) - FAILURE_CLASSES
        if unknown:
            raise CatalogError(f"unknown retry failure classes: {sorted(unknown)}")
        if not isinstance(self.requires_reconciliation, bool):
            raise CatalogError("retry reconciliation flag must be boolean")
        object.__setattr__(self, "retryable_failures", failures)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "retryable_failures": list(self.retryable_failures),
            "requires_reconciliation": self.requires_reconciliation,
        }


@dataclass(frozen=True, slots=True)
class FailureMode:
    failure_id: str
    trigger: str
    classification: str
    expected_behavior: str

    def __post_init__(self) -> None:
        failure_id = _identity(self.failure_id, "failure_id")
        trigger = _text(self.trigger, "failure trigger")
        classification = _text(self.classification, "failure classification")
        if classification not in FAILURE_CLASSES:
            raise CatalogError(f"unknown failure classification {classification!r}")
        expected = _text(self.expected_behavior, "failure expected_behavior")
        object.__setattr__(self, "failure_id", failure_id)
        object.__setattr__(self, "trigger", trigger)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "expected_behavior", expected)

    def as_dict(self) -> dict[str, str]:
        return {
            "failure_id": self.failure_id,
            "trigger": self.trigger,
            "classification": self.classification,
            "expected_behavior": self.expected_behavior,
        }


@dataclass(frozen=True, slots=True)
class OracleSpec:
    oracle_id: str
    checks: tuple[str, ...]
    independent_verifier_required: bool = True

    def __post_init__(self) -> None:
        oracle_id = _identity(self.oracle_id, "oracle_id")
        checks = _items(self.checks, "oracle checks")
        if not checks:
            raise CatalogError("oracle must declare at least one check")
        if not isinstance(self.independent_verifier_required, bool):
            raise CatalogError("oracle independence flag must be boolean")
        object.__setattr__(self, "oracle_id", oracle_id)
        object.__setattr__(self, "checks", checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "oracle_id": self.oracle_id,
            "checks": list(self.checks),
            "independent_verifier_required": self.independent_verifier_required,
        }


@dataclass(frozen=True, slots=True)
class ProcedureStep:
    step_id: str
    action: str
    target: str
    preconditions: tuple[str, ...]
    capabilities: tuple[str, ...]
    risk_class: str
    reversibility: str
    recovery: str
    postconditions: tuple[str, ...]
    evidence_outputs: tuple[str, ...]
    stop_behavior: str
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        values = {
            "step_id": _identity(self.step_id, "step_id"),
            "action": _identity(self.action, "step action"),
            "target": _identity(self.target, "step target"),
            "risk_class": _text(self.risk_class, "step risk_class"),
            "reversibility": _text(self.reversibility, "step reversibility"),
            "recovery": _text(self.recovery, "step recovery"),
            "stop_behavior": _text(self.stop_behavior, "step stop_behavior"),
        }
        if values["risk_class"] not in RISK_CLASSES:
            raise CatalogError(f"unknown step risk class {values['risk_class']!r}")
        if values["reversibility"] not in REVERSIBILITIES:
            raise CatalogError(f"unknown step reversibility {values['reversibility']!r}")
        preconditions = _items(self.preconditions, "step preconditions")
        capabilities = _items(self.capabilities, "step capabilities")
        postconditions = _items(self.postconditions, "step postconditions")
        evidence = _items(self.evidence_outputs, "step evidence_outputs")
        if not postconditions or not evidence:
            raise CatalogError("each step requires postconditions and evidence outputs")
        for key, value in values.items():
            object.__setattr__(self, key, value)
        object.__setattr__(self, "preconditions", preconditions)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "postconditions", postconditions)
        object.__setattr__(self, "evidence_outputs", evidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "target": self.target,
            "preconditions": list(self.preconditions),
            "capabilities": list(self.capabilities),
            "risk_class": self.risk_class,
            "reversibility": self.reversibility,
            "recovery": self.recovery,
            "postconditions": list(self.postconditions),
            "evidence_outputs": list(self.evidence_outputs),
            "stop_behavior": self.stop_behavior,
            "retry_policy": self.retry_policy.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProcedureDefinition:
    procedure_id: str
    name: str
    domain: str
    version: str
    purpose: str
    parameters: tuple[ParameterSpec, ...]
    preconditions: tuple[str, ...]
    capabilities: tuple[str, ...]
    risk_class: str
    reversibility: str
    steps: tuple[ProcedureStep, ...]
    oracle: OracleSpec | None
    evidence_outputs: tuple[str, ...]
    known_failures: tuple[FailureMode, ...]
    repair_policy: str
    stop_behavior: str
    timeout_seconds: int
    estimated_cost_usd: float
    proof_status: str = ProofStatus.CANDIDATE.value
    last_verified: str | None = None
    supersedes: str | None = None
    dependencies: tuple[str, ...] = ()
    runner_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        values = {
            "procedure_id": _identity(self.procedure_id, "procedure_id"),
            "name": _text(self.name, "procedure name"),
            "domain": _identity(self.domain, "procedure domain"),
            "version": _text(self.version, "procedure version"),
            "purpose": _text(self.purpose, "procedure purpose"),
            "risk_class": _text(self.risk_class, "procedure risk_class"),
            "reversibility": _text(self.reversibility, "procedure reversibility"),
            "repair_policy": _text(self.repair_policy, "repair_policy"),
            "stop_behavior": _text(self.stop_behavior, "stop_behavior"),
            "runner_id": _identity(self.runner_id, "runner_id"),
        }
        if not _VERSION.fullmatch(values["version"]):
            raise CatalogError("procedure version must be semantic major.minor.patch")
        if values["risk_class"] not in RISK_CLASSES:
            raise CatalogError(f"unknown procedure risk class {values['risk_class']!r}")
        if values["reversibility"] not in REVERSIBILITIES:
            raise CatalogError(f"unknown procedure reversibility {values['reversibility']!r}")
        if self.proof_status not in PROOF_STATUSES:
            raise CatalogError(f"unknown proof status {self.proof_status!r}")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int) or self.timeout_seconds < 1:
            raise CatalogError("timeout_seconds must be a positive integer")
        if isinstance(self.estimated_cost_usd, bool) or not isinstance(self.estimated_cost_usd, (int, float)) or self.estimated_cost_usd < 0:
            raise CatalogError("estimated_cost_usd must be non-negative")
        parameters = tuple(self.parameters)
        if len({item.name for item in parameters}) != len(parameters):
            raise CatalogError("parameter names must be unique")
        steps = tuple(self.steps)
        if not steps:
            raise CatalogError("procedure must have at least one step")
        if len({item.step_id for item in steps}) != len(steps):
            raise CatalogError("step ids must be unique")
        preconditions = _items(self.preconditions, "procedure preconditions")
        capabilities = _items(self.capabilities, "procedure capabilities")
        evidence = _items(self.evidence_outputs, "procedure evidence_outputs")
        failures = tuple(self.known_failures)
        if len({item.failure_id for item in failures}) != len(failures):
            raise CatalogError("failure ids must be unique")
        if not failures:
            raise CatalogError("procedure must declare known failure modes")
        dependencies = _items(self.dependencies, "procedure dependencies")
        supersedes = _identity(self.supersedes, "supersedes") if self.supersedes is not None else None
        if self.last_verified is not None:
            _text(self.last_verified, "last_verified")
        if self.proof_status in {ProofStatus.PROVEN_ONCE.value, ProofStatus.TRUSTED.value} and not self.last_verified:
            raise CatalogError("proven procedures require last_verified")
        metadata = _json_mapping(self.metadata, "metadata")
        for key, value in values.items():
            object.__setattr__(self, key, value)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "preconditions", preconditions)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "evidence_outputs", evidence)
        object.__setattr__(self, "known_failures", failures)
        object.__setattr__(self, "supersedes", supersedes)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "metadata", metadata)

    def as_dict(self) -> dict[str, Any]:
        return {
            "procedure_id": self.procedure_id,
            "name": self.name,
            "domain": self.domain,
            "version": self.version,
            "purpose": self.purpose,
            "parameters": [item.as_dict() for item in self.parameters],
            "preconditions": list(self.preconditions),
            "capabilities": list(self.capabilities),
            "risk_class": self.risk_class,
            "reversibility": self.reversibility,
            "steps": [item.as_dict() for item in self.steps],
            "oracle": self.oracle.as_dict() if self.oracle else None,
            "evidence_outputs": list(self.evidence_outputs),
            "known_failures": [item.as_dict() for item in self.known_failures],
            "repair_policy": self.repair_policy,
            "stop_behavior": self.stop_behavior,
            "timeout_seconds": self.timeout_seconds,
            "estimated_cost_usd": self.estimated_cost_usd,
            "proof_status": self.proof_status,
            "last_verified": self.last_verified,
            "supersedes": self.supersedes,
            "dependencies": list(self.dependencies),
            "runner_id": self.runner_id,
            "metadata": self.metadata,
        }

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.as_dict()).encode("utf-8")).hexdigest()

    def with_proof(self, *, status: str, last_verified: str) -> "ProcedureDefinition":
        if status not in {ProofStatus.PROVEN_ONCE.value, ProofStatus.TRUSTED.value, ProofStatus.PARTIAL.value, ProofStatus.REJECTED.value}:
            raise CatalogError(f"invalid promotion status {status!r}")
        return ProcedureDefinition(
            **{**self.as_dict(), "parameters": self.parameters, "steps": self.steps,
               "oracle": self.oracle, "known_failures": self.known_failures,
               "proof_status": status, "last_verified": last_verified}
        )


def contract_findings(definition: ProcedureDefinition) -> tuple[tuple[str, str], ...]:
    """Return security-sensitive string findings for the catalog validator."""
    return _scan_values(definition.as_dict())


__all__ = [
    "CatalogError", "FailureMode", "FAILURE_CLASSES", "OracleSpec", "ParameterSpec",
    "ProcedureDefinition", "ProcedureStep", "ProofStatus", "RiskClass", "RetryPolicy",
    "SUPPORTED_CAPABILITIES", "contract_findings",
]
