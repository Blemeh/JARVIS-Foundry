"""Git helpers: one worktree and branch per task."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

# Tool caches never count as the agent's work and are never committed.
CACHE_DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules",
              ".venv", ".tox")
_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


class GitError(RuntimeError):
    pass


def git(cwd: Path, *args: str, timeout: float = 300, check: bool = True) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    proc = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=str(cwd),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, check=False)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def toplevel(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel"))


def has_remote(repo: Path, remote: str) -> bool:
    return remote in git(repo, "remote").split()


def default_branch(repo: Path, remote: str = "origin") -> str:
    ref = git(repo, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD", check=False)
    if ref.startswith(f"{remote}/"):
        return ref[len(remote) + 1:]
    return git(repo, "symbolic-ref", "--short", "HEAD", check=False) or "main"


def branch_exists(repo: Path, branch: str) -> bool:
    return bool(git(repo, "branch", "--list", branch))


def free_branch(repo: Path, branch: str) -> str:
    """Never reuse or delete an existing branch: add a suffix instead."""
    candidate, n = branch, 2
    while branch_exists(repo, candidate):
        candidate, n = f"{branch}-{n}", n + 1
    return candidate


def base_ref(repo: Path, remote: str, base: str, fetch: bool) -> str:
    if has_remote(repo, remote):
        if fetch:
            git(repo, "fetch", "--quiet", remote, base, timeout=600)
        if git(repo, "rev-parse", "--verify", "--quiet", f"{remote}/{base}", check=False):
            return f"{remote}/{base}"
    return base


def create(repo: Path, dest: Path, branch: str, ref: str) -> tuple[str, str]:
    """Create `dest` as a new worktree on a new branch. Returns (branch, base_sha)."""
    with _LOCKS[str(repo.resolve())]:
        if dest.exists():
            remove(repo, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        branch = free_branch(repo, branch)
        base_sha = git(repo, "rev-parse", f"{ref}^{{commit}}")
        git(repo, "worktree", "add", "--quiet", "-b", branch, str(dest), base_sha)
    return branch, base_sha


def _force_remove(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:
        shutil.rmtree(path, onerror=_force_remove)


def remove(repo: Path, dest: Path) -> None:
    """Remove the worktree folder; the branch stays."""
    with _LOCKS[str(repo.resolve())]:
        git(repo, "worktree", "remove", "--force", str(dest), check=False)
        for attempt in range(5):
            if not dest.exists():
                break
            try:
                _rmtree(dest)
            except OSError:
                time.sleep(0.2 * (attempt + 1))  # Windows: files briefly locked
        git(repo, "worktree", "prune", check=False)


def _pathspec_excludes() -> list[str]:
    return [f":(exclude,glob)**/{name}/**" for name in CACHE_DIRS] + [
        ":(exclude,glob)**/*.pyc"]


def stage(worktree: Path, paths: list[str] | None = None) -> None:
    """Stage the given paths (or everything except tool caches)."""
    if paths is None:
        git(worktree, "add", "--all", "--", ".", *_pathspec_excludes())
        return
    subprocess.run(["git", "add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul"],
                   cwd=str(worktree), input="\0".join(paths).encode("utf-8"),
                   capture_output=True, check=True)


def commit_all(worktree: Path, message: str, paths: list[str] | None = None) -> str | None:
    """Commit the agent's changes (minus caches). Returns the new sha, or None if nothing."""
    stage(worktree, paths)
    if not git(worktree, "diff", "--cached", "--name-only"):
        return None
    identity = []
    if not git(worktree, "config", "user.email", check=False):
        identity = ["-c", "user.name=JARVIS", "-c", "user.email=jarvis@localhost"]
    git(worktree, *identity, "commit", "--quiet", "--no-verify", "-m", message)
    return git(worktree, "rev-parse", "HEAD")


def changed_files(worktree: Path, base_sha: str) -> list[str]:
    """Every path that differs from the base commit: committed, staged, unstaged, untracked."""
    tracked = git(worktree, "diff", "--name-only", "--no-renames", "-z", base_sha)
    untracked = git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    paths = {p for p in (tracked + "\0" + untracked).split("\0") if p.strip()}
    return sorted(p for p in paths
                  if not any(part in CACHE_DIRS for part in p.split("/"))
                  and not p.endswith(".pyc"))


def diff_text(worktree: Path, base_sha: str, limit: int = 60000) -> str:
    """Diff of the committed task branch against its base."""
    text = git(worktree, "diff", "--no-color", base_sha, "HEAD")
    return text if len(text) <= limit else text[:limit] + "\n... (diff truncated)"
