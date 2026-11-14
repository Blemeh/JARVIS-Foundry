"""Command-line interface."""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import time
from pathlib import Path

from . import __version__, config, notes, repo_profile, stop, worktree
from .store import ACTIVE, AWAITING_APPROVAL, FAILED, QUEUED, STOPPED, Store, now


def _ctx() -> tuple[config.Config, Store]:
    cfg = config.load()
    return cfg, Store(cfg.db_path)


def _short(text: str | None, width: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _cost(store: Store, task_id: int) -> str:
    cost, complete = store.cost(task_id)
    if not store.runs(task_id):
        return "-"
    return f"${cost:.2f}" if complete else "n/r"


def dev_checkout() -> bool:
    """True when this package is imported from a source checkout, not an install."""
    pkg = Path(__file__).resolve().parent
    return pkg.parent.name == "src" and (pkg.parent.parent / "pyproject.toml").exists()


# ------------------------------------------------------------------ commands
def cmd_init(args) -> int:
    cfg, created = config.init()
    Store(cfg.db_path)
    print(f"{'Created' if created else 'Found'} {cfg.path}")
    engine = {"claude_code": "claude", "codex": "codex", "hermes": "hermes"}.get(cfg.engine)
    if engine and not shutil.which(cfg.command or engine):
        print(f"warning: engine {cfg.engine!r} needs the `{engine}` CLI on PATH")
    if not shutil.which("git"):
        print("warning: git is not on PATH")
    print("Next: jarvis repo add <path>")
    return 0


def cmd_repo_add(args) -> int:
    cfg = config.load()
    path = worktree.toplevel(Path(args.path).expanduser().resolve())
    name = args.name or path.name
    values: dict = {"path": str(path)}
    for key in ("github", "base", "setup", "test", "lint", "build", "label"):
        if getattr(args, key) is not None:
            values[key] = getattr(args, key)
    if args.scope:
        values["scope"] = [s.strip() for s in args.scope.split(",") if s.strip()]
    if args.draft:
        values["draft"] = True
    if args.branch_prefix:
        values["branch_prefix"] = args.branch_prefix
    if args.require_tests:
        values["require_tests"] = True
    config.append_repo(cfg, name, values)
    cfg = config.load(cfg.home)
    repo = cfg.repos[name]
    base = repo.base or worktree.default_branch(path, repo.remote)
    ref = worktree.base_ref(path, repo.remote, base, fetch=False)
    try:  # tasks start from the base branch, so detect its commands, not the checkout's
        profile = repo_profile.resolve(repo, ref=ref)
    except worktree.GitError:
        ref, profile = "the current checkout", repo_profile.resolve(repo)
    notes_file = notes.ensure(cfg.home, name, profile)
    print(f"Added repo {name!r}: {path}")
    mode = f"GitHub {args.github}" if args.github else "local (PR body written to a file)"
    print(f"  mode: {mode}")
    print(f"  commands detected from {ref}:")
    for key in ("setup", "test", "lint", "build"):
        print(f"  {key}: {getattr(profile, key) or '(none)'}")
    if not profile.test and not profile.lint:
        print("warning: no test or lint command found; tasks cannot pass verification until "
              f"you set one (jarvis repo add ... --test CMD, or edit {cfg.path})")
    print(f"  notes: {notes_file}")
    return 0


def cmd_repo_ls(args) -> int:
    cfg = config.load()
    if not cfg.repos:
        print("No repos yet. Add one with: jarvis repo add <path>")
    for repo in cfg.repos.values():
        print(f"{repo.name:16} {repo.github or 'local':28} {repo.path}")
    return 0


def cmd_add(args) -> int:
    from .runner import needs_plan

    cfg, store = _ctx()
    if args.repo not in cfg.repos:
        print(f"error: unknown repo {args.repo!r}; registered: {', '.join(cfg.repos) or 'none'}",
              file=sys.stderr)
        return 2
    title, _, body = args.task.strip().partition("\n")
    body = (body + ("\n" + Path(args.body_file).read_text(encoding="utf-8")
                    if args.body_file else "")).strip()
    scope = [s.strip() for s in (args.scope or "").split(",") if s.strip()]
    plan = needs_plan(cfg, title, body, args.plan)
    task_id = store.add_task(args.repo, title.strip(), body, scope=scope, needs_plan=plan)
    print(f"Task {task_id} queued for {args.repo}" + (" (plan first)" if plan else ""))
    return 0


def cmd_ls(args) -> int:
    _, store = _ctx()
    rows = store.tasks([args.status.upper()] if args.status else None, limit=args.limit)
    if not rows:
        print("No tasks.")
        return 0
    print(f"{'ID':>4}  {'STATUS':17} {'REPO':12} {'TITLE':40} {'COST':>7}  RESULT")
    for t in rows:
        result = t["pr_url"] or t["reason"] or ""
        status = f"{t['status']}/{t['pr_state']}" if t["pr_state"] in ("merged", "closed") \
            else t["status"]
        print(f"{t['id']:>4}  {status:17} {_short(t['repo'], 12):12} "
              f"{_short(t['title'], 40):40} {_cost(store, t['id']):>7}  {_short(result, 70)}")
    counts = store.pr_counts()
    total = sum(counts.values())
    if total:
        merged = counts.get("merged", 0)
        print(f"\nGitHub pull requests: {total} opened, {merged} merged "
              f"({100 * merged // total}%), {counts.get('closed', 0)} closed without merge, "
              f"{counts.get('open', 0)} open. Outcomes update while `jarvis serve` runs.")
    return 0


def cmd_show(args) -> int:
    from .runner import money

    _, store = _ctx()
    t = store.task(args.id)
    if t is None:
        print(f"error: no task {args.id}", file=sys.stderr)
        return 2
    for key in ("id", "repo", "status", "title", "branch", "base_sha", "pr_url", "reason",
                "repairs", "issue", "created_at", "started_at", "finished_at"):
        if t[key] not in (None, ""):
            print(f"{key:12} {t[key]}")
    print(f"{'cost':12} {money(*store.cost(t['id'])) if store.runs(t['id']) else '-'}")
    if t["body"]:
        print("\n" + t["body"])
    if t["plan"]:
        print("\n--- plan" + (" (approved)" if t["plan_approved"] else "") + " ---\n" + t["plan"])
    runs = store.runs(t["id"])
    if runs:
        print("\n--- runs ---")
        for r in runs:
            cost = "n/r" if r["cost_usd"] is None else f"${r['cost_usd']:.2f}"
            print(f"#{r['id']} {r['kind']:7} attempt {r['attempt']} {r['outcome'] or 'running':8}"
                  f" {r['engine']}/{r['model'] or '?'} {cost} {r['duration_s'] or 0}s"
                  f"  {r['transcript'] or ''}")
    print("\n--- log ---")
    for line in store.logs(t["id"]):
        print(f"{line['ts']}  {line['message']}")
    return 0


def _install_signals(runner) -> None:
    def handler(signum, frame):
        print("\nShutting down: stopping the agent and re-queueing in-flight tasks...",
              file=sys.stderr)
        runner.shutdown.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handler)


