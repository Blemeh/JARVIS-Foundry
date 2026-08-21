"""Tests for syncing JARVIS provider settings into Hermes home .env."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.cli.hermes_env_sync import (
    provider_hermes_env_updates,
    sync_provider_to_hermes_env,
)
from jarvis_foundry.runtime.provider_config import ProviderCredentialError, ProviderSettings

SECRET = "sk-test-opencode-secret-key-1234567890"


def _settings(**kwargs: object) -> ProviderSettings:
    return ProviderSettings(
        provider=str(kwargs.get("provider", "opencode_zen")),
        model=str(kwargs.get("model", "x-preview-f-free")),
        base_url=str(kwargs.get("base_url", "https://opencode.ai/zen/v1")),
        api_key=kwargs.get("api_key"),  # type: ignore[arg-type]
        source=str(kwargs.get("source", "test")),
    )


def test_provider_hermes_env_updates_syncs_shared_opencode_key_for_zen():
    settings = _settings(api_key=SECRET)
    updates = provider_hermes_env_updates(settings)
    assert updates["OPENCODE_ZEN_API_KEY"] == SECRET
    assert updates["OPENCODE_GO_API_KEY"] == SECRET
    assert updates["OPENCODE_ZEN_BASE_URL"] == "https://opencode.ai/zen/v1"
    assert updates["OPENCODE_GO_BASE_URL"] == "https://opencode.ai/zen/go/v1"


def test_provider_hermes_env_updates_syncs_shared_opencode_key_for_go():
    settings = _settings(
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key=SECRET,
    )
    updates = provider_hermes_env_updates(settings)
    assert updates["OPENCODE_ZEN_API_KEY"] == SECRET
    assert updates["OPENCODE_GO_API_KEY"] == SECRET
    assert updates["OPENCODE_GO_BASE_URL"] == "https://opencode.ai/zen/go/v1"
    assert updates["OPENCODE_ZEN_BASE_URL"] == "https://opencode.ai/zen/v1"


def test_sync_provider_to_hermes_env_aligns_both_keys(tmp_path: Path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    env_path = hermes_home / ".env"
    env_path.write_text(
        "\n".join([
            "OPENCODE_ZEN_API_KEY=sk-old-zen-key-123456789012345",
            "OPENCODE_GO_API_KEY=sk-old-go-key-1234567890123456",
            "WHATSAPP_ENABLED=true",
        ]) + "\n",
        encoding="utf-8",
    )
    sync_provider_to_hermes_env(_settings(api_key=SECRET), hermes_home=hermes_home)
    text = env_path.read_text(encoding="utf-8")
    assert f"OPENCODE_ZEN_API_KEY={SECRET}" in text
    assert f"OPENCODE_GO_API_KEY={SECRET}" in text
    assert "WHATSAPP_ENABLED=true" in text
    assert "sk-old-zen-key" not in text
    assert "sk-old-go-key" not in text


def test_sync_provider_to_hermes_env_requires_api_key(tmp_path: Path):
    with pytest.raises(ProviderCredentialError):
        sync_provider_to_hermes_env(
            _settings(api_key=None),
            hermes_home=tmp_path / "hermes",
        )
