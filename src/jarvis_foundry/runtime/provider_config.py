"""Machine-local LLM provider configuration (single owner-editable source).

Default path: ``%LOCALAPPDATA%/JARVIS/config/provider.yaml``

Resolved once at the runtime boundary (``WorkerRuntimeConfig.from_yaml`` /
``load_provider_settings``) and propagated downward. Lower layers must not
re-read the file or mutate process environment.

Precedence (highest first):

1. authorized runtime credential/base-url overrides (``JARVIS_RUNTIME_API_KEY``,
   ``JARVIS_RUNTIME_BASE_URL``) and call-site test overrides
2. machine-local ``provider.yaml`` for provider/model/base_url/api_key
3. legacy environment variables (compatibility only)
4. deterministic safe defaults (credential absent -> fail closed at dispatch)

``JARVIS_RUNTIME_PROVIDER`` / ``JARVIS_RUNTIME_MODEL`` are rejected — they
cannot redefine the trusted policy they would be checked against.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..deployment.layout import install_root

DEFAULT_PROVIDER = "opencode_go"
DEFAULT_MODEL = "muse-spark-1.2-contributor"
DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"

# Persisted missions recorded under the prior owner mandate remain replayable.
HISTORICAL_MODELS = frozenset({"deepseek-v4-flash"})

_RUNTIME_CREDENTIAL_ENV = {
    "api_key": "JARVIS_RUNTIME_API_KEY",
    "base_url": "JARVIS_RUNTIME_BASE_URL",
}
_FORBIDDEN_RUNTIME_POLICY_ENV = {
    "provider": "JARVIS_RUNTIME_PROVIDER",
    "model": "JARVIS_RUNTIME_MODEL",
}

_LEGACY_ENV = {
    "api_key": (
        "JARVIS_OPENCODE_API_KEY",
        "OPENCODE_GO_API_KEY",
        "OPENCODE_API_KEY",
    ),
    "base_url": (
        "JARVIS_OPENCODE_BASE_URL",
        "OPENCODE_BASE_URL",
        "OPENCODE_GO_BASE_URL",
    ),
}

_REDACTED = "<REDACTED>"
_SECRET_TOKEN = re.compile(r"sk-[A-Za-z0-9]{16,}")


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    provider: str
    model: str
    base_url: str
    api_key: str | None
    source: str

    def redacted(self) -> dict[str, str]:
        """Safe representation for logs, errors, and status output."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": _REDACTED if self.api_key else "",
            "source": self.source,
        }

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ProviderCredentialError(
                "OpenCode API key is not configured; set api_key in "
                f"{default_provider_config_path()} or supply an authorized "
                "runtime override")
        return self.api_key

    def trusted_models(self) -> frozenset[str]:
        """Models accepted for new dispatch and identity verification."""
        return frozenset({self.model, *HISTORICAL_MODELS})

    def is_trusted_identity(self, identity: dict[str, Any]) -> bool:
        if not isinstance(identity, dict):
            return False
        session_id = identity.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return False
        if identity.get("provider") != self.provider:
            return False
        model = identity.get("model")
        return isinstance(model, str) and model in self.trusted_models()


class ProviderCredentialError(RuntimeError):
    """Required provider credential is missing."""


class ProviderConfigError(RuntimeError):
    """Machine-local provider configuration is invalid."""


def default_provider_config_path(root: Path | None = None) -> Path:
    return install_root(root) / "config" / "provider.yaml"


def default_provider_settings() -> ProviderSettings:
    """Deterministic defaults without reading disk (disabled/test paths)."""
    return ProviderSettings(
        provider=DEFAULT_PROVIDER,
        model=DEFAULT_MODEL,
        base_url=DEFAULT_BASE_URL,
        api_key=None,
        source="default",
    )


def _sanitize_error_text(text: str) -> str:
    if not text:
        return text
    return _SECRET_TOKEN.sub(_REDACTED, text)


def _reject_unauthorized_runtime_policy_overrides() -> None:
    for field, env_name in _FORBIDDEN_RUNTIME_POLICY_ENV.items():
        value = os.environ.get(env_name)
        if value is not None and str(value).strip():
            raise ProviderConfigError(
                f"unauthorized runtime override {env_name} is not permitted; "
                f"change {field} in {default_provider_config_path()}")


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _runtime_override(field: str) -> str | None:
    env_name = _RUNTIME_CREDENTIAL_ENV.get(field)
    if env_name is None:
        return None
    return _first_env((env_name,))


