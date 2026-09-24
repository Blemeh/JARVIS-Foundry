"""Machine-local provider configuration resolution (repair pass)."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from unittest import mock

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.cycle import run_cycle
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.verify import (
    VerificationError,
    record_verification_request,
)
from jarvis_foundry.events import Event
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.runtime.authority import resolve_runtime_authority
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.provider_config import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    ProviderConfigError,
    ProviderCredentialError,
    ProviderSettings,
    default_provider_config_path,
    default_provider_settings,
    ensure_provider_template,
    hermes_provider_env,
    load_provider_settings,
    openhands_provider_env,
)
from jarvis_foundry.supervisor.service import Supervisor
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


def test_hermes_env_uses_opencode_zen_names():
    settings = _settings(
        provider="opencode_zen",
        model="x-preview-f-free",
        api_key="sk-hermes-zen-key-1234567890",
        base_url="https://opencode.ai/zen/v1",
    )
    env = hermes_provider_env(settings)
    assert env == {
        "OPENCODE_ZEN_API_KEY": "sk-hermes-zen-key-1234567890",
        "OPENCODE_ZEN_BASE_URL": "https://opencode.ai/zen/v1",
    }
    assert "OPENCODE_GO_API_KEY" not in env


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
    )
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
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
        database=database,
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
    assert captured["OPENCODE_GO_API_KEY"] not in {
        settings.api_key,
        "sk-stale-legacy-key-1234567890",
    }
    assert captured["OPENCODE_GO_API_KEY"]
    assert captured["OPENCODE_GO_BASE_URL"].startswith("http://127.0.0.1:")


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


def _seed_agent_run(
    database: FoundryDatabase,
    run_id: str,
    *,
    provider: str,
    model: str,
    mission_id: str = "mission-1",
    step_id: str = "step-1",
    role: str = "builder",
) -> None:
    database.append(Event.create(
        "agent_run.started",
        {
            "run_id": run_id,
            "mission_id": mission_id,
            "step_id": step_id,
            "role": role,
            "provider": provider,
            "model": model,
            "context_manifest_hash": "hash",
            "tool_policy": [],
        },
        source_type="test",
        source_id="test",
        idempotency_key=f"agent_run.started:{run_id}:{provider}",
    ))


def test_evolution_verifier_accepts_persisted_historical_deepseek(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    run_id = "historical-deepseek-run"
    _seed_agent_run(
        database,
        run_id,
        provider="opencode_go",
        model="deepseek-v4-flash",
    )
    persisted = {
        "provider": "opencode_go",
        "model": "deepseek-v4-flash",
        "session_id": "historical-builder",
    }
    record_verification_request(
        database,
        improvement_id=improvement_id,
        subject_ref="candidate",
        criteria=["criterion-1"],
        artifacts={"artifact": "a" * 64},
        builder_identity=persisted,
        controller="supervisor",
        provider_settings=settings,
        builder_run_id=run_id,
        builder_mission_id="mission-1",
        builder_step_id="step-1",
    )


def test_new_arbitrary_deepseek_identity_rejected(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry4.db")
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
                "provider": "opencode_go",
                "model": "deepseek-v4-flash",
                "session_id": "forged-new",
            },
            controller="supervisor",
            provider_settings=settings,
        )


def test_forged_historical_deepseek_rejected(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry5.db")
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
                "provider": "opencode_go",
                "model": "deepseek-v4-flash",
                "session_id": "forged-historical",
            },
            controller="supervisor",
            provider_settings=settings,
        )


def test_forged_matching_persisted_identity_without_run_rejected(config_path):
    """Caller-supplied identity dict is not authority without durable proof."""
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry-forged.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    forged = {
        "provider": "opencode_go",
        "model": "deepseek-v4-flash",
        "session_id": "forged-no-db",
    }
    with pytest.raises(VerificationError):
        record_verification_request(
            database,
            improvement_id=improvement_id,
            subject_ref="candidate",
            criteria=["criterion-1"],
            artifacts={"artifact": "a" * 64},
            builder_identity=forged,
            controller="supervisor",
            provider_settings=settings,
            builder_run_id="run-does-not-exist",
            builder_mission_id="mission-1",
            builder_step_id="step-1",
        )


def test_historical_identity_mismatch_rejected(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry-mismatch.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    run_id = "historical-muse-run"
    _seed_agent_run(
        database,
        run_id,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
    )
    with pytest.raises(VerificationError):
        record_verification_request(
            database,
            improvement_id=improvement_id,
            subject_ref="candidate",
            criteria=["criterion-1"],
            artifacts={"artifact": "a" * 64},
            builder_identity={
                "provider": "opencode_go",
                "model": "deepseek-v4-flash",
                "session_id": "mismatch",
            },
            controller="supervisor",
            provider_settings=settings,
            builder_run_id=run_id,
            builder_mission_id="mission-1",
            builder_step_id="step-1",
        )


def test_wrong_run_historical_identity_rejected(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry-wrong-run.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    _seed_agent_run(
        database,
        "other-deepseek-run",
        provider="opencode_go",
        model="deepseek-v4-flash",
    )
    with pytest.raises(VerificationError):
        record_verification_request(
            database,
            improvement_id=improvement_id,
            subject_ref="candidate",
            criteria=["criterion-1"],
            artifacts={"artifact": "a" * 64},
            builder_identity={
                "provider": "opencode_go",
                "model": "deepseek-v4-flash",
                "session_id": "wrong-run",
            },
            controller="supervisor",
            provider_settings=settings,
            builder_run_id="wrong-run-id",
            builder_mission_id="mission-1",
            builder_step_id="step-1",
        )


def test_unrelated_real_historical_deepseek_run_rejected(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry-unrelated.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    _seed_agent_run(
        database,
        "subject-muse-run",
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        mission_id="mission-subject",
        step_id="step-1",
    )
    _seed_agent_run(
        database,
        "unrelated-deepseek-run",
        provider="opencode_go",
        model="deepseek-v4-flash",
        mission_id="mission-other",
        step_id="step-9",
    )
    with pytest.raises(VerificationError):
        record_verification_request(
            database,
            improvement_id=improvement_id,
            subject_ref="candidate",
            criteria=["criterion-1"],
            artifacts={"artifact": "a" * 64},
            builder_identity={
                "provider": "opencode_go",
                "model": "deepseek-v4-flash",
                "session_id": "stolen-run",
            },
            controller="supervisor",
            provider_settings=settings,
            builder_run_id="unrelated-deepseek-run",
            builder_mission_id="mission-subject",
            builder_step_id="step-1",
        )


def test_wrong_mission_historical_run_rejected(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry-wrong-mission.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    _seed_agent_run(
        database,
        "deepseek-other-mission",
        provider="opencode_go",
        model="deepseek-v4-flash",
        mission_id="mission-other",
        step_id="step-1",
    )
    with pytest.raises(VerificationError):
        record_verification_request(
            database,
            improvement_id=improvement_id,
            subject_ref="candidate",
            criteria=["criterion-1"],
            artifacts={"artifact": "a" * 64},
            builder_identity={
                "provider": "opencode_go",
                "model": "deepseek-v4-flash",
                "session_id": "wrong-mission",
            },
            controller="supervisor",
            provider_settings=settings,
            builder_run_id="deepseek-other-mission",
            builder_mission_id="mission-subject",
            builder_step_id="step-1",
        )


def test_wrong_step_historical_run_rejected(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry-wrong-step.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    _seed_agent_run(
        database,
        "deepseek-step-a",
        provider="opencode_go",
        model="deepseek-v4-flash",
        mission_id="mission-1",
        step_id="step-a",
    )
    with pytest.raises(VerificationError):
        record_verification_request(
            database,
            improvement_id=improvement_id,
            subject_ref="candidate",
            criteria=["criterion-1"],
            artifacts={"artifact": "a" * 64},
            builder_identity={
                "provider": "opencode_go",
                "model": "deepseek-v4-flash",
                "session_id": "wrong-step",
            },
            controller="supervisor",
            provider_settings=settings,
            builder_run_id="deepseek-step-a",
            builder_mission_id="mission-1",
            builder_step_id="step-b",
        )


def test_wrong_role_historical_run_rejected(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry-wrong-role.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    _seed_agent_run(
        database,
        "deepseek-verifier-run",
        provider="opencode_go",
        model="deepseek-v4-flash",
        mission_id="mission-1",
        step_id="step-1",
        role="verifier",
    )
    with pytest.raises(VerificationError):
        record_verification_request(
            database,
            improvement_id=improvement_id,
            subject_ref="candidate",
            criteria=["criterion-1"],
            artifacts={"artifact": "a" * 64},
            builder_identity={
                "provider": "opencode_go",
                "model": "deepseek-v4-flash",
                "session_id": "wrong-role",
            },
            controller="supervisor",
            provider_settings=settings,
            builder_run_id="deepseek-verifier-run",
            builder_mission_id="mission-1",
            builder_step_id="step-1",
        )


def test_caller_supplied_identity_dict_has_zero_authority(config_path):
    _write_provider(
        config_path,
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="",
    )
    settings = load_provider_settings(config_path=config_path)
    database = FoundryDatabase(config_path.parent / "foundry-dict-authority.db")
    database.initialize()
    improvement_id = _record_improvement(database)
    claimed = {
        "provider": "opencode_go",
        "model": "deepseek-v4-flash",
        "session_id": "dict-only",
    }
    with pytest.raises(VerificationError):
        record_verification_request(
            database,
            improvement_id=improvement_id,
            subject_ref="candidate",
            criteria=["criterion-1"],
            artifacts={"artifact": "a" * 64},
            builder_identity=claimed,
            controller="supervisor",
            provider_settings=settings,
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
        provider_settings=load_provider_settings(config_path=config_path),
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
            provider_settings=load_provider_settings(config_path=config_path),
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


SECRET_TOKEN = "SECRET_SHOULD_NEVER_APPEAR_GROK_FINAL_913527"


def _value_contains_secret(value: object, secret: str) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        return secret in text
    try:
        rendered = repr(value)
    except Exception:
        rendered = type(value).__name__
    return secret in rendered


def _exception_graph_contains_secret(error: BaseException, secret: str) -> list[str]:
    """Return paths in the exception graph where secret appears."""
    paths: list[str] = []
    seen: set[int] = set()

    def walk_tb(tb, prefix: str) -> None:
        depth = 0
        while tb is not None:
            frame = tb.tb_frame
            locals_map = getattr(frame, "f_locals", {}) or {}
            for name, value in locals_map.items():
                if secret in str(name) or _value_contains_secret(value, secret):
                    paths.append(f"{prefix}.tb[{depth}].f_locals.{name}")
            tb = tb.tb_next
            depth += 1

    def walk(exc: BaseException | None, prefix: str) -> None:
        if exc is None or id(exc) in seen:
            return
        seen.add(id(exc))
        if secret in str(exc):
            paths.append(f"{prefix}:str")
        if secret in repr(exc):
            paths.append(f"{prefix}:repr")
        if _value_contains_secret(getattr(exc, "args", ()), secret):
            paths.append(f"{prefix}.args")
        for key, value in getattr(exc, "__dict__", {}).items():
            if secret in str(key) or _value_contains_secret(value, secret):
                paths.append(f"{prefix}.{key}")
        for attr in ("problem_mark", "context_mark", "problem", "context"):
            mark = getattr(exc, attr, None)
            if mark is None:
                continue
            if _value_contains_secret(mark, secret):
                paths.append(f"{prefix}.{attr}")
            buffer = getattr(mark, "buffer", None)
            if buffer is not None and _value_contains_secret(buffer, secret):
                paths.append(f"{prefix}.{attr}.buffer")
        walk_tb(exc.__traceback__, f"{prefix}.__traceback__")
        walk(exc.__cause__, f"{prefix}.__cause__")
        walk(exc.__context__, f"{prefix}.__context__")

    walk(error, "error")
    return paths


@pytest.fixture()
def db(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_provider_settings_repr_and_str_secret_safe():
    settings = _settings(api_key=SECRET_TOKEN)
    assert SECRET_TOKEN not in repr(settings)
    assert SECRET_TOKEN not in str(settings)


def test_malformed_yaml_exception_chain_secret_safe(config_path, caplog):
    config_path.write_text(
        f"provider: opencode_go\napi_key: {SECRET_TOKEN}\n: [unterminated\n",
        encoding="utf-8",
    )
    try:
        load_provider_settings(config_path=config_path)
    except ProviderConfigError as error:
        rendered = "".join(traceback.format_exception(error))
        leaks = _exception_graph_contains_secret(error, SECRET_TOKEN)
        assert SECRET_TOKEN not in str(error)
        assert SECRET_TOKEN not in repr(error)
        assert SECRET_TOKEN not in rendered
        assert SECRET_TOKEN not in caplog.text
        assert error.__cause__ is None
        assert error.__context__ is None
        assert leaks == []
    else:
        pytest.fail("expected ProviderConfigError")


def test_supervisor_authority_reads_provider_once(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "JARVIS"))
    provider_path = tmp_path / "JARVIS" / "config" / "provider.yaml"
    provider_path.parent.mkdir(parents=True)
    provider_path.write_text(
        "provider: opencode_go\n"
        "model: muse-spark-1.2-contributor\n"
        "base_url: https://opencode.ai/zen/go/v1\n"
        'api_key: ""\n',
        encoding="utf-8",
    )
    with mock.patch(
        "jarvis_foundry.runtime.authority.load_provider_settings",
        wraps=load_provider_settings,
    ) as loader:
        authority = resolve_runtime_authority(home=home)
        cfg = WorkerRuntimeConfig.from_yaml(
            None, provider_settings=authority.provider_settings)
    assert loader.call_count == 1
    assert cfg.provider_settings.model == "muse-spark-1.2-contributor"


def test_supervisor_spawn_uses_authority_provider_settings(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    settings = _settings(api_key="sk-supervisor-spawn-key-1234567890")
    supervisor = Supervisor(
        database,
        home / "EMERGENCY_STOP",
        spawn_worker_loop=True,
        provider_settings=settings,
    )
    with mock.patch(
        "jarvis_foundry.runtime.config.load_provider_settings",
    ) as loader:
        supervisor._maybe_spawn_worker_loop()
        loader.assert_not_called()


def test_supervisor_stop_before_provider_config(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    (home / "EMERGENCY_STOP").write_text("{}", encoding="utf-8")
    provider_path = tmp_path / "JARVIS" / "config" / "provider.yaml"
    provider_path.parent.mkdir(parents=True)
    provider_path.write_text(
        f"provider: opencode_go\napi_key: {SECRET_TOKEN}\n: [bad\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "JARVIS"))
    with mock.patch(
        "jarvis_foundry.runtime.authority.load_provider_settings",
    ) as loader:
        from jarvis_foundry.supervisor import run as supervisor_run

        code = supervisor_run.main(["--home", str(home), "--once"])
    output = capsys.readouterr().out
    assert code == 0
    assert json.loads(output.splitlines()[0])["supervisor"] == "FROZEN"
    assert SECRET_TOKEN not in output
    loader.assert_not_called()


def test_supervisor_normal_startup_resolves_provider(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    provider_path = tmp_path / "JARVIS" / "config" / "provider.yaml"
    provider_path.parent.mkdir(parents=True)
    provider_path.write_text(
        "provider: opencode_go\n"
        "model: muse-spark-1.2-contributor\n"
        "base_url: https://opencode.ai/zen/go/v1\n"
        'api_key: ""\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "JARVIS"))
    from jarvis_foundry.supervisor import run as supervisor_run

    code = supervisor_run.main(["--home", str(home), "--once"])
    assert code == 0


def test_evolution_cycle_reads_provider_once(db, tmp_path, monkeypatch):
    workload = tmp_path / "workload"
    workload.mkdir()
    for index in range(3):
        (workload / f"case-{index}.txt").write_text(f"input-{index}", encoding="utf-8")
    improvement_id = _record_improvement(db)
    build_ok = [
        sys.executable, "-c",
        "import pathlib\npathlib.Path('built.txt').write_text('built')\n",
    ]
    fast = [sys.executable, "-c", "import pathlib,sys\npathlib.Path(sys.argv[1]).read_text()\n"]
    slow = [
        sys.executable, "-c",
        "import pathlib,sys,time\npathlib.Path(sys.argv[1]).read_text()\ntime.sleep(0.3)\n",
    ]
    with mock.patch(
        "jarvis_foundry.evolution.cycle.load_provider_settings",
        wraps=load_provider_settings,
    ) as loader:
        result = run_cycle(
            db,
            improvement_id=improvement_id,
            source_commit="c" * 40,
            workload_dir=workload,
            baseline_command=slow,
            candidate_command=fast,
            build_command=build_ok,
            controller="supervisor",
        )
    assert loader.call_count == 1
