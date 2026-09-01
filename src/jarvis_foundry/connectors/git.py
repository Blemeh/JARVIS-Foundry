"""V5-6 governed Git connector built on the canonical V3 adapter."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Any

from ..database import FoundryDatabase
from ..v3_git import GitEffectAdapter, ls_remote_head, preflight_identity
from .contract import ConnectorSpec


class GitConnectorError(ValueError):
    """A Git target or local mutation is outside the connector contract."""


class GitConnector:
    """Lane-owned Git operations with provider-side ref reconciliation.

    Push ambiguity and effect journaling stay in the existing
    :class:`GitEffectAdapter`; this class only binds identity and owned
    worktree boundaries around it.
    """

    spec = ConnectorSpec(
        connector_id="conn-git-v5",
        kind="git",
        version="1.0.0",
        actions={
            "git.inspect": "read",
            "git.read_ref": "read",
            "git.diff": "read",
            "git.branch": "write",
            "git.commit": "write",
            "git.push": "external_effect",
        },
        capabilities={
            "inspect": "READY",
            "read_refs": "READY",
            "diff": "READY",
            "local_branch": "READY",
            "local_commit": "READY",
            "push": "NOT_TESTED",
        },
        retry_policy={"max_attempts": 2, "backoff_s": [0, 0.1]},
        target_kinds=("git_worktree", "git_remote"),
        idempotency_strategy="provider-key",
        reconciliation_strategy="provider-query",
        supports_cancellation=False,
        cost_metadata={"unit": "local_git", "cost_known": True},
        evidence_fields=("action_key", "remote_head", "provider_state", "outcome"),
    )

    def __init__(self, database: FoundryDatabase, *, owned_root: str | Path) -> None:
        self.database = database
        self.owned_root = Path(owned_root).expanduser().resolve(strict=False)
        self._effects = GitEffectAdapter(database, source_id="v5-git")

    def _owned_worktree(self, worktree: str | Path) -> Path:
        resolved = Path(worktree).expanduser().resolve(strict=False)
        try:
            inside = os.path.commonpath(
                [os.path.normcase(str(resolved)), os.path.normcase(str(self.owned_root))]
            ) == os.path.normcase(str(self.owned_root))
        except ValueError as error:
            raise GitConnectorError("worktree is outside the owned root") from error
        if not inside:
            raise GitConnectorError("worktree is outside the owned root")
        if not (resolved / ".git").exists():
            raise GitConnectorError(f"not a git worktree: {resolved}")
        return resolved

    @staticmethod
    def _run(worktree: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        )
        if result.returncode != 0:
            raise GitConnectorError(result.stderr.strip() or "git command failed")
        return result.stdout.strip()

    def inspect(
        self,
        *,
        worktree: str | Path,
        remote: str | Path,
        branch: str = "main",
        expected_head: str | None = None,
    ) -> dict[str, Any]:
        owned = self._owned_worktree(worktree)
        return preflight_identity(
            worktree=owned, remote=remote, branch=branch,
            expected_head=expected_head, reject_dirty=False,
        )

    @staticmethod
    def read_ref(remote: str | Path, branch: str = "main") -> str | None:
        return ls_remote_head(remote, branch)

    def create_commit(
        self,
        worktree: str | Path,
        changes: Mapping[str, str],
        message: str,
    ) -> str:
        """Write only named files in an owned clean worktree and commit once."""
        owned = self._owned_worktree(worktree)
        if not isinstance(message, str) or not message.strip():
            raise GitConnectorError("commit message is required")
        if not changes:
            raise GitConnectorError("commit requires at least one change")
        status = self._run(owned, "status", "--porcelain")
        if status:
            raise ValueError(f"dirty worktree: refusing git effect for {owned}")
        paths: list[str] = []
        for relative, content in changes.items():
            if not isinstance(relative, str) or not relative or relative != relative.strip():
                raise GitConnectorError("commit path must be a trimmed relative path")
            target = (owned / relative).resolve(strict=False)
            try:
                inside = os.path.commonpath(
                    [os.path.normcase(str(target)), os.path.normcase(str(owned))]
                ) == os.path.normcase(str(owned))
            except ValueError as error:
                raise GitConnectorError("commit path is outside the owned worktree") from error
            if not inside or target == owned or ".git" in target.relative_to(owned).parts:
                raise GitConnectorError("commit path is outside the owned worktree")
            if not isinstance(content, str):
                raise GitConnectorError("commit content must be text")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            paths.append(str(target.relative_to(owned)))
        self._run(owned, "add", "--", *paths)
        staged = subprocess.run(
            ["git", "-C", str(owned), "diff", "--cached", "--quiet"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if staged.returncode == 0:
            raise GitConnectorError("changes produced no Git diff")
        self._run(owned, "commit", "-m", message)
        head = self._run(owned, "rev-parse", "HEAD")
        if len(head) != 40:
            raise GitConnectorError("git commit did not return a full object id")
        return head

    def commit_existing(
        self,
        worktree: str | Path,
        paths: tuple[str, ...] | list[str],
        message: str,
    ) -> str:
        """Commit named worker changes that were already tested in place.

        ``create_commit`` remains the atomic write-and-commit primitive for
        callers that have no pre-commit test phase.  Journey 3 needs the
        stronger ordering worker patch -> tests -> commit, so this companion
        stages only the named paths and performs no additional file writes.
        """

        owned = self._owned_worktree(worktree)
        if not isinstance(message, str) or not message.strip():
            raise GitConnectorError("commit message is required")
        selected = tuple(paths)
        if not selected:
            raise GitConnectorError("commit requires at least one existing path")
        status = self._run(owned, "status", "--porcelain")
        if not status:
            raise GitConnectorError("worktree has no existing changes to commit")
        relative_paths: list[str] = []
        for relative in selected:
            if not isinstance(relative, str) or not relative or relative != relative.strip():
                raise GitConnectorError("commit path must be a trimmed relative path")
            target = (owned / relative).resolve(strict=False)
            try:
                inside = os.path.commonpath(
                    [os.path.normcase(str(target)), os.path.normcase(str(owned))]
                ) == os.path.normcase(str(owned))
            except ValueError as error:
                raise GitConnectorError("commit path is outside the owned worktree") from error
            if not inside or target == owned or ".git" in target.relative_to(owned).parts:
                raise GitConnectorError("commit path is outside the owned worktree")
            relative_paths.append(str(target.relative_to(owned)))
        self._run(owned, "add", "--", *relative_paths)
        staged = subprocess.run(
            ["git", "-C", str(owned), "diff", "--cached", "--quiet"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if staged.returncode == 0:
            raise GitConnectorError("named paths produced no Git diff")
        self._run(owned, "commit", "-m", message)
        head = self._run(owned, "rev-parse", "HEAD")
        if len(head) != 40:
            raise GitConnectorError("git commit did not return a full object id")
        return head

    def create_branch(self, worktree: str | Path, name: str) -> dict[str, Any]:
        """Create and check out ``name`` from a clean owned worktree."""
        owned = self._owned_worktree(worktree)
        if not isinstance(name, str) or not name.strip() or any(
            char in name for char in (" ", "..", "\\", "\n")
        ):
            raise GitConnectorError("branch name must be an exact identity")
        status = self._run(owned, "status", "--porcelain")
        if status:
            raise GitConnectorError(f"dirty worktree: refusing git branch for {owned}")
        self._run(owned, "checkout", "-b", name.strip())
        return {
            "worktree": str(owned),
            "branch": self._run(owned, "branch", "--show-current"),
            "head": self._run(owned, "rev-parse", "HEAD"),
        }

    def diff(self, worktree: str | Path) -> dict[str, Any]:
        """Observe status, unstaged+staged patch, and HEAD. Does not mutate."""
        owned = self._owned_worktree(worktree)
        status = self._run(owned, "status", "--porcelain")
        patch = subprocess.run(
            ["git", "-C", str(owned), "diff", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        )
        if patch.returncode != 0:
            raise GitConnectorError(patch.stderr.strip() or "git diff failed")
        return {
            "worktree": str(owned),
            "head": self._run(owned, "rev-parse", "HEAD"),
            "branch": self._run(owned, "branch", "--show-current") or "DETACHED",
            "dirty": bool(status),
            "status": status,
            "patch": patch.stdout,
        }

    def push(
        self,
        *,
        action_id: str,
        worktree: str | Path,
        remote: str | Path,
        branch: str = "main",
        expected_head: str | None = None,
        execute: Callable[..., Any] | None = None,
        stop_path: str | None = None,
    ) -> dict[str, Any]:
        owned = self._owned_worktree(worktree)
        return self._effects.push(
            action_id=action_id, worktree=owned, remote=remote, branch=branch,
            expected_head=expected_head, execute=execute, stop_path=stop_path,
        )

    def reconcile(
        self,
        *,
        action_id: str,
        remote: str | Path,
        branch: str = "main",
        expected_head: str,
    ) -> dict[str, Any]:
        return self._effects.reconcile_pending(
            action_id=action_id, remote=remote, branch=branch,
            expected_head=expected_head,
        )


__all__ = ["GitConnector", "GitConnectorError"]
