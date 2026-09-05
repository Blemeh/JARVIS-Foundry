from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

import jarvis_foundry.workforce.worktrees as workforce_worktrees
from jarvis_foundry.clock import isoformat, utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.supervisor.leases import LeaseManager, LeaseUnavailable
from jarvis_foundry.workforce import (
    WorkPackage,
    WorktreeManager,
    WorkforceResourceError,
    create_work_package,
    rebuild_workforce_state,
)
from jarvis_foundry.workforce.state import _event


def _run(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir(parents=True)
    _run(root, "init", "-q")
    _run(root, "config", "user.email", "jarvis@example.invalid")
    _run(root, "config", "user.name", "JARVIS Test")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("BASE = True\n", encoding="utf-8")
    _run(root, "add", ".")
    _run(root, "commit", "-qm", "base")
    return root, _run(root, "rev-parse", "HEAD")


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()
    return database


def _package(
    tmp_path: Path,
    source_sha: str,
    *,
    package_id: str,
    branch: str,
    allowed_paths: tuple[str, ...],
    lineage: dict | None = None,
    repository_id: str = "fixture-repository",
    worktree_path: Path | None = None,
) -> WorkPackage:
    return WorkPackage(
        package_id=package_id,
        parent_objective_id="objective-v13",
        role="builder",
        capability="worker.code",
        bounded_goal=f"Implement {package_id}.",
        acceptance_criteria=("focused tests pass",),
        source_sha=source_sha,
        repository_id=repository_id,
        branch_name=branch,
        worktree_path=str(
            worktree_path or tmp_path / "worktrees" / package_id
        ),
        allowed_paths=allowed_paths,
        required_tests=("pytest -q",),
        integration_target="candidate/v13-integration",
        owner_constraints=("preserve dirty owner files",),
        lineage=lineage or {},
        lease_seconds=60,
    )


def test_prepare_exact_worktree_preserves_dirty_source(tmp_path: Path) -> None:
    source, base_sha = _repository(tmp_path)
    dirty = source / "owner-notes.txt"
    dirty.write_text("preserve me\n", encoding="utf-8")
    before_status = _run(source, "status", "--porcelain=v1")
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-one",
        branch="candidate/package-one",
        allowed_paths=("src/package_one",),
    )
    create_work_package(database, package)

    manager = WorktreeManager(database)
    prepared = manager.prepare(package.package_id, source_repository=source)

    assert prepared.base_sha == base_sha
    assert _run(Path(prepared.worktree_path), "rev-parse", "HEAD") == base_sha
    assert _run(Path(prepared.worktree_path), "branch", "--show-current") == (
        "candidate/package-one"
    )
    assert _run(source, "status", "--porcelain=v1") == before_status
    assert dirty.read_text(encoding="utf-8") == "preserve me\n"
    resource = database.row("resources", "resource_id", prepared.resource_id)
    assert resource is not None
    state = rebuild_workforce_state(database)
    assert state.package(package.package_id).status == "READY"
    assert state.claims[prepared.claim_id].status == "ACTIVE"

    replayed = WorktreeManager(FoundryDatabase(database.path)).prepare(
        package.package_id,
        source_repository=source,
    )
    assert replayed == prepared
    assert database.event_count(event_type="workforce.worktree_prepared") == 1


