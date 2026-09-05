"""Shared construction for one canonical MissionLoop worker session."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Type

from ..control.policy import ActionPolicy
from ..database import FoundryDatabase
from ..mission.loop import MissionLoop
from ..paths import FoundryPaths
from ..roles.router import RoleRouter
from ..supervisor.leases import LeaseManager
from .config import WorkerRuntimeConfig


def build_runtime_role_router(
    config: WorkerRuntimeConfig,
) -> RoleRouter:
    """Build the existing provider router from canonical runtime policy."""

    router = RoleRouter(provider_settings=config.provider_settings)
    pool_providers = tuple(
        item.provider for item in config.provider_settings_pool
    )
    fallback_providers = tuple(
        provider
        for provider in pool_providers
        if provider != config.provider_settings.provider
    )
    if config.provider_resilience_enabled and fallback_providers:
        router = router.clone_with({
            role: {"fallbacks": fallback_providers}
            for role in (
                "executive",
                "builder",
                "verifier",
                "critic",
                "repair",
                "researcher",
                "premium_reviewer",
            )
        })
    router.enforce_directive_routing(
        allow_safe_fallbacks=config.provider_resilience_enabled,
        allowed_providers=pool_providers,
    )
    return router


def build_canonical_mission_loop(
    database: FoundryDatabase,
    paths: FoundryPaths,
    config: WorkerRuntimeConfig,
    *,
    stack: Any,
    leases: LeaseManager,
    source_id: str,
    fault_hook: Callable[[str], None] | None = None,
    mission_loop_cls: Type[MissionLoop] = MissionLoop,
) -> MissionLoop:
    """Construct the one MissionLoop with canonical provider policy."""

    resilience = dict(config.provider_resilience or {})
    retry = dict(resilience.get("retry") or {})
    policy = dict(resilience.get("policy") or {})
    stop_path = config.resolve_stop_path(paths.home)
    return mission_loop_cls(
        database,
        router=build_runtime_role_router(config),
        adapter=stack.bridge,
        leases=leases,
        max_repairs=config.max_repairs,
        source_id=source_id,
        stop_path=stop_path,
        policy=ActionPolicy(database, Path(stop_path)),
        fault_hook=fault_hook,
        provider_candidates=tuple(
            getattr(stack, "provider_candidates", ()) or ()
        ),
        max_provider_attempts=int(
            retry.get("max_total_attempts", 4)
        ),
        max_provider_retries=int(
            retry.get("max_retries_per_provider", 0)
        ),
        allowed_data_use=tuple(
            str(item) for item in policy.get("allowed_data_use") or ()
        ),
        allowed_providers=tuple(
            str(item)
            for item in resilience.get("allowed_providers") or ()
        ),
        denied_providers=tuple(
            str(item)
            for item in resilience.get("denied_providers") or ()
        ),
        provider_budget_usd=float(
            resilience.get("max_cost_usd") or 0.0
        ),
        provider_routing_constraints=dict(
            resilience.get("constraints") or {}
        ),
    )


__all__ = [
    "build_canonical_mission_loop",
    "build_runtime_role_router",
]
