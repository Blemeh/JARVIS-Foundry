"""Provider metadata registry shared by runtime resolution and ``jarvis setup``.

No secrets are stored here — only display names, provider ids, default URLs,
and Hermes environment variable names.
"""
from __future__ import annotations

from dataclasses import dataclass

OPENCODE_FAMILY_ID = "opencode"


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
        default_model="muse-spark-1.2-contributor",
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
