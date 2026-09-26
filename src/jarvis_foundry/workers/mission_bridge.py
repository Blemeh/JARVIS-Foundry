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

import math
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from ..clock import isoformat, utc_now
from ..database import FoundryDatabase
from ..events import Event
from ..supervisor.estop import EmergencyStop
from ..roles.adapter import ProviderFailure, RoleAdapter
from ..roles.manifest import ContextManifest
from ..roles.results import (
    BLOCKED,
    COMPLETED,
    FAILED,
    RoleResult,
    VERDICT_NOT_TESTED,
)
from ..workforce.state import rebuild_workforce_state
from ..workforce.worktrees import validate_execution_worktree
from .adapters.base import ProviderUnavailable
from .adapters.registry import WorkerRegistry
from .contracts import WorkerStepContract
from .envelope import NOT_AVAILABLE, ResultEnvelope
from .evidence import BeforeState, EvidenceReconciler
from .result_contract import result_instruction
from .router import WorkerRouter


_FABRIC_SCOPED_PATH_FIELDS = {
    "file.write": "target",
    "file.read": "target",
    "file.replace": "target",
    "file.append": "target",
    "dir.list": "target",
    "artifact.write": "target",
    "artifact.research": "target",
    "artifact.docx.create": "target",
    "artifact.docx.verify": "target",
    "artifact.table.export": "target",
    "artifact.pdf.copy": "target",
    "artifact.pptx.copy": "target",
    "artifact.image.transform": "target",
    "hash.file.sha256": "path",
}
_FABRIC_WIDE_LOCAL_ACTIONS = frozenset({
    "git.branch",
    "git.commit",
    "git.push",
    "script.run",
    "tests.run",
})


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
        use_fabric: bool = False,
        mcp_backend=None,
        mcp_mutation_verifier=None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.database = database
        self.router = router
        self.registry = registry
        self._monotonic = monotonic or time.monotonic
        self.reconciler = reconciler or EvidenceReconciler(
            monotonic=self._monotonic
        )
        self._lifecycle_deadlines: dict[str, float] = {}
        self._lifecycle_deadlines_lock = threading.Lock()
        #: Deterministic per-role task signals; the canonical mission layer
        #: does not yet carry task_classification, so the wiring supplies it.
        self.task_signals = task_signals or {}
        self.role_signals = dict(role_signals or {})
        self.framework = "hybrid"
        self.stop_path = Path(stop_path).resolve() if stop_path else None
        self.vault_root = Path(vault_root).resolve() if vault_root else None
        # Structured fabric effects on the canonical worker path. Lab
        # scripted Hermes stays off this so fault-injection adapters still run.
        self.use_fabric = bool(use_fabric)
        self.mcp_backend = mcp_backend
        self.mcp_mutation_verifier = mcp_mutation_verifier

    def set_lifecycle_deadline(
        self,
        run_id: str,
        deadline_monotonic: float,
    ) -> None:
        """Bind one exact package deadline to a canonical role run."""

        deadline = float(deadline_monotonic)
        if not math.isfinite(deadline):
            raise ValueError("lifecycle deadline must be finite")
        with self._lifecycle_deadlines_lock:
            self._lifecycle_deadlines[str(run_id)] = deadline

    def clear_lifecycle_deadline(self, run_id: str) -> None:
        with self._lifecycle_deadlines_lock:
            self._lifecycle_deadlines.pop(str(run_id), None)

    def _lifecycle_deadline(self, run_id: str) -> float | None:
        with self._lifecycle_deadlines_lock:
            return self._lifecycle_deadlines.get(str(run_id))

    def _remaining_duration(
        self,
        duration_seconds: int,
        deadline_monotonic: float | None,
    ) -> int:
        if deadline_monotonic is None:
            return int(duration_seconds)
        remaining = deadline_monotonic - self._monotonic()
        if remaining < 1:
            return 0
        return min(int(duration_seconds), int(math.floor(remaining)))

    @staticmethod
    def _observed_cost(envelope) -> float | None:
        availability = envelope.usage_availability
        raw_cost = envelope.usage_cost
        if (
            not isinstance(availability, str)
            or availability.strip().lower() != "available"
            or raw_cost in (None, NOT_AVAILABLE)
            or isinstance(raw_cost, bool)
            or not isinstance(raw_cost, (int, float))
        ):
            return None
        try:
            value = float(raw_cost)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value >= 0 else None

    def _cost_telemetry_required(self, manifest: ContextManifest) -> bool:
        if not manifest.package_id:
            return False
        if (
            math.isfinite(float(manifest.budget_usd))
            and float(manifest.budget_usd) > 0
        ):
            return True
        package = rebuild_workforce_state(self.database).package(
            manifest.package_id
        ).package
        return package.cost_budget_usd is not None

    def _reconcile_failed_worker_attempt(
        self,
        contract: WorkerStepContract,
        *,
        before: BeforeState,
        worker: str,
        error: BaseException,
        lifecycle_deadline: float | None,
    ) -> tuple[tuple[str, ...], str]:
        """Observe a worker's local delta even when no envelope was returned."""

        failure = f"{type(error).__name__}: {str(error)[-400:]}"
        envelope = ResultEnvelope.from_contract(
            contract,
            framework=worker,
            worker_status=FAILED,
            verdict=VERDICT_NOT_TESTED,
            claimed_outcome="worker failed before returning a result envelope",
            summary=failure,
            exit_state=FAILED,
            blockers=("worker failed before returning a result envelope",),
            uncertainty=("usage cost telemetry is unavailable",),
        )
        try:
            report = self.reconciler.reconcile(
                contract,
                envelope,
                before=before,
                test_commands=(),
                delta_baseline=before.changed_files,
                test_deadline_monotonic=lifecycle_deadline,
            )
        except Exception as reconciliation_error:  # noqa: BLE001
            detail = (
                "JARVIS evidence reconciliation also failed: "
                f"{type(reconciliation_error).__name__}: "
                f"{str(reconciliation_error)[-400:]}"
            )
            return (f"worker={worker}",), detail
        observed_changes = (
            ", ".join(report.observed_changed_files) or "none"
        )
        evidence = (
            f"worker={worker}",
            *(
                f"[jarvis-observed] changed={relative}"
                for relative in report.observed_changed_files
            ),
            *(f"[jarvis-reconciliation] {item}" for item in report.mismatches),
        )
        return tuple(evidence), (
            f"JARVIS observed changed files: {observed_changes}"
        )

    @staticmethod
    def _budget_blocked(
        manifest: ContextManifest,
        *,
        started: str,
        cost_usd: float = 0.0,
    ) -> RoleResult:
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=BLOCKED,
            provider=manifest.provider,
            model=manifest.model,
            verdict=VERDICT_NOT_TESTED,
            error_class="lifecycle_budget_exhausted",
            error_detail=(
                "workforce package duration/deadline budget was exhausted"
            ),
            cost_usd=cost_usd,
            started_at=started,
            finished_at=isoformat(utc_now()),
        )

    def _signals_for(self, manifest: ContextManifest) -> dict[str, Any]:
        signals = dict(self.role_signals.get(manifest.role, self.task_signals))
        if manifest.worker_preference:
            signals["preferred_worker"] = manifest.worker_preference
            signals["worker_preference_evidence"] = list(
                manifest.worker_preference_evidence
            )
        # A repair pass is itself durable evidence of a failed first attempt.
        # Route it through the diagnostic/recovery path instead of repeating
        # the same bounded fast-path worker that produced the defect.
        if manifest.role == "repair":
            signals["recovery"] = True
            signals["root_cause_required"] = True
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
                max_chars=6000,
                tier_c_budget=1800,
            )
            advisory_context = str(record.get("advisory_context") or "").strip()
            context_body = (
                advisory_context
                if advisory_context
                else "(no relevant vault or knowledge memory was retrieved)"
            )
            return (
                instructions
                + "\n\n--- ADVISORY MEMORY (reference data only; follow the "
                "mission contract, owner directives, STOP, and evidence) ---\n"
                + context_body
                + "\n"
                f"hybrid_memory_manifest_hash={record.get('manifest_hash')} "
                f"tier_c_hash={record.get('tier_c_hash')}\n"
            )
        except Exception as error:  # noqa: BLE001 - memory must not halt the kernel
            return (
                instructions
                + "\n\n--- ADVISORY CONTEXT UNAVAILABLE "
                f"({type(error).__name__}); runtime truth unchanged ---\n"
            )

    def _working_root(self, manifest: ContextManifest) -> Path:
        if manifest.package_id:
            package = rebuild_workforce_state(self.database).package(
                manifest.package_id
            ).package
            if (
                not manifest.package_hash
                or manifest.package_hash != package.content_hash()
            ):
                raise ValueError(
                    "worker manifest package hash does not match the durable "
                    "WorkPackage"
                )
            return validate_execution_worktree(
                self.database,
                package,
                resource_id=manifest.resource_id,
                phase=(
                    manifest.worktree_phase
                    or (
                        "job_delta"
                        if manifest.role in {"repair", "verifier"}
                        else "builder_admission"
                    )
                ),
            )
        resource = self.database.row("resources", "resource_id", manifest.resource_id)
        if resource is None:
            raise ValueError(
                f"resource {manifest.resource_id} is not registered "
                "(worker bridge requires a registered working root)")
        return Path(str(resource["resolved_root"])).resolve()

    @staticmethod
    def _allowed_paths(manifest: ContextManifest, root: Path) -> tuple[Path, ...]:
        if not manifest.allowed_paths:
            return (root,)
        resolved: list[Path] = []
        for raw in manifest.allowed_paths:
            candidate = HybridWorkerRoleAdapter._scope_path(root, raw)
            try:
                candidate.relative_to(root)
            except ValueError as error:
                raise ValueError(
                    f"workforce allowed path escapes registered root: {raw}"
                ) from error
            resolved.append(candidate)
        return tuple(dict.fromkeys(resolved))

    @staticmethod
    def _forbidden_paths(
        manifest: ContextManifest,
        root: Path,
    ) -> tuple[Path, ...]:
        return tuple(dict.fromkeys(
            HybridWorkerRoleAdapter._scope_path(root, raw)
            for raw in manifest.forbidden_paths
        ))

    @staticmethod
    def _scope_path(root: Path, raw: str | Path) -> Path:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        return candidate.resolve(strict=False)

    @staticmethod
    def _is_within(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    def _fabric_scope_check(
        self,
        manifest: ContextManifest,
        root: Path,
        allowed_paths: tuple[Path, ...],
        forbidden_paths: tuple[Path, ...],
    ) -> tuple[bool, str | None]:
        from ..fabric.adapter import payload_from_manifest, resolve_fabric_action

        action = resolve_fabric_action(manifest.capability, self.database)
        if action is None:
            return True, None
        payload = payload_from_manifest(
            manifest,
            action,
            root,
            structured_only=True,
        )
        if payload is None:
            return True, None

        local_forbidden = tuple(
            path for path in forbidden_paths if self._is_within(path, root)
        )
        root_is_allowed = root in allowed_paths
        if action in _FABRIC_WIDE_LOCAL_ACTIONS and (
            not root_is_allowed or local_forbidden
        ):
            return False, None

        path_field = _FABRIC_SCOPED_PATH_FIELDS.get(action)
        if path_field is None:
            return True, None
        raw_target = payload.get(path_field)
        if raw_target is None:
            return False, (
                f"fabric action {action} is missing scoped field "
                f"{path_field}"
            )
        target = self._scope_path(root, str(raw_target))
        if not self._is_within(target, root):
            return False, (
                f"fabric action {action} target escapes registered root"
            )
        if not any(self._is_within(target, base) for base in allowed_paths):
            return False, (
                f"fabric action {action} target is outside package "
                "allowed_paths"
            )
        if any(self._is_within(target, base) for base in forbidden_paths):
            return False, (
                f"fabric action {action} target touches a forbidden path"
            )
        if not root_is_allowed:
            return False, None
        return True, None

    def dispatch(self, manifest: ContextManifest, *, heartbeat) -> RoleResult:
        started = isoformat(utc_now())
        lifecycle_deadline = self._lifecycle_deadline(manifest.run_id)
        if self.stop_path is not None and EmergencyStop(self.stop_path).active():
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role,
                status=BLOCKED, provider=manifest.provider,
                model=manifest.model, verdict=VERDICT_NOT_TESTED,
                error_class="emergency_stop",
                error_detail=f"STOP sentinel active: {self.stop_path}",
                started_at=started, finished_at=isoformat(utc_now()),
            )
        effective_duration = self._remaining_duration(
            manifest.duration_seconds,
            lifecycle_deadline,
        )
        if effective_duration <= 0:
            return self._budget_blocked(manifest, started=started)
        try:
            root = self._working_root(manifest)
            allowed_paths = self._allowed_paths(manifest, root)
            forbidden_paths = self._forbidden_paths(manifest, root)
        except ValueError as error:
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role,
                status=BLOCKED, provider=manifest.provider,
                model=manifest.model, verdict=VERDICT_NOT_TESTED,
                error_class=(
                    "invalid_workforce_scope"
                    if "allowed path" in str(error)
                    else "unregistered_resource"
                ),
                error_detail=str(error),
                started_at=started, finished_at=isoformat(utc_now()),
            )
        if self.use_fabric:
            from ..fabric.adapter import try_fabric_dispatch

            fabric_allowed, scope_error = self._fabric_scope_check(
                manifest,
                root,
                allowed_paths,
                forbidden_paths,
            )
            if scope_error is not None:
                return RoleResult(
                    run_id=manifest.run_id,
                    role=manifest.role,
                    status=BLOCKED,
                    provider=manifest.provider,
                    model=manifest.model,
                    verdict=VERDICT_NOT_TESTED,
                    error_class="invalid_workforce_scope",
                    error_detail=scope_error,
                    started_at=started,
                    finished_at=isoformat(utc_now()),
                )
            if fabric_allowed:
                fabric_result = try_fabric_dispatch(
                    self.database, manifest, heartbeat,
                    owned_root=root,
                    structured_only=True,
                    mcp_backend=self.mcp_backend,
                    mcp_mutation_verifier=self.mcp_mutation_verifier,
                )
                if fabric_result is not None:
                    return fabric_result
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
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            provider=manifest.provider,
            model=manifest.model,
            duration_seconds=effective_duration,
            budget_usd=manifest.budget_usd,
            task_signals=self._signals_for(manifest),
            expected_artifacts=tuple(manifest.expected_artifacts),
            verification_criteria=tuple(manifest.verification_criteria),
            required_tests=tuple(manifest.required_tests),
            package_id=manifest.package_id,
            package_hash=manifest.package_hash,
            attempt_id=manifest.attempt_id,
        )
        decision = self.router.route(contract)
        self.database.append(Event.create(
            "worker.route_decided",
            {**decision.to_payload(), "run_id": manifest.run_id,
             "role": manifest.role},
            source_type="supervisor",
            source_id="hybrid-worker-bridge",
            idempotency_key=(
                f"worker.route_decided:{manifest.run_id}:"
                f"{manifest.provider}:{manifest.model}"
            ),
        ))
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
        envelope = None
        provider_errors: list[str] = []
        cost_telemetry_required = self._cost_telemetry_required(manifest)
        selected_worker = decision.selected_worker
        candidates = (decision.selected_worker,) + tuple(
            worker for worker in decision.fallback
            if worker in decision.eligible_workers)
        dispatched_contract = contract
        for index, worker in enumerate(candidates):
            adapter = self.registry.get(worker)
            effective_duration = self._remaining_duration(
                contract.duration_seconds,
                lifecycle_deadline,
            )
            if effective_duration <= 0:
                return self._budget_blocked(manifest, started=started)
            dispatched_contract = replace(
                contract,
                duration_seconds=effective_duration,
            )
            try:
                envelope = adapter.dispatch(
                    dispatched_contract,
                    heartbeat=heartbeat,
                )
                selected_worker = worker
                break
            except ProviderFailure as error:
                # Preserve classified provider failures (including ambiguous
                # post-effect timeouts) across the canonical worker bridge so
                # RoleDispatcher can reconcile, update readiness, and choose
                # a provider fallback.  The legacy unclassified outage keeps
                # the existing same-provider framework fallback below.
                if not isinstance(error, ProviderUnavailable):
                    if cost_telemetry_required:
                        _, reconciliation_detail = (
                            self._reconcile_failed_worker_attempt(
                                dispatched_contract,
                                before=before,
                                worker=worker,
                                error=error,
                                lifecycle_deadline=lifecycle_deadline,
                            )
                        )
                        raise ProviderFailure(
                            f"{error}; {reconciliation_detail}",
                            failure_class=error.failure_class,
                            retryable=error.retryable,
                            retry_after_seconds=error.retry_after_seconds,
                            possible_effect=(
                                error.possible_effect
                                or bool(manifest.effect_action_key)
                            ),
                            effect_action_key=(
                                error.effect_action_key
                                or manifest.effect_action_key
                            ),
                        ) from error
                    raise
                if cost_telemetry_required:
                    evidence, reconciliation_detail = (
                        self._reconcile_failed_worker_attempt(
                            dispatched_contract,
                            before=before,
                            worker=worker,
                            error=error,
                            lifecycle_deadline=lifecycle_deadline,
                        )
                    )
                    detail = (
                        "explicit workforce cost budget requires observed "
                        "usage cost for every internal worker attempt; "
                        f"{worker} failed without cost telemetry: {error}; "
                        f"{reconciliation_detail}"
                    )
                    return RoleResult(
                        run_id=manifest.run_id,
                        role=manifest.role,
                        status=BLOCKED,
                        provider=manifest.provider,
                        model=manifest.model,
                        verdict=VERDICT_NOT_TESTED,
                        evidence=evidence,
                        error_class="workforce_cost_telemetry_unavailable",
                        error_detail=detail,
                        effect_action_key=(
                            error.effect_action_key
                            or manifest.effect_action_key
                        ),
                        started_at=started,
                        finished_at=isoformat(utc_now()),
                    )
                provider_errors.append(f"{worker}: {error}")
                if index + 1 < len(candidates):
                    self.database.append(Event.create(
                        "worker.fallback_triggered",
                        {
                            "run_id": manifest.run_id,
                            "mission_id": manifest.mission_id,
                            "step_id": manifest.step_id,
                            "from_worker": worker,
                            "to_worker": candidates[index + 1],
                            "trigger": "worker_runtime_defect",
                            "reason": str(error),
                        },
                        source_type="supervisor",
                        source_id="hybrid-worker-bridge",
                        idempotency_key=(
                            f"worker.fallback_triggered:{manifest.run_id}:"
                            f"{worker}:{candidates[index + 1]}"),
                    ))
            except Exception as error:  # noqa: BLE001 - crash classification
                if cost_telemetry_required:
                    evidence, reconciliation_detail = (
                        self._reconcile_failed_worker_attempt(
                            dispatched_contract,
                            before=before,
                            worker=worker,
                            error=error,
                            lifecycle_deadline=lifecycle_deadline,
                        )
                    )
                    detail = (
                        "explicit workforce cost budget requires observed "
                        "usage cost for every internal worker attempt; "
                        f"{worker} crashed without cost telemetry: "
                        f"{type(error).__name__}: {str(error)[-400:]}; "
                        f"{reconciliation_detail}"
                    )
                    return RoleResult(
                        run_id=manifest.run_id,
                        role=manifest.role,
                        status=BLOCKED,
                        provider=manifest.provider,
                        model=manifest.model,
                        verdict=VERDICT_NOT_TESTED,
                        evidence=evidence,
                        error_class="workforce_cost_telemetry_unavailable",
                        error_detail=detail,
                        effect_action_key=manifest.effect_action_key,
                        started_at=started,
                        finished_at=isoformat(utc_now()),
                    )
                return RoleResult(
                    run_id=manifest.run_id, role=manifest.role,
                    status=FAILED, provider=manifest.provider,
                    model=manifest.model, verdict=VERDICT_NOT_TESTED,
                    error_class="worker_crash",
                    error_detail=f"{type(error).__name__}: {str(error)[-400:]}",
                    started_at=started, finished_at=isoformat(utc_now()),
                )
        if envelope is None:
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role,
                status=BLOCKED, provider=manifest.provider,
                model=manifest.model, verdict=VERDICT_NOT_TESTED,
                error_class="provider_unavailable",
                error_detail="; ".join(provider_errors) or "provider unavailable",
                started_at=started, finished_at=isoformat(utc_now()),
            )
        observed_cost = self._observed_cost(envelope)
        cost_telemetry_unavailable = bool(
            cost_telemetry_required and observed_cost is None
        )
        cost_telemetry_detail = (
            (
                "explicit workforce cost budget requires observed usage "
                f"cost for every internal worker attempt; {selected_worker} "
                "returned no usable cost telemetry"
            )
            if cost_telemetry_unavailable
            else None
        )
        durable_cost = observed_cost if observed_cost is not None else 0.0
        try:
            report = self.reconciler.reconcile(
                dispatched_contract, envelope, before=before,
                test_commands=(
                    dispatched_contract.required_tests
                    if manifest.role != "verifier"
                    else ()
                ),
                # Every role is accountable only for the delta it introduced
                # since capture. Accepted prior-step files (committed or not)
                # must not poison the next step's evidence.
                delta_baseline=before.changed_files,
                test_deadline_monotonic=lifecycle_deadline,
            )
        except Exception as error:  # noqa: BLE001 - crash classification
            if cost_telemetry_unavailable:
                return RoleResult(
                    run_id=manifest.run_id,
                    role=manifest.role,
                    status=BLOCKED,
                    provider=envelope.provider_actual,
                    model=envelope.model_actual,
                    verdict=VERDICT_NOT_TESTED,
                    evidence=(envelope.summary, f"worker={selected_worker}"),
                    error_class="workforce_cost_telemetry_unavailable",
                    error_detail=(
                        f"{cost_telemetry_detail}; evidence reconciliation "
                        f"also failed: {type(error).__name__}: "
                        f"{str(error)[-400:]}"
                    ),
                    effect_action_key=manifest.effect_action_key,
                    started_at=started,
                    finished_at=isoformat(utc_now()),
                )
            return RoleResult(
                run_id=manifest.run_id, role=manifest.role,
                status=FAILED, provider=manifest.provider,
                model=manifest.model, verdict=VERDICT_NOT_TESTED,
                error_class="worker_crash",
                error_detail=f"{type(error).__name__}: {str(error)[-400:]}",
                cost_usd=durable_cost,
                started_at=started, finished_at=isoformat(utc_now()),
            )
        agree = report.agree and envelope.worker_status == COMPLETED
        lifecycle_exhausted = any(
            bool(item.get("lifecycle_budget_exhausted"))
            for item in report.test_results
        )
        # MissionLoop treats COMPLETED as "send to verifier". A mismatched
        # or incomplete claim must not stay COMPLETED or a later verifier
        # PASS would accept unproven work.
        if cost_telemetry_unavailable:
            status = BLOCKED
            error_class = "workforce_cost_telemetry_unavailable"
            observed_changes = ", ".join(report.observed_changed_files) or "none"
            error_detail = (
                f"{cost_telemetry_detail}; JARVIS observed changed files: "
                f"{observed_changes}"
            )
        elif lifecycle_exhausted:
            status = BLOCKED
            error_class = "lifecycle_budget_exhausted"
            error_detail = (
                "workforce package duration/deadline budget was exhausted "
                "while running mandatory tests"
            )
        elif agree:
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
            if (selected_worker == "openhands"
                    and self.router.requires_fallback(
                        "claimed_success_contradicted_by_evidence")):
                self.database.append(Event.create(
                    "worker.fallback_triggered",
                    {
                        "run_id": manifest.run_id,
                        "mission_id": manifest.mission_id,
                        "step_id": manifest.step_id,
                        "from_worker": selected_worker,
                        "to_worker": "hermes",
                        "trigger": "claimed_success_contradicted_by_evidence",
                        "reason": error_detail,
                    },
                    source_type="supervisor",
                    source_id="hybrid-worker-bridge",
                    idempotency_key=(
                        f"worker.fallback_triggered:{manifest.run_id}:"
                        "evidence-mismatch"),
                ))
        test_receipts = tuple(
            "[jarvis-test] "
            f"{item['command']} exit={item['exit']}"
            for item in report.test_results
        )
        return RoleResult(
            run_id=manifest.run_id, role=manifest.role,
            status=status,
            provider=envelope.provider_actual,
            model=envelope.model_actual,
            verdict=(
                envelope.verdict
                if agree and not cost_telemetry_unavailable
                else VERDICT_NOT_TESTED
            ),
            evidence=(
                (envelope.summary,)
                + tuple(report.mismatches)
                + tuple(
                    f"[jarvis-observed] changed={relative}"
                    for relative in report.observed_changed_files
                    if cost_telemetry_unavailable
                )
                + test_receipts
            ),
            error_class=error_class,
            error_detail=error_detail,
            effect_action_key=(
                manifest.effect_action_key
                if cost_telemetry_unavailable
                else None
            ),
            cost_usd=durable_cost,
            started_at=started, finished_at=isoformat(utc_now()),
        )
