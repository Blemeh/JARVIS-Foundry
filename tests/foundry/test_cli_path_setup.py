"""Global ``jarvis`` command installation."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.deployment.layout import cli_cmd
from jarvis_foundry.deployment.cli_launcher import (
    CliLaunchError,
    _inside,
    _parse_current_value,
)
from jarvis_foundry.deployment.path_setup import bootstrap_global_cli
from jarvis_foundry.deployment.release import (
    JARVIS_CLI_CMD_TEXT,
    JARVIS_CLI_CURRENT_SHA_VALIDATOR_TEXT,
    LAUNCHER_CMD_TEXT,
    install_cli_launcher,
)


def test_install_cli_launcher_writes_jarvis_cmd(tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    path = install_cli_launcher(root)
    assert path == cli_cmd(root)
    assert r"source\src\jarvis_foundry\deployment\cli_launcher.py" in path.read_text(
        encoding="utf-8")
    validator = root / "bin" / "validate-current.ps1"
    assert validator.read_text(encoding="utf-8") == JARVIS_CLI_CURRENT_SHA_VALIDATOR_TEXT
    python_bin = local / "Python" / "bin" / "jarvis.cmd"
    assert python_bin.is_file()


def test_bootstrap_writes_launcher(tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(
        "jarvis_foundry.deployment.path_setup.ensure_jarvis_bin_on_path",
        lambda *_a, **_k: False,
    )
    launcher = bootstrap_global_cli(root)
    assert launcher.is_file()
    assert JARVIS_CLI_CMD_TEXT.splitlines()[0] in launcher.read_text(encoding="utf-8")


def test_cli_launcher_uses_current_immutable_release_and_fails_closed():
    text = JARVIS_CLI_CMD_TEXT

    assert 'for /f "usebackq delims=" %%A in (`' in text
    assert r'-File "%JARVIS_INSTALL_ROOT%\bin\validate-current.ps1"' in text
    assert "-EncodedCommand " not in text
    assert r'set "PY=%RELEASE%\.venv\Scripts\python.exe"' in text
    assert r'set "CLI_ENTRY=%RELEASE%\source\src\jarvis_foundry\deployment\cli_launcher.py"' in text
    assert r'"%PY%" -I "%CLI_ENTRY%" %*' in text
    assert "-I -m jarvis_foundry.deployment.launcher" in LAUNCHER_CMD_TEXT
    assert r'"%PY%" -I -m jarvis_foundry.cli.main %*' in text
    assert "FAIL_CLOSED: CURRENT pointer must contain one full lowercase Git object id" in text
    assert 'FAIL_CLOSED: release python missing' in text
    assert 'JARVIS_RELEASE_SHA=%SHA%' in text
    assert "C:\\Python313\\python.exe" not in text
    assert "%APPDATA%\\Python\\Python313\\Scripts\\jarvis.exe" not in text
    assert "where py" not in text


def test_cli_cmd_accepts_valid_current_before_release_lookup(tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(root))
    launcher = install_cli_launcher(root)
    (root / "CURRENT").write_text("a" * 40 + "\n", encoding="ascii")

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(launcher)],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=20,
        check=False,
    )

    assert result.returncode == 2
    assert "FAIL_CLOSED: release python missing" in result.stderr
    assert "CURRENT pointer must contain" not in result.stderr


def test_cli_cmd_rejects_metacharacters_in_current_without_execution(tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(root))
    launcher = install_cli_launcher(root)
    marker = tmp_path / "injected marker.txt"
    malicious = f'{"a" * 40}" & echo injected > "{marker}" & rem "'
    (root / "CURRENT").write_text(malicious, encoding="utf-8")

    command = f"& '{launcher}'; exit $LASTEXITCODE"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=20,
        check=False,
    )

    assert result.returncode == 2
    assert "CURRENT pointer must contain" in result.stderr
    assert not marker.exists()


def test_current_pointer_value_must_be_one_full_lowercase_git_object_id():
    valid = "a" * 40
    assert _parse_current_value(f"{valid}\n") == valid
    for malformed in (
        "", "../outside", "A" * 40, "c" * 41,
        f"{valid}\n{'b' * 40}\n",
    ):
        with pytest.raises(CliLaunchError):
            _parse_current_value(malformed)


def test_cli_source_origin_must_remain_inside_current_release():
    release = Path("C:/JARVIS/releases/" + "a" * 40)
    assert _inside(release / "source" / "src" / "jarvis_foundry" / "cli" / "main.py", release)
    assert not _inside(Path(r"C:\temp\stale\jarvis_foundry\cli\main.py"), release)
