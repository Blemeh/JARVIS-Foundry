"""Machine-local provider configuration resolution (repair pass)."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.verify import (
    VerificationError,
    record_verification_request,
)
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.provider_config import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    ProviderConfigError,
    ProviderCredentialError,
    ProviderSettings,
    default_provider_config_path,
    ensure_provider_template,
    hermes_provider_env,
    load_provider_settings,
    openhands_provider_env,
)
from jarvis_foundry.workers.adapters.base import ProviderUnavailable
from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
from jarvis_foundry.workers.adapters.openhands import OpenHandsWorkerAdapter
from jarvis_foundry.workers.contracts import WorkerStepContract


@pytest.fixture
def config_path(monkeypatch, tmp_path):
    path = tmp_path / "provider.yaml"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "JARVIS"))
    for name in (
        "JARVIS_RUNTIME_PROVIDER",
        "JARVIS_RUNTIME_MODEL",
        "JARVIS_RUNTIME_API_KEY",
        "JARVIS_RUNTIME_BASE_URL",
        "JARVIS_OPENCODE_API_KEY",
        "OPENCODE_GO_API_KEY",
        "OPENCODE_API_KEY",
        "JARVIS_OPENCODE_BASE_URL",
        "OPENCODE_BASE_URL",
        "OPENCODE_GO_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "jarvis_foundry.runtime.provider_config.default_provider_config_path",
        lambda root=None: path,
    )
    return path


def _write_provider(path: Path, **fields: str) -> None:
    lines = [f"{key}: {value}" for key, value in fields.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _settings(**kwargs: object) -> ProviderSettings:
    return ProviderSettings(
        provider=str(kwargs.get("provider", DEFAULT_PROVIDER)),
        model=str(kwargs.get("model", DEFAULT_MODEL)),
        base_url=str(kwargs.get("base_url", "https://opencode.ai/zen/go/v1")),
        api_key=kwargs.get("api_key"),  # type: ignore[arg-type]
        source=str(kwargs.get("source", "test")),
    )


def test_single_resolution_at_worker_config_boundary(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="sk-single-resolution-key-1234567890",
    )
    with mock.patch(
        "jarvis_foundry.runtime.config.load_provider_settings",
        wraps=load_provider_settings,
    ) as loader:
        cfg = WorkerRuntimeConfig.from_yaml(None)
    assert loader.call_count == 1
    assert cfg.provider_settings.api_key == "sk-single-resolution-key-1234567890"


def test_adapters_do_not_reread_provider_file(tmp_path, monkeypatch):
    settings = _settings(
        api_key="sk-adapter-bound-key-1234567890",
        base_url="https://bound.example/v1",
    )
    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    adapter = OpenHandsWorkerAdapter(
        openhands_exe=exe,
        data_dir=data_dir,
        enabled=True,
        provider_settings=settings,
    )
    captured: dict[str, str] = {}

    def fake_run(command, *, cwd, env, **kwargs):
        captured.update(env)
        return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(
        "jarvis_foundry.workers.adapters.openhands.run_subprocess_with_heartbeat",
        fake_run,
    )
    monkeypatch.setattr(
        "jarvis_foundry.workers.adapters.openhands.snapshot_openhands_tree",
        lambda *_a, **_k: {},
    )
    with mock.patch(
        "jarvis_foundry.runtime.provider_config.load_provider_settings",
    ) as loader:
        adapter.dispatch(
            WorkerStepContract(
                run_id="r", mission_id="m", step_id="s", attempt=1,
                role="builder", objective="o", instructions="i",
                working_root=tmp_path, model=settings.model,
            ),
            heartbeat=lambda: None,
        )
        loader.assert_not_called()
    assert captured["LLM_API_KEY"] == "sk-adapter-bound-key-1234567890"
    assert captured["LLM_BASE_URL"] == "https://bound.example/v1"


def test_hermes_env_uses_opencode_go_names():
    settings = _settings(
        api_key="sk-hermes-key-1234567890",
        base_url="https://hermes.example/v1",
    )
    env = hermes_provider_env(settings)
    assert env == {
        "OPENCODE_GO_API_KEY": "sk-hermes-key-1234567890",
        "OPENCODE_GO_BASE_URL": "https://hermes.example/v1",
    }


def test_yaml_key_and_base_url_win_over_stale_legacy_env(config_path, monkeypatch):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://yaml.example/v1",
        api_key="sk-yaml-wins-key-1234567890",
    )
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-stale-legacy-key-1234567890")
    monkeypatch.setenv("OPENCODE_GO_BASE_URL", "https://stale.example/v1")
    settings = load_provider_settings(config_path=config_path)
    assert settings.api_key == "sk-yaml-wins-key-1234567890"
    assert settings.base_url == "https://yaml.example/v1"


def test_hermes_dispatch_overrides_stale_legacy_env(tmp_path, monkeypatch):
    settings = _settings(
        api_key="sk-hermes-authoritative-key-1234567890",
        base_url="https://authoritative.example/v1",
    )
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-stale-legacy-key-1234567890")
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
    assert captured["OPENCODE_GO_API_KEY"] == "sk-hermes-authoritative-key-1234567890"
    assert captured["OPENCODE_GO_BASE_URL"] == "https://authoritative.example/v1"


def test_missing_api_key_fails_closed_hermes(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path,
        hermes_home=home,
        enabled=True,
        python_exe="python",
        provider_settings=_settings(api_key=None),
    )
    with pytest.raises(ProviderUnavailable, match="API key"):
        adapter.dispatch(
            WorkerStepContract(
                run_id="r", mission_id="m", step_id="s", attempt=1,
                role="builder", objective="o", instructions="i",
                working_root=tmp_path,
            ),
            heartbeat=lambda: None,
        )


def test_missing_api_key_fails_closed_openhands(tmp_path):
    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    adapter = OpenHandsWorkerAdapter(
        openhands_exe=exe,
        data_dir=data_dir,
        enabled=True,
        provider_settings=_settings(api_key=None),
    )
    with pytest.raises(ProviderUnavailable, match="API key"):
        adapter.dispatch(
            WorkerStepContract(
                run_id="r", mission_id="m", step_id="s", attempt=1,
                role="builder", objective="o", instructions="i",
                working_root=tmp_path,
            ),
            heartbeat=lambda: None,
        )


def test_malformed_yaml_does_not_reveal_secret(config_path):
    secret = "sk-malformed-yaml-secret-key-1234567890"
    config_path.write_text(
        f"provider: opencode_go\napi_key: {secret}\n: [unterminated\n",
        encoding="utf-8",
    )
    with pytest.raises(ProviderConfigError) as exc:
        load_provider_settings(config_path=config_path)
    assert secret not in str(exc.value)


def test_stop_freezes_before_provider_config_required(tmp_path, monkeypatch):
    stop = tmp_path / "EMERGENCY_STOP"
    stop.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "jarvis_foundry.runtime.worker_loop.FoundryPaths.discover",
        lambda _home=None: FoundryPaths(tmp_path),
    )
    monkeypatch.setattr(
        "jarvis_foundry.runtime.worker_loop.WorkerRuntimeConfig.from_yaml",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("provider config must not load when STOP is active")),
    )
    from jarvis_foundry.runtime import worker_loop

    code = worker_loop.main(["--home", str(tmp_path), "--once"])
    assert code == 0


def test_unauthorized_runtime_provider_override_rejected(config_path, monkeypatch):
    monkeypatch.setenv("JARVIS_RUNTIME_PROVIDER", "vertex_ai")
    with pytest.raises(ProviderConfigError, match="unauthorized runtime override"):
        load_provider_settings(config_path=config_path)


def test_unauthorized_runtime_model_override_rejected(config_path, monkeypatch):
    monkeypatch.setenv("JARVIS_RUNTIME_MODEL", "kimi-k2.7-code")
    with pytest.raises(ProviderConfigError, match="unauthorized runtime override"):
        load_provider_settings(config_path=config_path)


def test_new_mission_uses_muse_from_provider_config(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    router = RoleRouter(
        provider_settings=load_provider_settings(config_path=config_path))
    assert router.config("builder").preferred_model == "muse-spark-1.2-contributor"


def _record_improvement(database: FoundryDatabase) -> str:
    return record_improvement(
        database,
        problem="test",
        evidence_refs=["e"],
        provenance={"source": "test"},
        hypothesis="h",
        expected_mechanism="m",
        baseline_metric={"name": "n", "value": 1, "unit": "u", "source": "s"},
        mmi={"minimum_delta": 1, "direction": "decrease", "unit": "u"},
        guardrails={
            "correctness": "ok",
            "safety": "no live mutation",
            "cost": "no paid calls",
            "latency": "no regression",
        },
        workload={"ref": "w", "hash": "a" * 64},
        observation={"requirement": "r", "cycles": 1},
        declared_risk="R1",
        surfaces=["tests"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "revert", "rollback_threshold": "5m"},
    )


def test_evolution_verifier_accepts_historical_deepseek_identity(config_path):
    database = FoundryDatabase(config_path.parent / "foundry.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    record_verification_request(
        database,
        improvement_id=improvement_id,
        subject_ref="candidate",
        criteria=["criterion-1"],
        artifacts={"artifact": "a" * 64},
        builder_identity={
            "provider": "opencode_go",
            "model": "deepseek-v4-flash",
            "session_id": "historical-builder",
        },
        controller="supervisor",
    )


def test_evolution_verifier_accepts_configured_muse_identity(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    database = FoundryDatabase(config_path.parent / "foundry2.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    record_verification_request(
        database,
        improvement_id=improvement_id,
        subject_ref="candidate",
        criteria=["criterion-1"],
        artifacts={"artifact": "a" * 64},
        builder_identity={
            "provider": "opencode_go",
            "model": "muse-spark-1.2-contributor",
            "session_id": "muse-builder",
        },
        controller="supervisor",
    )


def test_evolution_verifier_rejects_foreign_identity(config_path):
    database = FoundryDatabase(config_path.parent / "foundry3.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    with pytest.raises(VerificationError):
        record_verification_request(
            database,
            improvement_id=improvement_id,
            subject_ref="candidate",
            criteria=["criterion-1"],
            artifacts={"artifact": "a" * 64},
            builder_identity={
                "provider": "vertex_ai",
                "model": "gemini-pro",
                "session_id": "foreign",
            },
            controller="supervisor",
        )


def test_openhands_provider_env_requires_key():
    with pytest.raises(ProviderCredentialError):
        openhands_provider_env(_settings(api_key=None))


def test_worker_runtime_overlays_provider_model(config_path, tmp_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    worker_yaml = tmp_path / "worker.yaml"
    worker_yaml.write_text(
        "enabled: true\nprovider: opencode_go\nmodel: deepseek-v4-flash\n",
        encoding="utf-8",
    )
    settings = load_provider_settings(config_path=config_path)
    cfg = WorkerRuntimeConfig.from_yaml(worker_yaml, provider_settings=settings)
    assert cfg.model == "muse-spark-1.2-contributor"


def test_ensure_provider_template_is_not_tracked(tmp_path):
    target = tmp_path / "JARVIS" / "config" / "provider.yaml"
    written = ensure_provider_template(target)
    assert written == target
    assert DEFAULT_MODEL in written.read_text(encoding="utf-8")


def test_default_provider_config_path_uses_jarvis_install_root(tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(root))
    assert default_provider_config_path() == root / "config" / "provider.yaml"
