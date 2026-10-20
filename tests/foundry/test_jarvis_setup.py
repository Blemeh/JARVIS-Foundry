"""Tests for ``jarvis setup`` final repair: Hermes probe, billing, masked input."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from jarvis_foundry.cli import main as cli_main
from jarvis_foundry.cli.hermes_probe import (
    HERMES_PROBE_EXPECTED,
    HermesProbeRuntime,
    build_hermes_probe_command,
    run_hermes_live_probe,
)
from jarvis_foundry.cli.masked_input import read_masked_api_key
from jarvis_foundry.cli.setup import (
    ValidationReport,
    discover_models,
    evaluate_provider_configuration,
    format_setup_show,
    format_test_report,
    format_validation_report,
    mark_pending_apply,
    run_interactive_setup,
    run_show,
    run_test,
    save_provider_config_atomic,
    try_discover_models,
    validate_configuration_structure,
)
from jarvis_foundry.runtime.provider_config import (
    ProviderConfigError,
    ProviderCredentialError,
    ProviderSettings,
    hermes_provider_env,
    load_provider_settings,
)
from jarvis_foundry.runtime.provider_registry import live_test_allowed, require_service
from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
from jarvis_foundry.workers.contracts import WorkerStepContract

SECRET = "sk-test-opencode-secret-key-1234567890"
MODELS_BODY = json.dumps({"data": [{"id": "x-preview-f-free"}, {"id": "other-model"}]})
PROBE_RUNTIME = HermesProbeRuntime(
    hermes_root=Path("C:/probe/hermes-root"),
    python_exe="C:/probe/python.exe",
)


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
    raise AssertionError("HTTP inference endpoints must not be used for setup tests")


def _successful_probe(_settings_obj, _runtime):
    return SimpleNamespace(success=True, error=None)


def _failing_probe(_settings_obj, _runtime):
    return SimpleNamespace(success=False, error="probe failed")


def test_live_test_allowed_only_for_free_model():
    assert live_test_allowed("opencode_zen", "x-preview-f-free")
    assert not live_test_allowed("opencode_zen", "some-paid-model")
    assert not live_test_allowed("opencode_go", "muse-spark-1.2-contributor")


def test_free_model_uses_real_hermes_probe_path(tmp_path):
    settings = _settings(
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )
    captured: dict[str, object] = {}

    def fake_runner(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            command, 0, stdout=f"ok {HERMES_PROBE_EXPECTED}\n", stderr="")

    result = run_hermes_live_probe(
        settings,
        HermesProbeRuntime(hermes_root=tmp_path, python_exe=sys.executable),
        run_subprocess=fake_runner,
        work_root=tmp_path / "probe",
    )
    assert result.success
    assert captured["command"] == build_hermes_probe_command(
        settings,
        python_exe=sys.executable,
        working_root=tmp_path / "probe" / "work",
    )
    assert captured["env"]["OPENCODE_ZEN_API_KEY"] == SECRET
    assert "OPENCODE_GO_API_KEY" not in captured["env"]


def test_free_model_successful_execution_passes():
    report = evaluate_provider_configuration(
        _settings(
            provider="opencode_zen",
            model="x-preview-f-free",
            base_url="https://opencode.ai/zen/v1",
            api_key=SECRET,
        ),
        probe_runtime=PROBE_RUNTIME,
        run_probe=_successful_probe,
    )
    assert report.configuration == "PASS"
    assert report.live_free_test == "PASS"


def test_paid_model_zero_inference_calls():
    calls: list[tuple[ProviderSettings, HermesProbeRuntime]] = []

    def track_probe(settings_obj, runtime):
        calls.append((settings_obj, runtime))
        return SimpleNamespace(success=True, error=None)

    report = evaluate_provider_configuration(
        _settings(
            provider="opencode_zen",
            model="some-paid-model",
            base_url="https://opencode.ai/zen/v1",
            api_key=SECRET,
        ),
        probe_runtime=PROBE_RUNTIME,
        run_probe=track_probe,
    )
    assert report.configuration == "PASS"
    assert report.live_inference == "SKIPPED"
    assert calls == []


def test_unknown_cost_model_zero_inference_calls():
    calls = []

    def track_probe(*args):
        calls.append(args)
        return SimpleNamespace(success=True, error=None)

    report = evaluate_provider_configuration(
        _settings(
            provider="opencode_go",
            model="muse-spark-1.2-contributor",
            api_key=SECRET,
        ),
        probe_runtime=PROBE_RUNTIME,
        run_probe=track_probe,
    )
    assert report.skip_reason == "BILLING PROTECTION"
    assert calls == []


def test_skipped_billable_test_is_not_fail():
    report = evaluate_provider_configuration(
        _settings(
            provider="opencode_zen",
            model="some-paid-model",
            base_url="https://opencode.ai/zen/v1",
            api_key=SECRET,
        ),
        probe_runtime=PROBE_RUNTIME,
        run_probe=_successful_probe,
    )
    assert report.exit_code == 0
    assert report.live_free_test == "SKIPPED"


def test_skipped_test_does_not_claim_selected_model_tested():
    report = evaluate_provider_configuration(
        _settings(
            provider="opencode_zen",
            model="some-paid-model",
            base_url="https://opencode.ai/zen/v1",
            api_key=SECRET,
        ),
        probe_runtime=PROBE_RUNTIME,
        run_probe=_successful_probe,
    )
    text = format_test_report(report)
    assert "Selected-model live test: SKIPPED" in text
    assert "PASS" not in text.split("Selected-model live test:")[-1]


def test_no_free_model_substitution_for_other_model():
    seen_models: list[str] = []

    def track_probe(settings_obj, _runtime):
        seen_models.append(settings_obj.model)
        return SimpleNamespace(success=True, error=None)

    evaluate_provider_configuration(
        _settings(
            provider="opencode_zen",
            model="some-paid-model",
            base_url="https://opencode.ai/zen/v1",
            api_key=SECRET,
        ),
        probe_runtime=PROBE_RUNTIME,
        run_probe=track_probe,
    )
    assert seen_models == []


def test_config_save_when_live_test_skipped(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key=SECRET,
    )
    inputs = iter(["1", "2", "y", "some-paid-model"])
    code = run_interactive_setup(
        config_path=config_path,
        input_func=lambda _prompt: next(inputs),
        read_secret=lambda _prompt: SECRET,
        fetch=_fake_fetch,
        probe_runtime=PROBE_RUNTIME,
        run_probe=_successful_probe,
    )
    assert code == 0
    settings = load_provider_settings(config_path=config_path)
    assert settings.model == "some-paid-model"


def test_discovery_403_manual_free_model_live_pass(config_path):
    def fail_fetch(*_a, **_k):
        return 403, "forbidden"

    inputs = iter(["1", "2", "x-preview-f-free"])
    code = run_interactive_setup(
        config_path=config_path,
        input_func=lambda _prompt: next(inputs),
        read_secret=lambda _prompt: SECRET,
        fetch=fail_fetch,
        probe_runtime=PROBE_RUNTIME,
        run_probe=_successful_probe,
    )
    assert code == 0


def test_discovery_failure_paid_manual_validation_pass_inference_skipped():
    report = evaluate_provider_configuration(
        _settings(
            provider="opencode_zen",
            model="some-paid-model",
            base_url="https://opencode.ai/zen/v1",
            api_key=SECRET,
        ),
        fetch=lambda *_a, **_k: (403, "forbidden"),
        probe_runtime=PROBE_RUNTIME,
        run_probe=_successful_probe,
    )
    assert report.configuration == "PASS"
    assert report.skip_reason == "BILLING PROTECTION"


def test_opencode_go_env_mapping():
    env = hermes_provider_env(_settings(api_key=SECRET))
    assert env["OPENCODE_GO_API_KEY"] == SECRET


def test_opencode_zen_env_mapping():
    settings = _settings(
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )
    env = hermes_provider_env(settings)
    assert env == {
        "OPENCODE_ZEN_API_KEY": SECRET,
        "OPENCODE_ZEN_BASE_URL": "https://opencode.ai/zen/v1",
    }
    assert "OPENCODE_GO_API_KEY" not in env


def test_shared_opencode_credential_go_to_zen(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key=SECRET,
    )
    settings = load_provider_settings(config_path=config_path)
    zen = ProviderSettings(
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=settings.api_key,
        source="setup",
    )
    assert hermes_provider_env(zen)["OPENCODE_ZEN_API_KEY"] == SECRET


def test_masked_input_shows_stars_only(monkeypatch, capsys):
    if sys.platform != "win32":
        pytest.skip("Windows masked-input test")
    chars = iter(list("abc") + ["\r"])

    def fake_getwch():
        return next(chars)

    monkeypatch.setattr("msvcrt.getwch", fake_getwch)
    assert read_masked_api_key() == "abc"
    assert capsys.readouterr().out == "API key: ***\n"


def test_pasted_masked_input_shows_stars_only(monkeypatch, capsys):
    if sys.platform != "win32":
        pytest.skip("Windows masked-input test")
    pasted = list("pastekey") + ["\r"]
    monkeypatch.setattr("msvcrt.getwch", lambda: pasted.pop(0))
    assert read_masked_api_key() == "pastekey"
    assert capsys.readouterr().out == "API key: ********\n"


def test_masked_input_backspace(monkeypatch, capsys):
    if sys.platform != "win32":
        pytest.skip("Windows masked-input test")
    sequence = list("ab") + ["\x08", "c", "\r"]
    monkeypatch.setattr("msvcrt.getwch", lambda: sequence.pop(0))
    assert read_masked_api_key() == "ac"
    output = capsys.readouterr().out
    assert output.startswith("API key: **")
    assert SECRET not in output


def test_masked_input_empty_submission(monkeypatch, capsys):
    if sys.platform != "win32":
        pytest.skip("Windows masked-input test")
    monkeypatch.setattr("msvcrt.getwch", lambda: "\r")
    assert read_masked_api_key() == ""
    assert capsys.readouterr().out == "API key: \n"


def test_masked_input_ctrl_c(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows masked-input test")
    monkeypatch.setattr("msvcrt.getwch", lambda: "\x03")
    with pytest.raises(KeyboardInterrupt):
        read_masked_api_key()


def test_secret_absent_from_validation_output(capsys):
    report = evaluate_provider_configuration(
        _settings(
            provider="opencode_zen",
            model="x-preview-f-free",
            base_url="https://opencode.ai/zen/v1",
            api_key=SECRET,
        ),
        probe_runtime=PROBE_RUNTIME,
        run_probe=_successful_probe,
    )
    print(format_validation_report(
        report,
        provider_id="opencode_zen",
        model="x-preview-f-free",
    ))
    assert SECRET not in capsys.readouterr().out


def test_failed_required_validation_preserves_config(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key=SECRET,
    )
    original = config_path.read_text(encoding="utf-8")
    inputs = iter(["1", "2", "y", "x-preview-f-free"])
    code = run_interactive_setup(
        config_path=config_path,
        input_func=lambda _prompt: next(inputs),
        read_secret=lambda _prompt: SECRET,
        fetch=_fake_fetch,
        probe_runtime=PROBE_RUNTIME,
        run_probe=_failing_probe,
    )
    assert code == 1
    assert config_path.read_text(encoding="utf-8") == original


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


def test_test_obeys_billing_policy(config_path, capsys):
    _write_provider(
        config_path,
        provider="opencode_zen",
        model="some-paid-model",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )
    assert run_test(
        config_path=config_path,
        probe_runtime=PROBE_RUNTIME,
        run_probe=_successful_probe,
    ) == 0
    output = capsys.readouterr().out
    assert "BILLING PROTECTION" in output


def test_silent_provider_fallback_rejected(tmp_path):
    settings = _settings(
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )

    def wrong_provider_runner(command, **kwargs):
        assert command[command.index("--provider") + 1] == "opencode-zen"
        command[command.index("--provider") + 1] = "opencode-go"
        return subprocess.CompletedProcess(
            command, 0, stdout=f"{HERMES_PROBE_EXPECTED}\n", stderr="")

    result = run_hermes_live_probe(
        settings,
        HermesProbeRuntime(hermes_root=tmp_path, python_exe=sys.executable),
        run_subprocess=wrong_provider_runner,
        work_root=tmp_path / "probe",
    )
    assert not result.success


def test_silent_model_fallback_rejected(tmp_path):
    settings = _settings(
        provider="opencode_zen",
        model="x-preview-f-free",
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
    )

    def wrong_model_runner(command, **kwargs):
        command[command.index("--model") + 1] = "other-model"
        return subprocess.CompletedProcess(
            command, 0, stdout=f"{HERMES_PROBE_EXPECTED}\n", stderr="")

    result = run_hermes_live_probe(
        settings,
        HermesProbeRuntime(hermes_root=tmp_path, python_exe=sys.executable),
        run_subprocess=wrong_model_runner,
        work_root=tmp_path / "probe",
    )
    assert not result.success


def test_atomic_config_write(config_path):
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


def test_model_discovery_optional_on_403():
    assert try_discover_models(
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
        fetch=lambda *_a, **_k: (403, "forbidden"),
    ) is None


def test_model_discovery_parsing():
    models = discover_models(
        base_url="https://opencode.ai/zen/v1",
        api_key=SECRET,
        fetch=_fake_fetch,
    )
    assert "x-preview-f-free" in models


def test_model_discovery_sends_non_python_user_agent(monkeypatch):
    captured: dict[str, str] = {}

    class FakeResponse:
        status = 200

        def read(self):
            return MODELS_BODY.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=30.0):
        for key, value in request.header_items():
            captured[key] = value
        return FakeResponse()

    monkeypatch.setattr("jarvis_foundry.cli.setup.urllib.request.urlopen", fake_urlopen)
    from jarvis_foundry.cli.setup import _DISCOVERY_USER_AGENT, _authorized_request

    _authorized_request("https://opencode.ai/zen/v1/models", api_key=SECRET)
    assert captured.get("User-agent") == _DISCOVERY_USER_AGENT


def test_unsupported_provider_fails_closed():
    with pytest.raises(ProviderConfigError):
        hermes_provider_env(_settings(provider="vertex_ai", api_key=SECRET))


def test_missing_key_fails_closed():
    with pytest.raises(ProviderCredentialError):
        hermes_provider_env(_settings(api_key=None))


def test_validate_configuration_structure_requires_model():
    ok, reason = validate_configuration_structure(_settings(model="  ", api_key=SECRET))
    assert not ok
    assert "model" in reason


def test_zen_dispatch_is_refused_and_never_receives_a_stale_go_key(tmp_path, monkeypatch):
    settings = _settings(
        provider="opencode_zen",
        model="x-preview-f-free",
        api_key=SECRET,
        base_url="https://opencode.ai/zen/v1",
    )
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-stale-go-key-1234567890")
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
    # Owner policy: only the exact OpenCode Go model may be dispatched (no
    # pay-as-you-go Zen route), so a Zen dispatch is refused before any
    # subprocess exists and the stale Go key can never reach it.
    with pytest.raises(ProviderFailure, match="owner-authorized OpenCode Go"):
        adapter.dispatch(
            WorkerStepContract(
                run_id="r", mission_id="m", step_id="s", attempt=1,
                role="builder", objective="o", instructions="i",
                working_root=tmp_path, model=settings.model,
            ),
            heartbeat=lambda: None,
        )
    assert captured == {}


def test_provider_registry_metadata():
    zen = require_service("opencode_zen")
    assert zen.free_test_model == "x-preview-f-free"
    assert zen.live_test_allowed_for_free_model


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
        "jarvis_foundry.cli.setup.resolve_hermes_probe_runtime",
        return_value=PROBE_RUNTIME,
    ), mock.patch(
        "jarvis_foundry.cli.setup.run_hermes_live_probe",
        return_value=SimpleNamespace(success=True, error=None),
    ):
        assert cli_main.main(["setup", "--test"]) == 0
    assert SECRET not in capsys.readouterr().out
