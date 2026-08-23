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
        )


def verify_manifest_hash(manifest: ContextManifest, recorded_hash: str) -> bool:
    """Recompute the content hash and compare with the recorded durable value."""
    return manifest.content_hash() == recorded_hash
