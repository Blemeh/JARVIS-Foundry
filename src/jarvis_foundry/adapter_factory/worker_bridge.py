"""Optional bridge from a generated adapter to the canonical worker contract."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from ..workers.adapters.base import ProviderUnavailable, WorkerAdapter
from ..workers.contracts import WorkerStepContract
from ..workers.envelope import (
    BLOCKED,
    COMPLETED,
    FAILED,
    VERDICT_BLOCKED,
    VERDICT_NOT_TESTED,
    ResultEnvelope,
)
from .runtime import invoke_typed_adapter, load_verified_artifact


class GeneratedWorkerAdapter(WorkerAdapter):
    """Translate one WorkerStepContract onto a built adapter artifact.

    The bridge does not replace MissionLoop verification.  It reports the
    adapter receipt as worker evidence and leaves the worker verdict
    ``NOT_TESTED`` for the independent verifier.
    """

    def __init__(
        self,
        artifact_root: str | Path,
        *,
        capability_id: str,
        enabled: bool = False,
        python_executable: str | Path | None = None,
        config: Mapping[str, Any] | None = None,
        stop_path: str | Path | None = None,
    ) -> None:
        root, _manifest, descriptor = load_verified_artifact(artifact_root)
        self.artifact_root = root
        self.framework = descriptor.adapter_id
        self.capability_id = capability_id
        self.enabled = enabled
        self.python_executable = python_executable
        self.config = dict(config or {})
        self.stop_path = stop_path

    def dispatch(
        self,
        contract: WorkerStepContract,
        *,
        heartbeat: Callable[[], None],
    ) -> ResultEnvelope:
        if not self.enabled:
            raise ProviderUnavailable(f"generated adapter {self.framework} is disabled")
        envelope = dict(contract.action_envelope or {})
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            payload = envelope
        heartbeat()
        result = invoke_typed_adapter(
            self.artifact_root,
            self.capability_id,
            payload,
            mission_id=contract.mission_id,
            workflow_id=f"workflow:{contract.mission_id}",
            step_id=contract.step_id,
            attempt=contract.attempt,
            config=self.config,
            python_executable=self.python_executable,
            canonical_effect_authorized=bool(envelope.get("canonical_effect_authorized")),
            stop_path=self.stop_path,
            allowed_root=contract.working_root,
        )
        receipt = result.get("receipt") or {}
        claimed = str((result.get("result") or {}).get("claimed") or "UNKNOWN")
        if result.get("success"):
            status = COMPLETED
            verdict = VERDICT_NOT_TESTED
            blockers: tuple[str, ...] = ()
        elif claimed == "BLOCKED":
            status = BLOCKED
            verdict = VERDICT_BLOCKED
            blockers = (str((result.get("result") or {}).get("error_code") or "adapter blocked"),)
        else:
            status = FAILED
            verdict = VERDICT_NOT_TESTED
            blockers = ()
        return ResultEnvelope.from_contract(
            contract,
            framework=self.framework,
            worker_status=status,
            verdict=verdict,
            claimed_outcome=claimed,
            summary=(
                f"generated adapter {self.framework} returned {claimed}; "
                f"receipt={receipt.get('receipt_id', 'none')}"
            ),
            worker_results=(
                f"adapter_receipt:{receipt.get('receipt_id', 'none')}",
                f"adapter_verified:{result.get('verified', 'UNKNOWN')}",
            ),
            blockers=blockers,
        )


__all__ = ["GeneratedWorkerAdapter"]
