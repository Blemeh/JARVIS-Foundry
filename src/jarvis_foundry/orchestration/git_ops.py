"""Real git inspection, isolation, and diff evidence for R8 missions."""

from __future__ import annotations

import subprocess  # nosec B404 - read-only git capture with a fixed command prefix
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.orchestration.paths import (
    TEMP_BASE,
    copy_tree,
    resolve_within,
    tree_hash,
)


@dataclass(frozen=True)
class GitRun:
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    wall_seconds: float

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


def run_git(args: list[str], cwd: Path, *, timeout_seconds: int = 120) -> GitRun:
    started = time.monotonic()
    completed = subprocess.run(  # nosec B603 B607 - no shell, fixed `git` prefix only
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return GitRun(
        command=("git", *args),
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        wall_seconds=round(time.monotonic() - started, 3),
    )


def inspect_project(root: Path, *, captured_at: str | None = None) -> dict[str, Any]:
    """Read-only inspection: git state, dirty/untracked report, and tree hash."""
    root = root.resolve()
    head_run = run_git(["rev-parse", "HEAD"], root)
    branch_run = run_git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    status_run = run_git(["status", "--porcelain", "--untracked-files=all"], root)
    untracked_run = run_git(["ls-files", "--others", "--exclude-standard"], root)
    porcelain = [line for line in status_run.stdout.splitlines() if line.strip()]
    untracked = [line for line in untracked_run.stdout.splitlines() if line.strip()]
    head = head_run.stdout.strip() if head_run.exit_code == 0 else None
    return {
        "path": str(root),
        "exists": root.is_dir(),
        "is_git_repo": head is not None or branch_run.exit_code == 0,
        "head": head,
        "branch": branch_run.stdout.strip() if branch_run.exit_code == 0 else None,
        "porcelain": porcelain,
        "untracked": untracked,
        "dirty_report": {
            "porcelain_count": len(porcelain),
            "untracked_count": len(untracked),
            "porcelain": porcelain,
            "untracked": untracked,
        },
        "tree_hash": tree_hash(root) if root.is_dir() else None,
        "captured_at": captured_at,
    }


def clone_project(
    source: Path,
    destination: Path,
    *,
    branch: str,
    snapshot_untracked: bool = False,
    proving_root: Path | None = None,
) -> dict[str, Any]:
    """Create an isolated clone under the proving area with a working branch."""
    source_root = source.resolve()
    dest_root = resolve_within(proving_root or TEMP_BASE, destination)
    if dest_root.exists():
        raise RuntimeError(f"clone destination already exists: {dest_root}")
    dest_root.parent.mkdir(parents=True, exist_ok=True)
    clone = run_git(["clone", "--quiet", str(source_root), str(dest_root)], dest_root.parent)
    if clone.exit_code != 0:
        raise RuntimeError(f"git clone failed: {clone.output[:1000]}")
    switch = run_git(["switch", "-c", branch], dest_root)
    if switch.exit_code != 0:
        raise RuntimeError(f"branch creation failed: {switch.output[:1000]}")
    if snapshot_untracked:
        copy_tree(source_root, dest_root)
    return {
        "source": str(source_root),
        "clone": str(dest_root),
        "branch": branch,
        "clone_head": run_git(["rev-parse", "HEAD"], dest_root).stdout.strip(),
        "clone_porcelain_before": run_git(
            ["status", "--porcelain", "--untracked-files=all"], dest_root
        ).stdout.splitlines(),
    }


def diff_evidence(clone: Path, *, proving_root: Path | None = None) -> dict[str, Any]:
    """Real working-tree diff evidence for the clone (intent-to-add for new files)."""
    clone_root = resolve_within(proving_root or TEMP_BASE, clone)
    status = run_git(["status", "--porcelain", "--untracked-files=all"], clone_root)
    run_git(["add", "-N", "."], clone_root)
    stat = run_git(["diff", "--stat"], clone_root)
    full = run_git(["diff"], clone_root)
    name_only = run_git(["diff", "--name-only"], clone_root)
    return {
        "clone": str(clone_root),
        "status_porcelain": status.stdout.splitlines(),
        "diff_stat": stat.stdout,
        "diff_full": full.stdout,
        "diff_name_only": name_only.stdout.splitlines(),
        "tree_hash": tree_hash(clone_root),
    }


def tree_state_hash(clone: Path, *, proving_root: Path | None = None) -> str:
    """Hash of git-tracked state plus all untracked files in the clone."""
    clone_root = resolve_within(proving_root or TEMP_BASE, clone)
    return tree_hash(clone_root)


def untracked_and_changed_files(
    clone: Path, *, proving_root: Path | None = None
) -> dict[str, dict[str, object]]:
    """Map of all worker-visible file changes (status porcelain plus tree scan)."""
    clone_root = resolve_within(proving_root or TEMP_BASE, clone)
    status = run_git(["status", "--porcelain", "--untracked-files=all"], clone_root)
    paths: list[str] = []
    for line in status.stdout.splitlines():
        if len(line) <= 3:
            continue
        paths.append(line[3:].strip().replace("\\", "/"))
    from jarvis_foundry.orchestration.paths import file_entry

    result: dict[str, dict[str, object]] = {}
    for relative in sorted(paths):
        if "__pycache__" in Path(relative).parts or relative.endswith(".pyc"):
            continue
        path = clone_root / relative
        if path.is_file():
            result[relative] = file_entry(path)
    return result


def porcelain_hash(clone: Path, *, proving_root: Path | None = None) -> str:
    clone_root = resolve_within(proving_root or TEMP_BASE, clone)
    status = run_git(["status", "--porcelain", "--untracked-files=all"], clone_root)
    return canonical_hash(status.stdout.splitlines())
