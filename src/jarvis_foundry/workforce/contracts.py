"""Typed contracts for bounded AI workforce management.

The workforce layer is subordinate to the existing Executive, MissionLoop,
provider, effect, and verifier authorities. It records work that those
authorities requested and the bounded worker sessions used to carry it out.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..clock import isoformat
from ..events import canonical_json

WORKFORCE_SCHEMA = "v13.ai-workforce.v1"

PACKAGE_STATUSES = frozenset({
    "PLANNED",
    "READY",
    "RUNNING",
    "WAITING",
    "VERIFYING",
    "ACCEPTED",
    "REJECTED",
    "FAILED",
    "CANCELLED",
    "STOPPED",
})
TERMINAL_PACKAGE_STATUSES = frozenset({
    "ACCEPTED",
    "REJECTED",
    "CANCELLED",
})
SESSION_STATUSES = frozenset({
    "STARTING",
    "RUNNING",
    "HEALTHY",
    "STALLED",
    "BLOCKED",
    "FAILED",
    "COMPLETED",
    "TERMINATED",
    "STOPPED",
})
TERMINAL_SESSION_STATUSES = frozenset({
    "BLOCKED",
    "FAILED",
    "COMPLETED",
    "TERMINATED",
    "STOPPED",
})
OBSERVATION_KINDS = frozenset({
    "HEALTHY_PROGRESS",
    "NO_PROGRESS_STALL",
    "PROVIDER_OUTAGE",
    "WRONG_REPOSITORY",
    "WRONG_BRANCH",
    "SCOPE_ESCAPE",
    "GOAL_DRIFT",
    "ENDLESS_ANALYSIS",
    "MANDATORY_TESTS_SKIPPED",
    "FALSE_COMPLETION",
    "SESSION_DEAD",
    "PARTIAL_MUTATION",
    "REPEATED_FAILURE",
    "EXTERNAL_BLOCKER",
})
INTERVENTION_ACTIONS = frozenset({
    "CONTINUE",
    "REDIRECT",
    "REPAIR",
    "RESTART",
    "REASSIGN",
    "TERMINATE",
    "REDUCE_SCOPE",
    "ESCALATE",
    "WAIT",
})
CONVERGENCE_CLASSIFICATIONS = frozenset({
    "ALREADY_REPRESENTED",
    "UNIQUE_ACCEPTED_DELTA",
    "FULLY_SUPERSEDED",
    "PARTIALLY_ABSORBED",
    "CONFLICTING",
    "FAILED_REJECTED",
    "EVIDENCE_ONLY",
    "INCOMPLETE",
})

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_RISK_CLASSES = frozenset({"C0", "C1", "C2", "C3"})


class WorkforceContractError(ValueError):
    """A workforce contract is incomplete or internally inconsistent."""


def _required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WorkforceContractError(f"{name} must be non-empty")
    return text


def _strings(values: object, name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = (values,)
    try:
        result = tuple(_required(item, name) for item in values)
    except TypeError as error:
        raise WorkforceContractError(f"{name} must be a sequence") from error
    if len(result) != len(set(result)):
        raise WorkforceContractError(f"{name} must not contain duplicates")
    return result


def _mapping(value: object, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise WorkforceContractError(f"{name} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _optional_budget(value: object, name: str) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise WorkforceContractError(f"{name} must be finite and non-negative")
    return result


@dataclass(frozen=True, slots=True)
class WorkPackage:
    """Immutable bounded work requested by the existing Executive."""

    package_id: str
    parent_objective_id: str
    role: str
    capability: str
    bounded_goal: str
    acceptance_criteria: tuple[str, ...]
    source_sha: str
    repository_id: str
    branch_name: str
    worktree_path: str
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...] = ()
    campaign_id: str | None = None
    mission_id: str | None = None
    step_id: str | None = None
    dependencies: tuple[str, ...] = ()
    mission_step_contract_id: str | None = None
    mission_step_contract_hash: str | None = None
    context_manifest_id: str | None = None
    context_manifest_hash: str | None = None
    expected_artifacts: tuple[str, ...] = ()
    required_tests: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    risk_class: str = "C1"
    duration_seconds: int = 900
    token_budget: int | None = None
    cost_budget_usd: float | None = None
    deadline: str | None = None
    lease_seconds: int = 1200
    result_schema: str = "worker-result.v1"
    integration_target: str = ""
    owner_constraints: tuple[str, ...] = ()
    owner_veto_snapshot: dict[str, Any] = field(default_factory=dict)
    routing_identity: str | None = None
    effect_action_key: str | None = None
    lineage: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=isoformat)

    def __post_init__(self) -> None:
        for name in (
            "package_id",
            "parent_objective_id",
            "role",
            "capability",
            "bounded_goal",
            "repository_id",
            "branch_name",
            "worktree_path",
            "result_schema",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        source_sha = _required(self.source_sha, "source_sha").lower()
        if not _SHA_PATTERN.fullmatch(source_sha):
            raise WorkforceContractError("source_sha must be a full 40-character Git SHA")
        object.__setattr__(self, "source_sha", source_sha)
        for name in (
            "acceptance_criteria",
            "allowed_paths",
            "forbidden_paths",
            "dependencies",
            "expected_artifacts",
            "required_tests",
            "evidence_requirements",
            "owner_constraints",
        ):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        if not self.acceptance_criteria:
            raise WorkforceContractError("acceptance_criteria must not be empty")
        if not self.allowed_paths:
            raise WorkforceContractError("allowed_paths must not be empty")
        if self.package_id in self.dependencies:
            raise WorkforceContractError("a work package cannot depend on itself")
        if set(self.allowed_paths) & set(self.forbidden_paths):
            raise WorkforceContractError("allowed_paths and forbidden_paths must not overlap")
        if self.risk_class not in _RISK_CLASSES:
            raise WorkforceContractError(
                f"risk_class must be one of {sorted(_RISK_CLASSES)}"
            )
        if int(self.duration_seconds) <= 0:
            raise WorkforceContractError("duration_seconds must be positive")
        if int(self.lease_seconds) <= 0:
            raise WorkforceContractError("lease_seconds must be positive")
        object.__setattr__(self, "duration_seconds", int(self.duration_seconds))
        object.__setattr__(self, "lease_seconds", int(self.lease_seconds))
        if self.token_budget is not None:
            if int(self.token_budget) < 0:
                raise WorkforceContractError("token_budget must be non-negative")
            object.__setattr__(self, "token_budget", int(self.token_budget))
        object.__setattr__(
            self,
            "cost_budget_usd",
            _optional_budget(self.cost_budget_usd, "cost_budget_usd"),
        )
        object.__setattr__(
            self,
            "owner_veto_snapshot",
            _mapping(self.owner_veto_snapshot, "owner_veto_snapshot"),
        )
        object.__setattr__(self, "lineage", _mapping(self.lineage, "lineage"))
        for name in (
            "campaign_id",
            "mission_id",
            "step_id",
            "mission_step_contract_id",
            "mission_step_contract_hash",
            "context_manifest_id",
            "context_manifest_hash",
            "routing_identity",
            "effect_action_key",
            "deadline",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required(value, name))
        if bool(self.mission_step_contract_id) != bool(self.mission_step_contract_hash):
            raise WorkforceContractError(
                "mission step contract id and hash must be supplied together"
            )
        if self.mission_step_contract_hash is not None and (
            len(self.mission_step_contract_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.mission_step_contract_hash.lower()
            )
        ):
            raise WorkforceContractError(
                "mission_step_contract_hash must be a sha256 hex digest"
            )
        if self.mission_id is not None and self.step_id is None:
            raise WorkforceContractError(
                "mission-bound work packages require step_id"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKFORCE_SCHEMA,
            "package_id": self.package_id,
            "parent_objective_id": self.parent_objective_id,
            "campaign_id": self.campaign_id,
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "role": self.role,
            "capability": self.capability,
            "bounded_goal": self.bounded_goal,
            "acceptance_criteria": list(self.acceptance_criteria),
            "source_sha": self.source_sha,
            "repository_id": self.repository_id,
            "branch_name": self.branch_name,
            "worktree_path": self.worktree_path,
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "dependencies": list(self.dependencies),
            "mission_step_contract_id": self.mission_step_contract_id,
            "mission_step_contract_hash": self.mission_step_contract_hash,
            "context_manifest_id": self.context_manifest_id,
            "context_manifest_hash": self.context_manifest_hash,
            "expected_artifacts": list(self.expected_artifacts),
            "required_tests": list(self.required_tests),
            "evidence_requirements": list(self.evidence_requirements),
            "risk_class": self.risk_class,
            "duration_seconds": self.duration_seconds,
            "token_budget": self.token_budget,
            "cost_budget_usd": self.cost_budget_usd,
            "deadline": self.deadline,
            "lease_seconds": self.lease_seconds,
            "result_schema": self.result_schema,
            "integration_target": self.integration_target,
            "owner_constraints": list(self.owner_constraints),
            "owner_veto_snapshot": self.owner_veto_snapshot,
            "routing_identity": self.routing_identity,
            "effect_action_key": self.effect_action_key,
            "lineage": self.lineage,
            "created_at": self.created_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WorkPackage":
        return cls(
            package_id=payload["package_id"],
            parent_objective_id=payload["parent_objective_id"],
            campaign_id=payload.get("campaign_id"),
            mission_id=payload.get("mission_id"),
            step_id=payload.get("step_id"),
            role=payload["role"],
            capability=payload["capability"],
            bounded_goal=payload["bounded_goal"],
            acceptance_criteria=tuple(payload.get("acceptance_criteria") or ()),
            source_sha=payload["source_sha"],
            repository_id=payload["repository_id"],
            branch_name=payload["branch_name"],
            worktree_path=payload["worktree_path"],
            allowed_paths=tuple(payload.get("allowed_paths") or ()),
            forbidden_paths=tuple(payload.get("forbidden_paths") or ()),
            dependencies=tuple(payload.get("dependencies") or ()),
            mission_step_contract_id=payload.get("mission_step_contract_id"),
            mission_step_contract_hash=payload.get("mission_step_contract_hash"),
            context_manifest_id=payload.get("context_manifest_id"),
            context_manifest_hash=payload.get("context_manifest_hash"),
            expected_artifacts=tuple(payload.get("expected_artifacts") or ()),
            required_tests=tuple(payload.get("required_tests") or ()),
            evidence_requirements=tuple(payload.get("evidence_requirements") or ()),
            risk_class=payload.get("risk_class", "C1"),
            duration_seconds=int(payload.get("duration_seconds", 900)),
            token_budget=payload.get("token_budget"),
            cost_budget_usd=payload.get("cost_budget_usd"),
            deadline=payload.get("deadline"),
            lease_seconds=int(payload.get("lease_seconds", 1200)),
            result_schema=payload.get("result_schema", "worker-result.v1"),
            integration_target=payload.get("integration_target", ""),
            owner_constraints=tuple(payload.get("owner_constraints") or ()),
            owner_veto_snapshot=dict(payload.get("owner_veto_snapshot") or {}),
            routing_identity=payload.get("routing_identity"),
            effect_action_key=payload.get("effect_action_key"),
            lineage=dict(payload.get("lineage") or {}),
            created_at=payload.get("created_at", isoformat()),
        )

    def content_hash(self) -> str:
        payload = self.to_payload()
        payload.pop("created_at", None)
        return hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkerSession:
    """A workforce binding to the canonical ``agent_run`` lifecycle."""

    session_id: str
    package_id: str
    run_id: str
    attempt: int
    framework: str
    provider: str
    model: str
    status: str = "STARTING"
    started_at: str = field(default_factory=isoformat)
    heartbeat_at: str | None = None
    finished_at: str | None = None
    progress_fingerprint: str | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("session_id", "package_id", "run_id", "framework", "provider", "model"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if int(self.attempt) < 1:
            raise WorkforceContractError("session attempt must be positive")
        object.__setattr__(self, "attempt", int(self.attempt))
        if self.status not in SESSION_STATUSES:
            raise WorkforceContractError(f"unsupported session status: {self.status}")
        object.__setattr__(self, "result", _mapping(self.result, "result"))

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKFORCE_SCHEMA,
            "session_id": self.session_id,
            "package_id": self.package_id,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "framework": self.framework,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "finished_at": self.finished_at,
            "progress_fingerprint": self.progress_fingerprint,
            "result": self.result,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WorkerSession":
        return cls(
            session_id=payload["session_id"],
            package_id=payload["package_id"],
            run_id=payload["run_id"],
            attempt=int(payload.get("attempt", 1)),
            framework=payload["framework"],
            provider=payload["provider"],
            model=payload["model"],
            status=payload.get("status", "STARTING"),
            started_at=payload.get("started_at", isoformat()),
            heartbeat_at=payload.get("heartbeat_at"),
            finished_at=payload.get("finished_at"),
            progress_fingerprint=payload.get("progress_fingerprint"),
            result=dict(payload.get("result") or {}),
        )


@dataclass(frozen=True, slots=True)
class ResourceClaim:
    claim_id: str
    package_id: str
    resource_id: str
    worktree_path: str
    mutable_paths: tuple[str, ...]
    lease_expires_at: str
    session_id: str | None = None
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        for name in (
            "claim_id",
            "package_id",
            "resource_id",
            "worktree_path",
            "lease_expires_at",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(
            self, "mutable_paths", _strings(self.mutable_paths, "mutable_paths")
        )
        if not self.mutable_paths:
            raise WorkforceContractError("mutable_paths must not be empty")
        if self.status not in {"ACTIVE", "RELEASED", "EXPIRED"}:
            raise WorkforceContractError(f"unsupported resource claim status: {self.status}")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKFORCE_SCHEMA,
            "claim_id": self.claim_id,
            "package_id": self.package_id,
            "session_id": self.session_id,
            "resource_id": self.resource_id,
            "worktree_path": self.worktree_path,
            "mutable_paths": list(self.mutable_paths),
            "lease_expires_at": self.lease_expires_at,
            "status": self.status,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ResourceClaim":
        return cls(
            claim_id=payload["claim_id"],
            package_id=payload["package_id"],
            session_id=payload.get("session_id"),
            resource_id=payload["resource_id"],
            worktree_path=payload["worktree_path"],
            mutable_paths=tuple(payload.get("mutable_paths") or ()),
            lease_expires_at=payload["lease_expires_at"],
            status=payload.get("status", "ACTIVE"),
        )


@dataclass(frozen=True, slots=True)
class WorkerObservation:
    observation_id: str
    package_id: str
    session_id: str
    kind: str
    evidence: tuple[str, ...]
    progress_fingerprint: str | None = None
    observed_at: str = field(default_factory=isoformat)

    def __post_init__(self) -> None:
        for name in ("observation_id", "package_id", "session_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.kind not in OBSERVATION_KINDS:
            raise WorkforceContractError(f"unsupported observation kind: {self.kind}")
        object.__setattr__(self, "evidence", _strings(self.evidence, "evidence"))
        if not self.evidence:
            raise WorkforceContractError("observation evidence must not be empty")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKFORCE_SCHEMA,
            "observation_id": self.observation_id,
            "package_id": self.package_id,
            "session_id": self.session_id,
            "kind": self.kind,
            "evidence": list(self.evidence),
            "progress_fingerprint": self.progress_fingerprint,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WorkerObservation":
        return cls(
            observation_id=payload["observation_id"],
            package_id=payload["package_id"],
            session_id=payload["session_id"],
            kind=payload["kind"],
            evidence=tuple(payload.get("evidence") or ()),
            progress_fingerprint=payload.get("progress_fingerprint"),
            observed_at=payload.get("observed_at", isoformat()),
        )


@dataclass(frozen=True, slots=True)
class InterventionRecord:
    intervention_id: str
    package_id: str
    session_id: str
    action: str
    reason: str
    observation_id: str | None = None
    instruction: str = ""
    replacement_framework: str | None = None
    replacement_session_id: str | None = None
    before_progress_fingerprint: str | None = None
    after_progress_fingerprint: str | None = None
    improved: bool | None = None
    outcome: str = "REQUESTED"
    created_at: str = field(default_factory=isoformat)

    def __post_init__(self) -> None:
        for name in ("intervention_id", "package_id", "session_id", "reason"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.action not in INTERVENTION_ACTIONS:
            raise WorkforceContractError(f"unsupported intervention action: {self.action}")
        for name in (
            "observation_id",
            "replacement_framework",
            "replacement_session_id",
            "before_progress_fingerprint",
            "after_progress_fingerprint",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required(value, name))
        if self.improved is not None and not isinstance(self.improved, bool):
            raise WorkforceContractError("intervention improved must be boolean or null")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKFORCE_SCHEMA,
            "intervention_id": self.intervention_id,
            "package_id": self.package_id,
            "session_id": self.session_id,
            "action": self.action,
            "reason": self.reason,
            "observation_id": self.observation_id,
            "instruction": self.instruction,
            "replacement_framework": self.replacement_framework,
            "replacement_session_id": self.replacement_session_id,
            "before_progress_fingerprint": self.before_progress_fingerprint,
            "after_progress_fingerprint": self.after_progress_fingerprint,
            "improved": self.improved,
            "outcome": self.outcome,
            "created_at": self.created_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "InterventionRecord":
        return cls(
            intervention_id=payload["intervention_id"],
            package_id=payload["package_id"],
            session_id=payload["session_id"],
            action=payload["action"],
            reason=payload["reason"],
            observation_id=payload.get("observation_id"),
            instruction=payload.get("instruction", ""),
            replacement_framework=payload.get("replacement_framework"),
            replacement_session_id=payload.get("replacement_session_id"),
            before_progress_fingerprint=payload.get(
                "before_progress_fingerprint"
            ),
            after_progress_fingerprint=payload.get(
                "after_progress_fingerprint"
            ),
            improved=payload.get("improved"),
            outcome=payload.get("outcome", "REQUESTED"),
            created_at=payload.get("created_at", isoformat()),
        )


@dataclass(frozen=True, slots=True)
class SuitabilitySample:
    sample_id: str
    package_id: str
    session_id: str
    role: str
    framework: str
    provider: str
    model: str
    domain: str
    success: bool
    verifier_rejected: bool
    repaired: bool
    latency_seconds: float | None = None
    cost_usd: float | None = None
    provider_failed: bool = False
    drifted: bool = False
    stalled: bool = False
    scope_violated: bool = False
    artifact_quality: float | None = None
    cancellation_recovered: bool | None = None
    recorded_at: str = field(default_factory=isoformat)

    def __post_init__(self) -> None:
        for name in (
            "sample_id",
            "package_id",
            "session_id",
            "role",
            "framework",
            "provider",
            "model",
            "domain",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in ("latency_seconds", "cost_usd", "artifact_quality"):
            value = getattr(self, name)
            if value is None:
                continue
            result = float(value)
            if not math.isfinite(result) or result < 0:
                raise WorkforceContractError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, result)
        if self.artifact_quality is not None and self.artifact_quality > 1:
            raise WorkforceContractError("artifact_quality must be within [0, 1]")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKFORCE_SCHEMA,
            "sample_id": self.sample_id,
            "package_id": self.package_id,
            "session_id": self.session_id,
            "role": self.role,
            "framework": self.framework,
            "provider": self.provider,
            "model": self.model,
            "domain": self.domain,
            "success": self.success,
            "verifier_rejected": self.verifier_rejected,
            "repaired": self.repaired,
            "latency_seconds": self.latency_seconds,
            "cost_usd": self.cost_usd,
            "provider_failed": self.provider_failed,
            "drifted": self.drifted,
            "stalled": self.stalled,
            "scope_violated": self.scope_violated,
            "artifact_quality": self.artifact_quality,
            "cancellation_recovered": self.cancellation_recovered,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SuitabilitySample":
        return cls(
            sample_id=payload["sample_id"],
            package_id=payload["package_id"],
            session_id=payload["session_id"],
            role=payload["role"],
            framework=payload["framework"],
            provider=payload["provider"],
            model=payload["model"],
            domain=payload["domain"],
            success=bool(payload.get("success")),
            verifier_rejected=bool(payload.get("verifier_rejected")),
            repaired=bool(payload.get("repaired")),
            latency_seconds=payload.get("latency_seconds"),
            cost_usd=payload.get("cost_usd"),
            provider_failed=bool(payload.get("provider_failed")),
            drifted=bool(payload.get("drifted")),
            stalled=bool(payload.get("stalled")),
            scope_violated=bool(payload.get("scope_violated")),
            artifact_quality=payload.get("artifact_quality"),
            cancellation_recovered=payload.get("cancellation_recovered"),
            recorded_at=payload.get("recorded_at", isoformat()),
        )


@dataclass(frozen=True, slots=True)
class ConvergenceDecision:
    decision_id: str
    integration_target: str
    classifications: dict[str, str]
    accepted_packages: tuple[str, ...]
    conflicts: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    recommendation: str = ""
    created_at: str = field(default_factory=isoformat)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _required(self.decision_id, "decision_id"))
        object.__setattr__(
            self,
            "integration_target",
            _required(self.integration_target, "integration_target"),
        )
        classifications = {
            _required(package_id, "classification package_id"): _required(
                classification, "classification"
            )
            for package_id, classification in _mapping(
                self.classifications, "classifications"
            ).items()
        }
        invalid = set(classifications.values()) - CONVERGENCE_CLASSIFICATIONS
        if invalid:
            raise WorkforceContractError(
                f"unsupported convergence classifications: {sorted(invalid)}"
            )
        object.__setattr__(self, "classifications", classifications)
        object.__setattr__(
            self,
            "accepted_packages",
            _strings(self.accepted_packages, "accepted_packages"),
        )
        object.__setattr__(self, "conflicts", _strings(self.conflicts, "conflicts"))
        object.__setattr__(self, "evidence", _strings(self.evidence, "evidence"))
        if not classifications:
            raise WorkforceContractError("classifications must not be empty")
        unknown = set(self.accepted_packages) - set(classifications)
        if unknown:
            raise WorkforceContractError(
                f"accepted packages lack classifications: {sorted(unknown)}"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": WORKFORCE_SCHEMA,
            "decision_id": self.decision_id,
            "integration_target": self.integration_target,
            "classifications": self.classifications,
            "accepted_packages": list(self.accepted_packages),
            "conflicts": list(self.conflicts),
            "evidence": list(self.evidence),
            "recommendation": self.recommendation,
            "created_at": self.created_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ConvergenceDecision":
        return cls(
            decision_id=payload["decision_id"],
            integration_target=payload["integration_target"],
            classifications=dict(payload.get("classifications") or {}),
            accepted_packages=tuple(payload.get("accepted_packages") or ()),
            conflicts=tuple(payload.get("conflicts") or ()),
            evidence=tuple(payload.get("evidence") or ()),
            recommendation=payload.get("recommendation", ""),
            created_at=payload.get("created_at", isoformat()),
        )
