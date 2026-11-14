"""Shared fixtures. Tests never touch the real HOME, git config, PATH or repos."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jarvis import config
from jarvis.agents.fake import FakeAgent
from jarvis.runner import Runner
from jarvis.store import Store

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
WRONG = "def add(a, b):\n    return a * b\n"
TEST = ("import unittest\n\nfrom calc import add\n\n\nclass AddTest(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
TEST_CMD = f'"{sys.executable}" -m unittest -q'


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point JARVIS_HOME and git's global config at temp files."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis home"))
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = Test\n\temail = test@example.com\n"
                         "[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    yield


@pytest.fixture
def repo(tmp_path) -> Path:
    """A small Python repo with a failing unit test and a bare `origin` remote."""
    work = tmp_path / "my repo"
    work.mkdir()
    (work / "calc.py").write_text(BUGGY)
    (work / "test_calc.py").write_text(TEST)
    (work / "README.md").write_text("# calc\n")
    (work / ".gitignore").write_text("__pycache__/\n")
    git(work, "init", "-q", "-b", "main")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "init")
    bare = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    git(work, "remote", "add", "origin", str(bare))
    git(work, "fetch", "-q", "origin")
    git(work, "remote", "set-head", "origin", "main")
    return work


@pytest.fixture
def make_cfg(repo):
    def _make(**overrides) -> config.Config:
        cfg, _ = config.init()
        repo_values = {"path": str(repo), "test": TEST_CMD}
        repo_values.update(overrides.pop("repo", {}))
        config.append_repo(cfg, "calc", repo_values)
        cfg = config.load(cfg.home)
        cfg.review = False
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg
    return _make


@pytest.fixture
def run_task(make_cfg):
    """Queue one task and process it with a scripted fake agent."""
    def _run(script: dict, *, title="Fix add() so the tests pass", scope=(), cfg=None, **kw):
        cfg = cfg or make_cfg(**kw)
        store = Store(cfg.db_path)
        agent = FakeAgent(script)
        runner = Runner(cfg, store, agent)
        task_id = store.add_task("calc", title, scope=scope)
        status = runner.process(store.claim(runner.owner))
        return status, store.task(task_id), runner, agent
    return _run
