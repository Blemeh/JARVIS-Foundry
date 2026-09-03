"""Autonomous mission loop (WP5).

``MissionLoop`` runs one mission step end-to-end without owner prompting:

1. propose the next action deterministically (durable mission state only);
2. advance the mission state machine (READY -> EXECUTING);
3. dispatch the builder (or the repair role on a repair attempt) through the
   WP4 role adapter;
4. intake the structured result and advance to VERIFYING;
5. dispatch an independent verifier (provider-diverse when possible),
   recording the verdict with full independence metadata;
6. PASS -> governed local commit (only after verified PASS) -> ACCEPTING ->
   evidence bundle -> ACTION_SELECTION; FAIL -> bounded repair (REPAIRING)
   or BLOCKED with a precise reason when the repair budget is exhausted;
7. honest waiting when no next action is scheduled (no invented work).

Every transition and verdict is a durable event; the loop is deterministic
and re-runnable (idempotent events, ledger replay in the dispatcher).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from ..control.broker import emergency_stop_reason
from ..control.policy import ActionPolicy
from ..database import FoundryDatabase
from ..domain import ActionEnvelope
from ..events import Event
from ..roles.adapter import RoleAdapter
from ..roles.dispatcher import RoleDispatcher
from ..roles.results import (
    BLOCKED,
    COMPLETED,
    FAILED,
    RoleResult,
)
from ..roles.router import ProviderRouteCandidate, RoleRouter
from ..supervisor.leases import LeaseManager
from .commit import CommitError, commit_after_pass
from .proposal import ProposalError, Waiting, propose_next_action
from ..v3_fault import invoke_fault_hook
from .verification import (
    accepted_verifications,
    dangling_verdict_for_run,
    dangling_verdicts_for_run_prefix,
    failed_verdict_count,
    latest_verdict_for_step,
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
    verdict_for_verifier_run,
)
from .verifier_binding import (
    AcceptanceGate,
    build_terminal_evidence,
    capture_binding,
    deterministic_verdict_id,
    fresh_verifier_identity,
    VERIFIER_CONTRACT_VERSION,
    verifier_contract_digest,
)

PASSED = "PASSED"
FAILED = "FAILED"  # outcome constant; distinct from roles.results.FAILED status
BLOCKED_OUTCOME = "BLOCKED"
WAITING_OUTCOME = "WAITING"


def _hash_target(target: Path, *, limit: int = 50) -> dict[str, str]:
    """sha256 of every file under the step target (bounded) — binds the
    verified verdict to the frozen artifact hashes (DIR-2026-08-12 item 1).
    Paths are relative, forward-slash normalized; unreadable files are
    skipped (their absence from the map is visible in the bundle)."""
    hashes: dict[str, str] = {}
    if target.is_file():
        try:
            return {target.name: hashlib.sha256(target.read_bytes()).hexdigest()}
        except OSError:
            return {}
    if not target.is_dir():
        return hashes
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
        stop_path: Path | str | None = None,
        fault_hook: Callable[[str], None] | None = None,
        provider_candidates: Iterable[ProviderRouteCandidate] = (),
        max_provider_attempts: int = 4,
        max_provider_retries: int = 0,
        allowed_data_use: tuple[str, ...] = (),
        allowed_providers: tuple[str, ...] = (),
        denied_providers: tuple[str, ...] = (),
        provider_budget_usd: float = 0.0,
        provider_routing_constraints: dict[str, Any] | None = None,
    ) -> None:
        self.database = database
        self.router = router
        provider_candidates = tuple(provider_candidates)
        self.dispatcher = RoleDispatcher(
            database, router, adapter,
            leases=leases, source_id=source_id,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            fault_hook=fault_hook,
            provider_candidates=provider_candidates,
            max_total_attempts=max_provider_attempts,
            max_retries_per_provider=max_provider_retries,
            allowed_data_use=allowed_data_use,
            allowed_providers=allowed_providers,
            denied_providers=denied_providers,
            provider_override_allows_fallback=bool(provider_candidates),
            routing_constraints=provider_routing_constraints,
        )
        self.provider_budget_usd = max(0.0, float(provider_budget_usd))
        self.policy = policy
        self.max_repairs = max_repairs
        self.fault_hook = fault_hook
        if stop_path is not None:
            self.stop_path = Path(stop_path)
        elif policy is not None:
            self.stop_path = Path(policy.stop_path)
        else:
            # Same identity as jarvis stop / Supervisor: {foundry_home}/EMERGENCY_STOP
            self.stop_path = Path(database.path).resolve().parent / "EMERGENCY_STOP"

    def _stop_reason(self, mission_id: str | None = None) -> str | None:
        identities = None
        if mission_id:
            mission = self.database.row("missions", "mission_id", mission_id)
            if mission is not None:
                identities = {
                    "GLOBAL", mission_id, str(mission.get("resource_id") or ""),
                }
        return emergency_stop_reason(self.database, self.stop_path, identities=identities)

    def _scoped_stop_reason(self, mission_id: str) -> str | None:
        """Call the scope-aware stop probe with a narrow legacy fallback.

        Older embedders occasionally replace ``_stop_reason`` with a
        zero-argument callback. Detect that callable shape before invoking it
        so the canonical implementation still receives the mission scope and
        no TypeError escapes from a compatibility hook.
        """
        import inspect

        try:
            parameters = inspect.signature(self._stop_reason).parameters.values()
            accepts_positional = any(
                parameter.kind in (parameter.POSITIONAL_ONLY,
                                   parameter.POSITIONAL_OR_KEYWORD,
                                   parameter.VAR_POSITIONAL)
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_positional = True
        return self._stop_reason(mission_id) if accepts_positional else self._stop_reason()

    def _stop_blocked(self, mission_id: str, *, step_id: str,
                      run_id: str | None = None) -> StepOutcome | None:
        """Freeze a pass at every external-effect boundary.

        STOP is a safety decision, not a provider result.  A durable state is
        still written so a fresh loop can re-orient after the owner clears the
        stop; no builder, verifier, commit, or completion event is emitted
        after the gate observes STOP.
        """
        reason = self._scoped_stop_reason(mission_id)
        if not reason:
            return None
        mission = self._mission(mission_id)
        state = str(mission["state"])
        if state in {"READY", "EXECUTING", "VERIFYING", "REPAIRING"}:
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            self.database.append(Event.create(
                "mission.stop_blocked",
                {"mission_id": mission_id, "step_id": step_id,
                 "run_id": run_id, "reason": reason},
                source_type="supervisor", source_id="mission-loop",
                idempotency_key=f"mission.stop_blocked:{mission_id}:{run_id or 'none'}"))
        elif state == "ACCEPTING":
            # ACCEPTING can only return to ACTION_SELECTION or COMPLETE.  A
            # cleared next-action slot is the durable reorientation point.
            self._advance(mission_id, "ACTION_SELECTION",
                          next_action=None, run_id=run_id)
            self.database.append(Event.create(
                "mission.stop_blocked",
                {"mission_id": mission_id, "step_id": step_id,
                 "run_id": run_id, "reason": reason},
                source_type="supervisor", source_id="mission-loop",
                idempotency_key=f"mission.stop_blocked:{mission_id}:{run_id or 'none'}"))
        return StepOutcome(outcome=BLOCKED_OUTCOME, step_id=step_id,
                           run_id=run_id,
                           reason=f"STOP active: {reason}")

    def _reorient_after_stop(self, mission_id: str) -> None:
        """Move a STOP-blocked mission back to its scheduled action once clear."""
        if self._scoped_stop_reason(mission_id):
            return
        mission = self._mission(mission_id)
        if str(mission["state"]) != "BLOCKED":
            return
        if not any(e.event_type == "mission.stop_blocked"
                   and e.payload.get("mission_id") == mission_id
                   for e in self.database.events()):
            return
        self._advance(
            mission_id, "ACTION_SELECTION",
            next_action=mission.get("next_action"),
            run_id=f"resume-stop-{mission_id}",
        )

    @staticmethod
    def _pass_requires_jarvis_predicate(
        *,
        target: Path,
        expected_artifacts: tuple[str, ...],
    ) -> str | None:
        """Model PASS is insufficient: JARVIS must see an artifact predicate."""
        if expected_artifacts:
            missing = []
            root = target if target.is_dir() else target.parent
            for rel in expected_artifacts:
                candidate = Path(rel)
                if candidate.is_absolute():
                    path = candidate
                elif (target.is_file() and len(candidate.parts) <= len(target.parts)
                      and tuple(target.parts[-len(candidate.parts):]) == candidate.parts):
                    path = target
                else:
                    path = root / rel
                if not path.exists():
                    missing.append(rel)
            if missing:
                return "expected artifacts missing: " + ", ".join(missing)
            return None
        if target.is_file():
            return None
        hashes = _hash_target(target)
        if hashes:
            return None
        return "model PASS is not backed by a JARVIS-owned artifact predicate"

    def _repair_or_block(
        self, *, mission_id: str, step_id: str, run_id: str, verdict_id: str,
        findings: list[str], reason: str,
    ) -> StepOutcome:
        repair_budget = self.max_repairs
        fails = failed_verdict_count(self.database, mission_id, step_id)
        if fails <= repair_budget:
            self._advance(mission_id, "REPAIRING", run_id=run_id)
            return StepOutcome(
                outcome=FAILED, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id, reason=reason,
            )
        self._advance(mission_id, "BLOCKED", run_id=run_id)
        return StepOutcome(
            outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
            verdict_id=verdict_id,
            reason=f"repair budget exhausted ({fails} failed verdicts > {repair_budget})",
        )

    def _continue_after_pass(self, mission_id: str, *, run_id: str | None) -> None:
        """Durable next-action owner after a verified PASS.

        Contract-driven missions keep an ordered remaining-step plan:
        the next un-passed contract becomes ``next_action``. When none
        remain, the mission COMPLETEs from durable truth. Missions with
        no contracts keep the legacy WAITING behaviour (cleared
        ``next_action`` at ACTION_SELECTION) so existing executive
        schedulers still work.
        """
        from .contracts import contract_rows, next_executable_contract

        remaining = next_executable_contract(self.database, mission_id)
        if remaining is not None:
            self._advance(
                mission_id, "ACTION_SELECTION",
                next_action=remaining.instructions, run_id=run_id)
            return
        if contract_rows(self.database, mission_id):
            completed = Event.create(
                "mission.completed",
                {"mission_id": mission_id, "outcome": "PASSED", "run_id": run_id},
                source_type="supervisor",
                source_id="mission-loop",
                idempotency_key=f"mission.completed:{mission_id}",
            )
            self._advance_with_event(
                mission_id, "COMPLETE", event=completed,
                next_action=None, run_id=run_id)
            invoke_fault_hook(self.fault_hook, "post mission.completed")
            return
        self._advance(mission_id, "ACTION_SELECTION", next_action=None, run_id=run_id)

    def _advance_with_event(
        self,
        mission_id: str,
        state: str,
        *,
        event: Event,
        next_action: str | None,
        run_id: str | None,
    ) -> None:
        """Commit a terminal state transition and its lifecycle event once."""
        current = self._mission(mission_id)
        if str(current["state"]) == state:
            return
        key = f"mission.state_changed:{mission_id}:{state}:{next_action or 'none'}"
        if run_id is not None:
            key = f"{key}:run-{run_id}"
            epoch = self._provider_transition_epoch(mission_id, run_id)
            if epoch:
                key = f"{key}:resume-{epoch}"
        transition = Event.create(
            "mission.state_changed",
            {
                "mission_id": mission_id,
                "state": state,
                "next_action": next_action,
                "run_id": run_id,
            },
            source_type="supervisor",
            source_id="mission-loop",
            idempotency_key=key,
        )
        self.database.append_events([transition, event])

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
            epoch = self._provider_transition_epoch(mission_id, run_id)
            if epoch:
                key = f"{key}:resume-{epoch}"
        event = Event.create(
            "mission.state_changed",
            {
                "mission_id": mission_id,
                "state": state,
                "next_action": next_action,
                "run_id": run_id,
            },
            source_type="supervisor",
            source_id="mission-loop",
            idempotency_key=key,
        )
        self.database.append(event)

    def _provider_transition_epoch(
        self, mission_id: str, run_id: str,
    ) -> str | None:
        """Distinct transition delivery epoch for a resumed provider wait.

        The mission and agent run identities stay unchanged.  Only the
        append-only state-transition delivery key gains the wait episode, so
        BLOCKED -> ACTION_SELECTION can legally walk READY/EXECUTING again
        without an old idempotency key suppressing the projection update.
        """

        for event in self.database.events(
            readonly=True,
            newest_first=True,
            limit=1000,
            event_type_prefixes=("mission.provider_wait_resumed",),
        ):
            payload = event.payload
            if (
                str(payload.get("mission_id") or "") == mission_id
                and str(payload.get("run_id") or "") == run_id
            ):
                return str(payload.get("wait_id") or "") or None
        return None

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
        # Evidence identity is derived from the immutable pass contract.  A
        # duplicate caller therefore re-delivers the same event instead of
        # creating an unbounded set of otherwise identical bundles.
        bundle_material = {
            "schema": "evidence-bundle-v2",
            "mission_id": mission_id,
            "step_id": step_id,
            "run_id": run_id,
            "verdict_id": verdict_id,
            "artifact_hashes": artifact_hashes or {},
            "commit_head": (commit_state or {}).get("commit_head"),
        }
        bundle_id = "bundle-" + hashlib.sha256(
            json.dumps(bundle_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
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
        stop_reason = self._scoped_stop_reason(mission_id)
        if stop_reason:
            return StepOutcome(outcome=BLOCKED_OUTCOME, reason=stop_reason)
        self._reorient_after_stop(mission_id)
        mission = self._mission(mission_id)
        state = str(mission["state"])
        if run_id is None and state in {"ACTION_SELECTION", "READY"}:
            from ..executive.continuous import provider_resume_run_id
            run_id = provider_resume_run_id(
                self.database, mission_id=mission_id, step_id=step_id)
        from .contracts import contract_rows
        contract_driven = bool(contract_rows(self.database, mission_id))
        # A deliberate crash can leave the canonical mission in EXECUTING or
        # VERIFYING.  Resume the durable run instead of asking the proposal
        # layer (which correctly rejects those in-flight states) to invent a
        # new action.  When the caller does not carry the run id, recover the
        # latest builder/repair run from the ledger.
        if run_id is None and state in {"EXECUTING", "VERIFYING"}:
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT run_id FROM agent_runs "
                    "WHERE mission_id = ? AND step_id = ? "
                    "AND role IN ('builder', 'repair') "
                    "ORDER BY rowid DESC LIMIT 1",
                    (mission_id, step_id),
                ).fetchone()
            run_id = str(row[0]) if row else None
        if contract_driven and run_id is None and state in {
            "ACTION_SELECTION", "ACCEPTING", "COMPLETE",
        }:
            prior = latest_verdict_for_step(
                self.database, mission_id, step_id,
            )
            accepted = accepted_verifications(
                self.database, mission_id=mission_id,
            )
            if (
                prior is not None
                and str(prior.get("status") or "") == "PASS"
                and str(prior.get("verdict_id") or "") in accepted
            ):
                run_id = str(prior.get("builder_run_id") or "") or None
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        # A duplicate delivery after a durable PASS is a replay, not a new
        # mission attempt.  This guard is intentionally scoped to the exact
        # builder run and step; a later repair run or a different step must
        # still execute and produce a new verifier binding.
        if contract_driven and state in {
            "ACTION_SELECTION", "ACCEPTING", "COMPLETE",
        }:
            prior = latest_verdict_for_step(self.database, mission_id, step_id)
            builder_row = self.database.row("agent_runs", "run_id", run_id)
            if (prior is not None and prior.get("status") == "PASS"
                    and str(prior.get("builder_run_id") or "") == run_id
                    and builder_row is not None
                    and builder_row.get("status") == "COMPLETED"):
                # A duplicate delivery is a replay only while the frozen
                # acceptance seal still matches the live tree.  Re-check the
                # canonical gate here; otherwise a post-PASS mutation could
                # be reported as healthy before the normal verifier-revision
                # path gets a chance to re-evaluate it.
                replay_decision = AcceptanceGate(
                    self.database, stop_path=self.stop_path,
                ).evaluate(
                    mission_id=mission_id, step_id=step_id, target=target,
                    builder_run_id=run_id,
                )
                if replay_decision.accepted:
                    commit_state: dict[str, Any] | None = None
                    if commit_envelope is not None and self.policy is not None:
                        resource = self.database.row(
                            "resources", "resource_id",
                            str(mission["resource_id"]),
                        )
                        if resource is None:
                            return StepOutcome(
                                outcome=BLOCKED_OUTCOME,
                                step_id=step_id,
                                run_id=run_id,
                                verdict_id=str(prior.get("verdict_id") or ""),
                                reason="resource record missing for commit replay",
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
                                fault_hook=self.fault_hook,
                            )
                        except CommitError as error:
                            return StepOutcome(
                                outcome=BLOCKED_OUTCOME,
                                step_id=step_id,
                                run_id=run_id,
                                verdict_id=str(prior.get("verdict_id") or ""),
                                reason=f"commit replay after PASS failed: {error}",
                            )
                    record_verification_acceptance(
                        self.database,
                        verdict_id=str(prior.get("verdict_id") or ""),
                        mission_id=mission_id,
                        step_id=step_id,
                        builder_run_id=str(prior.get("builder_run_id") or run_id),
                        verifier_run_id=str(
                            prior.get("verifier_run_id") or f"{run_id}-replay"
                        ),
                        checks=replay_decision.checks,
                    )
                    stopped = self._stop_blocked(
                        mission_id, step_id=step_id, run_id=run_id,
                    )
                    if stopped is not None:
                        return stopped
                    self._record_evidence(
                        mission_id=mission_id,
                        step_id=step_id,
                        run_id=run_id,
                        verdict_id=str(prior.get("verdict_id") or ""),
                        commit_state=commit_state,
                        criteria=(
                            verification_criteria
                            or [
                                "step result is verified by an independent "
                                "verifier"
                            ]
                        ),
                        role=str(builder_row.get("role") or "builder"),
                        artifact_hashes=_hash_target(target),
                    )
                    self._continue_after_pass(mission_id, run_id=run_id)
                    return StepOutcome(
                        outcome=PASSED, step_id=step_id, run_id=run_id,
                        verdict_id=str(prior.get("verdict_id") or ""),
                        commit_head=(commit_state or {}).get("commit_head"),
                    )
        if state in {"EXECUTING", "VERIFYING"}:
            proposal = SimpleNamespace(
                redirected=False,
                action_requirement=str(mission.get("next_action") or ""),
                owner_constraints=(),
                owner_constraint_status="CLEAR",
            )
        else:
            proposal = propose_next_action(self.database, mission_id)
            if isinstance(proposal, Waiting):
                return StepOutcome(outcome=WAITING_OUTCOME, reason=proposal.reason)
        verdict_id = deterministic_verdict_id(
            mission_id=mission_id, step_id=step_id, builder_run_id=run_id,
            verifier_run_id=f"{run_id}-crash",
            contract_digest=verifier_contract_digest({
                "mission_id": mission_id, "step_id": step_id,
                "role": "builder", "instructions": instructions,
                "capability": capability, "attempt": failed_verdict_count(
                    self.database, mission_id, step_id) + 1,
            }),
        )
        criteria = verification_criteria or ["step result is verified by an independent verifier"]

        role = "repair" if state == "REPAIRING" else "builder"
        attempt = failed_verdict_count(self.database, mission_id, step_id) + 1
        repair_context_text = ""

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
            try:
                findings = tuple(json.loads(str(latest.get("findings_json") or "[]")))
                requirements = tuple(json.loads(str(
                    latest.get("repair_requirements_json") or "[]")))
            except (TypeError, ValueError, json.JSONDecodeError):
                findings = ()
                requirements = ()
            repair_context = [
                "This is an automatic repair pass after an independent FAIL.",
                "Inspect the current partial state before changing it; preserve valid work.",
            ]
            if findings:
                repair_context.append("Verifier findings:")
                repair_context.extend(f"- {value}" for value in findings)
            if requirements:
                repair_context.append("Repair requirements:")
                repair_context.extend(f"- {value}" for value in requirements)
            repair_context_text = "\n".join(repair_context)

        if state not in {"EXECUTING", "VERIFYING"}:
            self._advance(mission_id, "READY", run_id=run_id)
            self._advance(mission_id, "EXECUTING", run_id=run_id)
        stopped = self._stop_blocked(mission_id, step_id=step_id,
                                     run_id=run_id)
        if stopped is not None:
            return stopped

        # ``proposal.action_requirement`` is projected from the canonical
        # mission state, including an authenticated REDIRECT.  Carry it into
        # the provider-facing instruction so the downstream worker executes
        # the interpreted next action rather than merely seeing a control row.
        dispatch_instructions = instructions
        if proposal.redirected:
            dispatch_instructions = (
                f"Canonical mission next action: {proposal.action_requirement}\n\n"
                f"{instructions}")
        if proposal.owner_constraints:
            constraint_lines = [
                "Durable owner constraints (verbatim; policy gates remain authoritative):"
            ]
            for constraint in proposal.owner_constraints:
                text = str(constraint.get("exact_text") or "").strip()
                if text:
                    constraint_lines.append(
                        f"- [{constraint.get('state', 'UNKNOWN')}] {text}")
            if proposal.owner_constraint_status != "CLEAR":
                constraint_lines.append(
                    f"Owner-constraint status: {proposal.owner_constraint_status}; "
                    "do not infer an additional rule from ambiguous text.")
            dispatch_instructions = (
                "\n".join(constraint_lines) + "\n\n" + dispatch_instructions)
        if repair_context_text:
            dispatch_instructions = (
                repair_context_text + "\n\n" + dispatch_instructions)
        if state == "VERIFYING":
            row = self.database.row("agent_runs", "run_id", run_id)
            try:
                if row is None or row.get("status") != "COMPLETED":
                    raise ValueError("durable builder run is not complete")
                result = RoleResult.from_payload(json.loads(str(row["result_json"])))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                self._advance(mission_id, "BLOCKED", run_id=run_id)
                return StepOutcome(
                    outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                    reason=f"cannot resume VERIFYING run: {error}",
                )
        else:
            result = self.dispatcher.dispatch(
                role=role,
                mission_id=mission_id,
                resource_id=str(mission["resource_id"]),
                capability=capability,
                step_id=step_id,
                run_id=run_id,
                instructions=dispatch_instructions,
                expected_artifacts=expected_artifacts,
                # Only carry criteria in the manifest when the caller explicitly
                # provided them: default steps keep byte-identical manifest
                # hashes (historical replay semantics unchanged).
                verification_criteria=(
                    tuple(verification_criteria)
                    if verification_criteria is not None else ()),
                forbidden_paths=forbidden_paths,
                attempt=attempt,
                budget_usd=self.provider_budget_usd,
            )
        if result.status == BLOCKED:
            if str(result.error_class or "").startswith("provider_"):
                from ..executive.continuous import schedule_mission_provider_wait
                schedule_mission_provider_wait(
                    self.database,
                    mission_id=mission_id,
                    step_id=step_id,
                    run_id=run_id,
                    provider_ids=result.wait_for_providers,
                    reason=(
                        result.error_detail
                        or f"{role} session blocked: {result.error_class}"
                    ),
                    error_class=str(result.error_class),
                    effect_action_key=result.effect_action_key,
                )
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                reason=f"{role} session blocked: {result.error_class or result.status}",
            )
        if result.status != COMPLETED:
            crash_context = {
                "mission_id": mission_id, "step_id": step_id,
                "role": role, "instructions": dispatch_instructions,
                "capability": capability, "attempt": attempt,
            }
            crash_verifier_id = f"{run_id}-crash"
            verdict_id = deterministic_verdict_id(
                mission_id=mission_id, step_id=step_id,
                builder_run_id=run_id, verifier_run_id=crash_verifier_id,
                contract_digest=verifier_contract_digest(crash_context),
            )
            start_verification(
                self.database,
                verdict_id=verdict_id,
                mission_id=mission_id,
                step_id=step_id,
                builder_run_id=run_id,
                verifier_run_id=crash_verifier_id,
                independence={
                    "builder_provider": result.provider,
                    "verifier_provider": "jarvis",
                    "provider_diverse": True,
                    "session_independent": True,
                    "provider_independent": True,
                    "same_provider_limitation": "",
                },
                criteria=criteria,
            )
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome="FAIL",
                findings=list(result.evidence) or [result.error_detail or result.status],
                evidence={"builder_run_id": run_id, "error_class": result.error_class},
                repair_requirements=["recover partial worker mutation and re-verify"],
                reason=f"{role} crashed or failed: {result.error_class or result.status}",
            )
            return self._repair_or_block(
                mission_id=mission_id, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id,
                findings=list(result.evidence),
                reason=f"{role} session did not complete: {result.error_class or result.status}",
            )

        stopped = self._stop_blocked(mission_id, step_id=step_id,
                                     run_id=run_id)
        if stopped is not None:
            return stopped
        self._advance(mission_id, "VERIFYING", run_id=run_id)
        verifier_provider = self.router.diversity_resolution(result.provider)
        base_verifier_run_id = f"{run_id}-verify"
        verifier_criteria = (
            tuple(verification_criteria)
            if verification_criteria is not None else ())
        # Keep the provider-facing verifier contract intentionally free of
        # builder claims.  The immutable target, criteria and forbidden-path
        # fields live in the manifest and are independently re-hashed at the
        # acceptance gate.
        verifier_instructions = (
            f"independently verify step {step_id} of {mission_id}: "
            f"verifier contract {VERIFIER_CONTRACT_VERSION}; "
            "read-only inspection only: inspect the frozen artifact "
            "directly and confirm each "
            "criterion. The .jarvis-result.json marker is a control record "
            "only; do not treat its claims as independent proof. "
            "End your response with a line: "
            "VERDICT: PASS | FAIL | BLOCKED | NOT_TESTED "
            "(NOT_TESTED when you cannot determine a verdict).")
        verifier_config = self.router.config("verifier")
        base_manifest = self.dispatcher.manifest_for(
            base_verifier_run_id, "verifier", mission_id, step_id,
            str(mission["resource_id"]), "verification",
            verifier_instructions, 300, self.provider_budget_usd, verifier_provider,
            verifier_config,
            expected_artifacts=expected_artifacts,
            verification_criteria=verifier_criteria,
            forbidden_paths=forbidden_paths,
            attempt=attempt,
        )
        verifier_context = {
            "mission_id": mission_id,
            "step_id": step_id,
            "resource_id": str(mission["resource_id"]),
            "capability": "verification",
            "provider": verifier_provider,
            "model": verifier_config.preferred_model,
            "instructions": verifier_instructions,
            "expected_artifacts": list(expected_artifacts),
            "verification_criteria": list(verifier_criteria),
            "forbidden_paths": list(forbidden_paths),
            "attempt": attempt,
            "verifier_contract_version": VERIFIER_CONTRACT_VERSION,
        }

        # Select the legacy verifier id only when the persisted manifest is
        # byte-for-byte compatible.  Any changed instruction, provider,
        # model, criteria or scope receives a deterministic content-addressed
        # identity, preserving the old run/evidence as immutable history.
        verifier_row = self.database.row("agent_runs", "run_id", base_verifier_run_id)

        def _manifest_hash_for_row(row: dict[str, Any], manifest) -> str:
            from dataclasses import replace
            return replace(
                manifest,
                provider=str(row.get("provider") or manifest.provider),
                model=str(row.get("model") or manifest.model),
            ).content_hash()

        base_compatible = (
            verifier_row is None
            or (
                verifier_row.get("status") == "COMPLETED"
                and str(verifier_row.get("context_manifest_hash") or "")
                == _manifest_hash_for_row(verifier_row, base_manifest)
            )
        )

        def _completed_verifier_is_reusable(run: str) -> bool:
            """Only reuse a completed verifier whose PASS still seals the tree.

            A historical verifier process can be complete while its frozen
            artifact or working-tree seal has become stale.  Replaying that
            provider result would turn the next acceptance check into a
            terminal failure instead of letting the current release perform a
            fresh read-only evaluation.  A missing verdict is intentionally
            reusable for crash recovery: the completed provider result can be
            sealed by a newly recorded outcome without another provider call.
            """
            verdict = verdict_for_verifier_run(
                self.database, mission_id=mission_id, step_id=step_id,
                verifier_run_id=run,
            )
            if verdict is None:
                return True
            if str(verdict.get("status") or "") != "PASS":
                return False
            return AcceptanceGate(
                self.database, stop_path=self.stop_path,
            ).evaluate(
                mission_id=mission_id, step_id=step_id,
                target=target, builder_run_id=run_id,
            ).accepted

        # A completed legacy run is only a replay when its durable PASS still
        # matches the live artifact.  If the tree drifted, force a new
        # content-addressed verifier session; the old evidence remains
        # immutable history and cannot be silently re-authorized.
        if base_compatible and verifier_row is not None:
            if not _completed_verifier_is_reusable(base_verifier_run_id):
                base_compatible = False

        if base_compatible:
            verifier_revision = 1
            verifier_use_base = True
        else:
            # A failed/reaped verifier cannot be restarted under its old
            # idempotency key.  Reuse a completed content-addressed revision
            # when its manifest is exact; otherwise choose the next stable
            # revision.  The database is the source of truth, so concurrent
            # callers converge on this same identity.
            verifier_revision = 1
            verifier_use_base = False
            with self.database.connect() as connection:
                rows = connection.execute(
                    "SELECT run_id, status, context_manifest_hash, provider, model "
                    "FROM agent_runs WHERE mission_id = ? AND step_id = ? "
                    "AND role = 'verifier' AND run_id LIKE ? ORDER BY rowid",
                    (mission_id, step_id, f"{base_verifier_run_id}-v%"),
                ).fetchall()
            for row in rows:
                row_dict = dict(row)
                candidate_manifest = self.dispatcher.manifest_for(
                    str(row_dict["run_id"]), "verifier", mission_id, step_id,
                    str(mission["resource_id"]), "verification",
                    verifier_instructions, 300, 0.0, verifier_provider,
                    verifier_config,
                    expected_artifacts=expected_artifacts,
                    verification_criteria=verifier_criteria,
                    forbidden_paths=forbidden_paths,
                    attempt=attempt,
                )
                if (row_dict.get("status") == "COMPLETED"
                        and str(row_dict.get("context_manifest_hash") or "")
                        == _manifest_hash_for_row(row_dict, candidate_manifest)):
                    if not _completed_verifier_is_reusable(str(row_dict["run_id"])):
                        verifier_revision = max(verifier_revision, 2)
                        continue
                    verifier_use_base = False
                    # Preserve an exact completed content-addressed session.
                    verifier_revision = int(str(row_dict["run_id"]).rsplit("-r", 1)[1]) \
                        if "-r" in str(row_dict["run_id"]) and str(row_dict["run_id"]).rsplit("-r", 1)[1].isdigit() else 1
                    break
                if str(row_dict.get("run_id") or "").startswith(base_verifier_run_id + "-v"):
                    suffix = str(row_dict["run_id"]).rsplit("-r", 1)[1] \
                        if "-r" in str(row_dict["run_id"]) and str(row_dict["run_id"]).rsplit("-r", 1)[1].isdigit() else "1"
                    verifier_revision = max(verifier_revision, int(suffix) + 1)

        verdict_id, verifier_run_id = fresh_verifier_identity(
            run_id,
            attempt=attempt,
            identity_context=verifier_context,
            base_verifier_run_id=base_verifier_run_id,
            use_base=verifier_use_base,
            revision=verifier_revision,
        )
        verifier_manifest = self.dispatcher.manifest_for(
            verifier_run_id, "verifier", mission_id, step_id,
            str(mission["resource_id"]), "verification",
            verifier_instructions, 300, self.provider_budget_usd, verifier_provider,
            verifier_config,
            expected_artifacts=expected_artifacts,
            verification_criteria=verifier_criteria,
            forbidden_paths=forbidden_paths,
            attempt=attempt,
        )
        # If a legacy/random release already recorded a verdict for this
        # exact verifier session, retain its immutable verdict identity on an
        # exact replay.  New content-addressed sessions use the deterministic
        # v2 id above.
        existing_verdict = verdict_for_verifier_run(
            self.database, mission_id=mission_id, step_id=step_id,
            verifier_run_id=verifier_run_id)
        if existing_verdict is not None and existing_verdict.get("status") != "STARTED":
            verdict_id = str(existing_verdict["verdict_id"])

        # Every interrupted STARTED verdict in this builder's verifier family
        # is corrected additively before the new attempt.  No old event or
        # evidence row is edited or deleted.
        for dangling in dangling_verdicts_for_run_prefix(self.database, base_verifier_run_id):
            if str(dangling.get("verdict_id")) == verdict_id:
                continue
            record_verification_outcome(
                self.database, verdict_id=dangling["verdict_id"],
                outcome="BLOCKED", findings=[], evidence={},
                reason="verification pass interrupted before its outcome "
                       "was recorded; superseded by the resumed pass")

        # Master Goal 2: the verifier is a fresh isolated run and the verdict
        # binding is frozen BEFORE any verifier session starts.
        capture = capture_binding(
            self.database,
            verdict_id=verdict_id,
            mission_id=mission_id,
            step_id=step_id,
            target=target,
            builder_run_id=run_id,
            verifier_run_id=verifier_run_id,
            context_manifest_hash=verifier_manifest.content_hash(),
            independence={
                "builder_provider": result.provider,
                "verifier_provider": verifier_provider,
                "provider_diverse": result.provider != verifier_provider,
                "session_independent": True,
                "provider_independent": result.provider != verifier_provider,
                "same_provider_limitation": (
                    "same-provider verification: reduced provider "
                    "independence; isolation enforced through a fresh "
                    "verifier session with distinct run identity"
                    if result.provider == verifier_provider else ""
                ),
                "session_isolation": "fresh-session",
            },
            criteria=criteria,
        )
        invoke_fault_hook(self.fault_hook, "post verification.started")
        stopped = self._stop_blocked(mission_id, step_id=step_id,
                                     run_id=run_id)
        if stopped is not None:
            return stopped
        verifier = self.dispatcher.dispatch(
            role="verifier",
            mission_id=mission_id,
            resource_id=str(mission["resource_id"]),
            capability="verification",
            step_id=step_id,
            run_id=verifier_run_id,
            provider_override=verifier_provider,
            instructions=verifier_instructions,
            expected_artifacts=expected_artifacts,
            verification_criteria=verifier_criteria,
            forbidden_paths=forbidden_paths,
            attempt=attempt,
            budget_usd=self.provider_budget_usd,
        )
        stopped = self._stop_blocked(mission_id, step_id=step_id,
                                     run_id=run_id)
        if stopped is not None:
            return stopped
        if verifier.status != COMPLETED:
            if (verifier.error_class == "evidence_mismatch"
                    or verifier.verdict == "FAIL"):
                findings = list(verifier.evidence) or [
                    verifier.error_detail or verifier.status]
                record_verification_outcome(
                    self.database, verdict_id=verdict_id, outcome="FAIL",
                    findings=findings,
                    evidence=build_terminal_evidence(
                        capture=capture,
                        verifier_session_id=verifier_run_id,
                        extra={"verifier_completed": False},
                    ),
                    repair_requirements=["rebuild the step artifact and re-verify"],
                    reason=verifier.error_detail or "verifier evidence mismatch",
                )
                return self._repair_or_block(
                    mission_id=mission_id, step_id=step_id, run_id=run_id,
                    verdict_id=verdict_id, findings=findings,
                    reason=verifier.error_detail or "verifier evidence mismatch",
                )
            # Verifier crash / interruption: honest BLOCKED with the frozen
            # binding preserved; a resumed pass supersedes this verdict with
            # a fresh binding and a fresh verifier identity.
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome="BLOCKED",
                findings=[], evidence={},
                reason=f"verifier session did not complete: {verifier.error_class or verifier.status}",
                blocking_dependency=verifier.error_class,
            )
            if str(verifier.error_class or "").startswith("provider_"):
                from ..executive.continuous import schedule_mission_provider_wait
                schedule_mission_provider_wait(
                    self.database,
                    mission_id=mission_id,
                    step_id=step_id,
                    run_id=run_id,
                    provider_ids=verifier.wait_for_providers,
                    reason=(
                        verifier.error_detail
                        or f"verifier session blocked: {verifier.error_class}"
                    ),
                    error_class=str(verifier.error_class),
                    effect_action_key=verifier.effect_action_key,
                )
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id, reason=verifier.error_detail or "verifier unavailable",
            )

        if verifier.verdict == "PASS":
            stopped = self._stop_blocked(mission_id, step_id=step_id,
                                         run_id=run_id)
            if stopped is not None:
                return stopped
            refused = self._pass_requires_jarvis_predicate(
                target=target, expected_artifacts=expected_artifacts,
            )
            if refused or result.error_class == "evidence_mismatch":
                reason = refused or (result.error_detail or "evidence reconciliation disagreed")
                record_verification_outcome(
                    self.database, verdict_id=verdict_id, outcome="FAIL",
                    findings=[reason],
                    evidence=build_terminal_evidence(
                        capture=capture,
                        verifier_session_id=verifier_run_id,
                        extra={"reconciler": "disagree"},
                    ),
                    repair_requirements=["create the claimed artifact and re-verify"],
                    reason=reason,
                )
                return self._repair_or_block(
                    mission_id=mission_id, step_id=step_id, run_id=run_id,
                    verdict_id=verdict_id, findings=[reason], reason=reason,
                )
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome="PASS",
                findings=[],
                evidence=build_terminal_evidence(
                    capture=capture,
                    verifier_session_id=getattr(verifier, "session_id", "") or verifier_run_id,
                ),
            )
            # Master Goal 2: the fail-closed acceptance gate re-evaluates the
            # whole invariant set against the durable ledger AND the live
            # tree before anything may advance. A false-success claim that
            # somehow reached this point is refused here — no commit, no
            # ACCEPTING, no ACTION_SELECTION, no mission.completed.
            gate = AcceptanceGate(self.database, stop_path=self.stop_path)
            decision = gate.evaluate(
                mission_id=mission_id, step_id=step_id, target=target,
                builder_run_id=run_id,
            )
            if not decision.accepted:
                record_verification_outcome(
                    self.database, verdict_id=verdict_id, outcome="FAIL",
                    findings=[decision.reason],
                    evidence=build_terminal_evidence(
                        capture=capture,
                        verifier_session_id=getattr(verifier, "session_id", "") or verifier_run_id,
                        extra={"acceptance_gate": decision.checks},
                    ),
                    repair_requirements=[
                        "repair the acceptance-gate refusal and re-verify with a fresh verifier",
                    ],
                    reason=f"acceptance gate refused: {decision.reason}",
                )
                return self._repair_or_block(
                    mission_id=mission_id, step_id=step_id, run_id=run_id,
                    verdict_id=verdict_id, findings=[decision.reason],
                    reason=f"acceptance gate refused: {decision.reason}",
                )
            # The commit happens while still VERIFYING (only after the PASS
            # verdict and acceptance-gate decision are durable). The final
            # acceptance receipt is deliberately recorded AFTER this governed
            # effect, so a crash cannot make an uncommitted step look accepted.
            commit_state: dict[str, Any] | None = None
            stopped = self._stop_blocked(mission_id, step_id=step_id,
                                         run_id=run_id)
            if stopped is not None:
                return stopped
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
                        fault_hook=self.fault_hook,
                    )
                except CommitError as error:
                    self._advance(mission_id, "BLOCKED", run_id=run_id)
                    return StepOutcome(
                        outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                        verdict_id=verdict_id, reason=f"commit after PASS failed: {error}",
                    )
            record_verification_acceptance(
                self.database,
                verdict_id=verdict_id,
                mission_id=mission_id,
                step_id=step_id,
                builder_run_id=run_id,
                verifier_run_id=getattr(verifier, "session_id", "") or verifier_run_id,
                checks=decision.checks,
            )
            invoke_fault_hook(self.fault_hook, "post verification.accepted")
            invoke_fault_hook(
                self.fault_hook, f"post verification.accepted:{step_id}"
            )
            stopped = self._stop_blocked(mission_id, step_id=step_id,
                                         run_id=run_id)
            if stopped is not None:
                return stopped
            self._advance(mission_id, "ACCEPTING", run_id=run_id)
            stopped = self._stop_blocked(mission_id, step_id=step_id,
                                         run_id=run_id)
            if stopped is not None:
                return stopped
            self._record_evidence(
                mission_id=mission_id, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id, commit_state=commit_state, criteria=criteria,
                role=role,
                artifact_hashes=_hash_target(target),
            )
            stopped = self._stop_blocked(mission_id, step_id=step_id,
                                         run_id=run_id)
            if stopped is not None:
                return stopped
            self._continue_after_pass(mission_id, run_id=run_id)
            return StepOutcome(
                outcome=PASSED, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id,
                commit_head=(commit_state or {}).get("commit_head"),
            )

        if verifier.verdict != "FAIL":
            recorded = verifier.verdict or "empty"
            outcome = "NOT_TESTED" if recorded == "NOT_TESTED" else "BLOCKED"
            if recorded not in ("NOT_TESTED", "BLOCKED"):
                outcome = "BLOCKED"
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome=outcome,
                findings=list(verifier.evidence),
                evidence={"verifier_run_id": verifier_run_id},
                reason=(
                    f"verifier verdict {recorded} is not a FAIL and must not "
                    "consume repair budget"),
                blocking_dependency=recorded,
            )
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id,
                reason=f"verifier {recorded}",
            )

        # FAIL -> bounded repair or precise BLOCKED
        findings = list(verifier.evidence)
        record_verification_outcome(
            self.database, verdict_id=verdict_id, outcome="FAIL",
            findings=findings,
            evidence={"verifier_run_id": verifier_run_id},
            repair_requirements=["rebuild the step artifact and re-verify"],
        )
        return self._repair_or_block(
            mission_id=mission_id, step_id=step_id, run_id=run_id,
            verdict_id=verdict_id,
            findings=findings,
            reason="; ".join(findings) or "verification failed",
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
        # Keep the scoped mission identity on the canonical path.  The helper
        # also handles the narrow zero-argument callback shape used by older
        # embedders without weakening production scope enforcement.
        stop_reason = self._scoped_stop_reason(mission_id)
        if stop_reason:
            return StepOutcome(outcome=BLOCKED_OUTCOME, reason=stop_reason)
        self._reorient_after_stop(mission_id)
        if self._scoped_stop_reason(mission_id):
            current = self._mission(mission_id)
            if str(current["state"]) == "ACCEPTING":
                self._advance(mission_id, "ACTION_SELECTION",
                              next_action=None,
                              run_id=f"stop-accepting-{mission_id}")
            return StepOutcome(
                outcome=BLOCKED_OUTCOME,
                step_id=step_id,
                reason=f"STOP active: {self._scoped_stop_reason(mission_id)}",
            )
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
