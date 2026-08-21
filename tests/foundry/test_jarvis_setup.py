"""Tests for ``jarvis setup`` and OpenCode provider family mapping."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from jarvis_foundry.cli import main as cli_main
from jarvis_foundry.cli.setup import (
    discover_models,
    format_setup_show,
    format_test_report,
    mark_pending_apply,
    run_interactive_setup,
    run_show,
    run_test,
    save_provider_config_atomic,
    evaluate_provider_configuration,
)
from jarvis_foundry.runtime.provider_config import (
    ProviderConfigError,
    ProviderCredentialError,
    ProviderSettings,
    hermes_provider_env,
    load_provider_settings,
)
from jarvis_foundry.runtime.provider_registry import require_service
from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
from jarvis_foundry.workers.contracts import WorkerStepContract

SECRET = "sk-test-opencode-secret-key-1234567890"
MODELS_BODY = json.dumps({"data": [{"id": "x-preview-f-free"}, {"id": "other-model"}]})
CHAT_OK = json.dumps({"choices": [{"message": {"content": "pong"}}]})


@pytest.fixture
def config_path(monkeypatch, tmp_path):
    path = tmp_path / "provider.yaml"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "JARVIS"))
    monkeypatch.setattr(
        "jarvis_foundry.runtime.provider_config.default_provider_config_path",
        lambda root=None: path,
    )
    monkeypatch.setattr(
        "jarvis_foundry.cli.setup.default_provider_config_path",
        lambda root=None: path,
    )
    for name in (
        "JARVIS_RUNTIME_PROVIDER",
        "JARVIS_RUNTIME_MODEL",
        "JARVIS_RUNTIME_API_KEY",
        "JARVIS_RUNTIME_BASE_URL",
        "JARVIS_OPENCODE_API_KEY",
        "OPENCODE_GO_API_KEY",
        "OPENCODE_ZEN_API_KEY",
        "OPENCODE_API_KEY",
        "JARVIS_OPENCODE_BASE_URL",
        "OPENCODE_BASE_URL",
        "OPENCODE_GO_BASE_URL",
        "OPENCODE_ZEN_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    return path


def _write_provider(path: Path, **fields: str) -> None:
    lines = [f"{key}: {value}" for key, value in fields.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _settings(**kwargs: object) -> ProviderSettings:
    return ProviderSettings(
        provider=str(kwargs.get("provider", "opencode_go")),
        model=str(kwargs.get("model", "muse-spark-1.2-contributor")),
        base_url=str(kwargs.get("base_url", "https://opencode.ai/zen/go/v1")),
        api_key=kwargs.get("api_key"),  # type: ignore[arg-type]
        source=str(kwargs.get("source", "test")),
    )


def _fake_fetch(url: str, *, api_key: str, method: str = "GET", payload=None):
    if url.endswith("/models"):
        return 200, MODELS_BODY
    if url.endswith("/chat/completions") and method == "POST":
        return 200, CHAT_OK
    return 500, "error"


def test_opencode_go_env_mapping():
    settings = _settings(
        provider="opencode_go",
        api_key=SECRET,
        base_url="https://opencode.ai/zen/go/v1",
    )
    env = hermes_provider_env(settings)
    assert env == {
        "OPENCODE_GO_API_KEY": SECRET,
        "OPENCODE_GO_BASE_URL": "https://opencode.ai/zen/go/v1",
    }


def test_opencode_zen_env_mapping():
    settings = _settings(
        provider="opencode_zen",
        model="x-preview-f-free",
        api_key=SECRET,
        base_url="https://opencode.ai/zen/v1",
    )
    env = hermes_provider_env(settings)
    assert env == {
        "OPENCODE_ZEN_API_KEY": SECRET,
        "OPENCODE_ZEN_BASE_URL": "https://opencode.ai/zen/v1",
    }
    assert "OPENCODE_GO_API_KEY" not in env
    assert "OPENCODE_GO_BASE_URL" not in env


def test_shared_opencode_credential_reused_go_to_zen(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key=SECRET,
    )
    settings = load_provider_settings(config_path=config_path)
    assert settings.api_key == SECRET
    zen = ProviderSettings(
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=settings.api_key,
        source="setup",
    )
    env = hermes_provider_env(zen)
    assert env["OPENCODE_ZEN_API_KEY"] == SECRET


def test_new_credential_replacement(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="sk-old-key-1234567890123456",
    )
    save_provider_config_atomic(
        config_path=config_path,
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )
    settings = load_provider_settings(config_path=config_path)
    assert settings.provider == "opencode_zen"
    assert settings.api_key == SECRET


def test_unsupported_provider_fails_closed():
    with pytest.raises(ProviderConfigError, match="unsupported provider"):
        hermes_provider_env(_settings(provider="vertex_ai", api_key=SECRET))


def test_missing_key_fails_closed():
    with pytest.raises(ProviderCredentialError):
        hermes_provider_env(_settings(api_key=None))


def test_model_discovery_parsing():
    models = discover_models(
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
        fetch=_fake_fetch,
    )
    assert models == ["x-preview-f-free", "other-model"]


def test_manual_model_fallback_on_discovery_failure():
    def fail_fetch(*_a, **_k):
        raise ProviderConfigError("model discovery failed with HTTP 401")

    with pytest.raises(ProviderConfigError):
        discover_models(
            base_url="https://opencode.ai/zen/v1",
            api_key=SECRET,
            fetch=fail_fetch,
        )


def test_failed_connectivity_test_preserves_old_config(config_path, capsys):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key=SECRET,
    )
    original = config_path.read_text(encoding="utf-8")

    def fail_fetch(*_a, **_k):
        return 401, "unauthorized"

    inputs = iter(["1", "2", "y", "x-preview-f-free"])
    code = run_interactive_setup(
        config_path=config_path,
        input_func=lambda _prompt: next(inputs),
        getpass_func=lambda _prompt: SECRET,
        fetch=fail_fetch,
    )
    assert code == 1
    assert config_path.read_text(encoding="utf-8") == original
    assert SECRET not in capsys.readouterr().out


def test_atomic_config_write(config_path):
    save_provider_config_atomic(
        config_path=config_path,
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )
    assert config_path.is_file()
    settings = load_provider_settings(config_path=config_path)
    assert settings.provider == "opencode_zen"
    assert settings.model == "x-preview-f-free"
    assert settings.api_key == SECRET


def test_show_secret_safe(config_path, capsys):
    _write_provider(
        config_path,
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )
    assert run_show(config_path=config_path) == 0
    output = capsys.readouterr().out
    assert SECRET not in output
    assert "API key configured: YES" in output
    assert "Provider family: OpenCode" in output
    assert "Service: OpenCode Zen" in output
    assert "Provider ID: opencode_zen" in output


def test_test_command_works(config_path, capsys):
    _write_provider(
        config_path,
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )
    with mock.patch(
        "jarvis_foundry.cli.setup._authorized_request",
        side_effect=_fake_fetch,
    ):
        assert run_test(config_path=config_path) == 0
    output = capsys.readouterr().out
    assert "Authentication: PASS" in output
    assert SECRET not in output


def test_interactive_save_on_success_marks_pending_apply(config_path):
    inputs = iter(["1", "2", "1"])
    code = run_interactive_setup(
        config_path=config_path,
        input_func=lambda _prompt: next(inputs),
        getpass_func=lambda _prompt: SECRET,
        fetch=_fake_fetch,
    )
    assert code == 0
    settings = load_provider_settings(config_path=config_path)
    assert settings.provider == "opencode_zen"
    assert settings.model == "x-preview-f-free"
    assert (config_path.parent / "provider.apply_pending").is_file()


def test_zen_dispatch_clears_stale_go_env(tmp_path, monkeypatch):
    settings = _settings(
        provider="opencode_zen",
        model="x-preview-f-free",
        api_key=SECRET,
        base_url="https://opencode.ai/zen/v1",
    )
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-stale-go-key-1234567890")
    monkeypatch.setenv("OPENCODE_GO_BASE_URL", "https://stale.example/v1")
    home = tmp_path / "home"
    home.mkdir()
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path,
        hermes_home=home,
        enabled=True,
        python_exe="python",
        provider_settings=settings,
    )
    captured: dict[str, str] = {}

    def fake_run(command, *, cwd, env, **kwargs):
        captured.update(env)
        return type("CP", (), {
            "returncode": 0, "stdout": "VERDICT: PASS", "stderr": "",
        })()

    monkeypatch.setattr(
        "jarvis_foundry.workers.adapters.hermes.run_subprocess_with_heartbeat",
        fake_run,
    )
    adapter.dispatch(
        WorkerStepContract(
            run_id="r", mission_id="m", step_id="s", attempt=1,
            role="builder", objective="o", instructions="i",
            working_root=tmp_path, model=settings.model,
        ),
        heartbeat=lambda: None,
    )
    assert captured["OPENCODE_ZEN_API_KEY"] == SECRET
    assert "OPENCODE_GO_API_KEY" not in captured
    assert "OPENCODE_GO_BASE_URL" not in captured


def test_cli_setup_show_and_test(config_path, capsys):
    _write_provider(
        config_path,
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )
    assert cli_main.main(["setup", "--show"]) == 0
    with mock.patch(
        "jarvis_foundry.cli.setup._authorized_request",
        side_effect=_fake_fetch,
    ):
        assert cli_main.main(["setup", "--test"]) == 0
    output = capsys.readouterr().out
    assert SECRET not in output


def test_provider_registry_metadata():
    zen = require_service("opencode_zen")
    go = require_service("opencode_go")
    assert zen.hermes_api_key_env == "OPENCODE_ZEN_API_KEY"
    assert go.hermes_api_key_env == "OPENCODE_GO_API_KEY"
    assert zen.family_id == go.family_id


def test_format_setup_show_matches_contract():
    text = format_setup_show(_settings(
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    ))
    assert "Provider family: OpenCode" in text
    assert "Service: OpenCode Zen" in text
    assert SECRET not in text


def test_test_report_all_pass():
    report = evaluate_provider_configuration(
        _settings(
            provider="opencode_zen",
            model="x-preview-f-free",
            base_url="https://opencode.ai/zen/v1",
            api_key=SECRET,
        ),
        fetch=_fake_fetch,
    )
    assert report.passed
    assert "PASS" in format_test_report(report)
