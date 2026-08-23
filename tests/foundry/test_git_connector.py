"""V5-6 disposable Git connector proof."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.connectors.git import GitConnector, GitConnectorError
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.v3_git import GitReconcilePending, ls_remote_head

from v3_fixtures import make_bare_remote


def _clone_with_commit(bare: Path, tmp_path: Path) -> Path:
    work = tmp_path / "worktree"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "v5-test@local"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "v5-test"], check=True)
    (work / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "base"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", str(bare), "HEAD:main"], check=True)
    return work


def test_owned_commit_and_provider_ref_roundtrip(tmp_path: Path) -> None:
    bare = make_bare_remote(tmp_path / "remote")
    work = _clone_with_commit(bare, tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    connector = GitConnector(database, owned_root=tmp_path)

    identity = connector.inspect(worktree=work, remote=bare)
    assert identity["worktree"] == str(work.resolve())
    assert connector.read_ref(bare) == identity["remote_head"]

    head = connector.create_commit(work, {"change.txt": "v5\n"}, "v5 change")
    assert len(head) == 40
    assert not subprocess.run(
        ["git", "-C", str(work), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    pushed = connector.push(action_id="git-1", worktree=work, remote=bare)
    assert pushed["outcome"] == "COMPLETED"
    assert connector.read_ref(bare) == head
    replay = connector.push(action_id="git-1", worktree=work, remote=bare)
    assert replay["performed"] is False
    assert ls_remote_head(bare) == head


def test_ambiguous_push_requires_provider_reconciliation(tmp_path: Path) -> None:
    bare = make_bare_remote(tmp_path / "remote")
    work = _clone_with_commit(bare, tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    connector = GitConnector(database, owned_root=tmp_path)
    connector.create_commit(work, {"ambiguous.txt": "yes\n"}, "ambiguous")
    local_head = connector.inspect(worktree=work, remote=bare)["local_head"]

    def land_then_timeout(worktree: Path, remote: Path, branch: str) -> None:
        subprocess.run(["git", "-C", str(worktree), "push", str(remote), branch], check=True)
        raise TimeoutError("lost response")

    with pytest.raises(GitReconcilePending):
        connector.push(action_id="git-amb", worktree=work, remote=bare, execute=land_then_timeout)
    with pytest.raises(GitReconcilePending):
        connector.push(action_id="git-amb", worktree=work, remote=bare, execute=lambda *_: None)
    resolved = connector.reconcile(action_id="git-amb", remote=bare, expected_head=local_head)
    assert resolved["reconciled"] is True
    assert connector.push(action_id="git-amb", worktree=work, remote=bare)["performed"] is False


def test_commit_and_push_refuse_unowned_or_dirty_worktrees(tmp_path: Path) -> None:
    bare = make_bare_remote(tmp_path / "remote")
    work = _clone_with_commit(bare, tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    connector = GitConnector(database, owned_root=tmp_path / "owned-only")
    (tmp_path / "owned-only").mkdir()
    with pytest.raises(GitConnectorError, match="owned root"):
        connector.create_commit(work, {"bad.txt": "x"}, "bad")
    owned_work = tmp_path / "owned-only" / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(owned_work)], check=True)
    (owned_work / "dirty.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(ValueError, match="dirty worktree"):
        connector.push(action_id="git-dirty", worktree=owned_work, remote=bare)