def test_execution_validation_rejects_dirty_or_replaced_checkout(
    tmp_path: Path,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-execution-identity",
        branch="candidate/execution-identity",
        allowed_paths=("src/execution-identity",),
    )
    create_work_package(database, package)
    prepared = WorktreeManager(database).prepare(
        package.package_id,
        source_repository=source,
    )
    target = Path(prepared.worktree_path)

    assert workforce_worktrees.validate_execution_worktree(
        database,
        package,
        resource_id=prepared.resource_id,
    ) == target.resolve()

    contamination = target / "untracked-contamination.txt"
    contamination.write_text("unexpected writer\n", encoding="utf-8")
    with pytest.raises(WorkforceResourceError, match="dirty"):
        workforce_worktrees.validate_execution_worktree(
            database,
            package,
            resource_id=prepared.resource_id,
        )
    assert workforce_worktrees.validate_execution_worktree(
        database,
        package,
        resource_id=prepared.resource_id,
        phase="job_delta",
    ) == target.resolve()
    contamination.unlink()

    exclude_path = Path(
        _run(target, "rev-parse", "--git-path", "info/exclude")
    )
    if not exclude_path.is_absolute():
        exclude_path = target / exclude_path
    exclude_path.write_text(
        exclude_path.read_text(encoding="utf-8")
        + "\nignored-contamination.py\n",
        encoding="utf-8",
    )
    ignored = target / "ignored-contamination.py"
    ignored.write_text("EXECUTE = True\n", encoding="utf-8")
    assert _run(target, "status", "--porcelain=v1") == ""
    with pytest.raises(WorkforceResourceError, match="dirty"):
        workforce_worktrees.validate_execution_worktree(
            database,
            package,
            resource_id=prepared.resource_id,
        )
    assert workforce_worktrees.validate_execution_worktree(
        database,
        package,
        resource_id=prepared.resource_id,
        phase="job_delta",
    ) == target.resolve()
    ignored.unlink()

    result = target / "src" / "execution-identity" / "result.py"
    result.parent.mkdir(parents=True)
    result.write_text("RESULT = True\n", encoding="utf-8")
    _run(target, "add", "src/execution-identity/result.py")
    _run(target, "commit", "-qm", "record package result")
    with pytest.raises(WorkforceResourceError, match="HEAD"):
        workforce_worktrees.validate_execution_worktree(
            database,
            package,
            resource_id=prepared.resource_id,
        )
    assert workforce_worktrees.validate_execution_worktree(
        database,
        package,
        resource_id=prepared.resource_id,
        phase="job_delta",
    ) == target.resolve()

    _run(source, "worktree", "remove", str(target))
    _run(tmp_path, "clone", "-q", str(source), str(target))
    _run(
        target,
        "checkout",
        "-qb",
        package.branch_name,
        f"origin/{package.branch_name}",
    )
    with pytest.raises(
        WorkforceResourceError,
        match="linked worktree|common Git directory|identity changed",
    ):
        workforce_worktrees.validate_execution_worktree(
            database,
            package,
            resource_id=prepared.resource_id,
            phase="job_delta",
        )


def test_worktree_git_reads_ignore_replacement_objects(
    tmp_path: Path,
) -> None:
    source, base_sha = _repository(tmp_path)
    original = _run(
        source,
        "--no-replace-objects",
        "show",
        f"{base_sha}:src/base.py",
    )
    _run(source, "checkout", "-q", "--detach", base_sha)
    (source / "src" / "base.py").write_text(
        "BASE = False\n",
        encoding="utf-8",
    )
    _run(source, "add", "src/base.py")
    _run(source, "commit", "-qm", "forged replacement tree")
    forged_tree = _run(source, "rev-parse", "HEAD^{tree}")
    _run(source, "checkout", "-q", "--detach", base_sha)
    replacement_sha = _run(
        source,
        "commit-tree",
        forged_tree,
        "-m",
        "forged replacement object",
    )
    _run(source, "replace", base_sha, replacement_sha)
    assert _run(source, "show", f"{base_sha}:src/base.py") == "BASE = False"
    assert workforce_worktrees._git(
        source,
        "show",
        f"{base_sha}:src/base.py",
    ) == original

    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-replacement-object",
        branch="candidate/replacement-object",
        allowed_paths=("src/replacement-object",),
    )
    create_work_package(database, package)
    prepared = WorktreeManager(database).prepare(
        package.package_id,
        source_repository=source,
    )

    assert workforce_worktrees.validate_execution_worktree(
        database,
        package,
        resource_id=prepared.resource_id,
    ) == Path(prepared.worktree_path).resolve()
    assert workforce_worktrees._git(
        Path(prepared.worktree_path),
        "show",
        f"{base_sha}:src/base.py",
    ) == original


