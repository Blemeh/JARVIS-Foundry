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
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

from ..clock import parse, utc_now
from ..control.broker import emergency_stop_reason
from ..control.policy import ActionPolicy
from ..database import FoundryDatabase
from ..domain import ActionEnvelope
from ..events import Event, EventConflict
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
from .commit import (
    CommitError,
    close_mission_commit_claims,
    commit_after_pass,
)
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
_COST_QUANTUM = Decimal("0.000001")
_COST_MICROS_PER_USD = 1_000_000


def _cost_micros(value: float) -> int:
    observed = Decimal(str(value))
    quantized = observed.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)
    return int(quantized * _COST_MICROS_PER_USD)


def _cost_usd(value: int) -> float:
    return float(Decimal(int(value)) / Decimal(_COST_MICROS_PER_USD))


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
        monotonic_clock: Callable[[], float] | None = None,
        utc_clock: Callable[[], datetime] | None = None,
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
        self._monotonic = monotonic_clock or time.monotonic
        self._utc_now = utc_clock or utc_now
        if stop_path is not None:
            self.stop_path = Path(stop_path)
        elif policy is not None:
            self.stop_path = Path(policy.stop_path)
        else:
            # Same identity as jarvis stop / Supervisor: {foundry_home}/EMERGENCY_STOP
            self.stop_path = Path(database.path).resolve().parent / "EMERGENCY_STOP"

    def _dispatch_with_lifecycle_deadline(
        self,
        *,
        deadline_monotonic: float | None,
        **dispatch_kwargs: Any,
    ) -> RoleResult:
        adapter = self.dispatcher.adapter
        run_id = str(dispatch_kwargs["run_id"])
        setter = getattr(adapter, "set_lifecycle_deadline", None)
        clearer = getattr(adapter, "clear_lifecycle_deadline", None)
        if deadline_monotonic is not None and callable(setter):
            setter(run_id, deadline_monotonic)
        try:
            return self.dispatcher.dispatch(**dispatch_kwargs)
        finally:
            if deadline_monotonic is not None and callable(clearer):
                clearer(run_id)

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

    def _resuming_workforce_delta_after_session_loss(
        self,
        *,
        mission_id: str,
        step_id: str,
        package_id: str,
        package_hash: str,
    ) -> bool:
        latest_transition = next(
            (
                event
                for event in reversed(self.database.events(
                    event_types=("mission.state_changed",),
                    readonly=True,
                ))
                if str(event.payload.get("mission_id") or "") == mission_id
            ),
            None,
        )
        if not (
            latest_transition is not None
            and latest_transition.source_type == "supervisor"
            and latest_transition.source_id == "role-reaper"
            and str(latest_transition.payload.get("state") or "")
            == "ACTION_SELECTION"
            and str(latest_transition.payload.get("reason") or "")
            == "resume_after_session_loss"
        ):
            return False
        with self.database.connect(readonly=True) as connection:
            failed_rows = connection.execute(
                "SELECT run_id FROM agent_runs "
                "WHERE mission_id = ? AND step_id = ? "
                "AND role IN ('builder', 'repair') AND status = 'FAILED'",
                (mission_id, step_id),
            ).fetchall()
        failed_run_ids = {str(row["run_id"]) for row in failed_rows}
        if not failed_run_ids:
            return False
        return any(
            event.source_type == "supervisor"
            and event.source_id == "role-dispatcher"
            and str(event.payload.get("run_id") or "") in failed_run_ids
            and str(event.payload.get("package_id") or "") == package_id
            and str(event.payload.get("package_hash") or "") == package_hash
            for event in self.database.events(
                event_types=("agent_run.started",),
                readonly=True,
            )
        )

    @staticmethod
    def _jarvis_predicate_satisfied(*, verifier: RoleResult) -> str | None:
        """Name the runtime-owned predicate that certified this step.

        Only the Fabric's canonical verifier sets this marker, and it sets it
        from the re-derived verdict rather than from model text, so it can
        stand in for the path-shaped artifact predicate the acceptance rule
        looks for elsewhere.
        """

        from ..fabric.model import CANONICAL_PREDICATE_EVIDENCE, VerifyVerdict

        if str(verifier.provider or "") != "fabric":
            return None
        evidence = [str(item) for item in (verifier.evidence or ())]
        if CANONICAL_PREDICATE_EVIDENCE not in evidence:
            return None
        if f"verified:{VerifyVerdict.MATCH.value}" not in evidence:
            return None
        return CANONICAL_PREDICATE_EVIDENCE

    @staticmethod
    def _pass_requires_jarvis_predicate(
        *,
        target: Path,
        expected_artifacts: tuple[str, ...],
        jarvis_predicate: str | None = None,
    ) -> str | None:
        """Model PASS is insufficient: JARVIS must see an artifact predicate."""
        if jarvis_predicate is not None:
            # The verification was itself a JARVIS-owned predicate re-derived
            # from the durable receipt (see the Fabric canonical verifier in
            # fabric/adapter.py).  That is a stronger artifact predicate than
            # the path checks below, so it satisfies the rule without
            # weakening it — but only the one marker the runtime writes does.
            from ..fabric.model import CANONICAL_PREDICATE_EVIDENCE

            if jarvis_predicate != CANONICAL_PREDICATE_EVIDENCE:
                return f"unknown JARVIS predicate marker: {jarvis_predicate}"
            return None
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
        try:
            mission = self.database.row("missions", "mission_id", mission_id) or {}
            created_from = str(mission.get("created_from") or "")
            # Executive-owned owner-goal missions carry their strategy in the
            # Executive plan (attempted_strategies + recovery_plan), not in the
            # MissionLoop repair role. A verifier FAIL here is strategy evidence
            # for the Executive's next materially different mission, never a cue
            # to replay the same contracts under REPAIRING. Fail closed to
            # BLOCKED immediately so decide_continuation can REPLAN once with a
            # grounded strategy instead of burning repair budget on the same one.
            if created_from.startswith((
                "portfolio:owner-goal:",
                "portfolio:executive-step:",
            )):
                repair_budget = min(repair_budget, 0)
        except Exception:
            pass
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
            self._reconcile_accepted_campaign_child(mission_id)
            return
        self._advance(mission_id, "ACTION_SELECTION", next_action=None, run_id=run_id)

    def _reconcile_accepted_campaign_child(self, mission_id: str) -> None:
        """Close final-outcome and campaign-child truth after MissionLoop PASS.

        Campaign children stay RUNNING only while verifier/acceptance evidence
        is still missing. Once that evidence exists, the existing campaign
        reconciler promotes the bound child without owner intervention and
        without writing SUCCEEDED directly into the database.
        """

        try:
            from ..executive.campaigns import (
                rebuild_campaign_state,
                reconcile_campaign_children,
            )
            from ..world_model.connector import reconcile_outcome_learning

            reconcile_outcome_learning(
                self.database,
                mission_id=mission_id,
                source_id="outcome-reconciler",
            )
            if self.database.event_count(event_type="campaign.created") <= 0:
                return
            campaign_ids = {
                child.campaign_id
                for child in rebuild_campaign_state(self.database).children.values()
                if child.mission_id == mission_id
            }
            for campaign_id in sorted(campaign_ids):
                reconcile_campaign_children(
                    self.database,
                    campaign_id,
                    controller="supervisor",
                    stop_path=self.stop_path,
                )
        except Exception:
            return

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
        if state in self._TERMINAL_STATES:
            self._close_stranded_commit_claims(mission_id)

    _TERMINAL_STATES = frozenset({"COMPLETE", "FAILED", "CANCELLED"})

    def _mission_commit_target(self, mission_id: str) -> Path | None:
        """The worktree this mission commits into, from durable admission truth."""
        linked = self.database.events(
            event_types=("initiative.execution_linked",), readonly=True)
        for event in reversed(linked):
            if str(event.payload.get("mission_id") or "") != mission_id:
                continue
            raw = str(event.payload.get("worktree") or "")
            candidate = Path(raw) if raw else None
            if candidate is not None and candidate.is_dir():
                return candidate
            return None
        return None

    def _close_stranded_commit_claims(self, mission_id: str) -> None:
        """Reconcile commit claims this mission can no longer resolve.

        The commit effect identity binds the dispatching run, so a claim left
        STARTED by a run that never reached the connector -- because the
        mission blocked on a later step, or the process died -- can never
        complete on its own.  The pre-live ambiguity audit refuses to promote
        while any such claim is non-terminal, so the mission lifecycle closes
        it here.  Truth comes from Git; nothing is re-performed.
        """
        try:
            target = self._mission_commit_target(mission_id)
            close_mission_commit_claims(
                self.database, mission_id=mission_id, worktree=target)
        except Exception:  # noqa: BLE001 - never mask the state transition
            return


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

        from ..executive.continuous import provider_resume_epoch

        return provider_resume_epoch(
            self.database,
            mission_id=mission_id,
            run_id=run_id,
        )

    def _workforce_lineage_for_run(
        self,
        run_id: str,
    ) -> tuple[str, str, str] | None:
        """Recover one canonical workforce lineage from provider attempts."""

        observed: tuple[str, str, str] | None = None
        for event in self.database.events(
            readonly=True,
            event_types=("provider.dispatch_attempted",),
        ):
            payload = event.payload
            if str(payload.get("run_id") or "") != run_id:
                continue
            lineage = (
                str(payload.get("package_id") or ""),
                str(payload.get("package_hash") or ""),
                str(payload.get("attempt_id") or ""),
            )
            if not any(lineage):
                continue
            if not all(lineage):
                raise ValueError(
                    "accepted replay provider attempt has incomplete "
                    "workforce lineage"
                )
            provider = str(
                payload.get("provider") or payload.get("provider_id") or ""
            )
            model = str(
                payload.get("model") or payload.get("model_id") or ""
            )
            generation = payload.get("attempt_generation")
            ordinal = payload.get("attempt_ordinal")
            if (
                event.source_type != self.dispatcher.source_type
                or event.source_id != self.dispatcher.source_id
                or not isinstance(generation, int)
                or isinstance(generation, bool)
                or not isinstance(ordinal, int)
                or isinstance(ordinal, bool)
                or generation < 0
                or ordinal < 1
                or not provider
                or not model
                or event.idempotency_key
                != (
                    f"provider.dispatch_attempted:{run_id}:{generation}:"
                    f"{ordinal}:{provider}:{model}"
                )
            ):
                raise ValueError(
                    "accepted replay provider attempt identity is not "
                    "canonical"
                )
            if observed is not None and observed != lineage:
                raise ValueError(
                    "accepted replay provider attempts changed workforce "
                    "lineage"
                )
            observed = lineage
        return observed

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
        workforce_context: Mapping[str, Any] | None = None,
    ) -> StepOutcome:
        """One dispatch + verify cycle for a mission step.

        Returns FAILED (mission is REPAIRING) when verification fails and the
        repair budget remains — the caller continues with
        ``run_step(..., run_id=<repair>)`` or calls ``run_until_settled``.
        """
        workforce = dict(workforce_context or {})
        unknown_workforce_fields = set(workforce) - {
            "package_id",
            "package_hash",
            "attempt_id",
            "allowed_paths",
            "required_tests",
            "duration_seconds",
            "cost_budget_usd",
            "deadline",
            "owner_constraints",
            "effect_action_key",
            "worker_preference",
            "worker_preference_evidence",
        }
        if unknown_workforce_fields:
            raise ValueError(
                "unsupported workforce context fields: "
                + ", ".join(sorted(unknown_workforce_fields))
            )
        package_id = str(workforce.get("package_id") or "") or None
        package_hash = str(workforce.get("package_hash") or "") or None
        attempt_id = str(workforce.get("attempt_id") or "") or None
        workforce_lineage = (package_id, package_hash, attempt_id)
        if any(workforce_lineage) and not all(workforce_lineage):
            raise ValueError(
                "workforce context requires package_id, package_hash, and attempt_id"
            )
        workforce_active = all(workforce_lineage)
        requested_workforce_deadline_text = (
            str(workforce.get("deadline") or "").strip() or None
        )
        if requested_workforce_deadline_text is not None and not workforce_active:
            raise ValueError(
                "workforce deadline requires complete package lineage"
            )
        try:
            requested_workforce_deadline = (
                parse(requested_workforce_deadline_text)
                if requested_workforce_deadline_text is not None
                else None
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                "workforce deadline must be an ISO-8601 timestamp"
            ) from error
        workforce_deadline_text = requested_workforce_deadline_text
        workforce_deadline = requested_workforce_deadline
        workforce_entry_utc = (
            self._utc_now() if workforce_active else None
        )
        workforce_entry_monotonic = (
            self._monotonic() if workforce_active else None
        )
        raw_allowed_paths = workforce.get("allowed_paths") or ()
        if not isinstance(raw_allowed_paths, (tuple, list)):
            raise ValueError("workforce allowed_paths must be a sequence")
        workforce_allowed_paths = tuple(
            str(value).strip() for value in raw_allowed_paths
        )
        if any(not value for value in workforce_allowed_paths):
            raise ValueError("workforce allowed_paths contains an empty path")
        raw_required_tests = workforce.get("required_tests") or ()
        if not isinstance(raw_required_tests, (tuple, list)):
            raise ValueError("workforce required_tests must be a sequence")
        workforce_required_tests = tuple(
            str(value).strip() for value in raw_required_tests
        )
        if any(not value for value in workforce_required_tests):
            raise ValueError("workforce required_tests contains an empty command")
        owner_constraints_echoed = (
            workforce.get("owner_constraints") is not None
        )
        raw_owner_constraints = workforce.get("owner_constraints") or ()
        if not isinstance(raw_owner_constraints, (tuple, list)):
            raise ValueError("workforce owner_constraints must be a sequence")
        workforce_owner_constraints = tuple(
            str(value).strip() for value in raw_owner_constraints
        )
        if any(not value for value in workforce_owner_constraints):
            raise ValueError(
                "workforce owner_constraints contains an empty constraint"
            )
        if owner_constraints_echoed and not workforce_active:
            raise ValueError(
                "workforce owner_constraints require complete package lineage"
            )
        workforce_duration_seconds = int(
            workforce["duration_seconds"]
            if "duration_seconds" in workforce
            else 300
        )
        if not 1 <= workforce_duration_seconds <= 86_400:
            raise ValueError(
                "workforce duration_seconds is outside [1, 86400]"
            )
        raw_cost_budget = workforce.get("cost_budget_usd")
        workforce_package_budget_usd = (
            None
            if raw_cost_budget is None
            else float(raw_cost_budget)
        )
        if (
            workforce_package_budget_usd is not None
            and (
                not math.isfinite(workforce_package_budget_usd)
                or workforce_package_budget_usd <= 0
            )
        ):
            raise ValueError(
                "workforce cost_budget_usd must be finite and positive"
            )
        bounded_budgets = [
            value
            for value in (
                float(self.provider_budget_usd),
                workforce_package_budget_usd,
            )
            if value is not None and value > 0
        ]
        workforce_budget_usd = (
            min(bounded_budgets) if bounded_budgets else 0.0
        )
        workforce_cost_limit_usd = (
            workforce_budget_usd
            if workforce_active and workforce_budget_usd > 0
            else None
        )
        workforce_cost_limit_micros = (
            _cost_micros(workforce_cost_limit_usd)
            if workforce_cost_limit_usd is not None
            else None
        )
        workforce_effect_action_key = (
            str(workforce.get("effect_action_key") or "").strip() or None
        )
        worker_preference = (
            str(workforce.get("worker_preference") or "").strip() or None
        )
        raw_preference_evidence = (
            workforce.get("worker_preference_evidence") or ()
        )
        if not isinstance(raw_preference_evidence, (tuple, list)):
            raise ValueError(
                "worker_preference_evidence must be a sequence"
            )
        worker_preference_evidence = tuple(
            str(value).strip() for value in raw_preference_evidence
        )
        if worker_preference and (
            not worker_preference_evidence
            or any(not value for value in worker_preference_evidence)
        ):
            raise ValueError(
                "worker preference requires non-empty evidence references"
            )
        if not worker_preference and worker_preference_evidence:
            raise ValueError(
                "worker preference evidence requires a worker preference"
            )

        stop_reason = self._scoped_stop_reason(mission_id)
        if stop_reason:
            return StepOutcome(outcome=BLOCKED_OUTCOME, reason=stop_reason)
        self._reorient_after_stop(mission_id)
        mission = self._mission(mission_id)
        state = str(mission["state"])
        campaign_context = None
        if self.database.event_count(event_type="campaign.created") > 0:
            try:
                from ..executive.campaigns import (
                    CampaignError,
                    campaign_dispatch_context,
                )

                campaign_context = campaign_dispatch_context(
                    self.database,
                    mission_id=mission_id,
                    step_id=step_id,
                )
            except CampaignError as error:
                return StepOutcome(
                    outcome=BLOCKED_OUTCOME,
                    step_id=step_id,
                    run_id=run_id,
                    reason=f"campaign dispatch contract blocked: {error}",
                )
        campaign_lineage = None
        campaign_routing_request = None
        campaign_isolation_key = None
        campaign_effect_action_key = None
        if campaign_context is not None:
            campaign_lineage = {
                "objective_id": str(campaign_context["objective_id"]),
                "campaign_id": str(campaign_context["campaign_id"]),
                "child_id": str(campaign_context["child_id"]),
            }
            campaign_routing_request = campaign_context["routing_request"]
            campaign_isolation_key = campaign_context.get("isolation_key")
            campaign_effect_action_key = campaign_context.get(
                "effect_action_key"
            )
        if (
            campaign_effect_action_key
            and workforce_effect_action_key
            and str(campaign_effect_action_key)
            != workforce_effect_action_key
        ):
            raise ValueError(
                "campaign and workforce effect identities disagree"
            )
        effective_effect_action_key = (
            workforce_effect_action_key or campaign_effect_action_key
        )
        role = "repair" if state == "REPAIRING" else "builder"
        # Validate the caller's executable step and the immutable workforce
        # package before replay can commit, accept, emit evidence, or advance.
        target = self._validate_step(
            mission, step_id=step_id, capability=capability, target=target,
            expected_effects=expected_effects, rollback=rollback,
        )
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
        existing_builder = self.database.row(
            "agent_runs",
            "run_id",
            run_id,
        )
        replay_prior = None
        if contract_driven and state in {
            "ACTION_SELECTION", "VERIFYING", "ACCEPTING", "COMPLETE",
        }:
            replay_prior = latest_verdict_for_step(
                self.database,
                mission_id,
                step_id,
            )
        accepted_replay_candidate = bool(
            replay_prior is not None
            and replay_prior.get("status") == "PASS"
            and str(replay_prior.get("builder_run_id") or "") == run_id
            and existing_builder is not None
            and existing_builder.get("status") == "COMPLETED"
            and str(existing_builder.get("mission_id") or "") == mission_id
            and str(existing_builder.get("step_id") or "") == step_id
        )
        if workforce_active:
            self.dispatcher.durable_workforce_package(
                str(package_id),
                str(package_hash),
            )
        if accepted_replay_candidate:
            replay_workforce_lineage = self._workforce_lineage_for_run(run_id)
            expected_workforce_lineage = (
                str(package_id or ""),
                str(package_hash or ""),
                str(attempt_id or ""),
            )
            if (
                workforce_active
                and replay_workforce_lineage != expected_workforce_lineage
            ):
                raise ValueError(
                    "accepted replay workforce lineage does not match "
                    "the completed builder run"
                )
            if not workforce_active and replay_workforce_lineage is not None:
                raise ValueError(
                    "accepted workforce replay requires complete package "
                    "lineage"
                )
        canonical_session_attempt = (
            self.dispatcher.workforce_session_attempt(
                package_id=str(package_id),
                package_hash=str(package_hash),
                attempt_id=str(attempt_id),
            )
            if workforce_active
            else None
        )
        replacement_session = bool(
            canonical_session_attempt is not None
            and canonical_session_attempt > 1
        )
        session_loss_recovery = bool(
            workforce_active
            and state == "ACTION_SELECTION"
            and replacement_session
            and self._resuming_workforce_delta_after_session_loss(
                mission_id=mission_id,
                step_id=step_id,
                package_id=str(package_id),
                package_hash=str(package_hash),
            )
        )
        execution_worktree_phase = (
            (
                "job_delta"
                if (
                    role == "repair"
                    or state in {"EXECUTING", "VERIFYING"}
                    or accepted_replay_candidate
                    or replacement_session
                    or session_loss_recovery
                )
                else "builder_admission"
            )
            if workforce_active
            else None
        )
        if workforce_active:
            durable_package = self.dispatcher.validate_workforce_dispatch(
                package_id=package_id,
                package_hash=package_hash,
                attempt_id=attempt_id,
                mission_id=mission_id,
                step_id=step_id,
                role=role,
                resource_id=str(mission["resource_id"]),
                capability=capability,
                duration_seconds=workforce_duration_seconds,
                allowed_paths=workforce_allowed_paths,
                forbidden_paths=forbidden_paths,
                expected_artifacts=expected_artifacts,
                required_tests=workforce_required_tests,
                effect_action_key=effective_effect_action_key,
                worktree_phase=execution_worktree_phase,
            )
            assert durable_package is not None
            durable_deadline_text = (
                str(durable_package.deadline or "").strip() or None
            )
            try:
                durable_deadline = (
                    parse(durable_deadline_text)
                    if durable_deadline_text is not None
                    else None
                )
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "immutable WorkPackage deadline must be an ISO-8601 "
                    "timestamp"
                ) from error
            if (
                requested_workforce_deadline is not None
                and requested_workforce_deadline != durable_deadline
            ):
                raise ValueError(
                    "workforce deadline does not match the immutable "
                    "WorkPackage"
                )
            durable_owner_constraints = tuple(
                durable_package.owner_constraints
            )
            if (
                owner_constraints_echoed
                and workforce_owner_constraints
                != durable_owner_constraints
            ):
                raise ValueError(
                    "workforce owner_constraints do not match the immutable "
                    "WorkPackage"
                )
            workforce_deadline_text = durable_deadline_text
            workforce_deadline = durable_deadline
            workforce_owner_constraints = durable_owner_constraints
        workforce_deadline_monotonic: float | None = None
        if workforce_active:
            assert workforce_entry_utc is not None
            assert workforce_entry_monotonic is not None
            remaining_at_entry = float(workforce_duration_seconds)
            if existing_builder is not None and existing_builder.get("started_at"):
                try:
                    durable_started_at = parse(
                        str(existing_builder["started_at"])
                    )
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        "durable workforce builder start time is invalid"
                    ) from error
                elapsed = max(
                    0.0,
                    (
                        workforce_entry_utc - durable_started_at
                    ).total_seconds(),
                )
                remaining_at_entry = min(
                    remaining_at_entry,
                    float(workforce_duration_seconds) - elapsed,
                )
            if workforce_deadline is not None:
                remaining_at_entry = min(
                    remaining_at_entry,
                    (
                        workforce_deadline - workforce_entry_utc
                    ).total_seconds(),
                )
            workforce_deadline_monotonic = (
                workforce_entry_monotonic + max(0.0, remaining_at_entry)
            )

        def _remaining_workforce_seconds() -> float | None:
            if workforce_deadline_monotonic is None:
                return None
            return max(
                0.0,
                workforce_deadline_monotonic - self._monotonic(),
            )

        # A duplicate delivery after a durable PASS is a replay, not a new
        # mission attempt.  This guard is intentionally scoped to the exact
        # builder run and step; a later repair run or a different step must
        # still execute and produce a new verifier binding.
        if contract_driven and state in {
            "ACTION_SELECTION", "VERIFYING", "ACCEPTING", "COMPLETE",
        }:
            prior = latest_verdict_for_step(self.database, mission_id, step_id)
            builder_row = self.database.row("agent_runs", "run_id", run_id)
            if (prior is not None and prior.get("status") == "PASS"
                    and str(prior.get("builder_run_id") or "") == run_id
                    and builder_row is not None
                    and builder_row.get("status") == "COMPLETED"
                    and str(builder_row.get("mission_id") or "") == mission_id
                    and str(builder_row.get("step_id") or "") == step_id):
                replay_workforce_lineage = self._workforce_lineage_for_run(
                    run_id
                )
                expected_workforce_lineage = (
                    str(package_id or ""),
                    str(package_hash or ""),
                    str(attempt_id or ""),
                )
                if workforce_active:
                    if (
                        replay_workforce_lineage
                        != expected_workforce_lineage
                    ):
                        raise ValueError(
                            "accepted replay workforce lineage does not match "
                            "the completed builder run"
                        )
                elif replay_workforce_lineage is not None:
                    raise ValueError(
                        "accepted workforce replay requires complete package "
                        "lineage"
                    )
                remaining_seconds = _remaining_workforce_seconds()
                if remaining_seconds is not None and remaining_seconds < 1:
                    return StepOutcome(
                        outcome=BLOCKED_OUTCOME,
                        step_id=step_id,
                        run_id=run_id,
                        verdict_id=str(prior.get("verdict_id") or ""),
                        reason=(
                            "workforce package duration/deadline budget was "
                            "exhausted before accepted-run replay"
                        ),
                    )
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
                    if state == "VERIFYING":
                        self._advance(
                            mission_id,
                            "ACCEPTING",
                            run_id=run_id,
                        )
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

        attempt = failed_verdict_count(self.database, mission_id, step_id) + 1
        repair_context_text = ""

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

        if (
            campaign_context is not None
            and campaign_context.get("child_status") == "READY"
        ):
            try:
                from ..executive.campaigns import record_child_state

                record_child_state(
                    self.database,
                    str(campaign_context["child_id"]),
                    "RUNNING",
                    reason="canonical MissionLoop admitted exact campaign child",
                    stop_path=self.stop_path,
                )
            except CampaignError as error:
                return StepOutcome(
                    outcome=BLOCKED_OUTCOME,
                    step_id=step_id,
                    run_id=run_id,
                    reason=f"campaign dispatch admission blocked: {error}",
                )

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
        if workforce_owner_constraints:
            package_constraints = [
                "Immutable workforce package owner constraints:",
                *(
                    f"- {constraint}"
                    for constraint in workforce_owner_constraints
                ),
            ]
            dispatch_instructions = (
                "\n".join(package_constraints)
                + "\n\n"
                + dispatch_instructions
            )
        if workforce_deadline_text is not None:
            dispatch_instructions = (
                "Immutable workforce package execution deadline:\n"
                f"- Absolute UTC deadline: {workforce_deadline_text}"
                + "\n\n"
                + dispatch_instructions
            )
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
            remaining_seconds = _remaining_workforce_seconds()
            if remaining_seconds is not None and remaining_seconds < 1:
                self._advance(mission_id, "BLOCKED", run_id=run_id)
                return StepOutcome(
                    outcome=BLOCKED_OUTCOME,
                    step_id=step_id,
                    run_id=run_id,
                    reason=(
                        "workforce package duration/deadline budget was "
                        "exhausted before builder dispatch"
                    ),
                )
            result = self._dispatch_with_lifecycle_deadline(
                deadline_monotonic=workforce_deadline_monotonic,
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
                allowed_paths=workforce_allowed_paths,
                forbidden_paths=forbidden_paths,
                required_tests=workforce_required_tests,
                attempt=attempt,
                duration_seconds=workforce_duration_seconds,
                budget_usd=workforce_budget_usd,
                routing_request=campaign_routing_request,
                campaign_lineage=campaign_lineage,
                campaign_isolation_key=campaign_isolation_key,
                effect_action_key=effective_effect_action_key,
                package_id=package_id,
                package_hash=package_hash,
                attempt_id=attempt_id,
                worktree_phase=execution_worktree_phase,
                worker_preference=worker_preference,
                worker_preference_evidence=worker_preference_evidence,
            )
        if result.status == BLOCKED:
            effect_wait_key = (
                result.effect_action_key
                if result.error_class in {
                    "provider_effect_identity_mismatch",
                    "provider_effect_reconciliation_required",
                }
                else None
            )
            if (
                str(result.error_class or "").startswith("provider_")
                and (result.wait_for_providers or effect_wait_key)
            ):
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
                    effect_action_key=effect_wait_key,
                    campaign_lineage=campaign_lineage,
                    routing_identity=(
                        campaign_routing_request.identity_hash()
                        if campaign_routing_request is not None else None
                    ),
                    campaign_isolation_key=campaign_isolation_key,
                    stop_path=self.stop_path,
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

        remaining_seconds = _remaining_workforce_seconds()
        if remaining_seconds is not None and remaining_seconds < 1:
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME,
                step_id=step_id,
                run_id=run_id,
                reason=(
                    "workforce package duration/deadline budget was "
                    "exhausted before independent verification"
                ),
            )
        if (
            workforce_cost_limit_micros is not None
            and _cost_micros(max(0.0, float(result.cost_usd)))
            >= workforce_cost_limit_micros
        ):
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME,
                step_id=step_id,
                run_id=run_id,
                reason=(
                    "workforce cost budget was exhausted before "
                    "independent verification"
                ),
            )

        required_test_receipts = tuple(
            f"[jarvis-test] {command} exit=0"
            for command in workforce_required_tests
        )
        missing_test_receipts = tuple(
            receipt
            for receipt in required_test_receipts
            if receipt not in result.evidence
        )
        if missing_test_receipts:
            test_verifier_run_id = f"{run_id}-crash"
            start_verification(
                self.database,
                verdict_id=verdict_id,
                mission_id=mission_id,
                step_id=step_id,
                builder_run_id=run_id,
                verifier_run_id=test_verifier_run_id,
                independence={
                    "builder_provider": result.provider,
                    "verifier_provider": "jarvis",
                    "provider_diverse": True,
                    "session_independent": True,
                    "provider_independent": True,
                    "same_provider_limitation": "",
                },
                criteria=list(criteria),
            )
            findings = [
                "mandatory JARVIS-side test receipt is missing: "
                + receipt
                for receipt in missing_test_receipts
            ]
            record_verification_outcome(
                self.database,
                verdict_id=verdict_id,
                outcome="FAIL",
                findings=findings,
                evidence={
                    "builder_run_id": run_id,
                    "required_test_receipts": list(required_test_receipts),
                    "observed_evidence": list(result.evidence),
                },
                repair_requirements=[
                    "run every mandatory package test through JARVIS evidence "
                    "reconciliation"
                ],
                reason="mandatory workforce tests were not proven",
            )
            return self._repair_or_block(
                mission_id=mission_id,
                step_id=step_id,
                run_id=run_id,
                verdict_id=verdict_id,
                findings=findings,
                reason="mandatory workforce tests were not proven",
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
        verifier_test_context = (
            "\n\nJARVIS-observed mandatory test receipts:\n"
            + "\n".join(f"- {receipt}" for receipt in required_test_receipts)
            if required_test_receipts
            else ""
        )
        verifier_instructions = (
            f"independently verify step {step_id} of {mission_id}: "
            f"verifier contract {VERIFIER_CONTRACT_VERSION}; "
            "read-only inspection only: inspect the frozen artifact "
            "directly and confirm each "
            "criterion. The .jarvis-result.json marker is a control record "
            "only; do not treat its claims as independent proof. "
            "At the end, choose exactly one verdict and emit one line in "
            "this format: VERDICT: PASS. Replace PASS with exactly one of "
            "FAIL, BLOCKED, or NOT_TESTED when appropriate; do not list "
            "the alternatives."
            f"{verifier_test_context}")
        remaining_seconds = _remaining_workforce_seconds()
        if remaining_seconds is not None:
            if remaining_seconds < 1:
                self._advance(mission_id, "BLOCKED", run_id=run_id)
                return StepOutcome(
                    outcome=BLOCKED_OUTCOME,
                    step_id=step_id,
                    run_id=run_id,
                    reason=(
                        "workforce package duration/deadline budget was "
                        "exhausted before independent verification"
                    ),
                )
            verifier_duration_seconds = min(
                300,
                int(math.floor(remaining_seconds)),
            )
        else:
            verifier_duration_seconds = min(
                300,
                workforce_duration_seconds,
            )
        verifier_budget_usd = workforce_budget_usd
        if workforce_cost_limit_micros is not None:
            remaining_budget_micros = (
                workforce_cost_limit_micros
                - _cost_micros(max(0.0, float(result.cost_usd)))
            )
            if remaining_budget_micros <= 0:
                self._advance(mission_id, "BLOCKED", run_id=run_id)
                return StepOutcome(
                    outcome=BLOCKED_OUTCOME,
                    step_id=step_id,
                    run_id=run_id,
                    reason=(
                        "workforce cost budget was exhausted before "
                        "independent verification"
                    ),
                )
            verifier_budget_usd = _cost_usd(remaining_budget_micros)
        verifier_config = self.router.config("verifier")
        base_manifest = self.dispatcher.manifest_for(
            base_verifier_run_id, "verifier", mission_id, step_id,
            str(mission["resource_id"]), "verification",
            verifier_instructions,
            verifier_duration_seconds,
            verifier_budget_usd,
            verifier_provider,
            verifier_config,
            expected_artifacts=expected_artifacts,
            verification_criteria=verifier_criteria,
            allowed_paths=workforce_allowed_paths,
            forbidden_paths=forbidden_paths,
            required_tests=workforce_required_tests,
            attempt=attempt,
            effect_action_key=effective_effect_action_key,
            package_id=package_id,
            package_hash=package_hash,
            attempt_id=attempt_id,
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
            "allowed_paths": list(workforce_allowed_paths),
            "forbidden_paths": list(forbidden_paths),
            "required_tests": list(workforce_required_tests),
            "attempt": attempt,
            "effect_action_key": effective_effect_action_key,
            "package_id": package_id,
            "package_hash": package_hash,
            "attempt_id": attempt_id,
            "verifier_contract_version": VERIFIER_CONTRACT_VERSION,
        }
        if workforce_active:
            verifier_context["required_test_receipts"] = list(
                required_test_receipts
            )
        if workforce_deadline_text is not None:
            verifier_context["deadline"] = workforce_deadline_text

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
                candidate_budget_usd = (
                    verifier_budget_usd if workforce_active else 0.0
                )
                candidate_manifest = self.dispatcher.manifest_for(
                    str(row_dict["run_id"]), "verifier", mission_id, step_id,
                    str(mission["resource_id"]), "verification",
                    verifier_instructions,
                    verifier_duration_seconds,
                    candidate_budget_usd,
                    verifier_provider,
                    verifier_config,
                    expected_artifacts=expected_artifacts,
                    verification_criteria=verifier_criteria,
                    allowed_paths=workforce_allowed_paths,
                    forbidden_paths=forbidden_paths,
                    required_tests=workforce_required_tests,
                    attempt=attempt,
                    effect_action_key=effective_effect_action_key,
                    package_id=package_id,
                    package_hash=package_hash,
                    attempt_id=attempt_id,
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
            verifier_instructions,
            verifier_duration_seconds,
            verifier_budget_usd,
            verifier_provider,
            verifier_config,
            expected_artifacts=expected_artifacts,
            verification_criteria=verifier_criteria,
            allowed_paths=workforce_allowed_paths,
            forbidden_paths=forbidden_paths,
            required_tests=workforce_required_tests,
            attempt=attempt,
            effect_action_key=effective_effect_action_key,
            package_id=package_id,
            package_hash=package_hash,
            attempt_id=attempt_id,
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
        try:
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
        except EventConflict as error:
            # The verdict identity is derived from the pass contract, not from
            # the working tree, so a pass that is resumed after the frozen
            # state drifted cannot re-freeze a different binding under the same
            # identity.  The ledger refuses it; the loop refuses honestly with
            # a precise reason and leaves the resume to a fresh attempt instead
            # of propagating a control-plane exception.
            reason = (
                "the frozen verification binding changed while a verification "
                "pass was open; the ledger refused a second STARTED record for "
                f"{verdict_id} ({error})"
            )
            record_verification_outcome(
                self.database, verdict_id=verdict_id, outcome="BLOCKED",
                findings=[reason],
                evidence={
                    "builder_run_id": run_id,
                    "verifier_run_id": verifier_run_id,
                },
                reason=reason,
                blocking_dependency="verification_binding_drift",
            )
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME, step_id=step_id, run_id=run_id,
                verdict_id=verdict_id, reason=reason,
            )
        invoke_fault_hook(self.fault_hook, "post verification.started")
        stopped = self._stop_blocked(mission_id, step_id=step_id,
                                     run_id=run_id)
        if stopped is not None:
            return stopped
        verifier = self._dispatch_with_lifecycle_deadline(
            deadline_monotonic=workforce_deadline_monotonic,
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
            allowed_paths=workforce_allowed_paths,
            forbidden_paths=forbidden_paths,
            required_tests=workforce_required_tests,
            attempt=attempt,
            duration_seconds=verifier_duration_seconds,
            budget_usd=verifier_budget_usd,
            effect_action_key=effective_effect_action_key,
            package_id=package_id,
            package_hash=package_hash,
            attempt_id=attempt_id,
        )
        stopped = self._stop_blocked(mission_id, step_id=step_id,
                                     run_id=run_id)
        if stopped is not None:
            return stopped
        remaining_seconds = _remaining_workforce_seconds()
        if remaining_seconds is not None and remaining_seconds <= 0:
            reason = (
                "workforce package duration/deadline budget was exhausted "
                "during independent verification"
            )
            record_verification_outcome(
                self.database,
                verdict_id=verdict_id,
                outcome="BLOCKED",
                findings=[],
                evidence=build_terminal_evidence(
                    capture=capture,
                    verifier_session_id=verifier_run_id,
                    extra={"lifecycle_budget_exhausted": True},
                ),
                reason=reason,
                blocking_dependency="lifecycle_budget_exhausted",
            )
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME,
                step_id=step_id,
                run_id=run_id,
                verdict_id=verdict_id,
                reason=reason,
            )
        observed_cost_micros = (
            _cost_micros(max(0.0, float(result.cost_usd)))
            + _cost_micros(max(0.0, float(verifier.cost_usd)))
        )
        if (
            workforce_cost_limit_micros is not None
            and observed_cost_micros > workforce_cost_limit_micros
        ):
            observed_cost = _cost_usd(observed_cost_micros)
            cost_limit_usd = _cost_usd(workforce_cost_limit_micros)
            reason = (
                "workforce observed cost exceeded the package budget "
                f"({observed_cost:.6f} > "
                f"{cost_limit_usd:.6f} USD)"
            )
            record_verification_outcome(
                self.database,
                verdict_id=verdict_id,
                outcome="BLOCKED",
                findings=[],
                evidence=build_terminal_evidence(
                    capture=capture,
                    verifier_session_id=verifier_run_id,
                    extra={
                        "builder_cost_usd": result.cost_usd,
                        "verifier_cost_usd": verifier.cost_usd,
                        "cost_limit_usd": cost_limit_usd,
                    },
                ),
                reason=reason,
                blocking_dependency="cost_budget_exhausted",
            )
            self._advance(mission_id, "BLOCKED", run_id=run_id)
            return StepOutcome(
                outcome=BLOCKED_OUTCOME,
                step_id=step_id,
                run_id=run_id,
                verdict_id=verdict_id,
                reason=reason,
            )
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
            jarvis_predicate = self._jarvis_predicate_satisfied(verifier=verifier)
            refused = self._pass_requires_jarvis_predicate(
                target=target, expected_artifacts=expected_artifacts,
                jarvis_predicate=jarvis_predicate,
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
                    extra=(
                        {
                            "jarvis_predicate": jarvis_predicate,
                            "verifier_answered_by": (
                                getattr(verifier, "provider", "") or ""
                            ),
                        }
                        if jarvis_predicate is not None else None
                    ),
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
                checks={
                    **decision.checks,
                    **(
                        {
                            "jarvis_predicate": jarvis_predicate,
                            "verifier_answered_by": (
                                getattr(verifier, "provider", "") or ""
                            ),
                        }
                        if jarvis_predicate is not None else {}
                    ),
                },
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
