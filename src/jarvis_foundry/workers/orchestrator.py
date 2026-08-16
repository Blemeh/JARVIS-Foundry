"""Hybrid worker control loop (JARVIS owns its workforce).

``HybridStepController.run_step`` proves the full §15/§16 flows without
owner prompting:

1. STOP/veto check (no dispatch while STOP is active);
2. deterministic route decision (recorded ``worker.route_decided``);
3. before-state capture;
4. dispatch the selected worker through the adapter boundary;
5. record the normalized envelope (``worker.run_recorded``);
6. independent evidence reconciliation (``worker.evidence_reconciled``) —
   a worker claim that disagrees with observed reality is NO PASS;
7. independent verification (fresh verifier worker, verdict recorded via
   the canonical verification ledger);
8. PASS -> accept + commit per policy -> ACTION_SELECTION (next action);
9. evidence mismatch / FAIL -> NO PASS -> verifier confirms defect ->
   OpenHands -> Hermes escalation (repair attempt) -> re-reconcile ->
   fresh verifier -> PASS -> next action; repair budget exhausted ->
   honest BLOCKED.

STOP semantics are never weakened for worker convenience: STOP blocks new
dispatch, cancels queued fallback, and records the refusal.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..clock import isoformat, utc_now
from ..control.policy import ActionPolicy
from ..database import FoundryDatabase
from ..domain import require_transition
from ..events import Event
from ..mission.verification import (
    record_verification_outcome,
    start_verification,
)
from .adapters.base import ProviderUnavailable, WorkerAdapter
from .adapters.registry import WorkerRegistry
from .contracts import WORKER_HERMES, WORKER_OPENHANDS, WorkerStepContract
from .envelope import (
    COMPLETED,
    VERDICT_PASS,
    ResultEnvelope,
)
from .evidence import BeforeState, EvidenceReport, EvidenceReconciler
from .router import RouteDecision, WorkerRouter

PASSED = "PASSED"
FAILED = "FAILED"
BLOCKED_OUTCOME = "BLOCKED"
STOPPED_OUTCOME = "STOPPED"


@dataclass(frozen=True, slots=True)
class HybridStepOutcome:
    outcome: str
    step_id: str | None = None
    run_id: str | None = None
    route_decision: RouteDecision | None = None
    envelope: ResultEnvelope | None = None
    evidence: EvidenceReport | None = None
    repair_run_id: str | None = None
    verdict_id: str | None = None
    commit_head: str | None = None
    reason: str | None = None


class HybridStepController:
    def __init__(
        self,
        *,
        database: FoundryDatabase,
        router: WorkerRouter,
        registry: WorkerRegistry,
        policy: ActionPolicy | None = None,
        stop_path: Path | None = None,
        reconciler: EvidenceReconciler | None = None,
        verifier_factory: Callable[..., ResultEnvelope] | None = None,
        max_repairs: int = 2,
        source_id: str = "hybrid-worker-controller",
    ) -> None:
        self.database = database
        self.router = router
        self.registry = registry
        self.policy = policy
        self.stop_path = stop_path.resolve() if stop_path is not None else None
        self.reconciler = reconciler or EvidenceReconciler()
        self.verifier_factory = verifier_factory
        self.max_repairs = max_repairs
        self.source_id = source_id
        self._heartbeat_count: dict[str, int] = {}

    # -- durable events -----------------------------------------------------

    def _append(self, event_type: str, payload: dict[str, Any],
                *, idempotency_key: str) -> None:
        event = Event.create(
            event_type, payload,
            source_type="supervisor", source_id=self.source_id,
            idempotency_key=idempotency_key,
        )
        self.database.append(event)

    def _heartbeat(self, run_id: str) -> None:
        self._heartbeat_count[run_id] = self._heartbeat_count.get(run_id, 0) + 1
        self._append(
            "worker.heartbeat", {"run_id": run_id},
            idempotency_key=f"worker.heartbeat:{run_id}:{self._heartbeat_count[run_id]}",
        )

    def _advance(self, mission_id: str, state: str, *,
                 run_id: str | None = None, next_action: str | None = None) -> None:
        """Durable mission.state_changed through the canonical state machine."""
        row = self.database.row("missions", "mission_id", mission_id)
        if row is None:
            return
        current = str(row["state"])
        if current == state:
            return
        require_transition(current, state)
        if next_action is None:
            next_action = row["next_action"]
        key = f"mission.state_changed:{mission_id}:{state}:{next_action or 'none'}"
        if run_id is not None:
            key = f"{key}:run-{run_id}"
        event = Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": next_action},
            source_type="supervisor", source_id=self.source_id,
            idempotency_key=key,
        )
        self.database.append(event)

    # -- STOP gate ----------------------------------------------------------

    def _stop_verdict(self) -> str | None:
        """Returns a stop reason when STOP/veto forbids dispatch, else None."""
        if self.stop_path is not None and self.stop_path.exists():
            return "emergency stop sentinel present"
        if self.policy is not None:
            with self.database.connect() as connection:
                paused = connection.execute(
                    "SELECT directive_id FROM directives WHERE active = 1 "
                    "AND command_type = 'PAUSE' ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
                if paused:
                    return f"owner pause directive {paused[0]} is active"
                stopped = connection.execute(
                    "SELECT directive_id FROM directives WHERE active = 1 "
                    "AND command_type = 'STOP_EVERYTHING' ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
                if stopped:
                    return f"owner stop directive {stopped[0]} is active"
        return None

    def _record_stop_blocked(self, contract: WorkerStepContract, reason: str) -> None:
        self._append(
            "worker.stop_blocked",
            {"run_id": contract.run_id, "mission_id": contract.mission_id,
             "step_id": contract.step_id, "framework": "",
             "reason": reason},
            idempotency_key=f"worker.stop_blocked:{contract.run_id}",
        )

    # -- route / dispatch / evidence ---------------------------------------

    def _record_route(self, contract: WorkerStepContract,
                      decision: RouteDecision) -> None:
        self._append(
            "worker.route_decided",
            {"contract_hash": contract.content_hash(),
             **decision.to_payload()},
            idempotency_key=f"worker.route_decided:{contract.run_id}",
        )

    def _record_envelope(self, contract: WorkerStepContract,
                         envelope: ResultEnvelope) -> None:
        self._append(
            "worker.run_recorded",
            {"contract_hash": contract.content_hash(),
             "envelope": envelope.to_payload()},
            idempotency_key=f"worker.run_recorded:{envelope.run_id}",
        )

    def _record_evidence(self, envelope: ResultEnvelope,
                         report: EvidenceReport) -> None:
        self._append(
            "worker.evidence_reconciled",
            {"run_id": envelope.run_id, "agree": report.agree,
             "mismatches": list(report.mismatches),
             "containment_violations": list(report.containment_violations),
             "unrecorded_mutations": list(report.unrecorded_mutations)},
            idempotency_key=f"worker.evidence_reconciled:{envelope.run_id}",
        )

    def _record_fallback(self, envelope: ResultEnvelope, trigger: str) -> None:
        self._append(
            "worker.fallback_triggered",
            {"run_id": envelope.run_id, "mission_id": envelope.mission_id,
             "step_id": envelope.step_id,
             "from_worker": envelope.framework,
             "to_worker": WORKER_HERMES, "trigger": trigger},
            idempotency_key=f"worker.fallback_triggered:{envelope.run_id}:{trigger}",
        )

    @staticmethod
    def _capture_partial_state(
        contract: WorkerStepContract,
        before: BeforeState,
    ) -> dict[str, Any]:
        """Snapshot the worktree after a crash so partial mutations are
        visible in evidence (Path D). Never claims completion."""
        try:
            from .evidence import git_changed_files, git_diff_hash
            return {
                "changed_files": list(git_changed_files(contract.working_root)),
                "diff_hash": git_diff_hash(contract.working_root),
                "head": before.head,
            }
        except Exception as error:  # noqa: BLE001
            return {"capture_error": str(error)[-300:]}

    # -- verifier -----------------------------------------------------------

    def _verify(
        self,
        contract: WorkerStepContract,
        envelope: ResultEnvelope,
        report: EvidenceReport,
    ) -> tuple[str, str | None]:
        """Independent verification; returns (verdict, verdict_id)."""
        stop_reason = self._stop_verdict()
        if stop_reason:
            self._record_stop_blocked(contract, stop_reason)
            return "STOPPED", None
        verdict_id = f"verdict-{uuid.uuid4().hex[:12]}"
        verifier_run_id = f"{envelope.run_id}-verify"
        independence = {
            "builder_worker": envelope.framework,
            "builder_provider": envelope.provider_actual,
            "verifier_provider": envelope.provider_actual,
            "provider_diverse": False,
            "same_provider_limitation": (
                "verifier ran under the same provider/model family as the "
                "builder (owner DeepSeek-only mandate); independence is "
                "delivered through fresh sessions + adversarial probes"),
        }
        criteria = list(contract.verification_criteria) or [
            "worker outcome agrees with observed evidence",
        ]
        start_verification(
            self.database,
            verdict_id=verdict_id,
            mission_id=contract.mission_id,
            step_id=contract.step_id,
            builder_run_id=envelope.run_id,
            verifier_run_id=verifier_run_id,
            independence=independence,
            criteria=criteria,
        )

        verifier_contract = WorkerStepContract(
            run_id=verifier_run_id,
            mission_id=contract.mission_id,
            step_id=contract.step_id,
            attempt=contract.attempt,
            role="verifier",
            objective=contract.objective,
            instructions=(
                f"Independently verify step {contract.step_id} of "
                f"{contract.mission_id}. Acceptance criteria: "
                f"{'; '.join(criteria)}. Inspect the frozen artifact "
                f"directly (worktree {contract.working_root}). Worker "
                f"claim: {envelope.claimed_outcome or 'none'}. Evidence "
                f"mismatches reported by JARVIS: "
                f"{'; '.join(report.mismatches) or 'none'}. End your "
                "response with a line: VERDICT: PASS | FAIL | BLOCKED | "
                "NOT_TESTED (NOT_TESTED when you cannot determine)."
            ),
            working_root=contract.working_root,
            allowed_paths=contract.allowed_paths,
            forbidden_paths=contract.forbidden_paths,
            context_manifest_id=contract.context_manifest_id,
            context_manifest_hash=contract.context_manifest_hash,
            action_envelope=contract.action_envelope,
            provider=contract.provider,
            model=contract.model,
            duration_seconds=contract.duration_seconds,
            budget_usd=contract.budget_usd,
            task_signals={"bounded": True, "explicit_acceptance": True,
                          "tests_available": True, "narrow_scope": True},
            verification_criteria=tuple(criteria),
        )
        try:
            if self.verifier_factory is not None:
                result = self.verifier_factory(
                    verifier_contract, envelope=envelope, report=report)
            else:
                adapter = self.registry.get(WORKER_HERMES)
                result = adapter.dispatch(
                    verifier_contract, heartbeat=lambda: self._heartbeat(verifier_run_id))
        except ProviderUnavailable as error:
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome="BLOCKED",
                findings=[], evidence={},
                reason=f"verifier unavailable: {error}",
                blocking_dependency="provider_unavailable")
            return "BLOCKED", verdict_id
        self._record_envelope(verifier_contract, result)
        if result.worker_status != COMPLETED or result.verdict != VERDICT_PASS:
            outcome = "FAIL" if result.worker_status == COMPLETED else "BLOCKED"
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome=outcome,
                findings=list(result.blockers) or list(result.warnings) or [],
                evidence={"verifier_run_id": verifier_run_id,
                          "verdict": result.verdict},
                repair_requirements=["rebuild the step artifact and re-verify"],
            )
            return result.verdict or ("BLOCKED" if outcome == "BLOCKED" else "FAIL"), verdict_id
        record_verification_outcome(
            self.database, verdict_id=verdict_id, outcome="PASS",
            findings=[], evidence={"verifier_run_id": verifier_run_id},
        )
        return "PASS", verdict_id

    # -- main flow ----------------------------------------------------------

    def run_step(
        self,
        contract: WorkerStepContract,
        *,
        test_commands: tuple[str, ...] = (),
        expected_artifact_hashes: dict[str, str] | None = None,
        persistence_scan_roots: tuple[Path, ...] = (),
    ) -> HybridStepOutcome:
        stop_reason = self._stop_verdict()
        if stop_reason:
            self._record_stop_blocked(contract, stop_reason)
            return HybridStepOutcome(
                outcome=STOPPED_OUTCOME, step_id=contract.step_id,
                run_id=contract.run_id, reason=stop_reason)

        decision = self.router.route(contract)
        self._record_route(contract, decision)
        if not decision.selected_worker:
            return HybridStepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=contract.step_id,
                run_id=contract.run_id, route_decision=decision,
                reason=decision.reason)

        self._advance(contract.mission_id, "READY", run_id=contract.run_id)
        self._advance(contract.mission_id, "EXECUTING", run_id=contract.run_id)
        before = self.reconciler.capture_before(contract)
        adapter = self.registry.get(decision.selected_worker)
        try:
            envelope = adapter.dispatch(
                contract, heartbeat=lambda: self._heartbeat(contract.run_id))
        except ProviderUnavailable as error:
            self._advance(contract.mission_id, "BLOCKED", run_id=contract.run_id)
            return HybridStepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=contract.step_id,
                run_id=contract.run_id, route_decision=decision,
                reason=f"{decision.selected_worker} provider unavailable: {error}")
        except Exception as error:  # noqa: BLE001 - worker crash (Path D)
            # A worker that dies mid-mutation leaves partial observable
            # state. Capture it, record it durably, and classify the run as
            # FAILED so the mission repair path can safely retry — never
            # invent success from a crash.
            partial = self._capture_partial_state(contract, before)
            self._append(
                "worker.crashed",
                {"run_id": contract.run_id,
                 "mission_id": contract.mission_id,
                 "step_id": contract.step_id,
                 "framework": decision.selected_worker,
                 "error_class": type(error).__name__,
                 "error_detail": str(error)[-500:],
                 "partial_state": partial},
                idempotency_key=f"worker.crashed:{contract.run_id}",
            )
            if decision.selected_worker == WORKER_OPENHANDS and self.router.is_eligible(WORKER_HERMES):
                return self._run_repair(
                    contract, decision, None, None, None,
                    test_commands=test_commands,
                    expected_artifact_hashes=expected_artifact_hashes,
                    persistence_scan_roots=persistence_scan_roots,
                    crash_error=str(error),
                )
            self._advance(contract.mission_id, "BLOCKED", run_id=contract.run_id)
            return HybridStepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=contract.step_id,
                run_id=contract.run_id, route_decision=decision,
                reason=("worker crashed before completion: "
                        f"{type(error).__name__}: {str(error)[-300:]}"))
        self._record_envelope(contract, envelope)
        self._advance(contract.mission_id, "VERIFYING", run_id=contract.run_id)

        report = self.reconciler.reconcile(
            contract, envelope, before=before,
            test_commands=test_commands,
            expected_artifact_hashes=expected_artifact_hashes,
            persistence_scan_roots=persistence_scan_roots,
        )
        self._record_evidence(envelope, report)

        clean = (
            report.agree
            and envelope.worker_status == COMPLETED
            and envelope.verdict == VERDICT_PASS
        )
        if clean:
            verdict, verdict_id = self._verify(contract, envelope, report)
            if verdict == "STOPPED":
                return HybridStepOutcome(
                    outcome=STOPPED_OUTCOME, step_id=contract.step_id,
                    run_id=contract.run_id, route_decision=decision,
                    envelope=envelope, evidence=report,
                    reason="STOP active; verification cancelled")
            if verdict == "PASS":
                self._advance_to_next_action(contract)
                return HybridStepOutcome(
                    outcome=PASSED, step_id=contract.step_id,
                    run_id=contract.run_id, route_decision=decision,
                    envelope=envelope, evidence=report, verdict_id=verdict_id,
                )
            # The independent verifier refused PASS: the worker does not
            # certify itself. Escalate to a Hermes repair when possible;
            # otherwise the mission blocks and the run is FAILED.
            self._record_fallback(envelope, "verifier_failed")
            if not self.router.is_eligible(WORKER_HERMES):
                self._advance(contract.mission_id, "BLOCKED", run_id=contract.run_id)
                return HybridStepOutcome(
                    outcome=FAILED, step_id=contract.step_id,
                    run_id=contract.run_id, route_decision=decision,
                    envelope=envelope, evidence=report, verdict_id=verdict_id,
                    reason=f"independent verifier did not pass: {verdict}")
            return self._run_repair(
                contract, decision, envelope, report, verdict_id,
                test_commands=test_commands,
                expected_artifact_hashes=expected_artifact_hashes,
                persistence_scan_roots=persistence_scan_roots,
            )

        # NO PASS: evidence mismatch or worker failure. Confirm the defect
        # with a verifier, then escalate (OpenHands -> Hermes repair).
        trigger = (
            "claimed_success_contradicted_by_evidence"
            if report.agree is False and envelope.worker_status == COMPLETED
            else "worker_runtime_defect"
        )
        if report.containment_violations:
            trigger = "scope_violation"
        if report.unrecorded_mutations:
            trigger = "claimed_success_contradicted_by_evidence"
        self._record_fallback(envelope, trigger)
        verdict, verdict_id = self._verify(contract, envelope, report)
        if verdict == "STOPPED":
            return HybridStepOutcome(
                outcome=STOPPED_OUTCOME, step_id=contract.step_id,
                run_id=contract.run_id, route_decision=decision,
                envelope=envelope, evidence=report,
                reason="STOP active; verification cancelled")
        if decision.selected_worker != WORKER_OPENHANDS or not self.router.is_eligible(WORKER_HERMES):
            self._advance(contract.mission_id, "BLOCKED", run_id=contract.run_id)
            return HybridStepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=contract.step_id,
                run_id=contract.run_id, route_decision=decision,
                envelope=envelope, evidence=report, verdict_id=verdict_id,
                reason=("worker failed and no eligible repair path exists"
                        if verdict != "PASS"
                        else "evidence reconciliation did not fully agree; "
                             "refusing acceptance despite verifier PASS"),
            )
        return self._run_repair(
            contract, decision, envelope, report, verdict_id,
            test_commands=test_commands,
            expected_artifact_hashes=expected_artifact_hashes,
            persistence_scan_roots=persistence_scan_roots,
        )

    def _run_repair(
        self,
        original: WorkerStepContract,
        decision: RouteDecision,
        failed_envelope: ResultEnvelope | None,
        failed_report: EvidenceReport | None,
        failed_verdict_id: str | None,
        *,
        test_commands: tuple[str, ...],
        expected_artifact_hashes: dict[str, str] | None,
        persistence_scan_roots: tuple[Path, ...],
        crash_error: str | None = None,
    ) -> HybridStepOutcome:
        """Hermes repair attempt(s) after an OpenHands failure/mismatch."""
        repair_run = 0
        last_outcome: HybridStepOutcome | None = None
        while repair_run < self.max_repairs:
            stop_reason = self._stop_verdict()
            if stop_reason:
                self._record_stop_blocked(original, stop_reason)
                self._advance(original.mission_id, "BLOCKED", run_id=original.run_id)
                return HybridStepOutcome(
                    outcome=STOPPED_OUTCOME, step_id=original.step_id,
                    run_id=original.run_id, reason=stop_reason)
            repair_run += 1
            repair_run_id = f"{original.run_id}-repair-r{repair_run}"
            self._advance(original.mission_id, "REPAIRING", run_id=repair_run_id)
            self._advance(original.mission_id, "READY", run_id=repair_run_id)
            self._advance(original.mission_id, "EXECUTING", run_id=repair_run_id)
            repair_contract = WorkerStepContract(
                run_id=repair_run_id,
                mission_id=original.mission_id,
                step_id=original.step_id,
                attempt=repair_run,
                role="repair",
                objective=original.objective,
                instructions=(
                    f"Repair step {original.step_id} of {original.mission_id} "
                    f"after an OpenHands attempt failed."
                    + (
                        f" Defect confirmed: "
                        f"{'; '.join(failed_report.mismatches) if failed_report else 'unknown'}."
                        if not crash_error else
                        f" Worker crashed mid-run: {crash_error[:400]}."
                    )
                    + " Restore root-cause correctness; run the public tests; "
                    "do not patch the symptom. Original instructions: "
                    f"{original.instructions}"
                ),
                working_root=original.working_root,
                allowed_paths=original.allowed_paths,
                forbidden_paths=original.forbidden_paths,
                context_manifest_id=original.context_manifest_id,
                context_manifest_hash=original.context_manifest_hash,
                action_envelope=original.action_envelope,
                provider=original.provider,
                model=original.model,
                duration_seconds=original.duration_seconds,
                budget_usd=original.budget_usd,
                task_signals={"bounded": True, "explicit_acceptance": True,
                              "tests_available": True, "narrow_scope": True,
                              "root_cause_required": True},
                expected_artifacts=original.expected_artifacts,
                verification_criteria=original.verification_criteria,
            )
            repair_before = self.reconciler.capture_before(repair_contract)
            adapter = self.registry.get(WORKER_HERMES)
            try:
                repair_envelope = adapter.dispatch(
                    repair_contract,
                    heartbeat=lambda: self._heartbeat(repair_run_id))
            except ProviderUnavailable as error:
                self._advance(original.mission_id, "BLOCKED", run_id=repair_run_id)
                return HybridStepOutcome(
                    outcome=BLOCKED_OUTCOME, step_id=original.step_id,
                    run_id=original.run_id, route_decision=decision,
                    envelope=failed_envelope, evidence=failed_report,
                    repair_run_id=repair_run_id, verdict_id=failed_verdict_id,
                    reason=f"repair worker unavailable: {error}")
            except Exception as error:  # noqa: BLE001 - repair crash (Path D)
                partial = self._capture_partial_state(repair_contract, repair_before)
                self._append(
                    "worker.crashed",
                    {"run_id": repair_run_id,
                     "mission_id": original.mission_id,
                     "step_id": original.step_id,
                     "framework": WORKER_HERMES,
                     "error_class": type(error).__name__,
                     "error_detail": str(error)[-500:],
                     "partial_state": partial},
                    idempotency_key=f"worker.crashed:{repair_run_id}",
                )
                last_outcome = HybridStepOutcome(
                    outcome=FAILED, step_id=original.step_id,
                    run_id=original.run_id, route_decision=decision,
                    repair_run_id=repair_run_id, verdict_id=failed_verdict_id,
                    reason=f"repair worker crashed: {type(error).__name__}: {str(error)[-300:]}")
                continue
            self._record_envelope(repair_contract, repair_envelope)
            self._advance(original.mission_id, "VERIFYING", run_id=repair_run_id)
            repair_report = self.reconciler.reconcile(
                repair_contract, repair_envelope, before=repair_before,
                test_commands=test_commands,
                expected_artifact_hashes=expected_artifact_hashes,
                persistence_scan_roots=persistence_scan_roots,
            )
            self._record_evidence(repair_envelope, repair_report)
            if not repair_report.agree or repair_envelope.worker_status != COMPLETED:
                last_outcome = HybridStepOutcome(
                    outcome=FAILED, step_id=original.step_id,
                    run_id=original.run_id, route_decision=decision,
                    envelope=repair_envelope, evidence=repair_report,
                    repair_run_id=repair_run_id, verdict_id=failed_verdict_id,
                    reason="; ".join(repair_report.mismatches) or "repair evidence mismatch")
                continue
            verdict, verdict_id = self._verify(repair_contract, repair_envelope, repair_report)
            if verdict == "STOPPED":
                return HybridStepOutcome(
                    outcome=STOPPED_OUTCOME, step_id=original.step_id,
                    run_id=original.run_id, route_decision=decision,
                    envelope=repair_envelope, evidence=repair_report,
                    repair_run_id=repair_run_id,
                    reason="STOP active; verification cancelled")
            if verdict == "PASS":
                self._advance_to_next_action(original)
                return HybridStepOutcome(
                    outcome=PASSED, step_id=original.step_id,
                    run_id=repair_run_id, route_decision=decision,
                    envelope=repair_envelope, evidence=repair_report,
                    repair_run_id=repair_run_id, verdict_id=verdict_id,
                )
            last_outcome = HybridStepOutcome(
                outcome=FAILED, step_id=original.step_id,
                run_id=original.run_id, route_decision=decision,
                envelope=repair_envelope, evidence=repair_report,
                repair_run_id=repair_run_id, verdict_id=verdict_id,
                reason=f"repair verifier did not pass: {verdict}")
        self._advance(original.mission_id, "BLOCKED", run_id=repair_run_id or original.run_id)
        return HybridStepOutcome(
            outcome=BLOCKED_OUTCOME, step_id=original.step_id,
            run_id=original.run_id, route_decision=decision,
            envelope=failed_envelope, evidence=failed_report,
            repair_run_id=last_outcome.repair_run_id if last_outcome else None,
            verdict_id=last_outcome.verdict_id if last_outcome else failed_verdict_id,
            reason=f"repair budget exhausted ({self.max_repairs} attempts)",
        )

    def _advance_to_next_action(self, contract: WorkerStepContract) -> None:
        """Durable mission advance to ACTION_SELECTION (next action)."""
        self._advance(contract.mission_id, "ACCEPTING", run_id=contract.run_id)
        self._advance(contract.mission_id, "ACTION_SELECTION", run_id=contract.run_id,
                      next_action=None)
