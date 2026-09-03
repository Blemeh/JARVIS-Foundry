"""Role session dispatcher (WP4).

``RoleDispatcher.dispatch`` runs one isolated role session end-to-end:

1. resolve the role's provider chain from the router;
2. replay a COMPLETED run from the ledger when the caller retries the exact
   same context (crashed-caller recovery — no re-run, no new events);
3. acquire a durable lease for the session (no duplicate concurrent session
   for the same mission/step/role key);
4. append an ``agent_run.started`` event carrying the exact context manifest
   hash and the resolved provider identity;
5. try providers in order (preferred -> fallbacks), emitting heartbeats and
   appending ``agent_run.heartbeat`` events while a session runs;
6. on success append ``agent_run.completed`` with the structured result; on
   exhaustion append ``agent_run.failed`` (provider_unavailable) and return a
   BLOCKED result;
7. release the lease.

Everything is durable: a crashed dispatcher leaves the run row in RUNNING
with a lease that expires, and ``roles.reaper.reap_stale_role_runs`` marks it
FAILED (heartbeat_timeout) deterministically (the supervisor's subprocess
reaper handles worker leases, WP2).
"""
from __future__ import annotations

from dataclasses import replace
import json
from typing import Any, Callable, Iterable

from ..clock import isoformat, utc_now
from ..database import FoundryDatabase
from ..events import Event, EventConflict
from ..supervisor.leases import LeaseManager, LeaseUnavailable
from .adapter import (
    ProviderFailure,
    ProviderUnavailable,
    RoleAdapter,
    classify_provider_failure,
)
from .manifest import ContextManifest
from .results import BLOCKED, COMPLETED, FAILED, RoleResult
from .router import (
    ProviderRouteCandidate,
    RoleRouter,
    RoutingRequest,
)
from ..runtime.provider_registry import (
    ProviderReadiness,
    latest_provider_readiness,
    record_provider_readiness,
)
from ..v3_fault import invoke_fault_hook


class DispatchError(RuntimeError):
    pass


