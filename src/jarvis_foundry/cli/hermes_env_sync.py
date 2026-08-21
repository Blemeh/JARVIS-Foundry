"""Sync JARVIS provider settings into the Hermes home ``.env`` file."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..runtime.provider_config import ProviderCredentialError, ProviderSettings
from ..runtime.provider_registry import (
    is_opencode_family,
    require_service,
)

_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


@dataclass(frozen=True, slots=True)
class HermesEnvSyncResult:
    hermes_home: Path
    env_path: Path
    updated_keys: tuple[str, ...]


def resolve_hermes_home() -> Path:
    """Return the live Hermes home directory."""
    env = os.environ.get("HERMES_HOME")
    if env and str(env).strip():
        return Path(env).expanduser().resolve()
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (local / "hermes").resolve()


def provider_hermes_env_updates(settings: ProviderSettings) -> dict[str, str]:
    """Map resolved provider settings to Hermes ``.env`` keys.

    OpenCode Go and Zen share one credential; when either is active both
    Hermes env vars are updated so home and profile routes stay aligned.
    """
    service = require_service(settings.provider)
    updates = {
        service.hermes_api_key_env: settings.require_api_key(),
        service.hermes_base_url_env: settings.base_url,
    }
    if is_opencode_family(settings.provider):
        api_key = settings.require_api_key()
        go = require_service("opencode_go")
        zen = require_service("opencode_zen")
        updates[go.hermes_api_key_env] = api_key
        updates[zen.hermes_api_key_env] = api_key
        updates[go.hermes_base_url_env] = go.default_base_url
        updates[zen.hermes_base_url_env] = zen.default_base_url
        if settings.provider == go.provider_id:
            updates[go.hermes_base_url_env] = settings.base_url
        else:
            updates[zen.hermes_base_url_env] = settings.base_url
    return updates


def _upsert_env_lines(lines: list[str], updates: dict[str, str]) -> tuple[list[str], list[str]]:
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        match = _ENV_LINE.match(line)
        if match is None:
            output.append(line)
            continue
        key = match.group(1)
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    appended: list[str] = []
    for key, value in remaining.items():
        output.append(f"{key}={value}")
        appended.append(key)
    updated = [key for key in updates if key not in appended]
    return output, updated + appended


def sync_provider_to_hermes_env(
    settings: ProviderSettings,
    *,
    hermes_home: Path | None = None,
) -> HermesEnvSyncResult:
    """Write provider credentials into ``<HERMES_HOME>/.env``."""
    home = (hermes_home or resolve_hermes_home()).resolve()
    env_path = home / ".env"
    updates = provider_hermes_env_updates(settings)
    if env_path.is_file():
        text = env_path.read_text(encoding="utf-8", errors="replace")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        new_lines, updated_keys = _upsert_env_lines(lines, updates)
        content = newline.join(new_lines)
        if text.endswith(("\n", "\r\n")):
            content += newline
    else:
        home.mkdir(parents=True, exist_ok=True)
        updated_keys = list(updates)
        content = "\n".join(f"{key}={value}" for key, value in updates.items()) + "\n"
    env_path.write_text(content, encoding="utf-8")
    return HermesEnvSyncResult(
        hermes_home=home,
        env_path=env_path,
        updated_keys=tuple(updated_keys),
    )


def sync_provider_to_hermes_env_safe(
    settings: ProviderSettings,
    *,
    hermes_home: Path | None = None,
) -> HermesEnvSyncResult | None:
    """Sync when credentials exist; return ``None`` when api_key is absent."""
    try:
        return sync_provider_to_hermes_env(settings, hermes_home=hermes_home)
    except ProviderCredentialError:
        return None
