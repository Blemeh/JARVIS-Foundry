"""Push branches and open pull requests (gh, or a local PR-body file); read issues."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from . import proc
from .worktree import git


class GitHubError(RuntimeError):
    pass


def gh(*args: str, timeout: float = 120) -> str:
    try:
        res = subprocess.run([proc.which("gh"), *args], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubError(f"gh {args[0]} failed: {exc}") from exc
    if res.returncode != 0:
        raise GitHubError(f"gh {' '.join(args[:2])} failed: {(res.stderr or res.stdout).strip()}")
    return res.stdout.strip()


def push(worktree: Path, remote: str, branch: str, forbidden: set[str]) -> None:
    """Push the task branch. Refuses default/base branches; never force-pushes."""
    if branch in forbidden or not branch:
        raise GitHubError(f"refusing to push to protected branch {branch!r}")
    git(worktree, "push", "--quiet", remote, f"HEAD:refs/heads/{branch}", timeout=600)


def open_pr(*, github: str, home: Path, task_id: int, branch: str, base: str, title: str,
            body: str, draft: bool, remote: str | None) -> str:
    """Returns the PR URL (GitHub mode) or the path of the PR-body file (local mode)."""
    if not github:
        out = home / "prs" / f"{task_id}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        where = f"{remote}/{branch}" if remote else f"{branch} (local branch, no remote)"
        out.write_text(f"# {title}\n\nHead: {where}\nBase: {base}\n\n{body}\n", encoding="utf-8")
        return str(out)
    with tempfile.TemporaryDirectory(prefix="jarvis-pr-") as tmp:
        body_file = Path(tmp) / "body.md"
        body_file.write_text(body, encoding="utf-8")
        args = ["pr", "create", "--repo", github, "--base", base, "--head", branch,
                "--title", title, "--body-file", str(body_file)]
        out = gh(*args, *(["--draft"] if draft else []))
    return out.splitlines()[-1].strip() if out else ""


def logged_in() -> bool:
    try:
        gh("auth", "status", timeout=30)
        return True
    except GitHubError:
        return False


def labeled_issues(github: str, label: str) -> list[dict]:
    out = gh("issue", "list", "--repo", github, "--label", label, "--state", "open",
             "--json", "number,title,body,url", "--limit", "50")
    return json.loads(out or "[]")


def comment(github: str, number: int, body: str) -> None:
    gh("issue", "comment", str(number), "--repo", github, "--body", body)


def pr_outcome(url: str) -> tuple[str, list[str]]:
    """(state: open | merged | closed, review feedback texts) of a pull request."""
    data = json.loads(gh("pr", "view", url, "--json", "state,reviews,comments") or "{}")
    feedback = [item.get("body", "").strip() for key in ("reviews", "comments")
                for item in data.get(key) or [] if (item.get("body") or "").strip()]
    return str(data.get("state", "OPEN")).lower(), feedback
