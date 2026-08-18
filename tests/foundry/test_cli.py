"""WP2 CLI tests: both --home positions and every subcommand end-to-end."""

from __future__ import annotations

import json

from jarvis_foundry.migrations import SCHEMA_VERSION


def _run(capsys, *argv: str) -> dict:
    from jarvis_foundry.cli.main import main

    assert main(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


def test_commands_work_without_home_flag(tmp_path, monkeypatch):
    # Pre-existing CLI bug found during the WP7 activation: --home uses
    # argparse SUPPRESS, so a bare `jarvis <cmd>` crashed with
    # AttributeError. Commands must resolve the default home instead.
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from jarvis_foundry.cli.main import main
    assert main(["init"]) == 0
    assert main(["status"]) == 0
    assert main(["doctor"]) == 0
    assert (tmp_path / "foundry.db").exists()


def test_init_and_doctor_with_home_before_subcommand(tmp_path, capsys):
    assert _run(capsys, "--home", str(tmp_path), "init")["schema_version"] == SCHEMA_VERSION
    assert _run(capsys, "--home", str(tmp_path), "doctor")["database_quick_check"] == "ok"


def test_home_after_subcommand_is_accepted(tmp_path, capsys):
    assert _run(capsys, "init", "--home", str(tmp_path))["schema_version"] == SCHEMA_VERSION
    assert _run(capsys, "doctor", "--home", str(tmp_path))["database_quick_check"] == "ok"


def test_both_home_positions_target_the_same_database(tmp_path, capsys):
    _run(capsys, "--home", str(tmp_path), "init")
    first = _run(capsys, "status", "--home", str(tmp_path))
    second = _run(capsys, "--home", str(tmp_path), "status")
    assert first["home"] == second["home"] == str(tmp_path.resolve())


def test_stop_and_resume_roundtrip(tmp_path, capsys):
    _run(capsys, "--home", str(tmp_path), "init")
    record = _run(capsys, "--home", str(tmp_path), "stop", "--reason", "cli test")
    assert record["source"] == "local_cli"
    assert (tmp_path / "EMERGENCY_STOP").exists()
    result = _run(capsys, "--home", str(tmp_path), "resume")
    assert result["sentinel_cleared"] is True
    assert not (tmp_path / "EMERGENCY_STOP").exists()


def test_events_and_status_report_counts(tmp_path, capsys):
    _run(capsys, "--home", str(tmp_path), "init")
    status = _run(capsys, "--home", str(tmp_path), "status")
    assert status["counts"]["events"] == 0
    assert "agent_runs" in status["counts"]
    assert _run(capsys, "--home", str(tmp_path), "events", "--limit", "10") == []


def test_status_and_doctor_do_not_migrate(tmp_path, capsys, monkeypatch):
    """Live `jarvis status` / `doctor` must not call initialize() — that
    migrates and write-locks the Supervisor database."""
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.cli.main import main

    _run(capsys, "--home", str(tmp_path), "init")
    calls: list[str] = []
    original = FoundryDatabase.initialize

    def spy(self, target=None):
        calls.append("initialize")
        return original(self, target=target)

    monkeypatch.setattr(FoundryDatabase, "initialize", spy)
    assert main(["status", "--home", str(tmp_path)]) == 0
    assert main(["doctor", "--home", str(tmp_path)]) == 0
    assert calls == []


def test_status_without_database_does_not_create_one(tmp_path, capsys):
    from jarvis_foundry.cli.main import main

    code = main(["status", "--home", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["error"] == "database_not_initialized"
    assert not (tmp_path / "foundry.db").exists()
