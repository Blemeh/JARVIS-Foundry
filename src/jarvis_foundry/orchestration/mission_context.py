"""Bounded mission context construction for R8 worker dispatch."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash


@dataclass(frozen=True)
class MissionContext:
    mission_id: str
    mission: str
    current_step: str
    role: str
    objective: str
    known_verified_state: Mapping[str, Any]
    relevant_previous_failures: tuple[str, ...]
    allowed_mutation: tuple[str, ...]
    forbidden_mutation: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    required_tests: tuple[str, ...]
    required_evidence: tuple[str, ...]
    rollback_expectation: str
    result_contract: Mapping[str, Any]
    dispatch: Mapping[str, Any] = field(default_factory=dict)
    worker_spec: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "MISSION": self.mission,
            "CURRENT STEP": self.current_step,
            "ROLE": self.role,
            "OBJECTIVE": self.objective,
            "KNOWN VERIFIED STATE": dict(self.known_verified_state),
            "RELEVANT PREVIOUS FAILURES": list(self.relevant_previous_failures),
            "ALLOWED MUTATION": list(self.allowed_mutation),
            "FORBIDDEN MUTATION": list(self.forbidden_mutation),
            "ACCEPTANCE CRITERIA": list(self.acceptance_criteria),
            "REQUIRED TESTS": list(self.required_tests),
            "REQUIRED EVIDENCE": list(self.required_evidence),
            "ROLLBACK EXPECTATION": self.rollback_expectation,
            "RESULT CONTRACT": dict(self.result_contract),
            "DISPATCH": dict(self.dispatch),
            "WORKER SPEC": dict(self.worker_spec),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MissionContext:
        return MissionContext(
            mission_id=str(payload["mission_id"]),
            mission=str(payload["MISSION"]),
            current_step=str(payload["CURRENT STEP"]),
            role=str(payload["ROLE"]),
            objective=str(payload["OBJECTIVE"]),
            known_verified_state=dict(payload["KNOWN VERIFIED STATE"]),
            relevant_previous_failures=tuple(
                str(item) for item in payload["RELEVANT PREVIOUS FAILURES"]
            ),
            allowed_mutation=tuple(str(item) for item in payload["ALLOWED MUTATION"]),
            forbidden_mutation=tuple(str(item) for item in payload["FORBIDDEN MUTATION"]),
            acceptance_criteria=tuple(str(item) for item in payload["ACCEPTANCE CRITERIA"]),
            required_tests=tuple(str(item) for item in payload["REQUIRED TESTS"]),
            required_evidence=tuple(str(item) for item in payload["REQUIRED EVIDENCE"]),
            rollback_expectation=str(payload["ROLLBACK EXPECTATION"]),
            result_contract=dict(payload["RESULT CONTRACT"]),
            dispatch=dict(payload.get("DISPATCH", {})),
            worker_spec=dict(payload.get("WORKER SPEC", {})),
        )

    def content_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def write(self, path: Path) -> None:
        from jarvis_foundry.orchestration.paths import resolve_within

        target = resolve_within(path.parent, path)
        target.write_text(
            json_dumps(self.to_dict()),
            encoding="utf-8",
        )


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def build_mission_context(
    *,
    mission_id: str,
    mission: str,
    current_step: str,
    role: str,
    objective: str,
    known_verified_state: Mapping[str, Any],
    relevant_previous_failures: list[str] | tuple[str, ...],
    allowed_mutation: list[str] | tuple[str, ...],
    forbidden_mutation: list[str] | tuple[str, ...],
    acceptance_criteria: list[str] | tuple[str, ...],
    required_tests: list[str] | tuple[str, ...],
    required_evidence: list[str] | tuple[str, ...],
    rollback_expectation: str,
    result_contract: Mapping[str, Any],
    dispatch: Mapping[str, Any] | None = None,
    worker_spec: Mapping[str, Any] | None = None,
) -> MissionContext:
    return MissionContext(
        mission_id=mission_id,
        mission=mission,
        current_step=current_step,
        role=role,
        objective=objective,
        known_verified_state=known_verified_state,
        relevant_previous_failures=tuple(relevant_previous_failures),
        allowed_mutation=tuple(allowed_mutation),
        forbidden_mutation=tuple(forbidden_mutation),
        acceptance_criteria=tuple(acceptance_criteria),
        required_tests=tuple(required_tests),
        required_evidence=tuple(required_evidence),
        rollback_expectation=rollback_expectation,
        result_contract=result_contract,
        dispatch=dispatch or {},
        worker_spec=worker_spec or {},
    )
