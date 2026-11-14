"""Baseline runs, test-change checks, PR outcome learning, serve --once, notes de-dup."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from conftest import FIXED, TEST, WRONG, git
from jarvis import cli, github, notes
from jarvis.agents.fake import FakeAgent
from jarvis.runner import Runner
from jarvis.store import DONE, Store

EXTRA_TEST = TEST + "\n    def test_negative(self):\n        self.assertEqual(add(-1, 1), 0)\n"


def test_baseline_shows_before_and_after(run_task):
    status, task, _, agent = run_task({"code": [{"write": {"calc.py": FIXED}}]})
    assert status == DONE
    prompt = agent.prompts[0][1]
    assert "## The same commands on the base branch, before your change" in prompt
    assert "FAILED (exit code 1)" in prompt and "AssertionError" in prompt
    body = Path(task["pr_url"]).read_text(encoding="utf-8")
    assert "exit 0" in body and "before the change: exit 1" in body
    assert "No test files changed." in body


def test_baseline_can_be_turned_off(run_task):
    status, task, _, agent = run_task({"code": [{"write": {"calc.py": FIXED}}]},
                                      baseline=False)
    assert status == DONE
    assert "before your change" not in agent.prompts[0][1]
    assert "before the change" not in Path(task["pr_url"]).read_text(encoding="utf-8")


def test_require_tests_forces_a_test_then_passes(run_task):
    script = {"code": [{"write": {"calc.py": FIXED}},
                       {"write": {"calc.py": FIXED, "test_calc.py": EXTRA_TEST}}]}
    status, task, _, agent = run_task(script, repo={"require_tests": True})
    assert status == DONE, task["reason"]
    assert task["repairs"] == 1
    assert "requires every change to add or update a test" in agent.prompts[0][1]
    assert "No test file was added or changed" in agent.prompts[1][1]
    assert "Tests changed: `test_calc.py`" in Path(task["pr_url"]).read_text(encoding="utf-8")


def test_require_tests_exhausted_fails(run_task):
    status, task, _, _ = run_task({"code": [{"write": {"calc.py": FIXED}}]},
                                  repo={"require_tests": True})
    assert status == "FAILED" and "No test file was added or changed" in task["reason"]


@pytest.fixture
def gh_prs(monkeypatch):
    """Stub gh: PR creation returns sequential URLs; `pr view` returns `states[url]`."""
    states: dict[str, dict] = {}
    created: list[str] = []

    def gh(*args, timeout=120):
        if args[:2] == ("pr", "create"):
            created.append(f"https://github.com/owner/calc/pull/{len(created) + 1}")
            return created[-1]
        if args[:2] == ("pr", "view"):
            return json.dumps(states.get(args[2], {"state": "OPEN"}))
        if args[:2] == ("issue", "list"):
            return "[]"
        return ""

    monkeypatch.setattr(github, "gh", gh)
    return states


def test_pr_outcomes_are_tracked_and_reviews_become_lessons(make_cfg, gh_prs, capsys):
    cfg = make_cfg(repo={"github": "owner/calc"})
    store = Store(cfg.db_path)
    agent = FakeAgent({"code": [{"write": {"calc.py": FIXED}}],
                       "lessons": {"output": "NOTE: keep functions pure in calc.py"}})
    runner = Runner(cfg, store, agent)
    first, second = store.add_task("calc", "Fix add() A"), store.add_task("calc", "Fix add() B")
    for _ in range(2):
        assert runner.process(store.claim(runner.owner)) == DONE
    assert runner.sync_prs() == 0  # both still open
    gh_prs[store.task(first)["pr_url"]] = {
        "state": "CLOSED", "comments": [{"body": "Please keep functions pure."}], "reviews": []}
    gh_prs[store.task(second)["pr_url"]] = {"state": "MERGED", "comments": [], "reviews": []}
    assert runner.sync_prs() == 2
    assert store.task(first)["pr_state"] == "closed"
    assert store.task(second)["pr_state"] == "merged"
    lessons = [p for kind, p in agent.prompts if kind == "lessons"]
    assert len(lessons) == 1  # only the PR with feedback is distilled
    assert "closed without being merged" in lessons[0] and "keep functions pure" in lessons[0]
    assert [p["text"] for p in store.proposals("calc")] == ["keep functions pure in calc.py"]
    assert runner.sync_prs() == 0  # final states are not polled again
    assert cli.main(["ls"]) == 0
    out = capsys.readouterr().out
    assert "DONE/closed" in out and "DONE/merged" in out
    assert "GitHub pull requests: 2 opened, 1 merged (50%), 1 closed without merge, 0 open" in out


def test_serve_once_polls_works_queue_and_exits(make_cfg, gh_prs):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    runner = Runner(cfg, store, FakeAgent({"code": [{"write": {"calc.py": FIXED}}]}))
    ids = [store.add_task("calc", "Fix add() A"), store.add_task("calc", "Fix add() B")]
    thread = threading.Thread(target=runner.serve, kwargs={"once": True})
    thread.start()
    thread.join(timeout=60)
    assert not thread.is_alive()
    assert [store.task(i)["status"] for i in ids] == [DONE, DONE]


def test_notes_skip_near_duplicates_and_rejected(make_cfg):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    first = notes.propose(store, cfg.home, "calc", 1,
                          "NOTE: Tests live in `tests/` and import from the `shop` package.")
    assert len(first) == 1
    again = notes.propose(store, cfg.home, "calc", 2,
                          "NOTE: Tests live in tests/ and import from the shop package\n"
                          "NOTE: Run the linter with ruff before committing")
    assert [store.proposals("calc")[-1]["text"]] == ["Run the linter with ruff before committing"]
    assert len(again) == 1
    store.set_proposal(again[0], "rejected")
    assert notes.propose(store, cfg.home, "calc", 3,
                         "NOTE: run the linter with ruff before committing.") == []


def test_repo_add_detects_commands_from_the_base_branch(repo, capsys):
    git(repo, "checkout", "-q", "-b", "frontend")
    (repo / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "frontend")
    assert cli.main(["init"]) == 0
    assert cli.main(["repo", "add", str(repo), "--name", "calc", "--label", "bot",
                     "--require-tests"]) == 0
    out = capsys.readouterr().out
    assert "commands detected from origin/main" in out
    assert "test: npm test" not in out and "-m unittest" in out
    from jarvis import config

    repo_cfg = config.load().repos["calc"]
    assert repo_cfg.label == "bot" and repo_cfg.require_tests is True


def test_wrong_fix_shows_baseline_in_repair_prompt(run_task):
    _, _, _, agent = run_task({"code": [{"write": {"calc.py": WRONG}},
                                        {"write": {"calc.py": FIXED}}]})
    repair = agent.prompts[1][1]
    assert "before your change" in repair and "previous attempt failed" in repair
