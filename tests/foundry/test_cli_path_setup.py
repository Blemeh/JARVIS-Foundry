"""Global ``jarvis`` command installation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
import venv
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
from jarvis_foundry.migrations import SCHEMA_VERSION


def _create_synthetic_current_release(root: Path) -> tuple[Path, str]:
    source_repo = Path(__file__).resolve().parents[2]
    source_sha = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True, timeout=20,
    ).stdout.strip()
    git_tree = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", f"{source_sha}^{{tree}}"],
        capture_output=True, text=True, check=True, timeout=20,
    ).stdout.strip()
    release = root / "releases" / source_sha
    snapshot = release / "source"
    snapshot.mkdir(parents=True)
    shutil.copytree(source_repo / "src", snapshot / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(source_repo / "packaging", snapshot / "packaging")
    shutil.copy2(source_repo / "pyproject.toml", snapshot / "pyproject.toml")
    package_assets = snapshot / "src" / "jarvis_foundry" / "assets" / "packaging"
    package_assets.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_repo / "packaging", package_assets)

    environment = release / ".venv"
    venv.EnvBuilder(system_site_packages=True).create(str(environment))
    active_site_packages = {
        str(Path(entry).resolve()) for entry in sys.path
        if entry and Path(entry).name.casefold() in {
            "site-packages", "dist-packages",
        }
    }
    if active_site_packages:
        dependency_paths = (
            environment / "Lib" / "site-packages" / "test-runtime-deps.pth")
        dependency_paths.write_text(
            "\n".join(sorted(active_site_packages)) + "\n", encoding="utf-8")
    manifest = {
        "release_id": f"rel-{source_sha[:12]}-test0001",
        "source_sha": source_sha,
        "git_commit": source_sha,
        "git_tree": git_tree,
        "schema_compatibility": {"package_schema": SCHEMA_VERSION},
        "provenance": {
            "source_repo": str(source_repo),
            "git_commit": source_sha,
            "git_tree": git_tree,
        },
        "previous_runtime": None,
        "web_runtime": None,
        "command_center_bundle": None,
    }
    (release / "RELEASE.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "CURRENT").write_text(source_sha + "\n", encoding="ascii")
    return release, source_sha


def _run_launcher(launcher: Path, arguments: list[str],
                  environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    invocation = launcher.parent / f".invoke-{uuid.uuid4().hex}.cmd"
    command = 'call "' + str(launcher) + '" ' + " ".join(
        '"' + argument + '"' for argument in arguments)
    invocation.write_text(
        "@echo off\r\n" + command + "\r\nexit /b %ERRORLEVEL%\r\n",
        encoding="utf-8",
    )
    try:
        return subprocess.run(
            ["cmd.exe", "/d", "/c", str(invocation)],
            capture_output=True, text=True, env=environment, timeout=60,
            check=False,
        )
    finally:
        invocation.unlink(missing_ok=True)


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


def test_cli_cmd_fails_closed_when_current_is_missing(tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(root))
    launcher = install_cli_launcher(root)

    result = _run_launcher(launcher, [], os.environ.copy())

    assert result.returncode == 2
    assert "CURRENT pointer" in result.stderr


def test_cli_cmd_uses_current_release_python_and_ignores_hostile_pythonpath(
        tmp_path, monkeypatch):
    root = tmp_path / "JARVIS"
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(root))
    launcher = install_cli_launcher(root)
    release, source_sha = _create_synthetic_current_release(root)
    home = tmp_path / "foundry-home"
    hostile = tmp_path / "hostile-pythonpath"
    hostile.mkdir()
    marker = tmp_path / "hostile-sitecustomize-ran.txt"
    (hostile / "sitecustomize.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['JARVIS_TEST_MARKER']).write_text('injected')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({
        "JARVIS_INSTALL_ROOT": str(root),
        "LOCALAPPDATA": str(local),
        "PYTHONPATH": str(hostile),
        "PYTHONHOME": str(hostile / "invalid-python-home"),
        "JARVIS_TEST_MARKER": str(marker),
    })

    actual_release_run = _run_launcher(
        launcher, ["--home", str(home), "control", "status"], environment)
    assert actual_release_run.returncode == 1
    assert "database_not_initialized" in actual_release_run.stdout
    assert not marker.exists()

    current_entry = release / "source" / "src" / "jarvis_foundry" \
        / "deployment" / "cli_launcher.py"
    wrong_interpreter = subprocess.run(
        [sys.executable, "-I", str(current_entry), "--home", str(home),
         "control", "status"],
        capture_output=True, text=True, env=environment, timeout=60,
        check=False,
    )
    assert wrong_interpreter.returncode == 2
    assert "not CURRENT release Python" in wrong_interpreter.stdout

    manifest_path = release / "RELEASE.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_sha"] = "b" * len(source_sha)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    invalid_manifest_run = _run_launcher(
        launcher, ["--home", str(home), "control", "status"], environment)
    assert invalid_manifest_run.returncode == 2
    assert "FAIL_CLOSED" in invalid_manifest_run.stdout
    assert "source_sha does not match directory" in invalid_manifest_run.stdout
    assert not marker.exists()


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