def _legacy_value(field: str) -> str | None:
    names = _LEGACY_ENV.get(field)
    if not names:
        return None
    return _first_env(names)


def _load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError as error:  # pragma: no cover
        raise ProviderConfigError("pyyaml is required to load provider config") from error
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as error:
        raise ProviderConfigError(
            f"could not read provider config at {path}") from error
    except Exception as error:
        raise ProviderConfigError(
            "provider config is malformed; check syntax and field types") from error
    if not isinstance(raw, dict):
        raise ProviderConfigError("provider config must be a mapping")
    return raw


def _pick_trusted(
    field: str,
    *,
    yaml_cfg: dict[str, Any],
    default: str | None = None,
) -> tuple[str | None, str | None]:
    yaml_value = yaml_cfg.get(field)
    if yaml_value is not None and str(yaml_value).strip():
        return str(yaml_value).strip(), "provider_yaml"
    legacy = _legacy_value(field)
    if legacy:
        return legacy, "legacy_env"
    if default is not None:
        return default, "default"
    return None, None


def load_provider_settings(
    *,
    config_path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> ProviderSettings:
    """Resolve provider settings once. Call at the runtime boundary only."""
    _reject_unauthorized_runtime_policy_overrides()
    path = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else default_provider_config_path()
    )
    yaml_cfg = _load_yaml_config(path)

    provider, provider_source = _pick_trusted(
        "provider", yaml_cfg=yaml_cfg, default=DEFAULT_PROVIDER)
    model, model_source = _pick_trusted(
        "model", yaml_cfg=yaml_cfg, default=DEFAULT_MODEL)
    base_url, base_source = _pick_trusted(
        "base_url", yaml_cfg=yaml_cfg, default=DEFAULT_BASE_URL)

    api_key: str | None = None
    api_source: str | None = None
    if overrides and overrides.get("api_key") is not None:
        candidate = str(overrides["api_key"]).strip()
        if candidate:
            api_key, api_source = candidate, "override"
    if api_key is None:
        runtime_key = _runtime_override("api_key")
        if runtime_key:
            api_key, api_source = runtime_key, "runtime_env"
    if api_key is None:
        yaml_key = yaml_cfg.get("api_key")
        if yaml_key is not None and str(yaml_key).strip():
            api_key, api_source = str(yaml_key).strip(), "provider_yaml"
    if api_key is None:
        legacy_key = _legacy_value("api_key")
        if legacy_key:
            api_key, api_source = legacy_key, "legacy_env"

    if overrides and overrides.get("base_url") is not None:
        candidate = str(overrides["base_url"]).strip()
        if candidate:
            base_url, base_source = candidate, "override"
    if base_source not in {"override", "runtime_env"}:
        runtime_base = _runtime_override("base_url")
        if runtime_base:
            base_url, base_source = runtime_base, "runtime_env"

    sources = {item for item in (provider_source, model_source, base_source, api_source) if item}
    precedence = ("override", "runtime_env", "provider_yaml", "legacy_env", "default")
    source = "default"
    for tag in precedence:
        if tag in sources:
            source = tag
            break

    return ProviderSettings(
        provider=str(provider or DEFAULT_PROVIDER),
        model=str(model or DEFAULT_MODEL),
        base_url=str(base_url or DEFAULT_BASE_URL),
        api_key=api_key,
        source=source,
    )


def hermes_provider_env(settings: ProviderSettings) -> dict[str, str]:
    """Hermes subprocess env fragment (authoritative over inherited env)."""
    return {
        "OPENCODE_GO_API_KEY": settings.require_api_key(),
        "OPENCODE_GO_BASE_URL": settings.base_url,
    }


def openhands_provider_env(settings: ProviderSettings) -> dict[str, str]:
    """OpenHands subprocess env fragment (authoritative over inherited env)."""
    return {
        "LLM_API_KEY": settings.require_api_key(),
        "LLM_BASE_URL": settings.base_url,
    }


def ensure_provider_template(path: Path | None = None) -> Path:
    """Write a non-secret template when the machine-local file is absent."""
    target = path or default_provider_config_path()
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    template = (
        "# Machine-local JARVIS provider configuration (never commit).\n"
        f"provider: {DEFAULT_PROVIDER}\n"
        f"model: {DEFAULT_MODEL}\n"
        f"base_url: {DEFAULT_BASE_URL}\n"
        'api_key: ""\n'
    )
    target.write_text(template, encoding="utf-8")
    return target
