"""Unit tests for ``scripts/verify.py`` (the one-command health check)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def verify():
    spec = importlib.util.spec_from_file_location("jarvis_verify_script", ROOT / "scripts" / "verify.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def test_last_failure_line_prefers_the_assertion_over_trailing_noise(verify) -> None:
    output = "\n".join([
        "..F.",
        "E   assert 3 == 1",
        "FAILED tests/foundry/test_x.py::test_y - assert 3 == 1",
        "1 failed, 2 passed in 0.5s",
    ])
    assert verify._last_failure_line(output) == "FAILED tests/foundry/test_x.py::test_y - assert 3 == 1"


def test_last_failure_line_falls_back_to_last_line_and_truncates(verify) -> None:
    assert verify._last_failure_line("") == ""
    assert verify._last_failure_line("boom") == "boom"
    assert len(verify._last_failure_line("x" * 1000)) == 220


def test_discover_tests_filters_by_substring(verify) -> None:
    everything = verify.discover_tests([])
    assert everything and all(p.name.startswith("test_") for p in everything)
    narrowed = verify.discover_tests(["verify_script"])
    assert [p.name for p in narrowed] == ["test_verify_script.py"]
    assert verify.discover_tests(["definitely-not-a-test-name"]) == []


def test_hermetic_env_never_exposes_the_live_machine(verify, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OBSIDIAN_VAULT", "C:/real/vault")
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", "C:/real/install")
    env = verify.hermetic_env(tmp_path)
    assert env["LOCALAPPDATA"].startswith(str(tmp_path))
    assert Path(env["LOCALAPPDATA"]).is_dir()
    assert env["TEMP"] == env["TMP"] == str(tmp_path)
    assert "OBSIDIAN_VAULT" not in env and "JARVIS_INSTALL_ROOT" not in env
    assert env["HERMES_HOME"].startswith(str(tmp_path)) and Path(env["HERMES_HOME"]).is_dir()
    assert env["GIT_CEILING_DIRECTORIES"] == str(ROOT)


def test_inside_detects_nesting(verify, tmp_path) -> None:
    assert verify._inside(tmp_path / "a" / "b", tmp_path)
    assert not verify._inside(tmp_path.parent, tmp_path)


def test_scratch_root_is_writable_and_outside_nothing_it_should_not_be(verify) -> None:
    scratch = verify.scratch_root()
    try:
        assert scratch.is_dir()
        assert verify.writable(scratch)
    finally:
        if scratch.name.startswith("jarvis-verify-"):
            import shutil

            shutil.rmtree(scratch, ignore_errors=True)


def test_suite_cannot_touch_the_owner_windows_environment_or_hermes_profile() -> None:
    """Guards from tests/foundry/conftest.py are active for every test."""
    import os

    winreg = pytest.importorskip("winreg")
    with pytest.raises(AssertionError, match="registry"):
        winreg.SetValueEx(None, "Path", 0, winreg.REG_SZ, "x")
    from jarvis_foundry.deployment.path_setup import ensure_jarvis_bin_on_path

    assert ensure_jarvis_bin_on_path() is False
    assert "pytest" in os.environ["HERMES_HOME"].lower() or "hermes-home" in os.environ["HERMES_HOME"].lower()
    assert os.environ["LOCALAPPDATA"] != os.path.expandvars(r"%USERPROFILE%\AppData\Local")