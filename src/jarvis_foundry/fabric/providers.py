"""Capability-aware provider routing.

D-031 still forbids silent model substitution. If the configured provider
cannot run unattended, this module marks INTERACTIVE_REQUIRED and selects
a non-LLM capability when the task can proceed without a model. It never
fabricates a READY provider.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..runtime.provider_config import ProviderSettings, default_provider_settings
from ..runtime.provider_registry import get_service
from .model import ProviderRoute, UnattendedMode


CONTRIBUTOR_MODELS = frozenset({"muse-spark-1.3-contributor"})


def unattended_mode_for(
    settings: ProviderSettings,
    *,
    hermes_noninteractive_consent: bool | None = None,
) -> UnattendedMode:
    """Honest OpenCode/Hermes noninteractive truth.

    Contributor-tier OpenCode models fail closed in Hermes unless the owner
    recorded ``allow_data_training_tiers_noninteractive``. Absent consent is
    INTERACTIVE_REQUIRED, not READY. Unknown consent stays UNKNOWN.
    """
    service = get_service(settings.provider)
    if service is None:
        return UnattendedMode.UNKNOWN
    if settings.model in CONTRIBUTOR_MODELS:
        if hermes_noninteractive_consent is True:
            return UnattendedMode.ALLOWED
        if hermes_noninteractive_consent is False:
            return UnattendedMode.INTERACTIVE_REQUIRED
        return UnattendedMode.INTERACTIVE_REQUIRED
    if hermes_noninteractive_consent is False:
        return UnattendedMode.INTERACTIVE_REQUIRED
    if hermes_noninteractive_consent is True:
        return UnattendedMode.ALLOWED
    return UnattendedMode.UNKNOWN


def declared_capabilities(provider: str) -> tuple[str, ...]:
    service = get_service(provider)
    if service is None:
        return ()
    return tuple(service.capabilities)


def route_provider(
    *,
    required_capabilities: tuple[str, ...] = (),
    unattended: bool = True,
    settings: ProviderSettings | None = None,
    hermes_noninteractive_consent: bool | None = None,
    runtime_config: Mapping[str, Any] | None = None,
    observations: Iterable[Any] | None = None,
) -> ProviderRoute:
    """Select the configured provider only when it can satisfy the work.

    No alternate LLM is chosen. If the provider is interactive-only and
    the work is unattended, selection is refused so a local tool can run
    instead. Optional ``observations`` are planned through
    ``route_provider_candidates`` so UNKNOWN health/auth cannot be treated
    as READY.
    """
    authority = settings or default_provider_settings()
    if runtime_config is not None and hermes_noninteractive_consent is None:
        hermes = runtime_config.get("hermes") if isinstance(runtime_config, Mapping) else None
        if isinstance(hermes, Mapping):
            hermes_noninteractive_consent = bool(
                hermes.get("allow_data_training_tiers_noninteractive")
            )
    mode = unattended_mode_for(
        authority, hermes_noninteractive_consent=hermes_noninteractive_consent
    )
    known = declared_capabilities(authority.provider)
    missing = tuple(item for item in required_capabilities if item and item not in known)
    if known and missing:
        return ProviderRoute(
            provider=authority.provider,
            model=authority.model,
            unattended=mode,
            selected=False,
            reason=(
                f"provider {authority.provider} does not declare "
                f"{', '.join(missing)}; UNKNOWN stays UNKNOWN"
            ),
            capabilities=known,
        )
    if unattended and mode is UnattendedMode.INTERACTIVE_REQUIRED:
        return ProviderRoute(
            provider=authority.provider,
            model=authority.model,
            unattended=mode,
            selected=False,
            reason=(
                f"{authority.provider}/{authority.model} cannot run unattended "
                "without noninteractive consent; autonomous work must use a "
                "local capability"
            ),
            capabilities=known,
        )
    if unattended and mode is UnattendedMode.UNKNOWN:
        return ProviderRoute(
            provider=authority.provider,
            model=authority.model,
            unattended=mode,
            selected=False,
            reason=(
                f"{authority.provider}/{authority.model} unattended ability is "
                "UNKNOWN; refusing silent use"
            ),
            capabilities=known,
        )
    if observations is not None:
        from ..roles.router import RoutingRequest, route_provider_candidates
        decision = route_provider_candidates(
            RoutingRequest(
                task_type="llm.dispatch",
                required_capabilities=required_capabilities,
                preferred_provider=authority.provider,
                strict_identity=(authority.provider, authority.model),
            ),
            observations,
        )
        selected = decision.status in {"SELECTED", "FALLBACK"} and decision.selected is not None
        return ProviderRoute(
            provider=authority.provider,
            model=authority.model,
            unattended=mode,
            selected=selected,
            reason=decision.reason,
            capabilities=known,
        )
    if not authority.api_key and authority.source == "default":
        return ProviderRoute(
            provider=authority.provider,
            model=authority.model,
            unattended=mode,
            selected=False,
            reason="provider credential is not configured",
            capabilities=known,
        )
    return ProviderRoute(
        provider=authority.provider,
        model=authority.model,
        unattended=mode,
        selected=True,
        reason="configured provider satisfies the task",
        capabilities=known,
    )
