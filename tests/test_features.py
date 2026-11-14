"""Plan-first, reviewer, notes, GitHub mode, issue intake and webhook (fake engine)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from conftest import FIXED, WRONG, git
from jarvis import cli, github, notes
from jarvis.agents.fake import FakeAgent
from jarvis.runner import Runner
from jarvis.store import AWAITING_APPROVAL, DONE, FAILED, QUEUED, Store


def test_plan_first_waits_for_approval(make_cfg, repo, capsys):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    agent = FakeAgent({"plan": {"output": "1. Change + in calc.py", "write_anyway": True,
                                "write": {"calc.py": "garbage"}},
                       "code": [{"write": {"calc.py": FIXED}}]})
    runner = Runner(cfg, store, agent)
    assert cli.main(["add", "--repo", "calc", "--plan", "Fix add()"]) == 0
    task_id = store.tasks()[0]["id"]
    assert runner.process(store.claim(runner.owner)) == AWAITING_APPROVAL
    task = store.task(task_id)
    assert task["plan"] == "1. Change + in calc.py"
    assert not git(repo, "branch", "--list", task["branch"])  # plan phase leaves nothing
    assert store.claim(runner.owner) is None  # not runnable until approved
    assert cli.main(["show", str(task_id)]) == 0
    assert "1. Change + in calc.py" in capsys.readouterr().out
    assert cli.main(["approve", str(task_id)]) == 0
    assert store.task(task_id)["status"] == QUEUED
    assert runner.process(store.claim(runner.owner)) == DONE
    assert "Approved plan (follow it)" in agent.prompts[-1][1]
    assert git(repo, "show", f"{store.task(task_id)['branch']}:calc.py").strip() == FIXED.strip()


def test_plan_reject_and_word_threshold(make_cfg):
    cfg = make_cfg()
    cfg.path.write_text(cfg.path.read_text().replace("plan_over_words = 150",
                                                     "plan_over_words = 5"))
    assert cli.main(["add", "--repo", "calc", "one two three four five six seven"]) == 0
    assert cli.main(["add", "--repo", "calc", "short task"]) == 0
    store = Store(cfg.db_path)
    big, small = store.tasks()[1], store.tasks()[0]
    assert big["needs_plan"] == 1 and small["needs_plan"] == 0
    store.update(big["id"], status=AWAITING_APPROVAL)
    assert cli.main(["approve", str(big["id"]), "--reject"]) == 0
    assert store.task(big["id"])["status"] == FAILED
    assert cli.main(["approve", str(small["id"])]) == 2  # not awaiting approval


def test_reviewer_notes_in_pr_body_and_review_is_read_only(run_task, make_cfg, repo):
    cfg = make_cfg(review=True)
    script = {"code": [{"write": {"calc.py": FIXED}}],
              "review": {"output": "- Risk: no test for negatives", "write_anyway": True,
                         "write": {"calc.py": "broken by reviewer"}}}
    status, task, _, agent = run_task(script, cfg=cfg)
    assert status == DONE
    body = Path(task["pr_url"]).read_text(encoding="utf-8")
    assert "## Reviewer notes (informational)" in body and "no test for negatives" in body
    assert [k for k, _ in agent.prompts] == ["code", "review"]
    assert "```diff" in agent.prompts[1][1] and "+    return a + b" in agent.prompts[1][1]
    assert git(repo, "show", f"{task['branch']}:calc.py").strip() == FIXED.strip()


def test_failed_review_does_not_block_pr(run_task, make_cfg):
    cfg = make_cfg(review=True)
    status, task, _, _ = run_task({"code": [{"write": {"calc.py": FIXED}}],
                                   "review": {"exit": 1}}, cfg=cfg)
    assert status == DONE
    assert "Reviewer unavailable" in Path(task["pr_url"]).read_text(encoding="utf-8")


def test_notes_proposals_accept_and_reject(run_task, capsys):
    status, task, runner, _ = run_task({"code": [{"write": {"calc.py": FIXED}, "output":
                                       "done\nNOTE: tests use unittest\nNOTE: keep calc tiny"}]})
    assert status == DONE
    home = runner.cfg.home
    assert cli.main(["notes", "calc"]) == 0
    out = capsys.readouterr().out
    assert "#1 (task 1): tests use unittest" in out and "#2" in out
    assert cli.main(["notes", "calc", "--accept", "1", "--reject", "2"]) == 0
    text = notes.read(home, "calc")
    assert "- tests use unittest" in text and "keep calc tiny" not in text
    assert runner.store.proposals("calc") == []
    # an accepted lesson is not proposed again and reaches the next prompt
    assert notes.propose(runner.store, home, "calc", 1, "NOTE: tests use unittest") == []
    status, _, _, agent = run_task({"code": [{"write": {"calc.py": FIXED}}]}, cfg=runner.cfg)
    assert "tests use unittest" in agent.prompts[0][1]


@pytest.fixture
def fake_gh(monkeypatch):
    calls: list[list[str]] = []
    issues: list[dict] = []

    def gh(*args, timeout=120):
        calls.append(list(args))
        if args[:2] == ("issue", "list"):
            return json.dumps(issues)
        if args[:2] == ("pr", "create"):
            body = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
            calls[-1].append(body)
            return "https://github.com/owner/calc/pull/42"
        return ""

    monkeypatch.setattr(github, "gh", gh)
    return calls, issues


def test_github_mode_pushes_branch_and_opens_draft_pr(run_task, repo, fake_gh):
    calls, _ = fake_gh
    origin = repo.parent / "origin.git"
    main_before = git(origin, "rev-parse", "main")
    status, task, _, _ = run_task({"code": [{"write": {"calc.py": FIXED}}]},
                                  repo={"github": "owner/calc", "draft": True})
    assert status == DONE and task["pr_url"] == "https://github.com/owner/calc/pull/42"
    create = next(c for c in calls if c[:2] == ["pr", "create"])
    for flag, value in (("--repo", "owner/calc"), ("--base", "main"),
                        ("--head", task["branch"])):
        assert create[create.index(flag) + 1] == value
    assert "--draft" in create and "## Verification" in create[-1]
    assert git(origin, "rev-parse", "main") == main_before
    assert git(origin, "rev-parse", task["branch"])


def test_push_refuses_protected_branch(repo):
    with pytest.raises(github.GitHubError):
        github.push(repo, "origin", "main", {"main"})


def test_issue_intake_links_pr_and_comments_on_failure(make_cfg, repo, fake_gh):
    calls, issues = fake_gh
    issues += [{"number": 7, "title": "Fix add()", "body": "It subtracts.",
                "url": "https://github.com/owner/calc/issues/7"},
               {"number": 8, "title": "Impossible", "body": "", "url": "u8"}]
    cfg = make_cfg(repo={"github": "owner/calc"})
    store = Store(cfg.db_path)
    runner = Runner(cfg, store, FakeAgent({"code": [{"write": {"calc.py": FIXED}}]}))
    assert len(runner.intake()) == 2
    assert runner.intake() == []  # no duplicates
    assert store.last_poll("calc")
    first = store.claim(runner.owner)
    assert runner.process(first) == DONE
    pr = next(c for c in calls if c[:2] == ["pr", "create"])
    assert "Closes #7" in pr[-1]
    runner.agent = FakeAgent({"code": [{"write": {"calc.py": WRONG}}]})
    assert runner.process(store.claim(runner.owner)) == FAILED
    comment = next(c for c in calls if c[:2] == ["issue", "comment"])
    assert comment[2] == "8" and "could not complete" in comment[comment.index("--body") + 1]


def test_webhook_events(run_task, make_cfg):
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cfg = make_cfg(webhook=f"http://127.0.0.1:{server.server_port}/hook")
        run_task({"code": [{"write": {"calc.py": FIXED}}]}, cfg=cfg)
        run_task({"code": [{"output": "nope"}]}, cfg=cfg)
    finally:
        server.shutdown()
    assert [e["event"] for e in received] == ["pr_ready", "failed"]
    assert received[0]["pr_url"] and received[1]["status"] == FAILED
    assert "no changes" in received[1]["reason"]


def test_webhook_failure_is_logged_not_fatal(run_task, make_cfg):
    cfg = make_cfg(webhook="http://127.0.0.1:9/unreachable")
    status, task, runner, _ = run_task({"code": [{"write": {"calc.py": FIXED}}]}, cfg=cfg)
    assert status == DONE
    assert any("webhook pr_ready failed" in r["message"] for r in runner.store.logs(task["id"]))


def test_setup_command_failure_fails_task(run_task):
    status, task, _, _ = run_task({"code": [{"write": {"calc.py": FIXED}}]},
                                  repo={"setup": "git definitely-not-a-command"})
    assert status == FAILED and "setup command failed" in task["reason"]


def test_no_verification_command_cannot_pass(run_task):
    status, task, _, _ = run_task({"code": [{"write": {"calc.py": FIXED}}]},
                                  repo={"test": "", "lint": ""})
    assert status == FAILED and "cannot be verified" in task["reason"]


def test_unknown_repo_fails_cleanly(make_cfg):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    runner = Runner(cfg, store, FakeAgent())
    task_id = store.add_task("ghost", "Do something")
    assert runner.process(store.claim(runner.owner)) == FAILED
    assert "not registered" in store.task(task_id)["reason"]
