"""Typed inputs and outputs for the V12 thin-adapter factory.

The factory consumes a *selected* discovery solution.  It does not rank
solutions and it does not register capabilities.  Its records are deliberately
JSON-safe so the canonical artifact trial can consume the exact descriptor
without inventing a second adapter interpretation.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..effects import EffectClass
from ..events import canonical_json
from ..fabric.model import CapabilitySpec, Kind, Semantic, UnattendedMode


DESCRIPTOR_VERSION = "jarvis.v12.selected-solution-adapter.v1"
ARTIFACT_VERSION = "jarvis.v12.adapter-artifact.v1"
ADAPTER_PROTOCOL = "jarvis.capability.adapter.v1"
_IDENTITY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AdapterFactoryError(ValueError):
    """A selected solution or generated artifact is unsafe or malformed."""


class SolutionClass(StrEnum):
    PYTHON_SDK = "python_sdk"
    CLI = "cli"
    HTTP_API = "http_api"
    MCP_STDIO = "mcp_stdio"
    JARVIS_COMPONENT = "jarvis_component"


_SOLUTION_CLASS_ALIASES = {
    "api": SolutionClass.HTTP_API,
    "cli": SolutionClass.CLI,
    "http": SolutionClass.HTTP_API,
    "http_api": SolutionClass.HTTP_API,
    "jarvis": SolutionClass.JARVIS_COMPONENT,
    "jarvis_component": SolutionClass.JARVIS_COMPONENT,
    "mcp": SolutionClass.MCP_STDIO,
    "mcp_stdio": SolutionClass.MCP_STDIO,
    "python": SolutionClass.PYTHON_SDK,
    "python_sdk": SolutionClass.PYTHON_SDK,
    "sdk": SolutionClass.PYTHON_SDK,
    "source": SolutionClass.JARVIS_COMPONENT,
}


def _solution_class(value: Any) -> SolutionClass:
    normalized = str(value or "").strip().lower().replace("-", "_")
    try:
        return _SOLUTION_CLASS_ALIASES[normalized]
    except KeyError as error:
        raise AdapterFactoryError(f"unsupported solution class {value!r}") from error


class AdapterShape(StrEnum):
    CONNECTOR = "connector"
    WORKER = "worker"


class DependencyKind(StrEnum):
    PYTHON = "python"
    EXECUTABLE = "executable"
    SERVICE = "service"
    JARVIS = "jarvis"


class ProbeKind(StrEnum):
    PYTHON_IMPORT = "python_import"
    COMMAND = "command"
    HTTP = "http"
    MCP_TOOLS_LIST = "mcp_tools_list"
    JARVIS_IMPORT = "jarvis_import"


def _identity(value: object, name: str, *, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterFactoryError(f"{name} must be a non-empty identity")
    result = value.strip()
    if len(result) > limit or not _IDENTITY.fullmatch(result):
        raise AdapterFactoryError(f"{name} must be a bounded identity")
    return result


def _text(value: object, name: str, *, limit: int = 2048) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdapterFactoryError(f"{name} must be non-empty text")
    result = value.strip()
    if len(result) > limit or any(ord(char) < 32 for char in result):
        raise AdapterFactoryError(f"{name} is invalid or too long")
    return result


def _strings(value: Sequence[object] | None, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise AdapterFactoryError(f"{name} must be a list")
    return tuple(_text(item, f"{name} item", limit=512) for item in value)


def _mapping(value: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AdapterFactoryError(f"{name} must be an object")
    try:
        result = json.loads(canonical_json(dict(value)))
    except (TypeError, ValueError) as error:
        raise AdapterFactoryError(f"{name} must contain JSON-safe values") from error
    if not isinstance(result, dict):
        raise AdapterFactoryError(f"{name} must be an object")
    return result


def _semver(value: object, name: str) -> str:
    result = _text(value, name, limit=80)
    if not re.fullmatch(r"\d+\.\d+\.\d+", result):
        raise AdapterFactoryError(f"{name} must use N.N.N")
    return result


@dataclass(frozen=True, slots=True)
class DependencyDeclaration:
    name: str
    kind: DependencyKind
    version: str
    source: str
    sha256: str | None = None
    import_name: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identity(self.name, "dependency.name"))
        if not isinstance(self.kind, DependencyKind):
            object.__setattr__(self, "kind", DependencyKind(self.kind))
        object.__setattr__(self, "version", _text(self.version, "dependency.version", limit=120))
        object.__setattr__(self, "source", _text(self.source, "dependency.source", limit=512))
        if self.sha256 is not None:
            digest = str(self.sha256).lower()
            if not _SHA256.fullmatch(digest):
                raise AdapterFactoryError("dependency.sha256 must be a lowercase sha256")
            object.__setattr__(self, "sha256", digest)
        if self.import_name is not None:
            object.__setattr__(self, "import_name", _identity(self.import_name, "dependency.import_name"))
        if not isinstance(self.required, bool):
            raise AdapterFactoryError("dependency.required must be boolean")

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "version": self.version,
            "source": self.source,
            "sha256": self.sha256,
            "import_name": self.import_name,
            "required": self.required,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DependencyDeclaration":
        return cls(
            name=value.get("name"),
            kind=DependencyKind(value.get("kind")),
            version=value.get("version"),
            source=value.get("source"),
            sha256=value.get("sha256"),
            import_name=value.get("import_name"),
            required=value.get("required", True),
        )


@dataclass(frozen=True, slots=True)
class ConfigurationRequirement:
    name: str
    source: str = "environment"
    required: bool = True
    secret: bool = False
    description: str = "runtime configuration"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identity(self.name, "configuration.name"))
        if self.source not in {"environment", "runtime", "file"}:
            raise AdapterFactoryError("configuration.source is unsupported")
        if not isinstance(self.required, bool) or not isinstance(self.secret, bool):
            raise AdapterFactoryError("configuration flags must be boolean")
        object.__setattr__(self, "description", _text(self.description, "configuration.description", limit=512))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "required": self.required,
            "secret": self.secret,
            "description": self.description,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ConfigurationRequirement":
        return cls(
            name=value.get("name"),
            source=str(value.get("source") or "environment"),
            required=value.get("required", True),
            secret=value.get("secret", False),
            description=str(value.get("description") or "runtime configuration"),
        )


@dataclass(frozen=True, slots=True)
class ReadinessProbe:
    kind: ProbeKind
    target: str
    arguments: tuple[str, ...] = ()
    expected: Mapping[str, Any] = field(default_factory=dict)
    timeout_ms: int = 10_000

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProbeKind):
            object.__setattr__(self, "kind", ProbeKind(self.kind))
        object.__setattr__(self, "target", _text(self.target, "readiness.target"))
        object.__setattr__(self, "arguments", _strings(self.arguments, "readiness.arguments"))
        object.__setattr__(self, "expected", _mapping(self.expected, "readiness.expected"))
        if not isinstance(self.timeout_ms, int) or isinstance(self.timeout_ms, bool) or not 50 <= self.timeout_ms <= 300_000:
            raise AdapterFactoryError("readiness.timeout_ms must be between 50 and 300000")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "target": self.target,
            "arguments": list(self.arguments),
            "expected": dict(self.expected),
            "timeout_ms": self.timeout_ms,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReadinessProbe":
        return cls(
            kind=ProbeKind(value.get("kind")),
            target=value.get("target"),
            arguments=tuple(value.get("arguments") or ()),
            expected=value.get("expected") or {},
            timeout_ms=int(value.get("timeout_ms") or 10_000),
        )


@dataclass(frozen=True, slots=True)
class InvocationMapping:
    """Declarative, non-shell mapping from JARVIS input to one solution call."""

    target: str
    arguments: tuple[Any, ...] = ()
    keyword_arguments: Mapping[str, Any] = field(default_factory=dict)
    method: str | None = None
    headers: Mapping[str, Any] = field(default_factory=dict)
    output: Mapping[str, Any] = field(default_factory=dict)
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _text(self.target, "invocation.target"))
        try:
            arguments = json.loads(canonical_json(list(self.arguments)))
        except (TypeError, ValueError) as error:
            raise AdapterFactoryError("invocation.arguments must be JSON-safe") from error
        object.__setattr__(self, "arguments", tuple(arguments))
        object.__setattr__(self, "keyword_arguments", _mapping(self.keyword_arguments, "invocation.keyword_arguments"))
        object.__setattr__(self, "headers", _mapping(self.headers, "invocation.headers"))
        object.__setattr__(self, "output", _mapping(self.output, "invocation.output"))
        object.__setattr__(self, "allowed_origins", _strings(self.allowed_origins, "invocation.allowed_origins"))
        if self.method is not None:
            method = _identity(self.method.upper(), "invocation.method", limit=20)
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                raise AdapterFactoryError("invocation.method is unsupported")
            object.__setattr__(self, "method", method)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "arguments": list(self.arguments),
            "keyword_arguments": dict(self.keyword_arguments),
            "method": self.method,
            "headers": dict(self.headers),
            "output": dict(self.output),
            "allowed_origins": list(self.allowed_origins),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "InvocationMapping":
        return cls(
            target=value.get("target"),
            arguments=tuple(value.get("arguments") or ()),
            keyword_arguments=value.get("keyword_arguments") or {},
            method=value.get("method"),
            headers=value.get("headers") or {},
            output=value.get("output") or {},
            allowed_origins=tuple(value.get("allowed_origins") or ()),
        )


@dataclass(frozen=True, slots=True)
class ReconciliationMapping:
    strategy: str = "none"
    compare_fields: tuple[str, ...] = ()
    invocation: InvocationMapping | None = None
    ambiguity_outcome: str = "FROZEN"

    def __post_init__(self) -> None:
        if self.strategy not in {"none", "repeat_read", "system_readback", "provider_query"}:
            raise AdapterFactoryError("reconciliation.strategy is unsupported")
        object.__setattr__(self, "compare_fields", _strings(self.compare_fields, "reconciliation.compare_fields"))
        if self.invocation is not None and not isinstance(self.invocation, InvocationMapping):
            raise AdapterFactoryError("reconciliation.invocation must be an InvocationMapping")
        if self.ambiguity_outcome not in {"UNKNOWN", "FROZEN"}:
            raise AdapterFactoryError("reconciliation.ambiguity_outcome must be UNKNOWN or FROZEN")

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "compare_fields": list(self.compare_fields),
            "invocation": self.invocation.as_dict() if self.invocation else None,
            "ambiguity_outcome": self.ambiguity_outcome,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ReconciliationMapping":
        raw = value or {}
        invocation = raw.get("invocation")
        return cls(
            strategy=str(raw.get("strategy") or "none"),
            compare_fields=tuple(raw.get("compare_fields") or ()),
            invocation=InvocationMapping.from_mapping(invocation) if isinstance(invocation, Mapping) else None,
            ambiguity_outcome=str(raw.get("ambiguity_outcome") or "FROZEN"),
        )


@dataclass(frozen=True, slots=True)
class ContractCase:
    case_id: str
    payload: Mapping[str, Any]
    expected: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _identity(self.case_id, "contract_case.case_id"))
        object.__setattr__(self, "payload", _mapping(self.payload, "contract_case.payload"))
        object.__setattr__(self, "expected", _mapping(self.expected, "contract_case.expected"))

    def as_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "payload": dict(self.payload), "expected": dict(self.expected)}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ContractCase":
        return cls(value.get("case_id"), value.get("payload") or {}, value.get("expected") or {})


@dataclass(frozen=True, slots=True)
class CapabilityAdapterContract:
    capability_id: str
    version: str
    action_kind: str
    semantic: Semantic
    effect_class: EffectClass
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    invocation: InvocationMapping
    reconciliation: ReconciliationMapping = field(default_factory=ReconciliationMapping)
    timeout_ms: int = 120_000
    cancel_supported: bool = True
    unattended: UnattendedMode = UnattendedMode.ALLOWED
    requirements: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[object, ...] = ()
    limitations: tuple[str, ...] = ()
    contract_cases: tuple[ContractCase, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _identity(self.capability_id, "capability_id"))
        object.__setattr__(self, "version", _semver(self.version, "capability.version"))
        object.__setattr__(self, "action_kind", _identity(self.action_kind, "action_kind"))
        if not isinstance(self.semantic, Semantic):
            object.__setattr__(self, "semantic", Semantic(self.semantic))
        if not isinstance(self.effect_class, EffectClass):
            object.__setattr__(self, "effect_class", EffectClass(self.effect_class))
        if not isinstance(self.unattended, UnattendedMode):
            object.__setattr__(self, "unattended", UnattendedMode(self.unattended))
        object.__setattr__(self, "input_schema", _mapping(self.input_schema, "input_schema"))
        object.__setattr__(self, "output_schema", _mapping(self.output_schema, "output_schema"))
        if not isinstance(self.invocation, InvocationMapping):
            raise AdapterFactoryError("capability.invocation must be an InvocationMapping")
        if not isinstance(self.reconciliation, ReconciliationMapping):
            raise AdapterFactoryError("capability.reconciliation must be a ReconciliationMapping")
        if not isinstance(self.timeout_ms, int) or isinstance(self.timeout_ms, bool) or not 50 <= self.timeout_ms <= 3_600_000:
            raise AdapterFactoryError("capability.timeout_ms is out of bounds")
        if not isinstance(self.cancel_supported, bool):
            raise AdapterFactoryError("capability.cancel_supported must be boolean")
        object.__setattr__(self, "requirements", _strings(self.requirements, "capability.requirements"))
        object.__setattr__(self, "preconditions", _strings(self.preconditions, "capability.preconditions"))
        object.__setattr__(self, "limitations", _strings(self.limitations, "capability.limitations"))
        object.__setattr__(self, "contract_cases", tuple(self.contract_cases))
        if any(not isinstance(case, ContractCase) for case in self.contract_cases):
            raise AdapterFactoryError("capability.contract_cases must contain ContractCase values")
        if self.effect_class is not EffectClass.READ_ONLY and self.reconciliation.strategy == "none":
            raise AdapterFactoryError("mutating capabilities require a reconciliation seam")

    @property
    def input_schema_id(self) -> str:
        return str(self.input_schema.get("$id") or f"jarvis.action.{self.action_kind}.input.v1")

    @property
    def output_schema_id(self) -> str:
        return str(self.output_schema.get("$id") or f"jarvis.action.{self.action_kind}.receipt.v1")

    def fabric_spec(self, *, executor: str) -> CapabilitySpec:
        return CapabilitySpec(
            capability_id=self.capability_id,
            version=self.version,
            kind=Kind.ADAPTER,
            action_kind=self.action_kind,
            semantic=self.semantic,
            executor=executor,
            requirements=self.requirements,
            unattended=self.unattended,
            limitations=self.limitations,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "action_kind": self.action_kind,
            "semantic": self.semantic.value,
            "effect_class": self.effect_class.value,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "invocation": self.invocation.as_dict(),
            "reconciliation": self.reconciliation.as_dict(),
            "timeout_ms": self.timeout_ms,
            "cancel_supported": self.cancel_supported,
            "unattended": self.unattended.value,
            "requirements": list(self.requirements),
            "preconditions": list(self.preconditions),
            "postconditions": list(self.postconditions),
            "limitations": list(self.limitations),
            "contract_cases": [case.as_dict() for case in self.contract_cases],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CapabilityAdapterContract":
        return cls(
            capability_id=value.get("capability_id"),
            version=value.get("version"),
            action_kind=value.get("action_kind"),
            semantic=Semantic(value.get("semantic")),
            effect_class=EffectClass(value.get("effect_class")),
            input_schema=value.get("input_schema") or {},
            output_schema=value.get("output_schema") or {},
            invocation=InvocationMapping.from_mapping(value.get("invocation") or {}),
            reconciliation=ReconciliationMapping.from_mapping(value.get("reconciliation")),
            timeout_ms=int(value.get("timeout_ms") or 120_000),
            cancel_supported=value.get("cancel_supported", True),
            unattended=UnattendedMode(value.get("unattended") or UnattendedMode.ALLOWED),
            requirements=tuple(value.get("requirements") or ()),
            preconditions=tuple(value.get("preconditions") or ()),
            postconditions=tuple(value.get("postconditions") or ()),
            limitations=tuple(value.get("limitations") or ()),
            contract_cases=tuple(ContractCase.from_mapping(case) for case in (value.get("contract_cases") or ())),
        )


@dataclass(frozen=True, slots=True)
class SelectedSolutionDescriptor:
    adapter_id: str
    solution_id: str
    solution_class: SolutionClass
    solution_name: str
    solution_version: str
    locator: str
    adapter_shape: AdapterShape
    readiness: ReadinessProbe
    capabilities: tuple[CapabilityAdapterContract, ...]
    dependencies: tuple[DependencyDeclaration, ...]
    configuration: tuple[ConfigurationRequirement, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    selection: Mapping[str, Any] = field(default_factory=dict)
    disable_reason: str = "adapter disabled before probation adoption"

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _identity(self.adapter_id, "adapter_id"))
        object.__setattr__(self, "solution_id", _identity(self.solution_id, "solution_id"))
        if not isinstance(self.solution_class, SolutionClass):
            object.__setattr__(self, "solution_class", SolutionClass(self.solution_class))
        if not isinstance(self.adapter_shape, AdapterShape):
            object.__setattr__(self, "adapter_shape", AdapterShape(self.adapter_shape))
        object.__setattr__(self, "solution_name", _text(self.solution_name, "solution_name", limit=240))
        object.__setattr__(self, "solution_version", _text(self.solution_version, "solution_version", limit=120))
        object.__setattr__(self, "locator", _text(self.locator, "locator"))
        if not isinstance(self.readiness, ReadinessProbe):
            raise AdapterFactoryError("readiness must be a ReadinessProbe")
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        object.__setattr__(self, "configuration", tuple(self.configuration))
        if not self.capabilities or len({item.capability_id for item in self.capabilities}) != len(self.capabilities):
            raise AdapterFactoryError("capabilities must be non-empty with unique ids")
        if len({item.action_kind for item in self.capabilities}) != len(self.capabilities):
            raise AdapterFactoryError("capability action kinds must be unique per adapter")
        if not self.dependencies:
            raise AdapterFactoryError("at least one dependency declaration is required")
        object.__setattr__(self, "provenance", _mapping(self.provenance, "provenance"))
        object.__setattr__(self, "selection", _mapping(self.selection, "selection"))
        object.__setattr__(self, "disable_reason", _text(self.disable_reason, "disable_reason", limit=512))

    @property
    def descriptor_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.as_dict()).encode("utf-8")).hexdigest()

    @property
    def artifact_id(self) -> str:
        return f"{self.adapter_id}-{self.descriptor_hash[:16]}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": DESCRIPTOR_VERSION,
            "adapter_protocol": ADAPTER_PROTOCOL,
            "adapter_id": self.adapter_id,
            "solution_id": self.solution_id,
            "solution_class": self.solution_class.value,
            "solution_name": self.solution_name,
            "solution_version": self.solution_version,
            "locator": self.locator,
            "adapter_shape": self.adapter_shape.value,
            "readiness": self.readiness.as_dict(),
            "capabilities": [item.as_dict() for item in self.capabilities],
            "dependencies": [item.as_dict() for item in self.dependencies],
            "configuration": [item.as_dict() for item in self.configuration],
            "provenance": dict(self.provenance),
            "selection": dict(self.selection),
            "disable_reason": self.disable_reason,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SelectedSolutionDescriptor":
        if not isinstance(value, Mapping):
            raise AdapterFactoryError("selected solution descriptor must be an object")
        version = value.get("contract_version")
        if version not in {None, DESCRIPTOR_VERSION}:
            raise AdapterFactoryError(f"unsupported descriptor contract {version!r}")
        return cls(
            adapter_id=value.get("adapter_id"),
            solution_id=value.get("solution_id") or value.get("candidate_id"),
            solution_class=_solution_class(value.get("solution_class") or value.get("solution_kind")),
            solution_name=value.get("solution_name") or value.get("name"),
            solution_version=str(value.get("solution_version") or value.get("version") or "unknown"),
            locator=value.get("locator"),
            adapter_shape=AdapterShape(value.get("adapter_shape") or "connector"),
            readiness=ReadinessProbe.from_mapping(value.get("readiness") or {}),
            capabilities=tuple(CapabilityAdapterContract.from_mapping(item) for item in (value.get("capabilities") or ())),
            dependencies=tuple(DependencyDeclaration.from_mapping(item) for item in (value.get("dependencies") or ())),
            configuration=tuple(ConfigurationRequirement.from_mapping(item) for item in (value.get("configuration") or ())),
            provenance=value.get("provenance") or {},
            selection=value.get("selection") or {
                key: value.get(key)
                for key in ("candidate_id", "gap_id", "tier", "summary", "compatibility", "integration_cost")
                if value.get(key) is not None
            },
            disable_reason=str(value.get("disable_reason") or "adapter disabled before probation adoption"),
        )

    @classmethod
    def load(cls, path: str | Path) -> "SelectedSolutionDescriptor":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AdapterFactoryError(f"cannot read selected solution descriptor: {path}") from error
        return cls.from_mapping(value)


@dataclass(frozen=True, slots=True)
class AdapterBuildArtifact:
    artifact_id: str
    adapter_id: str
    descriptor_hash: str
    root: Path
    manifest_path: Path
    files: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": ARTIFACT_VERSION,
            "artifact_id": self.artifact_id,
            "adapter_id": self.adapter_id,
            "descriptor_hash": self.descriptor_hash,
            "root": str(self.root),
            "manifest_path": str(self.manifest_path),
            "files": dict(self.files),
        }


__all__ = [
    "ADAPTER_PROTOCOL", "ARTIFACT_VERSION", "DESCRIPTOR_VERSION",
    "AdapterBuildArtifact", "AdapterFactoryError", "AdapterShape",
    "CapabilityAdapterContract", "ConfigurationRequirement", "ContractCase",
    "DependencyDeclaration", "DependencyKind", "InvocationMapping", "ProbeKind",
    "ReadinessProbe", "ReconciliationMapping", "SelectedSolutionDescriptor",
    "SolutionClass",
]