def test_prepare_rejects_source_checkout_and_unlinked_foreign_checkout(
    tmp_path: Path,
) -> None:
    source, base_sha = _repository(tmp_path)
    dirty = source / "owner-dirty.txt"
    dirty.write_text("preserve\n", encoding="utf-8")
    before_status = _run(source, "status", "--porcelain=v1")
    database = _database(tmp_path)
    source_package = _package(
        tmp_path,
        base_sha,
        package_id="package-source-checkout",
        branch="candidate/source-checkout",
        allowed_paths=("src/source-checkout",),
        worktree_path=source,
    )
    create_work_package(database, source_package)
    manager = WorktreeManager(database)

    with pytest.raises(WorkforceResourceError, match="distinct linked Git worktree"):
        manager.prepare(source_package.package_id, source_repository=source)

    assert database.event_count(event_type="workforce.resource_claimed") == 0
    assert _run(source, "status", "--porcelain=v1") == before_status
    assert dirty.read_text(encoding="utf-8") == "preserve\n"

    foreign = tmp_path / "foreign-checkout"
    _run(tmp_path, "clone", "-q", str(source), str(foreign))
    foreign_package = _package(
        tmp_path,
        base_sha,
        package_id="package-foreign-checkout",
        branch="candidate/foreign-checkout",
        allowed_paths=("src/foreign-checkout",),
        worktree_path=foreign,
    )
    _run(
        foreign,
        "checkout",
        "-qb",
        foreign_package.branch_name,
        base_sha,
    )
    create_work_package(database, foreign_package)

    with pytest.raises(WorkforceResourceError, match="linked worktree"):
        manager.prepare(foreign_package.package_id, source_repository=source)

    assert database.event_count(event_type="workforce.resource_claimed") == 0
    assert _run(source, "status", "--porcelain=v1") == before_status


