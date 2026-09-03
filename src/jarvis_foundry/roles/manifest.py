"""Context manifests for isolated role sessions (WP4).

A ContextManifest captures the EXACT context a role session must receive:
role identity, mission/step/resource binding, capability, provider and model,
isolation group, tool policy, and budget/duration bounds. The manifest is
canonical and content-addressed (``content_hash``), so the durable
``agent_runs.context_manifest_hash`` column can be recomputed and compared
at any time — the recorded hash proves the session received exactly the
context that was planned.

Manifests never carry raw credentials (policy: capabilities, not secrets).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

from ..events import canonical_json


@dataclass(frozen=True, slots=True)
class ContextManifest:
    run_id: str
    role: str
    mission_id: str
    step_id: str | None
    resource_id: str
    capability: str
    provider: str
    model: str
    isolation_group: str
    tool_policy: tuple[str, ...] = field(default_factory=tuple)
    budget_usd: float = 0.0
    duration_seconds: int = 300
    instructions: str = ""
    # Hybrid worker contract enrichment: durable mission truth carried into
    # the worker contract (acceptance criteria, expected artifacts, scope,
    # attempt). Optional fields are omitted from the payload when empty so
    # existing recorded manifest hashes stay byte-identical (replay
    # semantics unchanged for historical rows).
    expected_artifacts: tuple[str, ...] = field(default_factory=tuple)
    verification_criteria: tuple[str, ...] = field(default_factory=tuple)
    forbidden_paths: tuple[str, ...] = field(default_factory=tuple)
    attempt: int = 1
    # V12 campaign lineage.  These fields are optional for historical and
    # non-campaign runs, but a campaign child supplies the complete set.  The
    # routing contract and effect key are hashed with the worker context so a
    # fallback cannot silently shed identity or capability requirements.
    objective_id: str | None = None
    campaign_id: str | None = None
    child_id: str | None = None
    campaign_isolation_key: str | None = None
    routing_requirements: dict[str, Any] = field(default_factory=dict)
    routing_identity: str | None = None
    effect_action_key: str | None = None

    def __post_init__(self) -> None:
        """Reject unbounded or malformed worker context at the boundary."""
        text_fields = {
            "run_id": (self.run_id, 256),
            "role": (self.role, 64),
            "mission_id": (self.mission_id, 256),
            "resource_id": (self.resource_id, 256),
            "capability": (self.capability, 128),
            "provider": (self.provider, 128),
            "model": (self.model, 256),
            "isolation_group": (self.isolation_group, 128),
        }
        for name, (value, limit) in text_fields.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"manifest {name} is required")
            if len(value) > limit:
                raise ValueError(f"manifest {name} exceeds {limit} characters")
        if self.step_id is not None and len(str(self.step_id)) > 256:
            raise ValueError("manifest step_id exceeds 256 characters")
        if not isinstance(self.tool_policy, (tuple, list)) or len(self.tool_policy) > 64:
            raise ValueError("manifest tool_policy is too large")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 128
               for item in self.tool_policy):
            raise ValueError("manifest tool_policy contains an invalid entry")
        if not math.isfinite(float(self.budget_usd)) or self.budget_usd < 0:
            raise ValueError("manifest budget_usd must be finite and non-negative")
        if not 1 <= int(self.duration_seconds) <= 86_400:
            raise ValueError("manifest duration_seconds is outside [1, 86400]")
        if len(self.instructions) > 12_000:
            raise ValueError("manifest instructions exceed 12000 characters")
        for name, values in (("expected_artifacts", self.expected_artifacts),
                             ("verification_criteria", self.verification_criteria),
                             ("forbidden_paths", self.forbidden_paths)):
            if not isinstance(values, (tuple, list)) or len(values) > 128:
                raise ValueError(f"manifest {name} is too large")
            if any(not isinstance(item, str) or len(item) > 512 for item in values):
                raise ValueError(f"manifest {name} contains an invalid entry")
        if int(self.attempt) < 1:
            raise ValueError("manifest attempt must be positive")
        optional_text = {
            "objective_id": (self.objective_id, 256),
            "campaign_id": (self.campaign_id, 256),
            "child_id": (self.child_id, 256),
            "campaign_isolation_key": (self.campaign_isolation_key, 512),
            "routing_identity": (self.routing_identity, 128),
            "effect_action_key": (self.effect_action_key, 512),
        }
        for name, (value, limit) in optional_text.items():
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > limit
            ):
                raise ValueError(f"manifest {name} is invalid")
        lineage = (self.objective_id, self.campaign_id, self.child_id)
        if any(lineage) and not all(lineage):
            raise ValueError(
                "manifest campaign lineage requires objective_id, campaign_id, and child_id"
            )
        if not isinstance(self.routing_requirements, dict):
            raise ValueError("manifest routing_requirements must be a mapping")
        if len(canonical_json(self.routing_requirements)) > 16_000:
            raise ValueError("manifest routing_requirements is too large")
        if bool(self.routing_requirements) != bool(self.routing_identity):
            raise ValueError(
                "manifest routing requirements and identity must be supplied together"
            )
        if self.routing_requirements:
            from .router import RoutingRequest

            request = RoutingRequest.from_mapping(self.routing_requirements)
            if request.identity_hash() != self.routing_identity:
                raise ValueError(
                    "manifest routing identity does not match its requirements"
                )

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "role": self.role,
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "resource_id": self.resource_id,
            "capability": self.capability,
            "provider": self.provider,
            "model": self.model,
            "isolation_group": self.isolation_group,
            "tool_policy": list(self.tool_policy),
            "budget_usd": self.budget_usd,
            "duration_seconds": self.duration_seconds,
            "instructions": self.instructions,
        }
        if self.expected_artifacts:
            payload["expected_artifacts"] = list(self.expected_artifacts)
        if self.verification_criteria:
            payload["verification_criteria"] = list(self.verification_criteria)
        if self.forbidden_paths:
            payload["forbidden_paths"] = list(self.forbidden_paths)
        if self.attempt != 1:
            payload["attempt"] = self.attempt
        if self.objective_id:
            payload.update({
                "objective_id": self.objective_id,
                "campaign_id": self.campaign_id,
                "child_id": self.child_id,
            })
        if self.campaign_isolation_key:
            payload["campaign_isolation_key"] = self.campaign_isolation_key
        if self.routing_requirements:
            payload["routing_requirements"] = dict(self.routing_requirements)
            payload["routing_identity"] = self.routing_identity
        if self.effect_action_key:
            payload["effect_action_key"] = self.effect_action_key
        return payload

    def content_hash(self) -> str:
        """Deterministic sha256 over the canonical manifest payload."""
        return hashlib.sha256(
            canonical_json(self.to_payload()).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ContextManifest":
        return cls(
            run_id=payload["run_id"],
            role=payload["role"],
            mission_id=payload["mission_id"],
            step_id=payload.get("step_id"),
            resource_id=payload["resource_id"],
            capability=payload["capability"],
            provider=payload["provider"],
            model=payload["model"],
            isolation_group=payload["isolation_group"],
            tool_policy=tuple(payload.get("tool_policy", [])),
            budget_usd=float(payload.get("budget_usd", 0.0)),
            duration_seconds=int(payload.get("duration_seconds", 300)),
            instructions=payload.get("instructions", ""),
            expected_artifacts=tuple(payload.get("expected_artifacts", [])),
            verification_criteria=tuple(payload.get("verification_criteria", [])),
            forbidden_paths=tuple(payload.get("forbidden_paths", [])),
            attempt=int(payload.get("attempt", 1)),
            objective_id=(
                str(payload["objective_id"]) if payload.get("objective_id") else None
            ),
            campaign_id=(
                str(payload["campaign_id"]) if payload.get("campaign_id") else None
            ),
            child_id=str(payload["child_id"]) if payload.get("child_id") else None,
            campaign_isolation_key=(
                str(payload["campaign_isolation_key"])
                if payload.get("campaign_isolation_key") else None
            ),
            routing_requirements=dict(payload.get("routing_requirements") or {}),
            routing_identity=(
                str(payload["routing_identity"])
                if payload.get("routing_identity") else None
            ),
            effect_action_key=(
                str(payload["effect_action_key"])
                if payload.get("effect_action_key") else None
            ),
        )


def verify_manifest_hash(manifest: ContextManifest, recorded_hash: str) -> bool:
    """Recompute the content hash and compare with the recorded durable value."""
    return manifest.content_hash() == recorded_hash
