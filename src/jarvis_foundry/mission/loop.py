"""Autonomous mission loop (WP5).

``MissionLoop`` runs one mission step end-to-end without owner prompting:

1. propose the next action deterministically (durable mission state only);
2. advance the mission state machine (READY -> EXECUTING);
3. dispatch the builder (or the repair role on a repair attempt) through the
   WP4 role adapter;
4. intake the structured result and advance to VERIFYING;
5. dispatch an independent verifier (provider-diverse when possible),
   recording the verdict with full independence metadata;
6. PASS -> ACCEPTING -> local commit (only after verified PASS) -> evidence
   bundle -> ACTION_SELECTION; FAIL -> bounded repair (REPAIRING) or BLOCKED
   with a precise reason when the repair budget is exhausted;
7. honest waiting when no next action is scheduled (no invented work).

Every transition and verdict is a durable event; the loop is deterministic
and re-runnable (idempotent events, ledger replay in the dispatcher).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..control.policy import ActionPolicy
from ..database import FoundryDatabase
from ..domain import ActionEnvelope
from ..events import Event
from ..roles.adapter import RoleAdapter
from ..roles.dispatcher import RoleDispatcher
from ..roles.results import COMPLETED
from ..roles.router import RoleRouter
from ..supervisor.leases import LeaseManager
from .commit import CommitError, commit_after_pass
from .proposal import Proposal, ProposalError, Waiting, propose_next_action
from .verification import (
    dangling_verdict_for_run,
    failed_verdict_count,
    latest_verdict_for_step,
    record_verification_outcome,
    start_verification,
)

PASSED = "PASSED"
FAILED = "FAILED"
BLOCKED_OUTCOME = "BLOCKED"
WAITING_OUTCOME = "WAITING"


def _hash_target(target: Path, *, limit: int = 50) -> dict[str, str]:
    """sha256 of every file under the step target (bounded) — binds the
    verified verdict to the frozen artifact hashes (DIR-2026-08-12 item 1).
    Paths are relative, forward-slash normalized; unreadable files are
    skipped (their absence from the map is visible in the bundle)."""
    hashes: dict[str, str] = {}
    if not target.is_dir():
        return hashes
    import hashlib
    import itertools
    for path in itertools.islice(target.rglob("*"), limit * 8):
        if not path.is_file():
            continue
        rel = str(path.relative_to(target)).replace("\\", "/")
        try:
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if len(hashes) >= limit:
            break
    return hashes


@dataclass(frozen=True, slots=True)
class StepOutcome:
    outcome: str  # PASSED | FAILED | BLOCKED | WAITING
    step_id: str | None = None
    run_id: str | None = None
    verdict_id: str | None = None
    commit_head: str | None = None
    reason: str | None = None


class MissionLoop:
    def __init__(
        self,
        database: FoundryDatabase,
        router: RoleRouter,
        adapter: RoleAdapter,
        *,
        leases: LeaseManager | None = None,
        policy: ActionPolicy | None = None,
        max_repairs: int = 2,
        source_id: str = "mission-loop",
        heartbeat_interval_seconds: int = 60,
    ) -> None:
        self.database = database
        self.router = router
        self.dispatcher = RoleDispatcher(
            database, router, adapter,
            leases=leases, source_id=source_id,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        self.policy = policy
        self.max_repairs = max_repairs

    # -- durable helpers ----------------------------------------------------

    def _mission(self, mission_id: str) -> dict[str, Any]:
        row = self.database.row("missions", "mission_id", mission_id)
        if row is None:
            raise ProposalError(f"unknown mission: {mission_id}")
        return row

    def _validate_step(
        self,
        mission: dict[str, Any],
        *,
        step_id: str,
        capability: str,
        target: Path,
        expected_effects: tuple[str, ...],
        rollback: dict[str, Any],
    ) -> Path:
        """Envelope contract validation BEFORE any dispatch (D2 / F3).

        Rejects: missing step identity, unregistered capability (not present
        in the resource's capability map), empty expected effects, empty
        rollback contract, and targets that resolve outside the registered
        resource root. Raises ProposalError — nothing is dispatched.
        """
        if not step_id or not str(step_id).strip():
            raise ProposalError("step_id is required")
        if not capability or not str(capability).strip():
            raise ProposalError("capability is required")
        if not expected_effects:
            raise ProposalError("expected effects are required")
        if not rollback or not isinstance(rollback, dict) or not rollback:
            raise ProposalError("rollback contract is required")
        resource = self.database.row("resources", "resource_id", str(mission["resource_id"]))
        if resource is None:
            raise ProposalError("resource is not registered")
        capabilities = json.loads(str(resource["capabilities_json"]))
        if capability not in capabilities:
            raise ProposalError(
                f"capability {capability!r} is not registered for resource "
                f"{resource['resource_id']}")
        root = Path(str(resource["resolved_root"])).resolve()
        requested = target.expanduser()
        if not requested.is_absolute():
            requested = root / requested
        try:
            requested.resolve(strict=False).relative_to(root)
        except ValueError:
            raise ProposalError("target escapes the registered resource root")
        return requested

    def _advance(self, mission_id: str, state: str, *, next_action: str | None = ...,
                 run_id: str | None = None) -> None:
        """Durable mission.state_changed (transition-validated by the domain).

        ``next_action`` is always carried explicitly: the projection writes it
        verbatim, so an omitted key would clear the scheduled requirement.
        The ``...`` sentinel preserves the current requirement. Same-state
        advances are skipped so a crash-resumed loop never issues an invalid
        X -> X transition event.

        Idempotency keys are scoped per loop pass via the pass's ``run_id``:
        each pass re-walks the state machine with fresh events, while
        re-delivering the SAME pass (same run_id) dedupes. The run_id is
        durable pass identity — an instance-local counter would collide when
        a FRESH MissionLoop instance resumes a mission left in REPAIRING
        (verifier finding F2: pass-1 keys of the new instance dedupe against
        the previous instance's pass-1 events, freezing the state machine).
        """
        current = self._mission(mission_id)
        if str(current["state"]) == state:
            return
        if next_action is ...:
            next_action = current["next_action"]
        key = f"mission.state_changed:{mission_id}:{state}:{next_action or 'none'}"
        if run_id is not None:
            key = f"{key}:run-{run_id}"
        event = Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": next_action},
            source_type="supervisor",
            source_id="mission-loop",
            idempotency_key=key,
        )
        self.database.append(event)

    def _record_evidence(
        self,
        *,
        mission_id: str,
        step_id: str,
        run_id: str,
        verdict_id: str,
        commit_state: dict[str, Any] | None,
        criteria: list[str],
        role: str,
        artifact_hashes: dict[str, str] | None = None,
    ) -> None:
        bundle_id = f"bundle-{uuid.uuid4().hex[:12]}"
        event = Event.create(
            "evidence.created",
            {
                "bundle_id": bundle_id,
                "mission_id": mission_id,
                "step_id": step_id,
                "objective": self._mission(mission_id)["objective"],
                "criteria": criteria,
                "before_state": {"head": (commit_state or {}).get("baseline_commit")},
                "baseline_commit": (commit_state or {}).get("baseline_commit"),
                "final_diff": {"stat": (commit_state or {}).get("diff_stat")},
                "files_changed": (commit_state or {}).get("files_changed", []),
                "commands_run": ["jarvis_foundry mission loop step dispatch"],
                "artifact_hashes": artifact_hashes or {},
                "after_state": {"head": (commit_state or {}).get("commit_head")},
                "uncertainty": {},
                "rollback_proof": {"kind": "git_reset_soft_available", "note": "commit is local-only in the registered worktree"},
                "builder_claim": {"run_id": run_id, "role": role, "status": "COMPLETED"},
                "verifier_verdict_id": verdict_id,
            },
            source_type="supervisor",
            source_id="mission-loop",
            idempotency_key=f"evidence.created:{bundle_id}",
        )
        self.database.append(event)

    # -- step execution -----------------------------------------------------

    def run_step(
        self,
        *,
        mission_id: str,
        step_id: str,
        capability: str,
        target: Path,
        instructions: str,
        expected_effects: tuple[str, ...],
        rollback: dict[str, Any],
        risk_class: str = "C1",
        run_id: str | None = None,
        commit_envelope: ActionEnvelope | None = None,
        verification_criteria: list[str] | None = None,
        expected_artifacts: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
    ) -> StepOutcome:
        """One dispatch + verify cycle for a mission step.

        Returns FAILED (mission is REPAIRING) when verification fails and the
        repair budget remains — the caller continues with
        ``run_step(..., run_id=<repair>)`` or calls ``run_until_settled``.
        """
        proposal = propose_next_action(self.database, mission_id)
        if isinstance(proposal, Waiting):
            return StepOutcome(outcome=WAITING_OUTCOME, reason=proposal.reason)
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        verdict_id = f"verdict-{uuid.uuid4().hex[:12]}"
        criteria = verification_criteria or ["step result is verified by an independent verifier"]

        mission = self._mission(mission_id)
        state = str(mission["state"])
        role = "repair" if state == "REPAIRING" else "builder"
        attempt = failed_verdict_count(self.database, mission_id, step_id) + 1

        # D2: envelope contract validated BEFORE any dispatch — unknown step/
        # capability/rollback and out-of-root targets are rejected here, never
        # dispatched (verifier finding F3).
        target = self._validate_step(
            mission, step_id=step_id, capability=capability, target=target,
            expected_effects=expected_effects, rollback=rollback,
        )
        # F6: the repair role is only reachable through a durable FAIL verdict
        # for this step (a hand-crafted REPAIRING state must not invent one).
        if role == "repair":
            latest = latest_verdict_for_step(self.database, mission_id, step_id)
            if latest is None or latest["status"] != "FAIL":
                raise ProposalError(
                    f"repair requested for {mission_id} step {step_id} but no FAIL verdict exists")

        self._advance(mission_id, "READY", run_id=run_id)
        self._advance(mission_id, "EXECUTING", run_id=run_id)

        result = self.dispatcher.dispatch(
            role=role,
            mission_id=mission_id,
            resource_id=str(mission["resource_id"]),
            capability=capability,
            step_id=step_id,
            run_id=run_id,
            instructions=instructions,
            expected_artifacts=expected_artifacts,
            # Only carry criteria in the manifest when the caller explicitly
            # provided them: default steps keep byte-identical manifest
            # hashes (historical replay semantics unchanged).
            verification_criteria=(
                tuple(verification_criteria)
                if verification_criteria is not None else ()),
            forbidden_paths=forbidden_paths,
            attempt=attempt,
        )
        if result.status != COMPLETED or result.error_class == "evidence_mismatch":
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                reason=f"{role} session did not complete: {result.error_class or result.status}",
            )

        self._advance(mission_id, "VERIFYING", run_id=run_id)
        verifier_provider = self.router.diversity_resolution(result.provider)
        verifier_run_id = f"{run_id}-verify"

        # DIR-2026-08-12 item 3 (exactly-once recovery): when this pass
        # resumes a run whose builder COMPLETED earlier, verification may
        # have been interrupted mid-flight. Recovery is state-aware and
        # generic:
        # (a) a STARTED verdict whose verifier run never reached a
        #     terminal is corrected additively (verification.blocked:
        #     interrupted; superseded) — never left dangling;
        # (b) if the pass's verifier run row exists WITHOUT a terminal
        #     status (interrupted RUNNING or reaped FAILED), the verifier
        #     is dispatched under a FRESH run id — re-dispatching under
        #     the old id would share the row with a possibly-live orphan
        #     session, and its completion event would either conflict on
        #     the started idempotency key or be blocked by the terminal
        #     guard (S3);
        # (c) a COMPLETED verifier row replays through the dispatcher (no
        #     new session) — the builder is NEVER re-executed (its run
        #     replays from the ledger: exactly-once mutation).
        dangling = dangling_verdict_for_run(self.database, verifier_run_id)
        if dangling is not None:
            record_verification_outcome(
                self.database, verdict_id=dangling["verdict_id"],
                outcome="BLOCKED", findings=[], evidence={},
                reason="verification pass interrupted before its outcome "
                       "was recorded; superseded by the resumed pass")
        verifier_row = self.database.row("agent_runs", "run_id", verifier_run_id)
        if verifier_row is not None and verifier_row["status"] != "COMPLETED":
            with self.database.connect() as connection:
                attempt = connection.execute(
                    "SELECT COUNT(*) FROM agent_runs WHERE run_id LIKE ?",
                    (f"{run_id}-verify%",)).fetchone()[0]
            verifier_run_id = f"{run_id}-verify-r{attempt}"

        start_verification(
            self.database,
            verdict_id=verdict_id,
            mission_id=mission_id,
            step_id=step_id,
            builder_run_id=run_id,
            verifier_run_id=verifier_run_id,
            independence={
                "builder_provider": result.provider,
                "verifier_provider": verifier_provider,
                "provider_diverse": result.provider != verifier_provider,
                "same_provider_limitation": (
                    "verifier ran under the same provider as the builder"
                    if result.provider == verifier_provider else ""
                ),
            },
            criteria=criteria,
        )
        verifier = self.dispatcher.dispatch(
            role="verifier",
            mission_id=mission_id,
            resource_id=str(mission["resource_id"]),
            capability="verification",
            step_id=step_id,
            run_id=verifier_run_id,
            provider_override=verifier_provider,
            instructions=(
                f"independently verify step {step_id} of {mission_id}: "
                "inspect the frozen artifact directly and confirm each "
                "criterion. End your response with a line: "
                "VERDICT: PASS | FAIL | BLOCKED | NOT_TESTED "
                "(NOT_TESTED when you cannot determine a verdict)."),
            expected_artifacts=expected_artifacts,
            verification_criteria=(
                tuple(verification_criteria)
                if verification_criteria is not None else ()),
            forbidden_paths=forbidden_paths,
            attempt=attempt,
        )
        if verifier.status != COMPLETED:
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome="BLOCKED",
                findings=[], evidence={},
                reason=f"verifier session did not complete: {verifier.error_class or verifier.status}",
                blocking_dependency=verifier.error_class,
            )
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id, reason=verifier.error_detail or "verifier unavailable",
            )

        if verifier.verdict == "PASS":
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome="PASS",
                findings=[], evidence={"verifier_run_id": verifier_run_id},
            )
            # The commit happens while still VERIFYING (only after the PASS
            # verdict is durable); a failed commit blocks from VERIFYING,
            # which the state machine allows. ACCEPTING is entered only when
            # the verified work is fully accepted.
            commit_state: dict[str, Any] | None = None
            if commit_envelope is not None and self.policy is not None:
                resource = self.database.row("resources", "resource_id", str(mission["resource_id"]))
                if resource is None:
                    self._advance(mission_id, "BLOCKED", run_id=run_id)
                    return StepOutcome(
                        outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                        verdict_id=verdict_id, reason="resource record missing for commit",
                    )
                try:
                    commit_state = commit_after_pass(
                        self.database,
                        mission_id=mission_id,
                        step_id=step_id,
                        run_id=run_id,
                        worktree=Path(str(resource["resolved_root"])),
                        policy=self.policy,
                        envelope=commit_envelope,
                    )
                except CommitError as error:
                    self._advance(mission_id, "BLOCKED", run_id=run_id)
                    return StepOutcome(
                        outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                        verdict_id=verdict_id, reason=f"commit after PASS failed: {error}",
                    )
            self._advance(mission_id, "ACCEPTING", run_id=run_id)
            self._record_evidence(
                mission_id=mission_id, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id, commit_state=commit_state, criteria=criteria,
                role=role,
                artifact_hashes=_hash_target(target),
            )
            self._advance(mission_id, "ACTION_SELECTION", next_action=None, run_id=run_id)
            return StepOutcome(
                outcome=PASSED, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id,
                commit_head=(commit_state or {}).get("commit_head"),
            )

        # FAIL -> bounded repair or precise BLOCKED
        findings = list(verifier.evidence)
        record_verification_outcome(
            self.database, verdict_id=verdict_id, outcome="FAIL",
            findings=findings,
            evidence={"verifier_run_id": verifier_run_id},
            repair_requirements=["rebuild the step artifact and re-verify"],
        )
        repair_budget = self.max_repairs
        fails = failed_verdict_count(self.database, mission_id, step_id)
        if fails <= repair_budget:
            self._advance(mission_id, "REPAIRING", run_id=run_id)
            return StepOutcome(
                outcome=FAILED, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id, reason="; ".join(findings) or "verification failed",
            )
        self._advance(mission_id, "BLOCKED", run_id=run_id)
        return StepOutcome(
            outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
            verdict_id=verdict_id,
            reason=f"repair budget exhausted ({fails} failed verdicts > {repair_budget})",
        )

    def run_until_settled(
        self,
        *,
        mission_id: str,
        step_id: str,
        capability: str,
        target: Path,
        instructions: str,
        expected_effects: tuple[str, ...],
        rollback: dict[str, Any],
        risk_class: str = "C1",
        commit_envelope: ActionEnvelope | None = None,
        verification_criteria: list[str] | None = None,
        expected_artifacts: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
    ) -> StepOutcome:
        """Run the step through FAIL -> repair cycles until PASSED, BLOCKED,
        or WAITING — never looping beyond the durable repair budget."""
        for attempt in range(self.max_repairs + 2):
            outcome = self.run_step(
                mission_id=mission_id, step_id=step_id, capability=capability,
                target=target, instructions=instructions,
                expected_effects=expected_effects, rollback=rollback,
                risk_class=risk_class, commit_envelope=commit_envelope,
                verification_criteria=verification_criteria,
                expected_artifacts=expected_artifacts,
                forbidden_paths=forbidden_paths,
            )
            if outcome.outcome != FAILED:
                return outcome
            if attempt == self.max_repairs + 1:
                return StepOutcome(
                    outcome=BLOCKED_OUTCOME, step_id=step_id,
                    reason="run_until_settled safety cap exceeded",
                )
        return StepOutcome(outcome=BLOCKED_OUTCOME, step_id=step_id,
                           reason="unreachable safety guard")  # pragma: no cover
