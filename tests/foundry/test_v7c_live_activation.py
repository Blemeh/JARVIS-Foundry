"""Live V7C activation: the supervisor builds the resilience coordinator
only when the worker runtime YAML explicitly enables it (fail-closed)."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.supervisor.run import _resilience_coordinator


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_coordinator_off_by_default(tmp_path: Path) -> None:
    database = _database(tmp_path)
    config = tmp_path / "worker.yaml"
    config.write_text("enabled: true\n", encoding="utf-8")
    assert _resilience_coordinator(database, config) is None


def test_coordinator_requires_explicit_flag(tmp_path: Path) -> None:
    database = _database(tmp_path)
    config = tmp_path / "worker.yaml"
    config.write_text(
        "enabled: true\nresilience:\n  coordinator_enabled: true\n",
        encoding="utf-8")
    coordinator = _resilience_coordinator(database, config)
    assert coordinator is not None


def test_coordinator_flag_false_stays_off_and_bad_yaml_fails_closed(
        tmp_path: Path) -> None:
    database = _database(tmp_path)
    off = tmp_path / "off.yaml"
    off.write_text("resilience:\n  coordinator_enabled: false\n",
                   encoding="utf-8")
    assert _resilience_coordinator(database, off) is None
    missing = tmp_path / "missing.yaml"
    assert _resilience_coordinator(database, missing) is None


def test_supervisor_uses_resolved_worker_yaml_for_coordinator(
        tmp_path: Path, monkeypatch) -> None:
    import jarvis_foundry.deployment.guard as deployment_guard
    import jarvis_foundry.runtime.authority as runtime_authority
    import jarvis_foundry.supervisor.run as supervisor_run

    home = tmp_path / "foundry-home"
    home.mkdir()
    config = tmp_path / "profile-worker.yaml"
    config.write_text(
        "enabled: true\nresilience:\n  coordinator_enabled: true\n",
        encoding="utf-8",
    )
    authority = SimpleNamespace(
        worker_yaml=config,
        spawn_worker_loop=False,
        provider_settings=None,
        provider_settings_pool=(),
    )
    monkeypatch.setattr(
        runtime_authority,
        "resolve_runtime_authority",
        lambda **_kwargs: authority,
    )
    monkeypatch.setattr(
        deployment_guard,
        "refuse_developer_checkout_on_live_home",
        lambda _home: None,
    )
    captured: dict[str, object] = {}

    class StubSupervisor:
        def __init__(self, _database, _stop_path, **kwargs):
            captured.update(kwargs)

        def start(self):
            return "STOPPED"

        def stop(self):
            return None

    class StubConsoleHandler:
        def install(self):
            return None

        def uninstall(self):
            return None

    monkeypatch.setattr(supervisor_run, "Supervisor", StubSupervisor)
    monkeypatch.setattr(
        supervisor_run,
        "make_console_handler",
        lambda _supervisor: StubConsoleHandler(),
    )
    coordinator_inputs: list[Path | None] = []
    build_coordinator = supervisor_run._resilience_coordinator

    def capture_coordinator(source_database, config_path):
        coordinator_inputs.append(config_path)
        return build_coordinator(source_database, config_path)

    monkeypatch.setattr(
        supervisor_run, "_resilience_coordinator", capture_coordinator,
    )

    assert supervisor_run.main(["--home", str(home), "--once"]) == 0
    assert coordinator_inputs == [config]
    assert captured["resilience"] is not None