def cmd_run(args) -> int:
    from .runner import Runner

    cfg, store = _ctx()
    if stop.active(cfg.home):
        print("STOP is active; run `jarvis resume` first.", file=sys.stderr)
        return 1
    runner = Runner(cfg, store)
    _install_signals(runner)
    count = runner.run_queue()
    print(f"Processed {count} task(s).")
    for t in store.tasks(limit=count) if count else []:
        if t["status"] == AWAITING_APPROVAL:
            print(f"  #{t['id']} plan ready: read it with `jarvis show {t['id']}`, then "
                  f"`jarvis approve {t['id']}` (or --reject)")
        else:
            print(f"  #{t['id']} {t['status']}: {t['pr_url'] or t['reason'] or ''}")
    if stop.active(cfg.home):
        print("STOP is active; the queue is paused.")
    return 0


def cmd_serve(args) -> int:
    from .runner import Runner

    if dev_checkout() and not args.allow_dev:
        print("error: `jarvis serve` runs from an installed package, not a source checkout.\n"
              "Install it (uv tool install / pipx) or pass --allow-dev for development.",
              file=sys.stderr)
        return 2
    cfg, store = _ctx()
    if args.concurrency:
        cfg.concurrency = args.concurrency
    runner = Runner(cfg, store)
    runner.echo = True
    _install_signals(runner)
    print(f"jarvis serve: {len(cfg.repos)} repo(s), concurrency {cfg.concurrency}, "
          f"engine {cfg.engine}. Ctrl+C to shut down.", flush=True)
    runner.serve(intake=not args.no_intake, once=args.once)
    print("jarvis serve: shut down cleanly.")
    return 0


def cmd_stop(args) -> int:
    cfg, store = _ctx()
    stop.activate(cfg.home, args.reason or "jarvis stop")
    print("STOP activated. Running agents are being killed; no new task will start.")
    deadline = time.monotonic() + args.wait
    while store.tasks(ACTIVE) and time.monotonic() < deadline:
        time.sleep(0.25)
    active = store.tasks(ACTIVE)
    if active:
        print(f"Still active after {args.wait}s: " + ", ".join(f"#{t['id']}" for t in active)
              + " (is a JARVIS process still running?)")
        return 1
    stopped = store.tasks([STOPPED])
    ids = ", ".join(f"#{t['id']}" for t in stopped)
    print("All work halted." + (f" Stopped: {ids}" if stopped else ""))
    return 0


def cmd_resume(args) -> int:
    cfg, store = _ctx()
    cleared = stop.clear(cfg.home)
    count = store.requeue([STOPPED])
    print(("STOP cleared. " if cleared else "STOP was not active. ")
          + f"{count} stopped task(s) re-queued. A running `jarvis serve` continues now; "
            "otherwise run `jarvis run`.")
    return 0


