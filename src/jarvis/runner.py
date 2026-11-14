"""The task lifecycle (one task) and the queue loops (`jarvis run`, `jarvis serve`)."""

from __future__ import annotations

import json
import os
import re
import socket
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, github, notes, notify, proc, prompt, repo_profile, stop, verify, worktree
from .agents import AgentRequest, AgentResult, make_runner
from .config import Config, RepoConfig
from .store import (
    ACTIVE,
    AWAITING_APPROVAL,
    DONE,
    FAILED,
    PLANNING,
    QUEUED,
    REJECTED,
    STOPPED,
    Store,
    now,
)


@dataclass
class Job:
    task: dict
    repo: RepoConfig
    started: float = field(default_factory=time.monotonic)
    wt: Path | None = None
    branch: str = ""
    base: str = ""
    base_sha: str = ""
    profile: repo_profile.Profile | None = None
    summary: str = ""
    model: str = ""
    cost_note_logged: bool = False
    baseline: list[verify.CommandResult] = field(default_factory=list)

    @property
    def id(self) -> int:
        return self.task["id"]


class CapReached(Exception):
    pass


def money(cost: float, complete: bool) -> str:
    if complete:
        return f"${cost:.2f}"
    return "NOT REPORTED" if cost == 0 else f"NOT REPORTED (reported part: ${cost:.2f})"


def needs_plan(cfg: Config, title: str, body: str, forced: bool = False) -> bool:
    words = len(f"{title} {body}".split())
    return forced or (cfg.plan_over_words > 0 and words > cfg.plan_over_words)


