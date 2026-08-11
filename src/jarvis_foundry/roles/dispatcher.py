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

import json
from pathlib import Path
from typing import Callable

from ..clock import isoformat, utc_now
from ..database import FoundryDatabase
from ..events import Event
from ..supervisor.leases import LeaseManager
from .adapter import ProviderUnavailable, RoleAdapter
from .manifest import ContextManifest
from .results import BLOCKED, COMPLETED, FAILED, RoleResult
from .router import RoleRouter


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
    ) -> None:
        self.database = database
        self.router = router
        self.adapter = adapter
        self.leases = leases or LeaseManager(database)
        self.source_type = source_type
        self.source_id = source_id
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._heartbeat_count: dict[str, int] = {}

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
    ) -> ContextManifest:
        return ContextManifest(
            run_id=run_id, role=role, mission_id=mission_id,
            step_id=step_id, resource_id=resource_id, capability=capability,
            provider=provider, model=config.preferred_model,
            isolation_group=config.independence_group,
            tool_policy=config.required_capabilities,
            budget_usd=budget_usd, duration_seconds=duration_seconds,
            instructions=instructions,
        )

    def _replay_completed(self, run_id: str, prospective: ContextManifest) -> RoleResult | None:
        """Replay a completed run from the ledger when the caller retries the
        exact same context (crashed-caller recovery). Returns None when there
        is nothing to replay (unknown, running, failed, or changed context)."""
        row = self.database.row("agent_runs", "run_id", run_id)
        if row is None or row["status"] != "COMPLETED":
            return None
        if row["context_manifest_hash"] != prospective.content_hash():
            return None  # changed context under the same run_id: not a replay
        try:
            return RoleResult.from_payload(json.loads(str(row["result_json"])))
        except (KeyError, TypeError, ValueError):
            return None

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
    ) -> RoleResult:
        import uuid
        run_id = run_id or f"run-{uuid.uuid4()}"
        lease_key = f"role:{role}:{mission_id}:{step_id or 'start'}"

        # 1. provider chain (override only for tests/staging)
        chain = (provider_override,) if provider_override else self.router.provider_chain(role)
        if not chain:
            self._fail_run(run_id, "provider_unavailable", f"no providers configured for role {role}")
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider="", model="",
                verdict="BLOCKED", error_class="provider_unavailable",
                error_detail=f"no providers configured for role {role}",
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )

        config = self.router.config(role)

        # 1b. crashed-caller recovery: identical context + completed run replays
        # from the ledger (no re-run, no new events). A changed context under
        # the same run_id proceeds and fails loudly on the event idempotency
        # key (first write stands, WP3 B6 semantics).
        prospective = self._manifest(
            run_id, role, mission_id, step_id, resource_id, capability,
            instructions, duration_seconds, budget_usd, chain[0], config,
        )
        replayed = self._replay_completed(run_id, prospective)
        if replayed is not None:
            return replayed

        # 2. lease
        try:
            self.leases.acquire(lease_key, run_id, duration_seconds)
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
        try:
            for provider in chain:
                model = config.preferred_model
                manifest = self._manifest(
                    run_id, role, mission_id, step_id, resource_id, capability,
                    instructions, duration_seconds, budget_usd, provider, config,
                )
                self._start_run(manifest, provider, model)
                tried.append(provider)
                try:
                    result = self.adapter.dispatch(
                        manifest,
                        heartbeat=lambda rid=run_id: self._heartbeat(rid),
                    )
                except ProviderUnavailable as error:
                    last_error = error
                    self._fail_run(run_id, "provider_unavailable", str(error), suffix=provider)
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
                else:
                    self._fail_run(run_id, result.error_class or "session_failed",
                                   result.error_detail, suffix=provider)
                return result

            # 4. all providers exhausted
            detail = f"all providers failed for role {role}: tried={tried}; last={last_error}"
            self._fail_run(run_id, "provider_unavailable", detail, suffix="exhausted")
            return RoleResult(
                run_id=run_id, role=role, status=BLOCKED, provider=tried[-1] if tried else "",
                model=config.preferred_model, verdict="BLOCKED",
                error_class="provider_unavailable", error_detail=detail,
                started_at=isoformat(utc_now()), finished_at=isoformat(utc_now()),
            )
        finally:
            self.leases.release(lease_key, run_id)
