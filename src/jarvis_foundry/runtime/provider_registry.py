"""Provider metadata registry shared by runtime resolution and ``jarvis setup``.

No secrets are stored here — only display names, provider ids, default URLs,
and Hermes environment variable names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
from typing import Any

OPENCODE_FAMILY_ID = "opencode"


class ProviderReadiness(StrEnum):
    """Runtime provider state used by routing and provider wakes.

    ``UNKNOWN`` remains fail-closed.  ``DEGRADED`` is usable only when the
    request constraints still pass; the other non-ready states are never
    silently selected.
    """

    READY = "READY"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    OUTAGE = "OUTAGE"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProviderFamily:
    family_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class ProviderService:
    provider_id: str
    family_id: str
    display_name: str
    default_base_url: str
    hermes_api_key_env: str
    hermes_base_url_env: str
    default_model: str
    free_test_model: str | None = None
    live_test_allowed_for_free_model: bool = False
    # Capability/limit declarations are intentionally optional.  Empty or
    # absent values mean UNKNOWN to the operational route planner, never
    # "supports everything".
    known_models: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    context_window_tokens: int | None = None
    tool_support: tuple[str, ...] = ()
    task_classes: tuple[str, ...] = ()
    data_use_classification: str = "UNKNOWN"
    estimated_cost_usd: float | None = None
    limits: dict[str, Any] = field(default_factory=dict)
    required_authority: str = "SUPERVISOR_CONTROLLER"


_PROVIDER_FAMILIES: dict[str, ProviderFamily] = {
    OPENCODE_FAMILY_ID: ProviderFamily(
        family_id=OPENCODE_FAMILY_ID,
        display_name="OpenCode",
    ),
}

_PROVIDER_SERVICES: dict[str, ProviderService] = {
    "opencode_go": ProviderService(
        provider_id="opencode_go",
        family_id=OPENCODE_FAMILY_ID,
        display_name="OpenCode Go",
        default_base_url="https://opencode.ai/zen/go/v1",
        hermes_api_key_env="OPENCODE_GO_API_KEY",
        hermes_base_url_env="OPENCODE_GO_BASE_URL",
        default_model="muse-spark-1.3-contributor",
        known_models=("muse-spark-1.3-contributor",),
        capabilities=(
            "structured_reasoning", "code", "tools", "tests", "research",
            "artifact_inspection", "adversarial_verification",
            "adversarial_review",
        ),
        tool_support=("hermes_oneshot",),
        task_classes=(
            "executive", "builder", "verifier", "critic", "repair",
            "researcher",
        ),
        data_use_classification="TRAINING_TIER",
        limits={"unattended": "requires allow_data_training_tiers_noninteractive"},
    ),
    "opencode_zen": ProviderService(
        provider_id="opencode_zen",
        family_id=OPENCODE_FAMILY_ID,
        display_name="OpenCode Zen",
        default_base_url="https://opencode.ai/zen/v1",
        hermes_api_key_env="OPENCODE_ZEN_API_KEY",
        hermes_base_url_env="OPENCODE_ZEN_BASE_URL",
        default_model="x-preview-f-free",
        free_test_model="x-preview-f-free",
        live_test_allowed_for_free_model=True,
        known_models=("x-preview-f-free",),
        capabilities=("structured_reasoning",),
        tool_support=("hermes_oneshot",),
        task_classes=("executive", "researcher"),
        data_use_classification="UNKNOWN",
        limits={"live_test": "free_model_only"},
    ),
}


class UnsupportedProviderError(ValueError):
    """Provider id is not registered."""


def list_families() -> tuple[ProviderFamily, ...]:
    return tuple(_PROVIDER_FAMILIES.values())


def list_services(*, family_id: str | None = None) -> tuple[ProviderService, ...]:
    services = tuple(_PROVIDER_SERVICES.values())
    if family_id is None:
        return services
    return tuple(item for item in services if item.family_id == family_id)


def get_service(provider_id: str) -> ProviderService | None:
    return _PROVIDER_SERVICES.get(provider_id)


def require_service(provider_id: str) -> ProviderService:
    service = get_service(provider_id)
    if service is None:
        raise UnsupportedProviderError(
            f"unsupported provider {provider_id!r}; "
            f"supported: {', '.join(sorted(_PROVIDER_SERVICES))}")
    return service


def get_family(family_id: str) -> ProviderFamily | None:
    return _PROVIDER_FAMILIES.get(family_id)


def family_for_provider(provider_id: str) -> ProviderFamily | None:
    service = get_service(provider_id)
    if service is None:
        return None
    return get_family(service.family_id)


def is_opencode_family(provider_id: str) -> bool:
    service = get_service(provider_id)
    return service is not None and service.family_id == OPENCODE_FAMILY_ID


def opencode_hermes_env_names() -> tuple[str, ...]:
    names: list[str] = []
    for service in _PROVIDER_SERVICES.values():
        if service.family_id == OPENCODE_FAMILY_ID:
            names.extend([service.hermes_api_key_env, service.hermes_base_url_env])
    for legacy in (
        "OPENCODE_API_KEY",
        "OPENCODE_BASE_URL",
        "JARVIS_OPENCODE_API_KEY",
        "JARVIS_OPENCODE_BASE_URL",
    ):
        names.append(legacy)
    return tuple(dict.fromkeys(names))


def live_test_allowed(provider_id: str, model: str) -> bool:
    """True only for explicitly approved free-model live Hermes probes."""
    service = get_service(provider_id)
    if service is None or not service.live_test_allowed_for_free_model:
        return False
    if service.free_test_model is None:
        return False
    return model.strip() == service.free_test_model


def provider_profile(provider_id: str, model: str | None = None) -> dict[str, Any]:
    """Return the existing registry's non-secret capability profile."""

    service = require_service(provider_id)
    selected_model = str(model or service.default_model)
    return {
        "provider_id": service.provider_id,
        "family_id": service.family_id,
        "model_id": selected_model,
        "known_model": not service.known_models or selected_model in service.known_models,
        "capabilities": list(service.capabilities),
        "task_classes": list(service.task_classes),
        "tool_support": list(service.tool_support),
        "context_window_tokens": service.context_window_tokens,
        "limits": dict(service.limits),
        "estimated_cost_usd": service.estimated_cost_usd,
        "data_use_classification": service.data_use_classification,
    }