class Runner:
    def __init__(self, cfg: Config, store: Store | None = None, agent=None):
        self.cfg = cfg
        self.store = store or Store(cfg.db_path)
        self.agent = agent or make_runner(cfg)
        self.owner = f"{socket.gethostname()}:{os.getpid()}"
        self.shutdown = threading.Event()

    def stopping(self) -> bool:
        return self.shutdown.is_set() or stop.active(self.cfg.home)

    def log(self, task_id: int | None, message: str) -> None:
        self.store.log(task_id, proc.redact(message))

    # ------------------------------------------------------------ queue loops
    def recover(self) -> list[int]:
        """Re-queue tasks left PLANNING/RUNNING by a process that no longer exists."""
        host, recovered = socket.gethostname(), []
        for task in self.store.tasks(ACTIVE):
            owner_host, _, pid = (task["owner"] or "").rpartition(":")
            if owner_host == host and pid.isdigit() and int(pid) != os.getpid() \
                    and proc.pid_alive(int(pid)):
                continue
            for run in self.store.open_runs(task["id"]):  # agent orphaned by the crash
                if run["pid"] and proc.pid_alive(run["pid"]):
                    proc.kill_pid(run["pid"])
                self.store.finish_run(run["id"], outcome="crashed")
            repo = self.cfg.repos.get(task["repo"])
            if repo and task["worktree"]:
                self._discard(repo, Path(task["worktree"]), task["branch"], task["base_sha"])
            self.store.update(task["id"], status=QUEUED, owner=None, worktree=None,
                              reason=None)
            self.log(task["id"], f"recovered: owner {task['owner']} is gone; re-queued")
            recovered.append(task["id"])
        return recovered

    def run_queue(self) -> int:
        """Foreground: process queued tasks one by one until the queue is empty."""
        self.recover()
        count = 0
        while not self.stopping():
            task = self.store.claim(self.owner)
            if task is None:
                break
            self.process(task)
            count += 1
        return count

    def serve(self, *, intake: bool = True, once: bool = False) -> None:
        """Background loop: poll issues and PR outcomes, keep up to `concurrency` tasks
        running. With `once`, do one poll, work the queue until it is empty, and return."""
        self.recover()
        workers: dict[int, threading.Thread] = {}
        next_poll = 0.0
        while not self.shutdown.is_set():
            workers = {tid: t for tid, t in workers.items() if t.is_alive()}
            if stop.active(self.cfg.home):
                if once:
                    break
            else:
                if time.monotonic() >= next_poll:
                    if intake:
                        self.intake()
                    self.sync_prs()
                    next_poll = time.monotonic() + self.cfg.poll_interval_s
                while len(workers) < max(1, self.cfg.concurrency):
                    task = self.store.claim(self.owner)
                    if task is None:
                        break
                    thread = threading.Thread(target=self._thread, args=(task,), daemon=True)
                    thread.start()
                    workers[task["id"]] = thread
                if once and not workers:
                    break
            self.shutdown.wait(0.2 if once else 1.0)
        for thread in workers.values():
            thread.join(timeout=60)

    def _thread(self, task) -> None:
        try:
            self.process(task)
        finally:
            self.store.close()

    def intake(self) -> list[int]:
        """Create tasks from open GitHub issues labeled for JARVIS."""
        created = []
        for repo in self.cfg.repos.values():
            if not repo.github:
                continue
            try:
                issues = github.labeled_issues(repo.github, repo.label)
            except github.GitHubError as exc:
                self.log(None, f"issue intake for {repo.name} failed: {exc}")
                continue
            for issue in issues:
                if self.store.task_for_issue(repo.name, issue["number"]):
                    continue
                body = f"{(issue.get('body') or '').strip()}\n\nGitHub issue: {issue['url']}"
                task_id = self.store.add_task(
                    repo.name, issue["title"], body, issue=issue["number"],
                    needs_plan=needs_plan(self.cfg, issue["title"], body), scope=repo.scope)
                self.log(task_id, f"created from {repo.github}#{issue['number']}")
                created.append(task_id)
            self.store.set_last_poll(repo.name)
        return created

    def sync_prs(self) -> int:
        """Record merged/closed outcomes of JARVIS pull requests; learn from reviews."""
        changed = 0
        for task in self.store.open_prs():
            repo = self.cfg.repos.get(task["repo"])
            if repo is None or not repo.github:
                continue
            try:
                state, feedback = github.pr_outcome(task["pr_url"])
            except github.GitHubError as exc:
                self.log(task["id"], f"pull request sync failed: {exc}")
                continue
            if state == (task["pr_state"] or "open"):
                continue
            self.store.update(task["id"], pr_state=state)
            self.log(task["id"], f"pull request {state}")
            changed += 1
            if state != "open" and feedback:
                self.learn(task, state, feedback)
        return changed

    def learn(self, task, state: str, feedback: list[str]) -> list[int]:
        """Turn review feedback on a merged or closed PR into proposed repo notes."""
        run_id = self.store.add_run(task["id"], "lessons", 0, self.agent.name)
        cap = min(1.0, self.cfg.max_cost_usd) if self.cfg.max_cost_usd else None
        with tempfile.TemporaryDirectory(prefix="jarvis-lessons-") as tmp:
            result = self.agent.run(AgentRequest(
                prompt.lessons_prompt(task, state, feedback), Path(tmp),
                min(600, self.cfg.agent_timeout_s), "lessons", 0, True, cap), self.stopping)
        self.store.finish_run(run_id, model=result.model, outcome="ok" if result.ok else "error",
                              exit_code=result.exit_code, cost_usd=result.cost_usd,
                              duration_s=round(result.duration_s, 1))
        ids = notes.propose(self.store, self.cfg.home, task["repo"], task["id"],
                            result.output) if result.ok else []
        self.log(task["id"], f"lessons from the {state} PR: {len(ids)} proposed"
                             + ("" if result.ok else f" (agent error: {result.error})"))
        return ids

    # ------------------------------------------------------------ one task
    def process(self, task) -> str:
        job = Job(dict(task), self.cfg.repos.get(task["repo"]))
        try:
            if job.repo is None:
                return self.finish(job, FAILED, f"repo {task['repo']!r} is not registered")
            return self._process(job)
        except Exception as exc:  # noqa: BLE001 - every task ends in an honest state
            self.log(job.id, "internal error:\n" + traceback.format_exc()[-2000:])
            return self.finish(job, FAILED, f"internal error: {exc}")

    def _process(self, job: Job) -> str:
        repo, task = job.repo, job.task
        job.base = repo.base or worktree.default_branch(repo.path, repo.remote)
        ref = worktree.base_ref(repo.path, repo.remote, job.base, fetch=bool(repo.github))
        job.wt = self.cfg.home / "worktrees" / re.sub(r"[^\w.-]", "_", repo.name) / str(job.id)
        job.branch, job.base_sha = worktree.create(
            repo.path, job.wt, f"{repo.branch_prefix}{job.id}", ref)
        self.store.update(job.id, branch=job.branch, base_sha=job.base_sha, worktree=str(job.wt))
        self.log(job.id, f"worktree {job.wt} on {job.branch} from {ref} ({job.base_sha[:10]})")
        job.profile = repo_profile.resolve(repo, job.wt)
        if job.profile.setup:
            res = verify.run_command("setup", job.profile.setup, job.wt,
                                     self.cfg.verify_timeout_s, self.stopping)
            if self.stopping():
                return self.finish(job, STOPPED, "stopped during setup")
            if not res.ok:
                return self.finish(job, FAILED, f"setup command failed: {res.command}\n"
                                                f"{res.output[-1500:]}")
        if task["needs_plan"] and not task["plan_approved"]:
            return self.plan(job)
        return self.build(job)

    def call(self, job: Job, kind: str, attempt: int, text: str, read_only: bool) -> AgentResult:
        elapsed = time.monotonic() - job.started
        remaining = self.cfg.task_timeout_s - elapsed
        if remaining <= 1:
            raise CapReached(f"task time cap of {self.cfg.task_timeout_s}s reached")
        spent, complete = self.store.cost(job.id)
        cap = self.cfg.max_cost_usd
        if cap and spent >= cap:
            raise CapReached(f"cost cap ${cap:.2f} reached (spent ${spent:.2f})")
        timeout = min(self.cfg.agent_timeout_s, remaining)
        run_id = self.store.add_run(job.id, kind, attempt, self.agent.name)
        self.log(job.id, f"{kind} call {attempt} started ({self.agent.name}, "
                         f"timeout {timeout:.0f}s)")
        request = AgentRequest(text, job.wt, timeout, kind, attempt, read_only,
                               (cap - spent) if cap else None,
                               lambda pid: self.store.set_run_pid(run_id, pid))
        result = self.agent.run(request, self.stopping)
        if result.timed_out and timeout < self.cfg.agent_timeout_s:
            result.error = f"task time cap of {self.cfg.task_timeout_s}s reached"
        transcript = self.cfg.home / "runs" / str(job.id) / f"{run_id}-{kind}.log"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(proc.redact(result.transcript or result.output), encoding="utf-8")
        outcome = ("stopped" if result.stopped else "timeout" if result.timed_out
                   else "ok" if result.ok else "error")
        self.store.finish_run(run_id, model=result.model, outcome=outcome,
                              exit_code=result.exit_code, cost_usd=result.cost_usd,
                              duration_s=round(result.duration_s, 1), transcript=str(transcript))
        if result.cost_usd is None and not job.cost_note_logged:
            job.cost_note_logged = True
            self.log(job.id, f"{self.agent.name} does not report cost: NOT REPORTED")
        if read_only:  # plan/review calls never change the worktree
            worktree.git(job.wt, "reset", "--hard", "--quiet", check=False)
            worktree.git(job.wt, "clean", "-fdq", check=False)
        self.log(job.id, f"{kind} call {attempt} {outcome} in {result.duration_s:.0f}s"
                         + (f": {result.error}" if result.error else ""))
        return result

    def _agent_failure(self, job: Job, result: AgentResult) -> str:
        if result.stopped:
            return self.finish(job, STOPPED, "stopped by STOP")
        if result.timed_out and "cap" in result.error:
            return self.finish(job, FAILED, result.error)
        if result.timed_out:
            return self.finish(job, FAILED, f"agent timed out after {result.duration_s:.0f}s")
        return self.finish(job, FAILED, f"agent error: {result.error}")

    def plan(self, job: Job) -> str:
        self.store.update(job.id, status=PLANNING)
        try:
            result = self.call(job, "plan", 0, prompt.plan_prompt(
                job.task, notes=notes.read(self.cfg.home, job.repo.name),
                commands=job.profile.commands()), read_only=True)
        except CapReached as exc:
            return self.finish(job, FAILED, str(exc))
        if not result.ok:
            return self._agent_failure(job, result)
        self._discard(job.repo, job.wt, job.branch, job.base_sha)
        self.store.update(job.id, status=AWAITING_APPROVAL, plan=result.output, owner=None,
                          worktree=None)
        self.log(job.id, "plan ready; waiting for `jarvis approve`")
        self._notify(job, "plan_ready", AWAITING_APPROVAL, "")
        return AWAITING_APPROVAL

    def build(self, job: Job) -> str:
        repo, cfg = job.repo, self.cfg
        allowed = list(json.loads(job.task["scope"] or "[]")) or repo.scope
        failure, verdict = "", None
        if cfg.baseline and job.profile.commands():
            job.baseline = verify.run_all(job.profile, job.wt, cfg.verify_timeout_s,
                                          self.stopping)
            if self.stopping():
                return self.finish(job, STOPPED, "stopped during the baseline run")
            self.log(job.id, "baseline: " + ", ".join(
                f"{r.name} {'ok' if r.ok else 'FAILED'}" for r in job.baseline))
        for attempt in range(cfg.max_repairs + 1):
            text = prompt.task_prompt(
                job.task, notes=notes.read(cfg.home, repo.name), commands=job.profile.commands(),
                allowed=allowed, protected=repo.protected, plan=job.task["plan"] or "",
                failure=failure, attempt=attempt, max_repairs=cfg.max_repairs,
                baseline=job.baseline, require_tests=repo.require_tests)
            try:
                result = self.call(job, "code" if attempt == 0 else "repair", attempt, text, False)
            except CapReached as exc:
                return self.finish(job, FAILED, str(exc))
            if not result.ok:
                return self._agent_failure(job, result)
            job.summary, job.model = result.output, result.model
            verdict = verify.verify(job.wt, job.base_sha, job.profile, allowed=allowed,
                                    protected=repo.protected, timeout=cfg.verify_timeout_s,
                                    should_stop=self.stopping, require_tests=repo.require_tests)
            self.log(job.id, f"verify attempt {attempt}: {verdict.kind} "
                             + "; ".join(r.splitlines()[0] for r in verdict.reasons))
            if verdict.ok:
                return self.deliver(job, verdict, attempt)
            if verdict.kind == verify.STOPPED:
                return self.finish(job, STOPPED, "stopped by STOP")
            if verdict.kind == verify.SCOPE:
                return self.finish(job, REJECTED, "out-of-scope edit: "
                                   + "; ".join(verdict.reasons))
            if verdict.kind == verify.EMPTY:
                return self.finish(job, FAILED, "the agent made no changes: "
                                   + (result.output.strip()[:500] or "(no explanation)"))
            if verdict.kind == verify.NO_COMMANDS:
                return self.finish(job, FAILED, verdict.reasons[0])
            if attempt < cfg.max_repairs:
                self.store.update(job.id, repairs=attempt + 1)
                failure = verdict.feedback()
        return self.finish(job, FAILED, f"verification still failing after {cfg.max_repairs} "
                           f"repair attempt(s): " + "; ".join(verdict.reasons))

    def deliver(self, job: Job, verdict: verify.Verdict, repairs: int) -> str:
        repo = job.repo
        worktree.commit_all(job.wt, f"jarvis: {job.task['title'][:72]}\n\nTask #{job.id}",
                            verdict.files)
        if worktree.git(job.wt, "rev-parse", "HEAD") == job.base_sha:
            return self.finish(job, FAILED, "nothing to commit after verification")
        review = ""
        if self.cfg.review:
            try:
                result = self.call(job, "review", 0, prompt.review_prompt(
                    job.task, worktree.diff_text(job.wt, job.base_sha)), read_only=True)
                review = result.output if result.ok else f"Reviewer unavailable: {result.error}"
            except CapReached as exc:
                review = f"Reviewer skipped: {exc}"
        if self.stopping():
            return self.finish(job, STOPPED, "stopped before the pull request was opened")
        body = self.pr_body(job, verdict, review, repairs)
        has_remote = worktree.has_remote(repo.path, repo.remote)
        default = worktree.default_branch(repo.path, repo.remote)
        try:
            if repo.github or has_remote:
                github.push(job.wt, repo.remote, job.branch, {job.base, default, "main", "master"})
            url = github.open_pr(github=repo.github, home=self.cfg.home, task_id=job.id,
                                 branch=job.branch, base=job.base, title=job.task["title"],
                                 body=body, draft=repo.draft,
                                 remote=repo.remote if has_remote else None)
        except (github.GitHubError, worktree.GitError) as exc:
            return self.finish(job, FAILED, f"could not publish the branch: {exc}")
        self.store.update(job.id, pr_url=url)
        notes.propose(self.store, self.cfg.home, repo.name, job.id, job.summary)
        return self.finish(job, DONE, "")

    # ------------------------------------------------------------ endings
    def pr_body(self, job: Job, verdict: verify.Verdict, review: str, repairs: int) -> str:
        cost, complete = self.store.cost(job.id)
        minutes, seconds = divmod(int(time.monotonic() - job.started), 60)
        lines = ["## Summary", job.task["title"], ""]
        summary = notes.NOTE_LINE.sub("", job.summary).strip()  # NOTE lines are proposals
        if summary:
            lines += [proc.redact(proc.tail(summary, 3000)), ""]
        if job.task["issue"] and job.repo.github:
            lines += [f"Closes #{job.task['issue']}", ""]
        lines += ["## Changed files"] + [f"- `{f}`" for f in verdict.files[:200]]
        tests = verify.test_files(verdict.files)
        lines += ["", f"Tests changed: {', '.join(f'`{t}`' for t in tests)}" if tests
                  else "No test files changed."]
        lines += ["", "## Verification", "JARVIS ran these commands itself in the task worktree:"]
        before = {r.command: r for r in job.baseline}
        for r in verdict.results:
            was = before.get(r.command)
            note = "" if was is None else (
                f"; before the change: {'timed out' if was.timed_out else f'exit {was.code}'}")
            output = r.output.splitlines()
            shown = "\n".join((["..."] if len(output) > 40 else []) + output[-40:])
            lines += ["", f"`{r.command}` exit {r.code} ({r.duration}s){note}",
                      "<details><summary>output</summary>", "", "```",
                      shown or "(no output)", "```", "</details>"]
        if review:
            lines += ["", "## Reviewer notes (informational)", proc.redact(review.strip())]
        lines += ["", "## Run",
                  f"- Engine: {self.agent.name}, model: {job.model or 'unknown'}",
                  f"- Repairs: {repairs}",
                  f"- Cost: {money(cost, complete)}",
                  f"- Duration: {minutes}m{seconds:02d}s",
                  f"- Task #{job.id}, JARVIS {__version__}"]
        return "\n".join(lines)

    def _discard(self, repo: RepoConfig, wt: Path | None, branch: str | None,
                 base_sha: str | None) -> None:
        """Remove the worktree; delete the branch only if it holds no commits of its own."""
        if wt is not None:
            worktree.remove(repo.path, wt)
        if branch and base_sha and worktree.branch_exists(repo.path, branch):
            if worktree.git(repo.path, "rev-parse", branch, check=False) == base_sha:
                worktree.git(repo.path, "branch", "-D", branch, check=False)

    def finish(self, job: Job, status: str, reason: str) -> str:
        if status == STOPPED and not stop.active(self.cfg.home):
            status, reason = QUEUED, "interrupted by shutdown; re-queued"
        repo, wt = job.repo, job.wt
        if repo is not None and wt is not None and wt.exists():
            if status in (FAILED, REJECTED):  # keep the attempt for inspection
                try:
                    worktree.commit_all(wt, f"jarvis: unverified work for task #{job.id} "
                                            f"({status})")
                except worktree.GitError as exc:
                    self.log(job.id, f"could not commit work in progress: {exc}")
            if status in (STOPPED, QUEUED):
                self._discard(repo, wt, job.branch, job.base_sha)
            else:
                worktree.remove(repo.path, wt)
        fields = {"status": status, "reason": proc.redact(reason) or None, "owner": None}
        if status in (DONE, FAILED, REJECTED, STOPPED):
            fields["finished_at"] = now()
        if status in (STOPPED, QUEUED):
            fields["worktree"] = None
        self.store.update(job.id, **fields)
        self.log(job.id, f"{status}" + (f": {reason}" if reason else ""))
        if status == DONE:
            self._notify(job, "pr_ready", status, "")
        elif status in (FAILED, REJECTED):
            self._notify(job, "failed", status, reason)
            if job.task.get("issue") and repo is not None and repo.github:
                try:
                    github.comment(repo.github, job.task["issue"],
                                   f"JARVIS could not complete this task ({status}).\n\n"
                                   f"Reason: {proc.redact(reason)[:1500]}\n\n"
                                   f"Branch kept for inspection: `{job.branch or 'none'}`")
                except github.GitHubError as exc:
                    self.log(job.id, f"could not comment on the issue: {exc}")
        return status

    def _notify(self, job: Job, event: str, status: str, reason: str) -> None:
        task = self.store.task(job.id)
        error = notify.send(self.cfg.webhook, event, {
            "task_id": job.id, "repo": job.task["repo"], "title": job.task["title"],
            "status": status, "pr_url": task["pr_url"] if task else None,
            "reason": proc.redact(reason)[:1000]})
        if error:
            self.log(job.id, f"webhook {event} failed: {error}")
