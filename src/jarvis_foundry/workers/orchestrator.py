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
)
from ..mission.verifier_binding import (
    AcceptanceGate,
    build_terminal_evidence,
    capture_binding,
    fresh_verifier_identity,
    VERIFIER_CONTRACT_VERSION,
    verifier_contract_digest,
    verifier_replay_state,
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
from .result_contract import result_instruction
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

    def _stop_verdict(self, contract: WorkerStepContract | None = None) -> str | None:
        """Returns a stop reason when STOP/veto forbids dispatch, else None."""
        if self.stop_path is not None and self.stop_path.exists():
            return "emergency stop sentinel present"
        if self.policy is not None:
            from ..control.broker import emergency_stop_reason

            identities = None
            if contract is not None:
                identities = {"GLOBAL", contract.mission_id}
                if contract.action_envelope is not None:
                    envelope = contract.action_envelope
                    if isinstance(envelope, dict):
                        envelope_values = (
                            envelope.get("action_id"),
                            envelope.get("resource_id"),
                            envelope.get("capability"),
                        )
                    else:
                        envelope_values = (
                            getattr(envelope, "action_id", None),
                            getattr(envelope, "resource_id", None),
                            getattr(envelope, "capability", None),
                        )
                    identities.update({
                        str(value) for value in envelope_values if value is not None
                    })
            return emergency_stop_reason(self.database, self.stop_path, identities=identities)
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
        before: BeforeState | None = None,
    ) -> tuple[str, str | None]:
        """Independent verification; returns (verdict, verdict_id).

        Master Goal 2: the verifier is a fresh isolated run with a distinct
        run identity; the verdict binding (mission, step, target, builder
        run, verifier run, context manifest, artifact hashes, diff hash) is
        frozen before the verifier session starts and every terminal
        outcome carries the hash-bound evidence set.
        """
        stop_reason = self._stop_verdict(contract)
        if stop_reason:
            self._record_stop_blocked(contract, stop_reason)
            return "STOPPED", None
        criteria = list(contract.verification_criteria) or [
            "worker outcome agrees with observed evidence",
        ]
        verifier_instructions = (
            f"Independently verify step {contract.step_id} of "
            f"{contract.mission_id}. Acceptance criteria: "
            f"{'; '.join(criteria)}. Verifier contract "
            f"{VERIFIER_CONTRACT_VERSION}; read-only inspection only: "
            f"inspect the frozen artifact "
            f"directly (worktree {contract.working_root}). The "
            ".jarvis-result.json marker is a control record only; do not "
            "treat its claims as independent proof. Reconstruct the "
            "acceptance facts from the artifact, repository and criteria "
            "available to this read-only session. End your "
            "response with a line: VERDICT: PASS | FAIL | BLOCKED | "
            "NOT_TESTED (NOT_TESTED when you cannot determine)."
        )
        verifier_context = {
            "mission_id": contract.mission_id,
            "step_id": contract.step_id,
            "target": str(contract.working_root.resolve()),
            "objective": contract.objective,
            "instructions": verifier_instructions,
            "criteria": criteria,
            "allowed_paths": [str(path.resolve()) for path in contract.allowed_paths],
            "forbidden_paths": [str(path.resolve()) for path in contract.forbidden_paths],
            "provider": envelope.provider_actual,
            "model": envelope.model_actual,
            "attempt": contract.attempt,
            "verifier_contract_version": VERIFIER_CONTRACT_VERSION,
            # Builder claims and the builder's reconciliation report are not
            # verifier input.  They remain in the durable binding/audit record
            # only, so a verifier can independently reconstruct the result.
            "before_state": {
                "head": before.head,
                "diff_hash": before.diff_hash,
                "changed_files": list(before.changed_files),
                "file_hashes": dict(before.file_hashes),
            } if before is not None else {},
        }
        contract_digest = verifier_contract_digest(verifier_context)
        reusable, revision = verifier_replay_state(
            self.database,
            mission_id=contract.mission_id,
            step_id=contract.step_id,
            builder_run_id=contract.run_id,
            contract_digest=contract_digest,
        )
        if reusable is not None:
            existing_verdict_id = str(reusable.get("verdict_id") or "")
            decision = AcceptanceGate(self.database, stop_path=self.stop_path).evaluate(
                mission_id=contract.mission_id,
                step_id=contract.step_id,
                target=contract.working_root,
                builder_run_id=envelope.run_id,
            )
            if decision.accepted:
                return "PASS", existing_verdict_id
            # A stale/fabricated historical PASS is not an authorization, but
            # it also must not wedge the mission at FAIL.  Re-open a fresh,
            # content-addressed verifier session so the current release can
            # re-evaluate the live evidence.  STOP/veto remain terminal
            # controls and are never bypassed by this recovery path.
            if any(key in decision.checks for key in ("stop_active", "veto_active")):
                return "STOPPED", existing_verdict_id
            reusable = None
            revision = max(int(revision) + 1, 2)
        verdict_id, verifier_run_id = fresh_verifier_identity(
            contract.run_id,
            attempt=contract.attempt,
            identity_context=verifier_context,
            base_verifier_run_id=f"{contract.run_id}-verify",
            revision=revision,
        )
        independence = {
            "builder_worker": envelope.framework,
            "builder_provider": envelope.provider_actual,
            "verifier_provider": envelope.provider_actual,
            "provider_diverse": False,
            "session_independent": True,
            "provider_independent": False,
            "same_provider_limitation": (
                "same-provider verification: reduced provider independence "
                "(owner model-lock mandate); isolation enforced through a "
                "fresh verifier session with distinct run identity"),
            "session_isolation": "fresh-session",
        }
        capture = capture_binding(
            self.database,
            verdict_id=verdict_id,
            mission_id=contract.mission_id,
            step_id=contract.step_id,
            target=contract.working_root,
            builder_run_id=envelope.run_id,
            verifier_run_id=verifier_run_id,
            context_manifest_id=contract.context_manifest_id,
            context_manifest_hash=contract.context_manifest_hash,
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
            instructions=verifier_instructions,
            working_root=contract.working_root,
            allowed_paths=contract.allowed_paths,
            forbidden_paths=contract.forbidden_paths,
            context_manifest_id=contract.context_manifest_id,
            context_manifest_hash=contract.context_manifest_hash,
            # Do not share the builder's mutable action envelope with the
            # verifier session; verification is read-only and contract-only.
            action_envelope={},
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
        except Exception as error:  # noqa: BLE001 - verifier crash (Master Goal 2)
            # A verifier that dies mid-review records NO PASS and NO FAIL:
            # an interrupted verification is an honest BLOCKED against the
            # frozen binding, resumable by a later fresh pass.
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome="BLOCKED",
                findings=[], evidence={},
                reason=(f"verifier crashed mid-review: "
                        f"{type(error).__name__}: {str(error)[-300:]}"),
                blocking_dependency="verifier_crashed")
            return "BLOCKED", verdict_id
        terminal_evidence = build_terminal_evidence(
            capture=capture,
            verifier_session_id=getattr(result, "session_id", "") or verifier_run_id,
        )
        self._record_envelope(verifier_contract, result)
        if result.worker_status != COMPLETED or result.verdict != VERDICT_PASS:
            outcome = "FAIL" if result.worker_status == COMPLETED else "BLOCKED"
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome=outcome,
                findings=list(result.blockers) or list(result.warnings) or [],
                evidence=terminal_evidence,
                repair_requirements=["rebuild the step artifact and re-verify"],
            )
            return result.verdict or ("BLOCKED" if outcome == "BLOCKED" else "FAIL"), verdict_id
        record_verification_outcome(
            self.database, verdict_id=verdict_id, outcome="PASS",
            findings=[], evidence=terminal_evidence,
        )
        # Master Goal 2: fail-closed acceptance gate before any advance.
        gate = AcceptanceGate(self.database, stop_path=self.stop_path)
        decision = gate.evaluate(
            mission_id=contract.mission_id, step_id=contract.step_id,
            target=contract.working_root, builder_run_id=envelope.run_id,
        )
        if not decision.accepted:
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome="FAIL",
                findings=[decision.reason],
                evidence=build_terminal_evidence(
                    capture=capture,
                    verifier_session_id=getattr(result, "session_id", "") or verifier_run_id,
                    extra={"acceptance_gate": decision.checks},
                ),
                repair_requirements=[
                    "repair the acceptance-gate refusal and re-verify with a fresh verifier",
                ],
                reason=f"acceptance gate refused: {decision.reason}",
            )
            return "FAIL", verdict_id
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
        enriched = result_instruction(
            contract.instructions, working_root=contract.working_root)
        if enriched != contract.instructions:
            contract = WorkerStepContract(
                run_id=contract.run_id, mission_id=contract.mission_id,
                step_id=contract.step_id, attempt=contract.attempt,
                role=contract.role, objective=contract.objective,
                instructions=enriched,
                working_root=contract.working_root,
                allowed_paths=contract.allowed_paths,
                forbidden_paths=contract.forbidden_paths,
                context_manifest_id=contract.context_manifest_id,
                context_manifest_hash=contract.context_manifest_hash,
                action_envelope=contract.action_envelope,
                provider=contract.provider, model=contract.model,
                duration_seconds=contract.duration_seconds,
                budget_usd=contract.budget_usd,
                task_signals=contract.task_signals,
                expected_artifacts=contract.expected_artifacts,
                verification_criteria=contract.verification_criteria,
            )
        stop_reason = self._stop_verdict(contract)
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
            verdict, verdict_id = self._verify(contract, envelope, report, before)
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
        verdict, verdict_id = self._verify(contract, envelope, report, before)
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
            stop_reason = self._stop_verdict(original)
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
            verdict, verdict_id = self._verify(
                repair_contract, repair_envelope, repair_report, repair_before)
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