def test_replay_rejects_wrong_source_and_replaced_unlinked_checkout(
    tmp_path: Path,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-replay-identity",
        branch="candidate/replay-identity",
        allowed_paths=("src/replay-identity",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database)
    prepared = manager.prepare(package.package_id, source_repository=source)
    target = Path(prepared.worktree_path)

    alternate_source = tmp_path / "alternate-source"
    _run(tmp_path, "clone", "-q", str(source), str(alternate_source))
    with pytest.raises(
        WorkforceResourceError,
        match="source repository identity changed",
    ):
        manager.prepare(
            package.package_id,
            source_repository=alternate_source,
        )

    _run(source, "worktree", "remove", str(target))
    _run(tmp_path, "clone", "-q", str(source), str(target))
    _run(
        target,
        "checkout",
        "-qb",
        package.branch_name,
        f"origin/{package.branch_name}",
    )
    with pytest.raises(WorkforceResourceError, match="linked worktree"):
        manager.prepare(package.package_id, source_repository=source)


def test_replay_rejects_same_branch_with_unrelated_head(tmp_path: Path) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-replay-ancestry",
        branch="candidate/replay-ancestry",
        allowed_paths=("src/replay-ancestry",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database)
    prepared = manager.prepare(package.package_id, source_repository=source)
    target = Path(prepared.worktree_path)

    _run(target, "checkout", "--orphan", "replacement-history")
    _run(target, "rm", "-rf", ".")
    (target / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    _run(target, "add", ".")
    _run(target, "commit", "-qm", "unrelated root")
    _run(target, "branch", "-M", package.branch_name)

    with pytest.raises(WorkforceResourceError, match="no longer an ancestor"):
        manager.prepare(package.package_id, source_repository=source)

    assert (target / "unrelated.txt").read_text(encoding="utf-8") == "unrelated\n"
    assert _run(target, "status", "--porcelain=v1") == ""


def test_disjoint_scopes_can_be_claimed_in_one_repository(tmp_path: Path) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    first = _package(
        tmp_path,
        base_sha,
        package_id="package-a",
        branch="candidate/package-a",
        allowed_paths=("src/a",),
    )
    second = _package(
        tmp_path,
        base_sha,
        package_id="package-b",
        branch="candidate/package-b",
        allowed_paths=("src/b",),
    )
    create_work_package(database, first)
    create_work_package(database, second)
    manager = WorktreeManager(database)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(manager.prepare, first.package_id, source_repository=source),
            pool.submit(manager.prepare, second.package_id, source_repository=source),
        ]
        prepared = [future.result(timeout=30) for future in futures]

    assert len({item.claim_id for item in prepared}) == 2
    assert len(rebuild_workforce_state(database).active_claims()) == 2


def test_physical_repository_identity_blocks_logical_id_aliases(
    tmp_path: Path,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    first = _package(
        tmp_path,
        base_sha,
        package_id="package-physical-a",
        branch="candidate/physical-a",
        allowed_paths=("src/shared",),
        repository_id="logical-repository-a",
    )
    alias = _package(
        tmp_path,
        base_sha,
        package_id="package-physical-b",
        branch="candidate/physical-b",
        allowed_paths=("src/shared/nested",),
        repository_id="logical-repository-b",
    )
    create_work_package(database, first)
    create_work_package(database, alias)
    manager = WorktreeManager(database)

    manager.prepare(first.package_id, source_repository=source)
    with pytest.raises(WorkforceResourceError, match="conflicts"):
        manager.prepare(alias.package_id, source_repository=source)

    assert not Path(alias.worktree_path).exists()
    assert len(rebuild_workforce_state(database).active_claims()) == 1


def test_same_logical_id_keeps_distinct_physical_repositories_independent(
    tmp_path: Path,
) -> None:
    first_source, first_sha = _repository(tmp_path / "first")
    second_source, second_sha = _repository(tmp_path / "second")
    database = _database(tmp_path)
    first = _package(
        tmp_path / "first",
        first_sha,
        package_id="package-independent-a",
        branch="candidate/independent-a",
        allowed_paths=("src/shared",),
        repository_id="shared-logical-id",
    )
    second = _package(
        tmp_path / "second",
        second_sha,
        package_id="package-independent-b",
        branch="candidate/independent-b",
        allowed_paths=("src/shared",),
        repository_id="shared-logical-id",
    )
    create_work_package(database, first)
    create_work_package(database, second)
    manager = WorktreeManager(database)

    manager.prepare(first.package_id, source_repository=first_source)
    manager.prepare(second.package_id, source_repository=second_source)

    assert len(rebuild_workforce_state(database).active_claims()) == 2


@pytest.mark.parametrize(
    "alias_repository_id",
    ("logical-owner", "logical-alias"),
)
def test_active_claim_exclusively_owns_same_linked_checkout(
    tmp_path: Path,
    alias_repository_id: str,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    shared_worktree = tmp_path / "worktrees" / "shared-checkout"
    owner = _package(
        tmp_path,
        base_sha,
        package_id="package-checkout-owner",
        branch="candidate/shared-checkout",
        allowed_paths=("src/owner",),
        repository_id="logical-owner",
        worktree_path=shared_worktree,
    )
    alias = _package(
        tmp_path,
        base_sha,
        package_id=f"package-checkout-alias-{alias_repository_id}",
        branch=owner.branch_name,
        allowed_paths=("src/alias",),
        repository_id=alias_repository_id,
        worktree_path=shared_worktree,
    )
    create_work_package(database, owner)
    create_work_package(database, alias)
    manager = WorktreeManager(database)

    prepared = manager.prepare(owner.package_id, source_repository=source)
    with pytest.raises(
        WorkforceResourceError,
        match="worktree.*active package",
    ):
        manager.prepare(alias.package_id, source_repository=source)

    state = rebuild_workforce_state(database)
    assert [claim.package_id for claim in state.active_claims()] == [
        owner.package_id
    ]
    assert database.event_count(event_type="workforce.resource_claimed") == 1
    assert Path(prepared.worktree_path).resolve() == shared_worktree.resolve()
    assert _run(shared_worktree, "branch", "--show-current") == owner.branch_name
    assert _run(shared_worktree, "status", "--porcelain=v1") == ""


def test_comparison_group_requires_distinct_linked_checkouts(
    tmp_path: Path,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    shared_worktree = tmp_path / "worktrees" / "shared-comparison-checkout"
    lineage = {"comparison_group": "compare-distinct-checkouts"}
    first = _package(
        tmp_path,
        base_sha,
        package_id="comparison-checkout-a",
        branch="candidate/shared-comparison-checkout",
        allowed_paths=("src/shared",),
        lineage=lineage,
        repository_id="logical-comparison-a",
        worktree_path=shared_worktree,
    )
    alias = _package(
        tmp_path,
        base_sha,
        package_id="comparison-checkout-b",
        branch=first.branch_name,
        allowed_paths=("src/shared",),
        lineage=lineage,
        repository_id="logical-comparison-b",
        worktree_path=shared_worktree,
    )
    create_work_package(database, first)
    create_work_package(database, alias)
    manager = WorktreeManager(database)

    manager.prepare(first.package_id, source_repository=source)
    with pytest.raises(
        WorkforceResourceError,
        match="worktree.*active package",
    ):
        manager.prepare(alias.package_id, source_repository=source)

    assert [
        claim.package_id
        for claim in rebuild_workforce_state(database).active_claims()
    ] == [first.package_id]


def test_accidental_overlap_is_blocked_but_comparison_group_is_explicit(
    tmp_path: Path,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    first = _package(
        tmp_path,
        base_sha,
        package_id="package-primary",
        branch="candidate/package-primary",
        allowed_paths=("src/shared",),
    )
    blocked = _package(
        tmp_path,
        base_sha,
        package_id="package-blocked",
        branch="candidate/package-blocked",
        allowed_paths=("src/shared/nested",),
    )
    create_work_package(database, first)
    create_work_package(database, blocked)
    manager = WorktreeManager(database)
    manager.prepare(first.package_id, source_repository=source)
    with pytest.raises(WorkforceResourceError, match="conflicts"):
        manager.prepare(blocked.package_id, source_repository=source)
    assert not Path(blocked.worktree_path).exists()
    assert _run(source, "branch", "--list", blocked.branch_name) == ""

    comparison_database = _database(tmp_path / "comparison")
    comparison_a = _package(
        tmp_path / "comparison",
        base_sha,
        package_id="candidate-a",
        branch="candidate/comparison-a",
        allowed_paths=("src/shared",),
        lineage={"comparison_group": "compare-shared"},
        repository_id="logical-comparison-a",
    )
    comparison_b = _package(
        tmp_path / "comparison",
        base_sha,
        package_id="candidate-b",
        branch="candidate/comparison-b",
        allowed_paths=("src/shared",),
        lineage={"comparison_group": "compare-shared"},
        repository_id="logical-comparison-b",
    )
    create_work_package(comparison_database, comparison_a)
    create_work_package(comparison_database, comparison_b)
    comparison_manager = WorktreeManager(comparison_database)
    comparison_manager.prepare(comparison_a.package_id, source_repository=source)
    comparison_manager.prepare(comparison_b.package_id, source_repository=source)
    assert len(rebuild_workforce_state(comparison_database).active_claims()) == 2


def test_release_expiry_and_cleanup_are_restart_safe(tmp_path: Path) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-release",
        branch="candidate/package-release",
        allowed_paths=("src/release",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database)
    prepared = manager.prepare(package.package_id, source_repository=source)

    released = manager.release(prepared.claim_id, reason="package finished")
    assert released.status == "RELEASED"
    assert manager.release(prepared.claim_id, reason="replay") == released
    assert manager.cleanup_eligibility(package.package_id)["classification"] == (
        "PRESERVE"
    )

    reacquired = manager.claim(package.package_id, resource_id=prepared.resource_id)
    assert reacquired.claim_id.endswith(":g2")
    expired = manager.expire_claims(
        now=utc_now() + timedelta(minutes=5),
        repository_id=package.repository_id,
    )
    assert [claim.claim_id for claim in expired] == [reacquired.claim_id]
    reopened = rebuild_workforce_state(FoundryDatabase(database.path))
    assert reopened.claims[reacquired.claim_id].status == "EXPIRED"


def test_bound_session_can_renew_mutable_scope_without_reclaiming(
    tmp_path: Path,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-renew",
        branch="candidate/package-renew",
        allowed_paths=("src/renew",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database)
    prepared = manager.prepare(package.package_id, source_repository=source)
    observed_at = utc_now()

    renewed = manager.renew(
        prepared.claim_id,
        session_id="run-renew",
        duration_seconds=300,
        now=observed_at,
    )

    assert renewed.session_id == "run-renew"
    assert manager.expire_claims(
        now=observed_at + timedelta(seconds=60),
    ) == ()
    assert database.event_count(event_type="workforce.resource_claimed") == 1
    assert database.event_count(event_type="workforce.resource_renewed") == 1


def test_repository_mutex_waits_past_legacy_attempt_cliff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    manager = WorktreeManager(
        database,
        mutex_wait_seconds=3.0,
        mutex_retry_seconds=0.05,
        mutex_lease_seconds=300,
    )

    class ContendedLeases:
        def __init__(self) -> None:
            self.attempts = 0

        def acquire(
            self,
            lease_key: str,
            owner_id: str,
            duration_seconds: int,
        ) -> None:
            del lease_key, owner_id
            assert duration_seconds == 300
            self.attempts += 1
            if self.attempts <= 45:
                raise LeaseUnavailable("held")

    leases = ContendedLeases()
    manager.leases = leases
    clock = [0.0]
    monkeypatch.setattr(workforce_worktrees.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        workforce_worktrees.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    manager._acquire_mutex("repository", "package")

    assert leases.attempts == 46
    assert clock[0] > 2.0
    assert clock[0] < 3.0

    class BusyLeases:
        @staticmethod
        def acquire(
            lease_key: str,
            owner_id: str,
            duration_seconds: int,
        ) -> None:
            del lease_key, owner_id, duration_seconds
            raise LeaseUnavailable("held")

    manager.leases = BusyLeases()
    clock[0] = 0.0
    with pytest.raises(WorkforceResourceError, match="3.000s"):
        manager._acquire_mutex("repository", "package")
    assert clock[0] == pytest.approx(3.0)


def test_repository_claim_mutex_renews_during_prolonged_repository_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    first = _package(
        tmp_path,
        base_sha,
        package_id="package-mutex-owner",
        branch="candidate/mutex-owner",
        allowed_paths=("src/owner",),
    )
    second = _package(
        tmp_path,
        base_sha,
        package_id="package-mutex-waiter",
        branch="candidate/mutex-waiter",
        allowed_paths=("src/waiter",),
    )
    create_work_package(database, first)
    create_work_package(database, second)
    setup = WorktreeManager(database)
    setup.prepare(first.package_id, source_repository=source)
    prepared = setup.prepare(second.package_id, source_repository=source)
    setup.release(prepared.claim_id, reason="exercise claim mutex")

    manager = WorktreeManager(
        database,
        mutex_wait_seconds=3.0,
        mutex_lease_seconds=1,
    )
    physical_repository_id = manager._repository_identity_for_package(
        second
    ).physical_repository_id
    mutex_key = f"workforce-claim:{physical_repository_id[:24]}"
    scan_started = threading.Event()
    allow_scan = threading.Event()
    original_repository_id = manager._claim_physical_repository_id

    def prolonged_repository_scan(claim):
        if claim.package_id == first.package_id:
            scan_started.set()
            assert allow_scan.wait(timeout=5)
        return original_repository_id(claim)

    monkeypatch.setattr(
        manager,
        "_claim_physical_repository_id",
        prolonged_repository_scan,
    )
    claimed = []
    errors: list[BaseException] = []

    def reclaim() -> None:
        try:
            claimed.append(
                manager.claim(
                    second.package_id,
                    resource_id=prepared.resource_id,
                )
            )
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=reclaim, daemon=True)
    thread.start()
    assert scan_started.wait(timeout=5)
    try:
        time.sleep(1.2)
        with pytest.raises(LeaseUnavailable):
            LeaseManager(database).acquire(mutex_key, "intruder", 1)
    finally:
        allow_scan.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert not errors
    assert [claim.claim_id for claim in claimed] == [
        f"claim:{second.package_id}:g2"
    ]
    assert database.row("leases", "lease_key", mutex_key) is None


def test_repository_claim_append_rejects_lost_mutex_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-mutex-stolen",
        branch="candidate/mutex-stolen",
        allowed_paths=("src/stolen",),
    )
    create_work_package(database, package)
    setup = WorktreeManager(database)
    prepared = setup.prepare(package.package_id, source_repository=source)
    setup.release(prepared.claim_id, reason="exercise final mutex proof")

    manager = WorktreeManager(database)
    physical_repository_id = manager._repository_identity_for_package(
        package
    ).physical_repository_id
    mutex_key = f"workforce-claim:{physical_repository_id[:24]}"

    def steal_mutex(*, now=None, repository_id=None):
        del now, repository_id
        expires_at = isoformat(utc_now() + timedelta(minutes=5))
        with database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE leases SET owner_id = ?, expires_at = ? "
                "WHERE lease_key = ?",
                ("intruder", expires_at, mutex_key),
            )
            assert cursor.rowcount == 1
        return ()

    monkeypatch.setattr(manager, "expire_claims", steal_mutex)

    with pytest.raises(WorkforceResourceError, match="repository claim mutex"):
        manager.claim(
            package.package_id,
            resource_id=prepared.resource_id,
        )

    assert database.event_count(event_type="workforce.resource_claimed") == 1
    assert rebuild_workforce_state(database).active_claims() == ()
    assert LeaseManager(database).release(mutex_key, "intruder")


def test_registration_failure_releases_claim_and_clean_retry_reconciles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-registration-failure",
        branch="candidate/package-registration-failure",
        allowed_paths=("src/registration_failure",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database)
    append = database.append
    failed = False

    def fail_first_registration(event):
        nonlocal failed
        if event.event_type == "project.registered" and not failed:
            failed = True
            raise RuntimeError("injected registration failure")
        return append(event)

    monkeypatch.setattr(database, "append", fail_first_registration)
    with pytest.raises(RuntimeError, match="injected registration failure"):
        manager.prepare(package.package_id, source_repository=source)

    target = Path(package.worktree_path)
    assert target.is_dir()
    assert _run(target, "status", "--porcelain=v1") == ""
    failed_state = rebuild_workforce_state(database)
    first_claim = failed_state.claims["claim:package-registration-failure:g1"]
    assert first_claim.status == "RELEASED"
    release_event = database.events(
        event_types=("workforce.resource_released",),
    )[-1]
    assert release_event.payload["expected_status"] == "ACTIVE"
    assert release_event.payload["expected_version"] == 1
    assert release_event.payload["version"] == 2

    monkeypatch.setattr(database, "append", append)
    prepared = manager.prepare(package.package_id, source_repository=source)

    assert prepared.claim_id == "claim:package-registration-failure:g2"
    recovered = rebuild_workforce_state(database)
    assert recovered.package(package.package_id).status == "READY"
    assert recovered.claims[prepared.claim_id].status == "ACTIVE"
    assert target.is_dir()


def test_failed_preparation_preserves_dirty_partial_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-dirty-failure",
        branch="candidate/package-dirty-failure",
        allowed_paths=("src/dirty_failure",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database)

    def dirty_then_fail(path, *, resource_id):
        del resource_id
        marker = Path(path) / "owner-uncommitted.txt"
        marker.write_text("preserve this\n", encoding="utf-8")
        raise RuntimeError("injected inspection failure")

    monkeypatch.setattr(
        workforce_worktrees,
        "inspect_git_resource",
        dirty_then_fail,
    )
    with pytest.raises(RuntimeError, match="injected inspection failure"):
        manager.prepare(package.package_id, source_repository=source)

    target = Path(package.worktree_path)
    marker = target / "owner-uncommitted.txt"
    assert marker.read_text(encoding="utf-8") == "preserve this\n"
    state = rebuild_workforce_state(database)
    assert state.claims["claim:package-dirty-failure:g1"].status == "RELEASED"

    with pytest.raises(WorkforceResourceError, match="dirty.*preserved"):
        manager.prepare(package.package_id, source_repository=source)
    assert marker.read_text(encoding="utf-8") == "preserve this\n"


def test_stop_after_claim_blocks_git_effect_and_releases_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-stop-before-git",
        branch="candidate/package-stop-before-git",
        allowed_paths=("src/stop_before_git",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database, stop_path=stop_path)
    claim = manager.claim

    def claim_then_stop(*args, **kwargs):
        value = claim(*args, **kwargs)
        stop_path.write_text("stop\n", encoding="utf-8")
        return value

    monkeypatch.setattr(manager, "claim", claim_then_stop)
    with pytest.raises(PermissionError, match="workforce stopped"):
        manager.prepare(package.package_id, source_repository=source)

    assert not Path(package.worktree_path).exists()
    state = rebuild_workforce_state(database)
    assert state.claims["claim:package-stop-before-git:g1"].status == "RELEASED"


def test_stop_after_git_inspection_blocks_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-stop-before-registration",
        branch="candidate/package-stop-before-registration",
        allowed_paths=("src/stop_before_registration",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database, stop_path=stop_path)
    inspect = workforce_worktrees.inspect_git_resource

    def inspect_then_stop(path, *, resource_id):
        value = inspect(path, resource_id=resource_id)
        stop_path.write_text("stop\n", encoding="utf-8")
        return value

    monkeypatch.setattr(
        workforce_worktrees,
        "inspect_git_resource",
        inspect_then_stop,
    )
    with pytest.raises(PermissionError, match="workforce stopped"):
        manager.prepare(package.package_id, source_repository=source)

    assert Path(package.worktree_path).is_dir()
    assert database.event_count(event_type="project.registered") == 0
    state = rebuild_workforce_state(database)
    claim = state.claims["claim:package-stop-before-registration:g1"]
    assert claim.status == "RELEASED"


def test_release_serializes_after_inflight_renewal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id="package-cas",
        branch="candidate/package-cas",
        allowed_paths=("src/cas",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database)
    prepared = manager.prepare(package.package_id, source_repository=source)
    renewal_read = threading.Event()
    allow_renewal = threading.Event()
    release_finished = threading.Event()
    rebuild = workforce_worktrees._rebuild_workforce_state_on_connection

    def pause_renewal(database_arg, connection):
        state = rebuild(database_arg, connection)
        if threading.current_thread().name == "claim-renewal":
            renewal_read.set()
            assert allow_renewal.wait(timeout=5)
        return state

    monkeypatch.setattr(
        workforce_worktrees,
        "_rebuild_workforce_state_on_connection",
        pause_renewal,
    )
    renewal_error: list[BaseException] = []
    release_error: list[BaseException] = []

    def renew() -> None:
        try:
            manager.renew(
                prepared.claim_id,
                session_id="run-cas",
                duration_seconds=300,
            )
        except BaseException as error:
            renewal_error.append(error)

    def release() -> None:
        try:
            manager.release(prepared.claim_id, reason="finished")
        except BaseException as error:
            release_error.append(error)
        finally:
            release_finished.set()

    renewal_thread = threading.Thread(
        target=renew,
        name="claim-renewal",
        daemon=True,
    )
    renewal_thread.start()
    assert renewal_read.wait(timeout=5)
    release_thread = threading.Thread(
        target=release,
        name="claim-release",
        daemon=True,
    )
    release_thread.start()
    try:
        assert not release_finished.wait(timeout=0.2)
    finally:
        allow_renewal.set()
    renewal_thread.join(timeout=5)
    release_thread.join(timeout=5)

    assert not renewal_thread.is_alive()
    assert not release_thread.is_alive()
    assert not renewal_error
    assert not release_error
    assert rebuild_workforce_state(database).claims[prepared.claim_id].status == (
        "RELEASED"
    )
    renewed_event = database.events(
        event_types=("workforce.resource_renewed",),
    )[-1]
    released_event = database.events(
        event_types=("workforce.resource_released",),
    )[-1]
    assert renewed_event.payload["expected_status"] == "ACTIVE"
    assert renewed_event.payload["expected_version"] == 1
    assert renewed_event.payload["version"] == 2
    assert released_event.payload["expected_status"] == "ACTIVE"
    assert released_event.payload["expected_version"] == 2
    assert released_event.payload["version"] == 3


@pytest.mark.parametrize(
    ("operation", "event_type", "expected_status"),
    (
        ("renew", "workforce.resource_renewed", "ACTIVE"),
        ("release", "workforce.resource_released", "RELEASED"),
        ("expire", "workforce.resource_expired", "EXPIRED"),
    ),
)
def test_rejected_claim_version_does_not_advance_mutation_sequence(
    tmp_path: Path,
    operation: str,
    event_type: str,
    expected_status: str,
) -> None:
    source, base_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = _package(
        tmp_path,
        base_sha,
        package_id=f"package-stale-version-{operation}",
        branch=f"candidate/stale-version-{operation}",
        allowed_paths=(f"src/stale-version-{operation}",),
    )
    create_work_package(database, package)
    manager = WorktreeManager(database)
    prepared = manager.prepare(package.package_id, source_repository=source)
    claim = rebuild_workforce_state(database).claims[prepared.claim_id]
    stale_payload = {
        **claim.to_payload(),
        "session_id": "stale-session",
        "expected_status": "ACTIVE",
        "expected_version": 98,
        "version": 99,
    }
    database.append(
        _event(
            "workforce.resource_renewed",
            stale_payload,
            key=f"stale-v99:{claim.claim_id}",
            correlation_id=package.package_id,
            source_id="adversarial-test",
        )
    )

    assert rebuild_workforce_state(database).claims[claim.claim_id] == claim
    if operation == "renew":
        manager.renew(
            claim.claim_id,
            session_id="current-session",
            duration_seconds=600,
            now=utc_now(),
        )
    elif operation == "release":
        manager.release(claim.claim_id, reason="finished")
    else:
        manager.expire_claims(now=utc_now() + timedelta(minutes=5))

    mutation = database.events(event_types=(event_type,))[-1]
    assert mutation.payload["expected_version"] == 1
    assert mutation.payload["version"] == 2
    assert mutation.payload["version"] != 100
    assert (
        rebuild_workforce_state(database).claims[claim.claim_id].status
        == expected_status
    )
