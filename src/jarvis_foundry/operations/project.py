"""Disposable project/Git workflows for broad-operation proofs.

This adapter deliberately permits only a caller-owned disposable root and a
local bare remote.  It is a project-operation seam above the canonical Git
effect implementation, not a second connector framework and never a path to
the owner's live repository.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..events import canonical_json
from .procedure import OperationError


class ProjectWorkflowError(OperationError):
    pass


def _safe_local_path(value: str | Path, *, field_name: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_absolute():
        raise ProjectWorkflowError(f"{field_name} must be an absolute path")
    if any(part in {".git", "foundry.db"} for part in path.parts[-2:]):
        raise ProjectWorkflowError(f"{field_name} points at a protected internal path")
    return path


def _assert_disposable(root: Path) -> None:
    markers = {"temp", "tmp", "disposable", "sandbox", "lab", "fixtures"}
    if not any(part.lower() in markers for part in root.parts):
        raise ProjectWorkflowError(
            f"refusing non-disposable project root {root}; use a temp/lab root")


@dataclass(frozen=True, slots=True)
class ProjectObservation:
    root: str
    branch: str
    head: str | None
    dirty_paths: tuple[str, ...]
    issue_spec_hash: str | None
    remote: str | None

    @property
    def clean(self) -> bool:
        return not self.dirty_paths


@dataclass(frozen=True, slots=True)
class ProjectWorkflowReceipt:
    root: str
    branch: str
    commit: str
    remote: str
    remote_commit: str
    issue_spec_hash: str
    test_command: tuple[str, ...]


class DisposableProjectWorkflow:
    """Inspect, edit, test, commit, push, and reconcile a local project."""

    def __init__(
        self,
        root: str | Path,
        *,
        remote: str | Path,
        branch: str = "main",
    ) -> None:
        self.root = _safe_local_path(root, field_name="project root")
        self.remote = _safe_local_path(remote, field_name="project remote")
        _assert_disposable(self.root)
        if self.remote == self.root:
            raise ProjectWorkflowError("project and bare remote must be different paths")
        if not branch or any(char in branch for char in "\r\n "):
            raise ProjectWorkflowError("branch must be a single exact name")
        self.branch = branch

    def _git(self, args: Sequence[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        if result.returncode != 0:
            raise ProjectWorkflowError(
                f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
        return result

    def initialize(self, issue_spec: str) -> ProjectObservation:
        if not isinstance(issue_spec, str) or not issue_spec.strip():
            raise ProjectWorkflowError("issue/spec must be non-empty")
        self.root.mkdir(parents=True, exist_ok=True)
        if (self.root / ".git").exists():
            raise ProjectWorkflowError(f"project already initialized: {self.root}")
        self._run_unbound(["git", "init", "--initial-branch", self.branch], self.root)
        self._run_unbound(["git", "-C", str(self.root), "config", "user.name", "JARVIS Disposable"], self.root)
        self._run_unbound(["git", "-C", str(self.root), "config", "user.email", "jarvis-disposable@example.invalid"], self.root)
        self.write_file("ISSUE.md", issue_spec.rstrip() + "\n")
        self._run_unbound(["git", "init", "--bare", str(self.remote)], self.remote.parent)
        self._git(["remote", "add", "origin", str(self.remote)])
        return self.inspect()

    def inspect(self) -> ProjectObservation:
        if not (self.root / ".git").exists():
            raise ProjectWorkflowError(f"not a Git project: {self.root}")
        status = self._git(["status", "--porcelain=v1"]).stdout.splitlines()
        branch = self._git(["branch", "--show-current"]).stdout.strip()
        head_result = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        head = head_result.stdout.strip() if head_result.returncode == 0 else None
        issue = self.root / "ISSUE.md"
        issue_hash = hashlib.sha256(issue.read_bytes()).hexdigest() if issue.is_file() else None
        remote_result = subprocess.run(
            ["git", "-C", str(self.root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        remote = remote_result.stdout.strip() if remote_result.returncode == 0 else None
        return ProjectObservation(
            root=str(self.root), branch=branch, head=head,
            dirty_paths=tuple(line[3:] if len(line) > 3 else line for line in status),
            issue_spec_hash=issue_hash, remote=remote,
        )

    def resume(self) -> ProjectObservation:
        """Re-open an interrupted disposable workflow after identity checks."""
        observation = self.inspect()
        if observation.branch != self.branch:
            raise ProjectWorkflowError(
                f"project branch changed during interruption: {observation.branch!r}")
        if observation.remote is None or Path(observation.remote).expanduser().resolve() != self.remote:
            raise ProjectWorkflowError("project remote identity changed during interruption")
        return observation

    def write_file(self, relative_path: str, content: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ProjectWorkflowError("relative file path is required")
        if Path(relative_path).is_absolute():
            raise ProjectWorkflowError("project edits must use relative paths")
        destination = (self.root / relative_path).resolve()
        try:
            destination.relative_to(self.root)
        except ValueError as exc:
            raise ProjectWorkflowError("project edit escapes the disposable root") from exc
        if ".git" in destination.relative_to(self.root).parts:
            raise ProjectWorkflowError("project edits cannot target .git")
        if not isinstance(content, str):
            raise ProjectWorkflowError("project file content must be text")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.v7b.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
        return destination

    def run_tests(self, command: Sequence[str], *, timeout: float = 120.0) -> tuple[str, ...]:
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise ProjectWorkflowError("test command must be a non-empty argv list")
        result = subprocess.run(
            list(command), cwd=self.root, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        if result.returncode != 0:
            raise ProjectWorkflowError(
                f"project tests failed ({result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[-1000:]}")
        return tuple(command)

    def commit(self, message: str) -> str:
        if not isinstance(message, str) or not message.strip():
            raise ProjectWorkflowError("commit message is required")
        self._git(["add", "--all", "--", "."])
        staged = self._git(["diff", "--cached", "--name-only"]).stdout.splitlines()
        if not staged:
            raise ProjectWorkflowError("project commit has no changes")
        self._git(["-c", "user.name=JARVIS Disposable", "-c",
                   "user.email=jarvis-disposable@example.invalid", "commit",
                   "-m", message])
        return self._git(["rev-parse", "HEAD"]).stdout.strip()

    def push(self) -> str:
        self._git(["push", "--set-upstream", "origin", self.branch], timeout=60)
        return self.verify_remote()

    def verify_remote(self) -> str:
        head = self._git(["rev-parse", "HEAD"]).stdout.strip()
        result = subprocess.run(
            ["git", "ls-remote", str(self.remote), f"refs/heads/{self.branch}"],
            cwd=self.root, capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode != 0:
            raise ProjectWorkflowError(f"remote read failed: {result.stderr.strip()}")
        lines = result.stdout.splitlines()
        remote_head = lines[0].split()[0] if lines else ""
        if remote_head != head:
            raise ProjectWorkflowError(
                f"remote reconciliation mismatch: local={head}, remote={remote_head}")
        return remote_head

    def run(
        self,
        *,
        issue_spec: str,
        edits: Mapping[str, str],
        test_command: Sequence[str],
        commit_message: str,
    ) -> ProjectWorkflowReceipt:
        self.initialize(issue_spec)
        for path, content in edits.items():
            self.write_file(path, content)
        command = self.run_tests(test_command)
        commit = self.commit(commit_message)
        remote_commit = self.push()
        observation = self.inspect()
        if observation.issue_spec_hash is None:
            raise ProjectWorkflowError("issue/spec disappeared before receipt")
        return ProjectWorkflowReceipt(
            root=str(self.root), branch=self.branch, commit=commit,
            remote=str(self.remote), remote_commit=remote_commit,
            issue_spec_hash=observation.issue_spec_hash,
            test_command=command,
        )

    @staticmethod
    def _run_unbound(command: Sequence[str], cwd: Path) -> None:
        result = subprocess.run(
            list(command), cwd=cwd, capture_output=True, text=True,
            timeout=60, check=False,
        )
        if result.returncode != 0:
            raise ProjectWorkflowError(
                f"command {' '.join(command)} failed: "
                f"{(result.stderr or result.stdout).strip()}")


__all__ = [
    "DisposableProjectWorkflow", "ProjectObservation", "ProjectWorkflowError",
    "ProjectWorkflowReceipt",
]
