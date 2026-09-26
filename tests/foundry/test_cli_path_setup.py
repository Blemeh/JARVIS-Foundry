"""Global ``jarvis`` command installation."""
from __future__ import annotations

from jarvis_foundry.deployment.layout import cli_cmd
from jarvis_foundry.deployment.path_setup import bootstrap_global_cli
from jarvis_foundry.deployment.release import JARVIS_CLI_CMD_TEXT, install_cli_launcher


def test_install_cli_launcher_writes_jarvis_cmd(tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    path = install_cli_launcher(root)
    assert path == cli_cmd(root)
    assert "jarvis_foundry.cli.main" in path.read_text(encoding="utf-8")
    python_bin = local / "Python" / "bin" / "jarvis.cmd"
    assert python_bin.is_file()


def test_bootstrap_writes_launcher(tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    monkeypatch.setattr(
        "jarvis_foundry.deployment.path_setup.ensure_jarvis_bin_on_path",
        lambda *_a, **_k: False,
    )
    launcher = bootstrap_global_cli(root)
    assert launcher.is_file()
    assert JARVIS_CLI_CMD_TEXT.splitlines()[0] in launcher.read_text(encoding="utf-8")


def test_cli_launcher_uses_current_immutable_release_and_fails_closed():
    text = JARVIS_CLI_CMD_TEXT

    assert 'set "CURRENT_FILE=%JARVIS_INSTALL_ROOT%\\CURRENT"' in text
    assert 'set "PY=%RELEASE%\\.venv\\Scripts\\python.exe"' in text
    assert 'FAIL_CLOSED: CURRENT pointer missing' in text
    assert 'FAIL_CLOSED: release python missing' in text
    assert 'JARVIS_RELEASE_SHA=%SHA%' in text
    assert '"%PY%" -m jarvis_foundry.cli.main %*' in text
    assert "C:\\Python313\\python.exe" not in text
    assert "%APPDATA%\\Python\\Python313\\Scripts\\jarvis.exe" not in text
    assert "where py" not in text