class RoleDispatcher:
    def __init__(
        self,
        database: FoundryDatabase,
        router: RoleRouter,
        adapter: RoleAdapter,
        *,
        leases: LeaseManager | None = None,
        source_type: str = "supervisor",
        source_id: str = "role-dispatcher",
        heartbeat_interval_seconds: int = 60,
        fault_hook: Callable[[str], None] | None = None,
        provider_candidates: Iterable[ProviderRouteCandidate] = (),
        max_total_attempts: int = 4,
        max_retries_per_provider: int = 0,
        allowed_data_use: tuple[str, ...] = (),
        allowed_providers: tuple[str, ...] = (),
        denied_providers: tuple[str, ...] = (),
        provider_override_allows_fallback: bool = False,
        routing_constraints: dict[str, Any] | None = None,
    ) -> None:
        self.database = database
        self.router = router
        self.adapter = adapter
        self.leases = leases or LeaseManager(database)
        self.source_type = source_type
        self.source_id = source_id
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.fault_hook = fault_hook
        self.provider_candidates = tuple(provider_candidates)
        self.max_total_attempts = max(1, min(int(max_total_attempts), 12))
        self.max_retries_per_provider = max(
            0, min(int(max_retries_per_provider), 2))
        self.allowed_data_use = tuple(allowed_data_use)
        self.allowed_providers = tuple(allowed_providers)
        self.denied_providers = tuple(denied_providers)
        self.provider_override_allows_fallback = bool(
            provider_override_allows_fallback)
        self.routing_constraints = dict(routing_constraints or {})
        self.startup_grace_seconds = 30  # V7-S5 promotion (cycle-r2b):
        # cover adapter boot before the first session heartbeat
        self._heartbeat_count: dict[str, int] = {}
        # A duplicate caller for the same durable run is allowed to wait for
        # the owner of that run to finish.  This is deliberately bounded: a
        # genuinely orphaned session remains visible as RUNNING/BLOCKED and
        # is recovered by the reaper, while a concurrent retry converges on
        # the one durable result instead of racing its idempotency key.
        self.replay_wait_seconds = 30.0

    # -- durable events -----------------------------------------------------

    def _append(self, event_type: str, payload: dict, *, idempotency_key: str) -> None:
        event = Event.create(
            event_type, payload,
            source_type=self.source_type, source_id=self.source_id,
            idempotency_key=idempotency_key,
        )
        self.database.append(event)

    def _start_run(self, manifest: ContextManifest, provider: str, model: str) -> None:
        # provider is part of the key so a fallback re-start is a distinct
        # durable event; the projection upsert keeps one row per run_id.
        self._append("agent_run.started", {
            "run_id": manifest.run_id,
            "mission_id": manifest.mission_id,
            "step_id": manifest.step_id,
            "role": manifest.role,
            "provider": provider,
            "model": model,
            "context_manifest_hash": manifest.content_hash(),
            "tool_policy": list(manifest.tool_policy),
        }, idempotency_key=f"agent_run.started:{manifest.run_id}:{provider}")

    def _heartbeat(self, run_id: str) -> None:
        # unique per (run, attempt) via an incrementing counter — never
        # collides with a prior heartbeat even within the same microsecond
        self._heartbeat_count[run_id] = self._heartbeat_count.get(run_id, 0) + 1
        self._append("agent_run.heartbeat", {"run_id": run_id},
                     idempotency_key=f"agent_run.heartbeat:{run_id}:{self._heartbeat_count[run_id]}")

    def _complete_run(self, result: RoleResult) -> None:
        self._append("agent_run.completed", {
            "run_id": result.run_id,
            "result": result.to_payload(),
            "cost": {"usd": result.cost_usd},
        }, idempotency_key=f"agent_run.completed:{result.run_id}")

    def _fail_run(self, run_id: str, failure_class: str, detail: str | None = None,
                  *, suffix: str | None = None) -> None:
        payload = {"run_id": run_id, "failure_class": failure_class}
        if detail is not None:
            payload["detail"] = detail
        key = f"agent_run.failed:{run_id}:{failure_class}"
        if suffix:
            key = f"{key}:{suffix}"
        self._append("agent_run.failed", payload, idempotency_key=key)

    # -- dispatch -----------------------------------------------------------

    def _manifest(
        self,
        run_id: str,
        role: str,
        mission_id: str,
        step_id: str | None,
        resource_id: str,
        capability: str,
        instructions: str,
        duration_seconds: int,
        budget_usd: float,
        provider: str,
        config,
        *,
        model: str | None = None,
        expected_artifacts: tuple[str, ...] = (),
        verification_criteria: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        attempt: int = 1,
    ) -> ContextManifest:
        return ContextManifest(
            run_id=run_id, role=role, mission_id=mission_id,
            step_id=step_id, resource_id=resource_id, capability=capability,
            provider=provider, model=model or config.preferred_model,
            isolation_group=config.independence_group,
            tool_policy=config.required_capabilities,
            budget_usd=budget_usd, duration_seconds=duration_seconds,
            instructions=instructions,
            expected_artifacts=expected_artifacts,
            verification_criteria=verification_criteria,
            forbidden_paths=forbidden_paths,
            attempt=attempt,
        )

    def manifest_for(
        self,
        run_id: str,
        role: str,
        mission_id: str,
        step_id: str | None,
        resource_id: str,
        capability: str,
        instructions: str,
        duration_seconds: int,
        budget_usd: float,
        provider: str,
        config,
        *,
        model: str | None = None,
        expected_artifacts: tuple[str, ...] = (),
        verification_criteria: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        attempt: int = 1,
    ) -> ContextManifest:
        """Build the exact manifest that :meth:`dispatch` will persist.

        MissionLoop uses this public, side-effect-free helper to decide
        whether a historical verifier session is compatible before selecting
        a legacy or versioned identity.  Keeping construction in one place
        prevents the identity decision and the dispatched context from
        drifting apart.
        """
        return self._manifest(
            run_id, role, mission_id, step_id, resource_id, capability,
            instructions, duration_seconds, budget_usd, provider, config,
            model=model,
            expected_artifacts=expected_artifacts,
            verification_criteria=verification_criteria,
            forbidden_paths=forbidden_paths,
            attempt=attempt,
        )

    def _replay_completed(self, run_id: str, prospective: ContextManifest) -> RoleResult | None:
        """Replay a completed run from the ledger when the caller retries the
        exact same context (crashed-caller recovery). Returns None when there
        is nothing to replay (unknown, running, failed, or changed context)."""
        row = self.database.row("agent_runs", "run_id", run_id)
        if row is None or row["status"] != "COMPLETED":
            return None
        # Recompute the manifest hash using the persisted execution identity
        # so machine-local default model changes do not break replay.
        if row.get("provider") and row.get("model"):
            prospective = replace(
                prospective,
                provider=str(row["provider"]),
                model=str(row["model"]),
            )
        if row["context_manifest_hash"] != prospective.content_hash():
            return None  # changed context under the same run_id: not a replay
        try:
            return RoleResult.from_payload(json.loads(str(row["result_json"])))
        except (KeyError, TypeError, ValueError):
            return None

    def _candidate_snapshot(self) -> tuple[ProviderRouteCandidate, ...]:
        """Overlay configured profiles with the latest durable readiness."""

        current: list[ProviderRouteCandidate] = []
        for candidate in self.provider_candidates:
            observed = latest_provider_readiness(
                self.database, candidate.provider, candidate.model)
            if observed is None:
                current.append(candidate)
                continue
            readiness = str(
                observed.get("readiness") or observed.get("status") or "UNKNOWN"
            ).upper()
            if readiness in {"READY", "DEGRADED"}:
                health = "READY" if readiness == "READY" else "DEGRADED"
                availability = "AVAILABLE"
            else:
                health = readiness
                availability = readiness
            current.append(replace(
                candidate,
                readiness=readiness,
                health=health,
                auth_state=str(observed.get("auth_state") or candidate.auth_state),
                availability=availability,
                evidence_refs=tuple(dict.fromkeys([
                    *candidate.evidence_refs,
                    *(str(item) for item in observed.get("evidence_refs") or ()),
                ])),
            ))
        return tuple(current)

    def _record_provider_attempt(
        self,
        *,
        run_id: str,
        mission_id: str,
        step_id: str | None,
        role: str,
        candidate: ProviderRouteCandidate,
        ordinal: int,
    ) -> None:
        self._append("provider.dispatch_attempted", {
            "run_id": run_id,
            "mission_id": mission_id,
            "step_id": step_id,
            "role": role,
            "provider_id": candidate.provider,
            "provider": candidate.provider,
            "model_id": candidate.model,
            "model": candidate.model,
            "attempt_ordinal": ordinal,
        }, idempotency_key=(
            f"provider.dispatch_attempted:{run_id}:{ordinal}:"
            f"{candidate.provider}:{candidate.model}"))

    def _attempt_history(self, run_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        events = self.database.events(readonly=True)
        last_resume = -1
        for index, event in enumerate(events):
            if (
                event.event_type == "mission.provider_wait_resumed"
                and str(event.payload.get("run_id") or "") == run_id
            ):
                last_resume = index
        terminal_attempts = {
            (
                int(event.payload.get("attempt_ordinal") or 0),
                str(event.payload.get("provider")
                    or event.payload.get("provider_id") or ""),
            )
            for index, event in enumerate(events)
            if index > last_resume
            and event.event_type == "provider.dispatch_failed"
            and str(event.payload.get("run_id") or "") == run_id
        }
        for index, event in enumerate(events):
            if index <= last_resume or event.event_type != "provider.dispatch_attempted":
                continue
            attempt_identity = (
                int(event.payload.get("attempt_ordinal") or 0),
                str(event.payload.get("provider")
                    or event.payload.get("provider_id") or ""),
            )
            # A durable attempt marker without a classified terminal failure
            # is a crash window before/inside adapter dispatch, not consumed
            # retry budget.  Replay that exact attempt identity once.
            if (
                str(event.payload.get("run_id") or "") == run_id
                and attempt_identity in terminal_attempts
            ):
                rows.append(dict(event.payload))
        return rows

    def _pending_effect_reconciliation(
        self, run_id: str,
    ) -> tuple[str, str] | None:
        required: list[tuple[int, str, str]] = []
        resolved: set[str] = set()
        for index, event in enumerate(self.database.events(readonly=True)):
            if str(event.payload.get("run_id") or "") != run_id:
                continue
            action_key = str(event.payload.get("effect_action_key") or "")
            if not action_key:
                continue
            if event.event_type == "provider.effect_reconciliation_required":
                required.append((index, action_key, str(event.payload.get("provider") or "")))
            elif event.event_type == "provider.effect_reconciled":
                resolved.add(action_key)
        for _index, action_key, provider in reversed(required):
            if action_key in resolved:
                continue
            try:
                reconciled = any(
                    event.event_type == "effect.reconciled"
                    and str(event.payload.get("action_key") or "") == action_key
                    for event in self.database.events(readonly=True)
                )
                if reconciled:
                    self._append("provider.effect_reconciled", {
                        "run_id": run_id,
                        "provider": provider,
                        "effect_action_key": action_key,
                        "status": "RECONCILED",
                    }, idempotency_key=(
                        f"provider.effect_reconciled:{run_id}:"
                        f"{action_key}:RECONCILED"))
                    continue
            except Exception:
                pass
            return action_key, provider
        return None

    @staticmethod
    def _failure_error_class(failure_class: str) -> str:
        return {
            "RATE_LIMITED": "provider_rate_limited",
            "OUTAGE": "provider_unavailable",
            "DEGRADED": "provider_degraded",
            "INCOMPATIBLE": "provider_incompatible",
            "BLOCKED_POLICY": "provider_policy_blocked",
            "OWNER_RESTRICTED": "provider_owner_restricted",
            "COST_BLOCKED": "provider_cost_blocked",
            "COST_LIMIT": "provider_cost_blocked",
        }.get(str(failure_class).upper(), "provider_unavailable")

    def dispatch(
        self,
        *,
        role: str,
        mission_id: str,
        resource_id: str,
        capability: str,
        step_id: str | None = None,
        run_id: str | None = None,
        instructions: str = "",
        duration_seconds: int = 300,
        budget_usd: float = 0.0,
        provider_override: str | None = None,
        expected_artifacts: tuple[str, ...] = (),
        verification_criteria: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        attempt: int = 1,
    ) -> RoleResult:
        import uuid
        run_id = run_id or f"run-{uuid.uuid4()}"
        lease_key = f"role:{role}:{mission_id}:{step_id or 'start'}"

        config = self.router.config(role)
        legacy_chain = (
            (provider_override,) if provider_override
            else self.router.provider_chain(role)
        )
        candidates = self._candidate_snapshot()
        route_failure: str | None = None
        if candidates:
            if provider_override and not self.provider_override_allows_fallback:
                candidates = tuple(
                    item for item in candidates
                    if item.provider == provider_override)
            raw_constraints = self.routing_constraints
            role_constraints = raw_constraints.get(role)
            constraints = (
                dict(role_constraints)
                if isinstance(role_constraints, dict)
                else raw_constraints
            )
            configured_cost = constraints.get("max_cost_usd")
            max_cost = (
                budget_usd if budget_usd > 0
                else float(configured_cost) if configured_cost is not None
                else None
            )
            decision = self.router.route(
                RoutingRequest(
                    task_type=role,
                    required_tools=tuple(
                        str(item) for item in constraints.get("required_tools") or ()),
                    unattended=bool(constraints.get("unattended", True)),
                    context_tokens=(
                        int(constraints["context_tokens"])
                        if constraints.get("context_tokens") is not None else None),
                    latency_budget_ms=(
                        float(constraints["latency_budget_ms"])
                        if constraints.get("latency_budget_ms") is not None else None),
                    reliability_floor=(
                        float(constraints["reliability_floor"])
                        if constraints.get("reliability_floor") is not None else None),
                    max_cost_usd=max_cost,
                    preferred_provider=(provider_override or config.preferred_provider),
                    allowed_providers=self.allowed_providers,
                    denied_providers=self.denied_providers,
                    allowed_data_use=self.allowed_data_use,
                ),
                candidates,
                role=role,
            )
            self._append("provider.route_decided", {
                "run_id": run_id,
                "mission_id": mission_id,
                "step_id": step_id,
                "role": role,
                **decision.to_dict(),
            }, idempotency_key=(
                f"provider.route_decided:{run_id}:{attempt}:"
                f"{decision.status}:{decision.failure_class or 'selected'}"))
            chain_candidates = tuple(decision.attempted)
            route_failure = decision.failure_class
        else:
            chain_candidates = tuple(ProviderRouteCandidate(
                provider=provider,
                model=config.preferred_model,
                readiness="READY",
                health="READY",
                auth_state="READY",
                availability="AVAILABLE",
                noninteractive_allowed=True,
            ) for provider in legacy_chain)

        if not chain_candidates:
            failure_class = route_failure or "NO_CANDIDATES"
            error_class = self._failure_error_class(failure_class)
            wait_providers = tuple(
                dict.fromkeys(item.provider for item in candidates)
            )
            detail = (
                f"no safe provider satisfied role {role}: {failure_class}")
            self._fail_run(run_id, error_class, detail)
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider="", model="",
                verdict="BLOCKED", error_class=error_class,
                error_detail=detail, wait_for_providers=wait_providers,
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )

        pending_effect = self._pending_effect_reconciliation(run_id)
        if pending_effect is not None:
            action_key, provider = pending_effect
            detail = (
                f"provider effect {action_key} remains ambiguous; reconcile before fallback")
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED,
                provider=provider, model="", verdict="BLOCKED",
                error_class="provider_effect_reconciliation_required",
                error_detail=detail, effect_action_key=action_key,
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )

        if not legacy_chain and not candidates:
            self._fail_run(run_id, "provider_unavailable", f"no providers configured for role {role}")
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider="", model="",
                verdict="BLOCKED", error_class="provider_unavailable",
                error_detail=f"no providers configured for role {role}",
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )

        # 1b. crashed-caller recovery: identical context + completed run replays
        # from the ledger (no re-run, no new events). A changed context under
        # the same run_id proceeds and fails loudly on the event idempotency
        # key (first write stands, WP3 B6 semantics).
        prospective = self._manifest(
            run_id, role, mission_id, step_id, resource_id, capability,
            instructions, duration_seconds, budget_usd,
            chain_candidates[0].provider, config,
            model=chain_candidates[0].model,
            expected_artifacts=expected_artifacts,
            verification_criteria=verification_criteria,
            forbidden_paths=forbidden_paths,
            attempt=attempt,
        )
        replayed = self._replay_completed(run_id, prospective)
        if replayed is not None:
            return replayed
        completed = self.database.row("agent_runs", "run_id", run_id)
        if completed is not None and completed.get("status") == "COMPLETED":
            # A completed identity is immutable.  Retry history would
            # otherwise suppress every candidate before agent_run.started
            # gets a chance to enforce the original first-write conflict.
            raise EventConflict(
                f"completed run {run_id} was redispatched with changed context")

        # 2. lease
        try:
            self.leases.acquire(lease_key, run_id,
                               duration_seconds + self.startup_grace_seconds)
        except LeaseUnavailable as error:
            # Only a caller retrying the SAME run id may wait.  A different
            # run id is a competing session and must be blocked immediately;
            # this preserves the lease invariant and avoids turning a stale
            # lease into an unbounded wait.
            existing = self.database.row("agent_runs", "run_id", run_id)
            if existing is not None and existing.get("status") == "RUNNING":
                import time
                deadline = time.monotonic() + self.replay_wait_seconds
                while time.monotonic() < deadline:
                    time.sleep(0.05)
                    settled = self.database.row("agent_runs", "run_id", run_id)
                    if settled is None:
                        break
                    if settled.get("status") == "COMPLETED":
                        replayed = self._replay_completed(run_id, prospective)
                        if replayed is not None:
                            return replayed
                        # A terminal row with a changed context is never
                        # silently reused; the caller receives a precise
                        # conflict instead of corrupting the old run.
                        break
                    if settled.get("status") == "FAILED":
                        break
                settled = self.database.row("agent_runs", "run_id", run_id)
                if settled is not None and settled.get("status") == "COMPLETED":
                    replayed = self._replay_completed(run_id, prospective)
                    if replayed is not None:
                        return replayed
                return RoleResult(
                    run_id=run_id, role=role, status=BLOCKED,
                    provider=str(existing.get("provider") or ""),
                    model=str(existing.get("model") or ""),
                    verdict="BLOCKED", error_class="run_in_progress",
                    error_detail=(
                        f"run {run_id} is already owned by a live dispatcher; "
                        "the bounded replay wait expired"),
                    started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
                )
            # Do not append agent_run.failed for a competing caller: doing so
            # would mutate the owner’s still-live run into FAILED.  A lease
            # refusal is an honest, non-mutating result for this caller.  If
            # this run id has never been projected, the denial itself is still
            # recorded for auditability (the projection remains empty).
            if existing is None:
                self._fail_run(run_id, "lease_unavailable", str(error))
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider="", model="",
                verdict="BLOCKED", error_class="lease_unavailable",
                error_detail=str(error),
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )
        except Exception as error:
            self._fail_run(run_id, "lease_unavailable", str(error))
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider="", model="",
                verdict="BLOCKED", error_class="lease_unavailable",
                error_detail=str(error),
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )

        last_error: Exception | None = None
        tried: list[str] = []
        history = self._attempt_history(run_id)
        provider_counts: dict[str, int] = {}
        for row in history:
            provider = str(row.get("provider") or row.get("provider_id") or "")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
        total_attempts = len(history)
        failure_classes: list[str] = []
        try:
            queue = list(chain_candidates)
            while queue and total_attempts < self.max_total_attempts:
                candidate = queue.pop(0)
                provider = candidate.provider
                model = candidate.model
                count = provider_counts.get(provider, 0)
                if count >= 1 + self.max_retries_per_provider:
                    continue
                total_attempts += 1
                provider_counts[provider] = count + 1
                manifest = self._manifest(
                    run_id, role, mission_id, step_id, resource_id, capability,
                    instructions, duration_seconds, budget_usd,
                    provider, config, model=model,
                    expected_artifacts=expected_artifacts,
                    verification_criteria=verification_criteria,
                    forbidden_paths=forbidden_paths,
                    attempt=attempt,
                )
                self._record_provider_attempt(
                    run_id=run_id,
                    mission_id=mission_id,
                    step_id=step_id,
                    role=role,
                    candidate=candidate,
                    ordinal=total_attempts,
                )
                self._start_run(manifest, provider, model)
                # The start event is durable before the provider adapter is
                # entered.  A disposable injector can interrupt here and a
                # replay of the same run_id will resume without a second
                # mutation.
                invoke_fault_hook(self.fault_hook, "post agent_run.started")
                tried.append(provider)

                def _beat(rid: str = run_id) -> None:
                    # DIR-2026-08-12 item 2: every heartbeat renews the
                    # session lease — a live, heartbeating process must
                    # never be reaped. A lease that cannot be renewed
                    # (reaped/gone) raises LeaseUnavailable: the session
                    # cannot complete durably and fails honestly.
                    self._heartbeat(rid)
                    self.leases.renew(lease_key, rid, duration_seconds)

                try:
                    result = self.adapter.dispatch(
                        manifest,
                        heartbeat=_beat,
                    )
                except ProviderFailure as error:
                    last_error = error
                    failure_class = str(error.failure_class or "OUTAGE").upper()
                    failure_classes.append(failure_class)
                    error_class = self._failure_error_class(failure_class)
                    self._fail_run(
                        run_id, error_class, str(error),
                        suffix=f"{provider}:{total_attempts}")
                    self._append("provider.dispatch_failed", {
                        "run_id": run_id,
                        "mission_id": mission_id,
                        "step_id": step_id,
                        "role": role,
                        "provider_id": provider,
                        "provider": provider,
                        "model_id": model,
                        "model": model,
                        "failure_class": failure_class,
                        "retryable": error.retryable,
                        "retry_after_seconds": error.retry_after_seconds,
                        "possible_effect": error.possible_effect,
                        "effect_action_key": error.effect_action_key,
                        "attempt_ordinal": total_attempts,
                    }, idempotency_key=(
                        f"provider.dispatch_failed:{run_id}:{total_attempts}:"
                        f"{provider}:{failure_class}"))
                    readiness = (
                        ProviderReadiness.RATE_LIMITED
                        if failure_class == "RATE_LIMITED" else
                        ProviderReadiness.INCOMPATIBLE
                        if failure_class == "INCOMPATIBLE" else
                        ProviderReadiness.BLOCKED_POLICY
                        if failure_class in {"BLOCKED_POLICY", "OWNER_RESTRICTED"} else
                        ProviderReadiness.DEGRADED
                        if failure_class == "DEGRADED" else
                        ProviderReadiness.OUTAGE
                    )
                    record_provider_readiness(
                        self.database,
                        provider_id=provider,
                        model=model,
                        readiness=readiness,
                        reason=str(error),
                        retry_after_seconds=error.retry_after_seconds,
                        notify_wakes=False,
                        source_id=self.source_id,
                    )
                    if error.possible_effect:
                        action_key = str(error.effect_action_key or "").strip()
                        if not action_key:
                            action_key = f"provider-effect:{mission_id}:{step_id or 'start'}:{run_id}"
                        self._append("provider.effect_reconciliation_required", {
                            "run_id": run_id,
                            "mission_id": mission_id,
                            "step_id": step_id,
                            "provider": provider,
                            "model": model,
                            "effect_action_key": action_key,
                            "failure_class": failure_class,
                            "reason": str(error)[:500],
                        }, idempotency_key=(
                            f"provider.effect_reconciliation_required:"
                            f"{run_id}:{action_key}"))
                        return RoleResult(
                            run_id=run_id, role=role, status=BLOCKED,
                            provider=provider, model=model, verdict="BLOCKED",
                            error_class="provider_effect_reconciliation_required",
                            error_detail=(
                                "possible external mutation must be reconciled "
                                "before any alternate provider dispatch"),
                            effect_action_key=action_key,
                            started_at=isoformat(utc_now()),
                            finished_at=isoformat(utc_now()),
                        )
                    # A retry is only justified when explicitly classified,
                    # has no required backoff, and remains within both bounds.
                    if (
                        error.retryable
                        and not error.retry_after_seconds
                        and provider_counts[provider] < 1 + self.max_retries_per_provider
                    ):
                        queue.insert(0, replace(candidate, readiness="DEGRADED"))
                    continue
                except Exception as error:  # adapter crash -> failed run
                    last_error = error
                    self._fail_run(run_id, "adapter_crash", str(error), suffix=provider)
                    return RoleResult(
                        run_id=run_id, role=role, status=FAILED,
                        provider=provider, model=model,
                        verdict="NOT_TESTED", error_class="adapter_crash",
                        error_detail=str(error),
                        started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
                    )
                if result.status == COMPLETED:
                    self._complete_run(result)
                    self._append("provider.dispatch_succeeded", {
                        "run_id": run_id,
                        "mission_id": mission_id,
                        "step_id": step_id,
                        "role": role,
                        "provider_id": result.provider,
                        "provider": result.provider,
                        "model_id": result.model,
                        "model": result.model,
                        "attempt_ordinal": total_attempts,
                        "fallback_used": len(tried) > 1,
                        "cost_usd": result.cost_usd,
                    }, idempotency_key=(
                        f"provider.dispatch_succeeded:{run_id}:"
                        f"{result.provider}:{result.model}"))
                    record_provider_readiness(
                        self.database,
                        provider_id=result.provider,
                        model=result.model,
                        readiness=ProviderReadiness.READY,
                        reason="provider dispatch completed",
                        evidence_refs=(f"agent_run:{run_id}",),
                        notify_wakes=False,
                        source_id=self.source_id,
                    )
                    return result
                result_failure = {
                    "provider_rate_limited": "RATE_LIMITED",
                    "provider_degraded": "DEGRADED",
                    "provider_incompatible": "INCOMPATIBLE",
                    "provider_policy_blocked": "BLOCKED_POLICY",
                    "provider_cost_blocked": "COST_BLOCKED",
                }.get(
                    str(result.error_class or ""),
                    classify_provider_failure(
                        f"{result.error_class or ''} {result.error_detail or ''}"),
                )
                if result.error_class in {
                    "provider_unavailable", "provider_rate_limited",
                    "provider_degraded", "provider_incompatible",
                    "provider_policy_blocked", "provider_cost_blocked",
                }:
                    last_error = ProviderFailure(
                        result.error_detail or result.error_class or "provider failed",
                        failure_class=result_failure,
                    )
                    failure_classes.append(result_failure)
                    self._fail_run(
                        run_id, result.error_class or "provider_unavailable",
                        result.error_detail, suffix=f"{provider}:{total_attempts}")
                    self._append("provider.dispatch_failed", {
                        "run_id": run_id,
                        "mission_id": mission_id,
                        "step_id": step_id,
                        "role": role,
                        "provider_id": provider,
                        "provider": provider,
                        "model_id": model,
                        "model": model,
                        "failure_class": result_failure,
                        "retryable": False,
                        "possible_effect": False,
                        "effect_action_key": None,
                        "attempt_ordinal": total_attempts,
                    }, idempotency_key=(
                        f"provider.dispatch_failed:{run_id}:{total_attempts}:"
                        f"{provider}:{result_failure}"))
                    readiness = (
                        ProviderReadiness.RATE_LIMITED
                        if result_failure == "RATE_LIMITED" else
                        ProviderReadiness.INCOMPATIBLE
                        if result_failure == "INCOMPATIBLE" else
                        ProviderReadiness.BLOCKED_POLICY
                        if result_failure == "BLOCKED_POLICY" else
                        ProviderReadiness.DEGRADED
                        if result_failure == "DEGRADED" else
                        ProviderReadiness.OUTAGE
                    )
                    record_provider_readiness(
                        self.database,
                        provider_id=provider,
                        model=model,
                        readiness=readiness,
                        reason=result.error_detail or result.error_class or "provider failed",
                        notify_wakes=False,
                        source_id=self.source_id,
                    )
                    continue
                self._fail_run(run_id, result.error_class or "session_failed",
                               result.error_detail, suffix=provider)
                return result

            # 4. all providers exhausted
            detail = f"all providers failed for role {role}: tried={tried}; last={last_error}"
            terminal_class = failure_classes[-1] if failure_classes else (
                route_failure or "OUTAGE")
            error_class = self._failure_error_class(terminal_class)
            if not self.provider_candidates:
                # Preserve the pre-V12 public result contract for legacy
                # single-provider dispatch while classification remains
                # durable in provider.dispatch_failed.
                error_class = "provider_unavailable"
            self._fail_run(run_id, error_class, detail, suffix="exhausted")
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider=tried[-1] if tried else "",
                model=(
                    next((item.model for item in reversed(chain_candidates)
                          if item.provider in tried), config.preferred_model)
                ), verdict="BLOCKED",
                error_class=error_class, error_detail=detail,
                wait_for_providers=tuple(dict.fromkeys(
                    item.provider for item in chain_candidates
                )),
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )
        finally:
            self.leases.release(lease_key, run_id)
