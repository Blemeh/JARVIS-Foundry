"""Lane 3 developer-effects demo: real local substrate, honest receipts.

Run:  python var/lane3-demo/demo.py all

Scenarios:
  1. Git: branch -> deterministic commit -> push to a local bare remote ->
     provider readback -> second identical push is an already-satisfied no-op
     -> a divergent push reports a truthful rejection.
  2. GitHub issue: intent -> one provider POST -> readback MATCH -> durable
     receipt; the same intent twice yields exactly one provider object.
  3. GitHub pull request publish: deterministic branch + push + PR with
     readback acceptance and restart replay.
  4. Crash recovery: a child process starts the canonical effect, POSTs the
     issue, and dies with os._exit before any journal completion. A fresh
     process reaps the stale claim, reads provider truth, finalizes the
     ORIGINAL effect and issues zero duplicate writes.

Everything runs against a disposable workspace under var/lane3-demo/work.
LIVE JARVIS is never touched: no live database, release pointer, trust
window, scheduler entry, or provider credential is read or written.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DEMO_ROOT = Path(__file__).resolve().parent
REPO_ROOT = DEMO_ROOT.parents[1]
SRC_ROOT = REPO_ROOT / "src"
for entry in (str(SRC_ROOT), str(DEMO_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from fake_github_provider import FakeGitHubProvider  # noqa: E402

from jarvis_foundry.connectors.git import GitConnector  # noqa: E402
from jarvis_foundry.connectors.github_write import (  # noqa: E402
    GitHubPullRequestEffectDriver,
    GitHubRepositoryAuthorization,
    GitHubWriteAdapter,
)
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.effects import effect_state  # noqa: E402
from jarvis_foundry.fabric import (  # noqa: E402
    ExecutorContext,
    TaskIntent,
    execute_action,
    seed_builtins,
)

WORK_ROOT = DEMO_ROOT / "work"
RECEIPTS_ROOT = DEMO_ROOT / "receipts"


def _reset(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _receipt(name: str, document: dict[str, Any]) -> dict[str, Any]:
    RECEIPTS_ROOT.mkdir(parents=True, exist_ok=True)
    path = RECEIPTS_ROOT / f"{name}.json"
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return document


def _run_git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True,
    )
    return result.stdout.strip()


def _init_repo(worktree: Path, bare: Path) -> None:
    worktree.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(worktree)], check=True)
    _run_git(worktree, "config", "user.email", "lane3-demo@local")
    _run_git(worktree, "config", "user.name", "Lane 3 Demo")
    (worktree / "README.md").write_text("lane3 demo seed\n", encoding="utf-8")
    _run_git(worktree, "add", "README.md")
    _run_git(worktree, "commit", "-q", "-m", "seed")
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    _run_git(worktree, "push", "-q", str(bare), "main")


def _database(path: Path) -> FoundryDatabase:
    database = FoundryDatabase(path)
    database.initialize()
    return database


def _action_receipt(result: Any) -> dict[str, Any]:
    return {
        "capability_id": result.capability_id,
        "action_kind": result.action_kind,
        "claimed": result.claimed.value,
        "verified": result.verified.value,
        "success": result.success,
        "note": result.note,
        "observed": dict(result.observed),
        "receipt": dict(result.receipt),
    }


# ---------------------------------------------------------------------------
# 1. Git branch/commit/push against a local bare remote
# ---------------------------------------------------------------------------

def run_git_scenario() -> dict[str, Any]:
    root = WORK_ROOT / "git"
    _reset(root)
    bare = root / "remote.git"
    seed = root / "seed"
    _init_repo(seed, bare)
    database = _database(root / "foundry.db")
    connector = GitConnector(database, owned_root=root / "owned")
    work = root / "owned" / "repo"
    work.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    _run_git(work, "config", "user.email", "lane3-demo@local")
    _run_git(work, "config", "user.name", "Lane 3 Demo")

    branch = connector.create_branch(work, "lane3/feature")
    head = connector.create_commit(
        work,
        {"feature.txt": "deterministic lane 3 change\n"},
        "Lane 3 demo deterministic commit",
    )
    first = connector.push(
        action_id="lane3-demo-push-1",
        worktree=work,
        remote=str(bare),
        branch="lane3/feature",
        expected_head=head,
    )
    readback = connector.remote_branch_state(
        str(bare), "lane3/feature", expected_head=head,
    )
    second = connector.push(
        action_id="lane3-demo-push-2",
        worktree=work,
        remote=str(bare),
        branch="lane3/feature",
        expected_head=head,
    )

    other = root / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    _run_git(other, "config", "user.email", "other@local")
    _run_git(other, "config", "user.name", "Other")
    _run_git(other, "checkout", "-q", "lane3/feature")
    (other / "upstream.txt").write_text("advanced upstream\n", encoding="utf-8")
    _run_git(other, "add", "upstream.txt")
    _run_git(other, "commit", "-q", "-m", "upstream advance")
    _run_git(other, "push", "-q", "origin", "lane3/feature")
    upstream_head = _run_git(other, "rev-parse", "HEAD")

    divergent_head = connector.create_commit(
        work,
        {"local.txt": "divergent local change\n"},
        "Lane 3 divergent local commit",
    )
    rejected = connector.push(
        action_id="lane3-demo-push-rejected",
        worktree=work,
        remote=str(bare),
        branch="lane3/feature",
        expected_head=divergent_head,
    )
    preserved = connector.remote_branch_state(
        str(bare), "lane3/feature", expected_head=upstream_head,
    )

    return _receipt("git", {
        "branch": branch["branch"],
        "commit": head,
        "first_push": first,
        "readback": readback,
        "second_push": second,
        "divergent_push": rejected,
        "remote_preserved_after_rejection": preserved,
        "duplicate_material_effects": 0 if second["performed"] is False else 1,
    })


# ---------------------------------------------------------------------------
# 2/3. GitHub issue dedupe and PR publish against the fake provider
# ---------------------------------------------------------------------------

def _write_ctx(
    database: FoundryDatabase,
    provider: FakeGitHubProvider,
    owned_root: Path,
    *,
    dispatch_enabled: bool = True,
) -> ExecutorContext:
    return ExecutorContext(
        database=database,
        owned_root=owned_root,
        github_write=provider.write,
        github_api=provider.read,
        github_writes_enabled=dispatch_enabled,
        github_authorized_repositories=("owner/demo",),
    )


ISSUE_PAYLOAD: dict[str, Any] = {
    "owner": "owner",
    "repo": "demo",
    "title": "Lane 3 durable issue",
    "body": "exact owner text preserved verbatim",
}


def run_issue_scenario() -> dict[str, Any]:
    root = WORK_ROOT / "github-issue"
    _reset(root)
    database = _database(root / "foundry.db")
    provider = FakeGitHubProvider(root / "provider")
    ctx = _write_ctx(database, provider, root / "owned")
    seed_builtins(database)

    first = execute_action(
        database, ctx, TaskIntent(task_id="task.lane3.issue", required_actions=("github.issue.create",)),
        "github.issue.create", ISSUE_PAYLOAD,
    )
    second = execute_action(
        database, ctx, TaskIntent(task_id="task.lane3.issue", required_actions=("github.issue.create",)),
        "github.issue.create", ISSUE_PAYLOAD,
    )
    return _receipt("github-issue", {
        "first": _action_receipt(first),
        "second": _action_receipt(second),
        "provider_write_calls": provider.write_calls,
        "provider_issues": provider.issues,
    })


def run_pr_scenario() -> dict[str, Any]:
    root = WORK_ROOT / "github-pr"
    _reset(root)
    bare = root / "remote.git"
    seed = root / "seed"
    _init_repo(seed, bare)
    database = _database(root / "foundry.db")
    provider = FakeGitHubProvider(root / "provider")
    owned = root / "owned"
    work = owned / "repo"
    work.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    _run_git(work, "config", "user.email", "lane3-demo@local")
    _run_git(work, "config", "user.name", "Lane 3 Demo")
    (work / "pr-change.txt").write_text("pull request change\n", encoding="utf-8")
    _run_git(work, "add", "pr-change.txt")
    _run_git(work, "commit", "-q", "-m", "lane 3 PR change")
    head_sha = _run_git(work, "rev-parse", "HEAD")

    adapter = GitHubWriteAdapter(
        transport=provider.write,
        read_transport=provider.read,
        dispatch_enabled=True,
        authorized_repositories=("owner/demo",),
    )
    authorization = GitHubRepositoryAuthorization(
        authorization_id="owner-grant:lane3-demo",
        repository="owner/demo",
        remote=str(bare),
        allowed_base_branches=("main",),
        owner_authorized=True,
        dispatch_enabled=True,
    )
    driver = GitHubPullRequestEffectDriver(
        database, owned_root=owned, adapter=adapter,
    )
    first = driver.publish(
        authorization=authorization,
        worktree=work,
        owner="owner",
        repo="demo",
        remote=str(bare),
        base="main",
        head_sha=head_sha,
        title="Lane 3 deterministic pull request",
        body="published exactly once",
    )
    second = driver.publish(
        authorization=authorization,
        worktree=work,
        owner="owner",
        repo="demo",
        remote=str(bare),
        base="main",
        head_sha=head_sha,
        title="Lane 3 deterministic pull request",
        body="published exactly once",
    )
    plan = first.get("push_effect", {})
    return _receipt("github-pr", {
        "first_publish": first,
        "second_publish": second,
        "provider_write_calls": provider.write_calls,
        "provider_pulls": provider.pulls,
        "remote_branch_head": GitConnector.read_ref(str(bare), str(first.get("branch") or plan.get("branch") or "")),
    })


# ---------------------------------------------------------------------------
# 4. Crash recovery: child process dies after the provider accepted
# ---------------------------------------------------------------------------

CRASH_PAYLOAD: dict[str, Any] = {
    "owner": "owner",
    "repo": "demo",
    "title": "Lane 3 crash-recovery issue",
    "body": "the provider accepts this exactly once",
    "claim_ttl_s": 0.01,
}


def run_crash_child(root: Path) -> int:
    database = _database(root / "foundry.db")
    provider = FakeGitHubProvider(root / "provider")
    seed_builtins(database)

    def dying_write(endpoint: str, payload: dict[str, Any]) -> tuple[int, bytes]:
        status, body = provider.write(endpoint, payload)
        print(json.dumps({"provider_posts": provider.write_calls, "status": status}))
        sys.stdout.flush()
        os._exit(9)

    ctx = ExecutorContext(
        database=database,
        owned_root=root / "owned",
        github_write=dying_write,
        github_api=provider.read,
        github_writes_enabled=True,
        github_authorized_repositories=("owner/demo",),
    )
    execute_action(
        database, ctx,
        TaskIntent(task_id="task.lane3.crash", required_actions=("github.issue.create",)),
        "github.issue.create", CRASH_PAYLOAD,
    )
    return 3


def run_crash_scenario() -> dict[str, Any]:
    root = WORK_ROOT / "crash"
    _reset(root)
    database = _database(root / "foundry.db")
    seed_builtins(database)
    child = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "crash-child", str(root)],
        capture_output=True, text=True, timeout=120,
    )
    if child.returncode != 9:
        raise RuntimeError(
            f"crash child did not die as intended: rc={child.returncode} "
            f"stderr={child.stderr[-500:]}"
        )

    started = [
        event for event in database.events()
        if event.event_type == "system.action_started"
        and event.payload.get("kind") == "github.issue.create"
    ]
    if len(started) != 1:
        raise RuntimeError(f"expected exactly one started effect, found {len(started)}")
    action_key = str(started[0].payload["action_key"])
    before = effect_state(database, action_key)
    if before.get("status") != "STARTED":
        raise RuntimeError(f"expected STARTED after crash, found {before.get('status')}")

    time.sleep(0.05)
    provider = FakeGitHubProvider(root / "provider")
    fresh_ctx = _write_ctx(database, provider, root / "owned", dispatch_enabled=False)
    recovered = execute_action(
        database, fresh_ctx,
        TaskIntent(task_id="task.lane3.crash", required_actions=("github.write.reconcile",)),
        "github.write.reconcile",
        {
            "original_effect_id": action_key,
            "original_action": "github.issue.create",
            "request": {key: value for key, value in CRASH_PAYLOAD.items() if key != "claim_ttl_s"},
        },
    )
    replay = execute_action(
        database, fresh_ctx,
        TaskIntent(task_id="task.lane3.crash", required_actions=("github.issue.create",)),
        "github.issue.create",
        {key: value for key, value in CRASH_PAYLOAD.items() if key != "claim_ttl_s"},
    )
    after = effect_state(database, action_key)
    return _receipt("crash-recovery", {
        "child_exit_code": child.returncode,
        "child_stdout": child.stdout.strip(),
        "effect_key": action_key,
        "state_before_recovery": before,
        "recovered": _action_receipt(recovered),
        "replayed_intent": _action_receipt(replay),
        "state_after_recovery": after,
        "provider_write_calls": provider.write_calls,
        "provider_issues": provider.issues,
    })


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "all"
    if command == "crash-child":
        return run_crash_child(Path(argv[2]))
    if command == "git":
        print(json.dumps(run_git_scenario(), indent=2, default=str))
        return 0
    if command == "issue":
        print(json.dumps(run_issue_scenario(), indent=2, default=str))
        return 0
    if command == "pr":
        print(json.dumps(run_pr_scenario(), indent=2, default=str))
        return 0
    if command == "crash":
        print(json.dumps(run_crash_scenario(), indent=2, default=str))
        return 0
    if command == "all":
        results = {
            "git": run_git_scenario(),
            "github_issue": run_issue_scenario(),
            "github_pr": run_pr_scenario(),
            "crash_recovery": run_crash_scenario(),
        }
        print(json.dumps(results, indent=2, default=str))
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
