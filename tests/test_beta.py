"""Private-beta hardening: preflight, engine-error pause, checkout guard, cancel, retry."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FIXED, TEST, WRONG, git
from jarvis import cli, config, github
from jarvis.agents.fake import FakeAgent
from jarvis.runner import Runner, preflight
from jarvis.store import CANCELLED, DONE, FAILED, QUEUED, RUNNING, Store


def _set(cfg, old, new):
    cfg.path.write_text(cfg.path.read_text().replace(old, new))


def test_preflight_missing_engine_keeps_tasks_queued(make_cfg, capsys):
    cfg = make_cfg()
    _set(cfg, 'command = ""', 'command = "definitely-not-installed-claude"')
    store = Store(cfg.db_path)
    task_id = store.add_task("calc", "Fix add()")
    assert cli.main(["run"]) == 2
    err = capsys.readouterr().err
    assert "needs `definitely-not-installed-claude` on PATH" in err and "stay queued" in err
    assert store.task(task_id)["status"] == QUEUED


def test_preflight_checks_repos_with_queued_work(make_cfg, tmp_path):
    cfg = make_cfg()
    config.append_repo(cfg, "gone", {"path": str(tmp_path / "deleted-repo")})
    cfg = config.load(cfg.home)
    store = Store(cfg.db_path)
    assert not any("gone" in p for p in preflight(cfg, store))  # no queued work there
    store.add_task("gone", "x")
    assert any("repo gone:" in p and "not a git repository" in p for p in preflight(cfg, store))


def test_preflight_requires_gh_login_for_github_repos(make_cfg, monkeypatch):
    cfg = make_cfg(repo={"github": "owner/calc"})
    store = Store(cfg.db_path)
    monkeypatch.setattr("jarvis.runner.shutil.which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(github, "logged_in", lambda: False)
    assert preflight(cfg, store) == []  # nothing queued, not serving
    assert preflight(cfg, store, serving=True) == ["`gh` is not logged in; run `gh auth login`"]
    monkeypatch.setattr(github, "logged_in", lambda: True)
    assert preflight(cfg, store, serving=True) == []


def test_engine_errors_pause_the_queue(make_cfg, capsys, tmp_path):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    runner = Runner(cfg, store, FakeAgent({"code": [{"exit": 1}]}))
    ids = [store.add_task("calc", f"task {i}") for i in range(3)]
    assert runner.run_queue() == 2
    assert [store.task(i)["status"] for i in ids] == [FAILED, FAILED, QUEUED]
    assert "agent error" in store.task(ids[0])["reason"]
    assert "queue is paused" in runner.paused
    # through the CLI, the pause is reported and the exit code says something is wrong
    script = tmp_path / "s.json"
    script.write_text(json.dumps({"code": [{"exit": 1}]}))
    _set(cfg, 'name = "claude_code"', f'name = "fake"\nscript = {json.dumps(str(script))}')
    store.add_task("calc", "task 4")
    assert cli.main(["run"]) == 1
    assert "engine error" in capsys.readouterr().err


def test_an_ordinary_failure_resets_the_engine_error_count(make_cfg):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    runner = Runner(cfg, store, FakeAgent({"code": [{"exit": 1}]}))
    store.add_task("calc", "engine error")
    runner.process(store.claim(runner.owner))
    assert runner.engine_errors == 1
    runner.agent = FakeAgent({"code": [{"write": {"calc.py": FIXED}}]})
    store.add_task("calc", "works")
    assert runner.process(store.claim(runner.owner)) == DONE
    assert runner.engine_errors == 0


def test_writes_to_the_users_checkout_are_flagged(run_task, repo):
    outside = repo / "notes-from-agent.txt"
    status, task, runner, _ = run_task({"code": [{"write": {"calc.py": FIXED},
                                                  "outside": {str(outside): "oops"}}]})
    assert status == DONE
    body = Path(task["pr_url"]).read_text(encoding="utf-8")
    assert "## Warning: your checkout changed during the agent run" in body
    assert "notes-from-agent.txt" in body
    assert any("your checkout changed" in r["message"] for r in runner.store.logs(task["id"]))
    assert outside.read_text() == "oops"  # JARVIS never deletes the user's files


def test_clean_runs_have_no_checkout_warning(run_task):
    status, task, _, _ = run_task({"code": [{"write": {"calc.py": FIXED}}]})
    assert status == DONE
    assert "Warning" not in Path(task["pr_url"]).read_text(encoding="utf-8")


def test_cancel_and_retry(make_cfg, repo, capsys):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    queued = store.add_task("calc", "never mind")
    assert cli.main(["cancel", str(queued)]) == 0
    assert store.task(queued)["status"] == CANCELLED
    runner = Runner(cfg, store, FakeAgent({"code": [{"write": {"calc.py": WRONG}}]}))
    assert store.claim(runner.owner) is None  # cancelled tasks never run
    running = store.add_task("calc", "busy")
    store.update(running, status=RUNNING)
    assert cli.main(["cancel", str(running)]) == 2
    assert "jarvis stop" in capsys.readouterr().err
    failed = store.add_task("calc", "Fix add()")
    store.update(running, status=CANCELLED)
    assert runner.process(store.claim(runner.owner)) == FAILED
    first_branch = store.task(failed)["branch"]
    assert cli.main(["retry", str(failed)]) == 0
    task = store.task(failed)
    assert task["status"] == QUEUED and task["reason"] is None and task["repairs"] == 0
    runner.agent = FakeAgent({"code": [{"write": {"calc.py": FIXED}}]})
    assert runner.process(store.claim(runner.owner)) == DONE
    task = store.task(failed)
    assert task["branch"] == f"{first_branch}-2"  # the failed attempt's branch is kept
    assert git(repo, "show", f"{first_branch}:calc.py").strip() == WRONG.strip()
    assert cli.main(["retry", str(failed)]) == 2  # DONE tasks are not retried
    assert cli.main(["retry", "999"]) == 2


CHEAT = ("import unittest\n\n\nclass AddTest(unittest.TestCase):\n"
         "    def test_nothing(self):\n        pass\n")


def test_passing_by_deleting_assertions_is_flagged(run_task):
    status, task, runner, _ = run_task({"code": [{"write": {"test_calc.py": CHEAT}}]})
    assert status == DONE  # it does pass verification: the warning is what protects you
    body = Path(task["pr_url"]).read_text(encoding="utf-8")
    assert "## Warning: tests were removed or weakened" in body
    assert "test_calc.py: 0 test(s) and 1 assertion(s) fewer than before" in body
    assert any("tests removed or weakened" in r["message"] for r in runner.store.logs(task["id"]))


def test_adding_tests_is_not_flagged(run_task):
    more = TEST + "\n    def test_zero(self):\n        self.assertEqual(add(0, 0), 0)\n"
    status, task, _, _ = run_task({"code": [{"write": {"calc.py": FIXED, "test_calc.py": more}}]})
    assert status == DONE
    assert "removed or weakened" not in Path(task["pr_url"]).read_text(encoding="utf-8")


def test_serve_survives_a_failing_poll_step(make_cfg, monkeypatch):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    runner = Runner(cfg, store, FakeAgent({"code": [{"write": {"calc.py": FIXED}}]}))
    calls = []

    def broken_intake():
        calls.append(1)
        raise ValueError("gh returned garbage")

    monkeypatch.setattr(runner, "intake", broken_intake)
    task_id = store.add_task("calc", "Fix add()")
    runner.serve(once=True)
    assert calls == [1]
    assert store.task(task_id)["status"] == DONE  # the queue still ran
    assert any("intake failed" in r["message"] for r in store.db.execute(
        "SELECT message FROM log WHERE task_id IS NULL").fetchall())