def cmd_approve(args) -> int:
    _, store = _ctx()
    t = store.task(args.id)
    if t is None or t["status"] != AWAITING_APPROVAL:
        print(f"error: task {args.id} is not awaiting approval", file=sys.stderr)
        return 2
    if args.reject:
        store.update(args.id, status=FAILED, reason="plan rejected by owner", finished_at=now())
        print(f"Task {args.id} plan rejected.")
    else:
        store.update(args.id, status=QUEUED, plan_approved=1)
        print(f"Task {args.id} plan approved and queued.")
    store.log(args.id, "plan rejected" if args.reject else "plan approved")
    return 0


def cmd_notes(args) -> int:
    cfg, store = _ctx()
    if args.repo not in cfg.repos:
        print(f"error: unknown repo {args.repo!r}", file=sys.stderr)
        return 2
    if args.accept or args.reject:
        for pid in args.accept or []:
            row = notes.accept(store, cfg.home, pid)
            print(f"accepted #{pid}" if row else f"no proposal #{pid}")
        for pid in args.reject or []:
            row = store.set_proposal(pid, "rejected")
            print(f"rejected #{pid}" if row else f"no proposal #{pid}")
        return 0
    print(f"Notes file: {notes.path(cfg.home, args.repo)}\n")
    print(notes.read(cfg.home, args.repo) or "(empty)")
    pending = store.proposals(args.repo)
    print("Proposed lessons:" if pending else "No proposed lessons.")
    for row in pending:
        print(f"  #{row['id']} (task {row['task_id']}): {row['text']}")
    if pending:
        print("Accept or reject with: jarvis notes REPO --accept ID / --reject ID")
    return 0


# ------------------------------------------------------------------ parser
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis", description="Turn a coding task into a tested "
                                "pull request. Never merges, never pushes to a default branch.")
    p.add_argument("--version", action="version", version=f"jarvis {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create $JARVIS_HOME (~/.jarvis) and config.toml"
                   ).set_defaults(func=cmd_init)

    repo = sub.add_parser("repo", help="register repositories").add_subparsers(
        dest="repo_command", required=True)
    add = repo.add_parser("add", help="register a git repository")
    add.add_argument("path")
    add.add_argument("--name")
    add.add_argument("--github", help="owner/name: push and open real pull requests with gh")
    add.add_argument("--base", help="base branch (default: the repo's default branch)")
    for key in ("setup", "test", "lint", "build"):
        add.add_argument(f"--{key}", help=f"{key} command (default: detected)")
    add.add_argument("--scope", help="comma-separated allowed paths (default: whole repo)")
    add.add_argument("--draft", action="store_true", help="open pull requests as drafts")
    add.add_argument("--branch-prefix", help="task branch prefix (default: jarvis/)")
    add.add_argument("--label", help="issue label for `jarvis serve` intake (default: jarvis)")
    add.add_argument("--require-tests", action="store_true",
                     help="fail verification when a change adds or changes no test file")
    add.set_defaults(func=cmd_repo_add)
    repo.add_parser("ls", help="list registered repositories").set_defaults(func=cmd_repo_ls)

    task = sub.add_parser("add", help="queue a coding task")
    task.add_argument("task", help="the task; first line is the title")
    task.add_argument("--repo", required=True)
    task.add_argument("--plan", action="store_true", help="require an approved plan first")
    task.add_argument("--scope", help="comma-separated allowed paths for this task")
    task.add_argument("--body-file", help="file with more task detail")
    task.set_defaults(func=cmd_add)

    ls = sub.add_parser("ls", help="list tasks")
    ls.add_argument("--status")
    ls.add_argument("--limit", type=int, default=30)
    ls.set_defaults(func=cmd_ls)

    show = sub.add_parser("show", help="show one task with runs and log")
    show.add_argument("id", type=int)
    show.set_defaults(func=cmd_show)

    sub.add_parser("run", help="process the queue in the foreground until it is empty"
                   ).set_defaults(func=cmd_run)

    serve = sub.add_parser("serve", help="background loop: issue intake + task queue")
    serve.add_argument("--concurrency", type=int)
    serve.add_argument("--no-intake", action="store_true", help="do not poll GitHub issues")
    serve.add_argument("--once", action="store_true",
                       help="one cycle: poll issues and PR outcomes, work the queue, exit")
    serve.add_argument("--allow-dev", action="store_true", help="allow a source checkout")
    serve.set_defaults(func=cmd_serve)

    st = sub.add_parser("stop", help="halt all work now (kills running agents)")
    st.add_argument("reason", nargs="?")
    st.add_argument("--wait", type=float, default=15, help="seconds to wait for halt")
    st.set_defaults(func=cmd_stop)
    sub.add_parser("resume", help="clear STOP and re-queue stopped tasks"
                   ).set_defaults(func=cmd_resume)

    ap = sub.add_parser("approve", help="approve (or --reject) a task's plan")
    ap.add_argument("id", type=int)
    ap.add_argument("--reject", action="store_true")
    ap.set_defaults(func=cmd_approve)

    nt = sub.add_parser("notes", help="show repo notes and proposed lessons")
    nt.add_argument("repo")
    nt.add_argument("--accept", type=int, action="append")
    nt.add_argument("--reject", type=int, action="append")
    nt.set_defaults(func=cmd_notes)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (config.ConfigError, worktree.GitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

