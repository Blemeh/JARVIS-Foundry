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

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

from ..clock import isoformat, parse, utc_now
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

if TYPE_CHECKING:
    from ..workforce.contracts import WorkPackage
    from ..workforce.worktrees import ExecutionWorktreePhase

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
_COST_QUANTUM = Decimal("0.000001")
_COST_MICROS_PER_USD = 1_000_000
_ATTEMPT_SETTLEMENT_SCHEMA = "provider.dispatch-attempt-settlement.v1"


@dataclass(frozen=True)
class _CostTotals:
    run_micros: int = 0
    package_micros: int = 0
    attempt_role_micros: int = 0

    def add(self, cost_micros: int) -> "_CostTotals":
        return _CostTotals(
            run_micros=self.run_micros + cost_micros,
            package_micros=self.package_micros + cost_micros,
            attempt_role_micros=self.attempt_role_micros + cost_micros,
        )

    def budget_spend(self, role: str) -> int:
        return (
            self.attempt_role_micros
            if role == "verifier"
            else self.package_micros
        )

    def reported(self, role: str, *, package_budget: bool) -> int:
        return self.budget_spend(role) if package_budget else self.run_micros

    def settlement_fields(
        self,
        *,
        attempt_micros: int | None = None,
        budget_micros: int | None = None,
    ) -> dict[str, int]:
        fields = {
            "run_cost": self.run_micros,
            "package_cost": self.package_micros,
            "attempt_role_cost": self.attempt_role_micros,
        }
        if attempt_micros is not None:
            fields["attempt_cost"] = attempt_micros
        if budget_micros is not None:
            fields["budget"] = budget_micros
        return fields


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

    def _event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> Event:
        return Event.create(
            event_type,
            payload,
            source_type=self.source_type,
            source_id=self.source_id,
            idempotency_key=idempotency_key,
        )

    def _append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[int, bool]:
        return self.database.append(self._event(
            event_type,
            payload,
            idempotency_key=idempotency_key,
        ))

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
        if manifest.package_id:
            payload.update({
                "package_id": manifest.package_id,
                "package_hash": manifest.package_hash,
                "attempt_id": manifest.attempt_id,
                "worktree_phase": manifest.worktree_phase,
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

    @staticmethod
    def _default_worktree_phase(role: str) -> "ExecutionWorktreePhase":
        return (
            "job_delta"
            if role in {"repair", "verifier"}
            else "builder_admission"
        )

    @staticmethod
    def _workforce_attempt_ordinal(
        package_id: str,
        attempt_id: str,
    ) -> int:
        attempt_prefix = f"workforce:{package_id}:a"
        attempt_suffix = (
            attempt_id[len(attempt_prefix):]
            if attempt_id.startswith(attempt_prefix)
            else ""
        )
        if not attempt_suffix.isdigit() or int(attempt_suffix) < 1:
            raise ValueError(
                "workforce dispatch attempt_id is not bound to the immutable "
                "WorkPackage"
            )
        return int(attempt_suffix)

    def workforce_session_attempt(
        self,
        *,
        package_id: str,
        package_hash: str,
        attempt_id: str,
    ) -> int | None:
        """Return the exact canonical gateway attempt, when one is bound."""

        ordinal = self._workforce_attempt_ordinal(package_id, attempt_id)
        bound_events = [
            event
            for event in self.database.events(
                event_types=("workforce.session_bound",),
                readonly=True,
            )
            if str(event.payload.get("session_id") or "") == attempt_id
        ]
        if not bound_events:
            if ordinal > 1:
                raise EventConflict(
                    "replacement workforce attempt has no canonical "
                    f"session binding: {attempt_id}"
                )
            return None
        if len(bound_events) != 1:
            raise EventConflict(
                f"workforce attempt has conflicting session bindings: {attempt_id}"
            )
        event = bound_events[0]
        payload = event.payload
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise EventConflict(
                f"workforce session binding has invalid result lineage: {attempt_id}"
            )
        if not (
            event.source_type == "supervisor"
            and bool(event.source_id)
            and event.correlation_id == package_id
            and event.idempotency_key == f"workforce.session_bound:{attempt_id}"
            and str(payload.get("session_id") or "") == attempt_id
            and str(payload.get("run_id") or "") == attempt_id
            and str(payload.get("package_id") or "") == package_id
            and payload.get("attempt") == ordinal
            and payload.get("expected_session_version") == 0
            and payload.get("session_version") == 1
            and str(result.get("package_hash") or "") == package_hash
            and bool(str(result.get("claim_id") or ""))
            and bool(str(result.get("resource_id") or ""))
        ):
            raise EventConflict(
                f"workforce session binding is not canonical: {attempt_id}"
            )

        from ..workforce.contracts import TERMINAL_SESSION_STATUSES
        from ..workforce.state import rebuild_workforce_state

        state = rebuild_workforce_state(self.database)
        session = state.sessions.get(attempt_id)
        if not (
            session is not None
            and session.package_id == package_id
            and session.run_id == attempt_id
            and session.attempt == ordinal
            and str(session.result.get("package_hash") or "") == package_hash
        ):
            raise EventConflict(
                f"workforce session replay changed immutable lineage: {attempt_id}"
            )
        sessions = state.sessions_for(package_id)
        latest = max(sessions, key=lambda item: item.attempt)
        if latest.session_id != attempt_id:
            raise EventConflict(
                f"workforce session attempt is superseded: {attempt_id}"
            )
        by_attempt = {item.attempt: item for item in sessions}
        for prior_ordinal in range(1, ordinal):
            prior = by_attempt.get(prior_ordinal)
            if (
                prior is None
                or prior.status not in TERMINAL_SESSION_STATUSES
            ):
                raise EventConflict(
                    "replacement workforce attempt lacks a terminal canonical "
                    f"predecessor: {attempt_id}"
                )
        return ordinal

    def _recorded_worktree_phase(
        self,
        run_id: str,
    ) -> "ExecutionWorktreePhase | None":
        for event in reversed(self.database.events(
            event_types=("agent_run.started",),
            readonly=True,
        )):
            if (
                str(event.payload.get("run_id") or "") != run_id
                or event.source_type != self.source_type
                or event.source_id != self.source_id
            ):
                continue
            phase = str(event.payload.get("worktree_phase") or "")
            if phase in {"builder_admission", "job_delta"}:
                return phase
            return None
        return None

    @staticmethod
    def _validate_existing_workforce_run(
        row: Mapping[str, Any],
        *,
        run_id: str,
        mission_id: str,
        step_id: str | None,
        role: str,
        terminal_replay_available: bool = False,
        provider_resume_available: bool = False,
    ) -> None:
        if not (
            str(row.get("mission_id") or "") == mission_id
            and str(row.get("step_id") or "") == str(step_id or "")
            and str(row.get("role") or "") == role
        ):
            raise EventConflict(
                f"workforce run {run_id} belongs to different execution lineage"
            )
        status = str(row.get("status") or "")
        if (
            status not in {"RUNNING", "COMPLETED"}
            and not (
                status == "FAILED"
                and (
                    terminal_replay_available
                    or provider_resume_available
                )
            )
        ):
            raise EventConflict(
                f"terminal workforce run {run_id} requires a fresh run_id"
            )

    def _has_exact_workforce_run_lineage(
        self,
        *,
        run_id: str,
        package_id: str,
        package_hash: str,
        attempt_id: str,
    ) -> bool:
        for event in reversed(self.database.events(
            event_types=("agent_run.started",),
            readonly=True,
        )):
            if str(event.payload.get("run_id") or "") != run_id:
                continue
            return bool(
                event.source_type == self.source_type
                and event.source_id == self.source_id
                and str(event.payload.get("package_id") or "") == package_id
                and str(event.payload.get("package_hash") or "") == package_hash
                and str(event.payload.get("attempt_id") or "") == attempt_id
            )
        return False

    def _can_resume_settled_workforce_fallback(
        self,
        *,
        lease_key: str,
        run_id: str,
        attempt_generation: int,
        package_id: str,
        package_hash: str,
        attempt_id: str,
    ) -> bool:
        with self.database.connect() as connection:
            lease = connection.execute(
                "SELECT expires_at FROM leases WHERE lease_key = ?",
                (lease_key,),
            ).fetchone()
        if lease is not None:
            try:
                if parse(str(lease["expires_at"])) > utc_now():
                    return False
            except (TypeError, ValueError):
                return False

        settlements = self._validated_attempt_settlements(
            run_id=run_id,
            package_id=package_id,
            package_hash=package_hash,
            attempt_generation=attempt_generation,
        )
        if (
            any(bool(item["terminal_run"]) for item in settlements)
            or any(not bool(item["attempt_started"]) for item in settlements)
        ):
            return False
        settled_identities = {
            (
                int(item["attempt_ordinal"]),
                str(item["provider"]),
                str(item["model"]),
            )
            for item in settlements
        }
        if sorted(identity[0] for identity in settled_identities) != list(
            range(1, len(settled_identities) + 1)
        ):
            return False

        attempted_identities: list[tuple[int, str, str]] = []
        for event in self.database.events(
            readonly=True,
            event_types=("provider.dispatch_attempted",),
        ):
            payload = event.payload
            if (
                str(payload.get("run_id") or "") != run_id
                or payload.get("attempt_generation") != attempt_generation
            ):
                continue
            if (
                event.source_type != self.source_type
                or event.source_id != self.source_id
            ):
                return False
            identity = (
                int(payload.get("attempt_ordinal") or 0),
                str(payload.get("provider") or payload.get("provider_id") or ""),
                str(payload.get("model") or payload.get("model_id") or ""),
            )
            if (
                not identity[1]
                or not identity[2]
                or str(payload.get("package_id") or "") != package_id
                or str(payload.get("package_hash") or "") != package_hash
                or str(payload.get("attempt_id") or "") != attempt_id
                or event.idempotency_key != (
                    f"provider.dispatch_attempted:{run_id}:"
                    f"{attempt_generation}:{identity[0]}:"
                    f"{identity[1]}:{identity[2]}"
                )
            ):
                return False
            attempted_identities.append(identity)
        attempted_set = set(attempted_identities)
        if (
            len(attempted_identities) != len(attempted_set)
            or not settled_identities.issubset(attempted_set)
        ):
            return False
        dangling = attempted_set - settled_identities
        return (
            bool(settled_identities)
            and not dangling
        ) or (
            len(dangling) == 1
            and next(iter(dangling))[0] == len(settled_identities) + 1
        )

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
        allowed_paths: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        required_tests: tuple[str, ...] = (),
        attempt: int = 1,
        routing_request: RoutingRequest | None = None,
        campaign_lineage: Mapping[str, str] | None = None,
        campaign_isolation_key: str | None = None,
        effect_action_key: str | None = None,
        package_id: str | None = None,
        package_hash: str | None = None,
        attempt_id: str | None = None,
        worktree_phase: "ExecutionWorktreePhase | None" = None,
        worker_preference: str | None = None,
        worker_preference_evidence: tuple[str, ...] = (),
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
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            required_tests=required_tests,
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
            package_id=package_id,
            package_hash=package_hash,
            attempt_id=attempt_id,
            worktree_phase=worktree_phase,
            worker_preference=worker_preference,
            worker_preference_evidence=worker_preference_evidence,
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
        allowed_paths: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        required_tests: tuple[str, ...] = (),
        attempt: int = 1,
        routing_request: RoutingRequest | None = None,
        campaign_lineage: Mapping[str, str] | None = None,
        campaign_isolation_key: str | None = None,
        effect_action_key: str | None = None,
        package_id: str | None = None,
        package_hash: str | None = None,
        attempt_id: str | None = None,
        worktree_phase: "ExecutionWorktreePhase | None" = None,
        worker_preference: str | None = None,
        worker_preference_evidence: tuple[str, ...] = (),
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
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            required_tests=required_tests,
            attempt=attempt,
            routing_request=routing_request,
            campaign_lineage=campaign_lineage,
            campaign_isolation_key=campaign_isolation_key,
            effect_action_key=effect_action_key,
            package_id=package_id,
            package_hash=package_hash,
            attempt_id=attempt_id,
            worktree_phase=(
                worktree_phase
                if worktree_phase is not None
                else (
                    self._default_worktree_phase(role)
                    if package_id
                    else None
                )
            ),
            worker_preference=worker_preference,
            worker_preference_evidence=worker_preference_evidence,
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

    def _legacy_attempt_marker_exists(
        self,
        *,
        run_id: str,
        candidate: ProviderRouteCandidate,
        ordinal: int,
    ) -> bool:
        """Detect a pre-settlement dangling attempt in the current wait epoch."""

        events = self.database.events(readonly=True)
        from ..executive.continuous import canonical_provider_resume_events

        resume_ids = {
            event.event_id
            for event in canonical_provider_resume_events(
                self.database,
                run_id=run_id,
            )
        }
        last_resume = max(
            (
                index
                for index, event in enumerate(events)
                if event.event_id in resume_ids
            ),
            default=-1,
        )
        for index, event in enumerate(events):
            payload = event.payload
            if (
                index <= last_resume
                or event.event_type != "provider.dispatch_attempted"
                or str(payload.get("run_id") or "") != run_id
                or "attempt_generation" in payload
            ):
                continue
            if (
                int(payload.get("attempt_ordinal") or 0) == ordinal
                and str(payload.get("provider")
                        or payload.get("provider_id") or "")
                == candidate.provider
                and str(payload.get("model")
                        or payload.get("model_id") or "")
                == candidate.model
            ):
                return True
        return False

    def _record_provider_attempt(
        self,
        *,
        run_id: str,
        mission_id: str,
        step_id: str | None,
        role: str,
        candidate: ProviderRouteCandidate,
        attempt_generation: int,
        ordinal: int,
        estimated_cost_micros: int | None,
        lineage: Mapping[str, Any] | None = None,
    ) -> bool:
        if self._legacy_attempt_marker_exists(
            run_id=run_id,
            candidate=candidate,
            ordinal=ordinal,
        ):
            return False
        _, inserted = self._append("provider.dispatch_attempted", {
            "run_id": run_id,
            "mission_id": mission_id,
            "step_id": step_id,
            "role": role,
            "provider_id": candidate.provider,
            "provider": candidate.provider,
            "model_id": candidate.model,
            "model": candidate.model,
            "attempt_generation": attempt_generation,
            "attempt_ordinal": ordinal,
            "estimate_state": (
                "AVAILABLE"
                if estimated_cost_micros is not None
                else "UNAVAILABLE"
            ),
            "estimated_cost_micros": estimated_cost_micros,
            "estimated_cost_usd": (
                self._cost_usd(estimated_cost_micros)
                if estimated_cost_micros is not None
                else None
            ),
            **dict(lineage or {}),
        }, idempotency_key=(
            f"provider.dispatch_attempted:{run_id}:{attempt_generation}:{ordinal}:"
            f"{candidate.provider}:{candidate.model}"))
        return inserted

    def _attempt_history(
        self,
        run_id: str,
        attempt_generation: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        events = self.database.events(readonly=True)
        identities: set[tuple[int, str, str]] = set()
        for payload in self._validated_attempt_settlements(
            run_id=run_id,
            attempt_generation=attempt_generation,
        ):
            if not bool(payload["attempt_started"]):
                continue
            identity = (
                int(payload["attempt_ordinal"]),
                str(payload["provider"]),
                str(payload["model"]),
            )
            identities.add(identity)
            rows.append(dict(payload))

        # Compatibility for histories written before attempt settlements.
        from ..executive.continuous import canonical_provider_resume_events

        resume_ids = {
            event.event_id
            for event in canonical_provider_resume_events(
                self.database,
                run_id=run_id,
            )
        }
        last_resume = max(
            (
                index
                for index, event in enumerate(events)
                if event.event_id in resume_ids
            ),
            default=-1,
        )
        terminal_attempts = {
            (
                int(event.payload.get("attempt_ordinal") or 0),
                str(event.payload.get("provider")
                    or event.payload.get("provider_id") or ""),
                str(event.payload.get("model")
                    or event.payload.get("model_id") or ""),
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
                str(event.payload.get("model")
                    or event.payload.get("model_id") or ""),
            )
            if (
                str(event.payload.get("run_id") or "") == run_id
                and attempt_identity in terminal_attempts
                and attempt_identity not in identities
            ):
                identities.add(attempt_identity)
                rows.append(dict(event.payload))
        rows.sort(key=lambda item: int(item.get("attempt_ordinal") or 0))
        return rows

    @staticmethod
    def _cost_micros(value: Any) -> int | None:
        try:
            observed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not observed.is_finite() or observed < 0:
            return None
        try:
            quantized = observed.quantize(
                _COST_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
        except InvalidOperation:
            return None
        return int(quantized * _COST_MICROS_PER_USD)

    @staticmethod
    def _cost_usd(cost_micros: int) -> float:
        return float(
            Decimal(int(cost_micros)) / Decimal(_COST_MICROS_PER_USD)
        )

    @classmethod
    def _payload_cost_micros(cls, payload: Mapping[str, Any]) -> int | None:
        raw_micros = payload.get("cost_micros")
        if raw_micros is not None:
            if (
                not isinstance(raw_micros, int)
                or isinstance(raw_micros, bool)
                or raw_micros < 0
            ):
                return None
            return raw_micros
        return cls._cost_micros(payload.get("cost_usd"))

    def _attempt_generation(self, run_id: str) -> int:
        from ..executive.continuous import canonical_provider_resume_events

        return len(
            canonical_provider_resume_events(
                self.database,
                run_id=run_id,
            )
        )

    def _has_unconsumed_provider_resume(self, run_id: str) -> bool:
        from ..executive.continuous import canonical_provider_resume_events

        resume_ids = {
            event.event_id
            for event in canonical_provider_resume_events(
                self.database,
                run_id=run_id,
            )
        }
        if not resume_ids:
            return False
        latest_resume = -1
        latest_terminal = -1
        for index, event in enumerate(self.database.events(readonly=True)):
            if event.event_id in resume_ids:
                latest_resume = index
            if (
                event.event_type in {"agent_run.completed", "agent_run.failed"}
                and str(event.payload.get("run_id") or "") == run_id
            ):
                latest_terminal = index
        return latest_resume > latest_terminal >= 0

    def _validated_attempt_settlements(
        self,
        *,
        run_id: str | None = None,
        package_id: str | None = None,
        package_hash: str | None = None,
        attempt_generation: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return canonical settlements after validating their full identity."""

        settlements: list[dict[str, Any]] = []
        identities: set[str] = set()
        package_authorities: dict[str, WorkPackage] = {}
        for event in self.database.events(
            readonly=True,
            event_types=("provider.dispatch_attempt_settled",),
        ):
            payload = event.payload
            observed_run_id = str(payload.get("run_id") or "")
            observed_package_id = str(payload.get("package_id") or "")
            if run_id is not None and observed_run_id != run_id:
                continue
            if (
                run_id is None
                and package_id is not None
                and observed_package_id != package_id
            ):
                continue
            if (
                event.source_type != self.source_type
                or event.source_id != self.source_id
            ):
                raise EventConflict(
                    "attempt settlement is not owned by the configured "
                    f"dispatcher: {observed_run_id or 'UNKNOWN'}"
                )
            if payload.get("schema") != _ATTEMPT_SETTLEMENT_SCHEMA:
                raise EventConflict(
                    "attempt settlement schema is not canonical: "
                    f"{observed_run_id or 'UNKNOWN'}"
                )
            generation = payload.get("attempt_generation")
            ordinal = payload.get("attempt_ordinal")
            if (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or not isinstance(ordinal, int)
                or isinstance(ordinal, bool)
                or generation < 0
                or ordinal < 0
            ):
                raise EventConflict(
                    "attempt settlement generation or ordinal is invalid"
                )
            if (
                attempt_generation is not None
                and generation != attempt_generation
            ):
                continue
            role = str(payload.get("role") or "")
            provider = str(payload.get("provider") or "")
            model = str(payload.get("model") or "")
            if not observed_run_id or not role or not provider or not model:
                raise EventConflict(
                    "attempt settlement has incomplete run or provider identity"
                )
            identity = (
                f"{observed_run_id}:{generation}:{ordinal}:{provider}:{model}"
            )
            if (
                str(payload.get("settlement_id") or "") != identity
                or event.idempotency_key
                != f"provider.dispatch_attempt_settled:{identity}"
            ):
                raise EventConflict(
                    f"attempt settlement identity is not canonical: {identity}"
                )
            if identity in identities:
                raise EventConflict(
                    f"attempt settlement identity is duplicated: {identity}"
                )
            identities.add(identity)
            if not isinstance(payload.get("attempt_started"), bool):
                raise EventConflict(
                    f"attempt settlement has invalid started state: {identity}"
                )
            if not isinstance(payload.get("terminal_run"), bool):
                raise EventConflict(
                    f"attempt settlement has invalid terminal state: {identity}"
                )
            context_hash = str(payload.get("context_manifest_hash") or "")
            if (
                len(context_hash) != 64
                or any(character not in "0123456789abcdef"
                       for character in context_hash.lower())
            ):
                raise EventConflict(
                    f"attempt settlement has invalid context hash: {identity}"
                )
            lineage = (
                observed_package_id,
                str(payload.get("package_hash") or ""),
                str(payload.get("attempt_id") or ""),
            )
            if not all(lineage):
                raise EventConflict(
                    f"attempt settlement lacks complete package lineage: {identity}"
                )
            observed_package_hash = lineage[1]
            observed_attempt_id = lineage[2]
            package = package_authorities.get(observed_package_id)
            if package is None:
                package = self._durable_work_package(observed_package_id)
                if package is None:
                    raise EventConflict(
                        "attempt settlement references no durable WorkPackage: "
                        f"{identity}"
                    )
                package_authorities[observed_package_id] = package
            if package.content_hash() != observed_package_hash:
                raise EventConflict(
                    f"attempt settlement changed durable package hash: {identity}"
                )
            attempt_prefix = f"workforce:{observed_package_id}:a"
            attempt_suffix = (
                observed_attempt_id[len(attempt_prefix):]
                if observed_attempt_id.startswith(attempt_prefix)
                else ""
            )
            if not attempt_suffix.isdigit() or int(attempt_suffix) < 1:
                raise EventConflict(
                    f"attempt settlement has invalid package attempt: {identity}"
                )
            if role not in {package.role, "repair", "verifier"}:
                raise EventConflict(
                    f"attempt settlement has unauthorized package role: {identity}"
                )
            if (
                package.mission_id is not None
                and str(payload.get("mission_id") or "") != package.mission_id
            ):
                raise EventConflict(
                    f"attempt settlement changed package mission: {identity}"
                )
            if (
                package.step_id is not None
                and str(payload.get("step_id") or "") != package.step_id
            ):
                raise EventConflict(
                    f"attempt settlement changed package step: {identity}"
                )
            if package_id is not None:
                if observed_package_id != package_id:
                    raise EventConflict(
                        f"attempt settlement changed package identity: {identity}"
                    )
                if str(payload.get("package_hash") or "") != str(
                    package_hash or ""
                ):
                    raise EventConflict(
                        f"attempt settlement changed package hash: {identity}"
                    )
            estimate_state = str(payload.get("estimate_state") or "")
            estimated_cost = self._payload_cost_micros({
                "cost_micros": payload.get("estimated_cost_micros"),
                "cost_usd": payload.get("estimated_cost_usd"),
            })
            if estimate_state == "AVAILABLE":
                if estimated_cost is None:
                    raise EventConflict(
                        f"attempt settlement lacks provider estimate: {identity}"
                    )
                if self._cost_micros(payload.get("estimated_cost_usd")) != (
                    estimated_cost
                ):
                    raise EventConflict(
                        f"attempt settlement estimate units disagree: {identity}"
                    )
            elif estimate_state == "UNAVAILABLE":
                if (
                    payload.get("estimated_cost_micros") is not None
                    or payload.get("estimated_cost_usd") is not None
                ):
                    raise EventConflict(
                        f"attempt settlement reports unavailable estimate: {identity}"
                    )
            else:
                raise EventConflict(
                    f"attempt settlement has invalid estimate state: {identity}"
                )
            cost_state = str(payload.get("cost_state") or "")
            if cost_state not in {"OBSERVED", "UNAVAILABLE", "NOT_INCURRED"}:
                raise EventConflict(
                    f"attempt settlement has invalid cost state: {identity}"
                )
            observed_cost = self._payload_cost_micros({
                "cost_micros": payload.get("observed_cost_micros"),
                "cost_usd": payload.get("observed_cost_usd"),
            })
            if cost_state == "OBSERVED":
                if observed_cost is None:
                    raise EventConflict(
                        f"attempt settlement lacks observed cost: {identity}"
                    )
                if self._cost_micros(payload.get("observed_cost_usd")) != (
                    observed_cost
                ):
                    raise EventConflict(
                        f"attempt settlement cost units disagree: {identity}"
                    )
            elif (
                payload.get("observed_cost_micros") is not None
                or payload.get("observed_cost_usd") is not None
            ):
                raise EventConflict(
                    f"attempt settlement reports unincurred cost: {identity}"
                )
            if cost_state == "OBSERVED" and not payload["attempt_started"]:
                raise EventConflict(
                    f"attempt settlement observes cost before dispatch: {identity}"
                )
            if cost_state == "NOT_INCURRED" and payload["attempt_started"]:
                raise EventConflict(
                    f"attempt settlement hides a started attempt cost: {identity}"
                )
            if (
                cost_state == "UNAVAILABLE"
                and not str(payload.get("cost_reason") or "").strip()
            ):
                raise EventConflict(
                    f"attempt settlement lacks cost-unavailable reason: {identity}"
                )
            for prefix in (
                "run_cost",
                "package_cost",
                "attempt_role_cost",
                "attempt_cost",
                "budget",
            ):
                micros_name = f"{prefix}_micros"
                usd_name = f"{prefix}_usd"
                if micros_name not in payload and usd_name not in payload:
                    continue
                micros = self._payload_cost_micros({
                    "cost_micros": payload.get(micros_name),
                    "cost_usd": payload.get(usd_name),
                })
                if (
                    micros is None
                    or self._cost_micros(payload.get(usd_name)) != micros
                ):
                    raise EventConflict(
                        f"attempt settlement {prefix} units disagree: {identity}"
                    )
            if not str(payload.get("outcome") or "").strip():
                raise EventConflict(
                    f"attempt settlement lacks outcome: {identity}"
                )
            result_payload = payload.get("result")
            if not isinstance(result_payload, Mapping):
                raise EventConflict(
                    f"attempt settlement result is malformed: {identity}"
                )
            raw_identity_transition = result_payload.get(
                "identity_transition_authorized",
                False,
            )
            if not isinstance(raw_identity_transition, bool):
                raise EventConflict(
                    "attempt settlement result has invalid identity "
                    f"transition authority: {identity}"
                )
            if self._cost_micros(result_payload.get("cost_usd", 0.0)) is None:
                raise EventConflict(
                    f"attempt settlement result has invalid cost: {identity}"
                )
            try:
                result = RoleResult.from_payload(dict(result_payload))
            except (KeyError, TypeError, ValueError) as error:
                raise EventConflict(
                    f"attempt settlement result is malformed: {identity}"
                ) from error
            if result.run_id != observed_run_id or result.role != role:
                raise EventConflict(
                    f"attempt settlement result identity disagrees: {identity}"
                )
            if (
                (result.provider != provider or result.model != model)
                and not (
                    result.status == COMPLETED
                    and result.identity_transition_authorized
                    and result.provider == "fabric"
                    and result.model == "local"
                    and any(
                        str(item).startswith("fabric:")
                        for item in result.evidence
                    )
                )
            ):
                raise EventConflict(
                    f"attempt settlement result provider disagrees: {identity}"
                )
            settlements.append(dict(payload))
        return settlements

    def _observed_cost_totals(
        self,
        run_id: str,
        package_id: str | None = None,
        package_hash: str | None = None,
        attempt_id: str | None = None,
        role: str | None = None,
        *,
        settlement_authority: bool = False,
    ) -> _CostTotals:
        """Return run, package, and attempt-role spend in integer micro-USD."""
        run_total = 0
        package_total = 0
        attempt_role_total = 0
        if settlement_authority:
            if not package_id or not package_hash:
                raise DispatchError(
                    "settlement cost authority requires package identity"
                )
            for payload in self._validated_attempt_settlements(
                package_id=package_id,
                package_hash=package_hash,
            ):
                if payload["cost_state"] != "OBSERVED":
                    continue
                observed = self._payload_cost_micros({
                    "cost_micros": payload.get("observed_cost_micros"),
                    "cost_usd": payload.get("observed_cost_usd"),
                })
                if observed is None:  # validated above
                    raise EventConflict(
                        "canonical attempt settlement lost observed cost"
                    )
                if str(payload.get("run_id") or "") == run_id:
                    run_total += observed
                package_total += observed
                if (
                    attempt_id
                    and role
                    and str(payload.get("attempt_id") or "") == attempt_id
                    and str(payload.get("role") or "") == role
                ):
                    attempt_role_total += observed
            return _CostTotals(run_total, package_total, attempt_role_total)
        for event in self.database.events(
            readonly=True,
            event_type_prefixes=("provider.dispatch_cost_recorded",),
        ):
            payload = event.payload
            observed = self._payload_cost_micros(payload)
            if observed is None:
                continue
            if str(payload.get("run_id") or "") == run_id:
                run_total += observed
            package_match = bool(
                package_id
                and package_hash
                and str(payload.get("package_id") or "") == package_id
                and str(payload.get("package_hash") or "") == package_hash
            )
            if package_match:
                package_total += observed
                if (
                    attempt_id
                    and role
                    and str(payload.get("attempt_id") or "") == attempt_id
                    and str(payload.get("role") or "") == role
                ):
                    attempt_role_total += observed
        return _CostTotals(run_total, package_total, attempt_role_total)

    def _durable_work_package(
        self,
        package_id: str | None,
    ) -> WorkPackage | None:
        from ..workforce.contracts import WorkPackage

        if not package_id:
            return None
        found: WorkPackage | None = None
        for event in self.database.events(
            readonly=True,
            event_types=("workforce.package_created",),
        ):
            if str(event.payload.get("package_id") or "") != package_id:
                continue
            if (
                event.source_type != "supervisor"
                or event.idempotency_key
                != f"workforce.package_created:{package_id}"
                or event.correlation_id != package_id
            ):
                raise EventConflict(
                    f"durable WorkPackage authority is invalid: {package_id}"
                )
            candidate = WorkPackage.from_payload(event.payload)
            if (
                found is not None
                and found.content_hash() != candidate.content_hash()
            ):
                raise EventConflict(
                    "conflicting durable WorkPackage definitions for "
                    f"{package_id}"
                )
            found = candidate
        return found

    def durable_workforce_package(
        self,
        package_id: str,
        package_hash: str,
    ) -> WorkPackage:
        """Resolve one immutable WorkPackage by its exact content hash."""

        package = self._durable_work_package(package_id)
        if package is None:
            raise ValueError(
                "workforce dispatch requires an immutable durable WorkPackage"
            )
        durable_hash = package.content_hash()
        if package_hash != durable_hash:
            raise ValueError(
                "workforce package hash does not match the immutable "
                f"WorkPackage: expected={durable_hash} observed={package_hash}"
            )
        return package

    @staticmethod
    def _validate_durable_workforce_dispatch(
        package: WorkPackage,
        *,
        mission_id: str,
        step_id: str | None,
        role: str,
        capability: str,
        duration_seconds: int,
        allowed_paths: tuple[str, ...],
        forbidden_paths: tuple[str, ...],
        expected_artifacts: tuple[str, ...],
        required_tests: tuple[str, ...],
        effect_action_key: str | None,
        attempt_id: str,
    ) -> None:
        if package.mission_id is not None and package.mission_id != mission_id:
            raise ValueError(
                "workforce dispatch mission does not match the immutable "
                "WorkPackage"
            )
        if package.step_id is not None and package.step_id != step_id:
            raise ValueError(
                "workforce dispatch step does not match the immutable "
                "WorkPackage"
            )
        if role not in {package.role, "repair", "verifier"}:
            raise ValueError(
                "workforce dispatch role is not authorized by the immutable "
                "WorkPackage"
            )
        expected_capability = (
            "verification" if role == "verifier" else package.capability
        )
        if capability != expected_capability:
            raise ValueError(
                "workforce dispatch capability does not match the immutable "
                "WorkPackage"
            )
        if int(duration_seconds) > package.duration_seconds:
            raise ValueError(
                "workforce dispatch duration exceeds the immutable WorkPackage"
            )
        requested_paths = set(allowed_paths)
        if not requested_paths or not requested_paths.issubset(
            set(package.allowed_paths)
        ):
            raise ValueError(
                "workforce dispatch allowed paths exceed the immutable "
                "WorkPackage"
            )
        if not set(package.forbidden_paths).issubset(set(forbidden_paths)):
            raise ValueError(
                "workforce dispatch omits immutable forbidden paths"
            )
        if not set(package.expected_artifacts).issubset(
            set(expected_artifacts)
        ):
            raise ValueError(
                "workforce dispatch omits immutable expected artifacts"
            )
        if not set(package.required_tests).issubset(set(required_tests)):
            raise ValueError(
                "workforce dispatch omits immutable required tests"
            )
        if (
            package.effect_action_key is not None
            and package.effect_action_key != effect_action_key
        ):
            raise ValueError(
                "workforce dispatch effect identity does not match the "
                "immutable WorkPackage"
            )
        RoleDispatcher._workforce_attempt_ordinal(
            package.package_id,
            attempt_id,
        )

    def validate_workforce_dispatch(
        self,
        *,
        package_id: str | None,
        package_hash: str | None,
        attempt_id: str | None,
        run_id: str | None = None,
        mission_id: str,
        step_id: str | None,
        role: str,
        resource_id: str,
        capability: str,
        duration_seconds: int,
        allowed_paths: tuple[str, ...],
        forbidden_paths: tuple[str, ...],
        expected_artifacts: tuple[str, ...],
        required_tests: tuple[str, ...],
        effect_action_key: str | None,
        worktree_phase: ExecutionWorktreePhase | None = None,
    ) -> WorkPackage | None:
        """Validate a workforce dispatch without creating durable run state."""

        package_lineage = (package_id, package_hash, attempt_id)
        if any(package_lineage) and not all(package_lineage):
            raise ValueError(
                "workforce dispatch requires package_id, package_hash, and "
                "attempt_id"
            )
        if not all(package_lineage):
            return None
        package = self.durable_workforce_package(
            str(package_id),
            str(package_hash),
        )
        durable_hash = package.content_hash()
        self._validate_durable_workforce_dispatch(
            package,
            mission_id=mission_id,
            step_id=step_id,
            role=role,
            capability=capability,
            duration_seconds=duration_seconds,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            expected_artifacts=expected_artifacts,
            required_tests=required_tests,
            effect_action_key=effect_action_key,
            attempt_id=str(attempt_id),
        )
        from ..workforce.worktrees import validate_execution_worktree

        if worktree_phase is None:
            worktree_phase = (
                "job_delta"
                if role in {"repair", "verifier"}
                else "builder_admission"
            )
        if (
            role == package.role == "builder"
            and worktree_phase == "job_delta"
            and not self._has_exact_workforce_run_lineage(
                run_id=str(run_id or attempt_id),
                package_id=package.package_id,
                package_hash=durable_hash,
                attempt_id=str(attempt_id),
            )
        ):
            bound_attempt = self.workforce_session_attempt(
                package_id=package.package_id,
                package_hash=durable_hash,
                attempt_id=str(attempt_id),
            )
            if bound_attempt is None:
                raise EventConflict(
                    "fresh builder job-delta execution requires a canonical "
                    f"workforce session binding: {attempt_id}"
                )
        validate_execution_worktree(
            self.database,
            package,
            resource_id=resource_id,
            phase=worktree_phase,
        )
        return package

    def _package_cost_unavailable(
        self,
        package_id: str | None,
        package_hash: str | None,
    ) -> dict[str, Any] | None:
        if not package_id or not package_hash:
            return None
        latest = None
        for payload in self._validated_attempt_settlements(
            package_id=package_id,
            package_hash=package_hash,
        ):
            if payload["cost_state"] == "UNAVAILABLE":
                latest = dict(payload)
        return latest

    def _budget_blocked_result(
        self,
        manifest: ContextManifest,
        *,
        error_class: str,
        detail: str,
        cost_micros: int,
        result: RoleResult | None = None,
        effect_action_key: str | None = None,
    ) -> RoleResult:
        changes: dict[str, Any] = {
            "status": BLOCKED,
            "verdict": "BLOCKED",
            "error_class": error_class,
            "error_detail": detail,
            "cost_usd": self._cost_usd(cost_micros),
        }
        if effect_action_key is not None:
            changes["effect_action_key"] = effect_action_key
        if result is not None:
            return replace(result, **changes)
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            provider=manifest.provider,
            model=manifest.model,
            started_at=isoformat(utc_now()),
            finished_at=isoformat(utc_now()),
            **changes,
        )

    @classmethod
    def _cost_budget_detail(
        cls,
        *,
        role: str,
        observed_cost_micros: int,
        budget_micros: int,
    ) -> str:
        scope = "package" if role != "verifier" else "verification"
        return (
            f"workforce {scope} observed cost exhausted the cost budget "
            f"({cls._cost_usd(observed_cost_micros):.6f} >= "
            f"{cls._cost_usd(budget_micros):.6f} USD)"
        )

    def _terminal_attempt_settlement(
        self,
        run_id: str,
        attempt_generation: int,
        package_id: str | None,
        package_hash: str | None,
    ) -> dict[str, Any] | None:
        terminal = None
        for payload in self._validated_attempt_settlements(
            run_id=run_id,
            package_id=package_id,
            package_hash=package_hash,
            attempt_generation=attempt_generation,
        ):
            if bool(payload["terminal_run"]):
                if (
                    terminal is not None
                    and terminal.get("settlement_id")
                    != payload.get("settlement_id")
                ):
                    raise EventConflict(
                        f"run {run_id} generation {attempt_generation} has "
                        "conflicting terminal settlements"
                    )
                terminal = dict(payload)
        return terminal

    def _replay_terminal_attempt(
        self,
        run_id: str,
        prospective: ContextManifest,
        *,
        attempt_generation: int,
        package_id: str | None,
        package_hash: str | None,
    ) -> RoleResult | None:
        settlement = self._terminal_attempt_settlement(
            run_id,
            attempt_generation,
            package_id,
            package_hash,
        )
        if settlement is None:
            return None
        exact = replace(
            prospective,
            provider=str(settlement.get("provider") or ""),
            model=str(settlement.get("model") or ""),
        )
        if settlement.get("context_manifest_hash") != exact.content_hash():
            raise EventConflict(
                f"terminal run {run_id} was redispatched with changed context"
            )
        try:
            return RoleResult.from_payload(dict(settlement["result"]))
        except (KeyError, TypeError, ValueError) as error:
            raise DispatchError(
                f"terminal attempt settlement for {run_id} is malformed"
            ) from error

    def _settle_budgeted_attempt(
        self,
        *,
        manifest: ContextManifest,
        result: RoleResult,
        attempt_generation: int,
        attempt_ordinal: int,
        attempt_started: bool,
        estimate_micros: int | None,
        cost_state: str,
        observed_cost_micros: int | None,
        outcome: str,
        terminal_run: bool,
        provider_event_type: str,
        failure_class: str | None,
        lineage: Mapping[str, Any],
        retryable: bool = False,
        retry_after_seconds: float | None = None,
        possible_effect: bool = False,
        cost_reason: str | None = None,
        totals: Mapping[str, int] | None = None,
        provider_details: Mapping[str, Any] | None = None,
        extra_events: Iterable[Event] = (),
    ) -> RoleResult:
        if result.run_id != manifest.run_id or result.role != manifest.role:
            raise DispatchError(
                "attempt result identity must match the dispatched manifest"
            )
        provider = manifest.provider
        model = manifest.model
        identity = (
            f"{result.run_id}:{attempt_generation}:{attempt_ordinal}:"
            f"{provider}:{model}"
        )
        cost_fields: dict[str, Any] = {}
        for name, micros in dict(totals or {}).items():
            cost_fields[f"{name}_micros"] = micros
            cost_fields[f"{name}_usd"] = self._cost_usd(micros)
        settlement_payload = {
            "schema": _ATTEMPT_SETTLEMENT_SCHEMA,
            "settlement_id": identity,
            "run_id": result.run_id,
            "mission_id": manifest.mission_id,
            "step_id": manifest.step_id,
            "role": manifest.role,
            "provider": provider,
            "model": model,
            "attempt_generation": attempt_generation,
            "attempt_ordinal": attempt_ordinal,
            "attempt_started": bool(attempt_started),
            "context_manifest_hash": manifest.content_hash(),
            "estimate_state": (
                "AVAILABLE" if estimate_micros is not None else "UNAVAILABLE"
            ),
            "estimated_cost_micros": estimate_micros,
            "estimated_cost_usd": (
                self._cost_usd(estimate_micros)
                if estimate_micros is not None
                else None
            ),
            "cost_state": cost_state,
            "cost_reason": cost_reason,
            "observed_cost_micros": observed_cost_micros,
            "observed_cost_usd": (
                self._cost_usd(observed_cost_micros)
                if observed_cost_micros is not None
                else None
            ),
            "outcome": outcome,
            "terminal_run": bool(terminal_run),
            "result": result.to_payload(),
            **cost_fields,
            **dict(lineage),
        }
        events = [self._event(
            "provider.dispatch_attempt_settled",
            settlement_payload,
            idempotency_key=f"provider.dispatch_attempt_settled:{identity}",
        )]
        if cost_state == "OBSERVED":
            if observed_cost_micros is None:
                raise DispatchError("observed settlement requires cost micros")
            events.append(self._event(
                "provider.dispatch_cost_recorded",
                {
                    "run_id": result.run_id,
                    "mission_id": manifest.mission_id,
                    "step_id": manifest.step_id,
                    "role": manifest.role,
                    "provider_id": provider,
                    "provider": provider,
                    "model_id": model,
                    "model": model,
                    "attempt_generation": attempt_generation,
                    "attempt_ordinal": attempt_ordinal,
                    "cost_micros": observed_cost_micros,
                    "cost_usd": self._cost_usd(observed_cost_micros),
                    **dict(lineage),
                },
                idempotency_key=f"provider.dispatch_cost_recorded:{identity}",
            ))
        elif cost_state == "UNAVAILABLE":
            reason = cost_reason or "attempt cost telemetry was unavailable"
            events.append(self._event(
                "provider.dispatch_cost_unavailable",
                {
                    "run_id": result.run_id,
                    "mission_id": manifest.mission_id,
                    "step_id": manifest.step_id,
                    "role": manifest.role,
                    "provider_id": provider,
                    "provider": provider,
                    "model_id": model,
                    "model": model,
                    "attempt_generation": attempt_generation,
                    "attempt_ordinal": attempt_ordinal,
                    "reason": reason,
                    **dict(lineage),
                },
                idempotency_key=f"provider.dispatch_cost_unavailable:{identity}",
            ))
        elif cost_state != "NOT_INCURRED":
            raise DispatchError(f"unsupported attempt cost state: {cost_state}")

        provider_payload = {
            "run_id": result.run_id,
            "mission_id": manifest.mission_id,
            "step_id": manifest.step_id,
            "role": manifest.role,
            "provider_id": (
                result.provider
                if provider_event_type == "provider.dispatch_succeeded"
                else provider
            ),
            "provider": (
                result.provider
                if provider_event_type == "provider.dispatch_succeeded"
                else provider
            ),
            "model_id": (
                result.model
                if provider_event_type == "provider.dispatch_succeeded"
                else model
            ),
            "model": (
                result.model
                if provider_event_type == "provider.dispatch_succeeded"
                else model
            ),
            "attempt_generation": attempt_generation,
            "attempt_ordinal": attempt_ordinal,
            "failure_class": failure_class,
            "retryable": bool(retryable),
            "retry_after_seconds": retry_after_seconds,
            "possible_effect": bool(possible_effect),
            "effect_action_key": result.effect_action_key,
            **cost_fields,
            **dict(provider_details or {}),
            **dict(lineage),
        }
        events.append(self._event(
            provider_event_type,
            provider_payload,
            idempotency_key=f"{provider_event_type}:{identity}:{outcome}",
        ))
        events.extend(extra_events)
        if provider_event_type == "provider.dispatch_succeeded":
            events.append(self._event(
                "agent_run.completed",
                {
                    "run_id": result.run_id,
                    "result": result.to_payload(),
                    "cost": {"usd": result.cost_usd},
                },
                idempotency_key=f"agent_run.completed:{result.run_id}",
            ))
        elif provider_event_type == "provider.dispatch_failed" or terminal_run:
            run_failure = result.error_class or failure_class or "session_failed"
            failure_payload: dict[str, Any] = {
                "run_id": result.run_id,
                "failure_class": run_failure,
            }
            if result.error_detail is not None:
                failure_payload["detail"] = result.error_detail
            events.append(self._event(
                "agent_run.failed",
                failure_payload,
                idempotency_key=(
                    f"agent_run.failed:{result.run_id}:{run_failure}:"
                    f"{attempt_generation}:{attempt_ordinal}"
                ),
            ))
        self.database.append_events(events)
        return result

    def _cost_budget_exhausted_result(
        self,
        *,
        result: RoleResult,
        role: str,
        totals: _CostTotals,
        budget_micros: int,
        observed_cost_micros: int | None = None,
        detail_role: str | None = None,
    ) -> RoleResult:
        observed_cost_micros = (
            totals.budget_spend(role)
            if observed_cost_micros is None
            else observed_cost_micros
        )
        detail = self._cost_budget_detail(
            role=detail_role or role,
            observed_cost_micros=observed_cost_micros,
            budget_micros=budget_micros,
        )
        return replace(
            result,
            status=BLOCKED,
            verdict="BLOCKED",
            error_class="workforce_cost_budget_exhausted",
            error_detail=detail,
            cost_usd=self._cost_usd(totals.reported(
                role,
                package_budget=True,
            )),
        )

    def _record_attempt_cost(
        self,
        *,
        run_id: str,
        mission_id: str,
        step_id: str | None,
        role: str,
        provider: str,
        model: str,
        attempt_generation: int,
        attempt_ordinal: int,
        cost_usd: float,
        record_zero: bool,
        lineage: Mapping[str, Any],
    ) -> int:
        cost_micros = self._cost_micros(cost_usd)
        if cost_micros is None:
            raise DispatchError("attempt cost must be finite and non-negative")
        if cost_micros == 0 and not record_zero:
            return self._observed_cost_totals(run_id).run_micros
        self._append("provider.dispatch_cost_recorded", {
            "run_id": run_id,
            "mission_id": mission_id,
            "step_id": step_id,
            "role": role,
            "provider_id": provider,
            "provider": provider,
            "model_id": model,
            "model": model,
            "attempt_generation": attempt_generation,
            "attempt_ordinal": attempt_ordinal,
            "cost_micros": cost_micros,
            "cost_usd": self._cost_usd(cost_micros),
            **dict(lineage),
        }, idempotency_key=(
            f"provider.dispatch_cost_recorded:{run_id}:"
            f"{attempt_generation}:{attempt_ordinal}:{provider}:{model}"
        ))
        return self._observed_cost_totals(run_id).run_micros

    def _record_cost_unavailable(
        self,
        *,
        run_id: str,
        mission_id: str,
        step_id: str | None,
        role: str,
        provider: str,
        model: str,
        attempt_generation: int,
        attempt_ordinal: int,
        reason: str,
        lineage: Mapping[str, Any],
    ) -> None:
        self._append("provider.dispatch_cost_unavailable", {
            "run_id": run_id,
            "mission_id": mission_id,
            "step_id": step_id,
            "role": role,
            "provider_id": provider,
            "provider": provider,
            "model_id": model,
            "model": model,
            "attempt_generation": attempt_generation,
            "attempt_ordinal": attempt_ordinal,
            "reason": reason,
            **dict(lineage),
        }, idempotency_key=(
            f"provider.dispatch_cost_unavailable:{run_id}:"
            f"{attempt_generation}:{attempt_ordinal}:{provider}:{model}"
        ))

    def _effect_reconciliation_requirement(
        self,
        *,
        run_id: str,
        mission_id: str,
        step_id: str | None,
        provider: str,
        model: str,
        failure_class: str,
        reason: str,
        effect_action_key: str,
        attempt_generation: int,
        attempt_ordinal: int,
        lineage: Mapping[str, Any],
        observed_effect_action_key: str | None = None,
    ) -> Event:
        requirement_id = (
            f"{run_id}:{attempt_generation}:{attempt_ordinal}:"
            f"{effect_action_key}"
        )
        return self._event(
            "provider.effect_reconciliation_required",
            {
                "run_id": run_id,
                "mission_id": mission_id,
                "step_id": step_id,
                "provider": provider,
                "model": model,
                "failure_class": failure_class,
                "reason": str(reason)[:500],
                **dict(lineage),
                "effect_action_key": effect_action_key,
                "attempt_generation": attempt_generation,
                "attempt_ordinal": attempt_ordinal,
                "effect_requirement_id": requirement_id,
                **(
                    {
                        "observed_effect_action_key": (
                            observed_effect_action_key
                        )
                    }
                    if observed_effect_action_key
                    else {}
                ),
            },
            idempotency_key=(
                "provider.effect_reconciliation_required:"
                f"{requirement_id}"
            ),
        )

    def _pending_effect_reconciliation(
        self, run_id: str,
    ) -> tuple[str, str] | None:
        events = tuple(self.database.events(readonly=True))
        required: list[tuple[int, str, str, str]] = []
        receipts: list[tuple[int, str, str]] = []
        for index, event in enumerate(events):
            if str(event.payload.get("run_id") or "") != run_id:
                continue
            action_key = str(event.payload.get("effect_action_key") or "")
            if not action_key:
                continue
            if event.event_type == "provider.effect_reconciliation_required":
                generation = event.payload.get("attempt_generation")
                ordinal = event.payload.get("attempt_ordinal")
                requirement_id = str(
                    event.payload.get("effect_requirement_id") or ""
                )
                legacy = (
                    generation is None
                    and ordinal is None
                    and not requirement_id
                )
                if legacy:
                    requirement_id = f"legacy:{event.event_id}"
                    expected_key = (
                        "provider.effect_reconciliation_required:"
                        f"{run_id}:{action_key}"
                    )
                else:
                    if (
                        not isinstance(generation, int)
                        or isinstance(generation, bool)
                        or generation < 0
                        or not isinstance(ordinal, int)
                        or isinstance(ordinal, bool)
                        or ordinal < 1
                    ):
                        raise EventConflict(
                            "effect reconciliation requirement has invalid "
                            "attempt identity"
                        )
                    expected_requirement_id = (
                        f"{run_id}:{generation}:{ordinal}:{action_key}"
                    )
                    if requirement_id != expected_requirement_id:
                        raise EventConflict(
                            "effect reconciliation requirement changed its "
                            "attempt identity"
                        )
                    expected_key = (
                        "provider.effect_reconciliation_required:"
                        f"{requirement_id}"
                    )
                if (
                    event.source_type != self.source_type
                    or event.source_id != self.source_id
                    or event.idempotency_key != expected_key
                ):
                    raise EventConflict(
                        "effect reconciliation requirement lacks canonical "
                        f"dispatcher authority: {requirement_id}"
                    )
                required.append((
                    index,
                    action_key,
                    str(event.payload.get("provider") or ""),
                    requirement_id,
                ))
            elif event.event_type == "provider.effect_reconciled":
                requirement_id = str(
                    event.payload.get("effect_requirement_id") or ""
                )
                expected_key = (
                    f"provider.effect_reconciled:{requirement_id}:RECONCILED"
                    if requirement_id
                    else (
                        f"provider.effect_reconciled:{run_id}:"
                        f"{action_key}:RECONCILED"
                    )
                )
                if (
                    event.source_type == self.source_type
                    and event.source_id == self.source_id
                    and event.idempotency_key == expected_key
                    and str(event.payload.get("status") or "")
                    == "RECONCILED"
                ):
                    receipts.append((
                        index,
                        action_key,
                        requirement_id,
                    ))
        for (
            requirement_index,
            action_key,
            provider,
            requirement_id,
        ) in reversed(required):
            if any(
                receipt_index > requirement_index
                and receipt_action_key == action_key
                and (
                    receipt_requirement_id == requirement_id
                    or (
                        requirement_id.startswith("legacy:")
                        and not receipt_requirement_id
                    )
                )
                for (
                    receipt_index,
                    receipt_action_key,
                    receipt_requirement_id,
                ) in receipts
            ):
                continue
            reconciliation = next(
                (
                    event
                    for event in events[requirement_index + 1:]
                    if (
                        event.event_type == "effect.reconciled"
                        and event.source_type == "supervisor"
                        and event.source_id == "v5-effect-driver"
                        and event.idempotency_key is not None
                        and event.idempotency_key.startswith(
                            f"{action_key}.reconciled."
                        )
                        and str(event.payload.get("action_key") or "")
                        == action_key
                    )
                )
                ,
                None,
            )
            if reconciliation is not None:
                receipt_requirement_id = (
                    ""
                    if requirement_id.startswith("legacy:")
                    else requirement_id
                )
                self._append(
                    "provider.effect_reconciled",
                    {
                        "run_id": run_id,
                        "provider": provider,
                        "effect_action_key": action_key,
                        "status": "RECONCILED",
                        "reconciliation_event_id": reconciliation.event_id,
                        **(
                            {
                                "effect_requirement_id": (
                                    receipt_requirement_id
                                )
                            }
                            if receipt_requirement_id
                            else {}
                        ),
                    },
                    idempotency_key=(
                        "provider.effect_reconciled:"
                        f"{receipt_requirement_id}:RECONCILED"
                        if receipt_requirement_id
                        else (
                            f"provider.effect_reconciled:{run_id}:"
                            f"{action_key}:RECONCILED"
                        )
                    ),
                )
                continue
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
            "COST_UNKNOWN": "workforce_cost_estimate_unavailable",
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
        allowed_paths: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        required_tests: tuple[str, ...] = (),
        attempt: int = 1,
        routing_request: RoutingRequest | None = None,
        campaign_lineage: Mapping[str, str] | None = None,
        campaign_isolation_key: str | None = None,
        effect_action_key: str | None = None,
        package_id: str | None = None,
        package_hash: str | None = None,
        attempt_id: str | None = None,
        worktree_phase: "ExecutionWorktreePhase | None" = None,
        worker_preference: str | None = None,
        worker_preference_evidence: tuple[str, ...] = (),
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
        package_lineage = (package_id, package_hash, attempt_id)
        if all(package_lineage):
            lineage = {
                **lineage,
                "package_id": package_id,
                "package_hash": package_hash,
                "attempt_id": attempt_id,
            }
        existing_run = self.database.row("agent_runs", "run_id", run_id)
        workforce_active = all(package_lineage)
        attempt_generation: int | None = None
        preflight_terminal: dict[str, Any] | None = None
        validation_worktree_phase = worktree_phase
        manifest_worktree_phase = worktree_phase
        if workforce_active:
            if validation_worktree_phase is None:
                validation_worktree_phase = self._default_worktree_phase(role)
            if existing_run is not None:
                attempt_generation = self._attempt_generation(run_id)
                preflight_terminal = self._terminal_attempt_settlement(
                    run_id,
                    attempt_generation,
                    str(package_id),
                    str(package_hash),
                )
                self._validate_existing_workforce_run(
                    existing_run,
                    run_id=run_id,
                    mission_id=mission_id,
                    step_id=step_id,
                    role=role,
                    terminal_replay_available=preflight_terminal is not None,
                    provider_resume_available=(
                        self._has_unconsumed_provider_resume(run_id)
                    ),
                )
                validation_worktree_phase = "job_delta"
                manifest_worktree_phase = self._recorded_worktree_phase(run_id)
            elif manifest_worktree_phase is None:
                manifest_worktree_phase = validation_worktree_phase
        durable_package = self.validate_workforce_dispatch(
            package_id=package_id,
            package_hash=package_hash,
            attempt_id=attempt_id,
            run_id=run_id,
            mission_id=mission_id,
            step_id=step_id,
            role=role,
            resource_id=resource_id,
            capability=capability,
            duration_seconds=duration_seconds,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            expected_artifacts=expected_artifacts,
            required_tests=required_tests,
            effect_action_key=effect_action_key,
            worktree_phase=validation_worktree_phase,
        )
        durable_budget_micros: int | None = None
        if durable_package is not None:
            if durable_package.cost_budget_usd is not None:
                durable_budget_micros = self._cost_micros(
                    durable_package.cost_budget_usd
                )
                if durable_budget_micros is None:
                    raise ValueError("durable WorkPackage cost budget is invalid")
                requested_budget_micros = (
                    self._cost_micros(task_budget)
                    if task_budget is not None
                    else durable_budget_micros
                )
                if requested_budget_micros is None:
                    raise ValueError("effective workforce cost budget is invalid")
                if requested_budget_micros > durable_budget_micros:
                    raise ValueError(
                        "effective workforce cost budget exceeds the immutable "
                        "WorkPackage cost_budget_usd"
                    )
                task_budget = self._cost_usd(requested_budget_micros)
                effective_request = replace(
                    effective_request,
                    max_cost_usd=task_budget,
                )
            elif task_budget is not None:
                raise ValueError(
                    "effective workforce cost budget requires an immutable "
                    "WorkPackage cost_budget_usd"
                )
        workforce_cost_telemetry_required = bool(
            all(package_lineage)
            and durable_budget_micros is not None
        )
        task_budget_micros = (
            self._cost_micros(task_budget)
            if task_budget is not None
            else None
        )
        if (
            workforce_cost_telemetry_required
            and task_budget_micros is None
        ):
            raise ValueError(
                "explicit workforce cost budget must be finite and non-negative"
            )
        manifest_budget_usd = (
            task_budget
            if workforce_cost_telemetry_required and task_budget is not None
            else budget_usd
        )

        def make_manifest(provider: str, model: str) -> ContextManifest:
            return self._manifest(
                run_id,
                role,
                mission_id,
                step_id,
                resource_id,
                capability,
                instructions,
                duration_seconds,
                manifest_budget_usd,
                provider,
                config,
                model=model,
                expected_artifacts=expected_artifacts,
                verification_criteria=verification_criteria,
                allowed_paths=allowed_paths,
                forbidden_paths=forbidden_paths,
                required_tests=required_tests,
                attempt=attempt,
                routing_request=routing_request,
                campaign_lineage=campaign_lineage,
                campaign_isolation_key=campaign_isolation_key,
                effect_action_key=effect_action_key,
                package_id=package_id,
                package_hash=package_hash,
                attempt_id=attempt_id,
                worktree_phase=manifest_worktree_phase,
                worker_preference=worker_preference,
                worker_preference_evidence=worker_preference_evidence,
            )

        if workforce_active and existing_run is not None:
            prior_manifest = make_manifest(
                str(
                    existing_run.get("provider")
                    or config.preferred_provider
                ),
                str(existing_run.get("model") or config.preferred_model),
            )
            if (
                str(existing_run.get("context_manifest_hash") or "")
                != prior_manifest.content_hash()
            ):
                raise EventConflict(
                    f"workforce run {run_id} was redispatched with changed context"
                )
            if attempt_generation is None:
                attempt_generation = self._attempt_generation(run_id)
            resumable_fallback = (
                str(existing_run.get("status") or "") == "RUNNING"
                and self._can_resume_settled_workforce_fallback(
                    lease_key=lease_key,
                    run_id=run_id,
                    attempt_generation=attempt_generation,
                    package_id=str(package_id),
                    package_hash=str(package_hash),
                    attempt_id=str(attempt_id),
                )
            )
            if (
                str(existing_run.get("status") or "") == "RUNNING"
                and not resumable_fallback
            ):
                import time

                deadline = time.monotonic() + self.replay_wait_seconds
                while time.monotonic() < deadline:
                    time.sleep(0.05)
                    settled = self.database.row(
                        "agent_runs",
                        "run_id",
                        run_id,
                    )
                    if settled is None:
                        break
                    if settled.get("status") == "COMPLETED":
                        replayed = self._replay_completed(
                            run_id,
                            prior_manifest,
                        )
                        if replayed is not None:
                            return replayed
                        raise EventConflict(
                            f"workforce run {run_id} completed with changed "
                            "context"
                        )
                    if settled.get("status") == "FAILED":
                        return RoleResult(
                            run_id=run_id,
                            role=role,
                            status=BLOCKED,
                            provider=str(settled.get("provider") or ""),
                            model=str(settled.get("model") or ""),
                            verdict="BLOCKED",
                            error_class="run_reconciliation_required",
                            error_detail=(
                                f"workforce run {run_id} failed while a "
                                "duplicate caller was waiting; recover under "
                                "a fresh run_id"
                            ),
                            started_at=isoformat(utc_now()),
                            finished_at=isoformat(utc_now()),
                        )
                return RoleResult(
                    run_id=run_id,
                    role=role,
                    status=BLOCKED,
                    provider=str(existing_run.get("provider") or ""),
                    model=str(existing_run.get("model") or ""),
                    verdict="BLOCKED",
                    error_class="run_in_progress",
                    error_detail=(
                        f"workforce run {run_id} is already owned by a live "
                        "dispatcher; the bounded replay wait expired"
                    ),
                    started_at=isoformat(utc_now()),
                    finished_at=isoformat(utc_now()),
                )

        if attempt_generation is None:
            attempt_generation = self._attempt_generation(run_id)
        terminal = preflight_terminal
        if terminal is None:
            terminal = self._terminal_attempt_settlement(
                run_id,
                attempt_generation,
                package_id,
                package_hash,
            )
        if terminal is not None:
            settled = self._replay_terminal_attempt(
                run_id,
                make_manifest(
                    str(terminal.get("provider") or config.preferred_provider),
                    str(terminal.get("model") or config.preferred_model),
                ),
                attempt_generation=attempt_generation,
                package_id=package_id,
                package_hash=package_hash,
            )
            if settled is not None:
                return settled

        completed = self.database.row("agent_runs", "run_id", run_id)
        if completed is not None and completed.get("status") == "COMPLETED":
            prospective = make_manifest(
                str(completed.get("provider") or config.preferred_provider),
                str(completed.get("model") or config.preferred_model),
            )
            replayed = self._replay_completed(run_id, prospective)
            if replayed is not None:
                return replayed
            raise EventConflict(
                f"completed run {run_id} was redispatched with changed context"
            )

        pending_effect = self._pending_effect_reconciliation(run_id)
        if pending_effect is not None:
            action_key, provider = pending_effect
            detail = (
                f"provider effect {action_key} remains ambiguous; "
                "reconcile before fallback"
            )
            return RoleResult(
                run_id=run_id,
                role=role,
                status=BLOCKED,
                provider=provider,
                model="",
                verdict="BLOCKED",
                error_class="provider_effect_reconciliation_required",
                error_detail=detail,
                effect_action_key=action_key,
                started_at=isoformat(utc_now()),
                finished_at=isoformat(utc_now()),
            )

        costs = self._observed_cost_totals(
            run_id,
            package_id,
            package_hash,
            attempt_id,
            role,
            settlement_authority=workforce_cost_telemetry_required,
        )

        if workforce_cost_telemetry_required:
            gate_provider = (
                provider_override
                or (legacy_chain[0] if legacy_chain else config.preferred_provider)
            )
            gate_manifest = make_manifest(gate_provider, config.preferred_model)
            reported_cost_micros = costs.reported(
                role,
                package_budget=True,
            )
            gate_totals = costs.settlement_fields()
            unavailable = self._package_cost_unavailable(
                package_id,
                package_hash,
            )
            if unavailable is not None:
                unavailable_result = unavailable.get("result")
                result_detail = (
                    unavailable_result.get("error_detail")
                    if isinstance(unavailable_result, Mapping)
                    else None
                )
                prior_reason = str(
                    unavailable.get("cost_reason")
                    or result_detail
                    or "a prior package attempt exposed no cost telemetry"
                )
                detail = (
                    "explicit workforce cost budget cannot continue because "
                    f"package spend is indeterminate: {prior_reason}"
                )
                result = self._budget_blocked_result(
                    gate_manifest,
                    error_class="workforce_cost_telemetry_unavailable",
                    detail=detail,
                    cost_micros=reported_cost_micros,
                )
                return self._settle_budgeted_attempt(
                    manifest=gate_manifest,
                    result=result,
                    attempt_generation=attempt_generation,
                    attempt_ordinal=0,
                    attempt_started=False,
                    estimate_micros=None,
                    cost_state="NOT_INCURRED",
                    observed_cost_micros=None,
                    outcome="PACKAGE_COST_UNAVAILABLE",
                    terminal_run=True,
                    provider_event_type="provider.dispatch_skipped",
                    failure_class="COST_UNKNOWN",
                    lineage=lineage,
                    totals=gate_totals,
                    provider_details={"reason": detail},
                )
            budget_observed_spend_micros = costs.budget_spend(role)
            exhausted_budget_micros: int | None = None
            exhausted_observed_micros = budget_observed_spend_micros
            exhausted_detail_role = role
            if (
                durable_budget_micros is not None
                and costs.package_micros >= durable_budget_micros
            ):
                exhausted_budget_micros = durable_budget_micros
                exhausted_observed_micros = costs.package_micros
                exhausted_detail_role = "builder"
                reported_cost_micros = costs.package_micros
            elif (
                task_budget_micros is not None
                and budget_observed_spend_micros >= task_budget_micros
            ):
                exhausted_budget_micros = task_budget_micros
            if exhausted_budget_micros is not None:
                detail = self._cost_budget_detail(
                    role=exhausted_detail_role,
                    observed_cost_micros=exhausted_observed_micros,
                    budget_micros=exhausted_budget_micros,
                )
                result = self._budget_blocked_result(
                    gate_manifest,
                    error_class="workforce_cost_budget_exhausted",
                    detail=detail,
                    cost_micros=reported_cost_micros,
                )
                return self._settle_budgeted_attempt(
                    manifest=gate_manifest,
                    result=result,
                    attempt_generation=attempt_generation,
                    attempt_ordinal=0,
                    attempt_started=False,
                    estimate_micros=None,
                    cost_state="NOT_INCURRED",
                    observed_cost_micros=None,
                    outcome="COST_LIMIT",
                    terminal_run=True,
                    provider_event_type="provider.dispatch_skipped",
                    failure_class="COST_LIMIT",
                    lineage=lineage,
                    totals={
                        **gate_totals,
                        "budget": exhausted_budget_micros,
                    },
                    provider_details={"reason": detail},
                )

        decision = None
        if candidates:
            if provider_override and not self.provider_override_allows_fallback:
                candidates = tuple(
                    item for item in candidates
                    if item.provider == provider_override)
            route_request = (
                replace(effective_request, max_cost_usd=None)
                if workforce_cost_telemetry_required
                else effective_request
            )
            decision = self.router.route(
                route_request,
                candidates,
                role=role,
            )
            self._append("provider.route_decided", {
                "run_id": run_id,
                "mission_id": mission_id,
                "step_id": step_id,
                "role": role,
                "attempt_generation": attempt_generation,
                "routing_request": (
                    routing_request.to_dict() if routing_request is not None else None
                ),
                "effective_routing_request": effective_request.to_dict(),
                **lineage,
                **decision.to_dict(),
            }, idempotency_key=(
                f"provider.route_decided:{run_id}:{attempt_generation}:{attempt}:"
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
            result = RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider="", model="",
                verdict="BLOCKED", error_class=error_class,
                error_detail=detail, wait_for_providers=wait_providers,
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )
            if workforce_cost_telemetry_required:
                settlement_provider = (
                    provider_override
                    or config.preferred_provider
                    or "unavailable"
                )
                settlement_model = config.preferred_model or "unavailable"
                result = replace(
                    result,
                    provider=settlement_provider,
                    model=settlement_model,
                )
                return self._settle_budgeted_attempt(
                    manifest=make_manifest(
                        settlement_provider,
                        settlement_model,
                    ),
                    result=result,
                    attempt_generation=attempt_generation,
                    attempt_ordinal=0,
                    attempt_started=False,
                    estimate_micros=None,
                    cost_state="NOT_INCURRED",
                    observed_cost_micros=None,
                    outcome="NO_CANDIDATES",
                    terminal_run=True,
                    provider_event_type="provider.dispatch_skipped",
                    failure_class=failure_class,
                    lineage=lineage,
                    totals=costs.settlement_fields(),
                    provider_details={
                        "reason": detail,
                        "wait_for_providers": list(wait_providers),
                    },
                )
            self._fail_run(run_id, error_class, detail)
            return result

        if not legacy_chain and not candidates:
            self._fail_run(run_id, "provider_unavailable", f"no providers configured for role {role}")
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider="", model="",
                verdict="BLOCKED", error_class="provider_unavailable",
                error_detail=f"no providers configured for role {role}",
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )

        prospective = make_manifest(
            chain_candidates[0].provider,
            chain_candidates[0].model,
        )

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
        history = self._attempt_history(run_id, attempt_generation)
        provider_counts: dict[str, int] = {}
        for row in history:
            provider = str(row.get("provider") or row.get("provider_id") or "")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
        total_attempts = len(history)
        costs = self._observed_cost_totals(
            run_id,
            package_id,
            package_hash,
            attempt_id,
            role,
            settlement_authority=workforce_cost_telemetry_required,
        )
        estimated_spend_micros = sum(
            (
                int(row["estimated_cost_micros"])
                if row.get("estimated_cost_micros") is not None
                else self._cost_micros(row.get("estimated_cost_usd")) or 0
            )
            for row in history
        )
        failure_classes: list[str] = []
        wait_eligible: list[str] = []
        package_budget_estimate_blocked = False
        try:
            queue = list(chain_candidates)
            while queue and total_attempts < self.max_total_attempts:
                candidate = queue.pop(0)
                provider = candidate.provider
                model = candidate.model
                count = provider_counts.get(provider, 0)
                if count >= 1 + self.max_retries_per_provider:
                    continue
                manifest = make_manifest(provider, model)
                estimate_micros = self._cost_micros(candidate.cost_usd)
                next_ordinal = total_attempts + 1

                def settle_attempt(
                    settled_result: RoleResult,
                    *,
                    outcome: str,
                    attempt_started: bool = True,
                    cost_state: str = "UNAVAILABLE",
                    observed_cost_micros: int | None = None,
                    terminal_run: bool = True,
                    provider_event_type: str = "provider.dispatch_failed",
                    failure_class: str | None = None,
                    retryable: bool = False,
                    retry_after_seconds: float | None = None,
                    possible_effect: bool = False,
                    cost_reason: str | None = None,
                    totals: Mapping[str, int] | None = None,
                    provider_details: Mapping[str, Any] | None = None,
                    extra_events: Iterable[Event] = (),
                ) -> RoleResult:
                    return self._settle_budgeted_attempt(
                        manifest=manifest,
                        result=settled_result,
                        attempt_generation=attempt_generation,
                        attempt_ordinal=next_ordinal,
                        attempt_started=attempt_started,
                        estimate_micros=estimate_micros,
                        cost_state=cost_state,
                        observed_cost_micros=observed_cost_micros,
                        outcome=outcome,
                        terminal_run=terminal_run,
                        provider_event_type=provider_event_type,
                        failure_class=failure_class,
                        lineage=lineage,
                        retryable=retryable,
                        retry_after_seconds=retry_after_seconds,
                        possible_effect=possible_effect,
                        cost_reason=cost_reason,
                        totals=totals,
                        provider_details=provider_details,
                        extra_events=extra_events,
                    )

                def current_reported_cost_micros() -> int:
                    return costs.reported(role, package_budget=True)

                if (
                    workforce_cost_telemetry_required
                    and estimate_micros is None
                ):
                    detail = (
                        "explicit workforce cost budget requires a finite "
                        "non-negative provider estimate before dispatch; "
                        f"{provider}/{model} has no usable estimate"
                    )
                    result = self._budget_blocked_result(
                        manifest,
                        error_class="workforce_cost_estimate_unavailable",
                        detail=detail,
                        cost_micros=current_reported_cost_micros(),
                    )
                    return settle_attempt(
                        result,
                        outcome="ESTIMATE_UNAVAILABLE",
                        attempt_started=False,
                        cost_state="NOT_INCURRED",
                        provider_event_type="provider.dispatch_skipped",
                        failure_class="COST_UNKNOWN",
                    )
                if estimate_micros is None:
                    estimate_micros = 0
                budget_estimated_spend_micros = estimated_spend_micros
                estimate_budget_micros = task_budget_micros
                if workforce_cost_telemetry_required:
                    budget_estimated_spend_micros = costs.budget_spend(role)
                    if (
                        durable_budget_micros is not None
                        and costs.package_micros + estimate_micros
                        > durable_budget_micros
                    ):
                        package_budget_estimate_blocked = True
                        budget_estimated_spend_micros = costs.package_micros
                        estimate_budget_micros = durable_budget_micros
                if (
                    estimate_budget_micros is not None
                    and (
                        budget_estimated_spend_micros + estimate_micros
                        > estimate_budget_micros
                    )
                ):
                    failure_classes.append("COST_LIMIT")
                    projected_micros = (
                        budget_estimated_spend_micros + estimate_micros
                    )
                    last_error = ProviderFailure(
                        f"cumulative provider estimate would exceed budget: "
                        f"{self._cost_usd(projected_micros):.6f} > "
                        f"{self._cost_usd(estimate_budget_micros):.6f}",
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
                        "attempt_generation": attempt_generation,
                        "estimated_spend_micros": budget_estimated_spend_micros,
                        "estimated_spend_usd": self._cost_usd(
                            budget_estimated_spend_micros
                        ),
                        "candidate_estimated_cost_micros": estimate_micros,
                        "candidate_estimated_cost_usd": self._cost_usd(
                            estimate_micros
                        ),
                        "budget_micros": estimate_budget_micros,
                        "budget_usd": self._cost_usd(
                            estimate_budget_micros
                        ),
                        **lineage,
                    }, idempotency_key=(
                        f"provider.dispatch_skipped:{run_id}:"
                        f"{attempt_generation}:{provider}:"
                        f"cost:{budget_estimated_spend_micros}:"
                        f"{estimate_budget_micros}"))
                    continue
                total_attempts += 1
                provider_counts[provider] = count + 1
                estimated_spend_micros += estimate_micros
                self._start_run(manifest, provider, model)
                invoke_fault_hook(self.fault_hook, "post agent_run.started")
                attempt_inserted = self._record_provider_attempt(
                    run_id=run_id,
                    mission_id=mission_id,
                    step_id=step_id,
                    role=role,
                    candidate=candidate,
                    attempt_generation=attempt_generation,
                    ordinal=total_attempts,
                    estimated_cost_micros=estimate_micros,
                    lineage=lineage,
                )
                if all(package_lineage) and not attempt_inserted:
                    detail = (
                        "a prior process may have dispatched this provider "
                        "attempt without a terminal receipt; the call will "
                        "not be repeated"
                    )
                    if workforce_cost_telemetry_required:
                        result = self._budget_blocked_result(
                            manifest,
                            error_class="workforce_cost_telemetry_unavailable",
                            detail=detail,
                            cost_micros=current_reported_cost_micros(),
                            effect_action_key=effect_action_key,
                        )
                        failure_class = "COST_UNKNOWN"
                    else:
                        result = RoleResult(
                            run_id=run_id,
                            role=role,
                            status=BLOCKED,
                            provider=provider,
                            model=model,
                            verdict="BLOCKED",
                            error_class=(
                                "provider_attempt_reconciliation_required"
                            ),
                            error_detail=detail,
                            effect_action_key=effect_action_key,
                            cost_usd=self._cost_usd(costs.run_micros),
                            started_at=isoformat(utc_now()),
                            finished_at=isoformat(utc_now()),
                        )
                        failure_class = "ATTEMPT_OUTCOME_UNKNOWN"
                    return settle_attempt(
                        result,
                        outcome="INTERRUPTED_UNKNOWN",
                        failure_class=failure_class,
                        possible_effect=bool(effect_action_key),
                        cost_reason=detail,
                    )
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
                    invoke_fault_hook(
                        self.fault_hook,
                        "post provider dispatch",
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
                        effect_action_key or reported_action_key
                    )
                    effect_identity_mismatch = bool(
                        effect_action_key
                        and reported_action_key
                        and reported_action_key != effect_action_key
                    )
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
                    if workforce_cost_telemetry_required:
                        cost_detail = (
                            "explicit workforce cost budget requires observed "
                            "usage cost for every provider attempt; failed "
                            f"attempt {provider}/{model} exposed no cost "
                            f"telemetry: {str(error)[-400:]}"
                        )
                        action_key = resolved_action_key
                        extra_events: list[Event] = []
                        if error.possible_effect:
                            if not action_key:
                                action_key = (
                                    f"provider-effect:{mission_id}:"
                                    f"{step_id or 'start'}:{run_id}"
                                )
                            if effect_identity_mismatch:
                                extra_events.append(self._event(
                                    "provider.effect_identity_mismatch",
                                    {
                                        "run_id": run_id,
                                        "mission_id": mission_id,
                                        "step_id": step_id,
                                        "provider": provider,
                                        "expected_effect_action_key": (
                                            effect_action_key
                                        ),
                                        "observed_effect_action_key": (
                                            reported_action_key
                                        ),
                                        "attempt_generation": (
                                            attempt_generation
                                        ),
                                        "attempt_ordinal": total_attempts,
                                        **lineage,
                                    },
                                    idempotency_key=(
                                        "provider.effect_identity_mismatch:"
                                        f"{run_id}:{attempt_generation}:"
                                        f"{total_attempts}:"
                                        f"{reported_action_key}"
                                    ),
                                ))
                            extra_events.append(
                                self._effect_reconciliation_requirement(
                                    run_id=run_id,
                                    mission_id=mission_id,
                                    step_id=step_id,
                                    provider=provider,
                                    model=model,
                                    failure_class=failure_class,
                                    reason=str(error),
                                    effect_action_key=action_key,
                                    attempt_generation=attempt_generation,
                                    attempt_ordinal=total_attempts,
                                    lineage=lineage,
                                    observed_effect_action_key=(
                                        reported_action_key
                                        if effect_identity_mismatch
                                        else None
                                    ),
                                )
                            )
                            result_error_class = (
                                "provider_effect_identity_mismatch"
                                if effect_identity_mismatch
                                else "provider_effect_reconciliation_required"
                            )
                            result_detail = (
                                "possible external mutation must be reconciled "
                                "before fallback; attempt cost is unavailable"
                            )
                        else:
                            result_error_class = (
                                "workforce_cost_telemetry_unavailable"
                            )
                            result_detail = cost_detail
                        result = self._budget_blocked_result(
                            manifest,
                            error_class=result_error_class,
                            detail=result_detail,
                            cost_micros=current_reported_cost_micros(),
                            effect_action_key=action_key,
                        )
                        result = settle_attempt(
                            result,
                            outcome=(
                                "POSSIBLE_EFFECT_COST_UNKNOWN"
                                if error.possible_effect
                                else "PROVIDER_FAILURE_COST_UNKNOWN"
                            ),
                            failure_class=failure_class,
                            retry_after_seconds=error.retry_after_seconds,
                            possible_effect=error.possible_effect,
                            cost_reason=cost_detail,
                            extra_events=extra_events,
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
                        return result

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
                        "attempt_generation": attempt_generation,
                        "attempt_ordinal": total_attempts,
                        **lineage,
                        "effect_action_key": resolved_action_key,
                    }, idempotency_key=(
                        f"provider.dispatch_failed:{run_id}:"
                        f"{attempt_generation}:{total_attempts}:"
                        f"{provider}:{failure_class}"))
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
                                "attempt_generation": attempt_generation,
                                "attempt_ordinal": total_attempts,
                                **lineage,
                            }, idempotency_key=(
                                f"provider.effect_identity_mismatch:{run_id}:"
                                f"{attempt_generation}:{total_attempts}:"
                                f"{reported_action_key}"
                            ))
                        self.database.append_events([
                            self._effect_reconciliation_requirement(
                                run_id=run_id,
                                mission_id=mission_id,
                                step_id=step_id,
                                provider=provider,
                                model=model,
                                failure_class=failure_class,
                                reason=str(error),
                                effect_action_key=action_key,
                                attempt_generation=attempt_generation,
                                attempt_ordinal=total_attempts,
                                lineage=lineage,
                                observed_effect_action_key=(
                                    reported_action_key
                                    if effect_identity_mismatch
                                    else None
                                ),
                            )
                        ])
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
                            cost_usd=self._cost_usd(costs.run_micros),
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
                    if workforce_cost_telemetry_required:
                        detail = (
                            "explicit workforce cost budget requires observed "
                            "usage cost for every provider attempt; adapter "
                            f"failed before cost telemetry was available: {error}"
                        )
                        result = self._budget_blocked_result(
                            manifest,
                            error_class="workforce_cost_telemetry_unavailable",
                            detail=detail,
                            cost_micros=current_reported_cost_micros(),
                        )
                        return settle_attempt(
                            result,
                            outcome="ADAPTER_CRASH_COST_UNKNOWN",
                            failure_class="COST_UNKNOWN",
                            cost_reason=detail,
                        )
                    self._fail_run(run_id, "adapter_crash", str(error), suffix=provider)
                    return RoleResult(
                        run_id=run_id, role=role, status=FAILED,
                        provider=provider, model=model,
                        verdict="NOT_TESTED", error_class="adapter_crash",
                        error_detail=str(error),
                        started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
                    )
                observed_provider = result.provider
                observed_model = result.model
                observed_provider_evidence = tuple(result.evidence)
                identity_changed = (
                    observed_provider != provider
                    or observed_model != model
                )
                authorized_local_fabric = (
                    result.status == COMPLETED
                    and result.identity_transition_authorized
                    and observed_provider == "fabric"
                    and observed_model == "local"
                    and any(
                        str(item).startswith("fabric:")
                        for item in observed_provider_evidence
                    )
                )
                provider_identity_rejected = bool(
                    identity_changed and not authorized_local_fabric
                )
                result_identity_rejected = bool(
                    result.run_id != run_id or result.role != role
                )
                identity_events: tuple[Event, ...] = ()
                if result_identity_rejected:
                    observed_run_id = result.run_id
                    observed_role = result.role
                    detail = (
                        "provider result changed dispatched run/role identity: "
                        f"expected={run_id}/{role} "
                        f"observed={observed_run_id}/{observed_role}"
                    )
                    action_key = (
                        effect_action_key
                        or (
                            f"provider-effect:{mission_id}:"
                            f"{step_id or 'start'}:{run_id}"
                        )
                    )
                    identity_events = (
                        self._event(
                            "provider.result_identity_mismatch",
                            {
                                "run_id": run_id,
                                "mission_id": mission_id,
                                "step_id": step_id,
                                "role": role,
                                "provider": provider,
                                "model": model,
                                "observed_run_id": observed_run_id,
                                "observed_role": observed_role,
                                "reason": detail,
                                "attempt_generation": attempt_generation,
                                "attempt_ordinal": total_attempts,
                                **lineage,
                            },
                            idempotency_key=(
                                "provider.result_identity_mismatch:"
                                f"{run_id}:{attempt_generation}:"
                                f"{total_attempts}:{provider}:{model}"
                            ),
                        ),
                        self._effect_reconciliation_requirement(
                            run_id=run_id,
                            mission_id=mission_id,
                            step_id=step_id,
                            provider=provider,
                            model=model,
                            failure_class="INCOMPATIBLE",
                            reason=detail,
                            effect_action_key=action_key,
                            attempt_generation=attempt_generation,
                            attempt_ordinal=total_attempts,
                            lineage=lineage,
                            observed_effect_action_key=(
                                str(result.effect_action_key or "") or None
                            ),
                        ),
                    )
                    result = replace(
                        result,
                        run_id=run_id,
                        role=role,
                        provider=provider,
                        model=model,
                        status=BLOCKED,
                        verdict="BLOCKED",
                        error_class="provider_effect_reconciliation_required",
                        error_detail=detail,
                        effect_action_key=action_key,
                    )
                if provider_identity_rejected:
                    detail = (
                        "provider/model identity changed during dispatch: "
                        f"requested={provider}/{model} "
                        f"observed={observed_provider}/{observed_model}"
                    )
                    effect_identity = (
                        effect_action_key
                        or (
                            f"provider-effect:{mission_id}:"
                            f"{step_id or 'start'}:{run_id}"
                        )
                    )
                    mismatch_events = [self._event(
                        "provider.identity_mismatch",
                        {
                            "run_id": run_id,
                            "mission_id": mission_id,
                            "step_id": step_id,
                            "role": role,
                            "requested_provider": provider,
                            "requested_model": model,
                            "observed_provider": observed_provider,
                            "observed_model": observed_model,
                            "reason": detail,
                            **lineage,
                        },
                        idempotency_key=(
                            f"provider.identity_mismatch:{run_id}:"
                            f"{provider}:{model}"
                        ),
                    )]
                    if not result_identity_rejected:
                        mismatch_events.append(
                            self._effect_reconciliation_requirement(
                                run_id=run_id,
                                mission_id=mission_id,
                                step_id=step_id,
                                provider=provider,
                                model=model,
                                failure_class="INCOMPATIBLE",
                                reason=detail,
                                effect_action_key=effect_identity,
                                attempt_generation=attempt_generation,
                                attempt_ordinal=total_attempts,
                                lineage=lineage,
                                observed_effect_action_key=(
                                    str(result.effect_action_key or "") or None
                                ),
                            )
                        )
                    identity_events = (
                        *identity_events,
                        *tuple(mismatch_events),
                    )
                    combined_detail = "; ".join(
                        item
                        for item in (result.error_detail, detail)
                        if item
                    )
                    result = replace(
                        result,
                        provider=provider,
                        model=model,
                        status=BLOCKED,
                        verdict="BLOCKED",
                        error_class="provider_effect_reconciliation_required",
                        error_detail=combined_detail,
                        effect_action_key=effect_identity,
                    )
                elif identity_changed and not result_identity_rejected:
                    transition = self._event(
                        "provider.identity_transition_authorized",
                        {
                            "run_id": run_id,
                            "mission_id": mission_id,
                            "step_id": step_id,
                            "role": role,
                            "requested_provider": provider,
                            "requested_model": model,
                            "observed_provider": observed_provider,
                            "observed_model": observed_model,
                            "authority": "canonical_capability_fabric",
                            "evidence": list(observed_provider_evidence),
                            **lineage,
                        },
                        idempotency_key=(
                            "provider.identity_transition_authorized:"
                            f"{run_id}:{provider}:{model}:"
                            f"{observed_provider}:{observed_model}"
                        ),
                    )
                    if workforce_cost_telemetry_required:
                        identity_events = (transition,)
                    else:
                        self.database.append(transition)
                attempt_cost_micros = self._cost_micros(result.cost_usd)
                settlement_totals: dict[str, int] = {}
                if workforce_cost_telemetry_required:
                    if (
                        result.error_class
                        == "workforce_cost_telemetry_unavailable"
                        or attempt_cost_micros is None
                    ):
                        detail = (
                            result.error_detail
                            if result.error_class
                            == "workforce_cost_telemetry_unavailable"
                            else (
                                "explicit workforce cost budget requires a "
                                "finite non-negative observed cost for every "
                                "provider attempt"
                            )
                        ) or "attempt cost telemetry was unavailable"
                        if (
                            provider_identity_rejected
                            or result_identity_rejected
                        ):
                            return settle_attempt(
                                result,
                                outcome=(
                                    "IDENTITY_MISMATCH_COST_UNKNOWN"
                                ),
                                failure_class="INCOMPATIBLE",
                                possible_effect=bool(
                                    result.effect_action_key
                                ),
                                cost_reason=detail,
                                extra_events=identity_events,
                            )
                        result = self._budget_blocked_result(
                            manifest,
                            error_class="workforce_cost_telemetry_unavailable",
                            detail=detail,
                            cost_micros=current_reported_cost_micros(),
                            result=result,
                        )
                        return settle_attempt(
                            result,
                            outcome="RESULT_COST_UNKNOWN",
                            failure_class="COST_UNKNOWN",
                            possible_effect=bool(result.effect_action_key),
                            cost_reason=detail,
                            extra_events=identity_events,
                        )
                    projected = costs.add(attempt_cost_micros)
                    settlement_totals = projected.settlement_fields(
                        attempt_micros=attempt_cost_micros,
                    )
                    result = replace(
                        result,
                        cost_usd=self._cost_usd(projected.reported(
                            role,
                            package_budget=True,
                        )),
                    )
                    budget_observed_spend_micros = projected.budget_spend(role)
                    completed_terminal_verifier = (
                        role == "verifier"
                        and result.status == COMPLETED
                    )
                    effective_exhausted = bool(
                        task_budget_micros is not None
                        and (
                            budget_observed_spend_micros
                            > task_budget_micros
                            or (
                                not completed_terminal_verifier
                                and budget_observed_spend_micros
                                >= task_budget_micros
                            )
                        )
                    )
                    durable_exhausted = bool(
                        durable_budget_micros is not None
                        and (
                            projected.package_micros
                            > durable_budget_micros
                            or (
                                not completed_terminal_verifier
                                and projected.package_micros
                                >= durable_budget_micros
                            )
                        )
                    )
                    cost_budget_exhausted = (
                        durable_exhausted or effective_exhausted
                    )
                    if durable_exhausted:
                        exhausted_budget_micros = durable_budget_micros
                        exhausted_observed_micros = projected.package_micros
                        exhausted_detail_role = "builder"
                    else:
                        exhausted_budget_micros = task_budget_micros
                        exhausted_observed_micros = (
                            budget_observed_spend_micros
                        )
                        exhausted_detail_role = role
                    attempt_cost = self._cost_usd(attempt_cost_micros)
                else:
                    if (
                        result.error_class
                        == "workforce_cost_telemetry_unavailable"
                    ):
                        detail = (
                            result.error_detail
                            or "attempt cost telemetry was unavailable"
                        )
                        self._record_cost_unavailable(
                            run_id=run_id,
                            mission_id=mission_id,
                            step_id=step_id,
                            role=role,
                            provider=provider,
                            model=model,
                            attempt_generation=attempt_generation,
                            attempt_ordinal=total_attempts,
                            reason=detail,
                            lineage=lineage,
                        )
                        result = replace(
                            result,
                            status=BLOCKED,
                            verdict="BLOCKED",
                            cost_usd=self._cost_usd(costs.run_micros),
                        )
                        self._fail_run(
                            run_id,
                            "workforce_cost_telemetry_unavailable",
                            detail,
                            suffix=f"{provider}:{total_attempts}",
                        )
                        return result
                    attempt_cost = (
                        self._cost_usd(attempt_cost_micros)
                        if attempt_cost_micros is not None
                        else 0.0
                    )
                    self._record_attempt_cost(
                        run_id=run_id,
                        mission_id=mission_id,
                        step_id=step_id,
                        role=role,
                        provider=provider,
                        model=model,
                        attempt_generation=attempt_generation,
                        attempt_ordinal=total_attempts,
                        cost_usd=attempt_cost,
                        record_zero=False,
                        lineage=lineage,
                    )
                    costs = self._observed_cost_totals(
                        run_id,
                        package_id,
                        package_hash,
                        attempt_id,
                        role,
                    )
                    result = replace(
                        result,
                        cost_usd=self._cost_usd(costs.run_micros),
                    )
                    cost_budget_exhausted = False
                if provider_identity_rejected:
                    if workforce_cost_telemetry_required:
                        return settle_attempt(
                            result,
                            cost_state="OBSERVED",
                            observed_cost_micros=attempt_cost_micros,
                            outcome="PROVIDER_IDENTITY_MISMATCH",
                            failure_class="INCOMPATIBLE",
                            possible_effect=bool(result.effect_action_key),
                            totals=settlement_totals,
                            extra_events=identity_events,
                        )
                    self._fail_run(
                        run_id,
                        "provider_incompatible",
                        result.error_detail,
                        suffix=f"identity:{provider}:{total_attempts}",
                    )
                    self.database.append_events(list(identity_events))
                    return result
                if result_identity_rejected:
                    if workforce_cost_telemetry_required:
                        return settle_attempt(
                            result,
                            cost_state="OBSERVED",
                            observed_cost_micros=attempt_cost_micros,
                            outcome="RESULT_IDENTITY_MISMATCH",
                            failure_class="INCOMPATIBLE",
                            possible_effect=True,
                            totals=settlement_totals,
                            extra_events=identity_events,
                        )
                    self._fail_run(
                        run_id,
                        "provider_effect_reconciliation_required",
                        result.error_detail,
                        suffix=f"result-identity:{provider}:{total_attempts}",
                    )
                    self.database.append_events(list(identity_events))
                    return result
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
                    result = replace(
                        result,
                        status=BLOCKED,
                        verdict="BLOCKED",
                        error_class="provider_effect_identity_mismatch",
                        error_detail=detail,
                        effect_action_key=effect_action_key,
                    )
                    effect_events = (
                        self._event(
                            "provider.effect_identity_mismatch",
                            {
                                "run_id": run_id,
                                "mission_id": mission_id,
                                "step_id": step_id,
                                "provider": provider,
                                "expected_effect_action_key": effect_action_key,
                                "observed_effect_action_key": (
                                    observed_action_key
                                ),
                                "attempt_generation": attempt_generation,
                                "attempt_ordinal": total_attempts,
                                **lineage,
                            },
                            idempotency_key=(
                                "provider.effect_identity_mismatch:"
                                f"{run_id}:{attempt_generation}:"
                                f"{total_attempts}:{observed_action_key}"
                            ),
                        ),
                        self._effect_reconciliation_requirement(
                            run_id=run_id,
                            mission_id=mission_id,
                            step_id=step_id,
                            provider=provider,
                            model=model,
                            failure_class="INCOMPATIBLE",
                            reason=detail,
                            effect_action_key=effect_action_key,
                            attempt_generation=attempt_generation,
                            attempt_ordinal=total_attempts,
                            lineage=lineage,
                            observed_effect_action_key=observed_action_key,
                        ),
                    )
                    if workforce_cost_telemetry_required:
                        return settle_attempt(
                            result,
                            cost_state="OBSERVED",
                            observed_cost_micros=attempt_cost_micros,
                            outcome="EFFECT_IDENTITY_MISMATCH",
                            failure_class="INCOMPATIBLE",
                            possible_effect=True,
                            totals=settlement_totals,
                            extra_events=(*identity_events, *effect_events),
                        )
                    self._fail_run(
                        run_id,
                        "provider_incompatible",
                        detail,
                        suffix=f"effect-identity:{provider}:{total_attempts}",
                    )
                    self.database.append_events(list(effect_events))
                    return result
                if effect_action_key and not result.effect_action_key:
                    result = replace(result, effect_action_key=effect_action_key)
                if result.status == COMPLETED:
                    if cost_budget_exhausted:
                        exhausted = self._cost_budget_exhausted_result(
                            result=result,
                            role=role,
                            totals=projected,
                            budget_micros=exhausted_budget_micros,
                            observed_cost_micros=exhausted_observed_micros,
                            detail_role=exhausted_detail_role,
                        )
                        return settle_attempt(
                            exhausted,
                            cost_state="OBSERVED",
                            observed_cost_micros=attempt_cost_micros,
                            outcome="COST_LIMIT",
                            failure_class="COST_LIMIT",
                            totals=projected.settlement_fields(
                                attempt_micros=attempt_cost_micros,
                                budget_micros=exhausted_budget_micros,
                            ),
                            extra_events=identity_events,
                        )
                    if workforce_cost_telemetry_required:
                        result = settle_attempt(
                            result,
                            cost_state="OBSERVED",
                            observed_cost_micros=attempt_cost_micros,
                            outcome="COMPLETED",
                            provider_event_type="provider.dispatch_succeeded",
                            totals=settlement_totals,
                            provider_details={
                                "fallback_used": (
                                    len(tried) > 1
                                    or bool(
                                        decision and decision.fallback_used
                                    )
                                ),
                                "cumulative_cost_micros": projected.run_micros,
                                "cumulative_cost_usd": self._cost_usd(
                                    projected.run_micros
                                ),
                                "package_cumulative_cost_micros": (
                                    projected.package_micros
                                ),
                                "package_cumulative_cost_usd": self._cost_usd(
                                    projected.package_micros
                                ),
                                "attempt_role_cumulative_cost_micros": (
                                    projected.attempt_role_micros
                                ),
                                "attempt_role_cumulative_cost_usd": (
                                    self._cost_usd(
                                        projected.attempt_role_micros
                                    )
                                ),
                                "cost_usd": result.cost_usd,
                            },
                            extra_events=identity_events,
                        )
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
                        "attempt_cost_usd": attempt_cost,
                        "cumulative_cost_usd": self._cost_usd(costs.run_micros),
                        "package_cumulative_cost_usd": self._cost_usd(
                            costs.package_micros
                        ),
                        "attempt_role_cumulative_cost_usd": (
                            self._cost_usd(costs.attempt_role_micros)
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
                if cost_budget_exhausted:
                    exhausted = self._cost_budget_exhausted_result(
                        result=result,
                        role=role,
                        totals=projected,
                        budget_micros=exhausted_budget_micros,
                        observed_cost_micros=exhausted_observed_micros,
                        detail_role=exhausted_detail_role,
                    )
                    return settle_attempt(
                        exhausted,
                        cost_state="OBSERVED",
                        observed_cost_micros=attempt_cost_micros,
                        outcome="COST_LIMIT",
                        failure_class="COST_LIMIT",
                        totals=projected.settlement_fields(
                            attempt_micros=attempt_cost_micros,
                            budget_micros=exhausted_budget_micros,
                        ),
                    )
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
                    if workforce_cost_telemetry_required:
                        settle_attempt(
                            result,
                            cost_state="OBSERVED",
                            observed_cost_micros=attempt_cost_micros,
                            outcome=result_failure,
                            terminal_run=False,
                            failure_class=result_failure,
                            totals=settlement_totals,
                        )
                        costs = projected
                    else:
                        self._fail_run(
                            run_id,
                            result.error_class or "provider_unavailable",
                            result.error_detail,
                            suffix=f"{provider}:{total_attempts}",
                        )
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
                            "attempt_generation": attempt_generation,
                            "attempt_ordinal": total_attempts,
                            **lineage,
                        }, idempotency_key=(
                            f"provider.dispatch_failed:{run_id}:"
                            f"{attempt_generation}:{total_attempts}:"
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
                if workforce_cost_telemetry_required:
                    return settle_attempt(
                        result,
                        cost_state="OBSERVED",
                        observed_cost_micros=attempt_cost_micros,
                        outcome=result.error_class or "SESSION_FAILED",
                        failure_class=result.error_class or "session_failed",
                        totals=settlement_totals,
                    )
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
            rejected_waits = tuple(
                str(item.get("provider") or "")
                for item in (decision.rejected if decision is not None else ())
                if str(item.get("failure_class") or "")
                in _TRANSIENT_PROVIDER_FAILURES
                and str(item.get("provider") or "")
            )
            last_candidate = chain_candidates[-1]
            terminal_provider = tried[-1] if tried else last_candidate.provider
            terminal_model = next(
                (
                    item.model
                    for item in reversed(chain_candidates)
                    if item.provider == terminal_provider
                ),
                last_candidate.model,
            )
            result = RoleResult(
                run_id=run_id,
                role=role,
                status=BLOCKED,
                provider=terminal_provider,
                model=terminal_model,
                verdict="BLOCKED",
                error_class=error_class,
                error_detail=detail,
                cost_usd=self._cost_usd(
                    costs.package_micros
                    if (
                        workforce_cost_telemetry_required
                        and package_budget_estimate_blocked
                        and not tried
                    )
                    else costs.reported(
                        role,
                        package_budget=workforce_cost_telemetry_required,
                    )
                ),
                wait_for_providers=tuple(dict.fromkeys(
                    (*rejected_waits, *wait_eligible)
                )),
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )
            if workforce_cost_telemetry_required:
                return self._settle_budgeted_attempt(
                    manifest=make_manifest(terminal_provider, terminal_model),
                    result=result,
                    attempt_generation=attempt_generation,
                    attempt_ordinal=total_attempts + 1,
                    attempt_started=False,
                    estimate_micros=None,
                    cost_state="NOT_INCURRED",
                    observed_cost_micros=None,
                    outcome="PROVIDERS_EXHAUSTED",
                    terminal_run=True,
                    provider_event_type="provider.dispatch_skipped",
                    failure_class=terminal_class,
                    lineage=lineage,
                    totals=costs.settlement_fields(),
                    provider_details={"reason": detail},
                )
            self._fail_run(run_id, error_class, detail, suffix="exhausted")
            return result
        finally:
            self.leases.release(lease_key, run_id)
