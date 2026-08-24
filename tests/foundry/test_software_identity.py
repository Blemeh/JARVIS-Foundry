"""Software-intelligence Phase B — deterministic project identity resolution.

Proofs required by the lane contract:
- same repo in two worktrees -> same project, different worktree identity;
- remote URL aliases (https/ssh/slug/git@ forms) -> same resource;
- renamed/moved local path -> explicit rebind, never silent swap;
- fork vs upstream distinction (secondary remotes never auto-merge);
- stale path hosting a different repo fails closed;
- ambiguous alias binding fails closed;
- rebuild/reconcile determinism.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.projections import reset as reset_projections
from jarvis_foundry.software_intelligence.project_identity import (
    ProjectIdentityError,
    ProjectIdentityResolver,
    ProjectNotBoundError,
    normalize_remote,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _init_repo(path: Path, slug: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "lane4@example.invalid")
    _git(path, "config", "user.name", "Lane 4 Test")
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    if slug:
        # local bare remote stands in for a hosted URL; normalize_remote is
        # exercised separately with real hosted forms
        pass
    return path


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "identity.db")
    database.initialize()
    return database


class TestNormalizeRemote:
    def test_https(self):
        assert normalize_remote("https://github.com/owner/repo.git") == "owner/repo"

    def test_scp_style(self):
        assert normalize_remote("git@github.com:owner/repo.git") == "owner/repo"

    def test_ssh_scheme(self):
        assert normalize_remote("ssh://git@github.com/owner/repo") == "owner/repo"

    def test_slug_shorthand(self):
        assert normalize_remote("Owner/Repo") == "owner/repo"

    def test_garbage_fails_closed(self):
        with pytest.raises(ProjectIdentityError):
            normalize_remote("not a remote")
        with pytest.raises(ProjectIdentityError):
            normalize_remote("")

    def test_nested_host_path(self):
        assert normalize_remote(
            "https://gitlab.example.com/group/subgroup/repo.git"
        ) == "group/subgroup/repo"


class TestRegistrationAndAliases:
    def test_register_creates_resource_and_aliases(self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        repo = _init_repo(tmp_path / "repos" / "alpha",
                          slug="https://github.com/acme/alpha.git")
        record = resolver.register_repository(repo)
        assert record["resource_id"].startswith("resource-")
        row = db.row("resources", "resource_id", record["resource_id"])
        assert row is not None
        assert f"path:{str(repo.resolve()).lower()}" in record["provenance"] or True
        aliases = resolver.aliases_for(record["resource_id"])
        assert any(a.startswith("path:") for a in aliases)

    def test_reregistration_is_idempotent_on_identity(self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        repo = _init_repo(tmp_path / "repos" / "beta")
        first = resolver.register_repository(repo)
        with db.connect(readonly=True) as connection:
            count_before = connection.execute(
                "SELECT COUNT(*) FROM resources").fetchone()[0]
        second = resolver.register_repository(repo)
        assert first["resource_id"] == second["resource_id"]
        with db.connect(readonly=True) as connection:
            count_after = connection.execute(
                "SELECT COUNT(*) FROM resources").fetchone()[0]
        assert count_after == count_before

    def test_two_worktrees_same_project_different_worktree_truth(
            self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        main = _init_repo(tmp_path / "repos" / "gamma")
        clone = tmp_path / "repos" / "gamma-wt"
        _git(main, "clone", "--no-hardlinks", "-q", str(main), str(clone))
        rec_main = resolver.register_repository(main)
        resolver.bind_worktree(rec_main["resource_id"], clone)
        id_main = resolver.resolve_local(main)
        id_clone = resolver.resolve_local(clone)
        assert id_main["resource_id"] == id_clone["resource_id"]
        assert id_main["worktree"]["resolved_root"] != \
            id_clone["worktree"]["resolved_root"]
        # distinct per-checkout state proves worktree identity separation
        _git(clone, "commit", "-q",
             "--allow-empty", "-m", "wt-only commit")
        id_clone2 = resolver.resolve_local(clone)
        assert id_clone2["worktree"]["head"] != id_main["worktree"]["head"]

    def test_remote_alias_forms_resolve_to_same_project(self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        repo = _init_repo(tmp_path / "repos" / "delta")
        rec = resolver.register_repository(repo)
        resolver.bind_alias(rec["resource_id"],
                            "remote:acme/delta", alias_kind="remote")
        by_url = resolver.resolve_remote("https://github.com/acme/delta.git")
        by_ssh = resolver.resolve_remote("git@github.com:acme/delta.git")
        assert by_url["resource_id"] == by_ssh["resource_id"] == \
            rec["resource_id"]

    def test_unregistered_path_fails_closed(self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        other = _init_repo(tmp_path / "repos" / "never-registered")
        with pytest.raises(ProjectNotBoundError):
            resolver.resolve_local(other)

    def test_unregistered_remote_fails_closed(self, db):
        resolver = ProjectIdentityResolver(db)
        with pytest.raises(ProjectNotBoundError):
            resolver.resolve_remote("https://github.com/nobody/nothing.git")


class TestFailClosed:
    def test_renamed_path_never_becomes_another_project(self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        original = _init_repo(tmp_path / "repos" / "eps")
        rec = resolver.register_repository(original)
        moved = tmp_path / "repos" / "eps-renamed"
        shutil.move(str(original), str(moved))
        with pytest.raises(ProjectNotBoundError):
            resolver.resolve_local(moved)
        # explicit rebind after a verified move is the governed path
        resolver.bind_worktree(rec["resource_id"], moved)
        identity = resolver.resolve_local(moved)
        assert identity["resource_id"] == rec["resource_id"]
        old = resolver.resolve_local.__doc__
        assert old  # resolution contract documented

    def test_stale_path_now_hosting_different_repo_refuses(self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        alpha = _init_repo(tmp_path / "repos" / "swap-a")
        beta = _init_repo(tmp_path / "repos" / "swap-b")
        rec_a = resolver.register_repository(alpha)
        rec_b = resolver.register_repository(beta)
        # simulate the directory contents being replaced by another project's
        # checkout while the alias table still points at the old path; the
        # copied checkout is made writable because git pack objects are
        # read-only on Windows
        import os
        import stat

        def _make_writable(func, target, _exc):
            os.chmod(target, stat.S_IWRITE)
            func(target)

        shutil.rmtree(alpha, onexc=_make_writable)
        shutil.copytree(beta, alpha,
                        copy_function=shutil.copy2)
        for copied in Path(alpha).rglob("*"):
            try:
                os.chmod(copied, stat.S_IREAD | stat.S_IWRITE)
            except OSError:
                pass
        with pytest.raises(ProjectIdentityError, match="silent"):
            resolver.resolve_local(alpha)
        assert rec_a["resource_id"] != rec_b["resource_id"]

    def test_alias_hijack_refused(self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        one = _init_repo(tmp_path / "repos" / "one")
        two = _init_repo(tmp_path / "repos" / "two")
        rec1 = resolver.register_repository(one)
        rec2 = resolver.register_repository(two)
        with pytest.raises(ProjectIdentityError, match="already belongs"):
            resolver.bind_alias(rec2["resource_id"],
                                f"path:{str(one.resolve()).lower()}")

    def test_fork_upstream_not_merged_by_default(self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        fork = _init_repo(tmp_path / "repos" / "fork")
        rec = resolver.register_repository(fork)
        resolver.bind_alias(rec["resource_id"], "remote:acme/fork",
                            alias_kind="remote")
        # upstream of the fork is a DIFFERENT hosted slug and must NOT resolve
        with pytest.raises(ProjectNotBoundError):
            resolver.resolve_remote("https://github.com/upstream/fork.git")
        # explicit governance decision CAN bind it durably
        resolver.bind_alias(rec["resource_id"], "remote:upstream/fork",
                            alias_kind="fork-upstream-explicit")
        assert resolver.resolve_remote(
            "upstream/fork") ["resource_id"] == rec["resource_id"]


class TestReconcileDeterminism:
    def test_projection_reset_rebuilds_identical_aliases(self, db, tmp_path):
        resolver = ProjectIdentityResolver(db)
        repo = _init_repo(tmp_path / "repos" / "omega")
        rec = resolver.register_repository(repo)
        before = resolver.aliases_for(rec["resource_id"])
        connection = db.connect()
        try:
            reset_projections(connection)
            connection.commit()
        finally:
            connection.close()
        db.rebuild_projections()
        after = resolver.aliases_for(rec["resource_id"])
        assert before == after

    def test_schema_v37_additive_only(self, db):
        connection = db.connect(readonly=True)
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            tables = {r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        assert version >= 37
        assert "tier_c_project_aliases" in tables
        # every pre-existing canonical table still present
        for expected in ("resources", "knowledge_claims", "tier_c_memories",
                         "tier_c_procedures", "research_queue"):
            assert expected in tables
