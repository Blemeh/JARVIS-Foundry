"""Prompt 2 M3: immutable release identity, schema fail-closed, clone migrate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.lab_hermes import LabHermesAdapter
from jarvis_foundry.deployment.layout import current_pointer, launcher_cmd, release_dir
from jarvis_foundry.deployment.release import (
    ReleaseError,
    migrate_clone,
    rollback_current,
    set_current,
    validate_release,
    write_pointer,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime._stack import build_worker_stack
from jarvis_foundry.workers.contracts import WORKER_HERMES
from jarvis_foundry.workers.router import READY


def _fake_release(tmp_path: Path, sha: str, *, schema: int = SCHEMA_VERSION) -> Path:
    directory = tmp_path / "JARVIS" / "releases" / sha
    python_dir = directory / ".venv" / "Scripts"
    python_dir.mkdir(parents=True)
    (python_dir / "python.exe").write_text("fake", encoding="utf-8")
    manifest = {
        "release_id": f"rel-{sha[:8]}",
        "source_sha": sha,
        "git_commit": sha,
        "git_tree": "1" * 40,
        "python_version": "3.13.0",
        "package_version": "0.1.0",
        "schema_compatibility": {"package_schema": schema, "opens_min": schema, "opens_max": schema},
        "created_at": "2026-08-19T00:00:00Z",
    }
    (directory / "RELEASE.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def test_validate_release_matches_directory_and_schema(tmp_path):
    sha = "a" * 40
    _fake_release(tmp_path, sha)
    identity = validate_release(sha, root=tmp_path / "JARVIS", require_schema_match=False)
    assert identity["source_sha"] == sha
    assert Path(identity["python_exe"]).is_file()


def test_validate_release_rejects_sha_mismatch(tmp_path):
    sha = "b" * 40
    directory = _fake_release(tmp_path, sha)
    manifest = json.loads((directory / "RELEASE.json").read_text(encoding="utf-8"))
    manifest["source_sha"] = "c" * 40
    (directory / "RELEASE.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReleaseError, match="source_sha"):
        validate_release(sha, root=tmp_path / "JARVIS", require_schema_match=False)


@pytest.mark.parametrize("field", ["git_commit", "git_tree"])
def test_validate_release_requires_git_identity(tmp_path, field):
    sha = "9" * 40
    directory = _fake_release(tmp_path, sha)
    manifest = json.loads((directory / "RELEASE.json").read_text(encoding="utf-8"))
    del manifest[field]
    (directory / "RELEASE.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReleaseError, match=field):
        validate_release(sha, root=tmp_path / "JARVIS", require_schema_match=False)


def test_validate_release_rejects_commit_source_mismatch(tmp_path):
    sha = "8" * 40
    directory = _fake_release(tmp_path, sha)
    manifest = json.loads((directory / "RELEASE.json").read_text(encoding="utf-8"))
    manifest["git_commit"] = "7" * 40
    (directory / "RELEASE.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReleaseError, match="git_commit does not match source_sha"):
        validate_release(sha, root=tmp_path / "JARVIS", require_schema_match=False)


def test_schema_mismatch_fails_closed(tmp_path):
    sha = "d" * 40
    _fake_release(tmp_path, sha)
    home = tmp_path / "foundry"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize(target=33)
    with pytest.raises(ReleaseError, match="schema mismatch"):
        validate_release(sha, root=tmp_path / "JARVIS", foundry_home=home)


def test_current_previous_pointers(tmp_path):
    first, second = "e" * 40, "f" * 40
    _fake_release(tmp_path, first)
    _fake_release(tmp_path, second)
    root = tmp_path / "JARVIS"
    set_current(first, root=root)
    assert current_pointer(root).read_text(encoding="utf-8").strip() == first
    set_current(second, root=root)
    assert current_pointer(root).read_text(encoding="utf-8").strip() == second
    assert (root / "PREVIOUS").read_text(encoding="utf-8").strip() == first


def test_migrate_clone_v33_to_package_schema(tmp_path):
    source = tmp_path / "live.db"
    FoundryDatabase(source).initialize(target=33)
    clone = tmp_path / "clone.db"
    report = migrate_clone(source, clone)
    assert report["schema"] == SCHEMA_VERSION
    assert report["integrity"] == "ok"
    assert report["consistent"] is True
    cloned = FoundryDatabase(clone)
    with cloned.connect(readonly=True) as connection:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "mission_step_contracts" in names
    with FoundryDatabase(source).connect(readonly=True) as connection:
        live_names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "mission_step_contracts" not in live_names


def test_lab_scripted_hermes_does_not_use_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path / "unrelated" if False else tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    cfg = WorkerRuntimeConfig(
        enabled=True,
        lab_scripted_hermes=True,
        runtime_state_root="var/worker-runtime",
        role_signals={"builder": {"bounded": True}, "verifier": {"bounded": True}, "repair": {"recovery": True}},
    )
    stack = build_worker_stack(database, FoundryPaths(tmp_path), cfg, run_id="lab")
    assert WORKER_HERMES in stack.enabled_frameworks
    assert stack.router.readiness[WORKER_HERMES].state == READY
    assert stack.disposable_root.is_relative_to(tmp_path)
    assert not stack.disposable_root.is_relative_to(unrelated)


def test_stable_launcher_cmd_clears_pythonpath(tmp_path):
    from jarvis_foundry.deployment.layout import headless_launcher_cmd
    from jarvis_foundry.deployment.release import _install_stable_launcher
    path = _install_stable_launcher(tmp_path / "JARVIS")
    text = path.read_text(encoding="utf-8")
    assert "PYTHONPATH=" in text
    assert "jarvis_foundry.deployment.launcher" in text
    assert launcher_cmd(tmp_path / "JARVIS") == path
    headless = headless_launcher_cmd(tmp_path / "JARVIS")
    assert headless.is_file()
    assert "pythonw.exe" in headless.read_text(encoding="utf-8")


def test_rollback_swaps_current_and_previous(tmp_path):
    first, second = "1" * 40, "2" * 40
    _fake_release(tmp_path, first)
    _fake_release(tmp_path, second)
    root = tmp_path / "JARVIS"
    set_current(first, root=root)
    set_current(second, root=root)
    home = tmp_path / "foundry"
    FoundryDatabase(home / "foundry.db").initialize()
    result = rollback_current(root=root, foundry_home=home)
    assert result["current"] == first
    assert result["previous"] == second
    assert current_pointer(root).read_text(encoding="utf-8").strip() == first
    restored = rollback_current(root=root, foundry_home=home)
    assert restored["current"] == second
    assert restored["previous"] == first


def test_rollback_refuses_schema_mismatch(tmp_path):
    first, second = "3" * 40, "4" * 40
    _fake_release(tmp_path, first)
    _fake_release(tmp_path, second)
    root = tmp_path / "JARVIS"
    set_current(first, root=root)
    set_current(second, root=root)
    home = tmp_path / "foundry"
    FoundryDatabase(home / "foundry.db").initialize(target=33)
    with pytest.raises(ReleaseError, match="schema mismatch"):
        rollback_current(root=root, foundry_home=home)
    assert current_pointer(root).read_text(encoding="utf-8").strip() == second
