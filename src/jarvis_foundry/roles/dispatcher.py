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
from typing import Any, Callable, Iterable, Mapping

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

_TRANSIENT_PROVIDER_FAILURES = frozenset({
    "RATE_LIMITED", "OUTAGE", "DEGRADED",
    "HEALTH_UNAVAILABLE", "HEALTH_UNKNOWN",
    "AUTH_UNAVAILABLE", "AUTH_UNKNOWN",
    "PROVIDER_UNAVAILABLE", "PROVIDER_UNKNOWN",
})
_INCOMPATIBLE_PROVIDER_FAILURES = frozenset({
    "INCOMPATIBLE", "CAPABILITY_MISMATCH", "DIRECTIVE_MISMATCH",
    "CONTEXT_LIMIT", "LATENCY_LIMIT", "RELIABILITY_LIMIT",
})


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

    @staticmethod
    def _bounded_min(*values: Any) -> float | None:
        present = [float(value) for value in values if value is not None]
        return min(present) if present else None

    @staticmethod
    def _allowed_intersection(
        requested: tuple[str, ...], policy: tuple[str, ...], *, empty: str,
    ) -> tuple[str, ...]:
        if requested and policy:
            intersection = tuple(
                item for item in requested if item in set(policy)
            )
            return intersection or (empty,)
        return requested or policy

    def _effective_routing_request(
        self,
        *,
        role: str,
        config: Any,
        budget_usd: float,
        provider_override: str | None,
        routing_request: RoutingRequest | None,
    ) -> RoutingRequest:
        """Merge child requirements with the stricter runtime policy."""

        raw_constraints = self.routing_constraints
        role_constraints = raw_constraints.get(role)
        constraints = (
            dict(role_constraints)
            if isinstance(role_constraints, dict)
            else raw_constraints
        )
        requested = routing_request or RoutingRequest(task_type=role)
        configured_cost = constraints.get("max_cost_usd")
        max_cost = self._bounded_min(
            requested.max_cost_usd,
            budget_usd if budget_usd > 0 else None,
            float(configured_cost) if configured_cost is not None else None,
        )
        allowed = self._allowed_intersection(
            requested.allowed_providers,
            self.allowed_providers,
            empty="__NO_PROVIDER_ALLOWED__",
        )
        allowed_data_use = self._allowed_intersection(
            requested.allowed_data_use,
            self.allowed_data_use,
            empty="__NO_DATA_USE_ALLOWED__",
        )
        required_tools = tuple(dict.fromkeys((
            *requested.required_tools,
            *(str(item) for item in constraints.get("required_tools") or ()),
        )))
        context_tokens = max(
            (
                int(value) for value in (
                    requested.context_tokens,
                    constraints.get("context_tokens"),
                ) if value is not None
            ),
            default=None,
        )
        reliability_floor = max(
            (
                float(value) for value in (
                    requested.reliability_floor,
                    constraints.get("reliability_floor"),
                ) if value is not None
            ),
            default=None,
        )
        latency_budget = self._bounded_min(
            requested.latency_budget_ms,
            constraints.get("latency_budget_ms"),
        )
        return replace(
            requested,
            required_tools=required_tools,
            unattended=(
                requested.unattended
                or bool(constraints.get("unattended", True))
            ),
            context_tokens=context_tokens,
            latency_budget_ms=latency_budget,
            reliability_floor=reliability_floor,
            max_cost_usd=max_cost,
            preferred_provider=(
                provider_override
                or requested.preferred_provider
                or config.preferred_provider
            ),
            allowed_providers=allowed,
            denied_providers=tuple(dict.fromkeys((
                *requested.denied_providers,
                *self.denied_providers,
            ))),
            allowed_data_use=allowed_data_use,
        )

    @staticmethod
    def _lineage_payload(
        campaign_lineage: Mapping[str, str] | None,
        *,
        run_id: str,
        routing_request: RoutingRequest | None,
        effect_action_key: str | None,
    ) -> dict[str, Any]:
        if not campaign_lineage:
            return {}
        return {
            "objective_id": str(campaign_lineage.get("objective_id") or ""),
            "campaign_id": str(campaign_lineage.get("campaign_id") or ""),
            "child_id": str(campaign_lineage.get("child_id") or ""),
            "builder_run_id": run_id,
            "routing_identity": (
                routing_request.identity_hash() if routing_request is not None else None
            ),
            "effect_action_key": effect_action_key,
        }

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
        payload = {
            "run_id": manifest.run_id,
            "mission_id": manifest.mission_id,
            "step_id": manifest.step_id,
            "role": manifest.role,
            "provider": provider,
            "model": model,
            "context_manifest_hash": manifest.content_hash(),
            "tool_policy": list(manifest.tool_policy),
        }
        if manifest.campaign_id:
            payload.update({
                "objective_id": manifest.objective_id,
                "campaign_id": manifest.campaign_id,
                "child_id": manifest.child_id,
                "builder_run_id": manifest.run_id,
                "routing_identity": manifest.routing_identity,
                "effect_action_key": manifest.effect_action_key,
            })
        self._append(
            "agent_run.started",
            payload,
            idempotency_key=f"agent_run.started:{manifest.run_id}:{provider}",
        )

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
        routing_request: RoutingRequest | None = None,
        campaign_lineage: Mapping[str, str] | None = None,
        campaign_isolation_key: str | None = None,
        effect_action_key: str | None = None,
    ) -> ContextManifest:
        lineage = dict(campaign_lineage or {})
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
            objective_id=(
                str(lineage.get("objective_id")) if lineage.get("objective_id") else None
            ),
            campaign_id=(
                str(lineage.get("campaign_id")) if lineage.get("campaign_id") else None
            ),
            child_id=(
                str(lineage.get("child_id")) if lineage.get("child_id") else None
            ),
            campaign_isolation_key=campaign_isolation_key,
            routing_requirements=(
                routing_request.to_dict() if routing_request is not None else {}
            ),
            routing_identity=(
                routing_request.identity_hash() if routing_request is not None else None
            ),
            effect_action_key=effect_action_key,
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
        routing_request: RoutingRequest | None = None,
        campaign_lineage: Mapping[str, str] | None = None,
        campaign_isolation_key: str | None = None,
        effect_action_key: str | None = None,
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
            routing_request=routing_request,
            campaign_lineage=campaign_lineage,
            campaign_isolation_key=campaign_isolation_key,
            effect_action_key=effect_action_key,
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
        lineage: Mapping[str, Any] | None = None,
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
            "estimated_cost_usd": candidate.cost_usd,
            **dict(lineage or {}),
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
            "CAPABILITY_MISMATCH": "provider_incompatible",
            "DIRECTIVE_MISMATCH": "provider_incompatible",
            "CONTEXT_LIMIT": "provider_incompatible",
            "LATENCY_LIMIT": "provider_incompatible",
            "RELIABILITY_LIMIT": "provider_incompatible",
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
        routing_request: RoutingRequest | None = None,
        campaign_lineage: Mapping[str, str] | None = None,
        campaign_isolation_key: str | None = None,
        effect_action_key: str | None = None,
    ) -> RoleResult:
        import uuid
        run_id = run_id or f"run-{uuid.uuid4()}"
        lease_key = f"role:{role}:{mission_id}:{step_id or 'start'}"

        if campaign_lineage:
            required_lineage = {
                str(campaign_lineage.get(name) or "")
                for name in ("objective_id", "campaign_id", "child_id")
            }
            if "" in required_lineage or routing_request is None:
                raise ValueError(
                    "campaign dispatch requires complete lineage and routing contract"
                )

        config = self.router.config(role)
        legacy_chain = (
            (provider_override,) if provider_override
            else self.router.provider_chain(role)
        )
        candidates = self._candidate_snapshot()
        route_failure: str | None = None
        effective_request = self._effective_routing_request(
            role=role,
            config=config,
            budget_usd=budget_usd,
            provider_override=provider_override,
            routing_request=routing_request,
        )
        task_budget: float | None = effective_request.max_cost_usd
        lineage = self._lineage_payload(
            campaign_lineage,
            run_id=run_id,
            routing_request=routing_request,
            effect_action_key=effect_action_key,
        )
        decision = None
        if candidates:
            if provider_override and not self.provider_override_allows_fallback:
                candidates = tuple(
                    item for item in candidates
                    if item.provider == provider_override)
            decision = self.router.route(
                effective_request,
                candidates,
                role=role,
            )
            self._append("provider.route_decided", {
                "run_id": run_id,
                "mission_id": mission_id,
                "step_id": step_id,
                "role": role,
                "routing_request": (
                    routing_request.to_dict() if routing_request is not None else None
                ),
                "effective_routing_request": effective_request.to_dict(),
                **lineage,
                **decision.to_dict(),
            }, idempotency_key=(
                f"provider.route_decided:{run_id}:{attempt}:"
                f"{decision.status}:{decision.failure_class or 'selected'}"))
            chain_candidates = tuple(decision.attempted)
            route_failure = decision.failure_class
        else:
            # A campaign child requires observed provider/capability truth.
            # Legacy non-campaign callers retain the pre-V12 configured-chain
            # behavior so historical manifests and local fabric tests remain
            # replay compatible.
            chain_candidates = () if routing_request is not None else tuple(
                ProviderRouteCandidate(
                    provider=provider,
                    model=config.preferred_model,
                    readiness="READY",
                    health="READY",
                    auth_state="READY",
                    availability="AVAILABLE",
                    noninteractive_allowed=True,
                ) for provider in legacy_chain
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

        if not chain_candidates:
            rejected = decision.rejected if decision is not None else ()
            wait_providers = tuple(dict.fromkeys(
                str(item.get("provider") or "")
                for item in rejected
                if str(item.get("failure_class") or "")
                in _TRANSIENT_PROVIDER_FAILURES
                and str(item.get("provider") or "")
            ))
            failure_class = route_failure or "NO_CANDIDATES"
            error_class = self._failure_error_class(failure_class)
            detail = (
                f"no safe provider satisfied role {role}: {failure_class}")
            self._fail_run(run_id, error_class, detail)
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider="", model="",
                verdict="BLOCKED", error_class=error_class,
                error_detail=detail, wait_for_providers=wait_providers,
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
            routing_request=routing_request,
            campaign_lineage=campaign_lineage,
            campaign_isolation_key=campaign_isolation_key,
            effect_action_key=effect_action_key,
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
        estimated_spend = sum(
            max(0.0, float(row.get("estimated_cost_usd") or 0.0))
            for row in history
        )
        failure_classes: list[str] = []
        wait_eligible: list[str] = []
        try:
            queue = list(chain_candidates)
            while queue and total_attempts < self.max_total_attempts:
                candidate = queue.pop(0)
                provider = candidate.provider
                model = candidate.model
                count = provider_counts.get(provider, 0)
                if count >= 1 + self.max_retries_per_provider:
                    continue
                estimate = max(0.0, float(candidate.cost_usd or 0.0))
                if (
                    task_budget is not None
                    and estimated_spend + estimate > task_budget
                ):
                    failure_classes.append("COST_LIMIT")
                    last_error = ProviderFailure(
                        f"cumulative provider estimate would exceed budget: "
                        f"{estimated_spend + estimate:.6f} > {task_budget:.6f}",
                        failure_class="COST_LIMIT",
                    )
                    self._append("provider.dispatch_skipped", {
                        "run_id": run_id,
                        "mission_id": mission_id,
                        "step_id": step_id,
                        "role": role,
                        "provider": provider,
                        "model": model,
                        "failure_class": "COST_LIMIT",
                        "estimated_spend_usd": estimated_spend,
                        "candidate_estimated_cost_usd": estimate,
                        "budget_usd": task_budget,
                        **lineage,
                    }, idempotency_key=(
                        f"provider.dispatch_skipped:{run_id}:{provider}:"
                        f"cost:{estimated_spend}:{task_budget}"))
                    continue
                total_attempts += 1
                provider_counts[provider] = count + 1
                estimated_spend += estimate
                manifest = self._manifest(
                    run_id, role, mission_id, step_id, resource_id, capability,
                    instructions, duration_seconds, budget_usd,
                    provider, config, model=model,
                    expected_artifacts=expected_artifacts,
                    verification_criteria=verification_criteria,
                    forbidden_paths=forbidden_paths,
                    attempt=attempt,
                    routing_request=routing_request,
                    campaign_lineage=campaign_lineage,
                    campaign_isolation_key=campaign_isolation_key,
                    effect_action_key=effect_action_key,
                )
                self._record_provider_attempt(
                    run_id=run_id,
                    mission_id=mission_id,
                    step_id=step_id,
                    role=role,
                    candidate=candidate,
                    ordinal=total_attempts,
                    lineage=lineage,
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
                    if failure_class in _TRANSIENT_PROVIDER_FAILURES:
                        wait_eligible.append(provider)
                    error_class = self._failure_error_class(failure_class)
                    reported_action_key = str(
                        error.effect_action_key or ""
                    ).strip()
                    resolved_action_key = (
                        reported_action_key or effect_action_key
                    )
                    effect_identity_mismatch = bool(
                        effect_action_key
                        and reported_action_key
                        and reported_action_key != effect_action_key
                    )
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
                        "attempt_ordinal": total_attempts,
                        **lineage,
                        "effect_action_key": resolved_action_key,
                    }, idempotency_key=(
                        f"provider.dispatch_failed:{run_id}:{total_attempts}:"
                        f"{provider}:{failure_class}"))
                    readiness = (
                        ProviderReadiness.RATE_LIMITED
                        if failure_class == "RATE_LIMITED" else
                        ProviderReadiness.INCOMPATIBLE
                        if failure_class in _INCOMPATIBLE_PROVIDER_FAILURES else
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
                        action_key = resolved_action_key
                        if not action_key:
                            action_key = f"provider-effect:{mission_id}:{step_id or 'start'}:{run_id}"
                        if effect_identity_mismatch:
                            self._append("provider.effect_identity_mismatch", {
                                "run_id": run_id,
                                "mission_id": mission_id,
                                "step_id": step_id,
                                "provider": provider,
                                "expected_effect_action_key": effect_action_key,
                                "observed_effect_action_key": reported_action_key,
                                **lineage,
                            }, idempotency_key=(
                                f"provider.effect_identity_mismatch:{run_id}:"
                                f"{reported_action_key}"
                            ))
                        self._append("provider.effect_reconciliation_required", {
                            "run_id": run_id,
                            "mission_id": mission_id,
                            "step_id": step_id,
                            "provider": provider,
                            "model": model,
                            "failure_class": failure_class,
                            "reason": str(error)[:500],
                            **lineage,
                            "effect_action_key": action_key,
                        }, idempotency_key=(
                            f"provider.effect_reconciliation_required:"
                            f"{run_id}:{action_key}"))
                        return RoleResult(
                            run_id=run_id, role=role, status=BLOCKED,
                            provider=provider, model=model, verdict="BLOCKED",
                            error_class=(
                                "provider_effect_identity_mismatch"
                                if effect_identity_mismatch
                                else "provider_effect_reconciliation_required"
                            ),
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
                if (
                    effect_action_key
                    and result.effect_action_key
                    and result.effect_action_key != effect_action_key
                ):
                    observed_action_key = str(result.effect_action_key)
                    detail = (
                        "provider returned a different effect identity: "
                        f"expected={effect_action_key} observed={observed_action_key}"
                    )
                    self._fail_run(
                        run_id,
                        "provider_incompatible",
                        detail,
                        suffix=f"effect-identity:{provider}:{total_attempts}",
                    )
                    self._append("provider.effect_identity_mismatch", {
                        "run_id": run_id,
                        "mission_id": mission_id,
                        "step_id": step_id,
                        "provider": provider,
                        "expected_effect_action_key": effect_action_key,
                        "observed_effect_action_key": observed_action_key,
                        **lineage,
                    }, idempotency_key=(
                        f"provider.effect_identity_mismatch:{run_id}:"
                        f"{observed_action_key}"
                    ))
                    self._append("provider.effect_reconciliation_required", {
                        "run_id": run_id,
                        "mission_id": mission_id,
                        "step_id": step_id,
                        "provider": provider,
                        "model": model,
                        "failure_class": "INCOMPATIBLE",
                        "reason": detail,
                        **lineage,
                        "effect_action_key": observed_action_key,
                    }, idempotency_key=(
                        f"provider.effect_reconciliation_required:"
                        f"{run_id}:{observed_action_key}"
                    ))
                    return RoleResult(
                        run_id=run_id,
                        role=role,
                        status=BLOCKED,
                        provider=provider,
                        model=model,
                        verdict="BLOCKED",
                        error_class="provider_effect_identity_mismatch",
                        error_detail=detail,
                        effect_action_key=observed_action_key,
                        started_at=result.started_at,
                        finished_at=result.finished_at,
                    )
                if effect_action_key and not result.effect_action_key:
                    result = replace(result, effect_action_key=effect_action_key)
                if result.status == COMPLETED:
                    identity_changed = (
                        result.provider != provider
                        or result.model != model
                    )
                    authorized_local_fabric = (
                        result.identity_transition_authorized
                        and result.provider == "fabric"
                        and result.model == "local"
                        and any(str(item).startswith("fabric:")
                                for item in result.evidence)
                    )
                    if identity_changed and not authorized_local_fabric:
                        detail = (
                            "provider/model identity changed during dispatch: "
                            f"requested={provider}/{model} "
                            f"observed={result.provider}/{result.model}"
                        )
                        self._fail_run(
                            run_id, "provider_incompatible", detail,
                            suffix=f"identity:{provider}:{total_attempts}")
                        self._append("provider.identity_mismatch", {
                            "run_id": run_id,
                            "mission_id": mission_id,
                            "step_id": step_id,
                            "role": role,
                            "requested_provider": provider,
                            "requested_model": model,
                            "observed_provider": result.provider,
                            "observed_model": result.model,
                            "reason": detail,
                            **lineage,
                        }, idempotency_key=(
                            f"provider.identity_mismatch:{run_id}:"
                            f"{provider}:{model}"))
                        return RoleResult(
                            run_id=run_id,
                            role=role,
                            status=BLOCKED,
                            provider=result.provider,
                            model=result.model,
                            verdict="BLOCKED",
                            error_class="provider_incompatible",
                            error_detail=detail,
                            started_at=result.started_at,
                            finished_at=result.finished_at,
                        )
                    if identity_changed:
                        self._append("provider.identity_transition_authorized", {
                            "run_id": run_id,
                            "mission_id": mission_id,
                            "step_id": step_id,
                            "role": role,
                            "requested_provider": provider,
                            "requested_model": model,
                            "observed_provider": result.provider,
                            "observed_model": result.model,
                            "authority": "canonical_capability_fabric",
                            "evidence": list(result.evidence),
                            **lineage,
                        }, idempotency_key=(
                            f"provider.identity_transition_authorized:{run_id}:"
                            f"{provider}:{model}:{result.provider}:{result.model}"))
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
                        "fallback_used": (
                            len(tried) > 1
                            or bool(decision and decision.fallback_used)
                        ),
                        "cost_usd": result.cost_usd,
                        **lineage,
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
                    if result_failure in _TRANSIENT_PROVIDER_FAILURES:
                        wait_eligible.append(provider)
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
                        **lineage,
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
            rejected_waits = tuple(
                str(item.get("provider") or "")
                for item in (decision.rejected if decision is not None else ())
                if str(item.get("failure_class") or "")
                in _TRANSIENT_PROVIDER_FAILURES
                and str(item.get("provider") or "")
            )
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider=tried[-1] if tried else "",
                model=(
                    next((item.model for item in reversed(chain_candidates)
                          if item.provider in tried), config.preferred_model)
                ), verdict="BLOCKED",
                error_class=error_class, error_detail=detail,
                wait_for_providers=tuple(dict.fromkeys(
                    (*rejected_waits, *wait_eligible)
                )),
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )
        finally:
            self.leases.release(lease_key, run_id)
