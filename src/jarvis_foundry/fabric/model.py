"""V9 capability-fabric domain types.

Readiness is derived from evidence, never from a declaration. UNKNOWN
stays UNKNOWN. READY cannot be constructed without a last-verified
stamp. Unattended mode is a first-class field so interactive providers
cannot be selected for autonomous work by accident.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class Kind(StrEnum):
    CONNECTOR = "connector"
    PROVIDER = "provider"
    TOOL = "tool"
    SCRIPT = "script"
    SKILL = "skill"
    WORKFLOW = "workflow"
    ADAPTER = "adapter"


class Readiness(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"
    UNKNOWN = "UNKNOWN"
    VETOED = "VETOED"
    MISSING = "MISSING"


class CapabilityTrust(StrEnum):
    """Catalog trust for a capability registration.

    ``ESTABLISHED`` is the legacy/default state for capabilities that were
    already governed by Fabric.  A self-expanded capability is deliberately
    registered as ``PROBATION`` until a separately authorized promotion
    decision exists.  Trust is distinct from operational readiness: a
    probation capability may be READY for a bounded read, while still being
    forbidden from irreversible effects.
    """

    ESTABLISHED = "ESTABLISHED"
    PROBATION = "PROBATION"
    DISABLED = "DISABLED"


class UnattendedMode(StrEnum):
    ALLOWED = "ALLOWED"
    INTERACTIVE_REQUIRED = "INTERACTIVE_REQUIRED"
    UNKNOWN = "UNKNOWN"


class Semantic(StrEnum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    COMMUNICATE = "communicate"
    EXTERNAL_EFFECT = "external_effect"


class GapKind(StrEnum):
    CONFIGURATION = "configuration"
    TOOL = "tool"
    PROVIDER = "provider"
    CONNECTOR = "connector"
    SCRIPT = "script"
    WORKFLOW = "workflow"
    SKILL = "skill"
    ADAPTER = "adapter"
    OWNER_PERMISSION = "owner_permission"
    EXTERNAL_DEPENDENCY = "external_dependency"


class GapStatus(StrEnum):
    DETECTED = "DETECTED"
    SPECIFIED = "SPECIFIED"
    CANDIDATE = "CANDIDATE"
    REJECTED = "REJECTED"
    REGISTERED = "REGISTERED"


class ClaimOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class VerifyVerdict(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


_SEMVER = __import__("re").compile(r"^\d+\.\d+\.\d+$")
_IDENTITY = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9_.:/-]*$")


class FabricError(ValueError):
    """A caller supplied an illegal fabric record or transition."""


def _identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FabricError(f"{field_name} must be a non-empty identity")
    result = value.strip()
    if not _IDENTITY.fullmatch(result):
        raise FabricError(f"{field_name} must be an exact identity")
    return result


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FabricError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise FabricError(f"{field_name} contains a control character")
    return result


def _optional_identity(value: object, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    return _identity(value, field_name)


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """What a capability claims it can do. Not proof that it can."""

    capability_id: str
    version: str
    kind: Kind
    action_kind: str
    semantic: Semantic
    executor: str
    requirements: tuple[str, ...] = ()
    unattended: UnattendedMode = UnattendedMode.UNKNOWN
    limitations: tuple[str, ...] = ()
    supersedes: str | None = None
    provider_deps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _identity(self.capability_id, "capability_id"))
        object.__setattr__(self, "action_kind", _identity(self.action_kind, "action_kind"))
        object.__setattr__(self, "executor", _text(self.executor, "executor"))
        if not _SEMVER.match(self.version):
            raise FabricError(f"version must be N.N.N, got {self.version!r}")
        if not isinstance(self.kind, Kind):
            object.__setattr__(self, "kind", Kind(self.kind))
        if not isinstance(self.semantic, Semantic):
            object.__setattr__(self, "semantic", Semantic(self.semantic))
        if not isinstance(self.unattended, UnattendedMode):
            object.__setattr__(self, "unattended", UnattendedMode(self.unattended))
        reqs = tuple(_identity(item, "requirement") for item in self.requirements)
        object.__setattr__(self, "requirements", reqs)
        limits = tuple(_text(item, "limitation") for item in self.limitations)
        object.__setattr__(self, "limitations", limits)
        deps = tuple(_identity(item, "provider_dep") for item in self.provider_deps)
        object.__setattr__(self, "provider_deps", deps)
        object.__setattr__(self, "supersedes", _optional_identity(self.supersedes, "supersedes"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "kind": self.kind.value,
            "action_kind": self.action_kind,
            "semantic": self.semantic.value,
            "executor": self.executor,
            "requirements": list(self.requirements),
            "unattended": self.unattended.value,
            "limitations": list(self.limitations),
            "supersedes": self.supersedes,
            "provider_deps": list(self.provider_deps),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CapabilitySpec":
        return cls(
            capability_id=str(value["capability_id"]),
            version=str(value["version"]),
            kind=Kind(value["kind"]),
            action_kind=str(value["action_kind"]),
            semantic=Semantic(value["semantic"]),
            executor=str(value["executor"]),
            requirements=tuple(value.get("requirements") or ()),
            unattended=UnattendedMode(value.get("unattended") or UnattendedMode.UNKNOWN),
            limitations=tuple(value.get("limitations") or ()),
            supersedes=value.get("supersedes"),
            provider_deps=tuple(value.get("provider_deps") or ()),
        )


@dataclass(frozen=True, slots=True)
class ObservedReadiness:
    """Derived operational truth for one capability id."""

    capability_id: str
    state: Readiness
    last_verified_at: str | None = None
    last_outcome: str | None = None
    consecutive_failures: int = 0
    note: str = ""
    claimed_vs_verified: VerifyVerdict = VerifyVerdict.UNKNOWN
    trust_state: CapabilityTrust = CapabilityTrust.ESTABLISHED

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _identity(self.capability_id, "capability_id"))
        if not isinstance(self.state, Readiness):
            object.__setattr__(self, "state", Readiness(self.state))
        if not isinstance(self.claimed_vs_verified, VerifyVerdict):
            object.__setattr__(
                self, "claimed_vs_verified", VerifyVerdict(self.claimed_vs_verified)
            )
        if not isinstance(self.trust_state, CapabilityTrust):
            object.__setattr__(self, "trust_state", CapabilityTrust(self.trust_state))
        if self.consecutive_failures < 0:
            raise FabricError("consecutive_failures cannot be negative")
        if self.state is Readiness.READY:
            if not self.last_verified_at:
                raise FabricError("READY requires last_verified_at")
            if self.last_outcome != "SUCCESS":
                raise FabricError("READY requires last_outcome SUCCESS")
        if self.state is Readiness.UNKNOWN and self.last_outcome == "SUCCESS":
            raise FabricError("UNKNOWN cannot carry a SUCCESS outcome")

    def selectable(self, *, unattended: bool, allow_probation: bool = False) -> bool:
        if self.state is not Readiness.READY:
            return False
        if self.trust_state is CapabilityTrust.DISABLED:
            return False
        return self.trust_state is CapabilityTrust.ESTABLISHED or allow_probation

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "state": self.state.value,
            "last_verified_at": self.last_verified_at,
            "last_outcome": self.last_outcome,
            "consecutive_failures": self.consecutive_failures,
            "note": self.note,
            "claimed_vs_verified": self.claimed_vs_verified.value,
            "trust_state": self.trust_state.value,
        }


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """One bound action in a multi-capability workflow.

    Payload strings starting with ``$`` resolve against prior step
    observations (``$write.path``). Combinations are data, not code.
    """

    step_id: str
    action_kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    on_failure: str = "stop"
    step_version: int = 1
    postconditions: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _identity(self.step_id, "step_id"))
        object.__setattr__(self, "action_kind", _identity(self.action_kind, "action_kind"))
        if self.on_failure not in {"stop", "continue", "soft", "optional", "retry"}:
            raise FabricError("on_failure must be stop, continue, soft, optional, or retry")
        if (
            not isinstance(self.step_version, int)
            or isinstance(self.step_version, bool)
            or self.step_version < 1
        ):
            raise FabricError("step_version must be a positive integer")
        normalized_postconditions: list[object] = []
        for item in self.postconditions:
            if isinstance(item, str):
                normalized_postconditions.append(_text(item, "postcondition"))
            elif isinstance(item, Mapping):
                normalized_postconditions.append(dict(item))
            else:
                raise FabricError("postcondition must be a string or mapping")
        object.__setattr__(self, "postconditions", tuple(normalized_postconditions))
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class TaskIntent:
    task_id: str
    required_actions: tuple[str, ...]
    unattended: bool = True
    preferred: str | None = None
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identity(self.task_id, "task_id"))
        actions = tuple(_identity(item, "required_action") for item in self.required_actions)
        if not actions:
            raise FabricError("task intent requires at least one action")
        object.__setattr__(self, "required_actions", actions)
        object.__setattr__(self, "preferred", _optional_identity(self.preferred, "preferred"))
        object.__setattr__(
            self, "constraints", tuple(_text(item, "constraint") for item in self.constraints)
        )


@dataclass(frozen=True, slots=True)
class GapRecord:
    gap_id: str
    needed_action: str
    missing: GapKind
    status: GapStatus
    detail: str
    candidate_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "gap_id", _identity(self.gap_id, "gap_id"))
        object.__setattr__(self, "needed_action", _identity(self.needed_action, "needed_action"))
        if not isinstance(self.missing, GapKind):
            object.__setattr__(self, "missing", GapKind(self.missing))
        if not isinstance(self.status, GapStatus):
            object.__setattr__(self, "status", GapStatus(self.status))
        object.__setattr__(self, "detail", _text(self.detail, "detail"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "needed_action": self.needed_action,
            "missing": self.missing.value,
            "status": self.status.value,
            "detail": self.detail,
            "candidate_path": self.candidate_path,
        }


@dataclass(frozen=True, slots=True)
class Selection:
    task_id: str
    action_kind: str
    capability_id: str | None
    fallbacks: tuple[str, ...]
    reason: str
    gap: GapRecord | None = None

    @property
    def available(self) -> bool:
        return self.capability_id is not None and self.gap is None


@dataclass(frozen=True, slots=True)
class ActionResult:
    capability_id: str
    action_kind: str
    claimed: ClaimOutcome
    verified: VerifyVerdict
    observed: dict[str, Any] = field(default_factory=dict)
    claimed_effect: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    receipt: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.claimed is ClaimOutcome.COMPLETED and self.verified is VerifyVerdict.MATCH

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "capability_id": self.capability_id,
            "action_kind": self.action_kind,
            "claimed": self.claimed.value,
            "verified": self.verified.value,
            "observed": dict(self.observed),
            "claimed_effect": dict(self.claimed_effect),
            "note": self.note,
        }
        if self.receipt:
            payload["receipt"] = dict(self.receipt)
        return payload


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    provider: str
    model: str
    unattended: UnattendedMode
    selected: bool
    reason: str
    fallbacks: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "unattended": self.unattended.value,
            "selected": self.selected,
            "reason": self.reason,
            "fallbacks": list(self.fallbacks),
            "capabilities": list(self.capabilities),
        }


FAILURE_THRESHOLD = 3