def record_provider_profile(
    database: Any,
    *,
    provider_id: str,
    model: str,
    profile_overrides: dict[str, Any] | None = None,
    source_id: str = "provider-runtime",
) -> dict[str, Any]:
    """Append one event-derived provider profile; never stores credentials."""

    from ..events import Event, canonical_json

    payload = provider_profile(provider_id, model)
    overrides = dict(profile_overrides or {})
    for key in (
        "capabilities", "task_classes", "tool_support",
        "context_window_tokens", "limits", "estimated_cost_usd",
        "data_use_classification",
    ):
        if key in overrides:
            payload[key] = overrides[key]
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]
    database.append(Event.create(
        "provider.profile_observed",
        payload,
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=(
            f"provider.profile_observed:{provider_id}:{model}:{digest}"),
    ))
    return payload


def record_provider_readiness(
    database: Any,
    *,
    provider_id: str,
    model: str,
    readiness: ProviderReadiness | str,
    reason: str = "",
    evidence_refs: tuple[str, ...] = (),
    retry_after_seconds: float | None = None,
    source_id: str = "provider-runtime",
    notify_wakes: bool = True,
) -> dict[str, Any]:
    """Record current readiness and notify the existing wake machinery.

    State is event-derived and idempotent for the same evidence revision.  A
    positive recovery observation is what releases a provider wait; merely
    having a configured provider never does.
    """

    from ..events import Event, canonical_json

    state = ProviderReadiness(str(readiness).upper())
    payload = {
        "provider_id": str(provider_id),
        "provider": str(provider_id),
        "model_id": str(model),
        "model": str(model),
        "readiness": state.value,
        "status": state.value,
        "health": (
            "READY" if state is ProviderReadiness.READY
            else "DEGRADED" if state is ProviderReadiness.DEGRADED
            else state.value
        ),
        "auth_state": (
            "READY" if state in {ProviderReadiness.READY, ProviderReadiness.DEGRADED}
            else "UNKNOWN"
        ),
        "availability": (
            "AVAILABLE" if state in {ProviderReadiness.READY, ProviderReadiness.DEGRADED}
            else state.value
        ),
        "reason": str(reason)[:500],
        "evidence_refs": list(dict.fromkeys(str(item) for item in evidence_refs)),
        "retry_after_seconds": retry_after_seconds,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]
    previous = latest_provider_readiness(database, provider_id, model)
    if previous is not None:
        comparable = {
            key: value for key, value in previous.items()
            if key not in {"recorded_at", "event_id"}
        }
        if canonical_json(comparable) == canonical_json(payload):
            return payload
    predecessor = str((previous or {}).get("event_id") or "initial")
    database.append(Event.create(
        "provider.readiness_observed",
        payload,
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=(
            f"provider.readiness_observed:{provider_id}:{model}:"
            f"{predecessor}:{digest}"),
    ))
    if notify_wakes and state in {
        ProviderReadiness.READY, ProviderReadiness.DEGRADED,
    }:
        from ..executive.continuous import on_provider_readiness_changed

        on_provider_readiness_changed(
            database, provider_id=provider_id, model=model,
            controller="supervisor",
        )
    return payload


def latest_provider_readiness(
    database: Any,
    provider_id: str,
    model: str | None = None,
) -> dict[str, Any] | None:
    """Read the newest matching runtime observation from the ledger."""

    for event in database.events(
        readonly=True,
        newest_first=True,
        limit=1000,
        event_type_prefixes=("provider.readiness_", "provider.health_", "provider.availability_"),
    ):
        payload = event.payload
        observed_provider = str(
            payload.get("provider_id") or payload.get("provider") or "")
        observed_model = str(payload.get("model_id") or payload.get("model") or "")
        if observed_provider != provider_id:
            continue
        if model and observed_model and observed_model != model:
            continue
        return {
            **payload,
            "recorded_at": event.recorded_at,
            "event_id": event.event_id,
        }
    return None
