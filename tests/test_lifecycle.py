"""Every lifecycle path of a task, driven by the fake engine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from conftest import FIXED, WRONG, git
from jarvis import proc, stop
from jarvis.agents.fake import FakeAgent
from jarvis.runner import Runner
from jarvis.store import DONE, FAILED, QUEUED, REJECTED, RUNNING, STOPPED, Store

SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_success_opens_local_pr(run_task, repo):
    main_before = git(repo.parent / "origin.git", "rev-parse", "main")
    status, task, runner, _ = run_task({"code": [{"write": {"calc.py": FIXED}, "cost": 0.12,
                                                  "output": "Fixed add().\nNOTE: use unittest"}]})
    assert status == DONE, task["reason"]
    body = Path(task["pr_url"]).read_text(encoding="utf-8")
    for expected in ("## Summary", "Fixed add().", "## Changed files", "`calc.py`",
                     "## Verification", "exit 0", "Engine: fake, model: fake-model",
                     "Cost: $0.12", "Duration:", "Repairs: 0"):
        assert expected in body
    assert "NOTE:" not in body  # lessons become proposals, not PR text
    assert [p["text"] for p in runner.store.proposals("calc")] == ["use unittest"]
    # the branch was pushed to the bare origin; its default branch did not move
    origin = repo.parent / "origin.git"
    assert git(origin, "rev-parse", "main") == main_before
    assert git(origin, "show", f"{task['branch']}:calc.py").strip() == FIXED.strip()
    assert git(origin, "rev-list", "--count", f"main..{task['branch']}") == "1"
    assert not Path(task["worktree"]).exists()  # worktree folder cleaned up
    assert git(repo, "rev-parse", "HEAD") == main_before  # the user's checkout untouched


def test_test_failure_then_repair_then_success(run_task):
    status, task, _, agent = run_task({"code": [{"write": {"calc.py": WRONG}},
                                                {"write": {"calc.py": FIXED}}]})
    assert status == DONE, task["reason"]
    assert task["repairs"] == 1
    kinds = [kind for kind, _ in agent.prompts]
    assert kinds == ["code", "repair"]
    repair_prompt = agent.prompts[1][1]
    assert "previous attempt failed verification" in repair_prompt
    assert "AssertionError" in repair_prompt or "FAILED" in repair_prompt
    assert "Repairs: 1" in Path(task["pr_url"]).read_text(encoding="utf-8")


def test_repairs_exhausted_is_failed_and_branch_kept(run_task, repo):
    status, task, runner, agent = run_task({"code": [{"write": {"calc.py": WRONG}}]})
    assert status == FAILED
    assert "after 2 repair attempt(s)" in task["reason"]
    assert len(agent.prompts) == 3
    assert task["pr_url"] is None
    # the attempt is committed on the kept branch for inspection
    assert git(repo, "show", f"{task['branch']}:calc.py").strip() == WRONG.strip()
    assert not git(repo.parent / "origin.git", "branch", "--list", task["branch"])


def test_out_of_scope_edit_is_rejected(run_task):
    script = {"code": [{"write": {"calc.py": FIXED, "README.md": "# hacked\n"}}]}
    status, task, _, agent = run_task(script, scope=["calc.py"])
    assert status == REJECTED
    assert "README.md" in task["reason"] and "outside the allowed scope" in task["reason"]
    assert len(agent.prompts) == 1  # no repair after a scope breach


def test_protected_path_is_rejected(run_task):
    script = {"code": [{"write": {"calc.py": FIXED, ".github/workflows/ci.yml": "x: 1\n"}}]}
    status, task, _, _ = run_task(script, repo={"protected": [".github/"]})
    assert status == REJECTED and "protected" in task["reason"]


def test_empty_diff_is_failed(run_task):
    status, task, _, agent = run_task({"code": [{"output": "This cannot be done."}]})
    assert status == FAILED
    assert "no changes" in task["reason"] and "cannot be done" in task["reason"]
    assert len(agent.prompts) == 1


def test_agent_timeout_is_failed(run_task):
    started = time.monotonic()
    status, task, _, _ = run_task({"code": [{"sleep": 60}]}, agent_timeout_s=1)
    assert status == FAILED and "timed out" in task["reason"]
    assert time.monotonic() - started < 15


def test_task_time_cap(run_task):
    status, task, _, _ = run_task({"code": [{"sleep": 60}]}, task_timeout_s=2)
    assert status == FAILED and "time cap" in task["reason"]


def test_cost_cap_stops_repairs(run_task):
    status, task, _, agent = run_task({"code": [{"write": {"calc.py": WRONG}, "cost": 0.6}]},
                                      max_cost_usd=1.0)
    assert status == FAILED and "cost cap $1.00 reached" in task["reason"]
    assert len(agent.prompts) == 2


def test_unreported_cost_is_not_guessed(run_task):
    status, task, _, _ = run_task({"code": [{"write": {"calc.py": FIXED}, "cost": None}]})
    assert status == DONE
    assert "Cost: NOT REPORTED" in Path(task["pr_url"]).read_text(encoding="utf-8")


def test_stop_during_run_kills_agent_and_resume_requeues(make_cfg, repo):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    runner = Runner(cfg, store, FakeAgent({"code": [{"sleep": 120}]}))
    task_id = store.add_task("calc", "Long task")
    worker = threading.Thread(target=runner.run_queue)
    worker.start()
    deadline = time.monotonic() + 20
    while not store.runs(task_id) and time.monotonic() < deadline:
        time.sleep(0.1)
    stop.activate(cfg.home, "test")
    stopped_at = time.monotonic()
    worker.join(timeout=15)
    assert not worker.is_alive()
    assert time.monotonic() - stopped_at < 10
    task = store.task(task_id)
    assert task["status"] == STOPPED
    assert store.runs(task_id)[0]["outcome"] == "stopped"
    assert not git(repo, "branch", "--list", task["branch"])  # empty branch discarded
    # RESUME continues the queue
    stop.clear(cfg.home)
    assert store.requeue([STOPPED]) == 1
    runner.agent = FakeAgent({"code": [{"write": {"calc.py": FIXED}}]})
    assert runner.run_queue() == 1
    assert store.task(task_id)["status"] == DONE


def test_crashed_running_task_is_recovered(make_cfg, tmp_path):
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"code": [{"sleep": 120}]}))
    cfg = make_cfg()  # the child process reads the config file, so put the engine there
    text = cfg.path.read_text(encoding="utf-8").replace(
        'name = "claude_code"', f'name = "fake"\nscript = {json.dumps(str(script))}')
    cfg.path.write_text(text, encoding="utf-8")
    store = Store(cfg.db_path)
    task_id = store.add_task("calc", "Fix add()")
    env = dict(os.environ, PYTHONPATH=SRC)
    child = subprocess.Popen([sys.executable, "-m", "jarvis", "run"], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (
            store.runs(task_id) and store.runs(task_id)[0]["pid"]):
        time.sleep(0.1)
    child.kill()  # simulate a crash: no cleanup runs
    child.wait(timeout=10)
    assert store.task(task_id)["status"] == RUNNING
    orphan = store.runs(task_id)[0]["pid"]
    assert orphan and proc.pid_alive(orphan)  # the agent outlived its crashed runner
    script.write_text(json.dumps({"code": [{"write": {"calc.py": FIXED}}]}))
    runner = Runner(cfg, store, FakeAgent.from_file(str(script)))
    assert runner.recover() == [task_id]
    assert store.task(task_id)["status"] == QUEUED
    deadline = time.monotonic() + 10
    while proc.pid_alive(orphan) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not proc.pid_alive(orphan)  # recovery killed the orphaned agent
    assert store.runs(task_id)[0]["outcome"] == "crashed"
    runner.run_queue()
    task = store.task(task_id)
    assert task["status"] == DONE, task["reason"]
    assert any("recovered" in row["message"] for row in store.logs(task_id))


def test_two_tasks_on_same_repo_do_not_collide(make_cfg, repo):
    cfg = make_cfg(concurrency=2)
    store = Store(cfg.db_path)
    agent = FakeAgent({"code": [{"sleep": 1, "write": {"calc.py": FIXED}}]})
    runner = Runner(cfg, store, agent)
    ids = [store.add_task("calc", "Fix add() A"), store.add_task("calc", "Fix add() B")]
    serve = threading.Thread(target=runner.serve, kwargs={"intake": False})
    serve.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and any(
            store.task(i)["status"] not in (DONE, FAILED) for i in ids):
        time.sleep(0.2)
    runner.shutdown.set()
    serve.join(timeout=30)
    tasks = [store.task(i) for i in ids]
    assert [t["status"] for t in tasks] == [DONE, DONE], [t["reason"] for t in tasks]
    assert tasks[0]["branch"] != tasks[1]["branch"]
    for t in tasks:
        assert git(repo, "rev-list", "--count", f"main..{t['branch']}") == "1"


def test_shutdown_requeues_inflight_task(make_cfg):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    runner = Runner(cfg, store, FakeAgent({"code": [{"sleep": 120}]}))
    task_id = store.add_task("calc", "Long task")
    serve = threading.Thread(target=runner.serve, kwargs={"intake": False})
    serve.start()
    deadline = time.monotonic() + 20
    while not store.runs(task_id) and time.monotonic() < deadline:
        time.sleep(0.1)
    runner.shutdown.set()
    serve.join(timeout=20)
    assert not serve.is_alive()
    task = store.task(task_id)
    assert task["status"] == QUEUED and "shutdown" in task["reason"]
