"""V3 WP5 tests: disposable bare-remote push/pull proof + ls-remote
reconciliation; no blind retry after ambiguous effects."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.v3_git import (
    GitEffectAdapter,
    GitReconcilePending,
    ls_remote_head,
    preflight_identity,
)

from v3_fixtures import make_bare_remote


def _commit(worktree: Path, filename: str, marker: str) -> str:
    subprocess.run(["git", "-C", str(worktree), "config", "user.email",
                    "v3-test@local"], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name",
                    "v3-test"], check=True)
    (worktree / filename).write_text(marker, encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", filename], check=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-q", "-m",
                    marker], check=True)
    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True)
    return head.stdout.strip()


def _clone_with_commit(bare: Path, tmp_path: Path, marker: str) -> Path:
    work = tmp_path / f"clone-{marker[:8]}"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    _commit(work, "proof.txt", marker)
    return work


def test_preflight_identity_resolves_and_mismatch_fails_closed(
        tmp_path):
    bare = make_bare_remote(tmp_path / "remote-a")
    work = _clone_with_commit(bare, tmp_path, "preflight")
    identity = preflight_identity(worktree=work, remote=bare,
                                  branch="main")
    assert identity["branch"] == "main"
    assert len(identity["local_head"]) == 40
    assert identity["remote_head"]
    with pytest.raises(ValueError, match="expected_head mismatch"):
        preflight_identity(worktree=work, remote=bare, branch="main",
                           expected_head="0" * 40)
    with pytest.raises(ValueError, match="not a git worktree"):
        preflight_identity(worktree=tmp_path / "nope", remote=bare)


def test_disposable_remote_push_completes_with_provider_proof(tmp_path):
    bare = make_bare_remote(tmp_path / "remote-b")
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    adapter = GitEffectAdapter(database)
    work = _clone_with_commit(bare, tmp_path, "push-proof")
    local_head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()

    result = adapter.push(action_id="act-1", worktree=work, remote=bare,
                          branch="main")
    assert result["outcome"] == "COMPLETED"
    # PROVIDER-SIDE proof: the remote actually carries the commit.
    assert ls_remote_head(bare) == local_head == result["remote_head"]

    # Idempotent re-delivery of the SAME action: recorded outcome, no
    # second push.
    again = adapter.push(action_id="act-1", worktree=work, remote=bare,
                         branch="main")
    assert again["outcome"] == "COMPLETED" and again["performed"] is False


def test_rejected_push_records_failed_without_retry(tmp_path):
    bare = make_bare_remote(tmp_path / "remote-c")
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    adapter = GitEffectAdapter(database)
    work = _clone_with_commit(bare, tmp_path, "rejected-push")

    # Force a non-fast-forward rejection: another clone pushes first.
    rival = tmp_path / "rival"
    subprocess.run(["git", "clone", "-q", str(bare), str(rival)], check=True)
    rival_head = _commit(rival, "rival.txt", "rival wins")
    subprocess.run(["git", "-C", str(rival), "push", "-q", str(bare),
                    "main"], check=True)

    def _stale_push(worktree, remote, branch):
        out = subprocess.run(
            ["git", "-C", str(worktree), "push", str(remote), branch],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or "push rejected")
        return out

    result = adapter.push(action_id="act-reject", worktree=work,
                          remote=bare, branch="main", execute=_stale_push)
    assert result["outcome"] == "FAILED"
    assert ls_remote_head(bare) == rival_head  # remote unchanged by us
    with pytest.raises(RuntimeError, match="previously failed"):
        adapter.push(action_id="act-reject", worktree=work, remote=bare,
                     branch="main", execute=_stale_push)


def test_ambiguous_timeout_freezes_and_blind_retry_raises(tmp_path):
    bare = make_bare_remote(tmp_path / "remote-d")
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    adapter = GitEffectAdapter(database)
    work = _clone_with_commit(bare, tmp_path, "timeout-proof")

    def _hanging_push(worktree, remote, branch):
        raise TimeoutError("push timed out")

    with pytest.raises(GitReconcilePending):
        adapter.push(action_id="act-amb", worktree=work, remote=bare,
                     branch="main", execute=_hanging_push)

    events = [e.event_type for e in database.events()]
    assert "effect.reconcile_pending" in events

    # NO blind retry: the same action id is refused while pending.
    with pytest.raises(GitReconcilePending):
        adapter.push(action_id="act-amb", worktree=work, remote=bare,
                     branch="main", execute=lambda w, r, b: None)

    local_head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    resolution = adapter.reconcile_pending(
        action_id="act-amb", remote=bare, branch="main",
        expected_head=local_head)
    # The push never landed remotely (it timed out before transfer).
    assert resolution["reconciled"] is False


def test_reconcile_confirms_landed_push_after_ambiguity(tmp_path):
    bare = make_bare_remote(tmp_path / "remote-e")
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    adapter = GitEffectAdapter(database)
    work = _clone_with_commit(bare, tmp_path, "landed-anyway")
    local_head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()

    def _ambiguous_success(worktree, remote, branch):
        # The push DID land, then the caller lost the answer (timeout).
        subprocess.run(
            ["git", "-C", str(worktree), "push", str(remote), branch],
            capture_output=True, text=True, timeout=60, check=True)
        raise TimeoutError("answer lost")

    with pytest.raises(GitReconcilePending):
        adapter.push(action_id="act-lost", worktree=work, remote=bare,
                     branch="main", execute=_ambiguous_success)
    resolution = adapter.reconcile_pending(
        action_id="act-lost", remote=bare, branch="main",
        expected_head=local_head)
    assert resolution["reconciled"] is True
    assert resolution["remote_head"] == local_head


def test_subprocess_timeout_expired_is_ambiguous_not_failed(tmp_path):
    bare = make_bare_remote(tmp_path / "remote-timeout-expired")
    database = FoundryDatabase(tmp_path / "db-timeout-expired.sqlite")
    database.initialize()
    adapter = GitEffectAdapter(database)
    work = _clone_with_commit(bare, tmp_path, "timeout-expired")

    def _expired(worktree, remote, branch):
        raise subprocess.TimeoutExpired(["git", "push"], timeout=1)

    with pytest.raises(GitReconcilePending):
        adapter.push(action_id="act-timeout-expired", worktree=work,
                     remote=bare, branch="main", execute=_expired)
    assert database.event_count(event_type="effect.reconcile_pending") == 1
    assert database.event_count(event_type="system.action_failed") == 0


def test_landed_reconciliation_is_terminal_and_does_not_repush(tmp_path):
    bare = make_bare_remote(tmp_path / "remote-reconciled-terminal")
    database = FoundryDatabase(tmp_path / "db-reconciled-terminal.sqlite")
    database.initialize()
    adapter = GitEffectAdapter(database)
    work = _clone_with_commit(bare, tmp_path, "reconciled-terminal")
    local_head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()

    def _land_then_timeout(worktree, remote, branch):
        subprocess.run(["git", "-C", str(worktree), "push", str(remote), branch],
                       capture_output=True, text=True, check=True)
        raise subprocess.TimeoutExpired(["git", "push"], timeout=1)

    with pytest.raises(GitReconcilePending):
        adapter.push(action_id="act-reconciled-terminal", worktree=work,
                     remote=bare, branch="main", execute=_land_then_timeout)
    resolution = adapter.reconcile_pending(
        action_id="act-reconciled-terminal", remote=bare, branch="main",
        expected_head=local_head)
    assert resolution["reconciled"] is True
    replay = adapter.push(
        action_id="act-reconciled-terminal", worktree=work, remote=bare,
        branch="main", execute=lambda *_: (_ for _ in ()).throw(
            AssertionError("reconciled push must not execute again")))
    assert replay["outcome"] == "COMPLETED"
    assert replay["performed"] is False


def test_dirty_worktree_is_rejected_before_effect_start(tmp_path):
    bare = make_bare_remote(tmp_path / "remote-dirty")
    database = FoundryDatabase(tmp_path / "db-dirty.sqlite")
    database.initialize()
    adapter = GitEffectAdapter(database)
    work = _clone_with_commit(bare, tmp_path, "dirty-worktree")
    (work / "untracked.txt").write_text("not part of the effect", encoding="utf-8")
    with pytest.raises(ValueError, match="dirty worktree"):
        adapter.push(action_id="act-dirty", worktree=work, remote=bare,
                     branch="main")
    assert database.event_count(event_type="system.action_started") == 0


def test_stop_blocks_git_push(tmp_path):
    from jarvis_foundry.owner_controls import OwnerControlService, sign_command

    bare = make_bare_remote(tmp_path / "remote-f")
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    secret = b"test-secret-32-bytes-minimum!!"
    svc = OwnerControlService(database, secret=secret, owner_id="owner")
    nonce = "stop-nonce-git"
    sig = sign_command(secret, "STOP_EVERYTHING", "GLOBAL", "halt", nonce)
    svc.apply("STOP_EVERYTHING", target="GLOBAL", exact_text="halt",
              nonce=nonce, signature=sig)
    adapter = GitEffectAdapter(database)
    work = _clone_with_commit(bare, tmp_path, "stopped")
    with pytest.raises(PermissionError, match="git push stopped"):
        adapter.push(action_id="act-stop", worktree=work, remote=bare,
                     branch="main")


def test_pull_from_disposable_remote_roundtrip(tmp_path):
    bare = make_bare_remote(tmp_path / "remote-g")
    writer = _clone_with_commit(bare, tmp_path, "pull-source")
    subprocess.run(["git", "-C", str(writer), "push", "-q", str(bare),
                    "main"], check=True)
    reader = tmp_path / "reader"
    subprocess.run(["git", "clone", "-q", str(bare), str(reader)],
                   check=True)
    pulled = subprocess.run(
        ["git", "-C", str(reader), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    writer_head = subprocess.run(
        ["git", "-C", str(writer), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert pulled == writer_head == ls_remote_head(bare)
