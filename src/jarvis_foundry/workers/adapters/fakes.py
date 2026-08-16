"""Deterministic scripted worker adapter for gate tests and staging.

``ScriptedWorkerAdapter`` returns scripted envelopes and records every
contract it received (exact-context assertions), including scripted false
success claims, scope escapes, missing artifacts, crashes, and provider
unavailability — so the router/evidence/orchestrator layers are tested
without touching any real CLI.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...clock import isoformat, utc_now
from ..contracts import WorkerStepContract
from ..envelope import (
    COMPLETED,
    FAILED,
    VERDICT_FAIL,
    VERDICT_NOT_TESTED,
    VERDICT_PASS,
    ResultEnvelope,
)
from .base import ProviderUnavailable


@dataclass(frozen=True, slots=True)
class ScriptedOutcome:
    worker_status: str = COMPLETED
    verdict: str | None = VERDICT_PASS
    claimed_outcome: str = "scripted worker completed"
    summary: str = ""
    files_changed: tuple[str, ...] = ()
    observed_touched_paths: tuple[str, ...] = ()
    diff_hash: str | None = None
    expected_artifacts_present: bool = True
    error_class: str | None = None
    error_detail: str | None = None
    usage_availability: str = "NOT_AVAILABLE"


class ScriptedWorkerAdapter:
    """Scripted adapter; ``framework`` and scripted outcomes are configurable."""

    def __init__(
        self,
        framework: str,
        *,
        outcome: ScriptedOutcome | None = None,
        outcomes_by_run: dict[str, ScriptedOutcome] | None = None,
        fail_provider: bool = False,
    ) -> None:
        self.framework = framework
        self.outcome = outcome or ScriptedOutcome()
        self.outcomes_by_run = dict(outcomes_by_run or {})
        self.fail_provider = fail_provider
        self.received: list[WorkerStepContract] = []
        self.heartbeat_calls = 0

    def dispatch(
        self,
        contract: WorkerStepContract,
        *,
        heartbeat: Callable[[], None],
    ) -> ResultEnvelope:
        self.received.append(contract)
        if self.fail_provider:
            raise ProviderUnavailable(
                f"provider {contract.provider} unavailable (scripted)")
        outcome = self.outcomes_by_run.get(contract.run_id, self.outcome)
        heartbeat()
        self.heartbeat_calls += 1
        if outcome.worker_status == FAILED:
            return ResultEnvelope.from_contract(
                contract, framework=self.framework,
                worker_status=FAILED,
                verdict=VERDICT_FAIL if outcome.verdict == VERDICT_FAIL else VERDICT_NOT_TESTED,
                claimed_outcome="scripted failure",
                summary=outcome.summary,
                worker_results=(outcome.error_class or "scripted_failure",),
                usage_availability=outcome.usage_availability,
            )
        artifact_hashes: tuple[str, ...] = ()
        if outcome.expected_artifacts_present:
            artifact_hashes = tuple(
                hashlib.sha256((contract.working_root / rel).read_bytes()).hexdigest()
                for rel in contract.expected_artifacts
                if (contract.working_root / rel).is_file()
            )
        return ResultEnvelope.from_contract(
            contract, framework=self.framework,
            worker_status=outcome.worker_status,
            verdict=outcome.verdict,
            claimed_outcome=outcome.claimed_outcome,
            summary=outcome.summary,
            files_changed=outcome.files_changed,
            observed_touched_paths=outcome.observed_touched_paths,
            diff_hash=outcome.diff_hash,
            worker_artifact_hashes=artifact_hashes,
            usage_availability=outcome.usage_availability,
        )
