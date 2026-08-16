"""Canonical worker result envelope (Hybrid Worker Integration).

Every worker framework normalizes its outcome into one durable contract:
run identity, worker framework plus requested vs ACTUAL provider/model,
context reference, scope and observed touched paths, execution timing,
the worker's own claim, observed changes, verification the worker
performed, usage telemetry, uncertainty/blockers/warnings, and the
JARVIS-side worker_status.

Usage fields stay ``NOT_AVAILABLE`` when the runtime does not expose them;
token/cost values are never invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..clock import isoformat, utc_now
from .contracts import DIRECTIVE_MODEL, DIRECTIVE_PROVIDER, WorkerStepContract

RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
CANCELLED = "CANCELLED"

VALID_STATUSES = frozenset({RUNNING, COMPLETED, FAILED, BLOCKED, CANCELLED})

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_BLOCKED = "BLOCKED"
VERDICT_NOT_TESTED = "NOT_TESTED"

VALID_VERDICTS = frozenset({VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED, VERDICT_NOT_TESTED})

NOT_AVAILABLE = "NOT_AVAILABLE"


class EnvelopeValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResultEnvelope:
    run_id: str
    mission_id: str
    step_id: str
    attempt: int
    role: str
    # worker identity
    framework: str
    provider_requested: str
    model_requested: str
    provider_actual: str
    model_actual: str
    # context
    manifest_id: str | None = None
    manifest_hash: str | None = None
    # scope
    registered_resource_id: str = ""
    working_root: str = ""
    allowed_paths: tuple[str, ...] = field(default_factory=tuple)
    forbidden_paths: tuple[str, ...] = field(default_factory=tuple)
    observed_touched_paths: tuple[str, ...] = field(default_factory=tuple)
    # execution
    started_at: str = field(default_factory=isoformat)
    finished_at: str = field(default_factory=isoformat)
    wall_time: float = 0.0
    exit_state: str = COMPLETED
    # worker claim (prose is not canonical truth)
    claimed_outcome: str = ""
    summary: str = ""
    # changes
    files_changed: tuple[str, ...] = field(default_factory=tuple)
    diff_hash: str | None = None
    before_state_ref: str | None = None
    after_state_ref: str | None = None
    # verification performed by the worker (never certification)
    worker_commands: tuple[str, ...] = field(default_factory=tuple)
    worker_results: tuple[str, ...] = field(default_factory=tuple)
    worker_raw_artifacts: tuple[str, ...] = field(default_factory=tuple)
    worker_artifact_hashes: tuple[str, ...] = field(default_factory=tuple)
    # usage (NOT_AVAILABLE when the runtime does not expose it)
    usage_calls: int | str = NOT_AVAILABLE
    usage_input_tokens: int | str = NOT_AVAILABLE
    usage_output_tokens: int | str = NOT_AVAILABLE
    usage_total_tokens: int | str = NOT_AVAILABLE
    usage_cost: float | str = NOT_AVAILABLE
    usage_availability: str = NOT_AVAILABLE
    # uncertainty / blockers / warnings
    uncertainty: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    # JARVIS-side worker status + verdict
    worker_status: str = COMPLETED
    verdict: str | None = None

    def __post_init__(self) -> None:
        if self.worker_status not in VALID_STATUSES:
            raise EnvelopeValidationError(
                f"invalid worker status: {self.worker_status!r}")
        if self.verdict is not None and self.verdict not in VALID_VERDICTS:
            raise EnvelopeValidationError(
                f"invalid verdict: {self.verdict!r}")
        if self.usage_availability == NOT_AVAILABLE:
            for value in (self.usage_calls, self.usage_input_tokens,
                          self.usage_output_tokens, self.usage_total_tokens,
                          self.usage_cost):
                if value not in (NOT_AVAILABLE, 0, 0.0, None):
                    raise EnvelopeValidationError(
                        "usage values must stay NOT_AVAILABLE when usage "
                        "availability is NOT_AVAILABLE (never invent "
                        "token/cost values)")

    @classmethod
    def from_contract(
        cls,
        contract: WorkerStepContract,
        *,
        framework: str,
        worker_status: str = COMPLETED,
        verdict: str | None = None,
        claimed_outcome: str = "",
        summary: str = "",
        observed_touched_paths: tuple[str, ...] = (),
        **overrides: Any,
    ) -> "ResultEnvelope":
        payload: dict[str, Any] = {
            "run_id": contract.run_id,
            "mission_id": contract.mission_id,
            "step_id": contract.step_id,
            "attempt": contract.attempt,
            "role": contract.role,
            "framework": framework,
            "provider_requested": contract.provider,
            "model_requested": contract.model,
            "provider_actual": contract.provider,
            "model_actual": contract.model,
            "manifest_id": contract.context_manifest_id,
            "manifest_hash": contract.context_manifest_hash,
            "working_root": str(contract.working_root),
            "allowed_paths": [str(p) for p in contract.allowed_paths],
            "forbidden_paths": [str(p) for p in contract.forbidden_paths],
            "worker_status": worker_status,
            "verdict": verdict,
            "claimed_outcome": claimed_outcome,
            "summary": summary,
            "observed_touched_paths": list(observed_touched_paths),
            **overrides,
        }
        return cls.from_payload(payload)

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "attempt": self.attempt,
            "role": self.role,
            "framework": self.framework,
            "provider_requested": self.provider_requested,
            "model_requested": self.model_requested,
            "provider_actual": self.provider_actual,
            "model_actual": self.model_actual,
            "manifest_id": self.manifest_id,
            "manifest_hash": self.manifest_hash,
            "registered_resource_id": self.registered_resource_id,
            "working_root": self.working_root,
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "observed_touched_paths": list(self.observed_touched_paths),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "wall_time": self.wall_time,
            "exit_state": self.exit_state,
            "claimed_outcome": self.claimed_outcome,
            "summary": self.summary,
            "files_changed": list(self.files_changed),
            "diff_hash": self.diff_hash,
            "before_state_ref": self.before_state_ref,
            "after_state_ref": self.after_state_ref,
            "worker_commands": list(self.worker_commands),
            "worker_results": list(self.worker_results),
            "worker_raw_artifacts": list(self.worker_raw_artifacts),
            "worker_artifact_hashes": list(self.worker_artifact_hashes),
            "usage_calls": self.usage_calls,
            "usage_input_tokens": self.usage_input_tokens,
            "usage_output_tokens": self.usage_output_tokens,
            "usage_total_tokens": self.usage_total_tokens,
            "usage_cost": self.usage_cost,
            "usage_availability": self.usage_availability,
            "uncertainty": list(self.uncertainty),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "worker_status": self.worker_status,
            "verdict": self.verdict,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ResultEnvelope":
        def _tokens(value: Any) -> int | str:
            if value in (None, NOT_AVAILABLE):
                return NOT_AVAILABLE
            return int(value)

        cost = payload.get("usage_cost")
        return cls(
            run_id=payload["run_id"],
            mission_id=payload["mission_id"],
            step_id=payload["step_id"],
            attempt=int(payload.get("attempt", 1)),
            role=payload.get("role", "builder"),
            framework=payload["framework"],
            provider_requested=payload.get("provider_requested", DIRECTIVE_PROVIDER),
            model_requested=payload.get("model_requested", DIRECTIVE_MODEL),
            provider_actual=payload.get("provider_actual", payload.get("provider_requested", DIRECTIVE_PROVIDER)),
            model_actual=payload.get("model_actual", payload.get("model_requested", DIRECTIVE_MODEL)),
            manifest_id=payload.get("manifest_id"),
            manifest_hash=payload.get("manifest_hash"),
            registered_resource_id=payload.get("registered_resource_id", ""),
            working_root=payload.get("working_root", ""),
            allowed_paths=tuple(payload.get("allowed_paths", [])),
            forbidden_paths=tuple(payload.get("forbidden_paths", [])),
            observed_touched_paths=tuple(payload.get("observed_touched_paths", [])),
            started_at=payload.get("started_at", isoformat(utc_now())),
            finished_at=payload.get("finished_at", isoformat(utc_now())),
            wall_time=float(payload.get("wall_time", 0.0)),
            exit_state=payload.get("exit_state", COMPLETED),
            claimed_outcome=payload.get("claimed_outcome", ""),
            summary=payload.get("summary", ""),
            files_changed=tuple(payload.get("files_changed", [])),
            diff_hash=payload.get("diff_hash"),
            before_state_ref=payload.get("before_state_ref"),
            after_state_ref=payload.get("after_state_ref"),
            worker_commands=tuple(payload.get("worker_commands", [])),
            worker_results=tuple(payload.get("worker_results", [])),
            worker_raw_artifacts=tuple(payload.get("worker_raw_artifacts", [])),
            worker_artifact_hashes=tuple(payload.get("worker_artifact_hashes", [])),
            usage_calls=_tokens(payload.get("usage_calls", NOT_AVAILABLE)),
            usage_input_tokens=_tokens(payload.get("usage_input_tokens", NOT_AVAILABLE)),
            usage_output_tokens=_tokens(payload.get("usage_output_tokens", NOT_AVAILABLE)),
            usage_total_tokens=_tokens(payload.get("usage_total_tokens", NOT_AVAILABLE)),
            usage_cost=float(cost) if cost not in (None, NOT_AVAILABLE) else NOT_AVAILABLE,
            usage_availability=payload.get("usage_availability", NOT_AVAILABLE),
            uncertainty=tuple(payload.get("uncertainty", [])),
            blockers=tuple(payload.get("blockers", [])),
            warnings=tuple(payload.get("warnings", [])),
            worker_status=payload.get("worker_status", COMPLETED),
            verdict=payload.get("verdict"),
        )
