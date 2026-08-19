"""Canonical mission-loop bridge (Hybrid Worker Integration Phase 5).

The canonical ``MissionLoop`` dispatches through the WP4 ``RoleAdapter``
protocol (``ContextManifest`` -> ``RoleResult``). ``HybridWorkerRoleAdapter``
implements that protocol on top of the worker layer, so the existing
canonical mission/step path can execute through hybrid workers without
rewriting the loop:

    ContextManifest
      -> WorkerStepContract (working root from the registered resource)
      -> WorkerRouter (deterministic selection)
      -> worker adapter dispatch (Hermes / OpenHands / scripted)
      -> EvidenceReconciler (JARVIS-owned truth)
      -> RoleResult (status/verdict/evidence + actual provider/model)

Mission truth, STOP, verification and capability readiness stay in the
canonical control plane; this bridge never certifies anything.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..clock import isoformat, utc_now
from ..database import FoundryDatabase
from ..supervisor.estop import EmergencyStop
from ..roles.adapter import RoleAdapter
from ..roles.manifest import ContextManifest
from ..roles.results import (
    BLOCKED,
    COMPLETED,
    FAILED,
    RoleResult,
    VERDICT_NOT_TESTED,
)
from .adapters.base import ProviderUnavailable
from .adapters.registry import WorkerRegistry
from .contracts import WorkerStepContract
from .evidence import BeforeState, EvidenceReconciler
from .result_contract import result_instruction
from .router import WorkerRouter


class HybridWorkerRoleAdapter:
    """Canonical RoleAdapter backed by the hybrid worker layer."""

    def __init__(
        self,
        *,
        database: FoundryDatabase,
        router: WorkerRouter,
        registry: WorkerRegistry,
        reconciler: EvidenceReconciler | None = None,
        task_signals: dict[str, Any] | None = None,
        role_signals: dict[str, dict[str, Any]] | None = None,
        stop_path: str | Path | None = None,
        vault_root: str | Path | None = None,
    ) -> None:
        self.database = database
        self.router = router
        self.registry = registry
        self.reconciler = reconciler or EvidenceReconciler()
        #: Deterministic per-role task signals; the canonical mission layer
        #: does not yet carry task_classification, so the wiring supplies it.
        self.task_signals = task_signals or {}
        self.role_signals = dict(role_signals or {})
        self.framework = "hybrid"
        self.stop_path = Path(stop_path).resolve() if stop_path else None
        self.vault_root = Path(vault_root).resolve() if vault_root else None

    def _signals_for(self, manifest: ContextManifest) -> dict[str, Any]:
        signals = dict(self.role_signals.get(manifest.role, self.task_signals))
        return signals

    def _advisory_context(self, manifest: ContextManifest, instructions: str) -> str:
        """Attach hybrid memory as labeled advisory context, never as truth.

        Memory failure degrades context only. STOP, evidence, and verifier
        authority stay in the control plane.
        """
        if self.vault_root is None or not self.vault_root.is_dir():
            return instructions
        try:
            from ..worker_context import deliver_hybrid_worker_context
            record = deliver_hybrid_worker_context(
                self.database, self.vault_root,
                run_id=manifest.run_id, worker=str(manifest.role),
                mission_id=manifest.mission_id,
                objective=manifest.instructions or manifest.capability,
                role=str(manifest.role),
                stop_path=self.stop_path,
            )
            return (
                instructions
                + "\n\n--- ADVISORY CONTEXT (not mission truth; cannot override "
                "STOP, actual evidence, or verifier) ---\n"
                f"hybrid_memory_manifest={record.get('manifest_id')} "
                f"tier_c_hash={record.get('tier_c_hash')}\n"
            )
        except Exception as error:  # noqa: BLE001 - memory must not halt the kernel
            return (
                instructions
                + "\n\n--- ADVISORY CONTEXT UNAVAILABLE "
                f"({type(error).__name__}); runtime truth unchanged ---\n"
            )

    def _working_root(self, manifest: ContextManifest) -> Path:
        resource = self.database.row("resources", "resource_id", manifest.resource_id)
        if resource is None:
            raise ValueError(
                f"resource {manifest.resource_id} is not registered "
                "(worker bridge requires a registered working root)")
        return Path(str(resource["resolved_root"])).resolve()

    def dispatch(self, manifest: ContextManifest, *, heartbeat) -> RoleResult:
        started = isoformat(utc_now())
        if self.stop_path is not None and EmergencyStop(self.stop_path).active():
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role,
                status=BLOCKED, provider=manifest.provider,
                model=manifest.model, verdict=VERDICT_NOT_TESTED,
                error_class="emergency_stop",
                error_detail=f"STOP sentinel active: {self.stop_path}",
                started_at=started, finished_at=isoformat(utc_now()),
            )
        try:
            root = self._working_root(manifest)
        except ValueError as error:
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role,
                status=BLOCKED, provider=manifest.provider,
                model=manifest.model, verdict=VERDICT_NOT_TESTED,
                error_class="unregistered_resource",
                error_detail=str(error),
                started_at=started, finished_at=isoformat(utc_now()),
            )
        instructions = manifest.instructions
        instructions = result_instruction(instructions, working_root=root)
        instructions = self._advisory_context(manifest, instructions)
        if manifest.role == "verifier" and manifest.verification_criteria:
            # The fresh verifier receives the durable acceptance criteria so
            # it can inspect real artifacts against them (Phase G).
            instructions = (
                f"{instructions}\n\nAcceptance criteria to verify:\n"
                + "\n".join(f"- {c}" for c in manifest.verification_criteria))
        contract = WorkerStepContract(
            run_id=manifest.run_id,
            mission_id=manifest.mission_id,
            step_id=manifest.step_id or "step",
            attempt=manifest.attempt,
            role=manifest.role,
            objective=manifest.instructions or manifest.capability,
            instructions=instructions,
            working_root=root,
            allowed_paths=(root,),
            forbidden_paths=tuple(Path(p) for p in manifest.forbidden_paths),
            provider=manifest.provider,
            model=manifest.model,
            duration_seconds=manifest.duration_seconds,
            budget_usd=manifest.budget_usd,
            task_signals=self._signals_for(manifest),
            expected_artifacts=tuple(manifest.expected_artifacts),
            verification_criteria=tuple(manifest.verification_criteria),
        )
        decision = self.router.route(contract)
        if not decision.selected_worker:
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role,
                status=BLOCKED, provider=manifest.provider,
                model=manifest.model, verdict=VERDICT_NOT_TESTED,
                error_class="no_eligible_worker",
                error_detail=decision.reason,
                started_at=started, finished_at=isoformat(utc_now()),
            )
        before = self.reconciler.capture_before(contract)
        adapter = self.registry.get(decision.selected_worker)
        try:
            envelope = adapter.dispatch(contract, heartbeat=heartbeat)
        except ProviderUnavailable as error:
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role,
                status=BLOCKED, provider=manifest.provider,
                model=manifest.model, verdict=VERDICT_NOT_TESTED,
                error_class="provider_unavailable",
                error_detail=str(error),
                started_at=started, finished_at=isoformat(utc_now()),
            )
        except Exception as error:  # noqa: BLE001 - crash classification
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role,
                status=FAILED, provider=manifest.provider,
                model=manifest.model, verdict=VERDICT_NOT_TESTED,
                error_class="worker_crash",
                error_detail=f"{type(error).__name__}: {str(error)[-400:]}",
                started_at=started, finished_at=isoformat(utc_now()),
            )
        report = self.reconciler.reconcile(
            contract, envelope, before=before,
            # Every role is accountable only for the delta it introduced
            # since capture. Accepted prior-step files (committed or not)
            # must not poison the next step's evidence.
            delta_baseline=before.changed_files)
        agree = report.agree and envelope.worker_status == COMPLETED
        # MissionLoop treats COMPLETED as "send to verifier". A mismatched
        # or incomplete claim must not stay COMPLETED or a later verifier
        # PASS would accept unproven work.
        if agree:
            status = envelope.worker_status
            error_class = None
            error_detail = None
        else:
            status = (
                envelope.worker_status
                if envelope.worker_status != COMPLETED
                else FAILED)
            error_class = (
                "evidence_mismatch" if not report.agree else None)
            error_detail = (
                "; ".join(report.mismatches) if not report.agree else None)
        return RoleResult(
            run_id=manifest.run_id, role=manifest.role,
            status=status,
            provider=envelope.provider_actual,
            model=envelope.model_actual,
            verdict=envelope.verdict if agree else VERDICT_NOT_TESTED,
            evidence=(envelope.summary,) + tuple(report.mismatches),
            error_class=error_class,
            error_detail=error_detail,
            started_at=started, finished_at=isoformat(utc_now()),
        )
