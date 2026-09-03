"""Reuse-first capability solution discovery for V12.

This module is deliberately a discovery/comparison lane.  It turns the
read-only V11 capability requirement set into a durable gap requirement,
searches the existing Fabric and source tree, performs bounded Git history
archaeology, and (when needed) asks the existing GitHub research connector for
mature external candidates.  The result is a comparison and recommendation;
there is no install, registration, trial, provider write, worktree creation,
or execution path here.

Every external value is evidence only (``DATA_ONLY``).  The only readiness
authority consulted for an existing capability is the Capability Fabric
Catalog.  Git history is intentionally implemented here instead of being
confused with ``operations.reuse``: that module reuses verified procedures,
not repository archaeology.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .capability_requirements import (
    CapabilityRequirementSet,
    RequirementReadiness,
)
from .clock import isoformat
from .database import FoundryDatabase
from .events import Event, EventConflict, canonical_json, payload_hash
from .fabric.catalog import catalog_snapshot
from .connectors.github_research import GitHubResearchConnector
from .platform.windows_subprocess import headless_run_kwargs
from .research_contract import classify_content, record_research_item


CONTRACT_VERSION = "jarvis.v12.capability-solution-discovery.v1"
GAP_CONTRACT_VERSION = "jarvis.v12.capability-gap-requirement.v1"
SEARCH_CONTRACT_VERSION = "jarvis.v12.capability-search-report.v1"
COMPARISON_CONTRACT_VERSION = "jarvis.v12.capability-comparison.v1"
RECOMMENDATION_CONTRACT_VERSION = "jarvis.v12.capability-recommendation.v1"

SOURCE_ID = "v12-capability-discovery"
SYSTEM_AUTHORITY = "JARVIS_SYSTEM_METADATA"
EXTERNAL_AUTHORITY = "DATA_ONLY"
FABRIC_PATH = "src/jarvis_foundry/fabric/catalog.py"

REQUIREMENT_RECORDED = "capability.gap.requirement_recorded"
SEARCH_COMPLETED = "capability.solution.search_completed"
CANDIDATE_RECORDED = "capability.solution.candidate_recorded"
COMPARISON_RECORDED = "capability.solution.comparison_recorded"
RECOMMENDATION_RECORDED = "capability.solution.recommendation_recorded"

MAX_TEXT = 1200
MAX_SUMMARY = 1800
MAX_EVIDENCE_ITEMS = 24
MAX_EVIDENCE_TEXT = 900
MAX_SEARCH_TERMS = 8
MAX_SOURCE_FILES = 600
MAX_SOURCE_FILE_BYTES = 384 * 1024
MAX_HISTORY_REFS = 120
MAX_HISTORY_WORKTREES = 120
MAX_HISTORY_COMMITS = 80
MAX_HISTORY_HEADS = 32
MAX_HISTORY_MATCHES = 48


class DiscoveryError(ValueError):
    """A discovery contract or bounded search input is invalid."""


class ReuseTier(IntEnum):
    """The frozen solution search order (lower is earlier/better)."""

    FABRIC_CATALOG = 1
    JARVIS_SOURCE = 2
    JARVIS_GIT = 3
    EXTERNAL = 4
    CUSTOM_BUILD = 5

    @property
    def label(self) -> str:
        return {
            ReuseTier.FABRIC_CATALOG: "FABRIC_CATALOG",
            ReuseTier.JARVIS_SOURCE: "JARVIS_SOURCE",
            ReuseTier.JARVIS_GIT: "JARVIS_GIT",
            ReuseTier.EXTERNAL: "EXTERNAL",
            ReuseTier.CUSTOM_BUILD: "CUSTOM_BUILD",
        }[self]


class CandidateDisposition(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    RECOMMENDED = "RECOMMENDED"


class SearchState(StrEnum):
    COMPLETED = "COMPLETED"
    EMPTY = "EMPTY"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def _text(value: Any, field_name: str, *, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    result = str(value).replace("\x00", "").strip()
    if any(ord(char) < 32 and char not in "\n\t" for char in result):
        raise DiscoveryError(f"{field_name} contains a control character")
    return result[:limit]


def _identity(value: Any, field_name: str, *, limit: int = 256) -> str:
    result = _text(value, field_name, limit=limit)
    if not result or any(char.isspace() for char in result):
        raise DiscoveryError(f"{field_name} must be a non-empty bounded identity")
    return result


def _unique_text(value: Any, field_name: str, *, limit: int = 80) -> tuple[str, ...]:
    if value is None:
        return ()
    values: Sequence[Any]
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise DiscoveryError(f"{field_name} must be a list")
    return tuple(sorted({
        _text(item, f"{field_name} item", limit=limit)
        for item in values
        if _text(item, f"{field_name} item", limit=limit)
    }))


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DiscoveryError(f"{field_name} must be an object")
    try:
        normalized = json.loads(canonical_json(dict(value)))
    except (TypeError, ValueError) as error:
        raise DiscoveryError(f"{field_name} must be JSON-safe") from error
    if not isinstance(normalized, dict):
        raise DiscoveryError(f"{field_name} must be an object")
    return normalized


def _bounded_float(value: Any, field_name: str, *, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise DiscoveryError(f"{field_name} must be numeric") from error
    if not math.isfinite(result):
        raise DiscoveryError(f"{field_name} must be finite")
    return max(0.0, min(1.0, result))


def _bounded_hours(value: Any, field_name: str, *, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise DiscoveryError(f"{field_name} must be numeric") from error
    if not math.isfinite(result) or result < 0.0 or result > 10000.0:
        raise DiscoveryError(f"{field_name} must be finite and between 0 and 10000")
    return result


def _safe_int(value: Any, *, default: int = 0, maximum: int = 2_000_000_000) -> int:
    """Coerce untrusted provider counters without letting one row abort a search."""

    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, min(maximum, result))


def _safe_bool(value: Any, *, default: bool = False) -> bool:
    """Normalize JSON-ish booleans; string ``"false"`` must not become true."""

    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes", "y"}:
        return True
    if lowered in {"false", "0", "no", "n", ""}:
        return False
    return default


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _now() -> str:
    return isoformat()


@dataclass(frozen=True, slots=True)
class CompatibilityRequirements:
    """Constraints an adapter must satisfy before a trial is considered."""

    platforms: tuple[str, ...] = ("windows",)
    runtime: str = "python>=3.13"
    integration_modes: tuple[str, ...] = (
        "source", "python", "sdk", "cli", "api", "mcp",
    )
    required_interfaces: tuple[str, ...] = (
        "bounded adapter contract", "readback/evidence hook", "STOP-aware boundary",
    )
    forbidden_effects: tuple[str, ...] = (
        "install", "register", "provider_write", "live_mutation",
    )
    license_policy: str = "permissive_or_owner_approved"
    unattended: bool = True
    read_only_trial: bool = True
    max_new_dependencies: int = 3
    max_adapter_files: int = 6

    def __post_init__(self) -> None:
        object.__setattr__(self, "platforms", _unique_text(self.platforms, "platforms"))
        object.__setattr__(self, "integration_modes", _unique_text(
            self.integration_modes, "integration_modes"))
        object.__setattr__(self, "required_interfaces", _unique_text(
            self.required_interfaces, "required_interfaces"))
        object.__setattr__(self, "forbidden_effects", _unique_text(
            self.forbidden_effects, "forbidden_effects"))
        object.__setattr__(self, "runtime", _text(self.runtime, "runtime", limit=120))
        object.__setattr__(self, "license_policy", _text(
            self.license_policy, "license_policy", limit=120))
        if not isinstance(self.unattended, bool) or not isinstance(self.read_only_trial, bool):
            raise DiscoveryError("unattended and read_only_trial must be booleans")
        if int(self.max_new_dependencies) < 0 or int(self.max_adapter_files) < 1:
            raise DiscoveryError("dependency/file limits are invalid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "platforms": list(self.platforms),
            "runtime": self.runtime,
            "integration_modes": list(self.integration_modes),
            "required_interfaces": list(self.required_interfaces),
            "forbidden_effects": list(self.forbidden_effects),
            "license_policy": self.license_policy,
            "unattended": self.unattended,
            "read_only_trial": self.read_only_trial,
            "max_new_dependencies": int(self.max_new_dependencies),
            "max_adapter_files": int(self.max_adapter_files),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "CompatibilityRequirements":
        raw = _mapping(value, "compatibility")
        return cls(
            platforms=_unique_text(raw.get("platforms") or ("windows",), "platforms"),
            runtime=_text(raw.get("runtime") or "python>=3.13", "runtime", limit=120),
            integration_modes=_unique_text(raw.get("integration_modes") or (
                "source", "python", "sdk", "cli", "api", "mcp",
            ), "integration_modes"),
            required_interfaces=_unique_text(raw.get("required_interfaces") or (
                "bounded adapter contract", "readback/evidence hook", "STOP-aware boundary",
            ), "required_interfaces"),
            forbidden_effects=_unique_text(raw.get("forbidden_effects") or (
                "install", "register", "provider_write", "live_mutation",
            ), "forbidden_effects"),
            license_policy=_text(raw.get("license_policy") or "permissive_or_owner_approved", "license_policy", limit=120),
            unattended=bool(raw.get("unattended", True)),
            read_only_trial=bool(raw.get("read_only_trial", True)),
            max_new_dependencies=int(raw.get("max_new_dependencies", 3)),
            max_adapter_files=int(raw.get("max_adapter_files", 6)),
        )


@dataclass(frozen=True, slots=True)
class CapabilityGapRequirement:
    """Durable, typed input to one bounded solution-discovery run."""

    gap_id: str
    requirement_set_id: str
    requested_action: str | None
    missing_capability_id: str | None
    description: str
    required_actions: tuple[str, ...]
    required_capability_ids: tuple[str, ...]
    compatibility: CompatibilityRequirements = field(default_factory=CompatibilityRequirements)
    candidate_identity: Mapping[str, Any] = field(default_factory=dict)
    blocking: bool = True
    source: str = "capability_requirements"
    authority: str = SYSTEM_AUTHORITY
    external_content_policy: str = EXTERNAL_AUTHORITY
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "gap_id", _identity(self.gap_id, "gap_id"))
        object.__setattr__(self, "requirement_set_id", _identity(
            self.requirement_set_id, "requirement_set_id"))
        if self.requested_action is not None:
            object.__setattr__(self, "requested_action", _identity(
                self.requested_action, "requested_action"))
        if self.missing_capability_id is not None:
            object.__setattr__(self, "missing_capability_id", _identity(
                self.missing_capability_id, "missing_capability_id"))
        object.__setattr__(self, "description", _text(self.description, "description", limit=MAX_SUMMARY))
        if not self.description:
            raise DiscoveryError("description is required")
        object.__setattr__(self, "required_actions", _unique_text(
            self.required_actions, "required_actions"))
        object.__setattr__(self, "required_capability_ids", _unique_text(
            self.required_capability_ids, "required_capability_ids"))
        if not self.required_actions and not self.required_capability_ids:
            raise DiscoveryError("at least one required action or capability is required")
        if self.authority != SYSTEM_AUTHORITY:
            raise DiscoveryError("gap requirements require system metadata authority")
        if self.external_content_policy != EXTERNAL_AUTHORITY:
            raise DiscoveryError("external content policy must remain DATA_ONLY")
        object.__setattr__(self, "candidate_identity", _mapping(
            self.candidate_identity, "candidate_identity"))

    @classmethod
    def from_requirement_set(
        cls,
        requirement_set: CapabilityRequirementSet,
        *,
        compatibility: CompatibilityRequirements | None = None,
    ) -> tuple["CapabilityGapRequirement", ...]:
        """Project missing/blocked actions into stable discovery inputs."""

        if not isinstance(requirement_set, CapabilityRequirementSet):
            raise DiscoveryError("requirement_set must be a CapabilityRequirementSet")
        evidence = {
            item.capability_id: item
            for item in requirement_set.capability_evidence
        }
        requested: list[tuple[str | None, str | None, str]] = []
        for capability_id in requirement_set.missing_capabilities:
            item = evidence.get(capability_id)
            action = item.action_kind if item is not None else None
            requested.append((action, capability_id, "capability is missing from the Fabric Catalog"))
        for capability_id in requirement_set.blocked_capabilities:
            if capability_id in requirement_set.missing_capabilities:
                continue
            item = evidence.get(capability_id)
            action = item.action_kind if item is not None else None
            reason = item.blocker if item is not None else "capability is not usable"
            requested.append((action, capability_id, f"capability is blocked: {reason}"))
        for action in requirement_set.unresolved_actions:
            requested.append((action, None, "required action has no Fabric Catalog mapping"))
        if not requested and requirement_set.readiness is not RequirementReadiness.READY:
            requested.append((None, None, "structured capability requirements are blocked"))
        result: list[CapabilityGapRequirement] = []
        for action, capability_id, reason in requested:
            marker = {
                "requirement_set_id": requirement_set.requirement_set_id,
                "requested_action": action,
                "missing_capability_id": capability_id,
                "required_actions": list(requirement_set.required_capability_ids),
            }
            gap_id = "gapreq-" + hashlib.sha256(
                canonical_json(marker).encode("utf-8")
            ).hexdigest()[:24]
            # A solution fills this one missing/blocked requirement.  Ready
            # sibling dependencies remain context, not accidental extra work.
            actions = (action,) if action else ()
            capability_ids = (capability_id,) if capability_id else ()
            result.append(cls(
                gap_id=gap_id,
                requirement_set_id=requirement_set.requirement_set_id,
                requested_action=action,
                missing_capability_id=capability_id,
                description=reason,
                required_actions=actions,
                required_capability_ids=capability_ids,
                compatibility=compatibility or CompatibilityRequirements(),
                candidate_identity=requirement_set.candidate_identity,
                blocking=True,
            ))
        return tuple(result)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CapabilityGapRequirement":
        raw = _mapping(value, "gap_requirement")
        return cls(
            gap_id=raw["gap_id"],
            requirement_set_id=raw["requirement_set_id"],
            requested_action=raw.get("requested_action"),
            missing_capability_id=raw.get("missing_capability_id"),
            description=raw["description"],
            required_actions=tuple(raw.get("required_actions") or ()),
            required_capability_ids=tuple(raw.get("required_capability_ids") or ()),
            compatibility=CompatibilityRequirements.from_mapping(raw.get("compatibility")),
            candidate_identity=raw.get("candidate_identity") or {},
            blocking=bool(raw.get("blocking", True)),
            source=_text(raw.get("source") or "capability_requirements", "source", limit=160),
            authority=_text(raw.get("authority") or SYSTEM_AUTHORITY, "authority", limit=120),
            external_content_policy=_text(
                raw.get("external_content_policy") or EXTERNAL_AUTHORITY,
                "external_content_policy", limit=80,
            ),
            created_at=_text(raw.get("created_at") or _now(), "created_at", limit=80),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": GAP_CONTRACT_VERSION,
            "gap_id": self.gap_id,
            "requirement_set_id": self.requirement_set_id,
            "requested_action": self.requested_action,
            "missing_capability_id": self.missing_capability_id,
            "description": self.description,
            "required_actions": list(self.required_actions),
            "required_capability_ids": list(self.required_capability_ids),
            "compatibility": self.compatibility.as_dict(),
            "candidate_identity": dict(self.candidate_identity),
            "blocking": self.blocking,
            "source": self.source,
            "authority": self.authority,
            "external_content_policy": self.external_content_policy,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class CompatibilityAssessment:
    state: str
    score: float
    satisfied: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in {"COMPATIBLE", "PARTIAL", "INCOMPATIBLE", "UNKNOWN"}:
            raise DiscoveryError(f"invalid compatibility state {self.state}")
        object.__setattr__(self, "score", _bounded_float(self.score, "compatibility.score"))
        for field_name in ("satisfied", "violations", "unknowns"):
            object.__setattr__(self, field_name, _unique_text(
                getattr(self, field_name), f"compatibility.{field_name}"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "score": self.score,
            "satisfied": list(self.satisfied),
            "violations": list(self.violations),
            "unknowns": list(self.unknowns),
        }


@dataclass(frozen=True, slots=True)
class CapabilityCoverage:
    required_actions: tuple[str, ...]
    matched_actions: tuple[str, ...] = ()
    partial_actions: tuple[str, ...] = ()
    missing_actions: tuple[str, ...] = ()
    score: float = 0.0
    confidence: float = 0.0

    def __post_init__(self) -> None:
        required = set(_unique_text(self.required_actions, "coverage.required_actions"))
        matched = set(_unique_text(self.matched_actions, "coverage.matched_actions")) & required
        partial = set(_unique_text(self.partial_actions, "coverage.partial_actions")) & required
        missing = required - matched - partial
        object.__setattr__(self, "required_actions", tuple(sorted(required)))
        object.__setattr__(self, "matched_actions", tuple(sorted(matched)))
        object.__setattr__(self, "partial_actions", tuple(sorted(partial)))
        object.__setattr__(self, "missing_actions", tuple(sorted(missing)))
        score = (len(matched) + 0.5 * len(partial)) / len(required) if required else 0.0
        object.__setattr__(self, "score", max(score, _bounded_float(self.score, "coverage.score")))
        object.__setattr__(self, "confidence", _bounded_float(self.confidence, "coverage.confidence"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_actions": list(self.required_actions),
            "matched_actions": list(self.matched_actions),
            "partial_actions": list(self.partial_actions),
            "missing_actions": list(self.missing_actions),
            "score": self.score,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class IntegrationCostEstimate:
    person_hours: float
    adapter_files: int
    new_dependencies: int
    configuration_steps: int
    credential_scopes: tuple[str, ...] = ()
    migration_required: bool = False
    rollback_complexity: str = "LOW"
    confidence: float = 0.0
    rationale: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "person_hours", _bounded_hours(
            self.person_hours, "cost.person_hours"))
        for field_name in ("adapter_files", "new_dependencies", "configuration_steps"):
            value = int(getattr(self, field_name))
            if value < 0 or value > 1000:
                raise DiscoveryError(f"cost.{field_name} is out of bounds")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "credential_scopes", _unique_text(
            self.credential_scopes, "cost.credential_scopes"))
        object.__setattr__(self, "rollback_complexity", _text(
            self.rollback_complexity, "cost.rollback_complexity", limit=40).upper() or "UNKNOWN")
        object.__setattr__(self, "confidence", _bounded_float(self.confidence, "cost.confidence"))
        object.__setattr__(self, "rationale", _unique_text(self.rationale, "cost.rationale", limit=MAX_EVIDENCE_TEXT))

    @property
    def score(self) -> float:
        return max(0.0, min(1.0, 1.0 - self.person_hours / 40.0))

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_hours": self.person_hours,
            "adapter_files": self.adapter_files,
            "new_dependencies": self.new_dependencies,
            "configuration_steps": self.configuration_steps,
            "credential_scopes": list(self.credential_scopes),
            "migration_required": self.migration_required,
            "rollback_complexity": self.rollback_complexity,
            "confidence": self.confidence,
            "rationale": list(self.rationale),
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class MaturityEvidence:
    state: str
    score: float
    version: str | None = None
    release_count: int | None = None
    stars: int | None = None
    forks: int | None = None
    license: str | None = None
    archived: bool | None = None
    last_release_at: str | None = None
    docs_present: bool | None = None
    tests_present: bool | None = None
    evidence_refs: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in {"PROVEN", "MATURE", "PRESENT_UNPROVEN", "HISTORICAL", "IMMATURE", "UNKNOWN"}:
            raise DiscoveryError(f"invalid maturity state {self.state}")
        object.__setattr__(self, "score", _bounded_float(self.score, "maturity.score"))
        for field_name in ("version", "license", "last_release_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _text(value, f"maturity.{field_name}", limit=160))
        for field_name in ("release_count", "stars", "forks"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise DiscoveryError(f"maturity.{field_name} must be a non-negative integer")
        object.__setattr__(self, "evidence_refs", _unique_text(self.evidence_refs, "maturity.evidence_refs"))
        object.__setattr__(self, "unknowns", _unique_text(self.unknowns, "maturity.unknowns"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "score": self.score,
            "version": self.version,
            "release_count": self.release_count,
            "stars": self.stars,
            "forks": self.forks,
            "license": self.license,
            "archived": self.archived,
            "last_release_at": self.last_release_at,
            "docs_present": self.docs_present,
            "tests_present": self.tests_present,
            "evidence_refs": list(self.evidence_refs),
            "unknowns": list(self.unknowns),
        }


@dataclass(frozen=True, slots=True)
class MaintenanceEvidence:
    state: str
    score: float
    last_activity_at: str | None = None
    release_cadence: str | None = None
    issue_health: str | None = None
    maintainer_signal: str | None = None
    evidence_refs: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in {"ACTIVE", "MAINTAINED", "HISTORICAL", "STALE", "UNKNOWN"}:
            raise DiscoveryError(f"invalid maintenance state {self.state}")
        object.__setattr__(self, "score", _bounded_float(self.score, "maintenance.score"))
        for field_name in ("last_activity_at", "release_cadence", "issue_health", "maintainer_signal"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _text(value, f"maintenance.{field_name}", limit=160))
        object.__setattr__(self, "evidence_refs", _unique_text(self.evidence_refs, "maintenance.evidence_refs"))
        object.__setattr__(self, "unknowns", _unique_text(self.unknowns, "maintenance.unknowns"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "score": self.score,
            "last_activity_at": self.last_activity_at,
            "release_cadence": self.release_cadence,
            "issue_health": self.issue_health,
            "maintainer_signal": self.maintainer_signal,
            "evidence_refs": list(self.evidence_refs),
            "unknowns": list(self.unknowns),
        }


@dataclass(frozen=True, slots=True)
class SolutionCandidate:
    """Normalized candidate independent of where it was found."""

    candidate_id: str
    gap_id: str
    tier: ReuseTier
    name: str
    solution_kind: str
    locator: str
    version: str | None
    summary: str
    coverage: CapabilityCoverage
    compatibility: CompatibilityAssessment
    integration_cost: IntegrationCostEstimate
    maturity: MaturityEvidence
    maintenance: MaintenanceEvidence
    evidence: tuple[Mapping[str, Any], ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    authority: str = SYSTEM_AUTHORITY
    data_only: bool = False
    disposition: CandidateDisposition = CandidateDisposition.UNREVIEWED
    rejection_reasons: tuple[str, ...] = ()
    installed: bool = False
    registered: bool = False
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _identity(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "gap_id", _identity(self.gap_id, "gap_id"))
        object.__setattr__(self, "name", _text(self.name, "candidate.name", limit=240))
        object.__setattr__(self, "solution_kind", _identity(self.solution_kind, "solution_kind", limit=80))
        object.__setattr__(self, "locator", _text(self.locator, "candidate.locator", limit=MAX_TEXT))
        if not self.locator:
            raise DiscoveryError("candidate locator is required")
        if self.version is not None:
            object.__setattr__(self, "version", _text(self.version, "candidate.version", limit=160))
        object.__setattr__(self, "summary", _text(self.summary, "candidate.summary", limit=MAX_SUMMARY))
        if self.tier is ReuseTier.EXTERNAL:
            if self.authority != EXTERNAL_AUTHORITY or not self.data_only:
                raise DiscoveryError("external candidates must remain DATA_ONLY")
        if self.authority == EXTERNAL_AUTHORITY:
            object.__setattr__(self, "data_only", True)
        object.__setattr__(self, "evidence", _bounded_evidence(self.evidence))
        object.__setattr__(self, "provenance", _mapping(self.provenance, "candidate.provenance"))
        object.__setattr__(self, "rejection_reasons", _unique_text(
            self.rejection_reasons, "candidate.rejection_reasons", limit=240))
        if self.installed or self.registered or self.execution_authorized:
            raise DiscoveryError("solution discovery cannot install, register, or authorize execution")

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "candidate_id": self.candidate_id,
            "gap_id": self.gap_id,
            "tier": self.tier.label,
            "tier_order": int(self.tier),
            "name": self.name,
            "solution_kind": self.solution_kind,
            "locator": self.locator,
            "version": self.version,
            "summary": self.summary,
            "coverage": self.coverage.as_dict(),
            "compatibility": self.compatibility.as_dict(),
            "integration_cost": self.integration_cost.as_dict(),
            "maturity": self.maturity.as_dict(),
            "maintenance": self.maintenance.as_dict(),
            "evidence": [dict(item) for item in self.evidence],
            "provenance": dict(self.provenance),
            "authority": self.authority,
            "data_only": self.data_only,
            "disposition": self.disposition.value,
            "rejection_reasons": list(self.rejection_reasons),
            "installed": False,
            "registered": False,
            "execution_authorized": False,
        }


def _bounded_evidence(value: Iterable[Mapping[str, Any]] | None) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    for item in list(value or ())[:MAX_EVIDENCE_ITEMS]:
        if not isinstance(item, Mapping):
            continue
        try:
            clean = json.loads(canonical_json(dict(item)))
        except (TypeError, ValueError):
            continue
        if isinstance(clean, dict):
            result.append(clean)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class SolutionComparison:
    comparison_id: str
    gap_id: str
    ranked_candidate_ids: tuple[str, ...]
    winner_candidate_id: str | None
    scores: Mapping[str, Mapping[str, Any]]
    rejection_reasons: Mapping[str, tuple[str, ...]]
    reuse_order: tuple[str, ...] = (
        "FABRIC_CATALOG", "JARVIS_SOURCE", "JARVIS_GIT", "EXTERNAL", "CUSTOM_BUILD",
    )
    decision: str = "DEFER"
    evaluated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparison_id", _identity(self.comparison_id, "comparison_id"))
        object.__setattr__(self, "gap_id", _identity(self.gap_id, "gap_id"))
        object.__setattr__(self, "ranked_candidate_ids", _unique_text(
            self.ranked_candidate_ids, "ranked_candidate_ids"))
        if self.winner_candidate_id is not None:
            object.__setattr__(self, "winner_candidate_id", _identity(
                self.winner_candidate_id, "winner_candidate_id"))
        object.__setattr__(self, "scores", _mapping(self.scores, "scores"))
        object.__setattr__(self, "rejection_reasons", {
            str(key): tuple(_unique_text(value, f"rejection_reasons.{key}", limit=240))
            for key, value in dict(self.rejection_reasons).items()
        })
        object.__setattr__(self, "decision", _text(self.decision, "decision", limit=80).upper())
        reuse_order = tuple(_text(item, "reuse_order item", limit=80).upper() for item in self.reuse_order)
        if reuse_order != ("FABRIC_CATALOG", "JARVIS_SOURCE", "JARVIS_GIT", "EXTERNAL", "CUSTOM_BUILD"):
            raise DiscoveryError("reuse_order must preserve the frozen five-tier order")
        object.__setattr__(self, "reuse_order", reuse_order)
        if self.winner_candidate_id is not None and self.winner_candidate_id not in self.ranked_candidate_ids:
            raise DiscoveryError("comparison winner must appear in ranked_candidate_ids")
        object.__setattr__(self, "evaluated_at", _text(self.evaluated_at, "comparison.evaluated_at", limit=80))

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": COMPARISON_CONTRACT_VERSION,
            "comparison_id": self.comparison_id,
            "gap_id": self.gap_id,
            "ranked_candidate_ids": list(self.ranked_candidate_ids),
            "winner_candidate_id": self.winner_candidate_id,
            "scores": dict(self.scores),
            "rejection_reasons": {key: list(value) for key, value in self.rejection_reasons.items()},
            "reuse_order": list(self.reuse_order),
            "decision": self.decision,
            "evaluated_at": self.evaluated_at,
        }


@dataclass(frozen=True, slots=True)
class SolutionRecommendation:
    recommendation_id: str
    gap_id: str
    comparison_id: str
    selected_candidate_id: str | None
    status: str
    reason: str
    next_step: str
    constraints: tuple[str, ...] = (
        "DISCOVERY_ONLY", "NO_INSTALL", "NO_REGISTER", "NO_PROVIDER_WRITE", "NO_LIVE_MUTATION",
    )
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "recommendation_id", _identity(self.recommendation_id, "recommendation_id"))
        object.__setattr__(self, "gap_id", _identity(self.gap_id, "gap_id"))
        object.__setattr__(self, "comparison_id", _identity(self.comparison_id, "comparison_id"))
        if self.selected_candidate_id is not None:
            object.__setattr__(self, "selected_candidate_id", _identity(
                self.selected_candidate_id, "selected_candidate_id"))
        object.__setattr__(self, "status", _text(self.status, "recommendation.status", limit=80).upper())
        if self.status not in {"RECOMMENDED", "DEFERRED", "NO_SOLUTION"}:
            raise DiscoveryError("invalid recommendation status")
        object.__setattr__(self, "reason", _text(self.reason, "recommendation.reason", limit=MAX_SUMMARY))
        object.__setattr__(self, "next_step", _text(self.next_step, "recommendation.next_step", limit=MAX_SUMMARY))
        object.__setattr__(self, "constraints", _unique_text(self.constraints, "recommendation.constraints", limit=120))
        required_constraints = {
            "DISCOVERY_ONLY", "NO_INSTALL", "NO_REGISTER", "NO_PROVIDER_WRITE", "NO_LIVE_MUTATION",
        }
        if not required_constraints.issubset(self.constraints):
            raise DiscoveryError("recommendation constraints must preserve discovery-only boundaries")
        if self.status == "RECOMMENDED" and self.selected_candidate_id is None:
            raise DiscoveryError("recommended solution must select a candidate")
        if self.status == "NO_SOLUTION" and self.selected_candidate_id is not None:
            raise DiscoveryError("no-solution recommendation cannot select a candidate")
        object.__setattr__(self, "created_at", _text(self.created_at, "recommendation.created_at", limit=80))

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": RECOMMENDATION_CONTRACT_VERSION,
            "recommendation_id": self.recommendation_id,
            "gap_id": self.gap_id,
            "comparison_id": self.comparison_id,
            "selected_candidate_id": self.selected_candidate_id,
            "status": self.status,
            "reason": self.reason,
            "next_step": self.next_step,
            "constraints": list(self.constraints),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class SearchReport:
    stage: str
    state: SearchState
    candidate_ids: tuple[str, ...] = ()
    inspected: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[Mapping[str, Any], ...] = ()
    errors: tuple[str, ...] = ()
    read_only: bool = True
    authority: str = SYSTEM_AUTHORITY

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _identity(self.stage, "search.stage", limit=80).upper())
        if not isinstance(self.state, SearchState):
            try:
                object.__setattr__(self, "state", SearchState(str(self.state).upper()))
            except ValueError as error:
                raise DiscoveryError("invalid search state") from error
        object.__setattr__(self, "candidate_ids", _unique_text(self.candidate_ids, "search.candidate_ids", limit=120))
        object.__setattr__(self, "inspected", _mapping(self.inspected, "search.inspected"))
        object.__setattr__(self, "evidence", _bounded_evidence(self.evidence))
        object.__setattr__(self, "errors", _unique_text(self.errors, "search.errors", limit=MAX_EVIDENCE_TEXT))
        if not isinstance(self.read_only, bool) or not self.read_only:
            raise DiscoveryError("solution discovery search reports must be read-only")
        object.__setattr__(self, "authority", _identity(self.authority, "search.authority", limit=120))

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": SEARCH_CONTRACT_VERSION,
            "stage": self.stage,
            "state": self.state.value,
            "candidate_ids": list(self.candidate_ids),
            "inspected": dict(self.inspected),
            "evidence": [dict(item) for item in self.evidence],
            "errors": list(self.errors),
            "read_only": self.read_only,
            "authority": self.authority,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryRun:
    requirement: CapabilityGapRequirement
    candidates: tuple[SolutionCandidate, ...]
    comparison: SolutionComparison
    recommendation: SolutionRecommendation
    search_reports: tuple[SearchReport, ...]
    read_only: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.read_only, bool) or not self.read_only:
            raise DiscoveryError("discovery runs must be read-only")

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "requirement": self.requirement.as_dict(),
            "candidates": [item.as_dict() for item in self.candidates],
            "comparison": self.comparison.as_dict(),
            "recommendation": self.recommendation.as_dict(),
            "search_reports": [item.as_dict() for item in self.search_reports],
            "read_only": self.read_only,
            "mutations": {
                "install": False,
                "register": False,
                "provider_write": False,
                "live_mutation": False,
            },
        }


def _candidate_id(gap_id: str, tier: ReuseTier, locator: str, version: str | None) -> str:
    return "sol-" + hashlib.sha256(canonical_json({
        "gap_id": gap_id, "tier": tier.label, "locator": locator, "version": version,
    }).encode("utf-8")).hexdigest()[:24]


def _required_action_set(requirement: CapabilityGapRequirement) -> tuple[str, ...]:
    actions = set(requirement.required_actions)
    for value in requirement.required_capability_ids:
        if value.startswith("cap."):
            actions.add(value[4:])
    if requirement.requested_action:
        actions.add(requirement.requested_action)
    return tuple(sorted(actions))


def _normalize_coverage(requirement: CapabilityGapRequirement, raw: Mapping[str, Any]) -> CapabilityCoverage:
    required = _required_action_set(requirement)
    matched = raw.get("matched_actions") or raw.get("matched") or ()
    partial = raw.get("partial_actions") or raw.get("partial") or ()
    missing = raw.get("missing_actions") or raw.get("missing") or ()
    if isinstance(matched, str):
        matched = (matched,)
    if isinstance(partial, str):
        partial = (partial,)
    if isinstance(missing, str):
        missing = (missing,)
    return CapabilityCoverage(
        required_actions=required,
        matched_actions=tuple(str(item) for item in matched),
        partial_actions=tuple(str(item) for item in partial),
        missing_actions=tuple(str(item) for item in missing),
        confidence=_bounded_float(raw.get("confidence"), "coverage.confidence", default=0.5),
    )


def _default_cost(tier: ReuseTier, kind: str, *, evidence_count: int = 0) -> IntegrationCostEstimate:
    defaults = {
        ReuseTier.FABRIC_CATALOG: (0.5, 0, 0, 1, "catalog entry already exists"),
        ReuseTier.JARVIS_SOURCE: (4.0, 1, 0, 1, "existing source requires a thin adapter/wiring pass"),
        ReuseTier.JARVIS_GIT: (10.0, 2, 0, 2, "historical code requires semantic port and current-contract review"),
        ReuseTier.EXTERNAL: (12.0, 2, 1, 3, "external solution requires bounded adapter and dependency review"),
        ReuseTier.CUSTOM_BUILD: (32.0, 4, 0, 4, "custom build is a last-resort implementation"),
    }
    hours, files, deps, steps, rationale = defaults[tier]
    if "cli" in kind.lower():
        hours += 2.0
    return IntegrationCostEstimate(
        person_hours=hours, adapter_files=files, new_dependencies=deps,
        configuration_steps=steps, confidence=min(0.95, 0.35 + evidence_count * 0.05),
        rationale=(rationale,),
    )


def _default_compatibility(
    requirement: CapabilityGapRequirement,
    tier: ReuseTier,
    raw: Mapping[str, Any],
) -> CompatibilityAssessment:
    if raw.get("state"):
        return CompatibilityAssessment(
            state=_text(raw.get("state"), "compatibility.state", limit=32).upper(),
            score=_bounded_float(raw.get("score"), "compatibility.score", default=0.5),
            satisfied=tuple(raw.get("satisfied") or ()),
            violations=tuple(raw.get("violations") or ()),
            unknowns=tuple(raw.get("unknowns") or ()),
        )
    if tier is ReuseTier.FABRIC_CATALOG:
        return CompatibilityAssessment("COMPATIBLE", 1.0, ("registered Fabric capability contract",))
    if tier is ReuseTier.JARVIS_SOURCE:
        return CompatibilityAssessment(
            "PARTIAL", 0.7,
            ("same Python runtime and authority spine",),
            unknowns=("source component readiness requires focused verification",),
        )
    if tier is ReuseTier.JARVIS_GIT:
        return CompatibilityAssessment("UNKNOWN", 0.35, unknowns=(
            "historical branch may target a superseded contract",
        ))
    if tier is ReuseTier.CUSTOM_BUILD:
        return CompatibilityAssessment("UNKNOWN", 0.1, unknowns=("no implementation evidence",))
    return CompatibilityAssessment("UNKNOWN", 0.25, unknowns=(
        "external platform, license, and adapter behavior require validation",
    ))


def _default_maturity(tier: ReuseTier, *, version: str | None, refs: Sequence[str] = (), tested: bool = False) -> MaturityEvidence:
    if tier is ReuseTier.FABRIC_CATALOG:
        return MaturityEvidence("PROVEN", 0.9, version=version, tests_present=tested,
                                evidence_refs=tuple(refs))
    if tier is ReuseTier.JARVIS_SOURCE:
        return MaturityEvidence("PRESENT_UNPROVEN", 0.55, version=version,
                                tests_present=tested, evidence_refs=tuple(refs),
                                unknowns=("not a registered/readiness-proven capability",))
    if tier is ReuseTier.JARVIS_GIT:
        return MaturityEvidence("HISTORICAL", 0.45, version=version,
                                evidence_refs=tuple(refs),
                                unknowns=("historical proof is not current runtime proof",))
    if tier is ReuseTier.CUSTOM_BUILD:
        return MaturityEvidence("UNKNOWN", 0.0, evidence_refs=tuple(refs),
                                unknowns=("no mature implementation exists",))
    return MaturityEvidence("UNKNOWN", 0.0, version=version,
                            evidence_refs=tuple(refs),
                            unknowns=("external maturity evidence not supplied",))


def _default_maintenance(tier: ReuseTier, *, refs: Sequence[str] = ()) -> MaintenanceEvidence:
    if tier is ReuseTier.FABRIC_CATALOG:
        return MaintenanceEvidence("MAINTAINED", 0.85, evidence_refs=tuple(refs))
    if tier is ReuseTier.JARVIS_SOURCE:
        return MaintenanceEvidence("ACTIVE", 0.75, evidence_refs=tuple(refs))
    if tier is ReuseTier.JARVIS_GIT:
        return MaintenanceEvidence("HISTORICAL", 0.25, evidence_refs=tuple(refs),
                                   unknowns=("historical branch activity may have ended",))
    if tier is ReuseTier.CUSTOM_BUILD:
        return MaintenanceEvidence("UNKNOWN", 0.0, evidence_refs=tuple(refs),
                                   unknowns=("maintenance burden would be owner-owned",))
    return MaintenanceEvidence("UNKNOWN", 0.0, evidence_refs=tuple(refs),
                               unknowns=("external maintenance signal not supplied",))


def normalize_solution_candidate(
    requirement: CapabilityGapRequirement,
    raw: Mapping[str, Any],
    *,
    tier: ReuseTier,
) -> SolutionCandidate:
    """Normalize one source result without allowing it to become authority."""

    if not isinstance(raw, Mapping):
        raise DiscoveryError("solution candidate must be an object")
    locator = _text(raw.get("locator") or raw.get("source_ref"), "candidate.locator", limit=MAX_TEXT)
    if not locator:
        raise DiscoveryError("candidate locator is required")
    version_raw = raw.get("version")
    version = _text(version_raw, "candidate.version", limit=160) if version_raw is not None else None
    name = _text(raw.get("name") or raw.get("title") or locator, "candidate.name", limit=240)
    kind = _text(raw.get("solution_kind") or raw.get("kind") or tier.label.lower(), "solution_kind", limit=80)
    summary = _text(raw.get("summary") or raw.get("description") or name, "candidate.summary", limit=MAX_SUMMARY)
    evidence = _bounded_evidence(raw.get("evidence") or ())
    provenance = _mapping(raw.get("provenance") or {}, "candidate.provenance")
    authority = EXTERNAL_AUTHORITY if tier is ReuseTier.EXTERNAL else _text(
        raw.get("authority") or SYSTEM_AUTHORITY, "candidate.authority", limit=120)
    candidate_id = _candidate_id(requirement.gap_id, tier, locator, version)
    refs = tuple(
        _text(item.get("ref") or item.get("source_ref") or item.get("locator"), "evidence.ref", limit=MAX_TEXT)
        for item in evidence
        if isinstance(item, Mapping) and (item.get("ref") or item.get("source_ref") or item.get("locator"))
    )
    compatibility = _default_compatibility(requirement, tier, _mapping(raw.get("compatibility") or {}, "compatibility"))
    maturity = _default_maturity(
        tier, version=version, refs=refs,
        tested=bool(raw.get("tests_present") or raw.get("tested")),
    )
    maintenance = _default_maintenance(tier, refs=refs)
    if raw.get("maturity"):
        m = _mapping(raw.get("maturity"), "maturity")
        maturity = MaturityEvidence(
            state=_text(m.get("state") or maturity.state, "maturity.state", limit=40).upper(),
            score=_bounded_float(m.get("score"), "maturity.score", default=maturity.score),
            version=m.get("version", version),
            release_count=m.get("release_count"), stars=m.get("stars"), forks=m.get("forks"),
            license=m.get("license"), archived=m.get("archived"),
            last_release_at=m.get("last_release_at"), docs_present=m.get("docs_present"),
            tests_present=m.get("tests_present"),
            evidence_refs=tuple(m.get("evidence_refs") or refs),
            unknowns=tuple(m.get("unknowns") or maturity.unknowns),
        )
    if raw.get("maintenance"):
        m = _mapping(raw.get("maintenance"), "maintenance")
        maintenance = MaintenanceEvidence(
            state=_text(m.get("state") or maintenance.state, "maintenance.state", limit=40).upper(),
            score=_bounded_float(m.get("score"), "maintenance.score", default=maintenance.score),
            last_activity_at=m.get("last_activity_at"), release_cadence=m.get("release_cadence"),
            issue_health=m.get("issue_health"), maintainer_signal=m.get("maintainer_signal"),
            evidence_refs=tuple(m.get("evidence_refs") or refs),
            unknowns=tuple(m.get("unknowns") or maintenance.unknowns),
        )
    cost = _default_cost(tier, kind, evidence_count=len(evidence))
    if raw.get("integration_cost"):
        c = _mapping(raw.get("integration_cost"), "integration_cost")
        cost = IntegrationCostEstimate(
            person_hours=c.get("person_hours", cost.person_hours),
            adapter_files=c.get("adapter_files", cost.adapter_files),
            new_dependencies=c.get("new_dependencies", cost.new_dependencies),
            configuration_steps=c.get("configuration_steps", cost.configuration_steps),
            credential_scopes=tuple(c.get("credential_scopes") or cost.credential_scopes),
            migration_required=bool(c.get("migration_required", cost.migration_required)),
            rollback_complexity=c.get("rollback_complexity", cost.rollback_complexity),
            confidence=c.get("confidence", cost.confidence),
            rationale=tuple(c.get("rationale") or cost.rationale),
        )
    return SolutionCandidate(
        candidate_id=candidate_id,
        gap_id=requirement.gap_id,
        tier=tier,
        name=name,
        solution_kind=kind,
        locator=locator,
        version=version,
        summary=summary,
        coverage=_normalize_coverage(requirement, _mapping(raw.get("coverage") or {}, "coverage")),
        compatibility=compatibility,
        integration_cost=cost,
        maturity=maturity,
        maintenance=maintenance,
        evidence=evidence,
        provenance=provenance,
        authority=authority,
        data_only=tier is ReuseTier.EXTERNAL or bool(raw.get("data_only")),
    )


def _git_run(repo: Path, *args: str, timeout: float = 15.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, **headless_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 124, "", str(error)
    return result.returncode, result.stdout, result.stderr


def _resolve_git_root(path: str | Path) -> tuple[Path | None, str | None]:
    candidate = Path(path).expanduser().resolve(strict=False)
    code, stdout, stderr = _git_run(candidate, "rev-parse", "--show-toplevel")
    if code != 0 or not stdout.strip():
        return None, _text(stderr or "not a git repository", "git.error", limit=MAX_EVIDENCE_TEXT)
    return Path(stdout.strip()).resolve(), None


def _search_terms(requirement: CapabilityGapRequirement) -> tuple[str, ...]:
    raw = list(_required_action_set(requirement))
    raw.extend(requirement.required_capability_ids)
    raw.append(requirement.description)
    words: set[str] = set()
    stopwords = {
        "cap", "the", "and", "for", "with", "from", "this", "required",
        # Requirement prose is useful context in the durable record, but these
        # generic words create false positives when used as repository/Fabric
        # search literals (for example, every capability row is "bounded").
        "capability", "capabilities", "registered", "registration", "bounded",
        "mission", "owner", "needs", "need", "real", "missing", "blocked",
        "ability", "solution", "search", "requires", "required", "one", "a",
    }
    for value in raw:
        for token in re.split(r"[^A-Za-z0-9_.-]+", value.lower()):
            if len(token) >= 3 and token not in stopwords:
                words.add(token)
    return tuple(sorted(words, key=lambda item: (-len(item), item))[:MAX_SEARCH_TERMS])


def _action_literals(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z][A-Za-z0-9_-]*\.[A-Za-z0-9_.-]+", text))


def _source_candidate(
    requirement: CapabilityGapRequirement,
    *,
    root: Path,
    head: str,
    relative: str,
    text: str,
    line_numbers: Sequence[int],
) -> SolutionCandidate:
    required = set(_required_action_set(requirement))
    literals = _action_literals(text)
    matched = required & literals
    lower = text.lower()
    partial = {
        action for action in required
        if action not in matched and any(part in lower for part in action.split(".") if len(part) > 3)
    }
    rel = relative.replace("\\", "/")
    test_present = "test" in rel.lower() or "tests" in lower[:2000]
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    raw = {
        "name": f"Current JARVIS source: {rel}",
        "solution_kind": "source_component",
        "locator": f"source:{rel}@{head}",
        "version": head,
        "summary": f"Tracked source component matched {', '.join(sorted(set(matched or partial))) or 'search terms'}; readiness still requires an adapter-level proof.",
        "coverage": {"matched_actions": sorted(matched), "partial_actions": sorted(partial), "confidence": 0.8},
        "compatibility": {"state": "PARTIAL", "score": 0.72, "satisfied": ["same JARVIS Python runtime", "existing authority spine"], "unknowns": ["component is not independently registered for the requested gap"]},
        "maturity": {"state": "PRESENT_UNPROVEN", "score": 0.58 if test_present else 0.48, "version": head, "tests_present": test_present, "evidence_refs": [f"source:{rel}"]},
        "maintenance": {"state": "ACTIVE", "score": 0.78, "last_activity_at": head, "evidence_refs": [f"source:{rel}"]},
        "integration_cost": {"person_hours": 3.5 if matched else 6.0, "adapter_files": 1, "new_dependencies": 0, "configuration_steps": 1, "confidence": 0.75},
        "evidence": [{"ref": f"source:{rel}", "head": head, "sha256_16": digest, "line_numbers": list(line_numbers)[:12], "matched_literals": sorted(matched), "authority": SYSTEM_AUTHORITY}],
        "provenance": {"repository": str(root), "path": rel, "head": head, "searcher": "repository-source-search", "read_only": True},
    }
    return normalize_solution_candidate(requirement, raw, tier=ReuseTier.JARVIS_SOURCE)


def search_repository_source(
    requirement: CapabilityGapRequirement,
    repo_root: str | Path,
    *,
    max_files: int = MAX_SOURCE_FILES,
) -> tuple[tuple[SolutionCandidate, ...], SearchReport]:
    """Search tracked current source/config files without executing them."""

    root, error = _resolve_git_root(repo_root)
    if root is None:
        return (), SearchReport("JARVIS_SOURCE", SearchState.BLOCKED,
                                inspected={"repository": str(repo_root)}, errors=(error or "git root unavailable",))
    code, stdout, stderr = _git_run(root, "rev-parse", "HEAD")
    if code != 0:
        return (), SearchReport("JARVIS_SOURCE", SearchState.BLOCKED,
                                inspected={"repository": str(root)}, errors=(stderr.strip() or "HEAD unavailable",))
    head = stdout.strip()
    code, files_output, stderr = _git_run(root, "ls-files", "-z", "--", "src", "config", "pyproject.toml")
    if code != 0:
        return (), SearchReport("JARVIS_SOURCE", SearchState.BLOCKED,
                                inspected={"repository": str(root), "head": head}, errors=(stderr.strip() or "git ls-files failed",))
    terms = _search_terms(requirement)
    paths = [item for item in files_output.split("\x00") if item][:max(1, int(max_files))]
    candidates: list[tuple[int, SolutionCandidate]] = []
    scanned = 0
    errors: list[str] = []
    for relative in paths:
        target = (root / relative).resolve(strict=False)
        try:
            target.relative_to(root)
            if not target.is_file() or target.stat().st_size > MAX_SOURCE_FILE_BYTES:
                continue
            data = target.read_bytes()
            text = data.decode("utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}:{type(exc).__name__}")
            continue
        scanned += 1
        lower = text.lower()
        path_lower = relative.lower()
        matched_terms = [term for term in terms if term in lower or term in path_lower]
        if not matched_terms:
            continue
        lines = [index for index, line in enumerate(text.splitlines(), start=1)
                 if any(term in line.lower() for term in terms)][:12]
        candidate = _source_candidate(requirement, root=root, head=head,
                                      relative=relative, text=text, line_numbers=lines)
        strength = len(set(matched_terms)) + 3 * len(candidate.coverage.matched_actions)
        candidates.append((strength, candidate))
    candidates.sort(key=lambda item: (-item[0], item[1].locator))
    normalized = tuple(item[1] for item in candidates[:24])
    report = SearchReport(
        "JARVIS_SOURCE",
        SearchState.COMPLETED if normalized else SearchState.EMPTY,
        candidate_ids=tuple(item.candidate_id for item in normalized),
        inspected={"repository": str(root), "head": head, "files_considered": len(paths), "files_scanned": scanned, "terms": list(terms), "bounded": True},
        evidence=({"searcher": "repository-source-search", "git_command": "git ls-files + bounded file reads", "authority": SYSTEM_AUTHORITY},),
        errors=tuple(errors[:12]),
    )
    return normalized, report


def _fabric_candidate(
    requirement: CapabilityGapRequirement,
    row: Mapping[str, Any],
    *,
    snapshot_digest: str,
) -> SolutionCandidate:
    spec = dict(row.get("spec") or {})
    readiness = dict(row.get("readiness") or {})
    metadata = dict(row.get("metadata") or {})
    capability_id = str(spec.get("capability_id") or "")
    action = str(spec.get("action_kind") or "")
    required = set(_required_action_set(requirement))
    matched = required & {action, capability_id[4:] if capability_id.startswith("cap.") else capability_id}
    if capability_id in requirement.required_capability_ids:
        matched.update(required)
    state = str(readiness.get("state") or "UNKNOWN")
    unattended = str(spec.get("unattended") or "UNKNOWN")
    ready = state == "READY" and (not requirement.compatibility.unattended or unattended == "ALLOWED")
    compat = {
        "state": "COMPATIBLE" if ready else "INCOMPATIBLE",
        "score": 1.0 if ready else 0.0,
        "satisfied": ["Capability Fabric Catalog contract", "single canonical readiness authority"],
        "violations": [] if ready else [f"FABRIC_READINESS_{state}"],
        "unknowns": [] if ready else (["unattended readiness is not ALLOWED"] if unattended != "ALLOWED" else []),
    }
    maturity_state = "PROVEN" if ready and readiness.get("last_verified_at") else "PRESENT_UNPROVEN"
    maturity_score = 0.95 if maturity_state == "PROVEN" else 0.45
    refs = [f"fabric:{capability_id}", f"fabric-snapshot:{snapshot_digest[7:23]}"]
    raw = {
        "name": f"Fabric capability {capability_id}",
        "solution_kind": "fabric_capability",
        "locator": f"fabric:{capability_id}",
        "version": spec.get("version"),
        "summary": f"Existing Catalog entry for {action or capability_id}; readiness={state}, unattended={unattended}.",
        "coverage": {"matched_actions": sorted(matched), "confidence": 1.0},
        "compatibility": compat,
        "maturity": {"state": maturity_state, "score": maturity_score, "version": spec.get("version"), "tests_present": bool(readiness.get("last_verified_at")), "evidence_refs": refs},
        "maintenance": {"state": "MAINTAINED", "score": 0.9, "last_activity_at": readiness.get("last_verified_at"), "evidence_refs": refs},
        "integration_cost": {"person_hours": 0.5, "adapter_files": 0, "new_dependencies": 0, "configuration_steps": 0, "confidence": 0.95},
        "evidence": [{"ref": refs[0], "capability_id": capability_id, "action_kind": action, "readiness": readiness, "metadata": metadata, "authority": SYSTEM_AUTHORITY}],
        "provenance": {"searcher": "fabric-catalog-search", "fabric_path": FABRIC_PATH, "snapshot_digest": snapshot_digest, "read_only": True},
    }
    return normalize_solution_candidate(requirement, raw, tier=ReuseTier.FABRIC_CATALOG)


def search_fabric_catalog(
    database: FoundryDatabase,
    requirement: CapabilityGapRequirement,
    *,
    max_specs: int = 120,
) -> tuple[tuple[SolutionCandidate, ...], SearchReport]:
    """Search only the canonical Fabric Catalog for an existing capability."""

    snapshot = catalog_snapshot(database)
    rows = list(snapshot.get("capabilities") or ())[:max(1, int(max_specs))]
    digest = _digest(snapshot)
    terms = _search_terms(requirement)
    required = set(_required_action_set(requirement))
    matches: list[tuple[int, SolutionCandidate]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        spec = row.get("spec") or {}
        cap = str(spec.get("capability_id") or "")
        action = str(spec.get("action_kind") or "")
        haystack = " ".join((cap, action, str(spec.get("semantic") or ""), " ".join(map(str, spec.get("limitations") or ()))))
        lower = haystack.lower()
        exact = bool(required & {action, cap[4:] if cap.startswith("cap.") else cap})
        term_hits = sum(term in lower for term in terms)
        if not exact and term_hits == 0:
            continue
        candidate = _fabric_candidate(requirement, row, snapshot_digest=digest)
        matches.append((10 * int(exact) + term_hits, candidate))
    matches.sort(key=lambda item: (-item[0], item[1].locator))
    candidates = tuple(item[1] for item in matches[:24])
    report = SearchReport(
        "FABRIC_CATALOG",
        SearchState.COMPLETED if candidates else SearchState.EMPTY,
        candidate_ids=tuple(item.candidate_id for item in candidates),
        inspected={"catalog_count": int(snapshot.get("count") or len(rows)), "snapshot_digest": digest, "terms": list(terms), "bounded": True},
        evidence=({"searcher": "fabric-catalog-search", "fabric_path": FABRIC_PATH, "authority": "CAPABILITY_FABRIC_CATALOG", "read_only": True},),
    )
    return candidates, report


def _parse_ref_rows(value: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in value.splitlines():
        parts = line.split("\x00")
        if len(parts) < 4:
            continue
        ref, sha, date, subject = (part.strip() for part in parts[:4])
        if re.fullmatch(r"[0-9a-fA-F]{40}", sha) and ref:
            rows.append({"ref": ref[:240], "sha": sha.lower(), "date": date[:80], "subject": subject[:MAX_TEXT]})
    return rows


def _parse_commit_rows(value: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in value.splitlines():
        parts = line.split("\x00")
        if len(parts) < 3:
            continue
        sha, date, subject = (part.strip() for part in parts[:3])
        if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            rows.append({"sha": sha.lower(), "date": date[:80], "subject": subject[:MAX_TEXT]})
    return rows


def _parse_worktrees(value: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in value.splitlines() + [""]:
        if not line:
            if current:
                result.append(current)
                current = {}
            continue
        key, _, item = line.partition(" ")
        if key in {"worktree", "HEAD", "branch", "prunable"}:
            current[key] = item.strip()[:MAX_TEXT]
    return result


def _history_candidate(
    requirement: CapabilityGapRequirement,
    *,
    commit: Mapping[str, Any],
    paths: Sequence[str],
    refs: Sequence[str],
    worktrees: Sequence[str],
    matched_terms: Sequence[str],
) -> SolutionCandidate:
    required = set(_required_action_set(requirement))
    haystack = " ".join([str(commit.get("subject") or ""), *paths, *matched_terms]).lower()
    matched = {action for action in required if action.lower() in haystack}
    partial = {action for action in required if action not in matched and any(part in haystack for part in action.lower().split(".") if len(part) > 3)}
    sha = str(commit.get("sha") or "")
    short_paths = tuple(path.replace("\\", "/")[:240] for path in paths[:16])
    ref_text = tuple(sorted(set(refs)))
    wt_text = tuple(sorted(set(worktrees)))
    locator = f"git:{sha}"
    refs_evidence = [f"git:commit:{sha}", *[f"git:ref:{ref}" for ref in ref_text[:8]]]
    raw = {
        "name": f"Historical JARVIS change {sha[:12]}",
        "solution_kind": "historical_component",
        "locator": locator,
        "version": sha,
        "summary": f"Historical commit: {_text(commit.get('subject') or 'unlabeled commit', 'history.subject', limit=MAX_SUMMARY)}.",
        "coverage": {"matched_actions": sorted(matched), "partial_actions": sorted(partial), "confidence": 0.6},
        "compatibility": {"state": "UNKNOWN", "score": 0.32, "unknowns": ["historical source may target a superseded contract"]},
        "maturity": {"state": "HISTORICAL", "score": 0.48 if ref_text or wt_text else 0.35, "version": sha, "evidence_refs": refs_evidence, "unknowns": ["historical proof is not current runtime proof"]},
        "maintenance": {"state": "HISTORICAL", "score": 0.28, "last_activity_at": commit.get("date"), "evidence_refs": refs_evidence, "unknowns": ["historical branch activity may have ended"]},
        "integration_cost": {"person_hours": 10.0, "adapter_files": 2, "new_dependencies": 0, "configuration_steps": 2, "confidence": 0.58},
        "evidence": [{"ref": f"git:commit:{sha}", "commit": sha, "subject": _text(commit.get("subject"), "history.subject", limit=MAX_EVIDENCE_TEXT), "paths": list(short_paths), "refs": list(ref_text[:8]), "worktrees": list(wt_text[:8]), "matched_terms": list(matched_terms), "authority": SYSTEM_AUTHORITY}],
        "provenance": {"searcher": "git-archaeology", "commit": sha, "refs": list(ref_text[:8]), "worktrees": list(wt_text[:8]), "read_only": True},
    }
    return normalize_solution_candidate(requirement, raw, tier=ReuseTier.JARVIS_GIT)


def _history_file_candidate(
    requirement: CapabilityGapRequirement,
    *,
    head: str,
    path: str,
    line: int,
    text: str,
    refs: Sequence[str],
) -> SolutionCandidate:
    required = set(_required_action_set(requirement))
    lower = text.lower()
    matched = {action for action in required if action.lower() in lower}
    partial = {action for action in required if action not in matched and any(part in lower for part in action.lower().split(".") if len(part) > 3)}
    locator = f"git:{head}:{path}"
    raw = {
        "name": f"Historical JARVIS source: {path}",
        "solution_kind": "historical_source",
        "locator": locator,
        "version": head,
        "summary": f"Bounded git-grep hit in {path} at line {line}; requires semantic port review.",
        "coverage": {"matched_actions": sorted(matched), "partial_actions": sorted(partial), "confidence": 0.55},
        "compatibility": {"state": "UNKNOWN", "score": 0.3, "unknowns": ["historical source is not current registered capability"]},
        "maturity": {"state": "HISTORICAL", "score": 0.4, "version": head, "evidence_refs": [locator]},
        "maintenance": {"state": "HISTORICAL", "score": 0.2, "evidence_refs": [locator]},
        "integration_cost": {"person_hours": 12.0, "adapter_files": 2, "new_dependencies": 0, "configuration_steps": 2, "confidence": 0.45},
        "evidence": [{"ref": locator, "head": head, "path": path, "line": line, "excerpt": _text(text, "history.excerpt", limit=MAX_EVIDENCE_TEXT), "refs": list(refs[:8]), "authority": SYSTEM_AUTHORITY}],
        "provenance": {"searcher": "git-archaeology", "head": head, "path": path, "refs": list(refs[:8]), "read_only": True},
    }
    return normalize_solution_candidate(requirement, raw, tier=ReuseTier.JARVIS_GIT)


def search_git_archaeology(
    requirement: CapabilityGapRequirement,
    repo_root: str | Path,
    *,
    max_refs: int = MAX_HISTORY_REFS,
    max_worktrees: int = MAX_HISTORY_WORKTREES,
    max_commits: int = MAX_HISTORY_COMMITS,
    max_heads: int = MAX_HISTORY_HEADS,
    max_matches: int = MAX_HISTORY_MATCHES,
) -> tuple[tuple[SolutionCandidate, ...], SearchReport]:
    """Perform real, bounded archaeology over refs, worktrees, commits, and files.

    ``operations.reuse`` is intentionally not called: it handles verified
    procedure-template reuse, whereas this search inspects Git's actual
    refs/worktree/commit/file graph.
    """

    root, error = _resolve_git_root(repo_root)
    if root is None:
        return (), SearchReport("JARVIS_GIT", SearchState.BLOCKED,
                                inspected={"repository": str(repo_root), "searcher": "git-archaeology"}, errors=(error or "git root unavailable",))
    code, head_out, head_err = _git_run(root, "rev-parse", "HEAD")
    if code != 0:
        return (), SearchReport("JARVIS_GIT", SearchState.BLOCKED,
                                inspected={"repository": str(root), "searcher": "git-archaeology"}, errors=(head_err.strip() or "HEAD unavailable",))
    head = head_out.strip()
    # ``for-each-ref`` uses the pretty-format ``%00`` escape for a literal
    # NUL.  ``%x00`` is accepted by ``git log`` but is emitted literally by
    # this command, which would make the ref parser report zero refs.
    code, ref_out, ref_err = _git_run(root, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(committerdate:iso8601-strict)%00%(subject)", "refs/heads", "refs/remotes", timeout=25.0)
    refs = _parse_ref_rows(ref_out) if code == 0 else []
    code_wt, wt_out, wt_err = _git_run(root, "worktree", "list", "--porcelain", timeout=25.0)
    worktree_rows = _parse_worktrees(wt_out) if code_wt == 0 else []
    terms = _search_terms(requirement)
    query = " ".join(terms[:4]) or "capability"
    commits: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    code_log, log_out, log_err = _git_run(root, "log", "--all", "--no-merges", "-n", str(max(1, int(max_commits))), "--date=iso-strict", "--format=%H%x00%ad%x00%s", "--regexp-ignore-case", f"--grep={query}", timeout=30.0)
    if code_log == 0:
        for row in _parse_commit_rows(log_out):
            commits[row["sha"]] = {"sha": row["sha"], "date": row["date"], "subject": row["subject"]}
    elif log_err.strip():
        errors.append("git log message search: " + _text(log_err, "git.error", limit=MAX_EVIDENCE_TEXT))
    for term in terms[:4]:
        code_g, out_g, err_g = _git_run(root, "log", "--all", "--no-merges", "-n", "24", "--date=iso-strict", "--format=%H%x00%ad%x00%s", "--regexp-ignore-case", f"-G{re.escape(term)}", "--", "src", "config", "pyproject.toml", timeout=30.0)
        if code_g != 0:
            if err_g.strip():
                errors.append(f"git log -G {term}: " + _text(err_g, "git.error", limit=MAX_EVIDENCE_TEXT))
            continue
        for row in _parse_commit_rows(out_g):
            commits.setdefault(row["sha"], {"sha": row["sha"], "date": row["date"], "subject": row["subject"]})
    commit_rows = list(commits.values())[:max(1, int(max_commits))]
    ref_by_sha: dict[str, list[str]] = {}
    for row in refs[:max(1, int(max_refs))]:
        ref_by_sha.setdefault(row["sha"], []).append(row["ref"])
    wt_by_head: dict[str, list[str]] = {}
    for row in worktree_rows[:max(1, int(max_worktrees))]:
        if row.get("HEAD"):
            wt_by_head.setdefault(str(row["HEAD"]), []).append(str(row.get("worktree") or ""))
    candidates: list[SolutionCandidate] = []
    for commit in commit_rows:
        sha = str(commit["sha"])
        code_p, path_out, _ = _git_run(root, "show", "--format=", "--name-only", sha, timeout=15.0)
        paths = [item.strip() for item in path_out.splitlines() if item.strip()][:24] if code_p == 0 else []
        refs_for = ref_by_sha.get(sha, [])
        worktrees_for = wt_by_head.get(sha, [])
        hay = " ".join([str(commit.get("subject") or ""), *paths]).lower()
        matched_terms = [term for term in terms if term in hay]
        if not matched_terms and not paths:
            continue
        candidates.append(_history_candidate(requirement, commit=commit, paths=paths,
                                             refs=refs_for, worktrees=worktrees_for,
                                             matched_terms=matched_terms))
        if len(candidates) >= max_matches:
            break
    # Inspect actual branch/worktree tips with git-grep.  This is the part
    # that makes the archaeology real even when commit subjects are silent.
    selected_heads: list[str] = [head]
    scored_refs = sorted(refs[:max(1, int(max_refs))], key=lambda row: (
        -sum(term in (row["ref"] + " " + row["subject"]).lower() for term in terms), row["ref"],
    ))
    for row in scored_refs:
        if row["sha"] not in selected_heads:
            selected_heads.append(row["sha"])
        if len(selected_heads) >= max(1, int(max_heads)):
            break
    grepped = 0
    for selected in selected_heads:
        refs_for = [row["ref"] for row in refs if row["sha"] == selected][:8]
        for term in terms[:4]:
            code_x, out_x, err_x = _git_run(root, "grep", "-I", "-n", "--max-count=8", "-e", term, selected, "--", "src", "config", "pyproject.toml", timeout=25.0)
            if code_x not in (0, 1) and err_x.strip():
                errors.append(f"git grep {term}: " + _text(err_x, "git.error", limit=MAX_EVIDENCE_TEXT))
            if code_x != 0:
                continue
            for line in out_x.splitlines()[:8]:
                parts = line.split(":", 3)
                if len(parts) < 4:
                    continue
                _, path, line_number, excerpt = parts
                try:
                    line_no = int(line_number)
                except ValueError:
                    continue
                candidates.append(_history_file_candidate(
                    requirement, head=selected, path=path, line=line_no,
                    text=excerpt, refs=refs_for,
                ))
                grepped += 1
                if grepped >= max_matches:
                    break
            if grepped >= max_matches:
                break
        if grepped >= max_matches:
            break
    unique: dict[str, SolutionCandidate] = {item.candidate_id: item for item in candidates}
    normalized = tuple(unique.values())[:max_matches]
    report = SearchReport(
        "JARVIS_GIT",
        SearchState.COMPLETED if normalized or refs or worktree_rows else SearchState.EMPTY,
        candidate_ids=tuple(item.candidate_id for item in normalized),
        inspected={
            "repository": str(root), "current_head": head,
            "refs_inspected": min(len(refs), max_refs),
            "worktrees_inspected": min(len(worktree_rows), max_worktrees),
            "commits_inspected": len(commit_rows), "heads_grepped": len(selected_heads),
            "file_matches": grepped, "terms": list(terms), "bounded": True,
            "searcher": "git-archaeology",
            "commands": ["git for-each-ref", "git worktree list --porcelain", "git log --all --grep", "git log --all -G", "git show --name-only", "git grep"],
        },
        evidence=({"searcher": "git-archaeology", "operations_reuse_used": False, "authority": SYSTEM_AUTHORITY, "read_only": True},),
        errors=tuple(errors[:16]),
    )
    return normalized, report


def _external_query(requirement: CapabilityGapRequirement) -> str:
    text = " ".join((*_required_action_set(requirement), requirement.description))
    lowered = text.lower()
    if "mcp" in lowered or "model context" in lowered:
        return "Model Context Protocol Python SDK MCP client"
    if "ocr" in lowered or "image text" in lowered:
        return "OCR image text Tesseract mature library"
    if "uia" in lowered or "windows" in lowered and "automation" in lowered:
        return "Windows UI Automation Python SDK"
    terms = _search_terms(requirement)
    return "JARVIS " + " ".join(terms[:5])


def _safe_github_name(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", candidate):
        return None
    return candidate


def _age_days(value: Any) -> float | None:
    if not value:
        return None
    try:
        stamp = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(stamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0)
    except (TypeError, ValueError, OverflowError):
        return None


def _external_candidate(
    requirement: CapabilityGapRequirement,
    item: Mapping[str, Any],
    *,
    detail: Mapping[str, Any] | None,
    endpoint: str,
    attempt: Mapping[str, Any],
    claim_id: str | None,
) -> SolutionCandidate | None:
    full_name = _safe_github_name(item.get("full_name"))
    if full_name is None:
        return None
    merged = dict(item)
    if isinstance(detail, Mapping):
        merged.update({key: value for key, value in detail.items() if value is not None})
    name = _text(merged.get("name") or full_name, "external.name", limit=240)
    description = _text(merged.get("description") or "", "external.description", limit=MAX_SUMMARY)
    raw_topics = merged.get("topics") or ()
    if isinstance(raw_topics, str):
        raw_topics = (raw_topics,)
    topics = tuple(str(value).lower() for value in raw_topics if str(value).strip())
    text = " ".join((full_name, name, description, *topics)).lower()
    required = set(_required_action_set(requirement))
    matched: set[str] = set()
    partial: set[str] = set()
    for action in required:
        parts = [part for part in action.lower().split(".") if len(part) > 2]
        if action.lower() in text or ("mcp" in action.lower() and ("mcp" in text or "model context protocol" in text)):
            matched.add(action)
        elif any(part in text for part in parts):
            partial.add(action)
    language = str(merged.get("language") or "").lower()
    archived = _safe_bool(merged.get("archived", False))
    license_info = merged.get("license")
    license_name = license_info.get("spdx_id") or license_info.get("name") if isinstance(license_info, Mapping) else None
    age = _age_days(merged.get("pushed_at") or merged.get("updated_at"))
    stars = _safe_int(merged.get("stargazers_count") or merged.get("watchers_count"))
    forks = _safe_int(merged.get("forks_count"))
    issues = _safe_int(merged.get("open_issues_count"))
    if archived:
        compatibility = {"state": "INCOMPATIBLE", "score": 0.0, "violations": ["repository is archived"]}
    elif language == "python" and ("python" in requirement.compatibility.runtime.lower()):
        compatibility = {"state": "COMPATIBLE", "score": 0.84, "satisfied": ["Python runtime", "SDK/source integration mode"], "unknowns": ["adapter-specific contract and license approval require a trial"]}
    elif "cli" in requirement.compatibility.integration_modes and ("tesseract" in text or "ocr" in text):
        compatibility = {"state": "PARTIAL", "score": 0.68, "satisfied": ["CLI integration mode"], "unknowns": ["Windows binary packaging and postcondition contract"]}
    else:
        compatibility = {"state": "UNKNOWN", "score": 0.3, "unknowns": ["runtime/integration mode not established from research metadata"]}
    maturity_score = 0.0
    maturity_reasons: list[str] = []
    if stars >= 1000:
        maturity_score += 0.35
        maturity_reasons.append("large observed user signal")
    elif stars >= 100:
        maturity_score += 0.2
        maturity_reasons.append("meaningful observed user signal")
    if forks >= 50:
        maturity_score += 0.15
    if license_name:
        maturity_score += 0.15
    else:
        maturity_reasons.append("license not observed")
    if not archived:
        maturity_score += 0.15
    if age is not None and age < 730:
        maturity_score += 0.2
    elif age is None:
        maturity_reasons.append("activity date not observed")
    maturity_score = min(1.0, maturity_score)
    if maturity_score >= 0.72:
        maturity_state = "PROVEN"
    elif maturity_score >= 0.48:
        maturity_state = "MATURE"
    elif maturity_score > 0:
        maturity_state = "IMMATURE"
    else:
        maturity_state = "UNKNOWN"
    maintenance_score = 0.15 if archived else 0.45
    maintenance_state = "STALE" if archived else "UNKNOWN"
    if age is not None:
        maintenance_score = 0.9 if age < 180 else 0.72 if age < 730 else 0.4
        maintenance_state = "ACTIVE" if age < 180 else "MAINTAINED" if age < 730 else "STALE"
    api_ref = f"https://api.github.com/repos/{full_name}"
    refs = [api_ref]
    if claim_id:
        refs.append(f"claim:{claim_id}")
    classification = classify_content(description)
    solution_kind = "sdk" if "sdk" in text or language == "python" else "cli" if "tesseract" in text else "oss"
    raw = {
        "name": name,
        "solution_kind": solution_kind,
        "locator": f"github:{full_name}",
        "version": str(merged.get("default_branch") or "main"),
        "summary": description or f"GitHub repository {full_name} returned by the bounded mature-solution search.",
        "coverage": {"matched_actions": sorted(matched), "partial_actions": sorted(partial), "confidence": 0.72},
        "compatibility": compatibility,
        "maturity": {"state": maturity_state, "score": maturity_score, "version": str(merged.get("default_branch") or "main"), "stars": stars, "forks": forks, "license": license_name, "archived": archived, "last_release_at": merged.get("pushed_at") or merged.get("updated_at"), "docs_present": bool(merged.get("homepage") or merged.get("html_url")), "tests_present": None, "evidence_refs": refs, "unknowns": maturity_reasons},
        "maintenance": {"state": maintenance_state, "score": maintenance_score, "last_activity_at": merged.get("pushed_at") or merged.get("updated_at"), "issue_health": f"open_issues={issues}", "maintainer_signal": f"forks={forks}", "evidence_refs": refs},
        "integration_cost": {"person_hours": 10.0 if solution_kind == "sdk" else 14.0, "adapter_files": 2, "new_dependencies": 1, "configuration_steps": 3, "confidence": 0.62, "rationale": ["external dependency remains uninstalled; estimate is discovery-only"]},
        "evidence": [{"ref": api_ref, "query_endpoint": endpoint, "api_attempt": dict(attempt), "full_name": full_name, "description": description, "classification": classification, "stars": stars, "forks": forks, "open_issues": issues, "language": language, "license": license_name, "archived": archived, "pushed_at": merged.get("pushed_at"), "authority": EXTERNAL_AUTHORITY, "data_only": True}],
        "provenance": {"searcher": "existing-research-stack.github-research", "query_endpoint": endpoint, "repository_endpoint": api_ref, "authority": EXTERNAL_AUTHORITY, "data_only": True, "read_only": True},
        "data_only": True,
    }
    return normalize_solution_candidate(requirement, raw, tier=ReuseTier.EXTERNAL)


def search_external_solutions(
    database: FoundryDatabase,
    requirement: CapabilityGapRequirement,
    *,
    api_transport: Callable[[str], tuple[int, bytes]] | None = None,
    max_results: int = 5,
    record_claims: bool = True,
) -> tuple[tuple[SolutionCandidate, ...], SearchReport]:
    """Use the existing GitHub research connector for bounded OSS/API/SDK/CLI search."""

    query = _external_query(requirement)
    endpoint = "/search/repositories?" + urllib.parse.urlencode({
        "q": query, "sort": "stars", "order": "desc", "per_page": str(max(1, min(10, int(max_results)))),
    })
    connector = GitHubResearchConnector(api_transport=api_transport)
    try:
        attempt, document = connector.read_api("github.search.read", endpoint)
    except Exception as error:  # connector contract converts transport errors; keep boundary defensive
        report = SearchReport("EXTERNAL", SearchState.BLOCKED, inspected={"query": query, "endpoint": endpoint, "searcher": "existing-research-stack.github-research", "bounded": True}, errors=(_text(str(error), "external.error", limit=MAX_EVIDENCE_TEXT),), authority=EXTERNAL_AUTHORITY)
        return (), report
    attempt_evidence = attempt.to_evidence()
    if attempt.outcome != "COMPLETED" or not isinstance(document, Mapping):
        state = SearchState.BLOCKED if attempt.error_code else SearchState.EMPTY
        return (), SearchReport("EXTERNAL", state, inspected={"query": query, "endpoint": endpoint, "searcher": "existing-research-stack.github-research", "bounded": True}, evidence=({"attempt": attempt_evidence, "authority": EXTERNAL_AUTHORITY, "data_only": True},), errors=((str(attempt.error_code),) if attempt.error_code else ()), authority=EXTERNAL_AUTHORITY)
    items = list(document.get("items") or ())[:max(1, min(10, int(max_results)))]
    candidates: list[SolutionCandidate] = []
    evidence_rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        full_name = _safe_github_name(item.get("full_name"))
        if full_name is None:
            continue
        detail: Mapping[str, Any] | None = None
        detail_attempt: Mapping[str, Any] | None = None
        detail_endpoint = f"/repos/{full_name}"
        try:
            detail_attempt_obj, detail_doc = connector.read_api("github.repo.read", detail_endpoint)
            detail_attempt = detail_attempt_obj.to_evidence()
            if detail_attempt_obj.outcome == "COMPLETED" and isinstance(detail_doc, Mapping):
                detail = detail_doc
        except Exception as error:
            evidence_rows.append({"full_name": full_name, "detail_error": _text(str(error), "external.error", limit=MAX_EVIDENCE_TEXT), "authority": EXTERNAL_AUTHORITY})
        claim_id: str | None = None
        if record_claims:
            try:
                claim = record_research_item(
                    database,
                    mission_id=requirement.gap_id,
                    claim_kind="evidence",
                    statement=f"GitHub research observed repository {full_name} for a capability-gap solution search.",
                    source_type="github_research",
                    location=f"https://api.github.com/repos/{full_name}",
                    retrieved_at=_now(),
                    title=full_name,
                    confidence=0.55,
                )
                claim_id = str(claim.get("claim_id") or "") or None
            except Exception as error:
                evidence_rows.append({"full_name": full_name, "claim_error": _text(str(error), "external.error", limit=MAX_EVIDENCE_TEXT), "authority": EXTERNAL_AUTHORITY})
        candidate = _external_candidate(requirement, item, detail=detail, endpoint=endpoint, attempt=attempt_evidence, claim_id=claim_id)
        if candidate is not None:
            candidates.append(candidate)
            evidence_rows.append({"full_name": full_name, "detail_endpoint": detail_endpoint, "detail_attempt": detail_attempt, "claim_id": claim_id, "authority": EXTERNAL_AUTHORITY, "data_only": True})
    report = SearchReport(
        "EXTERNAL",
        SearchState.COMPLETED if candidates else SearchState.EMPTY,
        candidate_ids=tuple(item.candidate_id for item in candidates),
        inspected={"query": query, "endpoint": endpoint, "items_returned": len(items), "candidates": len(candidates), "searcher": "existing-research-stack.github-research", "bounded": True, "provider_writes": False},
        evidence=tuple([{**attempt_evidence, "authority": EXTERNAL_AUTHORITY, "data_only": True}, *evidence_rows][:MAX_EVIDENCE_ITEMS]),
        authority=EXTERNAL_AUTHORITY,
    )
    return tuple(candidates), report


_TIER_SCORE = {
    ReuseTier.FABRIC_CATALOG: 1.00,
    ReuseTier.JARVIS_SOURCE: 0.84,
    ReuseTier.JARVIS_GIT: 0.66,
    ReuseTier.EXTERNAL: 0.47,
    ReuseTier.CUSTOM_BUILD: 0.10,
}


def _candidate_evaluation(
    candidate: SolutionCandidate,
    requirement: CapabilityGapRequirement,
) -> tuple[bool, tuple[str, ...], dict[str, Any]]:
    reasons: list[str] = []
    coverage = candidate.coverage
    if coverage.missing_actions:
        reasons.extend(f"CAPABILITY_COVERAGE_MISSING:{action}" for action in coverage.missing_actions)
    if coverage.partial_actions:
        reasons.extend(f"CAPABILITY_COVERAGE_PARTIAL:{action}" for action in coverage.partial_actions)
    if candidate.compatibility.state == "INCOMPATIBLE":
        reasons.append("COMPATIBILITY_INCOMPATIBLE")
    elif candidate.compatibility.state == "UNKNOWN":
        reasons.append("COMPATIBILITY_UNPROVEN")
    elif candidate.compatibility.score < 0.55:
        reasons.append("COMPATIBILITY_BELOW_FLOOR")
    if candidate.integration_cost.new_dependencies > requirement.compatibility.max_new_dependencies:
        reasons.append("INTEGRATION_COST_DEPENDENCY_LIMIT")
    if candidate.integration_cost.adapter_files > requirement.compatibility.max_adapter_files:
        reasons.append("INTEGRATION_COST_ADAPTER_LIMIT")
    if candidate.maturity.state == "UNKNOWN" or candidate.maturity.score < 0.30:
        reasons.append("MATURITY_UNPROVEN")
    if candidate.maturity.state == "IMMATURE":
        reasons.append("MATURITY_IMMATURE")
    if candidate.tier is ReuseTier.CUSTOM_BUILD:
        reasons.append("CUSTOM_BUILD_LAST_RESORT")
        reasons.append("NO_TRIAL_AUTHORIZED_IN_DISCOVERY_LANE")
    if candidate.tier is ReuseTier.EXTERNAL and not candidate.data_only:
        reasons.append("EXTERNAL_CONTENT_NOT_DATA_ONLY")
    score = (
        0.27 * _TIER_SCORE[candidate.tier]
        + 0.27 * coverage.score
        + 0.18 * candidate.compatibility.score
        + 0.12 * candidate.maturity.score
        + 0.08 * candidate.maintenance.score
        + 0.06 * candidate.integration_cost.score
        + 0.02 * coverage.confidence
    )
    details = {
        "tier": candidate.tier.label,
        "tier_order": int(candidate.tier),
        "tier_priority": _TIER_SCORE[candidate.tier],
        "coverage": coverage.score,
        "compatibility": candidate.compatibility.score,
        "maturity": candidate.maturity.score,
        "maintenance": candidate.maintenance.score,
        "integration_cost": candidate.integration_cost.score,
        "evidence_confidence": coverage.confidence,
        "composite": round(score, 6),
        "eligible_before_reuse_order": not reasons,
    }
    return not reasons, tuple(sorted(set(reasons))), details


def compare_solution_candidates(
    requirement: CapabilityGapRequirement,
    candidates: Sequence[SolutionCandidate],
) -> tuple[tuple[SolutionCandidate, ...], SolutionComparison, SolutionRecommendation]:
    """Apply hard compatibility/coverage gates, then the frozen tier order."""

    if not candidates:
        comparison_id = "cmp-" + hashlib.sha256(
            canonical_json({"gap_id": requirement.gap_id, "candidates": []}).encode("utf-8")
        ).hexdigest()[:24]
        comparison = SolutionComparison(
            comparison_id=comparison_id, gap_id=requirement.gap_id,
            ranked_candidate_ids=(), winner_candidate_id=None, scores={},
            rejection_reasons={}, decision="DEFER",
        )
        recommendation = SolutionRecommendation(
            recommendation_id="rec-" + comparison_id[4:], gap_id=requirement.gap_id,
            comparison_id=comparison_id, selected_candidate_id=None,
            status="NO_SOLUTION", reason="No solution candidate was observed in the bounded search stages.",
            next_step="WAIT_FOR_NEW_EVIDENCE_OR_OWNER_REDIRECT",
        )
        return (), comparison, recommendation
    evaluations: dict[str, tuple[bool, tuple[str, ...], dict[str, Any]]] = {
        item.candidate_id: _candidate_evaluation(item, requirement) for item in candidates
    }
    eligible = [item for item in candidates if evaluations[item.candidate_id][0]]
    winner: SolutionCandidate | None = None
    if eligible:
        earliest_tier = min(item.tier for item in eligible)
        same_tier = [item for item in eligible if item.tier == earliest_tier]
        winner = max(same_tier, key=lambda item: (
            evaluations[item.candidate_id][2]["composite"],
            item.coverage.score,
            item.maturity.score,
            -item.integration_cost.person_hours,
            item.candidate_id,
        ))
    reasons: dict[str, tuple[str, ...]] = {}
    updated: list[SolutionCandidate] = []
    for item in candidates:
        ok, base_reasons, _details = evaluations[item.candidate_id]
        item_reasons = list(base_reasons)
        disposition = CandidateDisposition.ELIGIBLE if ok else CandidateDisposition.REJECTED
        if winner is not None and item.candidate_id != winner.candidate_id:
            if item.tier > winner.tier:
                item_reasons.append(f"REUSE_ORDER_DEFERRED_TO:{winner.candidate_id}")
                # Later tiers are explicitly parked once an earlier viable
                # solution exists, even when their own evidence is weak.
                disposition = CandidateDisposition.DEFERRED
            elif ok:
                item_reasons.append(f"LOWER_SCORE_WITHIN_TIER:{winner.candidate_id}")
        if winner is not None and item.candidate_id == winner.candidate_id:
            disposition = CandidateDisposition.RECOMMENDED
        item_reasons = sorted(set(item_reasons))
        if item_reasons:
            reasons[item.candidate_id] = tuple(item_reasons)
        updated.append(replace(item, disposition=disposition, rejection_reasons=tuple(item_reasons)))
    score_rows = {
        item.candidate_id: evaluations[item.candidate_id][2] for item in candidates
    }
    ranked = tuple(sorted(
        candidates,
        key=lambda item: (
            int(item.tier),
            0 if evaluations[item.candidate_id][0] else 1,
            -evaluations[item.candidate_id][2]["composite"],
            item.candidate_id,
        ),
    ))
    comparison_id = "cmp-" + hashlib.sha256(canonical_json({
        "gap_id": requirement.gap_id,
        "ranked": [item.candidate_id for item in ranked],
        "scores": score_rows,
        "reasons": reasons,
    }).encode("utf-8")).hexdigest()[:24]
    comparison = SolutionComparison(
        comparison_id=comparison_id,
        gap_id=requirement.gap_id,
        ranked_candidate_ids=tuple(item.candidate_id for item in ranked),
        winner_candidate_id=winner.candidate_id if winner else None,
        scores=score_rows,
        rejection_reasons=reasons,
        decision="RECOMMEND" if winner else "DEFER",
    )
    if winner is not None:
        reason = (
            f"{winner.name} is the highest viable candidate at frozen reuse tier "
            f"{winner.tier.label}; coverage={winner.coverage.score:.2f}, "
            f"compatibility={winner.compatibility.state}, maturity={winner.maturity.state}."
        )
        next_step = "OWNER_AUTHORIZED_TRIAL_AND_INDEPENDENT_VERIFICATION_REQUIRED"
        status = "RECOMMENDED"
        selected = winner.candidate_id
    else:
        reason = "Every observed candidate failed coverage, compatibility, maturity, or lane-authority gates."
        next_step = "WAIT_FOR_NEW_EVIDENCE_OR_OPEN_SEPARATE_EXPLICIT_BUILD_LANE"
        status = "DEFERRED"
        selected = None
    recommendation = SolutionRecommendation(
        recommendation_id="rec-" + comparison_id[4:], gap_id=requirement.gap_id,
        comparison_id=comparison_id, selected_candidate_id=selected,
        status=status, reason=reason, next_step=next_step,
    )
    return tuple(updated), comparison, recommendation


class CapabilitySolutionStore:
    """Durable ledger storage for discovery records and read-only reports."""

    @staticmethod
    def _append(database: FoundryDatabase, event_type: str, payload: Mapping[str, Any], key: str) -> None:
        database.append(Event.create(
            event_type, dict(payload), source_type="supervisor", source_id=SOURCE_ID,
            correlation_id=str(payload.get("gap_id") or payload.get("comparison_id") or key),
            idempotency_key=key,
        ))

    @classmethod
    def record_requirement(cls, database: FoundryDatabase, requirement: CapabilityGapRequirement) -> None:
        payload = requirement.as_dict()
        key = f"{REQUIREMENT_RECORDED}:{requirement.gap_id}"
        try:
            cls._append(database, REQUIREMENT_RECORDED, payload, key)
        except EventConflict:
            # The gap identity is stable, while its bounded description or
            # compatibility context may be refreshed.  Preserve both facts as
            # append-only revisions instead of overwriting the first input.
            cls._append(
                database,
                REQUIREMENT_RECORDED,
                payload,
                f"{key}:revision:{payload_hash(payload)[:24]}",
            )

    @classmethod
    def record_search(cls, database: FoundryDatabase, gap_id: str, report: SearchReport) -> None:
        payload = {"gap_id": gap_id, "report": report.as_dict()}
        digest = payload_hash(payload)
        cls._append(database, SEARCH_COMPLETED, payload, f"{SEARCH_COMPLETED}:{gap_id}:{report.stage}:{digest[:24]}")

    @classmethod
    def record_candidate(cls, database: FoundryDatabase, candidate: SolutionCandidate) -> None:
        payload = candidate.as_dict()
        try:
            cls._append(database, CANDIDATE_RECORDED, payload, f"{CANDIDATE_RECORDED}:{candidate.candidate_id}")
        except EventConflict:
            # A changed external revision is append-only evidence, never an
            # overwrite.  Stable candidate identity remains intact.
            cls._append(database, CANDIDATE_RECORDED, payload, f"{CANDIDATE_RECORDED}:{candidate.candidate_id}:revision:{payload_hash(payload)[:24]}")

    @classmethod
    def record_comparison(cls, database: FoundryDatabase, comparison: SolutionComparison) -> None:
        payload = comparison.as_dict()
        try:
            cls._append(database, COMPARISON_RECORDED, payload, f"{COMPARISON_RECORDED}:{comparison.comparison_id}")
        except EventConflict:
            cls._append(database, COMPARISON_RECORDED, payload, f"{COMPARISON_RECORDED}:{comparison.comparison_id}:revision:{payload_hash(payload)[:24]}")

    @classmethod
    def record_recommendation(cls, database: FoundryDatabase, recommendation: SolutionRecommendation) -> None:
        payload = recommendation.as_dict()
        try:
            cls._append(database, RECOMMENDATION_RECORDED, payload, f"{RECOMMENDATION_RECORDED}:{recommendation.recommendation_id}")
        except EventConflict:
            cls._append(database, RECOMMENDATION_RECORDED, payload, f"{RECOMMENDATION_RECORDED}:{recommendation.recommendation_id}:revision:{payload_hash(payload)[:24]}")

    @staticmethod
    def requirements(database: FoundryDatabase, *, gap_id: str | None = None) -> tuple[CapabilityGapRequirement, ...]:
        rows: dict[str, CapabilityGapRequirement] = {}
        for event in database.events(event_types=(REQUIREMENT_RECORDED,), readonly=True):
            if gap_id and event.payload.get("gap_id") != gap_id:
                continue
            rows[str(event.payload.get("gap_id"))] = CapabilityGapRequirement.from_mapping(event.payload)
        return tuple(rows[key] for key in sorted(rows))

    @staticmethod
    def candidates(database: FoundryDatabase, *, gap_id: str | None = None) -> tuple[SolutionCandidate, ...]:
        rows: dict[str, SolutionCandidate] = {}
        for event in database.events(event_types=(CANDIDATE_RECORDED,), readonly=True):
            payload = event.payload
            if gap_id and payload.get("gap_id") != gap_id:
                continue
            try:
                tier = ReuseTier(int(payload.get("tier_order")))
                coverage = CapabilityCoverage(**dict(payload.get("coverage") or {}))
                compatibility = CompatibilityAssessment(**dict(payload.get("compatibility") or {}))
                # ``as_dict`` exposes the derived cost score for evidence, but
                # it is a property rather than a constructor field.  Strip it
                # when replaying the durable event so persisted candidates can
                # be reconstructed instead of silently discarded.
                cost_payload = dict(payload.get("integration_cost") or {})
                cost_payload.pop("score", None)
                cost = IntegrationCostEstimate(**cost_payload)
                maturity = MaturityEvidence(**dict(payload.get("maturity") or {}))
                maintenance = MaintenanceEvidence(**dict(payload.get("maintenance") or {}))
                item = SolutionCandidate(
                    candidate_id=payload["candidate_id"], gap_id=payload["gap_id"], tier=tier,
                    name=payload["name"], solution_kind=payload["solution_kind"], locator=payload["locator"],
                    version=payload.get("version"), summary=payload.get("summary") or "",
                    coverage=coverage, compatibility=compatibility, integration_cost=cost,
                    maturity=maturity, maintenance=maintenance,
                    evidence=tuple(payload.get("evidence") or ()), provenance=payload.get("provenance") or {},
                    authority=payload.get("authority") or SYSTEM_AUTHORITY, data_only=bool(payload.get("data_only")),
                    disposition=CandidateDisposition(payload.get("disposition") or "UNREVIEWED"),
                    rejection_reasons=tuple(payload.get("rejection_reasons") or ()),
                )
            except (KeyError, TypeError, ValueError, DiscoveryError):
                continue
            rows[item.candidate_id] = item
        return tuple(rows[key] for key in sorted(rows))

    @staticmethod
    def searches(database: FoundryDatabase, *, gap_id: str | None = None) -> tuple[dict[str, Any], ...]:
        return tuple(
            event.payload for event in database.events(event_types=(SEARCH_COMPLETED,), readonly=True)
            if gap_id is None or event.payload.get("gap_id") == gap_id
        )


def _custom_candidate(requirement: CapabilityGapRequirement) -> SolutionCandidate:
    raw = {
        "name": "Custom implementation (last resort placeholder)",
        "solution_kind": "custom_build",
        "locator": f"custom:unimplemented:{requirement.gap_id}",
        "version": None,
        "summary": "No custom implementation is created in the discovery lane; this is an explicit last-resort comparison row.",
        "coverage": {"matched_actions": list(_required_action_set(requirement)), "confidence": 0.1},
        "compatibility": {"state": "PARTIAL", "score": 0.55, "unknowns": ["implementation does not exist"]},
        "maturity": {"state": "UNKNOWN", "score": 0.0, "unknowns": ["no implementation evidence"]},
        "maintenance": {"state": "UNKNOWN", "score": 0.0, "unknowns": ["owner-owned maintenance burden"]},
        "integration_cost": {"person_hours": 32.0, "adapter_files": 4, "new_dependencies": 0, "configuration_steps": 4, "confidence": 0.1},
        "evidence": [{"ref": "custom-build-placeholder", "authority": SYSTEM_AUTHORITY}],
        "provenance": {"searcher": "custom-last-resort-placeholder", "read_only": True},
    }
    return normalize_solution_candidate(requirement, raw, tier=ReuseTier.CUSTOM_BUILD)


class CapabilitySolutionDiscovery:
    """Orchestrate one bounded, durable, reuse-first discovery run."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        repo_root: str | Path | None = None,
        api_transport: Callable[[str], tuple[int, bytes]] | None = None,
    ) -> None:
        self.database = database
        self.repo_root = Path(repo_root or Path.cwd()).resolve(strict=False)
        self.api_transport = api_transport

    def discover(
        self,
        requirement: CapabilityGapRequirement,
        *,
        search_all_tiers: bool = False,
        include_custom: bool = True,
        record_claims: bool = True,
    ) -> DiscoveryRun:
        if not isinstance(requirement, CapabilityGapRequirement):
            raise DiscoveryError("requirement must be a CapabilityGapRequirement")
        CapabilitySolutionStore.record_requirement(self.database, requirement)
        all_candidates: list[SolutionCandidate] = []
        reports: list[SearchReport] = []
        fabric_candidates, fabric_report = search_fabric_catalog(self.database, requirement)
        all_candidates.extend(fabric_candidates)
        reports.append(fabric_report)
        CapabilitySolutionStore.record_search(self.database, requirement.gap_id, fabric_report)
        source_candidates, source_report = search_repository_source(requirement, self.repo_root)
        all_candidates.extend(source_candidates)
        reports.append(source_report)
        CapabilitySolutionStore.record_search(self.database, requirement.gap_id, source_report)
        history_candidates, history_report = search_git_archaeology(requirement, self.repo_root)
        all_candidates.extend(history_candidates)
        reports.append(history_report)
        CapabilitySolutionStore.record_search(self.database, requirement.gap_id, history_report)
        current_viable = any(
            _candidate_evaluation(item, requirement)[0]
            for item in all_candidates
        )
        if current_viable and not search_all_tiers:
            external_candidates: tuple[SolutionCandidate, ...] = ()
            external_report = SearchReport(
                "EXTERNAL", SearchState.SKIPPED,
                inspected={"reason": "frozen reuse order satisfied by an earlier viable tier", "bounded": True},
                evidence=({"searcher": "existing-research-stack.github-research", "skipped": True, "authority": EXTERNAL_AUTHORITY, "data_only": True},),
                authority=EXTERNAL_AUTHORITY,
            )
        else:
            external_candidates, external_report = search_external_solutions(
                self.database, requirement, api_transport=self.api_transport,
                record_claims=record_claims,
            )
        all_candidates.extend(external_candidates)
        reports.append(external_report)
        CapabilitySolutionStore.record_search(self.database, requirement.gap_id, external_report)
        if include_custom:
            all_candidates.append(_custom_candidate(requirement))
        unique = {item.candidate_id: item for item in all_candidates}
        normalized, comparison, recommendation = compare_solution_candidates(requirement, tuple(unique.values()))
        for item in normalized:
            CapabilitySolutionStore.record_candidate(self.database, item)
        CapabilitySolutionStore.record_comparison(self.database, comparison)
        CapabilitySolutionStore.record_recommendation(self.database, recommendation)
        return DiscoveryRun(
            requirement=requirement,
            candidates=normalized,
            comparison=comparison,
            recommendation=recommendation,
            search_reports=tuple(reports),
        )


__all__ = [
    "CANDIDATE_RECORDED",
    "COMPARISON_RECORDED",
    "CapabilityCoverage",
    "CapabilityGapRequirement",
    "CapabilitySolutionDiscovery",
    "CapabilitySolutionStore",
    "CompatibilityAssessment",
    "CompatibilityRequirements",
    "COMPARISON_CONTRACT_VERSION",
    "CONTRACT_VERSION",
    "DiscoveryError",
    "DiscoveryRun",
    "GAP_CONTRACT_VERSION",
    "IntegrationCostEstimate",
    "MaintenanceEvidence",
    "MaturityEvidence",
    "RECOMMENDATION_RECORDED",
    "ReuseTier",
    "SEARCH_COMPLETED",
    "SEARCH_CONTRACT_VERSION",
    "SearchReport",
    "SearchState",
    "SolutionCandidate",
    "SolutionComparison",
    "SolutionRecommendation",
    "compare_solution_candidates",
    "normalize_solution_candidate",
    "search_external_solutions",
    "search_fabric_catalog",
    "search_git_archaeology",
    "search_repository_source",
]
