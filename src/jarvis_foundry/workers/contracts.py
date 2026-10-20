"""Framework-neutral worker step contracts.

A ``WorkerStepContract`` is the ONLY thing JARVIS passes to a worker: the
mission/step binding, the working root and scope boundaries, the context
manifest reference, a snapshot of the action envelope, the requested
provider/model identity, and deterministic task-classification signals.

Nothing in the contract depends on Hermes or OpenHands formats. Adapters
translate at the single boundary that talks to a real CLI.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..events import canonical_json

WORKER_HERMES = "hermes"
WORKER_OPENHANDS = "openhands"
VALID_WORKERS = frozenset({WORKER_HERMES, WORKER_OPENHANDS})

#: DIR-2026-08-12 / External Fusion model mandate: every real worker run
#: resolves to the machine-local provider configuration (default muse-spark).
DIRECTIVE_PROVIDER = "opencode_go"
DIRECTIVE_MODEL = "muse-spark-1.3-contributor"


@dataclass(frozen=True, slots=True)
class WorkerStepContract:
    """A bounded step JARVIS requests from a worker framework."""

    run_id: str
    mission_id: str
    step_id: str
    attempt: int
    role: str  # builder | repair | verifier
    objective: str
    instructions: str
    working_root: Path
    allowed_paths: tuple[Path, ...] = field(default_factory=tuple)
    forbidden_paths: tuple[Path, ...] = field(default_factory=tuple)
    context_manifest_id: str | None = None
    context_manifest_hash: str | None = None
    action_envelope: dict[str, Any] = field(default_factory=dict)
    provider: str = DIRECTIVE_PROVIDER
    model: str = DIRECTIVE_MODEL
    duration_seconds: int = 900
    budget_usd: float = 0.0
    # Deterministic classification signals (see workers.router).
    task_signals: dict[str, Any] = field(default_factory=dict)
    expected_artifacts: tuple[str, ...] = field(default_factory=tuple)
    verification_criteria: tuple[str, ...] = field(default_factory=tuple)
    required_tests: tuple[str, ...] = field(default_factory=tuple)
    package_id: str | None = None
    package_hash: str | None = None
    attempt_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "attempt": self.attempt,
            "role": self.role,
            "objective": self.objective,
            "instructions": self.instructions,
            "working_root": str(self.working_root),
            "allowed_paths": [str(p) for p in self.allowed_paths],
            "forbidden_paths": [str(p) for p in self.forbidden_paths],
            "context_manifest_id": self.context_manifest_id,
            "context_manifest_hash": self.context_manifest_hash,
            "action_envelope": self.action_envelope,
            "provider": self.provider,
            "model": self.model,
            "duration_seconds": self.duration_seconds,
            "budget_usd": self.budget_usd,
            "task_signals": self.task_signals,
            "expected_artifacts": list(self.expected_artifacts),
            "verification_criteria": list(self.verification_criteria),
        }
        if self.required_tests:
            payload["required_tests"] = list(self.required_tests)
        if self.package_id:
            payload["package_id"] = self.package_id
        if self.package_hash:
            payload["package_hash"] = self.package_hash
        if self.attempt_id:
            payload["attempt_id"] = self.attempt_id
        return payload

    def content_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self.to_payload()).encode("utf-8")).hexdigest()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WorkerStepContract":
        return cls(
            run_id=payload["run_id"],
            mission_id=payload["mission_id"],
            step_id=payload["step_id"],
            attempt=int(payload.get("attempt", 1)),
            role=payload.get("role", "builder"),
            objective=payload.get("objective", ""),
            instructions=payload.get("instructions", ""),
            working_root=Path(payload["working_root"]),
            allowed_paths=tuple(Path(p) for p in payload.get("allowed_paths", [])),
            forbidden_paths=tuple(Path(p) for p in payload.get("forbidden_paths", [])),
            context_manifest_id=payload.get("context_manifest_id"),
            context_manifest_hash=payload.get("context_manifest_hash"),
            action_envelope=dict(payload.get("action_envelope") or {}),
            provider=payload.get("provider", DIRECTIVE_PROVIDER),
            model=payload.get("model", DIRECTIVE_MODEL),
            duration_seconds=int(payload.get("duration_seconds", 900)),
            budget_usd=float(payload.get("budget_usd", 0.0)),
            task_signals=dict(payload.get("task_signals") or {}),
            expected_artifacts=tuple(payload.get("expected_artifacts", [])),
            verification_criteria=tuple(payload.get("verification_criteria", [])),
            required_tests=tuple(payload.get("required_tests", [])),
            package_id=payload.get("package_id"),
            package_hash=payload.get("package_hash"),
            attempt_id=payload.get("attempt_id"),
        )
